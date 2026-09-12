# tests/test_net_trace_from_role.py
"""Э3.1 tests: a net_traces track/via may reference its net as (role, pad) —
net_from_role / net_from_role_pad — instead of a literal net
(plan_2026_09_12_internode_copper_core Э3.1; design §15).

The claim under test: a reference is resolved LIVE at apply time through the
same machinery a Cell's via/track uses — the role is searched over the whole
board with the record's own anchor_sheet/anchor_cluster narrowing, and the net
comes from net_from_role_pad (or lemma 2 when the pad is omitted). Everything
stays fatal rather than guessed: an unresolved/ambiguous role, a missing pad,
or a multi-net role without an explicit pad.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.config import NetTrace, TemplateTrack, TemplateVia
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config import load_config
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.net_trace_planner import plan_net_traces
from kicadstamp.utils.units import MM


def _fp(ref, x_mm=0.0, y_mm=0.0, role=None, cluster=None):
    return SimpleNamespace(ref=ref, position=Vector2.from_xy_mm(x_mm, y_mm),
                           angle_deg=0.0, _role=role, _cluster=cluster)


def _pad(number, net, x_mm=0.0, y_mm=0.0):
    return SimpleNamespace(number=str(number), net_name=net,
                           position=Vector2.from_xy_mm(x_mm, y_mm))


class _Adapter:
    """Minimal live-board double for the planner: footprints by role (with the
    custom-field lookup the narrowing cascade uses) and pads by ref + number."""

    def __init__(self, footprints, pads_by_ref, selected=()):
        self._fps = list(footprints)
        self._pads = pads_by_ref
        self._selected = list(selected)

    def get_footprints(self):
        return list(self._fps)

    def get_field_value(self, fp, name):
        return {"Role": getattr(fp, "_role", None),
                "Cluster": getattr(fp, "_cluster", None)}.get(name)

    def get_selected_items(self):
        return list(self._selected)

    def get_footprint(self, ref):
        return next((f for f in self._fps if f.ref == ref), None)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, []))

    def get_pad_by_number(self, fp, num):
        for p in self._pads.get(fp.ref, []):
            if str(p.number) == str(num):
                return p
        return None


def _anchor_pads():
    """The record's OWN anchor (role FPGA, pad 42) — always resolved first."""
    return [_pad("42", "/Channel_1/DAC/DB0", x_mm=50.0, y_mm=50.0)]


def _record(tracks=None, vias=None, **overrides):
    fields = dict(net="DAC_DB0", anchor_role="FPGA", anchor_pad="42",
                  tracks=tracks or [], vias=vias or [])
    fields.update(overrides)
    return NetTrace(**fields)


def _board(footprints, pads_by_ref):
    pads = {"U_FPGA": _anchor_pads()}
    pads.update(pads_by_ref)
    return _Adapter(footprints, pads)


# ── resolving a (role, pad) reference live ────────────────────────────────

def test_track_net_from_role_is_resolved_live_from_the_pad():
    track = TemplateTrack(start_along_mm=0, start_across_mm=0,
                          end_along_mm=1, end_across_mm=1, width_mm=0.25,
                          net_from_role="DAC_BUF", net_from_role_pad="5",
                          layer="F.Cu")
    nt = _record(tracks=[track])
    adapter = _board(
        [_fp("U_FPGA", role="FPGA"), _fp("U_DAC_BUF", role="DAC_BUF")],
        {"U_DAC_BUF": [_pad("5", "/Channel_1/DAC/+3V3_AVDD")]},
    )
    vias, tracks = plan_net_traces(adapter, [nt])
    assert [t.net_name for t in tracks] == ["/Channel_1/DAC/+3V3_AVDD"]


