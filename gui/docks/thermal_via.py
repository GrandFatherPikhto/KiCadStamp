# gui/docks/thermal_via.py
"""
ThermalViaArrayDock — pick an anchor (ref/role, optionally narrowed by
Cluster, or a named Point), a pad, a net, and the grid geometry
(rows/cols/margin/pattern/drill/diameter) a thermal_via_arrays: entry needs
— then Redraw (place it for real on the live board via ApplyPipeline
only=[name], see it, adjust rows/margin, Redraw again) and Save (write the
thermal_via_arrays entry into the target file). Requested live 2026-08-03
(Denis: "для thermal_via_array я бы сразу создал панель") — same Save+
Redraw shape as PlacerDock, picked over Save-only via AskUserQuestion so
via-grid geometry can be tuned against the live result the same way a
clone_placement's origin already is.

No Cluster-tagging step (unlike PlacerDock's Redraw) — thermal vias have no
Cluster field of their own to tag; anchor_cluster here only NARROWS which
footprint the anchor_role search considers, same meaning as everywhere else
in this project (rules/clone_placements' own anchor_cluster).

Anchor is Ref/Role (+ optional Cluster) or a named Point — no "Absolute XY"
mode: a thermal via array's origin always comes from a real pad
(compute_thermal_via_grid() needs the live footprint's actual pad shape,
see kicadstamp/placement/services/via_planner.py), never an arbitrary
coordinate. anchor_sheet is now covered too (closed 2026-08-15, plan
plan_2026_08_15_sheet_combo_everywhere.md) — same "field exists in the
model, GUI never reached it" gap as ClonePlacement's Origin tab, fixed the
same way: the sheet combo + entry["anchor_sheet"]/load(sheet=...) wiring
were already in place for every other dock, only the form never surfaced
the field. Searchable combo, not a whitelist.

Redraw follows PlacerDock's own reasoning exactly: loads the REAL target
file's full config (load_config), replaces-by-name in
cfg.thermal_via_arrays, then narrows execution via ApplyPipeline's own
only=[name] — this keeps every other rule/clone_placement/thermal via
array's already-placed vias protected by PlacementRegistry.reconcile()'s
known_anchor_ids the same way apply_only_filter (kicadstamp/apply_pipeline.py)
computes it from the FULL config before narrowing.

Validation (name required, anchor_point vs anchor_ref/anchor_role mutual
exclusion, unknown-key check) is NOT duplicated here — both Save and
Redraw call kicadstamp.config.load_thermal_via_array(entry), the same
per-entry validator load_config() itself uses (extracted 2026-08-03 from
its former inline-only form specifically so this dock could reuse it,
mirroring load_clone_placement's existing public/private split).

anchor_point IS autocompleted (added 2026-08-06, Denis: "думаю имена
Points тоже надо делать выпадашкой с именами") — set_root_path(), wired to
RootMetadataDock's root_changed same as RuleDock's/PlacerDock's own,
sources the combo from the WHOLE include graph via
collect_all_point_names(), not just this dock's own target file.
"""
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from kipy.errors import ApiError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
                              QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import (Config, RuntimeContext, load_config,
                                load_thermal_via_array)
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.i18n import _

from ..worker import start_long_op
from ._anchor_origin import AnchorOriginWidget
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, display_path, refresh_file_combo_choices,
                      set_combo_items, set_file_combo_selection, show_message,
                      upsert_list_entry)
from .rename import collect_all_point_names, collect_all_sheet_names

logger = logging.getLogger(__name__)


class ThermalViaArrayDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — same
    "plain QWidget, not its own QDockWidget" shape as ExtractDock/PlacerDock/
    RootMetadataDock, see their module docstrings."""

    # Fired after a successful Save — ConfigTreeDock listens to refresh its
    # Thermal via arrays category (see gui/dock_hub.py).
    saved = pyqtSignal()

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        # The currently running long op (gui/worker.py) — held so the
        # parent-less QThread isn't garbage-collected mid-run.
        self._active_op: Optional[Any] = None
        self._path: Optional[Path] = None
        self._root_path: Optional[Path] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Target file as a dropdown (2026-08-13, plan
        # tree_to_combo_file_pickers) — was a plain label + ConfigTreeDock
        # click only; same "a tree click yanks the user into a different
        # panel" pain every other file picker already fixed with a combo.
        # set_target_file() stays the shared entry point (tree click and
        # combo both feed it), so the tree keeps working unchanged.
        target_file_row = QHBoxLayout()
        target_file_row.addWidget(QLabel(_("Thermal via file:")))
        self.target_file_combo = QComboBox()
        self.target_file_combo.setPlaceholderText(_("pick a file (or browse it in the Config tree)"))
        self.target_file_combo.currentIndexChanged.connect(self._on_target_file_combo_changed)
        target_file_row.addWidget(self.target_file_combo, 1)
        layout.addLayout(target_file_row)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("name (used by --only, must be unique)"))
        form.addRow(_("Name:"), self.name_edit)
        layout.addLayout(form)

        self.origin_widget = AnchorOriginWidget(modes=["anchor", "point"], anchor_fields=["sheet", "cluster"])
        layout.addWidget(self.origin_widget)
        # Aliases onto the shared widget's own sub-widgets — kept so
        # existing tests/call sites that poke fields directly (e.g.
        # dock.anchor_ref_edit.setText(...)) keep working unchanged.
        self.anchor_mode_combo = self.origin_widget.origin_mode_combo
        self.anchor_ref_edit = self.origin_widget.anchor_ref_edit
        self.anchor_role_edit = self.origin_widget.anchor_role_edit
        self.anchor_sheet_edit = self.origin_widget.anchor_sheet_edit
        self.anchor_cluster_edit = self.origin_widget.anchor_cluster_edit
        self.point_edit = self.origin_widget.point_edit

        pad_net_form = QFormLayout()
        self.pad_edit = QLineEdit()
        self.pad_edit.setPlaceholderText(_("pad number on the anchor footprint"))
        pad_net_form.addRow(_("Pad:"), self.pad_edit)
        self.net_edit = QComboBox()
        configure_searchable(self.net_edit)
        self.net_edit.setCurrentText("GND")
        pad_net_form.addRow(_("Net:"), self.net_edit)
        layout.addLayout(pad_net_form)

        grid_row = QHBoxLayout()
        self.rows_edit = QLineEdit()
        self.rows_edit.setPlaceholderText("4")
        self.cols_edit = QLineEdit()
        self.cols_edit.setPlaceholderText("4")
        grid_row.addWidget(QLabel(_("Rows:")))
        grid_row.addWidget(self.rows_edit)
        grid_row.addWidget(QLabel(_("Cols:")))
        grid_row.addWidget(self.cols_edit)
        layout.addLayout(grid_row)

        geometry_form = QFormLayout()
        self.margin_edit = QLineEdit()
        self.margin_edit.setPlaceholderText("0.5")
        geometry_form.addRow(_("Margin (mm):"), self.margin_edit)
        self.pattern_combo = QComboBox()
        self.pattern_combo.addItems(["grid", "staggered"])
        geometry_form.addRow(_("Pattern:"), self.pattern_combo)
        self.drill_edit = QLineEdit()
        self.drill_edit.setPlaceholderText("0.3")
        geometry_form.addRow(_("Drill (mm):"), self.drill_edit)
        self.diameter_edit = QLineEdit()
        self.diameter_edit.setPlaceholderText("0.5")
        geometry_form.addRow(_("Diameter (mm):"), self.diameter_edit)
        layout.addLayout(geometry_form)

        checks_row = QHBoxLayout()
        self.retired_checkbox = QCheckBox(_("Retired"))
        self.skip_checkbox = QCheckBox(_("Skip"))
        checks_row.addWidget(self.retired_checkbox)
        checks_row.addWidget(self.skip_checkbox)
        layout.addLayout(checks_row)

        button_row = QHBoxLayout()
        self.redraw_button = QPushButton(_("Redraw"))
        self.redraw_button.clicked.connect(self._on_redraw)
        button_row.addWidget(self.redraw_button)
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        layout.addStretch(1)

    # ── Wiring from the Config tree ─────────────────────────────────────

    def set_target_file(self, path: Optional[Path]) -> None:
        self._path = path
        set_file_combo_selection(self.target_file_combo, path)

    def _on_target_file_combo_changed(self, index: int) -> None:
        path = self.target_file_combo.itemData(index)
        if path is not None:
            self.set_target_file(path)

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — the Point combo is
        sourced from the WHOLE include graph (a Point routinely lives in a
        different file than the thermal_via_arrays entry referencing it),
        same reasoning/pattern as RuleDock's/PlacerDock's own set_root_path."""
        self._root_path = path
        refresh_file_combo_choices((self.target_file_combo,), path, (self._path,))
        self._refresh_point_names()
        self._refresh_sheet_names()

    def _refresh_point_names(self) -> None:
        names = collect_all_point_names(self._root_path) if self._root_path is not None else []
        self.origin_widget.set_point_names(names)

    def _refresh_sheet_names(self) -> None:
        """Sheet-name autocomplete for the array's own anchor Sheet field —
        from the project's schematic files (RuntimeContext.sheet_names),
        refreshed on root-file change like the Point names (see
        collect_all_sheet_names, gui/docks/rename.py)."""
        names = collect_all_sheet_names(self._root_path) if self._root_path is not None else []
        self.origin_widget.set_known_sheets(names)

    def refresh_known_roles(self, snapshot) -> None:
        """Same "populate from the live board" pattern as PlacerDock's own
        refresh_known_roles — called by DockHub.push_snapshot at the same
        ~2s poll cadence."""
        roles = sorted({s.role for s in snapshot if s.role})
        clusters = sorted({s.cluster for s in snapshot if s.cluster})
        self.origin_widget.set_known_roles(roles, clusters)

    def refresh_known_nets(self, board) -> None:
        nets = sorted({n.name for n in board.adapter.get_all_nets() if n.name})
        set_combo_items(self.net_edit, nets)

    # ── Message helper ────────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        """Mirror into the Log dock at the level matching `style` — the docks
        no longer have an inline message_label (2026-08-13), the Log dock is
        the single destination."""
        show_message(text, style, logger)

    # ── Building the thermal_via_arrays entry dict (shared by Redraw/Save) ──

    def _parse_float(self, edit: QLineEdit, label: str, default: float) -> Optional[float]:
        text = edit.text().strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            self._show_message(_("{label}: {text!r} is not a number.").format(label=label, text=text), _ERROR_STYLE)
            return None

    def _parse_int(self, edit: QLineEdit, label: str, default: int) -> Optional[int]:
        text = edit.text().strip()
        if not text:
            return default
        try:
            return int(text)
        except ValueError:
            self._show_message(_("{label}: {text!r} is not an integer.").format(label=label, text=text), _ERROR_STYLE)
            return None

    def _build_entry_dict(self) -> Optional[Dict[str, Any]]:
        name = self.name_edit.text().strip()
        if not name:
            self._show_message(_("Name is required."), _ERROR_STYLE)
            return None
        pad = self.pad_edit.text().strip()
        if not pad:
            self._show_message(_("Pad is required."), _ERROR_STYLE)
            return None

        entry: Dict[str, Any] = {"name": name, "pad": pad}

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

        net = self.net_edit.currentText().strip()
        if net:
            entry["net"] = net

        rows = self._parse_int(self.rows_edit, _("Rows"), default=4)
        if rows is None:
            return None
        entry["rows"] = rows
        cols = self._parse_int(self.cols_edit, _("Cols"), default=4)
        if cols is None:
            return None
        entry["cols"] = cols

        margin = self._parse_float(self.margin_edit, _("Margin"), default=0.5)
        if margin is None:
            return None
        entry["margin_mm"] = margin

        entry["pattern"] = self.pattern_combo.currentText()

        drill = self._parse_float(self.drill_edit, _("Drill"), default=0.3)
        if drill is None:
            return None
        entry["drill_mm"] = drill
        diameter = self._parse_float(self.diameter_edit, _("Diameter"), default=0.5)
        if diameter is None:
            return None
        entry["diameter_mm"] = diameter

        if self.retired_checkbox.isChecked():
            entry["retired"] = True
        if self.skip_checkbox.isChecked():
            entry["skip"] = True

        return entry

    # ── Redraw ────────────────────────────────────────────────────────────

    def _on_redraw(self) -> None:
        self._show_message("")
        payload = self._collect_redraw_inputs()
        if payload is None:
            return
        self._start_redraw_op(payload)

    def _collect_redraw_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: read every widget + run every validation that can
        reject the request up front (including loading + mutating the
        target config). Returns a plain-data payload for the worker, or
        None after showing the error."""
        entry = self._build_entry_dict()
        if entry is None:
            return None
        if self._path is None:
            self._show_message(_("Pick a file in the Config tree first."), _ERROR_STYLE)
            return None

        try:
            tva = load_thermal_via_array(entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None

        try:
            if self._path.exists():
                cfg, ctx = load_config(str(self._path))
            else:
                cfg, ctx = Config(), RuntimeContext()
        except (ValidationError, OSError, yaml.YAMLError) as e:
            self._show_message(_("Failed to load file: {error}").format(error=e), _ERROR_STYLE)
            return None

        # Replace-by-name: previewing an already-saved entry's edits must
        # not create a second copy alongside the saved one.
        cfg.thermal_via_arrays = [t for t in cfg.thermal_via_arrays if t.name != tva.name]
        cfg.thermal_via_arrays.append(tva)

        return {"path": self._path, "cfg": cfg, "ctx": ctx, "name": tva.name}

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
            logger.exception("Thermal via array redraw failed")
            return {"error": _("Placement failed: {error}").format(error=e)}
        return {"name": payload["name"]}

    def _finish_redraw(self, result: Dict[str, Any]) -> None:
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        self._show_message(_("Placed {name!r}.").format(name=result["name"]), _SUCCESS_STYLE)

    def _start_redraw_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._main_window.connection, (self.redraw_button, self.save_button),
            self._run_redraw, self._finish_redraw, self._on_redraw_failed, payload)

    def _on_redraw_failed(self, message: str) -> None:
        self._show_message(_("Placement failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_redraw(self) -> None:
        """Synchronous composition of collect + run + finish — for tests
        and any caller that must not return until the redraw is complete."""
        payload = self._collect_redraw_inputs()
        if payload is None:
            return
        result = self._run_redraw(payload)
        self._finish_redraw(result)

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        entry = self._build_entry_dict()
        if entry is None:
            return
        if self._path is None:
            self._show_message(_("Pick a file in the Config tree first."), _ERROR_STYLE)
            return
        try:
            load_thermal_via_array(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return

        try:
            overwritten = upsert_list_entry(self._path, "thermal_via_arrays", entry)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        self._show_message(
            _("{action} {name!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                name=entry["name"], path=display_path(self._path)),
            _SUCCESS_STYLE)
        self.saved.emit()

    # ── Starting a brand new entry (ConfigTreeDock's Add thermal via pad) ───

    def new_thermal_via(self, path: Path) -> None:
        """Resets the form to its initial (blank) state and targets path —
        ConfigTreeDock's "Add thermal via pad" context-menu action
        (2026-08-03) opens this form empty rather than writing a raw stub
        straight to YAML, same reasoning as PlacerDock.new_placement()."""
        self._path = path
        self.name_edit.setText("")
        self.origin_widget.clear()
        self.pad_edit.setText("")
        self.net_edit.setCurrentText("GND")
        self.rows_edit.setText("")
        self.cols_edit.setText("")
        self.margin_edit.setText("")
        self.pattern_combo.setCurrentIndex(0)
        self.drill_edit.setText("")
        self.diameter_edit.setText("")
        self.retired_checkbox.setChecked(False)
        self.skip_checkbox.setChecked(False)
        self._show_message("")

    # ── Loading an already-saved entry back into the form ───────────────────

    def load_entry(self, entry: Dict[str, Any]) -> None:
        """Reverse of _build_entry_dict() — called by ConfigTreeDock's
        Thermal via arrays category (via thermal_via_picked) when an
        already-saved entry is clicked, same shape as PlacerDock.load_placement."""
        self._show_message("")
        self.name_edit.setText(str(entry.get("name", "")))
        self.pad_edit.setText(str(entry.get("pad", "")))
        self.net_edit.setCurrentText(str(entry.get("net", "GND")))

        if "anchor_point" in entry:
            self.origin_widget.load(mode="point", point=str(entry["anchor_point"]))
        else:
            self.origin_widget.load(
                mode="anchor", ref=str(entry.get("anchor_ref", "")),
                role=str(entry.get("anchor_role", "")),
                sheet=str(entry.get("anchor_sheet", "")),
                cluster=str(entry.get("anchor_cluster", "")))

        self.rows_edit.setText(str(entry.get("rows", 4)))
        self.cols_edit.setText(str(entry.get("cols", 4)))
        self.margin_edit.setText(str(entry.get("margin_mm", 0.5)))
        self.pattern_combo.setCurrentText(entry.get("pattern", "grid"))
        self.drill_edit.setText(str(entry.get("drill_mm", 0.3)))
        self.diameter_edit.setText(str(entry.get("diameter_mm", 0.5)))
        self.retired_checkbox.setChecked(bool(entry.get("retired", False)))
        self.skip_checkbox.setChecked(bool(entry.get("skip", False)))
