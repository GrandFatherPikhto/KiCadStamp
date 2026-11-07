# gui/docks/imprint.py
"""
ImprintFormWidget — Config-side Imprint record viewer + Reread (P5,
plan_2026_09_05_scheme_list.md §5.1, design §3).

An Imprint is a NAMED snapshot of a real, already-routed board region
(recorded via Tools -> "Imprints" -> "Record..."). This dock is the
MINIMAL Config side of that feature: it shows a loaded ``imprints:``
record — the CENTRE-frame geometry of the recorded region, a ``source_sheet``
readout and an EDITABLE ``pivot`` (Commit B1): the record's anchor point in
the centre-frame as x/y mm fields with a "Centre" quick-set (0,0 = the region
centre) and an "Apply" (Save pivot) that rewrites the pivot into the file that
owns the record (design_2026_09_07_scheme_list_pivot.md /
plan_2026_09_07_scheme_list_pivot_commit_b.md). Commit B2 adds a
"Take from selection" quick-set (pivot_from_selection_button): it reads the
CENTRE of the CURRENT live board selection and writes it into x/y as a pivot
in the centre-frame (selected centre minus the LIVE centre of the recorded
region, design_2026_09_07_imprint_pivot_commit_b2.md §1) — a pure live
read into the fields, still saved only by the explicit Apply — plus the
recorded-geometry
summary — and offers the one board action that belongs here,
**Reread**: re-run the capture against the live board
(kicadstamp.imprint_capture.build_imprint_diff), show the diff
dialog, and only on an explicit **Apply** rewrite the stored record in place
(``upsert_list_entry`` by name into the file that actually owns the record).

Deliberately NO Placement/Redraw in this dock (plan §5.1 / design §3/§8):
cloning an Imprint onto another sheet happens ONLY through the
Entity/Placement machinery in Trees (the P4 ApplyPipeline branch + the P6
"Instantiate..." wizard), never from a Config form. Nothing in this module
ever applies anything to the live board.

The module also hosts the PURE storage helpers every Imprint write path
shares (``imprint_to_dict``, the default storage path + the
auto-``include:`` ensure, the read/write helpers and the duplicate pre-checks),
so the Tools "Record..." flow (DockHub) and this form's Reread Apply use ONE
implementation instead of two copies.
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from kipy.errors import ApiError
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QPushButton,
                             QTabWidget, QVBoxLayout, QWidget)

from kicadstamp.cli_common import api_error_message
from kicadstamp.config import ImprintConfig, load_imprint
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.imprint_capture import (
    ImprintBoundaryNet,
    ImprintDiff,
    build_imprint_diff,
    capture_imprint,
)
from kicadstamp.utils.paths import overrides_path_for_config

from ..worker import (refresh_board_before_live_read,
                      refresh_snapshot_then_with_retry, start_long_op)
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      WARN_STYLE as _WARN_STYLE,
                      add_include, display_path, read_data, show_message,
                      upsert_list_entry)
from .imprint_refs_tab import ImprintRefsTab
from .imprint_dialogs import BoundaryNetDialog, RecordImprintDialog
from .imprint_record_scope import (_pivot_mm_text, all_sheet_paths,
                                   live_record_centre_mm, live_sheet_paths,
                                   pivot_centre_frame_from_selection,
                                   record_refs_for, refs_on_sheet,
                                   reread_scope_refs, sheet_paths_under,
                                   sheet_subtree_plan,
                                   snapshot_with_resolved_sheets)
from .rename import collect_graph_files, find_list_entry_file

logger = logging.getLogger(__name__)

# Re-exports (07.11, plan_2026_11_07_dialog_sizes_and_row_shift part Б): the
# Qt-free sheet/record half moved to imprint_record_scope.py and the two dialogs
# to imprint_dialogs.py — this file is a giant that may only shrink (rule 45).
# Every `gui.docks.imprint.<name>` caller (dock_hub, diagnostics/
# probe_gui_min_sizes, the tests) keeps importing from HERE, the same re-export
# idiom gui/docks/cell_editor.py already uses for its own Qt-free half.
# `QMessageBox` is re-exported for the same reason: the dialog cells patch
# `gui.docks.imprint.QMessageBox.warning` to prove no modal box opens.
__all__ = [
    "BoundaryNetDialog", "BoundaryNetRow", "ImprintFormWidget", "QMessageBox",
    "RecordImprintDialog", "all_sheet_paths", "boundary_net_rows",
    "choose_boundary_actions", "default_imprint_path", "ensure_imprint_storage",
    "imprint_diff_lines", "imprint_duplicate_problems", "imprint_to_dict",
    "live_record_centre_mm", "live_sheet_paths", "missing_record_refs",
    "pivot_centre_frame_from_selection", "read_imprint_records",
    "record_refs_for", "refs_on_sheet", "reread_scope_refs",
    "sheet_paths_under", "sheet_subtree_plan",
    "snapshot_with_resolved_sheets", "write_imprint_record",
]


# ── Pure storage helpers (shared by the Record... tool and Reread Apply) ────

def imprint_to_dict(record: ImprintConfig) -> Dict[str, Any]:
    """ImprintConfig -> plain dict for JSON/.sexp writing (the compact,
    round-trippable shape load_imprint() reads back). Mirror of
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


