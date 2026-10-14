# gui/status_bar.py
"""The MainWindow status bar: its widgets, built by ONE function.

Split out of gui/main_window.py on the acceptance of plan_2026_10_14_connection_
badge (2026-10-14, the follow-up that put the window back under its line gate):
gui/main_window.py is a giant that must only shrink, and the status bar is a
self-contained cluster — eight widgets, three of them buttons that call back into
the window. The CALL stays in MainWindow.__init__; the comments travel with the
code they explain (Denis's rule for the split), because a widget's "why" is worth
nothing at a distance from the widget.

THE HOST'S CONTRACT is deliberately tiny: four callables, all of them already
methods on MainWindow —
  * ``_poll(manual=...)``       — the Reconnect/Refresh button, and nothing else;
  * ``open_fieldstool()``       — the "Open fieldstool" button;
  * ``_show_kicad_processes()`` — the "KiCad processes..." button;
  * ``_set_busy(what)``         — the busy reporter, installed through
                                  gui.worker.set_busy_reporter by this function.
For a QStatusBar to exist the host must be a QMainWindow (or a stand-in with one).

Every widget lands as an ATTRIBUTE of the host — ``status_label``,
``action_button``, ``connection_badge``, ``dirty_label``, ``busy_label``,
``latency_label``, ``open_fieldstool_button``, ``kicad_processes_button`` —
because the window and several docks read them BY NAME (DockHub's dirty
indicator, the badge's own guards, the tray/close paths). The split changed WHERE
they are built, never what they are called.
"""
from PyQt6.QtWidgets import QLabel, QPushButton

from kicadstamp.i18n import _

from .connection_badge import ConnectionBadge
from .worker import set_busy_reporter


def build_status_bar(window) -> None:
    """Create the status bar's widgets on *window* (see the module docstring for
    the host's contract). Called exactly once, from MainWindow.__init__ — the
    widgets are long-lived for the whole session, so nothing here is idempotent
    and nothing here has to be."""
    # One-off messages ONLY since 2026-10-14 (plan_2026_10_14_connection_badge):
    # the connection state lives in the badge below, because a save used to erase
    # it from right here (Д1 of that plan).
    window.status_label = QLabel("")
    window.action_button = QPushButton(_("Reconnect"))
    window.action_button.clicked.connect(lambda: window._poll(manual=True))
    window.statusBar().addWidget(window.status_label, 1)

    # The connection badge (same plan) — FIRST of the permanent widgets, fed by
    # the two poll finish handlers; see gui/connection_badge.py.
    window.connection_badge = ConnectionBadge(window)
    window.statusBar().addPermanentWidget(window.connection_badge)

    # Project-dirty indicator (2026-09-01, plan project_save_model) — a ●
    # whenever the config working set has unsaved edits. Driven by
    # DockHub._update_dirty_indicator on every stage/clear.
    window.dirty_label = QLabel("")
    window.statusBar().addPermanentWidget(window.dirty_label)

    # Busy indicator (2026-09-12, plan_2026_09_12_busy_indicator Э1) — a
    # permanent status-bar label naming the operation the user started
    # ("Working with the board: placing"), empty when nothing runs. Same
    # shape as dirty_label above: one long-lived widget, written from one
    # place (_set_busy). The writer is gui/worker's busy-reporter hook, so
    # ONLY LongOpController operations reach it — the automatic poll ticks
    # go through PollWorkerHandle, which deliberately has no reporter (see
    # the hook's own comment in gui/worker.py).
    window.busy_label = QLabel("")
    window.statusBar().addPermanentWidget(window.busy_label)
    set_busy_reporter(window._set_busy)

    # Latency readout (plan_2026_09_13_ipc_timeout_and_latency Э3) — the
    # MEDIAN response time of the fast selection tick, e.g. "KiCad: 1.2 ms".
    # Same long-lived-widget shape as busy_label: one widget, written from
    # one place (_update_latency_label). Empty while there is no connection
    # — and NOT keyed on long_op_active, which the 400ms tick raises too.
    window.latency_label = QLabel("")
    window.statusBar().addPermanentWidget(window.latency_label)

    # Always on top / Tray icon checkboxes moved to the Settings tab
    # (ConfiguratorDock) 2026-08-15 — see gui/docks/configurator.py. The
    # actual window-flag/tray-icon LOGIC stays in MainWindow
    # (_set_always_on_top/_set_tray_enabled); only the UI moved. DockHub wires
    # the configurator's always_on_top_toggled/tray_enabled_toggled signals
    # back onto those two methods (see gui/dock_hub.py), and
    # _restore_window_state/_persist_settings read the checkboxes through
    # main_window._dock_hub.configurator_dock.

    window.open_fieldstool_button = QPushButton(_("Open fieldstool"))
    window.open_fieldstool_button.clicked.connect(window.open_fieldstool)
    window.statusBar().addPermanentWidget(window.open_fieldstool_button)

    # 2026-08-03 — a crashed/frozen kicad.exe left running alongside a
    # fresh one blocked the fresh one's IPC connection; this is a
    # shortcut for "look in Task Manager, pick the stuck one, kill it by
    # hand", not an automated decision (see gui/kicad_processes_dialog.py).
    window.kicad_processes_button = QPushButton(_("KiCad processes..."))
    window.kicad_processes_button.clicked.connect(window._show_kicad_processes)
    window.statusBar().addPermanentWidget(window.kicad_processes_button)

    window.statusBar().addPermanentWidget(window.action_button)
