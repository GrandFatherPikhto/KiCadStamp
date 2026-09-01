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

Placer/Root/Points/Rules (placer_dock/root_metadata_dock/points_dock/
rules_dock) are the one exception: 2026-08-03 they were merged into ONE
QDockWidget, DetailDock (gui/docks/detail_panel.py) — its own module
docstring covers why (Points/Rules added 2026-08-05, same shape). Those
attributes are kept as aliases straight into DetailDock's stack pages so every
existing call site keeps working unchanged; they are plain QWidgets now, not
QDockWidgets in their own right. EXCEPT extract_dock (2026-08-31, plan
extract_dialog_and_hide_existing.md) and thermal_via_dock (2026-09-01, plan
plan_2026_09_01_thermal_via_dialog.md): they are STANDALONE widgets hosted in
their non-modal dialogs (ExtractDialog / ThermalViaDialog) — the Detail dock
has no Extract/Thermal via page anymore, and the same single live instances
keep receiving the selection/snapshot ticks, set_root_path and saved.
"""
import logging
from functools import partial
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QDialog, QMessageBox

from kicadstamp.cli_common import peek_log_file
from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.i18n import _
from kicadstamp.logging_setup import get_log_listener

from .docks.anchor_tree import AnchorTreeDock
from .docks.config_tree import ConfigTreeDock
from .docks.configurator import ConfiguratorDock
from .docks.detail_panel import DetailDock
from .docks.trees_dock import TreesDock
from .docks.extract import ExtractDock
from .docks.extract_dialog import ExtractDialog
from .docks.fieldstool_dock import FieldsToolDock
from .docks.log_panel import LogDock
from .docks.pending import PendingChangesDock
from .docks.project_dialog import ProjectDialog
from .docks.role_cluster_tree import RoleClusterTreeDock
from .docks.root_metadata import RootMetadataDock
from .docks.settings_dialog import SettingsDialog
from .docks.thermal_via import ThermalViaArrayDock
from .docks.thermal_via_dialog import ThermalViaDialog


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
        # own QDockWidgets. EXCEPT extract_dock: 2026-08-31 (plan
        # extract_dialog_and_hide_existing.md) it is a STANDALONE widget
        # hosted in the non-modal ExtractDialog (the Detail dock has no
        # Extract page anymore) — the same single live instance keeps
        # receiving the selection-watch ticks / set_root_path / saved.
        self.extract_dock = ExtractDock(main_window, connection=connection)
        self.extract_dialog = ExtractDialog(self.extract_dock, main_window)
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
        self.placer_dock = self.detail_dock.placer_panel
        self.points_dock = self.detail_dock.points_panel
        self.rules_dock = self.detail_dock.rules_panel
        self.net_trace_dock = self.detail_dock.net_trace_panel
        self.cells_dock = self.detail_dock.cells_panel
        # Settings (2026-09-01, plan project_settings_dialogs): ConfiguratorDock
        # is no longer a Detail dock page either — it is a two-pane settings
        # browser (QTreeWidget of categories on the left, pages on the right,
        # see gui/docks/configurator.py) hosted in the MODAL SettingsDialog
        # (Tools > "Settings...", see gui/docks/settings_dialog.py). MainWindow
        # reads its checkboxes back through this alias / settings.state (see
        # _restore_window_state/_persist_settings/closeEvent there).
        self.configurator_dock = ConfiguratorDock(main_window, connection=connection)
        self.settings_dialog = SettingsDialog(self.configurator_dock, main_window)
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

        # Config working set (2026-09-01, plan project_save_model): every
        # stage/clear notifies this listener -> dirty indicator + a debounced
        # refresh so the tree/collectors show the staged content. QTimer-
        # debounced (a burst of stages in one event-loop turn coalesces into
        # one rebuild — same reasoning as AnchorTreeDock.schedule_refresh).
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
        self.config_tree_dock.cell_picked.connect(self.detail_dock.show_placer)
        # Entities leaf (phase 5.6): load into Placer's Entity source.
        self.config_tree_dock.entity_picked.connect(self.placer_dock.set_selected_entity)
        self.config_tree_dock.entity_picked.connect(self.detail_dock.show_placer)
        self.config_tree_dock.placement_picked.connect(self.placer_dock.load_placement)
        self.config_tree_dock.placement_picked.connect(self.detail_dock.show_placer)
        self.config_tree_dock.profile_picked.connect(self.extract_dock.pick_profile)
        self.config_tree_dock.profile_picked.connect(self._open_extract_dialog)
        self.config_tree_dock.thermal_via_picked.connect(self.thermal_via_dock.load_entry)
        self.config_tree_dock.thermal_via_picked.connect(self._open_thermal_via_dialog)
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
        # Auto-close the (non-modal) Extract dialog after a successful Extract
        # (2026-08-31, Denis): saved is emitted by _finish_extract only on
        # success — see gui/docks/extract.py.
        self.extract_dock.saved.connect(self.extract_dialog.hide)
        # Auto-close the (non-modal) Thermal via dialog after a successful Save
        # (2026-09-01, Denis): saved is emitted by _on_save only on success —
        # Redraw (placement) stays open for iterative tuning.
        self.thermal_via_dock.saved.connect(self.thermal_via_dialog.hide)
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
        # "New Extract..." (2026-08-31, plan extract_dialog_and_hide_existing.
        # md) — context menu + Tools menu -> the same plain fresh capture.
        self.config_tree_dock.new_extract_requested.connect(self._start_new_extract)
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
        """Push the live selection into the docks that react to it:
        ExtractDock (its aliases/origin combos and button state depend on
        what's currently selected) and PlacerDock (2026-08-31, plan
        placer_source_tab_gaps P.1 — its Cell-mode Cluster auto-fill reads
        the current selection's Cluster)."""
        self.extract_dock.set_board_selection(items, selected)
        self.placer_dock.set_board_selection(items, selected)

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
        reasoning as _start_new_placement above, for ThermalViaArrayDock: opens
        the (non-modal) Thermal via dialog with a fresh blank form."""
        self._open_thermal_via_dialog()
        self.thermal_via_dock.new_thermal_via(file_path)

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

    def reead_selected(self) -> None:
        """Main menu "Tools -> Re-read selected..." (2026-08-31, plan
        reead_selected_dialog.md) -> ExtractDock's batch re-read of the
        fully-selected Clusters (dialog with Entities + checkboxes)."""
        self.extract_dock.re_read_selected()

    def new_extract(self) -> None:
        """Main menu "Tools -> New Extract..." (2026-09-01) -> the same plain
        fresh capture as the Config tree context menu's "New Extract..."
        (new_extract_requested -> _start_new_extract)."""
        self._start_new_extract()

    def place_thermal_vias(self) -> None:
        """Main menu "Tools -> Place thermal vias..." (2026-09-01, plan
        plan_2026_09_01_thermal_via_dialog.md) -> the same fresh blank form as
        the Config tree context menu's "Add thermal via pad..." (add_thermal_
        via_requested -> _start_new_thermal_via)."""
        self._start_new_thermal_via(self.root_metadata_dock.root_path)

    def extract_tree_from_selection(self) -> None:
        """Main menu "Tools -> Extract tree..." (2026-09-01, plan
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
            self.extract_dock._selected_footprints,
            list(connection.snapshot or []),
            list(cfg.entities),
            (),
            sheet_names=sheet_names)
        # Diagnostic + defensive filter (same rationale as re_read_selected:
        # a row must be a sane single-line cluster).
        clusters = [c for c in clusters if c.cluster and "\n" not in c.cluster]
        if not clusters:
            QMessageBox.warning(
                self.main_window, _("Extract tree"),
                _("No fully selected Cluster found — select ALL components of a "
                  "cluster (its Cluster tag + sheet) first."))
            return

        from .docks.tree_from_selection import (
            cluster_errors,
            detect_inter_cluster_nets,
            resolve_entity_live_position_mm,
            resolve_role_anchor_base_mm,
            tree_anchor_from_cluster_entity,
        )
        from .docks.tree_from_selection_dialog import TreeFromSelectionDialog

        inter_nets = detect_inter_cluster_nets(
            self.extract_dock._raw_items, clusters,
            list(connection.snapshot or []),
            [r.net for r in cfg.rules])

        # Per-row "no cell" errors (block OK in the dialog) + per-row "existing
        # cluster anchor" prefills + the live Entity positions for the offset
        # preview. A failed live read just omits the position (the node is then
        # saved without xy — live-position rule at apply), never a crash.
        errors = cluster_errors(clusters, cfg.entities, cfg)
        prefills: dict[int, object] = {}
        entity_positions: dict[str, tuple[float, float]] = {}
        for i, c in enumerate(clusters):
            entity = next((e for e in cfg.entities
                           if e.name == c.entity_name), None)
            if entity is None:
                continue
            prefills[i] = tree_anchor_from_cluster_entity(entity, cfg)
            try:
                entity_positions[entity.name] = resolve_entity_live_position_mm(
                    adapter, cfg, entity, sheet_names)
            except Exception as e:  # noqa: BLE001 — live read, best-effort preview
                logging.warning("Extract tree: live position of Entity %r "
                                "unavailable: %s", entity.name, e)

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
        tree, build_errors = build_tree_from_clusters(
            selected, tree_name, anchor, cfg.entities, cfg,
            entity_positions=entity_positions, anchor_base=anchor_base)
        if tree is None:
            QMessageBox.warning(self.main_window, _("Extract tree"),
                                _("Cannot build the tree:\n{errors}")
                                .format(errors="\n".join(build_errors)))
            return

        # ── Save: trees: first, then the checked nets as net_traces: ──────
        from kicadstamp.config import load_tree
        from kicadstamp.config_writer import read_data, write_data
        from kicadstamp.link_trees import link_trees
        from kicadstamp.net_trace_extract import extract_net_trace, write_net_trace
        from kicadstamp.trees import tree_to_dict
        from .docks.entity_delete import backup_file
        try:
            backup_file(root_path)
            trees_dict = [tree_to_dict(t) for t in cfg.trees] + [tree_to_dict(tree)]
            write_data(root_path, {**read_data(root_path), "trees": trees_dict})
            reloaded = [load_tree(t) for t in trees_dict]
            link_trees(cfg, reloaded)
        except Exception as e:  # noqa: BLE001 — .bak is fresh; report, don't roll back
            QMessageBox.warning(self.main_window, _("Extract tree"),
                                _("Saved, but the round-trip check failed: {error}")
                                .format(error=e))
            return
        for net in dialog.selected_nets():
            try:
                nt = extract_net_trace(
                    adapter, net=net.net,
                    anchor_role=anchor.role,
                    anchor_sheet=anchor.anchor_sheet,
                    anchor_cluster=anchor.anchor_cluster,
                    anchor_pad=anchor.anchor_pad,
                    sheet_names=sheet_names)
                write_net_trace(str(root_path), nt)
            except Exception as e:  # noqa: BLE001 — one bad net must not drop the tree
                logging.warning("Extract tree: net %r not captured: %s",
                                net.net, e)

        # ── Refresh: show the new tree + graph everywhere without a restart ──
        self.trees_dock.reload_trees()
        self.config_tree_dock.refresh()
        self.config_tree_dock.graph_changed.emit()
        QMessageBox.information(
            self.main_window, _("Extract tree"),
            _("Tree {name!r} saved to {path}.")
            .format(name=tree.name, path=root_path))

    def _start_new_extract(self) -> None:
        """ConfigTreeDock's new_extract_requested delegate ("New Extract...",
        2026-08-31, plan extract_dialog_and_hide_existing.md): a plain fresh
        capture — opens the (non-modal) Extract dialog and arms it via
        ExtractDock.prepare_new_extract (clears Cell name / Profile key,
        unchecks profile save, auto-fills from the current Cluster)."""
        self._open_extract_dialog()
        self.extract_dock.prepare_new_extract()

    def _open_extract_dialog(self) -> None:
        """Show/raise the ONE live Extract dialog — non-modal, so the user can
        keep selecting on the board while it's open: the selection-watch tick
        keeps feeding the same extract_dock instance inside it. Closing via
        the window X just hides it (QDialog default), so the next open starts
        from the current board state."""
        self.extract_dialog.show()
        self.extract_dialog.raise_()
        self.extract_dialog.activateWindow()

    def _open_thermal_via_dialog(self) -> None:
        """Show/raise the ONE live Thermal via dialog — non-modal, so the user
        can keep selecting on the board while it's open: the ~2s snapshot tick
        keeps feeding the same thermal_via_dock instance inside it. Closing via
        the window X just hides it (QDialog default), so the next open starts
        from the current board state."""
        self.thermal_via_dialog.show()
        self.thermal_via_dialog.raise_()
        self.thermal_via_dialog.activateWindow()

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
        self.rules_dock.set_root_path(root_path)
        self.placer_dock.set_root_path(root_path)
        self.thermal_via_dock.set_root_path(root_path)
        self.cells_dock.set_root_path(root_path)
        self.tools_dock.set_root_path(root_path)
        self.points_dock.set_root_path(root_path)
        self.extract_dock.set_root_path(root_path)
        self.trees_dock.refresh_ref_candidates()
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

    def _sync_root_to_docks(self, path) -> None:
        """Re-notify every root_changed consumer with `path` (the current root
        or None), each guarded by _safe_call — the startup sync AND the
        Discard path (reload_project_from_disk) share this one list, so a
        discard can never drift from the initial wiring."""
        self._safe_call("config_tree_dock.set_root_file",
                        self.config_tree_dock.set_root_file, path)
        self._safe_call("anchor_tree_dock.set_root_file",
                        self.anchor_tree_dock.set_root_file, path)
        self._safe_call("trees_dock.set_root_file", self.trees_dock.set_root_file, path)
        self._safe_call("rules_dock.set_root_path", self.rules_dock.set_root_path, path)
        self._safe_call("placer_dock.set_root_path", self.placer_dock.set_root_path, path)
        self._safe_call("thermal_via_dock.set_root_path",
                        self.thermal_via_dock.set_root_path, path)
        self._safe_call("cells_dock.set_root_path", self.cells_dock.set_root_path, path)
        self._safe_call("tools_dock.set_root_path", self.tools_dock.set_root_path, path)
        self._safe_call("points_dock.set_root_path", self.points_dock.set_root_path, path)
        self._safe_call("extract_dock.set_root_path", self.extract_dock.set_root_path, path)
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
        self.anchor_tree_dock.schedule_refresh()
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
