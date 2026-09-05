# gui/docks/placer.py
"""
PlacerDock — pick a Cell + a Cluster name, an origin (absolute point /
anchor ref-or-role+pad / named Point), rotation, layer/mirror, and the net
params a by-nets placement needs — then Redraw (place it for real on the
live board, see it, adjust, Redraw again) and Save (write the
clone_placement into the Placer file). Requested live 2026-08-01:
"Суть пласера в том, чтобы выбрать кластер задать опорную точку...
Поменял координату, нажал перерисовать, оно переехало. Посмотрел,
утвердил."

Cluster tagging: ClonePlacement itself has NO output "Cluster" field —
checked directly against the pipeline (kicadstamp/placement/,
apply_pipeline.py): Cluster is only ever READ during apply (to narrow
anchor/role search), never written. The only place Cluster gets written
anywhere in this codebase is KiCadBoardAdapter.set_field_values_bulk() —
placement and tagging were always two separate manual steps. Redraw here
closes that gap itself: after a successful ApplyPipeline run, it
independently replays the SAME item through a throwaway PlacementPlanner
(plan_item() is pure computation, doesn't move anything — the real move
already happened via the pipeline) to recover which refs this placement
actually touched, and tags Cluster=<name> on them via the same
set_field_values_bulk() call.

Redraw uses the REAL Placer file's full config (load_config), not a
synthetic single-placement one — critical for
PlacementRegistry.reconcile()'s known_anchor_ids protection
(kicadstamp/registry.py): built from a config missing every OTHER
clone_placement already on the board, a redraw preview would read as
"everything else is gone" and PRUNE their vias/tracks. Loading the real
file and only NARROWING execution via ApplyPipeline's own `only=` keeps
everyone else protected while still previewing just this one. The in-
progress (possibly unsaved) form state replaces-by-name whatever's
already in cfg.clone_placements for this name, so Redraw always previews
the CURRENT form, not last Save's.

config_path passed to ApplyPipeline is always the Placer file's own path
(even when preloaded_cfg is given) — it's still used to derive
registry_path/track_registry_path (registry_path_for_config) when the
config itself doesn't set them explicitly, which is what makes repeated
Redraws idempotent (a second click recognizes vias/tracks the first
click already created, via the SAME registry file a real
`kicadstamp_cli.py apply` on this file would also use).

Cell picking moved out to ConfigTreeDock (gui/docks/config_tree.py, its
Cells category — replaced the earlier standalone CellListDock 2026-08-03,
see handoff_2026_08_03_gui_tree_risks_resolved.md), tabified with the
Components tree — feeds set_selected_cell() here. Cluster name
similarly follows RoleClusterTreeDock's cluster_picked signal when a
Cluster GROUP node is clicked there (set_cluster_name()) — both requested
live
2026-08-01 ("где выбирать cell? ...к дереву компонент надо добавить
табик со списком cell", "раз уж у нас есть список Cluster то при выборе
кластера надо сразу автоматически заполнять поле кластер"). Anchor
Role/Cluster are editable QComboBoxes populated from the live board
snapshot (refresh_known_roles(), called by MainWindow at the same ~2s
poll cadence as the rest of the docks) — "если выбираем
по роли то надо и поле anchor cluster да и лист... якорить, так уж по
полной". Ref stays a plain, unassisted text field — this project
deliberately avoids relying on refdes elsewhere (Role survives
re-annotation, Ref doesn't), the user confirmed live it's fine to leave
as a minor/deprioritized option now that it exists, not worth the same
treatment.

Scope NOT covered by this first version (kept out deliberately, not by
oversight): refs: explicit role->ref override, by_selection mode. All
still reachable by hand-editing the saved YAML; add UI for them if they
turn out to be needed often. anchor_sheet narrowing WAS in this deferred
list until 2026-08-15 — closed by making every Sheet field a searchable
combo sourced from the project's schematic files (see
plan_2026_08_15_sheet_combo_everywhere.md).

anchor_point IS autocompleted (closed 2026-08-06, Denis: "думаю имена
Points тоже надо делать выпадашкой с именами") — set_root_path(), wired to
RootMetadataDock's root_changed same as RuleDock's own (gui/docks/
rules.py), sources the combo from the WHOLE include graph via
collect_all_point_names(), not just this dock's own target file.

load_placement() (reverse of _build_entry_dict) lets ConfigTreeDock's Clone
placements category (gui/docks/config_tree.py — replaced the earlier
standalone PlacerListDock 2026-08-03) re-open an already-saved
clone_placement for editing/Redraw — requested live 2026-08-02 alongside a
"Placements" tab next to the Components tree/Cells list ("таб пласеров
(там где дерево
компонент и экстракторов)"), same "pick from a list you already browse"
pattern as Cell/Cluster picking.

NOTE (2026-09-05): the Nets / Net overrides / Refs tabs (and their
Params/auto-fill UI) were removed — nets auto-resolve (derive_role_nets)
since 2026-08-28. Stored nets:/params:/net_overrides:/refs: on existing
records are carried forward untouched on save; there is no manual GUI for
them in the Placer anymore.

Source: Cell (the role:/cluster: single-component modes were migrated 1:1 to
CoordinatePlacement's anchor-relative mode on 2026-08-12, Group 0
consolidation — see CoordinatePlacement's docstring; ClonePlacement is pure
template cloning again, cell: is mandatory).

Merged with CoordinatePlacerDock 2026-08-12 (Group 1): this is now the ONE
placement dock. The Source combo offers "Cell" (ClonePlacement, template
cloning) and "Single component" (CoordinatePlacement, no cell:, matched by
Cluster+Role) — _on_cell_mode_changed() switches which field set the form
shows, load_placement()/new_placement()/new_coordinate_placement() pick the
mode from the entry's cell: presence. coordinate_placements: is a normal
named-records tree section now (one leaf per entry, like clone_placements),
and Save/Redraw write/run through the same upsert/ApplyPipeline flow as the
clone source, matched by effective name.
"""
from dataclasses import replace
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kipy.errors import ApiError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout,
                              QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                              QMessageBox, QPushButton, QTabWidget, QVBoxLayout,
                              QWidget)

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import (ClonePlacement, Config, Entity, RuntimeContext,
                               clone_placement_effective_name,
                               coordinate_placement_effective_name,
                               entity_effective_name, load_clone_placement,
                               load_config, load_coordinate_placement, load_entity)
from kicadstamp.constants import CLUSTER_FIELD_NAME, DEFAULT_LOG_DIR
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.i18n import _
from kicadstamp.placement.planner import PlacementPlanner
from kicadstamp.utils.units import MM

from ..ui_utils import busy
from ..worker import start_long_op
from ._anchor_origin import AnchorOriginWidget
from .live_position import (LiveRead, read_anchor_live,
                            read_cell_anchor_offset_live,
                            read_clone_origin_live, read_coordinate_live)
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      WARN_STYLE as _WARN_STYLE, KeyValueTableEditor,
                      configure_searchable, display_path, merge_write,
                      parse_float_field, read_data, set_combo_items,
                      set_mode_pair_enabled, show_message, upsert_clone_placement,
                      upsert_entity, upsert_entity_placement, upsert_list_entry)
# _KeyValueTableEditor moved to _common.KeyValueTableEditor (2026-08-30,
# ToolsDock shares it) — keep the old private name for existing call sites
# and tests (placer_mod._KeyValueTableEditor).
_KeyValueTableEditor = KeyValueTableEditor
from .cascade import cascade_records, run_cascade_worker
from .entity_delete import delete_entry
from .rename import (collect_all_cell_names, collect_all_point_names,
                     collect_all_sheet_names, collect_section_entries,
                     entry_effective_name, find_dict_entry_file,
                     find_list_entry_file)

logger = logging.getLogger(__name__)


def _newest_operation_file(log_dir: Path) -> Optional[Path]:
    """The most recently written operation_*.json in `log_dir` (by st_ctime,
    same pick as `kicadstamp undo` / cmd_undo), or None when the directory
    doesn't exist or holds no operation files."""
    if not log_dir.exists():
        return None
    files = sorted(log_dir.glob("operation_*.json"), key=lambda p: p.stat().st_ctime)
    return files[-1] if files else None




