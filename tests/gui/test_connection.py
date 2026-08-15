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

    def _hangs_forever(timeout_ms):
        # Simulates kipy's block_on_dial=True never returning.
        threading.Event().wait()

    with patch("gui.connection.Board.connect", side_effect=_hangs_forever):
        start = time.monotonic()
        error = connection.connect()
        elapsed = time.monotonic() - start

    assert error is not None
    assert "did not return" in error
    # Bounded by timeout_ms + grace, nowhere near "forever".
    assert elapsed < 5.0
    assert connection.board is None


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