# The storage side file a NEW Imprint record lands in, and the pre-2026-09-12
# name this code used to create unconditionally (JSON). Both names live here so
# the backward-compatibility rule below has one source of truth.
IMPRINT_STORAGE_NAME = "scheme_lists.sexp"
LEGACY_IMPRINT_STORAGE_NAME = "scheme_lists.json"


def default_imprint_path(root_path: Path) -> Path:
    """The storage file for NEW Imprint records (plan §0.8) — a
    ``scheme_lists.sexp`` sitting NEXT TO the main profile, auto-included on
    first Record... (records can be large — real copper, not a parametric
    template — so they never bloat the hand-readable root profile).

    Backward compatibility (2026-09-12): a profile that ALREADY has the legacy
    ``scheme_lists.json`` side file keeps using it — such a profile
    ``include:``s exactly that name and must go on working with no change on
    disk (profiles/3ch-awg-tia-v103-old is one). Only when no such file exists
    does this return the s-expr name, so a fresh profile never creates a JSON
    config file. Nothing is ever converted, migrated or rewritten."""
    parent = Path(root_path).parent
    legacy = parent / LEGACY_IMPRINT_STORAGE_NAME
    if legacy.exists():
        return legacy
    return parent / IMPRINT_STORAGE_NAME


def ensure_imprint_storage(root_path: Path) -> Path:
    """Make the default storage file writable: create it when absent and wire
    its name into the ROOT profile's ``include:`` list (add_include is
    idempotent — a re-enabled/again-included file returns without a duplicate
    line). Returns the storage path.

    A file created HERE is always s-expr, written through the ONE config writer
    (Т3): the empty config carries the CURRENT format number and reads back as
    ``{}`` through sexp_to_dict (measured 2026-09-12) — the s-expr counterpart
    of the ``{}\\n`` the JSON storage used to be created as. A legacy
    ``scheme_lists.json`` is reused as-is (see default_imprint_path) and is
    NEVER rewritten or converted, so the two files can never both appear."""
    path = default_imprint_path(root_path)
    if not path.exists():
        from kicadstamp.config_writer import write_config_file

        write_config_file(path, {})
    add_include(Path(root_path), path.name)
    return path


def read_imprint_records(root_path: Path) -> List[Dict[str, Any]]:
    """Every raw ``imprints:`` record across the whole include: graph
    rooted at root_path (read through config_writer.read_data, so a staged
    working-set write is visible too). Empty list on any load failure — this
    is a pre-write duplicate check, not validation."""
    try:
        records: List[Dict[str, Any]] = []
        for path in collect_graph_files(Path(root_path)):
            for e in read_data(path).get("imprints") or []:
                if isinstance(e, dict):
                    records.append(e)
        return records
    except (ValidationError, OSError):
        return []


