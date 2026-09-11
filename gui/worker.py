# gui/worker.py
"""
Long-operation worker — moves blocking, IPC-heavy work (Extract / Redraw)
off the UI thread while preserving the kipy REQ socket's single-in-flight
rule.

Why this exists (Phase 5.2): kipy's connection is a pynng.Req0 socket —
exactly ONE request may be in flight per socket at a time. Two concurrent
owners (a background op + the GUI's polling timers) would interleave
requests mid-flight and corrupt the stream. Today those ops run on the UI
thread, which blocks the GUI's QTimers entirely (so there is implicitly only
one active socket) but freezes the window for the duration. Moving the op to
a worker thread would let the polling timers keep firing concurrently, so a
worker alone is not enough — the shared socket must be serialized.

Serialization model:
  * The shared BoardConnection carries a plain `long_op_active` flag.
  * LongOpController.start() sets it on the UI thread BEFORE the worker
    thread starts, and _release() clears it on the UI thread AFTER the op
    finishes (completion handlers run back on the UI thread via queued
    signal connections — the worker lives in a different thread).
  * While the flag is set, MainWindow._poll / _poll_board_selection and the
    embedded fieldstool's _push_selection_to_board skip their ticks, so the
    socket has exactly one active owner for the whole op. Extract uses the
    shared socket directly (board.adapter), making suspension mandatory;
    Redraw's ApplyPipeline opens its OWN kipy socket, so for it the flag is
    a coordination token that reproduces today's blocked-UI behaviour (no
    second socket while a write is underway).

QWidget safety: QWidgets may only be touched on the UI thread. Each op is
therefore split by the caller into: collect-inputs (UI thread — validation
+ widget reads), run (worker — pure IPC + file IO, NO widget access),
finish (UI thread — widget writes). This module only orchestrates the
thread boundary; the split lives in the docks.

Controller lifetime (2026-08-03 fix — see _ACTIVE_CONTROLLERS below):
start_long_op() itself keeps every in-flight LongOpController alive until
its QThread has genuinely stopped, regardless of what the caller does with
the returned reference — a caller that reassigns/drops its own reference
the moment a new op starts (e.g. MainWindow's ~400ms/2s polling ticks,
which overwrite a single instance attribute every cycle) used to trigger
premature C++ destruction of a still-running QThread.
"""
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)


class _LongOpWorker(QObject):
    """Runs fn(*args) on the worker thread and reports the outcome via
    signals. succeeded() carries the return value; failed() carries a
    human-readable message. Every exception is caught and routed to failed()
    so a worker bug can never silently kill the thread or leave the socket
    held."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[..., Any], args: tuple, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._args = args

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self._fn(*self._args)
        except Exception as e:
            logger.exception("Long operation failed")
            self.failed.emit(str(e))
            return
        self.succeeded.emit(result)


class LongOpController(QObject):
    """Owns the QThread + worker for one long operation. Must be created on
    the UI thread. start() acquires the shared socket (connection.
    long_op_active = True) and disables the guard widgets BEFORE the thread
    starts; the completion handlers run back on the UI thread (queued
    connections, since the worker lives in a different thread) and release
    the socket exactly once."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    # Fires once the worker QThread's event loop has ACTUALLY exited (not
    # just once a result arrived) — the only point at which it is safe to
    # drop the last reference to this controller. See start_long_op's
    # docstring/module-level _ACTIVE_CONTROLLERS for why this matters.
    thread_stopped = pyqtSignal()

    def __init__(self, connection: Any, widgets: Iterable[Any], parent=None):
        super().__init__(parent)
        self._connection = connection
        self._widgets: List[Any] = list(widgets)
        self._thread: Optional[QThread] = None
        self._worker: Optional[_LongOpWorker] = None
        self._released = False
        self._prior_enabled: Dict[Any, bool] = {}

    def start(self, fn: Callable[..., Any], *args) -> None:
        self._acquire()
        self._thread = QThread(self)
        self._worker = _LongOpWorker(fn, args)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._on_worker_succeeded)
        self._worker.failed.connect(self._on_worker_failed)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self.thread_stopped)
        self._thread.start()

    def _acquire(self) -> None:
        # Set on the UI thread BEFORE the worker starts so no polling tick
        # can race the op's first socket request.
        if self._connection is not None:
            self._connection.long_op_active = True
        for w in self._widgets:
            self._prior_enabled[w] = w.isEnabled()
            w.setEnabled(False)

    def _release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._connection is not None:
            self._connection.long_op_active = False
        for w, enabled in self._prior_enabled.items():
            w.setEnabled(enabled)
        if self._thread is not None:
            self._thread.quit()

    @pyqtSlot(object)
    def _on_worker_succeeded(self, result: Any) -> None:
        self._release()
        self.finished.emit(result)

    @pyqtSlot(str)
    def _on_worker_failed(self, message: str) -> None:
        self._release()
        self.failed.emit(message)


