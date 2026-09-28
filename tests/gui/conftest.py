# tests/gui/conftest.py
"""
Shared fixtures for gui/ dock tests. These are the ad hoc smoke-test
scripts run by hand throughout 2026-08-01's GUI work, formalized here —
requested live: "Ты там тесты гонял по GUI. Может имеет смысл их закинуть
в tests/gui?"

Offscreen, no live KiCad connection needed (unlike tests/integration_tests,
which require a running KiCad + board — see its own conftest.py): these
exercise dock logic (autofill, persistence, validation) against synthetic
selections and throwaway files, the same way every dock was hand-verified
before being committed this session. QT_QPA_PLATFORM=offscreen is set here
(before PyQt6 is imported anywhere) so plain `pytest` works without the
caller needing to export it first.
"""
import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtWidgets import QApplication, QMainWindow

from kicadstamp.constants import DEFAULT_TIMEOUT_MS

from gui import fieldstool_window as fieldstool_window_mod
from gui import settings
from gui.docks.log_panel import LogDock
from gui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole test session — Qt only tolerates a
    single instance per process, so this must be session-scoped, not
    per-test."""
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump(qapp, until, timeout=5.0):
    """Pump the Qt event loop until `until()` is truthy or the deadline
    passes — needed for anything dispatched through gui/worker.py's
    start_long_op (MainWindow._poll/_poll_board_selection, RoleClusterTreeDock
    Clear all/Delete selected, PlacerDock Redraw, ...), since the actual
    work runs on a background QThread and its completion signal is only
    delivered while the UI-thread event loop is spinning.

    IMPORTANT: `until` should check `not connection.long_op_active`, not
    "the worker's side effect is already visible" — the side effect runs on
    the worker thread and may be visible before the completion signal has
    reached the UI thread and released the flag; pumping only until the
    side effect appears can return before _release() has called
    thread.quit(), and a later thread.wait() (teardown, or the next op's
    start()) then hangs forever waiting for a quit that was never issued
    while anything was pumping."""
    deadline = time.monotonic() + timeout
    while not until():
        if time.monotonic() > deadline:
            raise TimeoutError("timed out waiting for worker signal")
        qapp.processEvents()
        time.sleep(0.005)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Every gui.settings.load()/save() call in a test hits a throwaway
    file instead of the developer's real gui/gui_state.json — autouse so
    no test can forget it and accidentally pollute real GUI state. Also
    isolates gui.fieldstool_window's own settings file — constructing the
    real MainWindow (see real_main_window below) embeds a real fieldstool
    MainWindow via FieldsToolDock, which would otherwise touch the
    developer's real fieldstool_gui_state.json (e.g. restoring a real
    root_sheet path)."""
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "gui_state.json")
    monkeypatch.setattr(fieldstool_window_mod, "FIELDSTOOL_SETTINGS_PATH",
                        tmp_path / "fieldstool_gui_state.json")


@pytest.fixture(autouse=True)
def _capture_dock_logs(caplog):
    """Dock status messages live ONLY in the Log dock since 2026-08-13 (the
    inline message_label was removed from every dock), so dock tests assert on
    caplog instead of the old dock.message_label.text(). Captured at INFO by
    default — success/warning messages (the common case) sit below caplog's
    WARNING default and would otherwise be silently missed."""
    caplog.set_level(logging.INFO)


class _FakeConnection:
    def __init__(self):
        self.board = None
        # Phase 5.2 — held exclusively by a background long op (Extract/
        # Redraw, gui/worker.py); the main window's polling timers and the
        # fieldstool's _push_selection_to_board check it, so the fake needs
        # the same attribute the real BoardConnection exposes.
        self.long_op_active = False
        # Same shape as BoardConnection.timeout_ms — the Settings tab's
        # ConfiguratorDock writes its spinbox value straight into
        # connection.timeout_ms (2026-08-15, plan configurator_panel), so
        # the fake needs the attribute too.
        self.timeout_ms = DEFAULT_TIMEOUT_MS

    def disconnect(self) -> None:
        """Mirrors the real BoardConnection.disconnect() (2026-08-04) at the
        observable level a test cares about — board becomes None. The real
        one also closes the underlying kipy socket (see its docstring), an
        implementation detail this fake has no equivalent for and doesn't
        need to replicate."""
        self.board = None

    @property
    def is_connected(self) -> bool:
        # The embedded fieldstool window checks connection.is_connected in
        # _push_selection_to_board — a property (board is not None), same
        # shape as the real BoardConnection, so a test that sets .board
        # after construction sees it flip automatically.
        return self.board is not None


