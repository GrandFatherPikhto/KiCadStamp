# mcp_server/connection.py
"""Lifecycle of the single KiCadBoardAdapter the MCP server talks to.

Owns one :class:`kicadstamp.kicad.adapter.KiCadBoardAdapter` (one pynng REQ
socket) per process, because a pynng REQ socket is not thread-safe and must not
be created/destroyed casually (see ``KiCadBoardAdapter.close()``'s docstring
about the Windows native crash from GC-finalised sockets).

Responsibilities:
  * lazy connect — the adapter is created (and the board loaded) only on the
    first tool call, not at server startup;
  * freshness — EVERY tool call is answered from a board rebuilt right before
    it (``refresh_board_before_live_read``), because the adapter caches the
    footprint list and this process stays up for hours: without it a tool
    serves the board as it was when KiCad was first connected (measured live,
    plan_2026_09_24_mcp_stale_board §1);
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

from kicadstamp.board_freshness import refresh_board_before_live_read
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _default_factory(timeout_ms: int):
    """Create a real adapter through the ONE factory (plan Т2/Т2а).

    Imported lazily so connection.py (and tests that only use a fake factory)
    never pay for kipy/pynng at import time (P0-2 lazy-import pattern).

    ``use_store=True`` asks the factory for the override LAYER unconditionally,
    even before any store is bound: the MCP server serves every profile from ONE
    adapter (one kipy REQ socket per process), while the store is a property of
    the CALL's config. The store of the current tool call is installed with
    ``FieldOverrideAdapter.bind_store`` (plan Т3, guard С19) — so the layer must
    exist from the start, empty, rather than being created on demand.
    """
    from kicadstamp.adapter_factory import create_board_adapter

    return create_board_adapter(timeout_ms=timeout_ms, use_store=True)


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

    def __init__(self, timeout_ms: int = DEFAULT_TIMEOUT_MS,
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

    def _ensure_open(self) -> tuple[KiCadBoardAdapter, bool]:
        """Return ``(adapter, just_loaded)``: the adapter to use, and whether
        THIS call created it (and therefore already loaded the board).

        Creating the adapter loads the board exactly once, so ``just_loaded`` is
        a FACT reported by the branch that created it — never an inference from
        ``refresh_board`` call counts, which is what plan_2026_09_24_mcp_stale_
        board §6.1 asks for: ``execute`` must refresh the board exactly once per
        call, and a caller that refreshed "just in case" here as well would make
        the counter 2 after ONE call, turning
        ``test_lazy_connect_creates_adapter_only_on_first_use`` red by the change
        itself rather than by a defect.

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
            return adapter, True
        return adapter, False

    def execute(self, fn: Callable[[KiCadBoardAdapter], T]) -> T:
        """Run *fn* with the live adapter, holding the serialising lock.

        The board behind the adapter is REBUILT once before *fn* runs, so every
        tool call sees the board as it is now instead of the footprint cache
        this process filled at connect time (plan_2026_09_24_mcp_stale_board
        §1/§6.2: always, not per tool — the invalidation itself is free, and only
        the call that then reads footprints pays for a board read).

        THE PRICE, measured live on the 332-footprint board
        (``kicadstamp.diagnostics.probe_mcp_call_cost``, counted at the kipy
        boundary — ``KiCad.get_open_documents`` and ``Board.get_footprints`` —
        because the adapter caches and its own call counter reads zero where the
        board was read; the Ш1 lesson, one layer up). One call that reads
        footprints costs 1 re-mint + 1 FULL READ of the footprint list:
        ``kicadstamp_get_footprint`` 26 ms, ``kicadstamp_list_footprints`` 30 ms,
        ``kicadstamp_get_items_by_uuid`` 38 ms; the first call of a process is the
        same class (26-37 ms cold), so this is not a cold-start artefact. The
        tools that never look at a footprint pay the re-mint only: 0.7-10 ms
        total, against 0.5-7.5 ms with the seam switched off in the same probe —
        and those tools read live copper either way, which dominates their number
        (``list_tracks`` 9.9 vs 7.5 ms). Told apart on purpose: the 166/186 ms
        class quoted in ``refresh_board_before_live_read``'s docstring comes from
        a DIFFERENT probe (plan_2026_09_22_imprint_stale_cache_sh2) and does not
        reproduce here — this probe puts the whole footprint-reading call at
        ~30 ms. The two numbers are not the same measurement and must not be
        averaged.

        No TTL or staleness policy is introduced with this (plan §7): the number
        above is the INPUT such a decision would need, not the decision.

        Refresh ONCE per call, never twice: when ``_ensure_open`` had to create
        the adapter it already loaded the board, and that is reported to us as a
        fact (see its docstring, §6.1).

        The refresh sits INSIDE the ``try`` deliberately: a link that died while
        the board was being rebuilt is exactly the class ``_reconnectable_
        errors`` exists for, and before this change a dead link could pass
        unnoticed — a read the cache could satisfy never touched the board at
        all, so it could not fail.

        Reconnects once (with a fresh adapter) when *fn* fails with a
        connection-level error (KiCad dropped mid-call), then re-runs *fn*. No
        refresh is needed on that path: the fresh adapter loaded the board as it
        was created.

        Holds the lock for the whole call, so a long apply run still blocks
        other tool calls on the single socket — intentional.
        """
        with self._lock:
            adapter, just_loaded = self._ensure_open()
            try:
                if not just_loaded:
                    refresh_board_before_live_read(adapter)
                return fn(adapter)
            except _reconnectable_errors():
                logger.warning(_("KiCad connection dropped during an MCP call; reconnecting once"))
                adapter.close()
                self._adapter = None
                fresh, just_loaded = self._ensure_open()
                if not just_loaded:
                    refresh_board_before_live_read(fresh)
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