# Module-level keep-alive set (2026-08-03 fix — see below). LongOpController
# has no Qt parent, so PyQt6 will delete its underlying C++ object (and, via
# Qt's parent-child ownership, its child QThread) the instant Python's own
# refcount on it hits zero — REGARDLESS of whether the QThread's OS-level
# run() has actually returned yet. MainWindow._poll()/_poll_board_selection()
# each start a new op every ~400ms-2s and store the returned controller in a
# single instance attribute, overwriting the previous one — the moment that
# overwrite happens, the PREVIOUS controller's only Python reference is
# dropped. If its thread had merely been told to quit() (by _release(), a
# few lines earlier in the very same event-loop turn) but hadn't yet
# actually exited its event loop, this produced exactly the crash found
# live: "QThread: Destroyed while thread '' is still running" /
# "RuntimeError: wrapped C/C++ object of type _LongOpWorker has been
# deleted" — non-deterministic (usually the old thread finishes in time,
# sometimes it doesn't). Fix: start_long_op keeps its own strong reference
# here, independent of whatever the caller does with the returned object,
# and only releases it once thread_stopped confirms the OS thread has
# genuinely exited — at that point deleting the controller is always safe.
_ACTIVE_CONTROLLERS: set = set()


def start_long_op(connection, widgets, fn, on_success, on_error, *args):
    """Convenience factory: builds a LongOpController, wires its finished/
    failed signals to on_success/on_error (both called on the UI thread),
    starts the op, and returns the controller (callers may keep their own
    reference too, e.g. to inspect state, but do not need to for correctness
    — see _ACTIVE_CONTROLLERS above)."""
    controller = LongOpController(connection, widgets)
    controller.finished.connect(on_success)
    controller.failed.connect(on_error)
    _ACTIVE_CONTROLLERS.add(controller)
    controller.thread_stopped.connect(lambda: _ACTIVE_CONTROLLERS.discard(controller))
    controller.start(fn, *args)
    return controller


def snapshot_refresh_supported(connection: Any) -> bool:
    """True when `connection` can rebuild its own full-board snapshot — i.e. a
    live board sits behind it (``Board.refresh()``, which
    :meth:`gui.connection.BoardConnection.refresh` calls before
    ``_rebuild_snapshot()``).

    A stand-in connection/board WITHOUT that surface (a foreign/read-only
    connection, a test double) has no fresher data to offer, so callers keep
    the cached snapshot and continue — the same provider-or-fallback shape the
    docks' live providers already use (see
    ``SchemeListFormWidget._live_snapshot``'s "tests/fallback" and
    ``RecordSchemeListDialog._live_selection``)."""
    return callable(getattr(getattr(connection, "board", None), "refresh", None))


def _refresh_snapshot_worker(connection: Any) -> Dict[str, Any]:
    """Worker thread: rebuild ``BoardConnection.snapshot`` from the live board.

    Runs inside :func:`start_long_op`, so the shared kipy REQ socket has
    exactly one in-flight owner (``connection.long_op_active``) for the whole
    rebuild: the polling timers skip their ticks and no second request can
    interleave into this transaction — the 2026-08-08 hang
    (``plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_fix.md`` §0). Never
    touches a widget.

    ``refresh()`` itself drops the connection and returns an error message when
    the live board is gone (KiCad closed/crashed since connect) — that error is
    handed back as-is instead of a stale snapshot."""
    error = connection.refresh()
    if error:
        return {"error": error, "snapshot": []}
    return {"error": None,
            "snapshot": list(getattr(connection, "snapshot", None) or [])}


def refresh_snapshot_then(connection: Any, widgets: Iterable[Any],
                          on_ready: Callable[[], Any],
                          on_error: Callable[[str], Any]) -> Any:
    """Rebuild the board snapshot on a worker thread, then continue on the UI
    thread — "freshness at the point of use".

    ``MainWindow._poll``'s automatic tick is a deliberate no-op once the board
    is connected (one IPC request at a time on the shared socket), so
    ``BoardConnection.snapshot`` freezes at connect/manual-refresh time and
    every GEOMETRY read from it follows stale coordinates. This helper
    is the shared rebuild point for those reads (K.2 #5/#6,
    ``plan_2026_09_11_stale_snapshot_positions.md`` R.1/R.2): callers pass the
    continuation ``on_ready()`` and it runs on the UI thread AFTER the rebuild,
    so every ``connection.snapshot`` read inside it sees the board as it is
    now.

    ``on_error(message)`` runs on the UI thread when the rebuild failed (the
    connection has been dropped by then — see
    :meth:`gui.connection.BoardConnection.refresh`).

    Deliberate synchronous fallbacks (``None`` is returned for both):
      * no refreshable board behind the connection (see
        :func:`snapshot_refresh_supported`) — ``on_ready()`` runs at once on
        the cached snapshot, the only data there is;
      * another long op already holds the shared socket — the request is
        REFUSED and logged, never queued (a second concurrent owner on the same
        REQ socket is exactly the corruption this module exists to prevent).
        Callers normally make that click impossible by passing a guard widget.

    Note that no adapter call ever happens on the calling (UI) thread: the
    rebuild runs on the worker thread, which is what keeps this compatible with
    the Commit H fix (no direct ``adapter.get_footprints()`` on the GUI thread).

    Returns the controller, or ``None`` when it fell back synchronously."""
    if not snapshot_refresh_supported(connection):
        on_ready()
        return None
    if getattr(connection, "long_op_active", False):
        logger.warning("Snapshot refresh refused: another long operation already "
                       "holds the shared kipy socket")
        return None
    return start_long_op(connection, widgets, _refresh_snapshot_worker,
                         lambda _result: on_ready(), on_error, connection)


