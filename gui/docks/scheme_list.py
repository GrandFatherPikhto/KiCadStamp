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
                             QListWidgetItem, QMessageBox, QPlainTextEdit,
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


def scheme_list_duplicate_problems(root_path: Path, name: str, refs: list) -> List[str]:
    """Cross-record pre-checks the loader would otherwise surface at the next
    load (plan §2 — the "Record..." action checks BEFORE capture, so an
    expensive board read is never wasted on a record that cannot be saved):
      - duplicate ``name`` across scheme_lists: entries;
      - a ref already recorded in ANOTHER Scheme List (ref-uniqueness §0.2).
    Returns localized problem strings; empty when the record is clean."""
    used_names = set()
    used_refs: set = set()
    for e in read_scheme_list_records(root_path):
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
    derives the actual capture refs via record_refs_for()."""

    def __init__(self, snapshot: list, selection_refs: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Record Scheme List"))
        self._snapshot = list(snapshot or [])
        self._sheet_paths = live_sheet_paths(self._snapshot)
        # Semantic state: does the CURRENT root have sub-sheets to prune?
        # Tracked explicitly (not via isVisible) so a never-shown dialog in
        # tests and the real shown dialog behave identically.
        self._root_has_subsheets = False

        layout = QVBoxLayout(self)
        name_form = QFormLayout()
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
class _BoundaryRow:
    net: str
    external_ref: Optional[str]


def confirm_boundary_exclusions(parent, boundary_nets: list) -> bool:
    """v1 boundary-net decision dialog (plan §5.3): a capture whose closure
    dropped whole connected components reaching only EXCLUDED footprints
    reports them as boundary_nets. v1 has a single action per net — exclude —
    so this dialog only CONFIRMS the exclusions (and shows the external ref
    that dragged each net as diagnostics); there is no truncate option.
    Returns True to record with the exclusions, False to cancel."""
    rows = [
        _BoundaryRow(net=bn.net, external_ref=bn.external_ref)
        for bn in boundary_nets if isinstance(bn, SchemeListBoundaryNet)
    ]
    lines = "\n".join(
        _("{net} (touched by {external_ref})").format(net=r.net, external_ref=r.external_ref)
        if r.external_ref else r.net
        for r in rows)
    box = QMessageBox(parent)
    box.setWindowTitle(_("Boundary nets"))
    box.setText(
        _("The recorded region touches copper of components outside the "
          "selection on the following net(s):\n\n{nets}\n\n"
          "This copper cannot be part of the Scheme List — v1 excludes it "
          "(drops each whole connected component). The exclusions are stored "
          "in the record; Reread will keep reporting changes to these nets.")
        .format(nets=lines))
    record_btn = box.addButton(_("Record with exclusions"),
                               QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    return box.clickedButton() is record_btn


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

    def clear(self) -> None:
        """Blank the form (nothing loaded)."""
        self._entry = {}
        self._path = None
        self.name_label.setText("")
        self.anchor_combo.clear()
        self.anchor_pad_label.setText("-")
        self.anchor_rotation_label.setText("-")
        self.source_sheet_label.setText("-")
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

    # ── Reread ──────────────────────────────────────────────────────────

    def _collect_reread_payload(self) -> Optional[Dict[str, Any]]:
        """UI thread: validate that a record is loaded and a live board is
        connected, then snapshot the plain-data payload for the worker."""
        board = getattr(self._connection, "board", None)
        if board is None:
            self._show_message(_("Connect to KiCad first."), _ERROR_STYLE)
            return None
        if not self._entry:
            self._show_message(_("Load a Scheme List record first."), _ERROR_STYLE)
            return None
        return {"board": board, "stored": dict(self._entry),
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
        touches a widget, applies nothing."""
        try:
            stored = load_scheme_list(payload["stored"])
            diff = build_scheme_list_diff(stored, payload["board"].adapter)
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
        """
        try:
            stored = load_scheme_list(payload["stored"])
            adapter = payload["board"].adapter
            fresh = capture_scheme_list(
                name=stored.name,
                refs=[c.ref for c in stored.components],
                anchor_ref=stored.anchor_ref,
                anchor_pad=stored.anchor_pad,
                adapter=adapter,
                # Reread keeps the record's stored source_sheet — the live
                # re-capture must NOT re-derive it (5a.2: source_sheet now
                # comes from the anchor's own sheet path, and Reread's job is
                # geometry, not re-sourcing).
                source_sheet=stored.source_sheet)
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