def test_via_net_from_role_is_resolved_live_from_the_pad():
    via = TemplateVia(offset_along_mm=0, offset_across_mm=0,
                      net_from_role="DAC_BUF", net_from_role_pad="5",
                      drill_mm=0.3, diameter_mm=0.6)
    nt = _record(vias=[via])
    adapter = _board(
        [_fp("U_FPGA", role="FPGA"), _fp("U_DAC_BUF", role="DAC_BUF")],
        {"U_DAC_BUF": [_pad("5", "/Channel_1/DAC/+3V3_AVDD")]},
    )
    vias, tracks = plan_net_traces(adapter, [nt])
    assert [v.net_name for v in vias] == ["/Channel_1/DAC/+3V3_AVDD"]


def test_net_from_role_without_a_pad_uses_lemma_two():
    """No net_from_role_pad: the role must carry exactly ONE non-rule net —
    GND is a rule net (RULE_NETS) and does not count."""
    track = TemplateTrack(net_from_role="DAC_BUF", layer="F.Cu")
    nt = _record(tracks=[track])
    adapter = _board(
        [_fp("U_FPGA", role="FPGA"), _fp("U_DAC_BUF", role="DAC_BUF")],
        {"U_DAC_BUF": [_pad("1", "/Channel_1/DAC/+3V3_AVDD"), _pad("2", "GND")]},
    )
    _vias, tracks = plan_net_traces(adapter, [nt])
    assert [t.net_name for t in tracks] == ["/Channel_1/DAC/+3V3_AVDD"]


def test_net_from_role_narrowed_by_the_records_own_cluster():
    """Two components share the role (the channel-copy case) — the record's own
    anchor_cluster picks the instance, exactly as it does for the anchor."""
    track = TemplateTrack(net_from_role="DAC_BUF", net_from_role_pad="5",
                          layer="F.Cu")
    nt = _record(tracks=[track], anchor_cluster="PIF_DVDD")
    adapter = _board(
        [_fp("U_FPGA", role="FPGA"),
         _fp("U_A", role="DAC_BUF", cluster="PIF_AVDD"),
         _fp("U_B", role="DAC_BUF", cluster="PIF_DVDD")],
        {"U_A": [_pad("5", "/Channel_1/AVDD/+3V3")],
         "U_B": [_pad("5", "/Channel_1/DVDD/+3V3")]},
    )
    _vias, tracks = plan_net_traces(adapter, [nt])
    assert [t.net_name for t in tracks] == ["/Channel_1/DVDD/+3V3"]


# ── fatal, never a guess ──────────────────────────────────────────────────

def test_missing_pad_on_the_resolved_role_is_fatal():
    track = TemplateTrack(net_from_role="DAC_BUF", net_from_role_pad="9",
                          layer="F.Cu")
    nt = _record(tracks=[track])
    adapter = _board(
        [_fp("U_FPGA", role="FPGA"), _fp("U_DAC_BUF", role="DAC_BUF")],
        {"U_DAC_BUF": [_pad("5", "/Channel_1/DAC/+3V3_AVDD")]},
    )
    with pytest.raises(ValidationError, match="not found or has no net"):
        plan_net_traces(adapter, [nt])


def test_role_absent_from_the_board_is_fatal():
    track = TemplateTrack(net_from_role="NOWHERE", layer="F.Cu")
    nt = _record(tracks=[track])
    adapter = _board([_fp("U_FPGA", role="FPGA")], {})
    with pytest.raises(ValidationError, match="not found on any component"):
        plan_net_traces(adapter, [nt])


def test_multi_net_role_without_a_pad_is_fatal():
    track = TemplateTrack(net_from_role="DAC_BUF", layer="F.Cu")
    nt = _record(tracks=[track])
    adapter = _board(
        [_fp("U_FPGA", role="FPGA"), _fp("U_DAC_BUF", role="DAC_BUF")],
        {"U_DAC_BUF": [_pad("1", "/A/VDD"), _pad("2", "/A/VDD2")]},
    )
    with pytest.raises(ValidationError, match="not exactly one"):
        plan_net_traces(adapter, [nt])


