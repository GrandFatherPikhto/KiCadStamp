# mcp_server/server.py
"""MCP server for KiCadStamp — protocol layer (stdio transport).

Registers the MCP tools that let Claude Code / Demon inspect and drive the
live KiCad board, then runs the stdio transport (local, no network). See the
design document techdocs/handoff/deepseek/design_2026_08_29_kicad_mcp_server.md.

Layer rules (design doc §2.1): server.py and tools.py are the only modules
that import the MCP SDK; handlers.py and connection.py stay SDK-free.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from .connection import ConnectionManager

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


def _raw_write_enabled() -> bool:
    """Raw write tools are OFF by default. They are registered when EITHER the
    ``KICADSTAMP_MCP_ALLOW_RAW_WRITE=1`` env var is set OR the GUI's Settings
    tab enabled them (``gui_state.json`` key ``mcp_allow_raw_write``, see
    gui/docks/configurator.py). The GUI path lets the Settings dock control the
    headless MCP server without an env var; any failure to read the GUI store
    just means "not enabled".
    """
    if os.environ.get("KICADSTAMP_MCP_ALLOW_RAW_WRITE") == "1":
        return True
    try:
        from gui import settings

        return bool(settings.state.get("mcp_allow_raw_write", False))
    except Exception:
        return False


def build_server(adapter_factory: Callable[[int], object] | None = None) -> MCPServer:
    """Create the MCPServer instance with all registered tools.

    :param adapter_factory: injected by tests to substitute a fake adapter;
        defaults to the real KiCadBoardAdapter (see ConnectionManager).
    Kept separate from import so main() can call setup_i18n() first and tests
    can build a fresh server per test.
    """
    # tools/handlers bind `_` at import time — import them lazily so main()'s
    # setup_i18n() runs first (P1-1 pattern) and so importing server.py never
    # pulls in handlers/kipy at module level.
    from .tools import register_raw_tools, register_tools

    manager = ConnectionManager(adapter_factory=adapter_factory)
    server = MCPServer(
        name=_SERVER_NAME,
        description=_SERVER_DESCRIPTION,
        version=_SERVER_VERSION,
        lifespan=_make_lifespan(manager),
    )
    register_tools(server, manager)
    # Raw write tools are OFF by default — they only exist when the operator
    # opts in explicitly (env var or the Settings tab, see _raw_write_enabled).
    if _raw_write_enabled():
        register_raw_tools(server, manager)
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
