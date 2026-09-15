# tests/test_template_selection_pad_area.py
"""Guards С8–С9 (Э4) of plan_2026_09_15_pad_geometry_thermal_vias.

`template_selection._inflated_boxes` / `_point_in_box` are the seam through which
BOTH consumers of "is this point on that pad" go: the Extract connectivity
closure (which decides whether a selected track is anchored at a kept pad) and
`internode_copper.find_copper_units` (which moors copper to pads). Before Э4 the
seam asked KiCad for a bounding box per pad, and that box is measurably unusable
for a rotated footprint: shifted by 1.724 mm for every pad of IC2 at 315° (Д1)
and an axis-aligned box around a rotated pad even when it is not shifted (Д2).

The guard for the inter-node half drives the REAL find_copper_units — through the
seam, without editing internode_copper.py (P.2.2 of the plan forbids that file:
a parallel task owns it).
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.constants import PAD_SHAPE_CUSTOM, PAD_SHAPE_RECT
from kicadstamp.domain.board import Footprint, Pad, Track
from kicadstamp.domain.geometry import BoardLayer, Box2, Vector2
from kicadstamp.geometry.pad_area import pad_area_of
from kicadstamp.internode_copper import PadRef, find_copper_units
from kicadstamp.template_selection import _filter_tracks_and_vias_within_selection

MM = 1_000_000
# The measured Д1 shift of every pad box of a footprint at 315°.
MEASURED_SHIFT_MM = (-0.899, -1.470)
_FP_POSITION_MM = (100.0, 100.0)


def _footprint(angle_deg: float, pad_shape: str) -> Footprint:
    return Footprint(ref="IC2", uuid="fp-ic2",
                     position=Vector2.from_xy_mm(*_FP_POSITION_MM),
                     angle_deg=angle_deg, layer=BoardLayer.BL_F_Cu)


def _mid_pad(shape: str = PAD_SHAPE_RECT) -> Pad:
    """The AD9707 signal pad (measured live): 0.300 x 0.850 mm, sitting at 45° in
    its own footprint, i.e. the worst case for an axis-aligned box."""
    return Pad(number="1", net_name="GND",
               position=Vector2.from_xy_mm(*_FP_POSITION_MM),
               size=Vector2.from_xy_mm(0.300, 0.850),
               angle_rad=math.radians(45.0), shape=shape)


class _Board:
    """The adapter surface template_selection needs: pads per footprint and one
    batch of boxes, whose geometry is the IDEAL box of each pad optionally shifted
    by the measured Д1 offset (what KiCad actually returns there)."""

    def __init__(self, pads: list[Pad], shift_mm=(0.0, 0.0), ideal_boxes: bool = True):
        self._pads = pads
        self._shift = Vector2.from_xy_mm(*shift_mm)
        self._ideal_boxes = ideal_boxes
        self.bbox_requests: list[int] = []

    def get_footprint_pads(self, footprint):
        return list(self._pads)

    def get_bounding_boxes(self, items):
        self.bbox_requests.append(len(items))
        boxes = []
        for item in items:
            box = self._ideal_box(item)
            if self._shift.x or self._shift.y:
                box.pos = Vector2(box.pos.x + self._shift.x, box.pos.y + self._shift.y)
            boxes.append(box)
        return boxes

    @staticmethod
    def _ideal_box(item) -> Box2:
        """The box KiCad OWES this item. For a pad with its own area that is the
        area's AABB; for a fallback pad (custom shape) it is the AABB of its size
        rectangle, computed here the same way."""
        area = pad_area_of(item)
        if area is not None:
            return area.bounds()
        r = item.angle_rad
        half_w, half_h = item.size.x / 2.0, item.size.y / 2.0
        half_x = abs(half_w * math.cos(r)) + abs(half_h * math.sin(r))
        half_y = abs(half_w * math.sin(r)) + abs(half_h * math.cos(r))
        return Box2(pos=Vector2(int(item.position.x - half_x),
                                int(item.position.y - half_y)),
                    size=Vector2(int(2 * half_x), int(2 * half_y)))


def _track(start_mm, end_mm, net: str = "GND") -> Track:
    return Track(uuid="t-1", start=Vector2.from_xy_mm(*start_mm),
                 end=Vector2.from_xy_mm(*end_mm),
                 net_name=net, width_mm=0.25, layer=BoardLayer.BL_F_Cu)


def _closure(adapter: _Board, track: Track, footprint: Footprint):
    """The Extract closure as the shipping code calls it (collect_dropped=True so
    a dropped track is visible rather than inferred)."""
    return _filter_tracks_and_vias_within_selection(
        [track], [], [footprint], adapter, collect_dropped=True)


class TestExtractClosureUsesThePadsOwnArea:
    """С8 — the Extract half of the seam."""

    def test_a_track_end_on_a_rotated_pad_is_kept_despite_a_shifted_kicad_box(self):
        """The end sits DEAD CENTRE on the pad while the box KiCad returns for
        that pad is 1.724 mm away: the closure must keep the track (it did before
        this change only because the box happened to be unshifted)."""
        fp = _footprint(angle_deg=315.0, pad_shape=PAD_SHAPE_RECT)
        adapter = _Board([_mid_pad()], shift_mm=MEASURED_SHIFT_MM)
        track = _track((100.0, 100.0), (101.0, 100.0))  # starts on the pad centre

        kept, _vias, dropped, _dropped_vias = _closure(adapter, track, fp)
        assert kept == [track]
        assert dropped == []
        # and the pad's own geometry really is what was used: the adapter was
        # asked for NOTHING
        assert adapter.bbox_requests == []

    def test_a_track_end_inside_the_axis_aligned_box_but_off_the_pad_is_not_anchored(self):
        """The two ends sit in the CORNERS of the pad's axis-aligned box — inside
        the box (so the old rule anchored them), well off the rotated copper. The
        track must be dropped as unanchored material."""
        fp = _footprint(angle_deg=0.0, pad_shape=PAD_SHAPE_RECT)
        adapter = _Board([_mid_pad()])
        track = _track((100.40, 100.40), (100.40, 99.60))

        kept, _vias, dropped, _dropped_vias = _closure(adapter, track, fp)
        assert kept == []
        assert dropped == [track]

    def test_the_fallback_still_anchors_through_kicad_boxes(self):
        """Control: a pad with no area of its own (custom) keeps the OLD
        behaviour — the box it gets from the adapter still anchors a track end
        that lands inside it. The fallback was not removed, only narrowed."""
        custom = _mid_pad(shape=PAD_SHAPE_CUSTOM)
        assert pad_area_of(custom) is None
        fp = _footprint(angle_deg=0.0, pad_shape=PAD_SHAPE_CUSTOM)
        adapter = _Board([custom])
        track = _track((100.40, 100.40), (100.40, 99.60))

        kept, _vias, dropped, _dropped_vias = _closure(adapter, track, fp)
        assert kept == [track]
        assert dropped == []
        assert adapter.bbox_requests == [1]

    def test_a_shifted_box_does_not_anchor_a_point_off_the_pad(self):
        """The mirror image of the first guard, and the reason the fallback is
        warned about: for a CUSTOM pad the shifted box is all we have, so a point
        that the shift moved into it reads as "on the pad" — the defect Д1 that
        the warning is about. Asserted so the guard above is visibly about the
        own area, not about the fallback being harmless."""
        custom = _mid_pad(shape=PAD_SHAPE_CUSTOM)
        fp = _footprint(angle_deg=315.0, pad_shape=PAD_SHAPE_CUSTOM)
        adapter = _Board([custom], shift_mm=MEASURED_SHIFT_MM)
        # The shifted box is the ideal one (centred on the pad) moved by the Д1
        # vector: a point at that box's CENTRE is 1.7 mm off the pad, in the air.
        track = _track((100.0 + MEASURED_SHIFT_MM[0], 100.0 + MEASURED_SHIFT_MM[1]),
                       (100.0 + MEASURED_SHIFT_MM[0], 99.0))

        kept, _vias, dropped, _dropped_vias = _closure(adapter, track, fp)
        assert kept == [track]
        assert dropped == []
        assert adapter.bbox_requests == [1]


class TestInterNodeCopperAttachesThroughTheSameSeam:
    """С9 — the inter-node half, through the REAL find_copper_units and the real
    (untouched) internode_copper.py."""

    @staticmethod
    def _fixture(pad_shape: str, angle_deg: float = 315.0):
        """A body at 315° with two real ring pads (0.300 x 0.850 mm, 0.5 mm
        apart, long axis along the body's Y — the live IC2 geometry) and a track
        running from pad centre to pad centre."""
        fp = _footprint(angle_deg=angle_deg, pad_shape=pad_shape)
        pads = []
        for number, local_y_mm in (("1", -1.75), ("2", -1.25)):
            # local (x=-2.45, y) turned with the body, the way KiCad poses a pad
            r = math.radians(angle_deg)
            lx_mm, ly_mm = -2.45, local_y_mm
            dx = lx_mm * math.cos(r) + ly_mm * math.sin(r)
            dy = -lx_mm * math.sin(r) + ly_mm * math.cos(r)
            pads.append(Pad(number=number, net_name="N",
                            position=Vector2.from_xy_mm(_FP_POSITION_MM[0] + dx,
                                                        _FP_POSITION_MM[1] + dy),
                            size=Vector2.from_xy_mm(0.300, 0.850),
                            angle_rad=math.radians(90.0 + angle_deg),
                            shape=pad_shape))
        track = _track((pads[0].position.x / MM, pads[0].position.y / MM),
                       (pads[1].position.x / MM, pads[1].position.y / MM), net="N")
        return fp, pads, track

    def test_copper_is_moored_to_the_right_pads_even_with_shifted_boxes(self):
        fp, pads, track = self._fixture(PAD_SHAPE_RECT)
        adapter = _Board(pads, shift_mm=MEASURED_SHIFT_MM)

        units, warnings = find_copper_units(adapter, [track], footprints=[fp])

        assert warnings == []
        assert len(units) == 1
        assert units[0].pads == (PadRef("IC2", "1"), PadRef("IC2", "2"))
        # the shift never even reaches the adapter: the pads have their own areas
        assert adapter.bbox_requests == []

    def test_the_same_fixture_through_the_shifted_boxes_alone_loses_the_pads(self):
        """The control that makes the guard above mean something: the SAME pad
        positions and sizes, only without an area of their own — i.e. exactly what
        the seam saw before Э4 — and the shifted boxes miss the track ends, so the
        copper is moored to no pad at all."""
        fp, pads, track = self._fixture(PAD_SHAPE_CUSTOM)
        adapter = _Board(pads, shift_mm=MEASURED_SHIFT_MM)

        units, _warnings = find_copper_units(adapter, [track], footprints=[fp])

        assert len(units) == 1
        assert units[0].pads == ()
        assert adapter.bbox_requests == [2]
