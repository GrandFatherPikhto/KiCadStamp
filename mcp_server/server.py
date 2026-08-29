# mcp_server/server.py
"""MCP server for KiCadStamp — protocol layer (stdio transport).

Registers the MCP tools that let Claude Code / Demon inspect and drive the
live KiCad board, then runs the stdio transport (local, no network). Tools
are registered in later steps of plan_2026_08_29_kicad_mcp_server_execution.md;
this Step 1 skeleton starts a server that lists zero tools.

Layer rules (design doc §2.1): server.py and tools.py are the only modules
that import the MCP SDK; handlers.py and connection.py stay SDK-free.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

_SERVER_NAME = "kicadstamp"
_SERVER_DESCRIPTION = (
    "KiCadStamp MCP server: read the live KiCad board, apply validated "
    "placement config, and (opt-in, raw) move footprints."
)
_SERVER_VERSION = "0.1.0"


def build_server() -> MCPServer:
    """Create the MCPServer instance with all registered tools.

    Kept as a separate function (not created at import) so tests can build a
    fresh server per test and so main() can call setup_i18n() first.
    """
    server = MCPServer(
        name=_SERVER_NAME,
        description=_SERVER_DESCRIPTION,
        version=_SERVER_VERSION,
    )
    # Tools are added in later steps (read tools -> apply_config -> raw move).
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
