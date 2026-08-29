# mcp_server/handlers.py
"""Logic layer: drives KiCadBoardAdapter / run_apply for the MCP tools.

Deliberately imports NO MCP SDK — only ``kicadstamp.*`` (design doc §2.1).
This is what unit tests exercise with a fake adapter, keeping the protocol
layer (server.py/tools.py) thin and untested-by-transport.

Populated in Steps 3-5 of plan_2026_08_29_kicad_mcp_server_execution.md:
- read tools (board identity, footprints, selection, nets),
- validated write (apply_config via run_apply),
- raw write (move_footprint, env-gated, board-identity guarded).
"""
