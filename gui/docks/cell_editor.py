# gui/docks/cell_editor.py
"""
CellDock — edits a `cells:` entry (kicadstamp/config/models.py's Cell):
Components/Vias/Tracks (local along/across offsets from the cell's own
(0,0)) plus, recursively, nested clone_placements referencing other
cells/roles. Requested live 2026-08-06 after Denis hit a real bug caused
by the ONLY existing way to create a Cell — ConfigTreeDock's "Add cell..."
wrote a raw `{"components": []}` stub straight to YAML with no form behind
it at all (see techdocs/handoff/handoff_2026_08_06_cell_editor_brainstorm.md):
"создавать экстрактор под один компонент, прости, тупняк" — a full
select-on-board-and-extract round trip was the only way to add so much as
one component slot to a cell by hand.

Table + detail-panel-below per category, same proven shape as RuleDock's
spokes editor — NOT a single tree merging all four kinds into one view
(considered and rejected: unlike RuleDock's spokes, none of Components/
Vias/Tracks/Nested cells share a common set of columns, so one tree would
mean the detail form below still has to switch shape on selection anyway,
buying nothing over four tabs). Denis, 2026-08-06: "если у нас вложенные
целлы могут быть, то скорее не список, а дерево" — the TREE he actually
meant is ConfigTreeDock's own Cells category showing a composite cell's
nested clone_placements as child nodes (read-only navigation), not this
dock's internal editor; see config_tree.py's _build_file_item.

Anchor (added 2026-08-06) — Cell.anchor_xy/anchor_role/anchor_pad,
DISPLAY-ONLY metadata, see Cell's own docstring in config/models.py: never
read by clone_position_calculator.py or any resolver. All offset_along_mm/
offset_across_mm fields already are self-consistent relative to the cell's
local (0,0) regardless of what anchor is set here — this only lets the
editor (and a human reading the YAML) see which existing component/pad was
treated as that origin, instead of it being an untracked fact only the
original extractor run knew. anchor_role is a searchable combo sourced from
THIS cell's own current Components list (not the live board) — it must
name one of them (validated on Save, see config/entries.py's _load_cell).

Net from role (added 2026-08-11) — Vias/Tracks each get a Net source
choice: Literal (the existing free-typed net:, unchanged) or From role
(net_from_role/net_from_role_pad — resolved live at apply time from the
named role's real pad, see kicadstamp/net_from_role_resolver.py and
net_resolution.resolve_net_from_role). The role combo is a plain closed
QComboBox sourced from THIS cell's own current Components list (same
widget/reasoning as anchor_role_combo below — free text is never valid
here); the pad field stays free text since pad numbers aren't enumerable
offline (no live board connection while editing a Cell). Mutual exclusion
between net: and net_from_role: is enforced by the existing config loader
(_load_template_via/_load_template_track), not duplicated here — the form
only ever writes one or the other depending on the mode picked.

Deliberate scope cuts for this first pass (Denis, "Го. Прям все три. без
остановки" — shipped in one sitting, not because these don't matter):
  - TemplateComponentSlot.vias (per-component vias) are NOT editable here —
    only cell-level Vias (spoke.vias in the resolver, TemplateVia at the
    cell's own top level). A component's own via list still round-trips
    correctly if the cell was created by the extractor and merely tweaked
    here (Save only rewrites what this dock itself builds — see
    _build_cell_dict, which reads any pre-existing per-component vias back
    out of the file being edited and preserves them verbatim), but this
    dock cannot ADD a new one. Same kind of explicit, documented scope
    limit as Clone profiles' read-only tree entry.
  - Nested clone_placements only expose their CORE fields (name, cell/role,
    xy, rotation_deg, mirror, layer) — nets/params/net_overrides/refs are
    preserved verbatim if already present (same read-back-and-keep
    mechanism as component vias above) but not editable from this form.

No Redraw/Resolve button — unlike Rule/Point/PlacerDock, a Cell has no
anchor of its own on the live board; it only ever gets a physical position
in the context of a ClonePlacement/Rule spoke that references it, so there
is nothing here to preview against KiCad.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                              QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
                              QLabel, QLineEdit, QMessageBox, QPushButton,
                              QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                              QWidget)

from kicadstamp.cell_geometry_refresh import build_import_plan, build_refresh_plan
from kicadstamp.cell_placement_copy import build_placement_copy_plan, donor_candidates_for
from kicadstamp.config import (load_cell, load_cell_placement, load_template_component_slot,
                               load_template_track, load_template_via)
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ..worker import start_long_op
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, display_path, merge_write, parse_float_field,
                      set_combo_items, show_message)
from .rename import collect_all_cell_names, collect_section_entries, find_dict_entry_file

logger = logging.getLogger(__name__)

_LAYER_ITEMS = [("F.Cu", "F.Cu"), ("B.Cu", "B.Cu")]
_INHERIT_LAYER_ITEMS = [(_("(inherit cell layer)"), None), ("F.Cu", "F.Cu"), ("B.Cu", "B.Cu")]

_COMPONENT_COLUMNS = ["Role", "Offset along", "Offset across", "Angle", "Layer",
                     "Net template", "Net template pad", "Same net as role"]
_VIA_COLUMNS = ["Offset along", "Offset across", "Net", "Drill", "Diameter"]
_TRACK_COLUMNS = ["Start along", "Start across", "End along", "End across", "Width", "Net", "Layer"]
_NESTED_COLUMNS = ["Name", "Content", "X", "Y", "Rotation", "Mirror", "Layer"]


def _layer_combo(items) -> QComboBox:
    combo = QComboBox()
    for text, data in items:
        combo.addItem(text, data)
    return combo


def _net_display(entry: Dict[str, Any]) -> str:
    """Net column text for a via/track row — literal net: as-is, or
    role:<role>[/pad:<pad>] for net_from_role (same "merge two possible
    shapes into one column" precedent as the Nested tab's Content column)."""
    role = entry.get("net_from_role")
    if role:
        pad = entry.get("net_from_role_pad")
        return f"role:{role}/pad:{pad}" if pad else f"role:{role}"
    return str(entry.get("net", ""))


# ── Refresh-geometry preview helpers (2026-09-03, plan cell_geometry_refresh)
# Pure (no widget access) so the GUI test can exercise the exact rows the
# dialog shows without a QDialog event loop.

_GEO_FIELD_LABELS = {
    "offset_along_mm": _("Offset along"),
    "offset_across_mm": _("Offset across"),
    "angle_deg": _("Angle"),
    "start_along_mm": _("Start along"),
    "start_across_mm": _("Start across"),
    "end_along_mm": _("End along"),
    "end_across_mm": _("End across"),
    "width_mm": _("Width"),
}

_GEO_GROUP_LABELS = {
    "components": _("Components"),
    "vias": _("Vias"),
    "tracks": _("Tracks"),
}


def _fmt_mm(value: Optional[float]) -> str:
    """Display form for a geometry value — mm numbers at 4 decimals (matches
    the extractor's storage precision), None rendered as '—' (not-yet-written
    offset key, means 0 in the refresh model)."""
    return "—" if value is None else f"{value:.4f}"


def refresh_preview_sections(components: List[Dict[str, Any]],
                             vias: List[Dict[str, Any]],
                             tracks: List[Dict[str, Any]],
                             plan) -> List[Dict[str, Any]]:
    """Build the read-only preview tables for a RefreshPlan.

    Returns one section dict per non-empty group: {"title", "rows"} where
    each row is [item_label, field_label, old_str, new_str, delta_str]
    (item_label = the role for a component, "via #i on {net}"/"track #i on
    {net}" for copper). A field whose recomputed value equals its current one
    (Δ ≈ 0) contributes NO row — a fully-unchanged selection yields an empty
    list, which the caller surfaces as "Nothing changed" instead of an empty
    dialog."""
    sections: List[Dict[str, Any]] = []
    groups = (
        ("components", components, plan.component_updates),
        ("vias", vias, plan.via_updates),
        ("tracks", tracks, plan.track_updates),
    )
    for group_key, records, updates in groups:
        if not updates:
            continue
        rows: List[List[str]] = []
        for record, new_geo in updates:
            idx = _identity_index(records, record)
            if group_key == "components":
                item = str(record.get("role", ""))
            else:
                kind = "via" if group_key == "vias" else "track"
                item = _("{kind} #{index} on {net}").format(
                    kind=kind, index=idx, net=_net_display(record))
            for field, new_value in new_geo.items():
                old_value = record.get(field)
                # An offset key that the old dict never wrote IS zero in the
                # refresh model — show it as such for a truthful Δ.
                old_display = 0.0 if old_value is None else old_value
                if abs(new_value - old_display) < 1e-9:
                    continue
                label = _GEO_FIELD_LABELS.get(field, field)
                rows.append([item, label,
                             _fmt_mm(old_value),
                             _fmt_mm(new_value),
                             f"{new_value - old_display:+.4f}"])
        if rows:
            sections.append({"title": _GEO_GROUP_LABELS[group_key], "rows": rows})
    return sections


def _identity_index(records: List[Dict[str, Any]], target: Dict[str, Any]) -> int:
    """index() by OBJECT identity, not equality — two distinct dicts with the
    same content would otherwise be conflated."""
    for i, rec in enumerate(records):
        if rec is target:
            return i
    return -1


class _RefreshPreviewDialog(QDialog):
    """Read-only old/new/Δ preview for one RefreshPlan — deliberately a
    separate lightweight widget (not the dock's editable tables, whose
    semantics differ). Apply/Cancel; the actual record.update() happens in the
    caller AFTER this dialog returns Accepted (so mutation + autostage stay in
    the dock's normal path). Since 2026-09-05 the plan may also ADD brand-new
    via/track records: a section may carry its own "headers" (a Kind/Position/
    Net list, the import-preview shape) instead of the default 5-column
    Item/Field/Old/New/Δ geometry shape — both render identically from rows."""

    def __init__(self, sections: List[Dict[str, Any]], parent=None,
                 title: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle(title or _("Refresh geometry from selection"))
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        for section in sections:
            headers = section.get("headers") or \
                [_("Item"), _("Field"), _("Old"), _("New"), _("Δ")]
            table = QTableWidget(0, len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Stretch)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            for row_index, row in enumerate(section["rows"]):
                table.insertRow(row_index)
                for col, value in enumerate(row):
                    table.setItem(row_index, col, QTableWidgetItem(str(value)))
            tabs.addTab(table, section["title"])
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox()
        apply_button = buttons.addButton(_("Apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        apply_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _record_position(record: Dict[str, Any], kind: str) -> str:
    """Human-readable position for one import-preview row — the record's own
    local geometry relative to the cell's zero-offset origin (the values the
    Apply button would write into the cell). A via shows its single offset
    point, a track its start → end pair."""
    if kind == "via":
        return "({x}, {y})".format(x=_fmt_mm(record.get("offset_along_mm")),
                                   y=_fmt_mm(record.get("offset_across_mm")))
    return "({sx}, {sy}) → ({ex}, {ey})".format(
        sx=_fmt_mm(record.get("start_along_mm")),
        sy=_fmt_mm(record.get("start_across_mm")),
        ex=_fmt_mm(record.get("end_along_mm")),
        ey=_fmt_mm(record.get("end_across_mm")))


def import_preview_rows(plan) -> List[List[str]]:
    """Build the read-only preview rows for an ImportPlan — one row per NEW
    via/track record the Apply button would append (deliberately NOT the
    old/new/Δ shape of refresh_preview_sections: Import creates records, it
    edits none). Columns are [kind, position, net_source] where position is
    the record's local geometry relative to the cell's zero-offset origin and
    net_source is the classified net (literal / role:[pad:] / rule-net null —
    the same _net_display convention as the dock's own via/track tables).
    Pure (no widget access) so the GUI test can exercise the exact rows the
    dialog shows without a QDialog event loop. Also reused by the additive
    refresh preview (2026-09-05) — a RefreshPlan carries the same
    new_via_records/new_track_records fields."""
    rows: List[List[str]] = []
    for record in plan.new_via_records:
        rows.append([_("Via"), _record_position(record, "via"), _net_display(record)])
    for record in plan.new_track_records:
        rows.append([_("Track"), _record_position(record, "track"), _net_display(record)])
    return rows


def refresh_new_records_section(plan) -> Optional[Dict[str, Any]]:
    """The preview tab for the via/track records build_refresh_plan would ADD
    (add_new_copper mode, 2026-09-05) — one Kind/Position/Net row per NEW
    record, the same shape as the Import preview. None when the plan adds
    nothing, so the caller never shows an empty tab."""
    rows = import_preview_rows(plan)
    if not rows:
        return None
    return {
        "title": _("New vias/tracks to add"),
        "headers": [_("Kind"), _("Position"), _("Net")],
        "rows": rows,
    }



class _ImportPreviewDialog(QDialog):
    """Read-only "these NEW via/track records will be appended" preview for one
    ImportPlan — the additive counterpart of _RefreshPreviewDialog (which shows
    Old/New/Δ edits to EXISTING records). One Kind/Position/Net table; Apply
    appends the listed records to the dock's _vias/_tracks — the actual extend
    happens in the caller AFTER this dialog returns Accepted, so mutation +
    autostage stay in the dock's normal path."""

    def __init__(self, rows: List[List[str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Import vias/tracks from selection"))
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels([_("Kind"), _("Position"), _("Net")])
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for row_index, row in enumerate(rows):
            table.insertRow(row_index)
            for col, value in enumerate(row):
                table.setItem(row_index, col, QTableWidgetItem(str(value)))
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox()
        apply_button = buttons.addButton(_("Apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        apply_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class _CopyPlacementDialog(QDialog):
    """MINIMAL donor picker for one "Copy placement from cell..." (2026-09-06,
    Denis: "В диалоге максимум что должно быть — комбобокс"): a searchable
    combobox of the cells that FIT the target by role set plus Copy/Cancel —
    nothing else, no preview tables. Writes nothing itself; the caller applies
    the plan after this returns Accepted (mutation + autostage stay in the
    dock's normal path)."""

    def __init__(self, candidates: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Copy placement from cell"))
        self.resize(460, 130)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.source_combo = QComboBox()
        configure_searchable(self.source_combo)
        form.addRow(_("Copy from cell:"), self.source_combo)
        layout.addLayout(form)
        buttons = QDialogButtonBox()
        self.copy_button = buttons.addButton(
            _("Copy"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.copy_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        names = sorted(candidates)
        if names:
            self.source_combo.addItems(names)
            self.source_combo.setCurrentIndex(0)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self._on_source_changed()

    def _on_source_changed(self) -> None:
        self.copy_button.setEnabled(bool(self.source_combo.currentText().strip()))

    def source(self) -> str:
        """The chosen donor cell's name."""
        return self.source_combo.currentText().strip()


class CellDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — same
    "plain QWidget, not its own QDockWidget" shape as Extract/Placer/
    Project/Thermal via/Points/Chains."""

    # Fired after a successful Save — ConfigTreeDock listens to refresh its
    # Cells category (see gui/dock_hub.py), same as every other dock here.
    saved = pyqtSignal()

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self._path: Optional[Path] = None
        self._root_path: Optional[Path] = None

        self._components: List[Dict[str, Any]] = []
        self._vias: List[Dict[str, Any]] = []
        self._tracks: List[Dict[str, Any]] = []
        self._nested: List[Dict[str, Any]] = []
        self._selected_component: Optional[int] = None
        self._selected_via: Optional[int] = None
        self._selected_track: Optional[int] = None
        self._selected_nested: Optional[int] = None
        # The currently running long op (gui/worker.py) — held so the
        # controller outlives its QThread (same pattern as every board-touching
        # dock: PointsDock/NetTraceDock/RoleClusterTreeDock keep _active_op).
        self._active_op: Optional[Any] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        head_form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("name (referenced by cell: elsewhere)"))
        head_form.addRow(_("Name:"), self.name_edit)
        self.layer_combo = _layer_combo(_LAYER_ITEMS)
        head_form.addRow(_("Layer:"), self.layer_combo)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText(_("optional free-form note"))
        head_form.addRow(_("Comment:"), self.comment_edit)
        layout.addLayout(head_form)

        anchor_form = QFormLayout()
        self.anchor_mode_combo = QComboBox()
        self.anchor_mode_combo.addItems([_("(none)"), _("XY"), _("Role")])
        self.anchor_mode_combo.currentIndexChanged.connect(self._on_anchor_mode_changed)
        anchor_form.addRow(_("Anchor:"), self.anchor_mode_combo)
        layout.addLayout(anchor_form)

        self._anchor_xy_row = QWidget()
        anchor_xy_row = QHBoxLayout(self._anchor_xy_row)
        anchor_xy_row.setContentsMargins(0, 0, 0, 0)
        self.anchor_x_edit = QLineEdit()
        self.anchor_x_edit.setPlaceholderText(_("X mm"))
        self.anchor_y_edit = QLineEdit()
        self.anchor_y_edit.setPlaceholderText(_("Y mm"))
        anchor_xy_row.addWidget(QLabel(_("X:")))
        anchor_xy_row.addWidget(self.anchor_x_edit)
        anchor_xy_row.addWidget(QLabel(_("Y:")))
        anchor_xy_row.addWidget(self.anchor_y_edit)
        layout.addWidget(self._anchor_xy_row)

        self._anchor_role_row = QWidget()
        anchor_role_form = QFormLayout(self._anchor_role_row)
        anchor_role_form.setContentsMargins(0, 0, 0, 0)
        # Deliberately NOT configure_searchable() — unlike every other Role
        # combo in this project (free-typed "picker, not whitelist"),
        # anchor_role MUST already be one of this cell's own components:
        # roles (validated at Save, see _load_cell) — free text is never
        # valid here, so a plain closed dropdown is the correct widget, not
        # just a workaround. Also plausibly the fix for a live freeze
        # (found 2026-08-06, Denis: clicked Role a couple times on a
        # freshly-added, still-componentless cell — an empty editable combo
        # + QCompleter popup is a real category of Qt hang) — either way,
        # this field's value space is a small closed set, editable search
        # buys nothing here.
        self.anchor_role_combo = QComboBox()
        anchor_role_form.addRow(_("Role:"), self.anchor_role_combo)
        self.anchor_pad_edit = QLineEdit()
        self.anchor_pad_edit.setPlaceholderText(_("pad (optional)"))
        anchor_role_form.addRow(_("Pad:"), self.anchor_pad_edit)
        layout.addWidget(self._anchor_role_row)

        # Refresh geometry from selection (2026-09-03, plan
        # cell_geometry_refresh) — an operation over the WHOLE loaded cell
        # (components + vias + tracks), so it lives above the tabs, not inside
        # any single component/via/track page. Enabled whenever the live board
        # adapter is present AND the loaded cell has components (the actual
        # empty-selection case is reported at run time — CellDock receives no
        # selection feed to gate on, see _update_refresh_enabled).
        refresh_row = QHBoxLayout()
        self.refresh_geometry_button = QPushButton(
            _("Refresh geometry from selection"))
        self.refresh_geometry_button.clicked.connect(self._on_refresh_geometry)
        self.refresh_geometry_button.setEnabled(False)
        refresh_row.addWidget(self.refresh_geometry_button)
        # Import vias/tracks from selection (2026-09-03, plan
        # fpga_oscill_missing_copper_and_cell_import §B.3) — the additive
        # counterpart of Refresh: backfills NEW via/track records for live
        # copper the cell's current records don't describe, and NEVER edits/
        # removes an existing one. Same activity gate, same worker pattern
        # (see _update_refresh_enabled, which gates BOTH buttons).
        self.import_vias_tracks_button = QPushButton(
            _("Import vias/tracks from selection"))
        self.import_vias_tracks_button.clicked.connect(self._on_import_vias_tracks)
        self.import_vias_tracks_button.setEnabled(False)
        refresh_row.addWidget(self.import_vias_tracks_button)
        layout.addLayout(refresh_row)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)
        self._build_components_tab()
        self._build_vias_tab()
        self._build_tracks_tab()
        self._build_nested_tab()

        # 2026-09-01 (plan project_save_model): no per-dock Save button — a
        # cell-level field's commit point (name/comment blur, layer/anchor
        # pick) or any row Add/Update/Remove action auto-stages the whole cell
        # into the working set; File > Save commits to disk. _loading guards
        # programmatic form population (new_cell/load_entry).
        self._loading = False
        self.name_edit.editingFinished.connect(self._autostage)
        self.comment_edit.editingFinished.connect(self._autostage)
        self.layer_combo.currentIndexChanged.connect(self._autostage)
        self.anchor_mode_combo.currentIndexChanged.connect(self._autostage)
        self.anchor_role_combo.currentIndexChanged.connect(self._autostage)
        for edit in (self.anchor_x_edit, self.anchor_y_edit, self.anchor_pad_edit):
            edit.editingFinished.connect(self._autostage)

        self._on_anchor_mode_changed()
        self._refresh_all_tables()

    # ── Tab construction ─────────────────────────────────────────────────

    def _build_components_tab(self) -> None:
        page = QWidget()
        page_layout = QVBoxLayout(page)

        self.components_table = QTableWidget(0, len(_COMPONENT_COLUMNS))
        self.components_table.setHorizontalHeaderLabels([_(c) for c in _COMPONENT_COLUMNS])
        self.components_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.components_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.components_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.components_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.components_table.itemSelectionChanged.connect(self._on_component_selection_changed)
        page_layout.addWidget(self.components_table, 1)

        form = QFormLayout()
        self.comp_role_edit = QComboBox()
        configure_searchable(self.comp_role_edit)
        form.addRow(_("Role:"), self.comp_role_edit)
        self.comp_offset_along_edit = QLineEdit()
        self.comp_offset_along_edit.setPlaceholderText(_("offset along mm (0)"))
        form.addRow(_("Offset along:"), self.comp_offset_along_edit)
        self.comp_offset_across_edit = QLineEdit()
        self.comp_offset_across_edit.setPlaceholderText(_("offset across mm (0)"))
        form.addRow(_("Offset across:"), self.comp_offset_across_edit)
        self.comp_angle_edit = QLineEdit()
        self.comp_angle_edit.setPlaceholderText(_("angle deg (0)"))
        form.addRow(_("Angle:"), self.comp_angle_edit)
        self.comp_layer_combo = _layer_combo(_INHERIT_LAYER_ITEMS)
        form.addRow(_("Layer:"), self.comp_layer_combo)
        self.comp_net_template_edit = QLineEdit()
        self.comp_net_template_edit.setPlaceholderText(_("optional — TemplatePlacer role matching"))
        form.addRow(_("Net template:"), self.comp_net_template_edit)
        self.comp_net_template_pad_edit = QLineEdit()
        self.comp_net_template_pad_edit.setPlaceholderText(_("pad (optional)"))
        form.addRow(_("Net template pad:"), self.comp_net_template_pad_edit)
        # 2026-08-16 (net_template_same_as_role): a CLOSED combo of THIS cell's
        # own roles (same widget kind/reasoning as anchor_role_combo /
        # via_net_from_role_combo — free text is never valid here, and _load_cell
        # fatals if the reference names a role that doesn't exist in this cell).
        # Names the OTHER role whose net this one shares — the cross-instance-
        # safe alternative to a pad number for electrically symmetric 2-pin R/C.
        self.comp_net_template_same_as_role_combo = QComboBox()
        form.addRow(_("Same net as role:"), self.comp_net_template_same_as_role_combo)
        page_layout.addLayout(form)

        row = QHBoxLayout()
        self.add_component_button = QPushButton(_("Add"))
        self.add_component_button.clicked.connect(self._on_add_component)
        row.addWidget(self.add_component_button)
        self.update_component_button = QPushButton(_("Update selected"))
        self.update_component_button.clicked.connect(self._on_update_component)
        row.addWidget(self.update_component_button)
        self.remove_component_button = QPushButton(_("Remove selected"))
        self.remove_component_button.clicked.connect(self._on_remove_component)
        row.addWidget(self.remove_component_button)
        page_layout.addLayout(row)

        self._tabs.addTab(page, _("Components"))

    def _build_vias_tab(self) -> None:
        page = QWidget()
        page_layout = QVBoxLayout(page)

        self.vias_table = QTableWidget(0, len(_VIA_COLUMNS))
        self.vias_table.setHorizontalHeaderLabels([_(c) for c in _VIA_COLUMNS])
        self.vias_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.vias_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.vias_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.vias_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.vias_table.itemSelectionChanged.connect(self._on_via_selection_changed)
        page_layout.addWidget(self.vias_table, 1)

        form = QFormLayout()
        self.via_offset_along_edit = QLineEdit()
        self.via_offset_along_edit.setPlaceholderText(_("offset along mm (0)"))
        form.addRow(_("Offset along:"), self.via_offset_along_edit)
        self.via_offset_across_edit = QLineEdit()
        self.via_offset_across_edit.setPlaceholderText(_("offset across mm (0)"))
        form.addRow(_("Offset across:"), self.via_offset_across_edit)
        self.via_net_source_combo = QComboBox()
        self.via_net_source_combo.addItems([_("Literal"), _("From role")])
        self.via_net_source_combo.currentIndexChanged.connect(self._on_via_net_source_changed)
        form.addRow(_("Net source:"), self.via_net_source_combo)

        self._via_net_literal_row = QWidget()
        via_net_literal_form = QFormLayout(self._via_net_literal_row)
        via_net_literal_form.setContentsMargins(0, 0, 0, 0)
        self.via_net_edit = QLineEdit()
        self.via_net_edit.setPlaceholderText(_("optional — blank means the rule's own net"))
        via_net_literal_form.addRow(_("Net:"), self.via_net_edit)
        form.addRow(self._via_net_literal_row)

        self._via_net_role_row = QWidget()
        via_net_role_form = QFormLayout(self._via_net_role_row)
        via_net_role_form.setContentsMargins(0, 0, 0, 0)
        # Plain closed QComboBox, not configure_searchable() — same reasoning
        # as anchor_role_combo: must already be one of this cell's own
        # components: roles, free text is never valid here (see module
        # docstring's "Net from role" paragraph).
        self.via_net_from_role_combo = QComboBox()
        via_net_role_form.addRow(_("Role:"), self.via_net_from_role_combo)
        self.via_net_from_role_pad_edit = QLineEdit()
        self.via_net_from_role_pad_edit.setPlaceholderText(
            _("optional pad — blank means the role's single non-rule net"))
        via_net_role_form.addRow(_("Pad:"), self.via_net_from_role_pad_edit)
        form.addRow(self._via_net_role_row)

        self.via_drill_edit = QLineEdit()
        self.via_drill_edit.setPlaceholderText(_("drill mm (0.3)"))
        form.addRow(_("Drill:"), self.via_drill_edit)
        self.via_diameter_edit = QLineEdit()
        self.via_diameter_edit.setPlaceholderText(_("diameter mm (0.6)"))
        form.addRow(_("Diameter:"), self.via_diameter_edit)
        page_layout.addLayout(form)

        row = QHBoxLayout()
        self.add_via_button = QPushButton(_("Add"))
        self.add_via_button.clicked.connect(self._on_add_via)
        row.addWidget(self.add_via_button)
        self.update_via_button = QPushButton(_("Update selected"))
        self.update_via_button.clicked.connect(self._on_update_via)
        row.addWidget(self.update_via_button)
        self.remove_via_button = QPushButton(_("Remove selected"))
        self.remove_via_button.clicked.connect(self._on_remove_via)
        row.addWidget(self.remove_via_button)
        page_layout.addLayout(row)

        self._on_via_net_source_changed()
        self._tabs.addTab(page, _("Vias"))

    def _build_tracks_tab(self) -> None:
        page = QWidget()
        page_layout = QVBoxLayout(page)

        self.tracks_table = QTableWidget(0, len(_TRACK_COLUMNS))
        self.tracks_table.setHorizontalHeaderLabels([_(c) for c in _TRACK_COLUMNS])
        self.tracks_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tracks_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tracks_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tracks_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tracks_table.itemSelectionChanged.connect(self._on_track_selection_changed)
        page_layout.addWidget(self.tracks_table, 1)

        form = QFormLayout()
        self.track_start_along_edit = QLineEdit()
        self.track_start_along_edit.setPlaceholderText("0")
        form.addRow(_("Start along:"), self.track_start_along_edit)
        self.track_start_across_edit = QLineEdit()
        self.track_start_across_edit.setPlaceholderText("0")
        form.addRow(_("Start across:"), self.track_start_across_edit)
        self.track_end_along_edit = QLineEdit()
        self.track_end_along_edit.setPlaceholderText("0")
        form.addRow(_("End along:"), self.track_end_along_edit)
        self.track_end_across_edit = QLineEdit()
        self.track_end_across_edit.setPlaceholderText("0")
        form.addRow(_("End across:"), self.track_end_across_edit)
        self.track_width_edit = QLineEdit()
        self.track_width_edit.setPlaceholderText(_("width mm (0.25)"))
        form.addRow(_("Width:"), self.track_width_edit)
        self.track_net_source_combo = QComboBox()
        self.track_net_source_combo.addItems([_("Literal"), _("From role")])
        self.track_net_source_combo.currentIndexChanged.connect(self._on_track_net_source_changed)
        form.addRow(_("Net source:"), self.track_net_source_combo)

        self._track_net_literal_row = QWidget()
        track_net_literal_form = QFormLayout(self._track_net_literal_row)
        track_net_literal_form.setContentsMargins(0, 0, 0, 0)
        self.track_net_edit = QLineEdit()
        self.track_net_edit.setPlaceholderText(_("optional — blank means the rule's own net"))
        track_net_literal_form.addRow(_("Net:"), self.track_net_edit)
        form.addRow(self._track_net_literal_row)

        self._track_net_role_row = QWidget()
        track_net_role_form = QFormLayout(self._track_net_role_row)
        track_net_role_form.setContentsMargins(0, 0, 0, 0)
        self.track_net_from_role_combo = QComboBox()
        track_net_role_form.addRow(_("Role:"), self.track_net_from_role_combo)
        self.track_net_from_role_pad_edit = QLineEdit()
        self.track_net_from_role_pad_edit.setPlaceholderText(
            _("optional pad — blank means the role's single non-rule net"))
        track_net_role_form.addRow(_("Pad:"), self.track_net_from_role_pad_edit)
        form.addRow(self._track_net_role_row)

        self.track_layer_combo = _layer_combo(_INHERIT_LAYER_ITEMS)
        form.addRow(_("Layer:"), self.track_layer_combo)
        page_layout.addLayout(form)

        row = QHBoxLayout()
        self.add_track_button = QPushButton(_("Add"))
        self.add_track_button.clicked.connect(self._on_add_track)
        row.addWidget(self.add_track_button)
        self.update_track_button = QPushButton(_("Update selected"))
        self.update_track_button.clicked.connect(self._on_update_track)
        row.addWidget(self.update_track_button)
        self.remove_track_button = QPushButton(_("Remove selected"))
        self.remove_track_button.clicked.connect(self._on_remove_track)
        row.addWidget(self.remove_track_button)
        page_layout.addLayout(row)

        self._on_track_net_source_changed()
        self._tabs.addTab(page, _("Tracks"))

    def _build_nested_tab(self) -> None:
        page = QWidget()
        page_layout = QVBoxLayout(page)

        self.nested_table = QTableWidget(0, len(_NESTED_COLUMNS))
        self.nested_table.setHorizontalHeaderLabels([_(c) for c in _NESTED_COLUMNS])
        self.nested_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.nested_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.nested_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.nested_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.nested_table.itemSelectionChanged.connect(self._on_nested_selection_changed)
        page_layout.addWidget(self.nested_table, 1)

        form = QFormLayout()
        self.nested_name_edit = QLineEdit()
        self.nested_name_edit.setPlaceholderText(_("name (registry key, must be unique in this cell)"))
        form.addRow(_("Name:"), self.nested_name_edit)
        self.nested_mode_combo = QComboBox()
        self.nested_mode_combo.addItems([_("Cell"), _("Role")])
        self.nested_mode_combo.currentIndexChanged.connect(self._on_nested_mode_changed)
        form.addRow(_("Content:"), self.nested_mode_combo)
        page_layout.addLayout(form)

        self._nested_cell_row = QWidget()
        nested_cell_form = QFormLayout(self._nested_cell_row)
        nested_cell_form.setContentsMargins(0, 0, 0, 0)
        self.nested_cell_combo = QComboBox()
        configure_searchable(self.nested_cell_combo)
        nested_cell_form.addRow(_("Cell:"), self.nested_cell_combo)
        page_layout.addWidget(self._nested_cell_row)

        self._nested_role_row = QWidget()
        nested_role_form = QFormLayout(self._nested_role_row)
        nested_role_form.setContentsMargins(0, 0, 0, 0)
        self.nested_role_combo = QComboBox()
        configure_searchable(self.nested_role_combo)
        nested_role_form.addRow(_("Role:"), self.nested_role_combo)
        page_layout.addWidget(self._nested_role_row)

        xy_row = QHBoxLayout()
        self.nested_x_edit = QLineEdit()
        self.nested_x_edit.setPlaceholderText("0")
        self.nested_y_edit = QLineEdit()
        self.nested_y_edit.setPlaceholderText("0")
        xy_row.addWidget(QLabel(_("X:")))
        xy_row.addWidget(self.nested_x_edit)
        xy_row.addWidget(QLabel(_("Y:")))
        xy_row.addWidget(self.nested_y_edit)
        page_layout.addLayout(xy_row)

        extra_form = QFormLayout()
        self.nested_rotation_edit = QLineEdit()
        self.nested_rotation_edit.setPlaceholderText("0")
        extra_form.addRow(_("Rotation (deg):"), self.nested_rotation_edit)
        self.nested_layer_combo = _layer_combo(_INHERIT_LAYER_ITEMS)
        extra_form.addRow(_("Layer:"), self.nested_layer_combo)
        page_layout.addLayout(extra_form)
        self.nested_mirror_checkbox = QCheckBox(_("Mirror"))
        page_layout.addWidget(self.nested_mirror_checkbox)

        row = QHBoxLayout()
        self.add_nested_button = QPushButton(_("Add"))
        self.add_nested_button.clicked.connect(self._on_add_nested)
        row.addWidget(self.add_nested_button)
        self.update_nested_button = QPushButton(_("Update selected"))
        self.update_nested_button.clicked.connect(self._on_update_nested)
        row.addWidget(self.update_nested_button)
        self.remove_nested_button = QPushButton(_("Remove selected"))
        self.remove_nested_button.clicked.connect(self._on_remove_nested)
        row.addWidget(self.remove_nested_button)
        page_layout.addLayout(row)

        self._on_nested_mode_changed()
        self._tabs.addTab(page, _("Nested cells"))

    # ── Wiring from the Config tree ─────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — new cells: entries are
        always written to the project root file (2026-08-21, plan
        flatten_and_single_file_gui), so the write target IS the root. The
        Nested cells tab's Cell combo stays sourced from the WHOLE include
        graph (a nested cell routinely lives in a different file)."""
        self._root_path = path
        self._path = path
        names = collect_all_cell_names(path) if path is not None else []
        set_combo_items(self.nested_cell_combo, names)

    def refresh_known_roles(self, snapshot) -> None:
        """Same "populate from the live board" pattern as PlacerDock's own
        refresh_known_roles — called by DockHub.push_snapshot. Component
        roles and nested role: both get matched against this field on the
        live board during placement (see component_pool.py/clone_role_
        resolver.py), so both combos are worth autocompleting from it."""
        roles = sorted({s.role for s in snapshot if s.role})
        set_combo_items(self.comp_role_edit, roles)
        set_combo_items(self.nested_role_combo, roles)
        # A push_snapshot only fires while connected (board present), so this
        # is the dock's live-board heartbeat — refresh the geometry button's
        # enabled state on it (adapter present AND a cell is loaded).
        self._update_refresh_enabled()

    # ── Anchor UI ─────────────────────────────────────────────────────────

    def _on_anchor_mode_changed(self) -> None:
        mode = self.anchor_mode_combo.currentIndex()
        self._anchor_xy_row.setVisible(mode == 1)
        self._anchor_role_row.setVisible(mode == 2)

    def _refresh_role_choices(self) -> None:
        """Repopulates every combo whose valid values are THIS cell's own
        current Components list — anchor_role_combo plus the two Vias/Tracks
        "Net source: From role" role pickers (same closed-set reasoning, see
        module docstring's "Net from role" paragraph). Renamed from
        _refresh_anchor_role_choices when the via/track combos were added —
        still called from the same two places (after the Components table
        changes, and once from _refresh_all_tables)."""
        roles = sorted({c["role"] for c in self._components})
        set_combo_items(self.anchor_role_combo, roles)
        set_combo_items(self.via_net_from_role_combo, roles)
        set_combo_items(self.track_net_from_role_combo, roles)
        # A leading "" placeholder keeps "no reference" a valid state: a closed
        # QComboBox auto-selects the first added item, so without it the first
        # role added to the cell would silently become this field's value (and
        # _build_component_dict would reject it as a same-net reference without
        # a net_template). "" round-trips through set_combo_items's
        # preserve-current-text logic, so an existing value survives refreshes.
        set_combo_items(self.comp_net_template_same_as_role_combo, [""] + roles)

    def _on_via_net_source_changed(self) -> None:
        from_role = self.via_net_source_combo.currentIndex() == 1
        self._via_net_literal_row.setVisible(not from_role)
        self._via_net_role_row.setVisible(from_role)

    def _on_track_net_source_changed(self) -> None:
        from_role = self.track_net_source_combo.currentIndex() == 1
        self._track_net_literal_row.setVisible(not from_role)
        self._track_net_role_row.setVisible(from_role)

    def _on_nested_mode_changed(self) -> None:
        mode = self.nested_mode_combo.currentIndex()
        self._nested_cell_row.setVisible(mode == 0)
        self._nested_role_row.setVisible(mode == 1)

    # ── Message/parsing helpers ──────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        """Mirror into the Log dock at the level matching `style` — the docks
        no longer have an inline message_label (2026-08-13), the Log dock is
        the single destination."""
        show_message(text, style, logger)

    def _parse_float(self, edit: QLineEdit, label: str, default: Optional[float]) -> Optional[float]:
        """(ok, value) parse via the shared parse_float_field
        (gui/docks/_common.py), mapped back to the overloaded-None convention
        this dock's many callers expect (None == an error was already
        shown)."""
        ok, value = parse_float_field(edit)
        if not ok:
            self._show_message(_("{label}: {text!r} is not a number.").format(label=label, text=edit.text().strip()),
                               _ERROR_STYLE)
            return None
        return default if value is None else value

    @staticmethod
    def _findable(combo: QComboBox, value) -> int:
        idx = combo.findData(value)
        return idx if idx >= 0 else 0

    # ── Tables refresh ───────────────────────────────────────────────────

    def _refresh_all_tables(self) -> None:
        self._refresh_components_table()
        self._refresh_vias_table()
        self._refresh_tracks_table()
        self._refresh_nested_table()
        self._refresh_role_choices()
        # The loaded cell changed (load_entry/new_cell/add/remove/...) — the
        # geometry-refresh button only makes sense on a non-empty cell.
        self._update_refresh_enabled()

    def _refresh_components_table(self) -> None:
        self.components_table.setRowCount(len(self._components))
        for row, c in enumerate(self._components):
            values = [
                str(c.get("role", "")),
                str(c.get("offset_along_mm", "")) if c.get("offset_along_mm") else "",
                str(c.get("offset_across_mm", "")) if c.get("offset_across_mm") else "",
                str(c.get("angle_deg", "")) if c.get("angle_deg") else "",
                str(c.get("layer", "")),
                str(c.get("net_template", "")),
                str(c.get("net_template_pad", "")),
                str(c.get("net_template_same_as_role", "")),
            ]
            for col, value in enumerate(values):
                self.components_table.setItem(row, col, QTableWidgetItem(value))
        self._refresh_role_choices()

    def _refresh_vias_table(self) -> None:
        self.vias_table.setRowCount(len(self._vias))
        for row, v in enumerate(self._vias):
            values = [
                str(v.get("offset_along_mm", "")) if v.get("offset_along_mm") else "",
                str(v.get("offset_across_mm", "")) if v.get("offset_across_mm") else "",
                _net_display(v),
                str(v.get("drill_mm", "")),
                str(v.get("diameter_mm", "")),
            ]
            for col, value in enumerate(values):
                self.vias_table.setItem(row, col, QTableWidgetItem(value))

    def _refresh_tracks_table(self) -> None:
        self.tracks_table.setRowCount(len(self._tracks))
        for row, t in enumerate(self._tracks):
            values = [
                str(t.get("start_along_mm", "")) if t.get("start_along_mm") else "",
                str(t.get("start_across_mm", "")) if t.get("start_across_mm") else "",
                str(t.get("end_along_mm", "")) if t.get("end_along_mm") else "",
                str(t.get("end_across_mm", "")) if t.get("end_across_mm") else "",
                str(t.get("width_mm", "")),
                _net_display(t),
                str(t.get("layer", "")),
            ]
            for col, value in enumerate(values):
                self.tracks_table.setItem(row, col, QTableWidgetItem(value))

    def _refresh_nested_table(self) -> None:
        self.nested_table.setRowCount(len(self._nested))
        for row, n in enumerate(self._nested):
            content = f"cell:{n['cell']}" if n.get("cell") is not None else f"role:{n.get('role', '')}"
            xy = n.get("xy") or [0.0, 0.0]
            values = [
                str(n.get("name", "")),
                content,
                str(xy[0]),
                str(xy[1]),
                str(n.get("rotation_deg", "")) if n.get("rotation_deg") else "",
                _("yes") if n.get("mirror") else "",
                str(n.get("layer", "")),
            ]
            for col, value in enumerate(values):
                self.nested_table.setItem(row, col, QTableWidgetItem(value))

    # ── Components tab ───────────────────────────────────────────────────

    def _on_component_selection_changed(self) -> None:
        rows = self.components_table.selectionModel().selectedRows()
        if not rows:
            self._selected_component = None
            return
        self._selected_component = rows[0].row()
        c = self._components[self._selected_component]
        self.comp_role_edit.setCurrentText(str(c.get("role", "")))
        self.comp_offset_along_edit.setText(str(c.get("offset_along_mm", "")) if c.get("offset_along_mm") else "")
        self.comp_offset_across_edit.setText(str(c.get("offset_across_mm", "")) if c.get("offset_across_mm") else "")
        self.comp_angle_edit.setText(str(c.get("angle_deg", "")) if c.get("angle_deg") else "")
        self.comp_layer_combo.setCurrentIndex(self._findable(self.comp_layer_combo, c.get("layer")))
        self.comp_net_template_edit.setText(str(c.get("net_template", "")))
        self.comp_net_template_pad_edit.setText(str(c.get("net_template_pad", "")))
        self.comp_net_template_same_as_role_combo.setCurrentText(str(c.get("net_template_same_as_role", "")))

    def _clear_component_editor(self) -> None:
        self.comp_role_edit.setCurrentText("")
        self.comp_offset_along_edit.setText("")
        self.comp_offset_across_edit.setText("")
        self.comp_angle_edit.setText("")
        self.comp_layer_combo.setCurrentIndex(0)
        self.comp_net_template_edit.setText("")
        self.comp_net_template_pad_edit.setText("")
        self.comp_net_template_same_as_role_combo.setCurrentText("")

    def _build_component_dict(self) -> Optional[Dict[str, Any]]:
        role = self.comp_role_edit.currentText().strip()
        if not role:
            self._show_message(_("Role is required."), _ERROR_STYLE)
            return None
        offset_along = self._parse_float(self.comp_offset_along_edit, _("Offset along"), 0.0)
        if offset_along is None:
            return None
        offset_across = self._parse_float(self.comp_offset_across_edit, _("Offset across"), 0.0)
        if offset_across is None:
            return None
        angle = self._parse_float(self.comp_angle_edit, _("Angle"), 0.0)
        if angle is None:
            return None
        entry: Dict[str, Any] = {"role": role}
        if offset_along:
            entry["offset_along_mm"] = offset_along
        if offset_across:
            entry["offset_across_mm"] = offset_across
        if angle:
            entry["angle_deg"] = angle
        layer = self.comp_layer_combo.currentData()
        if layer is not None:
            entry["layer"] = layer
        net_template = self.comp_net_template_edit.text().strip()
        if net_template:
            entry["net_template"] = net_template
        net_template_pad = self.comp_net_template_pad_edit.text().strip()
        if net_template_pad:
            # Mirror of the loader's fatal (entries.py's
            # _load_template_component_slot): a pad is only a pointer into a
            # net_template's candidate, never a net by itself — catching it
            # here keeps the form from assembling an entry the loader would
            # reject on the next load, instead of only failing later.
            if not net_template:
                self._show_message(_("Net template pad requires a net template."), _ERROR_STYLE)
                return None
            entry["net_template_pad"] = net_template_pad
        same_as_role = self.comp_net_template_same_as_role_combo.currentText().strip()
        if same_as_role:
            # Mirror of the loader's fatals (net_template_same_as_role
            # requires net_template; mutually exclusive with net_template_pad)
            # — caught here in the form, before the loader would on the next
            # load.
            if not net_template:
                self._show_message(_("Same net as role requires a net template."), _ERROR_STYLE)
                return None
            if net_template_pad:
                self._show_message(_("Pick one: a fixed pad number or a same-net role reference, not both."), _ERROR_STYLE)
                return None
            entry["net_template_same_as_role"] = same_as_role
        # Per-component vias are not editable in this dock (see module
        # docstring) — preserve whatever the selected row already had.
        if self._selected_component is not None:
            existing_vias = self._components[self._selected_component].get("vias")
            if existing_vias:
                entry["vias"] = existing_vias

        try:
            load_template_component_slot(entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None
        return entry

    def _on_add_component(self) -> None:
        entry = self._build_component_dict()
        if entry is None:
            return
        self._components.append(entry)
        self._refresh_components_table()
        self.components_table.selectRow(len(self._components) - 1)
        self._autostage()

    def _on_update_component(self) -> None:
        if self._selected_component is None:
            self._show_message(_("Pick a component row first."), _ERROR_STYLE)
            return
        entry = self._build_component_dict()
        if entry is None:
            return
        self._components[self._selected_component] = entry
        self._refresh_components_table()
        self.components_table.selectRow(self._selected_component)
        self._autostage()

    def _on_remove_component(self) -> None:
        if self._selected_component is None:
            self._show_message(_("Pick a component row first."), _ERROR_STYLE)
            return
        del self._components[self._selected_component]
        self._selected_component = None
        self._refresh_components_table()
        self._clear_component_editor()
        self._autostage()

    # ── Vias tab ──────────────────────────────────────────────────────────

    def _on_via_selection_changed(self) -> None:
        rows = self.vias_table.selectionModel().selectedRows()
        if not rows:
            self._selected_via = None
            return
        self._selected_via = rows[0].row()
        v = self._vias[self._selected_via]
        self.via_offset_along_edit.setText(str(v.get("offset_along_mm", "")) if v.get("offset_along_mm") else "")
        self.via_offset_across_edit.setText(str(v.get("offset_across_mm", "")) if v.get("offset_across_mm") else "")
        if v.get("net_from_role"):
            self.via_net_source_combo.setCurrentIndex(1)
            self.via_net_from_role_combo.setCurrentText(str(v["net_from_role"]))
            self.via_net_from_role_pad_edit.setText(str(v.get("net_from_role_pad", "")))
            self.via_net_edit.setText("")
        else:
            self.via_net_source_combo.setCurrentIndex(0)
            self.via_net_edit.setText(str(v.get("net", "")))
            self.via_net_from_role_combo.setCurrentText("")
            self.via_net_from_role_pad_edit.setText("")
        self._on_via_net_source_changed()
        self.via_drill_edit.setText(str(v.get("drill_mm", "")) if v.get("drill_mm") is not None else "")
        self.via_diameter_edit.setText(str(v.get("diameter_mm", "")) if v.get("diameter_mm") is not None else "")

    def _clear_via_editor(self) -> None:
        self.via_offset_along_edit.setText("")
        self.via_offset_across_edit.setText("")
        self.via_net_source_combo.setCurrentIndex(0)
        self.via_net_edit.setText("")
        self.via_net_from_role_combo.setCurrentText("")
        self.via_net_from_role_pad_edit.setText("")
        self._on_via_net_source_changed()
        self.via_drill_edit.setText("")
        self.via_diameter_edit.setText("")

    def _build_via_dict(self) -> Optional[Dict[str, Any]]:
        offset_along = self._parse_float(self.via_offset_along_edit, _("Offset along"), 0.0)
        if offset_along is None:
            return None
        offset_across = self._parse_float(self.via_offset_across_edit, _("Offset across"), 0.0)
        if offset_across is None:
            return None
        drill = self._parse_float(self.via_drill_edit, _("Drill"), 0.3)
        if drill is None:
            return None
        diameter = self._parse_float(self.via_diameter_edit, _("Diameter"), 0.6)
        if diameter is None:
            return None
        entry: Dict[str, Any] = {}
        if offset_along:
            entry["offset_along_mm"] = offset_along
        if offset_across:
            entry["offset_across_mm"] = offset_across
        if self.via_net_source_combo.currentIndex() == 1:
            role = self.via_net_from_role_combo.currentText().strip()
            if not role:
                self._show_message(_("Net source: From role — pick a Role first."), _ERROR_STYLE)
                return None
            entry["net_from_role"] = role
            pad = self.via_net_from_role_pad_edit.text().strip()
            if pad:
                entry["net_from_role_pad"] = pad
        else:
            net = self.via_net_edit.text().strip()
            if net:
                entry["net"] = net
        if drill != 0.3:
            entry["drill_mm"] = drill
        if diameter != 0.6:
            entry["diameter_mm"] = diameter

        try:
            load_template_via(entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None
        return entry

    def _on_add_via(self) -> None:
        entry = self._build_via_dict()
        if entry is None:
            return
        self._vias.append(entry)
        self._refresh_vias_table()
        self.vias_table.selectRow(len(self._vias) - 1)
        self._autostage()

    def _on_update_via(self) -> None:
        if self._selected_via is None:
            self._show_message(_("Pick a via row first."), _ERROR_STYLE)
            return
        entry = self._build_via_dict()
        if entry is None:
            return
        self._vias[self._selected_via] = entry
        self._refresh_vias_table()
        self.vias_table.selectRow(self._selected_via)
        self._autostage()

    def _on_remove_via(self) -> None:
        if self._selected_via is None:
            self._show_message(_("Pick a via row first."), _ERROR_STYLE)
            return
        del self._vias[self._selected_via]
        self._selected_via = None
        self._refresh_vias_table()
        self._clear_via_editor()
        self._autostage()

    # ── Tracks tab ────────────────────────────────────────────────────────

    def _on_track_selection_changed(self) -> None:
        rows = self.tracks_table.selectionModel().selectedRows()
        if not rows:
            self._selected_track = None
            return
        self._selected_track = rows[0].row()
        t = self._tracks[self._selected_track]
        self.track_start_along_edit.setText(str(t.get("start_along_mm", "")) if t.get("start_along_mm") else "")
        self.track_start_across_edit.setText(str(t.get("start_across_mm", "")) if t.get("start_across_mm") else "")
        self.track_end_along_edit.setText(str(t.get("end_along_mm", "")) if t.get("end_along_mm") else "")
        self.track_end_across_edit.setText(str(t.get("end_across_mm", "")) if t.get("end_across_mm") else "")
        self.track_width_edit.setText(str(t.get("width_mm", "")) if t.get("width_mm") is not None else "")
        if t.get("net_from_role"):
            self.track_net_source_combo.setCurrentIndex(1)
            self.track_net_from_role_combo.setCurrentText(str(t["net_from_role"]))
            self.track_net_from_role_pad_edit.setText(str(t.get("net_from_role_pad", "")))
            self.track_net_edit.setText("")
        else:
            self.track_net_source_combo.setCurrentIndex(0)
            self.track_net_edit.setText(str(t.get("net", "")))
            self.track_net_from_role_combo.setCurrentText("")
            self.track_net_from_role_pad_edit.setText("")
        self._on_track_net_source_changed()
        self.track_layer_combo.setCurrentIndex(self._findable(self.track_layer_combo, t.get("layer")))

    def _clear_track_editor(self) -> None:
        self.track_start_along_edit.setText("")
        self.track_start_across_edit.setText("")
        self.track_end_along_edit.setText("")
        self.track_end_across_edit.setText("")
        self.track_width_edit.setText("")
        self.track_net_source_combo.setCurrentIndex(0)
        self.track_net_edit.setText("")
        self.track_net_from_role_combo.setCurrentText("")
        self.track_net_from_role_pad_edit.setText("")
        self._on_track_net_source_changed()
        self.track_layer_combo.setCurrentIndex(0)

    def _build_track_dict(self) -> Optional[Dict[str, Any]]:
        start_along = self._parse_float(self.track_start_along_edit, _("Start along"), 0.0)
        if start_along is None:
            return None
        start_across = self._parse_float(self.track_start_across_edit, _("Start across"), 0.0)
        if start_across is None:
            return None
        end_along = self._parse_float(self.track_end_along_edit, _("End along"), 0.0)
        if end_along is None:
            return None
        end_across = self._parse_float(self.track_end_across_edit, _("End across"), 0.0)
        if end_across is None:
            return None
        width = self._parse_float(self.track_width_edit, _("Width"), 0.25)
        if width is None:
            return None
        entry: Dict[str, Any] = {}
        if start_along:
            entry["start_along_mm"] = start_along
        if start_across:
            entry["start_across_mm"] = start_across
        if end_along:
            entry["end_along_mm"] = end_along
        if end_across:
            entry["end_across_mm"] = end_across
        if width != 0.25:
            entry["width_mm"] = width
        if self.track_net_source_combo.currentIndex() == 1:
            role = self.track_net_from_role_combo.currentText().strip()
            if not role:
                self._show_message(_("Net source: From role — pick a Role first."), _ERROR_STYLE)
                return None
            entry["net_from_role"] = role
            pad = self.track_net_from_role_pad_edit.text().strip()
            if pad:
                entry["net_from_role_pad"] = pad
        else:
            net = self.track_net_edit.text().strip()
            if net:
                entry["net"] = net
        layer = self.track_layer_combo.currentData()
        if layer is not None:
            entry["layer"] = layer

        try:
            load_template_track(entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None
        return entry

    def _on_add_track(self) -> None:
        entry = self._build_track_dict()
        if entry is None:
            return
        self._tracks.append(entry)
        self._refresh_tracks_table()
        self.tracks_table.selectRow(len(self._tracks) - 1)
        self._autostage()

    def _on_update_track(self) -> None:
        if self._selected_track is None:
            self._show_message(_("Pick a track row first."), _ERROR_STYLE)
            return
        entry = self._build_track_dict()
        if entry is None:
            return
        self._tracks[self._selected_track] = entry
        self._refresh_tracks_table()
        self.tracks_table.selectRow(self._selected_track)
        self._autostage()

    def _on_remove_track(self) -> None:
        if self._selected_track is None:
            self._show_message(_("Pick a track row first."), _ERROR_STYLE)
            return
        del self._tracks[self._selected_track]
        self._selected_track = None
        self._refresh_tracks_table()
        self._clear_track_editor()
        self._autostage()

    # ── Nested cells tab ──────────────────────────────────────────────────

    def _on_nested_selection_changed(self) -> None:
        rows = self.nested_table.selectionModel().selectedRows()
        if not rows:
            self._selected_nested = None
            return
        self._selected_nested = rows[0].row()
        n = self._nested[self._selected_nested]
        self.nested_name_edit.setText(str(n.get("name", "")))
        if n.get("cell") is not None:
            self.nested_mode_combo.setCurrentIndex(0)
            self.nested_cell_combo.setCurrentText(str(n["cell"]))
        else:
            self.nested_mode_combo.setCurrentIndex(1)
            self.nested_role_combo.setCurrentText(str(n.get("role", "")))
        self._on_nested_mode_changed()
        xy = n.get("xy") or [0.0, 0.0]
        self.nested_x_edit.setText(str(xy[0]))
        self.nested_y_edit.setText(str(xy[1]))
        self.nested_rotation_edit.setText(str(n.get("rotation_deg", "")) if n.get("rotation_deg") else "")
        self.nested_layer_combo.setCurrentIndex(self._findable(self.nested_layer_combo, n.get("layer")))
        self.nested_mirror_checkbox.setChecked(bool(n.get("mirror", False)))

    def _clear_nested_editor(self) -> None:
        self.nested_name_edit.setText("")
        self.nested_mode_combo.setCurrentIndex(0)
        self.nested_cell_combo.setCurrentText("")
        self.nested_role_combo.setCurrentText("")
        self._on_nested_mode_changed()
        self.nested_x_edit.setText("")
        self.nested_y_edit.setText("")
        self.nested_rotation_edit.setText("")
        self.nested_layer_combo.setCurrentIndex(0)
        self.nested_mirror_checkbox.setChecked(False)

    def _build_nested_dict(self) -> Optional[Dict[str, Any]]:
        name = self.nested_name_edit.text().strip()
        if not name:
            self._show_message(_("Nested cell: name is required."), _ERROR_STYLE)
            return None
        entry: Dict[str, Any] = {"name": name}
        if self.nested_mode_combo.currentIndex() == 0:
            cell = self.nested_cell_combo.currentText().strip()
            if not cell:
                self._show_message(_("Pick a Cell first."), _ERROR_STYLE)
                return None
            entry["cell"] = cell
        else:
            role = self.nested_role_combo.currentText().strip()
            if not role:
                self._show_message(_("Pick a Role first."), _ERROR_STYLE)
                return None
            entry["role"] = role

        x = self._parse_float(self.nested_x_edit, _("X"), 0.0)
        if x is None:
            return None
        y = self._parse_float(self.nested_y_edit, _("Y"), 0.0)
        if y is None:
            return None
        if x or y:
            entry["xy"] = [x, y]
        rotation = self._parse_float(self.nested_rotation_edit, _("Rotation"), 0.0)
        if rotation is None:
            return None
        if rotation:
            entry["rotation_deg"] = rotation
        layer = self.nested_layer_combo.currentData()
        if layer is not None:
            entry["layer"] = layer
        if self.nested_mirror_checkbox.isChecked():
            entry["mirror"] = True
        # nets/params/net_overrides/refs are not editable in this dock (see
        # module docstring) — preserve whatever the selected row already had.
        if self._selected_nested is not None:
            existing = self._nested[self._selected_nested]
            for field in ("nets", "params", "net_overrides", "refs"):
                if existing.get(field):
                    entry[field] = existing[field]

        try:
            load_cell_placement(self.name_edit.text().strip() or "?", entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None
        return entry

    def _on_add_nested(self) -> None:
        entry = self._build_nested_dict()
        if entry is None:
            return
        self._nested.append(entry)
        self._refresh_nested_table()
        self.nested_table.selectRow(len(self._nested) - 1)
        self._autostage()

    def _on_update_nested(self) -> None:
        if self._selected_nested is None:
            self._show_message(_("Pick a nested-cell row first."), _ERROR_STYLE)
            return
        entry = self._build_nested_dict()
        if entry is None:
            return
        self._nested[self._selected_nested] = entry
        self._refresh_nested_table()
        self.nested_table.selectRow(self._selected_nested)
        self._autostage()

    def _on_remove_nested(self) -> None:
        if self._selected_nested is None:
            self._show_message(_("Pick a nested-cell row first."), _ERROR_STYLE)
            return
        del self._nested[self._selected_nested]
        self._selected_nested = None
        self._refresh_nested_table()
        self._clear_nested_editor()
        self._autostage()

    # ── Building the Cell entry dict (Save) ──────────────────────────────

    def _build_cell_dict(self) -> Optional[tuple]:
        name = self.name_edit.text().strip()
        if not name:
            self._show_message(_("Name is required."), _ERROR_STYLE)
            return None

        entry: Dict[str, Any] = {
            "layer": self.layer_combo.currentData() or "F.Cu",
            "components": list(self._components),
            "vias": list(self._vias),
            "tracks": list(self._tracks),
            "clone_placements": list(self._nested),
        }
        comment = self.comment_edit.text().strip()
        if comment:
            entry["comment"] = comment

        mode = self.anchor_mode_combo.currentIndex()
        if mode == 1:
            x = self._parse_float(self.anchor_x_edit, _("Anchor X"), None)
            if x is None and self.anchor_x_edit.text().strip():
                return None
            y = self._parse_float(self.anchor_y_edit, _("Anchor Y"), None)
            if y is None and self.anchor_y_edit.text().strip():
                return None
            if x is None or y is None:
                self._show_message(_("Anchor XY requires both X and Y."), _ERROR_STYLE)
                return None
            entry["anchor_xy"] = [x, y]
        elif mode == 2:
            role = self.anchor_role_combo.currentText().strip()
            if not role:
                self._show_message(_("Anchor: pick a Role first."), _ERROR_STYLE)
                return None
            entry["anchor_role"] = role
            pad = self.anchor_pad_edit.text().strip()
            if pad:
                entry["anchor_pad"] = pad

        try:
            load_cell(name, entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None
        return name, entry

    # ── Save (auto-stage) ─────────────────────────────────────────────────

    def _autostage(self) -> None:
        """Cell commit point -> stage the whole cell into the working set
        (2026-09-01, plan project_save_model). Skips programmatic population
        (_loading) and unnamed cells (no name yet); _on_save validates and
        reports — an invalid cell is never staged. Wrapped in try/except: an
        unhandled exception in a PyQt6 signal slot aborts the whole process,
        so a staging bug must degrade to a log line, never a crash."""
        try:
            if self._loading or self._path is None:
                return
            if not self.name_edit.text().strip():
                return
            self._on_save()
        except Exception:
            logger.exception("cell auto-stage failed")

    def _on_save(self) -> None:
        built = self._build_cell_dict()
        if built is None:
            return
        name, entry = built
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return

        try:
            overwritten = merge_write(self._path, {"cells": {name: entry}}, section="cells")
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        self._show_message(
            _("{action} {name!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                name=name, path=display_path(self._path)),
            _SUCCESS_STYLE)
        self.saved.emit()

    # ── Refresh geometry from selection (2026-09-03, plan cell_geometry_refresh)

    def _update_refresh_enabled(self) -> None:
        """The refresh-geometry AND import-vias/tracks buttons are meaningful
        only when a live board adapter is present AND a cell with components
        is loaded (Import needs the same clean role match as Refresh, see
        build_import_plan). CellDock receives no per-selection feed (only
        push_snapshot's role lists), so an EMPTY board selection is not gated
        here — the worker reports it as a clear error at click time instead."""
        connection = getattr(self._main_window, "connection", None)
        board = getattr(connection, "board", None) if connection is not None else None
        adapter = getattr(board, "adapter", None) if board is not None else None
        enabled = adapter is not None and bool(self._components)
        self.refresh_geometry_button.setEnabled(enabled)
        self.import_vias_tracks_button.setEnabled(enabled)

    def _refresh_origin_role(self) -> str | None:
        """The cell's anchor_role to refresh/import geometry against (v2: the
        MOUNT role, frame-preserving). Only in the dock's "Role/Pad anchor"
        mode; None keeps the legacy zero-slot origin for un-anchored cells."""
        if self.anchor_mode_combo.currentIndex() == 2:
            return self.anchor_role_combo.currentText().strip() or None
        return None

    def _on_refresh_geometry(self) -> None:
        """Button/context action: read the CURRENT board selection and refresh
        the loaded cell's geometry to match it. Board IPC (selection read +
        net_from_role resolution) runs on the worker thread via start_long_op;
        the preview dialog + Apply stay on the UI thread."""
        self._show_message("")
        connection = getattr(self._main_window, "connection", None)
        board = getattr(connection, "board", None) if connection is not None else None
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            self._show_message(_("Connect to KiCad first."), _ERROR_STYLE)
            return
        if not self._components:
            self._show_message(_("Load a cell with components first."), _ERROR_STYLE)
            return
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        if self._active_op is not None:
            return
        # Snapshot the current lists — the worker reads them while the UI may
        # keep ticking; build_refresh_plan never mutates them, and the records
        # it returns are the SAME dict objects, so Apply lands on the loaded
        # lists regardless of list identity.
        payload = {
            "board": board,
            "components": list(self._components),
            "vias": list(self._vias),
            "tracks": list(self._tracks),
            "origin_role": self._refresh_origin_role(),
        }
        self._active_op = start_long_op(
            connection, (self.refresh_geometry_button,),
            self._run_refresh_geometry, self._finish_refresh_geometry,
            self._on_refresh_op_failed, payload)

    def _run_refresh_geometry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: selection read + plan build — never touches a widget.
        Returns {"plan": RefreshPlan} or {"error": ...} (a ValidationError's
        format_fatal_error text shown verbatim in a QMessageBox)."""
        try:
            adapter = payload["board"].adapter
            items = adapter.get_selected_items()
            footprints = [i for i in items if isinstance(i, Footprint)]
            vias = [i for i in items if isinstance(i, Via)]
            tracks = [i for i in items if isinstance(i, Track)]
            plan = build_refresh_plan(
                payload["components"], payload["vias"], payload["tracks"],
                footprints, vias, tracks, adapter,
                origin_role=payload.get("origin_role"),
                add_new_copper=True)
        except ValidationError as e:
            return {"error": str(e)}
        return {"plan": plan}

    def _finish_refresh_geometry(self, result: Dict[str, Any]) -> None:
        """UI thread (worker finished): a plan error is shown as a warning with
        the FULL collected text; a clean plan is previewed; on Apply the dock's
        normal mutation/autostage path runs."""
        self._active_op = None
        if result.get("error"):
            QMessageBox.warning(
                self, _("Refresh geometry from selection"), result["error"])
            return
        plan = result["plan"]
        sections = refresh_preview_sections(
            self._components, self._vias, self._tracks, plan)
        new_section = refresh_new_records_section(plan)
        if new_section is not None:
            sections.append(new_section)
        total = sum(len(s["rows"]) for s in sections)
        if not total:
            self._show_message(
                _("Nothing changed — the selection already matches this cell's geometry."),
                _SUCCESS_STYLE)
            return
        dialog = _RefreshPreviewDialog(sections, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated, added = self._apply_refresh_plan(plan)
        if added:
            self._show_message(
                _("Updated {name!r} from selection — {updated} record(s) updated, "
                  "{added} new via/track record(s) added. Save to write the change.")
                .format(name=self.name_edit.text().strip(),
                        updated=updated, added=added),
                _SUCCESS_STYLE)
        else:
            self._show_message(
                _("Refreshed {name!r} from selection — {count} record(s) updated. "
                  "Save to write the change.").format(
                    name=self.name_edit.text().strip(), count=updated),
                _SUCCESS_STYLE)

    def _on_refresh_op_failed(self, message: str) -> None:
        self._active_op = None
        self._show_message(
            _("Refresh failed: {error}").format(error=message), _ERROR_STYLE)

    def _apply_refresh_plan(self, plan) -> tuple:
        """Apply a RefreshPlan to the loaded cell: mutate ONLY the geometric
        keys on the SAME dict objects already in self._components/_vias/
        _tracks (plan records ARE those dicts), then APPEND the plan's
        brand-new via/track records (add_new_copper mode, 2026-09-05) to
        self._vias/_tracks (extend, never replace), then refresh tables +
        autostage exactly like a manual row Update/Add. Returns
        (updated_count, added_count). Nothing is written to disk here — Save
        remains a separate explicit action, as everywhere in this dock."""
        updated = 0
        for record, new_geo in (plan.component_updates + plan.via_updates
                                + plan.track_updates):
            record.update(new_geo)
            updated += 1
        added = len(plan.new_via_records) + len(plan.new_track_records)
        self._vias.extend(plan.new_via_records)
        self._tracks.extend(plan.new_track_records)
        self._refresh_all_tables()
        self._autostage()
        return updated, added

    def refresh_from_selection_requested(self, name: str, file_path) -> None:
        """ConfigTreeDock's cell_refresh_requested delegate (2026-09-03) — the
        context menu's "Update from selection...": when the requested cell is
        not the one currently loaded, load it first, then run the same
        _on_refresh_geometry path as the dock's own button."""
        if self.name_edit.text().strip() != name or self._path != file_path:
            self.load_entry(name, file_path)
        self._on_refresh_geometry()

    # ── Import vias/tracks from selection (2026-09-03, plan
    #    fpga_oscill_missing_copper_and_cell_import §B.3) ─────────────────

    def _on_import_vias_tracks(self) -> None:
        """Button/context action: read the CURRENT board selection and import
        the live via/track copper it describes that the loaded cell's current
        records don't (backfill). Board IPC (selection read + net_from_role
        resolution) runs on the worker thread via start_long_op; the preview
        dialog + Apply stay on the UI thread."""
        self._show_message("")
        connection = getattr(self._main_window, "connection", None)
        board = getattr(connection, "board", None) if connection is not None else None
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            self._show_message(_("Connect to KiCad first."), _ERROR_STYLE)
            return
        if not self._components:
            self._show_message(_("Load a cell with components first."), _ERROR_STYLE)
            return
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        if self._active_op is not None:
            return
        # Snapshot the current lists — the worker reads them while the UI may
        # keep ticking; build_import_plan never mutates them, and the plan's
        # new records are brand-new dicts to APPEND on Apply (existing records
        # are untouched by construction).
        payload = {
            "board": board,
            "components": list(self._components),
            "vias": list(self._vias),
            "tracks": list(self._tracks),
            "origin_role": self._refresh_origin_role(),
        }
        self._active_op = start_long_op(
            connection, (self.import_vias_tracks_button,),
            self._run_import_vias_tracks, self._finish_import_vias_tracks,
            self._on_import_op_failed, payload)

    def _run_import_vias_tracks(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: selection read + plan build — never touches a widget.
        Returns {"plan": ImportPlan} or {"error": ...} (a ValidationError's
        format_fatal_error text shown verbatim in a QMessageBox)."""
        try:
            adapter = payload["board"].adapter
            items = adapter.get_selected_items()
            footprints = [i for i in items if isinstance(i, Footprint)]
            vias = [i for i in items if isinstance(i, Via)]
            tracks = [i for i in items if isinstance(i, Track)]
            plan = build_import_plan(
                payload["components"], payload["vias"], payload["tracks"],
                footprints, vias, tracks, adapter,
                origin_role=payload.get("origin_role"))
        except ValidationError as e:
            return {"error": str(e)}
        return {"plan": plan}

    def _finish_import_vias_tracks(self, result: Dict[str, Any]) -> None:
        """UI thread (worker finished): a plan error is shown as a warning with
        the FULL collected text; a clean plan is previewed (one row per NEW
        record); on Apply the dock's normal append/autostage path runs."""
        self._active_op = None
        if result.get("error"):
            QMessageBox.warning(
                self, _("Import vias/tracks from selection"), result["error"])
            return
        plan = result["plan"]
        rows = import_preview_rows(plan)
        if not rows:
            self._show_message(
                _("Nothing to import — every selected via/track is already "
                  "described by this cell."),
                _SUCCESS_STYLE)
            return
        dialog = _ImportPreviewDialog(rows, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        applied = self._apply_import_plan(plan)
        self._show_message(
            _("Imported {count} new record(s) from selection into {name!r}. "
              "Save to write the change.").format(
                count=applied, name=self.name_edit.text().strip()),
            _SUCCESS_STYLE)

    def _on_import_op_failed(self, message: str) -> None:
        self._active_op = None
        self._show_message(
            _("Import failed: {error}").format(error=message), _ERROR_STYLE)

    def _apply_import_plan(self, plan) -> int:
        """Apply an ImportPlan to the loaded cell: APPEND the plan's brand-new
        via/track records to self._vias/self._tracks (extend, never replace —
        plan §B.3), then refresh tables + autostage exactly like a manual row
        Add. Existing records are untouched by construction. Returns how many
        records were appended. Nothing is written to disk here — Save remains
        a separate explicit action, as everywhere in this dock."""
        self._vias.extend(plan.new_via_records)
        self._tracks.extend(plan.new_track_records)
        count = len(plan.new_via_records) + len(plan.new_track_records)
        self._refresh_all_tables()
        self._autostage()
        return count

    def import_from_selection_requested(self, name: str, file_path) -> None:
        """ConfigTreeDock's cell_import_requested delegate (2026-09-03) — the
        context menu's "Import from selection...": when the requested cell is
        not the one currently loaded, load it first, then run the same
        _on_import_vias_tracks path as the dock's own button."""
        if self.name_edit.text().strip() != name or self._path != file_path:
            self.load_entry(name, file_path)
        self._on_import_vias_tracks()

    # ── Copy placement from cell (2026-09-06, plan copy_placement_from_cell)

    def copy_placement_from_cell(self) -> None:
        """Config tree's "Copy placement from cell..." (context menu on a Cells
        leaf, via DockHub): copy the PLACEMENT of a suitable donor cell onto
        THIS loaded cell. The donor is picked in a MINIMAL dialog holding only
        a combobox of the cells that FIT by role set (Denis 2026-09-06 — no
        preview tables). build_placement_copy_plan still validates on Apply: a
        fatal is shown as a warning BEFORE anything changes; a clean plan is
        applied through the dock's normal overlay/append/autostage path. Purely
        config-level — no live board, no worker."""
        self._show_message("")
        current = self.name_edit.text().strip()
        if not current:
            self._show_message(
                _("Pick a cell to copy into first."), _ERROR_STYLE)
            return
        if self._root_path is None or self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        if not self._components:
            self._show_message(_("Load a cell with components first."), _ERROR_STYLE)
            return
        entries = collect_section_entries(self._root_path, "cells")
        candidates = donor_candidates_for(entries, current, self._components)
        if not candidates:
            self._show_message(
                _("No other cell fits this one's roles to copy from."), _ERROR_STYLE)
            return
        dialog = _CopyPlacementDialog(candidates, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        source = dialog.source()
        entry = entries.get(source) or {}
        try:
            plan = build_placement_copy_plan(
                list(entry.get("components") or []),
                list(entry.get("vias") or []),
                list(entry.get("tracks") or []),
                list(self._components))
        except ValidationError as e:
            QMessageBox.warning(
                self, _("Copy placement from cell"), str(e))
            return
        added_copper = self._apply_copy_plan(plan)
        self._show_message(
            _("Copied placement from {source!r} into {name!r} — {components} "
              "component(s) updated, {copper} via/track record(s) added. "
              "Save to write the change.")
            .format(source=source, name=current,
                    components=len(plan.component_updates),
                    copper=added_copper),
            _SUCCESS_STYLE)

    def copy_from_cell_requested(self, name: str, file_path) -> None:
        """ConfigTreeDock's cell_copy_requested delegate (2026-09-06) — the
        context menu's "Copy placement from cell...": when the requested cell is
        not the one currently loaded, load it first, then run the same
        copy_placement_from_cell path as the context action."""
        if self.name_edit.text().strip() != name or self._path != file_path:
            self.load_entry(name, file_path)
        self.copy_placement_from_cell()

    def _apply_copy_plan(self, plan) -> int:
        """Apply a PlacementCopyPlan to the loaded cell: overlay the geometric
        keys on the SAME target component dict objects already in
        self._components (the plan's slots ARE those dicts), then APPEND the
        deep-copied donor vias/tracks to self._vias/self._tracks (extend,
        never replace), then refresh tables + autostage exactly like
        Refresh/Import. The target's net_template fields are untouched by
        construction — a plan's new_geo never carries them. Returns how many
        via/track records were appended. Nothing is written to disk here —
        Save remains a separate explicit action, as everywhere in this dock."""
        for record, new_geo in plan.component_updates:
            record.update(new_geo)
        self._vias.extend(plan.new_via_records)
        self._tracks.extend(plan.new_track_records)
        count = len(plan.new_via_records) + len(plan.new_track_records)
        self._refresh_all_tables()
        self._autostage()
        return count

    # ── Starting a brand new entry (ConfigTreeDock's Add cell...) ────────

    def new_cell(self, path: Path) -> None:
        """Resets the form to its initial (blank) state — ConfigTreeDock's
        "Add cell..." context-menu action opens this form empty instead of
        writing a raw {"components": []} stub straight to YAML (the exact
        root cause of the Conn_PM5V bug this dock was built to fix — see
        module docstring), same reasoning as PlacerDock.new_placement()/
        ThermalViaArrayDock.new_thermal_via()/PointsDock.new_point()/
        RuleDock.new_rule(). The entry is written to the project root file
        (2026-08-21), so the passed path is ignored."""
        self._loading = True
        try:
            self._path = self._root_path
            self.name_edit.setText("")
            self.comment_edit.setText("")
            self.layer_combo.setCurrentIndex(0)
            self.anchor_mode_combo.setCurrentIndex(0)
            self.anchor_x_edit.setText("")
            self.anchor_y_edit.setText("")
            self.anchor_role_combo.setCurrentText("")
            self.anchor_pad_edit.setText("")
            self._on_anchor_mode_changed()
            self._components = []
            self._vias = []
            self._tracks = []
            self._nested = []
            self._selected_component = None
            self._selected_via = None
            self._selected_track = None
            self._selected_nested = None
            self._refresh_all_tables()
            self._clear_component_editor()
            self._clear_via_editor()
            self._clear_track_editor()
            self._clear_nested_editor()
        finally:
            self._loading = False
        self._show_message("")

    # ── Loading an already-saved entry back into the form ───────────────

    def load_entry(self, name: str, file_path: Optional[Path] = None) -> None:
        """Reverse of _build_cell_dict() — called by ConfigTreeDock's Cells
        category (via cell_edit_requested, NOT cell_picked — see config_
        tree.py's module docstring on why editing needs its own action
        distinct from "pick this cell as a placement's content") when an
        already-saved entry is clicked. cells: is a DICT section (see
        module docstring), so the signal only carries the name — the actual
        data is re-read fresh from the WHOLE include graph here (a cell can
        live in any included file). `file_path` (from cell_edit_requested)
        is the file the entry lives in; the WRITE target is set back to it
        so a Save updates that file instead of duplicating the cell into the
        root (2026-08-21 review fix)."""
        self._show_message("")
        if file_path is None:
            file_path = find_dict_entry_file(self._root_path, "cells", name)
        if file_path is not None:
            self._path = file_path
        entry = {}
        if self._root_path is not None:
            entry = collect_section_entries(self._root_path, "cells").get(name) or {}
        self._loading = True
        try:
            self.name_edit.setText(name)
            self.comment_edit.setText(str(entry.get("comment") or ""))
            self.layer_combo.setCurrentIndex(self._findable(self.layer_combo, entry.get("layer", "F.Cu")))

            if "anchor_xy" in entry:
                self.anchor_mode_combo.setCurrentIndex(1)
                xy = entry["anchor_xy"] or [0, 0]
                self.anchor_x_edit.setText(str(xy[0]))
                self.anchor_y_edit.setText(str(xy[1]))
            elif "anchor_role" in entry:
                self.anchor_mode_combo.setCurrentIndex(2)
                self.anchor_role_combo.setCurrentText(str(entry["anchor_role"]))
                self.anchor_pad_edit.setText(str(entry.get("anchor_pad", "")))
            else:
                self.anchor_mode_combo.setCurrentIndex(0)
                self.anchor_x_edit.setText("")
                self.anchor_y_edit.setText("")
                self.anchor_role_combo.setCurrentText("")
                self.anchor_pad_edit.setText("")
            self._on_anchor_mode_changed()

            self._components = [dict(c) for c in (entry.get("components") or [])]
            self._vias = [dict(v) for v in (entry.get("vias") or [])]
            self._tracks = [dict(t) for t in (entry.get("tracks") or [])]
            self._nested = [dict(n) for n in (entry.get("clone_placements") or [])]
            self._selected_component = None
            self._selected_via = None
            self._selected_track = None
            self._selected_nested = None
            self._refresh_all_tables()
            self._clear_component_editor()
            self._clear_via_editor()
            self._clear_track_editor()
            self._clear_nested_editor()
        finally:
            self._loading = False
