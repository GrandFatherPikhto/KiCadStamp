# tests/test_mcp_connection.py
"""Unit tests for mcp_server/connection.py — no live KiCad, fake adapter.

The ConnectionManager is the only layer that owns the single
KiCadBoardAdapter; these tests verify its lifecycle contract:
lazy connect, adapter reuse, one-time reconnect on a connection-level error,
"board vanished" recreation, and idempotent close.
"""
import pytest

from mcp_server.connection import ConnectionManager


class _FakeAdapter:
    """Minimal stand-in for KiCadBoardAdapter.

    :param fail_first: number of initial ``ping()``/``get_board_filename()``
        calls that raise ConnectionError, to simulate a dropped IPC link.
    """

    def __init__(self, name: str = "fake_board.kicad_pcb", fail_first: int = 0):
        self._name = name
        self._fail_remaining = fail_first
        self.refresh_count = 0
        self.close_count = 0

    def refresh_board(self):
        self.refresh_count += 1

    def get_board_filename(self):
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise ConnectionError("kipy socket closed")
        return self._name

    def ping(self) -> str:
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise ConnectionError("kipy socket closed")
        return "pong"

    def close(self):
        self.close_count += 1


def _make_manager(name: str = "fake_board.kicad_pcb", first_adapter_fail_first: int = 0):
    """Build a ConnectionManager with a fake factory that records every adapter
    it creates. Only the FIRST created adapter is ever flaky (that mirrors a
    real scenario: a long-lived link drops once, then the reconnect works)."""
    created = []

    def factory(timeout_ms):
        flaky = first_adapter_fail_first if not created else 0
        adapter = _FakeAdapter(name=name, fail_first=flaky)
        created.append(adapter)
        return adapter

    return ConnectionManager(timeout_ms=12345, adapter_factory=factory), created


def test_lazy_connect_creates_adapter_only_on_first_use():
    mgr, created = _make_manager()
    assert not mgr.is_connected
    assert created == []
    assert mgr.execute(lambda a: a.ping()) == "pong"
    assert mgr.is_connected
    assert len(created) == 1
    assert created[0].refresh_count == 1  # board loaded on connect


def test_reuses_the_same_adapter_across_calls():
    mgr, created = _make_manager()
    mgr.execute(lambda a: a.ping())
    mgr.execute(lambda a: a.ping())
    mgr.execute(lambda a: a.ping())
    assert len(created) == 1


def test_reconnects_once_after_connection_error():
    mgr, created = _make_manager(first_adapter_fail_first=1)
    # The first adapter raises ConnectionError on the first call; the manager
    # tears it down, reconnects with a fresh adapter and retries the call.
    assert mgr.execute(lambda a: a.ping()) == "pong"
    assert len(created) == 2
    assert created[0].close_count == 1  # dead adapter closed exactly once


def test_recreates_when_board_vanished():
    mgr, created = _make_manager()
    mgr.execute(lambda a: a.ping())
    # The open board disappears: get_board_filename() now returns None, so the
    # next use must recreate the adapter instead of reusing the stale one.
    created[0]._name = None
    mgr.execute(lambda a: a.ping())
    assert len(created) == 2
    assert created[0].close_count == 1


def test_close_is_idempotent_and_frees_adapter_once():
    mgr, created = _make_manager()
    mgr.execute(lambda a: a.ping())
    mgr.close()
    mgr.close()
    assert created[0].close_count == 1
    assert not mgr.is_connected


def test_execute_after_close_raises():
    mgr, _ = _make_manager()
    mgr.close()
    with pytest.raises(RuntimeError):
        mgr.execute(lambda a: a.ping())
