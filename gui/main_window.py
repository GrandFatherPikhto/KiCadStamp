# gui/main_window.py
"""
MainWindow — persistent shell for the KiCadStamp GUI: connection lifecycle
+ status bar + docks (Role/Cluster tree, fieldstool, config tree,
extract-to-file) + an optional tray icon. The docks themselves live in a
DockHub controller (see gui/dock_hub.py, Phase 3.3) — MainWindow only owns
the window and the BoardConnection, and drives its docks through DockHub
delegates.

Step 1: RoleClusterTreeDock — connect/reconnect, poll, show the live
snapshot grouped by Role/Cluster, click to highlight on the real board.
Step 2 used to be BulkFieldEditorDock, a PCB-only live-IPC Role/Cluster
editor — retired 2026-08-01: any field it wrote got silently reverted by
KiCad's own "Update PCB from Schematic" (Role/Cluster actually originate in
the schematic symbol), which is exactly the problem `fieldstool` was built
to solve correctly (direct `.kicad_sch` edits). 2026-09-05 (plan
components_fieldstool_master_detail) made the Components dock a master-detail
(tree | Pending tabs on the left, fieldstool's own standalone MainWindow as
the right QView), so FieldsToolDock (see gui/docks/fieldstool_dock.py) is now
only a non-dock facade over that window, not a right-hand dock. Then
ConfigTreeDock (pick a Root file, browse/edit its include: graph — folded
FilePickerDock's job into it 2026-08-03, see gui/docks/config_tree.py).
Phase F (2026-09-01) removed the Extract dock entirely — "Extract tree..."
(see DockHub.extract_tree_from_selection) is the single capture entry point.
kipy 0.7.1's Board has no selection/board-change push events (checked
directly against
the installed kipy.board.Board class), so "live" here means polled on a
QTimer, not pushed.

The timer's automatic tick only ever tries to CONNECT (while disconnected)
— it deliberately never re-fetches/rebuilds the tree on its own. An earlier
version also auto-refreshed every tick while connected, which rebuilds
RoleClusterTreeDock's whole QStandardItemModel each time; even with
selection/expansion restored, the visible flash/scroll-jump on an idle,
unchanged board was distracting (reported live 2026-08-01). Re-fetching the
snapshot and rebuilding the tree now only happens on an explicit action —
the status-bar button (Reconnect while disconnected, Refresh while
connected) — a deliberate user action, not a timer tick.

A SEPARATE, faster timer watches the board's own GUI selection (board ->
tree, the reverse of clicking a tree node) so re-selecting something by
mouse in KiCad shows up in the tree too.

Both `_poll()` and `_poll_board_selection()` dispatch their actual IPC
(connect()/refresh()/get_selected_items()) through gui/worker.py's
PollWorkerHandle — a background worker thread, same idea as Extract/
Redraw's start_long_op, instead of calling it directly on the UI thread
(2026-08-03 fix: a QTimer.timeout handler that blocks on a kipy call froze
the whole window, including repaint and input, for up to the socket's full
recv timeout — 20s, DEFAULT_TIMEOUT_MS — whenever KiCad disappeared
mid-request; not a deadlock, just an honest ~20s hang per bad tick, but
enough for the desktop to report "Application not responding"). Unlike
start_long_op (a fresh QThread + QObject per call — fine for Extract/
Redraw, rare one-shot ops), PollWorkerHandle is ONE persistent QThread +
QObject built once at startup and dispatched to via plain signal emits —
recreating the worker on every ~400ms-2s tick turned out to occasionally
deadlock (GIL vs. a Qt-internal connection mutex, see PollWorkerHandle's
own docstring and handoff_2026_08_07_worker_thread_gil_deadlock.md).
kipy's connection is a plain pynng.Req0 (request/reply) socket with no
per-request timeout override (the timeout is fixed once, at socket-connect
time — see kipy/client.py) and no locking, so only ONE request may be in
flight at a time across the whole app — `long_op_active` (see
connection.py) now mutually excludes a poll tick against a real long op
AND against itself (a tick that finds the flag already True — real op or a
still-running previous tick — just skips its turn silently; no queueing,
at most one poll-related task is ever in flight).

The fast tick deliberately does NOT call board.select() itself — the full
snapshot is cached on BoardConnection (see connection.py) and rebuilt only
by the ~2s poll / manual Refresh, and the tick builds its `selected` list by
ref against that cache. Building it on every tick was the main perf bug of
this timer (a full select() over every footprint, 2-3x a second). The tick
also early-exits entirely when neither the raw selection nor the cached
snapshot changed since the last tick, so the selection push to DockHub /
the tree highlight isn't churned for nothing.
"""
import base64
import logging
from pathlib import Path
from typing import Optional

from kicadstamp.domain.board import Footprint
from PyQt6.QtCore import QByteArray, Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow, QMenu,
                              QMessageBox, QPushButton, QSystemTrayIcon)

from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.explore import selection_signature
from kicadstamp.i18n import _

from . import settings
from .connection import BoardConnection
from .dock_hub import DockHub
from .app_icon import build_app_icon
from .docks.profile_import import run_import_dialog
from .hotkeys import build_action
from .kicad_processes_dialog import KicadProcessesDialog
from .worker import PollWorkerHandle

