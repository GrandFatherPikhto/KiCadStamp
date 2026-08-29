# tests/test_mcp_server.py
"""Unit tests for mcp_server/server.py — protocol layer, no transport.

Covers tool registration, the env-gate for raw write tools, the input JSON
schemas inferred from tool signatures, and tools->handlers dispatch through
server.call_tool() with a fake adapter injected via build_server(). The stdio
transport itself is verified by a live smoke handshake (plan §5.2), not here.
"""
import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server.server import _raw_write_enabled, build_server


@pytest.fixture(autouse=True)
def _isolate_gui_settings(tmp_path, monkeypatch):
    """Point the MCP server's raw-write gate at a throwaway gui_state.json so
    no test depends on (or touches) the developer's real one — the same
    discipline as tests/gui/conftest.py's isolated_settings."""
    from gui import settings as gui_settings

    monkeypatch.setattr(gui_settings, "SETTINGS_PATH", tmp_path / "gui_state.json")


class _FakeAdapter:
    """Minimal adapter stand-in injected into the ConnectionManager factory."""

    def __init__(self, name="fake.kicad_pcb", version="10.0.6"):
        self._name = name
        self._version = version

    def refresh_board(self):
        pass

    def get_board_filename(self):
        return self._name

    def get_version(self):
        return self._version

    def get_footprints(self):
        return []

    def get_footprint(self, ref):
        return None

    def get_field_value(self, fp, name):
        return None

    def get_footprint_pads(self, fp):
        return []

    def get_selected_items(self):
        return []

    def get_all_nets(self):
        return []


def _tool_names(server):
    return sorted(t.name for t in asyncio.run(server.list_tools()))


def _call_text(server, name, arguments):
    res = asyncio.run(server.call_tool(name, arguments))
    text = "".join(getattr(c, "text", "") or "" for c in res.content)
    return res.is_error, text


def _input_schema(tool):
    schema = tool.input_schema
    return schema.model_dump() if hasattr(schema, "model_dump") else schema


# --- registration & env gate -------------------------------------------------

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


def test_raw_tool_registered_when_gui_setting_enabled(monkeypatch):
    """The Settings tab's checkbox (gui_state.json's mcp_allow_raw_write) also
    enables the raw tool — the headless server reads the GUI store."""
    monkeypatch.delenv("KICADSTAMP_MCP_ALLOW_RAW_WRITE", raising=False)
    from gui import settings

    settings.state.set("mcp_allow_raw_write", True)
    names = _tool_names(build_server())
    assert "kicad_raw_move_footprint" in names


def test_raw_write_enabled_helper_prefers_env_over_gui(monkeypatch):
    monkeypatch.setenv("KICADSTAMP_MCP_ALLOW_RAW_WRITE", "1")
    from gui import settings

    settings.state.set("mcp_allow_raw_write", False)
    assert _raw_write_enabled() is True


# --- input schemas (inferred from tool signatures) ---------------------------

def test_input_schemas():
    server = build_server()
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    get_fp = _input_schema(tools["kicadstamp_get_footprint"])
    assert "ref" in get_fp.get("required", [])
    assert "ref" in get_fp.get("properties", {})

    list_fp = _input_schema(tools["kicadstamp_list_footprints"])
    assert "ref_prefix" in list_fp.get("properties", {})
    assert "ref_prefix" not in list_fp.get("required", [])


# --- tools -> handlers dispatch (through a fake adapter) ---------------------

def test_dispatch_board_identity():
    server = build_server(adapter_factory=lambda ms: _FakeAdapter("fake.kicad_pcb", "10.0.6"))
    is_error, text = _call_text(server, "kicadstamp_get_board_identity", {})
    assert is_error is not True
    assert '"fake.kicad_pcb"' in text
    assert '"10.0.6"' in text


def test_dispatch_get_footprint_missing_ref_is_tool_error():
    # A deliberate failure (ref not on the board) surfaces as a ToolError, not
    # as an "unexpected crash" — that is what distinguishes expected failures
    # from real bugs. (call_tool raises; the stdio transport serialises it as
    # an error response.)
    server = build_server(adapter_factory=lambda ms: _FakeAdapter())
    with pytest.raises(ToolError, match="not found"):
        _call_text(server, "kicadstamp_get_footprint", {"ref": "R999"})


def test_dispatch_apply_config_dry_run(monkeypatch):
    def fake_run_apply(options):
        return ["=== DRY RUN ===", "Moves: none"]

    monkeypatch.setattr("kicadstamp.apply_pipeline.run_apply", fake_run_apply)
    server = build_server()  # apply_config does not use the shared adapter
    is_error, text = _call_text(server, "kicadstamp_apply_config",
                                {"config_path": "profiles/x.sexp", "dry_run": True})
    assert is_error is not True
    assert "DRY RUN" in text
