# gui/docks/imprint_record_scope.py
"""The Qt-free half of the Imprint pages: WHICH part of the board a capture
covers, and where the record's own frame sits.

Moved VERBATIM out of gui/docks/imprint.py on 07.11 (plan
plan_2026_11_07_dialog_sizes_and_row_shift, part Б). The Record dialog needs
these helpers, and `imprint.py` is a giant that may only shrink (rule 45) — so
they live here, importable without Qt, the same cut gui/docks/cell_read_report.py
already makes for the Cell pages. `gui/docks/imprint.py` re-exports every name
below, so `gui.docks.imprint.<name>` keeps working unchanged.
"""
from __future__ import annotations

from typing import List, Optional

from kicadstamp.config import ImprintConfig
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.imprint_capture import _region_centre
from kicadstamp.utils.units import MM

from .tree_from_selection import selected_center_mm

# ── Pure "By sheet" scope helpers (shared by Record / Re-source / Reread) ───
#
# Every "By sheet" capture picks a ROOT sheet from the LIVE hierarchy and
# derives its footprint refs from the footprints' FULL resolved sheet-path
# (Selected.sheet), NOT from Board.select(sheet=...) (explore.py) whose filter
# is a single-segment membership test anywhere in the path — that would merge
# two same-named sheets at different nesting levels. The three helpers below do
# the prefix/equality matching correctly and are deliberately Qt-free so every
# Imprint write path (Tools Record... in DockHub, the Record dialog, the
# future Re-source and the Reread scope recompute) shares ONE implementation.


def snapshot_with_resolved_sheets(snapshot: list, sheet_names: dict) -> list:
    """A copy of `snapshot` whose .sheet is re-resolved against `sheet_names`
    (the config-based {uuid: name} map, ctx.sheet_names) instead of the live
    Board's OWN resolution — Board.connect() (gui/connection.py) never passes
    schematic_dir, so a live Board's own sheet_names is always {} and every
    .sheet ends up a list of None. Selected.fp is the raw footprint handle
    kept exactly for this kind of re-resolution (its own docstring). Record/
    Re-source call this on the snapshot BEFORE building RecordImprintDialog
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
    segments, unlike imprint_place._live_sheets),
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
    every PREFIX of them (Commit E, plan_2026_09_07_imprint_by_sheet_full_
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
    Sorted + deduplicated. Shared by DockHub's record_imprint and the
    future Re-source so both modes resolve refs identically."""
    if by_sheet:
        return sorted({r for p in (checked_paths or [])
                       for r in refs_on_sheet(snapshot, tuple(p))})
    return sorted(set(selection_refs))


def reread_scope_refs(stored: ImprintConfig, snapshot: list,
                      selection_refs: List[str],
                      active_scope_paths: Optional[list] = None) -> List[str]:
    """The CURRENT Reread scope for a stored record (5c.4, plan_2026_09_06_
    imprint_sheet_capture.md):
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


def _pivot_mm_text(v: float) -> str:
    """Compact mm formatting for the pivot x/y fields, the same trim `_fmt`
    uses (0.0 -> "0", -7.0 -> "-7", 1.23456 -> "1.2346") — a value filled from
    a live board read round-trips back through float() unchanged enough for the
    Apply/round-trip tests' approx comparisons (Commit B2)."""
    return f"{v:.4f}".rstrip("0").rstrip(".")
