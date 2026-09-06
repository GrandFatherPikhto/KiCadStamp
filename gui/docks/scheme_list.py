# gui/docks/scheme_list.py
"""
SchemeListFormWidget — Config-side Scheme List record viewer + Reread (P5,
plan_2026_09_05_scheme_list.md §5.1, design §3).

A Scheme List is a NAMED snapshot of a real, already-routed board region
(recorded via Tools -> "Scheme Lists" -> "Record..."). This dock is the
MINIMAL Config side of that feature: it shows a loaded ``scheme_lists:``
record READ-ONLY — its Anchor block (the closed set of captured component
refs plus the anchor_pad / anchor_rotation_deg / source_sheet readouts) and
the recorded-geometry summary — and offers the one action that belongs here,
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
                             QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPlainTextEdit,
                             QPushButton, QTabWidget, QVBoxLayout, QWidget)

from kicadstamp.config import SchemeListConfig, load_scheme_list
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.scheme_list_capture import (
    SchemeListBoundaryNet,
    SchemeListDiff,
    build_scheme_list_diff,
    capture_scheme_list,
)

from ..worker import start_long_op
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      add_include, display_path, read_data, show_message,
                      upsert_list_entry)
from .rename import collect_graph_files, find_list_entry_file

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
    fields omitted when unset, geometry lists always written."""
    d: Dict[str, Any] = {"name": record.name, "anchor_ref": record.anchor_ref}
    if record.anchor_pad:
        d["anchor_pad"] = record.anchor_pad
    if record.anchor_rotation_deg:
        d["anchor_rotation_deg"] = record.anchor_rotation_deg
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


# ── Diff dialog text ────────────────────────────────────────────────────────

def _fmt(v: float) -> str:
    """Compact mm/deg formatting for the diff dialog (0.1500 -> 0.15)."""
    return f"{v:.4f}".rstrip("0").rstrip(".")


