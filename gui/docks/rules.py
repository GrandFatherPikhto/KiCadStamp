# gui/docks/rules.py
"""
RuleDock — edits a `rules:` entry (kicadstamp/config/models.py's Rule +
ManualSpoke): one shared anchor (Ref/Role, narrowed by Sheet/Cluster, or a
named Point) plus an ordered list of spokes, each placing a Cell at a
specific pad of that anchor with its own hand-tuned shift/rotation.
Requested live 2026-08-05 after Denis connected fpga_spokes.yaml/
fpga_cap_pair_spoke.yaml to a real project and hit the long-flagged gap
(config_tree.py's own module docstring) that Rules had no edit form at
all.

Table + detail-panel-below, not a form-per-spoke or inline table editing —
picked live over putting spokes in the shared Config tree (Denis: "может
её сразу в общем дереве?"): a spoke has no name field to hang a tree leaf
label on, spoke ORDER is semantically significant (ComponentPool consumes
spokes in list order — see ManualSpoke's own docstring), and a table's
columns show every spoke's shift/rotation/cluster at a glance, which a
tree of bare leaf labels could not. The table itself is read-only
(NoEditTriggers) — all editing goes through the detail row below and its
explicit Add/Update/Remove/Move buttons, so a row's displayed values can
never drift from what Add/Update actually validated and stored.

Origin has only two modes (Anchor ref/role / Point) — Rule has no `xy`
field at all, unlike Points/PlacerDock's three-way combo.

Net/Origin/Spoke are tabbed (2026-08-05, Denis: "опять проблема с местом в
Rules... давай сделаем табы") — same "QVBoxLayout's minimum height is the
SUM of every section's own" problem Extract (2026-08-04) and Root/Project
(2026-08-05) already hit, same QTabWidget fix. Net carries the rule's own
identity+status (Net/Name/Retired/Skip); Origin carries the anchor-mode
combo and its two rows; Spoke carries every field the detail-row editor
below the table writes into (Pad/Cell/Cluster/Shift/Rotation/Retired/Skip).
The spokes table itself, its Move up/down, the Add/Update/Remove row, and
Redraw/Save all stay OUTSIDE the tabs — they act across whichever tab is
open, not scoped to one. Considered renaming the tab/dock "Spokes" instead
of "Rules" (Denis's suggestion) but kept "Rules": Net+Origin describe the
whole Rule, not just its spokes, and the config key is rules: — same
"keep the accurate name, don't undersell part of it" call as Root/Project.

spoke.cell is a searchable combo (Denis: "Чтобы назначать разные целлы
разным спицам? Да, думаю комбобоксик") sourced from collect_all_cell_names()
(gui/docks/rename.py) — EVERY cells: key reachable from the project's root
via include:, not just this file's own (a spoke's cell routinely lives in
a different file than the rule referencing it). Point-chain (this dock's
own anchor AND has no per-spoke equivalent — spokes anchor to a pad on
THIS rule's own anchor, never to an arbitrary point) is populated the same
whole-graph way via collect_all_point_names(). Both need the PROJECT's
root path, not this dock's own target file (which follows file_selected
like Placer/ThermalVia) — set_root_path() is wired to RootMetadataDock's
root_changed, the same second file-dependency Points' own docstring
flagged and deferred; here it's wired up front since the combo was
explicitly requested.

Redraw comes in two flavours (Denis: "Redraw Rule, Redraw (выбранная
спица) по-моему, так будет логично"):
  - Redraw Rule — the whole rule, all non-skipped spokes, same
    replace-by-name + ApplyPipeline(only=[...]) shape as ThermalViaArrayDock.
  - Redraw (selected spoke) — same, but every OTHER spoke gets a temporary
    skip=True injected into the copy handed to ApplyPipeline (never
    written back — Save is unaffected). Sound because spoke resolution
    shares ONE ComponentPool per net across the whole rule (see
    ManualPositionCalculator/ComponentPool) — you cannot resolve a single
    spoke in isolation without the rest of the pool, but you CAN ask the
    pipeline to skip every spoke except the one you want to see, which
    `skip:` already exists to do (see Rule's own docstring on skip vs
    retired).

Save writes via upsert_list_entry(key_fn=...) matching by
rule_effective_name (name if set, else net) — rules: is the one list
section without a REQUIRED name: field (see config/models.py's
rule_effective_name), unlike clone_placements:/thermal_via_arrays: which
always require one.

Spoke edits autosave (2026-08-20, plan rule_spoke_fixes stage 2): "editing
finished" on any spoke editor field (blur/Enter/combo pick/checkbox) does
what 'Update selected' + 'Save' used to do together — rebuild the selected
spoke and immediately persist the whole rule via upsert_list_entry. Add/
Update/Remove/Move also persist right away, so no spoke-level change ever
needs an explicit Save. The Save button remains, but only for the rule's
OWN Net/Origin fields (the one thing left without an editingFinished hook).
Every failed autosave is reported to the Log dock — never silent — so an
edit that could not be written is never mistaken for one that was.
"""
import dataclasses
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from kipy.errors import ApiError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout,
                              QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                              QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import (Config, RuntimeContext, load_config, load_manual_spoke,
                               load_rule, rule_effective_name)
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.i18n import _

from ..worker import start_long_op
from ._anchor_origin import AnchorOriginWidget
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, display_path, parse_float_field,
                      set_combo_items, set_mode_pair_enabled, show_message,
                      upsert_list_entry)
from .rename import (collect_all_cell_names, collect_all_point_names,
                     collect_all_rule_nets, collect_all_sheet_names,
                     collect_rules_by_net, find_list_entry_file)

