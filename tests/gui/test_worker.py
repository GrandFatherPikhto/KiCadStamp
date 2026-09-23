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
    active owner for its whole lifetime;
  * the worker QObject is destroyed ON THE UI THREAD, and only after its own
    thread has stopped (the 2026-09-18 GIL-vs-Qt-mutex freeze —
    plan_2026_09_18_worker_delete_deadlock; see the "Worker teardown" section
    at the end of this file for why measuring that took two instruments).

The QThread tests deliberately avoid touching controller._thread after the
event loop has been pumped (its finished->deleteLater chain may already have
deleted the C++ object); the thread reference is captured right after
start() (before any pump) and only used for a final wait(), which is safe
because the completion handler has already run (posting quit()) by then.
"""
import logging
import threading
import time
import weakref
from types import SimpleNamespace

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEvent, Qt

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


# ── Worker teardown (2026-09-18, plan_2026_09_18_worker_delete_deadlock) ─────
#
# The defect these tests pin is not "a dialog opened slowly". `finished` is
# emitted INSIDE the worker thread (Qt's QThreadPrivate::finish), so
# `finished.connect(worker.deleteLater)` was a DIRECT call: deleteLater posted a
# DeferredDelete event into the worker's own queue, and `finish` drains exactly
# that queue right after. The Python-subclassed QObject was therefore destroyed
# on a foreign thread, where sip takes the GIL, while the UI thread held the GIL
# and waited for a Qt signal/slot mutex — the live freeze of 2026-09-18
# (diagnostics/freeze_2026_09_18_boundary_dialog_pyspy.txt).
#
# Watching it took TWO instruments, because they answer in different cases —
# both measured in diagnostics/probe_worker_teardown_instrument.py:
#
#   * `QObject.destroyed` fires only when Qt itself deletes the C++ object (the
#     broken path). Connected with DirectConnection it runs IN THE THREAD DOING
#     THE DELETION — on the base commit it named a thread that was not the UI
#     thread;
#   * a weakref callback runs when the Python wrapper is deallocated, i.e. in
#     the thread whose sip dealloc runs `~QObject`. That is the ONLY instrument
#     that sees the fixed path: on the refcount path PyQt does not call Python
#     slots for `destroyed` at all.
#
# A test must NOT hold a strong reference to the worker it watches — that
# reference would itself be the reason the object is still alive — so the
# weakref is taken inside the worker's own __init__.
_TEARDOWN: list = []
_TEARDOWN_REFS: list = []


def _record_teardown(instrument: str) -> None:
    """(instrument, thread ident) of one observed destruction."""
    _TEARDOWN.append((instrument, threading.get_ident()))


def _on_worker_destroyed(*_args) -> None:
    _record_teardown("destroyed")


def _on_worker_collected(_ref) -> None:
    _record_teardown("weakref")


class _SpyWorker(worker_mod._LongOpWorker):
    """A `_LongOpWorker` that reports its own destruction, whichever way it
    happens. It is installed by the `teardown_watch` fixture, so every op the
    test starts is watched; the production class stays untouched."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.destroyed.connect(_on_worker_destroyed,
                               Qt.ConnectionType.DirectConnection)
        _TEARDOWN_REFS.append(weakref.ref(self, _on_worker_collected))


@pytest.fixture
def teardown_watch(monkeypatch):
    """[(instrument, thread ident)] of every worker destruction this test
    observes — see the section comment above for the two instruments."""
    _TEARDOWN.clear()
    _TEARDOWN_REFS.clear()
    monkeypatch.setattr(worker_mod, "_LongOpWorker", _SpyWorker)
    yield _TEARDOWN
    _TEARDOWN.clear()
    _TEARDOWN_REFS.clear()


