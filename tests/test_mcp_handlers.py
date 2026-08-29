# tests/test_mcp_handlers.py
"""Unit tests for mcp_server/handlers.py — READ tools, no live KiCad.

Handlers take a "live adapter" argument; tests inject a fake adapter built on
the real domain DTOs (Footprint/Pad/Net/Via/Track/Vector2). Covers board
identity, ref filtering, nm->mm conversion and layer strings, the missing-ref
path, selection kind mapping and net listing.
"""
from kicadstamp.domain.board import Footprint, Net, Pad, Track, Via
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
