# gui/docks/imprint_place.py
"""
ImprintPlaceFormWidget — Config-side QView "Place Imprint..." (P6,
plan_2026_09_05_scheme_list.md §6, design §6).

This is the *placement* side of the Imprint feature (the Config side of
the same record the read-only ImprintFormWidget + Reread in
gui/docks/imprint.py shows). It turns ONE recorded ``imprints:``
snapshot into a NEW imprint-based Entity plus a placement node in an
EXISTING tree (design §6 — "указывает, не копирует": the Entity only carries
``imprint:``/``sheet:``, never a copy of the geometry; a placement node
then says WHERE the snapshot's ``pivot`` lands
(design_2026_09_07_scheme_list_pivot.md — the node's rotation turns the whole
region around that pivot)).

The page is deliberately a plain QWidget Config right-QView page (the same
shape as ImprintFormWidget / NetTraceDock / Placer), built once by DockHub
and registered through ConfigTreeDock.add_right_page — NOT a modal dialog and
NOT a third tab of "Instantiate from Cell..." (Denis's anti-pattern §9.1, see
plan §6). The user picks:

  * the Imprint to place (imprint_combo — from cfg.imprints);
  * an optional target sheet (sheet_combo — blank / the record's source_sheet
    == mode "in place"; any other live sheet == twin target, design §5.2);
  * the EXISTING tree to append the node to (tree_combo — from cfg.trees;
    generated tree_instances trees are read-only and excluded) and the parent
    node inside it (parent_combo — DFS over the tree's nodes + a top-level
    sentinel; decision 1 — the node NEVER creates a new tree);
  * the node offset (x_spin/y_spin — TreeNode.xy semantics: offset relative
    to the chosen parent, or to the tree anchor for a top-level node;
    from_selection_check is an opt-in hint that fills them from the live
    board selection, design decision 4);
  * the node rotation (rotation_edit — a real QLineEdit like TreesDock's Node
    form; written ONTO the node at creation, decision 5);
  * the name of the NEW Entity (name_edit — non-empty and unique against
    cfg.entities).

"Place" then writes BOTH records through the Stage-1 core:
``upsert_entity(path, build_imprint_entity(...))`` +
``append_tree_child_node(path, tree_name, parent_ref, node_dict)`` — `path`
being the physical file that OWNS the chosen tree (resolved via
find_list_entry_file, config_writer stays core/Qt-free) — and emits
``saved`` for DockHub to refresh the Config tree + TreesDock.

The synchronous ``_do_place()`` test hook (mirror of ImprintFormWidget's
``_do_reread``) runs the whole write path without any dialog, so Stage 5's
GUI tests can drive validation/placement headlessly.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QVBoxLayout, QWidget)

from kicadstamp.config import load_config
from kicadstamp.config_writer import append_tree_child_node, upsert_entity
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ..connection import UiThreadBoardReadRefused, ui_thread_board_read
from ..worker import defer_while_socket_busy, socket_busy
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, set_combo_items, show_message)
from .rename import find_list_entry_file
from .imprint import snapshot_with_resolved_sheets
from .tree_from_selection import (
    build_imprint_entity,
    selected_center_mm,
)

logger = logging.getLogger(__name__)

# Combo choices that make no sense as a live place target (they carry no
# reachable record / are regenerated on every load) are excluded up front so a
# wrong pick never fails deep inside link_trees.
_TOP_LEVEL = _("— top level (no parent) —")

# ONE wording for "the shared socket is busy", used on BOTH channels (the Log line
# and the message box) — the same shape trees_dock's _REHANG_BUSY_TEXT uses after
# Т2-4б: it names the cause AND the outcome (the offset was not filled, the
# checkbox was turned off), so the action never ends as a tick that did nothing.
_BOARD_BUSY_TEXT = _(
    "The board is busy right now (a selection tick or a long operation holds the "
    "shared KiCad socket) — the X/Y offset was NOT filled from the selection. "
    "“Take from selection” was turned off; try again in a moment.")


def collect_parent_candidates(tree) -> List[tuple[Optional[str], str]]:
    """[(parent_ref | None, display)] for the parent_combo of one Tree: the
    top-level sentinel first (None — the node lands in ``tree.nodes``), then
    EVERY node of the tree, DFS, parent-before-child, indented by depth.

    A node is identified by its ``ref`` (link_trees guarantees a ref appears
    in at most one node of the whole config, so it is unambiguous here too);
    ``name`` is only a display decoration when it differs from the ref.
    Pure/Qt-free — callable from tests and from the combo rebuild."""
    out: List[tuple[Optional[str], str]] = [(None, _TOP_LEVEL)]

    def walk(nodes: list, depth: int) -> None:
        for n in nodes or []:
            label = "  " * depth + str(n.ref)
            if n.name and n.name != n.ref:
                label += f" ({n.name})"
            out.append((n.ref, label))
            walk(n.children, depth + 1)

    walk(tree.nodes if tree is not None else [], 0)
    return out


def placement_node_payload(entity_name: str, x_mm: float, y_mm: float,
                           rotation_deg: float) -> Dict[str, Any]:
    """The trees: node dict that PLACES the new imprint Entity — the raw
    dict-node shape config_writer.append_tree_child_node expects
    (ref/kind/xy/rotation, see Stage-1 tests' _placement_node). ``rotation``
    is written at creation (decision 5) — even a 0.0 is explicit here; the
    sexp serializer strips the default 0.0 on the round-trip. Pure/Qt-free."""
    node: Dict[str, Any] = {"ref": entity_name, "kind": "placement",
                            "xy": [x_mm, y_mm], "rotation": float(rotation_deg)}
    return node


def _twin_sibling_sheet_names(snapshot: list) -> List[str]:
    """Top-level sheet names that are REAL twins on the live board (2+ board
    instances sharing the same sub-sheet structure) — the ONLY valid
    ``entity.sheet`` targets for the onto-sibling twin resolve:
    kicadstamp/imprint_apply.py::_resolve_onto_sibling matches
    ``entity.sheet`` against exactly this set (built there via its own
    ``_twin_sheet_uuids`` + ``_name_to_twin_uuid`` over a LIVE
    ``adapter.get_footprints()``) — mirrored HERE so the Target-sheet combo
    never offers a sheet Apply would reject as "not a twin on the board".

    The twin rule is the one already battle-tested in
    channel_copy.py::_channel_sheet_uuids / imprint_apply.py::
    _twin_sheet_uuids: group every footprint by its inner_key = path[1:] of
    the sheet_path_uuids chain; a path[0] (top-level) sheet is a twin only
    when it is a member of a 2+ member group. Single-instance sheets
    (FPGA/Power/MCU-style) never qualify, and DAC/OpAmp-style SUB-sheets never
    appear at all — the result is top-level names only (the Place page places
    the whole recorded region as one rigid body onto a channel twin; there is
    no sub-sheet selectivity at Place time, design_2026_09_07_imprint_
    pivot.md).

    Computed from the CACHED, already sheet_names-resolved snapshot
    (Selected.fp.sheet_path_uuids gives the uuid chain — the same object
    channel_copy.build_channel_groups reads from the live footprint;
    Selected.sheet[0] gives the resolved top-level NAME), NEVER a fresh
    adapter call — a direct adapter.get_footprints()/build_channel_groups
    (adapter) from the GUI thread would race the background poll on the
    shared kipy REQ socket exactly like the pivot hang Commit H fixed
    (plan_2026_09_08_scheme_list_place_target_sheet_twin_filter.md §3).
    Qt-free: callable straight from a unit test."""
    groups: dict[str, dict[str, str]] = {}
    for s in snapshot:
        chain = list(getattr(getattr(s, "fp", None), "sheet_path_uuids", None) or ())
        if len(chain) < 2:
            continue
        inner = "/" + "/".join(chain[1:])
        groups.setdefault(inner, {})[chain[0]] = getattr(s, "ref", "")
    twin_uuids = {uuid for members in groups.values() if len(members) >= 2
                  for uuid in members}
    names: dict[str, str] = {}
    for s in snapshot:
        chain = list(getattr(getattr(s, "fp", None), "sheet_path_uuids", None) or ())
        sheet_path = getattr(s, "sheet", None) or ()
        if not chain or chain[0] not in twin_uuids or not sheet_path or not sheet_path[0]:
            continue
        names.setdefault(sheet_path[0], chain[0])
    return sorted(names)


class ImprintPlaceFormWidget(QWidget):
    """Config right-QView "Place Imprint..." page (P6, plan §6.1). See
    the module docstring for the flow and the fixed decisions (existing tree,
    never a new one; offset == TreeNode.xy; rotation set at creation; Entity
    carries only imprint/sheet)."""

    # Fired after a successful Place wrote the Entity + node — DockHub
    # refreshes the Config tree + TreesDock on it (Stage 3).
    saved = pyqtSignal()

    def __init__(self, main_window, connection=None):
        super().__init__(main_window)
        self._main_window = main_window
        self._connection = connection if connection is not None else main_window.connection
        self._root_path: Optional[Path] = None
        self._cfg = None
        self._ctx = None
        # The opt-in "from selection" hint reads the live-board selection the
        # DockHub pushes here via set_board_selection (Stage 3 wiring).
        self._selection: list = []
        # Imprint currently chosen (for the sheet-combo candidates) — the
        # combo's own source_sheet when a record is selected.
        self._selected_source_sheet: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        self.title_label = QLabel(_("Place Imprint"))
        self.title_label.setWordWrap(True)
        root.addWidget(self.title_label)

        form = QFormLayout()

        self.imprint_combo = QComboBox()
        configure_searchable(self.imprint_combo)
        self.imprint_combo.currentTextChanged.connect(
            lambda _t: self._on_imprint_changed())
        form.addRow(_("Imprint:"), self.imprint_combo)

        self.sheet_combo = QComboBox()
        configure_searchable(self.sheet_combo)
        self.sheet_combo.setToolTip(
            _("Leave empty to place on the sheet the Imprint was recorded "
              "from (in place); pick another sheet to place onto its twin."))
        form.addRow(_("Target sheet:"), self.sheet_combo)

        self.tree_combo = QComboBox()
        configure_searchable(self.tree_combo)
        self.tree_combo.currentTextChanged.connect(
            lambda _t: self._rebuild_parent_combo())
        form.addRow(_("Tree:"), self.tree_combo)

        self.parent_combo = QComboBox()
        self.parent_combo.setToolTip(_("Top level = relative to the tree anchor."))
        form.addRow(_("Parent node:"), self.parent_combo)

        root.addLayout(form)

        self.from_selection_check = QCheckBox(
            _("Take from selection (center of the selected group)"))
        self.from_selection_check.setChecked(False)  # opt-in, never auto-assume
        self.from_selection_check.toggled.connect(self._on_from_selection_toggled)
        root.addWidget(self.from_selection_check)

        pos_form = QFormLayout()
        self.x_spin = QDoubleSpinBox()
        self.x_spin.setRange(-10000.0, 10000.0)
        self.x_spin.setDecimals(3)
        self.y_spin = QDoubleSpinBox()
        self.y_spin.setRange(-10000.0, 10000.0)
        self.y_spin.setDecimals(3)
        pos_form.addRow(_("X offset (mm):"), self.x_spin)
        pos_form.addRow(_("Y offset (mm):"), self.y_spin)
        root.addLayout(pos_form)

        entity_form = QFormLayout()
        self.rotation_edit = QLineEdit()
        self.rotation_edit.setPlaceholderText("0")
        entity_form.addRow(_("Rotation (deg):"), self.rotation_edit)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("new Entity name (must be unique)"))
        entity_form.addRow(_("Entity name:"), self.name_edit)
        root.addLayout(entity_form)

        note = QLabel(
            _("Place adds a tree Entity that references the Imprint by "
              "name — it never copies the recorded geometry and never "
              "touches the live board until you Redraw the tree node."))
        note.setWordWrap(True)
        root.addWidget(note)

        buttons = QHBoxLayout()
        self.place_button = QPushButton(_("Place"))
        self.place_button.clicked.connect(self._on_place_clicked)
        buttons.addWidget(self.place_button)
        buttons.addStretch(1)
        root.addLayout(buttons)
        root.addStretch(1)

    # ── Message helper ──────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(text, style, logger)

    # ── Root / cfg wiring ───────────────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """DockHub root_changed slot — store the new root, reload the cfg for
        the cfg-derived combos and refresh them."""
        self._root_path = path
        self.refresh()

    def preset_imprint(self, name: str) -> None:
        """Preselect an Imprint record by name (the Config-tree context
        menu's "Place..." and the Tools delegate prefill the form with the
        record the user right-clicked / selected — Stage 3). No-op when the
        root/cfg is not loaded yet or the name is unknown (the caller shows
        the page first via set_root_path, so this is normally a live pick)."""
        if not name or self._cfg is None:
            return
        idx = self.imprint_combo.findText(name)
        if idx >= 0:
            self.imprint_combo.setCurrentIndex(idx)
            self._on_imprint_changed()

    def refresh(self) -> None:
        """Public — re-read the config at the current root (if any) and
        refresh the cfg-derived combos (imprints, trees, parent list),
        preserving current selections where they still exist. Called on
        root change and by DockHub after a graph change (Stage 3)."""
        self._cfg = None
        self._ctx = None
        if self._root_path is None:
            self._refresh_cfg_combos([])
            return
        try:
            self._cfg, self._ctx = load_config(str(self._root_path))
        except (ValidationError, OSError) as e:
            self._show_message(
                _("Failed to load the config: {error}").format(error=e),
                _ERROR_STYLE)
            self._refresh_cfg_combos([])
            return
        self._refresh_cfg_combos(list(self._cfg.imprints or []))
        self._on_imprint_changed()

    def _refresh_cfg_combos(self, imprints) -> None:
        """Repopulate imprint_combo + tree_combo from cfg (preserving the
        current selections where possible). The parent combo is rebuilt for
        the (possibly unchanged) tree selection."""
        scheme_names = [getattr(sl, "name", "") for sl in imprints if getattr(sl, "name", "")]
        if scheme_names:
            set_combo_items(self.imprint_combo, scheme_names)
            # A stale selection from a previous root must not silently point at
            # a record that no longer exists — blank it (placeholder shows).
            if self.imprint_combo.currentText().strip() not in scheme_names:
                self.imprint_combo.clearEditText()
        else:
            self.imprint_combo.clear()
            self.imprint_combo.setPlaceholderText(
                _("no Imprints recorded — use Tools → Imprints → Record..."))

        tree_names = self._placeable_tree_names()
        if tree_names:
            set_combo_items(self.tree_combo, tree_names)
            if self.tree_combo.currentText().strip() not in tree_names:
                self.tree_combo.clearEditText()
        else:
            self.tree_combo.clear()
            self.tree_combo.setPlaceholderText(
                _("no editable trees in this config"))
        self._rebuild_parent_combo()

    def _placeable_tree_names(self) -> List[str]:
        """Names of cfg.trees a manual node may be appended to — every
        hand-written tree, minus the read-only materialized tree_instances
        (they are regenerated on every load, so a manual child would be
        silently lost; TreesDock marks the same set read-only)."""
        cfg = self._cfg
        if cfg is None:
            return []
        generated = {ti.name for ti in (cfg.tree_instances or [])
                     if getattr(ti, "name", None)}
        return [t.name for t in (cfg.trees or []) if t.name not in generated]

    def _selected_tree(self):
        """The cfg.trees Tree object whose name is currently in tree_combo, or
        None when there is no cfg / no selection."""
        cfg = self._cfg
        if cfg is None:
            return None
        name = self.tree_combo.currentText().strip()
        for t in (cfg.trees or []):
            if t.name == name:
                return t
        return None

    def _rebuild_parent_combo(self) -> None:
        """DFS of the chosen tree (top-level sentinel first) into
        parent_combo, preserving the current selection where it still exists."""
        current = self.parent_combo.currentData()
        self.parent_combo.blockSignals(True)
        self.parent_combo.clear()
        tree = self._selected_tree()
        for ref, label in collect_parent_candidates(tree):
            self.parent_combo.addItem(label, ref)
        if current is not None:
            idx = self.parent_combo.findData(current)
            if idx >= 0:
                self.parent_combo.setCurrentIndex(idx)
        elif self.parent_combo.count():
            self.parent_combo.setCurrentIndex(0)
        self.parent_combo.blockSignals(False)

    def _on_imprint_changed(self) -> None:
        """Record selection changed — refresh the target-sheet candidates
        from the record's source_sheet + the live board's REAL twin
        top-level sheets (module-level _twin_sibling_sheet_names)."""
        self._selected_source_sheet = self._record_source_sheet(
            self.imprint_combo.currentText().strip())
        self._rebuild_sheet_combo()

    def _record_source_sheet(self, name: str) -> Optional[str]:
        """The source_sheet of the named imprint in the loaded cfg (the
        sheet the snapshot was recorded from), or None when unknown."""
        cfg = self._cfg
        if cfg is None or not name:
            return None
        for sl in (cfg.imprints or []):
            if getattr(sl, "name", "") == name:
                return getattr(sl, "source_sheet", None)
        return None

    def _rebuild_sheet_combo(self) -> None:
        """Target-sheet choices: blank (in place) first, then the record's own
        source_sheet and every REAL twin top-level sheet on the live board
        (via the module-level _twin_sibling_sheet_names — computed from the
        CACHED, re-resolved snapshot, never a fresh adapter call). Picking a
        value equal to source_sheet still means in place (design §5.2 p2), so
        only a genuinely different top-level value is written to the Entity.
        connection may be a fake without a snapshot attribute (tests) —
        getattr-guarded."""
        current = self.sheet_combo.currentText()
        source = self._selected_source_sheet or ""
        raw_snapshot = getattr(self._connection, "snapshot", None) or []
        # The raw board snapshot's own sheet_names is always {} (Board.connect()
        # never passes schematic_dir, gui/connection.py) — re-resolve against
        # the CONFIG's sheet_names (self._ctx, already loaded by refresh())
        # first, the same fix Record/Re-source already applies
        # (snapshot_with_resolved_sheets, plan_2026_09_07_imprint_sheet_
        # names_empty.md), or every .sheet segment is None and no twin is
        # ever offered (Denis' repro, plan_2026_09_08_imprint_place_target_
        # sheet_unresolved.md).
        sheet_names = dict(getattr(self._ctx, "sheet_names", {}) or {})
        resolved = snapshot_with_resolved_sheets(raw_snapshot, sheet_names)
        twins = _twin_sibling_sheet_names(resolved)
        source_top = source.split("/", 1)[0] if source else ""
        candidates: List[str] = []
        for value in ([source] if source else []) + twins:
            if not value or value in candidates:
                continue
            # The source's OWN top-level sheet is not re-offered as a bare
            # twin target: it carries the same "in place" semantics as the
            # blank/source option (design §5.2 p2), so selecting it would only
            # duplicate that choice — and _collect_payload would write it as
            # entity.sheet, i.e. an onto-sibling place against the source
            # channel itself. The source stays reachable (its full recorded
            # path) as the FIRST candidate above.
            if value != source and value == source_top:
                continue
            candidates.append(value)
        choices = [""] + candidates
        set_combo_items(self.sheet_combo, choices)
        # Blank (in place) is the default after a rebuild; a previously chosen
        # sheet survives only when it is still among the candidates.
        if current not in choices:
            self.sheet_combo.setCurrentIndex(0)

    # ── Board-selection hook (from-selection hint) ──────────────────────

    def set_board_selection(self, raw_items, selected_footprints) -> None:
        """Called by DockHub on every selection tick (Stage 3 wiring) — the
        opt-in "from selection" hint reads the current board selection."""
        self._selection = list(selected_footprints or [])

    def _live_adapter(self):
        board = getattr(self._connection, "board", None)
        return getattr(board, "adapter", None) if board is not None else None

    def _on_from_selection_toggled(self, checked: bool) -> None:
        """Opt-in hint (design decision 4): when checked, try to fill x/y from
        the live board (center of the current selection minus the chosen
        parent's live base). On any failure show a warning and fall back to
        manual entry (never a silent partial write).

        The read costs board time, so it ASKS THE SOCKET FIRST (door rule 3; Т2-7
        of plan_2026_09_22_door_t2_7_imprint_place): one deferred retry through
        `defer_while_socket_busy` while another owner holds the shared kipy REQ
        socket, and the continuation checks `socket_busy` once more one line
        before the read, because a tick can start inside the 120 ms delay. The
        ~400 ms selection tick holds that socket 16.4 % of a run (measured,
        trees_dock.py), so a refusal that said nothing would eat roughly every
        sixth click — the silence Т2-4б cleared on the re-hang path."""
        if not checked:
            return
        defer_while_socket_busy(self._connection, (),
                                self._fill_offset_from_selection,
                                self._tell_board_busy, owner=self)

    def _fill_offset_from_selection(self) -> None:
        """The continuation: read the offset and write the spin boxes.

        This is the SECOND legitimate shape of `defer_while_socket_busy`'s own
        contract (gui/worker.py): the continuation is short, synchronous and
        guards the socket itself one line before the read, so the helper
        contributes the deferral and the liveness check. A continuation that
        neither starts a worker nor checks the socket itself must not be passed
        there — and this one keeps that promise below."""
        connection = self._connection
        if socket_busy(connection):
            # The race window: the poll tick (or a long operation) took the socket
            # between the helper's own check and this line. The SAME refusal the
            # message box shows — returning silently here would eat exactly the
            # click the deferral was added to save (Т2-4б: the last attempt never
            # whispers).
            logger.warning(_BOARD_BUSY_TEXT)
            self._tell_board_busy()
            return
        # The door's sign (Т2-7). The read is deliberate and stays on the UI
        # thread; the table of leaves says WHY that is affordable and WHAT it does
        # not cover (diagnostics/probe_2026_09_22_imprint_place_leaf_cost.py,
        # 332-footprint board):
        #   * identity comes from the connection's cached snapshot (up to 5 s old
        #     by default — В31) and the position from ONE live footprint walk, so
        #     the role leaves (a role tree anchor, a chain node parent) go from a
        #     whole-board sweep — 1 get_footprints + 332 get_field_value, the
        #     ~110 ms cache-miss class Ш1 measured — to at most one cache walk,
        #     i.e. 0 IPC round trips while the poll's own cache is warm;
        #   * NAMED RESIDUAL, not covered here: a self / point / clone /
        #     coordinate-anchor leaf has no snapshot parameter to reach
        #     (entity_placement._entity_own_zero_slot_live_position,
        #     point_resolver.resolve_point_chain, ClonePositionCalculator,
        #     coordinate_position_calculator._resolve_external_anchor), so such a
        #     parent still pays the same 1 + 332 round trips on the UI thread. It
        #     is NOT moved to a worker: a checkbox must not cost a 120 ms deferral
        #     plus a socket of its own to close (Кn) for a sweep the user's own
        #     config asks for — the trade trees_dock's re-hang sign names for its
        #     own sub-seams.
        with ui_thread_board_read(
                reason="fill the node X/Y offset from the live board selection on "
                       "the checkbox tick — identity from the connection's cached "
                       "snapshot (up to 5 s old), position from one live "
                       "footprint walk instead of the whole-board 1 + 332 "
                       "exchange sweep (diagnostics/probe_2026_09_22_imprint_"
                       "place_leaf_cost.py) — NAMED as NOT covered: a self / "
                       "point / clone / coordinate-anchor parent still sweeps, "
                       "because those sub-seams take no snapshot"):
            offset_mm = self._read_from_selection_offset()
        if offset_mm is None:
            QMessageBox.warning(
                self, _("Take from selection"),
                _("Cannot derive the node offset from the selection — enter "
                  "the X/Y offset manually."))
            self.from_selection_check.setChecked(False)
            return
        self.x_spin.setValue(offset_mm[0])
        self.y_spin.setValue(offset_mm[1])

    def _tell_board_busy(self) -> None:
        """The user is TOLD when the socket is busy — on the deferred retry's last
        attempt, or in the race window — and the checkbox is turned OFF, so the
        action leaves a visible trace instead of a tick that did nothing
        (`defer_while_socket_busy`'s on_still_busy half, the Т2-4б idiom)."""
        QMessageBox.warning(self, _("Take from selection"), _BOARD_BUSY_TEXT)
        self.from_selection_check.setChecked(False)

    def _read_from_selection_offset(self) -> Optional[tuple[float, float]]:
        """(x_offset_mm, y_offset_mm) from the CURRENT board selection center
        minus the chosen parent's live base (tree anchor for top level), or
        None when the live prerequisites are missing (no adapter / no cfg /
        no selection / unresolvable base). Best-effort — a failure is a
        warning, never a crash.

        The DOOR'S refusal is NOT one of those failures (Т2-7): it is re-raised,
        never folded into None, because `None` is answered with "Cannot derive
        the node offset from the selection — enter the X/Y offset manually" and a
        door violation is not the selection's fault. That is the same false
        message Т2-4's mutation m9 found on the re-hang path. Be honest about the
        clause's reach: in production the guard runs in "log" mode
        (gui/connection.py UI_READ_LOG) and never raises at all, so this is a
        TEST-RIG guarantee — its value is that the false modal becomes impossible
        BY CONSTRUCTION, so a read added above the sign later cannot quietly
        become one.

        The `try` covers the WHOLE read, `self._live_adapter()` included: that is
        what gives the clause something to guard. The first version of the cell
        below proved the opposite — the door read stood ABOVE the `try`, so it
        escaped the `except Exception` no matter which clauses came after it, and
        the mutation that removes the clause stayed green."""
        connection = self._connection
        cfg = self._cfg
        if cfg is None:
            return None
        center = selected_center_mm(self._selection)
        if center is None:
            return None
        tree = self._selected_tree()
        if tree is None:
            return None
        try:
            adapter = self._live_adapter()
            if adapter is None:
                return None
            base = self._live_parent_base_mm(
                adapter, cfg, tree, snapshot=getattr(connection, "snapshot", None))
        except UiThreadBoardReadRefused:
            # The door's own refusal — never "the selection did not resolve".
            raise
        except Exception:  # noqa: BLE001 — live read, best-effort
            return None
        if base is None:
            return None
        return (center[0] - base[0], center[1] - base[1])

    def _live_parent_base_mm(self, adapter, cfg, tree, *,
                             snapshot=None) -> Optional[tuple[float, float]]:
        """(x_mm, y_mm) live base of the chosen parent — the tree anchor for a
        top-level node, the parent node's own record otherwise (same
        parent-base semantics as TreesDock's "Read current position"; the
        offset is measured from THIS base). None when unresolvable.

        `snapshot` (Т2-7): the connection's cached `List[Selected]`, forwarded to
        BOTH branches so the IDENTITY question ("which footprint carries this
        role / this cluster") is answered in memory instead of sweeping the
        board. The POSITION still comes from the adapter's current generation —
        `Selected.fp` is frozen at poll time (В31). Default None = the historical
        sweep, byte for byte, which is what apply/CLI/MCP keep getting.

        A NAMED NOTE on the node branch (§2(в) of the Т2-7 task): it calls
        `resolve_base_live_position` DIRECTLY, not `read_record_live_pose` the way
        TreesDock's base resolve does (`_resolve_node_base_pose`). For a parent
        that IS an Entity the two therefore answer DIFFERENT questions —
        `resolve_base_live_position` derives the position from the TREE that
        places the Entity, `read_record_live_pose` reads that Entity's LIVE
        CLUSTER — and they diverge the moment the cluster was moved in KiCad
        after the last redraw (measured: probe row D, (0,0) against (5,0) for a
        5 mm move). That divergence is a FINDING for the plan; it is NOT repaired
        here, silently or otherwise."""
        from kicadstamp.tree_position import (
            _anchor_base_live_position,
            resolve_base_live_position,
        )
        from kicadstamp.utils.units import MM

        sheet_names = dict(getattr(self._ctx, "sheet_names", {}) or {})
        parent_ref = self.parent_combo.currentData()
        if parent_ref is None:
            pos, _rot = _anchor_base_live_position(adapter, cfg, tree, sheet_names,
                                                   snapshot=snapshot)
            return (pos.x / MM, pos.y / MM)
        # Parent is an existing node: resolve its own record (same rules as a
        # real tree node — external refs resolve directly, records via the
        # kind dispatch of resolve_base_live_position).
        from .trees_dock import _resolve_probe_ref
        parent_node = self._find_node_by_ref(tree, parent_ref)
        if parent_node is None:
            return None
        record, _is_external = _resolve_probe_ref(cfg, parent_node.ref,
                                                  parent_node.kind)
        pos = resolve_base_live_position(adapter, cfg, parent_node.ref, record,
                                         {}, sheet_names, snapshot=snapshot)
        return (pos.x / MM, pos.y / MM)

    def _find_node_by_ref(self, tree, ref: str):
        """DFS the Tree for the node with `ref` (top-level or nested), or None
        — parent_combo selections must resolve to a real node of the tree."""
        def walk(nodes):
            for n in nodes or []:
                if n.ref == ref:
                    return n
                hit = walk(n.children)
                if hit is not None:
                    return hit
            return None
        return walk(tree.nodes if tree is not None else [])

    # ── The public action ───────────────────────────────────────────────

    def place(self) -> bool:
        """Public entry point (form button + DockHub delegate in Stage 3):
        validate the form, run the Place write, report the result and emit
        saved on success. Returns True when a Place actually happened."""
        problems = self.validate()
        if problems:
            QMessageBox.warning(self, self.windowTitle() or _("Place Imprint"),
                                problems[0])
            return False
        result = self._do_place()
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return False
        self._show_message(
            _("Placed Imprint {scheme!r} as Entity {entity!r} under tree "
              "{tree!r}.").format(scheme=result.get("scheme", "?"),
                                  entity=result.get("entity", "?"),
                                  tree=result.get("tree", "?")),
            _SUCCESS_STYLE)
        self.saved.emit()
        return True

    def _on_place_clicked(self) -> None:
        self.place()

    def validate(self) -> List[str]:
        """Localized problems blocking a Place — empty means the form is ready.
        Stage 2 validation set: an Imprint is chosen; a tree is chosen; a
        parent is chosen (top level is a valid choice); the Entity name is
        non-empty and unique against cfg.entities. Imprint / tree combos
        are editable+searchable, so a free-typed value must also name a real
        record (a nonexistent reference would be fatal at the next load)."""
        problems: List[str] = []
        cfg = self._cfg
        scheme = self.imprint_combo.currentText().strip()
        if not scheme:
            problems.append(_("Select an Imprint to place."))
        elif cfg is not None and not any(
                getattr(sl, "name", "") == scheme for sl in (cfg.imprints or [])):
            problems.append(_("Unknown Imprint {name!r}.").format(name=scheme))
        tree_name = self.tree_combo.currentText().strip()
        if not tree_name:
            problems.append(_("Pick a tree to place into."))
        elif cfg is not None and tree_name not in self._placeable_tree_names():
            problems.append(_("Unknown tree {name!r}.").format(name=tree_name))
        # parent_combo always has a selection (top-level sentinel or a node).
        entity_name = self.name_edit.text().strip()
        if not entity_name:
            problems.append(_("Entity name is required."))
        else:
            if cfg is not None and any(e.name == entity_name for e in cfg.entities):
                problems.append(
                    _("An entity named {name!r} already exists.").format(name=entity_name))
        text = self.rotation_edit.text().strip()
        if text:
            try:
                float(text)
            except ValueError:
                problems.append(_("Rotation must be a number."))
        return problems

    # ── Synchronous write path (mirror of ImprintFormWidget._do_reread) ──

    def _collect_payload(self) -> Optional[Dict[str, Any]]:
        """Snapshot the plain-data payload for the Place write, or None when a
        hard precondition (no root / no cfg) is missing (the message is
        logged — a dialog is the caller's job)."""
        if self._root_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return None
        if self._cfg is None:
            self._show_message(_("Failed to load the config."), _ERROR_STYLE)
            return None
        scheme = self.imprint_combo.currentText().strip()
        tree_name = self.tree_combo.currentText().strip()
        parent_ref = self.parent_combo.currentData()  # None == top level
        entity_name = self.name_edit.text().strip()
        source = self._selected_source_sheet or ""
        sheet_text = self.sheet_combo.currentText().strip()
        sheet = None if (not sheet_text or sheet_text == source) else sheet_text
        rotation = float(self.rotation_edit.text().strip() or "0.0")
        return {
            "root": str(self._root_path),
            "scheme": scheme,
            "sheet": sheet,
            "tree": tree_name,
            "parent_ref": parent_ref,
            "entity": entity_name,
            "x_mm": self.x_spin.value(),
            "y_mm": self.y_spin.value(),
            "rotation": rotation,
        }

    def _do_place(self) -> Dict[str, Any]:
        """Synchronous Place (no dialogs, for tests): validate, then build
        the Entity dict + the placement node dict, resolve the file that OWNS
        the chosen tree, write both (upsert_entity + append_tree_child_node —
        the Stage-1 core) and return a result dict. {"error": ...} on any
        failure (including validation); {"ok": ...} on success. The caller
        (place()/tests) owns saved.emit()."""
        problems = self.validate()
        if problems:
            return {"error": problems[0]}
        payload = self._collect_payload()
        if payload is None:
            return {"error": _("Place failed — check the log.")}
        path = find_list_entry_file(Path(payload["root"]), "trees",
                                    {"name": payload["tree"]})
        if path is None:
            return {"error": _("Tree {name!r} not found in the config graph.")
                    .format(name=payload["tree"])}
        try:
            entity = build_imprint_entity(
                payload["entity"], payload["scheme"], payload["sheet"])
            upsert_entity(Path(path), entity)
            node = placement_node_payload(
                payload["entity"], payload["x_mm"], payload["y_mm"],
                payload["rotation"])
            append_tree_child_node(Path(path), payload["tree"],
                                   payload["parent_ref"], node)
        except (OSError, ValidationError) as e:
            return {"error": _("Place failed: {error}").format(error=e)}
        return {"ok": True,
                "entity": payload["entity"],
                "scheme": payload["scheme"],
                "sheet": payload["sheet"],
                "tree": payload["tree"],
                "parent_ref": payload["parent_ref"],
                "path": str(path)}
