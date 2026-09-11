# gui/docks/scheme_list.py
"""
SchemeListFormWidget — Config-side Scheme List record viewer + Reread (P5,
plan_2026_09_05_scheme_list.md §5.1, design §3).

A Scheme List is a NAMED snapshot of a real, already-routed board region
(recorded via Tools -> "Scheme Lists" -> "Record..."). This dock is the
MINIMAL Config side of that feature: it shows a loaded ``scheme_lists:``
record — the CENTRE-frame geometry of the recorded region, a ``source_sheet``
readout and an EDITABLE ``pivot`` (Commit B1): the record's anchor point in
the centre-frame as x/y mm fields with a "Centre" quick-set (0,0 = the region
centre) and an "Apply" (Save pivot) that rewrites the pivot into the file that
owns the record (design_2026_09_07_scheme_list_pivot.md /
plan_2026_09_07_scheme_list_pivot_commit_b.md). Commit B2 adds a
"Take from selection" quick-set (pivot_from_selection_button): it reads the
CENTRE of the CURRENT live board selection and writes it into x/y as a pivot
in the centre-frame (selected centre minus the LIVE centre of the recorded
region, design_2026_09_07_scheme_list_pivot_commit_b2.md §1) — a pure live
read into the fields, still saved only by the explicit Apply — plus the
recorded-geometry
summary — and offers the one board action that belongs here,
**Reread**: re-run the capture against the live board
(kicadstamp.scheme_list_capture.build_scheme_list_diff), show the diff
dialog, and only on an explicit **Apply** rewrite the stored record in place
(``upsert_list_entry`` by name into the file that actually owns the record).

Deliberately NO Placement/Redraw in this dock (plan §5.1 / design §3/§8):
cloning a Scheme List onto another sheet happens ONLY through the
Entity/Placement machinery in Trees (the P4 ApplyPipeline branch + the P6
"Instantiate..." wizard), never from a Config form. Nothing in this module
ever applies anything to the live board.

The module also hosts the PURE storage helpers every Scheme List write path
shares (``scheme_list_to_dict``, the fixed ``scheme_lists.json`` path + the
auto-``include:`` ensure, the read/write helpers and the duplicate pre-checks),
so the Tools "Record..." flow (DockHub) and this form's Reread Apply use ONE
implementation instead of two copies.
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPlainTextEdit, QPushButton, QTabWidget,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.config import SchemeListConfig, load_scheme_list
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.scheme_list_capture import (
    SchemeListBoundaryNet,
    SchemeListDiff,
    _region_centre,
    build_scheme_list_diff,
    capture_scheme_list,
)
from kicadstamp.utils.units import MM

from ..worker import refresh_snapshot_then, start_long_op
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      WARN_STYLE as _WARN_STYLE,
                      add_include, display_path, read_data, show_message,
                      upsert_list_entry)
from .rename import collect_graph_files, find_list_entry_file
from .tree_from_selection import selected_center_mm

logger = logging.getLogger(__name__)


# ── Pure "By sheet" scope helpers (shared by Record / Re-source / Reread) ───
#
# Every "By sheet" capture picks a ROOT sheet from the LIVE hierarchy and
# derives its footprint refs from the footprints' FULL resolved sheet-path
# (Selected.sheet), NOT from Board.select(sheet=...) (explore.py) whose filter
# is a single-segment membership test anywhere in the path — that would merge
# two same-named sheets at different nesting levels. The three helpers below do
# the prefix/equality matching correctly and are deliberately Qt-free so every
# Scheme List write path (Tools Record... in DockHub, the Record dialog, the
# future Re-source and the Reread scope recompute) shares ONE implementation.

def snapshot_with_resolved_sheets(snapshot: list, sheet_names: dict) -> list:
    """A copy of `snapshot` whose .sheet is re-resolved against `sheet_names`
    (the config-based {uuid: name} map, ctx.sheet_names) instead of the live
    Board's OWN resolution — Board.connect() (gui/connection.py) never passes
    schematic_dir, so a live Board's own sheet_names is always {} and every
    .sheet ends up a list of None. Selected.fp is the raw footprint handle
    kept exactly for this kind of re-resolution (its own docstring). Record/
    Re-source call this on the snapshot BEFORE building RecordSchemeListDialog
    so its "By sheet" tab has real sheet paths to offer (2026-09-07 fix,
    plan_2026_09_07_scheme_list_sheet_names_empty.md).

    An empty `sheet_names` is a no-op (returns `snapshot` unchanged, untouched
    — not even read via .fp): resolving against an empty map could only
    produce all-None anyway, so there is nothing to gain from re-resolving,
    and callers/tests that build a synthetic snapshot without a real .fp
    (only .sheet, already whatever they intend) are not forced to fake one
    just because this helper runs unconditionally upstream."""
    if not sheet_names:
        return snapshot
    from dataclasses import replace

    from kicadstamp.sheet_names import resolve_sheet_path_names
    return [replace(s, sheet=resolve_sheet_path_names(s.fp, sheet_names))
            for s in snapshot]


def live_sheet_paths(snapshot: list) -> list[tuple[str, ...]]:
    """Distinct FULL sheet-path tuples across the live snapshot, sorted — the
    WHOLE Selected.sheet chain of every footprint (not flattened leaf
    segments, unlike TreesDock._live_sheets / scheme_list_place._live_sheets),
    so a nested sheet stays distinguishable from a same-named sheet elsewhere
    in the hierarchy. A path containing an unresolved (None/empty) segment is
    skipped — resolve_sheet_path_names can leave a gap when a .kicad_sch
    couldn't be parsed (sheet_names.py's own docstring), and a partial path
    cannot be matched reliably. These are the ROOT candidates for the "By
    sheet" tab's sheet_combo."""
    return sorted({tuple(s.sheet) for s in snapshot
                   if s.sheet and all(seg for seg in s.sheet)})


def all_sheet_paths(snapshot: list) -> list[tuple[str, ...]]:
    """Every REAL sheet of the live hierarchy — sorted distinct FULL paths AND
    every PREFIX of them (Commit E, plan_2026_09_07_scheme_list_by_sheet_full_
    tree.md). Unlike `live_sheet_paths` (which returns only the FULL leaf path
    of each footprint-bearing sheet), this also includes the CONTAINER sheets
    that carry no footprints of their own but have footprint-bearing
    sub-sheets — without them the "By sheet" tree can never show the schematic
    structure (on Denis's board it degenerated into a flat combobox of leaf
    paths: 9 leaf paths, top-level Channel_0/1/2/FPGA/MCU/Power, 0 parents).
    A path with an unresolved (None/empty) segment is skipped, as in
    live_sheet_paths."""
    paths = set()
    for s in snapshot:
        sheet = tuple(s.sheet or [])
        if not sheet or not all(seg for seg in sheet):
            continue
        for i in range(1, len(sheet) + 1):
            paths.add(sheet[:i])
    return sorted(paths)


def sheet_paths_under(paths: list, root: tuple[str, ...]) -> list[tuple[str, ...]]:
    """Every path in `paths` that IS `root` or a descendant of it (prefix
    match), sorted — the checklist ROWS once a root sheet is picked in "By
    sheet" (root itself is included: a sheet can have footprints of its own
    directly on it, in addition to whatever is on its sub-sheets)."""
    return sorted(p for p in paths if p[:len(root)] == root)


def refs_on_sheet(snapshot: list, path: tuple[str, ...]) -> list[str]:
    """Refs of footprints whose FULL sheet path EQUALS `path` EXACTLY — direct
    membership, NOT prefix/recursive (recursion is the checklist's job, not
    this helper's: sum refs_on_sheet(...) over the CHECKED rows to get the
    final capture set). Sorted, deduplicated."""
    return sorted({s.ref for s in snapshot if tuple(s.sheet or []) == path})


def sheet_subtree_plan(root: tuple[str, ...],
                       candidates: list[tuple[str, ...]]) -> list:
    """Qt-free plan of the "By sheet" sub-sheet TREE under `root` (Commit C,
    plan_2026_09_07_scheme_list_commit_c_sheet_tree.md §3.2) — replaces the
    flat indented checklist with a real hierarchy the dialog renders into a
    QTreeWidget.

    `candidates` — the SORTED sheet paths under `root` (root itself included,
    i.e. `sheet_paths_under` output): every path that has >=1 footprint in the
    live snapshot = one checkable "sheet row" (its DIRECT refs). Every
    candidate nests under its nearest ancestor. An INTERMEDIATE sheet missing
    from `candidates` (no footprints of its own) is inserted as a STRUCTURAL
    node — the renderer must NOT give it a checkbox (it can contribute no
    direct refs), it only keeps a nested sub-sheet from hanging without its
    parent.

    Returns a nested list starting with the single `root` node; each node is a
    dict {"path": tuple, "name": str, "children": [node, ...]}. Whether a node
    is a capturable candidate is decided by membership in `candidates` (a
    structural node's path is not there). Empty when `candidates` is empty — a
    leaf root with nothing under it hides the tree."""
    if not candidates:
        return []
    top: dict = {"path": tuple(root), "name": root[-1], "children": []}
    by_path = {tuple(root): top}
    for path in candidates:
        path = tuple(path)
        # Sorted order guarantees a parent (shorter prefix) is handled before
        # its child, so by the time we need `path[:depth]` it exists.
        for depth in range(len(root), len(path)):
            prefix = path[:depth + 1]
            if prefix in by_path:
                continue  # already present (a candidate or built earlier)
            node = {"path": prefix, "name": prefix[-1], "children": []}
            by_path[prefix] = node
            # parent (a node dict) exists — sorted order guarantees it.
            by_path[path[:depth]]["children"].append(node)
    return [top]


