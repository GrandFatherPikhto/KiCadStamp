# gui/docks/chain.py
"""
ChainDock — edits a `chains:` entry (kicadstamp/config/models.py's Chain +
ManualSpoke): one shared anchor (Ref/Role, narrowed by Sheet/Cluster, or a
named Point) plus an ordered list of spokes, each placing a Cell at a
specific pad of that anchor with its own hand-tuned shift/rotation.
Requested live 2026-08-05 after Denis connected fpga_spokes.yaml/
fpga_cap_pair_spoke.yaml to a real project and hit the long-flagged gap
(config_tree.py's own module docstring) that Rules had no edit form at
all.

2026-09-01 (plan rules_to_chains): this module was RuleDock/gui/docks/rules.py;
2026-09-05 (design config_qview_chain_entity_pages): ChainDock is now a page of
the Config dock's right QView (no dialog wrapper), and the pads are leaves in
the Config tree (category -> anchor -> chain -> pad) whose single click opens
the pad (spoke) editor here; a chain's double click opens chain mode, and the
Config chains-nav QView page offers an anchor -> chains -> pads drill. The
pads table / Move up-down / Add-Update-Remove buttons / Bulk-set Cell button
are all gone from this form (Redraw chain/spoke and Bulk set Cell moved to the
Config tree's context menu; a pad is edited via the pad mode below).

TWO MODES, one QStackedWidget (the plan's "QStackedWidget на два режима"):
  - chain mode: Net/Name/Comment + Origin (AnchorOriginWidget, "Read current
    position") + Retired/Skip — the old Net/Origin tabs;
  - pad mode: Pad/Cell/Cluster/Mode(Cartesian/Polar)/Shift(X,Y)/
    Radius+Angle/Rotation/Retired/Skip — the old Spoke tab, editing ONE pad.
  - load_chain(entry) / new_chain(path) switch to chain mode;
    load_pad(chain, index) / new_pad(chain) switch to pad mode.

Public entry points (all called by DockHub from the tree's double clicks /
context menu / Tools menu):
  - load_chain(entry, file_path=None) — fill chain mode from a saved entry;
  - load_pad(chain_entry, pad_index, file_path=None) — fill pad mode from one
    spoke of a saved chain (remembering the parent chain for the write);
  - new_chain(file_path) / new_pad(chain_entry, file_path) — blank forms for
    the Add paths;
  - _persist_chain(...) / _persist_pad(...) — the two validating writes
    (load_chain + upsert_list_entry), each emitting `saved` only when the
    tree's display can actually change (see each method's docstring);
  - redraw_chain(chain_dict) / redraw_pad(chain_dict, pad_index) — the
    worker-thread ApplyPipeline runs, driven from the tree's context menu;
  - bulk_set_cell(net) — the BulkSetCellDialog + apply, driven from the
    tree's context menu / Tools menu.

spoke.cell is a searchable combo (Denis: "Чтобы назначать разные целлы
разным спицам? Да, думаю комбобоксик") sourced from collect_all_cell_names()
(gui/docks/rename.py) — EVERY cells: key reachable from the project's root
via include:, not just this file's own. Both the Cell and Point (pad mode has
no Point — spokes anchor to a pad on THIS chain's own anchor) combos are
populated from the whole graph via the root path, wired through
set_root_path() to RootMetadataDock's root_changed (same second file
dependency as before).

Save writes via upsert_list_entry(key_fn=...) matching by chain_effective_name
(name if set, else net) — chains: is the one list section without a REQUIRED
name: field (see config/models.py's chain_effective_name), unlike
clone_placements:/thermal_via_arrays: which always require one.

Errors/fatals go to the Log dock (never silent) — same as the old RuleDock.
`saved` refreshes the Config tree; the editor stays open as the Config QView
page (the pad page carries its own Apply/Redraw actions).
"""
import dataclasses
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from kipy.errors import ApiError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                              QPushButton, QStackedWidget, QVBoxLayout,
                              QWidget)

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import (Chain, Config, RuntimeContext, load_config,
                               load_chain, load_manual_spoke,
                               chain_effective_name)
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.i18n import _
from kicadstamp.utils.units import MM

from ..worker import start_long_op
from ._anchor_origin import AnchorOriginWidget
from .live_position import read_anchor_live
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, display_path, parse_float_field,
                      set_combo_items, set_mode_pair_enabled, show_message,
                      upsert_list_entry)
