# mcp_server/__init__.py
"""MCP server for KiCadStamp — top-level integration package.

Exposes the live KiCad board and the validated apply path as MCP tools over
stdio, so Claude Code / Demon can inspect and drive the board. See docs/mcp.md
and the design document techdocs/handoff/deepseek/design_2026_08_29_kicad_mcp_server.md.
"""

__version__ = "0.1.0"
