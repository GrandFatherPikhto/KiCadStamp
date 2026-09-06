"""Scheme List capture (plan_2026_09_05_scheme_list.md P2) — pure capture over
a mock adapter.

Scenario geometry (mm; Y-down board frame is irrelevant here since the closure
is coordinate-only):
  R1(10,10) --t1 F.Cu--> C1(20,10) --t2 In1.Cu--> C2(24,10)
  via v1 at (20,10)                     (inner-layer track round-trip)
  J1(15,12) is a FOREIGN component (NOT recorded) whose pad GND drags a stub
  tf(15,12)->(15,14) — that stub is dropped by the closure and must surface as
  a boundary_net (not be silently captured). J1 sits inside the refs' bbox+1mm
  so its stub survives the pre-filter and reaches the closure.
"""
import pytest

from kicadstamp.domain.board import Footprint, Pad, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Box2, Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.scheme_list_capture import (
    _prefilter_copper,
    _segment_intersects_box,
    capture_scheme_list,
)
from kicadstamp.utils.units import MM

F = BoardLayer.BL_F_Cu
B = BoardLayer.BL_B_Cu
IN1 = BoardLayer.BL_In1_Cu

_V5 = "/Channel_0/AMP/+5V"
_GND = "/Channel_0/AMP/GND"


def _mm_xy(x_mm, y_mm):
    return Vector2.from_xy_mm(x_mm, y_mm)


def _fp(ref, x_mm, y_mm, angle=0.0, layer=F):
    return Footprint(ref=ref, uuid=f"uuid-{ref}", position=_mm_xy(x_mm, y_mm),
                     angle_deg=angle, layer=layer)


def _pad(fp_ref, x_mm, y_mm, net, number="1"):
    return Pad(number=number, net_name=net, position=_mm_xy(x_mm, y_mm),
               size=Vector2.from_xy_mm(1.0, 1.0))


def _track(x1, y1, x2, y2, net, layer=F, width=0.25):
    return Track(uuid=f"t-{x1}-{y1}-{x2}-{y2}", start=_mm_xy(x1, y1),
                 end=_mm_xy(x2, y2), net_name=net, width_mm=width, layer=layer)


def _via(x_mm, y_mm, net, drill=0.3, diam=0.6):
    return Via(uuid=f"v-{x_mm}-{y_mm}", position=_mm_xy(x_mm, y_mm),
               net_name=net, drill_mm=drill, diameter_mm=diam)


class FakeAdapter:
    """Mock board adapter: pads keyed by footprint ref, boxes around items."""

    def __init__(self, footprints, tracks, vias, pads_by_ref):
        self._fps = footprints
        self._tracks = tracks
        self._vias = vias
        self._pads = pads_by_ref

    def get_footprints(self):
        return list(self._fps)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, []))

    def get_bounding_boxes(self, items):
        out = []
        for it in items:
            if isinstance(it, Footprint):
                half = int(2.0 * MM)
            elif isinstance(it, Pad):
                half = int(0.5 * MM)
            elif isinstance(it, Via):
                half = max(int((it.diameter_mm / 2) * MM), int(0.25 * MM))
            else:
                out.append(None)
                continue
            p = it.position
            out.append(Box2(pos=Vector2.from_xy(p.x - half, p.y - half),
                            size=Vector2.from_xy(2 * half, 2 * half)))
        return out


def _scenario():
    """The docstring geometry; returns (adapter, refs)."""
    r1 = _fp("R1", 10, 10)
    c1 = _fp("C1", 20, 10, angle=90.0)
    c2 = _fp("C2", 24, 10)
    j1 = _fp("J1", 15, 12)
    fps = [r1, c1, c2, j1]
    pads = {
        "R1": [_pad("R1", 10, 10, _V5)],
        "C1": [_pad("C1", 20, 10, _V5)],
        "C2": [_pad("C2", 24, 10, _V5)],
        "J1": [_pad("J1", 15, 12, _GND)],
    }
    t1 = _track(10, 10, 20, 10, _V5, layer=F)
    t2 = _track(20, 10, 24, 10, _V5, layer=IN1)
    tf = _track(15, 12, 15, 14, _GND, layer=F)  # J1 stub -> boundary
    v1 = _via(20, 10, _V5)
    adapter = FakeAdapter(fps, [t1, t2, tf], [v1], pads)
    return adapter, ["R1", "C1", "C2"]