def record_refs_for(snapshot: list, by_sheet: bool,
                    checked_paths: Optional[list],
                    selection_refs: list) -> List[str]:
    """Derive the capture refs from a Record/Re-source dialog result:
      - "By sheet" — union of the DIRECT refs on every CHECKED sheet path
        (refs_on_sheet over `checked_paths`; no recursion — the checklist
        already expresses which sub-sheets to include);
      - "By selection" — the caller's own `selection_refs` unchanged (the
        pre-existing P2 behavior).
    Sorted + deduplicated. Shared by DockHub's record_scheme_list and the
    future Re-source so both modes resolve refs identically."""
    if by_sheet:
        return sorted({r for p in (checked_paths or [])
                       for r in refs_on_sheet(snapshot, tuple(p))})
    return sorted(set(selection_refs))


def reread_scope_refs(stored: SchemeListConfig, snapshot: list,
                      selection_refs: List[str],
                      active_scope_paths: Optional[list] = None) -> List[str]:
    """The CURRENT Reread scope for a stored record (5c.4, plan_2026_09_06_
    scheme_list_sheet_capture.md):
      - "By sheet"-record (``scope_sheet_paths`` set): recomputed from the SAME
        checked leaf paths over the LIVE snapshot (refs_on_sheet union) —
        Reread stays one-click, live state alone decides what appeared/vanish-
        ed on the recorded leaves. ``active_scope_paths`` (named presets,
        plan_2026_09_06_scheme_list_named_presets.md §8/§9) OVERRIDES the
        stored paths for THIS Reread: the scope comes from the preset the user
        picked on the record page, not from scope_sheet_paths. None (no preset
        / the "(current)" sentinel) = stored paths unchanged — byte-identical
        regression to 5c by default.
      - "By selection"-record (``scope_sheet_paths`` None): the CURRENT board
        selection refs — the user re-selects the (possibly changed) set, then
        clicks Reread. An empty list here is a caller decision point (warn,
        never silently diff the stored set).
    Sorted + deduplicated either way."""
    paths = (active_scope_paths if active_scope_paths is not None
             else stored.scope_sheet_paths)
    if paths:
        return sorted({r for p in paths
                       for r in refs_on_sheet(snapshot, tuple(p))})
    return sorted(set(selection_refs))


# ── Pure storage helpers (shared by the Record... tool and Reread Apply) ────

def scheme_list_to_dict(record: SchemeListConfig) -> Dict[str, Any]:
    """SchemeListConfig -> plain dict for JSON/.sexp writing (the compact,
    round-trippable shape load_scheme_list() reads back). Mirror of
    net_trace_extract.net_trace_to_dict: required fields first, optional
    fields omitted when unset, geometry lists always written.

    `pivot` (the record's anchor point in the centre-frame,
    design_2026_09_07_scheme_list_pivot.md) is written only when it differs
    from the (0,0)=centre default — load defaults an absent pivot to (0,0)."""
    d: Dict[str, Any] = {"name": record.name}
    if record.pivot != (0.0, 0.0):
        d["pivot"] = [record.pivot[0], record.pivot[1]]
    if record.source_sheet:
        d["source_sheet"] = record.source_sheet
    if record.scope_sheet_paths:
        # 5c.1 — a "By sheet" record's CHECKED leaf paths (Reread recomputes
        # the current scope from these); None (By selection) is not written.
        d["scope_sheet_paths"] = [list(p) for p in record.scope_sheet_paths]
    if record.scope_presets:
        # Named presets library (plan_2026_09_06_scheme_list_named_presets.md
        # §5) — written only when non-empty; [] (By selection) is not written.
        d["scope_presets"] = [
            {"name": p.name, "sheet_paths": [list(sp) for sp in p.sheet_paths]}
            for p in record.scope_presets]
    d["components"] = [
        {"ref": c.ref, "offset_along_mm": c.offset_along_mm,
         "offset_across_mm": c.offset_across_mm, "rotation_deg": c.rotation_deg}
        for c in record.components
    ]
    if record.vias:
        d["vias"] = [
            {"offset_along_mm": v.offset_along_mm, "offset_across_mm": v.offset_across_mm,
             "drill_mm": v.drill_mm, "diameter_mm": v.diameter_mm, "net": v.net}
            for v in record.vias
        ]
    if record.tracks:
        d["tracks"] = [
            {"start_along_mm": t.start_along_mm, "start_across_mm": t.start_across_mm,
             "end_along_mm": t.end_along_mm, "end_across_mm": t.end_across_mm,
             "width_mm": t.width_mm, "layer": t.layer, "net": t.net}
            for t in record.tracks
        ]
    if record.boundary_nets:
        d["boundary_nets"] = []
        for bn in record.boundary_nets:
            row: Dict[str, Any] = {"net": bn.net, "action": bn.action}
            if bn.external_ref:
                row["external_ref"] = bn.external_ref
            d["boundary_nets"].append(row)
    return d


def default_scheme_list_path(root_path: Path) -> Path:
    """The fixed storage file for NEW Scheme List records (plan §0.8) — a
    ``scheme_lists.json`` sitting NEXT TO the main profile, auto-included on
    first Record... (records can be large — real copper, not a parametric
    template — so they never bloat the hand-readable root profile)."""
    return Path(root_path).parent / "scheme_lists.json"


def ensure_scheme_list_storage(root_path: Path) -> Path:
    """Make the default ``scheme_lists.json`` writable: create it when absent
    and wire ``include: [scheme_lists.json]`` into the ROOT profile (add_include
    is idempotent — a re-enabled/again-included file returns without a
    duplicate line). Returns the storage path."""
    json_path = default_scheme_list_path(root_path)
    if not json_path.exists():
        json_path.write_text("{}\n", encoding="utf-8")
    add_include(Path(root_path), "scheme_lists.json")
    return json_path


def read_scheme_list_records(root_path: Path) -> List[Dict[str, Any]]:
    """Every raw ``scheme_lists:`` record across the whole include: graph
    rooted at root_path (read through config_writer.read_data, so a staged
    working-set write is visible too). Empty list on any load failure — this
    is a pre-write duplicate check, not validation."""
    try:
        records: List[Dict[str, Any]] = []
        for path in collect_graph_files(Path(root_path)):
            for e in read_data(path).get("scheme_lists") or []:
                if isinstance(e, dict):
                    records.append(e)
        return records
    except (ValidationError, OSError):
        return []


def scheme_list_duplicate_problems(root_path: Path, name: str, refs: list,
                                   *, exclude_name: Optional[str] = None) -> List[str]:
    """Cross-record pre-checks the loader would otherwise surface at the next
    load (plan §2 — the "Record..." action checks BEFORE capture, so an
    expensive board read is never wasted on a record that cannot be saved):
      - duplicate ``name`` across scheme_lists: entries;
      - a ref already recorded in ANOTHER Scheme List (ref-uniqueness §0.2).
    ``exclude_name`` (Re-source, plan_2026_09_06_scheme_list_sheet_capture.md
    5b.1): the record being re-sourced is SKIPPED entirely — its own name is
    not a duplicate of itself and its own refs are being REPLACED (replace,
    not conflict), so they must not count as "used by another record".
    Existing Record... callers pass no exclude_name -> None -> behavior
    unchanged. Returns localized problem strings; empty when clean."""
    used_names = set()
    used_refs: set = set()
    for e in read_scheme_list_records(root_path):
        if exclude_name is not None and e.get("name") == exclude_name:
            continue  # the record itself is being replaced, not duplicated
        used_names.add(e.get("name"))
        for c in e.get("components") or []:
            if isinstance(c, dict) and c.get("ref"):
                used_refs.add(c["ref"])
    problems: List[str] = []
    if name in used_names:
        problems.append(
            _("a Scheme List named {name!r} already exists — pick another name")
            .format(name=name))
    overlap = sorted(set(refs) & used_refs)
    if overlap:
        problems.append(
            _("component ref(s) already recorded in another Scheme List: {refs}")
            .format(refs=", ".join(overlap)))
    return problems


def write_scheme_list_record(root_path: Path, record: SchemeListConfig,
                             target_path: Optional[Path] = None) -> Path:
    """Persist one Scheme List record. Without ``target_path`` the record is
    written to the default ``scheme_lists.json`` (created + auto-included on
    first use); with it (Reread Apply — the file that actually owns the
    loaded record) the record is upserted there by name. Returns the written
    file. Pure file operation — callable from the UI thread or a worker."""
    if target_path is None:
        target_path = ensure_scheme_list_storage(root_path)
    upsert_list_entry(Path(target_path), "scheme_lists",
                      scheme_list_to_dict(record), key="name")
    return Path(target_path)


# ── Pure live-pivot helpers (Commit B2 / H) ────────────────────────────────
#
# The "Take from selection" source on the record page: translate the CENTRE of
# the CURRENT live board selection into the record's centre-frame (a pivot the
# user can then Apply/save). The record stores offsets FROM the region centre,
# but never that centre itself, so to move a live-board point into the
# centre-frame we need the centre of the recorded region ON THE LIVE BOARD,
# recomputed deterministically from the recorded refs' PRESENT positions (the
# same _region_centre formula capture uses) — a pivot computed against the
# current live centre stays consistent with how Reread/Apply re-centre the
# region on the next live read (plan_2026_09_07_scheme_list_pivot_commit_b2.md
# §1). Qt-free and side-effect-free so the math is unit-testable without a dock.
#
# Commit H (plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_fix.md): the
# helpers read the recorded refs' PRESENT POSITIONS from the full-board
# footprint SNAPSHOT (BoardConnection.snapshot — a list of Selected, each with
# .ref and the raw .fp handle), NOT by a fresh adapter.get_footprints() IPC
# call. The kipy REQ socket allows exactly ONE request in flight for the whole
# app (gui/connection.py); under a modal dialog's nested event loop the main
# window's poll ticks keep running on that same socket, so a click handler
# firing a second, unsynchronized adapter.get_footprints() collided with them
# and hung the "Take from selection" dialog. The snapshot is the same cache
# record_scheme_list/"By sheet" already build from, so zero extra IPC and zero
# race.

