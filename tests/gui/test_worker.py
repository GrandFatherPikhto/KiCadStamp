# tests/gui/test_worker.py
"""
Phase 5.2 — worker-thread long ops (gui/worker.py).

These run a REAL QThread (LongOpController) and pin down the serialization
contract that keeps the kipy REQ socket single-in-flight while a long op is
off the UI thread:

  * connection.long_op_active is set BEFORE the worker thread starts and
    cleared AFTER it finishes (on the UI thread, via queued signals), so no
    polling tick can ever race the op's first socket request;
  * guard widgets are disabled for the duration and restored afterwards;
  * results/errors come back through signals on the UI thread;
  * while the flag is set, the main window's polling ticks skip their socket
    work (_poll / _poll_board_selection), so the op is the socket's only
    active owner for its whole lifetime.

The QThread tests deliberately avoid touching controller._thread after the
event loop has been pumped (its finished->deleteLater chain may already have
deleted the C++ object); the thread reference is captured right after
start() (before any pump) and only used for a final wait(), which is safe
because the completion handler has already run (posting quit()) by then.
"""
import logging
import threading
import time
from types import SimpleNamespace

import gui.worker as worker_mod
from gui.worker import LongOpController, start_long_op


class _Button:
    """Stand-in for the two QWidget methods LongOpController touches —
    the worker never needs a real widget."""

    def __init__(self):
        self._enabled = True

    def isEnabled(self):
        return self._enabled

    def setEnabled(self, enabled):
        self._enabled = enabled


def _pump(qapp, until, timeout=5.0):
    """Pump the Qt event loop until `until()` is truthy or the deadline
    passes. Completion signals from the worker thread are queued to the UI
    thread, so they only fire while this pump is running."""
    deadline = time.monotonic() + timeout
    while not until():
        if time.monotonic() > deadline:
            raise TimeoutError("timed out waiting for worker signal")
        qapp.processEvents()
        time.sleep(0.005)


def test_controller_holds_and_releases_socket_around_op(qapp):
    """The flag + guard-widget disable happen BEFORE the worker thread
    starts (so no polling tick can race the op's first request), and both
    are released only after the op completes back on the UI thread."""
    connection = SimpleNamespace(long_op_active=False)
    button = _Button()
    entered = threading.Event()
    release = threading.Event()

    def slow_op():
        # Runs on the worker thread. start() set the flag + disabled the
        # widget synchronously on the UI thread before the thread started,
        # so both are already true here — if not, the assertion raises and
        # entered is never set, failing the test below.
        assert connection.long_op_active is True
        assert not button.isEnabled()
        entered.set()
        assert release.wait(5.0)  # hold the op open so the test can observe
        return "done"

    results = []
    controller = LongOpController(connection, [button])
    controller.finished.connect(results.append)
    controller.start(slow_op)
    thread = controller._thread  # capture before any pump (see module docstring)

    assert entered.wait(5.0), "worker never started"
    # Mid-flight: the op holds the socket exclusively and the guard widget
    # stays disabled for the whole duration.
    assert connection.long_op_active is True
    assert not button.isEnabled()

    release.set()
    _pump(qapp, lambda: results)
    assert thread.wait(2000), "worker thread did not finish"

    assert results == ["done"]
    assert connection.long_op_active is False
    assert button.isEnabled()


def test_controller_failed_path_releases_socket(qapp):
    """Even when the worker fn raises, the socket is released and the guard
    widgets restored — a worker bug can never leave the connection stuck
    'long op active'."""
    connection = SimpleNamespace(long_op_active=False)
    button = _Button()
    errors = []

    def boom():
        raise ValueError("kaboom")

    controller = LongOpController(connection, [button])
    controller.failed.connect(errors.append)
    controller.start(boom)
    thread = controller._thread

    _pump(qapp, lambda: errors)
    assert thread.wait(2000), "worker thread did not finish"

    assert errors == ["kaboom"]
    assert connection.long_op_active is False
    assert button.isEnabled()


def test_start_long_op_wires_signals_and_returns_controller(qapp):
    """start_long_op() is the factory the docks call: it must return the
    controller, wire finished->on_success / failed->on_error, and run
    fn(*args) off the UI thread. Callers may keep the returned reference
    (useful to inspect state) but do not need to for correctness — see the
    _ACTIVE_CONTROLLERS tests below."""
    connection = SimpleNamespace(long_op_active=False)
    results = []
    errors = []

    controller = start_long_op(
        connection, [], lambda x: x * 2, results.append, errors.append, 21)
    assert isinstance(controller, LongOpController)
    thread = controller._thread

    _pump(qapp, lambda: results)
    assert thread.wait(2000), "worker thread did not finish"

    assert results == [42]


def test_start_long_op_survives_caller_dropping_its_reference(qapp):
    """2026-08-03 regression: a caller that overwrites/drops its own
    reference to the returned controller the instant a new op starts (e.g.
    MainWindow._poll()/_poll_board_selection(), which store the result in a
    single instance attribute re-assigned every ~400ms-2s tick) used to
    crash — LongOpController has no Qt parent, so Python GC'ing the last
    reference deleted the underlying C++ object (and, via Qt's parent-child
    ownership, its child QThread) even while that thread's OS-level run()
    was still executing: "QThread: Destroyed while thread '' is still
    running" / "RuntimeError: wrapped C/C++ object of type _LongOpWorker has
    been deleted". start_long_op() must keep its own reference until the
    thread has genuinely stopped, independent of the caller."""
    connection = SimpleNamespace(long_op_active=False)
    results = []

    def slow_op():
        time.sleep(0.05)
        return "done"

    controller = start_long_op(connection, (), slow_op, results.append, results.append)
    assert controller in worker_mod._ACTIVE_CONTROLLERS

    # CPython deallocates on refcount-zero immediately, no gc.collect() needed
    # (and calling it here, concurrently with the still-running worker thread,
    # is itself a crash risk unrelated to what this test checks) — without the
    # fix, this del alone was already enough to destroy the still-running
    # QThread, since nothing else referenced the controller.
    del controller

    _pump(qapp, lambda: results)

    assert results == ["done"]
    assert connection.long_op_active is False


