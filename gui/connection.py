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
import threading
from typing import List, Optional

from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.explore import Board, Selected

logger = logging.getLogger(__name__)

# Grace period added on top of timeout_ms before giving up on a connect
# attempt — see _connect_with_timeout()'s docstring.
_CONNECT_TIMEOUT_GRACE_S = 2.0


def _connect_with_timeout(timeout_ms: int) -> Board:
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
            board = Board.connect(timeout_ms=timeout_ms)
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
        self.board: Optional[Board] = None
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

    @property
    def is_connected(self) -> bool:
        return self.board is not None

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

    def connect(self) -> Optional[str]:
        """Attempts a fresh connection. Returns None on success, or an error
        message on failure — never raises, so a QTimer tick doesn't need a
        try/except at every call site."""
        self.disconnect()  # closes any stale board first — see its docstring
        try:
            board = _connect_with_timeout(self.timeout_ms)
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