class PollTask:
    """One unit of work for PollWorkerHandle.submit() — plain data, no Qt
    machinery, so building one never touches a signal/connection."""
    __slots__ = ("tag", "fn", "args")

    def __init__(self, tag: object, fn: Callable[..., Any], args: tuple):
        self.tag = tag
        self.fn = fn
        self.args = args


class PollResult:
    """Outcome of a PollTask, carried back by PollWorker.resultReady."""
    __slots__ = ("tag", "value", "error")

    def __init__(self, tag: object, value: Any, error: Optional[str]):
        self.tag = tag
        self.value = value
        self.error = error


class PollWorker(QObject):
    """Lives on PollWorkerHandle's persistent QThread. Unlike _LongOpWorker
    (built fresh per op, fine for rare Extract/Redraw calls), exactly one
    PollWorker is created for the whole app lifetime — see PollWorkerHandle
    for why."""

    resultReady = pyqtSignal(object)  # PollResult

    @pyqtSlot(object)
    def run_task(self, task: PollTask) -> None:
        try:
            value = task.fn(*task.args)
        except Exception as e:
            logger.exception("Poll worker task failed")
            self.resultReady.emit(PollResult(task.tag, None, str(e)))
            return
        self.resultReady.emit(PollResult(task.tag, value, None))


class PollWorkerHandle(QObject):
    """One QThread + PollWorker pair, created once (by MainWindow, at
    startup) and kept alive for the whole app lifetime — unlike
    start_long_op's LongOpController, which deliberately builds a fresh
    QThread + _LongOpWorker for every single call.

    Why (found live 2026-08-07, see
    handoff_2026_08_07_worker_thread_gil_deadlock.md): MainWindow's poll
    ticks (_poll/_poll_board_selection) used to go through start_long_op
    too, meaning a new QThread + QObject pair was built and torn down every
    ~400ms-2s. Destroying a Python-subclassed QObject requires the GIL
    (PyQt6/sip checks whether disconnectNotify is overridden in Python on
    every teardown, even when it isn't) while that same teardown holds a
    Qt-internal connection-list mutex — if the UI thread is, at that exact
    moment, itself deep inside an unrelated Qt signal emission that needs
    the same mutex (e.g. a QTreeView relayout), both sides deadlock: the UI
    thread holds the GIL and wants the mutex, the dying worker thread holds
    the mutex and wants the GIL. Reproduced live once; not reliably
    reproducible on demand.

    The fix here is architectural, not a patch on the symptom: build the
    QThread + PollWorker ONCE and never destroy either until the whole app
    quits (see stop()). Every tick after that is just a plain signal emit
    (taskRequested -> the worker's already-connected run_task slot) — an
    emit never calls connectNotify/disconnectNotify, so it never touches
    the GIL-vs-mutex hazard above. dispatch is a persistent, ONE-TIME
    signal connection (in __init__), never reconnected/disconnected per
    call, for the same reason.
    """

    taskRequested = pyqtSignal(object)  # PollTask, auto-queued (worker thread)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = QThread(self)
        self._worker = PollWorker()
        self._worker.moveToThread(self._thread)
        self.taskRequested.connect(self._worker.run_task)
        self._worker.resultReady.connect(self._on_result)
        self._thread.start()
        # tag -> (on_success, on_error, connection); only ever touched on
        # the UI thread (submit() and _on_result() both run there).
        self._pending: Dict[object, tuple] = {}

    def submit(self, connection: Any, fn: Callable[..., Any], args: tuple,
               on_success: Callable[[Any], None], on_error: Callable[[str], None]) -> None:
        """Dispatches fn(*args) onto the persistent worker thread. Mirrors
        start_long_op's connection.long_op_active bookkeeping (set here,
        before the task is even queued, cleared in _on_result — same
        "before the worker starts, after it's done" bracket)."""
        if connection is not None:
            connection.long_op_active = True
        tag = object()
        self._pending[tag] = (on_success, on_error, connection)
        self.taskRequested.emit(PollTask(tag, fn, args))

    def _on_result(self, result: PollResult) -> None:
        on_success, on_error, connection = self._pending.pop(result.tag)
        if connection is not None:
            connection.long_op_active = False
        if result.error is not None:
            on_error(result.error)
        else:
            on_success(result.value)

    def stop(self) -> None:
        """Call once, at app shutdown (MainWindow's aboutToQuit handler) —
        the one point where actually tearing down this QThread is safe
        (nothing else will submit() again afterwards)."""
        self._thread.quit()
        self._thread.wait()
