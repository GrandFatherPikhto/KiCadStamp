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
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout,
                              QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                              QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from kicadstamp.config import (load_cell, load_cell_placement, load_template_component_slot,
                               load_template_track, load_template_via)
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from .. import yaml_io
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, display_path, merge_write, set_combo_items,
                      show_message)
from .rename import collect_all_cell_names

logger = logging.getLogger(__name__)

_LAYER_ITEMS = [("F.Cu", "F.Cu"), ("B.Cu", "B.Cu")]
_INHERIT_LAYER_ITEMS = [(_("(inherit cell layer)"), None), ("F.Cu", "F.Cu"), ("B.Cu", "B.Cu")]

_COMPONENT_COLUMNS = ["Role", "Offset along", "Offset across", "Angle", "Layer", "Net template"]
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


class CellDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — same
    "plain QWidget, not its own QDockWidget" shape as Extract/Placer/
    Project/Thermal via/Points/Rules."""

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.target_label = QLabel(_("No file picked (pick one in the Config tree)"))
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        head_form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("name (referenced by cell: elsewhere)"))
        head_form.addRow(_("Name:"), self.name_edit)
        self.layer_combo = _layer_combo(_LAYER_ITEMS)
        head_form.addRow(_("Layer:"), self.layer_combo)
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

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)
        self._build_components_tab()
        self._build_vias_tab()
        self._build_tracks_tab()
        self._build_nested_tab()

        button_row = QHBoxLayout()
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

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

    def set_target_file(self, path: Optional[Path]) -> None:
        self._path = path
        self.target_label.setText(
            display_path(path) if path is not None
            else _("No file picked (pick one in the Config tree)"))

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — the Nested cells
        tab's Cell combo is sourced from the WHOLE include graph (same
        reasoning as RuleDock's spoke.cell combo), not just this dock's own
        target file (a nested cell routinely lives in a different file)."""
        self._root_path = path
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
        show_message(self.message_label, text, style, logger)

    def _parse_float(self, edit: QLineEdit, label: str, default: Optional[float]) -> Optional[float]:
        text = edit.text().strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            self._show_message(_("{label}: {text!r} is not a number.").format(label=label, text=text),
                               _ERROR_STYLE)
            return None

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

    def _clear_component_editor(self) -> None:
        self.comp_role_edit.setCurrentText("")
        self.comp_offset_along_edit.setText("")
        self.comp_offset_across_edit.setText("")
        self.comp_angle_edit.setText("")
        self.comp_layer_combo.setCurrentIndex(0)
        self.comp_net_template_edit.setText("")

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
        self._show_message(_("Component added — remember to Save the cell."), _SUCCESS_STYLE)

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
        self._show_message(_("Component updated — remember to Save the cell."), _SUCCESS_STYLE)

    def _on_remove_component(self) -> None:
        if self._selected_component is None:
            self._show_message(_("Pick a component row first."), _ERROR_STYLE)
            return
        del self._components[self._selected_component]
        self._selected_component = None
        self._refresh_components_table()
        self._clear_component_editor()
        self._show_message(_("Component removed — remember to Save the cell."), _SUCCESS_STYLE)

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
        self._show_message(_("Via added — remember to Save the cell."), _SUCCESS_STYLE)

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
        self._show_message(_("Via updated — remember to Save the cell."), _SUCCESS_STYLE)

    def _on_remove_via(self) -> None:
        if self._selected_via is None:
            self._show_message(_("Pick a via row first."), _ERROR_STYLE)
            return
        del self._vias[self._selected_via]
        self._selected_via = None
        self._refresh_vias_table()
        self._clear_via_editor()
        self._show_message(_("Via removed — remember to Save the cell."), _SUCCESS_STYLE)

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
        self._show_message(_("Track added — remember to Save the cell."), _SUCCESS_STYLE)

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
        self._show_message(_("Track updated — remember to Save the cell."), _SUCCESS_STYLE)

    def _on_remove_track(self) -> None:
        if self._selected_track is None:
            self._show_message(_("Pick a track row first."), _ERROR_STYLE)
            return
        del self._tracks[self._selected_track]
        self._selected_track = None
        self._refresh_tracks_table()
        self._clear_track_editor()
        self._show_message(_("Track removed — remember to Save the cell."), _SUCCESS_STYLE)

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
        self._show_message(_("Nested cell added — remember to Save the cell."), _SUCCESS_STYLE)

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
        self._show_message(_("Nested cell updated — remember to Save the cell."), _SUCCESS_STYLE)

    def _on_remove_nested(self) -> None:
        if self._selected_nested is None:
            self._show_message(_("Pick a nested-cell row first."), _ERROR_STYLE)
            return
        del self._nested[self._selected_nested]
        self._selected_nested = None
        self._refresh_nested_table()
        self._clear_nested_editor()
        self._show_message(_("Nested cell removed — remember to Save the cell."), _SUCCESS_STYLE)

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

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        built = self._build_cell_dict()
        if built is None:
            return
        name, entry = built
        if self._path is None:
            self._show_message(_("Pick a file in the Config tree first."), _ERROR_STYLE)
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

    # ── Starting a brand new entry (ConfigTreeDock's Add cell...) ────────

    def new_cell(self, path: Path) -> None:
        """Resets the form to its initial (blank) state and targets path —
        ConfigTreeDock's "Add cell..." context-menu action opens this form
        empty instead of writing a raw {"components": []} stub straight to
        YAML (the exact root cause of the Conn_PM5V bug this dock was built
        to fix — see module docstring), same reasoning as PlacerDock.
        new_placement()/ThermalViaArrayDock.new_thermal_via()/PointsDock.
        new_point()/RuleDock.new_rule()."""
        self.set_target_file(path)
        self.name_edit.setText("")
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
        self._show_message("")

    # ── Loading an already-saved entry back into the form ───────────────

    def load_entry(self, name: str) -> None:
        """Reverse of _build_cell_dict() — called by ConfigTreeDock's Cells
        category (via cell_edit_requested, NOT cell_picked — see config_
        tree.py's module docstring on why editing needs its own action
        distinct from "pick this cell as a placement's content") when an
        already-saved entry is clicked. cells: is a DICT section (see
        module docstring), so the signal only carries the name — the
        actual data is re-read fresh from self._path here, same discipline
        PointsDock.load_entry already uses."""
        self._show_message("")
        entry = {}
        if self._path is not None:
            entry = (yaml_io.load_data(self._path).get("cells") or {}).get(name) or {}
        self.name_edit.setText(name)
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
