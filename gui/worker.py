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

Busy indicator (Э1, plan_2026_09_12_busy_indicator): every operation the USER
started is now visible on screen. LongOpController reports through the
module-level set_busy_reporter hook (see its block below) — the seam is
LongOpController alone, never connection.long_op_active, because the ~400ms
selection-poll tick raises that flag too and an indicator keyed on it would
blink on an idle GUI.

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
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, List, Optional

from PyQt6 import sip
from PyQt6.QtCore import QObject, QThread, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication

from kicadstamp.i18n import _

logger = logging.getLogger(__name__)


def is_ui_thread() -> bool:
    """True when the current thread is the GUI (UI) thread — the predicate the
    diagnostics recorder is given when the Settings > Diagnostics switch is ON
    (plan_2026_09_13_diagnostics_switch Э3).

    The recorder (kicadstamp/diagnostics/board_call_timing.py) never imports Qt,
    so the answer comes from here, injected from outside: recording the caller's
    frame is worth its cost for the ~0.4% of calls made on the UI thread (those
    are the ones that can freeze the window), and not for the other 99.6%.

    Deliberately NOT "the thread is named MainThread": in the MCP server process
    and in the CLI everything legitimately runs on MainThread, so a name check
    would call every one of their calls a violation. Comparing threads also
    keeps this honest when the GUI is embedded (tests, fieldstool)."""
    app = QApplication.instance()
    if app is None:
        return False
    return QThread.currentThread() == app.thread()


# ── "The call did not finish in time" recommendation (Э4б, plan_2026_09_13_ ──
# ipc_timeout_and_latency) ──────────────────────────────────────────────────
#
# A failed board call whose duration is at least this fraction of the configured
# IPC timeout is treated as a TIMEOUT — the reply probably exists, the machine
# was just busy/slow. Decided by TIME only, never by parsing the error text:
# kipy's KiCadClient.send() raises ConnectionError(...) `from None`, which
# severs both __cause__ and __context__, so pynng's Timeout type is not
# recoverable and the string carries no stable marker (verified 2026-09-13 by
# reading kipy's source). A text match would break silently on another pynng
# version/locale.
TIMEOUT_RECOMMENDATION_RATIO = 0.9


def timeout_recommendation(duration_s: Optional[float],
                           timeout_ms: Optional[int]) -> Optional[str]:
    """A Log line recommending a higher IPC timeout, or None when the failed
    call finished well within the timeout (a genuine error — e.g. KiCad was
    closed — must NOT get a timeout hint, Э5.9)."""
    if duration_s is None or not timeout_ms or timeout_ms <= 0:
        return None
    if duration_s < (timeout_ms / 1000.0) * TIMEOUT_RECOMMENDATION_RATIO:
        return None
    return _("The board call did not answer within about {waited} ms while the "
             "IPC timeout is {timeout} ms — it looks like it timed out. The "
             "reply probably exists; the machine was just busy or slow. If this "
             "repeats, raise the IPC timeout in Settings > KiCad (it applies on "
             "the next connection).").format(
        waited=int(duration_s * 1000), timeout=int(timeout_ms))

# ── Busy indicator (Э1, plan_2026_09_12_busy_indicator) ──────────────────────
#
# A long operation the user started (Extract/Redraw/re-read/refresh/…) must be
# visible while it runs — until 2026-09-12 nothing on screen changed during one
# and the trigger button kept looking live. The report travels over this
# module-level hook instead of a reference to MainWindow: worker.py must not
# start knowing about the window, and a test replaces the hook in one line.
#
# What it is deliberately NOT hung on: connection.long_op_active. That flag is
# ALSO raised by the ~400ms selection-poll tick (PollWorkerHandle.submit), so an
# indicator keyed on it would blink several times a second on an idle GUI (P.1
# of the plan). The background poll stays invisible and that is correct — the
# user did not start it.
#
# Reporter contract (always called on the UI thread):
#   reporter(text) — an operation STARTED; `text` is a short, ALREADY
#                    TRANSLATED word naming it, or GENERIC_BUSY_TEXT when the
#                    operation did not name itself (then the reporter uses its
#                    own generic wording);
#   reporter(None) — the operation FINISHED, on success and on error alike
#                    (_release is the single tail of both paths).
GENERIC_BUSY_TEXT = ""