from .rename import (collect_all_cell_names, collect_all_chain_nets,
                     collect_all_point_names, collect_all_sheet_names,
                     collect_chains_by_net, find_list_entry_file)

logger = logging.getLogger(__name__)


def _chain_identity(entry: Dict[str, Any]) -> Any:
    """upsert_list_entry's key_fn — mirrors chain_effective_name() at the
    raw-dict level (Save hasn't necessarily built a Chain object yet)."""
    return entry.get("name") or entry.get("net")


class BulkSetCellDialog(QDialog):
    """Stage 3 (2026-08-20, plan rule_spoke_fixes): pick a net + a new cell
    and PREVIEW the exact chains/spokes it will change before writing anything
    (a net's chains routinely live in DIFFERENT included files — see
    collect_chains_by_net). Pure selection + preview, read-only: the write
    itself happens in ChainDock._apply_bulk_cell_set, which reports per-chain
    success/failure to the Log dock (never a silent partial write)."""

    def __init__(self, root_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Bulk-set Cell for net"))
        self._root_path = root_path
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.net_combo = QComboBox()
        configure_searchable(self.net_combo)
        set_combo_items(self.net_combo, collect_all_chain_nets(root_path))
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
        affected = collect_chains_by_net(self._root_path, net)
        if not affected:
            self.preview_label.setText(_("No chains on net {net!r}.").format(net=net))
            return
        total_spokes = sum(len(chain.get("spokes") or []) for _, chain in affected)
        lines = [
            _("{name} in {file} (pads: {pads})").format(
                name=_chain_identity(chain), file=path.name,
                pads=", ".join(str(s.get("pad", "?")) for s in (chain.get("spokes") or [])))
            for path, chain in affected
        ]
        self.preview_label.setText(
            _("Will set cell {cell!r} on {n} chain(s) / {m} spoke(s) of net {net!r}:\n{lines}")
            .format(cell=self.cell_combo.currentText().strip(), n=len(affected),
                    m=total_spokes, net=net, lines="\n".join(lines)))


class ChainDock(QWidget):
    """Edits a single chain (chain mode) or one of its pads (pad mode). Hosted
    as a page in the Config dock's right QView (2026-09-05, design
    config_qview_chain_entity_pages)."""

    # Fired after a successful write that can change the tree's display —
    # ConfigTreeDock refreshes its Chains category (see gui/dock_hub.py).
    saved = pyqtSignal()

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self._active_op: Optional[Any] = None
        self._path: Optional[Path] = None
        self._root_path: Optional[Path] = None
        # Pad-mode state: the parent chain dict + the pad index being edited
        # (None in chain mode, or for a brand-new pad being appended).
        self._chain_entry: Optional[Dict[str, Any]] = None
        self._pad_index: Optional[int] = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # ── Chain mode (Net/Name/Comment + Origin + Retired/Skip) ─────────
        self._chain_page = QWidget()
        chain_layout = QVBoxLayout(self._chain_page)
        chain_form = QFormLayout()
        self.net_edit = QComboBox()
        configure_searchable(self.net_edit)
        chain_form.addRow(_("Net:"), self.net_edit)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("optional — defaults to net for --only"))
        chain_form.addRow(_("Name:"), self.name_edit)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText(_("optional free-form note"))
        chain_form.addRow(_("Comment:"), self.comment_edit)
        chain_layout.addLayout(chain_form)

        self.origin_widget = AnchorOriginWidget(modes=["anchor", "point"], anchor_fields=["sheet", "cluster"])
        chain_layout.addWidget(self.origin_widget)
        # Aliases onto the shared widget's own sub-widgets — kept so existing
        # tests/call sites that poke fields directly keep working.
        self.origin_mode_combo = self.origin_widget.origin_mode_combo
        self.anchor_ref_edit = self.origin_widget.anchor_ref_edit
        self.anchor_role_edit = self.origin_widget.anchor_role_edit
        self.anchor_sheet_edit = self.origin_widget.anchor_sheet_edit
        self.anchor_cluster_edit = self.origin_widget.anchor_cluster_edit
        self.point_edit = self.origin_widget.point_edit

        origin_readout_row = QHBoxLayout()
        self.read_position_button = QPushButton(_("Read current position"))
        self.read_position_button.clicked.connect(self._on_origin_read_position)
        origin_readout_row.addWidget(self.read_position_button)
        chain_layout.addLayout(origin_readout_row)
        self.anchor_position_label = QLabel("")
        self.anchor_position_label.setWordWrap(True)
        chain_layout.addWidget(self.anchor_position_label)

        checks_row = QHBoxLayout()
        self.retired_checkbox = QCheckBox(_("Retired"))
        self.skip_checkbox = QCheckBox(_("Skip"))
        checks_row.addWidget(self.retired_checkbox)
        checks_row.addWidget(self.skip_checkbox)
        chain_layout.addLayout(checks_row)
        chain_layout.addStretch(1)
        self._stack.addWidget(self._chain_page)

        # ── Pad mode (one spoke's fields) ─────────────────────────────────
        self._pad_page = QWidget()
        pad_layout = QVBoxLayout(self._pad_page)
        pad_form = QFormLayout()
        self.spoke_pad_edit = QLineEdit()
        self.spoke_pad_edit.setPlaceholderText(_("pad number on the chain's own anchor"))
        pad_form.addRow(_("Pad:"), self.spoke_pad_edit)
        self.spoke_cell_combo = QComboBox()
        configure_searchable(self.spoke_cell_combo)
        pad_form.addRow(_("Cell:"), self.spoke_cell_combo)
        self.spoke_cluster_combo = QComboBox()
        configure_searchable(self.spoke_cluster_combo)
        pad_form.addRow(_("Cluster:"), self.spoke_cluster_combo)
        # Position mode — Cartesian shift (default) vs Polar radius+angle.
        self.spoke_mode_combo = QComboBox()
        self.spoke_mode_combo.addItems([_("Cartesian"), _("Polar")])
        self.spoke_mode_combo.currentIndexChanged.connect(self._update_spoke_mode)
        pad_form.addRow(_("Mode:"), self.spoke_mode_combo)
        pad_layout.addLayout(pad_form)

        spoke_shift_row = QHBoxLayout()
        self.spoke_shift_x_edit = QLineEdit()
        self.spoke_shift_x_edit.setPlaceholderText(_("shift X mm (0)"))
        self.spoke_shift_y_edit = QLineEdit()
        self.spoke_shift_y_edit.setPlaceholderText(_("shift Y mm (0)"))
        spoke_shift_row.addWidget(QLabel(_("Shift X:")))
        spoke_shift_row.addWidget(self.spoke_shift_x_edit)
        spoke_shift_row.addWidget(QLabel(_("Shift Y:")))
        spoke_shift_row.addWidget(self.spoke_shift_y_edit)
        pad_layout.addLayout(spoke_shift_row)

        spoke_polar_row = QHBoxLayout()
        self.spoke_radius_edit = QLineEdit()
        self.spoke_radius_edit.setPlaceholderText(_("radius mm"))
        self.spoke_angle_edit = QLineEdit()
        self.spoke_angle_edit.setPlaceholderText(_("angle deg"))
        spoke_polar_row.addWidget(QLabel(_("Radius:")))
        spoke_polar_row.addWidget(self.spoke_radius_edit)
        spoke_polar_row.addWidget(QLabel(_("Angle:")))
        spoke_polar_row.addWidget(self.spoke_angle_edit)
        pad_layout.addLayout(spoke_polar_row)
        self._update_spoke_mode()

        pad_extra_form = QFormLayout()
        self.spoke_rotation_edit = QLineEdit()
        self.spoke_rotation_edit.setPlaceholderText("0")
        pad_extra_form.addRow(_("Rotation (deg):"), self.spoke_rotation_edit)
        pad_layout.addLayout(pad_extra_form)

        spoke_checks_row = QHBoxLayout()
        self.spoke_retired_checkbox = QCheckBox(_("Retired"))
        self.spoke_skip_checkbox = QCheckBox(_("Skip"))
        spoke_checks_row.addWidget(self.spoke_retired_checkbox)
        spoke_checks_row.addWidget(self.spoke_skip_checkbox)
        pad_layout.addLayout(spoke_checks_row)
        pad_layout.addStretch(1)

        # Explicit pad actions (2026-09-05, design config_qview_chain_entity_
        # pages §4) — the pad editor is now a Config right-QView page: "Apply"
        # commits the form into the config (working set), "Redraw" applies the
        # CURRENT form to the board (Placer-style, no file write). Redraw is
        # disabled for a brand-new unsaved pad (nothing to isolate yet).
        pad_actions_row = QHBoxLayout()
        self.pad_apply_button = QPushButton(_("Apply"))
        self.pad_apply_button.clicked.connect(self._on_save_pad)
        self.pad_redraw_button = QPushButton(_("Redraw"))
        self.pad_redraw_button.clicked.connect(self.redraw_pad_form)
        # Blank initial state: no saved spoke to isolate yet — only an existing
        # pad load enables it (see load_pad / _clear_pad_editor).
        self.pad_redraw_button.setEnabled(False)
        pad_actions_row.addWidget(self.pad_apply_button)
        pad_actions_row.addWidget(self.pad_redraw_button)
        pad_layout.addLayout(pad_actions_row)

        self._stack.addWidget(self._pad_page)

        # Auto-stage the chain's OWN fields (2026-09-01, plan project_save_model)
        # — see _autostage. _loading guards population.
        self.net_edit.activated.connect(self._autostage)
        net_line = self.net_edit.lineEdit()
        if net_line is not None:
            net_line.editingFinished.connect(self._autostage)
        self.name_edit.editingFinished.connect(self._autostage)
        self.comment_edit.editingFinished.connect(self._autostage)
        self.retired_checkbox.toggled.connect(self._autostage)
        self.skip_checkbox.toggled.connect(self._autostage)
        for w in self.origin_widget.findChildren(QLineEdit):
            w.editingFinished.connect(self._autostage)
        for w in self.origin_widget.findChildren(QComboBox):
            w.currentIndexChanged.connect(self._autostage)

    # ── Mode switching ───────────────────────────────────────────────────

    def _show_chain_mode(self) -> None:
        self._stack.setCurrentWidget(self._chain_page)

    def _show_pad_mode(self) -> None:
        self._stack.setCurrentWidget(self._pad_page)

    # ── Wiring from DockHub ─────────────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — new chains: entries are
        always written to the project root file (2026-08-21, plan
        flatten_and_single_file_gui), so the write target IS the root. The
        Cell/Sheet/Point combos stay sourced from the WHOLE include graph."""
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
        """Sheet-name autocomplete for the chain's own anchor Sheet field —
        from the project's schematic files (RuntimeContext.sheet_names),
        refreshed on root-file change like the Cell/Point names."""
        names = collect_all_sheet_names(self._root_path) if self._root_path is not None else []
        self.origin_widget.set_known_sheets(names)

    def _on_origin_read_position(self) -> None:
        """Chain mode's "Read current position" — an INFORMATIONAL readout of
        the chain anchor's live position/rotation (design
        2026_08_29_config_tree_read_live_position.md §1.4). The chain itself
        has no position field in the config (its position = anchor + per-spoke
        shifts), so nothing is filled into the config — only a label is shown,
        and the resolution doubles as an ambiguity check."""
        board = self._main_window.connection.board
        if board is None or getattr(board, "adapter", None) is None:
            QMessageBox.warning(
                self, _("Read current position"),
                _("No live board connection — connect KiCad first."))
            return
        fields, err = self.origin_widget.build()
        if err:
            QMessageBox.warning(self, _("Read current position"), err)
            return
        config_path = self._root_path if self._root_path is not None else self._path
        cfg, ctx = Config(), RuntimeContext()
        if config_path is not None and config_path.exists():
            try:
                cfg, ctx = load_config(str(config_path))
            except (ValidationError, OSError) as e:
                QMessageBox.warning(self, _("Read current position"), str(e))
                return
        sheet_names = ctx.sheet_names if ctx is not None else {}
        try:
            read = read_anchor_live(board.adapter, fields, cfg.points,
                                    sheet_names, _("chain anchor"))
        except ValidationError as e:
            QMessageBox.warning(self, _("Read current position"), str(e))
            return
        rot_s = f"{read.rotation_deg:.1f}" if read.rotation_deg is not None else "—"
        ref = fields.get("ref") or fields.get("role") or fields.get("point") or "?"
        self.anchor_position_label.setText(
            _("anchor {ref!r}: ({x:.3f}, {y:.3f}) mm @ {rot}°").format(
                ref=ref, x=read.position.x / MM, y=read.position.y / MM, rot=rot_s))

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

    def _update_spoke_mode(self) -> None:
        """Same Cartesian/Polar field-toggle as coordinate_placer's
        _update_row_mode() — Shift fields only in Cartesian, Radius/Angle
        only in Polar. Disabled (not hidden) keeps the editor layout stable."""
        set_mode_pair_enabled(
            self.spoke_mode_combo.currentIndex() == 1,
            (self.spoke_shift_x_edit, self.spoke_shift_y_edit),
            (self.spoke_radius_edit, self.spoke_angle_edit),
        )

    def _clear_pad_editor(self) -> None:
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
        # A blank pad form has no saved spoke to isolate on the board yet —
        # "Redraw" stays disabled until an existing pad loads (2026-09-05).
        self.pad_redraw_button.setEnabled(False)

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

    # ── Building the Chain entry dict (shared by Save/Redraw) ─────────────

    def _build_chain_dict(self) -> Optional[Dict[str, Any]]:
        net = self.net_edit.currentText().strip()
        if not net:
            self._show_message(_("Net is required."), _ERROR_STYLE)
            return None

        entry: Dict[str, Any] = {"net": net}
        if self._chain_entry is not None:
            entry["spokes"] = list(self._chain_entry.get("spokes") or [])
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

    # ── Persist (chain mode) ──────────────────────────────────────────────

    def _persist_chain(self, context: str, notify_tree: bool = False) -> None:
        """Build the current chain from the chain-mode form, validate it, and
        write it to the target file (upsert_list_entry). On ANY failure report
        it to the Log dock and return — a failed autosave must be visible,
        never silent. `saved` fires only when the tree's display can actually
        change: notify_tree (a name/net change), or a write that CREATED a
        brand-new chain (upsert returned "wrote", not "overwrote"). A plain
        value tweak on an already-saved chain is deliberately silent to the
        tree (config_tree_dock.refresh() clears and rebuilds the whole tree)."""
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        entry = self._build_chain_dict()
        if entry is None:
            return  # _build_chain_dict already reported the specific error
        try:
            load_chain(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return
        try:
            overwritten = upsert_list_entry(self._path, "chains", entry, key_fn=_chain_identity)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return
        suffix = _("{action} {name!r} in {path}").format(
            action=_("Overwrote") if overwritten else _("Wrote"),
            name=_chain_identity(entry), path=display_path(self._path))
        self._show_message(f"{context} {suffix}".strip(), _SUCCESS_STYLE)
        # Deliberately does NOT update self._chain_entry: chain-mode edits only
        # touch the chain's OWN fields (Net/Name/Origin/Retired/Skip), and its
        # pads come from the loaded entry (_chain_entry) — which a chain-field
        # save leaves unchanged, so the next _build_chain_dict still preserves
        # them. load_chain/new_chain/load_pad/new_pad own _chain_entry.
        if notify_tree or not overwritten:
            self.saved.emit()

    # ── Persist (pad mode) ────────────────────────────────────────────────

    def _persist_pad(self, context: str) -> None:
        """Build the current pad from the pad-mode form, splice it into the
        remembered parent chain's spokes: (replace at self._pad_index, or
        append when adding a brand-new pad), and rewrite the WHOLE chain via
        upsert_list_entry — a pad is not a standalone record, so editing one
        rewrites its parent chain (plan rules_to_chains §4). The write target
        is the file the parent chain actually lives in (find_list_entry_file),
        not necessarily the root. `saved` fires because the tree's pads are
        leaves of this chain."""
        if self._chain_entry is None or self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        spoke = self._build_spoke_dict()
        if spoke is None:
            return  # error already reported
        chain = dict(self._chain_entry)
        spokes = list(chain.get("spokes") or [])
        if self._pad_index is None:
            spokes.append(spoke)
        elif 0 <= self._pad_index < len(spokes):
            spokes[self._pad_index] = spoke
        else:
            spokes.append(spoke)
        chain["spokes"] = spokes
        try:
            load_chain(chain)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return
        try:
            upsert_list_entry(self._path, "chains", chain, key_fn=_chain_identity)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return
        self._chain_entry = chain
        self._show_message(
            _("{context} {name!r} in {path}").format(
                context=context or _("Pad saved"), name=_chain_identity(chain),
                path=display_path(self._path)),
            _SUCCESS_STYLE)
        self.saved.emit()

    # ── Auto-stage (chain mode, 2026-09-01 plan project_save_model) ───────

    def _autostage(self) -> None:
        """Chain-field commit point -> persist the whole chain. Skips
        population (_loading) and incomplete chains (no net yet). Wrapped in
        try/except: an unhandled exception in a PyQt6 signal slot aborts the
        whole process, so a staging bug must degrade to a log line, never a
        crash."""
        try:
            if self._loading or self._path is None:
                return
            if not self.net_edit.currentText().strip():
                return
            self._persist_chain("", notify_tree=True)
        except Exception:
            logger.exception("chain auto-stage failed")

    def _on_save(self) -> None:
        """Kept for tests and programmatic callers — the chain's OWN
        Net/Origin/retired/skip fields. Always notifies the tree, since a
        name/net change alters what it shows."""
        self._persist_chain("", notify_tree=True)

    def _on_save_pad(self) -> None:
        """Kept for tests and programmatic callers — persists the pad-mode
        form (see _persist_pad)."""
        self._persist_pad(_("Pad saved:"))

    # ── Redraw (driven from the Config tree's context menu / Tools menu) ───

    def _collect_redraw_payload(self, chains: List["Chain"]) -> Optional[Dict[str, Any]]:
        """Build the ApplyPipeline payload from ONE OR MORE loaded Chains —
        the tree's context menu / Tools menu pass the chain dicts; this loads
        and splices them into the config by identity (replace-by-name, so
        previewing already-saved chains' edits never creates second copies).
        `names` is the ordered list of effective names the pipeline's --only
        filter targets (a net redraw = its one chain, an anchor redraw = all
        chains under it, a spoke redraw = its one chain with the others'
        spokes skipped)."""
        config_path = self._root_path if self._root_path is not None else self._path
        if config_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return None
        try:
            if config_path.exists():
                cfg, ctx = load_config(str(config_path))
            else:
                cfg, ctx = Config(), RuntimeContext()
        except (ValidationError, OSError) as e:
            self._show_message(_("Failed to load file: {error}").format(error=e), _ERROR_STYLE)
            return None

        cfg = dataclasses.replace(cfg)  # graph cache is shared; don't mutate it
        names: List[str] = []
        for chain in chains:
            effective = chain_effective_name(chain)
            names.append(effective)
            cfg.chains = [c for c in cfg.chains if chain_effective_name(c) != effective]
            cfg.chains.append(chain)

        return {"path": config_path, "cfg": cfg, "ctx": ctx, "names": names}

    def redraw_chain(self, chain_dict: Dict[str, Any]) -> None:
        """Redraw the whole chain (all non-skipped spokes) — the Config tree's
        "Redraw chain" context action (chain_redraw_requested)."""
        self._show_message("")
        try:
            chain = load_chain(chain_dict)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return
        payload = self._collect_redraw_payload([chain])
        if payload is None:
            return
        self._start_redraw_op(payload)

    def redraw_pad(self, chain_dict: Dict[str, Any], pad_index: int) -> None:
        """Redraw ONE pad of a chain — the Config tree's "Redraw spoke"
        context action (pad_redraw_requested). Every OTHER spoke gets a
        temporary skip=True injected into the copy handed to ApplyPipeline
        (never written back) — safe because spoke resolution shares ONE
        ComponentPool per net across the whole chain."""
        self._show_message("")
        try:
            chain = load_chain(chain_dict)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return
        spokes = [dataclasses.replace(s, skip=(i != pad_index))
                  for i, s in enumerate(chain.spokes)]
        chain = dataclasses.replace(chain, spokes=spokes)
        payload = self._collect_redraw_payload([chain])
        if payload is None:
            return
        self._start_redraw_op(payload)

    def redraw_pad_form(self) -> None:
        """QView pad-page "Redraw" (2026-09-05, design
        config_qview_chain_entity_pages §4) — apply the CURRENT pad-mode form
        to the board, isolating exactly this spoke (every other spoke of the
        parent chain gets a temporary skip). Unlike `_persist_pad` this does
        NOT write the config: the pipeline is fed from the form content
        (Placer-style, see PlacerDock._collect_redraw_inputs), so the user can
        tune and re-redraw without committing. Guarded to an already-saved
        spoke (the button is disabled while a brand-new pad is unsaved)."""
        if self._chain_entry is None or self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        spoke = self._build_spoke_dict()
        if spoke is None:
            return  # error already reported
        if self._pad_index is None:
            return  # brand-new pad — no saved spoke to isolate (button disabled)
        chain = dict(self._chain_entry)
        spokes = list(chain.get("spokes") or [])
        if not (0 <= self._pad_index < len(spokes)):
            return
        spokes[self._pad_index] = spoke
        chain["spokes"] = spokes
        self.redraw_pad(chain, self._pad_index)

    def redraw_chains(self, chain_dicts: List[Dict[str, Any]]) -> None:
        """Redraw SEVERAL chains in one ApplyPipeline run — the Tools menu's
        "Redraw" on an ANCHOR node redraws every chain under that anchor
        (Denis, 2026-09-01: "если корневой компонент, то вообще все его
        спицы"). One run, not N sequential ones — the shared kipy socket must
        stay single-in-flight, and a burst of chains in the same net pool
        resolves together."""
        self._show_message("")
        chains: List["Chain"] = []
        for chain_dict in chain_dicts:
            try:
                chains.append(load_chain(chain_dict))
            except ValidationError as e:
                self._show_message(str(e), _ERROR_STYLE)
                return
        if not chains:
            self._show_message(_("Nothing to redraw."), _ERROR_STYLE)
            return
        payload = self._collect_redraw_payload(chains)
        if payload is None:
            return
        self._start_redraw_op(payload)

    def _run_redraw(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: ApplyPipeline run only — never touches a widget."""
        pipeline = ApplyPipeline(config_path=str(payload["path"]),
                                 preloaded_cfg=payload["cfg"], preloaded_ctx=payload["ctx"],
                                 only=payload["names"], dry_run=False)
        try:
            pipeline.run()
        except (PlacerError, ValidationError, ApiError) as e:
            return {"error": _("Placement failed: {error}").format(error=e)}
        except Exception as e:
            logger.exception("Chain redraw failed")
            return {"error": _("Placement failed: {error}").format(error=e)}
        return {"names": payload["names"]}

    def _finish_redraw(self, result: Dict[str, Any]) -> None:
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        names = ", ".join(result.get("names", []))
        self._show_message(_("Placed {name!r}.").format(name=names), _SUCCESS_STYLE)

    def _start_redraw_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._main_window.connection,
            (),  # no buttons to disable anymore — the form is a dialog
            self._run_redraw, self._finish_redraw, self._on_redraw_failed, payload)

    def _on_redraw_failed(self, message: str) -> None:
        self._show_message(_("Placement failed: {error}").format(error=message), _ERROR_STYLE)

    # ── Bulk-set Cell for net (driven from the tree's context menu) ────────

    def bulk_set_cell(self, net_hint: Optional[str] = None) -> None:
        """'Bulk set Cell for net...' — opens the preview dialog
        (BulkSetCellDialog) and applies the chosen cell to every chain on the
        chosen net (see _apply_bulk_cell_set). Driven from the Config tree's
        Chains category / chain context menu (bulk_set_cell_requested) and the
        Tools menu."""
        if self._root_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        dlg = BulkSetCellDialog(self._root_path, self)
        if net_hint:
            dlg.net_combo.setCurrentText(net_hint)
            dlg._refresh_preview()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        net = dlg.net_combo.currentText().strip()
        cell = dlg.cell_combo.currentText().strip()
        if not net or not cell:
            self._show_message(_("Bulk-set Cell needs both a net and a cell."), _ERROR_STYLE)
            return
        self._apply_bulk_cell_set(net, cell)

    def _apply_bulk_cell_set(self, net: str, cell: str) -> None:
        """Set spoke.cell = cell for EVERY chain on `net` across the whole
        include: graph, one upsert_list_entry per chain (chains routinely live
        in different included files — collect_chains_by_net walks them all).
        Partial failure is reported EXPLICITLY — never a silent half-applied
        change."""
        if self._root_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        affected = collect_chains_by_net(self._root_path, net)
        if not affected:
            self._show_message(_("No chains on net {net!r}.").format(net=net), _ERROR_STYLE)
            return
        ok: List[str] = []
        failed: List[str] = []
        for path, chain in affected:
            modified = dict(chain)
            modified["spokes"] = [{**s, "cell": cell} for s in (chain.get("spokes") or [])]
            name = _chain_identity(modified)
            try:
                load_chain(modified)  # validate before writing, same as _on_save
            except ValidationError as e:
                failed.append(_("{name} in {file}: {err}")
                              .format(name=name, file=display_path(path), err=e))
                continue
            try:
                upsert_list_entry(path, "chains", modified, key_fn=_chain_identity)
                ok.append(_("{name} in {file}").format(name=name, file=display_path(path)))
            except OSError as e:
                failed.append(_("{name} in {file}: {err}")
                              .format(name=name, file=display_path(path), err=e))
        if failed:
            self._show_message(
                _("Bulk-set Cell: wrote {n_ok} chain(s); FAILED {n_failed} — {failed}")
                .format(n_ok=len(ok), n_failed=len(failed), failed="; ".join(failed)),
                _ERROR_STYLE)
        else:
            self._show_message(
                _("Bulk-set Cell: wrote {n} chain(s) on net {net!r}.").format(n=len(ok), net=net),
                _SUCCESS_STYLE)
        self._reload_if_bulk_affected(net)

    def _reload_if_bulk_affected(self, net: str) -> None:
        """After a bulk cell set rewrote the files, the dock's in-memory
        editor for the currently-loaded chain (if it is on the affected net)
        is stale — re-read its entry from disk and reload the form so shown +
        saved state match the bulk result."""
        if self._root_path is None or self.net_edit.currentText().strip() != net:
            return
        loaded_identity = _chain_identity(
            {"net": net, "name": self.name_edit.text().strip() or None})
        for _file, chain in collect_chains_by_net(self._root_path, net):
            if isinstance(chain, dict) and _chain_identity(chain) == loaded_identity:
                self.load_chain(chain)
                return

    # ── Starting a brand new entry (Add net... / Add spoke...) ─────────────

    def new_chain(self, path: Path) -> None:
        """Resets the chain-mode form to its blank state — ConfigTreeDock's
        "Add chain..." / Tools menu's "Add net..." opens this form empty. The
        entry is written to the project root file (2026-08-21), so the passed
        path is ignored."""
        self._loading = True
        try:
            self._path = self._root_path
            self._chain_entry = None
            self._pad_index = None
            self.net_edit.setCurrentText("")
            self.name_edit.setText("")
            self.comment_edit.setText("")
            self.origin_widget.clear()
            self.retired_checkbox.setChecked(False)
            self.skip_checkbox.setChecked(False)
            self._show_chain_mode()
        finally:
            self._loading = False
        self._show_message("")

    def new_pad(self, chain_entry: Dict[str, Any], path: Path) -> None:
        """Resets the pad-mode form to its blank state for a BRAND-NEW pad
        appended to `chain_entry` — ConfigTreeDock's "Add spoke..." / Tools
        menu's "Add spoke..." (add_pad_requested). Remembers the parent chain
        so _persist_pad can append the new spoke to it."""
        self._loading = True
        try:
            self._path = self._root_path
            self._chain_entry = chain_entry
            self._pad_index = None  # append
            self._clear_pad_editor()
            self._show_pad_mode()
        finally:
            self._loading = False
        self._show_message("")

    # ── Loading an already-saved entry back into the form ──────────────────

    def load_chain(self, entry: Dict[str, Any], file_path: Optional[Path] = None) -> None:
        """Reverse of _build_chain_dict() — called by DockHub when the Config
        tree double-clicks a chain node (chain_edit_requested). chains: is a
        list section, so the payload is already the full dict. The WRITE
        target is set back to the file the chain actually lives in, so a Save
        updates that file instead of adding a root duplicate."""
        self._show_message("")
        if file_path is None:
            file_path = find_list_entry_file(self._root_path, "chains", entry)
        if file_path is not None:
            self._path = file_path
        self._chain_entry = dict(entry)
        self._pad_index = None
        self._loading = True
        try:
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
            self._show_chain_mode()
        finally:
            self._loading = False

    def load_pad(self, chain_entry: Dict[str, Any], pad_index: int,
                 file_path: Optional[Path] = None) -> None:
        """Fill the pad-mode form from ONE spoke of a saved chain — called by
        DockHub when the Config tree double-clicks a pad leaf
        (pad_edit_requested). Remembers the parent chain + pad index so
        _persist_pad rewrites the whole chain."""
        self._show_message("")
        if file_path is None:
            file_path = find_list_entry_file(self._root_path, "chains", chain_entry)
        if file_path is not None:
            self._path = file_path
        self._chain_entry = dict(chain_entry)
        self._pad_index = pad_index
        spokes = chain_entry.get("spokes") or []
        spoke = spokes[pad_index] if 0 <= pad_index < len(spokes) else {}
        self._loading = True
        try:
            self.net_edit.setCurrentText(str(chain_entry.get("net", "")))
            self.spoke_pad_edit.setText(str(spoke.get("pad", "")))
            self.spoke_cell_combo.setCurrentText(str(spoke.get("cell", "")))
            is_polar = spoke.get("radius_mm") is not None
            self.spoke_mode_combo.setCurrentIndex(1 if is_polar else 0)
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
            # An existing spoke loads — "Redraw" (isolate this pad from the
            # form) becomes available (2026-09-05).
            self.pad_redraw_button.setEnabled(True)
            self._show_pad_mode()
        finally:
            self._loading = False


# Backward-compat aliases for the 2026-09-01 Rule -> Chain rename (kept so
# external importers/tests referencing the old names keep working during the
# transition; the canonical names are ChainDock/_chain_identity above).
RuleDock = ChainDock
_rule_identity = _chain_identity
