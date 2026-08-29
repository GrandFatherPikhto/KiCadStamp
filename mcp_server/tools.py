# mcp_server/tools.py
"""MCP tool schemas (Pydantic models) + thin wrappers over handlers.

The only layer that touches the MCP SDK's argument model: maps validated
arguments onto handler calls and encodes results/errors. Registered onto the
MCPServer in server.build_server().

Populated in Steps 3-5 of plan_2026_08_29_kicad_mcp_server_execution.md.
"""