def live_record_centre_mm(record_refs: list, footprints) -> tuple[float, float] | None:
    """The centre of the recorded region ON THE LIVE BOARD (mm): the midpoint
    of the position extents of the PRESENT recorded footprints (the same
    _region_centre formula capture uses) — to translate a live-board point into
    the record's centre-frame consistently with Reread/Apply. None when NONE of
    the recorded refs is on the board (edge 1) — the centre cannot be computed;
    we never guess (the same never-guess discipline as the other live readers
    in gui/docks/live_position.py).

    `footprints` is the full-board footprint SNAPSHOT (Iterable[Selected] —
    BoardConnection.snapshot), NOT a live adapter: each Selected carries .ref
    and the raw .fp handle whose .position feeds _region_centre. Reading from
    the snapshot instead of a direct adapter call is what keeps this off the
    shared kipy REQ socket (see the section comment above)."""
    live_by_ref = {s.ref: s.fp for s in footprints
                   if getattr(s, "ref", None) is not None
                   and getattr(s, "fp", None) is not None}
    present = [live_by_ref[r] for r in record_refs if r in live_by_ref]
    if not present:
        return None
    centre_nm = _region_centre(present)  # nm Vector2, (min+max)/2 per axis
    return (centre_nm.x / MM, centre_nm.y / MM)


def missing_record_refs(record_refs: list, footprints) -> list:
    """Recorded refs that are NOT on the live board (edge 2: "not all recorded
    components are on the board") — for a UI warning. Empty when every recorded
    ref is present. Reads the refs from the footprint SNAPSHOT (see
    live_record_centre_mm — never a direct adapter IPC call)."""
    live_refs = {s.ref for s in footprints
                 if getattr(s, "ref", None) is not None}
    return sorted(r for r in record_refs if r not in live_refs)


def pivot_centre_frame_from_selection(record_refs: list, footprints, selected
                                      ) -> tuple[float, float]:
    """The pivot (mm, in the record's centre-frame) that puts the CENTRE of the
    CURRENT live selection at the record's origin on a Redraw:
    selected_center_mm(selected) minus the LIVE centre of the recorded region
    (live_record_centre_mm). `footprints` is the full-board footprint SNAPSHOT
    (see live_record_centre_mm for WHY the snapshot and not the adapter: the
    shared kipy REQ socket allows one in-flight request, so a click handler
    running under the modal dialog's nested event loop must never issue a
    second, unsynchronized adapter.get_footprints() while a poll tick is
    mid-flight on the same socket). Fatal-like cases are a ValidationError
    with a clear message (the same never-guess discipline as the other live
    readers in gui/docks/live_position.py):
      - none of the recorded refs is on the board (edge 1 — centre unknown);
      - the selection is empty / has no positions (edge 3)."""
    centre_mm = live_record_centre_mm(record_refs, footprints)
    if centre_mm is None:
        raise ValidationError(
            _("none of the recorded components is on the board — cannot "
              "compute the region centre"))
    sel_mm = selected_center_mm(selected)
    if sel_mm is None:
        raise ValidationError(
            _("select footprints on the board first — 'Take from selection' "
              "needs their positions"))
    return (sel_mm[0] - centre_mm[0], sel_mm[1] - centre_mm[1])


# ── Diff dialog text ────────────────────────────────────────────────────────

def _fmt(v: float) -> str:
    """Compact mm/deg formatting for the diff dialog (0.1500 -> 0.15)."""
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _pivot_mm_text(v: float) -> str:
    """Compact mm formatting for the pivot x/y fields, the same trim `_fmt`
    uses (0.0 -> "0", -7.0 -> "-7", 1.23456 -> "1.2346") — a value filled from
    a live board read round-trips back through float() unchanged enough for the
    Apply/round-trip tests' approx comparisons (Commit B2)."""
    return f"{v:.4f}".rstrip("0").rstrip(".")


def scheme_list_diff_lines(diff: SchemeListDiff) -> List[str]:
    """Human-readable (already localized) summary lines of a SchemeListDiff —
    shared by the diff dialog and any future log-only consumer."""
    lines: List[str] = []
    if diff.refs_not_found:
        lines.append(
            _("component(s) no longer on the board: {refs}")
            .format(refs=", ".join(diff.refs_not_found)))
    if diff.components_added:
        lines.append(
            _("component(s) added to the scope: {refs}")
            .format(refs=", ".join(c.ref for c in diff.components_added)))
    if diff.refs_removed_from_scope:
        lines.append(
            _("component(s) removed from the scope: {refs}")
            .format(refs=", ".join(diff.refs_removed_from_scope)))
    for c in diff.components_moved:
        lines.append(
            _("component {ref!r} moved: ({old_x}, {old_y}) mm, {old_rot} deg -> "
              "({new_x}, {new_y}) mm, {new_rot} deg").format(
                ref=c.ref,
                old_x=_fmt(c.old_offset_along_mm), old_y=_fmt(c.old_offset_across_mm),
                old_rot=_fmt(c.old_rotation_deg),
                new_x=_fmt(c.new_offset_along_mm), new_y=_fmt(c.new_offset_across_mm),
                new_rot=_fmt(c.new_rotation_deg)))
    if diff.vias_added:
        lines.append(_("vias added: {n}").format(n=len(diff.vias_added)))
    if diff.vias_removed:
        lines.append(_("vias removed: {n}").format(n=len(diff.vias_removed)))
    if diff.tracks_added:
        lines.append(_("tracks added: {n}").format(n=len(diff.tracks_added)))
    if diff.tracks_removed:
        lines.append(_("tracks removed: {n}").format(n=len(diff.tracks_removed)))
    if diff.boundary_nets_added:
        lines.append(_("new boundary net(s): {nets}").format(
            nets=", ".join(diff.boundary_nets_added)))
    if diff.boundary_nets_gone:
        lines.append(_("boundary net(s) gone: {nets}").format(
            nets=", ".join(diff.boundary_nets_gone)))
    return lines


# ── Dialogs ────────────────────────────────────────────────────────────────

