# tests/gui/test_status_bar.py
"""gui/status_bar.py — the status bar's widgets, built by ONE function.

Split out of gui/main_window.py on the acceptance of plan_2026_10_14_connection_badge
(the window had to come back under its line gate). These cells pin the SEAM, not the
widgets' behaviour: the widgets' own cells live where they belong — the badge's in
tests/gui/test_connection_badge.py, the busy/latency ones in tests/gui/test_worker.py
and tests/gui/test_ipc_timeout_and_latency.py, the dirty indicator's in the dock tests.
What a move like this CAN silently break is the host contract (four callables) and the
ATTRIBUTE NAMES the window and several docks read by name — that is what is checked here.

The host is a throwaway QMainWindow recording the four callables, so a cell can prove
the buttons really reach them. The busy reporter is read through gui.worker's own hook
and monkeypatch restores it at teardown: it is a PROCESS-GLOBAL by design (the same
shape the real MainWindow installs), and a stub left behind would be a dead reporter
for every later cell in the session.
"""
from PyQt6.QtWidgets import QLabel, QMainWindow, QPushButton

from gui import worker as worker_mod
from gui.connection_badge import STATE_DISCONNECTED, ConnectionBadge
from gui.status_bar import build_status_bar

#: Every widget the split must keep on the window: DockHub's dirty indicator, the
#: poll handlers (badge, buttons), the tray/close paths and the docks all read them
#: by name, so the names ARE the contract.
ATTRIBUTES = ("status_label", "action_button", "connection_badge", "dirty_label",
              "busy_label", "latency_label", "open_fieldstool_button",
              "kicad_processes_button")


class _Host(QMainWindow):
    """A QMainWindow with exactly the four callables gui/status_bar.py documents —
    the whole of its contract, nothing else."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.busy = []

    def _poll(self, manual=False):
        self.calls.append(("poll", manual))

    def open_fieldstool(self):
        self.calls.append(("fieldstool", None))

    def _show_kicad_processes(self):
        self.calls.append(("processes", None))

    def _set_busy(self, what):
        self.busy.append(what)


def _built(qapp, monkeypatch):
    monkeypatch.setattr(worker_mod, "_busy_reporter", None)
    host = _Host()
    build_status_bar(host)
    return host


def test_it_creates_every_widget_the_window_reads_by_name(qapp, monkeypatch):
    host = _built(qapp, monkeypatch)

    assert isinstance(host.status_label, QLabel) and host.status_label.text() == ""
    assert isinstance(host.action_button, QPushButton)
    assert host.action_button.text() == "Reconnect"
    assert isinstance(host.connection_badge, ConnectionBadge)
    assert host.connection_badge.state == STATE_DISCONNECTED
    for name in ("dirty_label", "busy_label", "latency_label"):
        label = getattr(host, name)
        assert isinstance(label, QLabel), f"{name} must be a QLabel"
        assert label.text() == "", f"{name} starts empty"


def test_the_three_buttons_reach_the_host(qapp, monkeypatch):
    """The buttons are this module's only reason to know the host at all — a typo
    in one connect() would otherwise leave a dead button in the status bar."""
    host = _built(qapp, monkeypatch)

    host.open_fieldstool_button.click()
    host.kicad_processes_button.click()
    host.action_button.click()

    assert host.calls == [("fieldstool", None), ("processes", None), ("poll", True)]


def test_the_message_label_is_the_stretchable_area_and_the_badge_is_permanent(
        qapp, monkeypatch):
    """The one-off message area and the permanent widgets are two different halves
    of a QStatusBar: a temporary message may replace the former and must never
    touch the latter (that is the whole point of the badge)."""
    host = _built(qapp, monkeypatch)

    assert host.status_label.parent() is host.statusBar()
    host.statusBar().showMessage("A temporary status message")

    assert host.statusBar().currentMessage() == "A temporary status message"
    assert host.status_label.text() == ""
    assert host.connection_badge.text() == "Not connected"
    assert host.connection_badge.x() < host.dirty_label.x(), \
        "the badge is FIRST of the permanent widgets"


def test_the_busy_reporter_is_installed_on_the_host(qapp, monkeypatch):
    """The busy label is fed by gui/worker's hook, never by the label itself — the
    install is part of the split, so it is pinned here."""
    host = _built(qapp, monkeypatch)

    assert worker_mod._busy_reporter == host._set_busy
    worker_mod._busy_reporter("PLACEHOLDER")
    assert host.busy == ["PLACEHOLDER"]


def test_the_real_window_gets_all_of_them(real_main_window):
    """Against the REAL MainWindow: every attribute the window and the docks read by
    name still exists after the move."""
    missing = [name for name in ATTRIBUTES if not hasattr(real_main_window, name)]
    assert missing == [], (
        "the status-bar split dropped attribute(s) other code reads by name: "
        + ", ".join(missing))
