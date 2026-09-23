# gui/connection.py
"""
BoardConnection — thin lifecycle wrapper around kicadstamp.explore.Board for
the GUI. Unlike the CLI (one run, connect-or-die), this app is meant to sit
open persistently alongside KiCad — KiCad may not be running yet when the
GUI starts, or may close/crash while the GUI stays open — so connecting is a
deliberate, retryable action polled from a QTimer, not something assumed to
succeed once at startup.
"""
import logging
import os
import sys
import threading
from collections import deque
from contextlib import contextmanager
from statistics import median
from typing import Callable, Deque, Dict, Iterator, List, Optional, Tuple

from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.explore import Board, Selected
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

# Board-call latency measurement (plan_2026_09_13_ipc_timeout_and_latency Э2).
# Two independent rolling windows, one per poll tick, because the two measure
# different things and must never be averaged together:
#   * FAST — MainWindow._run_poll_selection's get_selected_items(): the KiCad
#     "response time" (round trip), sampled ~every 400 ms;
#   * SLOW — MainWindow._run_poll's refresh() + snapshot rebuild: the price of a
#     "fat" board read, sampled on the slower reconnect/refresh tick.
# Only median and max are exposed, never the mean (one stuck tick would drag a
# mean; the median is stable). The windows are cleared on every disconnect so
# numbers from a previous KiCad session can never look like the current one.
LATENCY_KIND_FAST = "fast"
LATENCY_KIND_SLOW = "slow"
# Enough samples to be stable but short enough to follow the board as it grows.
LATENCY_WINDOW_SIZE = 40

# Grace period added on top of timeout_ms before giving up on a connect
# attempt — see _connect_with_timeout()'s docstring.
_CONNECT_TIMEOUT_GRACE_S = 2.0

# Board-read probe (plan_2026_09_13_board_access_door Э3): None = DISABLED, and
# that is the default. Diagnostics installs a callable here to count who reads
# the board and from which thread; the `board` getter only loads this name and
# tests it, so a disabled probe costs one attribute read and one branch — no
# stack walk. Production code NEVER assigns it (Э5: no enforcement of any kind).
board_read_probe: Optional[Callable[[], None]] = None


# ── The door's guard: a UI-thread read needs a sign ──────────────────────────
# plan_2026_09_21_board_door_enforcement (Т3/Т4). The getter below is the ONE
# door to the live board (plan_2026_09_13_board_access_door Э2); this is the
# guard that finally stands in it. What it refuses, EXACTLY: reading the board
# from the UI thread without a sign — and nothing else. A board value of None is
# out of its jurisdiction (that is a "is there a connection" check, no socket is
# touched, С4), and so is every other thread (the poll worker and a dock's own
# worker read the board legitimately, С3).
#
# WHO decides what "the UI thread" is: an INJECTED predicate, never this module
# (it does not import Qt, and must not). None — the default — means no thread
# check at all, which is what the CLI, the MCP server and every test that does
# not install one get for free. The GUI installs gui.worker.is_ui_thread from
# its PROCESS ENTRY POINT (kicadstamp/gui_main.py::main), deliberately NOT from
# MainWindow.__init__: ~60 GUI tests build a real MainWindow and read a stand-in
# board out of its real BoardConnection on the main thread, so a predicate
# installed in the constructor would refuse every one of them.
#
# Deliberately NOT gated on the VALUE being a real Board: the jurisdiction is
# the predicate, not the type (decided with Denis 2026-09-21). A type check
# protects nothing extra — a stand-in never appears in production — while it
# would make the guard's own watchdogs (С1–С3) unwritable: kipy's Board needs a
# live client, so a test could not put a real one behind the door.

class UiThreadBoardReadRefused(RuntimeError):
    """Reading the live board from the UI thread without a sign (Т4, "raise" mode).

    The message names the CALLER's file:line, never the guard's own site: the
    point of a refusal is to name the place that has to change. An exception,
    not a None return, on purpose — None already means "there is no connection",
    and the guard must not be mistakable for it (С4)."""