def _drain(app) -> None:
    """Let DeferredDelete through, then settle.

    `finished -> thread.deleteLater` (the QThread's own teardown, kept
    deliberately) and the worker's release both complete only while an
    outermost event loop — or this explicit pass — delivers what was posted;
    tests/gui/conftest._pump deliberately does not, so a test that measures the
    END state has to ask for it."""
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_the_worker_is_destroyed_on_the_ui_thread_after_a_successful_op(
        qapp, teardown_watch):
    """С2 of the plan. On the base commit the worker was destroyed in the
    worker thread itself (`destroyed` names a foreign thread here); the fix
    drops the last Python reference on the UI thread instead.

    A foreign-thread destruction is not a nitpick: that is the thread that then
    takes the GIL while holding a Qt signal/slot mutex — the freeze."""
    connection = SimpleNamespace(long_op_active=False)
    results = []
    controller = start_long_op(connection, (), lambda: "done",
                               results.append, results.append)
    _pump(qapp, lambda: results and
          controller not in worker_mod._ACTIVE_CONTROLLERS)
    _drain(qapp)

    assert teardown_watch, (
        "the worker was never destroyed after the operation completed — "
        "something still holds a reference to it (a kept reference is a leak, "
        "not a fix)")
    foreign = [obs for obs in teardown_watch if obs[1] != threading.get_ident()]
    assert foreign == [], (
        "the worker QObject was destroyed on a thread that is not the UI "
        "thread: " + ", ".join(f"{name}@thread{ident}"
                               for name, ident in foreign) +
        " — that is the 2026-09-18 GIL-vs-Qt-mutex deadlock (С2)")
    assert controller._worker is None, (
        "the controller still holds the per-op worker after the operation")


def _busy_kicad():
    """A board-STATE failure (the live shape: KiCad busy) — i.e. the ApiError
    branch of _LongOpWorker.run.

    Why the failure-path teardown tests must fail THIS way and not with a bare
    exception, measured the hard way: the generic branch logs
    `logger.exception`, tests/gui/conftest's autouse _capture_dock_logs fixture
    captures that record at INFO, and a traceback KEEPS ITS FRAMES — run()'s
    frame holds `self`, the worker. The captured record therefore keeps the
    whole worker alive for the duration of the test, so such a test measures the
    logging harness instead of the teardown (and `caplog.clear()` does not
    release every copy pytest keeps for its own report — tried, still retained).
    The ApiError branch keeps its traceback at DEBUG, below what the fixture
    captures, so nothing retains the worker and the destruction under test is
    the only one observed."""
    from kipy.errors import ApiError, ApiStatusCode

    raise ApiError("KiCad returned error: KiCad is busy and cannot respond to "
                   "API requests right now", code=ApiStatusCode.AS_BUSY)


def test_the_worker_is_destroyed_on_the_ui_thread_after_a_failed_op(
        qapp, teardown_watch):
    """The failure path tears down exactly like the success path — a failed op
    must not leave the worker to the worker thread either (see _busy_kicad for
    why this failure is an ApiError and not a bare exception)."""
    connection = SimpleNamespace(long_op_active=False)
    errors = []
    controller = start_long_op(connection, (), _busy_kicad,
                               lambda _r: None, errors.append)
    _pump(qapp, lambda: errors and
          controller not in worker_mod._ACTIVE_CONTROLLERS)
    _drain(qapp)

    assert errors and "KiCad is busy" in errors[0]
    assert teardown_watch, "the failed worker was never destroyed"
    foreign = [obs for obs in teardown_watch if obs[1] != threading.get_ident()]
    assert foreign == [], (
        "the failed worker QObject was destroyed off the UI thread: " + ", ".join(
            f"{name}@thread{ident}" for name, ident in foreign))


def test_the_payload_is_delivered_before_the_worker_is_destroyed(
        qapp, teardown_watch):
    """С3 of the plan. The order the callers depend on, unchanged: the
    controller's finished/failed first (with the payload verbatim), the worker
    still alive at that instant, thread_stopped afterwards — and only then the
    destruction.

    The "still alive" half is what forbids the tempting shortcut of dropping the
    reference in _release(): that runs while the `succeeded` payload is being
    delivered, and `run()` — the worker's own slot — can still be on the worker
    thread's stack there (its emit is the last statement)."""
    connection = SimpleNamespace(long_op_active=False)
    events = []
    worker_alive_at_delivery = []
    destroyed_at_delivery = []

    def on_success(result):
        events.append(("finished", result))
        worker_alive_at_delivery.append(controller._worker is not None)
        destroyed_at_delivery.append(list(teardown_watch))

    controller = start_long_op(connection, (), lambda: "done", on_success,
                               lambda message: events.append(("failed", message)))
    controller.thread_stopped.connect(
        lambda: events.append(("thread_stopped", None)))
    _pump(qapp, lambda: len(events) >= 2)
    _drain(qapp)

    assert events == [("finished", "done"), ("thread_stopped", None)]
    assert worker_alive_at_delivery == [True]
    assert destroyed_at_delivery == [[]], (
        "the worker was already destroyed while the result was being delivered")
    assert teardown_watch, (
        "the worker outlived the operation — thread_stopped is the point the "
        "keep-alive registry already treats as 'the thread has genuinely "
        "stopped', and teardown belongs there")


