# mcp_server/connection.py
"""Lifecycle of the single KiCadBoardAdapter the MCP server talks to.

Owns one :class:`kicadstamp.kicad.adapter.KiCadBoardAdapter` (one pynng REQ
socket) per process: lazy connect on first tool call, liveness checks and
reconnect when KiCad drops, a ``threading.Lock`` serialising access to the
socket, and ``close()`` on shutdown.

This module deliberately imports NO MCP SDK — only ``kicadstamp.*`` (design
doc §2.1). Populated in Step 2 of plan_2026_08_29_kicad_mcp_server_execution.md.
"""
