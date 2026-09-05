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
right. thermal_via_dock (2026-09-01, plan plan_2026_09_01_thermal_via_dialog.
md), points_dock (2026-09-01, plan plan_2026_09_01_points_dialog.md),
tools_dock (2026-09-01, plan plan_2026_09_01_tools_dialog_and_entity_roles.
md) and cells_dock (2026-09-04, plan plan_2026_09_04_celldock_to_dialog.md):
they are STANDALONE widgets hosted in their non-modal dialogs
(ThermalViaDialog / PointsDialog / ToolsDialog / CellDialog) — the Detail dock
has no Thermal via/Points/Tools/Cells page anymore, and the same single live
instances keep receiving the selection/snapshot ticks, set_root_path and saved.
"""
import logging
from functools import partial
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QDialog, QMessageBox, QTabWidget

from .docks._common import display_path, show_message
from .docks.entity_delete import delete_entry
from .docks.rename import entry_effective_name

from kicadstamp.cli_common import peek_log_file
from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.i18n import _
from kicadstamp.logging_setup import get_log_listener

from .docks.cell_dialog import CellDialog
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
from .docks.points_dialog import PointsDialog
from .docks.role_cluster_tree import RoleClusterTreeDock
from .docks.root_metadata import RootMetadataDock
from .docks.settings_dialog import SettingsDialog
from .docks.thermal_via import ThermalViaArrayDock
from .docks.thermal_via_dialog import ThermalViaDialog
from .docks.tools import ToolsDock
from .docks.tools_dialog import ToolsDialog
from .docks.instances_dialog import TreeInstancesDialog


class DockHub:
    """Constructs, lays out and wires every dock of the KiCadStamp main
    window. MainWindow creates one DockHub with its BoardConnection and then
    drives the docks through this controller's delegates."""

    def __init__(self, main_window, connection, verbose: bool = False):
        self.main_window = main_window
        # The root-config log_file: FileHandler currently attached to the
        # root logger, if any — see _on_root_file_changed_for_logging().
        self._log_file_handler: Optional[logging.Handler] = None

        # ── left group: Components tree, Config tree ──────────────────────
        self.tree_dock = RoleClusterTreeDock(main_window, connection=connection)
        main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)

        self.config_tree_dock = ConfigTreeDock(main_window)
        main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.config_tree_dock)
        main_window.tabifyDockWidget(self.tree_dock, self.config_tree_dock)

        # Hand-authored s-expr "trees" editor (2026-08-27, design
        # design_2026_08_27_trees_gui_dock.md) — tabbed with the Config tree so
        # the user finds "tree" in one place.
        self.trees_dock = TreesDock(main_window)
        main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.trees_dock)
        main_window.tabifyDockWidget(self.config_tree_dock, self.trees_dock)

        # Tab labels of the whole LEFT dock area go to the BOTTOM of the group
        # (plan_2026_09_04_trees_dock_master_detail.md §4, confirmed with Denis:
        # the full triple RoleClusterTreeDock + ConfigTreeDock + TreesDock group
        # moves, not just the Config/Trees pair — setTabPosition is per AREA, so
        # the single call covers all three).
        main_window.setTabPosition(
            Qt.DockWidgetArea.LeftDockWidgetArea, QTabWidget.TabPosition.South)

        # ── bottom: Pending changes (constructed here — shared between
        # RoleClusterTreeDock's live-board writes and fieldstool's own
        # Stage/Apply, see gui/docks/pending.py — docked further down,
        # tabbed with Log) ─────────────────────────────────────────────────
        self.pending_dock = PendingChangesDock(main_window)

        # ── right group: fieldstool, Detail (Extract/Placer/Root) ─────────
        self.fieldstool_dock = FieldsToolDock(
            main_window, connection=connection, pending_dock=self.pending_dock)
        main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.fieldstool_dock)

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
        # Thermal via (2026-09-01, plan plan_2026_09_01_thermal_via_dialog.md):
        # a STANDALONE widget hosted in the non-modal ThermalViaDialog — the
        # Detail dock has no Thermal via page anymore (same move as Extract
        # above). The same single live instance keeps receiving the snapshot
        # ticks / set_root_path / saved.
        self.thermal_via_dock = ThermalViaArrayDock(main_window)
        self.thermal_via_dialog = ThermalViaDialog(self.thermal_via_dock, main_window)
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
        # Chain (2026-09-01, plan rules_to_chains): the Chain form is a
        # STANDALONE widget hosted in the non-modal ChainDialog — the Detail
        # dock has no Rules page anymore (same move as Extract/Thermal via/
        # Points/Tools). The same single live instance keeps receiving the
        # snapshot ticks / set_root_path / saved. Redraw chain/spoke and Bulk
        # set Cell are driven from the Config tree's context menu
        # (chain_redraw_requested/pad_redraw_requested/bulk_set_cell_requested),
        # not from buttons inside the dialog.
        self.chain_dock = ChainDock(main_window)
        # Backward-compat alias for the 2026-09-01 Rule -> Chain rename — the
        # old rules_dock name still resolves to the live ChainDock.
        self.rules_dock = self.chain_dock
        # Chain as a Config right-QView page (2026-09-05, design
        # config_qview_chain_entity_pages): the single live ChainDock is now
        # embedded as a page in the Config dock's right QStack — a single click
        # on a chains: pad leaf opens the spoke editor there, and Add net/spoke
        # + Edit chain flows show the same page (a QWidget can only have one
        # parent, so the ChainDialog wrapper is GONE with this change).
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
        # Points (2026-09-01, plan plan_2026_09_01_points_dialog.md): a
        # STANDALONE widget hosted in the non-modal PointsDialog — the Detail
        # dock has no Points page anymore (same move as Extract/Thermal via).
        # The same single live instance keeps receiving the snapshot ticks /
        # set_root_path / saved. connection is needed for Resolve.
        self.points_dock = PointsDock(main_window, connection=connection)
        self.points_dialog = PointsDialog(self.points_dock, main_window)
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

        # ── bottom: Pending changes, Log ────────────────────────────────────
        main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.pending_dock)
        self.log_dock = LogDock(main_window, verbose=verbose)
        main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        main_window.tabifyDockWidget(self.pending_dock, self.log_dock)

        # All real TOP-LEVEL QDockWidgets (2026-08-27, handoff
        # sync_skip_message_and_view_menu): MainWindow's View menu wires each
        # one's ready-made toggleViewAction() so a closed dock can be brought
        # back without restarting. Deliberately NOT DetailDock's internal
        # panels (placer_dock/... are plain QWidgets switched by its own tab
        # bar — not independently closable/dockable, no toggleViewAction of
        # their own). Order matches construction above
        # (already grouped by area: Left / right / bottom).
        self.docks = [
            self.tree_dock, self.config_tree_dock, self.trees_dock,
            self.pending_dock, self.fieldstool_dock, self.log_dock,
        ]

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

    def _show_config_chain(self, *_args) -> None:
        """Route a chains pick (pad leaf / chain edit / Add net / Add spoke) to
        the Chain right page of the Config dock (2026-09-05, design
        config_qview_chain_entity_pages §4)."""
        self.config_tree_dock.show_page(self._chain_page)

    def _focus_config_tree_dock(self) -> None:
        """Show the Config dock and raise it to the front of its tab group —
        the Config-dock mirror of _focus_trees_dock (Tools-menu Add net/spoke
        delegates must bring the Config tab to front before showing a page)."""
        self.config_tree_dock.show()
        self.config_tree_dock.raise_()

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
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "cells_dock.set_root_path",
                    self.cells_dock.set_root_path))
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
        self.config_tree_dock.cell_picked.connect(self._show_config_placer)
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
        self.config_tree_dock.thermal_via_picked.connect(self.thermal_via_dock.load_entry)
        self.config_tree_dock.thermal_via_picked.connect(self._open_thermal_via_dialog)
        # Coordinate placements (2026-08-12, Group 1): a normal named-records
        # section now — a leaf click carries the full entry dict, loaded into
        # the merged PlacerDock's coordinate mode, exactly like clone_placements
        # -> placement_picked -> load_placement (see config_tree.py's
        # coordinate_placements_picked docstring).
        self.config_tree_dock.coordinate_placements_picked.connect(self.placer_dock.load_placement)
        self.config_tree_dock.coordinate_placements_picked.connect(self._show_config_placer)
        # Double click on a points: leaf (2026-09-01, plan
        # plan_2026_09_01_points_dialog.md) -> load the entry into the live
        # PointsDock and open the non-modal PointsDialog. Single click on a
        # points: leaf does NOTHING (the old points_picked wiring is gone).
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
        # "Edit cell..." (context menu, 2026-08-06) — deliberately NOT wired
        # to cell_picked, which keeps meaning "pick this cell as a
        # placement's content" (see config_tree.py's module docstring).
        # Context-menu actions never go through _on_clicked, so unlike a
        # plain leaf click, file_selected has NOT necessarily already
        # targeted CellDock at the right file — _edit_cell below sets it
        # explicitly before loading, same reasoning as _start_new_placement
        # etc. below for "Add ...".
        self.config_tree_dock.cell_edit_requested.connect(self._edit_cell)
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
        self.cells_dock.saved.connect(self.config_tree_dock.refresh)
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
        # Auto-close the (non-modal) Thermal via dialog after a successful Save
        # (2026-09-01, Denis): saved is emitted by _on_save only on success —
        # Redraw (placement) stays open for iterative tuning.
        self.thermal_via_dock.saved.connect(self.thermal_via_dialog.hide)
        # Auto-close the (non-modal) Points dialog after a successful Save
        # (2026-09-01, Denis): saved is emitted by _on_save only on success —
        # a Point has no Redraw, so Save closing is the whole point of the form.
        self.points_dock.saved.connect(self.points_dialog.hide)
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
        gui/main_window.py's _poll)."""
        self.tree_dock.set_footprints(snapshot)
        self.placer_dock.refresh_known_roles(snapshot)
        # NOTE (2026-09-05): placer_dock.refresh_known_nets is GONE — the
        # Placer's manual Nets/Net overrides/Refs tabs were removed (nets
        # auto-resolve); the other docks' refresh_known_nets stay below.
        self.thermal_via_dock.refresh_known_roles(snapshot)
        self.thermal_via_dock.refresh_known_nets(board)
        self.points_dock.refresh_known_roles(snapshot)
        self.chain_dock.refresh_known_roles(snapshot)
        self.chain_dock.refresh_known_nets(board)
        self.net_trace_dock.refresh_known_roles(snapshot)
        self.net_trace_dock.refresh_known_nets(board)
        self.cells_dock.refresh_known_roles(snapshot)
        self.tools_dock.refresh_known_nets(board)

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
        self.placer_dock.set_board_selection(items, selected)

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

    def open_fieldstool(self) -> None:
        """Bring the fieldstool tab to front even if another right-hand tab
        is active or the dock was individually closed."""
        self.fieldstool_dock.setVisible(True)
        self.fieldstool_dock.raise_()

    def _start_new_placement(self, placer_path) -> None:
        """ConfigTreeDock's add_placer_requested delegate — resets
        PlacerDock's form and brings the Config dock's Placer right page to
        front, same reasoning as open_fieldstool() above (the action was
        invoked from the Config tree tab)."""
        self.placer_dock.new_placement(placer_path)
        self._show_config_placer()

    def _start_new_thermal_via(self, file_path) -> None:
        """ConfigTreeDock's add_thermal_via_requested delegate — same
        reasoning as _start_new_placement above, for ThermalViaArrayDock: opens
        the (non-modal) Thermal via dialog with a fresh blank form."""
        self._open_thermal_via_dialog()
        self.thermal_via_dock.new_thermal_via(file_path)

    def _start_new_point(self, file_path) -> None:
        """ConfigTreeDock's add_point_requested delegate — opens the (non-modal)
        Points dialog with a fresh blank form, same reasoning as
        _start_new_thermal_via above, for PointsDock."""
        self._open_points_dialog()
        self.points_dock.new_point(file_path)

    def _start_edit_point(self, name) -> None:
        """ConfigTreeDock's points_edit_requested delegate (double click on a
        points: leaf, 2026-09-01, plan plan_2026_09_01_points_dialog.md) —
        loads the named point into the live PointsDock and opens the (non-
        modal) Points dialog with it."""
        self.points_dock.load_entry(name)
        self._open_points_dialog()

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

    def run_forest_full_redraw(self) -> None:
        """Main menu "Tools -> Trees -> Full redraw (all trees and modules)..."
        (plan
        2026-09-02 tree_module_embedding P3 п.3): the forest-wide, module-aware
        curated redraw across ALL trees. TreesDock owns the trees/cfg/ctx and
        the worker callback plumbing (no new dock button — menu only)."""
        self.trees_dock._run_forest_redraw()

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
        """Show the Trees dock and raise it to the front of its tab group."""
        self.trees_dock.show()
        self.trees_dock.raise_()

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
        anchor-position readout for the CURRENT tree."""
        self._focus_trees_dock()
        self.trees_dock._refresh_anchor_live_position()

    def redraw_selected(self) -> None:
        """Tools → Trees → Redraw selected: curated redraw of the CURRENT
        tree's CHECKED nodes (background worker)."""
        self._focus_trees_dock()
        self.trees_dock._on_redraw_selected()

    def redraw_whole_tree(self) -> None:
        """Tools → Trees → Redraw whole tree: curated redraw of EVERY node of
        the CURRENT tree (background worker)."""
        self._focus_trees_dock()
        self.trees_dock._on_redraw_whole_tree()

    def extract_tree_from_selection(self) -> None:
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
        clusters = fully_selected_clusters(
            self._selection_footprints,
            list(connection.snapshot or []),
            list(cfg.entities),
            (),
            sheet_names=sheet_names)
        # Diagnostic + defensive filter (same rationale as the retired
        # Re-read flow: a row must be a sane single-line cluster).
        clusters = [c for c in clusters if c.cluster and "\n" not in c.cluster]
        if not clusters:
            QMessageBox.warning(
                self.main_window, _("Extract tree"),
                _("No fully selected Cluster found — select ALL components of a "
                  "cluster (its Cluster tag + sheet) first."))
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

        inter_nets = detect_inter_cluster_nets(
            self._selection_raw_items, clusters,
            list(connection.snapshot or []),
            [r.net for r in cfg.rules],
            adapter=adapter)

        # Per-row "no cell" errors (block OK in the dialog) + per-row "existing
        # cluster anchor" prefills + the live Entity positions for the offset
        # preview. A failed live read just omits the position (the node is then
        # saved without xy — live-position rule at apply), never a crash.
        errors = cluster_errors(clusters, cfg.entities, cfg)
        prefills: dict[int, object] = {}
        entity_positions: dict[str, tuple[float, float]] = {}
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
        anchor_base = None
        try:
            anchor_base = resolve_role_anchor_base_mm(adapter, cfg, anchor, sheet_names)
        except Exception as e:  # noqa: BLE001 — without it nodes are saved without xy
            logging.warning("Extract tree: anchor base unavailable — nodes will "
                            "have no xy: %s", e)

        from .docks.tree_from_selection import build_tree_from_clusters
        # entity_positions already holds only the positions that resolved live
        # (failed reads are omitted -> that node is saved without xy).
        checked_nets = dialog.selected_nets()
        # Phase E (2026-09-01): entering an existing tree's name = RE-EXTRACT —
        # the tree is rebuilt from the current selection and replaces the old one.
        existing_tree = next((t for t in cfg.trees if t.name == tree_name), None)
        tree, build_errors = build_tree_from_clusters(
            selected, tree_name, anchor, cfg.entities, cfg,
            entity_positions=entity_positions, anchor_base=anchor_base,
            net_nodes=[n.net for n in checked_nets],
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
        from kicadstamp.net_trace_extract import extract_net_trace, net_trace_to_dict
        from kicadstamp.trees import tree_to_dict
        from .docks.entity_delete import backup_file
        from kicadstamp.domain.board import Track, Via
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
            # Phase B+C+D: capture the checked inter-cluster nets as net_traces:
            # records BEFORE the tree write, so the tree's net_trace nodes
            # resolve against cfg.net_traces at link_trees time.
            selected_raw = self._selection_raw_items
            net_traces_data = data.setdefault("net_traces", [])
            new_net_traces: list[NetTrace] = []
            for net in checked_nets:
                try:
                    # Phase B: capture ONLY the SELECTED copper of the net — the
                    # record must match the third-tab #tracks/#vias.
                    net_items = [i for i in selected_raw
                                 if isinstance(i, (Track, Via)) and i.net_name == net.net]
                    nt = extract_net_trace(
                        adapter, net=net.net,
                        anchor_role=anchor.role,
                        anchor_sheet=anchor.anchor_sheet,
                        anchor_cluster=anchor.anchor_cluster,
                        anchor_pad=anchor.anchor_pad,
                        sheet_names=sheet_names,
                        items=net_items)
                    # upsert by net (same semantics as write_net_trace)
                    entry = net_trace_to_dict(nt)
                    replaced = False
                    for i, e in enumerate(net_traces_data):
                        if isinstance(e, dict) and e.get("net") == nt.net:
                            net_traces_data[i] = entry
                            replaced = True
                            break
                    if not replaced:
                        net_traces_data.append(entry)
                    new_net_traces.append(nt)
                except Exception as e:  # noqa: BLE001 — one bad net must not drop the tree
                    logging.warning("Extract tree: net %r not captured: %s",
                                    net.net, e)
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
        clusters = fully_selected_clusters(
            self._selection_footprints,
            list(connection.snapshot or []),
            list(cfg.entities),
            (),
            sheet_names=sheet_names)
        # Diagnostic + defensive filter (same rationale as "Extract tree...": a
        # row must be a sane single-line cluster).
        clusters = [c for c in clusters if c.cluster and "\n" not in c.cluster]
        if not clusters:
            QMessageBox.warning(
                self.main_window, _("Extract cluster"),
                _("No fully selected Cluster found — select ALL components of a "
                  "cluster (its Cluster tag + sheet) first."))
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

    def _open_thermal_via_dialog(self) -> None:
        """Show/raise the ONE live Thermal via dialog — non-modal, so the user
        can keep selecting on the board while it's open: the ~2s snapshot tick
        keeps feeding the same thermal_via_dock instance inside it. Closing via
        the window X just hides it (QDialog default), so the next open starts
        from the current board state."""
        self.thermal_via_dialog.show()
        self.thermal_via_dialog.raise_()
        self.thermal_via_dialog.activateWindow()

    def _open_points_dialog(self) -> None:
        """Show/raise the ONE live Points dialog — non-modal, so the user can
        keep selecting on the board while it's open: the ~2s snapshot tick
        keeps feeding the same points_dock instance inside it. Closing via the
        window X just hides it (QDialog default), so the next open starts from
        the current board state."""
        self.points_dialog.show()
        self.points_dialog.raise_()
        self.points_dialog.activateWindow()

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

    def _refresh_cell_from_selection(self, name, file_path) -> None:
        """ConfigTreeDock's cell_refresh_requested delegate (2026-09-03, plan
        cell_geometry_refresh) — the context menu's "Update from selection...":
        same explicit file handling as _edit_cell, then drive CellDock's own
        refresh entry point (which loads the cell when it is not the currently
        open one and runs the same _on_refresh_geometry path as the button).
        Opens the (non-modal) Cell dialog with the loaded cell."""
        self.cells_dock.refresh_from_selection_requested(name, file_path)
        self._open_cell_dialog()

    def _import_cell_from_selection(self, name, file_path) -> None:
        """ConfigTreeDock's cell_import_requested delegate (2026-09-03, plan
        fpga_oscill_missing_copper_and_cell_import §B.3) — the context menu's
        "Import from selection...": the ADDITIVE backfill counterpart of
        _refresh_cell_from_selection (Refresh cannot ADD a record; Import
        never MODIFIES one). Same explicit file handling as _edit_cell, then
        drive CellDock's own import entry point (loads the cell when it is
        not the currently open one and runs the same _on_import_vias_tracks
        path as the button). Opens the (non-modal) Cell dialog with the
        loaded cell."""
        self.cells_dock.import_from_selection_requested(name, file_path)
        self._open_cell_dialog()

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
        self._safe_call("net_trace_dock.set_root_path",
                        self.net_trace_dock.set_root_path, path)
        self._safe_call("fieldstool_dock.set_root_path",
                        self.fieldstool_dock.set_root_path, path)
        self._safe_call("_on_root_file_changed_for_logging",
                        self._on_root_file_changed_for_logging, path)

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
