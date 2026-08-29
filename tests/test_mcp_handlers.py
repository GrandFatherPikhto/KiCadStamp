# tests/test_mcp_handlers.py
"""Unit tests for mcp_server/handlers.py — READ tools + validated apply, no
live KiCad.

Read handlers take a "live adapter" argument; tests inject a fake adapter
built on the real domain DTOs (Footprint/Pad/Net/Via/Track/Vector2). The
apply_config handler runs the existing run_apply path, which is monkeypatched
here so no IPC is ever reached.
"""
import logging

import pytest

from kicadstamp.domain.board import Footprint, Net, Pad, Track, Via
from kicadstamp.exceptions import ValidationError
from kicadstamp.domain.geometry import BoardLayer, Vector2

from mcp_server import handlers


def _fp(ref, x_mm=1.5, y_mm=2.5, angle=90.0, layer=BoardLayer.BL_F_Cu, uuid=None):
    return Footprint(
        ref=ref,
        uuid=uuid or ("uuid-" + ref),
        position=Vector2(x=round(x_mm * 1e6), y=round(y_mm * 1e6)),
        angle_deg=angle,
        layer=layer,
        value="VAL-" + ref,
    )


def _pad(number, net_name, x_mm=0.0, y_mm=0.0):
    return Pad(number=number, net_name=net_name,
               position=Vector2(x=round(x_mm * 1e6), y=round(y_mm * 1e6)))


class FakeAdapter:
    """Minimal adapter stand-in implementing exactly what handlers use."""

    def __init__(self, footprints=(), nets=(), selected=(), pads_by_ref=None,
                 fields=None, board_name="test_board.kicad_pcb", version="10.0.6"):
        self._fps = list(footprints)
        self._nets = list(nets)
        self._selected = list(selected)
        self._pads_by_ref = pads_by_ref or {}
        self._fields = fields or {}
        self._board_name = board_name
        self._version = version
        self.updated: list = []  # every update_items() push, for assertions

    def get_board_filename(self):
        return self._board_name

    def get_version(self):
        return self._version

    def get_footprints(self):
        return list(self._fps)

    def get_footprint(self, ref):
        return next((f for f in self._fps if f.ref == ref), None)

    def get_field_value(self, fp, name):
        return self._fields.get((fp.ref, name))

    def get_footprint_pads(self, fp):
        return list(self._pads_by_ref.get(fp.ref, []))

    def get_selected_items(self):
        return list(self._selected)

    def get_all_nets(self):
        return list(self._nets)

    def update_items(self, items):
        for dto in items:
            for i, stored in enumerate(self._fps):
                if stored.ref == dto.ref:
                    self._fps[i] = dto  # store the mutated DTO
        self.updated.append(items)

    def refresh_board(self):
        pass

    def commit_with_retry(self, description, work_fn, retries=1):
        work_fn()
        return True


# --- identity ---------------------------------------------------------------

def test_board_identity_connected():
    adapter = FakeAdapter(board_name="3CH-AWG-TIA-v103.kicad_pcb", version="10.0.6")
    info = handlers.get_board_identity(adapter)
    assert info == {
        "connected": True,
        "board_name": "3CH-AWG-TIA-v103.kicad_pcb",
        "kicad_version": "10.0.6",
    }


def test_board_identity_disconnected():
    adapter = FakeAdapter(board_name=None)
    info = handlers.get_board_identity(adapter)
    assert info["connected"] is False
    assert info["board_name"] is None


# --- list footprints --------------------------------------------------------

def test_list_footprints_filters_by_ref_prefix():
    adapter = FakeAdapter(footprints=[_fp("U1"), _fp("U2"), _fp("C1")])
    all_fps = handlers.list_footprints(adapter)
    u_fps = handlers.list_footprints(adapter, ref_prefix="U")
    assert [f["ref"] for f in all_fps] == ["U1", "U2", "C1"]
    assert [f["ref"] for f in u_fps] == ["U1", "U2"]


def test_list_footprints_converts_units_and_layer():
    adapter = FakeAdapter(footprints=[_fp("U1", x_mm=1.5, y_mm=2.5, angle=90.0)])
    entry = handlers.list_footprints(adapter)[0]
    assert entry["x_mm"] == 1.5
    assert entry["y_mm"] == 2.5
    assert entry["rotation_deg"] == 90.0
    assert entry["layer"] == "F.Cu"


def test_list_footprints_reads_role_and_cluster():
    fp = _fp("U1")
    adapter = FakeAdapter(footprints=[fp], fields={("U1", "Role"): "ADC", ("U1", "Cluster"): "Chan_0"})
    entry = handlers.list_footprints(adapter)[0]
    assert entry["role"] == "ADC"
    assert entry["cluster"] == "Chan_0"


# --- get footprint ----------------------------------------------------------

def test_get_footprint_missing_ref_returns_none():
    adapter = FakeAdapter(footprints=[_fp("U1")])
    assert handlers.get_footprint(adapter, "R999") is None


def test_get_footprint_includes_pads_and_nets():
    fp = _fp("U1")
    pads = [_pad("1", "+3V3", x_mm=0.1, y_mm=0.0), _pad("2", "GND", x_mm=-0.1, y_mm=0.0)]
    adapter = FakeAdapter(footprints=[fp], pads_by_ref={"U1": pads},
                          fields={("U1", "Role"): "ADC"})
    detail = handlers.get_footprint(adapter, "U1")
    assert detail["ref"] == "U1"
    assert detail["x_mm"] == 1.5
    assert detail["uuid"] == "uuid-U1"
    assert detail["fields"]["Role"] == "ADC"
    assert [p["number"] for p in detail["pads"]] == ["1", "2"]
    assert detail["pads"][0]["net"] == "+3V3"
    assert detail["nets"] == ["+3V3", "GND"]