class RecordSchemeListDialog(QDialog):
    """Record... / Re-source... dialog with TWO source tabs (design §2, plan
    plan_2026_09_06_scheme_list_sheet_capture.md 5a.3 — the same two-tab
    pattern "Instantiate from Cell..." already uses):

      - "By sheet" (DEFAULT tab) — the WHOLE live hierarchy as ONE tree
        (QTreeWidget, Commit E): every real sheet is a CHECKABLE node (top
        sheets like Channel_0/1/2 first, nested under their parents down to
        DAC/OpAmp...), container sheets with no footprints of their own
        included. ALL start UNCHECKED — tick a sheet (its whole subtree
        follows via the Commit D tri-state cascade: checking Channel_0 turns on
        DAC/OpAmp, a parent with a mixed subtree shows PartiallyChecked) to
        record it. A sheet is captured (its DIRECT refs included) whenever it
        is NOT unchecked; container paths are stored in the scope too (they add
        no refs today but keep a future Reread aware of the branch).
      - "By selection" (secondary tab) — the pre-existing P2 behavior: the
        CURRENT board selection, unchanged, for irregular cases.

    NO anchor is picked at Record time (design_2026_09_07_scheme_list_pivot.md):
    the record's frame is the CENTRE of the captured region's bbox and its
    pivot defaults to that centre — there is no anchor component to choose.
    The shared name_edit sits OUTSIDE the tabs. This dialog only reports what
    was picked; the caller (DockHub) derives the actual capture refs via
    record_refs_for().

    Re-source mode (``fixed_name``, plan_2026_09_06_scheme_list_sheet_capture.md
    5b.2): the SAME dialog is reused to RE-SOURCE an existing record — the name
    is pinned (read-only) to the record being replaced, the title/OK label say
    "Re-source" and an explicit in-dialog warning explains the "points at, does
    not copy" consequence (the record's refs/geometry are replaced; entities
    placed from it pick the new geometry on their next Apply/Redraw). Both
    source tabs stay available — re-sourcing can come from either mode."""

    def __init__(self, snapshot: list, selection_refs: List[str], parent=None,
                 fixed_name: Optional[str] = None, *,
                 adapter=None, selected_footprints=None, pivot_initial=None,
                 selection_provider=None, snapshot_provider=None,
                 connection=None):
        super().__init__(parent)
        self._fixed_name = fixed_name
        # Pivot/Anchor tab context (Commit F): the live adapter + current board
        # selection feed "Take from selection"; pivot_initial prefills the tab
        # (Re-source = the stored record's pivot, Record = None -> (0,0) centre).
        # `adapter` is kept ONLY as a "live board connected" gate (button enable
        # + the Connect-first warning) — position data comes from the snapshot,
        # never from a direct adapter.get_footprints() call (Commit H).
        self._adapter = adapter
        self._selected_footprints = list(selected_footprints or [])
        # Commit G: the modal dialog outlives the selection it was opened with,
        # so "Take from selection" must read the CURRENT board selection at
        # click time, not the open-time snapshot. selection_provider is a
        # callable returning the live selected footprints (DockHub's polled
        # copy — kept fresh by the main window's selection timer even under the
        # dialog's nested event loop). None -> fall back to the static copy.
        self._selection_provider = selection_provider
        # Commit H: the recorded refs' POSITIONS must be read the same way —
        # from the full-board footprint SNAPSHOT, and LIVE at click time (the
        # open-time `snapshot` argument is stale once the board has moved under
        # the modal dialog). snapshot_provider returns the current snapshot
        # (DockHub's connection.snapshot, refreshed by the poll timer); None ->
        # fall back to the constructor snapshot (tests/fallback).
        self._snapshot_provider = snapshot_provider
        # R.2.1 (2026-09-11, plan_2026_09_11_stale_snapshot_positions.md): the
        # snapshot_provider above can only ever return a snapshot built BEFORE
        # this modal dialog opened — the main window's own Refresh button is
        # blocked while exec() runs, and the automatic poll tick is a no-op once
        # connected — so its positions are frozen at connect/manual-refresh
        # time. "Take from selection" therefore REBUILDS the snapshot first, on
        # the worker thread, before reading any position out of it. None (or a
        # connection without a live board) keeps the cached snapshot: the
        # documented provider fallback, same as _live_snapshot.
        self._connection = connection
        self._pivot_op: Optional[Any] = None
        self._pivot_initial = (tuple(pivot_initial) if pivot_initial is not None
                               else (0.0, 0.0))
        if fixed_name:
            self.setWindowTitle(
                _("Re-source Scheme List {name!r}").format(name=fixed_name))
        else:
            self.setWindowTitle(_("Record Scheme List"))
        self._snapshot = list(snapshot or [])
        # FULL live hierarchy — every real sheet incl. containers without own
        # footprints (all_sheet_paths adds the prefixes), so the "By sheet"
        # tree reflects the schematic instead of only footprint-bearing leaves
        # (Commit E, plan_2026_09_07_scheme_list_by_sheet_full_tree.md).
        self._sheet_paths = all_sheet_paths(self._snapshot)
        self._selection_refs = list(selection_refs or [])
        # Reentrancy guard for the tri-state cascade (Commit D): our own
        # setCheckState calls re-enter itemChanged, so they are no-ops while a
        # cascade is already running.
        self._cascading = False
        # The SOURCE tab (0="By sheet", 1="By selection"), tracked separately
        # from the widget's currentIndex(): the Pivot/Anchor tab (index 2) is
        # NOT a source — visiting it must not silently flip is_by_sheet()'s
        # answer (plan_2026_09_08_scheme_list_pivot_tab_source_tracking_fix.md).
        # Default "By sheet" (0) matches the tab the dialog opens on.
        self._source_tab_index: int = 0

        layout = QVBoxLayout(self)
        name_form = QFormLayout()
        if fixed_name:
            # Re-source: the name is pinned — this is not a new record, it is
            # the same record being re-pointed at a different source.
            self.name_edit = QLineEdit(fixed_name)
            self.name_edit.setReadOnly(True)
            name_form.addRow(_("Name:"), self.name_edit)
            layout.addLayout(name_form)
            warn = QLabel(
                _("This replaces the recorded refs/geometry of {name!r} — "
                  "entities placed from it will pick up the new geometry on "
                  "their next Apply/Redraw. The sheet/selection it currently "
                  "comes from will NOT update automatically — Place onto it "
                  "too if it should follow.").format(name=fixed_name))
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #a60;")
            layout.addWidget(warn)
        else:
            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText(
                _("name (used by --only and Entity.scheme_list, must be unique)"))
            name_form.addRow(_("Name:"), self.name_edit)
            layout.addLayout(name_form)

        # Two source tabs — "By sheet" is added/selected FIRST (the default).
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1 — "By sheet": the WHOLE sheet hierarchy as one tree (Commit E).
        tab1 = QWidget()
        tab1_form = QFormLayout(tab1)
        # Every real sheet of the live hierarchy is a checkable node (containers
        # without own footprints included). ALL start UNCHECKED — you tick a
        # sheet to record it; its whole subtree follows via the Commit D
        # tri-state cascade. Captured = DIRECT refs of every sheet that is not
        # unchecked; container paths are stored in the scope too.
        self.sheet_tree = QTreeWidget()
        self.sheet_tree.setHeaderHidden(True)
        tab1_form.addRow(_("Sheets (check what to record):"), self.sheet_tree)
        # Optional "Save as preset" (plan_2026_09_06_scheme_list_named_presets.md
        # §6): the CURRENT checked tree becomes a NAMED preset saved in the
        # record. Fully optional — empty text = nothing saved, zero effect on
        # OK-gating or the 5a/5b result_data contract.
        self.save_preset_edit = QLineEdit()
        self.save_preset_edit.setPlaceholderText(
            _("optional — save this checklist as a named preset (same name overwrites it)"))
        tab1_form.addRow(_("Save as preset:"), self.save_preset_edit)
        # Tri-state cascade (Commit D): a sheet click toggles its whole subtree
        # and ancestors re-sync; _sync_ok_state re-gates OK on every change.
        self.sheet_tree.itemChanged.connect(self._on_sheet_item_changed)
        self.sheet_tree.itemChanged.connect(self._sync_ok_state)
        self.tabs.addTab(tab1, _("By sheet"))

        # Tab 2 — "By selection": the current board selection (unchanged).
        # Read-only summary (no anchor pick — the capture frame is the region
        # centre, pivot defaults to the centre).
        tab2 = QWidget()
        tab2_form = QFormLayout(tab2)
        self.selection_refs_label = QLabel(self._selection_summary_text())
        self.selection_refs_label.setWordWrap(True)
        tab2_form.addRow(_("Selection:"), self.selection_refs_label)
        self.tabs.addTab(tab2, _("By selection"))

        # Tab 3 — "Pivot/Anchor": the record's pivot set AT CREATION (Commit F,
        # plan_2026_09_07_scheme_list_commit_f_pivot_tab_in_record.md). x/y are
        # mm in the record's centre-frame, default (0,0) = the region centre;
        # "Centre" writes 0/0, "Take from selection" reads the live board.
        # There is NO Apply here — the dialog OK (Record/Re-source) is the save
        # and stores these fields as the new record's pivot.
        tab3 = QWidget()
        tab3_form = QFormLayout(tab3)
        self.pivot_x_edit = QLineEdit()
        self.pivot_x_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.pivot_x_edit.setFixedWidth(110)
        self.pivot_y_edit = QLineEdit()
        self.pivot_y_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.pivot_y_edit.setFixedWidth(110)
        self._set_pivot_fields(*self._pivot_initial)
        pivot_xy_row = QHBoxLayout()
        pivot_xy_row.setSpacing(4)
        pivot_xy_row.addWidget(QLabel("x"))
        pivot_xy_row.addWidget(self.pivot_x_edit)
        pivot_xy_row.addWidget(QLabel("y"))
        pivot_xy_row.addWidget(self.pivot_y_edit)
        pivot_xy_row.addStretch(1)
        self.pivot_hint_label = QLabel(
            _("(0, 0) = the region centre — x/y are mm offsets from it."))
        self.pivot_hint_label.setWordWrap(True)
        pivot_editor_lay = QVBoxLayout()
        pivot_editor_lay.setContentsMargins(0, 0, 0, 0)
        pivot_editor_lay.setSpacing(2)
        pivot_editor_lay.addLayout(pivot_xy_row)
        pivot_editor_lay.addWidget(self.pivot_hint_label)
        pivot_btn_row = QHBoxLayout()
        pivot_btn_row.setSpacing(4)
        self.pivot_centre_button = QPushButton(_("Centre"))
        self.pivot_centre_button.clicked.connect(self._on_pivot_centre)
        pivot_btn_row.addWidget(self.pivot_centre_button)
        self.pivot_from_selection_button = QPushButton(_("Take from selection"))
        self.pivot_from_selection_button.clicked.connect(
            self._on_pivot_from_selection)
        self.pivot_from_selection_button.setEnabled(self._adapter is not None)
        pivot_btn_row.addWidget(self.pivot_from_selection_button)
        pivot_btn_row.addStretch(1)
        pivot_editor_lay.addLayout(pivot_btn_row)
        pivot_widget = QWidget()
        pivot_widget.setLayout(pivot_editor_lay)
        tab3_form.addRow(_("Pivot:"), pivot_widget)
        self.tabs.addTab(tab3, _("Pivot / Anchor"))
        self.pivot_x_edit.textChanged.connect(self._sync_ok_state)
        self.pivot_y_edit.textChanged.connect(self._sync_ok_state)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel, self)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if fixed_name:
            # Explicit irreversibility: the OK label says what will happen.
            self._ok_button.setText(_("Re-source"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # OK is gated on a non-empty capturable ref set of the ACTIVE tab.
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._rebuild_sheet_tree()  # build the full hierarchy tree
        self._sync_ok_state()

    # ── helpers ─────────────────────────────────────────────────────────

    def _selection_summary_text(self) -> str:
        """Read-only summary of the "By selection" tab: the current board
        selection refs, or a note when there is none."""
        if self._selection_refs:
            return _("current board selection ({n}): {refs}").format(
                n=len(self._selection_refs), refs=", ".join(self._selection_refs))
        return _("nothing is selected on the board")

    # ── Pivot / Anchor tab (Commit F) ─────────────────────────────────────

    def _set_pivot_fields(self, x: float, y: float) -> None:
        """Write the x/y edits (mm, centre-frame)."""
        self.pivot_x_edit.setText(_pivot_mm_text(x))
        self.pivot_y_edit.setText(_pivot_mm_text(y))

    def pivot_value(self) -> tuple[float, float]:
        """(x, y) mm of the Pivot/Anchor tab in the record's centre-frame — the
        pivot the OK'd record stores (Commit F). Invalid input -> ValidationError
        (pattern: never write a malformed pivot)."""
        try:
            x = float(self.pivot_x_edit.text().strip())
            y = float(self.pivot_y_edit.text().strip())
        except ValueError:
            raise ValidationError(_("Pivot x/y must be numbers (mm).")) from None
        return (x, y)

    def _pivot_fields_ok(self) -> bool:
        """True when the Pivot/Anchor tab holds valid numbers (used to gate OK
        on top of a non-empty capturable ref set)."""
        try:
            self.pivot_value()
        except ValidationError:
            return False
        return True

    def _live_selection(self) -> list:
        """The selected footprints "Take from selection" should use: the LIVE
        board selection read at click time (selection_provider — Commit G), or
        the open-time snapshot when no provider was given (tests/fallback)."""
        if self._selection_provider is not None:
            return list(self._selection_provider())
        return list(self._selected_footprints)

    def _live_snapshot(self) -> list:
        """The full-board footprint snapshot "Take from selection" reads the
        recorded refs' POSITIONS from: the LIVE snapshot at click time
        (snapshot_provider — Commit H), or the open-time snapshot when no
        provider was given (tests/fallback). Reading positions from this cache
        (not the adapter) keeps the click off the shared kipy REQ socket — see
        live_record_centre_mm's docstring."""
        if self._snapshot_provider is not None:
            return list(self._snapshot_provider())
        return list(self._snapshot)

    def _on_pivot_centre(self) -> None:
        """'Centre' — write the centre default 0/0 into the x/y fields."""
        self._set_pivot_fields(0.0, 0.0)

    def _on_pivot_from_selection(self) -> None:
        """'Take from selection' — read the CURRENT board selection's centre and
        write x/y as the pivot in the centre-frame of the refs we would record
        now (selected centre minus the live centre of those refs' footprints,
        Commit B2 helpers). Needs a live board; the selection is read LIVE at
        click time (Commit G), and the recorded refs' POSITIONS are read from a
        freshly rebuilt full-board snapshot (R.2.1,
        plan_2026_09_11_stale_snapshot_positions.md): the rebuild runs on the
        worker thread first, so no adapter call happens on this GUI thread
        (Commit H — see live_record_centre_mm). Without a refreshable
        connection the cached snapshot is used as before (tests/fallback)."""
        if self._adapter is None:
            QMessageBox.warning(self, _("Scheme Lists"),
                                _("Connect to KiCad first."))
            return
        refs = self._checked_refs()
        if not refs:
            QMessageBox.warning(
                self, _("Scheme Lists"),
                _("No footprints to record — pick a sheet that has footprints "
                  "on the 'By sheet' tab, or select footprints on the board "
                  "for 'By selection'."))
            return
        self._pivot_op = refresh_snapshot_then(
            self._connection, (self.pivot_from_selection_button,),
            lambda: self._pivot_from_selection_now(refs),
            self._on_pivot_snapshot_refresh_failed)

    def _pivot_from_selection_now(self, refs: List[str]) -> None:
        """UI thread, AFTER the snapshot rebuild (see
        _on_pivot_from_selection): compute the pivot and fill the x/y fields.
        Without a refreshable connection this is the old synchronous
        cached-snapshot path (tests/fallback)."""
        try:
            x, y = pivot_centre_frame_from_selection(
                refs, self._live_snapshot(), self._live_selection())
        except ValidationError as e:
            QMessageBox.warning(self, _("Scheme Lists"), str(e))
            return
        self._set_pivot_fields(x, y)

    def _on_pivot_snapshot_refresh_failed(self, message: str) -> None:
        """UI thread: the worker could not rebuild the snapshot (the live board
        is gone — BoardConnection.refresh() drops the connection). Say so
        instead of silently computing the pivot from stale coordinates."""
        QMessageBox.warning(
            self, _("Scheme Lists"),
            _("Could not refresh the board snapshot: {error}").format(
                error=message))

    # ── "By sheet" helpers ──────────────────────────────────────────────

    def _rebuild_sheet_tree(self) -> None:
        """(Re)build the FULL-hierarchy "By sheet" tree (Commit E): one subtree
        per TOP-LEVEL sheet (Channel_0/1/2, ...), every real sheet a checkable
        node, ALL UNCHECKED by default — you tick what to record, and checking
        a node turns its whole subtree on via the Commit D cascade (a parent
        with a mixed subtree shows PartiallyChecked and is still read)."""
        paths = self._sheet_paths
        self.sheet_tree.blockSignals(True)
        self.sheet_tree.clear()
        if paths:
            tops = sorted({p[:1] for p in paths if p})
            for top in tops:
                under = [p for p in paths if p[:1] == top]
                plan = sheet_subtree_plan(top, under)
                self._add_tree_nodes(self.sheet_tree.invisibleRootItem(), plan)
        self.sheet_tree.expandAll()
        self.sheet_tree.setVisible(True)
        self.sheet_tree.blockSignals(False)
        self._sync_ok_state()

    def _add_tree_nodes(self, parent_item, nodes: list) -> None:
        """Recursively append `nodes` (sheet_subtree_plan output) under
        `parent_item`. Every node is a REAL sheet of the hierarchy and is
        CHECKABLE — UserRole = the full path (a tuple, so _checked_sheet_paths
        keeps returning the same tuple shape the tests compare against),
        tooltip = the "/"-joined path, ItemIsUserTristate lets the Commit D
        cascade show PartiallyChecked parents. ALL start UNCHECKED — you tick
        what to record (Commit E)."""
        for node in nodes:
            path = node["path"]
            item = QTreeWidgetItem(parent_item, [node["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, tuple(path))
            item.setToolTip(0, "/".join(path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                          | Qt.ItemFlag.ItemIsUserTristate)
            item.setCheckState(0, Qt.CheckState.Unchecked)  # tick to record
            self._add_tree_nodes(item, node["children"])

    def _on_sheet_item_changed(self, item, column) -> None:
        """Tri-state cascade (Commit D, plan_2026_09_07_scheme_list_commit_d_
        tristate_parent.md §3): a change to a checkable sheet row turns its
        WHOLE subtree on/off and re-syncs every ancestor (PartiallyChecked when
        some but not all sheets under it are on). Reentrancy-guarded by
        `self._cascading` — our own setCheckState calls re-enter this slot."""
        if self._cascading:
            return
        if column != 0 or item.data(0, Qt.ItemDataRole.UserRole) is None:
            return  # not a sheet row's checkbox
        on = item.checkState(0) != Qt.CheckState.Unchecked
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self._cascading = True
        try:
            # A tristate click on an OFF row lands on PartiallyChecked first —
            # normalize it to Checked so "tick to record" is a single click.
            item.setCheckState(0, state)
            self._set_descendant_states(item, state)
            self._sync_ancestors(item)
        finally:
            self._cascading = False

    def _set_descendant_states(self, parent_item, state) -> None:
        """Set `state` on every checkable sheet UNDER `parent_item`. Structural
        branches carry no checkbox and are skipped, but the walk passes through
        them so deeper sheets are reached."""
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            if child.data(0, Qt.ItemDataRole.UserRole) is not None:
                child.setCheckState(0, state)
            self._set_descendant_states(child, state)

    def _sync_ancestors(self, item) -> None:
        """Recompute the check state of every checkable ancestor of `item` from
        its whole subtree (Checked when every sheet under it is on, Unchecked
        when none is, otherwise PartiallyChecked). Structural ancestors are
        skipped (no checkbox) but the climb continues past them."""
        parent = item.parent()
        while parent is not None:
            if parent.data(0, Qt.ItemDataRole.UserRole) is not None:
                parent.setCheckState(0, self._branch_state(parent))
            parent = parent.parent()

    @staticmethod
    def _branch_state(item) -> Qt.CheckState:
        """What a sheet row's checkbox should show given `item` and its whole
        subtree of checkable sheets: Checked when every one is on, Unchecked
        when none is, PartiallyChecked when mixed."""
        states = set()

        def _collect(parent) -> None:
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.data(0, Qt.ItemDataRole.UserRole) is not None:
                    states.add(child.checkState(0))
                _collect(child)

        _collect(item)
        if item.data(0, Qt.ItemDataRole.UserRole) is not None:
            states.add(item.checkState(0))
        if not states or states <= {Qt.CheckState.Unchecked}:
            return Qt.CheckState.Unchecked
        if states <= {Qt.CheckState.Checked}:
            return Qt.CheckState.Checked
        return Qt.CheckState.PartiallyChecked

    def _checked_sheet_paths(self) -> List[Any]:
        """The sheet paths actually included — the single source of truth for
        "what is really read" (Commit D/E rule): every checkable node that is
        NOT Unchecked (Checked OR PartiallyChecked) contributes its DIRECT refs
        — a parent with some sub-sheets excluded is still read. Container
        sheets (no own footprints) are stored too: they add no refs today but
        keep a future Reread aware of the branch."""
        out: List[Any] = []

        def _walk(parent_item) -> None:
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                path = child.data(0, Qt.ItemDataRole.UserRole)
                if path is not None and child.checkState(0) != Qt.CheckState.Unchecked:
                    out.append(path)
                _walk(child)

        _walk(self.sheet_tree.invisibleRootItem())
        return out

    def _checked_refs(self) -> List[str]:
        """The refs the ACTIVE tab would capture — direct refs of the CHECKED
        paths ("By sheet") or the caller's selection_refs ("By selection").
        Used ONLY to gate OK on a non-empty capturable set; the caller
        re-derives the final capture refs from result_data via
        record_refs_for()."""
        if self.is_by_sheet():
            return sorted({r for p in self._checked_sheet_paths()
                           for r in refs_on_sheet(self._snapshot, p)})
        return sorted(set(self._selection_refs))

    def _sync_ok_state(self) -> None:
        """OK needs a capturable set AND a valid Pivot/Anchor tab: the ACTIVE
        SOURCE tab (By sheet / By selection — NOT the Pivot tab, index 2) must
        yield at least one ref (an empty board selection / all-unchecked sheet
        leaves nothing to record) and the pivot x/y must be numbers (a malformed
        pivot must never reach the record)."""
        ok = bool(self._checked_refs()) and self._pivot_fields_ok()
        self._ok_button.setEnabled(ok)

    def _on_tab_changed(self, index: int) -> None:
        """Track which SOURCE tab (0=By sheet, 1=By selection) is active,
        ignoring visits to the Pivot/Anchor tab (index 2) — it is not a source,
        switching to it must not silently change is_by_sheet()'s answer
        (plan_2026_09_08_scheme_list_pivot_tab_source_tracking_fix.md §0)."""
        if index in (0, 1):
            self._source_tab_index = index
        self._sync_ok_state()

    def is_by_sheet(self) -> bool:
        return self._source_tab_index == 0

    def result_data(self):
        """(name, sheet_path_or_None, checked_paths_or_None).
        sheet_path is ALWAYS None (Commit E has no root combobox); the caller
        derives the capture refs itself (record_refs_for) from the CHECKED
        paths for "By sheet", or uses its OWN selection_refs for "By selection"
        (this dialog does not own that list)."""
        name = self.name_edit.text().strip()
        if self.is_by_sheet():
            return (name, None, self._checked_sheet_paths())
        return (name, None, None)

    def preset_name_to_save(self) -> Optional[str]:
        """Non-empty text of the optional "Save as preset" field on the "By
        sheet" tab, or None (nothing to save — the default). Meaningless on
        "By selection" (scope_sheet_paths/scope_presets stay [] there)."""
        if not self.is_by_sheet():
            return None
        text = self.save_preset_edit.text().strip()
        return text or None


class SchemeListDiffDialog(QDialog):
    """Reread result (design §4): shows what changed against the live board
    and offers Apply (rewrite the stored record) — never applied silently.
    Apply is disabled when a recorded component is missing from the board
    (the record cannot be faithfully re-synced while a ref is off-board)."""

    def __init__(self, name: str, diff: SchemeListDiff, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Scheme List Reread"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            _("What changed for {name!r} on the live board:").format(name=name)))
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n".join(scheme_list_diff_lines(diff)) or _("no differences"))
        layout.addWidget(text)
        if diff.refs_not_found:
            warn = QLabel(_("Apply is disabled while component(s) are missing from the "
                            "board — restore them and Reread again."))
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #a60;")
            layout.addWidget(warn)
        buttons = QDialogButtonBox()
        apply_button = QPushButton(_("Apply"))
        apply_button.setEnabled(not diff.refs_not_found)
        buttons.addButton(apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)


@dataclass
class BoundaryNetRow:
    """One PURE row of the per-net boundary dialog (G1, plan §5-G1) — the net
    plus the external component ref that dragged its copper to the capture
    boundary (diagnostics only). Carries NO decision; the dialog's widgets
    decide."""
    net: str
    external_ref: Optional[str]


def boundary_net_rows(boundary_nets: list) -> list:
    """Filter a capture's boundary-net list down to real
    ``SchemeListBoundaryNet`` rows (the capture always produces them, but a
    stray non-model entry must not crash the dialog) — the pure input the
    boundary dialog renders. Net + external_ref only; no decision lives
    here."""
    return [
        BoundaryNetRow(net=bn.net, external_ref=bn.external_ref)
        for bn in boundary_nets if isinstance(bn, SchemeListBoundaryNet)
    ]


class BoundaryNetDialog(QDialog):
    """Per-net boundary decision (G1, plan §5-G1; replaces the v1
    ``confirm_boundary_exclusions`` QMessageBox, which could only CONFIRM the
    all-exclude outcome). One row per boundary net: the net + external-ref
    diagnostics and an ``Exclude | Truncate`` combo, DEFAULT Exclude; an OK
    ("Record") + Cancel button row. This dialog ONLY collects the per-net
    choice — it never applies anything; applying the actions is the caller's
    job (the two-phase Record flow re-runs capture with the chosen actions in
    G2)."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Boundary nets"))
        self._combos: List[tuple] = []  # (net, QComboBox) in row order
        layout = QVBoxLayout(self)
        explain = QLabel(
            _("Choose how to treat each boundary net. Exclude drops the whole "
              "connected component; Truncate keeps only the copper inside the "
              "capture region."))
        explain.setWordWrap(True)
        layout.addWidget(explain)

        form = QFormLayout()
        for row in rows:
            if row.external_ref:
                net_label = _("{net} (touched by {external_ref})").format(
                    net=row.net, external_ref=row.external_ref)
            else:
                net_label = row.net
            combo = QComboBox()
            combo.addItem(_("Exclude"), "exclude")
            combo.addItem(_("Truncate"), "truncate")
            self._combos.append((row.net, combo))
            form.addRow(QLabel(net_label), combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        record_button = QPushButton(_("Record"))
        buttons.addButton(record_button,
                          QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_actions(self) -> Dict[str, str]:
        """Read the widgets — the per-net truncate choices ONLY. A net left on
        Exclude is omitted ({} = everything excluded); the capture (Stage 4)
        defaults any net not mentioned in the dict to exclude. Pure widget
        reading, no decision logic."""
        return {net: "truncate" for net, combo in self._combos
                if combo.currentData() == "truncate"}


def choose_boundary_actions(parent, boundary_nets: list) -> Optional[Dict[str, str]]:
    """Per-net boundary decision (G1, plan §5-G1) — replaces the v1
    ``confirm_boundary_exclusions(...) -> bool``:
      - ``None`` — user pressed Cancel (nothing is written — as v1 Cancel);
      - ``{}`` — OK with every net left on Exclude (v1 record-with-exclusions,
        byte-identical);
      - ``{net: "truncate", ...}`` — OK after choosing truncate for specific
        nets; every other boundary net stays exclude.
    Building the dialog rows (boundary_net_rows) and reading the widgets
    (BoundaryNetDialog.selected_actions) are separate pure steps so the
    decision logic stays testable without showing a modal."""
    rows = boundary_net_rows(boundary_nets)
    if not rows:
        return {}
    dialog = BoundaryNetDialog(rows, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected_actions()


# ── The Config right-page form ─────────────────────────────────────────────

class SchemeListFormWidget(QWidget):
    """A Config-tree right-QView page (plan §5.2 — embedded via DockHub's
    add_right_page on the ConfigTreeDock's QStackedWidget), the same "plain
    QWidget, not its own QDockWidget" shape as NetTraceDock/ThermalViaArrayDock.
    Record page + Reread; the pivot is the one EDITABLE field — Commit B1 made
    it editable (x/y + "Centre" + "Apply"), Commit B2 adds the live "Take from
    selection" source that fills x/y from the current board selection — and
    everything else is read-only (see module docstring — no Placement/Redraw)."""

    # Fired after a write that rewrote the stored record — either a Reread
    # Apply or a pivot "Apply" (Save pivot). ConfigTreeDock listens to refresh
    # (see gui/dock_hub.py).
    saved = pyqtSignal()

    def __init__(self, main_window, connection=None):
        super().__init__(main_window)
        self._main_window = main_window
        self._connection = connection if connection is not None else main_window.connection
        self._active_op: Optional[Any] = None
        self._root_path: Optional[Path] = None
        self._path: Optional[Path] = None
        self._entry: Dict[str, Any] = {}
        # The polled live board selection (fed by DockHub.set_board_selection,
        # 5c.4) — the Reread scope of a "By selection"-record is the CURRENT
        # selection at click time, so this dock needs the same tick the other
        # selection-aware docks get.
        self._selection_footprints: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.name_label = QLabel("")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)

        # Pivot — the record's anchor point in the centre-frame
        # (design_2026_09_07_scheme_list_pivot.md), default (0,0) = the region
        # centre. Commit A showed a read-only readout; Commit B1 makes the pivot
        # EDITABLE: two mm QLineEdits (x, y in the record's centre-frame) + a
        # "(0,0) = centre" hint + a "Centre" quick-set (writes 0.00/0.00 into
        # the fields) and an explicit "Apply" (Save pivot) that rewrites the
        # record's owning file. Commit B2 adds the "Take from selection" LIVE
        # source (pivot_from_selection_button): it reads the centre of the
        # CURRENT board selection and fills x/y with the pivot in the
        # centre-frame — a preview only, the explicit Apply still saves.
        # Commit F moves this block onto its own "Pivot / Anchor" TAB.
        self.pivot_x_edit = QLineEdit()
        self.pivot_x_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.pivot_x_edit.setFixedWidth(110)
        self.pivot_y_edit = QLineEdit()
        self.pivot_y_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.pivot_y_edit.setFixedWidth(110)
        pivot_editor = QWidget()
        pivot_editor_lay = QVBoxLayout(pivot_editor)
        pivot_editor_lay.setContentsMargins(0, 0, 0, 0)
        pivot_editor_lay.setSpacing(2)
        pivot_xy_row = QHBoxLayout()
        pivot_xy_row.setSpacing(4)
        pivot_xy_row.addWidget(QLabel("x"))
        pivot_xy_row.addWidget(self.pivot_x_edit)
        pivot_xy_row.addWidget(QLabel("y"))
        pivot_xy_row.addWidget(self.pivot_y_edit)
        pivot_xy_row.addStretch(1)
        pivot_editor_lay.addLayout(pivot_xy_row)
        self.pivot_hint_label = QLabel(
            _("(0, 0) = the region centre — x/y are mm offsets from it."))
        self.pivot_hint_label.setWordWrap(True)
        pivot_editor_lay.addWidget(self.pivot_hint_label)
        pivot_btn_row = QHBoxLayout()
        pivot_btn_row.setSpacing(4)
        # "Take from selection" (Commit B2) — a LIVE source for the pivot: reads
        # the centre of the current board selection and writes x/y as the pivot
        # in the record's centre-frame (preview; Apply still saves).
        self.pivot_from_selection_button = QPushButton(_("Take from selection"))
        self.pivot_from_selection_button.clicked.connect(
            self._on_pivot_from_selection)
        pivot_btn_row.addWidget(self.pivot_from_selection_button)
        self.pivot_centre_button = QPushButton(_("Centre"))
        self.pivot_centre_button.clicked.connect(self._on_pivot_centre)
        pivot_btn_row.addWidget(self.pivot_centre_button)
        self.pivot_apply_button = QPushButton(_("Apply"))
        self.pivot_apply_button.clicked.connect(self._on_pivot_apply)
        pivot_btn_row.addWidget(self.pivot_apply_button)
        pivot_btn_row.addStretch(1)
        pivot_editor_lay.addLayout(pivot_btn_row)

        # Two tabs (Commit F): "Record" (read-only summary + Reread) and
        # "Pivot / Anchor" (the pivot editor; Apply = Save pivot still writes
        # the record's owning file — the same config write as before, now on
        # its own tab).
        self.page_tabs = QTabWidget()
        record_page = QWidget()
        record_lay = QVBoxLayout(record_page)
        record_form = QFormLayout()
        self.source_sheet_label = QLabel("-")
        record_form.addRow(_("Source sheet:"), self.source_sheet_label)
        # Named-presets selector (plan_2026_09_06_scheme_list_named_presets.md
        # §8) — shown ONLY when the loaded record carries a scope_presets
        # library; the first item is the "(current)" sentinel (data None =
        # use stored.scope_sheet_paths as-is, 5c behavior unchanged).
        self.preset_combo = QComboBox()
        self.preset_combo.setVisible(False)
        record_form.addRow(_("Preset:"), self.preset_combo)
        self.geometry_label = QLabel("")
        self.geometry_label.setWordWrap(True)
        record_form.addRow(_("Recorded geometry:"), self.geometry_label)
        record_lay.addLayout(record_form)
        note = QLabel(
            _("Reread compares this record against the live board and, after "
              "an explicit Apply, rewrites it — it never places anything. "
              "Cloning a Scheme List onto another sheet happens through a "
              "tree Entity (scheme_list:), not here."))
        note.setWordWrap(True)
        record_lay.addWidget(note)
        buttons = QHBoxLayout()
        self.reread_button = QPushButton(_("Reread"))
        self.reread_button.clicked.connect(self._on_reread)
        buttons.addWidget(self.reread_button)
        record_lay.addLayout(buttons)
        record_lay.addStretch(1)
        self.page_tabs.addTab(record_page, _("Record summary"))
        self.page_tabs.addTab(pivot_editor, _("Pivot / Anchor"))
        layout.addWidget(self.page_tabs)
        layout.addStretch(1)

    # ── Message helper ──────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(text, style, logger)

    # ── Root / record wiring ────────────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        self._root_path = path

    def set_board_selection(self, items, selected) -> None:
        """Live board selection tick (DockHub.set_board_selection fan-out,
        5c.4) — the Reread scope of a "By selection"-record is the CURRENT
        board selection at click time, so the user re-selects the (possibly
        changed) set and THEN clicks Reread. `items` is unused (kept for the
        shared fan-out signature)."""
        self._selection_footprints = list(selected)

    def clear(self) -> None:
        """Blank the form (nothing loaded)."""
        self._entry = {}
        self._path = None
        self.name_label.setText("")
        self.pivot_x_edit.clear()
        self.pivot_y_edit.clear()
        self.source_sheet_label.setText("-")
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.blockSignals(False)
        self.preset_combo.setVisible(False)
        self.geometry_label.setText("")

    def load_entry(self, entry: Dict[str, Any],
                   file_path: Optional[Path] = None) -> None:
        """Config-tree scheme_lists leaf click (scheme_list_picked): populate
        the read-only form from the saved record. The WRITE target is set back
        to the file the record actually lives in, so a Reread Apply updates
        that file instead of adding a root/duplicate record (2026-08-21 review
        fix pattern, same as net_trace.load_entry)."""
        self._show_message("")
        if file_path is None:
            file_path = find_list_entry_file(self._root_path, "scheme_lists", entry)
        if file_path is not None:
            self._path = Path(file_path)
        self._entry = dict(entry)
        try:
            record = load_scheme_list(entry)
        except ValidationError as e:
            # A hand-broken record must not crash the form — show the record's
            # raw identity and log the problem (it will fatal at the next load).
            self._show_message(str(e), _ERROR_STYLE)
            self.name_label.setText(
                _("Scheme List: {name}").format(name=entry.get("name", "?")))
            self.pivot_x_edit.clear()
            self.pivot_y_edit.clear()
            return
        self._render(record)

    def _render(self, record: SchemeListConfig) -> None:
        self.name_label.setText(
            _("Scheme List: {name}").format(name=record.name))
        pivot = record.pivot if record.pivot is not None else (0.0, 0.0)
        self.pivot_x_edit.setText(f"{pivot[0]:.2f}")
        self.pivot_y_edit.setText(f"{pivot[1]:.2f}")
        self.source_sheet_label.setText(record.source_sheet or _("(root sheet)"))
        self._render_preset_combo(record)
        boundary_nets = [bn.net for bn in record.boundary_nets]
        if boundary_nets:
            geometry = _("{components} components, {vias} vias, {tracks} tracks, "
                         "boundary: {nets}").format(
                components=len(record.components), vias=len(record.vias),
                tracks=len(record.tracks), nets=", ".join(boundary_nets))
        else:
            geometry = _("{components} components, {vias} vias, {tracks} tracks").format(
                components=len(record.components), vias=len(record.vias),
                tracks=len(record.tracks))
        self.geometry_label.setText(geometry)

    def _render_preset_combo(self, record: SchemeListConfig) -> None:
        """Fill the named-presets selector from a loaded record (plan §8):
        the "(current)" sentinel first (data None), then one item per
        scope_presets entry carrying its sheet_paths as data. Hidden entirely
        when the record carries no presets."""
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        if record.scope_presets:
            self.preset_combo.addItem(_("(current)"), None)
            for p in record.scope_presets:
                self.preset_combo.addItem(p.name, [list(sp) for sp in p.sheet_paths])
            self.preset_combo.setCurrentIndex(0)  # sentinel selected by default
        self.preset_combo.setVisible(bool(record.scope_presets))
        self.preset_combo.blockSignals(False)

    # ── Pivot editing (Commit B1 + B2, plan_2026_09_07_scheme_list_pivot_commit_b) ──
    # The record's pivot is EDITABLE on this page: x/y mm QLineEdits in the
    # record's centre-frame + a "Centre" quick-set (0/0 = the region centre)
    # and an explicit "Apply" (Save pivot) that rewrites the record's owning
    # file (write_scheme_list_record) — a pure config write, no live board.
    # Commit B2 adds "Take from selection" (_on_pivot_from_selection): a LIVE
    # source that fills x/y from the current board selection's centre (pure
    # helpers pivot_centre_frame_from_selection / live_record_centre_mm,
    # plan_2026_09_07_scheme_list_pivot_commit_b2.md) — it only prefills the
    # fields, the explicit Apply still saves.

    def _on_pivot_centre(self) -> None:
        """'Centre' — write the centre default 0/0 into the x/y fields (the
        record's pivot (0,0) = the region centre). No file write happens until
        'Apply' is pressed."""
        self.pivot_x_edit.setText("0.00")
        self.pivot_y_edit.setText("0.00")

    def _on_pivot_apply(self) -> None:
        """'Apply' (Save pivot) — read/validate the x/y fields (mm in the
        record's centre-frame), then rewrite the pivot into the record's
        owning file (write_scheme_list_record with target_path=self._path;
        scheme_list_to_dict omits a (0,0) pivot). Pure config write, no live
        board. Emits saved() so ConfigTreeDock refreshes (see gui/dock_hub.py)."""
        self._show_message("")
        if not self._entry or self._path is None:
            self._show_message(_("Load a Scheme List record first."), _ERROR_STYLE)
            return
        try:
            x = float(self.pivot_x_edit.text().strip())
            y = float(self.pivot_y_edit.text().strip())
        except ValueError:
            self._show_message(_("Pivot x/y must be numbers (mm)."), _ERROR_STYLE)
            return
        try:
            record = load_scheme_list(self._entry)
        except ValidationError as e:
            logger.warning("[SchemeList Pivot] Apply load_scheme_list failed: "
                           "%r (%s)", str(e), type(e).__name__)
            self._show_message(str(e), _ERROR_STYLE)
            return
        record.pivot = (x, y)
        root_path = self._root_path if self._root_path is not None else Path(".")
        try:
            written = write_scheme_list_record(root_path, record,
                                               target_path=self._path)
        except (ValidationError, OSError) as e:
            logger.warning("[SchemeList Pivot] Apply write failed: %r (%s)",
                           str(e), type(e).__name__)
            self._show_message(_("Pivot save failed: {error}").format(error=e),
                               _ERROR_STYLE)
            return
        # Keep the loaded raw entry in sync so a later Reread-Apply preserves
        # the just-saved pivot instead of reverting to the stale stored one.
        self._entry = scheme_list_to_dict(record)
        self._show_message(
            _("Pivot for Scheme List {name!r} saved -> {path}").format(
                name=record.name, path=display_path(written)), _SUCCESS_STYLE)
        logger.warning("[SchemeList Pivot] Apply OK -> %s", written)
        self.saved.emit()

    def _on_pivot_from_selection(self) -> None:
        """'Take from selection' — read the CURRENT live board selection's
        centre and write it into the x/y fields as the pivot in the record's
        centre-frame (selected centre minus the LIVE centre of the recorded
        region, recomputed from the recorded refs' present positions). Pure live
        read — fills the FIELDS as a preview; nothing is written until 'Apply'
        (Save pivot) is pressed (Commit B2, plan_2026_09_07_scheme_list_pivot_
        commit_b2.md §1). The recorded refs' positions come from the full-board
        footprint SNAPSHOT (self._connection.snapshot), never from a direct
        adapter.get_footprints() call on this GUI thread (Commit H,
        plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_fix.md §0) — and that
        snapshot is REBUILT first, on the worker thread, before any position is
        read out of it: it otherwise freezes at connect/manual-refresh time
        (R.2.1, plan_2026_09_11_stale_snapshot_positions.md)."""
        self._show_message("")
        if not self._entry or self._path is None:
            self._show_message(_("Load a Scheme List record first."), _ERROR_STYLE)
            return
        board = getattr(self._connection, "board", None)
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            self._show_message(_("Connect to KiCad first."), _ERROR_STYLE)
            return
        try:
            record = load_scheme_list(self._entry)
        except ValidationError as e:
            logger.warning("[SchemeList Pivot] TakeFromSel load_scheme_list "
                           "failed: %r (%s)", str(e), type(e).__name__)
            self._show_message(str(e), _ERROR_STYLE)
            return
        # R.2.1: rebuild the polled full-board snapshot on the worker thread
        # (never an adapter call here) BEFORE reading any position out of it.
        self._pivot_op = refresh_snapshot_then(
            self._connection, (self.pivot_from_selection_button,),
            lambda: self._pivot_from_selection_now(record),
            self._on_pivot_snapshot_refresh_failed)

    def _pivot_from_selection_now(self, record: SchemeListConfig) -> None:
        """UI thread, AFTER the snapshot rebuild (see
        _on_pivot_from_selection): the recorded refs' present positions feed the
        pivot preview. Without a refreshable connection this is the old
        synchronous cached-snapshot path (tests/fallback)."""
        # The polled full-board footprint snapshot (BoardConnection.snapshot) —
        # the same cache Reread already reads (see _collect_reread_payload).
        # `adapter` above is only the live-board gate; positions come from here.
        snapshot = getattr(self._connection, "snapshot", None) or []
        record_refs = [c.ref for c in record.components]
        missing = missing_record_refs(record_refs, snapshot)
        logger.warning("[SchemeList Pivot] TakeFromSel record=%r refs=%r "
                       "missing=%r", record.name, record_refs, missing)
        try:
            pivot_mm = pivot_centre_frame_from_selection(
                record_refs, snapshot, self._selection_footprints)
        except ValidationError as e:
            logger.warning("[SchemeList Pivot] TakeFromSel pivot failed: %r "
                           "(%s)", str(e), type(e).__name__)
            self._show_message(str(e), _ERROR_STYLE)
            return
        logger.warning("[SchemeList Pivot] TakeFromSel OK pivot=%r",
                       (pivot_mm[0], pivot_mm[1]))
        self.pivot_x_edit.setText(_pivot_mm_text(pivot_mm[0]))
        self.pivot_y_edit.setText(_pivot_mm_text(pivot_mm[1]))
        if missing:
            self._show_message(_("not all recorded components are on the board — "
                                 "pivot computed from the present ones"), _WARN_STYLE)
        else:
            self._show_message(
                _("Pivot taken from the board selection — press Apply to save it."),
                _SUCCESS_STYLE)

    def _on_pivot_snapshot_refresh_failed(self, message: str) -> None:
        """UI thread: the worker could not rebuild the snapshot (the live board
        is gone — BoardConnection.refresh() drops the connection). Say so
        instead of silently computing the pivot from stale coordinates."""
        self._show_message(
            _("Could not refresh the board snapshot: {error}").format(
                error=message), _ERROR_STYLE)

    # ── Reread ──────────────────────────────────────────────────────────

    def _collect_reread_payload(self) -> Optional[Dict[str, Any]]:
        """UI thread: validate that a record is loaded and a live board is
        connected, then snapshot the plain-data payload for the worker. The
        payload carries the CURRENT Reread ``scope_refs`` (5c.4) — recomputed
        on the UI thread from the stored scope_sheet_paths / the live board
        selection, so the worker's build_scheme_list_diff can add/remove refs."""
        board = getattr(self._connection, "board", None)
        if board is None:
            self._show_message(_("Connect to KiCad first."), _ERROR_STYLE)
            return None
        if not self._entry:
            self._show_message(_("Load a Scheme List record first."), _ERROR_STYLE)
            return None
        try:
            stored = load_scheme_list(self._entry)
        except ValidationError as e:
            # A hand-broken record must not crash the Reread flow either.
            self._show_message(str(e), _ERROR_STYLE)
            return None
        snapshot = getattr(self._connection, "snapshot", None) or []
        selection_refs = sorted({getattr(s, "ref", None)
                                 for s in self._selection_footprints
                                 if getattr(s, "ref", None)})
        # A named preset picked on the record page overrides the stored scope
        # for THIS Reread (data None = the "(current)" sentinel = stored
        # scope_sheet_paths unchanged, plan §8/§9/§10).
        active_scope_paths = self.preset_combo.currentData()
        scope_refs = reread_scope_refs(stored, snapshot, selection_refs,
                                       active_scope_paths=active_scope_paths)
        if stored.scope_sheet_paths is None and not scope_refs:
            # A "By selection"-record's Reread scope is the CURRENT board
            # selection — with none we cannot know what to re-read. Warn
            # instead of silently diffing the stored fixed set (5c.4).
            self._show_message(
                _("select footprints on the board first — this record's Reread "
                  "scope is the current selection"), _ERROR_STYLE)
            return None
        return {"board": board, "stored": dict(self._entry),
                "scope_refs": scope_refs,
                # The preset (if any) whose paths became the current scope —
                # Apply re-captures under THOSE paths and makes them the new
                # stored scope_sheet_paths (plan §10). None = 5c behavior.
                "active_scope_paths": active_scope_paths,
                "root": str(self._root_path) if self._root_path else None,
                "path": str(self._path) if self._path else None}

    def reread(self) -> None:
        """Public Reread entry point — used by the form's button, the Config
        tree context menu and the Tools -> Scheme Lists -> "Reread" delegate
        (triple exposure, plan §5.3)."""
        self._on_reread()

    def _on_reread(self) -> None:
        self._show_message("")
        payload = self._collect_reread_payload()
        if payload is None:
            return
        if self._active_op is not None:
            return
        self._active_op = start_long_op(
            self._connection, (self.reread_button,),
            self._run_reread, self._finish_reread, self._on_reread_op_failed,
            payload)

    def _run_reread(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: build the diff against the live board — never
        touches a widget, applies nothing. The CURRENT scope (payload's
        ``scope_refs``, 5c.4) makes the diff add/remove refs, not just diff the
        stored fixed set."""
        try:
            stored = load_scheme_list(payload["stored"])
            diff = build_scheme_list_diff(stored, payload["board"].adapter,
                                          scope_refs=payload.get("scope_refs"))
        except ValidationError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Scheme List Reread failed")
            return {"error": _("Reread failed: {error}").format(error=e)}
        return {"diff": diff, "name": stored.name}

    def _finish_reread(self, result: Dict[str, Any]) -> None:
        self._active_op = None
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        diff = result["diff"]
        if not diff.changed:
            self._show_message(
                _("{name!r} is up to date — nothing changed on the board.")
                .format(name=result["name"]), _SUCCESS_STYLE)
            return
        dialog = SchemeListDiffDialog(result["name"], diff, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_reread()

    def _on_reread_op_failed(self, message: str) -> None:
        self._active_op = None
        self._show_message(_("Reread failed: {error}").format(error=message), _ERROR_STYLE)

    def _apply_reread(self) -> None:
        """Apply half of Reread (explicit user confirmation already given in
        the diff dialog): re-capture the record's region on the live board and
        rewrite the stored record in place — never applied to the board."""
        payload = self._collect_reread_payload()
        if payload is None:
            return
        if self._active_op is not None:
            return
        self._active_op = start_long_op(
            self._connection, (self.reread_button,),
            self._run_reread_apply, self._finish_reread_apply,
            self._on_reread_op_failed, payload)

    def _run_reread_apply(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: fresh capture + write the record back to its owning
        file (or the default scheme_lists.json when none). Pure file/IPC work.

        5c: the capture refs are the record's CURRENT ``scope_refs`` (the
        payload's, = the diff's refs_for_fresh whenever Apply is allowed, i.e.
        while no recorded ref is missing) — added refs land in the record and
        refs removed from the scope fall out. The record's stored
        ``scope_sheet_paths`` is PRESERVED, otherwise the first Reread-Apply
        would silently drop the "By sheet" scope. Named presets (plan §10):
        a preset picked on the page BEFORE Reread becomes the NEW stored
        scope_sheet_paths (switch to the preset AND re-sync in one Apply);
        the scope_presets LIBRARY itself is never rewritten here — only an
        explicit Record/Re-source "Save as preset" does."""
        try:
            stored = load_scheme_list(payload["stored"])
            adapter = payload["board"].adapter
            scope_refs = payload.get("scope_refs")
            refs = (scope_refs if scope_refs is not None
                    else [c.ref for c in stored.components])
            # The preset (if any) whose paths became the current scope —
            # Apply makes it the new stored scope (plan §10); None (the
            # "(current)" sentinel / no presets) keeps 5c behavior.
            active_scope_paths = payload.get("active_scope_paths")
            scope_sheet_paths = (active_scope_paths
                                 if active_scope_paths is not None
                                 else stored.scope_sheet_paths)
            fresh = capture_scheme_list(
                name=stored.name,
                refs=refs,
                adapter=adapter,
                # Reread keeps the record's stored pivot and source_sheet —
                # the live re-capture must NOT reset a user-chosen pivot or
                # re-derive the sheet (design_2026_09_07_scheme_list_pivot.md:
                # Reread's job is geometry, not re-sourcing).
                pivot=stored.pivot,
                source_sheet=stored.source_sheet,
                # 5c: carry the (possibly preset-switched) "By sheet" scope
                # into the rewritten record (None for a "By selection"-record).
                scope_sheet_paths=scope_sheet_paths,
                # plan §10 — the preset LIBRARY is carried over verbatim:
                # Apply only switches WHICH paths are current, it never edits
                # the saved library.
                scope_presets=stored.scope_presets)
            load_scheme_list(scheme_list_to_dict(fresh))  # validate before writing
            root_path = Path(payload["root"]) if payload.get("root") else Path(".")
            target_path = Path(payload["path"]) if payload.get("path") else None
            written = write_scheme_list_record(root_path, fresh, target_path=target_path)
        except (ValidationError, OSError) as e:
            return {"error": _("Reread apply failed: {error}").format(error=e)}
        except Exception as e:
            logger.exception("Scheme List Reread apply failed")
            return {"error": _("Reread apply failed: {error}").format(error=e)}
        return {"name": fresh.name, "path": str(written)}

    def _finish_reread_apply(self, result: Dict[str, Any]) -> None:
        self._active_op = None
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        self._show_message(
            _("Updated Scheme List {name!r} from the live board -> {path}")
            .format(name=result["name"], path=display_path(Path(result["path"]))),
            _SUCCESS_STYLE)
        self.saved.emit()

    # ── Test hooks (synchronous, no worker thread — net_trace's _do_* shape) ──

    def _do_reread(self) -> Dict[str, Any]:
        """Synchronous Reread diff — for tests (no dialog is opened)."""
        payload = self._collect_reread_payload()
        if payload is None:
            return {}
        return self._run_reread(payload)

    def _do_reread_apply(self) -> Dict[str, Any]:
        """Synchronous Reread Apply — for tests (assumes the diff dialog was
        already accepted)."""
        payload = self._collect_reread_payload()
        if payload is None:
            return {}
        result = self._run_reread_apply(payload)
        self._finish_reread_apply(result)
        return result