# How a violation is ANSWERED — the two modes, and who picks which:
#
#   "raise" — the test rig's and the harness's answer: loud, and it kills the test.
#             That is what a watchman is for: measuring and pinning a rule.
#   "log"   — the USER's answer: a red (ERROR) Log line naming the caller, and the
#             read is allowed to go on.
#
# Why "log" exists at all, and why it is not the soft option it looks like
# (decided with Denis 2026-09-21, after measuring instead of guessing): an
# exception raised inside a Qt SLOT makes PyQt6 call qFatal() and the process
# dies with a core dump — measured, diagnostics/probe_slot_exception.py, EXIT=134.
# In production "refuse the read" would therefore have meant "kill the session and
# lose whatever was staged", not "report a violation". The old argument for the
# strict option — "a Log line everyone skims is enforcement in name only" — held
# while the offenders were unknown and counted a dozen-plus; after Т5 they are
# zero, so a lone red line cannot drown.
UI_READ_RAISE = "raise"
UI_READ_LOG = "log"

ui_thread_predicate: Optional[Callable[[], bool]] = None
# The pristine, never-armed state. Only set_ui_thread_predicate changes it, and
# that call REQUIRES the mode — see its docstring.
ui_thread_read_refusal: str = UI_READ_LOG

# Per-thread nesting depth of the sign. threading.local() is the point (asked
# for by Denis 2026-09-21): a sign taken on the UI thread must not travel to a
# worker, and one taken inside a worker must not travel back.
_ui_read_sign = threading.local()

# Sites already reported through the Log, so a handler that refuses in a loop
# does not flood it: one WARNING per file:line.
_refused_sites: set = set()
_refused_sites_lock = threading.Lock()

# This file, resolved once — the frame test that keeps the connection's OWN
# reads (is_connected/refresh/disconnect/_rebuild_snapshot) outside the guard's
# jurisdiction. Same trick, same reason as board_read_probe._skip_file.
_OWN_FILE = os.path.abspath(__file__)


def set_ui_thread_predicate(predicate: Optional[Callable[[], bool]], *,
                            refusal: str) -> None:
    """Install (or clear, with None) the "is this the UI thread" predicate, and
    choose — at the SAME site, in writing — how a violation is answered.

    Called by the GUI's process entry point with gui.worker.is_ui_thread (the
    same injection board_call_timing.set_ui_thread_predicate uses: this module
    never imports Qt), and by the test rig / the diagnostics harness with their
    own predicate.

    `refusal` is REQUIRED and has exactly two values — UI_READ_RAISE ("the read
    raises, loud and crashing": the test rig and the harness, whose job is to
    measure and to pin the rule) or UI_READ_LOG ("a red Log line naming the
    caller, and the read goes on": the production GUI). The full reasoning lives
    on the constants above; the short version is that in production an exception
    is not a refusal at all but a core dump, measured 2026-09-21.

    A required keyword on purpose: the mode is a DECISION, and a decision belongs
    where the guard is armed, in writing, not in a default somewhere else. There
    is still deliberately NO switch to turn the guard off "while debugging" (the
    door forbids one: a switch that can be flipped is a guard that is not
    standing)."""
    global ui_thread_predicate, ui_thread_read_refusal
    if refusal not in (UI_READ_RAISE, UI_READ_LOG):
        raise ValueError(
            f"refusal must be {UI_READ_RAISE!r} or {UI_READ_LOG!r}, "
            f"not {refusal!r}")
    ui_thread_predicate = predicate
    ui_thread_read_refusal = refusal