@pytest.fixture
def main_window(qapp):
    """A real QMainWindow (docks need a QWidget parent, not a plain
    object) with a stubbed .connection.board — enough for the docks under
    test here, which only ever check `connection.board is None` before
    touching the live board, and have that board's methods monkeypatched
    per-test where an actual write path needs exercising."""
    window = QMainWindow()
    window.connection = _FakeConnection()
    return window


@pytest.fixture
def fieldstool_window(qapp):
    """A real gui.fieldstool_window.MainWindow with a fake connection —
    exercises fieldstool's own staging/Apply logic standalone, without a
    live board or the embedding main GUI. Constructed the same way
    FieldsToolDock does (an injected connection is required, never
    optional — see gui/fieldstool_window.py)."""
    window = fieldstool_window_mod.MainWindow(connection=_FakeConnection())
    return window


@pytest.fixture
def real_main_window(qapp):
    """The real gui.main_window.MainWindow — unlike `main_window` above
    (a bare QMainWindow stub), this is needed for anything that exercises
    tray/closeEvent/single-instance/fieldstool-dock-embedding logic, all of
    which live on the real class. A tiny timeout_ms keeps construction fast
    (no live KiCad here — Board.connect() fails quickly and _poll() just
    records "not connected"). Stops this window's own two QTimers on
    teardown so a torn-down window doesn't keep polling (and writing
    MockLogRecords/touching connections) in the background across the rest
    of the test session — the embedded fieldstool MainWindow has none of
    its own to stop, it's driven entirely through this window's poll."""
    window = MainWindow(timeout_ms=10, verbose=False)
    yield window
    window._timer.stop()
    window._selection_timer.stop()
    # PollWorkerHandle's QThread is meant to outlive one MainWindow — it's
    # normally stopped via QApplication.aboutToQuit (see MainWindow.__init__),
    # which only fires once for the whole app's lifetime. Tests build many
    # MainWindows in the same QApplication session, so that never fires here;
    # without an explicit stop(), the still-running QThread gets torn down by
    # Qt's parent-child cascade whenever this window is garbage-collected —
    # "QThread: Destroyed while thread is still running" is fatal (found
    # live 2026-08-07 fixing this same class of bug, see
    # handoff_2026_08_07_worker_thread_gil_deadlock.md).
    window._poll_worker.stop()
    # Phase 4.3 — the embedded LogDock attaches its handler to the ROOT
    # logger; detach it so a session with several windows doesn't accumulate
    # handlers that keep every torn-down dock alive / keep logging forever.
    window.log_dock.remove_handler()
    # Same leak, same reasoning, for the root config's own log_file:
    # FileHandler (2026-08-06, see DockHub._on_root_file_changed_for_logging)
    # — also holds an open file handle a later tmp_path cleanup may need.
    log_file_handler = window._dock_hub._log_file_handler
    if log_file_handler is not None:
        logging.getLogger().removeHandler(log_file_handler)
        log_file_handler.close()
    if window._tray_icon is not None:
        window._tray_icon.hide()


@pytest.fixture
def log_dock(main_window):
    """LogDock attaches its handler to the ROOT logger and (since Phase
    4.3) detaches it via LogDock.remove_handler() when the dock is closed
    or destroyed — this fixture forces the root logger to DEBUG so
    handler-level filtering (INFO vs DEBUG) is actually what's being
    tested, matching what kicadstamp_gui.py's setup_logging() does for
    real (root logger at DEBUG, each handler filters independently) —
    see kicadstamp/logging_setup.py."""
    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.DEBUG)
    dock = LogDock(main_window, verbose=False)
    yield dock
    dock.remove_handler()
    root.setLevel(original_level)