def test_the_failure_payload_is_delivered_before_the_worker_is_destroyed(
        qapp, teardown_watch):
    """Same order on the failing path, with the human message verbatim (an
    ApiError, for the retention reason spelled out in _busy_kicad)."""
    from kicadstamp.cli_common import api_error_message
    from kipy.errors import ApiError, ApiStatusCode

    connection = SimpleNamespace(long_op_active=False)
    events = []
    worker_alive_at_delivery = []

    def on_error(message):
        events.append(("failed", message))
        worker_alive_at_delivery.append(controller._worker is not None)

    controller = start_long_op(connection, (), _busy_kicad,
                               lambda _r: None, on_error)
    controller.thread_stopped.connect(
        lambda: events.append(("thread_stopped", None)))
    _pump(qapp, lambda: len(events) >= 2)
    _drain(qapp)

    expected = api_error_message(ApiError(
        "KiCad returned error: KiCad is busy and cannot respond to API "
        "requests right now", code=ApiStatusCode.AS_BUSY))
    assert events == [("failed", expected), ("thread_stopped", None)]
    assert "KiCad is busy" in events[0][1]
    assert worker_alive_at_delivery == [True]
    assert teardown_watch


def test_the_qthread_is_still_deleted_by_finished_delete_later(qapp):
    """С4 of the plan: the NEIGHBOURING line stays. `QThread` objects belong to
    the thread that created them, so `finished -> self._thread.deleteLater` was
    never the problem (Ф5) and removing it "for symmetry" would leak a QThread
    per operation — this test is what makes that mistake loud."""
    connection = SimpleNamespace(long_op_active=False)
    results = []
    controller = start_long_op(connection, (), lambda: "done",
                               results.append, results.append)
    thread = controller._thread
    _pump(qapp, lambda: results and
          controller not in worker_mod._ACTIVE_CONTROLLERS)
    _drain(qapp)

    assert sip.isdeleted(thread), (
        "the QThread was not deleted — the finished -> deleteLater chain is "
        "gone and every long operation now leaks its thread")


# ── A socket without an owner: the refusal and the one deferred retry ────────
# plan_2026_09_23_socket_owner_and_door_docs.md, Ш1. connection.long_op_active
# has TWO independent owners — LongOpController and the ~400ms selection-poll
# tick (PollWorkerHandle.submit) — and until 2026-09-23 start() raised it
# unconditionally, so a click inside the tick's in-flight request ran a second
# REQ in the same transaction (Д1) and _release() cleared a token it had not
# raised (Д2). The refusal and the retry now live in LongOpController.start()
# alone, which is where all 56 start_long_op callers reach them unchanged.
#
# Measured live before the fix (kicadstamp/diagnostics/probe_socket_owner.py):
# Д1 ALIVE (two owners, the op ran into the tick's transaction), Д2 ALIVE (the
# tick's flag came back False). After the fix: both closed, one deferral only.