_busy_reporter: Optional[Callable[[Optional[str]], None]] = None


def set_busy_reporter(reporter: Optional[Callable[[Optional[str]], None]]) -> None:
    """Install (or, with None, remove) THE busy reporter — MainWindow calls this
    once at construction, with its own ``_set_busy``.

    Held by plain reference: the module slot is single and the only long-lived
    window installs it, so the next window that installs one simply replaces it
    (the GUI test suite builds many MainWindows in one process). A reporter that
    raises is logged and swallowed by :func:`_notify_busy`, so a window torn
    down mid-operation can never fail the operation itself."""
    global _busy_reporter
    _busy_reporter = reporter


def _notify_busy(text: Optional[str]) -> None:
    """Hand the current busy state to the registered reporter (a no-op when none
    is installed). Never raises: the indicator is cosmetic and must not be able
    to fail a board operation."""
    reporter = _busy_reporter
    if reporter is None:
        return
    try:
        reporter(text)
    except Exception:  # noqa: BLE001 — a broken indicator must never break an op
        logger.exception("Busy reporter failed")


def socket_busy(connection: Any) -> bool:
    """True while another owner holds the shared kipy REQ socket.

    The ~400ms selection-poll tick raises `connection.long_op_active` for its
    own in-flight request (PollWorkerHandle.submit) and every start_long_op
    operation raises it for the whole run, so a read that goes straight to the
    SHARED adapter must refuse while this is True instead of interleaving two
    REQ transactions on one socket — the live symptom being "Error receiving
    reply from KiCad: Operation canceled".

    A missing connection is NOT busy: the callers check for an absent board on
    their own path (and say so in the Log). The one-line guard
    role_cluster_tree.py's node click and fieldstool_window._push_selection_to_
    board already carry; this is only its single definition for the reads
    plan_2026_09_12_ui_thread_board_reads weeds out."""
    return bool(getattr(connection, "long_op_active", False))


