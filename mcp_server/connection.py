# mcp_server/connection.py
"""Lifecycle of the single KiCadBoardAdapter the MCP server talks to.

Owns one :class:`kicadstamp.kicad.adapter.KiCadBoardAdapter` (one pynng REQ
socket) per process, because a pynng REQ socket is not thread-safe and must not
be created/destroyed casually (see ``KiCadBoardAdapter.close()``'s docstring
about the Windows native crash from GC-finalised sockets).

Responsibilities:
  * lazy connect — the adapter is created (and the board loaded) only on the
    first tool call, not at server startup;
  * serialisation — a re-entrant lock guards every use of the shared adapter,
    so concurrent MCP tool calls never race on the single socket;
  * self-healing — connection-level failures (KiCad dropped the IPC link, the
    board vanished) tear the dead adapter down and retry once with a fresh one;
  * idempotent close — frees the kipy socket exactly once on shutdown.

The adapter is injected as a *factory* so unit tests substitute a fake without
touching kipy (tests/test_mcp_connection.py). This module deliberately imports
no MCP SDK (design doc §2.1).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from kicadstamp.kicad.adapter import KiCadBoardAdapter

from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _default_factory(timeout_ms: int):
    """Create a real KiCadBoardAdapter.

    Imported lazily so connection.py (and tests that only use a fake factory)
    never pay for kipy/pynng at import time (P0-2 lazy-import pattern).
    """
    from kicadstamp.kicad.adapter import KiCadBoardAdapter

    return KiCadBoardAdapter(timeout_ms=timeout_ms)


def _get_board_not_found_error():
    # Imported lazily to keep the module kipy-free at import (see above).
    from kicadstamp.exceptions import BoardNotFoundError

    return BoardNotFoundError


# The reconnectable tuple above needs the real class at module level, so it is
# built lazily by _reconnectable_errors() below instead of at import time.
_BOARD_NOT_FOUND_ERROR = None


def _reconnectable_errors():
    """Return the tuple of connection-level exception types to reconnect on.

    ``kicadstamp.exceptions.BoardNotFoundError`` is imported lazily to keep
    connection.py importable without kipy; the tuple is cached after the first
    call.
    """
    global _BOARD_NOT_FOUND_ERROR
    if _BOARD_NOT_FOUND_ERROR is None:
        _BOARD_NOT_FOUND_ERROR = _get_board_not_found_error()
    return (ConnectionError, _BOARD_NOT_FOUND_ERROR)


class ConnectionManager:
    """Owns the single KiCadBoardAdapter for the whole MCP server process.

    :param timeout_ms: kipy IPC timeout passed to the adapter factory.
    :param adapter_factory: callable ``(timeout_ms) -> adapter``; defaults to
        building a real :class:`KiCadBoardAdapter`. Tests inject a fake.
    """

    def __init__(self, timeout_ms: int = 20000,
                 adapter_factory: Callable[[int], KiCadBoardAdapter] | None = None) -> None:
        self._timeout_ms = timeout_ms
        self._factory: Callable[[int], KiCadBoardAdapter] = adapter_factory or _default_factory
        self._adapter: KiCadBoardAdapter | None = None
        self._lock = threading.RLock()
        self._closed = False

    @property
    def is_connected(self) -> bool:
        """True when an adapter exists and the manager was never closed."""
        with self._lock:
            return self._adapter is not None and not self._closed

    def _ensure_open(self) -> KiCadBoardAdapter:
        """Return a live adapter, creating it (and loading the board) on first
        use or when the current one lost its board.

        Raises the adapter's ``BoardNotFoundError`` when KiCad is not running
        or has no board open — the tools layer formats that as a clear message.
        """
        if self._closed:
            raise RuntimeError("ConnectionManager is closed")
        adapter = self._adapter
        if adapter is None or adapter.get_board_filename() is None:
            if adapter is not None:
                adapter.close()
            adapter = self._factory(self._timeout_ms)
            adapter.refresh_board()
            self._adapter = adapter
        return adapter

    def execute(self, fn: Callable[[KiCadBoardAdapter], T]) -> T:
        """Run *fn* with the live adapter, holding the serialising lock.

        Reconnects once (with a fresh adapter) when *fn* fails with a
        connection-level error (KiCad dropped mid-call), then re-runs *fn*.

        Holds the lock for the whole call, so a long apply run still blocks
        other tool calls on the single socket — intentional.
        """
        with self._lock:
            adapter = self._ensure_open()
            try:
                return fn(adapter)
            except _reconnectable_errors():
                logger.warning(_("KiCad connection dropped during an MCP call; reconnecting once"))
                adapter.close()
                self._adapter = None
                fresh = self._ensure_open()
                return fn(fresh)

    def close(self) -> None:
        """Free the kipy socket exactly once. Safe to call more than once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._adapter is not None:
                self._adapter.close()
                self._adapter = None
