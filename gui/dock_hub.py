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
from functools import partial
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt

from kicadstamp.cli_common import peek_log_file
from kicadstamp.logging_setup import get_log_listener

from .docks.anchor_tree import AnchorTreeDock
from .docks.config_tree import ConfigTreeDock
from .docks.trees_dock import TreesDock
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

        # Anchor dependency tree (2026-08-21, plan anchor_dependency_tree) —
        # the same records as ConfigTreeDock, regrouped by anchor edges
        # instead of file/section. Tabbed with the Config tree.
        self.anchor_tree_dock = AnchorTreeDock(main_window)
        main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.anchor_tree_dock)
        main_window.tabifyDockWidget(self.config_tree_dock, self.anchor_tree_dock)

        # Hand-authored s-expr "trees" editor (2026-08-27, design
        # design_2026_08_27_trees_gui_dock.md) — tabbed with the Config/Anchor
        # trees so the user finds "tree" in one place.
        self.trees_dock = TreesDock(main_window)
        main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.trees_dock)
        main_window.tabifyDockWidget(self.anchor_tree_dock, self.trees_dock)

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
        self.net_trace_dock = self.detail_dock.net_trace_panel
        self.cells_dock = self.detail_dock.cells_panel
        # Settings tab (ConfiguratorDock, 2026-08-15, plan
        # configurator_panel) — GUI/app settings for this machine, NOT
        # project config (see gui/docks/configurator.py's module docstring).
        # MainWindow reads its checkboxes back through this alias (see
        # _restore_window_state/_persist_settings/closeEvent there).
        self.configurator_dock = self.detail_dock.configurator_panel
        # Tools tab (ToolsDock, 2026-08-30 phase 5.2 stage 3) — the Entity's
        # electrical fields, moved out of PlacerDock.
        self.tools_dock = self.detail_dock.tools_panel

        # ── bottom: Pending changes, Log ────────────────────────────────────
        main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.pending_dock)
        self.log_dock = LogDock(main_window, verbose=verbose)
        main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        main_window.tabifyDockWidget(self.pending_dock, self.log_dock)

        # All real TOP-LEVEL QDockWidgets (2026-08-27, handoff
        # sync_skip_message_and_view_menu): MainWindow's View menu wires each
        # one's ready-made toggleViewAction() so a closed dock can be brought
        # back without restarting. Deliberately NOT DetailDock's internal
        # panels (extract_dock/placer_dock/... are plain QWidgets switched by
        # its own tab bar — not independently closable/dockable, no
        # toggleViewAction of their own). Order matches construction above
        # (already grouped by area: Left / right / bottom).
        self.docks = [
            self.tree_dock, self.config_tree_dock, self.anchor_tree_dock,
            self.trees_dock, self.pending_dock, self.fieldstool_dock,
            self.detail_dock, self.log_dock,
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
            partial(self._safe_call, "anchor_tree_dock.set_root_file",
                    self.anchor_tree_dock.set_root_file))
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "trees_dock.set_root_file",
                    self.trees_dock.set_root_file))
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "rules_dock.set_root_path",
                    self.rules_dock.set_root_path))
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
        # Extract's own Cell file/Profile file combos (added 2026-08-06,
        # Denis: "имя файла, куда пишем extract и cell... тоже, выпадашками"
        # — un-couples them from always following the same file_selected
        # click) need the whole include graph too, same reasoning as above.
        self.root_metadata_dock.root_changed.connect(
            partial(self._safe_call, "extract_dock.set_root_path",
                    self.extract_dock.set_root_path))
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
        self._safe_call("config_tree_dock.set_root_file",
                        self.config_tree_dock.set_root_file,
                        self.root_metadata_dock.root_path)
        self._safe_call("anchor_tree_dock.set_root_file",
                        self.anchor_tree_dock.set_root_file,
                        self.root_metadata_dock.root_path)
        self._safe_call("trees_dock.set_root_file", self.trees_dock.set_root_file,
                        self.root_metadata_dock.root_path)
        self._safe_call("rules_dock.set_root_path", self.rules_dock.set_root_path,
                        self.root_metadata_dock.root_path)
        self._safe_call("placer_dock.set_root_path", self.placer_dock.set_root_path,
                        self.root_metadata_dock.root_path)
        self._safe_call("thermal_via_dock.set_root_path",
                        self.thermal_via_dock.set_root_path,
                        self.root_metadata_dock.root_path)
        self._safe_call("cells_dock.set_root_path", self.cells_dock.set_root_path,
                        self.root_metadata_dock.root_path)
        self._safe_call("tools_dock.set_root_path", self.tools_dock.set_root_path,
                        self.root_metadata_dock.root_path)
        self._safe_call("points_dock.set_root_path", self.points_dock.set_root_path,
                        self.root_metadata_dock.root_path)
        self._safe_call("extract_dock.set_root_path", self.extract_dock.set_root_path,
                        self.root_metadata_dock.root_path)
        self._safe_call("net_trace_dock.set_root_path", self.net_trace_dock.set_root_path,
                        self.root_metadata_dock.root_path)
        self._safe_call("fieldstool_dock.set_root_path",
                        self.fieldstool_dock.set_root_path,
                        self.root_metadata_dock.root_path)
        self._safe_call("_on_root_file_changed_for_logging",
                        self._on_root_file_changed_for_logging,
                        self.root_metadata_dock.root_path)
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
        # Entities leaf (phase 5.6): load into Placer's Entity source.
        self.config_tree_dock.entity_picked.connect(self.placer_dock.set_selected_entity)
        self.config_tree_dock.entity_picked.connect(self.detail_dock.show_placer)
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
        self.config_tree_dock.net_trace_picked.connect(self.net_trace_dock.load_entry)
        self.config_tree_dock.net_trace_picked.connect(self.detail_dock.show_net_trace)
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
        self.extract_dock.saved.connect(self.config_tree_dock.refresh)
        self.points_dock.saved.connect(self.config_tree_dock.refresh)
        self.rules_dock.saved.connect(self.config_tree_dock.refresh)
        self.net_trace_dock.saved.connect(self.config_tree_dock.refresh)
        self.cells_dock.saved.connect(self.config_tree_dock.refresh)
        # The anchor tree shows the SAME records — refresh it on every Save too,
        # but DEBOUNCED (schedule_refresh coalesces a burst of saves into one
        # rebuild on the next event-loop turn, so it never stacks a second
        # synchronous full YAML re-read on top of ConfigTreeDock.refresh()).
        self.placer_dock.saved.connect(self.anchor_tree_dock.schedule_refresh)
        self.thermal_via_dock.saved.connect(self.anchor_tree_dock.schedule_refresh)
        self.extract_dock.saved.connect(self.anchor_tree_dock.schedule_refresh)
        self.points_dock.saved.connect(self.anchor_tree_dock.schedule_refresh)
        self.rules_dock.saved.connect(self.anchor_tree_dock.schedule_refresh)
        self.net_trace_dock.saved.connect(self.anchor_tree_dock.schedule_refresh)
        self.cells_dock.saved.connect(self.anchor_tree_dock.schedule_refresh)
        self.tools_dock.saved.connect(self.config_tree_dock.refresh)
        self.tools_dock.saved.connect(self.anchor_tree_dock.schedule_refresh)
        self.tools_dock.saved.connect(self._refresh_graph_dependent_choices)
        self.placer_dock.saved.connect(self._refresh_graph_dependent_choices)
        self.thermal_via_dock.saved.connect(self._refresh_graph_dependent_choices)
        self.extract_dock.saved.connect(self._refresh_graph_dependent_choices)
        self.points_dock.saved.connect(self._refresh_graph_dependent_choices)
        self.rules_dock.saved.connect(self._refresh_graph_dependent_choices)
        self.cells_dock.saved.connect(self._refresh_graph_dependent_choices)
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
        # "Add extract profile..." (2026-08-13, plan context_menu_by_section)
        # — NOT a blank form like the other Add-actions: it prepares the
        # Extract flow for a profile save (see ExtractDock.prepare_new_profile).
        self.config_tree_dock.add_extract_profile_requested.connect(
            self._start_new_extract_profile)
        # Config tree's own graph-mutating actions (_on_rename/_on_delete/
        # _add_included_file/_remove_file) -> every dock's graph-derived
        # combos, same handler the six entity-dock `saved` signals above
        # feed (2026-08-15, plan graph_changed_broadcast): a file added or
        # removed, or a cell:/point: name renamed or deleted, must be
        # visible everywhere immediately, not only after the root is
        # reassigned. NOT wired to the tree's initial refresh (first
        # population, not a change) or to _on_export (no graph change).
        self.config_tree_dock.graph_changed.connect(self._refresh_graph_dependent_choices)
        self.config_tree_dock.graph_changed.connect(self.anchor_tree_dock.schedule_refresh)

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
        """Re-apply the highlight stylesheet to the three highlight
        consumers — DetailDock's active tab, ConfigTreeDock's and
        RoleClusterTreeDock's selected tree item — after a change in the
        Settings tab (see gui/docks/configurator.py)."""
        self.detail_dock.apply_highlight()
        self.config_tree_dock.apply_highlight()
        self.anchor_tree_dock.apply_highlight()
        self.trees_dock.apply_highlight()
        self.tree_dock.apply_highlight()

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
        self.net_trace_dock.refresh_known_roles(snapshot)
        self.net_trace_dock.refresh_known_nets(board)
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

    def _start_new_extract_profile(self, file_path) -> None:
        """ConfigTreeDock's add_extract_profile_requested delegate — same
        reasoning as _start_new_placement above, for ExtractDock's profile
        preparation (see extract.py's prepare_new_profile — deliberately not
        a blank form like the other Add-actions). The Extract page is shown
        BEFORE preparing (bug 1, 2026-08-13): prepare_new_profile ends with
        profile_key_edit.setFocus(), and a setFocus on a page that isn't yet
        the current one in the QStackedWidget doesn't stick — the checkbox
        would still turn on, but the intended visible signal (focus in the
        profile-key field) would be silently lost."""
        self.detail_dock.show_extract()
        self.extract_dock.prepare_new_profile(file_path)

    def _refresh_graph_dependent_choices(self) -> None:
        """The include: graph's shape or an entry's name changed — either
        via ConfigTreeDock's own actions (add/remove a file, rename/delete a
        cell/point/...) or via one of the six entity docks' own Save
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
        docstring). Cheap to call repeatedly since 2026-08-15's mtime file
        cache (plan_2026_08_15_config_read_cache_startup.md) — this handler
        does NOT need its own caching, it just needs to fire at the right
        moments, which it previously didn't (found live — Denis: adding a
        Placer to a brand-new file required the file to already be visible
        from wherever the new file was created, the tree's own action never
        told any other dock)."""
        root_path = self.root_metadata_dock.root_path
        self.rules_dock.set_root_path(root_path)
        self.placer_dock.set_root_path(root_path)
        self.thermal_via_dock.set_root_path(root_path)
        self.cells_dock.set_root_path(root_path)
        self.tools_dock.set_root_path(root_path)
        self.points_dock.set_root_path(root_path)
        self.extract_dock.set_root_path(root_path)
        self.root_metadata_dock.refresh_working_file_choices()

    def _edit_cell(self, name, file_path) -> None:
        """ConfigTreeDock's cell_edit_requested delegate — right-click
        "Edit cell..." never goes through _on_clicked/file_selected (see
        that wiring's own comment above), so the file the cell lives in is
        passed explicitly here before loading — a later Save writes the edit
        back to that file, not the root (2026-08-21 review fix)."""
        self.cells_dock.load_entry(name, file_path)
        self.detail_dock.show_cells()

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