class _LongOpWorker(QObject):
    """Runs fn(*args) on the worker thread and reports the outcome via
    signals. succeeded() carries the return value; failed() carries a
    human-readable message. Every exception is caught and routed to failed()
    so a worker bug can never silently kill the thread or leave the socket
    held."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[..., Any], args: tuple,
                 connection: Any = None, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        # Only used to read the configured IPC timeout (connection.timeout_ms)
        # for the Э4б recommendation — the worker never calls the board itself.
        self._connection = connection

    def _with_timeout_hint(self, message: str, duration_s: float) -> str:
        hint = timeout_recommendation(
            duration_s, getattr(self._connection, "timeout_ms", None))
        return f"{message}\n{hint}" if hint else message

    @pyqtSlot()
    def run(self) -> None:
        # Э2/Э4б — time the call on the worker thread; the duration feeds the
        # timeout check on the failure path below.
        start = perf_counter()
        try:
            result = self._fn(*self._args)
        except Exception as e:
            duration_s = perf_counter() - start
            # A KiCad IPC failure is a board STATE problem (KiCad busy with an
            # unfinished tool, a dropped connection), not a bug: log the human
            # explanation and hand THAT to the caller, keeping the traceback at
            # DEBUG only (plan_2026_09_11_no_modals_and_busy_kicad X.2.2 — every
            # GUI long op, Apply/Extract/Redraw alike, funnels through here, so
            # this one branch closes the "raw stack in the Log for a busy
            # KiCad" gap for all of them). Genuinely unexpected exceptions keep
            # the traceback as before.
            from kipy.errors import ApiError
            if isinstance(e, ApiError):
                # Lazy, like cli_common.api_error_message: only an actual IPC
                # error pays for the import.
                from kicadstamp.cli_common import api_error_message
                message = self._with_timeout_hint(api_error_message(e), duration_s)
                logger.error(message)
                logger.debug("Long operation failed with a KiCad IPC error",
                             exc_info=True)
                self.failed.emit(message)
                return
            logger.exception("Long operation failed")
            self.failed.emit(self._with_timeout_hint(str(e), duration_s))
            return
        self.succeeded.emit(result)


class LongOpController(QObject):
    """Owns the QThread + worker for one long operation. Must be created on
    the UI thread. start() acquires the shared socket (connection.
    long_op_active = True) and disables the guard widgets BEFORE the thread
    starts; the completion handlers run back on the UI thread (queued
    connections, since the worker lives in a different thread) and release
    the socket exactly once.

    It is also the ONE place that reports "the user's operation is running" to
    the GUI (see the busy-indicator block above): ``busy_text`` is a short,
    already-translated word naming the operation, or None for the generic
    wording, and the wait cursor is pushed/popped in the same bracket."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    # Fires once the worker QThread's event loop has ACTUALLY exited (not
    # just once a result arrived) — the only point at which it is safe to
    # drop the last reference to this controller. See start_long_op's
    # docstring/module-level _ACTIVE_CONTROLLERS for why this matters.
    thread_stopped = pyqtSignal()

    def __init__(self, connection: Any, widgets: Iterable[Any], parent=None,
                 busy_text: Optional[str] = None):
        super().__init__(parent)
        self._connection = connection
        self._widgets: List[Any] = list(widgets)
        self._busy_text = busy_text
        self._thread: Optional[QThread] = None
        self._worker: Optional[_LongOpWorker] = None
        self._released = False
        self._prior_enabled: Dict[Any, bool] = {}
        # True while THIS op pushed the application-wide override cursor — Qt
        # restores override cursors by call count, so the pop is made
        # conditional on the matching push rather than on QApplication existing.
        self._cursor_set = False

    def start(self, fn: Callable[..., Any], *args) -> None:
        self._acquire()
        self._thread = QThread(self)
        self._worker = _LongOpWorker(fn, args, connection=self._connection)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._on_worker_succeeded)
        self._worker.failed.connect(self._on_worker_failed)
        # The worker is destroyed by DROPPING THE LAST REFERENCE on the UI
        # thread (self._worker = None in _on_thread_finished) — never by
        # `finished.connect(self._worker.deleteLater)`, which is what froze the
        # GUI for good on 2026-09-18. `finished` is emitted INSIDE the worker
        # thread (Qt's QThreadPrivate::finish), so that connect() is a DIRECT
        # call: deleteLater posts a DeferredDelete event into the WORKER's own
        # queue, and `finish` drains exactly that queue right after — so the
        # Python-subclassed QObject is destroyed on a foreign thread, where sip
        # takes the GIL. In the live dump
        # (diagnostics/freeze_2026_09_18_boundary_dialog_pyspy.txt) the UI thread
        # held the GIL and waited for QBasicMutex::lockInternal while that
        # thread held the signal/slot mutex and waited for the GIL; neither
        # side could move. Full plan: plan_2026_09_18_worker_delete_deadlock.
        #
        # Connected first, before the two deleteLater lines below, out of
        # tidiness — NOT because the order carries the guarantee. Measured
        # (diagnostics/claude_probe_finished_slot_order_2026_09_18.py, both
        # orders): the worker is destroyed before its QThread either way,
        # because dropping the last reference destroys it IMMEDIATELY by
        # refcount while deleteLater only POSTS a DeferredDelete event. Say it
        # plainly so nobody treats this line's position as load-bearing.
        self._thread.finished.connect(self._on_thread_finished)
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
        self._set_wait_cursor(True)
        _notify_busy(GENERIC_BUSY_TEXT if self._busy_text is None
                     else self._busy_text)

    def _release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._connection is not None:
            self._connection.long_op_active = False
        for w, enabled in self._prior_enabled.items():
            w.setEnabled(enabled)
        self._set_wait_cursor(False)
        _notify_busy(None)
        if self._thread is not None:
            self._thread.quit()

    def _set_wait_cursor(self, wait: bool) -> None:
        """Push/pop the application-wide wait cursor for the duration of the
        op (same two calls gui/ui_utils.busy uses for its synchronous
        operations). The UI thread stays free while the op runs on the worker,
        so the cursor is what the user sees immediately; a bare unit test with
        no QApplication simply skips it."""
        app = QApplication.instance()
        if app is None:
            return
        if wait:
            if not self._cursor_set:
                app.setOverrideCursor(Qt.CursorShape.WaitCursor)
                self._cursor_set = True
        elif self._cursor_set:
            app.restoreOverrideCursor()
            self._cursor_set = False

    @pyqtSlot(object)
    def _on_worker_succeeded(self, result: Any) -> None:
        self._release()
        self.finished.emit(result)

    @pyqtSlot(str)
    def _on_worker_failed(self, message: str) -> None:
        self._release()
        self.failed.emit(message)

    @pyqtSlot()
    def _on_thread_finished(self) -> None:
        """THE one place the per-op worker is destroyed — and it runs on the UI
        thread, which is the whole point (see the comment in :meth:`start`).

        Deliberately NOT `self._worker.deleteLater()`: Qt posts a DeferredDelete
        event into the OBJECT'S thread, and this object's thread is the one that
        has just finished, so nothing would ever drain it — measured in
        diagnostics/probe_worker_move_back.py (case B3: the object is never
        destroyed), and if the event did arrive before Qt finished tearing the
        thread down, the destruction would land back in the worker thread — the
        very deadlock being fixed.

        Deliberately NOT in _release() either: _release() runs while the
        `succeeded`/`failed` payload is still being delivered, and `run()` — the
        worker's own slot — can be on the worker thread's stack at that moment
        (the emit is its last statement). `finished` is the first point at which
        the object is provably idle, and it is also the point the caller's
        keep-alive registry already treats as "the thread has genuinely
        stopped"."""
        self._worker = None


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