logger = logging.getLogger(__name__)

_COLUMNS = ["Pad", "Cell", "Mode", "Shift X", "Shift Y", "Radius", "Angle",
            "Rotation", "Cluster", "Retired", "Skip"]


def _rule_identity(entry: Dict[str, Any]) -> Any:
    """upsert_list_entry's key_fn — mirrors rule_effective_name() at the
    raw-dict level (Save hasn't necessarily built a Rule object yet)."""
    return entry.get("name") or entry.get("net")


class BulkSetCellDialog(QDialog):
    """Stage 3 (2026-08-20, plan rule_spoke_fixes): pick a net + a new cell
    and PREVIEW the exact rules/spokes it will change before writing anything
    (a net's rules routinely live in DIFFERENT included files — see
    collect_rules_by_net). Pure selection + preview, read-only: the write
    itself happens in RuleDock._apply_bulk_cell_set, which reports per-rule
    success/failure to the Log dock (never a silent partial write)."""

    def __init__(self, root_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Bulk-set Cell for net"))
        self._root_path = root_path
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.net_combo = QComboBox()
        configure_searchable(self.net_combo)
        set_combo_items(self.net_combo, collect_all_rule_nets(root_path))
        form.addRow(_("Net:"), self.net_combo)
        self.cell_combo = QComboBox()
        configure_searchable(self.cell_combo)
        set_combo_items(self.cell_combo, collect_all_cell_names(root_path))
        form.addRow(_("New Cell:"), self.cell_combo)
        layout.addLayout(form)

        self.preview_label = QLabel()
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        buttons = QHBoxLayout()
        self.apply_button = QPushButton(_("Apply"))
        self.apply_button.clicked.connect(self.accept)
        cancel_button = QPushButton(_("Cancel"))
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        # Preview refreshes on combo change (typing too — it's a read-only
        # label update, not a write, so per-keystroke is fine).
        self.net_combo.activated.connect(self._refresh_preview)
        self.net_combo.editTextChanged.connect(self._refresh_preview)
        self.cell_combo.activated.connect(self._refresh_preview)
        self.cell_combo.editTextChanged.connect(self._refresh_preview)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        net = self.net_combo.currentText().strip()
        if not net:
            self.preview_label.setText(_("Pick a net."))
            return
        affected = collect_rules_by_net(self._root_path, net)
        if not affected:
            self.preview_label.setText(_("No rules on net {net!r}.").format(net=net))
            return
        total_spokes = sum(len(rule.get("spokes") or []) for _, rule in affected)
        lines = [
            _("{name} in {file} (pads: {pads})").format(
                name=_rule_identity(rule), file=path.name,
                pads=", ".join(str(s.get("pad", "?")) for s in (rule.get("spokes") or [])))
            for path, rule in affected
        ]
        self.preview_label.setText(
            _("Will set cell {cell!r} on {n} rule(s) / {m} spoke(s) of net {net!r}:\n{lines}")
            .format(cell=self.cell_combo.currentText().strip(), n=len(affected),
                    m=total_spokes, net=net, lines="\n".join(lines)))


class RuleDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — same
    "plain QWidget, not its own QDockWidget" shape as Extract/Placer/
    Project/Thermal via/Points."""

    # Fired after a successful Save — ConfigTreeDock listens to refresh its
    # Rules category (see gui/dock_hub.py), same as every other dock here.
    saved = pyqtSignal()

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self._active_op: Optional[Any] = None
        self._path: Optional[Path] = None
        self._root_path: Optional[Path] = None
        self._spokes: List[Dict[str, Any]] = []
        self._selected_index: Optional[int] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Tabbed (2026-08-05) — see module docstring for why Net/Origin/
        # Spoke split this way and why the table+buttons below stay outside
        # the tabs.
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)

        net_page = QWidget()
        net_page_layout = QVBoxLayout(net_page)
        rule_form = QFormLayout()
        self.net_edit = QComboBox()
        configure_searchable(self.net_edit)
        rule_form.addRow(_("Net:"), self.net_edit)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("optional — defaults to net for --only"))
        rule_form.addRow(_("Name:"), self.name_edit)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText(_("optional free-form note"))
        rule_form.addRow(_("Comment:"), self.comment_edit)
        net_page_layout.addLayout(rule_form)
        checks_row = QHBoxLayout()
        self.retired_checkbox = QCheckBox(_("Retired"))
        self.skip_checkbox = QCheckBox(_("Skip"))
        checks_row.addWidget(self.retired_checkbox)
        checks_row.addWidget(self.skip_checkbox)
        net_page_layout.addLayout(checks_row)
        net_page_layout.addStretch(1)
        self._tabs.addTab(net_page, _("Net"))

        origin_page = QWidget()
        origin_page_layout = QVBoxLayout(origin_page)
        self.origin_widget = AnchorOriginWidget(modes=["anchor", "point"], anchor_fields=["sheet", "cluster"])
        origin_page_layout.addWidget(self.origin_widget)
        # Aliases onto the shared widget's own sub-widgets — kept so
        # existing tests/call sites that poke fields directly keep working.
        self.origin_mode_combo = self.origin_widget.origin_mode_combo
        self.anchor_ref_edit = self.origin_widget.anchor_ref_edit
        self.anchor_role_edit = self.origin_widget.anchor_role_edit
        self.anchor_sheet_edit = self.origin_widget.anchor_sheet_edit
        self.anchor_cluster_edit = self.origin_widget.anchor_cluster_edit
        self.point_edit = self.origin_widget.point_edit
        origin_page_layout.addStretch(1)
        self._tabs.addTab(origin_page, _("Origin"))

        spoke_page = QWidget()
        spoke_page_layout = QVBoxLayout(spoke_page)
        spoke_form = QFormLayout()
        self.spoke_pad_edit = QLineEdit()
        self.spoke_pad_edit.setPlaceholderText(_("pad number on the rule's own anchor"))
        spoke_form.addRow(_("Pad:"), self.spoke_pad_edit)
        self.spoke_cell_combo = QComboBox()
        configure_searchable(self.spoke_cell_combo)
        spoke_form.addRow(_("Cell:"), self.spoke_cell_combo)
        self.spoke_cluster_combo = QComboBox()
        configure_searchable(self.spoke_cluster_combo)
        spoke_form.addRow(_("Cluster:"), self.spoke_cluster_combo)
        # Position mode — Cartesian shift (default) vs Polar radius+angle,
        # the same two mutually-exclusive modes ManualSpoke itself has (see
        # config/models.py). The combo only toggles WHICH fields the editor
        # writes; the loader's own "both set / neither fully set is fatal"
        # rule still applies on Save/Redraw.
        self.spoke_mode_combo = QComboBox()
        self.spoke_mode_combo.addItems([_("Cartesian"), _("Polar")])
        self.spoke_mode_combo.currentIndexChanged.connect(self._update_spoke_mode)
        spoke_form.addRow(_("Mode:"), self.spoke_mode_combo)
        spoke_page_layout.addLayout(spoke_form)

        spoke_shift_row = QHBoxLayout()
        self.spoke_shift_x_edit = QLineEdit()
        self.spoke_shift_x_edit.setPlaceholderText(_("shift X mm (0)"))
        self.spoke_shift_y_edit = QLineEdit()
        self.spoke_shift_y_edit.setPlaceholderText(_("shift Y mm (0)"))
        spoke_shift_row.addWidget(QLabel(_("Shift X:")))
        spoke_shift_row.addWidget(self.spoke_shift_x_edit)
        spoke_shift_row.addWidget(QLabel(_("Shift Y:")))
        spoke_shift_row.addWidget(self.spoke_shift_y_edit)
        spoke_page_layout.addLayout(spoke_shift_row)

        spoke_polar_row = QHBoxLayout()
        self.spoke_radius_edit = QLineEdit()
        self.spoke_radius_edit.setPlaceholderText(_("radius mm"))
        self.spoke_angle_edit = QLineEdit()
        self.spoke_angle_edit.setPlaceholderText(_("angle deg"))
        spoke_polar_row.addWidget(QLabel(_("Radius:")))
        spoke_polar_row.addWidget(self.spoke_radius_edit)
        spoke_polar_row.addWidget(QLabel(_("Angle:")))
        spoke_polar_row.addWidget(self.spoke_angle_edit)
        spoke_page_layout.addLayout(spoke_polar_row)
        self._update_spoke_mode()

        spoke_extra_form = QFormLayout()
        self.spoke_rotation_edit = QLineEdit()
        self.spoke_rotation_edit.setPlaceholderText("0")
        spoke_extra_form.addRow(_("Rotation (deg):"), self.spoke_rotation_edit)
        spoke_page_layout.addLayout(spoke_extra_form)

        spoke_checks_row = QHBoxLayout()
        self.spoke_retired_checkbox = QCheckBox(_("Retired"))
        self.spoke_skip_checkbox = QCheckBox(_("Skip"))
        spoke_checks_row.addWidget(self.spoke_retired_checkbox)
        spoke_checks_row.addWidget(self.spoke_skip_checkbox)
        spoke_page_layout.addLayout(spoke_checks_row)
        spoke_page_layout.addStretch(1)
        self._tabs.addTab(spoke_page, _("Spoke"))

        # Stage 2 (2026-08-20, plan rule_spoke_fixes): spoke edits autosave.
        # "Editing finished" on any spoke field (blur/Enter/combo pick/
        # checkbox) immediately does what 'Update selected' + 'Save' used to
        # do together — see _autosave_current_spoke. PyQt drops a signal's
        # extra args for a no-arg Python slot (same as spoke_mode_combo's
        # currentIndexChanged -> _update_spoke_mode above).
        for edit in (self.spoke_pad_edit, self.spoke_shift_x_edit,
                     self.spoke_shift_y_edit, self.spoke_radius_edit,
                     self.spoke_angle_edit, self.spoke_rotation_edit):
            edit.editingFinished.connect(self._autosave_current_spoke)
        for combo in (self.spoke_cell_combo, self.spoke_cluster_combo):
            combo.activated.connect(self._autosave_current_spoke)
            # An editable QComboBox has no editingFinished of its own — the
            # signal lives on its internal QLineEdit (Enter/blur on typed
            # text); 'activated' above covers picking from the dropdown.
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.editingFinished.connect(self._autosave_current_spoke)
        self.spoke_mode_combo.activated.connect(self._autosave_current_spoke)
        self.spoke_retired_checkbox.clicked.connect(self._autosave_current_spoke)
        self.spoke_skip_checkbox.clicked.connect(self._autosave_current_spoke)

        layout.addWidget(QLabel(_("Spokes:")))
        self.spokes_table = QTableWidget(0, len(_COLUMNS))
        self.spokes_table.setHorizontalHeaderLabels([_(c) for c in _COLUMNS])
        self.spokes_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.spokes_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.spokes_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.spokes_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.spokes_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        layout.addWidget(self.spokes_table, 1)

        move_row = QHBoxLayout()
        self.move_up_button = QPushButton(_("Move up"))
        self.move_up_button.clicked.connect(lambda: self._on_move_spoke(-1))
        move_row.addWidget(self.move_up_button)
        self.move_down_button = QPushButton(_("Move down"))
        self.move_down_button.clicked.connect(lambda: self._on_move_spoke(1))
        move_row.addWidget(self.move_down_button)
        layout.addLayout(move_row)

        spoke_button_row = QHBoxLayout()
        self.add_spoke_button = QPushButton(_("Add spoke"))
        self.add_spoke_button.clicked.connect(self._on_add_spoke)
        spoke_button_row.addWidget(self.add_spoke_button)
        self.update_spoke_button = QPushButton(_("Update selected"))
        self.update_spoke_button.clicked.connect(self._on_update_spoke)
        spoke_button_row.addWidget(self.update_spoke_button)
        self.remove_spoke_button = QPushButton(_("Remove selected"))
        self.remove_spoke_button.clicked.connect(self._on_remove_spoke)
        spoke_button_row.addWidget(self.remove_spoke_button)
        layout.addLayout(spoke_button_row)

        button_row = QHBoxLayout()
        self.redraw_rule_button = QPushButton(_("Redraw rule"))
        self.redraw_rule_button.clicked.connect(self._on_redraw_rule)
        button_row.addWidget(self.redraw_rule_button)
        self.redraw_spoke_button = QPushButton(_("Redraw selected spoke"))
        self.redraw_spoke_button.clicked.connect(self._on_redraw_spoke)
        button_row.addWidget(self.redraw_spoke_button)
        self.bulk_set_cell_button = QPushButton(_("Bulk set Cell for net…"))
        self.bulk_set_cell_button.clicked.connect(self._on_bulk_set_cell)
        button_row.addWidget(self.bulk_set_cell_button)
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self._refresh_table()

    # ── Wiring from the Config tree ─────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — new rules: entries are
        always written to the project root file (2026-08-21, plan
        flatten_and_single_file_gui), so the write target IS the root. The
        Cell/Point combos stay sourced from the WHOLE include graph."""
        self._root_path = path
        self._path = path
        self._refresh_cell_names()
        self._refresh_point_names()
        self._refresh_sheet_names()

    def _refresh_cell_names(self) -> None:
        names = collect_all_cell_names(self._root_path) if self._root_path is not None else []
        set_combo_items(self.spoke_cell_combo, names)

    def _refresh_point_names(self) -> None:
        names = collect_all_point_names(self._root_path) if self._root_path is not None else []
        self.origin_widget.set_point_names(names)

    def _refresh_sheet_names(self) -> None:
        """Sheet-name autocomplete for the rule's own anchor Sheet field —
        from the project's schematic files (RuntimeContext.sheet_names),
        refreshed on root-file change like the Cell/Point names (see
        collect_all_sheet_names, gui/docks/rename.py)."""
        names = collect_all_sheet_names(self._root_path) if self._root_path is not None else []
        self.origin_widget.set_known_sheets(names)

    def refresh_known_roles(self, snapshot) -> None:
        """Same "populate from the live board" pattern as PlacerDock's own
        refresh_known_roles — called by DockHub.push_snapshot."""
        roles = sorted({s.role for s in snapshot if s.role})
        clusters = sorted({s.cluster for s in snapshot if s.cluster})
        self.origin_widget.set_known_roles(roles, clusters)
        set_combo_items(self.spoke_cluster_combo, clusters)

    def refresh_known_nets(self, board) -> None:
        nets = sorted({n.name for n in board.adapter.get_all_nets() if n.name})
        set_combo_items(self.net_edit, nets)

    # ── Message helper ────────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        """Mirror into the Log dock at the level matching `style` — the docks
        no longer have an inline message_label (2026-08-13), the Log dock is
        the single destination."""
        show_message(text, style, logger)

    # ── Spokes table ──────────────────────────────────────────────────────

    def _refresh_table(self) -> None:
        self.spokes_table.setRowCount(len(self._spokes))
        for row, spoke in enumerate(self._spokes):
            is_polar = spoke.get("radius_mm") is not None
            # `is not None`, not truthiness — a legitimate 0.0 would render as
            # an empty field (2026-08-12, Group 2 fix).
            values = [
                str(spoke.get("pad", "")),
                str(spoke.get("cell", "")),
                _("Polar") if is_polar else _("Cartesian"),
                str(spoke.get("shift_x_mm", "")) if not is_polar and spoke.get("shift_x_mm") is not None else "",
                str(spoke.get("shift_y_mm", "")) if not is_polar and spoke.get("shift_y_mm") is not None else "",
                str(spoke.get("radius_mm", "")) if is_polar and spoke.get("radius_mm") is not None else "",
                str(spoke.get("angle_deg", "")) if is_polar and spoke.get("angle_deg") is not None else "",
                str(spoke.get("rotation_deg", "")) if spoke.get("rotation_deg") is not None else "",
                str(spoke.get("cluster", "")),
                _("yes") if spoke.get("retired") else "",
                _("yes") if spoke.get("skip") else "",
            ]
            for col, value in enumerate(values):
                self.spokes_table.setItem(row, col, QTableWidgetItem(value))

    def _update_spoke_mode(self) -> None:
        """Same Cartesian/Polar field-toggle as coordinate_placer's
        _update_row_mode() — Shift fields only in Cartesian, Radius/Angle
        only in Polar. Disabled (not hidden) keeps the editor layout stable,
        matching every other dock's convention — shared set_mode_pair_enabled
        (gui/docks/_common.py)."""
        set_mode_pair_enabled(
            self.spoke_mode_combo.currentIndex() == 1,
            (self.spoke_shift_x_edit, self.spoke_shift_y_edit),
            (self.spoke_radius_edit, self.spoke_angle_edit),
        )

    def _on_table_selection_changed(self) -> None:
        rows = self.spokes_table.selectionModel().selectedRows()
        if not rows:
            self._selected_index = None
            return
        self._selected_index = rows[0].row()
        self._load_spoke_into_editor(self._spokes[self._selected_index])

    def _load_spoke_into_editor(self, spoke: Dict[str, Any]) -> None:
        self.spoke_pad_edit.setText(str(spoke.get("pad", "")))
        self.spoke_cell_combo.setCurrentText(str(spoke.get("cell", "")))
        is_polar = spoke.get("radius_mm") is not None
        self.spoke_mode_combo.setCurrentIndex(1 if is_polar else 0)
        # `is not None`, not truthiness — a legitimate 0.0 would render as an
        # empty field (2026-08-12, Group 2 fix).
        self.spoke_shift_x_edit.setText(
            str(spoke.get("shift_x_mm", "")) if not is_polar and spoke.get("shift_x_mm") is not None else "")
        self.spoke_shift_y_edit.setText(
            str(spoke.get("shift_y_mm", "")) if not is_polar and spoke.get("shift_y_mm") is not None else "")
        self.spoke_radius_edit.setText(
            str(spoke.get("radius_mm", "")) if is_polar and spoke.get("radius_mm") is not None else "")
        self.spoke_angle_edit.setText(
            str(spoke.get("angle_deg", "")) if is_polar and spoke.get("angle_deg") is not None else "")
        self.spoke_rotation_edit.setText(
            str(spoke.get("rotation_deg", "")) if spoke.get("rotation_deg") is not None else "")
        self.spoke_cluster_combo.setCurrentText(str(spoke.get("cluster", "")))
        self.spoke_retired_checkbox.setChecked(bool(spoke.get("retired", False)))
        self.spoke_skip_checkbox.setChecked(bool(spoke.get("skip", False)))

    def _clear_spoke_editor(self) -> None:
        self.spoke_pad_edit.setText("")
        self.spoke_cell_combo.setCurrentText("")
        self.spoke_mode_combo.setCurrentIndex(0)
        self.spoke_shift_x_edit.setText("")
        self.spoke_shift_y_edit.setText("")
        self.spoke_radius_edit.setText("")
        self.spoke_angle_edit.setText("")
        self.spoke_rotation_edit.setText("")
        self.spoke_cluster_combo.setCurrentText("")
        self.spoke_retired_checkbox.setChecked(False)
        self.spoke_skip_checkbox.setChecked(False)

    def _build_spoke_dict(self) -> Optional[Dict[str, Any]]:
        pad = self.spoke_pad_edit.text().strip()
        if not pad:
            self._show_message(_("Pad is required."), _ERROR_STYLE)
            return None
        cell = self.spoke_cell_combo.currentText().strip()
        if not cell:
            self._show_message(_("Cell is required."), _ERROR_STYLE)
            return None

        entry: Dict[str, Any] = {"pad": pad, "cell": cell}
        if self.spoke_mode_combo.currentIndex() == 1:
            # Polar — radius/angle BOTH required (same rule as the loader).
            # Shared (ok, value) parse (parse_float_field): a "not a number"
            # error is reported and returned immediately, so the exact field
            # error is never clobbered by the generic "needs both" message
            # below — only genuinely-empty fields fall through to it (this
            # fixes the old overloaded-None behaviour, plan Group 2 item 5).
            ok, radius = parse_float_field(self.spoke_radius_edit)
            if not ok:
                self._show_message(_("Radius: {text!r} is not a number.")
                                   .format(text=self.spoke_radius_edit.text().strip()), _ERROR_STYLE)
                return None
            ok, angle = parse_float_field(self.spoke_angle_edit)
            if not ok:
                self._show_message(_("Angle: {text!r} is not a number.")
                                   .format(text=self.spoke_angle_edit.text().strip()), _ERROR_STYLE)
                return None
            if radius is None or angle is None:
                self._show_message(_("Polar mode needs both Radius and Angle."), _ERROR_STYLE)
                return None
            entry["radius_mm"] = radius
            entry["angle_deg"] = angle
        else:
            ok, shift_x = parse_float_field(self.spoke_shift_x_edit)
            if not ok:
                self._show_message(_("Shift X: {text!r} is not a number.")
                                   .format(text=self.spoke_shift_x_edit.text().strip()), _ERROR_STYLE)
                return None
            ok, shift_y = parse_float_field(self.spoke_shift_y_edit)
            if not ok:
                self._show_message(_("Shift Y: {text!r} is not a number.")
                                   .format(text=self.spoke_shift_y_edit.text().strip()), _ERROR_STYLE)
                return None
            shift_x = 0.0 if shift_x is None else shift_x
            shift_y = 0.0 if shift_y is None else shift_y
            if shift_x:
                entry["shift_x_mm"] = shift_x
            if shift_y:
                entry["shift_y_mm"] = shift_y
        ok, rotation = parse_float_field(self.spoke_rotation_edit)
        if not ok:
            self._show_message(_("Rotation: {text!r} is not a number.")
                               .format(text=self.spoke_rotation_edit.text().strip()), _ERROR_STYLE)
            return None
        rotation = 0.0 if rotation is None else rotation
        if rotation:
            entry["rotation_deg"] = rotation
        cluster = self.spoke_cluster_combo.currentText().strip()
        if cluster:
            entry["cluster"] = cluster
        if self.spoke_retired_checkbox.isChecked():
            entry["retired"] = True
        if self.spoke_skip_checkbox.isChecked():
            entry["skip"] = True

        try:
            load_manual_spoke(entry, self.net_edit.currentText().strip() or "?")
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None
        return entry

    def _persist_rule(self, context: str, notify_tree: bool = False) -> None:
        """Build the current rule from the form, validate it, and write it to
        the target file (upsert_list_entry). Shared by the Save button (the
        rule's own Net/Origin fields) and every spoke autosave path (Stage 2,
        2026-08-20). On ANY failure — invalid form, invalid rule, or write
        error — report it to the Log dock and return; a failed autosave must
        be visible, never silent (an edit that didn't get written looks
        exactly like one that did).

        `context` is a short operation label prefixed to the success message
        ("Spoke added:", ...); empty for the Save button. The `saved` signal
        fires only when the tree's display can actually change: notify_tree
        (Save button — a name/net change alters what the tree shows), or an
        autosave that CREATED a brand-new rule (upsert returned "wrote", not
        "overwrote"). A plain spoke-value tweak on an already-saved rule is
        deliberately silent to the tree — config_tree_dock.refresh() clears
        and rebuilds the whole tree (losing selection), so firing it on every
        blur would be both wasteful and jarring."""
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        entry = self._build_rule_dict()
        if entry is None:
            return  # _build_rule_dict already reported the specific error
        try:
            load_rule(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return
        try:
            overwritten = upsert_list_entry(self._path, "rules", entry, key_fn=_rule_identity)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return
        suffix = _("{action} {name!r} in {path}").format(
            action=_("Overwrote") if overwritten else _("Wrote"),
            name=_rule_identity(entry), path=display_path(self._path))
        self._show_message(f"{context} {suffix}".strip(), _SUCCESS_STYLE)
        if notify_tree or not overwritten:
            self.saved.emit()

    def _autosave_current_spoke(self) -> None:
        """Stage 2 (2026-08-20): a spoke editor field's 'editing finished'
        (blur/Enter/combo pick/checkbox). Immediately does what 'Update
        selected' + 'Save' used to do together: rebuild the SELECTED spoke
        from the editor, replace it in self._spokes, and persist the whole
        rule. No selected row = nothing to update (a brand-new spoke still
        goes through 'Add spoke'). Any failure is reported by _persist_rule
        /_build_spoke_dict — never silent."""
        if self._selected_index is None:
            return  # nothing to update; 'Add spoke' is the entry point for a new row
        entry = self._build_spoke_dict()
        if entry is None:
            return  # error already reported
        self._spokes[self._selected_index] = entry
        self._refresh_table()
        self.spokes_table.selectRow(self._selected_index)
        self._persist_rule(_("Spoke updated:"))

    def _on_add_spoke(self) -> None:
        entry = self._build_spoke_dict()
        if entry is None:
            return
        self._spokes.append(entry)
        self._refresh_table()
        self.spokes_table.selectRow(len(self._spokes) - 1)
        self._persist_rule(_("Spoke added:"))

    def _on_update_spoke(self) -> None:
        if self._selected_index is None:
            self._show_message(_("Pick a spoke row first."), _ERROR_STYLE)
            return
        entry = self._build_spoke_dict()
        if entry is None:
            return
        self._spokes[self._selected_index] = entry
        self._refresh_table()
        self.spokes_table.selectRow(self._selected_index)
        self._persist_rule(_("Spoke updated:"))

    def _on_remove_spoke(self) -> None:
        if self._selected_index is None:
            self._show_message(_("Pick a spoke row first."), _ERROR_STYLE)
            return
        del self._spokes[self._selected_index]
        self._selected_index = None
        self._refresh_table()
        self._clear_spoke_editor()
        self._persist_rule(_("Spoke removed:"))

    def _on_move_spoke(self, delta: int) -> None:
        if self._selected_index is None:
            self._show_message(_("Pick a spoke row first."), _ERROR_STYLE)
            return
        new_index = self._selected_index + delta
        if not (0 <= new_index < len(self._spokes)):
            return
        self._spokes[self._selected_index], self._spokes[new_index] = \
            self._spokes[new_index], self._spokes[self._selected_index]
        self._selected_index = new_index
        self._refresh_table()
        self.spokes_table.selectRow(new_index)
        self._persist_rule(_("Spoke moved:"))

    # ── Building the Rule entry dict (shared by Redraw/Save) ────────────────

    def _build_rule_dict(self) -> Optional[Dict[str, Any]]:
        net = self.net_edit.currentText().strip()
        if not net:
            self._show_message(_("Net is required."), _ERROR_STYLE)
            return None

        entry: Dict[str, Any] = {"net": net, "spokes": list(self._spokes)}
        name = self.name_edit.text().strip()
        if name:
            entry["name"] = name
        comment = self.comment_edit.text().strip()
        if comment:
            entry["comment"] = comment

        origin_fields, err = self.origin_widget.build()
        if err:
            self._show_message(err, _ERROR_STYLE)
            return None
        if origin_fields["mode"] == "anchor":
            if "ref" in origin_fields:
                entry["anchor_ref"] = origin_fields["ref"]
            else:
                entry["anchor_role"] = origin_fields["role"]
                if "sheet" in origin_fields:
                    entry["anchor_sheet"] = origin_fields["sheet"]
            if "cluster" in origin_fields:
                entry["anchor_cluster"] = origin_fields["cluster"]
        else:  # Point
            entry["anchor_point"] = origin_fields["point"]

        if self.retired_checkbox.isChecked():
            entry["retired"] = True
        if self.skip_checkbox.isChecked():
            entry["skip"] = True
        return entry

    # ── Redraw ────────────────────────────────────────────────────────────

    def _on_redraw_rule(self) -> None:
        self._show_message("")
        payload = self._collect_redraw_inputs(isolate_spoke_index=None)
        if payload is None:
            return
        self._start_redraw_op(payload)

    def _on_redraw_spoke(self) -> None:
        self._show_message("")
        if self._selected_index is None:
            self._show_message(_("Pick a spoke row first."), _ERROR_STYLE)
            return
        payload = self._collect_redraw_inputs(isolate_spoke_index=self._selected_index)
        if payload is None:
            return
        self._start_redraw_op(payload)

    def _collect_redraw_inputs(self, isolate_spoke_index: Optional[int]) -> Optional[Dict[str, Any]]:
        """UI thread: build+validate the current form's Rule and (for the
        selected-spoke variant) inject a temporary skip=True on every OTHER
        spoke — see module docstring on why this is safe (skip: already
        exists for exactly this narrowing, same as apply_pipeline.py's own
        drop_inactive_items)."""
        entry = self._build_rule_dict()
        if entry is None:
            return None
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return None

        try:
            rule = load_rule(entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None

        if isolate_spoke_index is not None:
            spokes = [dataclasses.replace(s, skip=(i != isolate_spoke_index))
                     for i, s in enumerate(rule.spokes)]
            rule = dataclasses.replace(rule, spokes=spokes)

        # Load from the project ROOT, not self._path (the file this rule is
        # saved into) — a spoke's cell routinely lives in a different
        # included file than the rule referencing it (see module docstring),
        # and only the root's own include: graph pulls those cells: in.
        # Loading self._path directly would silently see an empty cells: and
        # fail every spoke's "cell not found" check (found live 2026-08-06:
        # fpga_cap_pair_spoke, defined in a sibling include, not fpga_spokes.yaml
        # itself). Falls back to self._path when no root is known yet (e.g. a
        # standalone rules file opened without a Config tree root).
        config_path = self._root_path if self._root_path is not None else self._path
        try:
            if config_path.exists():
                cfg, ctx = load_config(str(config_path))
            else:
                cfg, ctx = Config(), RuntimeContext()
        except (ValidationError, OSError, yaml.YAMLError) as e:
            self._show_message(_("Failed to load file: {error}").format(error=e), _ERROR_STYLE)
            return None

        effective = rule_effective_name(rule)
        # Replace-by-identity: previewing an already-saved rule's edits must
        # not create a second copy alongside the saved one.
        cfg = dataclasses.replace(cfg)  # graph cache is shared; don't mutate it
        cfg.rules = [r for r in cfg.rules if rule_effective_name(r) != effective]
        cfg.rules.append(rule)

        return {"path": config_path, "cfg": cfg, "ctx": ctx, "name": effective}

    def _run_redraw(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: ApplyPipeline run only — never touches a widget."""
        pipeline = ApplyPipeline(config_path=str(payload["path"]),
                                 preloaded_cfg=payload["cfg"], preloaded_ctx=payload["ctx"],
                                 only=[payload["name"]], dry_run=False)
        try:
            pipeline.run()
        except (PlacerError, ValidationError, ApiError) as e:
            return {"error": _("Placement failed: {error}").format(error=e)}
        except Exception as e:
            logger.exception("Rule redraw failed")
            return {"error": _("Placement failed: {error}").format(error=e)}
        return {"name": payload["name"]}

    def _finish_redraw(self, result: Dict[str, Any]) -> None:
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        self._show_message(_("Placed {name!r}.").format(name=result["name"]), _SUCCESS_STYLE)

    def _start_redraw_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._main_window.connection,
            (self.redraw_rule_button, self.redraw_spoke_button, self.save_button),
            self._run_redraw, self._finish_redraw, self._on_redraw_failed, payload)

    def _on_redraw_failed(self, message: str) -> None:
        self._show_message(_("Placement failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_redraw_rule(self) -> None:
        """Synchronous composition of collect + run + finish — for tests."""
        payload = self._collect_redraw_inputs(isolate_spoke_index=None)
        if payload is None:
            return
        result = self._run_redraw(payload)
        self._finish_redraw(result)

    def _do_redraw_spoke(self) -> None:
        if self._selected_index is None:
            self._show_message(_("Pick a spoke row first."), _ERROR_STYLE)
            return
        payload = self._collect_redraw_inputs(isolate_spoke_index=self._selected_index)
        if payload is None:
            return
        result = self._run_redraw(payload)
        self._finish_redraw(result)

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        """The Save button's sole remaining job since Stage 2 (2026-08-20):
        the rule's OWN Net/Origin/retired/skip fields — spoke edits autosave
        themselves (see _persist_rule/_autosave_current_spoke). Always
        notifies the tree, since a name/net change alters what it shows."""
        self._persist_rule("", notify_tree=True)

    # ── Bulk-set Cell for net (Stage 3, 2026-08-20) ─────────────────────────

    def _on_bulk_set_cell(self) -> None:
        """'Bulk set Cell for net…' button — opens the preview dialog
        (BulkSetCellDialog), then applies the chosen cell to every rule on
        the chosen net (see _apply_bulk_cell_set)."""
        if self._root_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        dlg = BulkSetCellDialog(self._root_path, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        net = dlg.net_combo.currentText().strip()
        cell = dlg.cell_combo.currentText().strip()
        if not net or not cell:
            self._show_message(_("Bulk-set Cell needs both a net and a cell."), _ERROR_STYLE)
            return
        self._apply_bulk_cell_set(net, cell)

    def _apply_bulk_cell_set(self, net: str, cell: str) -> None:
        """Set spoke.cell = cell for EVERY rule on `net` across the whole
        include: graph, one upsert_list_entry per rule (rules routinely live
        in different included files — collect_rules_by_net walks them all).
        Partial failure is reported EXPLICITLY — which rules wrote and which
        did not — never a silent half-applied change. After a success the
        currently-loaded rule is reloaded if it was on the affected net
        (otherwise a later spoke autosave would write the stale pre-bulk
        state back over the bulk change — silent data loss)."""
        if self._root_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        affected = collect_rules_by_net(self._root_path, net)
        if not affected:
            self._show_message(_("No rules on net {net!r}.").format(net=net), _ERROR_STYLE)
            return
        ok: List[str] = []
        failed: List[str] = []
        for path, rule in affected:
            modified = dict(rule)
            modified["spokes"] = [{**s, "cell": cell} for s in (rule.get("spokes") or [])]
            name = _rule_identity(modified)
            try:
                load_rule(modified)  # validate before writing, same as _on_save
            except ValidationError as e:
                failed.append(_("{name} in {file}: {err}")
                              .format(name=name, file=display_path(path), err=e))
                continue
            try:
                upsert_list_entry(path, "rules", modified, key_fn=_rule_identity)
                ok.append(_("{name} in {file}").format(name=name, file=display_path(path)))
            except OSError as e:
                failed.append(_("{name} in {file}: {err}")
                              .format(name=name, file=display_path(path), err=e))
        if failed:
            self._show_message(
                _("Bulk-set Cell: wrote {n_ok} rule(s); FAILED {n_failed} — {failed}")
                .format(n_ok=len(ok), n_failed=len(failed), failed="; ".join(failed)),
                _ERROR_STYLE)
        else:
            self._show_message(
                _("Bulk-set Cell: wrote {n} rule(s) on net {net!r}.").format(n=len(ok), net=net),
                _SUCCESS_STYLE)
        self._reload_if_bulk_affected(net)

    def _reload_if_bulk_affected(self, net: str) -> None:
        """After a bulk cell set rewrote the files, the dock's in-memory
        editor for the currently-loaded rule (if it is on the affected net)
        is stale — and worse, a subsequent spoke autosave (Stage 2) would
        write that stale state back, silently undoing the bulk change. Re-read
        its entry from disk and reload the form so shown + autosaved state
        match the bulk result."""
        if self._root_path is None or self.net_edit.currentText().strip() != net:
            return
        loaded_identity = _rule_identity(
            {"net": net, "name": self.name_edit.text().strip() or None})
        # Rules on a net can live in ANY included file — search the whole
        # graph, not just the root (the dock's write target).
        for _file, rule in collect_rules_by_net(self._root_path, net):
            if isinstance(rule, dict) and _rule_identity(rule) == loaded_identity:
                self.load_entry(rule)
                return

    # ── Starting a brand new entry (ConfigTreeDock's Add rule...) ───────────

    def new_rule(self, path: Path) -> None:
        """Resets the form to its initial (blank) state — ConfigTreeDock's
        "Add rule..." context-menu action opens this form empty, same
        reasoning as PlacerDock.new_placement()/ThermalViaArrayDock.
        new_thermal_via()/PointsDock.new_point(). The entry is written to the
        project root file (2026-08-21), so the passed path is ignored."""
        self._path = self._root_path
        self.net_edit.setCurrentText("")
        self.name_edit.setText("")
        self.comment_edit.setText("")
        self.origin_widget.clear()
        self.retired_checkbox.setChecked(False)
        self.skip_checkbox.setChecked(False)
        self._spokes = []
        self._selected_index = None
        self._refresh_table()
        self._clear_spoke_editor()
        self._show_message("")

    # ── Loading an already-saved entry back into the form ───────────────────

    def load_entry(self, entry: Dict[str, Any], file_path: Optional[Path] = None) -> None:
        """Reverse of _build_rule_dict() — called by ConfigTreeDock's Rules
        category (via rule_picked) when an already-saved entry is clicked,
        same shape as PlacerDock.load_placement/ThermalViaArrayDock.
        load_entry. rules: is a list section (see module docstring), so
        the payload is already the full dict — no re-read needed. The WRITE
        target is set back to the file the rule actually lives in, so a Save
        updates that file instead of adding a root duplicate (2026-08-21
        review fix)."""
        self._show_message("")
        if file_path is None:
            file_path = find_list_entry_file(self._root_path, "rules", entry)
        if file_path is not None:
            self._path = file_path
        self.net_edit.setCurrentText(str(entry.get("net", "")))
        self.name_edit.setText(str(entry.get("name") or ""))
        self.comment_edit.setText(str(entry.get("comment") or ""))

        if "anchor_point" in entry:
            self.origin_widget.load(mode="point", point=str(entry["anchor_point"]))
        else:
            self.origin_widget.load(
                mode="anchor", ref=str(entry.get("anchor_ref", "")),
                role=str(entry.get("anchor_role", "")),
                sheet=str(entry.get("anchor_sheet", "")),
                cluster=str(entry.get("anchor_cluster", "")))

        self.retired_checkbox.setChecked(bool(entry.get("retired", False)))
        self.skip_checkbox.setChecked(bool(entry.get("skip", False)))

        self._spokes = [dict(s) for s in (entry.get("spokes") or [])]
        self._selected_index = None
        self._refresh_table()
        self._clear_spoke_editor()
