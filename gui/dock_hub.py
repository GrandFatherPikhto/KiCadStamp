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

Extract/Placer/Root/Thermal via/Points/Rules (extract_dock/placer_dock/
root_metadata_dock/thermal_via_dock/points_dock/rules_dock) are the one
exception: 2026-08-03 they were merged into ONE QDockWidget, DetailDock
(gui/docks/detail_panel.py) — its own module docstring covers why (Points/
Rules added 2026-08-05, same shape). Those attributes are kept as aliases
straight into DetailDock's stack pages so every existing call site keeps
working unchanged; they are plain QWidgets now, not QDockWidgets in their
own right.
"""
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt

from kicadstamp.cli_common import peek_log_file

from .docks.config_tree import ConfigTreeDock
from .docks.detail_panel import DetailDock
from .docks.fieldstool_dock import FieldsToolDock
from .docks.log_panel import LogDock
from .docks.pending import PendingChangesDock
from .docks.role_cluster_tree import RoleClusterTreeDock


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

        self.detail_dock = DetailDock(main_window, connection=connection)
        main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.detail_dock)
        main_window.tabifyDockWidget(self.fieldstool_dock, self.detail_dock)
        # Thin aliases — kept so every existing call site/test that reaches
        # a specific panel by name (extract_dock/placer_dock/
        # root_metadata_dock) keeps working unchanged; they're pages inside
        # detail_dock's stack now (gui/docks/detail_panel.py), not their
        # own QDockWidgets.
        self.extract_dock = self.detail_dock.extract_panel
        self.placer_dock = self.detail_dock.placer_panel
        self.root_metadata_dock = self.detail_dock.root_panel
        self.thermal_via_dock = self.detail_dock.thermal_via_panel
        self.points_dock = self.detail_dock.points_panel
        self.rules_dock = self.detail_dock.rules_panel
        self.cells_dock = self.detail_dock.cells_panel

        # ── bottom: Pending changes, Log ────────────────────────────────────
        main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.pending_dock)
        self.log_dock = LogDock(main_window, verbose=verbose)
        main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        main_window.tabifyDockWidget(self.pending_dock, self.log_dock)

        self._wire()

    def restore_tree_mode(self) -> None:
        """Restores the Components tree's "Not yet applied" (schematic)
        mode. Deliberately NOT part of __init__: restoring it rebuilds the
        tree, and that rebuild reads main_window.fieldstool_dock through the
        tree dock's lazy lookup — which cannot resolve until MainWindow has
        bound its DockHub (see RoleClusterTreeDock.restore_mode_from_
        settings()). MainWindow calls this right after constructing the hub.
        """
        self.tree_dock.restore_mode_from_settings()

    def _wire(self) -> None:
        """Every dock-to-dock connection (real pyqtSignals — a role can
        legitimately have more than one listener)."""

        # Config tree -> Extract/Placer (2026-08-03, replaces FilePickerDock
        # entirely — see gui/docks/config_tree.py's module docstring):
        # file_selected fires on every click anywhere in the tree, and
        # feeds ALL of ExtractDock's/PlacerDock's file targets at once —
        # what used to be three independently-assigned roles (Cells/
        # Extractor/Placer) collapse into "whichever file is currently
        # being browsed".
        self.config_tree_dock.file_selected.connect(self.extract_dock.set_target_file)
        self.config_tree_dock.file_selected.connect(self.extract_dock.set_profile_file)
        self.config_tree_dock.file_selected.connect(self.extract_dock.set_placer_file)
        self.config_tree_dock.file_selected.connect(self.placer_dock.set_cells_file)
        self.config_tree_dock.file_selected.connect(self.placer_dock.set_placer_file)
        self.config_tree_dock.file_selected.connect(self.thermal_via_dock.set_target_file)
        self.config_tree_dock.file_selected.connect(self.placer_dock.set_target_file)
        self.config_tree_dock.file_selected.connect(self.points_dock.set_target_file)
        self.config_tree_dock.file_selected.connect(self.rules_dock.set_target_file)
        self.config_tree_dock.file_selected.connect(self.cells_dock.set_target_file)
        # RootMetadataDock's Working file combobox (2026-08-11, Denis: "туда
        # же напрашивается и выбор текущего рабочего файла... без прыжков
        # между доками") — a second, direct entry point for the exact same
        # target-file broadcast as the 9 file_selected lines above, so the
        # working file can be changed from the Project tab without
        # navigating the tree. The tree still feeds the combobox's own
        # DISPLAY (set_working_file_from_tree, no re-broadcast — see
        # gui/docks/root_metadata.py's module docstring), so it never goes
        # stale relative to whatever was last clicked.
        self.config_tree_dock.file_selected.connect(self.root_metadata_dock.set_working_file_from_tree)
        self.root_metadata_dock.working_file_changed.connect(self.extract_dock.set_target_file)
        self.root_metadata_dock.working_file_changed.connect(self.extract_dock.set_profile_file)
        self.root_metadata_dock.working_file_changed.connect(self.extract_dock.set_placer_file)
        self.root_metadata_dock.working_file_changed.connect(self.placer_dock.set_cells_file)
        self.root_metadata_dock.working_file_changed.connect(self.placer_dock.set_placer_file)
        self.root_metadata_dock.working_file_changed.connect(self.thermal_via_dock.set_target_file)
        self.root_metadata_dock.working_file_changed.connect(self.placer_dock.set_target_file)
        self.root_metadata_dock.working_file_changed.connect(self.points_dock.set_target_file)
        self.root_metadata_dock.working_file_changed.connect(self.rules_dock.set_target_file)
        self.root_metadata_dock.working_file_changed.connect(self.cells_dock.set_target_file)
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
        self.root_metadata_dock.root_changed.connect(self.config_tree_dock.set_root_file)
        self.root_metadata_dock.root_changed.connect(self.rules_dock.set_root_path)
        self.root_metadata_dock.root_changed.connect(self.placer_dock.set_root_path)
        self.root_metadata_dock.root_changed.connect(self.thermal_via_dock.set_root_path)
        self.root_metadata_dock.root_changed.connect(self.cells_dock.set_root_path)
        # PointsDock's own target-file combo (added 2026-08-13, plan
        # tree_to_combo_file_pickers — the only dock that had no
        # set_root_path at all before) needs the same whole include graph,
        # same root_changed source as the four above.
        self.root_metadata_dock.root_changed.connect(self.points_dock.set_root_path)
        # Extract's own Cell file/Profile file combos (added 2026-08-06,
        # Denis: "имя файла, куда пишем extract и cell... тоже, выпадашками"
        # — un-couples them from always following the same file_selected
        # click) need the whole include graph too, same reasoning as above.
        self.root_metadata_dock.root_changed.connect(self.extract_dock.set_root_path)
        # fieldstool's root_sheet (added 2026-08-07, see config/models.py's
        # Config.root_sheet docstring) — same root_changed source, so
        # opening/switching a project automatically re-points fieldstool's
        # schematic-vs-board diff instead of silently keeping the previous
        # project's manually-picked root sheet.
        self.root_metadata_dock.root_changed.connect(self.fieldstool_dock.set_root_path)
        # log_file: (Config.log_file, root-file top-level key) — 2026-08-06,
        # found live: Denis had it set in root.yaml already, assumed
        # (reasonably) it already covered GUI runs too, but the GUI's own
        # setup_logging() call (kicadstamp_gui.py) never passed a log_file
        # at all — only kicadstamp_cli.py's `apply` command honored it (see
        # cli_common.peek_log_file). Reused here so a project's log_file:
        # covers the GUI too, not just the CLI.
        self.root_metadata_dock.root_changed.connect(self._on_root_file_changed_for_logging)
        # RootMetadataDock's own _restore_last_root() runs inside ITS
        # __init__ (gui/docks/root_metadata.py), which happens before
        # THIS wiring exists — so the very first root_changed emit (if a
        # root was restored on startup) fires into the void, before the
        # connect() above. Sync explicitly with whatever value is already
        # current, or a restored project silently opens with the Config
        # tree empty / Rules' Cell combo empty (found live 2026-08-05 for
        # the equivalent ConfigTreeDock-owned case this mirrors).
        self.config_tree_dock.set_root_file(self.root_metadata_dock.root_path)
        self.rules_dock.set_root_path(self.root_metadata_dock.root_path)
        self.placer_dock.set_root_path(self.root_metadata_dock.root_path)
        self.thermal_via_dock.set_root_path(self.root_metadata_dock.root_path)
        self.cells_dock.set_root_path(self.root_metadata_dock.root_path)
        self.points_dock.set_root_path(self.root_metadata_dock.root_path)
        self.extract_dock.set_root_path(self.root_metadata_dock.root_path)
        self.fieldstool_dock.set_root_path(self.root_metadata_dock.root_path)
        self._on_root_file_changed_for_logging(self.root_metadata_dock.root_path)
        # file_selected fires BEFORE the more specific cell_picked/
        # placement_picked/profile_picked signal on a leaf click (see
        # config_tree.py's _on_clicked) — so this fallback runs first and
        # the specific handler below (if any) wins by running after it,
        # same emission order the auto-switch relies on.
        self.config_tree_dock.file_selected.connect(lambda _path: self.detail_dock.show_root())

        # Components tree -> Placer: clicking a Cluster group node in the
        # tree fills PlacerDock's Cluster field; Config tree -> Placer/
        # Extract: clicking a Cell/Clone placement/Extract profile leaf
        # routes into the matching existing form (2026-08-03, GUI tree
        # roadmap Этап 1 — replaces the old CellListDock/PlacerListDock
        # wiring, same target methods, unified single source).
        self.tree_dock.cluster_picked.connect(self.placer_dock.set_cluster_name)
        self.config_tree_dock.cell_picked.connect(self.placer_dock.set_selected_cell)
        self.config_tree_dock.cell_picked.connect(self.detail_dock.show_placer)
        self.config_tree_dock.placement_picked.connect(self.placer_dock.load_placement)
        self.config_tree_dock.placement_picked.connect(self.detail_dock.show_placer)
        self.config_tree_dock.profile_picked.connect(self.extract_dock.pick_profile)
        self.config_tree_dock.profile_picked.connect(self.detail_dock.show_extract)
        self.config_tree_dock.thermal_via_picked.connect(self.thermal_via_dock.load_entry)
        self.config_tree_dock.thermal_via_picked.connect(self.detail_dock.show_thermal_via)
        # Coordinate placements (2026-08-12, Group 1): a normal named-records
        # section now — a leaf click carries the full entry dict, loaded into
        # the merged PlacerDock's coordinate mode, exactly like clone_placements
        # -> placement_picked -> load_placement (see config_tree.py's
        # coordinate_placements_picked docstring).
        self.config_tree_dock.coordinate_placements_picked.connect(self.placer_dock.load_placement)
        self.config_tree_dock.coordinate_placements_picked.connect(self.detail_dock.show_coordinate_placer)
        self.config_tree_dock.points_picked.connect(self.points_dock.load_entry)
        self.config_tree_dock.points_picked.connect(self.detail_dock.show_points)
        self.config_tree_dock.rule_picked.connect(self.rules_dock.load_entry)
        self.config_tree_dock.rule_picked.connect(self.detail_dock.show_rules)
        # "Edit cell..." (context menu, 2026-08-06) — deliberately NOT wired
        # to cell_picked, which keeps meaning "pick this cell as a
        # placement's content" (see config_tree.py's module docstring).
        # Context-menu actions never go through _on_clicked, so unlike a
        # plain leaf click, file_selected has NOT necessarily already
        # targeted CellDock at the right file — _edit_cell below sets it
        # explicitly before loading, same reasoning as _start_new_placement
        # etc. below for "Add ...".
        self.config_tree_dock.cell_edit_requested.connect(self._edit_cell)
        # Placer/Thermal via/Extract/Points/Rules -> Config tree: a
        # successful Save refreshes the whole tree (walk_include_tree() is
        # re-run) so a brand new (or renamed) entry shows up without
        # reassigning Files.
        self.placer_dock.saved.connect(self.config_tree_dock.refresh)
        self.thermal_via_dock.saved.connect(self.config_tree_dock.refresh)
        self.extract_dock.saved.connect(self.config_tree_dock.refresh)
        self.points_dock.saved.connect(self.config_tree_dock.refresh)
        self.rules_dock.saved.connect(self.config_tree_dock.refresh)
        self.cells_dock.saved.connect(self.config_tree_dock.refresh)
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
            self.detail_dock.show_coordinate_placer)
        self.config_tree_dock.add_point_requested.connect(self._start_new_point)
        self.config_tree_dock.add_rule_requested.connect(self._start_new_rule)
        self.config_tree_dock.add_cell_requested.connect(self._start_new_cell)

        # fieldstool tab -> Components tree: an explicit Rescan/Apply there
        # refreshes this tree's schematic view (see FieldsToolDock).
        self.fieldstool_dock.components_changed.connect(self.tree_dock.refresh_schematic_view)

    # ── delegates MainWindow's poll/timer logic drives ────────────────────

    def push_snapshot(self, snapshot, board) -> None:
        """Feed a freshly rebuilt BoardConnection.snapshot into the docks
        that display it — the ONE consumer of the snapshot (see
        gui/main_window.py's _poll)."""
        self.tree_dock.set_footprints(snapshot)
        self.placer_dock.refresh_known_roles(snapshot)
        self.placer_dock.refresh_known_nets(board)
        self.thermal_via_dock.refresh_known_roles(snapshot)
        self.thermal_via_dock.refresh_known_nets(board)
        self.points_dock.refresh_known_roles(snapshot)
        self.rules_dock.refresh_known_roles(snapshot)
        self.rules_dock.refresh_known_nets(board)
        self.cells_dock.refresh_known_roles(snapshot)

    def clear_components(self) -> None:
        """Connection-lost path: empty the Components tree (live mode only —
        set_footprints leaves an active schematic view untouched)."""
        self.tree_dock.set_footprints([])

    def highlight_selection(self, refs) -> None:
        """Board selection -> Components tree highlight (see
        gui/main_window.py's _poll_board_selection)."""
        self.tree_dock.highlight_board_selection(refs)

    def set_board_selection(self, items, selected) -> None:
        """Push the live selection into ExtractDock (its aliases/origin
        combos and button state depend on what's currently selected)."""
        self.extract_dock.set_board_selection(items, selected)

    def push_fieldstool_selection(self, refs) -> None:
        """Live board selection -> embedded fieldstool's target label (Phase
        5.1 — the main GUI's single 400ms tick now feeds BOTH the tree/
        ExtractDock and the embedded fieldstool, whose own selection timer is
        stopped when it shares the main connection)."""
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
        PlacerDock's form and brings the Detail dock's Placer page to
        front, same reasoning as open_fieldstool() above (the action was
        invoked from the Config tree tab, not the Detail tab)."""
        self.placer_dock.new_placement(placer_path)
        self.detail_dock.show_placer()  # raises/shows itself now — see detail_panel.py

    def _start_new_thermal_via(self, file_path) -> None:
        """ConfigTreeDock's add_thermal_via_requested delegate — same
        reasoning as _start_new_placement above, for ThermalViaArrayDock."""
        self.thermal_via_dock.new_thermal_via(file_path)
        self.detail_dock.show_thermal_via()

    def _start_new_point(self, file_path) -> None:
        """ConfigTreeDock's add_point_requested delegate — same reasoning
        as _start_new_placement above, for PointsDock."""
        self.points_dock.new_point(file_path)
        self.detail_dock.show_points()

    def _start_new_rule(self, file_path) -> None:
        """ConfigTreeDock's add_rule_requested delegate — same reasoning as
        _start_new_placement above, for RuleDock."""
        self.rules_dock.new_rule(file_path)
        self.detail_dock.show_rules()

    def _start_new_cell(self, file_path) -> None:
        """ConfigTreeDock's add_cell_requested delegate — same reasoning as
        _start_new_placement above, for CellDock."""
        self.cells_dock.new_cell(file_path)
        self.detail_dock.show_cells()

    def _edit_cell(self, name, file_path) -> None:
        """ConfigTreeDock's cell_edit_requested delegate — right-click
        "Edit cell..." never goes through _on_clicked/file_selected (see
        that wiring's own comment above), so the target file is set
        explicitly here before loading, then the Cells tab is raised."""
        self.cells_dock.set_target_file(file_path)
        self.cells_dock.load_entry(name)
        self.detail_dock.show_cells()

    def _on_root_file_changed_for_logging(self, path) -> None:
        """Attaches a root-logger FileHandler using the CURRENT root
        config's own log_file: (Config.log_file) — see this method's own
        connect() above for why. Re-peeked fresh on every root-file change
        (never cached), matching kicadstamp_cli.py's own per-invocation
        freshness — same cli_common.peek_log_file() helper, so a typo/
        missing log_file: is handled exactly the same way the CLI already
        handles it (a logged warning, never a raise). DEBUG level
        regardless of the GUI's own console/LogDock verbosity, same as the
        CLI's file handler (kicadstamp/logging_setup.py)."""
        root_logger = logging.getLogger()
        if self._log_file_handler is not None:
            root_logger.removeHandler(self._log_file_handler)
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
        root_logger.addHandler(handler)
        self._log_file_handler = handler