def imprint_duplicate_problems(root_path: Path, name: str, refs: list,
                                   *, exclude_name: Optional[str] = None) -> List[str]:
    """Cross-record pre-checks the loader would otherwise surface at the next
    load (plan §2 — the "Record..." action checks BEFORE capture, so an
    expensive board read is never wasted on a record that cannot be saved):
      - duplicate ``name`` across imprints: entries;
      - a ref already recorded in ANOTHER Imprint (ref-uniqueness §0.2).
    ``exclude_name`` (Re-source, plan_2026_09_06_scheme_list_sheet_capture.md
    5b.1): the record being re-sourced is SKIPPED entirely — its own name is
    not a duplicate of itself and its own refs are being REPLACED (replace,
    not conflict), so they must not count as "used by another record".
    Existing Record... callers pass no exclude_name -> None -> behavior
    unchanged. Returns localized problem strings; empty when clean."""
    used_names = set()
    used_refs: set = set()
    for e in read_imprint_records(root_path):
        if exclude_name is not None and e.get("name") == exclude_name:
            continue  # the record itself is being replaced, not duplicated
        used_names.add(e.get("name"))
        for c in e.get("components") or []:
            if isinstance(c, dict) and c.get("ref"):
                used_refs.add(c["ref"])
    problems: List[str] = []
    if name in used_names:
        problems.append(
            _("an Imprint named {name!r} already exists — pick another name")
            .format(name=name))
    overlap = sorted(set(refs) & used_refs)
    if overlap:
        problems.append(
            _("component ref(s) already recorded in another Imprint: {refs}")
            .format(refs=", ".join(overlap)))
    return problems


def write_imprint_record(root_path: Path, record: ImprintConfig,
                             target_path: Optional[Path] = None) -> Path:
    """Persist one Imprint record. Without ``target_path`` the record is
    written to the default storage file (``scheme_lists.sexp``, or the legacy
    ``scheme_lists.json`` when that already exists — created + auto-included on
    first use); with it (Reread Apply — the file that actually owns the
    loaded record) the record is upserted there by name. Returns the written
    file. Pure file operation — callable from the UI thread or a worker."""
    if target_path is None:
        target_path = ensure_imprint_storage(root_path)
    upsert_list_entry(Path(target_path), "imprints",
                      imprint_to_dict(record), key="name")
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
# record_imprint/"By sheet" already build from, so zero extra IPC and zero
# race.


def missing_record_refs(record_refs: list, footprints) -> list:
    """Recorded refs that are NOT on the live board (edge 2: "not all recorded
    components are on the board") — for a UI warning. Empty when every recorded
    ref is present. Reads the refs from the footprint SNAPSHOT (see
    live_record_centre_mm — never a direct adapter IPC call)."""
    live_refs = {s.ref for s in footprints
                 if getattr(s, "ref", None) is not None}
    return sorted(r for r in record_refs if r not in live_refs)


# ── Diff dialog text ────────────────────────────────────────────────────────

def _fmt(v: float) -> str:
    """Compact mm/deg formatting for the diff dialog (0.1500 -> 0.15)."""
    return f"{v:.4f}".rstrip("0").rstrip(".")


def imprint_diff_lines(diff: ImprintDiff) -> List[str]:
    """Human-readable (already localized) summary lines of an ImprintDiff —
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


# The "What changed" Reread dialog used to live here. It is GONE (2026-09-20,
# Denis, live): Reread is the re-read, and a box whose only useful answer is
# "yes" was in the way. The diff lines now go to the LOG (imprint_diff_lines is
# still the one place that words them) and the record is re-synced right away;
# the only refusal left is a recorded component missing from the board, where
# the write could not be faithful — and even that is a Log line, not a box.


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
    ``ImprintBoundaryNet`` rows (the capture always produces them, but a
    stray non-model entry must not crash the dialog) — the pure input the
    boundary dialog renders. Net + external_ref only; no decision lives
    here."""
    return [
        BoundaryNetRow(net=bn.net, external_ref=bn.external_ref)
        for bn in boundary_nets if isinstance(bn, ImprintBoundaryNet)
    ]


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