def start_long_op(connection, widgets, fn, on_success, on_error, *args,
                  busy_text: Optional[str] = None):
    """Convenience factory: builds a LongOpController, wires its finished/
    failed signals to on_success/on_error (both called on the UI thread),
    starts the op, and returns the controller (callers may keep their own
    reference too, e.g. to inspect state, but do not need to for correctness
    — see _ACTIVE_CONTROLLERS above).

    ``busy_text`` (keyword-only, so the optional parameter breaks no existing
    call and cannot collide with ``*args``) is the short, already-translated
    word the status-bar indicator shows while this op runs — e.g.
    ``_("placing")``. Omit it for the generic wording."""
    controller = LongOpController(connection, widgets, busy_text=busy_text)
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
    ``ImprintFormWidget._live_snapshot``'s "tests/fallback" and
    ``RecordImprintDialog._live_selection``).

    Т5-3 of plan_2026_09_21_board_door_enforcement: the REAL connection answers
    this about itself now (``BoardConnection.snapshot_refresh_supported``), because
    reading ``connection.board`` here was a door read on the UI thread — one of
    the two legal presence checks the armed run named. The getattr fallback stays,
    and it is checked with isinstance(bool) rather than "is not None" on purpose:
    a stand-in connection (a Mock) answers ANY attribute name with a Mock, which
    would otherwise be taken for the real answer."""
    own = getattr(connection, "snapshot_refresh_supported", None)
    if isinstance(own, bool):
        return own
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
                          on_error: Callable[[str], Any], *,
                          busy_text: Optional[str] = None,
                          on_refused: Optional[Callable[[], Any]] = None) -> Any:
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
        REQ socket is exactly the corruption this module exists to prevent), and
        ``on_refused`` (when given) runs so the caller can tell the two ``None``
        outcomes apart.

    ``on_refused`` is the ONLY signal that separates "the continuation already
    ran" from "the request was refused" — both return ``None``, deliberately
    (the refusal contract above is pinned by
    ``test_refresh_snapshot_then_refuses_while_another_long_op_holds_the_socket``).
    A caller that must react to a refusal passes it; a caller that does not care
    passes nothing and keeps the previous behaviour byte for byte. It runs
    synchronously on the calling (UI) thread and must be cheap and must NOT
    touch the adapter — it only schedules
    (:func:`refresh_snapshot_then_with_retry`) or reports.

    A guard widget does NOT make that click impossible: it disables the
    caller's own button for the caller's own operation only, while the ~400 ms
    selection-poll tick holds the shared socket ~16 % of the time (measured
    2026-09-14 from ``diagnostics/board_timing_806966.jsonl``). Clicks that must
    survive a busy socket go through :func:`refresh_snapshot_then_with_retry`,
    never through a guard alone.

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
        if on_refused is not None:
            on_refused()
        return None
    return start_long_op(connection, widgets, _refresh_snapshot_worker,
                         lambda _result: on_ready(), on_error, connection,
                         busy_text=busy_text)


SNAPSHOT_REFRESH_RETRY_DELAY_MS = 120
"""How long after a refused snapshot rebuild the single retry waits. The
selection-poll tick that usually holds the shared socket lives 67 ms at the
median and 77.5 ms at p90 (measured 2026-09-14,
``plan_2026_09_14_snapshot_refusal_dead_end`` P.0), so ~120 ms lands in a free
socket almost always while staying invisible to the user."""


def qt_object_gone(obj: Any) -> bool:
    """True when the Qt object behind `obj` no longer exists (a plain Python
    stand-in — a test double that is not a QObject — has no C++ side to
    outlive, so it counts as alive).

    Two callers, one question. :func:`refresh_snapshot_then_with_retry`'s retry
    needs it because the ``QTimer`` outlives the widget: running the
    continuation against a deleted dock/dialog would raise ``RuntimeError`` in
    the middle of an event-loop callback. `SplitterSizeKeeper.capture()`
    (gui/docks/_common.py) needs it for the harsher version of the same thing —
    its ``eventFilter`` runs from INSIDE Qt's C++ dispatch while the collector
    dismantles the keeper <-> splitter cycle, so a raise there is not an error
    in the log but the whole process aborting (measured 2026-09-17; the second
    instance of the 2026-09-13 ``_DialogSizeSaver`` defect). Public on purpose:
    one definition, one answer, shared."""
    try:
        return bool(sip.isdeleted(obj))
    except (TypeError, RuntimeError):
        return False


def refresh_snapshot_then_with_retry(
        connection: Any, widgets: Iterable[Any],
        on_ready: Callable[[], Any], on_error: Callable[[str], Any], *,
        busy_text: Optional[str] = None, owner: Any = None,
        retry_delay_ms: int = SNAPSHOT_REFRESH_RETRY_DELAY_MS,
        on_cached: Optional[Callable[[], Any]] = None,
        on_still_busy: Optional[Callable[[], Any]] = None) -> Any:
    """``refresh_snapshot_then`` plus the one thing a refused click needs: a
    SINGLE deferred retry, then the caller's choice of a cached continuation or
    a refusal.

    A refused click used to vanish (``plan_2026_09_14_snapshot_refusal_
    dead_end``): the ~400 ms selection-poll tick holds the shared socket ~16 %
    of the time, so roughly every sixth press met a refusal that
    :func:`refresh_snapshot_then` only logged. This wrapper makes the press
    survive:

    * attempt 1 goes through :func:`refresh_snapshot_then`; a refusal schedules
      EXACTLY ONE retry with ``QTimer.singleShot`` (``retry_delay_ms``) — the UI
      thread is never blocked, there is no wait loop and no ``processEvents()``;
    * attempt 2 is the LAST one; if it is refused too, ``_exhausted`` runs
      instead of a third attempt (two or more turn a rare silence into a rare
      hang — the class this module just finished clearing);
    * just before attempt 2 the retry checks that the window/dock is still
      alive (``owner`` and every guard widget): a deleted widget means the user
      closed the dialog during the delay, so the retry touches nothing and
      raises nothing.

    The exhausted outcome is the caller's choice, because staleness costs
    different things:

    * ``on_still_busy`` given (the imprint pivots, which read POSITIONS out
      of the snapshot): it runs and ``on_ready`` does NOT — a pivot computed
      from a stale snapshot is wrong geometry, not a slightly old list, so the
      operation is refused and the caller tells the user;
    * otherwise (the tree dialogs, Extract, fieldstool — list NAMES and cluster
      MEMBERSHIP): ``on_cached`` (when given) runs first so the caller can
      report the fallback, then ``on_ready()`` runs on the cached snapshot and
      the button works.

    No message is drawn here — the caller owns it (``show_message`` of
    ``gui.docks._common``: WARN for a cache fallback, ERROR for a refusal;
    plan §Э2-M). The diagnostic ``logger.warning`` inside
    :func:`refresh_snapshot_then` stays.

    Returns the first attempt's controller, or ``None`` (a synchronous
    fallback, or a refusal whose single retry is now armed)."""
    widget_list = tuple(widgets)

    def _gone() -> bool:
        candidates = [owner] if owner is not None else []
        candidates.extend(widget_list)
        return any(qt_object_gone(c) for c in candidates)

    def _exhausted() -> None:
        if on_still_busy is not None:
            on_still_busy()
            return
        if on_cached is not None:
            on_cached()
        on_ready()

    def _retry() -> None:
        if _gone():
            return
        refresh_snapshot_then(connection, widget_list, on_ready, on_error,
                              busy_text=busy_text, on_refused=_exhausted)

    def _defer_retry() -> None:
        QTimer.singleShot(retry_delay_ms, _retry)

    return refresh_snapshot_then(connection, widget_list, on_ready, on_error,
                                 busy_text=busy_text, on_refused=_defer_retry)


def defer_while_socket_busy(
        connection: Any, widgets: Iterable[Any],
        proceed: Callable[[], Any], on_still_busy: Callable[[], Any], *,
        owner: Any = None,
        retry_delay_ms: int = SNAPSHOT_REFRESH_RETRY_DELAY_MS) -> bool:
    """Run ``proceed()`` NOW when the shared kipy REQ socket is free, or arm EXACTLY ONE
    deferred retry when another owner holds it — the same shape
    :func:`refresh_snapshot_then_with_retry` gives a refused snapshot rebuild, for a
    caller whose continuation is a worker of its own (a live read under
    :func:`start_long_op`) instead of the snapshot rebuild.

    Why it exists (measured 2026-09-14, ``diagnostics/board_timing_806966.jsonl``): the
    ~400 ms selection-poll tick holds that socket 16.4 % of the time, so a live read that
    only REFUSES on a busy socket vanishes roughly every sixth press
    (``plan_2026_09_14_ui_thread_offenders`` §Э2 — the Instantiate-from-selection base).
    ``retry_delay_ms`` (120 ms) is longer than the tick's own p90 (77.5 ms), so the single
    retry almost always meets a free socket — and it is deferred through
    ``QTimer.singleShot``: no wait loop, no ``processEvents()``, the UI thread is never
    blocked for a millisecond.

    ONE retry, not a queue: a second refusal is a real refusal, and the caller owns what
    happens then (``on_still_busy`` — a message, a refusal, a fallback). Two or more
    retries turn a rare silence into a rare hang, the class this module just cleared.

    Just before the retry ``owner`` and every guard widget are checked for deletion
    (``sip.isdeleted``): the ``QTimer`` outlives the widget, so a dialog the user closed
    during the delay would otherwise be written to from an event-loop callback
    (``RuntimeError`` — the same class as the live ``_DialogSizeSaver`` ``abort()`` of
    2026-09-13).

    ``proceed()`` must be cheap and must NOT touch the adapter itself — it only STARTS a
    worker, which is what raises the token; that is why the free-socket check belongs
    here, on the UI thread, and not inside the worker.

    SECOND legitimate shape (2026-09-22, Т2-4б of plan_2026_09_22_live_adapter_class):
    a SHORT synchronous continuation that guards itself instead of starting a worker —
    TreesDock's ``_rehang`` runs the whole re-hang inline and its own ``socket_busy``
    check refuses one line before the read touches the shared adapter. The helper then
    contributes the DEFERRAL and the liveness check, and the "must not touch the
    adapter" half of the rule above is met by the continuation's OWN guard. It is the
    second half of that rule, never a licence to skip it: a continuation that neither
    starts a worker nor checks the socket itself must not be passed here.

    Returns True when ``proceed()`` already ran, False when the retry was armed or
    ``on_still_busy()`` already ran (i.e. the deadline was skipped)."""
    if not socket_busy(connection):
        proceed()
        return True
    widget_list = tuple(widgets)

    def _gone() -> bool:
        candidates = [owner] if owner is not None else []
        candidates.extend(widget_list)
        return any(qt_object_gone(c) for c in candidates)

    def _retry() -> None:
        if _gone():
            return
        if socket_busy(connection):
            on_still_busy()
            return
        proceed()

    QTimer.singleShot(retry_delay_ms, _retry)
    return False


class PollTask:
    """One unit of work for PollWorkerHandle.submit() — plain data, no Qt
    machinery, so building one never touches a signal/connection."""
    __slots__ = ("tag", "fn", "args", "connection")

    def __init__(self, tag: object, fn: Callable[..., Any], args: tuple,
                 connection: Any = None):
        self.tag = tag
        self.fn = fn
        self.args = args
        # Carried only so the worker thread can read the configured IPC timeout
        # for the Э4б recommendation; it never calls the board through it.
        self.connection = connection


class PollResult:
    """Outcome of a PollTask, carried back by PollWorker.resultReady."""
    __slots__ = ("tag", "value", "error", "duration_s")

    def __init__(self, tag: object, value: Any, error: Optional[str],
                 duration_s: Optional[float] = None):
        self.tag = tag
        self.value = value
        self.error = error
        # Wall time the task's fn took, measured on the worker thread (Э2/Э4б).
        self.duration_s = duration_s


class PollWorker(QObject):
    """Lives on PollWorkerHandle's persistent QThread. Unlike _LongOpWorker
    (built fresh per op, fine for rare Extract/Redraw calls), exactly one
    PollWorker is created for the whole app lifetime — see PollWorkerHandle
    for why."""

    resultReady = pyqtSignal(object)  # PollResult

    @pyqtSlot(object)
    def run_task(self, task: PollTask) -> None:
        # Э2 — the tick's own duration is measured here (on the worker thread)
        # and carried back in the PollResult; the timeout check happens in
        # PollWorkerHandle._on_result, the poll ticks' own error path.
        start = perf_counter()
        try:
            value = task.fn(*task.args)
        except Exception as e:
            duration_s = perf_counter() - start
            # Same board-state rule as _LongOpWorker.run (see the X.2.2 comment
            # there): a KiCad IPC failure gets the human text at ERROR, the
            # traceback only at DEBUG.
            from kipy.errors import ApiError
            if isinstance(e, ApiError):
                from kicadstamp.cli_common import api_error_message
                message = api_error_message(e)
                logger.error(message)
                logger.debug("Poll worker task failed with a KiCad IPC error",
                             exc_info=True)
                self.resultReady.emit(PollResult(task.tag, None, message, duration_s))
                return
            logger.exception("Poll worker task failed")
            self.resultReady.emit(PollResult(task.tag, None, str(e), duration_s))
            return
        self.resultReady.emit(
            PollResult(task.tag, value, None, perf_counter() - start))


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
        # NO `self._thread.finished.connect(self._worker.deleteLater)` here, and
        # none anywhere else: a worker that lives in another thread must not be
        # destroyed from that thread either (the same GIL-vs-Qt-mutex hazard the
        # per-op worker had — see LongOpController.start). This worker is built
        # WITH the handle and dies WITH it, on the main thread, which is why the
        # persistent poll thread needs no teardown of its own.
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
        self.taskRequested.emit(PollTask(tag, fn, args, connection))

    def _on_result(self, result: PollResult) -> None:
        on_success, on_error, connection = self._pending.pop(result.tag)
        if connection is not None:
            connection.long_op_active = False
        if result.error is not None:
            # Э4б — a poll task that RAISED is timed here, so a timeout gets the
            # same "raise the timeout" recommendation as a long op. (The two
            # poll ticks themselves catch their errors and RETURN them, so their
            # recommendation lands in the MainWindow finish handlers instead —
            # see gui/main_window.py; this is the generic safety net.)
            message = result.error
            hint = timeout_recommendation(
                result.duration_s, getattr(connection, "timeout_ms", None))
            if hint:
                message = f"{message}\n{hint}"
            on_error(message)
        else:
            on_success(result.value)

    def stop(self) -> None:
        """Call once, at app shutdown (MainWindow's aboutToQuit handler) —
        the one point where actually tearing down this QThread is safe
        (nothing else will submit() again afterwards)."""
        self._thread.quit()
        self._thread.wait()
