# gui/docks/coordinate_placer.py
"""
CoordinatePlacerDock — bulk table editor for coordinate_placements: (the
"dumb placer", Denis 2026-08-12: "он просто расставляет компоненты по
координатам... в кластере роли уникальные, делаем таблицу и расставляем").

Unlike ThermalViaArrayDock/RulesDock/PlacerDock — one entry per form, the
list itself browsed via ConfigTreeDock, click a leaf to load ONE entry —
this dock's editing unit is the WHOLE list at once: one QTableWidget, one
row per coordinate_placements: entry, live editable cell widgets
(QComboBox/QLineEdit/QCheckBox via setCellWidget, same pattern
ExtractDock's nets_table already uses for per-row Alias/Rule-net cells).
That shape was an explicit ask — a form-per-entry flow would mean clicking
back and forth for every one of a whole batch of resistors placed around an
FPGA, defeating the point of a "fast bulk table".

Each row is independently: (Cluster, Role) — Role already unique within one
Cluster instance by the project's Role/Cluster convention, resolved at
apply time by coordinate_position_calculator.py's
resolve_footprint_by_cluster_role() (exact match, fatal if ambiguous, never
guessed) — plus a position (Cartesian x_mm/y_mm OR polar center/radius/
angle, mutually exclusive), a rotation (required in Cartesian mode,
defaults to the polar angle otherwise), and an anchor (component centre or
one specific pad of the SAME component). See CoordinatePlacement's own
docstring in kicadstamp/config/models.py for the full field semantics —
this dock does not duplicate that validation, every row is checked through
the same load_coordinate_placement() the CLI/YAML path uses (Save AND
Place both validate every row before writing/running anything).

Save writes the WHOLE table via set_list_section() (kicadstamp/
config_writer.py, new 2026-08-12) — a full REPLACE of the
coordinate_placements: section, not upsert_list_entry()'s identity-matched
single-row replace/append (ThermalViaArrayDock's shape): the table already
IS the full list, editing one entry at a time and writing it back
individually would fight the "spreadsheet" workflow this was built for.

Place mirrors ThermalViaArrayDock's Redraw exactly (load_config() the real
target file, replace-by-name the rows this table currently holds, run
ApplyPipeline with only=[[the row names]], async via start_long_op) — every
OTHER item already in the file (rules, clone_placements, thermal via
arrays, or coordinate_placements rows from a different table/session
sharing this file) is left untouched and unprotected-from-narrowing exactly
like Redraw's own replace-by-name comment already explains.

No registry, no via/track, no Placer-file indirection — see
CoordinatePlacement's own docstring for why: a move is idempotent by
construction, this dock applies directly against its OWN target file (like
ThermalViaArrayDock/RulesDock/PlacerDock), not through a separate Placer
file the way ExtractDock needs one.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from kipy.errors import ApiError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout,
                              QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
                              QVBoxLayout, QWidget)

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import (Config, CoordinatePlacement, RuntimeContext, load_config,
                                load_coordinate_placement, coordinate_placement_effective_name)
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.i18n import _

from ..worker import start_long_op
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, display_path, parse_float_field, set_combo_items,
                      set_list_section, set_mode_pair_enabled, show_message)

logger = logging.getLogger(__name__)

(_COL_CLUSTER, _COL_ROLE, _COL_NAME, _COL_MODE, _COL_X, _COL_Y,
 _COL_CENTER_X, _COL_CENTER_Y, _COL_RADIUS, _COL_ANGLE, _COL_ROTATION,
 _COL_ANCHOR, _COL_PAD, _COL_ANCHOR_REF, _COL_ANCHOR_ROLE,
 _COL_ANCHOR_SHEET, _COL_ANCHOR_CLUSTER, _COL_ANCHOR_POINT,
 _COL_RETIRED, _COL_SKIP) = range(20)

_HEADERS = [
    _("Cluster"), _("Role"), _("Name"), _("Mode"), _("X mm"), _("Y mm"),
    _("Center X mm"), _("Center Y mm"), _("Radius mm"), _("Angle °"), _("Rotation °"),
    _("Anchor"), _("Pad"), _("Anchor ref"), _("Anchor role"),
    _("Anchor sheet"), _("Anchor cluster"), _("Anchor point"),
    _("Retired"), _("Skip"),
]


class CoordinatePlacerDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — same
    "plain QWidget, not its own QDockWidget" shape as every other dock
    living there."""

    # Fired after a successful Save — ConfigTreeDock listens to refresh its
    # Coordinate placements category (see gui/dock_hub.py).
    saved = pyqtSignal()

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        # The currently running long op (gui/worker.py) — held so the
        # parent-less QThread isn't garbage-collected mid-run.
        self._active_op: Optional[Any] = None
        self._path: Optional[Path] = None
        self._known_roles: List[str] = []
        self._known_clusters: List[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.target_label = QLabel(_("No file picked (pick one in the Config tree)"))
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, 1)

        row_buttons = QHBoxLayout()
        self.add_row_button = QPushButton(_("Add row"))
        self.add_row_button.clicked.connect(lambda: self._add_row())
        row_buttons.addWidget(self.add_row_button)
        self.delete_row_button = QPushButton(_("Delete selected row(s)"))
        self.delete_row_button.clicked.connect(self._on_delete_selected)
        row_buttons.addWidget(self.delete_row_button)
        layout.addLayout(row_buttons)

        action_buttons = QHBoxLayout()
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        action_buttons.addWidget(self.save_button)
        self.place_button = QPushButton(_("Place all"))
        self.place_button.clicked.connect(self._on_place)
        action_buttons.addWidget(self.place_button)
        layout.addLayout(action_buttons)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

    # ── Wiring from the Config tree ─────────────────────────────────────

    def set_target_file(self, path: Optional[Path]) -> None:
        self._path = path
        self.target_label.setText(
            display_path(path) if path is not None
            else _("No file picked (pick one in the Config tree)"))

    def load_from_file(self, path: Path) -> None:
        """Loads path's existing coordinate_placements: list into the table
        — called when ConfigTreeDock's Coordinate placements category is
        clicked. Whole-list editing unit (unlike thermal_via_arrays/rules/
        clone_placements' one-entry-per-leaf browsing) — there is no single
        "entry" to click into, the category itself opens the table."""
        self.set_target_file(path)
        self.table.setRowCount(0)
        # Guard against a None path (config_tree no longer emits it, but a
        # direct/dock_hub call must not crash the Qt process on None.exists(),
        # 2026-08-12 Group 2 fix).
        if path is None or not path.exists():
            self._show_message("")
            return
        try:
            cfg, _ctx = load_config(str(path))
        except (ValidationError, OSError, yaml.YAMLError) as e:
            self._show_message(_("Failed to load file: {error}").format(error=e), _ERROR_STYLE)
            return
        for cp in cfg.coordinate_placements:
            self._add_row(self._coordinate_placement_to_dict(cp))
        self._show_message("")

    def refresh_known_roles(self, snapshot) -> None:
        """Same "populate from the live board" pattern as every other
        dock's own refresh_known_roles — called by DockHub.push_snapshot at
        the same ~2s poll cadence. Every EXISTING row's combos are
        refreshed too, not just newly-added ones."""
        self._known_roles = sorted({s.role for s in snapshot if s.role})
        self._known_clusters = sorted({s.cluster for s in snapshot if s.cluster})
        for row in range(self.table.rowCount()):
            set_combo_items(self.table.cellWidget(row, _COL_CLUSTER), self._known_clusters)
            set_combo_items(self.table.cellWidget(row, _COL_ROLE), self._known_roles)
            # Anchor-relative columns (2026-08-12, Group 0) — same known-value
            # combos as the moved component's own Cluster/Role columns.
            set_combo_items(self.table.cellWidget(row, _COL_ANCHOR_ROLE), self._known_roles)
            set_combo_items(self.table.cellWidget(row, _COL_ANCHOR_CLUSTER), self._known_clusters)

    # ── Message helper ────────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(self.message_label, text, style, logger)

    # ── Row widgets ───────────────────────────────────────────────────────

    @staticmethod
    def _fmt(value: Optional[float]) -> str:
        return "" if value is None else str(value)

    def _add_row(self, entry: Optional[Dict[str, Any]] = None) -> None:
        entry = entry or {}
        row = self.table.rowCount()
        self.table.insertRow(row)

        cluster_combo = QComboBox()
        configure_searchable(cluster_combo)
        set_combo_items(cluster_combo, self._known_clusters)
        cluster_combo.setCurrentText(str(entry.get("cluster", "")))
        self.table.setCellWidget(row, _COL_CLUSTER, cluster_combo)

        role_combo = QComboBox()
        configure_searchable(role_combo)
        set_combo_items(role_combo, self._known_roles)
        role_combo.setCurrentText(str(entry.get("role", "")))
        self.table.setCellWidget(row, _COL_ROLE, role_combo)

        name_edit = QLineEdit(str(entry.get("name") or ""))
        name_edit.setPlaceholderText(_("cluster/role"))
        self.table.setCellWidget(row, _COL_NAME, name_edit)

        mode_combo = QComboBox()
        mode_combo.addItems([_("Cartesian"), _("Polar"), _("Anchor")])
        if any(entry.get(k) for k in ("anchor_ref", "anchor_role", "anchor_point")):
            mode_combo.setCurrentIndex(2)
        else:
            mode_combo.setCurrentIndex(1 if entry.get("center_x_mm") is not None else 0)
        self.table.setCellWidget(row, _COL_MODE, mode_combo)

        self.table.setCellWidget(row, _COL_X, QLineEdit(self._fmt(entry.get("x_mm"))))
        self.table.setCellWidget(row, _COL_Y, QLineEdit(self._fmt(entry.get("y_mm"))))
        self.table.setCellWidget(row, _COL_CENTER_X, QLineEdit(self._fmt(entry.get("center_x_mm"))))
        self.table.setCellWidget(row, _COL_CENTER_Y, QLineEdit(self._fmt(entry.get("center_y_mm"))))
        self.table.setCellWidget(row, _COL_RADIUS, QLineEdit(self._fmt(entry.get("radius_mm"))))
        self.table.setCellWidget(row, _COL_ANGLE, QLineEdit(self._fmt(entry.get("angle_deg"))))
        rotation_edit = QLineEdit(self._fmt(entry.get("rotation_deg")))
        rotation_edit.setPlaceholderText(_("= angle (polar only)"))
        self.table.setCellWidget(row, _COL_ROTATION, rotation_edit)

        anchor_combo = QComboBox()
        anchor_combo.addItems([_("Center"), _("Pad")])
        anchor_combo.setCurrentIndex(1 if entry.get("anchor") == "pad" else 0)
        self.table.setCellWidget(row, _COL_ANCHOR, anchor_combo)

        self.table.setCellWidget(row, _COL_PAD, QLineEdit(str(entry.get("anchor_pad") or "")))

        # Anchor-relative columns (2026-08-12, Group 0): enabled only in
        # Anchor mode. In that mode the Pad column above means the ANCHOR
        # component's pad (same semantics as Rule/ClonePlacement), and the
        # self-referential "Anchor" (Center/Pad) column is disabled.
        self.table.setCellWidget(row, _COL_ANCHOR_REF, QLineEdit(str(entry.get("anchor_ref") or "")))
        anchor_role_combo = QComboBox()
        configure_searchable(anchor_role_combo)
        set_combo_items(anchor_role_combo, self._known_roles)
        anchor_role_combo.setCurrentText(str(entry.get("anchor_role") or ""))
        self.table.setCellWidget(row, _COL_ANCHOR_ROLE, anchor_role_combo)
        self.table.setCellWidget(row, _COL_ANCHOR_SHEET, QLineEdit(str(entry.get("anchor_sheet") or "")))
        anchor_cluster_combo = QComboBox()
        configure_searchable(anchor_cluster_combo)
        set_combo_items(anchor_cluster_combo, self._known_clusters)
        anchor_cluster_combo.setCurrentText(str(entry.get("anchor_cluster") or ""))
        self.table.setCellWidget(row, _COL_ANCHOR_CLUSTER, anchor_cluster_combo)
        self.table.setCellWidget(row, _COL_ANCHOR_POINT, QLineEdit(str(entry.get("anchor_point") or "")))

        retired_checkbox = QCheckBox()
        retired_checkbox.setChecked(bool(entry.get("retired", False)))
        self.table.setCellWidget(row, _COL_RETIRED, retired_checkbox)
        skip_checkbox = QCheckBox()
        skip_checkbox.setChecked(bool(entry.get("skip", False)))
        self.table.setCellWidget(row, _COL_SKIP, skip_checkbox)

        # Fields irrelevant to the current Mode/Anchor stay VISIBLE but
        # disabled (not hidden) — predictable column positions in a wide
        # table beat a layout that reflows per row. The row index is resolved
        # FROM THE WIDGET at signal time (not captured in the closure) —
        # _on_delete_selected shifts surviving rows, so a captured index would
        # go stale and toggle the WRONG row (2026-08-12, Group 2 fix).
        mode_combo.currentIndexChanged.connect(
            lambda _i, w=mode_combo: self._update_row_mode_by_widget(w))
        anchor_combo.currentIndexChanged.connect(
            lambda _i, w=anchor_combo: self._update_row_anchor_by_widget(w))
        self._update_row_mode(row)
        self._update_row_anchor(row)

    def _update_row_mode_by_widget(self, widget) -> None:
        """Finds the row whose Mode combo is `widget` and toggles it — the
        row-safe replacement for a captured index (see _add_row's comment)."""
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, _COL_MODE) is widget:
                self._update_row_mode(r)
                return

    def _update_row_anchor_by_widget(self, widget) -> None:
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, _COL_ANCHOR) is widget:
                self._update_row_anchor(r)
                return

    def _update_row_mode(self, row: int) -> None:
        mode = self.table.cellWidget(row, _COL_MODE).currentIndex()
        is_polar = mode == 1
        is_anchor = mode == 2
        # Cartesian (X/Y) vs polar (center/radius/angle): X/Y are the OFFSET in
        # anchor mode too; the polar fields are absolute-only.
        set_mode_pair_enabled(
            is_polar,
            (self.table.cellWidget(row, _COL_X), self.table.cellWidget(row, _COL_Y)),
            (self.table.cellWidget(row, _COL_CENTER_X), self.table.cellWidget(row, _COL_CENTER_Y),
             self.table.cellWidget(row, _COL_RADIUS), self.table.cellWidget(row, _COL_ANGLE)),
        )
        # Self-referential anchor (Center/Pad) is meaningless in anchor mode;
        # there the Pad column means the ANCHOR component's pad and the anchor
        # identity columns (ref/role/sheet/cluster/point) are used instead.
        self.table.cellWidget(row, _COL_ANCHOR).setEnabled(not is_anchor)
        self.table.cellWidget(row, _COL_PAD).setEnabled(True)
        for col in (_COL_ANCHOR_REF, _COL_ANCHOR_ROLE, _COL_ANCHOR_SHEET,
                    _COL_ANCHOR_CLUSTER, _COL_ANCHOR_POINT):
            self.table.cellWidget(row, col).setEnabled(is_anchor)

    def _update_row_anchor(self, row: int) -> None:
        is_pad = self.table.cellWidget(row, _COL_ANCHOR).currentIndex() == 1
        self.table.cellWidget(row, _COL_PAD).setEnabled(is_pad)

    def _on_delete_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            self._show_message(_("Select a row first."), _ERROR_STYLE)
            return
        for row in rows:
            self.table.removeRow(row)

    # ── Row <-> dict conversion ──────────────────────────────────────────

    def _row_float(self, row: int, col: int) -> Tuple[bool, Optional[float]]:
        """(ok, value) — same shared parse as every other dock
        (parse_float_field in gui/docks/_common.py): empty text is a valid
        "not set" (None), letting load_coordinate_placement()'s own
        validation produce the real "which field is missing" error; only
        genuinely unparsable text (not a number at all) is caught here,
        since that has no equivalent YAML-side error to reuse."""
        widget = self.table.cellWidget(row, col)
        ok, value = parse_float_field(widget)
        if not ok:
            self._show_message(
                _("Row {row}, {column}: {text!r} is not a number.")
                .format(row=row + 1, column=_HEADERS[col], text=widget.text().strip()), _ERROR_STYLE)
        return ok, value

    def _row_to_entry(self, row: int) -> Optional[Dict[str, Any]]:
        cluster = self.table.cellWidget(row, _COL_CLUSTER).currentText().strip()
        role = self.table.cellWidget(row, _COL_ROLE).currentText().strip()
        entry: Dict[str, Any] = {"cluster": cluster, "role": role}
        name = self.table.cellWidget(row, _COL_NAME).text().strip()
        if name:
            entry["name"] = name

        mode = self.table.cellWidget(row, _COL_MODE).currentIndex()
        if mode == 2:
            # ANCHOR-RELATIVE: X/Y are the OFFSET from the anchor (or its
            # anchor_pad); Pad is the ANCHOR component's pad.
            cols = ((_COL_X, "x_mm"), (_COL_Y, "y_mm"), (_COL_ROTATION, "rotation_deg"))
            for col, key in cols:
                ok, value = self._row_float(row, col)
                if not ok:
                    return None
                if value is not None:
                    entry[key] = value
            ref = self.table.cellWidget(row, _COL_ANCHOR_REF).text().strip()
            anchor_role = self.table.cellWidget(row, _COL_ANCHOR_ROLE).currentText().strip()
            sheet = self.table.cellWidget(row, _COL_ANCHOR_SHEET).text().strip()
            anchor_cluster = self.table.cellWidget(row, _COL_ANCHOR_CLUSTER).currentText().strip()
            point = self.table.cellWidget(row, _COL_ANCHOR_POINT).text().strip()
            if ref:
                entry["anchor_ref"] = ref
            if anchor_role:
                entry["anchor_role"] = anchor_role
            if sheet:
                entry["anchor_sheet"] = sheet
            if anchor_cluster:
                entry["anchor_cluster"] = anchor_cluster
            if point:
                entry["anchor_point"] = point
            pad = self.table.cellWidget(row, _COL_PAD).text().strip()
            if pad:
                entry["anchor_pad"] = pad
        else:
            is_polar = mode == 1
            cols = ((_COL_CENTER_X, "center_x_mm"), (_COL_CENTER_Y, "center_y_mm"),
                    (_COL_RADIUS, "radius_mm"), (_COL_ANGLE, "angle_deg"),
                    (_COL_ROTATION, "rotation_deg")) if is_polar else (
                    (_COL_X, "x_mm"), (_COL_Y, "y_mm"), (_COL_ROTATION, "rotation_deg"))
            for col, key in cols:
                ok, value = self._row_float(row, col)
                if not ok:
                    return None
                if value is not None:
                    entry[key] = value
            if self.table.cellWidget(row, _COL_ANCHOR).currentIndex() == 1:
                entry["anchor"] = "pad"
                pad = self.table.cellWidget(row, _COL_PAD).text().strip()
                if pad:
                    entry["anchor_pad"] = pad

        if self.table.cellWidget(row, _COL_RETIRED).isChecked():
            entry["retired"] = True
        if self.table.cellWidget(row, _COL_SKIP).isChecked():
            entry["skip"] = True
        return entry

    @staticmethod
    def _coordinate_placement_to_dict(cp: CoordinatePlacement) -> Dict[str, Any]:
        """Reverse of _row_to_entry() — CoordinatePlacement -> the same
        dict shape _add_row() reads from, used by load_from_file()."""
        d: Dict[str, Any] = {"cluster": cp.cluster, "role": cp.role}
        if cp.name:
            d["name"] = cp.name
        if any(v is not None for v in (cp.anchor_ref, cp.anchor_role, cp.anchor_point)):
            # ANCHOR-RELATIVE: x_mm/y_mm (or radius_mm/angle_deg) are the offset.
            d["x_mm"], d["y_mm"] = cp.x_mm, cp.y_mm
            if cp.radius_mm is not None:
                d["radius_mm"], d["angle_deg"] = cp.radius_mm, cp.angle_deg
            if cp.anchor_ref:
                d["anchor_ref"] = cp.anchor_ref
            if cp.anchor_role:
                d["anchor_role"] = cp.anchor_role
            if cp.anchor_sheet:
                d["anchor_sheet"] = cp.anchor_sheet
            if cp.anchor_cluster:
                d["anchor_cluster"] = cp.anchor_cluster
            if cp.anchor_point:
                d["anchor_point"] = cp.anchor_point
            if cp.anchor_pad:
                d["anchor_pad"] = cp.anchor_pad
        else:
            if cp.x_mm is not None:
                d["x_mm"], d["y_mm"] = cp.x_mm, cp.y_mm
            else:
                d["center_x_mm"], d["center_y_mm"] = cp.center_x_mm, cp.center_y_mm
                d["radius_mm"], d["angle_deg"] = cp.radius_mm, cp.angle_deg
            if cp.anchor == "pad":
                d["anchor"] = "pad"
                d["anchor_pad"] = cp.anchor_pad
        if cp.rotation_deg is not None:
            d["rotation_deg"] = cp.rotation_deg
        if cp.retired:
            d["retired"] = True
        if cp.skip:
            d["skip"] = True
        return d

    def _build_entries(self) -> Optional[List[Dict[str, Any]]]:
        """Every row, read + validated through load_coordinate_placement()
        (the SAME validator the CLI/YAML path uses — no separate GUI
        validation logic to keep in sync) plus a table-wide duplicate-name
        check. None + an on-screen error (naming the offending row) on the
        first problem found — used by both Save and Place."""
        entries: List[Dict[str, Any]] = []
        placements: List[CoordinatePlacement] = []
        for row in range(self.table.rowCount()):
            entry = self._row_to_entry(row)
            if entry is None:
                return None
            try:
                cp = load_coordinate_placement(entry)
            except ValidationError as e:
                self._show_message(_("Row {row}: {error}").format(row=row + 1, error=e), _ERROR_STYLE)
                return None
            placements.append(cp)
            entries.append(entry)

        seen: Dict[str, int] = {}
        for cp in placements:
            name = coordinate_placement_effective_name(cp)
            seen[name] = seen.get(name, 0) + 1
        dupes = sorted(name for name, count in seen.items() if count > 1)
        if dupes:
            self._show_message(
                _("Duplicate name(s): {names} — every row needs a unique name "
                  "(explicit, or the default cluster/role pair).").format(names=dupes),
                _ERROR_STYLE)
            return None
        return entries

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        if self._path is None:
            self._show_message(_("Pick a file in the Config tree first."), _ERROR_STYLE)
            return
        entries = self._build_entries()
        if entries is None:
            return
        try:
            set_list_section(self._path, "coordinate_placements", entries)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return
        self._show_message(
            _("Wrote {count} coordinate_placements entries to {path}")
            .format(count=len(entries), path=display_path(self._path)), _SUCCESS_STYLE)
        self.saved.emit()

    # ── Place ─────────────────────────────────────────────────────────────

    def _on_place(self) -> None:
        self._show_message("")
        payload = self._collect_place_inputs()
        if payload is None:
            return
        self._start_place_op(payload)

    def _collect_place_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: read+validate every row, load the real target file,
        replace-by-name this table's own rows in it — same reasoning as
        ThermalViaArrayDock._collect_redraw_inputs's own comment."""
        if self._path is None:
            self._show_message(_("Pick a file in the Config tree first."), _ERROR_STYLE)
            return None
        entries = self._build_entries()
        if entries is None:
            return None
        if not entries:
            self._show_message(_("Nothing to place — the table is empty."), _ERROR_STYLE)
            return None

        try:
            if self._path.exists():
                cfg, ctx = load_config(str(self._path))
            else:
                cfg, ctx = Config(), RuntimeContext()
        except (ValidationError, OSError, yaml.YAMLError) as e:
            self._show_message(_("Failed to load file: {error}").format(error=e), _ERROR_STYLE)
            return None

        placements = [load_coordinate_placement(e) for e in entries]
        # Retired/skip rows stay in cfg (drop_inactive_items drops them before
        # --only sees them), but must NOT be in the --only names — otherwise
        # apply_only_filter can't find the name (the row was already dropped)
        # and "Place all" fails wholesale (2026-08-12, Group 2 fix).
        names = [coordinate_placement_effective_name(cp) for cp in placements
                 if not cp.retired and not cp.skip]
        cfg.coordinate_placements = [
            cp for cp in cfg.coordinate_placements
            if coordinate_placement_effective_name(cp) not in names
        ] + placements

        return {"path": self._path, "cfg": cfg, "ctx": ctx, "names": names}

    def _run_place(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: ApplyPipeline run only — never touches a widget."""
        pipeline = ApplyPipeline(config_path=str(payload["path"]),
                                 preloaded_cfg=payload["cfg"], preloaded_ctx=payload["ctx"],
                                 only=payload["names"], dry_run=False)
        try:
            pipeline.run()
        except (PlacerError, ValidationError, ApiError) as e:
            return {"error": _("Placement failed: {error}").format(error=e)}
        except Exception as e:
            logger.exception("Coordinate placement failed")
            return {"error": _("Placement failed: {error}").format(error=e)}
        return {"count": len(payload["names"])}

    def _finish_place(self, result: Dict[str, Any]) -> None:
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        self._show_message(_("Placed {count} row(s).").format(count=result["count"]), _SUCCESS_STYLE)

    def _start_place_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._main_window.connection, (self.save_button, self.place_button),
            self._run_place, self._finish_place, self._on_place_failed, payload)

    def _on_place_failed(self, message: str) -> None:
        self._show_message(_("Placement failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_place(self) -> None:
        """Synchronous composition of collect + run + finish — for tests
        and any caller that must not return until the run is complete."""
        payload = self._collect_place_inputs()
        if payload is None:
            return
        result = self._run_place(payload)
        self._finish_place(result)
