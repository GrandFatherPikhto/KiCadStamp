# tests/test_mcp_server.py
"""Unit tests for mcp_server/server.py — protocol layer, no transport.

Covers tool registration and the env-gate for the raw write tools. The stdio
transport itself is verified by a live smoke handshake (plan §5.2), not here.
"""
import asyncio

from mcp_server.server import build_server


def _tool_names(server):
    return sorted(t.name for t in asyncio.run(server.list_tools()))


def test_read_and_apply_tools_registered_by_default(monkeypatch):
    monkeypatch.delenv("KICADSTAMP_MCP_ALLOW_RAW_WRITE", raising=False)
    names = _tool_names(build_server())
    for expected in ("kicadstamp_get_board_identity", "kicadstamp_list_footprints",
                     "kicadstamp_get_footprint", "kicadstamp_get_selection",
                     "kicadstamp_list_nets", "kicadstamp_apply_config"):
        assert expected in names
    assert "kicad_raw_move_footprint" not in names  # raw is OFF by default


def test_raw_tool_registered_when_env_flag_set(monkeypatch):
    monkeypatch.setenv("KICADSTAMP_MCP_ALLOW_RAW_WRITE", "1")
    names = _tool_names(build_server())
    assert "kicad_raw_move_footprint" in names