def test_active_controllers_registry_releases_once_thread_stops(qapp):
    """The keep-alive registry must not leak forever — once thread_stopped
    fires (the OS thread has genuinely exited), the controller is dropped
    from _ACTIVE_CONTROLLERS and becomes collectible again."""
    connection = SimpleNamespace(long_op_active=False)
    controller = start_long_op(connection, (), lambda: "x", lambda r: None, lambda m: None)
    assert controller in worker_mod._ACTIVE_CONTROLLERS

    _pump(qapp, lambda: controller not in worker_mod._ACTIVE_CONTROLLERS)

    assert controller not in worker_mod._ACTIVE_CONTROLLERS


def test_poll_suspends_during_long_op(real_main_window, monkeypatch, qapp):
    """While long_op_active is set, _poll() must not touch the socket at
    all — not even a manual (button-click) refresh, which would interleave
    a connect()/refresh() into the op's in-flight REQ. _poll() itself now
    dispatches through start_long_op (2026-08-03 fix), so the second call's
    effect only lands after pumping the event loop — see conftest._pump's
    docstring for why the guard is `not long_op_active`, not "connect_calls
    is non-empty"."""
    window = real_main_window
    window._timer.stop()  # deterministic: no auto-tick can fire mid-test
    window._selection_timer.stop()
    # No live KiCad here regardless (MainWindow no longer connects at
    # construction time at all, see gui/main_window.py's __init__) — forced
    # anyway so this test never depends on that.
    window.connection.board = None

    connect_calls = []
    monkeypatch.setattr(window.connection, "connect",
                        lambda: connect_calls.append(1) or "fake error")

    window.connection.long_op_active = True
    window._poll(manual=True)
    assert connect_calls == []

    window.connection.long_op_active = False
    window._poll(manual=True)
    _pump(qapp, lambda: not window.connection.long_op_active)
    assert connect_calls == [1]


def test_selection_tick_suspends_during_long_op(real_main_window, monkeypatch, qapp):
    """While long_op_active is set, the fast selection-watch tick must not
    call get_selected_items() on the shared socket either. Same dispatch/pump
    reasoning as test_poll_suspends_during_long_op above."""
    window = real_main_window
    window._timer.stop()
    window._selection_timer.stop()

    get_selected_calls = []
    window.connection.board = SimpleNamespace(
        adapter=SimpleNamespace(
            get_selected_items=lambda: get_selected_calls.append(1) or []))
    # is_connected is a property (board is not None), so the tick would
    # normally proceed — only the long-op flag holds it back.

    window.connection.long_op_active = True
    window._poll_board_selection()
    assert get_selected_calls == []

    window.connection.long_op_active = False
    window._poll_board_selection()
    _pump(qapp, lambda: not window.connection.long_op_active)
    assert get_selected_calls == [1]


# ── KiCad IPC failures: human text, never a raw stack (X.2.2) ───────────────
# plan_2026_09_11_no_modals_and_busy_kicad X.2.2: EVERY GUI long op (Apply /
# Extract / Redraw — the docks' worker fns AND everything that lets an ApiError
# escape) funnels its failures through _LongOpWorker, so this is the one
# chokepoint that turns "kipy.errors.ApiError: KiCad returned error: KiCad is
# busy and cannot respond to API requests right now" (the last line of a ~20
# line stack, found live on a tree Redraw) into words the user can act on.


def test_long_op_api_error_reports_human_text_without_a_stack(qapp, caplog):
    """A busy KiCad inside a long op: on_error gets cli_common.
    api_error_message's explanation, the Log gets it at ERROR, and the
    traceback is kept at DEBUG only (never INFO+)."""
    from kipy.errors import ApiError, ApiStatusCode

    connection = SimpleNamespace(long_op_active=False)
    errors = []

    def busy():
        raise ApiError(
            "KiCad returned error: KiCad is busy and cannot respond to API "
            "requests right now", code=ApiStatusCode.AS_BUSY)

    controller = LongOpController(connection, [])
    controller.failed.connect(errors.append)

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        controller.start(busy)
        thread = controller._thread
        _pump(qapp, lambda: errors)
        assert thread.wait(2000), "worker thread did not finish"

    assert errors and "KiCad is busy" in errors[0]
    assert any(r.levelno == logging.ERROR and "KiCad is busy" in r.getMessage()
               for r in caplog.records)
    assert not any(r.exc_info and r.levelno >= logging.INFO
                   for r in caplog.records), \
        "the traceback must stay at DEBUG for a board-state failure"
    assert connection.long_op_active is False


def test_long_op_unexpected_exception_still_logs_the_stack(qapp, caplog):
    """The ApiError branch must not have weakened the general one: a genuinely
    unexpected Exception keeps logger.exception's traceback at ERROR."""
    connection = SimpleNamespace(long_op_active=False)
    errors = []

    def boom():
        raise RuntimeError("kaboom")

    controller = LongOpController(connection, [])
    controller.failed.connect(errors.append)

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        controller.start(boom)
        thread = controller._thread
        _pump(qapp, lambda: errors)
        assert thread.wait(2000), "worker thread did not finish"

    assert errors == ["kaboom"]
    assert any(r.exc_info and r.levelno >= logging.ERROR for r in caplog.records)
