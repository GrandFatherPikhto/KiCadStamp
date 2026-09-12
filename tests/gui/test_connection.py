# tests/gui/test_connection.py
"""BoardConnection.disconnect()/connect()/refresh() — 2026-08-04: a board's
underlying kipy client is now explicitly closed on every drop/replace
instead of relying on the garbage collector to eventually finalize its
pynng socket (see KiCadBoardAdapter.close()'s docstring for the native-crash
motivation: a silent Windows access violation with no Python frame on the
crashing thread, found live after many reconnects in one long-lived GUI
session)."""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gui.connection import BoardConnection, _connect_with_timeout


def _fake_board(select_result=None):
    board = MagicMock()
    board.select.return_value = select_result or []
    return board


def _wait_for_thread_exit(name, timeout=5.0):
    """Waits for the named helper thread to leave threading.enumerate().

    _connect_with_timeout() (and the mock that hangs longer than it) runs on a
    throwaway daemon thread, so "the caller returned" never implies "the
    thread finished". The hanging-dial test below releases its mock after its
    assertions and must then also prove that thread actually left — otherwise
    the next edit could quietly turn a bounded mock back into a thread parked
    for the rest of the run (plan_2026_09_12_parked_test_threads.md)."""
    deadline = time.monotonic() + timeout
    while True:
        if not any(t.name == name for t in threading.enumerate()):
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)


def test_disconnect_closes_the_adapter_and_clears_board():
    connection = BoardConnection()
    board = _fake_board()
    connection.board = board

    connection.disconnect()

    board.adapter.close.assert_called_once()
    assert connection.board is None


def test_disconnect_is_a_noop_when_no_board():
    connection = BoardConnection()

    connection.disconnect()  # must not raise

    assert connection.board is None


def test_connect_closes_a_stale_board_before_replacing_it():
    connection = BoardConnection()
    stale = _fake_board()
    connection.board = stale
    new_board = _fake_board()

    with patch("gui.connection.Board.connect", return_value=new_board):
        error = connection.connect()

    assert error is None
    stale.adapter.close.assert_called_once()
    assert connection.board is new_board


def test_refresh_failure_closes_the_adapter_and_drops_the_board():
    connection = BoardConnection()
    board = _fake_board()
    board.refresh.side_effect = RuntimeError("kicad gone")
    connection.board = board

    error = connection.refresh()

    assert error == "kicad gone"
    board.adapter.close.assert_called_once()
    assert connection.board is None


def test_connect_snapshot_failure_closes_the_adapter_and_drops_the_board():
    connection = BoardConnection()
    new_board = _fake_board()
    new_board.select.side_effect = RuntimeError("select failed")

    with patch("gui.connection.Board.connect", return_value=new_board):
        error = connection.connect()

    assert error == "select failed"
    new_board.adapter.close.assert_called_once()
    assert connection.board is None


def test_connect_times_out_instead_of_hanging_forever():
    """2026-08-11: found live via py-spy — kipy's own pynng dial (inside
    Board.connect(), block_on_dial=True) can hang forever on a stale socket,
    with no timeout knob of its own. BoardConnection.connect() must return an
    error promptly instead of blocking its caller (the single long-lived
    poll QThread — see gui/worker.py) forever."""
    connection = BoardConnection(timeout_ms=50)
    release = threading.Event()
    dial_returned = threading.Event()
    late_board = _fake_board()

    def _hangs_forever(timeout_ms):
        # Simulates kipy's block_on_dial=True never returning — until this
        # test releases it BELOW, i.e. it outlives the caller's give-up (that
        # is the whole point) but not the test run.
        release.wait()
        dial_returned.set()
        # Returning a Board keeps the internal daemon thread quiet once
        # released: it takes _connect_with_timeout()'s gave_up branch and
        # closes this late Board (the path its own test pins down below).
        return late_board

    with patch("gui.connection.Board.connect", side_effect=_hangs_forever):
        start = time.monotonic()
        error = connection.connect()
        elapsed = time.monotonic() - start

        assert error is not None
        assert "did not return" in error
        # Bounded by timeout_ms + grace, nowhere near "forever".
        assert elapsed < 5.0
        assert connection.board is None
        # The mocked dial is STILL parked here, so the three assertions above
        # were made by a connect() that gave up entirely on its own.
        assert dial_returned.is_set() is False
        assert late_board.adapter.close.call_count == 0

        # 2026-09-12 (plan_2026_09_12_parked_test_threads.md): leaving the mock
        # parked forever kept a live "BoardConnection.connect" daemon thread
        # next to Qt for the whole run. Release it now that the proof is on
        # record — nothing above this line is weakened or dropped.
        release.set()
        assert dial_returned.wait(5.0)

    # ...and the orphaned thread must actually finish, not stay parked. The
    # released dial returned a Board nobody will ever pick up, so _run()'s
    # late-success branch must close it (same contract as the test below).
    assert _wait_for_thread_exit("BoardConnection.connect")
    assert late_board.adapter.close.call_count == 1


def test_late_success_after_timeout_closes_the_orphaned_board():
    """2026-08-15: a Board.connect() that SUCCEEDS after the external timeout
    already fired used to be silently dropped — nobody ever called
    .adapter.close() on it, leaving its fresh kipy/pynng socket for the GC to
    finalize at an unpredictable point. Found while reading the 2026-08-15
    py-spy dump: this late-success path is exactly how "stale" sockets pile up
    for the GC to trip over (the scenario kicadstamp/kicad/pynng_safety.py's
    bounded close() survives — but the leak itself is closed here too)."""
    release = threading.Event()
    closed = threading.Event()
    late_board = _fake_board()
    late_board.adapter.close.side_effect = lambda: closed.set()

    def _slow_success(timeout_ms):
        # Returns only after the caller already gave up on us.
        release.wait()
        return late_board

    with patch("gui.connection.Board.connect", side_effect=_slow_success):
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            _connect_with_timeout(50)
        assert time.monotonic() - start < 5.0
        # Unblock the background thread now that the caller has moved on.
        release.set()
        # The orphaned Board must be closed explicitly, not leaked to GC.
        assert closed.wait(5.0)

    late_board.adapter.close.assert_called_once()
