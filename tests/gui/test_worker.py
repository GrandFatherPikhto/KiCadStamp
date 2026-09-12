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


# ── Busy indicator: the hook (Э1, plan_2026_09_12_busy_indicator) ────────────
# The reporter is the seam between LongOpController and the status bar. These
# tests pin both ends of its contract: reported when the operation starts,
# cleared when it finishes (through _release, the single tail of the success AND
# the failure path), and NEVER reported by the background poll — P.1 of the
# plan: hanging the indicator on connection.long_op_active instead would make it
# blink several times a second on an idle GUI.


def _install_recording_reporter(calls):
    """Install a recording reporter and return a restore callback — the hook is
    a module global, so anything that sets it must put it back."""
    worker_mod.set_busy_reporter(calls.append)
    return lambda: worker_mod.set_busy_reporter(None)


def test_busy_reporter_reports_the_operation_and_clears_on_success(qapp):
    """_acquire reports the operation's word BEFORE the worker thread starts
    (so the label is up for the op's whole life) and _release clears it after."""
    connection = SimpleNamespace(long_op_active=False)
    calls = []
    restore = _install_recording_reporter(calls)
    try:
        controller = start_long_op(connection, (), lambda: "done",
                                   lambda _r: None, lambda _m: None,
                                   busy_text="PLACEHOLDER")
        thread = controller._thread  # capture before any pump (see module docstring)
        # The report is part of start()'s synchronous UI-thread part.
        assert calls == ["PLACEHOLDER"]
        _pump(qapp, lambda: not connection.long_op_active)
        assert thread.wait(2000), "worker thread did not finish"
    finally:
        restore()

    assert calls == ["PLACEHOLDER", None]


def _boom():
    raise ValueError("kaboom")


def test_busy_reporter_clears_on_failure_too(qapp):
    """A failing operation must clear the indicator as well — otherwise the
    label would lie forever after the first error."""
    connection = SimpleNamespace(long_op_active=False)
    errors = []
    calls = []
    restore = _install_recording_reporter(calls)
    try:
        controller = start_long_op(connection, (), _boom, errors.append,
                                   errors.append, busy_text="PLACEHOLDER")
        thread = controller._thread
        assert calls == ["PLACEHOLDER"]
        _pump(qapp, lambda: errors)
        assert thread.wait(2000), "worker thread did not finish"
    finally:
        restore()

    assert errors == ["kaboom"]
    assert connection.long_op_active is False
    assert calls == ["PLACEHOLDER", None]


def test_busy_reporter_gets_the_generic_text_when_the_op_is_unnamed(qapp):
    """Without busy_text the reporter is told "busy, no name" — the sentinel the
    GUI turns into its own generic wording — never None, which means finished."""
    connection = SimpleNamespace(long_op_active=False)
    calls = []
    restore = _install_recording_reporter(calls)
    try:
        controller = start_long_op(connection, (), lambda: "done",
                                   lambda _r: None, lambda _m: None)
        thread = controller._thread
        assert calls == [worker_mod.GENERIC_BUSY_TEXT]
        assert worker_mod.GENERIC_BUSY_TEXT is not None
        _pump(qapp, lambda: not connection.long_op_active)
        assert thread.wait(2000), "worker thread did not finish"
    finally:
        restore()

    assert calls == [worker_mod.GENERIC_BUSY_TEXT, None]


def test_previously_disabled_guard_widget_stays_disabled(qapp):
    """_release restores each guard widget's PRIOR state — a widget that was
    already off (e.g. Redraw with nothing checked) must not be switched on by
    finishing an operation."""
    connection = SimpleNamespace(long_op_active=False)
    already_off = _Button()
    already_off.setEnabled(False)
    was_on = _Button()
    calls = []
    restore = _install_recording_reporter(calls)
    try:
        controller = start_long_op(connection, (already_off, was_on),
                                   lambda: "done", lambda _r: None,
                                   lambda _m: None)
        thread = controller._thread
        assert not already_off.isEnabled() and not was_on.isEnabled()
        _pump(qapp, lambda: not connection.long_op_active)
        assert thread.wait(2000), "worker thread did not finish"
    finally:
        restore()

    assert not already_off.isEnabled(), "an already-disabled widget was re-enabled"
    assert was_on.isEnabled()
    assert calls[-1] is None


def test_wait_cursor_goes_up_with_the_op_and_comes_back_down(qapp):
    """Э1: the wait cursor is pushed in _acquire and popped in _release. Qt
    restores override cursors by CALL COUNT, so an unbalanced pair would leak a
    permanent hourglass into the rest of the session (and one spare restore()
    would pop a cursor somebody else pushed) — hence both halves here."""
    from PyQt6.QtWidgets import QApplication

    assert QApplication.overrideCursor() is None, \
        "a previous test leaked an override cursor"
    connection = SimpleNamespace(long_op_active=False)
    entered = threading.Event()
    release = threading.Event()

    def slow_op():
        entered.set()
        assert release.wait(5.0)
        return "done"

    controller = start_long_op(connection, (), slow_op, lambda _r: None,
                               lambda _m: None)
    thread = controller._thread
    assert entered.wait(5.0), "worker never started"
    assert QApplication.overrideCursor() is not None, \
        "no wait cursor was set while the operation ran"

    release.set()
    _pump(qapp, lambda: not connection.long_op_active)
    assert thread.wait(2000), "worker thread did not finish"

    assert QApplication.overrideCursor() is None, "the wait cursor leaked"


def test_selection_poll_tick_never_touches_the_busy_reporter(real_main_window, qapp):
    """KEY test against the P.1 trap. The ~400ms selection tick is dispatched
    through PollWorkerHandle.submit, which raises connection.long_op_active
    exactly like a real operation does (asserted below) — so an indicator hung
    on that FLAG would report here. The reporter must stay silent: the poll is
    not something the user started. On code where the indicator sits on
    long_op_active this test fails."""
    window = real_main_window
    window._timer.stop()
    window._selection_timer.stop()
    window.connection.board = SimpleNamespace(
        adapter=SimpleNamespace(get_selected_items=lambda: []))

    calls = []
    worker_mod.set_busy_reporter(calls.append)
    try:
        window._poll_board_selection()
        # The tick really did take the shared-socket token — otherwise this test
        # would pass for the wrong reason.
        assert window.connection.long_op_active is True
        _pump(qapp, lambda: not window.connection.long_op_active)
    finally:
        worker_mod.set_busy_reporter(window._set_busy)

    assert calls == []


def test_real_window_busy_label_follows_a_long_op(real_main_window, qapp):
    """End to end: the hook MainWindow installs at construction is the one a
    long operation reaches — the label is up while the op runs and empty again
    once it finishes."""
    window = real_main_window
    connection = SimpleNamespace(long_op_active=False)
    entered = threading.Event()
    release = threading.Event()

    def slow_op():
        entered.set()
        assert release.wait(5.0)
        return "done"

    controller = start_long_op(connection, (), slow_op, lambda _r: None,
                               lambda _m: None, busy_text="PLACEHOLDER")
    thread = controller._thread
    assert entered.wait(5.0), "worker never started"
    assert "PLACEHOLDER" in window.busy_label.text()

    release.set()
    _pump(qapp, lambda: not connection.long_op_active)
    assert thread.wait(2000), "worker thread did not finish"

    assert window.busy_label.text() == ""
