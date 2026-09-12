# gui/dock_hub.py
"""
DockHub — owns every dock in the KiCadStamp main window: construction,
layout (add/tabify onto the owning QMainWindow) and all dock-to-dock signal
wiring. MainWindow keeps ownership of the window and BoardConnection only
(Phase 3.3 of the gui-optimization roadmap) and talks to its docks through
this controller, which is the single place dock coordination grows.

The docks are QDockWidgets parented to the QMainWindow, so Qt owns their
lifetime; DockHub creates/arranges/connects them and holds the references
MainWindow re-exposes as thin forwarding properties — needed for the parts
of the app that still reach a dock directly (notably RoleClusterTreeDock's
lazy fieldstool lookup and the test suite).

Placer/Root/Rules (placer_dock/root_metadata_dock/rules_dock) are the one
exception: 2026-08-03 they were merged into ONE QDockWidget, DetailDock
(gui/docks/detail_panel.py) — its own module docstring covers why (Points/
Rules added 2026-08-05, same shape). Those attributes are kept as aliases
straight into DetailDock's stack pages so every existing call site keeps
working unchanged; they are plain QWidgets now, not QDockWidgets in their own
right. Since 2026-09-05 (design config_qview_chain_entity_pages) the Config
dock is a master-detail and its right QView hosts Placer, NetTrace, the Chain
editor + chains navigation, the Entity page and (same move, QView pages) the
Points and Thermal via editors — DetailDock and the Points/Thermal/Chain
dialog wrappers are gone (a QWidget can only have one parent, so each live
dock is embedded directly as a Config QView page). tools_dock (2026-09-01,
plan plan_2026_09_01_tools_dialog_and_entity_roles.md, "Edit template") and
cells_dock (2026-09-04, plan plan_2026_09_04_celldock_to_dialog.md) are the
remaining standalone widgets hosted in non-modal dialogs (ToolsDialog /
CellDialog). Every dock keeps receiving the selection/snapshot ticks,
set_root_path and saved through the same single live instance.
"""
import logging
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional

from kipy.errors import ApiError
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (QDialog, QMessageBox, QSizePolicy, QTabWidget)

from .docks._common import (ERROR_STYLE as _ERROR_STYLE, display_path,
                            show_message)
from .docks.entity_delete import delete_entry
from .docks.extract_diagnostics import (format_cluster_rejections,
                                        rejections_log_detail)
from .docks.rename import entry_effective_name

from kicadstamp.cli_common import api_error_message, peek_log_file
from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.logging_setup import get_log_listener

from . import overlay_markers
from .docks.cell_dialog import CellDialog
from .docks.cell_anchor_view import (
    CellAnchorView,
    cleanup_all_overlays_sync,
)
from .docks.cell_editor import CellDock
from .docks.chain import ChainDock
from .docks.chains_nav import ChainsNavDock
from .docks.config_tree import ConfigTreeDock
from .docks.entity_page import EntityInfoDock
from .docks.configurator import ConfiguratorDock
from .docks.net_trace import NetTraceDock
from .docks.placer import PlacerDock
from .docks.trees_dock import TreesDock
from .docks.fieldstool_dock import FieldsToolDock
from .docks.log_panel import LogDock
from .docks.pending import PendingChangesDock
from .docks.project_dialog import ProjectDialog
from .docks.points import PointsDock
from .docks.role_cluster_tree import RoleClusterTreeDock
from .docks.root_metadata import RootMetadataDock
from .docks.settings_dialog import SettingsDialog
from .docks.thermal_via import ThermalViaArrayDock
from .docks.tools import ToolsDock
from .docks.tools_dialog import ToolsDialog
from .docks.instances_dialog import TreeInstancesDialog
from .docks.scheme_list import (
    RecordSchemeListDialog,
    SchemeListFormWidget,
    choose_boundary_actions,
    record_refs_for,
    scheme_list_duplicate_problems,
    snapshot_with_resolved_sheets,
    write_scheme_list_record,
)
from .docks.scheme_list_place import SchemeListPlaceFormWidget

logger = logging.getLogger(__name__)