class _CoordinatePlacementForm(QWidget):
    """Single CoordinatePlacement editor — the merged PlacerDock's
    coordinate mode (2026-08-12, Group 1): replaces the old
    CoordinatePlacerDock whole-list table with a one-entry-per-leaf form,
    the same "tree leaf = form" convention every other placement dock
    follows. Position modes mirror CoordinatePlacement's THREE modes
    (Cartesian-absolute / polar-around-centre / anchor-relative), reusing
    the shared set_mode_pair_enabled / parse_float_field helpers and the
    shared AnchorOriginWidget for the anchor IDENTITY fields (ref/role/
    sheet/pad/cluster/point). The anchor OFFSET is the form's own
    Cartesian/Polar-toggled row — the shared widget only builds its
    offset/polar rows when "xy" is among its modes, which an anchor-only
    block deliberately isn't.

    build() returns a plain dict (field-level parsing + anchor-block
    validation only); the dock validates it through load_coordinate_placement()
    — the same validator the CLI/YAML path uses — exactly like clone mode's
    _build_entry_dict does for load_clone_placement()."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._known_roles: List[str] = []
        self._known_clusters: List[str] = []

        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)

        # Cluster/Role/Name identify WHICH record is being edited — they are
        # no longer laid out here (2026-08-13, plan
        # coordinate_identity_on_source_tab): PlacerDock places them on the
        # Source tab, same as the Cell-mode identity fields, so the Coordinate
        # tab keeps only "where to put it". The widgets are still created as
        # this form's own attributes — every reader/writer below (build/load/
        # clear/set_known_roles, and the dock's current_entity_name) reaches
        # them by attribute, physical layout doesn't matter to them.
        self.cluster_combo = QComboBox()
        configure_searchable(self.cluster_combo)
        self.role_combo = QComboBox()
        configure_searchable(self.role_combo)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("cluster/role"))
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText(_("optional free-form note"))
        # Own-identity sheet (2026-08-15): OPTIONAL narrowing of Cluster+Role
        # to one physical instance when the same sheet is cloned/reused and
        # Cluster alone is identical across copies (Denis, live: AD_DAC/IC2).
        # Distinct from the Anchor widget's anchor_sheet — that one narrows
        # the OTHER, anchor component in anchor-relative mode.
        self.sheet_edit = QComboBox()
        configure_searchable(self.sheet_edit)
        self.sheet_edit.lineEdit().setPlaceholderText(
            _("sheet name (only if Cluster+Role is ambiguous across cloned sheets, optional)"))

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([_("Cartesian"), _("Polar (around centre)"), _("Anchor")])
        self.mode_combo.currentIndexChanged.connect(self._update_mode)
        form.addRow(_("Mode:"), self.mode_combo)

        self._cartesian_row = QWidget()
        cartesian_form = QFormLayout(self._cartesian_row)
        cartesian_form.setContentsMargins(0, 0, 0, 0)
        self.x_edit = QLineEdit()
        self.x_edit.setPlaceholderText(_("X mm"))
        self.y_edit = QLineEdit()
        self.y_edit.setPlaceholderText(_("Y mm"))
        cartesian_form.addRow(_("X mm:"), self.x_edit)
        cartesian_form.addRow(_("Y mm:"), self.y_edit)
        form.addRow(self._cartesian_row)

        self._polar_row = QWidget()
        polar_form = QFormLayout(self._polar_row)
        polar_form.setContentsMargins(0, 0, 0, 0)
        self.center_x_edit = QLineEdit()
        self.center_y_edit = QLineEdit()
        self.radius_edit = QLineEdit()
        self.angle_edit = QLineEdit()
        polar_form.addRow(_("Center X mm:"), self.center_x_edit)
        polar_form.addRow(_("Center Y mm:"), self.center_y_edit)
        polar_form.addRow(_("Radius mm:"), self.radius_edit)
        polar_form.addRow(_("Angle °:"), self.angle_edit)
        form.addRow(self._polar_row)

        # Self-referential anchor (ABSOLUTE modes only — the "centre of the
        # moved footprint vs one specific pad of it" choice the old table's
        # Anchor/Pad columns had; in the ANCHOR-RELATIVE mode below the pad
        # belongs to the OTHER, anchor component instead, so this row is
        # hidden there). 2026-08-13 review, bug 2: without it a record with
        # anchor: pad silently lost the field on re-save.
        self._anchor_row_absolute = QWidget()
        anchor_abs_form = QFormLayout(self._anchor_row_absolute)
        anchor_abs_form.setContentsMargins(0, 0, 0, 0)
        self.anchor_combo = QComboBox()
        self.anchor_combo.addItems([_("Center"), _("Pad")])
        self.anchor_combo.currentIndexChanged.connect(self._update_anchor_mode)
        anchor_abs_form.addRow(_("Anchor:"), self.anchor_combo)
        self.pad_edit = QLineEdit()
        self.pad_edit.setPlaceholderText(_("pad number (e.g. 1)"))
        anchor_abs_form.addRow(_("Pad:"), self.pad_edit)
        form.addRow(self._anchor_row_absolute)

        # Anchor-relative block — the shared AnchorOriginWidget provides ONLY
        # the anchor identity fields (ref/role/sheet/pad/cluster/point); the
        # OFFSET is the form's own row below (the shared widget builds its
        # offset/polar rows only when "xy" is among its modes, which an
        # anchor-only block deliberately isn't).
        self._anchor_widget = AnchorOriginWidget(
            modes=["anchor", "point"], anchor_fields=["sheet", "pad", "cluster"],
            shift=False, polar=False)
        form.addRow(self._anchor_widget)

        # Anchor OFFSET — Cartesian (X/Y) or polar (Radius/Angle), mutually
        # exclusive, same set_mode_pair_enabled toggle as every other
        # position mode here.
        self._anchor_offset_row = QWidget()
        offset_form = QFormLayout(self._anchor_offset_row)
        offset_form.setContentsMargins(0, 0, 0, 0)
        self._offset_combo = QComboBox()
        self._offset_combo.addItems([_("Cartesian"), _("Polar")])
        self._offset_combo.currentIndexChanged.connect(self._update_offset_mode)
        offset_form.addRow(_("Offset:"), self._offset_combo)
        xy_row = QHBoxLayout()
        self._offset_x_edit = QLineEdit()
        self._offset_x_edit.setPlaceholderText(_("X mm"))
        self._offset_y_edit = QLineEdit()
        self._offset_y_edit.setPlaceholderText(_("Y mm"))
        xy_row.addWidget(QLabel(_("X mm:")))
        xy_row.addWidget(self._offset_x_edit)
        xy_row.addWidget(QLabel(_("Y mm:")))
        xy_row.addWidget(self._offset_y_edit)
        offset_form.addRow(xy_row)
        ra_row = QHBoxLayout()
        self._offset_radius_edit = QLineEdit()
        self._offset_radius_edit.setPlaceholderText(_("radius mm"))
        self._offset_angle_edit = QLineEdit()
        self._offset_angle_edit.setPlaceholderText(_("angle deg"))
        ra_row.addWidget(QLabel(_("Radius mm:")))
        ra_row.addWidget(self._offset_radius_edit)
        ra_row.addWidget(QLabel(_("Angle °:")))
        ra_row.addWidget(self._offset_angle_edit)
        offset_form.addRow(ra_row)
        form.addRow(self._anchor_offset_row)
        self._update_offset_mode()

        self.rotation_edit = QLineEdit()
        self.rotation_edit.setPlaceholderText(_("= angle (polar) or 0"))
        form.addRow(_("Rotation °:"), self.rotation_edit)

        # "Read current position" (design 2026_08_29_config_tree_read_live_
        # position.md §1.1): fill the position/rotation fields from the live
        # (Role, Cluster) component on the board, expressed in the form's
        # CURRENT mode. PlacerDock wires the click (it owns the adapter).
        self.read_position_button = QPushButton(_("Read current position"))
        form.addRow(self.read_position_button)

        self.retired_checkbox = QCheckBox(_("Retired"))
        form.addRow(self.retired_checkbox)
        self.skip_checkbox = QCheckBox(_("Skip"))
        form.addRow(self.skip_checkbox)

        self._update_mode()

    # ── Mode visibility ──────────────────────────────────────────────────

    def _update_mode(self) -> None:
        mode = self.mode_combo.currentIndex()
        self._cartesian_row.setVisible(mode == 0)
        self._polar_row.setVisible(mode == 1)
        # Self-referential anchor applies to the ABSOLUTE modes only.
        self._anchor_row_absolute.setVisible(mode in (0, 1))
        self._anchor_widget.setVisible(mode == 2)
        self._anchor_offset_row.setVisible(mode == 2)

    def _update_anchor_mode(self) -> None:
        """The self-referential Pad field only matters when the Anchor combo
        is on Pad (same "disabled, not hidden" convention as the offset row)."""
        is_pad = self.anchor_combo.currentIndex() == 1
        self.pad_edit.setEnabled(is_pad)

    def _update_offset_mode(self) -> None:
        """Same Cartesian/Polar field-toggle as every other position mode
        here — X/Y enabled only in Cartesian, Radius/Angle only in Polar
        (disabled, not hidden, so the row keeps a stable layout)."""
        set_mode_pair_enabled(
            self._offset_combo.currentIndex() == 1,
            (self._offset_x_edit, self._offset_y_edit),
            (self._offset_radius_edit, self._offset_angle_edit))

    # ── Live-board / project wiring ──────────────────────────────────────

    def set_known_roles(self, roles: Sequence[str], clusters: Sequence[str]) -> None:
        self._known_roles = list(roles)
        self._known_clusters = list(clusters)
        set_combo_items(self.cluster_combo, clusters)
        set_combo_items(self.role_combo, roles)
        self._anchor_widget.set_known_roles(roles, clusters)

    def set_point_names(self, names: Sequence[str]) -> None:
        self._anchor_widget.set_point_names(names)

    def set_known_sheets(self, sheets: Sequence[str]) -> None:
        """Same "populate, don't restrict" pattern as set_known_roles —
        sheet names come from the project's schematic files
        (RuntimeContext.sheet_names, built in config/loader.py via
        schematic_dir/schematic_files), refreshed on root-file change (see
        collect_all_sheet_names, gui/docks/rename.py). Fed into BOTH the
        form's own sheet identity (self.sheet_edit) and the anchor widget's
        external-anchor sheet (self._anchor_widget) — the same split
        set_known_roles already makes (own Cluster/Role vs Anchor
        Cluster/Role)."""
        set_combo_items(self.sheet_edit, list(sheets))
        self._anchor_widget.set_known_sheets(sheets)

    # ── Float parsing (same shared helper as every other dock) ───────────

    @staticmethod
    def _parse_float(edit: QLineEdit, label: str) -> Tuple[Optional[float], Optional[str]]:
        ok, value = parse_float_field(edit)
        if not ok:
            return None, _("{label}: {text!r} is not a number.").format(
                label=label, text=edit.text().strip())
        return value, None

    # ── Build: read the form into a CoordinatePlacement-shaped dict ──────

    def build(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """(entry dict, error) — error is None on success. Mode-specific
        fields are written with their exact YAML key names (x_mm/y_mm/
        center_x_mm/.../anchor_ref/...), the same keys the table dock's
        _row_to_entry wrote."""
        cluster = self.cluster_combo.currentText().strip()
        role = self.role_combo.currentText().strip()
        entry: Dict[str, Any] = {"cluster": cluster, "role": role}
        name = self.name_edit.text().strip()
        if name:
            entry["name"] = name
        sheet = self.sheet_edit.currentText().strip()
        if sheet:
            entry["sheet"] = sheet
        comment = self.comment_edit.text().strip()
        if comment:
            entry["comment"] = comment

        rotation, err = self._parse_float(self.rotation_edit, _("Rotation"))
        if err:
            return None, err
        if rotation is not None:
            entry["rotation_deg"] = rotation

        mode = self.mode_combo.currentIndex()
        if mode == 2:
            # ANCHOR-RELATIVE: the anchor identity from the shared widget,
            # plus the offset row (Cartesian x_mm/y_mm or polar radius_mm/
            # angle_deg) as the OFFSET from the anchor (or its anchor_pad).
            fields, err = self._anchor_widget.build()
            if err:
                return None, err
            if self._offset_combo.currentIndex() == 1:
                radius, err = self._parse_float(self._offset_radius_edit, _("Radius"))
                if err:
                    return None, err
                angle, err = self._parse_float(self._offset_angle_edit, _("Angle"))
                if err:
                    return None, err
                if radius is not None or angle is not None:
                    if radius is None or angle is None:
                        return None, _("Polar offset needs BOTH Radius and Angle.")
                    entry["radius_mm"] = radius
                    entry["angle_deg"] = angle
            else:
                x, err = self._parse_float(self._offset_x_edit, _("Offset X"))
                if err:
                    return None, err
                y, err = self._parse_float(self._offset_y_edit, _("Offset Y"))
                if err:
                    return None, err
                if x is not None or y is not None:
                    entry["x_mm"] = x or 0.0
                    entry["y_mm"] = y or 0.0
            if "ref" in fields:
                entry["anchor_ref"] = fields["ref"]
            if "role" in fields:
                entry["anchor_role"] = fields["role"]
            if "sheet" in fields:
                entry["anchor_sheet"] = fields["sheet"]
            if "pad" in fields:
                entry["anchor_pad"] = fields["pad"]
            if "cluster" in fields:
                entry["anchor_cluster"] = fields["cluster"]
            if "point" in fields:
                entry["anchor_point"] = fields["point"]
        elif mode == 1:
            # POLAR-ABSOLUTE (around a fixed centre).
            for edit, key, label in (
                    (self.center_x_edit, "center_x_mm", _("Center X")),
                    (self.center_y_edit, "center_y_mm", _("Center Y")),
                    (self.radius_edit, "radius_mm", _("Radius")),
                    (self.angle_edit, "angle_deg", _("Angle"))):
                value, err = self._parse_float(edit, label)
                if err:
                    return None, err
                entry[key] = value
        else:
            # CARTESIAN-ABSOLUTE.
            x, err = self._parse_float(self.x_edit, _("X mm"))
            if err:
                return None, err
            y, err = self._parse_float(self.y_edit, _("Y mm"))
            if err:
                return None, err
            entry["x_mm"] = x
            entry["y_mm"] = y

        if mode != 2:
            # Self-referential anchor (absolute modes): 'pad' = the resolved
            # target lands on ONE specific pad of the moved footprint itself
            # (anchor_pad required iff anchor == 'pad', see the loader). In
            # anchor-relative mode the pad belongs to the ANCHOR component
            # instead and is handled by the widget's own build() above.
            if self.anchor_combo.currentIndex() == 1:
                entry["anchor"] = "pad"
                pad = self.pad_edit.text().strip()
                if pad:
                    entry["anchor_pad"] = pad

        if self.retired_checkbox.isChecked():
            entry["retired"] = True
        if self.skip_checkbox.isChecked():
            entry["skip"] = True
        return entry, None

    # ── Load: reverse of build(), populate the widgets from a saved entry ──

    def load(self, entry: Dict[str, Any]) -> None:
        # Reset the whole form first (same as new_coordinate_placement ->
        # clear()) so the anchor widget never keeps the previous record's
        # cluster/role when this one has no anchor (plan 2026-08-13, p.3 —
        # the stale value would otherwise feed the auto-fill trigger with a
        # WRONG cluster before this record's own nets load).
        self.clear()
        self.cluster_combo.setCurrentText(str(entry.get("cluster", "")))
        self.role_combo.setCurrentText(str(entry.get("role", "")))
        self.name_edit.setText(str(entry.get("name") or ""))
        self.comment_edit.setText(str(entry.get("comment") or ""))
        self.sheet_edit.setCurrentText(str(entry.get("sheet") or ""))
        rotation = entry.get("rotation_deg")
        self.rotation_edit.setText("" if rotation is None else str(rotation))
        self.retired_checkbox.setChecked(bool(entry.get("retired", False)))
        self.skip_checkbox.setChecked(bool(entry.get("skip", False)))

        if any(entry.get(k) for k in ("anchor_ref", "anchor_role", "anchor_point")):
            self.mode_combo.setCurrentIndex(2)
            # point= is passed explicitly — AnchorOriginWidget.load defaults
            # it to "" and unconditionally clears the combo, which would wipe
            # the anchor back out on the next Save (the same data-loss class
            # of bug as placer.py's own Group 2 fix).
            self._anchor_widget.load(
                mode="point" if entry.get("anchor_point") else "anchor",
                point=str(entry.get("anchor_point") or ""),
                ref=str(entry.get("anchor_ref") or ""),
                role=str(entry.get("anchor_role") or ""),
                sheet=str(entry.get("anchor_sheet") or ""),
                pad=str(entry.get("anchor_pad") or ""),
                cluster=str(entry.get("anchor_cluster") or ""))
            # Offset row: polar (radius/angle) or Cartesian (x/y).
            if entry.get("radius_mm") is not None:
                self._offset_combo.setCurrentIndex(1)
                self._offset_radius_edit.setText(str(entry["radius_mm"]))
                self._offset_angle_edit.setText(
                    "" if entry.get("angle_deg") is None else str(entry["angle_deg"]))
            else:
                self._offset_combo.setCurrentIndex(0)
                self._offset_x_edit.setText(
                    "" if entry.get("x_mm") is None else str(entry["x_mm"]))
                self._offset_y_edit.setText(
                    "" if entry.get("y_mm") is None else str(entry["y_mm"]))
        elif entry.get("center_x_mm") is not None:
            self.mode_combo.setCurrentIndex(1)
            for edit, key in ((self.center_x_edit, "center_x_mm"),
                              (self.center_y_edit, "center_y_mm"),
                              (self.radius_edit, "radius_mm"),
                              (self.angle_edit, "angle_deg")):
                value = entry.get(key)
                edit.setText("" if value is None else str(value))
        else:
            self.mode_combo.setCurrentIndex(0)
            self.x_edit.setText("" if entry.get("x_mm") is None else str(entry["x_mm"]))
            self.y_edit.setText("" if entry.get("y_mm") is None else str(entry["y_mm"]))
        if not any(entry.get(k) for k in ("anchor_ref", "anchor_role", "anchor_point")):
            # Self-referential anchor (absolute modes only — in anchor-relative
            # mode the widget's own pad field holds the ANCHOR's pad instead).
            self.anchor_combo.setCurrentIndex(1 if entry.get("anchor") == "pad" else 0)
            self.pad_edit.setText(str(entry.get("anchor_pad") or ""))
        self._update_mode()

    def clear(self) -> None:
        self.cluster_combo.setCurrentText("")
        self.role_combo.setCurrentText("")
        self.name_edit.setText("")
        self.comment_edit.setText("")
        self.sheet_edit.setCurrentText("")
        self.rotation_edit.setText("")
        self.x_edit.setText("")
        self.y_edit.setText("")
        self.center_x_edit.setText("")
        self.center_y_edit.setText("")
        self.radius_edit.setText("")
        self.angle_edit.setText("")
        self._offset_combo.setCurrentIndex(0)
        self._offset_x_edit.setText("")
        self._offset_y_edit.setText("")
        self._offset_radius_edit.setText("")
        self._offset_angle_edit.setText("")
        self.anchor_combo.setCurrentIndex(0)
        self.pad_edit.setText("")
        self.mode_combo.setCurrentIndex(0)
        self._anchor_widget.clear()
        self.retired_checkbox.setChecked(False)
        self.skip_checkbox.setChecked(False)
        self._update_mode()

    # ── Read current position (design 2026_08_29_config_tree_read_live_ ──
    #    position.md §3.1) — write a live read into the CURRENT mode ─────

    @staticmethod
    def _read_float_or_zero(edit: QLineEdit) -> float:
        """Best-effort read of a numeric field for a DEFAULT (blank/invalid ->
        0.0) — used for the fixed polar centre, where an absent value is a
        legitimate "board origin" default, not a user error."""
        try:
            return float(edit.text().strip())
        except ValueError:
            return 0.0

    def write_live_position(self, read: LiveRead,
                            anchor_position: Optional[Vector2] = None) -> None:
        """Fill the position/rotation fields from a live read, expressed in
        the form's CURRENT position mode:
          - Cartesian absolute: x/y = the read position;
          - polar-around-centre: radius/angle from the form's fixed centre;
          - anchor-relative: the offset from the resolved anchor
            (anchor_position — required in this mode, computed by the dock).
        Rotation is written whenever the read carries one (a point-relative
        read has none). Values rounded to 3 decimals, same as TreesDock."""
        x_mm = read.position.x / MM
        y_mm = read.position.y / MM
        mode = self.mode_combo.currentIndex()
        if mode == 0:  # Cartesian absolute
            self.x_edit.setText(f"{x_mm:.3f}")
            self.y_edit.setText(f"{y_mm:.3f}")
        elif mode == 1:  # polar around a fixed centre
            cx = self._read_float_or_zero(self.center_x_edit)
            cy = self._read_float_or_zero(self.center_y_edit)
            dx, dy = x_mm - cx, y_mm - cy
            self.radius_edit.setText(f"{math.hypot(dx, dy):.3f}")
            self.angle_edit.setText(f"{math.degrees(math.atan2(dy, dx)):.3f}")
        else:  # anchor-relative — needs the anchor's live position
            if anchor_position is None:
                return
            ox = x_mm - anchor_position.x / MM
            oy = y_mm - anchor_position.y / MM
            if self._offset_combo.currentIndex() == 1:  # polar offset
                self._offset_radius_edit.setText(f"{math.hypot(ox, oy):.3f}")
                self._offset_angle_edit.setText(f"{math.degrees(math.atan2(oy, ox)):.3f}")
            else:
                self._offset_x_edit.setText(f"{ox:.3f}")
                self._offset_y_edit.setText(f"{oy:.3f}")
        if read.rotation_deg is not None:
            self.rotation_edit.setText(f"{read.rotation_deg:.3f}")


class PlacerDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — used
    to be its own QDockWidget, merged 2026-08-03 (see the retired Extract
    dock's module docstring note for the same change). Layout builds
    directly on self
    instead of a wrapped QDockWidget-owned container; everything else is
    unchanged."""

    # Fired after a successful Save — ConfigTreeDock listens to refresh its
    # Clone placements category (see gui/dock_hub.py).
    saved = pyqtSignal()

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        # The currently running long op (gui/worker.py) — held so the
        # parent-less QThread isn't garbage-collected mid-run.
        self._active_op: Optional[Any] = None
        self._cells_path: Optional[Path] = None
        self._placer_path: Optional[Path] = None
        self._root_path: Optional[Path] = None
        self._selected_cell: Optional[str] = None
        # Live board selection (2026-08-31, plan placer_source_tab_gaps P.1)
        # — pushed every selection-watch tick via DockHub.set_board_selection,
        # same (raw_items, selected_footprints) shape as the retired Extract
        # dock's own set_board_selection. Drives the Cell-mode Cluster
        # auto-fill (only
        # the selected footprints' Cluster field is actually read here).
        self._raw_items: List[Any] = []
        self._selected_footprints: List[Any] = []
        # Selection-signature guard for _autofill_cluster_from_selection — a
        # tick whose cluster-relevant state (selected Cluster-set / dirty
        # flag / current field text) is unchanged is a no-op, so the ~400ms
        # selection-watch tick can't churn the auto-fill for nothing (same
        # "don't redo work on an unchanged tick" idea as the retired Extract
        # dock's _last_autofill_key).
        self._last_selection_cluster_signature: Optional[tuple] = None
        # G4.4 cache (2026-08-12, carried over from the merged-in coordinate
        # dock): last-tick known-value SETS — refresh_known_roles skips the
        # whole repopulation loop when neither has changed (the ~2s poll tick
        # almost never sees a change).
        self._known_roles_cache: set = set()
        self._known_clusters_cache: set = set()
        # Cluster/Name identity field (Cell mode) — True once it holds a value
        # the user is responsible for (typed/picked by hand, OR loaded from an
        # already-saved entry). set_cluster_name() (tree-click auto-fill) must
        # not clobber it once true; reset only when the form goes back to blank
        # (new_placement) — auto-fill is exactly what a BLANK form wants
        # (2026-08-15, plan cluster_field_autofill_not_hard_overwrite).
        self._cluster_identity_dirty: bool = False
        # Placer name (save/--only identity, separate from Cluster since
        # 2026-08-15) — True once the user has taken ownership (typed it
        # directly, or loaded an already-saved entry). Auto-fill-from-Cluster
        # only applies while this is False — i.e. only while CREATING a new
        # placement (Денис: "автозаполнение только при создании пласера.
        # Дальше уже не надо").
        self._placer_name_dirty: bool = False
        # Effective identity (placer_name or name) of whichever clone_placement
        # is currently loaded in the form — None for a brand new (unsaved)
        # entry. _do_save() compares this against the about-to-be-saved entry's
        # own identity: if they differ, the user renamed via Cluster or Placer
        # name field directly (not via the Config tree's Rename) — the OLD
        # entry must be removed first, or upsert (which only ever matches by
        # the CURRENT identity) appends a duplicate instead of replacing it
        # (2026-08-15, plan placer_form_save_renames_not_duplicates).
        self._loaded_clone_identity: Optional[str] = None
        # The same "form-level rename must delete the old record" identity,
        # for Entity mode (2026-08-30, Entity/Placement split, phase 5.2) —
        # see _loaded_clone_identity and _do_save_entity.
        self._loaded_entity_identity: Optional[str] = None
        # Stored manual by-nets override fields (params/nets/net_overrides/
        # refs) of whichever record is loaded — carried forward unchanged on
        # save, since the Placer no longer edits them (2026-09-05).
        self._loaded_override_fields: Dict[str, Any] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Tabbed (2026-08-06, Denis: "в пласере точно надо табом. Он может
        # быть длинный!") — same "a stacked QVBoxLayout's minimum height is
        # the SUM of every section's own" fix Extract/Root/Rules/Cells
        # already got. Buttons/message stay OUTSIDE the tabs — they act on
        # the whole placement, not one tab.
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)

        # Coordinate mode's identity fields (Cluster/Role/Name) are laid out
        # on the SOURCE tab (see _coordinate_identity_row below), so the form
        # must exist before the Source page is built — only its positioning
        # half is added to the Coordinate tab later (2026-08-13, plan
        # coordinate_identity_on_source_tab).
        self.coordinate_form = _CoordinatePlacementForm()
        self.coordinate_form.read_position_button.clicked.connect(
            self._on_coordinate_read_position)

        source_page = QWidget()
        source_page_layout = QVBoxLayout(source_page)
        source_form = QFormLayout()
        self.cell_mode_combo = QComboBox()
        # Two sources since 2026-08-12 (Group 1 consolidation): "Cell" =
        # ClonePlacement (template cloning, cell: required), "Single
        # component" = CoordinatePlacement (no cell:, Cluster+Role match).
        # _on_cell_mode_changed switches which form field set is shown.
        # Entity/Placement split (2026-08-30, phase 5.2): "Entity" is the new
        # source — a pick from cfg.entities (the "what"), placed via its trees:
        # node (Origin tab). "Cell" (legacy ClonePlacement) and "Single
        # component" (CoordinatePlacement) stay until the release cutover.
        self.cell_mode_combo.addItems([_("Cell"), _("Single component"), _("Entity")])
        self.cell_mode_combo.currentIndexChanged.connect(self._on_cell_mode_changed)
        source_form.addRow(_("Source:"), self.cell_mode_combo)
        source_page_layout.addLayout(source_form)

        self._cell_row = QWidget()
        cell_form = QFormLayout(self._cell_row)
        cell_form.setContentsMargins(0, 0, 0, 0)
        self.cell_combo = QComboBox()
        # Deliberately NOT configure_searchable() (2026-08-06, live freeze in
        # CellDock's own anchor_role_combo taught this: an editable combo +
        # QCompleter on a field whose value space is a CLOSED SET — must
        # match an existing cells: key — is both a plausible freeze risk and
        # semantically wrong; see CellDock's own anchor_role_combo for the
        # same fix). Cell picking still also works from the Config tree's
        # Cells category (set_selected_cell) — this combo is a second,
        # faster way to do the exact same thing in place, requested live
        # 2026-08-06 (Denis: "в пласере давай сделаем имя целла по
        # выпадающему комбо-боксу... не удобно" ходить в дерево конфига
        # за каждым пиком).
        self.cell_combo.setPlaceholderText(_("pick a cell"))
        self.cell_combo.currentTextChanged.connect(self.set_selected_cell)
        cell_form.addRow(_("Cell:"), self.cell_combo)
        source_page_layout.addWidget(self._cell_row)

        # Entity row (Entity/Placement split, phase 5.2): pick an Entity — its
        # cell/cluster/sheet/name/comment come FROM the record; the form's
        # Nets/Overrides/Refs/Origin tabs edit the rest (Entity fields + the
        # tree node). Hidden for Cell / Single-component modes.
        self._entity_row = QWidget()
        entity_form = QFormLayout(self._entity_row)
        entity_form.setContentsMargins(0, 0, 0, 0)
        self.entity_combo = QComboBox()
        self.entity_combo.setPlaceholderText(_("pick an Entity"))
        self.entity_combo.currentTextChanged.connect(self._on_entity_picked)
        entity_form.addRow(_("Entity:"), self.entity_combo)
        source_page_layout.addWidget(self._entity_row)

        self._name_row = QWidget()
        form = QFormLayout(self._name_row)
        form.setContentsMargins(0, 0, 0, 0)
        # Own-identity sheet (2026-08-15, Cell mode): narrows ambiguous
        # Cluster+Role when this cell is cloned across reused sheets — optional,
        # same (Sheet, Cluster, Role) order as the Single-component row above.
        self.sheet_edit = QComboBox()
        configure_searchable(self.sheet_edit)
        self.sheet_edit.lineEdit().setPlaceholderText(
            _("sheet name (narrows ambiguous Cluster+Role when this cell is "
              "cloned across reused sheets, optional)"))
        form.addRow(_("Sheet:"), self.sheet_edit)
        self.cluster_edit = QComboBox()
        configure_searchable(self.cluster_edit)
        self.cluster_edit.lineEdit().setPlaceholderText(_("cluster tag (written onto the board's components)"))
        form.addRow(_("Cluster:"), self.cluster_edit)
        self.placer_name_edit = QLineEdit()
        self.placer_name_edit.setPlaceholderText(
            _("same as Cluster unless changed (identity for Save/--only)"))
        form.addRow(_("Name:"), self.placer_name_edit)
        self.placer_comment_edit = QLineEdit()
        self.placer_comment_edit.setPlaceholderText(_("optional free-form note"))
        form.addRow(_("Comment:"), self.placer_comment_edit)
        source_page_layout.addWidget(self._name_row)
        # Auto-fill on the PLACEMENT's Cluster COMMIT (plan 2026-08-13, p.2;
        # re-tied to cluster_edit 2026-08-14, split anchor_cluster: the
        # auto-fill query key is clone.name, not anchor_cluster): activated =
        # a pick from the dropdown list, editingFinished = commit of typed
        # text (Enter / focus loss). Deliberately NOT currentTextChanged/
        # editTextChanged — those fire on every keystroke and would flood the
        # kipy socket with live board reads while the user is still typing.
        # Mark the Cluster/Name field "user-owned" on the same commit signals
        # (2026-08-15, plan cluster_field_autofill_not_hard_overwrite) — once
        # the user has typed/picked a value, the tree-click auto-fill must not
        # clobber it. Same signal choice as _maybe_autofill_nets: NOT
        # textChanged/editTextChanged (those fire on every keystroke).
        self.cluster_edit.activated.connect(self._mark_cluster_identity_dirty)
        self.cluster_edit.lineEdit().editingFinished.connect(self._mark_cluster_identity_dirty)
        # Placer name auto-fill from Cluster — ONLY while creating a brand new
        # placement (2026-08-15, plan clone_placement_placer_name_split; Денис:
        # "автозаполнение только при создании пласера. Дальше уже не надо").
        # Same commit signals as _maybe_autofill_nets; the _placer_name_dirty
        # flag (set on load/direct edit, reset only by new_placement) keeps
        # this from dragging Placer name along while editing an already-loaded
        # entry's Cluster.
        self.cluster_edit.activated.connect(self._maybe_autofill_placer_name)
        self.cluster_edit.lineEdit().editingFinished.connect(self._maybe_autofill_placer_name)
        self.placer_name_edit.editingFinished.connect(self._mark_placer_name_dirty)

        # Single-component (CoordinatePlacement) identity row on the SOURCE
        # tab (2026-08-13, plan coordinate_identity_on_source_tab, Denis:
        # "Cluster, Role, Name надо на первый таб перенести" — they used to
        # live on the Coordinate tab, mixed with the positioning fields,
        # which was confusing). These are the _CoordinatePlacementForm's own
        # widgets, just laid out here instead of inside the form — everything
        # that reads/writes them goes through coordinate_form.<attr> and is
        # unaffected. The "Cluster:" label intentionally matches _name_row
        # above (Cell mode) — two different fields, never visible together.
        self._coordinate_identity_row = QWidget()
        coordinate_identity_form = QFormLayout(self._coordinate_identity_row)
        coordinate_identity_form.setContentsMargins(0, 0, 0, 0)
        coordinate_identity_form.addRow(_("Sheet:"), self.coordinate_form.sheet_edit)
        coordinate_identity_form.addRow(_("Cluster:"), self.coordinate_form.cluster_combo)
        coordinate_identity_form.addRow(_("Role:"), self.coordinate_form.role_combo)
        coordinate_identity_form.addRow(_("Name:"), self.coordinate_form.name_edit)
        coordinate_identity_form.addRow(_("Comment:"), self.coordinate_form.comment_edit)
        source_page_layout.addWidget(self._coordinate_identity_row)
        source_page_layout.addStretch(1)
        self._tabs.addTab(source_page, _("Source"))

        # NOTE (2026-09-05): the Nets / Net overrides / Refs tabs were REMOVED —
        # nets auto-resolve (derive_role_nets, 2026-08-28), so the manual
        # by-nets override editors are unnecessary for the typical flow. Stored
        # nets:/params:/net_overrides:/refs: on existing records stay intact
        # (carried forward untouched on save — see _build_entry_dict /
        # _loaded_clone_override_fields). The Engine still applies them when
        # present; there is simply no GUI to edit them in the Placer anymore.

        # Coordinate mode's form (2026-08-12, Group 1): one tab hosting the
        # single-entry _CoordinatePlacementForm — shown only when the Source
        # combo is on "Single component", hidden for Cell/clone mode (see
        # _on_cell_mode_changed).
        coordinate_page = QWidget()
        coordinate_page_layout = QVBoxLayout(coordinate_page)
        # self.coordinate_form was created before the Source tab was built
        # (its identity fields live there); only its positioning half is
        # added to this page.
        coordinate_page_layout.addWidget(self.coordinate_form)
        coordinate_page_layout.addStretch(1)
        self._coordinate_tab_index = self._tabs.addTab(coordinate_page, _("Coordinate"))

        origin_page = QWidget()
        origin_page_layout = QVBoxLayout(origin_page)
        # shift=True here even though ClonePlacement has no separate shift
        # fields of its own — Anchor/Point mode reuse entry["xy"] itself to
        # carry the shift (see _build_entry_dict below), a ClonePlacement-
        # only quirk the shared widget deliberately stays ignorant of (see
        # gui/docks/_anchor_origin.py's module docstring).
        self.origin_widget = AnchorOriginWidget(
            modes=["xy", "anchor", "point"], anchor_fields=["sheet", "pad", "cluster"],
            shift=True, polar=True)
        origin_page_layout.addWidget(self.origin_widget)
        # Aliases onto the shared widget's own sub-widgets — kept so
        # existing tests/call sites that poke fields directly keep working.
        self.origin_mode_combo = self.origin_widget.origin_mode_combo
        self.x_edit = self.origin_widget.x_edit
        self.y_edit = self.origin_widget.y_edit
        self.anchor_ref_edit = self.origin_widget.anchor_ref_edit
        self.anchor_role_edit = self.origin_widget.anchor_role_edit
        self.anchor_sheet_edit = self.origin_widget.anchor_sheet_edit
        self.anchor_pad_edit = self.origin_widget.anchor_pad_edit
        self.anchor_cluster_edit = self.origin_widget.anchor_cluster_edit
        self.point_edit = self.origin_widget.point_edit
        self.shift_x_edit = self.origin_widget.shift_x_edit
        self.shift_y_edit = self.origin_widget.shift_y_edit
        # NOTE: the auto-fill auto-trigger used to be wired here on
        # anchor_cluster_edit (Origin tab); since the 2026-08-14 split it is
        # tied to cluster_edit (Source tab) instead — see the wiring right
        # after cluster_edit's creation above. anchor_cluster_edit narrows
        # only the anchor now, so it must NOT trigger role auto-fill.

        extra_form = QFormLayout()
        self.rotation_edit = QLineEdit()
        self.rotation_edit.setPlaceholderText("0")
        extra_form.addRow(_("Rotation (deg):"), self.rotation_edit)
        self.layer_combo = QComboBox()
        self.layer_combo.addItems([_("(cell default)"), "F.Cu", "B.Cu"])
        extra_form.addRow(_("Layer:"), self.layer_combo)
        # "Read current position" (design 2026_08_29_config_tree_read_live_
        # position.md §1.2/§3.2): fill the cell ORIGIN + rotation from the
        # live board, re-derived from a placed component of the cell.
        self.read_position_button = QPushButton(_("Read current position"))
        self.read_position_button.clicked.connect(self._on_clone_read_position)
        extra_form.addRow(self.read_position_button)
        origin_page_layout.addLayout(extra_form)
        self.mirror_checkbox = QCheckBox(_("Mirror"))
        origin_page_layout.addWidget(self.mirror_checkbox)
        # NOTE (2026-09-05): the "Cell anchor" box (assign/move the CELL's
        # REAL internal anchor — design 2026-09-04_cell_internal_anchor.md §2)
        # moved to its OWN tab below; this Origin page is only about THIS
        # placement's own position.
        # Entity mode's placement status (phase 5.2, stage 2): "placed under
        # tree X" vs "не размещено" — the Origin tab edits the Entity's trees:
        # node, and an Entity with no node is a legal unplaced record. Hidden
        # in Cell / Single-component modes (only _on_cell_mode_changed touches
        # this label).
        self._placement_status_label = QLabel("")
        self._placement_status_label.setVisible(False)
        origin_page_layout.addWidget(self._placement_status_label)
        origin_page_layout.addStretch(1)
        self._origin_tab_index = self._tabs.addTab(origin_page, _("Origin"))

        # "Cell anchor" — its OWN tab (Denis 2026-09-05): assign/move the
        # CELL's REAL internal anchor (design 2026-09-04_cell_internal_anchor.md
        # §2) — a one-shot rebase of the stored offsets so the chosen component
        # (role mode) or its pad (role+pad mode) becomes the cell's new local
        # (0,0). A per-CELL action — every applied instance of this cell shifts
        # on the next Redraw/Apply (design §2.4, expected — no blocking
        # warning). Deliberately separate from the Origin page, which is about
        # THIS placement's own position, not the cell's origin.
        cell_anchor_page = QWidget()
        cell_anchor_page_layout = QVBoxLayout(cell_anchor_page)
        self.cell_anchor_box = QGroupBox(_("Cell anchor"))
        cell_anchor_layout = QVBoxLayout(self.cell_anchor_box)
        cell_anchor_form = QFormLayout()
        self.cell_anchor_mode_combo = QComboBox()
        self.cell_anchor_mode_combo.addItems(
            [_("Role"), _("Role + Pad"), _("Point"), _("Reset (bbox default)")])
        cell_anchor_form.addRow(_("Anchor mode:"), self.cell_anchor_mode_combo)
        self.cell_anchor_role_combo = QComboBox()
        self.cell_anchor_role_combo.setPlaceholderText(
            _("pick this cell's component role"))
        cell_anchor_form.addRow(_("Role:"), self.cell_anchor_role_combo)
        self.cell_anchor_pad_edit = QLineEdit()
        self.cell_anchor_pad_edit.setPlaceholderText(_("pad (optional)"))
        cell_anchor_form.addRow(_("Pad:"), self.cell_anchor_pad_edit)
        point_row = QWidget()
        point_layout = QHBoxLayout(point_row)
        point_layout.setContentsMargins(0, 0, 0, 0)
        self.cell_anchor_x_edit = QLineEdit()
        self.cell_anchor_x_edit.setPlaceholderText(_("x (mm from bbox corner)"))
        self.cell_anchor_y_edit = QLineEdit()
        self.cell_anchor_y_edit.setPlaceholderText(_("y (mm from bbox corner)"))
        point_layout.addWidget(self.cell_anchor_x_edit)
        point_layout.addWidget(self.cell_anchor_y_edit)
        cell_anchor_form.addRow(_("Point X/Y:"), point_row)
        cell_anchor_layout.addLayout(cell_anchor_form)
        self.set_cell_anchor_button = QPushButton(_("Set as anchor"))
        self.set_cell_anchor_button.clicked.connect(self._on_set_cell_anchor)
        cell_anchor_layout.addWidget(self.set_cell_anchor_button)
        hint = QLabel(_("The anchor is a mount point (bbox frame): Role mounts on "
                        "that component, Role+Pad on that pad, only X/Y on that "
                        "point. The stored offsets are NEVER rewritten; the "
                        "anchor is applied at placement. Leave everything empty "
                        "to clear the anchor (back to the bbox default)."))
        hint.setWordWrap(True)
        cell_anchor_layout.addWidget(hint)
        cell_anchor_page_layout.addWidget(self.cell_anchor_box)
        cell_anchor_page_layout.addStretch(1)
        self._cell_anchor_tab_index = self._tabs.addTab(
            cell_anchor_page, _("Cell anchor"))

        button_row = QHBoxLayout()
        self.redraw_button = QPushButton(_("Redraw"))
        self.redraw_button.clicked.connect(self._on_redraw)
        button_row.addWidget(self.redraw_button)
        # 2026-09-05 (plan config_qview_placer_nettrace S-C): the Placer page
        # keeps ONLY Redraw + Select on board — Redraw dependents, Redraw &
        # Save and Undo were removed (Denis; plan_placer_button_cleanup).
        # Select on board (2026-08-25, handoff clone_item_resolver_select_and_
        # reextract): resolve the CURRENT form's placement to its live board
        # items and highlight them in pcbnew — visual check of what this
        # placement really owns, without moving anything. See
        # _on_select_on_board / placement/services/board_items_resolver.py.
        self.select_button = QPushButton(_("Select on board"))
        self.select_button.clicked.connect(self._on_select_on_board)
        button_row.addWidget(self.select_button)
        layout.addLayout(button_row)

        # Auto-stage wiring (2026-09-01, plan project_save_model): a field
        # commit point stages the current placement into the working set.
        # _loading guards programmatic population; _autostage silently skips
        # incomplete forms (an invalid partial placement is NEVER staged).
        self._loading = False
        self.entity_combo.currentIndexChanged.connect(self._autostage)
        self.cluster_edit.activated.connect(self._autostage)
        self.cluster_edit.lineEdit().editingFinished.connect(self._autostage)
        self.sheet_edit.currentIndexChanged.connect(self._autostage)
        self.placer_name_edit.editingFinished.connect(self._autostage)
        self.placer_comment_edit.editingFinished.connect(self._autostage)
        self.rotation_edit.editingFinished.connect(self._autostage)
        self.layer_combo.currentIndexChanged.connect(self._autostage)
        self.mirror_checkbox.toggled.connect(self._autostage)
        for w in self.origin_widget.findChildren(QLineEdit):
            w.editingFinished.connect(self._autostage)
        for w in self.origin_widget.findChildren(QComboBox):
            w.currentIndexChanged.connect(self._autostage)
        for w in self.coordinate_form.findChildren(QLineEdit):
            w.editingFinished.connect(self._autostage)
        for w in self.coordinate_form.findChildren(QComboBox):
            w.currentIndexChanged.connect(self._autostage)

        self._on_cell_mode_changed()

    # ── Cell source toggle ──────────────────────────────────────────────

    @property
    def is_coordinate(self) -> bool:
        """True when the Source combo is on "Single component"
        (CoordinatePlacement) mode (2026-08-13 cleanup): the scattered
        cell_mode_combo.currentIndex() == 1 checks collapsed into one name."""
        return self.cell_mode_combo.currentIndex() == 1

    @property
    def is_entity(self) -> bool:
        """True when the Source combo is on "Entity" mode (2026-08-30,
        Entity/Placement split, phase 5.2): the form edits an Entity record
        (name + cell + electrical/identity fields, NO position — that lives
        only in the trees: node)."""
        return self.cell_mode_combo.currentIndex() == 2

    def _on_cell_mode_changed(self) -> None:
        """Source-mode toggle (2026-08-12, Group 1): the merged dock edits
        BOTH ClonePlacement (cell:, template cloning) and CoordinatePlacement
        (single component, no cell:) — the form's field set adapts to which
        one the loaded/new entry is. Cell mode shows the clone field set
        (cell picker + name + Params/Nets/Overrides/Refs/Origin tabs);
        Single-component mode shows the _CoordinatePlacementForm's
        Cluster/Role/position block instead; Entity mode (2026-08-30) shows
        the Entity picker and the same electrical tabs. Kept as a method
        because the combo signal and new_placement/
        new_coordinate_placement/load_placement still call it."""
        is_coordinate = self.is_coordinate
        is_entity = self.is_entity
        self._cell_row.setVisible(not is_coordinate and not is_entity)
        self._name_row.setVisible(not is_coordinate and not is_entity)
        self._entity_row.setVisible(is_entity)
        self._coordinate_identity_row.setVisible(is_coordinate)
        self._placement_status_label.setVisible(is_entity)
        self._tabs.setTabVisible(self._origin_tab_index, not is_coordinate)
        self._tabs.setTabVisible(self._coordinate_tab_index, is_coordinate)
        # "Cell anchor" is a Cell (clone)-mode action — the tab is hidden in
        # Single-component / Entity modes (the Origin page is shared there;
        # the rebase would be meaningless).
        self._tabs.setTabVisible(self._cell_anchor_tab_index,
                                 not is_coordinate and not is_entity)

    # ── Wiring from the Config tree / Components tree ─────────────────────

    def _refresh_cell_choices(self) -> None:
        names = collect_all_cell_names(self._root_path) if self._root_path is not None else []
        set_combo_items(self.cell_combo, names)

    def _refresh_entity_choices(self) -> None:
        """Every entities: record name reachable from root_path via include:
        (2026-08-30, Entity/Placement split, phase 5.2) — the Source combo of
        Entity mode. Graph-wide like collect_all_cell_names: an Entity lives
        wherever its record does, possibly in any included file. Empty list
        on a broken/unsupported root config — combo autocomplete, not
        validation (same 2026-08-28 hardening as the collect_all_* helpers)."""
        names: List[str] = []
        if self._root_path is not None:
            try:
                cfg, _ctx = load_config(str(self._root_path))
            except (ValidationError, OSError):
                cfg = None
            if cfg is not None:
                names = [entity_effective_name(e) for e in cfg.entities]
        set_combo_items(self.entity_combo, sorted(names))

    def _load_entity_data(self, name: str) -> Optional[Entity]:
        """The Entity record named `name`, graph-wide (phase 5.2) — one
        load_config() of the root resolves the whole include graph. None on
        a broken config or an unknown name."""
        if not name or self._root_path is None:
            return None
        try:
            cfg, _ctx = load_config(str(self._root_path))
        except (ValidationError, OSError):
            return None
        for entity in cfg.entities:
            if entity_effective_name(entity) == name:
                return entity
        return None

    def _on_entity_picked(self, name: str) -> None:
        """Entity mode's pick (phase 5.2): load the picked record's own
        electrical/identity fields into the form so a Save re-writes the
        same record. Position deliberately NOT loaded — an Entity never
        carries a position (that is the trees: node's job)."""
        name = (name or "").strip()
        if not name:
            return
        entity = self._load_entity_data(name)
        if entity is None:
            return
        self._loading = True
        try:
            self._populate_entity_form(entity)
        finally:
            self._loading = False

    def _populate_entity_form(self, entity) -> None:
        """Fill the form from a loaded Entity — split out of _on_entity_picked
        so the whole populate sits under the _loading auto-stage guard (see
        _autostage)."""
        self._selected_cell = entity.cell
        self._rebuild_cell_role_choices()
        self.cluster_edit.setCurrentText(entity.cluster or "")
        self.placer_name_edit.setText(entity.name)
        self.placer_comment_edit.setText(entity.comment or "")
        self.sheet_edit.setCurrentText(entity.sheet or "")
        self.layer_combo.setCurrentIndex({"F.Cu": 1, "B.Cu": 2}.get(entity.layer, 0))
        self.mirror_checkbox.setChecked(entity.mirror)
        # Remember the identity the form loaded — _do_save_entity removes the
        # old record when the about-to-be-saved name differs (rename via the
        # combo), mirroring the clone path's _loaded_clone_identity.
        self._loaded_entity_identity = entity.name
        # Entity nets/params are edited in the Tools dock, not here — keep the
        # record's stored override fields so a Placer save never wipes them.
        self._loaded_override_fields = {}
        for key in ("params", "nets", "net_overrides", "refs"):
            value = getattr(entity, key, None)
            if value:
                self._loaded_override_fields[key] = value
        # Phase 5.2 stage 2: load the Entity's placement node (trees:) into
        # the Origin tab — or show "не размещено" when it has no node.
        self._load_entity_node_origin(entity)

    def _find_entity_node(self, name: str) -> Optional[tuple]:
        """(tree, node) for the trees: node that PLACES `name` — a recursive
        search across every tree in the whole include graph (a node can be a
        top-level node or nested under a parent). None when the Entity is
        unplaced ("не размещено" is legal — an Entity without a node)."""
        if not name or self._root_path is None:
            return None
        try:
            cfg, _ctx = load_config(str(self._root_path))
        except (ValidationError, OSError):
            return None
        for tree in cfg.trees:
            hit = self._search_node(tree.nodes, name)
            if hit is not None:
                return tree, hit
        return None

    @staticmethod
    def _search_node(nodes, name: str):
        """DFS for a TreeNode whose ref == name, or None."""
        for node in nodes:
            if node.ref == name:
                return node
            child = PlacerDock._search_node(node.children, name)
            if child is not None:
                return child
        return None

    def _load_entity_node_origin(self, entity: Entity) -> None:
        """Load the Entity's placement node into the Origin tab (phase 5.2,
        stage 2) — mirror of _build_entity_node_dict. The tree ANCHOR decides
        the widget mode (origin -> xy absolute; ref/role -> anchor; point ->
        point); the node's xy/polar becomes the offset/shift. An unplaced
        Entity clears the position widgets and says so."""
        found = self._find_entity_node(entity.name)
        if found is None:
            self.origin_widget.clear()
            self.rotation_edit.setText("")
            self._placement_status_label.setText(
                _("Not placed — set an origin to place it."))
            return
        tree, node = found
        anchor = tree.anchor
        polar = node.polar is not None
        if polar:
            radius, angle = node.polar
        else:
            xy = node.xy or (0.0, 0.0)
        if anchor.is_origin:
            if polar:
                self.origin_widget.load(mode="xy", polar=True, radius=radius, angle=angle)
            else:
                self.origin_widget.load(mode="xy", x=xy[0], y=xy[1])
        elif anchor.point is not None:
            if polar:
                self.origin_widget.load(mode="point", point=anchor.point,
                                        polar=True, radius=radius, angle=angle)
            else:
                self.origin_widget.load(mode="point", point=anchor.point,
                                        shift_x=xy[0], shift_y=xy[1])
        else:  # ref/role anchor
            if polar:
                self.origin_widget.load(
                    mode="anchor", ref=anchor.ref or "", role=anchor.role or "",
                    sheet=anchor.anchor_sheet or "", pad=anchor.anchor_pad or "",
                    cluster=anchor.anchor_cluster or "",
                    polar=True, radius=radius, angle=angle)
            else:
                self.origin_widget.load(
                    mode="anchor", ref=anchor.ref or "", role=anchor.role or "",
                    sheet=anchor.anchor_sheet or "", pad=anchor.anchor_pad or "",
                    cluster=anchor.anchor_cluster or "",
                    shift_x=xy[0], shift_y=xy[1])
        self.rotation_edit.setText(str(node.rotation))
        self._placement_status_label.setText(
            _("Placed under tree {tree!r}.").format(tree=tree.name))

    def _build_entity_node_dict(self) -> Optional[Dict[str, Any]]:
        """Entity mode's OPTIONAL placement payload (phase 5.2, stage 2):
        the Origin tab's position as the generic AnchorOriginWidget fields.
        None when the position part is blank — the Entity stays/keeps its
        current placement ("не размещено" is legal); nothing is written.
        Returns fields + shows the validation error when the position is
        filled but invalid."""
        origin = self.origin_widget
        polar = (origin._polar_combo is not None
                 and origin._polar_combo.currentIndex() == 1)
        mode = origin.mode
        if mode == "xy":
            if polar:
                if not (origin.radius_edit.text().strip()
                        or origin.angle_edit.text().strip()):
                    return None
            elif not (self.x_edit.text().strip() or self.y_edit.text().strip()):
                return None
        elif mode == "anchor" and not (self.anchor_ref_edit.text().strip()
                                       or self.anchor_role_edit.currentText().strip()):
            return None
        elif mode == "point" and not self.point_edit.currentText().strip():
            return None
        fields, err = origin.build()
        if err:
            self._show_message(err, _ERROR_STYLE)
            return None
        return fields

    def _cell_data(self, name: Optional[str]) -> dict:
        """Full cells: entry dict for `name`, read from the WHOLE include
        graph (a cell can live in any included file) — after the file pickers
        were removed (2026-08-21, plan flatten_and_single_file_gui) the dock
        edits the whole project, so a cell's params/roles must be looked up
        graph-wide, not in one hand-picked file."""
        if not name or self._root_path is None:
            return {}
        return collect_section_entries(self._root_path, "cells").get(name, {})

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — new clone_placements:/
        coordinate_placements: entries are always written to the project root
        file (2026-08-21, plan flatten_and_single_file_gui), so both file
        targets ARE the root. The Point combo stays sourced from the WHOLE
        include graph (a Point routinely lives in a different file than the
        clone_placement referencing it)."""
        self._root_path = path
        self._cells_path = path
        self._placer_path = path
        self._refresh_cell_choices()
        self._refresh_entity_choices()
        self._refresh_point_names()
        self._refresh_sheet_names()

    def _refresh_point_names(self) -> None:
        names = collect_all_point_names(self._root_path) if self._root_path is not None else []
        self.origin_widget.set_point_names(names)
        self.coordinate_form.set_point_names(names)

    def _refresh_sheet_names(self) -> None:
        """Sheet-name autocomplete for every Sheet field in this dock —
        origin_widget (ClonePlacement's external anchor), coordinate_form
        (Single-component mode's own sheet AND its anchor widget) and the
        Cell mode's own sheet_edit — from the project's schematic files
        (RuntimeContext.sheet_names), refreshed on root-file change like the
        Point names, NOT the ~2s board poll (see collect_all_sheet_names,
        gui/docks/rename.py)."""
        names = collect_all_sheet_names(self._root_path) if self._root_path is not None else []
        self.origin_widget.set_known_sheets(names)
        self.coordinate_form.set_known_sheets(names)
        set_combo_items(self.sheet_edit, names)

    def set_selected_cell(self, name: str) -> None:
        """Shared entry point for picking a Cell — called both by
        ConfigTreeDock's Cells category (see gui/docks/config_tree.py) when
        a Cell is clicked there (Cell picking used to live inside this dock,
        but the user expected it alongside the Components tree instead,
        2026-08-01: "где выбирать cell? ...к дереву компонент надо
        добавить табик со списком cell") AND by cell_combo's own
        currentTextChanged (2026-08-06: a second, in-place way to pick the
        same thing — see cell_combo's own comment). blockSignals around the
        combo update either way: called externally, it must reflect the new
        text into the combo without re-entering this same method through
        its own signal; called from the combo itself, the text already
        matches, so this is a no-op — either way, no double rebuild."""
        if not name:
            return
        if self.is_coordinate:
            # Picking a Cell is a clone-placement intent (2026-08-12, Group
            # 1) — switch the Source combo back to Cell mode so the picked
            # cell is actually visible in the form.
            self.cell_mode_combo.setCurrentIndex(0)
            self._on_cell_mode_changed()
        self._selected_cell = name
        self._refresh_cell_choices()
        self.cell_combo.blockSignals(True)
        if self.cell_combo.findText(name) < 0:
            self.cell_combo.addItem(name)
        self.cell_combo.setCurrentText(name)
        self.cell_combo.blockSignals(False)
        self._rebuild_cell_role_choices()

    def set_selected_entity(self, name: str) -> None:
        """ConfigTreeDock's Entities leaf click (phase 5.6): switch to Entity
        mode and load the picked Entity into the form — the same path as
        picking it in the Entity combo."""
        if not name:
            return
        self._loading = True
        try:
            if not self.is_entity:
                self.cell_mode_combo.setCurrentIndex(2)  # -> Entity (signal toggles)
                self._on_cell_mode_changed()
            self.entity_combo.setCurrentText(name)
        finally:
            self._loading = False

    def set_cluster_name(self, name: str) -> None:
        """Called by RoleClusterTreeDock's cluster_picked signal when a
        Cluster group node is clicked there — requested alongside the
        Cell-list
        move (2026-08-01: "раз уж у нас есть список Cluster то при выборе
        кластера надо сразу автоматически заполнять поле кластер").

        Only fires on a genuinely BLANK field (2026-08-15, plan
        cluster_field_autofill_not_hard_overwrite): once the field holds a
        value the user is responsible for — typed/picked by hand, or loaded
        from an already-saved entry — a stray tree click must not silently
        swap the placement's identity out from under an in-progress edit
        (this field doubles as ClonePlacement.name, the upsert identity key
        AND the Cluster tag written onto the board — clobbering it here
        reproduces the exact "duplicate entry" trap a real rename needs the
        Config tree's own Rename action for, see rename_entry() in
        rename.py)."""
        if self._cluster_identity_dirty:
            return
        self.cluster_edit.setCurrentText(name)
        # The auto-fill firing is itself a user-intent commit — a second click
        # on a DIFFERENT Cluster must not immediately overwrite it.
        self._cluster_identity_dirty = True

    def _mark_cluster_identity_dirty(self) -> None:
        """Marks the Cell-mode Cluster/Name field "owned" by the user — once
        it holds a typed/picked value, set_cluster_name()'s tree-click
        auto-fill must not clobber it (2026-08-15, plan
        cluster_field_autofill_not_hard_overwrite)."""
        self._cluster_identity_dirty = True

    def _maybe_autofill_placer_name(self) -> None:
        """Fills the Placer name (save/--only identity) from Cluster while
        CREATING a brand new placement (2026-08-15, plan
        clone_placement_placer_name_split) — once the user owns Placer name
        (typed it, or loaded an already-saved entry), editing Cluster must
        not drag it along."""
        if self._placer_name_dirty:
            return
        self.placer_name_edit.setText(self.cluster_edit.currentText().strip())

    def _mark_placer_name_dirty(self) -> None:
        """Marks the Placer name field "owned" by the user (typed directly or
        loaded) — Cluster auto-fill no longer applies (2026-08-15, plan
        clone_placement_placer_name_split)."""
        self._placer_name_dirty = True

    def set_board_selection(self, raw_items: List[Any],
                            selected_footprints: List[Any]) -> None:
        """Called every selection-watch tick (DockHub.set_board_selection,
        2026-08-31, plan placer_source_tab_gaps P.1) — the live board
        selection drives the Cell-mode Cluster auto-fill, mirroring the
        retired Extract dock's set_board_selection/_autofill_from_cluster.
        PlacerDock only reads the selected footprints' Cluster field here
        (raw_items is kept for signature symmetry with that hook)."""
        self._raw_items = raw_items
        self._selected_footprints = selected_footprints
        self._autofill_cluster_from_selection()

    def _autofill_cluster_from_selection(self) -> None:
        """Cell-mode Cluster auto-fill from the CURRENT board selection
        (2026-08-31, plan placer_source_tab_gaps P.1; Денис: selected a whole
        Cluster's components on the board, expected its name to fill itself
        into the Source tab's Cluster field, like the retired Extract dock
        does for Cell names). If the selected footprints all carry ONE
        non-empty Cluster, fill it into cluster_edit — but ONLY while the
        field is blank and not user-owned (_cluster_identity_dirty), the same
        "never overwrite what is already there" rule as set_cluster_name and
        the retired Extract dock's own _autofill_from_cluster. An empty/mixed
        selection silently does nothing.

        hand (the field is marked user-owned so a later tick/selection can't
        clobber it — same reasoning as set_cluster_name)."""
        if self.is_coordinate or self.is_entity:
            return
        # getattr guard (same defensive style as the retired Extract dock's
        # own getattr(s, "sheet", None) on the same selection-watch input):
        # the
        # DockHub wiring test passes plain strings through set_board_selection,
        # and a stray non-Selected entry must simply be skipped, not crash.
        clusters = frozenset(getattr(s, "cluster", None) for s in self._selected_footprints
                             if getattr(s, "cluster", None))
        signature = (clusters, self._cluster_identity_dirty,
                     self.cluster_edit.currentText().strip())
        if signature == self._last_selection_cluster_signature:
            return
        self._last_selection_cluster_signature = signature
        if len(clusters) != 1:
            return
        if self._cluster_identity_dirty or self.cluster_edit.currentText().strip():
            return
        cluster = next(iter(clusters))
        self.cluster_edit.setCurrentText(cluster)
        # A board-selection fill is as much a "the user owns this field now"
        # commit as a typed/picked one (set_cluster_name does the same) — a
        # later tick or tree-click must not silently swap the identity out
        # from under the in-progress edit.
        self._cluster_identity_dirty = True

    def refresh_known_roles(self, snapshot) -> None:
        """Populates the Cluster/anchor Role/anchor Cluster combos with
        distinct values already used on the board — "если выбираем по
        роли то надо и поле anchor cluster да и лист" (2026-08-01);
        Cluster itself made a searchable dropdown too, same reasoning,
        2026-08-04 ("а мы можем сделать кластер в пласере выпадающим?").
        Called by MainWindow at the same ~2s full-poll cadence as the rest
        of the docks (not the 400ms selection-watch tick — the known-value
        list barely changes tick to tick). `snapshot` is the cached
        BoardConnection.snapshot the caller already built — this used to
        call board.select() itself, a second full snapshot build per
        refresh (1.2 in techdocs/handoff/).

        G4.4 (2026-08-12): compare the computed known-value SETS against the
        previous tick and skip the whole repopulation loop when nothing
        changed — same set-compare guard as extract.py's _rebuild_net_aliases
        (carried over from the merged-in coordinate dock, which had its own
        copy of the same guard on its per-row combos)."""
        roles = {s.role for s in snapshot if s.role}
        clusters = {s.cluster for s in snapshot if s.cluster}
        if roles == self._known_roles_cache and clusters == self._known_clusters_cache:
            return
        self._known_roles_cache = roles
        self._known_clusters_cache = clusters
        roles = sorted(roles)
        clusters = sorted(clusters)
        set_combo_items(self.cluster_edit, clusters)
        self.origin_widget.set_known_roles(roles, clusters)
        # Coordinate mode (2026-08-12, Group 1): the form's Cluster/Role and
        # anchor Role/Cluster combos share the same live-board values.
        self.coordinate_form.set_known_roles(roles, clusters)

    def _rebuild_cell_role_choices(self) -> None:
        """Populate the Cell-anchor Role picker from the PICKED CELL's own
        components: roles, not every role on the live board (found live
        2026-08-06, Denis: "зачем в выпадашках ВСЕ доступные на плате
        роли? Нас же интересуют только роли относящиеся к Pi_Filter_p5v?").
        The Nets/Refs tables that used to share these key choices are gone
        (2026-09-05 — manual by-nets override editing removed)."""
        cell_data = self._cell_data(self._selected_cell)
        roles = sorted({c.get("role") for c in cell_data.get("components", []) if c.get("role")})
        # The Cell-anchor Role picker is sourced the SAME way as CellDock's
        # anchor_role_combo: THIS cell's own components, never the live board.
        set_combo_items(self.cell_anchor_role_combo, roles)

    # ── Message helper (same shape as the retired Extract dock's) ──────────

    def _show_message(self, text: str, style: str = "") -> None:
        """Mirror into the Log dock at the level matching `style` — the docks
        no longer have an inline message_label (2026-08-13), the Log dock is
        the single destination."""
        show_message(text, style, logger)

    def _carry_override_fields(self, entry: Dict[str, Any]) -> None:
        """Re-attach the LOADED record's stored by-nets override fields
        (params/nets/net_overrides/refs) to an entry being built for save —
        the Placer no longer edits them (tabs removed, 2026-09-05; nets
        auto-resolve), so they must survive any unrelated edit untouched."""
        for key in ("params", "nets", "net_overrides", "refs"):
            value = self._loaded_override_fields.get(key)
            if value:
                entry[key] = value

    # ── Building the clone_placement dict (shared by Redraw and Save) ──────

    def _parse_float(self, edit: QLineEdit, label: str, default: Optional[float] = None) -> Optional[float]:
        text = edit.text().strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            self._show_message(_("{label}: {text!r} is not a number.").format(label=label, text=text), _ERROR_STYLE)
            return None

    def _build_entry_dict(self) -> Optional[Dict[str, Any]]:
        # Source-mode branch (2026-08-12, Group 1): Single component =
        # CoordinatePlacement form, Cell = the clone path below. Entity
        # (2026-08-30, phase 5.2) builds a NO-position Entity record.
        if self.is_entity:
            return self._build_entity_dict()
        if self.is_coordinate:
            entry, err = self.coordinate_form.build()
            if err:
                self._show_message(err, _ERROR_STYLE)
                return None
            return entry
        # cell: is MANDATORY on ClonePlacement since 2026-08-12 (Group 0
        # consolidation — the role:/cluster: single-component modes migrated
        # 1:1 to coordinate_placements' anchor-relative mode), so this is the
        # plain cell path: name (cluster_edit) + selected cell.
        name = self.cluster_edit.currentText().strip()
        if not name:
            self._show_message(_("Cluster name is required."), _ERROR_STYLE)
            return None
        if not self._selected_cell:
            self._show_message(_("Pick a Cell first."), _ERROR_STYLE)
            return None
        entry: Dict[str, Any] = {"cluster": name, "cell": self._selected_cell}
        # Name (save/--only identity, split 2026-08-15 from the Cluster tag,
        # which moved into its own `cluster:` field 2026-08-24) — only written
        # when it actually differs from Cluster (same "don't write a redundant
        # field" principle as sheet below); when absent the loader/upsert fall
        # back to `cluster`, so existing configs stay untouched.
        identity = self.placer_name_edit.text().strip()
        if identity and identity != name:
            entry["name"] = identity
        # Own-identity sheet (2026-08-15, Cell mode) — only written when
        # non-empty, same pattern as name above.
        sheet = self.sheet_edit.currentText().strip()
        if sheet:
            entry["sheet"] = sheet
        # Optional free-form note — only written when non-empty.
        comment = self.placer_comment_edit.text().strip()
        if comment:
            entry["comment"] = comment

        origin_fields, err = self.origin_widget.build()
        if err:
            self._show_message(err, _ERROR_STYLE)
            return None
        mode = origin_fields["mode"]
        if mode == "xy":
            if "radius" in origin_fields:
                # Polar mode (optional alternative to xy — see ClonePlacement's
                # docstring): the origin is radius at angle, both required.
                entry["radius_mm"] = origin_fields["radius"]
                entry["angle_deg"] = origin_fields["angle"]
            else:
                entry["xy"] = [origin_fields["x"], origin_fields["y"]]
        else:
            if "radius" in origin_fields:
                # Polar OFFSET in Anchor/Point mode (2026-08-12, Group 2 fix) —
                # written as radius_mm/angle_deg, not xy (xy would be the
                # Cartesian shift).
                entry["radius_mm"] = origin_fields["radius"]
                entry["angle_deg"] = origin_fields["angle"]
            else:
                # ClonePlacement has no separate shift field — Anchor/Point mode
                # reuse xy: itself to carry the shift (see _anchor_origin.py's
                # module docstring on why the shared widget doesn't know this).
                entry["xy"] = [origin_fields["shift_x"], origin_fields["shift_y"]]
            if mode == "anchor":
                if "ref" in origin_fields:
                    entry["anchor_ref"] = origin_fields["ref"]
                else:
                    entry["anchor_role"] = origin_fields["role"]
                if "sheet" in origin_fields:
                    entry["anchor_sheet"] = origin_fields["sheet"]
                if "pad" in origin_fields:
                    entry["anchor_pad"] = origin_fields["pad"]
                if "cluster" in origin_fields:
                    entry["anchor_cluster"] = origin_fields["cluster"]
            else:  # Point
                entry["anchor_point"] = origin_fields["point"]

        rotation = self._parse_float(self.rotation_edit, _("Rotation"), default=0.0)
        if rotation is None:
            return None
        if rotation:
            entry["rotation_deg"] = rotation

        layer_idx = self.layer_combo.currentIndex()
        if layer_idx == 1:
            entry["layer"] = "F.Cu"
        elif layer_idx == 2:
            entry["layer"] = "B.Cu"

        if self.mirror_checkbox.isChecked():
            entry["mirror"] = True

        # Manual by-nets override fields are no longer edited in the Placer
        # (tabs removed, 2026-09-05 — nets auto-resolve) — carry forward
        # whatever the LOADED record already stored, so an unrelated edit here
        # never silently wipes an existing nets:/params:/net_overrides:/refs:.
        self._carry_override_fields(entry)

        return entry

    def _build_entity_dict(self) -> Optional[Dict[str, Any]]:
        """Entity mode's Save payload (2026-08-30, Entity/Placement split,
        phase 5.2): an Entity record — name + cell + electrical/identity
        fields, and NO position at all (position lives only in the trees:
        node; config/entries.py makes every positional key fatal on an
        Entity by design). The cell comes from the picked record, not the
        (hidden-in-this-mode) cell picker."""
        name = self.entity_combo.currentText().strip()
        if not name:
            self._show_message(_("Entity name is required."), _ERROR_STYLE)
            return None
        if not self._selected_cell:
            self._show_message(
                _("Pick an Entity first (its Cell comes from the record)."),
                _ERROR_STYLE)
            return None
        entry: Dict[str, Any] = {"name": name, "cell": self._selected_cell}

        cluster = self.cluster_edit.currentText().strip()
        if cluster:
            entry["cluster"] = cluster
        sheet = self.sheet_edit.currentText().strip()
        if sheet:
            entry["sheet"] = sheet
        comment = self.placer_comment_edit.text().strip()
        if comment:
            entry["comment"] = comment

        layer_idx = self.layer_combo.currentIndex()
        if layer_idx == 1:
            entry["layer"] = "F.Cu"
        elif layer_idx == 2:
            entry["layer"] = "B.Cu"
        if self.mirror_checkbox.isChecked():
            entry["mirror"] = True

        # Entity nets/params are edited in the Tools dock, not here — carry
        # forward the loaded record's stored override fields so a Placer save
        # never wipes them (same reasoning as the clone path above).
        self._carry_override_fields(entry)

        return entry

    # ── Redraw ────────────────────────────────────────────────────────────

    def _on_redraw(self) -> None:
        """Redraw button handler — form collection (validation + widget
        reads) runs on the UI thread; the ApplyPipeline run + cluster
        tagging run on a worker thread (see gui/worker.py). The pipeline
        opens its OWN kipy socket, but we still gate the shared connection
        (long_op_active) for the duration so the GUI's polling timers don't
        fire a second concurrent socket request — the same "one socket in
        flight" the old blocked-UI behaviour guaranteed implicitly."""
        self._show_message("")
        payload = self._collect_redraw_inputs()
        if payload is None:
            return
        self._start_redraw_op(payload)

    def _load_target_config(self, silent: bool = False) -> Optional[tuple]:
        """load_config() the dock's target file, or an empty
        (Config, RuntimeContext) when the file doesn't exist yet — shared by
        the clone and coordinate redraw paths (2026-08-13 cleanup: the same
        try/except used to be duplicated in both). Shows the error and
        returns None on a broken file. `silent=True` (2026-08-16, Auto-fill
        Sheet narrowing) suppresses that error message — the auto-fill path
        treats a broken placer file as an empty sheet_names best-effort, not
        a user-facing error (the quiet auto-trigger must not spam on every
        Cell/Cluster pick).

        sheet_names fallback (2026-08-15,
        plan_2026_08_15_redraw_sheet_names_from_root.md): schematic_dir:
        conventionally lives only on the project's ROOT config, which
        INCLUDES leaf files like components.yaml — resolve_includes() only
        merges DOWNWARD from the given starting path, so loading straight
        from a leaf Placer file (as this always has) can silently produce
        an empty ctx.sheet_names even though the project's root resolves it
        fine. If the leaf's own resolution came up empty and a different
        root is known, fall back to the root's sheet_names — this only
        fires when the leaf genuinely has none of its own, so a leaf that
        legitimately declares its own schematic_dir: keeps behaving exactly
        as before."""
        try:
            if self._placer_path.exists():
                cfg, ctx = load_config(str(self._placer_path))
            else:
                cfg, ctx = Config(), RuntimeContext()
            # len(), not "not ctx.sheet_names": LazySheetNameMap is always
            # truthy by design (2026-08-25) so the ubiquitous "sheet_names or
            # {}" fallbacks elsewhere never force a parse — but that means
            # "not ctx.sheet_names" can no longer detect a genuinely empty
            # map here. This IS the site that needs the real answer.
            if (len(ctx.sheet_names) == 0 and self._root_path is not None
                    and self._root_path != self._placer_path):
                try:
                    root_cfg, root_ctx = load_config(str(self._root_path))
                    # graph cache is shared; copy ctx before writing to it
                    ctx = replace(ctx, sheet_names=root_ctx.sheet_names)
                except (ValidationError, OSError):
                    pass  # keep the leaf's own (empty) sheet_names — don't fail
                          # Redraw over a fallback that didn't pan out
            return cfg, ctx
        except (ValidationError, OSError) as e:
            if not silent:
                self._show_message(_("Failed to load Placer file: {error}").format(error=e), _ERROR_STYLE)
            return None

    def _collect_redraw_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: read every widget + run every validation that can
        reject the request up front (including loading + mutating the Placer
        config). Returns a plain-data payload for the worker, or None after
        showing the error."""
        entry = self._build_entry_dict()
        if entry is None:
            return None
        if self._placer_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return None
        # Coordinate mode (2026-08-12, Group 1): the entry has no cell:, place
        # it through its own coordinate-specific collection path.
        if self.is_coordinate:
            return self._collect_coordinate_place_inputs(entry)
        # Role mode needs no cells.yaml at all — ClonePositionCalculator
        # synthesises its one-component Cell on the fly (see
        # _on_cell_mode_changed's docstring), cells: is never read.
        if "cell" in entry and self._cells_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return None

        try:
            clone_placement = load_clone_placement(entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None

        loaded = self._load_target_config()
        if loaded is None:
            return None
        cfg, ctx = loaded

        if "cell" in entry and entry["cell"] not in cfg.cells:
            self._show_message(
                _("Cell {cell!r} isn't reachable from the Placer file's include: — "
                  "extract/save it and make sure include: is wired (see Extract).")
                .format(cell=entry["cell"]), _ERROR_STYLE)
            return None

        # Replace-by-name: previewing an already-saved placement's edits
        # must not create a second copy alongside the saved one — match on the
        # effective save/--only identity (placer_name if set, else Cluster),
        # not raw .name (2026-08-15, plan clone_placement_placer_name_split).
        new_identity = clone_placement_effective_name(clone_placement)
        cfg = replace(cfg)  # graph cache is shared; don't mutate the cached Config
        cfg.clone_placements = [
            c for c in cfg.clone_placements
            if clone_placement_effective_name(c) != new_identity
        ]
        cfg.clone_placements.append(clone_placement)

        return {
            "placer_path": self._placer_path,
            "cfg": cfg,
            "ctx": ctx,
            # Cluster tag written onto the board by _tag_cluster — raw .cluster,
            # NOT the name identity (the two are split since 2026-08-15).
            "name": clone_placement.cluster,
            # save/--only identity for ApplyPipeline's own only= filter.
            "only_name": new_identity,
        }

    def _collect_coordinate_place_inputs(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Coordinate mode's Redraw/Place collection — the same shape as the
        clone path but for ONE CoordinatePlacement: validate through
        load_coordinate_placement(), load the real target file, replace-by-
        name this entry, and let ApplyPipeline place only=[its effective
        name]. No cluster tagging — the component is already identified by
        its own Cluster/Role, this type only moves it."""
        try:
            cp = load_coordinate_placement(entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None
        name = coordinate_placement_effective_name(cp)
        if cp.retired or cp.skip:
            # drop_inactive_items would drop the entry before --only sees it,
            # so apply_only_filter can't find the name — block the whole
            # place instead of letting it fail deep in the pipeline.
            self._show_message(
                _("{name!r} is retired/skipped — uncheck Retired/Skip to place it.")
                .format(name=name), _ERROR_STYLE)
            return None

        loaded = self._load_target_config()
        if loaded is None:
            return None
        cfg, ctx = loaded

        # Replace-by-name: previewing an already-saved entry's edits must
        # not create a second copy alongside the saved one.
        cfg = replace(cfg)  # graph cache is shared; don't mutate the cached Config
        cfg.coordinate_placements = [
            c for c in cfg.coordinate_placements
            if coordinate_placement_effective_name(c) != name
        ]
        cfg.coordinate_placements.append(cp)

        return {"placer_path": self._placer_path, "cfg": cfg, "ctx": ctx,
                "name": name, "coordinate": True}

    def _run_redraw(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: ApplyPipeline run + cluster tagging — never touches
        a widget. Returns {"name": ..., "tagged": ...} on success,
        {"error": str} for placement failure, or {"warn": str} when the
        placement itself succeeded but tagging didn't."""
        # only= must match the save/--only identity: the clone path carries it
        # as payload["only_name"] (Cluster tag lives separately in
        # payload["name"]); the coordinate path already puts its effective name
        # directly in payload["name"], so fall back to that.
        pipeline = ApplyPipeline(config_path=str(payload["placer_path"]),
                                 preloaded_cfg=payload["cfg"], preloaded_ctx=payload["ctx"],
                                 only=[payload.get("only_name", payload["name"])], dry_run=False)
        try:
            pipeline.run()
        except (PlacerError, ValidationError, ApiError) as e:
            return {"error": _("Placement failed: {error}").format(error=e)}
        except Exception as e:
            logger.exception("Placer redraw failed")
            return {"error": _("Placement failed: {error}").format(error=e)}

        if payload.get("coordinate"):
            # Coordinate mode: nothing to tag — the moved component is
            # identified by its own Cluster/Role fields, already set.
            return {"name": payload["name"], "tagged": None}

        try:
            tagged = self._tag_cluster(pipeline, payload["cfg"], payload["ctx"], payload["name"])
        except Exception as e:
            logger.exception("Cluster tagging after placement failed")
            return {"warn": _("Placed, but tagging Cluster failed: {error}").format(error=e)}

        return {"name": payload["name"], "tagged": tagged}

    def _finish_redraw(self, result: Dict[str, Any]) -> None:
        """UI thread: reflect the worker's result into the message label."""
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        if result.get("warn"):
            self._show_message(result["warn"], _WARN_STYLE)
            return
        if result.get("tagged") is None:
            # Coordinate mode — no cluster tagging happened.
            self._show_message(_("Placed {name!r}.").format(name=result["name"]), _SUCCESS_STYLE)
            return
        self._show_message(
            _("Placed {name!r} ({count} component(s) tagged Cluster={name!r}).")
            .format(name=result["name"], count=result["tagged"]), _SUCCESS_STYLE)

    def _action_buttons(self) -> tuple:
        """Every action button in the bottom row — disabled while any long op
        (Redraw) or the synchronous Save runs, so no two board-touching
        actions can overlap (same "one socket in flight" discipline as
        connection.long_op_active)."""
        return (self.redraw_button, self.select_button)

    def _on_select_on_board(self) -> None:
        """Select-on-board button — resolve the CURRENT form's placement
        (ClonePlacement or CoordinatePlacement) to its live board items via
        board_items_resolver.resolve_clone_board_items() and highlight them in
        pcbnew through adapter.select_items(). Read-only: never moves/tags/
        writes. Short synchronous board reads only (same discipline as
        refresh_known_roles), wrapped in busy() so no two board-touching
        actions overlap."""
        with busy(self._action_buttons()):
            board = self._main_window.connection.board
            if board is None or getattr(board, "adapter", None) is None:
                self._show_message(_("Not connected."), _ERROR_STYLE)
                return
            entry = self._build_entry_dict()
            if entry is None:
                return
            if self._placer_path is None:
                self._show_message(_("Set the project root first."), _ERROR_STYLE)
                return
            try:
                if self.is_coordinate:
                    placement = load_coordinate_placement(entry)
                else:
                    placement = load_clone_placement(entry)
            except ValidationError as e:
                self._show_message(str(e), _ERROR_STYLE)
                return

            loaded = self._load_target_config()
            if loaded is None:
                return
            cfg, ctx = loaded

            if not self.is_coordinate and placement.cell not in cfg.cells:
                self._show_message(
                    _("Cell {cell!r} isn't reachable from the Placer file's include: — "
                      "extract/save it and make sure include: is wired (see Extract).")
                    .format(cell=placement.cell), _ERROR_STYLE)
                return

            from kicadstamp.placement.services.board_items_resolver import resolve_clone_board_items
            from kicadstamp.registry import (registry_path_for_config,
                                             track_registry_path_for_config)

            registry_path = ctx.registry_path or registry_path_for_config(str(self._placer_path))
            track_registry_path = (ctx.track_registry_path
                                   or track_registry_path_for_config(str(self._placer_path)))
            try:
                items = resolve_clone_board_items(
                    board.adapter, cfg, ctx, placement,
                    registry_path=registry_path, track_registry_path=track_registry_path)
            except ValidationError as e:
                self._show_message(str(e), _ERROR_STYLE)
                return

            if not items:
                self._show_message(
                    _("nothing found on the board for this placement — has it been placed yet?"),
                    _WARN_STYLE)
                return

            board.adapter.select_items(items)
            name = (coordinate_placement_effective_name(placement)
                    if self.is_coordinate else clone_placement_effective_name(placement))
            self._show_message(_("Selected {count} item(s) on the board for {name!r}.")
                               .format(count=len(items), name=name), _SUCCESS_STYLE)

    @staticmethod
    def _anchor_origin_filled(aw: AnchorOriginWidget) -> Tuple[bool, bool]:
        """(anchor_set_filled, point_set_filled) for an AnchorOriginWidget —
        the "Read current position" auto-switch (2026-08-31, plan
        placer_source_tab_gaps P.2) needs this: Денис filled the Origin tab's
        anchor/point identity fields but left the mode combo on the default
        Absolute (xy), and the read silently wrote ABSOLUTE coordinates while
        ignoring the filled anchor. "Filled" mirrors build()'s own acceptance:
        anchor = Ref OR Role non-blank (they are mutually exclusive in
        build()), point = Point non-blank."""
        ref = aw.anchor_ref_edit.text().strip() if aw.anchor_ref_edit is not None else ""
        role = aw.anchor_role_edit.currentText().strip() if aw.anchor_role_edit is not None else ""
        point = aw.point_edit.currentText().strip() if aw.point_edit is not None else ""
        return (bool(ref or role), bool(point))

    def _on_coordinate_read_position(self) -> None:
        """Coordinate form's "Read current position" — resolve the CURRENT
        form's (Role, Cluster) to its live component and fill the position/
        rotation fields in the form's current mode (design
        2026_08_29_config_tree_read_live_position.md §3.1). Reads the form's
        identity fields directly (cluster/role/sheet) — NOT form.build(), which
        would require the position fields to already be filled. Short
        synchronous board reads wrapped in busy(), same as Select-on-board.
        Never a silent partial write: any failure is a warning, fields
        untouched."""
        with busy(self._action_buttons()):
            board = self._main_window.connection.board
            if board is None or getattr(board, "adapter", None) is None:
                QMessageBox.warning(
                    self, _("Read current position"),
                    _("No live board connection — connect KiCad first."))
                return
            form = self.coordinate_form
            cluster = form.cluster_combo.currentText().strip()
            role = form.role_combo.currentText().strip()
            if not cluster or not role:
                QMessageBox.warning(
                    self, _("Read current position"),
                    _("Cluster and Role are required to read the component's position."))
                return
            if self._placer_path is None:
                QMessageBox.warning(self, _("Read current position"),
                                    _("Set the project root first."))
                return
            loaded = self._load_target_config(silent=True)
            if loaded is None:
                return
            cfg, ctx = loaded
            sheet_names = ctx.sheet_names if ctx is not None else {}
            label = f"{cluster}/{role}"
            # 2026-08-31 (plan placer_source_tab_gaps P.2): the SAME class of
            # bug as the clone case — anchor fields filled but the mode combo
            # still on an ABSOLUTE mode (0 Cartesian / 1 Polar) silently
            # ignored them and wrote absolute coordinates. Auto-switch to the
            # anchor-relative mode (index 2) when unambiguous.
            anchor_filled, point_filled = self._anchor_origin_filled(form._anchor_widget)
            if form.mode_combo.currentIndex() != 2 and (anchor_filled or point_filled) \
                    and not (anchor_filled and point_filled):
                form.mode_combo.setCurrentIndex(2)
            try:
                sheet = form.sheet_edit.currentText().strip() or None
                read = read_coordinate_live(
                    board.adapter, cluster, role, sheet, sheet_names, label)
                anchor_position: Optional[Vector2] = None
                if form.mode_combo.currentIndex() == 2:  # anchor-relative
                    anchor_fields, err = form._anchor_widget.build()
                    if err:
                        QMessageBox.warning(self, _("Read current position"), err)
                        return
                    anchor_read = read_anchor_live(
                        board.adapter, anchor_fields, cfg.points, sheet_names, label)
                    anchor_position = anchor_read.position
                form.write_live_position(read, anchor_position=anchor_position)
            except ValidationError as e:
                QMessageBox.warning(self, _("Read current position"), str(e))
                return
            self._show_message(
                _("Read current position: ({x:.3f}, {y:.3f}) mm").format(
                    x=read.position.x / MM, y=read.position.y / MM),
                _SUCCESS_STYLE)

    def _build_clone_for_read(self) -> Optional[ClonePlacement]:
        """A ClonePlacement built from the CURRENT form's identity + nets +
        mirror, WITHOUT the origin/rotation/layer validation (those fields are
        empty before the first "Read current position", and read_clone_origin_
        live only needs cell/nets/cluster/mirror to resolve the cell's
        components). xy is a dummy (0,0) — never read by the live read."""
        cluster = self.cluster_edit.currentText().strip()
        if not cluster or not self._selected_cell:
            return None
        sheet = self.sheet_edit.currentText().strip() or None
        # Nets/params/overrides are no longer edited here (2026-09-05) — pass
        # the loaded record's stored override fields through so the live read
        # keeps resolving the cell's roles exactly as before.
        ov = self._loaded_override_fields
        return ClonePlacement(
            cluster=cluster, cell=self._selected_cell, xy=(0.0, 0.0),
            sheet=sheet, mirror=self.mirror_checkbox.isChecked(),
            params=ov.get("params") or {}, nets=ov.get("nets") or {},
            net_overrides=ov.get("net_overrides") or {},
            refs=ov.get("refs") or {})

    def _write_clone_live_origin(self, read: LiveRead,
                                 anchor_position: Optional[Vector2] = None) -> None:
        """Write a clone's live cell-origin read into the Origin tab, in its
        CURRENT mode: "xy" -> the absolute origin (Cartesian x/y or polar
        radius/angle); "anchor"/"point" -> the origin as the SHIFT from the
        resolved anchor (anchor_position, computed by the dock). Rotation is
        written whenever the read has one. 3-decimal rounding, same as
        TreesDock."""
        ow = self.origin_widget
        x_mm = read.position.x / MM
        y_mm = read.position.y / MM
        is_polar = ow._polar_combo is not None and ow._polar_combo.currentIndex() == 1
        mode = ow.mode
        if mode == "xy":
            if is_polar:
                ow.radius_edit.setText(f"{math.hypot(x_mm, y_mm):.3f}")
                ow.angle_edit.setText(f"{math.degrees(math.atan2(y_mm, x_mm)):.3f}")
            else:
                ow.x_edit.setText(f"{x_mm:.3f}")
                ow.y_edit.setText(f"{y_mm:.3f}")
        else:  # anchor / point — the origin as a shift from the anchor
            if anchor_position is None:
                return
            sx = x_mm - anchor_position.x / MM
            sy = y_mm - anchor_position.y / MM
            if is_polar:
                ow.radius_edit.setText(f"{math.hypot(sx, sy):.3f}")
                ow.angle_edit.setText(f"{math.degrees(math.atan2(sy, sx)):.3f}")
            else:
                ow.shift_x_edit.setText(f"{sx:.3f}")
                ow.shift_y_edit.setText(f"{sy:.3f}")
        if read.rotation_deg is not None:
            self.rotation_edit.setText(f"{read.rotation_deg:.3f}")

    def _on_clone_read_position(self) -> None:
        """Cell mode's "Read current position" — the "ячейка" case (design
        2026_08_29_config_tree_read_live_position.md §1.2/§3.2): re-derive the
        cell's CURRENT origin from a placed component on the live board and
        fill the Origin tab (absolute xy, or the shift from the resolved anchor
        in anchor/point mode) + rotation. Reads the CURRENT form's
        identity/nets without requiring the origin fields to be filled first.
        Failures are warnings, fields untouched — never a silent partial write."""
        with busy(self._action_buttons()):
            board = self._main_window.connection.board
            if board is None or getattr(board, "adapter", None) is None:
                QMessageBox.warning(
                    self, _("Read current position"),
                    _("No live board connection — connect KiCad first."))
                return
            if self._placer_path is None:
                QMessageBox.warning(self, _("Read current position"),
                                    _("Set the project root first."))
                return
            clone = self._build_clone_for_read()
            if clone is None:
                QMessageBox.warning(
                    self, _("Read current position"),
                    _("Cluster name and a Cell are required to read the cell's position."))
                return
            loaded = self._load_target_config(silent=True)
            if loaded is None:
                return
            cfg, ctx = loaded
            sheet_names = ctx.sheet_names if ctx is not None else {}
            # 2026-08-31 (plan placer_source_tab_gaps P.2): Денис filled the
            # Origin tab's anchor/point identity fields but left the mode combo
            # on the default Absolute (xy) — the read used to silently write
            # ABSOLUTE coordinates and ignore the filled anchor (numbers like
            # (64.074, -47.592), not the small offset expected next to the
            # FPGA anchor). Auto-switch the mode to the filled anchor/point set
            # (silent, no dialog — chosen UX 2026-08-31); only when
            # unambiguous: both filled stays as-is, build() would reject the
            # mixed state anyway.
            if self.origin_widget.mode == "xy":
                anchor_filled, point_filled = self._anchor_origin_filled(self.origin_widget)
                if anchor_filled and not point_filled:
                    self.origin_widget.origin_mode_combo.setCurrentIndex(
                        self.origin_widget._modes.index("anchor"))
                elif point_filled and not anchor_filled:
                    self.origin_widget.origin_mode_combo.setCurrentIndex(
                        self.origin_widget._modes.index("point"))
            try:
                read = read_clone_origin_live(board.adapter, cfg, clone, sheet_names)
                anchor_position: Optional[Vector2] = None
                if self.origin_widget.mode in ("anchor", "point"):
                    fields, err = self.origin_widget.build()
                    if err:
                        QMessageBox.warning(self, _("Read current position"), err)
                        return
                    anchor_read = read_anchor_live(
                        board.adapter, fields, cfg.points, sheet_names,
                        clone_placement_effective_name(clone))
                    anchor_position = anchor_read.position
                self._write_clone_live_origin(read, anchor_position=anchor_position)
            except ValidationError as e:
                QMessageBox.warning(self, _("Read current position"), str(e))
                return
            self._show_message(
                _("Read current position: ({x:.3f}, {y:.3f}) mm").format(
                    x=read.position.x / MM, y=read.position.y / MM),
                _SUCCESS_STYLE)

    # ── Cell internal anchor (design 2026-09-04_cell_internal_anchor) ──────

    @staticmethod
    def _cell_entry_mount_offset(entry: dict) -> tuple[float, float]:
        """The cell entry's CURRENT mount A in its stored frame (anchor_xy, else
        anchor_role's centre offset, else (0,0)) — the dict twin of
        geometry/cell_anchor.cell_mount_offset (design_2026_09_05 v2)."""
        xy = entry.get("anchor_xy")
        if xy is not None:
            return (float(xy[0]), float(xy[1]))
        role = entry.get("anchor_role")
        if role:
            for c in entry.get("components", []):
                if c.get("role") == role:
                    return (float(c.get("offset_along_mm", 0.0)),
                            float(c.get("offset_across_mm", 0.0)))
        return (0.0, 0.0)

    def _on_set_cell_anchor(self) -> None:
        """"Set as anchor" — RECORD the loaded CELL's MOUNT POINT on its OWN
        file WITHOUT rewriting any stored offset (design_2026_09_05 v2: stored
        offsets always stay in the bbox frame; geometry subtracts the anchor A
        at placement via cell_mount_offset). Mode picked by what is filled:
          - Role (Pad empty) — mount on that component's centre: A = its stored
            offset; writes anchor_xy + anchor_role. Offline.
          - Role + Pad — mount on that pad: A = the pad's stored point (needs
            the LIVE test instance: read_cell_anchor_offset_live); writes
            anchor_xy + anchor_role + anchor_pad.
          - only X/Y — mount on that bbox-frame point; writes anchor_xy.
          - nothing filled — CLEAR the anchor (back to the bbox default).
        Writes to the file that actually holds the cell (cells: is dict-keyed;
        find_dict_entry_file + merge_write — the same path CellDock's own edit
        uses), then saved is emitted so the Config tree refreshes. No mutation
        of offsets, no backup_file, no blocking warning (design §2.4)."""
        if self.is_coordinate or self.is_entity:
            self._show_message(
                _("Cell anchor applies to Cell (clone) mode only."), _ERROR_STYLE)
            return
        if not self._selected_cell:
            self._show_message(_("Pick a Cell first."), _ERROR_STYLE)
            return
        if self._root_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        mode = self.cell_anchor_mode_combo.currentIndex()
        role = self.cell_anchor_role_combo.currentText().strip()
        pad_text = self.cell_anchor_pad_edit.text().strip()
        if mode == 0:        # Role
            pad = None
        elif mode == 1:      # Role + Pad
            pad = pad_text or None
        elif mode == 2:      # Point
            role = ""
            pad = None
        else:                # Reset (bbox default)
            role = ""
            pad = None
        point_mode = mode == 2
        if mode in (0, 1) and not role:
            self._show_message(
                _("Set as anchor: pick a Role of this cell first."), _ERROR_STYLE)
            return
        if mode == 1 and pad is None:
            self._show_message(
                _("Set as anchor (Role + Pad): enter a pad, or switch the mode "
                  "to Role."), _ERROR_STYLE)
            return

        # The file that physically holds the cell (cells: is dict-keyed; the
        # entry may live in any file of the include: graph).
        target_file = find_dict_entry_file(
            self._root_path, "cells", self._selected_cell)
        if target_file is None:
            self._show_message(
                _("Set as anchor: cell {name!r} not found in the project config")
                .format(name=self._selected_cell), _ERROR_STYLE)
            return
        try:
            entry = (read_data(target_file).get("cells") or {}).get(self._selected_cell)
        except (ValidationError, OSError) as e:
            self._show_message(
                _("Failed to read {path}: {error}")
                .format(path=display_path(target_file), error=e), _ERROR_STYLE)
            return
        if not isinstance(entry, dict):
            self._show_message(
                _("Set as anchor: cell {name!r} not found in {path}")
                .format(name=self._selected_cell,
                        path=display_path(target_file)), _ERROR_STYLE)
            return
        # Only the anchor bookkeeping fields are ever written — never offsets.
        entry = dict(entry)

        desc = _("no anchor (bbox default)")
        if point_mode:
            x = self._parse_float(self.cell_anchor_x_edit, _("Anchor X"))
            y = self._parse_float(self.cell_anchor_y_edit, _("Anchor Y"))
            if x is None or y is None:
                self._show_message(
                    _("Set as anchor: point X and Y are required."), _ERROR_STYLE)
                return
            entry["anchor_xy"] = [x, y]
            entry.pop("anchor_role", None)
            entry.pop("anchor_pad", None)
            desc = _("point ({x:.3f}, {y:.3f}) mm").format(x=x, y=y)
        elif role:
            comp = next((c for c in entry.get("components", [])
                         if c.get("role") == role), None)
            if comp is None:
                self._show_message(
                    _("Set as anchor: role {role!r} is not a component of cell "
                      "{name!r}").format(role=role, name=self._selected_cell),
                    _ERROR_STYLE)
                return
            if pad is None:
                ax_mm = float(comp.get("offset_along_mm", 0.0))
                ay_mm = float(comp.get("offset_across_mm", 0.0))
                entry["anchor_xy"] = [ax_mm, ay_mm]
                entry["anchor_role"] = role
                entry.pop("anchor_pad", None)
                desc = _("role {role!r} at ({x:.3f}, {y:.3f}) mm").format(
                    role=role, x=ax_mm, y=ay_mm)
            else:
                # Role+Pad: the pad's stored point. read_cell_anchor_offset_live
                # returns the pad offset relative to the CURRENT mount; add the
                # current mount A to express it in the stored (bbox) frame.
                board = self._main_window.connection.board
                if board is None or getattr(board, "adapter", None) is None:
                    QMessageBox.warning(
                        self, _("Set as anchor"),
                        _("No live board connection — Role+Pad needs the pad's "
                          "real position. Leave Pad empty for a Role-only "
                          "anchor, or connect KiCad first."))
                    return
                clone = self._build_clone_for_read()
                if clone is None:
                    self._show_message(
                        _("Set as anchor: Cluster name and a Cell are required "
                          "to read the pad's position."), _ERROR_STYLE)
                    return
                loaded = self._load_target_config(silent=True)
                if loaded is None:
                    return
                cfg, ctx = loaded
                sheet_names = ctx.sheet_names if ctx is not None else {}
                try:
                    ax_mm, ay_mm = read_cell_anchor_offset_live(
                        board.adapter, cfg, clone, sheet_names, role, pad)
                except ValidationError as e:
                    QMessageBox.warning(self, _("Set as anchor"), str(e))
                    return
                a0, a1 = self._cell_entry_mount_offset(entry)
                entry["anchor_xy"] = [round(ax_mm + a0, 9), round(ay_mm + a1, 9)]
                entry["anchor_role"] = role
                entry["anchor_pad"] = pad
                desc = _("role {role!r} pad {pad!r} at ({x:.3f}, {y:.3f}) mm").format(
                    role=role, pad=pad,
                    x=entry["anchor_xy"][0], y=entry["anchor_xy"][1])
        else:
            # Clear — back to the bbox default (no anchor fields).
            entry.pop("anchor_xy", None)
            entry.pop("anchor_role", None)
            entry.pop("anchor_pad", None)

        try:
            merge_write(target_file, {"cells": {self._selected_cell: entry}},
                        section="cells")
        except (ValidationError, OSError) as e:
            self._show_message(
                _("Set as anchor failed: {error}").format(error=e), _ERROR_STYLE)
            return

        # The cells: entry changed under the graph cache — refresh the Config
        # tree (and anything else listening to saved); ConfigTree is the
        # display owner. Placed instances of this cell shift on the next
        # Redraw/Apply (the mount point moved — expected, design §2.4).
        self.saved.emit()
        self._show_message(
            _("Cell {name!r}: anchor set to {desc}. Stored offsets are never "
              "rewritten — placed instances shift on the next Redraw/Apply.")
            .format(name=self._selected_cell, desc=desc),
            _SUCCESS_STYLE)

    def _start_redraw_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._main_window.connection,
            self._action_buttons(),
            self._run_redraw, self._finish_redraw, self._on_redraw_failed, payload)

    def _on_redraw_failed(self, message: str) -> None:
        self._show_message(_("Placement failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_redraw(self) -> None:
        """Synchronous composition of collect + run + finish — the same
        behaviour the async button path would produce, kept for tests and
        any caller that must not return until the redraw is complete."""
        payload = self._collect_redraw_inputs()
        if payload is None:
            return
        result = self._run_redraw(payload)
        self._finish_redraw(result)

    # ── Redraw & Save ────────────────────────────────────────────────────

    def _on_redraw_and_save(self) -> None:
        """Redraw & Save button handler — same collect(UI thread)/run(worker
        thread) split as plain Redraw, reusing _collect_redraw_inputs and
        _run_redraw unchanged. The difference is the SUCCESS callback: it
        only runs after the worker has genuinely finished (queued signal back
        on the UI thread) and calls Save exactly once, then — never for a
        failed Redraw (no time.sleep/polling, no _on_redraw(); _on_save()
        race)."""
        self._show_message("")
        payload = self._collect_redraw_inputs()
        if payload is None:
            return
        self._start_redraw_and_save_op(payload)

    def _start_redraw_and_save_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._main_window.connection,
            self._action_buttons(),
            self._run_redraw, self._finish_redraw_and_save,
            self._on_redraw_and_save_failed, payload)

    def _finish_redraw_and_save(self, result: Dict[str, Any]) -> None:
        """UI thread, fired only after the worker's Redraw finished. Save runs
        ONLY on success — a failed Redraw (error dict) leaves a clear message
        that just Redraw was reached and Save was skipped."""
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            self._show_message(_("Save was not run — Redraw failed."), _ERROR_STYLE)
            return
        # Same message as plain Redraw (warn = placed but tagging failed, still
        # a successful placement worth saving), then the synchronous Save path.
        self._finish_redraw(result)
        self._do_save()

    def _on_redraw_and_save_failed(self, message: str) -> None:
        self._show_message(_("Placement failed: {error}").format(error=message), _ERROR_STYLE)
        self._show_message(_("Save was not run — Redraw failed."), _ERROR_STYLE)

    def _do_redraw_and_save(self) -> None:
        """Synchronous composition of collect + run + finish — the same "for
        tests" shape as _do_redraw (Redraw runs synchronously here, so Save
        is provably called AFTER it)."""
        payload = self._collect_redraw_inputs()
        if payload is None:
            return
        result = self._run_redraw(payload)
        self._finish_redraw_and_save(result)

    # ── Redraw dependents (§2.4) ────────────────────────────────────────

    def _on_redraw_dependents(self) -> None:
        """Cascade "Redraw dependents" for the CURRENT placement. The anchor
        graph is built from the project ROOT config (the whole include:
        graph), so dependents living in other included files are found; the
        current form only supplies the START record's identity (its saved
        anchor state is what the graph reads — see the anchor_graph module
        docstring)."""
        self._show_message("")
        if self._root_path is None:
            self._show_message(_("Pick a root file first."), _ERROR_STYLE)
            return
        entry = self._build_entry_dict()
        if entry is None:
            return
        try:
            if self.is_coordinate:
                cp = load_coordinate_placement(entry)
                name = coordinate_placement_effective_name(cp)
                start_key = f"coordinate:{name}"
            else:
                cp = load_clone_placement(entry)
                name = clone_placement_effective_name(cp)
                start_key = f"clone:{name}"
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return

        try:
            cfg, ctx = load_config(str(self._root_path))
        except (ValidationError, OSError) as e:
            self._show_message(
                _("Failed to load root config: {error}").format(error=e), _ERROR_STYLE)
            return
        try:
            records = cascade_records(cfg, start_key)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return
        # `records` always includes the start record itself; a cascade of ONE
        # (just the start, no dependents) is pointless — the plain Redraw
        # button already covers that single-record case.
        if len(records) <= 1:
            self._show_message(
                _("No records anchor on {name!r} — nothing to redraw.").format(name=name),
                _WARN_STYLE)
            return
        names = [r.name for r in records]
        self._start_cascade_op(str(self._root_path), cfg, ctx, names)

    def _start_cascade_op(self, config_path: str, cfg, ctx, names: list) -> None:
        payload = {"config_path": config_path, "cfg": cfg, "ctx": ctx, "names": names}
        logger.info(_("Redraw dependents: {count} record(s) in order: {order}")
                    .format(count=len(names), order=" -> ".join(names)))
        self._active_op = start_long_op(
            self._main_window.connection,
            self._action_buttons(),
            run_cascade_worker, self._finish_cascade, self._on_cascade_failed, payload)

    def _finish_cascade(self, results) -> None:
        ok = sum(1 for _name, good, _err in results if good)
        failed = len(results) - ok
        status = ", ".join(
            f"{name}={'ok' if good else 'FAILED'}" for name, good, _err in results)
        logger.info(_("Redraw dependents: {ok}/{total} ok — {status}")
                    .format(ok=ok, total=len(results), status=status))
        if failed:
            self._show_message(
                _("{failed}/{total} record(s) failed — see the log.")
                .format(failed=failed, total=len(results)), _ERROR_STYLE)
        else:
            self._show_message(
                _("Redrawn {total} record(s) (dependents).").format(total=len(results)),
                _SUCCESS_STYLE)

    def _on_cascade_failed(self, message: str) -> None:
        self._show_message(
            _("Redraw dependents failed: {error}").format(error=message), _ERROR_STYLE)

    def _tag_cluster(self, pipeline: ApplyPipeline, cfg: Config, ctx: RuntimeContext, name: str) -> int:
        """Recovers which refs this specific clone_placement touched (see
        module docstring) and tags them Cluster=name. Returns how many
        got tagged (0 if the item couldn't be found — shouldn't happen
        given `only=[name]` just ran successfully, but not fatal either
        way, since the board is already correctly placed regardless)."""
        my_item = next((it for it in pipeline.items
                         if it.kind == 'clone' and it.obj.cluster == name), None)
        if my_item is None:
            return 0

        planner = PlacementPlanner(pipeline.adapter, cfg, sheet_names=ctx.sheet_names if ctx else {})
        planner.begin_planning()
        # owner_ref is the placement_label of the level that resolved each
        # component (clone_position_calculator::_resolve_one_level): the
        # top-level ClonePlacement's OWN components carry this placement's
        # effective name, while components of nested CellPlacements carry the
        # nested name (ch1_pif_dvdd, ...). Filter to ONLY this placement's own
        # level — otherwise a composite cell's Redraw would re-tag every
        # nested sub-cell component with THIS cluster, wiping their own
        # Cluster fields (live bug 2026-08-26, handoff tag_cluster_overtag:
        # all 25 components of dac_buf ended up Cluster='DAC_BUF').
        own_label = clone_placement_effective_name(my_item.obj)
        refs: List[str] = []
        for item in pipeline.items:
            moves = planner.plan_item(item)
            if item is my_item:
                refs = [m.ref for m in moves if m.owner_ref == own_label]
                break

        updates = []
        for ref in refs:
            fp = pipeline.adapter.get_footprint(ref)
            if fp is not None:
                updates.append((fp, CLUSTER_FIELD_NAME, name))
        if updates:
            pipeline.adapter.set_field_values_bulk(updates, _("Placer: tag Cluster={name}").format(name=name))
        return len(updates)

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        with busy(self._action_buttons()):
            self._do_save()

    def _autostage(self) -> None:
        """Field commit point -> stage the current placement into the working
        set (2026-09-01, plan project_save_model); File > Save commits it.
        Skips programmatic population (_loading), no project, and INCOMPLETE
        forms: _do_save validates the whole placement, and during auto-stage
        its messages are silenced, so an invalid partial form is simply not
        staged — the working set never holds an invalid record, so the global
        Save's graph validation stays intact. Wrapped in try/except: an
        unhandled exception in a PyQt6 signal slot aborts the whole process,
        so a staging bug must degrade to a log line, never a crash."""
        if self._loading or self._placer_path is None:
            return
        if self.is_entity:
            if not self.entity_combo.currentText().strip():
                return
        elif self.is_coordinate:
            if not self.coordinate_form.cluster_combo.currentText().strip():
                return
        elif not self.cluster_edit.currentText().strip():
            return
        orig_show = self._show_message
        try:
            self._show_message = lambda *a, **k: None  # silent-skip invalid forms
            self._do_save()
        except Exception:
            logger.exception("placer auto-stage failed")
        finally:
            self._show_message = orig_show

    def _do_save(self) -> None:
        entry = self._build_entry_dict()
        if entry is None:
            return
        if self._placer_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        if self.is_entity:
            self._do_save_entity(entry)
            return
        if self.is_coordinate:
            self._do_save_coordinate(entry)
            return
        try:
            load_clone_placement(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return

        # Renamed via Cluster or Placer name directly in the form (not the
        # Config tree's Rename) — upsert alone would append a duplicate under
        # the new identity, leaving the old one behind. Remove the old entry
        # first (same mechanism ConfigTreeDock's own Delete uses), then upsert
        # the new one (2026-08-15, plan placer_form_save_renames_not_duplicates).
        new_identity = entry.get("name") or entry.get("cluster")
        if (self._loaded_clone_identity is not None
                and self._loaded_clone_identity != new_identity):
            try:
                delete_entry(None, self._placer_path, "clone_placements",
                             self._loaded_clone_identity, cascade=False)
            except OSError:
                pass  # already gone (e.g. saved twice in a row) — nothing to clean up
            self._loaded_clone_identity = new_identity

        try:
            overwritten = self._upsert_clone_placement(self._placer_path, entry)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        self._show_message(
            _("{action} {name!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                name=new_identity, path=display_path(self._placer_path)),
            _SUCCESS_STYLE)
        self.saved.emit()

    def _do_save_entity(self, entry: Dict[str, Any]) -> None:
        """Entity mode's save (2026-08-30, phase 5.2): validate through
        load_entity() (which fatals on any positional key — an Entity never
        carries a position), then upsert into entities: by name."""
        try:
            load_entity(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return

        # Renamed via the Entity combo directly — upsert alone would append
        # a duplicate under the new name, leaving the old record behind
        # (same mechanism as the clone path's rename, 2026-08-15).
        new_identity = entry["name"]
        if (self._loaded_entity_identity is not None
                and self._loaded_entity_identity != new_identity):
            try:
                delete_entry(None, self._placer_path, "entities",
                             self._loaded_entity_identity, cascade=False)
            except OSError:
                pass  # already gone (e.g. saved twice in a row) — nothing to clean up
            self._loaded_entity_identity = new_identity

        # Merge-preserve (2026-08-30, phase 5.2 stage 3): the form no longer
        # carries nets/net_overrides/refs (they moved to the Tools dock), and
        # upsert replaces the WHOLE record — so load the existing raw dict,
        # preserve from it ONLY the fields ToolsDock owns, and let the form's
        # payload override. A field the user CLEARED in the form (absent from
        # the payload, since _build_entity_dict omits falsy optionals) must
        # be CLEARED on disk, not resurrected from the old record by a
        # full-dict merge (2026-08-30 review fix). Write to the file the
        # Entity actually lives in, so an Entity in an included file is
        # updated in place, never duplicated into the root.
        target_path = self._placer_path
        existing_file = find_list_entry_file(
            self._root_path, "entities", {"name": new_identity})
        if existing_file is not None:
            target_path = existing_file
            try:
                existing_data = read_data(existing_file)
            except (ValidationError, OSError):
                existing_data = {}
            for existing_entry in existing_data.get("entities") or []:
                if isinstance(existing_entry, dict) and existing_entry.get("name") == new_identity:
                    preserved = {k: v for k, v in existing_entry.items()
                                 if k in ("nets", "net_overrides", "refs")}
                    entry = {**preserved, **entry}
                    break

        try:
            overwritten = upsert_entity(target_path, entry)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        # Phase 5.2 stage 2: the Origin tab edits the Entity's trees: node —
        # write it when a position was actually given (blank origin -> no
        # change, so an unplaced Entity stays unplaced; removing a node to
        # unplace is TreesDock's job). Written AFTER the entity so a failed
        # entity write never leaves a dangling placement behind.
        node_fields = self._build_entity_node_dict()
        if node_fields is not None:
            rotation = self._parse_float(self.rotation_edit, _("Rotation"), default=0.0)
            if rotation is None:
                return
            try:
                upsert_entity_placement(target_path, new_identity,
                                        node_fields, rotation)
            except OSError as e:
                self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
                return

        self._show_message(
            _("{action} entity {name!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                name=new_identity, path=display_path(target_path)),
            _SUCCESS_STYLE)
        self.saved.emit()

    def _do_save_coordinate(self, entry: Dict[str, Any]) -> None:
        """Coordinate mode's save (2026-08-12, Group 1): the same
        validate-then-upsert flow as the clone path, but into
        coordinate_placements: matched by EFFECTIVE name — the identity the
        tree/--only/duplicate-detection all use — so an entry whose default
        cluster/role name changes on re-save still replaces the same record
        the leaf click loaded."""
        try:
            cp = load_coordinate_placement(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return
        name = coordinate_placement_effective_name(cp)
        try:
            # Match existing entries by their RAW effective name (name or
            # cluster/role) WITHOUT re-validating each one through
            # load_coordinate_placement() — a single broken/legacy entry in
            # the file would otherwise raise a ValidationError from the
            # key_fn and kill the save of an unrelated record (2026-08-13
            # review, bug 1).
            overwritten = upsert_list_entry(
                self._placer_path, "coordinate_placements", entry,
                key_fn=lambda e: entry_effective_name("coordinate_placements", e))
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return
        self._show_message(
            _("{action} {name!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                name=name, path=display_path(self._placer_path)),
            _SUCCESS_STYLE)
        self.saved.emit()

    # ── Undo (2026-08-25) ────────────────────────────────────────────────

    def _resolve_operation_log_dir(self) -> Path:
        """The operation-log directory Undo should read — the SAME directory
        `apply` writes (ctx.operation_log_dir, resolved by load_config relative
        to the config file). Falls back to the project ROOT config's value when
        the Placer file itself doesn't set one (a leaf Placer file's own
        resolution comes up empty even though the root resolves it — same
        downward-only include: merge as _load_target_config's sheet_names
        fallback), and finally to DEFAULT_LOG_DIR exactly like cmd_undo."""
        if self._placer_path is not None:
            loaded = self._load_target_config(silent=True)
            if loaded is not None:
                _, ctx = loaded
                if ctx.operation_log_dir:
                    return Path(ctx.operation_log_dir)
                if self._root_path is not None and self._root_path != self._placer_path:
                    try:
                        _root_cfg, root_ctx = load_config(str(self._root_path))
                        if root_ctx.operation_log_dir:
                            return Path(root_ctx.operation_log_dir)
                    except (ValidationError, OSError):
                        pass
        return Path(DEFAULT_LOG_DIR)

    def _on_undo(self) -> None:
        """Undo button handler — undo the NEWEST operation_*.json in the whole
        project's operation_log_dir, NOT necessarily the operation this Placer
        form ran (same semantics as `kicadstamp undo`). Confirm first
        (destructive, one-shot), then run undo_last_operation on the worker
        thread — it opens its own kipy socket, gated by long_op_active like
        Redraw's ApplyPipeline."""
        self._show_message("")
        log_dir = self._resolve_operation_log_dir()
        last_file = _newest_operation_file(log_dir)
        if last_file is None:
            self._show_message(
                _("Nothing to undo — no operation logs in {dir}.")
                .format(dir=display_path(log_dir)), _ERROR_STYLE)
            return
        reply = QMessageBox.question(
            self, _("Undo last operation"),
            _("Undo the LAST operation logged in the whole project's "
              "operation-log directory ({dir})?\n\n"
              "It is not necessarily the operation THIS Placer form ran — it "
              "undoes whatever `kicadstamp undo` would pick (the newest "
              "operation_*.json). Moved components are restored and created "
              "vias/tracks are removed. This cannot be redone from the GUI.")
            .format(dir=display_path(log_dir)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start_undo_op(last_file)

    def _start_undo_op(self, last_file: Path) -> None:
        payload = {"json_path": str(last_file)}
        self._active_op = start_long_op(
            self._main_window.connection,
            self._action_buttons(),
            self._run_undo, self._finish_undo, self._on_undo_failed, payload)

    @staticmethod
    def _run_undo(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: undo the operation described by payload["json_path"]
        — never touches a widget. undo_last_operation builds its own
        KiCadBoardAdapter here on the worker thread (the same "the op opens its
        own kipy socket, gated by long_op_active" model as _run_redraw's
        ApplyPipeline)."""
        from kicadstamp.undo import undo_last_operation
        json_path = Path(payload["json_path"])
        try:
            undo_last_operation(json_path)
        except Exception as e:
            logger.exception("Undo failed")
            return {"error": str(e), "name": json_path.name}
        return {"name": json_path.name}

    def _finish_undo(self, result: Dict[str, Any]) -> None:
        """UI thread: report the undone operation's file name; an error leaves
        an explicit failure message (the confirm dialog must not imply
        something was undone when the undo actually failed)."""
        if result.get("error"):
            self._show_message(
                _("Undo failed for {name}: {error}")
                .format(name=result["name"], error=result["error"]), _ERROR_STYLE)
            return
        self._show_message(_("Undone operation {name}.").format(name=result["name"]),
                           _SUCCESS_STYLE)

    def _on_undo_failed(self, message: str) -> None:
        self._show_message(_("Undo failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_undo(self, last_file: Path) -> None:
        """Synchronous composition of run + finish — the same "for tests"
        shape as _do_redraw."""
        result = self._run_undo({"json_path": str(last_file)})
        self._finish_undo(result)

    # ── Starting a brand new placement (ConfigTreeDock's Add placer) ───────

    def new_placement(self, placer_path: Path) -> None:
        """Resets the form to its initial (blank) state — ConfigTreeDock's
        "Add placer" context-menu action (2026-08-03) opens this form empty
        rather than writing a raw stub straight to YAML, so the existing
        validated Save path (_do_save -> load_clone_placement) is what
        actually creates the entry, same as every other way a placement gets
        saved. The entry is written to the project root file (2026-08-21), so
        the passed path is ignored."""
        self._loading = True
        self._placer_path = self._root_path
        self._selected_cell = None
        self.cell_combo.setCurrentIndex(-1)
        self.cell_mode_combo.setCurrentIndex(0)
        self._on_cell_mode_changed()
        self.cluster_edit.setCurrentText("")
        # Auto-fill is exactly what a BLANK form wants (2026-08-15, plan
        # cluster_field_autofill_not_hard_overwrite) — reset the "user-owned"
        # flag so the next tree-click auto-fill works again.
        self._cluster_identity_dirty = False
        # Same for the board-selection auto-fill's signature guard
        # (2026-08-31, plan placer_source_tab_gaps P.1): a fresh blank form
        # must re-autofill even when the board selection hasn't changed since
        # the last tick.
        self._last_selection_cluster_signature = None
        # Placer name: same "blank form wants auto-fill" reset as Cluster
        # (2026-08-15, plan clone_placement_placer_name_split).
        self.placer_name_edit.setText("")
        self.placer_comment_edit.setText("")
        self._placer_name_dirty = False
        # A brand new (unsaved) placement has no prior identity — _do_save()
        # must just append, not try to remove an old entry
        # (2026-08-15, plan placer_form_save_renames_not_duplicates).
        self._loaded_clone_identity = None
        self._loaded_entity_identity = None
        self._placement_status_label.setText("")
        self.sheet_edit.setCurrentText("")
        self.origin_widget.clear()
        self.rotation_edit.setText("")
        self.layer_combo.setCurrentIndex(0)
        self.mirror_checkbox.setChecked(False)
        self._rebuild_cell_role_choices()
        self._loaded_override_fields = {}
        self._loading = False
        self._show_message("")

    def new_coordinate_placement(self, placer_path: Path) -> None:
        """Resets the form to a blank COORDINATE placement — ConfigTreeDock's
        "Add coordinate placement..." context-menu action opens the
        coordinate form empty, same validated-Save path as new_placement's
        clone counterpart (_do_save_coordinate -> load_coordinate_placement).
        The entry is written to the project root file (2026-08-21), so the
        passed path is ignored."""
        self._loading = True
        self._placer_path = self._root_path
        self.cell_mode_combo.setCurrentIndex(1)  # -> Single component (signal toggles tabs)
        self._on_cell_mode_changed()
        self.coordinate_form.clear()
        self._loading = False
        self._show_message("")

    # ── Loading an already-saved placement back into the form ──────────────

    def load_placement(self, entry: Dict[str, Any], file_path: Optional[Path] = None) -> None:
        """Reverse of _build_entry_dict() — called by ConfigTreeDock when a
        Clone placement OR Coordinate placement leaf is clicked (2026-08-12,
        Group 1: both route here, the form adapts to whether the entry has
        cell:). A clone_placement loads the cell-based field set, a
        coordinate_placement (no cell:) the _CoordinatePlacementForm. The
        WRITE target is set back to the file the entry actually lives in, so
        a Save updates that file instead of adding a root duplicate
        (2026-08-21 review fix)."""
        self._show_message("")
        section = "clone_placements" if "cell" in entry else "coordinate_placements"
        if file_path is None:
            file_path = find_list_entry_file(self._root_path, section, entry)
        if file_path is not None:
            self._placer_path = file_path
        # Reset the clone Origin before anything else (plan 2026-08-13, p.3):
        # on the moment set_selected_cell() runs below, anchor_cluster must be
        # empty (or already THIS record's), never the previous record's value —
        # otherwise the new auto-fill trigger could fire on a stale cluster
        # before this record's own nets load. Same call new_placement() uses.
        self._loading = True
        self.origin_widget.clear()
        if "cell" not in entry:
            # Coordinate placement — single component, no cell:.
            self.cell_mode_combo.setCurrentIndex(1)  # -> Single component (signal toggles tabs)
            self._on_cell_mode_changed()
            self.coordinate_form.load(entry)
            self._loading = False
            return
        self.cluster_edit.setCurrentText(str(entry.get("cluster", "")))
        # A loaded entry owns its identity (2026-08-15, plan
        # cluster_field_autofill_not_hard_overwrite) — a stray tree click must
        # not pull the form off an already-saved record.
        self._cluster_identity_dirty = True
        # A loaded entry owns its Placer name identity too — editing Cluster
        # on it must not drag Placer name along (2026-08-15, plan
        # clone_placement_placer_name_split).
        self.placer_name_edit.setText(str(entry.get("name") or ""))
        self.placer_comment_edit.setText(str(entry.get("comment") or ""))
        self._placer_name_dirty = True
        # Remember what identity this form loaded — _do_save() removes the old
        # entry when the about-to-be-saved identity differs (rename via the
        # form's own fields), instead of letting upsert append a duplicate
        # (2026-08-15, plan placer_form_save_renames_not_duplicates).
        self._loaded_clone_identity = entry.get("name") or entry.get("cluster")
        self.sheet_edit.setCurrentText(str(entry.get("sheet") or ""))
        # cell: is mandatory on ClonePlacement since 2026-08-12 (Group 0
        # consolidation — the role:/cluster: modes migrated to
        # coordinate_placements' anchor-relative mode), so a cell-bearing
        # entry is always Cell mode.
        self.cell_mode_combo.setCurrentIndex(0)
        self.set_selected_cell(entry["cell"])
        self._on_cell_mode_changed()  # setCurrentIndex above is a no-op signal-wise when unchanged

        xy = entry.get("xy") or [0.0, 0.0]
        radius = entry.get("radius_mm")
        if "anchor_point" in entry:
            if radius is not None:
                # Anchor + polar offset (2026-08-12, Group 2 fix — this used
                # to fall into the Cartesian-anchor branch, silently dropping
                # the polar offset on the next Save). point= is REQUIRED here
                # too (AnchorOriginWidget.load defaults it to "" and
                # unconditionally clears the combo — forgetting it would wipe
                # the anchor_point back out of the form on the next Save, the
                # same data-loss class of bug).
                self.origin_widget.load(mode="point", point=str(entry["anchor_point"]),
                                        polar=True, radius=radius,
                                        angle=entry.get("angle_deg"))
            else:
                self.origin_widget.load(mode="point", point=str(entry["anchor_point"]),
                                        shift_x=xy[0], shift_y=xy[1])
        elif "anchor_ref" in entry or "anchor_role" in entry:
            if radius is not None:
                self.origin_widget.load(
                    mode="anchor", ref=str(entry.get("anchor_ref", "")),
                    role=str(entry.get("anchor_role", "")),
                    sheet=str(entry.get("anchor_sheet", "")),
                    pad=str(entry.get("anchor_pad", "")),
                    cluster=str(entry.get("anchor_cluster", "")),
                    polar=True, radius=radius, angle=entry.get("angle_deg"))
            else:
                self.origin_widget.load(
                    mode="anchor", ref=str(entry.get("anchor_ref", "")),
                    role=str(entry.get("anchor_role", "")),
                    sheet=str(entry.get("anchor_sheet", "")),
                    pad=str(entry.get("anchor_pad", "")),
                    cluster=str(entry.get("anchor_cluster", "")),
                    shift_x=xy[0], shift_y=xy[1])
        elif radius is not None:
            # Polar offset (optional alternative to xy) — reload the XY row
            # in Polar mode with radius/angle (see ClonePlacement's docstring).
            self.origin_widget.load(mode="xy", polar=True, radius=radius,
                                    angle=entry.get("angle_deg"))
        else:
            self.origin_widget.load(mode="xy", x=xy[0], y=xy[1])

        self.rotation_edit.setText(str(entry.get("rotation_deg", 0)))
        self.layer_combo.setCurrentIndex({"F.Cu": 1, "B.Cu": 2}.get(entry.get("layer"), 0))
        self.mirror_checkbox.setChecked(bool(entry.get("mirror", False)))

        # Remember the loaded record's stored override fields so an unrelated
        # edit here never wipes them on save (tabs removed 2026-09-05).
        self._loaded_override_fields = {
            key: value for key, value in (
                ("params", entry.get("params")), ("nets", entry.get("nets")),
                ("net_overrides", entry.get("net_overrides")),
                ("refs", entry.get("refs")))
            if value}
        self._loading = False

    @staticmethod
    def _upsert_clone_placement(path: Path, entry: Dict[str, Any]) -> bool:
        """Read-merge-write like merge_write()/add_list_entry(), but for
        clone_placements: — a list of dicts matched by their own 'name' key,
        not by list membership: an entry whose name already exists gets
        REPLACED in place (same position), a new name gets appended. Every
        other key in the file (cells:, include:, extract_profiles:, ...) is
        left untouched. Delegates to
        gui/docks/_common.upsert_clone_placement (kept as a thin wrapper
        because the GUI tests call dock._upsert_clone_placement directly)."""
        return upsert_clone_placement(path, entry)

    @property
    def current_entity_name(self) -> str:
        """Best-effort "what's loaded in the form right now", for
        DetailDock's window title — the clone name in Cell mode, the
        coordinate effective name in Single-component mode."""
        if self.is_entity:
            return self.entity_combo.currentText().strip()
        if self.is_coordinate:
            return self.coordinate_form.name_edit.text().strip() \
                or self.coordinate_form.cluster_combo.currentText().strip()
        return self.cluster_edit.currentText().strip()