def scheme_list_diff_lines(diff: SchemeListDiff) -> List[str]:
    """Human-readable (already localized) summary lines of a SchemeListDiff —
    shared by the diff dialog and any future log-only consumer."""
    lines: List[str] = []
    if diff.refs_not_found:
        lines.append(
            _("component(s) no longer on the board: {refs}")
            .format(refs=", ".join(diff.refs_not_found)))
    if diff.anchor_missing:
        anchor = diff.refs_not_found[0] if diff.refs_not_found else "?"
        lines.append(_("the anchor {ref!r} is not on the board").format(ref=anchor))
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

      - "By sheet" (DEFAULT tab) — pick a ROOT sheet from the live hierarchy,
        under it a CHECKLIST of every sub-sheet (root itself included; all
        checked by default, uncheck a row to EXCLUDE that sheet). The anchor
        candidates are the DIRECT refs of the CHECKED sheets only (a sheet
        with children does not pull their refs in — the checklist is the only
        recursion). The checklist is hidden for a leaf sheet (nothing to
        prune).
      - "By selection" (secondary tab) — the pre-existing P2 behavior: the
        CURRENT board selection, unchanged, for irregular cases.

    anchor_pad is NOT asked here (v1 — the footprint centre is the offset
    origin, matching capture's default). The shared name_edit sits OUTSIDE the
    tabs. This dialog only reports what was picked; the caller (DockHub)
    derives the actual capture refs via record_refs_for().

    Re-source mode (``fixed_name``, plan_2026_09_06_scheme_list_sheet_capture.md
    5b.2): the SAME dialog is reused to RE-SOURCE an existing record — the name
    is pinned (read-only) to the record being replaced, the title/OK label say
    "Re-source" and an explicit in-dialog warning explains the "points at, does
    not copy" consequence (the record's refs/geometry are replaced; entities
    placed from it pick the new geometry on their next Apply/Redraw). Both
    source tabs stay available — re-sourcing can come from either mode."""

    def __init__(self, snapshot: list, selection_refs: List[str], parent=None,
                 fixed_name: Optional[str] = None):
        super().__init__(parent)
        self._fixed_name = fixed_name
        if fixed_name:
            self.setWindowTitle(
                _("Re-source Scheme List {name!r}").format(name=fixed_name))
        else:
            self.setWindowTitle(_("Record Scheme List"))
        self._snapshot = list(snapshot or [])
        self._sheet_paths = live_sheet_paths(self._snapshot)
        # Semantic state: does the CURRENT root have sub-sheets to prune?
        # Tracked explicitly (not via isVisible) so a never-shown dialog in
        # tests and the real shown dialog behave identically.
        self._root_has_subsheets = False

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

        # Tab 1 — "By sheet": root sheet combo + sub-sheet checklist + anchor.
        tab1 = QWidget()
        tab1_form = QFormLayout(tab1)
        self.sheet_combo = QComboBox()
        for path in self._sheet_paths:
            self.sheet_combo.addItem("/".join(path), path)
        tab1_form.addRow(_("Sheet:"), self.sheet_combo)
        self.sheet_checklist = QListWidget()
        self.sheet_checklist.setVisible(False)  # until a root with sub-sheets
        tab1_form.addRow(_("Sub-sheets (uncheck to exclude):"), self.sheet_checklist)
        # Optional "Save as preset" (plan_2026_09_06_scheme_list_named_presets.md
        # §6): the CURRENT checked checklist becomes a NAMED preset saved in the
        # record. Fully optional — empty text = nothing saved, zero effect on
        # OK-gating or the 5a/5b result_data contract.
        self.save_preset_edit = QLineEdit()
        self.save_preset_edit.setPlaceholderText(
            _("optional — save this checklist as a named preset (same name overwrites it)"))
        tab1_form.addRow(_("Save as preset:"), self.save_preset_edit)
        self.sheet_anchor_combo = QComboBox()
        tab1_form.addRow(_("Anchor:"), self.sheet_anchor_combo)
        self.sheet_combo.currentIndexChanged.connect(self._rebuild_checklist)
        self.sheet_checklist.itemChanged.connect(self._rebuild_sheet_anchor_combo)
        self.tabs.addTab(tab1, _("By sheet"))

        # Tab 2 — "By selection": the current board selection (unchanged).
        tab2 = QWidget()
        tab2_form = QFormLayout(tab2)
        self.selection_anchor_combo = QComboBox()
        self.selection_anchor_combo.addItems(selection_refs)
        tab2_form.addRow(_("Anchor:"), self.selection_anchor_combo)
        self.tabs.addTab(tab2, _("By selection"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel, self)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if fixed_name:
            # Explicit irreversibility: the OK label says what will happen.
            self._ok_button.setText(_("Re-source"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # OK is gated on a non-empty anchor combo of the ACTIVE tab.
        self.tabs.currentChanged.connect(self._sync_ok_state)
        self.sheet_anchor_combo.currentIndexChanged.connect(self._sync_ok_state)
        self.selection_anchor_combo.currentIndexChanged.connect(self._sync_ok_state)
        self._rebuild_checklist()  # populate for the default root sheet
        self._sync_ok_state()

    # ── "By sheet" helpers ──────────────────────────────────────────────

    def _rebuild_checklist(self) -> None:
        root = self.sheet_combo.currentData()
        self.sheet_checklist.clear()
        self._root_has_subsheets = False
        if root is None:
            self._sync_ok_state()
            return
        rows = sheet_paths_under(self._sheet_paths, root)
        if len(rows) <= 1:
            # Leaf sheet — nothing to prune, hide the checklist entirely
            # (design §2 — no checklist when the root has no sub-sheets).
            self.sheet_checklist.setVisible(False)
        else:
            self._root_has_subsheets = True
            self.sheet_checklist.setVisible(True)
            for path in rows:
                depth = len(path) - len(root)
                label = "  " * depth + path[-1]
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, path)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)  # all on by default
                self.sheet_checklist.addItem(item)
        self._rebuild_sheet_anchor_combo()

    def _checked_sheet_paths(self) -> List[Any]:
        """The sheet paths actually included — the single source of truth for
        "what is really checked". A root WITHOUT sub-sheets (no checklist rows)
        means the root itself is the only row."""
        root = self.sheet_combo.currentData()
        if root is None:
            return []
        if not self._root_has_subsheets:
            return [root]
        return [self.sheet_checklist.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.sheet_checklist.count())
                if self.sheet_checklist.item(i).checkState() == Qt.CheckState.Checked]

    def _rebuild_sheet_anchor_combo(self) -> None:
        refs = sorted({r for p in self._checked_sheet_paths()
                       for r in refs_on_sheet(self._snapshot, p)})
        self.sheet_anchor_combo.clear()
        self.sheet_anchor_combo.addItems(refs)
        self._sync_ok_state()

    def _sync_ok_state(self) -> None:
        """OK needs a capturable set — a non-empty anchor combo on the ACTIVE
        tab (an empty selection / all-unchecked sheet leaves nothing to anchor
        to, so the user must change the source instead of recording an empty
        capture)."""
        anchor_combo = (self.sheet_anchor_combo if self.is_by_sheet()
                        else self.selection_anchor_combo)
        self._ok_button.setEnabled(anchor_combo.count() > 0)

    def is_by_sheet(self) -> bool:
        return self.tabs.currentIndex() == 0

    def result_data(self):
        """(name, anchor_ref, sheet_path_or_None, checked_paths_or_None).
        sheet_path/checked_paths are None on the "By selection" tab; the caller
        derives the capture refs itself (record_refs_for) from the CHECKED
        paths for "By sheet", or uses its OWN selection_refs for "By selection"
        (this dialog does not own that list)."""
        name = self.name_edit.text().strip()
        if self.is_by_sheet():
            return (name, self.sheet_anchor_combo.currentText(),
                    self.sheet_combo.currentData(), self._checked_sheet_paths())
        return (name, self.selection_anchor_combo.currentText(), None, None)

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
    Read-only record + Reread (see module docstring — no Placement/Redraw)."""

    # Fired after a successful Reread Apply that rewrote the stored record —
    # ConfigTreeDock listens to refresh (see gui/dock_hub.py).
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

        form = QFormLayout()
        self.anchor_combo = QComboBox()
        self.anchor_combo.setEnabled(False)  # closed set — view only
        form.addRow(_("Anchor component:"), self.anchor_combo)
        self.anchor_pad_label = QLabel("-")
        form.addRow(_("Anchor pad:"), self.anchor_pad_label)
        self.anchor_rotation_label = QLabel("-")
        form.addRow(_("Anchor rotation (at record):"), self.anchor_rotation_label)
        self.source_sheet_label = QLabel("-")
        form.addRow(_("Source sheet:"), self.source_sheet_label)
        # Named-presets selector (plan_2026_09_06_scheme_list_named_presets.md
        # §8) — shown ONLY when the loaded record carries a scope_presets
        # library; the first item is the "(current)" sentinel (data None =
        # use stored.scope_sheet_paths as-is, 5c behavior unchanged).
        self.preset_combo = QComboBox()
        self.preset_combo.setVisible(False)
        form.addRow(_("Preset:"), self.preset_combo)
        self.geometry_label = QLabel("")
        self.geometry_label.setWordWrap(True)
        form.addRow(_("Recorded geometry:"), self.geometry_label)
        layout.addLayout(form)

        note = QLabel(
            _("Reread compares this record against the live board and, after "
              "an explicit Apply, rewrites it — it never places anything. "
              "Cloning a Scheme List onto another sheet happens through a "
              "tree Entity (scheme_list:), not here."))
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        self.reread_button = QPushButton(_("Reread"))
        self.reread_button.clicked.connect(self._on_reread)
        buttons.addWidget(self.reread_button)
        layout.addLayout(buttons)
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
        self.anchor_combo.clear()
        self.anchor_pad_label.setText("-")
        self.anchor_rotation_label.setText("-")
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
            self.anchor_combo.clear()
            return
        self._render(record)

    def _render(self, record: SchemeListConfig) -> None:
        self.name_label.setText(
            _("Scheme List: {name}").format(name=record.name))
        refs = [c.ref for c in record.components]
        self.anchor_combo.blockSignals(True)
        self.anchor_combo.clear()
        self.anchor_combo.addItems(refs)
        self.anchor_combo.setCurrentText(record.anchor_ref)
        self.anchor_combo.blockSignals(False)
        self.anchor_pad_label.setText(record.anchor_pad or "-")
        self.anchor_rotation_label.setText(f"{record.anchor_rotation_deg:.1f} deg")
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
                anchor_ref=stored.anchor_ref,
                anchor_pad=stored.anchor_pad,
                adapter=adapter,
                # Reread keeps the record's stored source_sheet — the live
                # re-capture must NOT re-derive it (5a.2: source_sheet now
                # comes from the anchor's own sheet path, and Reread's job is
                # geometry, not re-sourcing).
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