def _capture_retries(monkeypatch) -> list:
    """Capture QTimer.singleShot calls made by gui.worker (start()'s single
    deferred retry) WITHOUT waiting on real time. Returns
    [(delay_ms, callback), ...] — the idiom tests/gui/test_snapshot_freshness.py
    already uses."""
    scheduled: list = []
    monkeypatch.setattr(
        worker_mod.QTimer, "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)))
    return scheduled


def _drain_controller(qapp, controller) -> None:
    """Wait for a controller's QThread (when one was started) to stop — a test
    must never leave a thread running, which aborts the process at exit with
    the 2026-08-03 "QThread: Destroyed while thread is still running"."""
    if controller._thread is not None:
        _pump(qapp, lambda: controller not in worker_mod._ACTIVE_CONTROLLERS)
        controller._thread.wait(2000)


def _fire_retry_and_drain(qapp, scheduled, controller) -> None:
    """Fire the armed retry, then drain whatever it started (a mutated build
    may start a worker where the real one refuses)."""
    scheduled[0][1]()
    _drain_controller(qapp, controller)


def test_a_busy_socket_defers_the_op_and_does_not_seize_the_flag(
        qapp, monkeypatch):
    """С1 of plan_2026_09_23_socket_owner_and_door_docs.md (Д1). Another owner
    holds the shared socket: start() must NOT raise the flag and must NOT start
    the worker — it arms exactly ONE deferred retry instead."""
    connection = SimpleNamespace(long_op_active=True)
    started = []
    scheduled = _capture_retries(monkeypatch)
    controller = start_long_op(
        connection, (), lambda: started.append(True) or "x",
        lambda _r: None, lambda _m: None)

    try:
        assert controller._thread is None, (
            "the worker started into the tick's in-flight transaction")
        assert started == [], "the op ran against a busy socket"
        assert connection.long_op_active is True, "the tick's token was seized"
        assert len(scheduled) == 1, "more than one deferred retry was armed"
        assert scheduled[0][0] == worker_mod.SNAPSHOT_REFRESH_RETRY_DELAY_MS
    finally:
        # Drain: the retry's OWN outcome is С3/С4/С5's subject, not this test's.
        # On a mutated build the assertions above are exactly what fails, and the
        # worker they let start must not outlive the test.
        if scheduled:
            _fire_retry_and_drain(qapp, scheduled, controller)
        else:
            _drain_controller(qapp, controller)


def test_the_deferred_op_runs_once_the_socket_frees(qapp, monkeypatch):
    """С2 of plan_2026_09_23_socket_owner_and_door_docs.md. The socket frees
    within the 120 ms delay: the single retry acquires it, the worker runs, and
    the result reaches on_success exactly as on the immediate path."""
    connection = SimpleNamespace(long_op_active=True)
    results = []
    scheduled = _capture_retries(monkeypatch)
    controller = start_long_op(connection, (), lambda: "done",
                               results.append, lambda _m: None)
    try:
        assert scheduled and controller._thread is None
        connection.long_op_active = False      # the tick finished
        scheduled[0][1]()                      # fire the one retry
        thread = controller._thread
        assert thread is not None, "the deferred op never started its worker"
        _pump(qapp, lambda: bool(results) and
              controller not in worker_mod._ACTIVE_CONTROLLERS)
        assert thread.wait(2000), "worker thread did not finish"

        assert results == ["done"]
        assert connection.long_op_active is False
    finally:
        _drain_controller(qapp, controller)


def test_a_second_busy_socket_refuses_through_one_on_error(qapp, monkeypatch):
    """С3 of plan_2026_09_23_socket_owner_and_door_docs.md. The retry meets a
    STILL-busy socket: on_error is called exactly once, no worker starts, and
    no third attempt is armed."""
    connection = SimpleNamespace(long_op_active=True)
    started = []
    results = []
    errors = []
    scheduled = _capture_retries(monkeypatch)
    controller = start_long_op(
        connection, (), lambda: started.append(True) or "x",
        results.append, errors.append)
    try:
        assert scheduled and controller._thread is None
        scheduled[0][1]()                      # the retry; socket still busy

        assert len(errors) == 1, "on_error must be called exactly once"
        assert errors[0] == worker_mod._(
            "the board is busy — try again in a moment")
        assert results == [], "a refused op still reported success"
        assert started == [], "the worker ran against a busy socket"
        assert controller._thread is None
        assert len(scheduled) == 1, "a third attempt was armed"
    finally:
        # A mutated build that starts the worker here must not leave it running.
        _drain_controller(qapp, controller)


def test_a_refused_controller_leaves_the_keep_alive_registry(qapp, monkeypatch):
    """С4 of plan_2026_09_23_socket_owner_and_door_docs.md (trap 1). On the
    refusal path no QThread is ever created, so thread_stopped would never come
    from a finished thread — the controller would sit in _ACTIVE_CONTROLLERS
    for the rest of the session. The refusal must retire it explicitly."""
    connection = SimpleNamespace(long_op_active=True)
    errors = []
    scheduled = _capture_retries(monkeypatch)
    controller = start_long_op(connection, (), lambda: "x",
                               lambda _r: None, errors.append)
    try:
        assert scheduled, "no deferred retry was armed"
        assert controller in worker_mod._ACTIVE_CONTROLLERS
        scheduled[0][1]()

        assert errors, "the op was not refused"
        assert controller not in worker_mod._ACTIVE_CONTROLLERS, (
            "a controller whose refusal path never started a thread stayed in "
            "the keep-alive set forever")
    finally:
        _drain_controller(qapp, controller)


def test_a_controller_that_did_not_acquire_never_clears_the_flag(
        qapp, monkeypatch):
    """С5 of plan_2026_09_23_socket_owner_and_door_docs.md (Д2). A controller
    that never raised the flag must not clear it when it finishes: the flag
    belongs to the poll tick, whose request is still in flight — clearing it
    would let a third party into that transaction."""
    connection = SimpleNamespace(long_op_active=True)
    scheduled = _capture_retries(monkeypatch)
    controller = start_long_op(connection, (), lambda: "x",
                               lambda _r: None, lambda _m: None)
    try:
        assert scheduled, "no deferred retry was armed"
        scheduled[0][1]()                      # refusal: this op never acquired
        assert connection.long_op_active is True, (
            "the refusal path cleared a token the poll tick still holds")
    finally:
        _drain_controller(qapp, controller)


# ── the DEFERRED path keeps the busy visual honest (2026-09-24) ───────────
#
# §6.1 of `plan_2026_09_24_reload_store_snapshot`. Every cell below goes through
# the DEFERRED path — busy socket, start() arms the one retry, the retry then
# acquires, refuses or abandons — because that is the ONLY path on which
# `_show_busy()` runs twice and these properties can be violated at all. The
# direct path calls it once, and the guards it already has stayed green under
# every mutation measured on 2026-09-23 (plan §6.0: idempotence of the visual was
# claimed but guarded by nothing).

def test_a_deferred_op_leaves_the_guard_widgets_enabled(qapp, monkeypatch):
    """§6.1 of `plan_2026_09_24_reload_store_snapshot` — `_show_busy()` is
    idempotent, and this is the cell that says WHY it must be.

    The deferred path shows the busy visual BEFORE the retry (start() met a busy
    socket) and `_acquire()` shows it again once the socket frees. Without the
    `if self._visual_shown: return` guard the second pass records each widget's
    state as it is NOW — disabled — into `_prior_enabled`, and `_release()` then
    honestly restores the buttons to DISABLED. Measured on a live button:
    original enabled=True, without the guard enabled=False."""
    connection = SimpleNamespace(long_op_active=True)
    button = _Button()
    results = []
    scheduled = _capture_retries(monkeypatch)
    controller = start_long_op(connection, (button,), lambda: "done",
                               results.append, lambda _m: None)
    try:
        assert scheduled and controller._thread is None, (
            "the deferred path was not taken, so this cell would be measuring "
            "the direct one — where _show_busy() runs once")
        assert button.isEnabled() is False, "the busy visual never went up"

        connection.long_op_active = False          # the poll tick finished
        scheduled[0][1]()                          # the single retry
        thread = controller._thread
        assert thread is not None, "the deferred op never started its worker"
        _pump(qapp, lambda: bool(results) and
              controller not in worker_mod._ACTIVE_CONTROLLERS)
        assert thread.wait(2000), "worker thread did not finish"

        assert results == ["done"]
        assert button.isEnabled() is True, (
            "the guard widget is still DISABLED after the op finished: "
            "_show_busy() ran twice and the second pass remembered the state it "
            "had just created")
    finally:
        _drain_controller(qapp, controller)


def test_a_refused_deferred_op_leaves_the_guard_widgets_enabled(qapp, monkeypatch):
    """§6.1 of `plan_2026_09_24_reload_store_snapshot` — the refusal half:
    `_refuse_busy()` must call `_release()`.

    A retry that meets a STILL-busy socket gives up. Without the `_release()`
    there, the widgets `_show_busy()` disabled stay disabled for the rest of the
    session: the visual of an operation that never ran."""
    connection = SimpleNamespace(long_op_active=True)
    button = _Button()
    errors = []
    scheduled = _capture_retries(monkeypatch)
    controller = start_long_op(connection, (button,), lambda: "x",
                               lambda _r: None, errors.append)
    try:
        assert scheduled and button.isEnabled() is False
        scheduled[0][1]()                          # still busy → refusal

        assert errors, "the op was not refused"
        assert button.isEnabled() is True, (
            "the guard widget is still DISABLED after the refusal: the visual of "
            "an operation that never started outlived it")
    finally:
        _drain_controller(qapp, controller)


class _DisposableWidget:
    """A widget that answers normally until it `die()`s — the shape of the real
    thing: it is alive when start() raises the busy visual, and its C++ object is
    gone by the time the retry fires, because the dialog was destroyed during the
    120 ms delay. A touch after death raises exactly what PyQt6 raises."""

    _MESSAGE = "wrapped C/C++ object of type QPushButton has been deleted"

    def __init__(self):
        self._enabled = True
        self.gone = False

    def die(self) -> None:
        self.gone = True

    def isEnabled(self):
        self._check()
        return self._enabled

    def setEnabled(self, enabled):
        self._check()
        self._enabled = enabled

    def _check(self) -> None:
        if self.gone:
            raise RuntimeError(self._MESSAGE)


def _abandoned_deferred_start(qapp, monkeypatch):
    """(controller, widget, started) after an armed retry finds its OWNER
    destroyed — the one path that ends in `_abandon()`.

    `qt_object_gone` is monkeypatched to answer what the real one answers from
    `sip.isdeleted`: True for the widget that died, False for everything else.
    Patching the PREDICATE (not the widget's Python methods) is the point — the
    guard under test is the predicate's call site."""
    connection = SimpleNamespace(long_op_active=True)
    widget = _DisposableWidget()
    started = []
    scheduled = _capture_retries(monkeypatch)
    monkeypatch.setattr(worker_mod, "qt_object_gone",
                        lambda obj: getattr(obj, "gone", False))
    controller = start_long_op(connection, (widget,),
                               lambda: started.append(True),
                               lambda _r: None, lambda _m: None)
    assert scheduled, "no deferred retry was armed"
    widget.die()                                   # the dialog went away
    scheduled[0][1]()                              # the retry: the owner is gone
    return controller, widget, started


def test_an_abandoned_deferred_start_never_touches_the_dead_widget(
        qapp, monkeypatch):
    """§6.1 of `plan_2026_09_24_reload_store_snapshot` — `_deferred_start()`
    checks `qt_object_gone()` BEFORE it acquires.

    The QTimer outlives the dialog it was armed for, so the retry can run for an
    owner that no longer exists. Without the check it would `_acquire()` and put
    the busy visual on a deleted widget — every touch here raises, which is what
    a real deleted QPushButton does."""
    controller, _widget, started = _abandoned_deferred_start(qapp, monkeypatch)
    try:
        assert started == [], "a worker was started for a destroyed owner"
        assert controller._thread is None
    finally:
        _drain_controller(qapp, controller)


def test_an_abandoned_deferred_start_leaves_the_keep_alive_registry(
        qapp, monkeypatch):
    """§6.1 of `plan_2026_09_24_reload_store_snapshot` — `_abandon()` must call
    `_retire()`.

    No QThread is ever created on this path, so `thread_stopped` would never come
    from a finished thread and the controller would sit in `_ACTIVE_CONTROLLERS`
    for the rest of the session. That is the same leak С4 of
    plan_2026_09_23_socket_owner_and_door_docs closed for the refusal path; the
    "owner destroyed during the delay" path had no cell of its own until now."""
    controller, _widget, _started = _abandoned_deferred_start(qapp, monkeypatch)
    try:
        assert controller not in worker_mod._ACTIVE_CONTROLLERS, (
            "an abandoned deferred start leaked its controller in the keep-alive "
            "set for ever")
    finally:
        _drain_controller(qapp, controller)
