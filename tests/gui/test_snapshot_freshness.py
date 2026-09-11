# tests/gui/test_snapshot_freshness.py
"""R.4 gates for ``gui/worker.py::refresh_snapshot_then`` —
plan_2026_09_11_stale_snapshot_positions.md.

``MainWindow._poll``'s automatic tick is a deliberate no-op once the board is
connected (one request at a time on the shared kipy REQ socket), so
``BoardConnection.snapshot`` is rebuilt only by connect()/manual refresh: it is
frozen at connect time, and every GEOMETRY read that trusts it follows stale
coordinates (K.2 #5/#6). The fix rebuilds it AT THE POINT OF USE — on the
worker thread, inside ``start_long_op``, so the op is the socket's only active
owner — and never by calling the adapter from the UI thread, which was exactly
the Commit H hang (plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_fix.md
§0). These tests pin that contract down on the helper itself; the flow-level
gates (fresh position wins, fully-selected detection) live next to the docks'
own tests (tests/gui/test_scheme_list.py, tests/gui/test_phase3_wiring.py).
"""
import logging
import threading
from types import SimpleNamespace

from gui.worker import refresh_snapshot_then, snapshot_refresh_supported
from tests.gui.conftest import _pump


class _RefreshingConnection:
    """A BoardConnection stand-in with a LIVE board behind it: ``refresh()``
    swaps the frozen snapshot for the next one and records where it ran."""

    def __init__(self, snapshots):
        self.board = SimpleNamespace(refresh=lambda: None)
        self._pending = [list(s) for s in snapshots]
        self.snapshot = self._pending.pop(0) if self._pending else []
        self.long_op_active = False
        self.refresh_threads: list = []

    def refresh(self):
        self.refresh_threads.append(threading.current_thread().name)
        if self._pending:
            self.snapshot = self._pending.pop(0)
        return None


class _GuardWidget:
    """The guard widget start_long_op disables for the op's lifetime."""

    def __init__(self):
        self.enabled = True

    def isEnabled(self):
        return self.enabled

    def setEnabled(self, value):
        self.enabled = value


def test_snapshot_refresh_supported_requires_a_live_board():
    """Only a board that exposes refresh() can offer fresher data — everything
    else (no connection, no board, a board stand-in) keeps the cached snapshot."""
    assert not snapshot_refresh_supported(None)
    assert not snapshot_refresh_supported(SimpleNamespace(board=None))
    assert not snapshot_refresh_supported(
        SimpleNamespace(board=SimpleNamespace(adapter=object())))
    assert snapshot_refresh_supported(
        SimpleNamespace(board=SimpleNamespace(refresh=lambda: None)))


def test_refresh_snapshot_then_rebuilds_on_the_worker_thread_before_ready(qapp):
    """R.4 #2 + #3 — exactly ONE rebuild per operation, it runs on the WORKER
    thread (never the UI thread), and on_ready() runs afterwards on the UI
    thread, so the snapshot it reads is the fresh one."""
    connection = _RefreshingConnection([["stale"], ["fresh"]])
    widget = _GuardWidget()
    log: list = []

    refresh_snapshot_then(
        connection, (widget,),
        lambda: log.append(("ready", threading.current_thread().name,
                            list(connection.snapshot))),
        lambda message: log.append(("error", message)))

    # Nothing happened inline: the continuation only runs back on the UI thread
    # once the op's completion signal is delivered (queued from the worker).
    assert log == []
    assert not widget.enabled          # held for the op's lifetime

    _pump(qapp, lambda: not connection.long_op_active)

    assert [entry[0] for entry in log] == ["ready"]
    assert connection.refresh_threads                    # the rebuild happened...
    assert len(connection.refresh_threads) == 1          # ...exactly once
    assert connection.refresh_threads[0] != threading.current_thread().name
    assert log[0][1] == threading.current_thread().name  # on_ready = UI thread
    assert log[0][2] == ["fresh"]                        # fresh data, not stale
    assert widget.enabled                                # released again


def test_refresh_snapshot_then_falls_back_without_a_live_board():
    """A connection that cannot be refreshed (no live board behind it — a
    foreign/read-only connection, the docks' own stand-ins) keeps the cached
    snapshot and the continuation runs AT ONCE, on the calling thread: the old
    synchronous behaviour, unchanged."""
    connection = SimpleNamespace(snapshot=["cached"], board=None,
                                 long_op_active=False)
    log: list = []

    controller = refresh_snapshot_then(
        connection, (), lambda: log.append("ready"),
        lambda message: log.append(("error", message)))

    assert controller is None
    assert log == ["ready"]
    assert connection.long_op_active is False


def test_refresh_snapshot_then_refuses_while_another_long_op_holds_the_socket(
        caplog):
    """A second concurrent owner on the shared REQ socket is exactly the
    2026-09-08 hang, so the request is REFUSED (and logged), never queued and
    never an inline adapter call on the UI thread."""
    connection = _RefreshingConnection([["stale"]])
    connection.long_op_active = True
    log: list = []

    with caplog.at_level(logging.WARNING):
        controller = refresh_snapshot_then(
            connection, (), lambda: log.append("ready"),
            lambda message: log.append(("error", message)))

    assert controller is None
    assert log == []                     # neither on_ready nor on_error
    assert connection.refresh_threads == []
    assert any("refused" in r.message for r in caplog.records)