class TestCaptureHappyPath:
    def setup_method(self):
        self.adapter, self.refs = _scenario()
        self.cfg = capture_scheme_list("amp", self.refs, "R1", adapter=self.adapter)

    def test_identity_and_anchor(self):
        assert self.cfg.name == "amp"
        assert self.cfg.anchor_ref == "R1"
        assert self.cfg.anchor_pad is None
        # anchor R1 is the offset origin -> offset (0, 0)
        assert self.cfg.components[0].ref == "R1"
        assert self.cfg.components[0].offset_along_mm == 0.0
        assert self.cfg.components[0].offset_across_mm == 0.0

    def test_components_literal_refs_and_offsets(self):
        comps = {c.ref: c for c in self.cfg.components}
        assert list(comps) == ["R1", "C1", "C2"]
        # origin is R1 centre (10,10) — board-frame dx/dy offsets
        assert comps["C1"].offset_along_mm == pytest.approx(10.0)
        assert comps["C1"].offset_across_mm == pytest.approx(0.0)
        assert comps["C1"].rotation_deg == pytest.approx(90.0)  # absolute rotation
        assert comps["C2"].offset_along_mm == pytest.approx(14.0)

    def test_tracks_literal_net_and_inner_layer_string(self):
        by_layer = {t.layer: t for t in self.cfg.tracks}
        assert set(by_layer) == {"F.Cu", "In1.Cu"}  # inner layer NOT collapsed
        assert by_layer["F.Cu"].net == _V5
        assert by_layer["In1.Cu"].net == _V5
        assert by_layer["F.Cu"].width_mm == pytest.approx(0.25)
        # offsets in the anchor frame: t1 (10,10)->(20,10) becomes (0,0)->(10,0)
        assert by_layer["F.Cu"].start_along_mm == pytest.approx(0.0)
        assert by_layer["F.Cu"].end_along_mm == pytest.approx(10.0)

    def test_via_literal_record(self):
        assert len(self.cfg.vias) == 1
        via = self.cfg.vias[0]
        assert via.net == _V5
        assert via.offset_along_mm == pytest.approx(10.0)  # at C1 (20,10)
        assert via.offset_across_mm == pytest.approx(0.0)
        assert via.drill_mm == pytest.approx(0.3)
        assert via.diameter_mm == pytest.approx(0.6)

    def test_foreign_stub_is_boundary_not_captured(self):
        # J1's GND stub was dropped (not silently captured into tracks)
        assert all(t.net != _GND for t in self.cfg.tracks)
        assert len(self.cfg.boundary_nets) == 1
        bn = self.cfg.boundary_nets[0]
        assert bn.net == _GND
        assert bn.action == "exclude"
        assert bn.external_ref == "J1"  # diagnostics: which external fp dragged it

    def test_source_sheet_from_anchor_local_net(self):
        assert self.cfg.source_sheet == "Channel_0"


class TestCaptureFatals:
    def test_missing_refs_reported_in_one_fatal(self):
        adapter, refs = _scenario()
        with pytest.raises(ValidationError) as ei:
            capture_scheme_list("amp", refs + ["ZZ9", "QQ7"], "R1", adapter=adapter)
        msg = str(ei.value)
        assert "ZZ9" in msg and "QQ7" in msg

    def test_empty_refs_fatal(self):
        adapter, _ = _scenario()
        with pytest.raises(ValidationError):
            capture_scheme_list("amp", [], "R1", adapter=adapter)

    def test_anchor_not_among_refs_fatal(self):
        adapter, refs = _scenario()
        with pytest.raises(ValidationError):
            capture_scheme_list("amp", refs, "J1", adapter=adapter)

    def test_anchor_pad_not_found_fatal(self):
        adapter, refs = _scenario()
        with pytest.raises(ValidationError, match="pad"):
            capture_scheme_list("amp", refs, "R1", anchor_pad="9", adapter=adapter)

    def test_anchor_pad_origin_used_when_found(self):
        adapter, refs = _scenario()
        cfg = capture_scheme_list("amp", refs, "C1", anchor_pad="1", adapter=adapter)
        # origin = C1's pad = C1 centre (20,10) -> C2 offset (4, 0)
        comps = {c.ref: c for c in cfg.components}
        assert comps["C2"].offset_along_mm == pytest.approx(4.0)
        assert cfg.anchor_pad == "1"


class TestFarForeignCopperIsOutOfRegion:
    def test_far_copper_is_neither_captured_nor_boundary(self):
        adapter, refs = _scenario()
        far = _fp("J2", 100, 100)
        adapter._fps.append(far)
        adapter._pads["J2"] = [_pad("J2", 100, 100, "/Channel_1/AMP/CLK")]
        adapter._tracks.append(_track(100, 100, 100, 104, "/Channel_1/AMP/CLK", layer=B))
        cfg = capture_scheme_list("amp", refs, "R1", adapter=adapter)
        # far copper neither recorded nor reported — it is outside the region
        assert all(t.net != "/Channel_1/AMP/CLK" for t in cfg.tracks)
        assert all(bn.net != "/Channel_1/AMP/CLK" for bn in cfg.boundary_nets)


class TestPrefilterHelpers:
    def _region(self, x0, y0, x1, y1):
        return Box2(pos=Vector2.from_xy_mm(x0, y0), size=Vector2.from_xy_mm(x1 - x0, y1 - y0))

    def test_segment_crossing_region_is_kept_with_both_ends_outside(self):
        # Long track passes THROUGH the region but both endpoints are outside —
        # the plan's criterion (segment intersects bbox) must keep it.
        region = self._region(10, 10, 20, 20)
        crossing = _track(5, 15, 25, 15, "NET")
        assert _segment_intersects_box(crossing.start, crossing.end, region) is True
        kept_t, kept_v = _prefilter_copper([crossing], [], region)
        assert kept_t == [crossing]

    def test_parallel_track_outside_is_dropped(self):
        region = self._region(10, 10, 20, 20)
        outside = _track(5, 30, 25, 30, "NET")  # y=30 > 20, parallel, no crossing
        assert _segment_intersects_box(outside.start, outside.end, region) is False
        kept_t, _ = _prefilter_copper([outside], [], region)
        assert kept_t == []