# --- selection --------------------------------------------------------------

def test_get_selection_maps_kinds():
    fp = _fp("U1")
    via = Via(uuid="v-1", position=Vector2(0, 0), net_name="GND", drill_mm=0.3, diameter_mm=0.6)
    track = Track(uuid="t-1", start=Vector2(0, 0), end=Vector2(1, 1),
                  net_name="GND", width_mm=0.2, layer=BoardLayer.BL_F_Cu)
    adapter = FakeAdapter(selected=[fp, via, track])
    sel = handlers.get_selection(adapter)
    assert sel == [
        {"kind": "footprint", "ref": "U1", "uuid": "uuid-U1"},
        {"kind": "via", "uuid": "v-1"},
        {"kind": "track", "uuid": "t-1"},
    ]


def test_get_selection_empty():
    assert handlers.get_selection(FakeAdapter()) == []


# --- nets -------------------------------------------------------------------

def test_list_nets_sorted():
    adapter = FakeAdapter(nets=[Net(name="GND"), Net(name="+3V3"), Net(name="+3V3")])
    assert handlers.list_nets(adapter) == ["+3V3", "GND"]


# --- apply_config (validated write) -----------------------------------------

def test_apply_config_dry_run_returns_report_and_passes_arguments(monkeypatch):
    captured = {}

    def fake_run_apply(options):
        captured["options"] = options
        return ["=== DRY RUN ===", "Moves: none", "Vias: none"]

    monkeypatch.setattr("kicadstamp.apply_pipeline.run_apply", fake_run_apply)
    result = handlers.apply_config(
        "profiles/3ch-awg-tia-v103-test/3ch-awg-tia.sexp",
        dry_run=True, only=["A"], cluster=["C"], no_selection=True,
        timeout_ms=15000, batch_size=5, no_collision_check=True,
        collision_margin=0.5,
    )
    assert result == "=== DRY RUN ===\nMoves: none\nVias: none"
    opts = captured["options"]
    assert opts.config_path == "profiles/3ch-awg-tia-v103-test/3ch-awg-tia.sexp"
    assert opts.dry_run is True
    assert opts.only == ["A"]
    assert opts.cluster == ["C"]
    assert opts.no_selection is True
    assert opts.timeout_ms == 15000
    assert opts.batch_size == 5
    assert opts.no_collision_check is True
    assert opts.collision_margin == 0.5


def test_apply_config_real_run_captures_log_lines(monkeypatch):
    def fake_run_apply(options):
        logging.getLogger("kicadstamp.apply_pipeline").info(
            "All operations completed successfully")
        return None

    monkeypatch.setattr("kicadstamp.apply_pipeline.run_apply", fake_run_apply)
    result = handlers.apply_config("profiles/3ch-awg-tia-v103-test/3ch-awg-tia.sexp")
    assert "All operations completed successfully" in result
    assert "INFO" not in result  # plain lines, no level prefix


def test_apply_config_propagates_fatal_validation_error(monkeypatch):
    def fake_run_apply(options):
        raise ValidationError("connected board does not match this config")

    monkeypatch.setattr("kicadstamp.apply_pipeline.run_apply", fake_run_apply)
    with pytest.raises(ValidationError, match="connected board does not match"):
        handlers.apply_config("profiles/x.sexp")


# --- raw_move_footprint (high-risk write) ------------------------------------

def test_raw_move_footprint_requires_expected_board():
    """The board-identity guard is MANDATORY — expected_board_name is a
    required argument, so a raw write can never silently skip the check."""
    adapter = FakeAdapter(footprints=[_fp("R1")])
    with pytest.raises(TypeError):
        handlers.raw_move_footprint(adapter, "R1", x_mm=10.0, y_mm=20.0)


def test_raw_move_footprint_moves_and_reports_old_new():
    adapter = FakeAdapter(footprints=[_fp("R1", x_mm=1.0, y_mm=2.0, angle=0.0)])
    result = handlers.raw_move_footprint(adapter, "R1", x_mm=10.0, y_mm=20.0,
                                         expected_board_name="test_board.kicad_pcb",
                                         rotation_deg=45.0)
    assert result["board"] == "test_board.kicad_pcb"
    assert result["old"] == {"x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0, "layer": "F.Cu"}
    assert result["new"] == {"x_mm": 10.0, "y_mm": 20.0, "rotation_deg": 45.0, "layer": "F.Cu"}
    assert len(adapter.updated) == 1  # exactly one push


def test_raw_move_footprint_missing_ref_raises():
    adapter = FakeAdapter(footprints=[_fp("R1")])
    with pytest.raises(ValueError, match="not found"):
        handlers.raw_move_footprint(adapter, "R999", x_mm=0.0, y_mm=0.0,
                                    expected_board_name="test_board.kicad_pcb")


def test_raw_move_footprint_guard_blocks_on_wrong_board():
    adapter = FakeAdapter(footprints=[_fp("R1")], board_name="test_board.kicad_pcb")
    with pytest.raises(ValidationError):
        handlers.raw_move_footprint(adapter, "R1", x_mm=5.0, y_mm=5.0,
                                    expected_board_name="OTHER_BOARD")
    assert adapter.updated == []  # nothing was written


def test_raw_move_footprint_guard_passes_on_matching_board():
    adapter = FakeAdapter(footprints=[_fp("R1")], board_name="test_board.kicad_pcb")
    result = handlers.raw_move_footprint(adapter, "R1", x_mm=5.0, y_mm=5.0,
                                         expected_board_name="test_board.kicad_pcb")
    assert result["new"]["x_mm"] == 5.0
    assert result["new"]["y_mm"] == 5.0
    assert len(adapter.updated) == 1
