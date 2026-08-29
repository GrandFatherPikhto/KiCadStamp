# mcp_server/server.py
"""MCP server for KiCadStamp — protocol layer (stdio transport).

Registers the MCP tools that let Claude Code / Demon inspect and drive the
live KiCad board, then runs the stdio transport (local, no network). See the
design document techdocs/handoff/deepseek/design_2026_08_29_kicad_mcp_server.md.

Layer rules (design doc §2.1): server.py and tools.py are the only modules
that import the MCP SDK; handlers.py and connection.py stay SDK-free.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from .connection import ConnectionManager
from .tools import register_tools

_SERVER_NAME = "kicadstamp"
_SERVER_DESCRIPTION = (
    "KiCadStamp MCP server: read the live KiCad board, apply validated "
    "placement config, and (opt-in, raw) move footprints."
)
_SERVER_VERSION = "0.1.0"


def _make_lifespan(manager: ConnectionManager):
    """Async context manager that closes the shared adapter on shutdown."""

    @asynccontextmanager
    async def lifespan(server):
        yield None
        manager.close()

    return lifespan


def build_server(adapter_factory: Callable[[int], object] | None = None) -> MCPServer:
    """Create the MCPServer instance with all registered tools.

    :param adapter_factory: injected by tests to substitute a fake adapter;
        defaults to the real KiCadBoardAdapter (see ConnectionManager).
    Kept separate from import so main() can call setup_i18n() first and tests
    can build a fresh server per test.
    """
    manager = ConnectionManager(adapter_factory=adapter_factory)
    server = MCPServer(
        name=_SERVER_NAME,
        description=_SERVER_DESCRIPTION,
        version=_SERVER_VERSION,
        lifespan=_make_lifespan(manager),
    )
    register_tools(server, manager)
    return server


def main() -> None:
    """Entry point (console script ``kicadstamp-mcp``).

    Explicit i18n init (P1-1, 2026-08-25): set up gettext BEFORE importing any
    module that binds ``_`` at import time, so handlers see the right locale.
    """
    from kicadstamp.i18n import setup_i18n

    setup_i18n()
    server = build_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