class ImprintFormWidget(QWidget):
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
              "Cloning an Imprint onto another sheet happens through a "
              "tree Entity (imprint:), not here."))
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
        # Tab 3 — Components (2026-09-20, Д2 of
        # plan_2026_09_18_scheme_list_to_cell_and_capture.md; renamed from
        # "Roles" the same day, Denis: the tab shows the record's COMPONENTS,
        # the Role is one of their columns): the record's own components as a
        # table, ONE cluster field above it and the "Convert to cell" button.
        # The values go into the project's override store (our values win over
        # the board) and the conversion writes cells: — this page owns the
        # wiring, the tab owns the table (see gui/docks/imprint_refs_tab.py).
        self.refs_tab = ImprintRefsTab(self._main_window,
                                       connection=self._connection)
        self.refs_tab.saved.connect(self.saved.emit)
        self.page_tabs.addTab(self.refs_tab, _("Components"))
        layout.addWidget(self.page_tabs)
        layout.addStretch(1)

    # ── Message helper ──────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(text, style, logger)

    # ── Root / record wiring ────────────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        self._root_path = path
        # The Roles tab's override store belongs to the PROFILE: opening another
        # one re-reads it (the tab's own rule — an unchanged object is ignored).
        self.reload_overrides()

    def set_board_selection(self, items, selected) -> None:
        """Live board selection tick (DockHub.set_board_selection fan-out,
        5c.4) — the Reread scope of a "By selection"-record is the CURRENT
        board selection at click time, so the user re-selects the (possibly
        changed) set and THEN clicks Reread. `items` is unused (kept for the
        shared fan-out signature).

        The same tick feeds the Roles tab (2026-09-20, Д2): its board columns and
        role suggestions come from the page's polled snapshot, never from a live
        adapter call on the UI thread (door rule 6 / guard С12)."""
        self._selection_footprints = list(selected)
        self._sync_roles_tab()

    def _snapshot_records(self) -> list:
        """The polled whole-board snapshot as role-table records. Read from
        `connection.snapshot` (already read data — no board access here) and
        only ever used for display: the Roles tab needs each component's Role
        and its symbol uuid, both of which the snapshot already carries."""
        return ImprintRefsTab.records_from_snapshot(
            getattr(self._connection, "snapshot", None) or ())

    def reload_overrides(self) -> None:
        """Re-read the project's override store from its FILE and hand the fresh
        copy to the Roles tab — the stop for "another pane recorded" (Т5) and
        for a project change. A no-op without a project."""
        if self._root_path is None:
            self.refs_tab.set_overrides(None)
            return
        from kicadstamp.field_overrides import load_field_overrides
        self.refs_tab.set_overrides(
            load_field_overrides(overrides_path_for_config(str(self._root_path))))

    def _sync_roles_tab(self) -> None:
        """Hand the Roles tab the record it is showing plus the page's snapshot
        (UI thread, no board read). Called on every selection tick and after
        every (re)load of a record."""
        self.refs_tab.set_context(self._root_path, self._loaded_name(),
                                  self._loaded_record(),
                                  self._snapshot_records())

    def _loaded_record(self):
        """The parsed record of the currently shown entry, or None."""
        if not self._entry:
            return None
        try:
            return load_imprint(self._entry)
        except ValidationError:
            return None

    def _loaded_name(self) -> Optional[str]:
        return self._entry.get("name") if self._entry else None

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
        self.refs_tab.clear()

    def load_entry(self, entry: Dict[str, Any],
                   file_path: Optional[Path] = None) -> None:
        """Config-tree imprints leaf click (imprint_picked): populate
        the read-only form from the saved record. The WRITE target is set back
        to the file the record actually lives in, so a Reread Apply updates
        that file instead of adding a root/duplicate record (2026-08-21 review
        fix pattern, same as net_trace.load_entry)."""
        self._show_message("")
        if file_path is None:
            file_path = find_list_entry_file(self._root_path, "imprints", entry)
        if file_path is not None:
            self._path = Path(file_path)
        self._entry = dict(entry)
        try:
            record = load_imprint(entry)
        except ValidationError as e:
            # A hand-broken record must not crash the form — show the record's
            # raw identity and log the problem (it will fatal at the next load).
            self._show_message(str(e), _ERROR_STYLE)
            self.name_label.setText(
                _("Imprint: {name}").format(name=entry.get("name", "?")))
            self.pivot_x_edit.clear()
            self.pivot_y_edit.clear()
            self.refs_tab.clear()
            return
        self._render(record)
        # The Roles tab follows the loaded record (its refs, and the pivot that
        # becomes the created cell's mount point).
        self._sync_roles_tab()

    def _render(self, record: ImprintConfig) -> None:
        self.name_label.setText(
            _("Imprint: {name}").format(name=record.name))
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

    def _render_preset_combo(self, record: ImprintConfig) -> None:
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

    # ── Pivot editing (Commit B1 + B2, plan_2026_09_07_imprint_pivot_commit_b) ──
    # The record's pivot is EDITABLE on this page: x/y mm QLineEdits in the
    # record's centre-frame + a "Centre" quick-set (0/0 = the region centre)
    # and an explicit "Apply" (Save pivot) that rewrites the record's owning
    # file (write_imprint_record) — a pure config write, no live board.
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
        owning file (write_imprint_record with target_path=self._path;
        imprint_to_dict omits a (0,0) pivot). Pure config write, no live
        board. Emits saved() so ConfigTreeDock refreshes (see gui/dock_hub.py)."""
        self._show_message("")
        if not self._entry or self._path is None:
            self._show_message(_("Load an Imprint record first."), _ERROR_STYLE)
            return
        try:
            x = float(self.pivot_x_edit.text().strip())
            y = float(self.pivot_y_edit.text().strip())
        except ValueError:
            self._show_message(_("Pivot x/y must be numbers (mm)."), _ERROR_STYLE)
            return
        try:
            record = load_imprint(self._entry)
        except ValidationError as e:
            logger.warning("[Imprint Pivot] Apply load_imprint failed: "
                           "%r (%s)", str(e), type(e).__name__)
            self._show_message(str(e), _ERROR_STYLE)
            return
        record.pivot = (x, y)
        root_path = self._root_path if self._root_path is not None else Path(".")
        try:
            written = write_imprint_record(root_path, record,
                                               target_path=self._path)
        except (ValidationError, OSError) as e:
            logger.warning("[Imprint Pivot] Apply write failed: %r (%s)",
                           str(e), type(e).__name__)
            self._show_message(_("Pivot save failed: {error}").format(error=e),
                               _ERROR_STYLE)
            return
        # Keep the loaded raw entry in sync so a later Reread-Apply preserves
        # the just-saved pivot instead of reverting to the stale stored one.
        self._entry = imprint_to_dict(record)
        self._show_message(
            _("Pivot for Imprint {name!r} saved -> {path}").format(
                name=record.name, path=display_path(written)), _SUCCESS_STYLE)
        logger.warning("[Imprint Pivot] Apply OK -> %s", written)
        self.saved.emit()

    def _on_pivot_from_selection(self) -> None:
        """'Take from selection' — read the CURRENT live board selection's
        centre and write it into the x/y fields as the pivot in the record's
        centre-frame (selected centre minus the LIVE centre of the recorded
        region, recomputed from the recorded refs' present positions). Pure live
        read — fills the FIELDS as a preview; nothing is written until 'Apply'
        (Save pivot) is pressed (Commit B2, plan_2026_09_07_imprint_pivot_
        commit_b2.md §1). The recorded refs' positions come from the full-board
        footprint SNAPSHOT (self._connection.snapshot), never from a direct
        adapter.get_footprints() call on this GUI thread (Commit H,
        plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_fix.md §0) — and that
        snapshot is REBUILT first, on the worker thread, before any position is
        read out of it: it otherwise freezes at connect/manual-refresh time
        (R.2.1, plan_2026_09_11_stale_snapshot_positions.md)."""
        self._show_message("")
        if not self._entry or self._path is None:
            self._show_message(_("Load an Imprint record first."), _ERROR_STYLE)
            return
        board = getattr(self._connection, "board", None)
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            self._show_message(_("Connect to KiCad first."), _ERROR_STYLE)
            return
        try:
            record = load_imprint(self._entry)
        except ValidationError as e:
            logger.warning("[Imprint Pivot] TakeFromSel load_imprint "
                           "failed: %r (%s)", str(e), type(e).__name__)
            self._show_message(str(e), _ERROR_STYLE)
            return
        # R.2.1: rebuild the polled full-board snapshot on the worker thread
        # (never an adapter call here) BEFORE reading any position out of it.
        self._pivot_op = refresh_snapshot_then_with_retry(
            self._connection, (self.pivot_from_selection_button,),
            lambda: self._pivot_from_selection_now(record),
            self._on_pivot_snapshot_refresh_failed,
            busy_text=_("reading the board"),
            owner=self.pivot_from_selection_button,
            # Э2.5: same rule as the RecordImprintDialog pivot above — a
            # position read from a stale snapshot is wrong geometry, so refuse
            # and tell the user (ERROR, user-facing) instead of falling back.
            on_still_busy=lambda: self._on_pivot_snapshot_refresh_failed(
                _("another operation is using the board")))

    def _pivot_from_selection_now(self, record: ImprintConfig) -> None:
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
        logger.warning("[Imprint Pivot] TakeFromSel record=%r refs=%r "
                       "missing=%r", record.name, record_refs, missing)
        try:
            pivot_mm = pivot_centre_frame_from_selection(
                record_refs, snapshot, self._selection_footprints)
        except ValidationError as e:
            logger.warning("[Imprint Pivot] TakeFromSel pivot failed: %r "
                           "(%s)", str(e), type(e).__name__)
            self._show_message(str(e), _ERROR_STYLE)
            return
        logger.warning("[Imprint Pivot] TakeFromSel OK pivot=%r",
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
        selection, so the worker's build_imprint_diff can add/remove refs."""
        board = getattr(self._connection, "board", None)
        if board is None:
            self._show_message(_("Connect to KiCad first."), _ERROR_STYLE)
            return None
        if not self._entry:
            self._show_message(_("Load an Imprint record first."), _ERROR_STYLE)
            return None
        # A project ROOT is required, and it is not a formality: Apply WRITES the
        # record back, and without a root the worker had nothing to write to (it
        # fell back to Path(".") and the config writer rejected the extensionless
        # path — Denis's live ERROR, 2026-09-20). Refuse here, with a Log line.
        if self._root_path is None:
            self._show_message(
                _("Open a project first — Reread rewrites the file that owns "
                  "this record."), _ERROR_STYLE)
            return None
        try:
            stored = load_imprint(self._entry)
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
        tree context menu and the Tools -> Imprints -> "Reread" delegate
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
            # A live read: rebuild the board first, or the diff compares the
            # stored record against a stale cache (plan_2026_09_22_imprint_
            # stale_cache_sh2 §1.1/§2.4 — measured 1.8063 mm of lag).
            refresh_board_before_live_read(getattr(payload.get("board"), "adapter", None))
            stored = load_imprint(payload["stored"])
            diff = build_imprint_diff(stored, payload["board"].adapter,
                                          scope_refs=payload.get("scope_refs"))
        except ValidationError as e:
            return {"error": str(e)}
        except ApiError as e:
            # KiCad IPC failure = board STATE, not a bug (plan_2026_09_11_no_
            # modals_and_busy_kicad X.2.2): the human explanation (AS_BUSY ->
            # "finish the unfinished tool in KiCad"), never a raw stack.
            return {"error": _("Reread failed: {error}").format(
                error=api_error_message(e))}
        except Exception as e:
            logger.exception("Imprint Reread failed")
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
        # 2026-09-20 (Denis, live): the "What changed" dialog is GONE. Reread is
        # the re-read — a box whose only useful answer is "yes" was in the way, so
        # the diff lines go to the LOG and the record is re-synced right away.
        # The one refusal that stays is the one where the write could not be
        # faithful: a recorded component that is no longer on the board (the
        # record cannot be re-synced around it) — said in the Log, still no box.
        if diff.refs_not_found:
            self._show_message(
                _("Reread stopped — component(s) missing from the board: {refs}. "
                  "The record is unchanged; restore them and Reread again.")
                .format(refs=", ".join(diff.refs_not_found)), _ERROR_STYLE)
            return
        self._show_message("; ".join(imprint_diff_lines(diff)))
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
        file (or the default storage file when none — scheme_lists.sexp, or the
        legacy scheme_lists.json). Pure file/IPC work.

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
            # Same live-read rule as _run_reread above: Apply rewrites the record
            # from the board as it is now — what the user just saw in the diff.
            refresh_board_before_live_read(getattr(payload.get("board"), "adapter", None))
            stored = load_imprint(payload["stored"])
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
            fresh = capture_imprint(
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
            load_imprint(imprint_to_dict(fresh))  # validate before writing
            # NEVER a fallback path here: `Path(".")` has no config extension and
            # the writer rejects it (the live ERROR of 2026-09-20 was exactly that
            # — the UI guard above now stops it earlier, and this is the worker's
            # own belt: it must not have to invent where to write).
            if not payload.get("root"):
                return {"error": _("Reread apply failed: no project root to write "
                                   "the record to")}
            root_path = Path(payload["root"])
            target_path = Path(payload["path"]) if payload.get("path") else None
            written = write_imprint_record(root_path, fresh, target_path=target_path)
        except (ValidationError, OSError) as e:
            return {"error": _("Reread apply failed: {error}").format(error=e)}
        except ApiError as e:
            # Same board-state rule as _run_reread above (X.2.2).
            return {"error": _("Reread apply failed: {error}").format(
                error=api_error_message(e))}
        except Exception as e:
            logger.exception("Imprint Reread apply failed")
            return {"error": _("Reread apply failed: {error}").format(error=e)}
        return {"name": fresh.name, "path": str(written)}

    def _finish_reread_apply(self, result: Dict[str, Any]) -> None:
        self._active_op = None
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        # The record was REWRITTEN: re-read it from the file we just wrote, so the
        # form shows the new record and the Components tab shows its new
        # composition. Added live 2026-09-20: Denis re-read `zummer` after adding
        # D6 to the board, and the tab still listed the three old components —
        # the in-memory entry was a snapshot of the last load, and Reread's whole
        # job is geometry AND the ref set (5c). Reload BEFORE the success line:
        # that sentence must be the last word on the strip (the same order the
        # cell editor's Refs tab uses).
        self._reload_entry_from_file(result.get("path"))
        self._show_message(
            _("Updated Imprint {name!r} from the live board -> {path}")
            .format(name=result["name"], path=display_path(Path(result["path"]))),
            _SUCCESS_STYLE)
        self.saved.emit()

    def _reload_entry_from_file(self, file_path) -> None:
        """Re-read THIS record from the file that owns it and re-render the form
        (and the Components tab). The file is the truth; `self._entry` is only
        the snapshot the form was loaded with. A missing/renamed record in that
        file leaves the form as it is — the caller has already reported what was
        written, and guessing a composition would be worse than showing the old
        one."""
        name = (self._entry or {}).get("name")
        if not name or not file_path:
            return
        try:
            data = read_data(Path(file_path))
            entry = next((e for e in (data.get("imprints") or [])
                          if isinstance(e, dict) and e.get("name") == name), None)
            record = load_imprint(entry) if entry is not None else None
        except (ValidationError, OSError):
            return
        if record is None:
            return
        self._entry = dict(entry)
        self._path = Path(file_path)
        self._render(record)
        self._sync_roles_tab()

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