# Stable QAction ids for the global project save model (2026-09-01, plan
# project_save_model) — the same registry the Settings tab's hotkey list uses.
PROJECT_SAVE = "project.save"
PROJECT_DISCARD = "project.discard"

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 2000
SELECTION_POLL_INTERVAL_MS = 400

# Bump this if a FUTURE dock-layout change should make an old saved layout
# intentionally stale — restoreState() then just returns False and Qt falls
# back to whatever DockHub laid out by default (never a crash).
# Version 2 (2026-08-27, handoff sync_skip_message_and_view_menu §0): v1
# blobs were saved WITHOUT per-dock objectName(), so Qt could not reliably
# identify which dock a layout entry belonged to — such a blob is useless/
# potentially confusing, and a bump makes restoreState() ignore it cleanly.
# Version 3 (2026-09-05, plan components_fieldstool_master_detail): the
# fieldstool dock (right) and the Pending dock (bottom) are gone — both are
# now pages of the Components master-detail dock, so the old 6-dock blob
# would be mapped onto a 4-dock set and must be ignored.
_DOCK_STATE_VERSION = 3


class MainWindow(QMainWindow):
    def __init__(self, timeout_ms: int, verbose: bool = False):
        super().__init__()
        self.setWindowTitle(_("KiCadStamp"))
        self.resize(360, 640)

        self.connection = BoardConnection(timeout_ms=timeout_ms)
        self._tray_icon: Optional[QSystemTrayIcon] = None

        self.status_label = QLabel(_("Not connected"))
        self.action_button = QPushButton(_("Reconnect"))
        self.action_button.clicked.connect(lambda: self._poll(manual=True))
        self.statusBar().addWidget(self.status_label, 1)

        # Project-dirty indicator (2026-09-01, plan project_save_model) — a ●
        # whenever the config working set has unsaved edits. Driven by
        # DockHub._update_dirty_indicator on every stage/clear.
        self.dirty_label = QLabel("")
        self.statusBar().addPermanentWidget(self.dirty_label)

        # Always on top / Tray icon checkboxes moved to the Settings tab
        # (ConfiguratorDock) 2026-08-15 — see gui/docks/configurator.py. The
        # actual window-flag/tray-icon LOGIC stays here (_set_always_on_top/
        # _set_tray_enabled); only the UI moved. DockHub wires the
        # configurator's always_on_top_toggled/tray_enabled_toggled signals
        # back onto these two methods (see gui/dock_hub.py), and
        # _restore_window_state/_persist_settings read the checkboxes through
        # self._dock_hub.configurator_dock below.

        self.open_fieldstool_button = QPushButton(_("Open fieldstool"))
        self.open_fieldstool_button.clicked.connect(self.open_fieldstool)
        self.statusBar().addPermanentWidget(self.open_fieldstool_button)

        # 2026-08-03 — a crashed/frozen kicad.exe left running alongside a
        # fresh one blocked the fresh one's IPC connection; this is a
        # shortcut for "look in Task Manager, pick the stuck one, kill it by
        # hand", not an automated decision (see gui/kicad_processes_dialog.py).
        self.kicad_processes_button = QPushButton(_("KiCad processes..."))
        self.kicad_processes_button.clicked.connect(self._show_kicad_processes)
        self.statusBar().addPermanentWidget(self.kicad_processes_button)

        self.statusBar().addPermanentWidget(self.action_button)

        # All docks + their layout and inter-dock signal wiring live in
        # DockHub (Phase 3.3) — MainWindow keeps ownership of the window and
        # the BoardConnection, and drives its docks through this controller.
        self._dock_hub = DockHub(self, connection=self.connection, verbose=verbose)
        # Restoring "schematic mode" rebuilds the Components tree, and that
        # rebuild resolves main_window.fieldstool_dock through the tree
        # dock's lazy lookup — only possible now that _dock_hub is bound
        # (see DockHub.restore_tree_mode()).
        self._dock_hub.restore_tree_mode()

        # File menu (2026-08-30, plan dock_toolbars_menus_hotkeys Этап 1b) —
        # by FUNCTION, not per-dock. "Project..." (2026-09-01, plan
        # project_settings_dialogs) opens the non-modal ProjectDialog that
        # hosts the WHOLE RootMetadataDock — Open/New/Recent, the root
        # settings and the Working-file combo all moved INTO that dialog (the
        # old File > Open/New/Recent entries are gone; the Recent submenu with
        # them — the same recent_root_files source lives in the dock's own
        # Recent combo). Ctrl+O/Ctrl+N stay app-wide regardless of the
        # dialog's visibility: the dock's own build_action actions
        # (gui/hotkeys.py) are still registered on this window. Close is a
        # root-dock operation (close_project, guarded by the same
        # unsaved-changes prompt set_root_file uses). Quit reuses self._quit
        # (already the tray menu's handler).
        file_menu = self.menuBar().addMenu(_("&File"))
        self.project_action = QAction(_("&Project..."), self)
        self.project_action.triggered.connect(
            lambda: self._dock_hub._open_project_dialog())
        file_menu.addAction(self.project_action)

        # Save / Discard (2026-09-01, plan project_save_model) — the ONE global
        # save model: every dock's edits stage into the working set, and only
        # File > Save (Ctrl+S) commits them to disk. Discard reverts them.
        # Registered via build_action so Ctrl+S appears in the Settings hotkey
        # list and File > Save is rebindable like every other action.
        self.save_action = build_action(self, PROJECT_SAVE, _("&Save"), "Ctrl+S",
                                        self._save_project)
        file_menu.addAction(self.save_action)
        self.discard_action = build_action(
            self, PROJECT_DISCARD, _("&Discard unsaved changes..."), "",
            self._discard_project)
        file_menu.addAction(self.discard_action)

        self.close_action = QAction(_("Close"), self)
        self.close_action.triggered.connect(self.root_metadata_dock.close_project)
        file_menu.addAction(self.close_action)

        file_menu.addSeparator()
        self.quit_action = QAction(_("&Quit"), self)
        # lambda, not a direct connect: self._quit resolves at trigger time,
        # so tests (and any future rebinding) can patch it — same pattern as
        # the status-bar action_button above.
        self.quit_action.triggered.connect(lambda: self._quit())
        file_menu.addAction(self.quit_action)

        # (2026-09-04, plan staged_delete_stale_tree_and_save_hotkey Bug B) The
        # global File actions above (project.save / project.discard) registered
        # AFTER DockHub built the Settings Hotkeys page — rebuild that list now
        # so the global Save (Ctrl+S default) is present and rebindable, and so
        # the retired root_metadata.save can never be the row a Ctrl+S binding
        # lands on (which made the real Ctrl+S ambiguous and dead).
        self._dock_hub.configurator_dock.refresh_hotkeys()

        # Edit menu (2026-08-31, plan copy_cell_entity_from_profile) — by
        # function like File, not per-dock. Import from profile... copies one
        # Cell/Entity/Chain from ANOTHER profile into the current project BY
        # VALUE (independent copy, no include: link) — the picker dialog is
        # gui/docks/profile_import.py, and it needs the current project root
        # (owned by RootMetadataDock) to write the copy into.
        edit_menu = self.menuBar().addMenu(_("Edit"))
        self.import_from_profile_action = QAction(_("&Import from profile..."), self)
        self.import_from_profile_action.triggered.connect(lambda: run_import_dialog(self))
        edit_menu.addAction(self.import_from_profile_action)

        # Tools menu (2026-08-31, plan reead_selected_dialog.md) — between
        # Edit and View. Phase F (2026-09-01): "Re-read selected..." and
        # "New Extract..." were removed with the Extract dock. Since 2026-09-03
        # (plan plan_2026_09_03_trees_menu_tools.md) EVERY tree-related entry
        # lives in the nested "Trees" submenu below — both the capture flows
        # (Extract tree…/Extract cluster…/Full redraw…/Instances…) and the
        # whole-tree management actions relocated from the TreesDock toolbar
        # (Create/Rename/Delete tree, Anchor position, Redraw selected/whole).
        # The other Tools entries stay in the menu's root.
        tools_menu = self.menuBar().addMenu(_("Tools"))

        # ── Trees submenu (2026-09-03) ──────────────────────────────────
        # Every tree-related Tools action in one block. Create/Rename/Delete
        # tree + Anchor position + Redraw selected/whole were the TreesDock
        # toolbar buttons (removed 2026-09-03); they route through the same
        # dock handlers via DockHub delegates (which focus the dock first).
        self.trees_menu = tools_menu.addMenu(_("Trees"))
        # "Extract tree..." (2026-09-01, plan extract_selection_as_tree.md): a
        # NEW tree from the current board selection — fully-selected Clusters
        # become placement nodes (xy relative to a chosen role anchor), checked
        # inter-cluster nets are captured as net_traces: records.
        self.extract_tree_action = QAction(_("Extract tree..."), self)
        self.extract_tree_action.triggered.connect(
            lambda: self._dock_hub.extract_tree_from_selection())
        self.trees_menu.addAction(self.extract_tree_action)
        # "Extract cluster..." (2026-09-03, plan extract_cluster_entity): a
        # NARROWER sibling of "Extract tree..." — ONE fully-selected Cluster ->
        # a standalone flat Entity (+ its Cell if missing), NO tree node, NO
        # anchor, NO net_traces (an Entity stores no position; the user places
        # it later by any mechanism).
        self.extract_cluster_action = QAction(_("Extract cluster..."), self)
        self.extract_cluster_action.triggered.connect(
            lambda: self._dock_hub.extract_cluster_from_selection())
        self.trees_menu.addAction(self.extract_cluster_action)
        self.trees_menu.addSeparator()
        # Whole-tree management (relocated from the TreesDock toolbar,
        # 2026-09-03): "Create tree..." is the empty/manual tree — the dock's
        # old "Add tree…" renamed — and answers the missing "Создать дерево"
        # entry next to "Extract tree...".
        self.create_tree_action = QAction(_("Create tree..."), self)
        self.create_tree_action.triggered.connect(
            lambda: self._dock_hub.create_tree())
        self.trees_menu.addAction(self.create_tree_action)
        self.rename_tree_action = QAction(_("Rename tree..."), self)
        self.rename_tree_action.triggered.connect(
            lambda: self._dock_hub.rename_tree())
        self.trees_menu.addAction(self.rename_tree_action)
        self.delete_tree_action = QAction(_("Delete tree..."), self)
        self.delete_tree_action.triggered.connect(
            lambda: self._dock_hub.delete_tree())
        self.trees_menu.addAction(self.delete_tree_action)
        self.anchor_position_action = QAction(_("Anchor position"), self)
        self.anchor_position_action.triggered.connect(
            lambda: self._dock_hub.anchor_position())
        self.trees_menu.addAction(self.anchor_position_action)
        self.trees_menu.addSeparator()
        # Redraw (relocated from the TreesDock toolbar, 2026-09-03):
        # "Redraw selected" = the current tree's CHECKED nodes; "Redraw whole
        # tree" = every node of the current tree; "Full redraw..." = the
        # forest-wide module-aware run across ALL trees (plan 2026-09-02 P3).
        self.redraw_selected_action = QAction(_("Redraw selected"), self)
        self.redraw_selected_action.triggered.connect(
            lambda: self._dock_hub.redraw_selected())
        self.trees_menu.addAction(self.redraw_selected_action)
        self.redraw_whole_tree_action = QAction(_("Redraw whole tree"), self)
        self.redraw_whole_tree_action.triggered.connect(
            lambda: self._dock_hub.redraw_whole_tree())
        self.trees_menu.addAction(self.redraw_whole_tree_action)
        # "Full redraw (all trees and modules)..." (2026-09-02, plan
        # plan_2026_09_02_tree_module_embedding.md P3 п.3): the forest-wide,
        # module-aware curated redraw across ALL trees — menu-only, per Denis:
        # NO new dock buttons.
        self.full_redraw_action = QAction(
            _("Full redraw (all trees and modules)..."), self)
        self.full_redraw_action.triggered.connect(
            lambda: self._dock_hub.run_forest_full_redraw())
        self.trees_menu.addAction(self.full_redraw_action)
        self.trees_menu.addSeparator()
        # "Instances..." (2026-09-02, plan tree_instances P3): modal dialog
        # editing one template tree's `tree_instances:` declarations ({name,
        # sheet} rows) — the dialog only edits the short declarations, the
        # materialized instances regenerate on the next load.
        self.tree_instances_action = QAction(_("Instances..."), self)
        self.tree_instances_action.triggered.connect(
            lambda: self._dock_hub.open_instances_dialog())
        self.trees_menu.addAction(self.tree_instances_action)
        # "Instantiate from Cell..." (2026-09-03, plan instantiate_from_entity):
        # add ONE new group into the CURRENT tree by reusing an EXISTING Cell
        # as its internal layout (new Entity + placement node, staged until
        # Save) — the sibling of "Extract cluster...", but tree-bound.
        self.instantiate_cell_action = QAction(_("Instantiate from Cell..."), self)
        self.instantiate_cell_action.triggered.connect(
            lambda: self._dock_hub.instantiate_from_cell())
        self.trees_menu.addAction(self.instantiate_cell_action)

        # ── "Config" submenu (2026-09-04, plan plan_2026_09_04_celldock_to_
        #    dialog.md) ───────────────────────────────────────────────────
        # A future home for Config-related Tools actions — the first (and for
        # now only) entry is "Edit Cell...", which opens the standalone
        # (non-modal) Cell dialog hosting the single live CellDock (the Cell
        # form moved out of the Detail dock's Cells tab into a dialog — the
        # same move as Points/Tools/Thermal via/Chain before it; see
        # gui/dock_hub.py).
        self.config_menu = tools_menu.addMenu(_("Config"))
        self.edit_cell_action = QAction(_("Edit Cell..."), self)
        self.edit_cell_action.triggered.connect(
            lambda: self._dock_hub.edit_cell())
        self.config_menu.addAction(self.edit_cell_action)

        # ── Tools root (non-tree entries) ────────────────────────────────
        # "Place thermal vias..." (2026-09-01, plan
        # plan_2026_09_01_thermal_via_dialog.md): opens the standalone
        # (non-modal) Thermal via dialog with a fresh blank form — same shape
        # as the Config tree context menu's "Add thermal via pad...".
        self.place_thermal_vias_action = QAction(_("Place thermal vias..."), self)
        self.place_thermal_vias_action.triggered.connect(
            lambda: self._dock_hub.place_thermal_vias())
        tools_menu.addAction(self.place_thermal_vias_action)
        # "Add point..." (2026-09-01, plan plan_2026_09_01_points_dialog.md):
        # opens the standalone (non-modal) Points dialog with a fresh blank
        # form — same shape as the Config tree context menu's "Add point...".
        self.add_point_action = QAction(_("Add point..."), self)
        self.add_point_action.triggered.connect(
            lambda: self._dock_hub.new_point())
        tools_menu.addAction(self.add_point_action)
        # Chains (2026-09-01, plan rules_to_chains): "Add net..." opens the
        # (non-modal) Chain dialog in chain mode with a fresh blank form (same
        # shape as "Add point..." -> new_point). The menu labels follow the
        # NET-identity convention Denis chose (net identifies a chain for
        # --only); "Add spoke..." adds a pad to the currently selected chain
        # in the Config tree (pad mode); "Delete net..." removes the selected
        # chain from its file (with the usual timestamped backup).
        self.add_chain_action = QAction(_("Add net..."), self)
        self.add_chain_action.triggered.connect(
            lambda: self._dock_hub.add_chain())
        tools_menu.addAction(self.add_chain_action)
        self.add_spoke_action = QAction(_("Add spoke..."), self)
        self.add_spoke_action.triggered.connect(
            lambda: self._dock_hub.add_spoke())
        tools_menu.addAction(self.add_spoke_action)
        self.delete_chain_action = QAction(_("Delete net..."), self)
        self.delete_chain_action.triggered.connect(
            lambda: self._dock_hub.delete_selected_chain())
        tools_menu.addAction(self.delete_chain_action)
        # "Edit template..." (2026-09-01, plan plan_2026_09_01_tools_dialog_and_
        # entity_roles.md): opens the standalone (non-modal) Tools dialog — the
        # picked Entity's electrical fields (Nets/Net overrides/Refs), same
        # shape as the Config tree double click on an Entities leaf.
        self.edit_template_action = QAction(_("Edit template..."), self)
        self.edit_template_action.triggered.connect(
            lambda: self._dock_hub.edit_template())
        tools_menu.addAction(self.edit_template_action)
        # "Settings..." (2026-09-01, plan project_settings_dialogs): opens the
        # MODAL Settings dialog — a two-pane browser (category tree on the
        # left, settings pages on the right) with OK/Apply/Cancel.
        self.settings_action = QAction(_("Settings..."), self)
        self.settings_action.triggered.connect(
            lambda: self._dock_hub.open_settings_dialog())
        tools_menu.addAction(self.settings_action)

        # View menu (2026-08-27, handoff sync_skip_message_and_view_menu): the
        # app had no menu bar at all, so a closed dock had no way back short of
        # restarting. Every QDockWidget ships a ready-made toggleViewAction()
        # (checkable, self-tracks shown/hidden) — wire one per real top-level
        # dock (DockHub.docks), see gui/dock_hub.py for why DetailDock's
        # internal panels are excluded.
        view_menu = self.menuBar().addMenu(_("View"))
        for dock in self._dock_hub.docks:
            view_menu.addAction(dock.toggleViewAction())

        # One persistent worker thread for both poll ticks (see
        # PollWorkerHandle's docstring for why this must NOT be a fresh
        # QThread per tick like start_long_op — GIL/Qt-mutex deadlock found
        # live 2026-08-07, handoff_2026_08_07_worker_thread_gil_deadlock.md).
        # Stopped on app quit — QApplication.quit() (tray's _quit) bypasses
        # closeEvent entirely, so aboutToQuit is the one hook both paths
        # share.
        self._poll_worker = PollWorkerHandle(self)
        QApplication.instance().aboutToQuit.connect(self._poll_worker.stop)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_INTERVAL_MS)

        self._selection_timer = QTimer(self)
        self._selection_timer.timeout.connect(self._poll_board_selection)
        self._selection_timer.start(SELECTION_POLL_INTERVAL_MS)
        # Last (raw-selection, snapshot-version) tuple the fast tick acted
        # on — lets it early-exit when nothing changed (see
        # _poll_board_selection). None until the first successful tick.
        self._last_selection_signature = None

        self._restore_window_state()

        # No synchronous startup connect (2026-08-03 fix, second half): a
        # direct call here used to hang the constructor itself for up to the
        # socket's full recv timeout whenever the socket/KiCad was in a bad
        # state at launch (three real launches killed instantly, no log line
        # ever printed — construction never returned). The timer started
        # above fires its own first tick after POLL_INTERVAL_MS, already
        # through the background path below — a one-time ~2s wait before the
        # first connection attempt, traded deliberately for a constructor
        # that can never block regardless of KiCad's state.

    # Docks are owned by DockHub — these forwarding properties keep the
    # public surface working (and RoleClusterTreeDock's lazy fieldstool
    # lookup at gui/docks/role_cluster_tree.py:230) without MainWindow
    # owning the docks itself.

    @property
    def tree_dock(self):
        return self._dock_hub.tree_dock

    @property
    def config_tree_dock(self):
        return self._dock_hub.config_tree_dock

    @property
    def fieldstool_dock(self):
        return self._dock_hub.fieldstool_dock

    @property
    def placer_dock(self):
        return self._dock_hub.placer_dock

    @property
    def root_metadata_dock(self):
        return self._dock_hub.root_metadata_dock

    @property
    def thermal_via_dock(self):
        return self._dock_hub.thermal_via_dock

    @property
    def points_dock(self):
        return self._dock_hub.points_dock

    @property
    def chain_dock(self):
        return self._dock_hub.chain_dock

    @property
    def rules_dock(self):
        # Backward-compat alias for the 2026-09-01 Rule -> Chain rename.
        return self._dock_hub.chain_dock

    @property
    def log_dock(self):
        return self._dock_hub.log_dock

    @property
    def pending_dock(self):
        return self._dock_hub.pending_dock

    def _restore_window_state(self) -> None:
        """Plain x/y/width/height ints in gui_state.json, not Qt's own
        saveGeometry()/restoreGeometry() (a QByteArray blob — would need
        base64 to fit in JSON at all) or QSettings — same reason the rest of
        this GUI's persistence is plain JSON: staying human-readable/
        inspectable in one place beats using the platform-native mechanism
        for just this one thing.

        Dock/splitter/tab/floating layout (2026-08-27) is the deliberate
        exception — see _persist_settings's comment on "dock_state" below.
        Must run AFTER DockHub has added every dock (already true: this
        method is called at the end of __init__, well after
        self._dock_hub = DockHub(...) — restoreState() silently no-ops on a
        dock that doesn't exist yet)."""
        geometry = settings.state.get("window_geometry")
        if geometry and all(k in geometry for k in ("x", "y", "width", "height")):
            self.setGeometry(geometry["x"], geometry["y"], geometry["width"], geometry["height"])

        # Dock/splitter/tab/floating layout (2026-08-27). A missing key
        # (first run) or a corrupt/undecodable value (hand-edited file, a
        # version bump, a future binary-format change) must NEVER prevent the
        # window from opening — log and fall through to DockHub's own default
        # layout, same "never let saved state crash startup" discipline as
        # window_geometry's defensive `if geometry and all(...)` above.
        raw = settings.state.get("dock_state")
        if raw:
            try:
                blob = QByteArray(base64.b64decode(raw))
            except (ValueError, TypeError) as e:
                logger.warning("Failed to decode saved dock_state, ignoring: %s", e)
            else:
                if not self.restoreState(blob, _DOCK_STATE_VERSION):
                    logger.info("Saved dock layout did not apply (version/dock-set "
                                "mismatch) — using the default layout")

        # Always-on-top / tray (2026-09-01, plan project_settings_dialogs):
        # the Settings browser's checkboxes no longer flip the window flag /
        # tray icon on a toggle (settings now apply explicitly via the modal
        # dialog's OK/Apply — see gui/docks/configurator.py), so the persisted
        # flags are applied HERE at startup directly. setChecked keeps the
        # browser's widgets in sync (they are seeded from settings.state anyway
        # — see ConfiguratorDock.reload_from_state). DockHub is constructed
        # before this method runs, so configurator_dock is guaranteed to exist.
        if settings.state.get("always_on_top"):
            self._dock_hub.configurator_dock.always_on_top_checkbox.setChecked(True)
            self._set_always_on_top(True)
        if settings.state.get("tray_enabled"):
            self._dock_hub.configurator_dock.tray_checkbox.setChecked(True)
            self._set_tray_enabled(True)

    def _persist_settings(self) -> None:
        rect = self.geometry()
        settings.state.set("window_geometry", {"x": rect.x(), "y": rect.y(),
                                               "width": rect.width(), "height": rect.height()})
        # Dock/splitter/tab/floating layout (2026-08-27) — Qt's own
        # saveState(), base64 into the SAME plain-JSON gui_state.json. A
        # DELIBERATE exception to this file's "everything human-readable"
        # principle (see _restore_window_state's docstring on
        # window_geometry): saveState()'s payload is a rich, versioned Qt
        # blob covering dock positions, splitter ratios, tab order and
        # floating state — hand-rolling that into plain ints the way
        # window_geometry does would be a lot of fragile bespoke code for
        # something nobody hand-edits anyway (unlike window x/y, which people
        # DO sometimes fix by hand to rescue an off-screen window).
        dock_state = bytes(self.saveState(_DOCK_STATE_VERSION))
        settings.state.set("dock_state", base64.b64encode(dock_state).decode("ascii"))
        # Always-on-top / tray (2026-09-01, plan project_settings_dialogs):
        # the Settings dialog's apply() already persisted these keys (modal
        # OK/Apply — see gui/docks/configurator.py), and the browser's widgets
        # are re-seeded from state on every open/cancel — so on quit we just
        # re-assert the APPLIED state rather than reading possibly-unapplied
        # widget state.
        settings.state.set("always_on_top", bool(settings.state.get("always_on_top", False)))
        settings.state.set("tray_enabled", bool(settings.state.get("tray_enabled", False)))
        # Tree-dock UI state (2026-09-03, plan tree_ui_state_persistence P1/P2/P3):
        # the docks already persist their active-tab / expand / collapse state on
        # every user action and every rebuild; this final flush re-reads the
        # CURRENT widget state so an interaction after the last rebuild (a pure
        # tab switch or a manual expand/collapse, with no structural edit) is
        # still saved when the app quits.
        self._dock_hub.tree_dock.persist_ui_state()
        self._dock_hub.trees_dock.persist_ui_state()
        self._dock_hub.config_tree_dock.persist_ui_state()

    def closeEvent(self, event) -> None:
        """While the tray icon is enabled, the title-bar X hides instead of
        quitting — reachable again via the tray (see _set_tray_enabled/
        _toggle_visibility). Real quit only happens here when tray is off
        (today's original behavior, unchanged) or via the tray menu's Quit
        action, which bypasses this entirely (see _quit)."""
        if settings.state.get("tray_enabled", False):
            event.ignore()
            self.hide()
            return
        self._persist_settings()
        super().closeEvent(event)

    def _set_always_on_top(self, checked: bool) -> None:
        """setWindowFlag() only takes effect on the next show() — the window
        briefly disappears and reappears on most platforms (X11/Windows),
        which is the normal/expected way Qt does this, not a bug here."""
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()

    # ── Tray icon ────────────────────────────────────────────────────────

    def _set_tray_enabled(self, checked: bool) -> None:
        if checked:
            if self._tray_icon is not None:
                return
            if not QSystemTrayIcon.isSystemTrayAvailable():
                logger.warning(_("Tray icon requested but no system tray is available here."))
            self._tray_icon = QSystemTrayIcon(build_app_icon(), self)
            self._tray_icon.setToolTip(_("KiCadStamp"))
            menu = QMenu()
            menu.addAction(_("Show/Hide"), self._toggle_visibility)
            menu.addAction(_("Open fieldstool"), self.open_fieldstool)
            menu.addSeparator()
            menu.addAction(_("Quit"), self._quit)
            self._tray_icon.setContextMenu(menu)
            self._tray_icon.activated.connect(self._on_tray_activated)
            self._tray_icon.show()
        else:
            if self._tray_icon is not None:
                self._tray_icon.hide()
                self._tray_icon = None

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._toggle_visibility()

    def _toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.bring_to_front()

    def bring_to_front(self) -> None:
        """Un-hides/raises this window — called from the tray's Show/Hide
        action, and from SingleInstanceGuard.activation_requested when a
        second launch attempt pings this already-running instance
        (see kicadstamp_gui.py)."""
        self.show()
        self.raise_()
        self.activateWindow()

    def open_fieldstool(self) -> None:
        """Un-hides the main window if tray-hidden, and shows/raises the
        Components dock (which hosts the embedded fieldstool pane on the right
        of its splitter) even if another left-group tab is active or the dock
        was individually closed — used by both the tray menu and the status-bar
        button."""
        self.bring_to_front()
        self._dock_hub.open_fieldstool()

    def _show_kicad_processes(self) -> None:
        """Status-bar button — opens the manual KiCad-process picker (see
        gui/kicad_processes_dialog.py's module docstring for why this is a
        picker, never an automated kill)."""
        KicadProcessesDialog(self).exec()

    # ── Global save model (2026-09-01, plan project_save_model) ──────────

    def _save_project(self) -> None:
        """File > Save (Ctrl+S) — commit the whole staged working set to disk.
        The flush validates the staged graph BEFORE writing anything (see
        kicadstamp/config_working_set.py); on a cross-file inconsistency
        nothing is written and the errors are shown."""
        root = self.root_metadata_dock.root_path
        if root is None:
            self.status_label.setText(_("No project open — nothing to save"))
            return
        if not WORKING_SET.is_dirty():
            self.status_label.setText(_("Nothing to save"))
            return
        errors = WORKING_SET.flush(root)
        self._update_dirty_indicator()
        if errors:
            QMessageBox.warning(self, _("Save"),
                                _("Save failed — nothing was written:\n{errors}")
                                .format(errors="\n".join(errors)))
            return
        self.status_label.setText(_("Project saved"))

    def _discard_project(self) -> None:
        """File > Discard unsaved changes... — drop the working set and reload
        every dock from disk. Deliberately does NOT roll back Redraw steps that
        were already applied to the board (stated in the confirm dialog)."""
        if not WORKING_SET.is_dirty():
            return
        reply = QMessageBox.question(
            self, _("Discard unsaved changes"),
            _("Discard will revert unsaved CONFIG edits, but NOT Redraw steps "
              "already applied to the board. Continue?"),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if reply == QMessageBox.StandardButton.Save:
            self._save_project()
            return
        if reply != QMessageBox.StandardButton.Discard:
            return
        WORKING_SET.clear()
        self._dock_hub.reload_project_from_disk()
        self._update_dirty_indicator()
        self.status_label.setText(_("Unsaved config changes discarded"))

    def _update_dirty_indicator(self) -> None:
        """Reflect the working set's dirty state: a ● in the status bar and a ●
        prefix on the File > Save item. Driven by DockHub on every stage/clear
        (and by the Save/Discard handlers above)."""
        dirty = WORKING_SET.is_dirty()
        self.dirty_label.setText("●" if dirty else "")
        self.dirty_label.setToolTip(
            _("Project has unsaved config changes — press Ctrl+S to save")
            if dirty else "")
        save_action = getattr(self, "save_action", None)
        if save_action is not None:
            save_action.setText(_("● &Save") if dirty else _("&Save"))

    def _quit(self) -> None:
        """Tray menu's Quit — a real quit regardless of the tray checkbox.
        QApplication.quit() doesn't invoke closeEvent on any window (it just
        stops the event loop), so this deliberately bypasses self.close()/
        closeEvent entirely rather than needing a "really quit" flag."""
        self._persist_settings()
        QApplication.instance().quit()

    def request_refresh(self) -> None:
        """Public — lets a dock trigger an out-of-cycle refresh right after
        its own live board write (Stage in fieldstool, Clear all/Delete
        selected in the Components tree) instead of waiting for the user to
        notice nothing updated and click Refresh themselves. The automatic
        timer tick deliberately never refreshes once already connected (see
        _poll's docstring), so without this call Pending changes' diff would
        never pick up a write that just happened (found live 2026-08-03:
        Stage wrote Role/Cluster to the board, but Pending changes stayed
        empty until a manual Refresh). Same path as the status-bar button."""
        self._poll(manual=True)

    def _poll(self, manual: bool = False) -> None:
        """manual=True (button click) always does real work. manual=False (an
        automatic timer tick) only tries to connect while disconnected — see
        module docstring for why an already-connected idle tick is a
        deliberate no-op. Collect/decide here on the UI thread; the actual
        IPC (connect()/refresh()) runs on the persistent poll worker thread
        (see module docstring for why) — this method itself never blocks."""
        # A long op (Extract/Redraw) or another still-running poll tick holds
        # the shared socket; connecting/refreshing now would interleave a
        # second request into its in-flight REQ transaction. Skip silently —
        # no queueing, the next tick tries again.
        if self.connection.long_op_active:
            return
        if self.connection.is_connected and not manual:
            return
        self._poll_worker.submit(
            self.connection, self._run_poll, (manual,), self._finish_poll, self._on_poll_failed)

    def _run_poll(self, manual: bool) -> dict:
        """Worker thread: connection IPC only — never touches a widget."""
        if self.connection.is_connected:
            error = self.connection.refresh()
        else:
            error = self.connection.connect()
        return {"error": error}

    def _finish_poll(self, result: dict) -> None:
        """UI thread: reflect the worker's result into widgets."""
        error = result["error"]
        if error:
            self.status_label.setText(_("Not connected: {error}").format(error=error))
            self._dock_hub.clear_components()
        else:
            # connect()/refresh() already rebuilt BoardConnection.snapshot —
            # this is the ONE place that snapshot is consumed, so the docks
            # never call board.select() themselves (PlacerDock's
            # refresh_known_roles used to build a second full snapshot here;
            # the fast selection-watch tick used to build one every 400ms).
            snapshot = self.connection.snapshot
            self.status_label.setText(_("Connected — {count} components").format(count=len(snapshot)))
            self._dock_hub.push_snapshot(snapshot, self.connection.board)
            self._dock_hub.push_fieldstool_snapshot(snapshot)

        # Phase 5.1 — the embedded fieldstool shares this connection and no
        # longer runs its own connect/refresh poll, so mirror the status we
        # just computed into its label instead of letting it go stale.
        self._dock_hub.push_fieldstool_connection_status(error)

        self.action_button.setText(_("Refresh") if self.connection.is_connected else _("Reconnect"))

    def _on_poll_failed(self, message: str) -> None:
        """Safety net — _run_poll never raises (connect()/refresh() catch
        their own exceptions and return an error string instead), so this
        should not normally fire."""
        logger.error("Unexpected failure in connection-poll worker: %s", message)

    def _poll_board_selection(self) -> None:
        """The fast timer's tick — see module docstring. Collect/decide here
        on the UI thread; get_selected_items() runs on the persistent poll
        worker thread, same reasoning as _poll()."""
        # A long op (Extract/Redraw) or another still-running poll tick holds
        # the shared socket; get_selected_items() here would interleave into
        # its in-flight REQ.
        if self.connection.long_op_active:
            return
        if not self.connection.is_connected:
            return
        self._poll_worker.submit(
            self.connection, self._run_poll_selection, (), self._finish_poll_selection,
            self._on_poll_selection_failed)

    def _run_poll_selection(self) -> dict:
        """Worker thread: board IPC only — never touches a widget. Failure
        here (most likely: KiCad closed between two _poll() ticks, since that
        one only re-verifies the connection every POLL_INTERVAL_MS) drops the
        connection immediately rather than waiting for the slower timer to
        notice — connection.disconnect() (which also closes the underlying
        kipy socket, see its docstring) is a plain attribute write plus a
        socket close, neither of which is a widget touch, so it is safe here
        on the worker thread; the socket being closed is this very same
        thread's own, already broken (that's why we're in this except
        clause) — no cross-thread concern."""
        try:
            items = self.connection.board.adapter.get_selected_items()
        except Exception as e:
            self.connection.disconnect()
            return {"error": str(e)}
        return {"error": None, "items": items}

    def _finish_poll_selection(self, result: dict) -> None:
        """UI thread: reflect the worker's result into widgets/docks. Does
        not touch the tree's component list itself — only its live-selection
        highlighting; the slower _poll() owns the component list."""
        if result["error"]:
            logger.warning("Lost connection while polling board selection: %s", result["error"])
            self.status_label.setText(_("Not connected: {error}").format(error=result["error"]))
            self.action_button.setText(_("Reconnect"))
            return
        items = result["items"]
        refs = {item.ref for item in items
                if isinstance(item, Footprint)}

        # Early-exit guard (same idea as RoleClusterTreeDock.
        # highlight_board_selection()'s own): if neither the raw selection
        # nor the cached snapshot changed since the last tick, there is
        # nothing to repaint — skip both the tree highlight (which would
        # self-bail anyway) and DockHub.set_board_selection(). The raw
        # selection matters as much as the footprint refs (vias/tracks drive
        # the inter-cluster net detection), so the signature covers the whole
        # get_selected_items() list, not just refs.
        signature = (refs, selection_signature(items),
                     self.connection.snapshot_version)
        if signature == self._last_selection_signature:
            return
        self._last_selection_signature = signature

        self._dock_hub.highlight_selection(refs)
        by_ref = {s.ref: s for s in self.connection.snapshot}
        selected = [by_ref[ref] for ref in refs if ref in by_ref]
        self._dock_hub.set_board_selection(items, selected)
        # Phase 5.1 — the embedded fieldstool's live-selection cross-probe is
        # fed from this single tick too (its own 400ms timer is stopped when
        # it shares this connection).
        self._dock_hub.push_fieldstool_selection(refs)

    def _on_poll_selection_failed(self, message: str) -> None:
        """Safety net — _run_poll_selection catches its own exceptions and
        returns them as a result dict, so this should not normally fire."""
        logger.error("Unexpected failure in selection-poll worker: %s", message)

