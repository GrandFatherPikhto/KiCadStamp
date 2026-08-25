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
to solve correctly (direct `.kicad_sch` edits). FieldsToolDock (see
gui/docks/fieldstool_dock.py) now occupies that first right-hand tab
instead, embedding fieldstool's own standalone MainWindow whole. Then
ConfigTreeDock (pick a Root file, browse/edit its include: graph — folded
FilePickerDock's job into it 2026-08-03, see gui/docks/config_tree.py)
and ExtractDock (build a Cell from the current selection, write it into
whatever file is currently selected in the Config tree). kipy 0.7.1's
Board has no selection/board-change push events (checked directly against
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
snapshot changed since the last tick, so ExtractDock's per-selection widget
rebuilds (aliases, origin combos, button state) aren't churned for nothing.
"""
import logging
from typing import Optional

from kicadstamp.domain.board import Footprint
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow, QMenu,
                              QPushButton, QSystemTrayIcon)

from kicadstamp.explore import selection_signature
from kicadstamp.i18n import _

from . import settings
from .connection import BoardConnection
from .dock_hub import DockHub
from .app_icon import build_app_icon
from .kicad_processes_dialog import KicadProcessesDialog
from .worker import PollWorkerHandle

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 2000
SELECTION_POLL_INTERVAL_MS = 400


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
    def extract_dock(self):
        return self._dock_hub.extract_dock

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
    def rules_dock(self):
        return self._dock_hub.rules_dock

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
        for just this one thing."""
        geometry = settings.state.get("window_geometry")
        if geometry and all(k in geometry for k in ("x", "y", "width", "height")):
            self.setGeometry(geometry["x"], geometry["y"], geometry["width"], geometry["height"])
        # The checkboxes now live in the Settings tab (ConfiguratorDock,
        # moved here 2026-08-15) — setChecked triggers its
        # always_on_top_toggled/tray_enabled_toggled signals, which DockHub
        # wires to _set_always_on_top/_set_tray_enabled (gui/dock_hub.py).
        # DockHub is constructed before this method runs (line ~137 vs
        # here), so configurator_dock is guaranteed to exist.
        if settings.state.get("always_on_top"):
            self._dock_hub.configurator_dock.always_on_top_checkbox.setChecked(True)
        if settings.state.get("tray_enabled"):
            self._dock_hub.configurator_dock.tray_checkbox.setChecked(True)

    def _persist_settings(self) -> None:
        rect = self.geometry()
        settings.state.set("window_geometry", {"x": rect.x(), "y": rect.y(),
                                               "width": rect.width(), "height": rect.height()})
        # Checkboxes moved to the Settings tab 2026-08-15 — read their
        # state back through the ConfiguratorDock (see gui/docks/
        # configurator.py).
        configurator = self._dock_hub.configurator_dock
        settings.state.set("always_on_top", configurator.always_on_top_checkbox.isChecked())
        settings.state.set("tray_enabled", configurator.tray_checkbox.isChecked())

    def closeEvent(self, event) -> None:
        """While the tray icon is enabled, the title-bar X hides instead of
        quitting — reachable again via the tray (see _set_tray_enabled/
        _toggle_visibility). Real quit only happens here when tray is off
        (today's original behavior, unchanged) or via the tray menu's Quit
        action, which bypasses this entirely (see _quit)."""
        if self._dock_hub.configurator_dock.tray_checkbox.isChecked():
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
        """Un-hides the main window if tray-hidden, and brings the
        fieldstool tab to front even if another right-hand tab is active or
        the dock was individually closed — used by both the tray menu and
        the status-bar button."""
        self.bring_to_front()
        self._dock_hub.open_fieldstool()

    def _show_kicad_processes(self) -> None:
        """Status-bar button — opens the manual KiCad-process picker (see
        gui/kicad_processes_dialog.py's module docstring for why this is a
        picker, never an automated kill)."""
        KicadProcessesDialog(self).exec()

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
        # self-bail anyway) and ExtractDock.set_board_selection(), which
        # rebuilds its alias/role widgets on every call. The raw selection
        # matters as much as the footprint refs (vias/tracks drive
        # ExtractDock's via-net origin combo), so the signature covers the
        # whole get_selected_items() list, not just refs.
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