class DockHub:
    """Constructs, lays out and wires every dock of the KiCadStamp main
    window. MainWindow creates one DockHub with its BoardConnection and then
    drives the docks through this controller's delegates."""

    def __init__(self, main_window, connection, verbose: bool = False):
        self.main_window = main_window
        # The root-config log_file: FileHandler currently attached to the
        # root logger, if any — see _on_root_file_changed_for_logging().
        self._log_file_handler: Optional[logging.Handler] = None
        # V.3 (plan_2026_09_11_extract_selection_diagnostics): root paths
        # already checked for the "no schematic_dir -> sheet narrowing is off"
        # Log line — once per root, only with a live board.
        self._sheet_dir_checked: set[str] = set()

        # ── CENTRAL: the Components / Config / Trees tab group ─────────────
        # 2026-09-10 (task T, prompt_2026_09_10_central_widget_layout.md): these
        # three used to be QDockWidgets tabified in the LeftDockWidgetArea. With
        # QMainWindow.centralWidget() == None there was no elastic element in
        # the vertical direction except the bottom dock area, so the Log dock
        # silently absorbed every spare pixel: neither resizeDocks() nor a real
        # separator drag could shrink it, whatever the docks' size policies said
        # (S.1 measured exactly that). They are now the pages of ONE central
        # QTabWidget, which IS the elastic centre.
        #
        # Trade-off: the three are no longer docks — no floating window and no
        # per-dock close. Visually nothing changes: they already lived as one
        # tab group, and the tab bar stays at the bottom.
        #
        # 2026-09-05 (plan components_fieldstool_master_detail) still applies:
        # the shared Pending page and the embedded fieldstool window are built
        # FIRST so RoleClusterTreeDock can host them as its master-detail pages
        # (a QWidget can only have one parent).
        self.pending_dock = PendingChangesDock(main_window)
        self.fieldstool_dock = FieldsToolDock(
            main_window, connection=connection, pending_dock=self.pending_dock)
        self.tree_dock = RoleClusterTreeDock(
            main_window, connection=connection,
            pending_panel=self.pending_dock,
            fieldstool_window=self.fieldstool_dock.window)
        # Hand-authored s-expr "trees" editor (2026-08-27, design
        # design_2026_08_27_trees_gui_dock.md) — tabbed with the Config tree so
        # the user finds "tree" in one place.
        self.config_tree_dock = ConfigTreeDock(main_window)
        self.trees_dock = TreesDock(main_window)
        # S.3.2 (plan_2026_09_11_stale_snapshot_role_lists.md): the tree dock's
        # dialogs read their Role/Cluster candidates lazily from
        # connection.snapshot, so it gets the SAME "rebuild the snapshot, then
        # distribute it" operation the other docks receive through
        # push_snapshot — injected here instead of reached for through
        # main_window._dock_hub (see TreesDock.set_snapshot_refresher).
        self.trees_dock.set_snapshot_refresher(self.refresh_snapshot_and_push)

        # Tab labels at the BOTTOM, matching the tab bar the dock area used
        # (plan_2026_09_04_trees_dock_master_detail.md §4, confirmed with Denis:
        # the whole triple moves, not just the Config/Trees pair). Only the
        # Components dock's INNER tab bar (Components | Pending) sits on TOP of
        # its content (Denis's requirement).
        self.left_tabs = QTabWidget()
        self.left_tabs.setTabPosition(QTabWidget.TabPosition.South)
        self.left_tabs.addTab(self.tree_dock, _("Components"))
        self.left_tabs.addTab(self.config_tree_dock, _("Config"))
        self.left_tabs.addTab(self.trees_dock, _("Trees"))
        # The elastic centre: Expanding so it takes/gives the window's vertical
        # slack, and a minimum height of 1 so the Log dock can be grown past the
        # tabs' content height — the pages inside then scroll (S.2's per-page
        # QScrollArea wraps are what make that useful). 1, not 0: Qt treats an
        # explicit 0 as "unset" (see gui/docks/log_panel.py:157).
        self.left_tabs.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Expanding)
        self.left_tabs.setMinimumHeight(1)
        main_window.setCentralWidget(self.left_tabs)

        # Both live-board writers get an immediate out-of-cycle refresh hook
        # (see MainWindow.request_refresh) — the automatic poll tick never
        # refreshes on its own once already connected, so without this a
        # Stage/Clear all/Delete selected write would sit invisible to
        # Pending changes until the user manually clicked Refresh. getattr,
        # not a direct attribute access — DockHub itself is built (and
        # tested) against any plain QMainWindow, not just the real
        # gui.main_window.MainWindow (see test_phase3_wiring.py's "the
        # composition root works without a real MainWindow too").
        request_refresh = getattr(main_window, "request_refresh", None)
        self.tree_dock.on_board_written = request_refresh
        self.fieldstool_dock.window.on_board_written = request_refresh

        # Placer / NetTrace (2026-09-05, plan config_qview_placer_nettrace):
        # ConfigTreeDock is now a master-detail — the Config tree on the left
        # and a context QStack on the right. PlacerDock and NetTraceDock (the
        # two former DetailDock pages) are built here and embedded as the
        # Config dock's right pages; DetailDock is GONE (removed this day).
        self.placer_dock = PlacerDock(main_window)
        self.net_trace_dock = NetTraceDock(main_window, connection=connection)
        self._placer_page = self.config_tree_dock.add_right_page(self.placer_dock)
        self._net_trace_page = self.config_tree_dock.add_right_page(self.net_trace_dock)
        self._selection_raw_items: list = []
        self._selection_footprints: list = []
        # Thermal via (2026-09-05, design config_qview_chain_entity_pages, the
        # same QView move as Chain/Entity): the single live ThermalViaArrayDock
        # is a Config right-QView page (a single click on a thermal_via_arrays
        # leaf opens it) — a QWidget can only have one parent, so the old
        # ThermalViaDialog wrapper is gone. The same instance keeps receiving
        # the snapshot ticks / set_root_path / saved.
        self.thermal_via_dock = ThermalViaArrayDock(main_window)
        self._thermal_via_page = self.config_tree_dock.add_right_page(self.thermal_via_dock)
        # Scheme List (2026-09-06, plan scheme_list P5, design §3): the same
        # QView move as Thermal via/NetTrace — the single live
        # SchemeListFormWidget is a Config right-QView page (a single click on
        # a scheme_lists leaf opens the record read-only; Reread rewrites it on
        # an explicit Apply). No Placement/Redraw here — that lives in Trees
        # via the Entity(scheme_list:)/Placement machinery (P4/P6).
        self.scheme_list_dock = SchemeListFormWidget(main_window, connection=connection)
        self._scheme_list_page = self.config_tree_dock.add_right_page(self.scheme_list_dock)
        # Scheme List Place (2026-09-06, plan scheme_list §6 / P6 Stage 3): the
        # SEPARATE "Place Scheme List..." QView page (NOT a tab of "Instantiate
        # from Cell..." — Denis's anti-pattern §9.1). It turns ONE recorded
        # snapshot into a NEW scheme_list-based Entity + a placement node in an
        # EXISTING tree. Built once and registered as another Config right page,
        # driven by root_changed (set_root_path -> refresh cfg combos) and the
        # selection ticks (the opt-in "from selection" hint).
        self.scheme_list_place_dock = SchemeListPlaceFormWidget(
            main_window, connection=connection)
        self._scheme_list_place_page = self.config_tree_dock.add_right_page(
            self.scheme_list_place_dock)
        # Held across the Record.../capture worker ops (worker.py keeps its own
        # keep-alive too, but the dock keeps the returned controller for
        # inspection/idempotency, the same shape as the docks' _active_op).
        self._scheme_active_op = None
        # Project (2026-09-01, plan project_settings_dialogs): RootMetadataDock
        # is no longer a Detail dock page — it is hosted in the standalone
        # non-modal ProjectDialog (File > "Project...", see
        # gui/docks/project_dialog.py). The widget keeps its root-changed
        # broadcast / Working-file combobox from inside the dialog; the
        # root_metadata_dock attribute below stays the single source every
        # other dock follows.
        self.root_metadata_dock = RootMetadataDock(main_window)
        self.project_dialog = ProjectDialog(self.root_metadata_dock, main_window)
        # Cell (2026-09-04, plan plan_2026_09_04_celldock_to_dialog.md): the
        # Cell form (CellDock) is a STANDALONE widget hosted in the non-modal
        # CellDialog — the Detail dock has no Cells page anymore (same move as
        # Thermal via/Points/Tools/Chain). The same single live instance keeps
        # receiving the snapshot ticks / set_root_path / saved. The Cells page
        # used to be constructed inside DetailDock and aliased here; it is now
        # built directly, and the cell_edit_requested delegates below open the
        # dialog instead of switching a Detail dock tab.
        self.cells_dock = CellDock(main_window)
        self.cell_dialog = CellDialog(self.cells_dock, main_window)
        # Chain (2026-09-01 plan rules_to_chains -> 2026-09-05 QView move): the
        # single live ChainDock is embedded as a Config right-QView page below —
        # a QWidget can only have one parent, so the old ChainDialog wrapper is
        # gone. The same instance keeps receiving the snapshot ticks /
        # set_root_path / saved. Redraw chain/spoke and Bulk set Cell are driven
        # from the Config tree's context menu.
        self.chain_dock = ChainDock(main_window)
        # Backward-compat alias for the 2026-09-01 Rule -> Chain rename — the
        # old rules_dock name still resolves to the live ChainDock.
        self.rules_dock = self.chain_dock
        # A single click on a chains: pad leaf opens the spoke editor here; Add
        # net/spoke and Edit chain flows show the same page.
        self._chain_page = self.config_tree_dock.add_right_page(self.chain_dock)
        # Entity (2026-09-05, design config_qview_chain_entity_pages §5): the
        # Config right-QView page shown when an Entities leaf is selected — a
        # read-mostly Entity RECORD editor ("Справка": Name/Cell/Sheet/Cluster
        # read-only, Comment editable; plus the clickable placements list).
        self.entity_dock = EntityInfoDock(main_window)
        self._entity_page = self.config_tree_dock.add_right_page(self.entity_dock)
        # Chains navigation (2026-09-05, design config_qview_chain_entity_pages
        # §4/§8.2): a chains: ANCHOR/CHAIN single click shows a clickable drill
        # list (anchor -> chains -> pads) as another Config QView page; the pad
        # rows open the spoke editor (ChainDock page).
        self.chains_nav_dock = ChainsNavDock(main_window)
        self._chains_nav_page = self.config_tree_dock.add_right_page(self.chains_nav_dock)
        # Points (2026-09-05, design config_qview_chain_entity_pages, the same
        # QView move as Chain/Entity): the single live PointsDock is a Config
        # right-QView page (a single click on a points: leaf opens it) — a
        # QWidget can only have one parent, so the old PointsDialog wrapper is
        # gone. The same instance keeps receiving the snapshot ticks /
        # set_root_path / saved. connection is needed for Resolve.
        self.points_dock = PointsDock(main_window, connection=connection)
        self._points_page = self.config_tree_dock.add_right_page(self.points_dock)
        # Cell anchor (2026-09-09, Phase C of plan_2026_09_09_cell_anchor_v2_
        # declarative_and_board_overlay): the dedicated anchor editor
        # (Component/Marker tabs) is a Config right-QView page, opened from the
        # Cells context menu ("Cell anchor..." -> cell_anchor_requested).
        self.cell_anchor_view = CellAnchorView(main_window, connection=connection)
        self._cell_anchor_page = self.config_tree_dock.add_right_page(
            self.cell_anchor_view)
        # Settings (2026-09-01, plan project_settings_dialogs): ConfiguratorDock
        # is no longer a Detail dock page either — it is a two-pane settings
        # browser (QTreeWidget of categories on the left, pages on the right,
        # see gui/docks/configurator.py) hosted in the MODAL SettingsDialog
        # (Tools > "Settings...", see gui/docks/settings_dialog.py). MainWindow
        # reads its checkboxes back through this alias / settings.state (see
        # _restore_window_state/_persist_settings/closeEvent there).
        self.configurator_dock = ConfiguratorDock(main_window, connection=connection)
        self.settings_dialog = SettingsDialog(self.configurator_dock, main_window)
        # Tools (2026-09-01, plan plan_2026_09_01_tools_dialog_and_entity_roles.md):
        # the Entity electrical-fields form (ToolsDock) is a STANDALONE widget
        # hosted in the non-modal ToolsDialog — the Detail dock has no Tools
        # page anymore (same move as Extract/Thermal via/Points). The same
        # single live instance keeps receiving the snapshot ticks /
        # set_root_path / saved.
        self.tools_dock = ToolsDock(main_window)
        self.tools_dialog = ToolsDialog(self.tools_dock, main_window)

        # ── bottom: Log ────────────────────────────────────────────────────
        # 2026-09-05 (plan components_fieldstool_master_detail): Pending moved
        # into the Components dock's left tab, so Log is the sole bottom dock.
        self.log_dock = LogDock(main_window, verbose=verbose)
        main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)

        # All real TOP-LEVEL QDockWidgets (2026-08-27, handoff
        # sync_skip_message_and_view_menu): MainWindow's View menu wires each
        # one's ready-made toggleViewAction() so a closed dock can be brought
        # back without restarting. Deliberately NOT the internal master-detail
        # pages (placer_dock/..., and since 2026-09-05 the Components dock's
        # own Pending page + embedded fieldstool window — plain QWidgets, not
        # independently closable/dockable, no toggleViewAction of their own).
        # Order matches construction above (already grouped by area: Left /
        # bottom). Since task T (2026-09-10) the three Components/Config/Trees
        # widgets are pages of the central QTabWidget, not docks — they cannot
        # be floated or closed, so they have no toggleViewAction and no View-menu
        # entry. Only the Log remains a real top-level dock.
        self.docks = [self.log_dock]

        self._wire()

        # Hotkeys list in the Settings tab (ConfiguratorDock.refresh_hotkeys)
        # must reflect EVERY dock's actions, so refresh it once all docks are
        # constructed — ConfiguratorDock is built mid-way through this
        # __init__ (before ToolsDock), and LogDock only at line ~129 above, so
        # without this a late dock's hotkey would work (parent.addAction) but
        # silently never appear in Settings for rebinding. Idempotent; safe to
        # call again later if a dock ever registers hotkeys dynamically.
        self.configurator_dock.refresh_hotkeys()

        # Config working set (2026-09-01, plan project_save_model): every
        # stage/clear notifies this listener -> dirty indicator + a debounced
        # refresh so the tree/collectors show the staged content. QTimer-
        # debounced (a burst of stages in one event-loop turn coalesces into
        # one rebuild).
        self._ws_refresh_timer = QTimer(self.main_window)
        self._ws_refresh_timer.setSingleShot(True)
        self._ws_refresh_timer.timeout.connect(self._refresh_from_working_set)
        WORKING_SET.add_listener(self._on_working_set_changed)

    def restore_tree_mode(self) -> None:
        """Restores the Components tree's "Not yet applied" (schematic)
        mode. Deliberately NOT part of __init__: restoring it rebuilds the
        tree, and that rebuild reads main_window.fieldstool_dock through the
        tree dock's lazy lookup — which cannot resolve until MainWindow has
        bound its DockHub (see RoleClusterTreeDock.restore_mode_from_
        settings()). MainWindow calls this right after constructing the hub.
        """
        self.tree_dock.restore_mode_from_settings()

    # ── Config right-page routing (2026-09-05, plan config_qview_placer_nettrace) ──

    def _show_config_placer(self, *_args) -> None:
        """Route a Config tree pick (cell/placement/entity/coordinate) to the
        Placer right page of the Config dock — the payload of the picked leaf
        is irrelevant here (the load happened via the dedicated handler)."""
        self.config_tree_dock.show_page(self._placer_page)

    def _show_config_net_trace(self, *_args) -> None:
        """Route a net_trace pick to the NetTrace right page of the Config
        dock."""
        self.config_tree_dock.show_page(self._net_trace_page)

    def _show_config_scheme_list(self, *_args) -> None:
        """Route a scheme_lists pick to the Scheme List right page of the
        Config dock (2026-09-06, plan scheme_list P5)."""
        self.config_tree_dock.show_page(self._scheme_list_page)

    def _load_scheme_list_page(self, entry) -> None:
        """Scheme List leaf single click (scheme_list_picked, 2026-09-06, plan
        scheme_list P5) — load the record read-only and show it as a Config
        right-QView page (no dialog)."""
        self.scheme_list_dock.load_entry(entry)
        self._show_config_scheme_list()

    def _reread_scheme_list_from_tree(self, entry, file_path) -> None:
        """Config-tree context menu's "Reread..." delegate (scheme_list_reread_
        requested, 2026-09-06) — load the record (targeting its OWN file so an
        Apply rewrites it there) and run the Reread flow on the same page."""
        self.scheme_list_dock.load_entry(entry, file_path)
        self._show_config_scheme_list()
        self.scheme_list_dock.reread()

    # ── Scheme List Place (2026-09-06, plan scheme_list §6.3 / P6 Stage 3) ─
    # The Place QView is the Config side of turning ONE recorded snapshot into
    # a NEW scheme_list-based Entity + a placement node in an EXISTING tree.
    # Triple exposure: the context menu's "Place..." (scheme_list_place_
    # requested), the Tools menu's "Place..." (blank form, presets the record
    # currently selected in the Config tree when there is one) and the page's
    # own button. All three land on the same SchemeListPlaceFormWidget page.

    def _show_place_scheme_list_page(self, record_name) -> None:
        """Shared leg of the Place triple exposure: focus the Config dock,
        re-read the cfg at the current root (refresh() repopulates the cfg-
        derived combos), preset the record when one was requested and show the
        page. Order matters — preset_scheme_list needs a loaded cfg to resolve
        the name, so refresh() runs before the preset."""
        self._focus_config_tree_dock()
        self.scheme_list_place_dock.refresh()
        if record_name:
            self.scheme_list_place_dock.preset_scheme_list(record_name)
        self.config_tree_dock.show_page(self._scheme_list_place_page)

    def place_scheme_list(self) -> None:
        """Main menu "Tools -> Scheme Lists -> Place..." (plan §6.3): open the
        SchemeListPlaceFormWidget QView page, preset to the Scheme List record
        currently SELECTED in the Config tree when there is one (mirror of
        reread_scheme_list), otherwise a blank form — the user picks the record
        + tree + parent + offset/rotation in the page itself."""
        name = None
        selection = self.config_tree_dock.selected_scheme_list()
        if selection is not None:
            entry = selection[1]
            name = entry.get("name") if isinstance(entry, dict) else None
        self._show_place_scheme_list_page(name)

    def place_scheme_list_record(self, entry, file_path) -> None:
        """Config-tree context menu's "Place..." delegate
        (scheme_list_place_requested, 2026-09-06): preset the Place page to
        the right-clicked record (by its name) and show it. `file_path` is the
        record's owning file — the page re-resolves the tree owner itself."""
        name = entry.get("name") if isinstance(entry, dict) else None
        self._show_place_scheme_list_page(name)

    # ── Scheme List Re-source (2026-09-06, plan scheme_list §7 / Stage 5b) ─
    # "Re-source..." re-points an EXISTING record at a DIFFERENT source (sheet
    # or selection) under the SAME name — the same two-tab Record dialog with
    # the name pinned read-only (plan_2026_09_06_scheme_list_sheet_capture.md
    # 5b.2). The record is REPLACED in the file that already owns it
    # (target_path, like Reread Apply) — never moved to the default storage
    # file (scheme_lists.sexp, or the legacy scheme_lists.json).
    # Triple exposure: the context menu's "Re-source..."
    # (scheme_list_resource_requested) and the Tools menu's "Re-source..."
    # (needs a selected record); both converge on _run_resource_scheme_list.
    # The capture itself runs on the worker (_run_resource_capture, mirror of
    # Record's _run_record_capture) — never blocking the UI on live-board IPC.

    def resource_scheme_list_record(self, entry, file_path) -> None:
        """Config-tree context menu's "Re-source..." delegate
        (scheme_list_resource_requested, plan 5b.3/5b.4): the record is
        already known from the right-click — re-point it under the same name.
        `file_path` is the record's owning file (the re-source write target).

        No guard widget is passed to the capture (Э2, plan_2026_09_12_busy_
        indicator): this leg is started by a context-menu QAction built on the
        fly in the Config tree, and that object is gone with its menu long
        before the worker runs — there is nothing stable to disable. A second
        run would need a fresh right-click plus the whole dialog round trip."""
        self._run_resource_scheme_list(entry, file_path)

    def resource_scheme_list(self) -> None:
        """Main menu "Tools -> Scheme Lists -> Re-source..." (plan 5b.3): like
        Reread, this acts on the Scheme List record currently SELECTED in the
        Config tree — unlike Record there is no point in an empty re-source
        form, so a missing selection is a warning, not a blank dialog."""
        selection = self.config_tree_dock.selected_scheme_list()
        if selection is None:
            show_message(_("Select a Scheme List record first."), "",
                         logging.getLogger(__name__))
            return
        file_path, entry = selection
        self._run_resource_scheme_list(
            entry, file_path,
            trigger=self._menu_trigger_action("resource_scheme_list_action"))

    def _run_resource_scheme_list(self, entry, file_path, trigger=None) -> None:
        """UI thread — the shared Re-source flow: guards, the fixed-name
        RecordSchemeListDialog (both source tabs stay available), the ref
        derivation (record_refs_for — same By-sheet/By-selection branch as
        Record), the duplicate pre-checks with exclude_name = the record
        itself, then the capture dispatched to the worker. Never touches the
        board on this thread.

        `trigger` is the menu QAction the flow was started by, for the guard
        widget of its start_long_op (Э2, plan_2026_09_12_busy_indicator); the
        Config-tree context-menu leg passes None on purpose (see
        resource_scheme_list_record)."""
        from .worker import start_long_op
        root_path = self.root_metadata_dock.root_path
        if root_path is None:
            QMessageBox.warning(self.main_window, _("Scheme Lists"),
                                _("Set the project root first."))
            return
        record_name = entry.get("name") if isinstance(entry, dict) else None
        if not record_name:
            QMessageBox.warning(self.main_window, _("Scheme Lists"),
                                _("Select a Scheme List record first."))
            return
        connection = self.main_window.connection
        board = getattr(connection, "board", None)
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            # Connection state, not user input — a Log line, never a modal
            # (plan_2026_09_11_no_modals_and_busy_kicad X.1). The flow still
            # stops here (no dialog, no capture).
            show_message(_("Connect to KiCad first."), _ERROR_STYLE, logger)
            return
        # source_sheet derivation (capture_scheme_list's sheet_names parameter)
        # needs the {uuid: sheetname} map — best-effort, same as Record (a
        # broken config only leaves source_sheet None, never blocks). Loaded
        # BEFORE the dialog (2026-09-07 fix, symmetric to Record — see
        # plan_2026_09_07_scheme_list_sheet_names_empty.md): the live Board's
        # OWN sheet_names is always {}, so the snapshot the dialog is built
        # from needs the config-based map re-resolved into it first.
        sheet_names: Dict = {}
        try:
            from kicadstamp.config import load_config
            _cfg, ctx = load_config(str(root_path))
            sheet_names = dict(getattr(ctx, "sheet_names", {}) or {})
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "Re-source: could not load sheet_names from %s — source_sheet "
                "will be left unset", root_path, exc_info=True)
        # Full live snapshot + the polled board selection — the same two
        # sources Record's dialog feeds on (the record can be re-sourced from
        # either mode, not just "By sheet").
        snapshot = snapshot_with_resolved_sheets(
            getattr(connection, "snapshot", None) or [], sheet_names)
        selection_refs = sorted({getattr(s, "ref", None) for s in self._selection_footprints
                                 if getattr(s, "ref", None)})
        dialog = RecordSchemeListDialog(
            snapshot, selection_refs, self.main_window, fixed_name=record_name,
            adapter=adapter, selected_footprints=self._selection_footprints,
            # Re-source pre-fills the Pivot/Anchor tab from the record's stored
            # pivot, so leaving it untouched KEEPS the pivot (Commit F).
            pivot_initial=entry.get("pivot") if isinstance(entry, dict) else None,
            # Commit G — "Take from selection" reads the CURRENT board selection
            # at click time (see record_scheme_list).
            selection_provider=lambda: list(self._selection_footprints),
            # Commit H — the recorded refs' positions come from the LIVE
            # full-board snapshot (connection.snapshot, refreshed by the poll
            # timer under the dialog's modal event loop), never from a direct
            # adapter.get_footprints() IPC on the shared kipy REQ socket
            # (plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_fix.md §0).
            snapshot_provider=lambda: getattr(connection, "snapshot", None) or [],
            # R.2.1 (2026-09-11, plan_2026_09_11_stale_snapshot_positions.md):
            # the provider above returns the connection's snapshot, which is
            # only ever rebuilt by connect()/manual refresh — a no-op tick once
            # connected. The dialog therefore REBUILDS it on the worker thread
            # before "Take from selection" reads any position.
            connection=connection)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        _name, _sheet_path, checked_paths = dialog.result_data()
        try:
            pivot = dialog.pivot_value()  # Pivot/Anchor tab (Commit F)
        except ValidationError as e:
            QMessageBox.warning(self.main_window, _("Cannot re-source Scheme List"),
                                str(e))
            return
        refs = record_refs_for(snapshot, dialog.is_by_sheet(), checked_paths,
                               selection_refs)
        if not refs:
            QMessageBox.warning(
                self.main_window, _("Cannot re-source Scheme List"),
                _("No footprints to record — pick a sheet that has footprints "
                  "on the 'By sheet' tab, or select footprints on the board "
                  "for 'By selection'."))
            return
        # Duplicate pre-checks BEFORE the expensive capture (like Record) —
        # but the record ITSELF is excluded: its own name is not a duplicate
        # and its own old refs are being REPLACED (replace, not conflict).
        # Only a ref owned by ANOTHER record stays fatal.
        problems = scheme_list_duplicate_problems(
            root_path, record_name, refs, exclude_name=record_name)
        if problems:
            QMessageBox.warning(self.main_window,
                                _("Cannot re-source Scheme List"),
                                "\n".join(problems))
            return
        # 5c.1 — same scope persistence as Record: a "By sheet" Re-source
        # stores the NEW checked leaf paths as the record's scope; a "By
        # selection" Re-source stores none.
        if dialog.is_by_sheet():
            scope_sheet_paths = [list(p) for p in (checked_paths or [])]
        else:
            scope_sheet_paths = None
        # Named presets (plan_2026_09_06_scheme_list_named_presets.md §7,
        # Re-source semantics): the EXISTING record's own scope_presets library
        # is read from the record dict already in hand. A non-empty "Save as
        # preset" field overwrites only the same-name entry with the CURRENT
        # checked checklist; an EMPTY field leaves the existing library
        # UNTOUCHED (Re-source changes the geometry source, it does not wipe
        # saved checklist variants — unlike Record, where [] is the true
        # "no presets yet" default).
        preset_name = dialog.preset_name_to_save()
        existing_presets = [dict(p) for p in (entry.get("scope_presets") or [])]
        if preset_name:
            merged = [p for p in existing_presets if p.get("name") != preset_name]
            merged.append({"name": preset_name,
                           "sheet_paths": [list(p) for p in checked_paths]})
            payload_scope_presets = merged
        else:
            payload_scope_presets = existing_presets
        payload = {"board": board, "name": record_name, "refs": refs,
                   "root": str(root_path),
                   "target_path": (str(file_path)
                                   if file_path is not None else None),
                   "sheet_names": sheet_names,
                   "pivot": list(pivot),
                   "scope_sheet_paths": scope_sheet_paths,
                   "scope_presets": payload_scope_presets}
        scheme_widgets = [trigger] if trigger is not None else []
        self._scheme_active_op = start_long_op(
            connection, scheme_widgets, self._run_resource_capture,
            self._finish_resource_capture, self._on_resource_op_failed, payload,
            busy_text=_("reading the board"))

    def _show_config_chain(self, *_args) -> None:
        """Route a chains pick (pad leaf / chain edit / Add net / Add spoke) to
        the Chain right page of the Config dock (2026-09-05, design
        config_qview_chain_entity_pages §4)."""
        self.config_tree_dock.show_page(self._chain_page)

    def show_left_page(self, widget) -> None:
        """Bring one of the three central tabs to the front — the replacement
        for the old dock show()/raise_() pair, which cannot work now that the
        three widgets are pages of the central QTabWidget rather than docks
        (task T)."""
        self.left_tabs.setCurrentWidget(widget)

    def _focus_config_tree_dock(self) -> None:
        """Bring the Config tab to the front — the Config-dock mirror of
        _focus_trees_dock (Tools-menu Add net/spoke delegates must show the
        Config tab before opening a right page in it)."""
        self.show_left_page(self.config_tree_dock)

    def _show_config_entity(self, *_args) -> None:
        """Route an Entities leaf pick to the Entity right page of the Config
        dock (2026-09-05, design config_qview_chain_entity_pages §5)."""
        self.config_tree_dock.show_page(self._entity_page)

    def _jump_to_tree(self, tree_name: str) -> None:
        """Entity page's placement click -> open/raise the named tree in
        TreesDock (design config_qview_chain_entity_pages §8.6)."""
        self.trees_dock.activate_tree(tree_name)

    def _load_entity_page(self, name) -> None:
        """ConfigTreeDock's entity_picked delegate (single click on an Entities
        leaf, 2026-09-05, design config_qview_chain_entity_pages §5) — loads the
        Entity record into the Entity right-QView page and shows it."""
        self.entity_dock.load_entity(name)
        self._show_config_entity()

    def _show_config_chains_nav(self, *_args) -> None:
        """Route a chains anchor/chain pick to the chains-navigation right page
        of the Config dock (2026-09-05, design config_qview_chain_entity_pages
        §4/§8.2)."""
        self.config_tree_dock.show_page(self._chains_nav_page)

    def _show_chain_pads(self, chain) -> None:
        """chains: CHAIN node single click (2026-09-05, S2b) — the chains-nav
        QView page shows that chain's pads (clickable)."""
        self.chains_nav_dock.show_chain(chain)
        self._show_config_chains_nav()

    def _show_anchor_chains(self, anchor_key, chains) -> None:
        """chains: ANCHOR node single click (2026-09-05, S2b) — the chains-nav
        QView page shows the anchor's chains (clickable)."""
        self.chains_nav_dock.show_anchor(anchor_key, chains)
        self._show_config_chains_nav()

    def _wire(self) -> None:
        """Every dock-to-dock connection (real pyqtSignals — a role can
        legitimately have more than one listener)."""

        # 2026-08-21 (plan flatten_and_single_file_gui): the entity docks no
        # longer ask "which file do I write to" — every new record (rule/
        # clone_placement/coordinate_placement/thermal_via_array/point/cell/
        # extract_profile/net_trace) is written to the project ROOT file,
        # which each dock's own set_root_path() receives via root_changed
        # below. The old file_selected/working_file_changed -> set_target_file
        # broadcast (2026-08-03..2026-08-13) is gone; the tree still feeds
        # RootMetadataDock's Working-file combobox DISPLAY below.
        self.config_tree_dock.file_selected.connect(self.root_metadata_dock.set_working_file_from_tree)
        # Root ownership moved to RootMetadataDock 2026-08-11 (was
        # ConfigTreeDock's — see gui/docks/root_metadata.py's module
        # docstring for the full reasoning); root_changed replaces the old
        # root_file_changed as the source every listener below follows,
        # INCLUDING ConfigTreeDock itself now (it only rebuilds its tree,
        # no longer owns Open/New/Recent). Rules'/Placer's/ThermalVia's own
        # Cell/Point combos need the WHOLE include graph (see gui/docks/
        # rules.py's module docstring), which starts from the project's
        # root, not whatever file each dock's own set_target_file above
        # points it at.
        # EVERY root_changed consumer is guarded through _safe_call: a dock's
        # set_root_path/set_root_file may raise on a BROKEN root config (e.g.
        # a missing schematic_dir — RulesDock._refresh_sheet_names ->
        # build_sheet_name_map fatals). The GUI must ALWAYS start and stay
        # open: the error is logged (Log dock), the root path stays set, the
        # user fixes the config or picks another via Open/New. Applies to
        # both the restore-on-startup path and a manual Open/Recent in a
        # running GUI (both go through root_changed below).
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "config_tree_dock.set_root_file",
                    self.config_tree_dock.set_root_file))
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "trees_dock.set_root_file",
                    self.trees_dock.set_root_file))
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "chain_dock.set_root_path",
                    self.chain_dock.set_root_path))
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "entity_dock.set_root_path",
                    self.entity_dock.set_root_path))
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "net_trace_dock.set_root_path",
                    self.net_trace_dock.set_root_path))
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "placer_dock.set_root_path",
                    self.placer_dock.set_root_path))
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "thermal_via_dock.set_root_path",
                    self.thermal_via_dock.set_root_path))
        # Scheme List (2026-09-06, plan scheme_list P5): Reread Apply needs the
        # project root to resolve/write the record's owning file; the root also
        # feeds the Record... duplicate pre-checks.
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "scheme_list_dock.set_root_path",
                    self.scheme_list_dock.set_root_path))
        # Scheme List Place (2026-09-06, plan scheme_list §6 / P6 Stage 3): the
        # Place page's cfg combos (scheme lists / trees / parents) come from
        # the root config — same root_changed source as every other dock.
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "scheme_list_place_dock.set_root_path",
                    self.scheme_list_place_dock.set_root_path))
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "cells_dock.set_root_path",
                    self.cells_dock.set_root_path))
        # Cell anchor (Phase C, 2026-09-09): the marker/bbox live frame and the
        # Sheet combo need the project root — same root_changed source as every
        # other dock.
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "cell_anchor_view.set_root_path",
                    self.cell_anchor_view.set_root_path))
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "tools_dock.set_root_path",
                    self.tools_dock.set_root_path))
        # PointsDock's own target-file combo (added 2026-08-13, plan
        # tree_to_combo_file_pickers — the only dock that had no
        # set_root_path at all before) needs the same whole include graph,
        # same root_changed source as the four above.
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "points_dock.set_root_path",
                    self.points_dock.set_root_path))
        # fieldstool's root_sheet (added 2026-08-07, see config/models.py's
        # Config.root_sheet docstring) — same root_changed source, so
        # opening/switching a project automatically re-points fieldstool's
        # schematic-vs-board diff instead of silently keeping the previous
        # project's manually-picked root sheet.
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "fieldstool_dock.set_root_path",
                    self.fieldstool_dock.set_root_path))
        # log_file: (Config.log_file, root-file top-level key) — 2026-08-06,
        # found live: Denis had it set in root.yaml already, assumed
        # (reasonably) it already covered GUI runs too, but the GUI's own
        # setup_logging() call (kicadstamp_gui.py) never passed a log_file
        # at all — only kicadstamp_cli.py's `apply` command honored it (see
        # cli_common.peek_log_file). Reused here so a project's log_file:
        # covers the GUI too, not just the CLI.
        self.root_metadata_dock.root_changed.connect(self._on_root_file_changed_for_logging)
        # Config working set (2026-09-01, plan project_save_model): staging is
        # ON whenever a project root is open, OFF/cleared on close — a root
        # switch/close starts with a clean working set (the unsaved-changes
        # guard lives in RootMetadataDock.set_root_file/close_project).
        self.root_metadata_dock.root_changed.connect(self._on_root_changed_for_working_set)
        # RootMetadataDock's own _restore_last_root() runs inside ITS
        # __init__ (gui/docks/root_metadata.py), which happens before
        # THIS wiring exists — so the very first root_changed emit (if a
        # root was restored on startup) fires into the void, before the
        # connect() above. Sync explicitly with whatever value is already
        # current, or a restored project silently opens with the Config
        # tree empty / Rules' Cell combo empty (found live 2026-08-05 for
        # the equivalent ConfigTreeDock-owned case this mirrors).
        # The initial sync runs through the SAME _safe_call guard as the
        # root_changed connections above — on startup the restored root may
        # be broken, and any dock must fail loudly-but-harmlessly (log only)
        # instead of crashing DockHub.__init__/MainWindow.__init__.
        self._sync_root_to_docks(self.root_metadata_dock.root_path)
        self._on_root_changed_for_working_set(self.root_metadata_dock.root_path)
        # NOTE (2026-09-01, plan project_settings_dialogs): the old
        # file_selected -> detail_dock.show_root() fallback is GONE together
        # with the Project tab (RootMetadataDock now lives in the non-modal
        # ProjectDialog). A plain file/category click in the Config tree still
        # feeds RootMetadataDock's Working-file combo display via
        # set_working_file_from_tree (wired above), but no longer switches the
        # Detail dock. file_selected fires BEFORE the more specific
        # cell_picked/placement_picked/profile_picked signal on a leaf click
        # (see config_tree.py's _on_clicked) — the specific handler below (if
        # any) wins by running after it, same emission order as before.

        # Components tree -> Placer: clicking a Cluster group node in the
        # tree fills PlacerDock's Cluster field; Config tree -> Placer/
        # Extract: clicking a Cell/Clone placement/Extract profile leaf
        # routes into the matching existing form (2026-08-03, GUI tree
        # roadmap Этап 1 — replaces the old CellListDock/PlacerListDock
        # wiring, same target methods, unified single source).
        self.tree_dock.cluster_picked.connect(self.placer_dock.set_cluster_name)
        self.config_tree_dock.cell_picked.connect(self.placer_dock.set_selected_cell)
        # A cell-leaf click normally reveals the Placer page, but while the
        # Cell-anchor editor is the active Config right page it must make THAT
        # page follow the tree selection instead (G.4) — see _on_cell_picked.
        self.config_tree_dock.cell_picked.connect(self._on_cell_picked)
        # Entities leaf (2026-09-05, design config_qview_chain_entity_pages):
        # a single click opens the Entity right-QView page (record editor) —
        # NO longer routed into Placer's Entity mode (that mode stays available
        # as a Placer source, but the tree selection shows the record page).
        self.config_tree_dock.entity_picked.connect(self._load_entity_page)
        # Entities leaf DOUBLE click (2026-09-01, plan plan_2026_09_01_tools_
        # dialog_and_entity_roles.md): open the "Edit template" dialog
        # pre-loaded with that Entity (single click stays entity_picked above).
        self.config_tree_dock.entity_edit_requested.connect(
            self._start_edit_entity_template)
        self.config_tree_dock.placement_picked.connect(self.placer_dock.load_placement)
        self.config_tree_dock.placement_picked.connect(self._show_config_placer)
        # Thermal via (2026-09-05 QView move, design config_qview_chain_entity_
        # pages): a single click on a thermal_via_arrays leaf loads the record
        # and shows it as a Config right-QView page (no dialog).
        self.config_tree_dock.thermal_via_picked.connect(self._load_thermal_via_page)
        # Coordinate placements (2026-08-12, Group 1): a normal named-records
        # section now — a leaf click carries the full entry dict, loaded into
        # the merged PlacerDock's coordinate mode, exactly like clone_placements
        # -> placement_picked -> load_placement (see config_tree.py's
        # coordinate_placements_picked docstring).
        self.config_tree_dock.coordinate_placements_picked.connect(self.placer_dock.load_placement)
        self.config_tree_dock.coordinate_placements_picked.connect(self._show_config_placer)
        # Points (2026-09-05 QView move, design config_qview_chain_entity_pages):
        # a SINGLE click (points_picked) or a double click (points_edit_requested)
        # on a points: leaf loads the point and shows it as a Config right-QView
        # page (no dialog).
        self.config_tree_dock.points_picked.connect(self._start_edit_point)
        self.config_tree_dock.points_edit_requested.connect(self._start_edit_point)
        # Chains (2026-09-05, design config_qview_chain_entity_pages): the
        # chain editor lives as the Config dock's right-QView Chain page (no
        # dialog anymore). A SINGLE click on a chains: PAD leaf opens the spoke
        # editor (pad_picked); a DOUBLE click on a chain/pad leaf is the same
        # target (chain_edit_requested/pad_edit_requested); "Add spoke..."
        # (add_pad_requested) opens the same page in pad mode. The tree's
        # context menu still drives Redraw chain/spoke and Bulk set Cell
        # (chain_redraw_requested/pad_redraw_requested/bulk_set_cell_requested)
        # — those run the ApplyPipeline / bulk write on the same live
        # chain_dock instance.
        self.config_tree_dock.chain_edit_requested.connect(self._start_edit_chain)
        self.config_tree_dock.pad_edit_requested.connect(self._start_edit_pad)
        self.config_tree_dock.pad_picked.connect(self._start_edit_pad)
        self.config_tree_dock.add_pad_requested.connect(self._start_new_pad)
        self.config_tree_dock.chain_redraw_requested.connect(self.chain_dock.redraw_chain)
        self.config_tree_dock.pad_redraw_requested.connect(self.chain_dock.redraw_pad)
        self.config_tree_dock.anchor_redraw_requested.connect(self.chain_dock.redraw_chains)
        self.config_tree_dock.bulk_set_cell_requested.connect(self.chain_dock.bulk_set_cell)
        # Chains navigation (2026-09-05, design config_qview_chain_entity_pages
        # §4/§8.2): anchor/chain single clicks -> the chains-nav drill page; a
        # nav pad row opens the spoke editor; a nav chain row syncs the tree.
        self.config_tree_dock.chain_picked.connect(self._show_chain_pads)
        self.config_tree_dock.anchor_picked.connect(self._show_anchor_chains)
        self.chains_nav_dock.open_spoke.connect(self._start_edit_pad)
        self.chains_nav_dock.reveal_chain.connect(self.config_tree_dock.select_chains_chain)
        self.config_tree_dock.net_trace_picked.connect(self.net_trace_dock.load_entry)
        self.config_tree_dock.net_trace_picked.connect(self._show_config_net_trace)
        # Scheme List (2026-09-06, plan scheme_list P5): a single click on a
        # scheme_lists leaf opens the read-only record page; the context menu's
        # "Reread..." loads the record and runs the Reread flow on the same
        # page. No Add/Record here — a Scheme List can only be captured from
        # the live board (Tools -> "Scheme Lists" -> "Record...").
        self.config_tree_dock.scheme_list_picked.connect(self._load_scheme_list_page)
        self.config_tree_dock.scheme_list_reread_requested.connect(
            self._reread_scheme_list_from_tree)
        # 2026-09-06 (plan scheme_list §6.3 / P6 Stage 3): the context menu's
        # "Place..." — the Place page opens preset to the right-clicked record.
        self.config_tree_dock.scheme_list_place_requested.connect(
            self.place_scheme_list_record)
        # "Re-source..." (Stage 5b) — the context-menu leg re-sources the
        # right-clicked record under the same name (fixed-name dialog; the
        # Tools-menu leg is resource_scheme_list, called from main_window).
        self.config_tree_dock.scheme_list_resource_requested.connect(
            self.resource_scheme_list_record)
        # "Edit cell..." (context menu, 2026-08-06) — deliberately NOT wired
        # to cell_picked, which keeps meaning "pick this cell as a
        # placement's content" (see config_tree.py's module docstring).
        # Context-menu actions never go through _on_clicked, so unlike a
        # plain leaf click, file_selected has NOT necessarily already
        # targeted CellDock at the right file — _edit_cell below sets it
        # explicitly before loading, same reasoning as _start_new_placement
        # etc. below for "Add ...".
        self.config_tree_dock.cell_edit_requested.connect(self._edit_cell)
        # 2026-09-09 (Phase C of plan_2026_09_09_cell_anchor_v2_declarative_
        # and_board_overlay): the context menu's "Cell anchor..." opens the
        # dedicated anchor editor as a Config right-QView page (same explicit
        # file handling as _edit_cell).
        self.config_tree_dock.cell_anchor_requested.connect(self._edit_cell_anchor)
        # 2026-09-03 (plan cell_geometry_refresh): the context menu's "Update
        # from selection..." — same delegate shape as _edit_cell (explicit
        # file, then show the cell page), driving the cell's geometry refresh.
        self.config_tree_dock.cell_refresh_requested.connect(
            self._refresh_cell_from_selection)
        # 2026-09-03 (plan fpga_oscill_missing_copper_and_cell_import §B.3):
        # the context menu's "Import from selection..." — the ADDITIVE
        # counterpart of the refresh delegate above (backfills NEW via/track
        # records for live copper the cell doesn't describe yet; never edits
        # existing records).
        self.config_tree_dock.cell_import_requested.connect(
            self._import_cell_from_selection)
        # Э4 (2026-09-12, plan_2026_09_12_cell_layer_dialog): the context menu's
        # "... (choose layers)..." variants — the SAME two delegates with the
        # layer dialog in front (one extra flag, no second implementation).
        self.config_tree_dock.cell_refresh_layers_requested.connect(
            partial(self._refresh_cell_from_selection, choose_layers=True))
        self.config_tree_dock.cell_import_layers_requested.connect(
            partial(self._import_cell_from_selection, choose_layers=True))
        # 2026-09-06 (plan copy_placement_from_cell): the context menu's "Copy
        # placement from cell..." — the OFFLINE cell-to-cell placement copy
        # onto the requested cell (donor picked from a minimal role-set-fitted
        # combobox; no live board). Same delegate shape as the two above.
        self.config_tree_dock.cell_copy_requested.connect(
            self._copy_cell_placement)
        # Placer/Thermal via/Extract/Points/Chains -> Config tree: a
        # successful Save refreshes the whole tree (walk_include_tree() is
        # re-run) so a brand new (or renamed) entry shows up without
        # reassigning Files. The SAME six signals ALSO feed
        # _refresh_graph_dependent_choices (2026-08-15, plan
        # graph_changed_broadcast): an entity dock's Save can introduce a
        # brand-new NAME directly (e.g. CellDock's "Add cell..." + Save),
        # bypassing the tree entirely, and every OTHER dock's name-derived
        # combo (RulesDock.spoke_cell_combo, the point-name combos in
        # ThermalViaArrayDock/RulesDock/PlacerDock, ...) must hear about it
        # — the tree refresh above only updates the TREE's own display.
        self.placer_dock.saved.connect(self.config_tree_dock.refresh)
        self.thermal_via_dock.saved.connect(self.config_tree_dock.refresh)
        self.points_dock.saved.connect(self.config_tree_dock.refresh)
        self.chain_dock.saved.connect(self.config_tree_dock.refresh)
        self.net_trace_dock.saved.connect(self.config_tree_dock.refresh)
        # Scheme List (2026-09-06, plan scheme_list P5): a successful Reread
        # Apply rewrites the record — refresh the tree's leaf display.
        self.scheme_list_dock.saved.connect(self.config_tree_dock.refresh)
        # Scheme List Place (2026-09-06, plan scheme_list §6.3 / P6 Stage 3): a
        # successful Place wrote a NEW Entity + a NEW tree node — refresh the
        # Config tree's display, reload the Trees dock (its tree tabs hold the
        # config's trees: in memory) and broadcast graph_changed so every
        # graph-derived combo (Entity names, ...) hears about the new Entity.
        self.scheme_list_place_dock.saved.connect(self.config_tree_dock.refresh)
        self.scheme_list_place_dock.saved.connect(self.trees_dock.reload_trees)
        self.scheme_list_place_dock.saved.connect(
            self.config_tree_dock.graph_changed.emit)
        self.cells_dock.saved.connect(self.config_tree_dock.refresh)
        # Cell anchor (Phase C, 2026-09-09): a saved anchor rewrites the cell
        # entry — refresh the Config tree's leaf display.
        self.cell_anchor_view.saved.connect(self.config_tree_dock.refresh)
        # Cell anchor (Phase D, 2026-09-09, D.2): leaving the anchor editor
        # page (a Config right-QView page) drops the cell's drawn overlay —
        # the overlay is an editing aid shown only while the page is open.
        self.config_tree_dock.right_stack.currentChanged.connect(
            self._on_config_right_page_changed)
        self.tools_dock.saved.connect(self.config_tree_dock.refresh)
        self.tools_dock.saved.connect(self._refresh_graph_dependent_choices)
        # Auto-close after a SUCCESSFUL edit (2026-09-01, Denis: "диалог должен
        # авто-закрываться после успешной правки (как Points)") — a row action
        # in any table writes the Entity and emits saved; a validation failure
        # never emits it, so the dialog stays open for the fix.
        self.tools_dock.saved.connect(self.tools_dialog.hide)
        self.placer_dock.saved.connect(self._refresh_graph_dependent_choices)
        self.thermal_via_dock.saved.connect(self._refresh_graph_dependent_choices)
        self.points_dock.saved.connect(self._refresh_graph_dependent_choices)
        self.chain_dock.saved.connect(self._refresh_graph_dependent_choices)
        self.cells_dock.saved.connect(self._refresh_graph_dependent_choices)
        # Entity page (2026-09-05, design config_qview_chain_entity_pages): a
        # Comment edit refreshes the tree's comment glyph; a placement click
        # opens that tree in TreesDock.
        self.entity_dock.saved.connect(self.config_tree_dock.refresh)
        self.entity_dock.open_tree.connect(self._jump_to_tree)
        # Thermal via and Points are persistent Config right-QView pages
        # (2026-09-05, design config_qview_chain_entity_pages) — no auto-hide on
        # `saved`; the page stays open for iterative tuning (Thermal via Redraw).
        # The Chain editor is a persistent Config right-QView page (2026-09-05,
        # design config_qview_chain_entity_pages) — no auto-hide on `saved`: the
        # page stays open for iterative tuning (Redraw on the current form).
        # Config tree's "Add placer.../Add thermal via pad.../Add point.../
        # Add rule..." context-menu actions -> Placer/Thermal via/Points/
        # Rules: open the form blank, targeting the file the action was
        # invoked on, and bring that tab to front (same raise pattern as
        # open_fieldstool() below).
        self.config_tree_dock.add_placer_requested.connect(self._start_new_placement)
        self.config_tree_dock.add_thermal_via_requested.connect(self._start_new_thermal_via)
        # "Add coordinate placement..." (2026-08-12, Group 1) — opens the
        # merged PlacerDock's coordinate form blank for the target file, the
        # same "Add placer..." shape (new_placement) the clone source uses.
        self.config_tree_dock.add_coordinate_placement_requested.connect(
            self.placer_dock.new_coordinate_placement)
        self.config_tree_dock.add_coordinate_placement_requested.connect(
            self._show_config_placer)
        self.config_tree_dock.add_point_requested.connect(self._start_new_point)
        self.config_tree_dock.add_chain_requested.connect(self._start_new_chain)
        self.config_tree_dock.add_cell_requested.connect(self._start_new_cell)
        # Config tree's own graph-mutating actions (_on_rename/_on_delete/
        # _add_included_file/_remove_file) -> every dock's graph-derived
        # combos, same handler the seven entity-dock `saved` signals above
        # feed (2026-08-15, plan graph_changed_broadcast): a file added or
        # removed, or a cell:/point: name renamed or deleted, must be
        # visible everywhere immediately, not only after the root is
        # reassigned. TreesDock's dialog ref candidates join the same
        # broadcast via its lightweight refresh_ref_candidates() (plan
        # 2026-08-31_trees_dock_stale_after_entity_add.md) — NOT its
        # full set_root_file reset, which would wipe unsaved tree edits.
        # NOT wired to the tree's initial refresh (first population, not a
        # change) or to _on_export (no graph change).
        self.config_tree_dock.graph_changed.connect(self._refresh_graph_dependent_choices)

        # fieldstool tab -> Components tree: an explicit Rescan/Apply there
        # refreshes this tree's schematic view (see FieldsToolDock).
        self.fieldstool_dock.components_changed.connect(self.tree_dock.refresh_schematic_view)

        # Pending-table row click (2026-09-05, plan components_fieldstool_
        # master_detail) -> show that component in the fieldstool pane and
        # reveal it in the Components tree (see _on_pending_ref_activated).
        self.pending_dock.ref_activated.connect(self._on_pending_ref_activated)

        # Settings tab (ConfiguratorDock, 2026-08-15, plan
        # configurator_panel): the always-on-top/tray checkboxes MOVED here
        # from the status bar (gui/main_window.py) — the actual window-flag /
        # tray-icon logic stays in MainWindow, this re-wires the toggles back
        # onto it. getattr-guarded like request_refresh above: DockHub is
        # also built against a plain QMainWindow in tests, which has no
        # _set_always_on_top/_set_tray_enabled.
        set_always_on_top = getattr(self.main_window, "_set_always_on_top", None)
        if set_always_on_top is not None:
            self.configurator_dock.always_on_top_toggled.connect(set_always_on_top)
        set_tray_enabled = getattr(self.main_window, "_set_tray_enabled", None)
        if set_tray_enabled is not None:
            self.configurator_dock.tray_enabled_toggled.connect(set_tray_enabled)
        # Highlight scheme — re-apply to all three target widgets the moment
        # the Settings tab changes it (mode radio or custom color). All three
        # also applied it once at construction, so this is purely the live
        # half.
        self.configurator_dock.highlight_changed.connect(self._apply_highlight)

    def _apply_highlight(self) -> None:
        """Re-apply the highlight stylesheet to the highlight consumers —
        DetailDock's active tab, ConfigTreeDock's, TreesDock's and
        RoleClusterTreeDock's selected tree item — after a change in the
        Settings tab (see gui/docks/configurator.py)."""
        self.config_tree_dock.apply_highlight()
        self.trees_dock.apply_highlight()
        self.tree_dock.apply_highlight()

    # ── delegates MainWindow's poll/timer logic drives ────────────────────

    def push_snapshot(self, snapshot, board) -> None:
        """Feed a freshly rebuilt BoardConnection.snapshot into the docks
        that display it — the ONE consumer of the snapshot (see
        gui/main_window.py's _poll): the Components tree model (the ROWS) plus
        every Role/Cluster/NET known-value LIST, via push_known_lists (which
        now feeds TreesDock too — S.3.2 of
        plan_2026_09_11_stale_snapshot_role_lists.md).

        Only the manual Refresh/Reconnect path drives this one; the
        navigational freshness trigger uses push_known_lists alone, because
        the tree-model rebuild is exactly the churn the idle auto-tick
        deliberately avoids (see main_window.py's module docstring)."""
        self.tree_dock.set_footprints(snapshot)
        self.push_known_lists(snapshot, board)
        # NOTE (2026-09-05): placer_dock.refresh_known_nets is GONE — the
        # Placer's manual Nets/Net overrides/Refs tabs were removed (nets
        # auto-resolve); the other docks' refresh_known_nets stay here (they
        # read the BOARD, so they belong to the explicit Refresh path only —
        # see push_known_lists).
        self.thermal_via_dock.refresh_known_nets(board)
        self.chain_dock.refresh_known_nets(board)
        self.net_trace_dock.refresh_known_nets(board)
        self.tools_dock.refresh_known_nets(board)

    def push_known_lists(self, snapshot, board) -> None:
        """The SNAPSHOT-derived known-value lists: every Role/Cluster
        suggestion source, WITHOUT the Components-tree model rebuild
        (tree_dock.set_footprints) — that one shows board ROWS, not the
        known-value lists. This is what the navigational freshness trigger
        distributes (S.2/S.3, plan_2026_09_11_stale_snapshot_role_lists.md),
        while the row views keep what they have until the user asks for a full
        Refresh.

        Deliberately NOT the NET lists: refresh_known_nets reads the BOARD
        itself (adapter.get_all_nets()/get_tracks()), i.e. a live IPC call, and
        this trigger fires on the UI thread — a direct board read there is
        exactly what S.1 forbids (the 2026-08-08 hang). Those calls stay on the
        manual Refresh path (push_snapshot).

        The two TREE docks take no argument: they re-read the live cache
        themselves (RoleClusterTreeDock._connection.snapshot /
        TreesDock._live_roles/_live_clusters), so this call must simply happen
        AFTER the rebuild — the caller's contract (see
        refresh_snapshot_and_push)."""
        self.tree_dock.refresh_known_lists()
        self.trees_dock.refresh_known_lists()
        self.placer_dock.refresh_known_roles(snapshot)
        self.thermal_via_dock.refresh_known_roles(snapshot)
        self.points_dock.refresh_known_roles(snapshot)
        self.chain_dock.refresh_known_roles(snapshot)
        self.net_trace_dock.refresh_known_roles(snapshot)
        self.cells_dock.refresh_known_roles(snapshot)
        self.cell_anchor_view.refresh_known_roles(snapshot)

    def refresh_snapshot_and_push(self, on_ready=None) -> None:
        """THE one "rebuild the board snapshot, then distribute the fresh
        lists" operation (S.3.1, K.2 #1/#2/#9,
        plan_2026_09_11_stale_snapshot_role_lists.md).

        ``BoardConnection.snapshot`` is rebuilt only by connect()/manual
        refresh — the automatic poll tick is a deliberate no-op once connected
        (gui/main_window.py) — so every Role/Cluster combo kept showing the
        values the board had at connect time until the user hit Refresh. The
        rebuild runs on the WORKER thread (gui.worker.refresh_snapshot_then ->
        start_long_op, the shared kipy REQ socket's only in-flight owner) and
        never as a direct adapter call on the UI thread — that was the
        2026-08-08 hang fixed by Commit H. On success the fresh snapshot is
        distributed through push_known_lists (lists only, see its docstring);
        ``on_ready`` then runs on the UI thread — the tree dock's dialogs open
        there, with the candidates already fresh (TreesDock._refresh_snapshot_
        then).

        Without a live board behind the connection there is nothing fresher to
        distribute — the docks already hold THIS very snapshot (it was pushed
        when it was built) — so only ``on_ready`` runs; that also keeps the
        page-switch trigger safe (see below). While another long op holds the
        socket the rebuild is refused and ``on_ready`` still runs, so a user's
        click never dead-ends (gui.worker.refresh_snapshot_then's docstring)."""
        from .worker import refresh_snapshot_then, snapshot_refresh_supported
        connection = self.main_window.connection

        def _distribute() -> None:
            # on_ready FIRST, the cross-dock list distribution AFTER it
            # (2026-09-12, plan_2026_09_12_combo_refresh_deadlock.md §Э5): the
            # tree dock's on_ready opens a DIALOG, and it needs nothing from the
            # other docks — its candidates come from the config graph and
            # connection.snapshot directly (_all_ref_candidates/_used_refs,
            # TreesDock._prompt_node). Distributing first put all eight docks'
            # combo repopulations on the path of a button press in the tree: the
            # live "Add node" freeze was caught inside NetTraceDock's role combo,
            # a dock the user never touched, hanging on the same non-recursive
            # signal mutex as the repopulation (see set_combo_items/_wire_field_
            # changed). The lists are still distributed on this same worker-
            # completion turn, just after the dialog returns.
            if on_ready is not None:
                on_ready()
            self.push_known_lists(
                list(getattr(connection, "snapshot", None) or []),
                getattr(connection, "board", None))

        def _failed(message: str) -> None:
            # A failed rebuild means the live board is gone (BoardConnection.
            # refresh() drops the connection): log it and carry on — the
            # caller's own offline guards handle the rest.
            logger.warning("Board snapshot rebuild failed: %s", message)
            if on_ready is not None:
                on_ready()

        if not snapshot_refresh_supported(connection):
            # Nothing to rebuild. Do NOT distribute either: this trigger fires
            # from inside right_stack.setCurrentIndex (the page switch), and
            # pushing widget updates into the middle of that Qt transition
            # aborted the process (found live 2026-09-11 in
            # test_cell_selection_by_keyboard_opens_the_merged_page). With a
            # live board the distribution runs from the worker's completion
            # signal — i.e. outside the transition, which is safe.
            if on_ready is not None:
                on_ready()
            return

        refresh_snapshot_then(connection, (), _distribute, _failed)

    def clear_components(self) -> None:
        """Connection-lost path: empty the Components tree (live mode only —
        set_footprints leaves an active schematic view untouched)."""
        self.tree_dock.set_footprints([])

    def highlight_selection(self, refs) -> None:
        """Board selection -> Components tree highlight (see
        gui/main_window.py's _poll_board_selection)."""
        self.tree_dock.highlight_board_selection(refs)

    def set_board_selection(self, items, selected) -> None:
        """Push the live selection into the docks that react to it. Phase F
        (2026-09-01): the raw selection state is kept in DockHub itself
        ("Extract tree..." reads these) and PlacerDock still receives it
        (2026-08-31, plan placer_source_tab_gaps P.1 — its Cell-mode Cluster
        auto-fill reads the current selection's Cluster)."""
        self._selection_raw_items = list(items)
        self._selection_footprints = list(selected)
        # V.3: a selection tick proves a live board — the place to catch a root
        # opened BEFORE connecting (see _warn_if_sheet_narrowing_disabled).
        self._warn_if_sheet_narrowing_disabled(
            self.root_metadata_dock.root_path)
        self.placer_dock.set_board_selection(items, selected)
        # Scheme List Place (2026-09-06, plan scheme_list §6 / P6 Stage 3): the
        # Place page's opt-in "from selection" hint reads the current selection
        # center — fed by the same polled snapshot tick as Placer's auto-fill.
        self.scheme_list_place_dock.set_board_selection(items, selected)
        # Cell editor (2026-09-12, plan_2026_09_12_cell_layer_dialog Э3): the
        # layer dialog marks a layer "empty in the selection" from THIS tick —
        # the raw items, so the dock never reads the board itself on the UI
        # thread (P.3.4 of that plan).
        self.cells_dock.set_board_selection(items, selected)
        # Scheme List record Reread (5c.4): a "By selection"-record's scope is
        # the CURRENT board selection at click time — the record dock needs the
        # same selection tick.
        self.scheme_list_dock.set_board_selection(items, selected)

    def push_fieldstool_selection(self, refs) -> None:
        """Live board selection -> embedded fieldstool's target label (Phase
        5.1 — the main GUI's single 400ms tick now feeds BOTH the tree and the
        embedded fieldstool, whose own selection timer is stopped when it
        shares the main connection)."""
        self.fieldstool_dock.push_live_selection(refs)

    def push_fieldstool_snapshot(self, snapshot) -> None:
        """Feed the freshly rebuilt live-board snapshot into the embedded
        fieldstool window, so its Pending-changes diff (schematic vs board
        Role/Cluster) stays current without a poll of its own (see
        gui/main_window.py's _poll, same reasoning as push_snapshot)."""
        self.fieldstool_dock.push_live_snapshot(snapshot)

    def push_fieldstool_connection_status(self, error) -> None:
        """Mirror the shared connection's state into the embedded
        fieldstool's status label (Phase 5.1 — its own connect/refresh poll
        is stopped when it shares the main connection)."""
        self.fieldstool_dock.set_connection_status(error)

    def _on_pending_ref_activated(self, ref: str) -> None:
        """Pending-table row click (2026-09-05, plan components_fieldstool_
        master_detail): load the clicked ref into the embedded fieldstool pane
        (target + prefill, the same path a schematic tree leaf click uses) and
        reveal/select it in the Components tree."""
        self.fieldstool_dock.pick_leaf([ref])
        self.tree_dock.reveal_ref(ref)

    def open_fieldstool(self) -> None:
        """Bring the Components tab (which hosts the embedded fieldstool pane on
        the right of its splitter) to the front of the central tab group — the
        master-detail replacement for the retired right-hand fieldstool dock
        (2026-09-05, plan components_fieldstool_master_detail)."""
        self.show_left_page(self.tree_dock)

    def _start_new_placement(self, placer_path) -> None:
        """ConfigTreeDock's add_placer_requested delegate — resets
        PlacerDock's form and brings the Config dock's Placer right page to
        front, same reasoning as open_fieldstool() above (the action was
        invoked from the Config tree tab)."""
        self.placer_dock.new_placement(placer_path)
        self._show_config_placer()

    def _show_config_thermal_via(self, *_args) -> None:
        """Route a thermal_via_arrays pick to the Thermal via right page of the
        Config dock (2026-09-05 QView move, design config_qview_chain_entity_
        pages)."""
        self.config_tree_dock.show_page(self._thermal_via_page)

    def _load_thermal_via_page(self, entry) -> None:
        """Thermal via leaf click (thermal_via_picked, 2026-09-05 QView move) —
        load the record and show it as a Config right-QView page (no dialog)."""
        self.thermal_via_dock.load_entry(entry)
        self._show_config_thermal_via()

    def _show_config_points(self, *_args) -> None:
        """Route a points pick to the Points right page of the Config dock
        (2026-09-05 QView move, design config_qview_chain_entity_pages)."""
        self.config_tree_dock.show_page(self._points_page)

    def _start_new_thermal_via(self, file_path) -> None:
        """ConfigTreeDock's add_thermal_via_requested delegate (2026-09-05 QView
        move) — shows the Config dock's Thermal via right page with a fresh
        blank form."""
        self._focus_config_tree_dock()
        self.thermal_via_dock.new_thermal_via(file_path)
        self._show_config_thermal_via()

    def _start_new_point(self, file_path) -> None:
        """ConfigTreeDock's add_point_requested delegate (2026-09-05 QView move)
        — shows the Config dock's Points right page with a fresh blank form."""
        self._focus_config_tree_dock()
        self.points_dock.new_point(file_path)
        self._show_config_points()

    def _start_edit_point(self, name) -> None:
        """ConfigTreeDock's points_picked / points_edit_requested delegate
        (single or double click on a points: leaf, 2026-09-05 QView move) —
        loads the point into the live PointsDock and shows the Config Points
        right page."""
        self.points_dock.load_entry(name)
        self._show_config_points()

    def new_point(self) -> None:
        """Main menu "Tools -> Add point..." (2026-09-01) -> the same fresh
        blank form as the Config tree context menu's "Add point..." (add_point_
        requested -> _start_new_point)."""
        self._start_new_point(self.root_metadata_dock.root_path)

    def add_chain(self) -> None:
        """Main menu "Tools -> Add net..." (2026-09-01, plan rules_to_chains)
        -> the same fresh blank chain form as the Config tree context menu's
        "Add chain..." (add_chain_requested -> _start_new_chain). The menu
        labels a chain by its NET identity (Denis's decision)."""
        self._start_new_chain(self.root_metadata_dock.root_path)

    def add_spoke(self) -> None:
        """Main menu "Tools -> Add spoke..." (2026-09-01, plan rules_to_chains)
        -> opens the Chain dialog in pad mode with a fresh blank form, appending
        to the chain currently SELECTED in the Config tree. Requires a selected
        chains: CHAIN node — otherwise a message in the Log dock
        ("Pick a chain in the Config tree first"), mirroring the plan's
        Add-spoke-requires-selection rule."""
        chain = self._selected_tree_chain()
        if chain is None:
            show_message(_("Pick a chain in the Config tree first."), "", logging.getLogger(__name__))
            return
        self._start_new_pad(chain)

    def delete_selected_chain(self) -> None:
        """Main menu "Tools -> Delete net..." (2026-09-01, plan rules_to_chains)
        -> deletes the chain currently SELECTED in the Config tree via
        delete_entry (with the usual timestamped backup). Requires a selected
        chains: CHAIN node — otherwise a message in the Log dock."""
        selection = self.config_tree_dock.selected_chain()
        if selection is None:
            show_message(_("Pick a chain in the Config tree first."), "", logging.getLogger(__name__))
            return
        file_path, chain = selection
        name = entry_effective_name("chains", chain)
        report = delete_entry(self.root_metadata_dock.root_path, file_path, "chains", name,
                              cascade=False)
        self.config_tree_dock.refresh()
        self.config_tree_dock.graph_changed.emit()
        show_message(
            _("Deleted net {name!r}. Backed up: {backups}.").format(
                name=name, backups=", ".join(display_path(p) for p in report["backups"])),
            "", logging.getLogger(__name__))

    def _selected_tree_chain(self):
        """The currently selected chains: CHAIN node in the Config tree as its
        chain dict, or None (no selection / a different node kind)."""
        selection = self.config_tree_dock.selected_chain()
        if selection is None:
            return None
        return selection[1]

    def _start_new_chain(self, file_path) -> None:
        """ConfigTreeDock's add_chain_requested delegate (2026-09-05, design
        config_qview_chain_entity_pages) — opens the Config dock's Chain right
        page in chain mode with a fresh blank form, same reasoning as
        _start_new_placement above, for ChainDock."""
        self._focus_config_tree_dock()
        self.chain_dock.new_chain(file_path)
        self._show_config_chain()

    def _start_edit_chain(self, entry) -> None:
        """ConfigTreeDock's chain_edit_requested delegate (double click on a
        chains: chain node, 2026-09-01, plan rules_to_chains) — loads the
        chain into the live ChainDock's chain mode and shows it as the Config
        dock's Chain right page (2026-09-05)."""
        self.chain_dock.load_chain(entry)
        self._show_config_chain()

    def _start_edit_pad(self, chain_entry, pad_index) -> None:
        """ConfigTreeDock's pad_edit_requested / pad_picked delegate (single or
        double click on a chains: pad leaf, 2026-09-05, design
        config_qview_chain_entity_pages) — loads that one spoke into the live
        ChainDock's pad mode and shows it as the Config dock's Chain right
        page."""
        self.chain_dock.load_pad(chain_entry, pad_index)
        self._show_config_chain()

    def _start_new_pad(self, chain_entry) -> None:
        """ConfigTreeDock's add_pad_requested delegate ("Add spoke..." on a
        chain node) — shows the Config dock's Chain right page in pad mode with
        a fresh blank form, appending to the given parent chain."""
        self._focus_config_tree_dock()
        self.chain_dock.new_pad(chain_entry, self.root_metadata_dock.root_path)
        self._show_config_chain()

    def _start_new_cell(self, file_path) -> None:
        """ConfigTreeDock's add_cell_requested delegate — same reasoning as
        _start_new_thermal_via above, for CellDock: opens the (non-modal) Cell
        dialog with a fresh blank form."""
        self._open_cell_dialog()
        self.cells_dock.new_cell(file_path)

    def place_thermal_vias(self) -> None:
        """Main menu "Tools -> Place thermal vias..." (2026-09-01, plan
        plan_2026_09_01_thermal_via_dialog.md) -> the same fresh blank form as
        the Config tree context menu's "Add thermal via pad..." (add_thermal_
        via_requested -> _start_new_thermal_via)."""
        self._start_new_thermal_via(self.root_metadata_dock.root_path)

    def _menu_trigger_action(self, attr_name: str):
        """The menu QAction a menu-only flow was started by, to hand to
        start_long_op as its guard widget (Э2, plan_2026_09_12_busy_indicator:
        a DISABLED QAction is how a second click on the same menu entry is
        refused while the operation holds the shared kipy socket).

        Looked up by attribute NAME, lazily: MainWindow builds its Actions
        after DockHub exists, and a bare-stub window in a test has none — hence
        None instead of an AttributeError."""
        return getattr(self.main_window, attr_name, None)

    # ── Tools → "Scheme Lists" (2026-09-06, plan scheme_list §5.3 / Stage 5a)
    # "Record..." captures the source the user picks in a TWO-tab dialog — "By
    # sheet" (root sheet + sub-sheet checklist, the DEFAULT) or "By selection"
    # (the current board selection, unchanged P2 behavior) — as a named Scheme
    # List record; "Reread..." re-syncs the Scheme List record currently
    # SELECTED in the Config tree against the live board (the form button + the
    # context menu are the other two legs of the triple exposure). Neither ever
    # applies anything to the board — this is the pure Config side (design §3).

    def record_scheme_list(self) -> None:
        """Main menu "Tools -> Scheme Lists -> Record..." (plan §5.3 / Stage
        5a.3): open the two-tab Record dialog ("By sheet" default / "By
        selection"), derive the capture refs from what the user picked, ask a
        unique name inside the dialog, run capture on the worker (never
        blocking the UI — live-board IPC), confirm any boundary exclusions,
        and write the record to the default storage file (auto-including it on
        first use). No anchor is picked at Record time — the record's frame is the
        captured region's centre and its pivot defaults to that centre
        (design_2026_09_07_scheme_list_pivot.md)."""
        from .worker import start_long_op
        root_path = self.root_metadata_dock.root_path
        if root_path is None:
            QMessageBox.warning(self.main_window, _("Scheme Lists"),
                                _("Set the project root first."))
            return
        connection = self.main_window.connection
        board = getattr(connection, "board", None)
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            # Same connection-state rule as the Re-source flow above
            # (plan_2026_09_11_no_modals_and_busy_kicad X.1).
            show_message(_("Connect to KiCad first."), _ERROR_STYLE, logger)
            return
        # Э2 (plan_2026_09_12_busy_indicator): the Tools-menu QAction that
        # started this flow is disabled until the capture finishes, so the same
        # entry cannot put a second live-board read on the shared kipy socket.
        trigger = self._menu_trigger_action("record_scheme_list_action")
        # source_sheet derivation (capture_scheme_list's sheet_names parameter)
        # needs the {uuid: sheetname} map the project config carries
        # (ctx.sheet_names). Best-effort: a broken config must not block Record
        # — it only leaves source_sheet None (record is "in place only").
        # Loaded BEFORE the dialog (2026-09-07 fix,
        # plan_2026_09_07_scheme_list_sheet_names_empty.md): the live Board's
        # OWN sheet_names is always {} (Board.connect() in gui/connection.py
        # never passes schematic_dir), so every Selected.sheet from the raw
        # snapshot is a list of None — the config-based map is the only real
        # source, and the "By sheet" tab needs it re-resolved into the
        # snapshot it is built from, not just for source_sheet afterwards.
        sheet_names: Dict = {}
        try:
            from kicadstamp.config import load_config
            _cfg, ctx = load_config(str(root_path))
            sheet_names = dict(getattr(ctx, "sheet_names", {}) or {})
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "Record: could not load sheet_names from %s — source_sheet "
                "will be left unset", root_path, exc_info=True)
        # Full live snapshot (Board.select with no filters, refreshed off-
        # thread) feeds the "By sheet" tab's sheet-combo/checklist; the polled
        # board selection feeds the secondary "By selection" tab only.
        snapshot = snapshot_with_resolved_sheets(
            getattr(connection, "snapshot", None) or [], sheet_names)
        selection_refs = sorted({getattr(s, "ref", None) for s in self._selection_footprints
                                 if getattr(s, "ref", None)})
        dialog = RecordSchemeListDialog(
            snapshot, selection_refs, self.main_window,
            adapter=adapter, selected_footprints=self._selection_footprints,
            # Commit G — "Take from selection" reads the CURRENT board selection
            # at click time (the dialog outlives the open-time snapshot; the
            # polled copy stays fresh under its modal event loop).
            selection_provider=lambda: list(self._selection_footprints),
            # Commit H — the recorded refs' positions come from the LIVE
            # full-board snapshot (connection.snapshot, refreshed by the poll
            # timer under the dialog's modal event loop), never from a direct
            # adapter.get_footprints() IPC on the shared kipy REQ socket
            # (plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_fix.md §0).
            snapshot_provider=lambda: getattr(connection, "snapshot", None) or [],
            # R.2.1 (2026-09-11, plan_2026_09_11_stale_snapshot_positions.md):
            # same as the Re-source dialog — the recorded refs' positions must
            # not come from a snapshot frozen at connect time, so the dialog
            # rebuilds it on the worker thread first.
            connection=connection)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, _sheet_path, checked_paths = dialog.result_data()
        if not name:
            QMessageBox.warning(self.main_window, _("Scheme Lists"),
                                _("Name is required."))
            return
        try:
            pivot = dialog.pivot_value()  # Pivot/Anchor tab (Commit F)
        except ValidationError as e:
            QMessageBox.warning(self.main_window, _("Scheme Lists"), str(e))
            return
        refs = record_refs_for(snapshot, dialog.is_by_sheet(), checked_paths,
                               selection_refs)
        if not refs:
            QMessageBox.warning(
                self.main_window, _("Scheme Lists"),
                _("No footprints to record — pick a sheet that has footprints "
                  "on the 'By sheet' tab, or select footprints on the board "
                  "for 'By selection'."))
            return
        # Duplicate pre-checks BEFORE the expensive capture (plan §2): the
        # loader validates these at load, but a wasted board read must not
        # happen over a record that cannot be saved.
        problems = scheme_list_duplicate_problems(root_path, name, refs)
        if problems:
            QMessageBox.warning(self.main_window, _("Cannot record Scheme List"),
                                "\n".join(problems))
            return
        # 5c.1 — a "By sheet" Record persists the CHECKED leaf paths as the
        # record's scope (a later Reread recomputes the current scope from
        # them); a "By selection" Record persists no scope (None).
        if dialog.is_by_sheet():
            scope_sheet_paths = [list(p) for p in (checked_paths or [])]
        else:
            scope_sheet_paths = None
        # Named presets (plan_2026_09_06_scheme_list_named_presets.md §7): a
        # non-empty "Save as preset" field makes the CURRENT checked checklist
        # the record's FIRST named preset. A brand-new record has no prior
        # library (capture's [] default when nothing to save).
        preset_name = dialog.preset_name_to_save()
        if preset_name:
            payload_scope_presets = [
                {"name": preset_name,
                 "sheet_paths": [list(p) for p in checked_paths]}]
        else:
            payload_scope_presets = None
        payload = {"board": board, "name": name, "refs": refs,
                   "root": str(root_path),
                   "sheet_names": sheet_names,
                   "pivot": list(pivot),
                   "scope_sheet_paths": scope_sheet_paths,
                   "scope_presets": payload_scope_presets}
        scheme_widgets = [trigger] if trigger is not None else []
        self._scheme_active_op = start_long_op(
            connection, scheme_widgets, self._run_record_capture,
            self._finish_record_capture, self._on_record_op_failed, payload,
            busy_text=_("reading the board"))

    def _run_record_capture(self, payload: Dict) -> Dict[str, Any]:
        """Worker thread: the actual capture (live-board IPC) — never touches
        a widget. Phase-1 (the plain Record capture) runs WITHOUT
        boundary_net_actions — every boundary net defaults to exclude (v1).
        G2's phase-2 re-runs the SAME worker with the user's per-net
        boundary_net_actions, so a truncate choice re-captures the clipped
        copper. A payload without the key is byte-identical to v1."""
        from kicadstamp.config.models import SchemeListScopePreset
        from kicadstamp.scheme_list_capture import capture_scheme_list
        try:
            record = capture_scheme_list(
                name=payload["name"], refs=payload["refs"],
                adapter=payload["board"].adapter,
                sheet_names=payload.get("sheet_names"),
                pivot=payload.get("pivot"),
                scope_sheet_paths=payload.get("scope_sheet_paths"),
                scope_presets=[SchemeListScopePreset(**p)
                               for p in (payload.get("scope_presets") or [])],
                boundary_net_actions=payload.get("boundary_net_actions"))
        except ApiError as e:
            # KiCad IPC failure = board STATE, not a bug (plan_2026_09_11_no_
            # modals_and_busy_kicad X.2.2): the human explanation (AS_BUSY ->
            # "finish the unfinished tool in KiCad"), never a raw stack.
            return {"error": api_error_message(e)}
        except Exception as e:  # noqa: BLE001 — ValidationError family surfaces verbatim
            logging.getLogger(__name__).exception("Scheme List record capture failed")
            return {"error": str(e)}
        # `payload` rides along so phase-1's finish can re-run this worker with
        # the SAME Record payload + the chosen boundary_net_actions (G2).
        return {"record": record, "root": payload["root"], "payload": payload}

    def _finish_record_capture(self, result: Dict[str, Any]) -> None:
        """UI thread, phase-1 (G2, plan §5-G2): boundary-net decision, then
        either write phase-1 or launch phase-2:
          - no boundary_nets -> write phase-1 straight away (no dialog);
          - dialog Cancel (None) -> nothing is written (as v1 Cancel);
          - all-exclude ({}) -> write the ALREADY-captured phase-1 record (no
            second IPC — the v1 regression path);
          - a truncate choice -> phase-2: re-run _run_record_capture with the
            SAME payload + boundary_net_actions and write THAT result."""
        self._scheme_active_op = None
        if result.get("error"):
            QMessageBox.warning(self.main_window, _("Record failed"),
                                result["error"])
            return
        record = result["record"]
        if not record.boundary_nets:
            self._write_record_result(result)
            return
        actions = choose_boundary_actions(self.main_window, record.boundary_nets)
        if actions is None:
            return  # Cancel — nothing is written (as v1 Cancel)
        if not any(a == "truncate" for a in actions.values()):
            # All-exclude — the phase-1 record already IS what v1 would write.
            self._write_record_result(result)
            return
        # Truncate chosen: phase-2 re-captures with the per-net actions.
        from .worker import start_long_op
        payload2 = dict(result["payload"])
        payload2["boundary_net_actions"] = actions
        connection = self.main_window.connection
        # No guard widget (Э2, plan_2026_09_12_busy_indicator): this second leg
        # is started from the phase-1 COMPLETION HANDLER, not from a click —
        # between the two, the boundary dialog is modal, so no menu can be
        # reached and nothing is left to disable.
        self._scheme_active_op = start_long_op(
            connection, (), self._run_record_capture,
            self._finish_record_capture_phase2, self._on_record_op_failed,
            payload2, busy_text=_("reading the board"))

    def _finish_record_capture_phase2(self, result: Dict[str, Any]) -> None:
        """UI thread, phase-2 completion (G2): the record was re-captured WITH
        the user's boundary_net_actions — write it directly and NEVER re-open
        the decision dialog (every boundary net of the result already carries
        the decided action)."""
        self._scheme_active_op = None
        if result.get("error"):
            QMessageBox.warning(self.main_window, _("Record failed"),
                                result["error"])
            return
        self._write_record_result(result)

    def _write_record_result(self, result: Dict[str, Any]) -> None:
        """Shared Record tail (phase-1 all-exclude and phase-2): persist the
        record to the default storage file, refresh the Config tree and show the
        v1 status message."""
        record = result["record"]
        root_path = Path(result["root"])
        try:
            written = write_scheme_list_record(root_path, record)
        except OSError as e:
            QMessageBox.warning(self.main_window, _("Record failed"), str(e))
            return
        self.config_tree_dock.refresh()
        self.config_tree_dock.graph_changed.emit()
        show_message(
            _("Recorded Scheme List {name!r} — {components} components, "
              "{vias} vias, {tracks} tracks -> {path}").format(
                name=record.name, components=len(record.components),
                vias=len(record.vias), tracks=len(record.tracks),
                path=display_path(written)),
            "", logging.getLogger(__name__))

    def _on_record_op_failed(self, message: str) -> None:
        self._scheme_active_op = None
        QMessageBox.warning(self.main_window, _("Record failed"),
                            _("Operation failed: {error}").format(error=message))

    def _run_resource_capture(self, payload: Dict) -> Dict[str, Any]:
        """Worker thread — phase-1 Re-source capture ONLY (G3, plan §5-G3);
        never touches a widget. Captures the fresh record (name/refs/
        source_sheet/geometry from the NEW source) and returns it + root +
        target_path (the file that OWNS the record — the Re-source write target;
        the record is never moved between files, the §7 invariant) + the original
        payload (so the phase-1 finish can re-run this worker with the SAME
        payload + the user's boundary_net_actions). NO write happens here — G3
        splits capture and write (before G3 this worker captured AND wrote, so a
        truncate choice could never reach capture before the record was
        persisted). A payload without the boundary_net_actions key is
        byte-identical to v1."""
        from kicadstamp.config.models import SchemeListScopePreset
        from kicadstamp.scheme_list_capture import capture_scheme_list
        try:
            record = capture_scheme_list(
                name=payload["name"], refs=payload["refs"],
                adapter=payload["board"].adapter,
                sheet_names=payload.get("sheet_names"),
                pivot=payload.get("pivot"),
                scope_sheet_paths=payload.get("scope_sheet_paths"),
                scope_presets=[SchemeListScopePreset(**p)
                               for p in (payload.get("scope_presets") or [])],
                boundary_net_actions=payload.get("boundary_net_actions"))
        except ApiError as e:
            # Same board-state rule as the record capture above (X.2.2).
            return {"error": api_error_message(e)}
        except Exception as e:  # noqa: BLE001 — ValidationError family surfaces verbatim
            logging.getLogger(__name__).exception(
                "Scheme List re-source capture failed")
            return {"error": str(e)}
        return {"record": record, "root": payload["root"],
                "target_path": payload.get("target_path"), "payload": payload}

    def _finish_resource_capture(self, result: Dict[str, Any]) -> None:
        """UI thread, phase-1 (G3, plan §5-G3): boundary-net decision, then
        either write phase-1 or launch phase-2:
          - no boundary_nets -> write phase-1 straight away (no dialog);
          - dialog Cancel (None) -> nothing is written (the owner file is
            untouched);
          - all-exclude ({}) -> write the ALREADY-captured phase-1 record (no
            second IPC — the v1 record-with-exclusions regression);
          - a truncate choice -> phase-2: re-run _run_resource_capture with the
            SAME payload + boundary_net_actions and write THAT result to
            target_path."""
        self._scheme_active_op = None
        if result.get("error"):
            QMessageBox.warning(self.main_window, _("Re-source failed"),
                                result["error"])
            return
        record = result["record"]
        if not record.boundary_nets:
            self._write_resource_result(result)
            return
        actions = choose_boundary_actions(self.main_window, record.boundary_nets)
        if actions is None:
            return  # Cancel — nothing is written (owner file untouched)
        if not any(a == "truncate" for a in actions.values()):
            # All-exclude — the phase-1 record already IS what v1 would write.
            self._write_resource_result(result)
            return
        # Truncate chosen: phase-2 re-captures with the per-net actions.
        from .worker import start_long_op
        payload2 = dict(result["payload"])
        payload2["boundary_net_actions"] = actions
        connection = self.main_window.connection
        # No guard widget — same reasoning as the Record phase-2 above: started
        # from a completion handler behind a modal dialog, never from a click.
        self._scheme_active_op = start_long_op(
            connection, (), self._run_resource_capture,
            self._finish_resource_capture_phase2, self._on_resource_op_failed,
            payload2, busy_text=_("reading the board"))

    def _finish_resource_capture_phase2(self, result: Dict[str, Any]) -> None:
        """UI thread, phase-2 completion (G3): the record was re-captured WITH
        the user's boundary_net_actions — write it directly to target_path and
        NEVER re-open the decision dialog (every boundary net of the result
        already carries the decided action)."""
        self._scheme_active_op = None
        if result.get("error"):
            QMessageBox.warning(self.main_window, _("Re-source failed"),
                                result["error"])
            return
        self._write_resource_result(result)

    def _write_resource_result(self, result: Dict[str, Any]) -> None:
        """Shared Re-source tail (phase-1 all-exclude and phase-2): persist the
        record under its SAME name IN THE FILE THAT OWNS IT (target_path — like
        Reread Apply, NEVER the default storage file: re-sourcing never
        moves a record between files), refresh the Config tree and show the v1
        status message."""
        record = result["record"]
        root_path = Path(result["root"])
        target_path = (Path(result["target_path"])
                       if result.get("target_path") else None)
        try:
            written = write_scheme_list_record(root_path, record,
                                               target_path=target_path)
        except OSError as e:
            QMessageBox.warning(self.main_window, _("Re-source failed"), str(e))
            return
        self.config_tree_dock.refresh()
        self.config_tree_dock.graph_changed.emit()
        show_message(
            _("Re-sourced Scheme List {name!r} — {components} components, "
              "{vias} vias, {tracks} tracks -> {path}").format(
                name=record.name, components=len(record.components),
                vias=len(record.vias), tracks=len(record.tracks),
                path=display_path(written)),
            "", logging.getLogger(__name__))

    def _on_resource_op_failed(self, message: str) -> None:
        self._scheme_active_op = None
        QMessageBox.warning(self.main_window, _("Re-source failed"),
                            _("Operation failed: {error}").format(error=message))

    def reread_scheme_list(self) -> None:
        """Main menu "Tools -> Scheme Lists -> Reread..." (plan §5.3): the
        Scheme List record currently SELECTED in the Config tree — load it into
        the Scheme List right page and run the Reread flow there."""
        selection = self.config_tree_dock.selected_scheme_list()
        if selection is None:
            show_message(_("Pick a Scheme List in the Config tree first."), "",
                         logging.getLogger(__name__))
            return
        file_path, entry = selection
        self._focus_config_tree_dock()
        self._reread_scheme_list_from_tree(entry, file_path)

    def run_forest_full_redraw(self) -> None:
        """Main menu "Tools -> Trees -> Full redraw (all trees and modules)..."
        (plan
        2026-09-02 tree_module_embedding P3 п.3): the forest-wide, module-aware
        curated redraw across ALL trees. TreesDock owns the trees/cfg/ctx and
        the worker callback plumbing (no new dock button — menu only).

        The QAction is handed down as the operation's guard widget (Э2,
        plan_2026_09_12_busy_indicator): greyed out for the duration, so the
        same menu entry cannot start a second forest redraw."""
        self.trees_dock._run_forest_redraw(
            self._menu_trigger_action("full_redraw_action"))

    def open_instances_dialog(self) -> None:
        """Main menu "Tools -> Trees -> Instances..." (2026-09-02, plan tree_instances
        P3): modal dialog editing the `tree_instances:` SHORT declarations of
        one template tree ({name, sheet} rows, add/remove). Writes the section
        via config_writer.upsert_tree_instances — the dialog GENERATES nothing;
        materialization happens at the next load — then reloads TreesDock (and
        refreshes the Config tree) so the regenerated read-only instance tabs
        appear."""
        root_path = self.root_metadata_dock.root_path
        if root_path is None:
            QMessageBox.warning(self.main_window, _("Tree instances"),
                                _("Set the project root first."))
            return
        from kicadstamp.config import load_config
        try:
            cfg, _ctx = load_config(str(root_path))
        except Exception as e:  # noqa: BLE001 — a broken config must not crash the GUI
            QMessageBox.warning(self.main_window, _("Tree instances"),
                                _("Failed to load config: {error}").format(error=e))
            return
        dialog = TreeInstancesDialog(self.main_window, root_path, cfg)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.trees_dock.reload_trees()
            self.config_tree_dock.refresh()

    # ── Tools → Trees submenu: whole-tree actions (2026-09-03, plan
    #    plan_2026_09_03_trees_menu_tools.md) ─────────────────────────────
    # The TreesDock's whole-tree action buttons moved to the top-level menu
    # Tools → Trees; these delegates are the menu QActions' call points. Every
    # one focuses the Trees dock first so the action's context (the active
    # tree tab, the checkbox selection, the anchor readout) is visible.

    def _focus_trees_dock(self) -> None:
        """Bring the Trees tab to the front of the central tab group."""
        self.show_left_page(self.trees_dock)

    def create_tree(self) -> None:
        """Tools → Trees → Create tree…: the dock's empty-tree creation flow —
        name + the six-mode anchor dialog; staged (auto-staged) until
        File > Save."""
        self._focus_trees_dock()
        self.trees_dock._on_create_tree()

    def rename_tree(self) -> None:
        """Tools → Trees → Rename tree…: rename the CURRENT tree (the dock's
        active tab); staged until File > Save."""
        self._focus_trees_dock()
        self.trees_dock._on_rename_tree()

    def delete_tree(self) -> None:
        """Tools → Trees → Delete tree…: delete the CURRENT tree (the dock's
        active tab), confirmed; staged until File > Save."""
        self._focus_trees_dock()
        self.trees_dock._on_delete_tree()

    def instantiate_from_cell(self) -> None:
        """Tools → Trees → "Instantiate from Cell…" AND the TreesDock anchor
        context-menu action (2026-09-03, plan instantiate_from_entity): add
        ONE new group into the CURRENT tree by reusing an EXISTING Cell as its
        internal layout — a new Entity (no refs; roles resolve at Apply by
        cluster/sheet) + a top-level placement node, both STAGED (nothing is
        written until the global Save). The live board selection
        (_selection_footprints, set by set_board_selection) feeds the dialog's
        opt-in "take from selection" positioning."""
        if self.root_metadata_dock.root_path is None:
            QMessageBox.warning(self.main_window, _("Instantiate from Cell"),
                                _("Set the project root first."))
            return
        self._focus_trees_dock()
        selected = getattr(self, "_selection_footprints", []) or []
        raw_items = getattr(self, "_selection_raw_items", []) or []
        self.trees_dock._instantiate_from_cell(selected, raw_items)

    def anchor_position(self) -> None:
        """Tools → Trees → Anchor position: refresh the dock's read-only live
        anchor-position readout for the CURRENT tree (background worker —
        plan_2026_09_12_anchor_position_on_worker Э1). The menu QAction is the
        operation's guard widget (Э2, plan_2026_09_12_busy_indicator): greyed
        out while the read holds the shared kipy socket, so the same entry
        cannot start a second read."""
        self._focus_trees_dock()
        self.trees_dock._refresh_anchor_live_position(
            self._menu_trigger_action("anchor_position_action"))

    def redraw_selected(self) -> None:
        """Tools → Trees → Redraw selected: curated redraw of the CURRENT
        tree's CHECKED nodes (background worker). The menu QAction is handed
        down as the guard widget (Э2, plan_2026_09_12_busy_indicator)."""
        self._focus_trees_dock()
        self.trees_dock._on_redraw_selected(
            self._menu_trigger_action("redraw_selected_action"))

    def reread_internode_copper(self) -> None:
        """Tools → Trees → "Reread inter-node copper" (plan_2026_09_12_internode_
        copper_core Э4): re-read the CURRENT tree's copper between pads from the
        live board — a background worker, no dialog, the report in the Log. The
        menu QAction is the operation's guard widget (Э2)."""
        self._focus_trees_dock()
        self.trees_dock._on_reread_internode_copper(
            self._menu_trigger_action("reread_internode_action"))

    def identify_selected_copper(self) -> None:
        """Tools → Trees → "Whose copper is this?" (plan_2026_09_12_select_copper_
        by_record Э3): map the live board SELECTION back to the net_traces records
        that own it — a background worker, the answer in the Log, read-only. The
        menu QAction is the operation's guard widget (Э2)."""
        self._focus_trees_dock()
        self.trees_dock._on_identify_selected_copper(
            self._menu_trigger_action("identify_copper_action"))

    def redraw_whole_tree(self) -> None:
        """Tools → Trees → Redraw whole tree: curated redraw of EVERY node of
        the CURRENT tree (background worker). The menu QAction is handed down as
        the guard widget (Э2, plan_2026_09_12_busy_indicator)."""
        self._focus_trees_dock()
        self.trees_dock._on_redraw_whole_tree(
            self._menu_trigger_action("redraw_whole_tree_action"))

    def _refresh_snapshot_then(self, title: str, on_ready) -> None:
        """R.2.2 (2026-09-11, plan_2026_09_11_stale_snapshot_positions.md) — the
        ONE shared rebuild point of the two Extract flows.

        Both flows read ``connection.snapshot`` three times: the fully-selected
        Cluster detection and BOTH geometry payload builders (the inter-cluster
        net detection and the entity/anchor position reads). That snapshot is
        rebuilt only by connect()/manual refresh — MainWindow._poll's automatic
        tick is a deliberate no-op once connected — so it is frozen at connect
        time: a component ADDED to a cluster in KiCad afterwards is missing from
        the snapshot, which makes a PARTIAL selection look fully selected (and
        every position read out of it is stale). The rebuild runs on the worker
        thread — never a direct adapter/IPC call on the UI thread, which is
        exactly the 2026-08-08 hang fixed by Commit H
        (plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_fix.md) — and
        ``on_ready`` then continues the flow on the UI thread with the snapshot
        already fresh.

        A connection with no live board behind it cannot be refreshed; the flow
        then proceeds on the cached snapshot (the documented fallback, see
        gui/worker.py::refresh_snapshot_then).

        The rebuild names itself in the status bar (Э1/Э2 of
        plan_2026_09_12_busy_indicator): every Extract flow's first visible step
        IS this board read."""
        from .worker import refresh_snapshot_then
        refresh_snapshot_then(
            self.main_window.connection, (), on_ready,
            lambda message: self._show_snapshot_refresh_error(title, message),
            busy_text=_("reading the board"))

    def _show_snapshot_refresh_error(self, title: str, message: str) -> None:
        """UI thread: the worker could not rebuild the board snapshot (the live
        board is gone — BoardConnection.refresh() drops the connection). Report
        it instead of silently building the payload from stale coordinates."""
        QMessageBox.warning(
            self.main_window, title,
            _("Could not refresh the board snapshot: {error}").format(error=message))

    def extract_tree_from_selection(self) -> None:
        """Main menu "Tools -> Trees -> Extract tree...".

        R.2.2 (2026-09-11, plan_2026_09_11_stale_snapshot_positions.md): the
        flow itself (below) reads the connection's board SNAPSHOT to decide
        which Clusters are selected WHOLE and to build its geometry payloads,
        and that snapshot freezes at connect/manual-refresh time. It is
        therefore rebuilt first, on the worker thread — never a direct adapter
        call on the UI thread — via the ONE shared point
        :meth:`_refresh_snapshot_then`; everything else is unchanged."""
        self._refresh_snapshot_then(_("Extract tree"),
                                    self._extract_tree_from_selection_now)

    def _extract_tree_from_selection_now(self) -> None:
        """Main menu "Tools -> Trees -> Extract tree..." (2026-09-01, plan
        extract_selection_as_tree.md): build a NEW tree from the current board
        selection and save it into the root config's trees: section.

        Flow: detect the FULLY-selected Clusters (reead.py's detection — same
        selection truth as Re-read) -> show the 3-tab dialog (clusters /
        anchor / inter-cluster nets) -> on OK, build the Tree (every checked
        cluster = a top-level kind="placement" node with xy = the Entity's
        live position minus the live anchor base) and the checked inter-cluster
        nets as net_traces: records -> save through config_writer (backup +
        write + round-trip link_trees) -> refresh TreesDock / ConfigTreeDock.
        """
        connection = self.main_window.connection
        board = getattr(connection, "board", None)
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            QMessageBox.warning(self.main_window, _("Extract tree"),
                                _("Not connected."))
            return
        root_path = self.root_metadata_dock.root_path
        if root_path is None:
            QMessageBox.warning(self.main_window, _("Extract tree"),
                                _("Set the project root first."))
            return
        from kicadstamp.config import load_config
        try:
            cfg, ctx = load_config(str(root_path))
        except Exception as e:  # noqa: BLE001 — a broken config must not crash the GUI
            QMessageBox.warning(self.main_window, _("Extract tree"),
                                _("Failed to load config: {error}").format(error=e))
            return
        sheet_names = dict(ctx.sheet_names or {})

        from .docks.reead import fully_selected_clusters
        # V.2: the engine reports WHICH gate dropped each group (structured,
        # not text); the formatter turns them into a concrete message naming the
        # real cause instead of always blaming the selection.
        rejections: list = []
        clusters = fully_selected_clusters(
            self._selection_footprints,
            list(connection.snapshot or []),
            list(cfg.entities),
            (),
            sheet_names=sheet_names,
            rejections=rejections)
        # Diagnostic + defensive filter (same rationale as the retired
        # Re-read flow: a row must be a sane single-line cluster).
        clusters = [c for c in clusters if c.cluster and "\n" not in c.cluster]
        if not clusters:
            detail = format_cluster_rejections(rejections)
            if detail:
                # The on-screen message keeps the short Ref list; the full
                # detail goes to the Log (plan V.2).
                logging.info("Extract tree: no fully selected cluster — full "
                             "detail:\n%s", rejections_log_detail(rejections))
            QMessageBox.warning(
                self.main_window, _("Extract tree"),
                detail or _("No fully selected Cluster found — select ALL "
                            "components of a cluster (its Cluster tag + sheet) "
                            "first."))
            return

        from .docks.tree_from_selection import (
            cluster_errors,
            cluster_origin_role,
            create_cell_and_entity_for_cluster,
            detect_inter_cluster_nets,
            resolve_cluster_entity,
            resolve_cluster_live_position_mm,
            resolve_entity_live_position_mm,
            resolve_role_anchor_base_mm,
            tree_anchor_from_cluster_entity,
        )
        from .docks.tree_from_selection_dialog import TreeFromSelectionDialog

        # Plan Э5: the STRICT geometric rule (no rule-net exclusion, no coverage
        # threshold) — the same classification the re-read uses, so a created
        # and a re-read tree carry the same copper.
        inter_nets = detect_inter_cluster_nets(
            self._selection_raw_items, clusters, adapter=adapter)

        # Per-row "no cell" errors (block OK in the dialog) + per-row "existing
        # cluster anchor" prefills + the live Entity positions for the offset
        # preview. A failed live read just omits the position (the node is then
        # saved without xy — live-position rule at apply), never a crash.
        errors = cluster_errors(clusters, cfg.entities, cfg)
        prefills: dict[int, object] = {}
        entity_positions: dict[str, tuple[float, float, float]] = {}
        for i, c in enumerate(clusters):
            entity_name, _cell, is_new = resolve_cluster_entity(c, cfg)
            if is_new:
                # Auto-derived Entity (phase A): no cell exists yet — the
                # autopositioning preview reads the cluster's own live role
                # (the cell's future zero-slot, see cluster_origin_role).
                role = cluster_origin_role(c, self._selection_footprints)
                if role:
                    try:
                        entity_positions[entity_name] = resolve_cluster_live_position_mm(
                            adapter, cfg, c, sheet_names, role)
                    except Exception as e:  # noqa: BLE001 — best-effort preview
                        logging.warning("Extract tree: live position of cluster %r "
                                        "unavailable: %s", c.cluster, e)
                continue
            entity = next((e for e in cfg.entities if e.name == entity_name), None)
            if entity is None:
                continue
            prefills[i] = tree_anchor_from_cluster_entity(entity, cfg)
            try:
                entity_positions[entity_name] = resolve_entity_live_position_mm(
                    adapter, cfg, entity, sheet_names)
            except Exception as e:  # noqa: BLE001 — live read, best-effort preview
                logging.warning("Extract tree: live position of Entity %r "
                                "unavailable: %s", entity_name, e)

        def _anchor_base_provider(anchor):
            if anchor is None or anchor.role is None:
                return None
            try:
                return resolve_role_anchor_base_mm(adapter, cfg, anchor, sheet_names)
            except Exception:  # noqa: BLE001 — live read, best-effort preview
                return None

        dialog = TreeFromSelectionDialog(
            clusters, inter_nets, [t.name for t in cfg.trees],
            sheet_names=sheet_names,
            role_candidates=self.trees_dock._live_roles(),
            cluster_candidates=self.trees_dock._live_clusters(),
            parent=self.main_window,
            cluster_errors=errors,
            entity_positions=entity_positions,
            anchor_base_provider=_anchor_base_provider,
            prefills=prefills)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dialog.selected_clusters()
        if not selected:
            QMessageBox.warning(self.main_window, _("Extract tree"),
                                _("No clusters selected."))
            return
        tree_name = dialog.tree_name()
        anchor = dialog.build_anchor()
        if anchor is None:
            QMessageBox.warning(self.main_window, _("Extract tree"),
                                _("Role is required for the tree anchor."))
            return

        # Autopositioning: node xy = Entity live position - live anchor base.
        # resolve_role_anchor_base_mm also returns the anchor's LIVE rotation,
        # which build_tree_from_clusters needs to capture each node's offset in
        # the anchor's LOCAL frame and its rotation relative to the anchor (a
        # non-zero anchor angle at capture must not be re-applied on redraw).
        anchor_base = None
        try:
            anchor_base = resolve_role_anchor_base_mm(adapter, cfg, anchor, sheet_names)
        except Exception as e:  # noqa: BLE001 — without it nodes are saved without xy
            logging.warning("Extract tree: anchor base unavailable — nodes will "
                            "have no xy: %s", e)
        anchor_rot_deg = (anchor_base[2]
                          if anchor_base is not None and len(anchor_base) > 2
                          else None)

        from .docks.tree_from_selection import build_tree_from_clusters
        # entity_positions already holds only the positions that resolved live
        # (failed reads are omitted -> that node is saved without xy).
        checked_nets = dialog.selected_units()
        # Plan Э5: capture the checked inter-node UNITS as records BEFORE the
        # tree is built — a net_trace node's ref IS the record's identity
        # (name:, else a legacy record's net:), and the name is generated here.
        # The SAME builder the re-read uses (internode_capture.capture_units),
        # so a created and a re-read tree carry the same copper.
        from kicadstamp.internode_capture import capture_units
        node_by_ref = {ref: (c.cluster or c.sheet or "?")
                       for c in selected for ref in c.refs}
        captures, capture_warnings = capture_units(
            adapter, [n.unit for n in checked_nets if n.unit is not None],
            area_footprints=self._selection_footprints,
            node_by_ref=node_by_ref,
            sheet_names=sheet_names,
            existing_names=[nt.name or nt.net for nt in cfg.net_traces])
        for warning in capture_warnings:
            logging.warning("Extract tree: %s", warning)
        node_refs = [cap.identity for cap in captures]
        # Phase E (2026-09-01): entering an existing tree's name = RE-EXTRACT —
        # the tree is rebuilt from the current selection and replaces the old one.
        existing_tree = next((t for t in cfg.trees if t.name == tree_name), None)
        tree, build_errors = build_tree_from_clusters(
            selected, tree_name, anchor, cfg.entities, cfg,
            entity_positions=entity_positions, anchor_base=anchor_base,
            anchor_rot_deg=anchor_rot_deg,
            net_nodes=node_refs,
            allow_existing=existing_tree is not None)
        if tree is None:
            QMessageBox.warning(self.main_window, _("Extract tree"),
                                _("Cannot build the tree:\n{errors}")
                                .format(errors="\n".join(build_errors)))
            return

        # ── Save: auto-created cells/entities + net_traces + trees: ─────────
        from dataclasses import replace
        from kicadstamp.config import Entity, NetTrace, load_tree
        from kicadstamp.config_writer import read_data, write_data
        from kicadstamp.link_trees import link_trees
        from kicadstamp.net_trace_extract import net_trace_to_dict
        from kicadstamp.trees import tree_to_dict
        from .docks.entity_delete import backup_file
        try:
            backup_file(root_path)
            data = read_data(root_path)
            cells_data = data.setdefault("cells", {})
            new_entities: list[Entity] = []
            for c in selected:
                # Shared "one cluster -> Cell (if new) + Entity" step — the same
                # code "Extract cluster..." uses (2026-09-03, plan
                # extract_cluster_entity); a pure refactor of the inline block
                # that lived here (2026-09-01, extract_selection_as_tree.md).
                ent = create_cell_and_entity_for_cluster(
                    adapter, c, cfg, cells_data,
                    self._selection_footprints, self._selection_raw_items)
                if ent is None:
                    # An Entity for (cluster, sheet) already exists — it is
                    # reused, nothing to stage or append.
                    continue
                data.setdefault("entities", []).append(ent)
                new_entities.append(Entity(
                    name=ent["name"], cell=ent["cell"],
                    cluster=ent.get("cluster"), sheet=ent.get("sheet")))
            # Phase B+C+D (plan Э5): write the captured units as net_traces:
            # records BEFORE the tree write, so the tree's net_trace nodes
            # resolve against cfg.net_traces at link_trees time. Upsert by
            # IDENTITY (name:, else a legacy record's net:) — matching a named
            # record by its net would overwrite an unrelated bridge of it.
            net_traces_data = data.setdefault("net_traces", [])
            new_net_traces: list[NetTrace] = []
            for cap in captures:
                entry = net_trace_to_dict(cap.record)
                for i, e in enumerate(net_traces_data):
                    if isinstance(e, dict) \
                            and (e.get("name") or e.get("net")) == cap.identity:
                        net_traces_data[i] = entry
                        break
                else:
                    net_traces_data.append(entry)
                new_net_traces.append(cap.record)
            # Phase E: an existing tree with this name is REPLACED (re-extract),
            # not duplicated.
            kept_trees = [t for t in cfg.trees if t.name != tree_name]
            trees_dict = [tree_to_dict(t) for t in kept_trees] + [tree_to_dict(tree)]
            data["trees"] = trees_dict
            write_data(root_path, data)
            reloaded = [load_tree(t) for t in trees_dict]
            link_cfg = replace(
                cfg,
                entities=list(cfg.entities) + new_entities,
                net_traces=list(cfg.net_traces) + new_net_traces)
            link_trees(link_cfg, reloaded)
            # Phase E (plan_2026_09_09_..._phase_e): remember the (Cluster,
            # Sheet) each newly-created Entity's cell was extracted from — the
            # page-opening hint for the cell-anchor page / CellDock's "Select
            # cluster on the board" button. Only for NEW Entities (an existing
            # Entity's cluster was already remembered at its own creation);
            # a reused existing cell gets re-pointed to the new sheet instance.
            from .cell_edit_context import remember_cell_edit_context
            for _entity in new_entities:
                if getattr(_entity, "cell", None):
                    remember_cell_edit_context(
                        root_path, _entity.cell, _entity.cluster, _entity.sheet)
        except Exception as e:  # noqa: BLE001 — .bak is fresh; report, don't roll back
            QMessageBox.warning(self.main_window, _("Extract tree"),
                                _("Saved, but the round-trip check failed: {error}")
                                .format(error=e))
            return

        # ── Refresh: show the new tree + graph everywhere without a restart ──
        self.trees_dock.reload_trees()
        self.config_tree_dock.refresh()
        self.config_tree_dock.graph_changed.emit()
        QMessageBox.information(
            self.main_window, _("Extract tree"),
            _("Tree {name!r} saved to {path}.")
            .format(name=tree.name, path=root_path))

    def extract_cluster_from_selection(self) -> None:
        """Main menu "Tools -> Trees -> Extract cluster...".

        R.2.2 (2026-09-11, plan_2026_09_11_stale_snapshot_positions.md): same
        reason as "Extract tree..." above — the fully-selected-Cluster
        detection must not run against a snapshot frozen at connect time, so
        the shared :meth:`_refresh_snapshot_then` rebuilds it on the worker
        thread first."""
        self._refresh_snapshot_then(_("Extract cluster"),
                                    self._extract_cluster_from_selection_now)

    def _extract_cluster_from_selection_now(self) -> None:
        """Main menu "Tools -> Trees -> Extract cluster..." (2026-09-03, plan
        extract_cluster_entity): extract ONE fully-selected Cluster from the
        current selection as a standalone flat Entity — WITHOUT building any
        tree node (no anchor, no inter-cluster net_traces). The slug-named Cell
        is generated from the cluster's own selection when it doesn't exist
        yet; the (cluster, sheet)-matched Entity, when it exists, is REUSED
        (never duplicated). Placing the Entity (a manual tree node, a
        tree_instances template, ...) is a separate, later user step.

        Flow: adapter/root/cfg checks + fully_selected_clusters (the same
        selection truth as "Extract tree...", same empty warning) -> the small
        single-cluster ExtractClusterDialog -> on OK, persist through
        config_writer (backup_file + read_data/write_data, staged via
        WORKING_SET) -> ConfigTreeDock.refresh() + graph_changed (a new cell
        must reach every graph-derived combo).
        """
        connection = self.main_window.connection
        board = getattr(connection, "board", None)
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            QMessageBox.warning(self.main_window, _("Extract cluster"),
                                _("Not connected."))
            return
        root_path = self.root_metadata_dock.root_path
        if root_path is None:
            QMessageBox.warning(self.main_window, _("Extract cluster"),
                                _("Set the project root first."))
            return
        from kicadstamp.config import load_config
        try:
            cfg, ctx = load_config(str(root_path))
        except Exception as e:  # noqa: BLE001 — a broken config must not crash the GUI
            QMessageBox.warning(self.main_window, _("Extract cluster"),
                                _("Failed to load config: {error}").format(error=e))
            return
        sheet_names = dict(ctx.sheet_names or {})

        from .docks.reead import fully_selected_clusters
        # V.2: same structured diagnostics as "Extract tree..." — the concrete
        # cause, not the generic "select more" text.
        rejections: list = []
        clusters = fully_selected_clusters(
            self._selection_footprints,
            list(connection.snapshot or []),
            list(cfg.entities),
            (),
            sheet_names=sheet_names,
            rejections=rejections)
        # Diagnostic + defensive filter (same rationale as "Extract tree...": a
        # row must be a sane single-line cluster).
        clusters = [c for c in clusters if c.cluster and "\n" not in c.cluster]
        if not clusters:
            detail = format_cluster_rejections(rejections)
            if detail:
                logging.info("Extract cluster: no fully selected cluster — full "
                             "detail:\n%s", rejections_log_detail(rejections))
            QMessageBox.warning(
                self.main_window, _("Extract cluster"),
                detail or _("No fully selected Cluster found — select ALL "
                            "components of a cluster (its Cluster tag + sheet) "
                            "first."))
            return

        from .docks.tree_from_selection import create_cell_and_entity_for_cluster
        from .docks.extract_cluster_dialog import ExtractClusterDialog
        dialog = ExtractClusterDialog(
            self.main_window, clusters, cfg, self._selection_footprints)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        c = dialog.selected_cluster()
        if c is None:
            return
        entity_name = dialog.entity_name()

        # An Entity already exists for (cluster, sheet): reuse — nothing to
        # write, just confirm + refresh.
        if dialog.existing:
            self.config_tree_dock.refresh()
            QMessageBox.information(
                self.main_window, _("Extract cluster"),
                _("Entity {name!r} already exists for this Cluster + sheet — "
                  "reused, nothing new was created.").format(name=entity_name))
            return
        # Manual origin override (2026-09-04, plan extract_origin_pad_restore):
        # only read when a NEW Entity (+ its cell) is actually created; the
        # reuse path returns above. (None, None) = keep automatic detection.
        origin_role, origin_pad = dialog.origin_override()

        # ── Save: cell (if new) + entity, staged through config_writer ──────
        from kicadstamp.config_writer import read_data, write_data
        from .docks.entity_delete import backup_file
        cell_name = ""
        cell_new = False
        try:
            backup_file(root_path)
            data = read_data(root_path)
            cells_data = data.setdefault("cells", {})
            ent = create_cell_and_entity_for_cluster(
                adapter, c, cfg, cells_data,
                self._selection_footprints, self._selection_raw_items,
                entity_name=entity_name,
                origin_role=origin_role, origin_pad=origin_pad)
            if ent is None:
                # A matching Entity appeared between the dialog and the write —
                # treat it as a reuse, never a duplicate.
                self.config_tree_dock.refresh()
                QMessageBox.information(
                    self.main_window, _("Extract cluster"),
                    _("Entity {name!r} already exists for this Cluster + sheet "
                      "— reused, nothing new was created.").format(name=entity_name))
                return
            cell_name = ent["cell"]
            cell_new = cell_name not in cfg.cells
            data.setdefault("entities", []).append(ent)
            write_data(root_path, data)
        except Exception as e:  # noqa: BLE001 — .bak is fresh; report, don't roll back
            QMessageBox.warning(self.main_window, _("Extract cluster"),
                                _("Failed to save the Entity: {error}").format(error=e))
            return

        # Phase E (plan_2026_09_09_..._phase_e): remember the (Cluster, Sheet)
        # this cell was just created/extracted from — the page-opening hint for
        # the cell-anchor page and the CellDock "Select cluster on the board"
        # button. GUI-local state only, never written into the cell itself.
        from .cell_edit_context import remember_cell_edit_context
        remember_cell_edit_context(root_path, ent["cell"], c.cluster, c.sheet)

        # ── Refresh: the new cells:/entities: appear without a restart ──────
        self.config_tree_dock.refresh()
        self.config_tree_dock.graph_changed.emit()
        if cell_new:
            QMessageBox.information(
                self.main_window, _("Extract cluster"),
                _("Entity {name!r} with its new cell {cell!r} saved to {path}.")
                .format(name=ent["name"], cell=cell_name, path=root_path))
        else:
            QMessageBox.information(
                self.main_window, _("Extract cluster"),
                _("Entity {name!r} saved to {path} (cell {cell!r} already "
                  "existed).")
                .format(name=ent["name"], cell=cell_name, path=root_path))

    def _open_tools_dialog(self) -> None:
        """Show/raise the ONE live Tools dialog — non-modal, so the user can
        keep selecting on the board while it's open: the ~2s snapshot tick
        keeps feeding the same tools_dock instance inside it (refresh_known_
        nets). Closing via the window X just hides it (QDialog default), so
        the next open starts from the current state."""
        self.tools_dialog.show()
        self.tools_dialog.raise_()
        self.tools_dialog.activateWindow()

    def _open_cell_dialog(self) -> None:
        """Show/raise the ONE live Cell dialog (2026-09-04, plan
        plan_2026_09_04_celldock_to_dialog.md) — non-modal, so the user can
        keep selecting on the board while it's open: the ~2s snapshot tick
        keeps feeding the same cells_dock instance inside it (refresh_known_
        roles). Closing via the window X just hides it (QDialog default), so
        the next open starts from the current state."""
        self.cell_dialog.show()
        self.cell_dialog.raise_()
        self.cell_dialog.activateWindow()

    def _start_edit_entity_template(self, name) -> None:
        """ConfigTreeDock's entity_edit_requested delegate (double click on
        an Entities leaf, 2026-09-01, plan plan_2026_09_01_tools_dialog_and_
        entity_roles.md) — loads the named Entity into the live ToolsDock and
        opens the (non-modal) "Edit template" dialog with it."""
        self.tools_dock.load_entity(name)
        self._open_tools_dialog()

    def edit_template(self) -> None:
        """Main menu "Tools -> Edit template..." (2026-09-01) — opens the
        "Edit template" dialog; the Entity is picked inside it."""
        self._open_tools_dialog()

    def edit_cell(self) -> None:
        """Main menu "Tools -> Config -> Edit Cell..." (2026-09-04, plan
        plan_2026_09_04_celldock_to_dialog.md) — opens the (non-modal) Cell
        dialog; the Cell is picked inside it (or a fresh blank form via the
        Config tree's "Add cell...")."""
        self._open_cell_dialog()

    def _open_project_dialog(self) -> None:
        """Show/raise the ONE live Project dialog (File > "Project...",
        2026-09-01, plan project_settings_dialogs) — non-modal, so the user can
        keep working while it's open: the root_metadata_dock instance inside it
        keeps broadcasting root_changed / the Working-file combobox to every
        other dock. Closing via the window X just hides it (QDialog default),
        so the next open starts from the current project state."""
        self.project_dialog.show()
        self.project_dialog.raise_()
        self.project_dialog.activateWindow()

    def open_settings_dialog(self) -> None:
        """Open the MODAL Settings dialog (Tools > "Settings...",
        2026-09-01, plan project_settings_dialogs). Settings apply explicitly
        (OK/Apply/Cancel — see gui/docks/settings_dialog.py); open_modal()
        re-seeds the widgets from the persisted state first."""
        self.settings_dialog.open_modal()

    def _refresh_graph_dependent_choices(self) -> None:
        """The include: graph's shape or an entry's name changed — either
        via ConfigTreeDock's own actions (add/remove a file, rename/delete a
        cell/point/...) or via one of the entity docks' own Save
        creating/renaming an entry directly (e.g. CellDock's "Add cell..." +
        Save — a brand new cell name that RulesDock.spoke_cell_combo, sourced
        from collect_all_cell_names(), would otherwise not see until the
        root is reassigned; symmetrically for PointsDock's Save and every
        point-name combo in ThermalViaArrayDock/RulesDock/PlacerDock). Every
        dock with a graph-derived combobox must re-fetch its choices, the
        same way it already does on a root-file change (set_root_path is
        safe to call again: it only refreshes combo CHOICES, preserving the
        current selection via refresh_file_combo_choices' current_paths
        argument — it does not touch whatever entity is currently loaded in
        the dock's form, see gui/docks/_common.py's refresh_file_combo_choices
        docstring). TreesDock is the ONE exception to that set_root_path
        pattern (plan 2026-08-31_trees_dock_stale_after_entity_add.md): its
        set_root_file does a FULL reset that would wipe unsaved tree edits, so
        it gets a dedicated lightweight refresh_ref_candidates() that only
        re-reads its cfg/ctx and never touches the loaded trees/dirty state.
        Cheap to call repeatedly since 2026-08-15's mtime file
        cache (plan_2026_08_15_config_read_cache_startup.md) — this handler
        does NOT need its own caching, it just needs to fire at the right
        moments, which it previously didn't (found live — Denis: adding a
        Placer to a brand-new file required the file to already be visible
        from wherever the new file was created, the tree's own action never
        told any other dock)."""
        root_path = self.root_metadata_dock.root_path
        self.chain_dock.set_root_path(root_path)
        self.placer_dock.set_root_path(root_path)
        self.thermal_via_dock.set_root_path(root_path)
        self.cells_dock.set_root_path(root_path)
        self.tools_dock.set_root_path(root_path)
        self.entity_dock.set_root_path(root_path)
        self.points_dock.set_root_path(root_path)
        self.cell_anchor_view.set_root_path(root_path)
        self.trees_dock.refresh_ref_candidates()
        self.root_metadata_dock.refresh_working_file_choices()

    def _edit_cell(self, name, file_path) -> None:
        """ConfigTreeDock's cell_edit_requested delegate — right-click
        "Edit cell..." never goes through _on_clicked/file_selected (see
        that wiring's own comment above), so the file the cell lives in is
        passed explicitly here before loading — a later Save writes the edit
        back to that file, not the root (2026-08-21 review fix). Opens the
        (non-modal) Cell dialog with the loaded cell."""
        self.cells_dock.load_entry(name, file_path)
        self._open_cell_dialog()

    def _selected_cell_or_report(self):
        """The Config tree's currently selected cell as (name, file_path), or None
        with a Log line already written — the ONE "no cell selected" handling the
        Tools → Config delegates below share (same idiom as
        delete_selected_chain/reread_scheme_list)."""
        selection = self.config_tree_dock.selected_cell()
        if selection is None:
            show_message(_("Pick a cell in the Config tree first."), "",
                         logging.getLogger(__name__))
            return None
        return selection

    def update_selected_cell_from_selection(self, choose_layers: bool = False) -> None:
        """Main menu Tools → Config → "Update cell from selection...": the cell
        currently SELECTED in the Config tree (Denis asked for both variants in
        both places, Э4), then the very same read the context menu's "Update from
        selection..." runs. `choose_layers=True` is the OTHER variant: the layer
        dialog opens first and what it confirms becomes the read's layer set."""
        selection = self._selected_cell_or_report()
        if selection is None:
            return
        name, file_path = selection
        self.cells_dock.refresh_from_selection_requested(
            name, file_path, choose_layers=choose_layers)

    def import_selected_cell_from_selection(self, choose_layers: bool = False) -> None:
        """Main menu Tools → Config → "Import vias/tracks from selection...": the
        ADDITIVE counterpart of the delegate above, on the cell currently selected
        in the Config tree — same fast / choose-layers split."""
        selection = self._selected_cell_or_report()
        if selection is None:
            return
        name, file_path = selection
        self.cells_dock.import_from_selection_requested(
            name, file_path, choose_layers=choose_layers)

    def _refresh_cell_from_selection(self, name, file_path, choose_layers=False) -> None:
        """ConfigTreeDock's cell_refresh_requested delegate (2026-09-03, plan
        cell_geometry_refresh) — the context menu's "Update from selection...":
        same explicit file handling as _edit_cell, then drive CellDock's own
        refresh entry point (which loads the cell when it is not the currently
        open one and runs the same _on_refresh_geometry path as the button).

        2026-09-10 (plan_2026_09_10_cell_refresh_symmetric_and_no_dialog, H.3):
        deliberately does NOT open the Cell dialog, exactly like
        _copy_cell_placement below. The flat Config list made it look like
        "clicked refresh — an Edit Cell window popped up", and it is not needed:
        the per-record report goes to the Log dock and the change is staged for
        Save by _autostage(). "Edit cell..." keeps opening the dialog — that is
        that action's own purpose (its component/via/track tables have no home
        on the merged QView page)."""
        self.cells_dock.refresh_from_selection_requested(
            name, file_path, choose_layers=choose_layers)

    def _import_cell_from_selection(self, name, file_path, choose_layers=False) -> None:
        """ConfigTreeDock's cell_import_requested delegate (2026-09-03, plan
        fpga_oscill_missing_copper_and_cell_import §B.3) — the context menu's
        "Import from selection...": the ADDITIVE backfill counterpart of
        _refresh_cell_from_selection (Refresh cannot ADD a record; Import
        never MODIFIES one). Same explicit file handling as _edit_cell, then
        drive CellDock's own import entry point (loads the cell when it is
        not the currently open one and runs the same _on_import_vias_tracks
        path as the button).

        H.3 (2026-09-10): no Cell dialog here either — same reasoning as
        _refresh_cell_from_selection above; the result is reported in the Log
        and staged by _autostage()."""
        self.cells_dock.import_from_selection_requested(
            name, file_path, choose_layers=choose_layers)

    def _copy_cell_placement(self, name, file_path) -> None:
        """ConfigTreeDock's cell_copy_requested delegate (2026-09-06, plan
        copy_placement_from_cell) — the context menu's "Copy placement from
        cell...": the OFFLINE cell-to-cell placement copy onto the requested
        cell. Deliberately does NOT open any editor: the cell is loaded into
        CellDock's form invisibly, CellDock's own copy entry point runs the
        minimal role-fitted donor picker (a combobox-only dialog), applies the
        copy through its normal save path and reports the result in the Log —
        no Cell dialog, no entity/net editor pops up (Denis 2026-09-06)."""
        self.cells_dock.copy_from_cell_requested(name, file_path)

    def _on_cell_picked(self, name: str) -> None:
        """A Config-tree cell selection ALWAYS opens the ONE merged cell page
        (task V, prompt_2026_09_11_cell_page_merge.md) — whether it came from a
        mouse click, an arrow key (G.5) or the context menu. It used to reveal
        the Placer page and merely FOLLOW while the anchor page happened to be
        active; the two editors are one page now, so every cell pick goes there.

        The page reloads the picked cell through load_entry, which first clears
        the previous cell's working-context combos (_prefill_cell_context), so
        the Sheet/Cluster of the previously edited cell never leak (G.4). A
        repeat pick of the cell already loaded is a no-op — it must not drop
        unsaved input or remove the overlay the user is working with."""
        anchor_page = getattr(self, "_cell_anchor_page", None)
        if anchor_page is None:
            self._show_config_placer()
            return
        self._focus_config_tree_dock()
        self.config_tree_dock.show_page(anchor_page)
        if getattr(self.cell_anchor_view, "_cell_name", None) == name:
            return
        self.cell_anchor_view.load_entry(name, None)

    def _edit_cell_anchor(self, name, file_path) -> None:
        """ConfigTreeDock's cell_anchor_requested delegate (2026-09-09, Phase C
        of plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay) — the
        context menu's "Cell anchor...": load the cell into the dedicated
        anchor editor and show it as the Config dock's right-QView page (never
        goes through _on_clicked, so the owning file is passed explicitly)."""
        self.cell_anchor_view.load_entry(name, file_path)
        self._focus_config_tree_dock()
        self.config_tree_dock.show_page(self._cell_anchor_page)

    # ── Cell-anchor overlay cleanup (Phase D of plan_2026_09_09_cell_anchor_ ──
    # v2_declarative_and_board_overlay, D.2) ────────────────────────────────

    def _on_config_right_page_changed(self, index: int) -> None:
        """Config right-QView page switch — Phase D cleanup: when the user
        navigates AWAY from the cell-anchor editor page, its drawn overlay
        (marker + bbox) is removed from the board and forgotten. The overlay
        is an editing aid shown only while the page is open; leaving it
        (opening another Config tree node) is the explicit 'page close'.

        T2 (S.2/S.3, plan_2026_09_11_stale_snapshot_role_lists.md): the pages
        of this QView host the docks whose Role/Cluster/NET combos come from
        the live board, and a page switch is the explicit "I am about to use
        them" moment — so it is the freshness trigger for those lists (a
        worker-thread rebuild + push_known_lists; the row views are left alone,
        see push_known_lists)."""
        prev = getattr(self, "_config_right_page_index", 0)
        self._config_right_page_index = index
        if index != prev:
            self.refresh_snapshot_and_push()
        anchor_page = getattr(self, "_cell_anchor_page", None)
        if anchor_page is None or prev != anchor_page or index == anchor_page:
            return
        try:
            self.cell_anchor_view.cleanup()
        except Exception:  # noqa: BLE001 — cleanup must never break the GUI
            logger.exception("cell-anchor page-leave overlay cleanup failed")

    def cleanup_overlay_on_quit(self, connection) -> None:
        """GUI-shutdown overlay cleanup (Phase D D.2) — remove EVERY overlay
        shape the owner tracks (across all namespaces/keys) from the board,
        bounded and best-effort. Called by MainWindow._persist_settings — the
        one choke point shared by the real-quit closeEvent and the tray
        Quit. Never blocks quit for more than a bounded wait; never crashes
        quit on a dead socket."""
        try:
            cleanup_all_overlays_sync(connection)
        except Exception:  # noqa: BLE001 — quit must never be blocked
            logger.exception("overlay cleanup on quit failed")

    def reconcile_overlay(self, connection) -> None:
        """Reconcile the overlay key map with the live layer (E.2.4) — called
        by MainWindow._finish_poll on connect and on every manual refresh.

        The board read runs on a WORKER thread (start_long_op), never on the UI
        thread, and the report only reaches the Log — never a modal. Best
        effort by design: no adapter, a busy socket or a disabled layer is a
        silent no-op, because this is a visualisation housekeeping step.

        No guard widget and no busy word (Э2, plan_2026_09_12_busy_indicator):
        this is AUTOMATIC housekeeping fired by MainWindow._finish_poll on
        connect/refresh, not something the user started — the disabled-layer
        rule above plus the long_op_active check below are its only guards."""
        from .worker import start_long_op
        board = getattr(connection, "board", None)
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            return
        if getattr(connection, "long_op_active", False):
            return  # never interleave on the shared kipy REQ socket
        self._overlay_reconcile_op = start_long_op(
            connection, [], overlay_markers.owner.reconcile,
            lambda _report: None, lambda _message: None, adapter)

    def _attach_log_file_handler(self, handler) -> None:
        """Attach the root-config log_file: FileHandler either to the live
        QueueListener (when setup_logging() has started one) or directly to
        the ROOT logger when no listener exists (unit tests, no
        setup_logging call) — idempotent across both paths, so
        _on_root_file_changed_for_logging() can call it on every re-peek.
        Since 2026-08-15 (queue-based logging rework, see
        techdocs/handoff/plan_2026_08_15_queue_based_logging.md) the
        handler's formatting/writing runs on the listener's single thread,
        so logging can never block the calling thread on a handler lock."""
        if handler is None:
            return
        listener = get_log_listener()
        if listener is not None:
            if handler not in listener.handlers:
                listener.handlers = listener.handlers + (handler,)
        else:
            root = logging.getLogger()
            if handler not in root.handlers:
                root.addHandler(handler)

    def _detach_log_file_handler(self, handler) -> None:
        """Detach the root-config log_file: FileHandler from BOTH the ROOT
        logger and the live QueueListener (whichever path it was attached
        through) — idempotent, so _on_root_file_changed_for_logging() (swap
        on root-file change) and teardown fixtures can call it freely."""
        if handler is None:
            return
        root = logging.getLogger()
        if handler in root.handlers:
            root.removeHandler(handler)
        listener = get_log_listener()
        if listener is not None and handler in listener.handlers:
            listener.handlers = tuple(
                h for h in listener.handlers if h is not handler)

    def _safe_call(self, what: str, fn, *args) -> None:
        """Run a dock's root-notification callable safely: a BROKEN root
        config must never crash the GUI — on startup (restore) or on a manual
        Open/Recent. The error goes to the log only (Log dock picks it up);
        the root path stays set so the user can fix the config or choose
        another via Open/New. Central guard for every root_changed consumer
        and for _wire()'s initial sync (task: GUI must always start even with
        a broken root config)."""
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 — any dock computation must not kill the GUI
            logging.exception("GUI: %s failed on the current root config (root "
                              "file may be broken) — window stays open", what)

    def _sync_root_to_docks(self, path) -> None:
        """Re-notify every root_changed consumer with `path` (the current root
        or None), each guarded by _safe_call — the startup sync AND the
        Discard path (reload_project_from_disk) share this one list, so a
        discard can never drift from the initial wiring."""
        self._safe_call("config_tree_dock.set_root_file",
                        self.config_tree_dock.set_root_file, path)
        self._safe_call("trees_dock.set_root_file", self.trees_dock.set_root_file, path)
        self._safe_call("chain_dock.set_root_path", self.chain_dock.set_root_path, path)
        self._safe_call("placer_dock.set_root_path", self.placer_dock.set_root_path, path)
        self._safe_call("thermal_via_dock.set_root_path",
                        self.thermal_via_dock.set_root_path, path)
        self._safe_call("cells_dock.set_root_path", self.cells_dock.set_root_path, path)
        self._safe_call("tools_dock.set_root_path", self.tools_dock.set_root_path, path)
        self._safe_call("entity_dock.set_root_path", self.entity_dock.set_root_path, path)
        self._safe_call("points_dock.set_root_path", self.points_dock.set_root_path, path)
        self._safe_call("cell_anchor_view.set_root_path",
                        self.cell_anchor_view.set_root_path, path)
        self._safe_call("net_trace_dock.set_root_path",
                        self.net_trace_dock.set_root_path, path)
        # Scheme List Place (2026-09-06, plan scheme_list §6 / P6 Stage 3): the
        # Place page reads its cfg combos from the root — must be in the same
        # startup/discard sync list as every other root_changed consumer.
        self._safe_call("scheme_list_place_dock.set_root_path",
                        self.scheme_list_place_dock.set_root_path, path)
        self._safe_call("fieldstool_dock.set_root_path",
                        self.fieldstool_dock.set_root_path, path)
        self._safe_call("_on_root_file_changed_for_logging",
                        self._on_root_file_changed_for_logging, path)
        # V.3: a root config WITHOUT schematic_dir silently disables sheet-based
        # narrowing everywhere (Extract, anchor_sheet) — one informational Log
        # line, once per root, only when a live board makes it actionable.
        self._safe_call("_warn_if_sheet_narrowing_disabled",
                        self._warn_if_sheet_narrowing_disabled, path)

    def reload_project_from_disk(self) -> None:
        """Discard (File > Discard unsaved changes...): the working set was
        cleared, so re-sync every dock with the on-disk (committed) state by
        re-running the same root notification the startup path uses."""
        self._sync_root_to_docks(self.root_metadata_dock.root_path)
        self._update_dirty_indicator()

    # ── Config working set (2026-09-01, plan project_save_model) ─────────

    def _on_root_changed_for_working_set(self, root_path) -> None:
        """Enable/clear the config working set when the project root changes:
        staging is ON whenever a project is open (so every dock edit lands in
        the working set, not on disk), OFF/cleared when the project closes. A
        root switch starts with a clean working set; the unsaved-changes guard
        lives in RootMetadataDock.set_root_file/close_project."""
        WORKING_SET.enabled = root_path is not None
        WORKING_SET.clear()
        self._update_dirty_indicator()

    def _on_working_set_changed(self) -> None:
        """Every staged write (or clear) — reflect the dirty state immediately
        and schedule a debounced refresh so the tree/collectors show the staged
        content."""
        self._update_dirty_indicator()
        self._ws_refresh_timer.start()

    def _refresh_from_working_set(self) -> None:
        """Debounced: the working set changed (staged or flushed) — rebuild the
        trees and graph-derived combos from the current (staged) state."""
        self._safe_call("config_tree_dock.refresh", self.config_tree_dock.refresh)
        self._refresh_graph_dependent_choices()

    def _update_dirty_indicator(self) -> None:
        """Mirror the working set's dirty state into MainWindow's status-bar ●
        and File > Save text (getattr-guarded: DockHub is also built against a
        plain QMainWindow in tests)."""
        fn = getattr(self.main_window, "_update_dirty_indicator", None)
        if fn is not None:
            fn()

    def _warn_if_sheet_narrowing_disabled(self, path) -> None:
        """V.3 (plan_2026_09_11_extract_selection_diagnostics): without
        schematic_dir/schematic_files sheet names cannot be resolved, so every
        sheet-based narrowing silently does nothing — the misleading "No fully
        selected Cluster found" of V.0 is one symptom. Log ONE informational
        line (never a modal, never repeated for the same root) the first time
        such a config is loaded WITH a live board. Gated on the board because
        without it the warning is not actionable; skipped entirely for configs
        that DO carry schematic_dir. Called from _sync_root_to_docks (a project
        opened while already connected) and set_board_selection (connected
        after opening)."""
        if path is None:
            return
        key = str(path)
        if key in self._sheet_dir_checked:
            return
        connection = getattr(self.main_window, "connection", None)
        if getattr(connection, "board", None) is None:
            return
        self._sheet_dir_checked.add(key)
        try:
            from kicadstamp.config import load_config
            _cfg, ctx = load_config(key)
            # len() materialises the lazy RuntimeContext map (its own __bool__
            # is deliberately always True — see sheet_names.LazySheetNameMap).
            has_sheets = len(ctx.sheet_names) > 0
        except Exception:  # noqa: BLE001 — a broken config is reported by the docks
            return
        if has_sheets:
            return
        logging.info(_(
            "This config has no schematic_dir/schematic_files — sheet names "
            "cannot be resolved, so sheet-based narrowing (Extract "
            "cluster/tree, anchor_sheet) is disabled."))

    def _on_root_file_changed_for_logging(self, path) -> None:
        """Attaches a FileHandler using the CURRENT root config's own
        log_file: (Config.log_file) — see this method's own connect() above
        for why. Since 2026-08-15 (queue-based logging rework, see
        techdocs/handoff/plan_2026_08_15_queue_based_logging.md) the
        handler is attached to the live QueueListener (get_log_listener)
        whenever one exists — its single listener thread formats/writes
        records, so logging can never block the calling thread on a handler
        lock — falling back to a direct root-logger attachment when no
        listener is configured (unit tests, no setup_logging call).
        Re-peeked fresh on every root-file change (never cached), matching
        kicadstamp_cli.py's own per-invocation freshness — same
        cli_common.peek_log_file() helper, so a typo/missing log_file: is
        handled exactly the same way the CLI already handles it (a logged
        warning, never a raise). DEBUG level regardless of the GUI's own
        console/LogDock verbosity, same as the CLI's file handler
        (kicadstamp/logging_setup.py)."""
        if self._log_file_handler is not None:
            self._detach_log_file_handler(self._log_file_handler)
            self._log_file_handler = None
        if path is None:
            return
        log_file = peek_log_file(str(path))
        if log_file is None:
            return
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_file, encoding="utf-8")
        except OSError as e:
            logging.warning(f"Could not open log_file {log_file!r}: {e}")
            return
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        self._attach_log_file_handler(handler)
        self._log_file_handler = handler