@contextmanager
def ui_thread_board_read(*, reason: str) -> Iterator[None]:
    """Mark a DELIBERATE board read on the UI thread — the sign the guard wants.

    Usage::

        with ui_thread_board_read(reason="hand the adapter to a worker"):
            board = connection.board

    The sign lives at the CALL SITE and is visible while the code is read (Т3).
    ``reason`` is keyword-only and must be non-empty: the whole point is that the
    reader states, in words, why this read belongs on the UI thread — where the
    next person will see it. A flag that can be set globally and forgotten would
    be exactly the decoration the door is written against.

    Nesting is counted per thread, so leaving an inner sign does not clear an
    outer one."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(
            "ui_thread_board_read() requires a non-empty reason= keyword — it is "
            "what makes a deliberate UI-thread read visible at the call site")
    depth = getattr(_ui_read_sign, "depth", 0)
    _ui_read_sign.depth = depth + 1
    try:
        yield
    finally:
        _ui_read_sign.depth = depth


def _report_ui_thread_read(site: str) -> None:
    """ONE Log line per site — the half of the answer every mode shares.

    The LEVEL says which mode we are in, deliberately: in "raise" mode the
    exception is the headline and this line is a warning for the flows that
    swallow tracebacks; in "log" mode this line IS the whole report, so it is an
    ERROR — red in the Log, which is exactly what the user has to notice."""
    with _refused_sites_lock:
        first = site not in _refused_sites
        _refused_sites.add(site)
    if not first:
        return
    message = _("Reading the live board from the UI thread without a sign: "
                "{site}").format(site=site)
    if ui_thread_read_refusal == UI_READ_RAISE:
        logger.warning(message)
    else:
        logger.error(message)


def _ui_thread_read_refused_message(site: str) -> str:
    """The "raise" mode's message, naming the CALLER's site (С1)."""
    return (f"reading the live board from the UI thread without a sign: {site} — "
            "a presence check belongs on connection.is_connected, a real board "
            "read belongs on a worker (gui.worker.start_long_op), and a "
            "deliberate UI-thread read must say so: "
            "with ui_thread_board_read(reason=...)")


def _is_own_read(frame) -> bool:
    """True when the getter's caller is THIS file — the connection reading its
    own board (is_connected, refresh, disconnect, _rebuild_snapshot). Those are
    not consumers of the door but the door's own hinges, and `is_connected` is
    precisely the sanctioned UI-thread presence check (С10)."""
    return os.path.abspath(frame.f_code.co_filename) == _OWN_FILE


# ── The worker's own adapter and the ONE IPC timeout (Э3, ─────────────────────
# plan_2026_09_13_timeout_sweep) ─────────────────────────────────────────────
# A worker building its OWN adapter is deliberate and stays that way (a pynng
# REQ socket is single-owner, so it cannot be shared with the UI's poll ticks).
# What was wrong is the NUMBER those adapters were built with: each of the
# docks' workers carried its own hard-coded 20 s literal, so a user who raised
# the IPC timeout in Settings still had every background redraw/read answering
# on the old kipy default. This helper is the single answer to "how long does a
# worker wait for KiCad": the caller (UI side) reads the value the main
# connection is ACTUALLY running with and carries it in the worker payload; the
# worker reads it back out of that payload. It never reaches into the connection
# from the worker thread (a worker touches nothing the UI owns).
def worker_timeout_ms(source) -> int:
    """The IPC timeout a worker's own board adapter must be built with.

    *source* is either the live connection (caller/UI side — read
    ``connection.timeout_ms``, i.e. what Settings > KiCad has APPLIED, not what
    is merely saved in gui_state.json: the saved draft is written into the
    connection on apply(), see gui/docks/configurator.py) or the worker payload
    dict (worker side — the value the caller decided and carried in).

    Anything missing or unusable — no connection, a test double without the
    attribute, a payload from a caller that predates this — falls back to
    ``DEFAULT_TIMEOUT_MS``, the very default the main connection starts with, so
    a worker can never silently end up on kipy's own constructor default.
    """
    if isinstance(source, dict):
        value = source.get("timeout_ms")
    else:
        value = getattr(source, "timeout_ms", None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_TIMEOUT_MS
    return value


def _connect_with_timeout(timeout_ms: int, config_path=None) -> Board:
    """Board.connect() wrapped with an EXTERNAL timeout, run on a throwaway
    daemon thread.

    Found live 2026-08-11 via py-spy: kipy's own KiCadClient._connect()
    (kipy/client.py) opens its pynng.Req0 socket with `block_on_dial=True` —
    send_timeout/recv_timeout only bound message round-trips AFTER the dial
    succeeds, not the dial itself. Against a stale/dead socket (e.g. a
    previous KiCad instance's leftover pipe) this dial can block forever,
    with no timeout knob exposed by kipy/pynng to bound it from inside.

    That matters here specifically because the caller of connect() is
    gui/main_window.py's _run_poll, dispatched onto the app's single
    long-lived poll QThread (see gui/worker.py's PollWorkerHandle docstring,
    2026-08-07 GIL/Qt-mutex deadlock fix) — if THAT call blocks forever, the
    one poll worker thread every future tick depends on is wedged for the
    rest of the process's life, showing a permanent "Not Connected" even
    once KiCad is reachable again. Running the attempt on a separate daemon
    thread and bounding the wait here means the poll worker always regains
    control.

    Two failure outcomes, both deliberately non-fatal to the caller:
    - a thread stuck in dial() forever is orphaned (Python can't kill a
      thread) but harmless — daemon=True lets the process exit without
      waiting on it, and each retry just risks leaking one more such thread,
      not re-wedging the poll worker;
    - a dial() that SUCCEEDS after the external timeout already fired (late
      success) leaves the built Board — with its fresh kipy.KiCad/pynng.Req0
      inside — with no caller to ever pick it up; that path is closed
      explicitly here (see the gave_up branch in _run), not left for the GC
      to finalize the socket at some unpredictable later point (found live
      2026-08-15, and the exact scenario kicadstamp/kicad/pynng_safety.py's
      bounded close() exists to survive)."""
    result: list = []
    error: list = []
    gave_up = threading.Event()

    def _run():
        try:
            board = Board.connect(timeout_ms=timeout_ms, config_path=config_path)
        except Exception as e:
            error.append(e)
            return
        if gave_up.is_set():
            # The caller already timed out and moved on — nobody will ever
            # pick this Board up. Close it now instead of leaving an open
            # kipy/pynng socket for the GC to find later (found live
            # 2026-08-15 — the exact scenario kicadstamp/kicad/
            # pynng_safety.py's bounded close() exists to survive, but
            # better not to rely on that as the only guard).
            board.adapter.close()
            return
        result.append(board)

    thread = threading.Thread(target=_run, daemon=True, name="BoardConnection.connect")
    thread.start()
    thread.join(timeout=(timeout_ms / 1000.0) + _CONNECT_TIMEOUT_GRACE_S)
    if thread.is_alive():
        gave_up.set()
        raise TimeoutError(
            f"Board.connect() did not return within {timeout_ms}ms "
            f"(+{_CONNECT_TIMEOUT_GRACE_S}s grace) — likely a hung kipy dial() "
            f"on a stale socket"
        )
    if error:
        raise error[0]
    return result[0]


class BoardConnection:
    def __init__(self, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        self.timeout_ms = timeout_ms
        self._board: Optional[Board] = None
        # The CURRENT project's config path (Т5г) — what the poll adapter's
        # override layer is bound to. None until a project is open, which is the
        # pre-store world: the layer stays inert and every read is the board's.
        self._config_path: Optional[str] = None
        # Phase 5.2 — held exclusively by a background long op (Extract/
        # Redraw, see gui/worker.py): while True, MainWindow's polling timers
        # skip their ticks so this kipy REQ socket has exactly one in-flight
        # owner at a time (Extract runs on this shared socket directly).
        self.long_op_active = False
        # Full-board footprint snapshot (Board.select() with no filters),
        # rebuilt ONLY by _rebuild_snapshot() — i.e. on connect()/refresh()
        # (the ~2s poll / manual Refresh in gui/main_window.py._poll), never
        # on the 400ms selection-watch tick. Building it there was the main
        # perf bug of that tick (it re-ran board.select() over every
        # footprint 2-3x a second for no user-visible reason); consumers of
        # the tick build their `selected` lists by ref against this cache
        # instead. _snapshot_version lets those consumers tell "board data
        # changed" apart from "same data, new tick".
        self._snapshot: List[Selected] = []
        self._snapshot_version = 0
        # Rolling latency windows (see LATENCY_KIND_* above), owned here
        # because this is also the ONE place a connection dies — disconnect()
        # clears them (Э2: a previous KiCad session's numbers must never look
        # current).
        self._latency: Dict[str, Deque[float]] = {
            LATENCY_KIND_FAST: deque(maxlen=LATENCY_WINDOW_SIZE),
            LATENCY_KIND_SLOW: deque(maxlen=LATENCY_WINDOW_SIZE),
        }

    @property
    def board(self) -> Optional[Board]:
        """The live board — the ONE door to it (plan_2026_09_13_board_access_door
        Э2), with the guard standing in it since plan_2026_09_21_board_door_
        enforcement (Т4).

        Reads from the UI thread need a sign (``ui_thread_board_read``) UNLESS
        the value is None or the read comes from this file itself; every other
        thread passes untouched (С3). A read that violates the rule is reported
        ONCE per call site, and what happens next is the installed mode's
        business: raise (test rig, harness) or a red Log line and the read goes
        on (production) — see UI_READ_RAISE / UI_READ_LOG above. The setter
        stays (С5): ~20 test assignments of ``connection.board = <fake>`` catch
        real behaviour, and the point of force belongs on the READ, not the
        write.

        The optional probe (module-level ``board_read_probe``) is DISABLED by
        default and does nothing unless diagnostics has installed it — see Э3.
        It runs FIRST on purpose: a refused read is still counted as an attempt,
        so the recorder and the guard agree about what was tried."""
        probe = board_read_probe
        if probe is not None:
            probe()
        value = self._board
        if value is None:
            return None                      # С4 — nothing behind the door
        predicate = ui_thread_predicate
        if predicate is None or not predicate():
            return value                     # CLI/MCP/tests, or a worker (С3)
        if getattr(_ui_read_sign, "depth", 0) > 0:
            return value                     # signed (С2)
        frame = sys._getframe(1)
        if _is_own_read(frame):
            return value                     # the door's own hinges (С10)
        site = f"{frame.f_code.co_filename}:{frame.f_lineno}"
        _report_ui_thread_read(site)         # one line per site, any mode
        if ui_thread_read_refusal == UI_READ_RAISE:
            raise UiThreadBoardReadRefused(  # С1 — names the caller's site
                _ui_thread_read_refused_message(site))
        return value                         # "log" mode: red line, read goes on

    @board.setter
    def board(self, value: Optional[Board]) -> None:
        self._board = value

    @property
    def is_connected(self) -> bool:
        return self.board is not None

    @property
    def snapshot_refresh_supported(self) -> bool:
        """True when a board sits behind the door AND it can rebuild its own
        snapshot (``Board.refresh()``) — i.e. there is fresher data to offer.

        Т5-3 of plan_2026_09_21_board_door_enforcement: gui/worker.py's helper of
        the same name used to answer this by reading ``connection.board`` on the UI
        thread (a legal presence check, but a door read all the same). The door
        answers its own questions now, from its own storage — not a consumer read,
        so no sign is needed and the guard is not involved (see _is_own_read)."""
        return callable(getattr(self._board, "refresh", None))

    @property
    def override_reprojection_supported(self) -> bool:
        """True when the board behind the door can FORGET the Role/Cluster values
        it resolved against the previously bound store — i.e. it can be
        reprojected without reading the board.

        The same shape as snapshot_refresh_supported above, and asked the same
        way (getattr/callable, never a try/except around a whole method), because
        a board that simply cannot do it is a LEGITIMATE case, not an error:
        tests' stand-ins and a bare adapter on a bench have nothing to reproject
        (plan_2026_09_24_reload_store_snapshot §3.4). Getting this wrong is not
        theoretical — an unguarded call raises, DockHub._safe_call swallows it,
        and a `GUI: ... failed` ERROR lands in the Log on every single write."""
        return callable(getattr(self._board, "forget_role_cluster_values", None))

    def _reproject_snapshot_after_store_change(self) -> None:
        """Rebuild the snapshot from the store that was JUST rebound, with no
        board read at all (plan_2026_09_24_reload_store_snapshot §3.2/§3.3).

        The ONE place the connection answers "the store changed", shared by both
        methods that rebind it — ``reload_store`` (another holder wrote) and
        ``set_project_config`` (the project switched) — because this is a
        property of the CLASS rather than of one call path: either way a rebind
        leaves the snapshot describing the PREVIOUS store, and the automatic poll
        tick will not save us (an idle tick is a deliberate no-op once connected,
        gui/main_window.py:983).

        Costs nothing: the board did not change, so there is no round-trip to
        spend — only the resolution of the values in force is dropped (see
        Board.forget_role_cluster_values). Silent when the board cannot do it;
        see override_reprojection_supported.

        Reading ``self._board`` here is the door's own storage (as in
        snapshot_refresh_supported and _store_layer), and the rebuild below reads
        it through the property from THIS file — one of the door's own hinges, so
        no ui_thread_board_read sign belongs here (see _is_own_read)."""
        if not self.override_reprojection_supported:
            return
        self._board.forget_role_cluster_values()
        self._rebuild_snapshot()

    @property
    def snapshot(self) -> List[Selected]:
        return self._snapshot

    @property
    def snapshot_version(self) -> int:
        """Incremented every time the cached snapshot is rebuilt — a cheap,
        stable identity for "the board data changed since my last look",
        usable as a guard key by tick-based consumers (see
        MainWindow._poll_board_selection)."""
        return self._snapshot_version

    def _rebuild_snapshot(self) -> None:
        self._snapshot = self.board.select()
        self._snapshot_version += 1

    # ── Board-call latency (plan_2026_09_13_ipc_timeout_and_latency Э2) ────

    def record_latency(self, kind: str, seconds: Optional[float]) -> None:
        """Append one measured board-call duration (in SECONDS) to the rolling
        window named by `kind` (LATENCY_KIND_FAST/SLOW). Callers measure on the
        worker thread (a poll tick) and report here on the UI thread; a None or
        negative value is ignored so an unmeasured call never skews the window."""
        window = self._latency.get(kind)
        if window is None or seconds is None or seconds < 0:
            return
        window.append(float(seconds))

    def latency_stats(self, kind: str) -> Optional[Tuple[float, float]]:
        """(median, max) of the window in SECONDS, or None while it is empty.

        Median, not mean: one stuck tick (a call that hit the socket timeout)
        would drag a mean far off, while the median stays representative of a
        normal call — the point of the number is to let the user size the IPC
        timeout by data (Э3/Э4)."""
        window = self._latency.get(kind)
        if not window:
            return None
        return median(window), max(window)

    @property
    def has_latency(self) -> bool:
        """True once at least one window holds a sample — the status-bar
        readout stays empty until then (Э3)."""
        return any(self._latency.values())

    def reset_latency(self) -> None:
        """Drop every sample — called from disconnect() so a new session starts
        from a clean slate."""
        for window in self._latency.values():
            window.clear()

    def _store_layer(self):
        """(bound store, ``bind_store``) of the poll adapter, or (None, None).

        The ONE place the connection reaches into the adapter's override layer,
        shared by the two methods that need it: ``set_project_config`` (a project
        switch — needs the binder) and ``reload_store`` (a write elsewhere —
        needs the bound store's path as well). getattr, not a direct attribute
        access: a bare adapter (tests' fakes, a stand-in with no store at all) is
        not an error, it simply has no layer to rebind."""
        board = self._board
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            return None, None
        return (getattr(adapter, "store", None),
                getattr(adapter, "bind_store", None))

    def set_project_config(self, config_path) -> None:
        """Point this connection's POLL ADAPTER at the current profile's store
        (plan_2026_09_18_field_overrides_store Т5г).

        Why here: the adapter is created once per CONNECTION, while the profile —
        and so the override store — is a property of the PROJECT. A project switch
        must therefore reach an adapter that is already alive, and it must not
        reopen the socket: the layer is REBOUND (`bind_store`, the same mechanism
        the MCP server uses for its per-call profiles, Т3/С19), so the kipy client
        and its REQ socket are never touched. Binding ``None`` (no project, or a
        profile whose switch says "board") leaves the layer inert — the pre-store
        behaviour, by construction rather than by luck.

        Without this the GUI's own snapshot would show the BOARD's roles while the
        store holds others: a role noted only in KiCadStamp could not even be
        chosen in a picker (С25), while Pending would faithfully list it."""
        self._config_path = str(config_path) if config_path else None
        _store, binder = self._store_layer()
        if binder is None:
            # Nothing connected yet (the path is used at connect time), or a bare
            # adapter the caller built itself (tests' fakes) — nothing to bind.
            return
        from kicadstamp.adapter_factory import store_for_config
        store, source = (store_for_config(self._config_path)
                         if self._config_path else (None, None))
        binder(store, source=source)
        # The rebind alone leaves the snapshot describing the PREVIOUS profile
        # (plan_2026_09_24_reload_store_snapshot §3.3): same defect as the write
        # path below, so the same one call, under the same capability check.
        self._reproject_snapshot_after_store_change()

    def reload_store(self) -> None:
        """Re-read the poll adapter's store from ITS OWN FILE (Т5г's tail).

        The store is a FILE, and more than one holder keeps a copy of it in
        memory: a pane that records writes through a store of its own and saves,
        while this adapter holds the copy it was bound to when the project
        opened. The file is the truth, so a WRITE by one holder has to be
        announced to the others — DockHub._on_overrides_written is that
        announcement, and this is its stop on the poll adapter.

        The path comes from the bound store itself (``store.path``, the very
        trick gui/fieldstool_window.py's reload_overrides uses), so nothing here
        needs the project root — and the layer is REBOUND (``bind_store``),
        exactly as set_project_config does, so the kipy client and its REQ socket
        are never touched by THIS step.

        **Corrected 2026-09-24 (plan_2026_09_24_reload_store_snapshot §3.2).**
        This paragraph used to end with "never touched includes the board's own
        snapshot: the board did not change, and re-reading it would spend a socket
        round-trip". That is true of ``Board.refresh()`` and was read as if it
        covered the reprojection too — and the sentence was believed instead of
        the code: a role recorded in the fieldstool reached every picker and the
        Components tree only after a manual Refresh, because the automatic tick
        never rebuilds a connected snapshot (gui/main_window.py:983).

        What stays true is what the paragraph is FOR: the reload must not RE-READ
        THE BOARD. The board did not change, and a socket round-trip on it is
        exactly what gui/main_window.py's request_refresh forbids (door rule 3).
        What it must do is drop the values that were resolved against the
        PREVIOUS store and let the snapshot be rebuilt — free, because the
        reprojection re-reads the footprints the board already handed over (§2.3
        of that plan; the price is measured on the layer where the adapter's
        field cache lives, not by counting adapter calls).

        Silently does nothing when there is no bound store (no project open, a
        profile whose switch says "board", a bare adapter a caller built
        itself) — the same shape as set_project_config with no binder. A read
        error is left to the caller's guard (DockHub._safe_call logs it and the
        window stays open); this method invents no error of its own."""
        store, binder = self._store_layer()
        if store is None or binder is None:
            return
        path = getattr(store, "path", None)
        if path is None:
            return
        from kicadstamp.field_overrides import load_field_overrides
        binder(load_field_overrides(str(path)))
        # The file is now in memory: the values in force are the new ones, while
        # the snapshot still holds the resolution against the previous store.
        self._reproject_snapshot_after_store_change()

    def connect(self) -> Optional[str]:
        """Attempts a fresh connection. Returns None on success, or an error
        message on failure — never raises, so a QTimer tick doesn't need a
        try/except at every call site."""
        self.disconnect()  # closes any stale board first — see its docstring
        try:
            board = _connect_with_timeout(self.timeout_ms, self._config_path)
        except TimeoutError as e:
            logger.warning("Connect timed out: %s", e)
            return str(e)
        except Exception as e:
            logger.debug("Connect failed: %s", e)
            return str(e)
        # See KiCadBoardAdapter.check_write_crash_risk()'s docstring (issue
        # #24966) — cheap to call once up front, before any dock has a
        # chance to call select_items()/set_field_value() for the first time.
        board.adapter.check_write_crash_risk()
        self.board = board
        try:
            # Board.connect() already called refresh(), so the snapshot is
            # immediately consistent with the live board.
            self._rebuild_snapshot()
        except Exception as e:
            logger.warning("Snapshot after connect failed, dropping connection: %s", e)
            self.disconnect()
            return str(e)
        logger.info("Connected to KiCad")
        return None

    def refresh(self) -> Optional[str]:
        """Re-fetches the footprint snapshot on an already-connected board.
        On failure (KiCad closed/crashed since connect()), drops the
        connection so the next tick retries connect() from scratch instead
        of repeating the same stale error forever."""
        if self.board is None:
            return "not connected"
        try:
            self.board.refresh()
            self._rebuild_snapshot()
            return None
        except Exception as e:
            logger.warning("Refresh failed, dropping connection: %s", e)
            self.disconnect()
            return str(e)

    def disconnect(self) -> None:
        """Explicitly drops the current board, closing its underlying kipy
        client first (KiCadBoardAdapter.close()) instead of leaving that to
        the garbage collector — see that method's docstring for why (a
        silent native access-violation crash found live 2026-08-04, most
        likely from an unclosed pynng socket finalized at an unpredictable
        point after many reconnects in one long-lived GUI session). Safe to
        call whether or not a board is currently held — every place that
        used to do `self.board = None` directly now goes through this."""
        if self.board is not None:
            self.board.adapter.close()
        self.board = None
        # Э2 — the old session's latency numbers must not survive a drop.
        self.reset_latency()