# ── back-compat: a literal net and the record-level fall-back still win ───

def test_literal_item_net_is_used_as_before():
    track = TemplateTrack(net="LITERAL", layer="F.Cu")
    via = TemplateVia(net="VIA_NET", drill_mm=0.3, diameter_mm=0.6)
    nt = _record(tracks=[track], vias=[via])
    adapter = _board([_fp("U_FPGA", role="FPGA")], {})
    vias, tracks = plan_net_traces(adapter, [nt])
    assert [t.net_name for t in tracks] == ["LITERAL"]
    assert [v.net_name for v in vias] == ["VIA_NET"]


def test_item_without_a_net_falls_back_to_the_record_net():
    track = TemplateTrack(layer="F.Cu")
    nt = _record(tracks=[track])
    adapter = _board([_fp("U_FPGA", role="FPGA")], {})
    _vias, tracks = plan_net_traces(adapter, [nt])
    assert [t.net_name for t in tracks] == ["DAC_DB0"]


def test_registry_key_keeps_the_net_prefix_and_uses_the_identity():
    """A legacy record's key is byte-identical to before; a named record keys by
    its name — so two bridges of one net never collide in the registry."""
    legacy = _record(tracks=[TemplateTrack(net="LITERAL", layer="F.Cu")])
    named = _record(name="bridge__a__b",
                    tracks=[TemplateTrack(net="LITERAL", layer="F.Cu")])
    adapter = _board([_fp("U_FPGA", role="FPGA")], {})
    _v, legacy_tracks = plan_net_traces(adapter, [legacy])
    _v2, named_tracks = plan_net_traces(adapter, [named])
    assert legacy_tracks[0].registry_key.startswith("net:DAC_DB0|DAC_DB0|")
    assert named_tracks[0].registry_key.startswith("net:bridge__a__b|bridge__a__b|")


# ── loader: the two fields are mutually exclusive, pad needs a role ───────

def test_loader_rejects_net_and_net_from_role_on_one_item(tmp_path):
    path = tmp_path / "board.sexp"
    path.write_text(dict_to_sexp({"net_traces": [{
        "net": "DAC_DB0", "anchor_role": "FPGA",
        "tracks": [{"start_along_mm": 1.0, "start_across_mm": 2.0,
                    "end_along_mm": 3.0, "end_across_mm": 4.0,
                    "width_mm": 0.2, "net": "DAC_DB0",
                    "net_from_role": "DAC_BUF", "layer": "F.Cu"}],
    }]}), encoding="utf-8")
    with pytest.raises(ValidationError, match="mutually exclusive"):
        load_config(str(path))


def test_loader_rejects_net_from_role_pad_without_a_role(tmp_path):
    path = tmp_path / "board.sexp"
    path.write_text(dict_to_sexp({"net_traces": [{
        "net": "DAC_DB0", "anchor_role": "FPGA",
        "vias": [{"offset_along_mm": 1.0, "offset_across_mm": 2.0,
                  "net_from_role_pad": "5", "drill_mm": 0.3,
                  "diameter_mm": 0.6}],
    }]}), encoding="utf-8")
    with pytest.raises(ValidationError, match="without via.net_from_role"):
        load_config(str(path))


def test_loader_keeps_the_record_level_net_required_with_role_items(tmp_path):
    """The record still needs `net:` (which network this copper belongs to) even
    when every item carries a (role, pad) reference."""
    path = tmp_path / "board.sexp"
    path.write_text(dict_to_sexp({"net_traces": [{
        "anchor_role": "FPGA",
        "vias": [{"offset_along_mm": 1.0, "offset_across_mm": 2.0,
                  "net_from_role": "DAC_BUF", "drill_mm": 0.3,
                  "diameter_mm": 0.6}],
    }]}), encoding="utf-8")
    with pytest.raises(ValidationError, match="without net"):
        load_config(str(path))
