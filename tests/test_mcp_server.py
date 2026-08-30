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

from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2

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

    def __init__(self, name="fake.kicad_pcb", version="10.0.6",
                 footprints=(), tracks=(), vias=()):
        self._name = name
        self._version = version
        self._footprints = list(footprints)
        self._tracks = list(tracks)
        self._vias = list(vias)

    def refresh_board(self):
        pass

    def get_board_filename(self):
        return self._name

    def get_version(self):
        return self._version

    def get_footprints(self):
        return list(self._footprints)

    def get_footprint(self, ref):
        return next((f for f in self._footprints if f.ref == ref), None)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

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
                     "kicadstamp_list_nets", "kicadstamp_get_items_by_uuid",
                     "kicadstamp_list_tracks", "kicadstamp_list_vias",
                     "kicadstamp_apply_config"):
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

    by_uuid = _input_schema(tools["kicadstamp_get_items_by_uuid"])
    assert "uuids" in by_uuid.get("required", [])
    assert "uuids" in by_uuid.get("properties", {})

    list_tracks = _input_schema(tools["kicadstamp_list_tracks"])
    assert "net" in list_tracks.get("properties", {})
    assert "layer" in list_tracks.get("properties", {})
    assert "net" not in list_tracks.get("required", [])
    assert "layer" not in list_tracks.get("required", [])

    list_vias = _input_schema(tools["kicadstamp_list_vias"])
    assert "net" in list_vias.get("properties", {})
    assert "net" not in list_vias.get("required", [])


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


def _routing_adapter(footprints=()):
    """Board with one F.Cu track on GND, one B.Cu track on +3V3 and one via."""
    track_f = Track(uuid="t-f", start=Vector2(0, 0), end=Vector2(1_000_000, 0),
                    net_name="GND", width_mm=0.25, layer=BoardLayer.BL_F_Cu)
    track_b = Track(uuid="t-b", start=Vector2(0, 0), end=Vector2(0, 1_000_000),
                    net_name="+3V3", width_mm=0.5, layer=BoardLayer.BL_B_Cu)
    via = Via(uuid="v-1", position=Vector2(0, 0), net_name="GND",
              drill_mm=0.3, diameter_mm=0.6)
    return _FakeAdapter(footprints=footprints, tracks=[track_f, track_b],
                        vias=[via])


# --- tracks / vias / get_items_by_uuid --------------------------------------

def test_dispatch_get_items_by_uuid_mixed_found_and_missing():
    fp = Footprint(ref="U1", uuid="fp-u1", position=Vector2(0, 0),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu, value="MCU")
    server = build_server(adapter_factory=lambda ms: _routing_adapter([fp]))
    is_error, text = _call_text(server, "kicadstamp_get_items_by_uuid",
                                {"uuids": ["t-f", "v-1", "fp-u1", "ghost"]})
    assert is_error is not True
    # every requested uuid appears exactly once, in request order
    assert text.count('"uuid"') == 4
    assert '"t-f"' in text and '"kind": "track"' in text
    assert '"v-1"' in text and '"kind": "via"' in text
    assert '"fp-u1"' in text and '"kind": "footprint"' in text
    assert '"ghost"' in text and '"kind": null' in text and '"found": false' in text


def test_dispatch_get_items_by_uuid_all_missing():
    server = build_server(adapter_factory=lambda ms: _FakeAdapter())
    is_error, text = _call_text(server, "kicadstamp_get_items_by_uuid",
                                {"uuids": ["a", "b"]})
    assert is_error is not True
    assert '"uuid": "a"' in text and '"uuid": "b"' in text
    assert text.count('"found": false') == 2


def test_dispatch_list_tracks_filters_by_net_and_layer():
    server = build_server(adapter_factory=lambda ms: _routing_adapter())
    is_error, text = _call_text(server, "kicadstamp_list_tracks",
                                {"net": "GND", "layer": "F.Cu"})
    assert is_error is not True
    assert '"t-f"' in text
    assert '"t-b"' not in text


def test_dispatch_list_tracks_no_match_returns_empty_list():
    server = build_server(adapter_factory=lambda ms: _routing_adapter())
    is_error, text = _call_text(server, "kicadstamp_list_tracks", {"net": "NOPE"})
    assert is_error is not True
    # the empty list serialises to an empty content text (exact [] is asserted
    # at the handler level in test_mcp_handlers.py)
    assert text == ""


def test_dispatch_list_vias_filters_by_net():
    server = build_server(adapter_factory=lambda ms: _routing_adapter())
    is_error, text = _call_text(server, "kicadstamp_list_vias", {"net": "GND"})
    assert is_error is not True
    assert '"v-1"' in text and '"kind": "via"' in text


def test_dispatch_list_vias_no_match_returns_empty_list():
    server = build_server(adapter_factory=lambda ms: _routing_adapter())
    is_error, text = _call_text(server, "kicadstamp_list_vias", {"net": "NOPE"})
    assert is_error is not True
    # the empty list serialises to an empty content text (exact [] is asserted
    # at the handler level in test_mcp_handlers.py)
    assert text == ""
