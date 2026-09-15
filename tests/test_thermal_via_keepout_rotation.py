# tests/test_thermal_via_keepout_rotation.py
"""Guards С5–С7 (Э2/Э3) of plan_2026_09_15_pad_geometry_thermal_vias.

The fixture is the REAL IC2 of the 3ch-awg-tia-v103 board, read live on
15.09.2026: an open 3.25x3.25 mm pad "33" with a ring of 32 pads
0.300x0.850 mm on a 0.5 mm pitch, rows at ±2.45 mm — pad 1 sits at
(−2.45, −1.75) mm with the body at 0°, exactly as the plan records. The via
array is Denis's own entry (ad_dac_via_pad: 4x4, margin 0.5 mm, via 0.5/0.3 mm,
clearance 0.2 mm, search 3.0 mm).

The fake adapter plays KiCad: it places a pad by turning its LOCAL position and
angle by the footprint's angle with the project's own primitive, and it returns
each pad's IDEAL bounding box, optionally shifted by the Д1 offset KiCad was
measured to return (every pad of a footprint at 315° comes back shifted by
(−0.899, −1.470) mm = 1.724 mm). That shift is what turns Denis's thermal via
array into "12 of 16 ideal points blocked, 4 vias off the pad", so the guards
below assert BOTH halves: the new rule is invariant, and the old rule visibly
breaks under exactly this fixture (otherwise the test would prove nothing).
"""
import logging
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.config import Config, ThermalViaArrayConfig
from kicadstamp.constants import PAD_SHAPE_RECT
from kicadstamp.domain.board import Footprint, Pad
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.geometry.keepout import build_keepout, point_is_clear
from kicadstamp.geometry.pad_area import pad_area_of
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.geometry.thermal_grid import compute_thermal_via_grid
from kicadstamp.placement.services.via_planner import ViaPlanner

MM = 1_000_000
# The measured Д1 shift of EVERY pad box of a footprint at 315°.
MEASURED_SHIFT_MM = (-0.899, -1.470)
_LOGGER_NAME = "kicadstamp.placement.services.via_planner"

# (pad number, local x mm, local y mm, size x mm, size y mm, local angle deg)
# Numbers follow the live read for pads 1-8 (left column, bottom to top) and
# 25-32 (bottom row, right to left); the right/top columns keep the mirror
# numbering. Only the geometric ring matters here — pad "33" is the only number
# anything resolves by name.
_BODY: list[tuple[str, float, float, float, float, float]] = [("33", 0.0, 0.0, 3.25, 3.25, 0.0)]
for _i in range(8):
    _y = -1.75 + 0.5 * _i
    _BODY.append((str(1 + _i), -2.45, _y, 0.300, 0.850, 90.0))
    _BODY.append((str(9 + _i), 2.45, _y, 0.300, 0.850, 90.0))
for _i in range(8):
    _x = 1.75 - 0.5 * _i
    _BODY.append((str(17 + _i), _x, 2.45, 0.300, 0.850, 0.0))
    _BODY.append((str(25 + _i), _x, -2.45, 0.300, 0.850, 0.0))


class _FakeBoard:
    """The smallest adapter the real ViaPlanner needs — and a faithful KiCad:
    pads are posed from their local geometry, so a rotated footprint really
    moves its pads."""

    def __init__(self, angle_deg: float = 0.0, box_shift_mm=(0.0, 0.0)):
        self.fp = Footprint(ref="IC2", uuid="fp-ic2",
                            position=Vector2.from_xy_mm(223.0, 114.238),
                            angle_deg=angle_deg, layer=BoardLayer.BL_F_Cu)
        self.box_shift = Vector2.from_xy_mm(*box_shift_mm)
        self.pads = [self._pose(spec) for spec in _BODY]
        self.bbox_batch_sizes: list[int] = []

    def _pose(self, spec) -> Pad:
        number, lx_mm, ly_mm, sx_mm, sy_mm, local_angle = spec
        offset = rotate_local_offset(lx_mm, ly_mm, self.fp.angle_deg)
        return Pad(number=number, net_name="GND",
                   position=Vector2(self.fp.position.x + offset.x,
                                    self.fp.position.y + offset.y),
                   size=Vector2.from_xy_mm(sx_mm, sy_mm),
                   angle_rad=math.radians(local_angle + self.fp.angle_deg),
                   shape=PAD_SHAPE_RECT)

    def pad(self, number: str) -> Pad:
        return next(p for p in self.pads if p.number == number)

    # --- the adapter surface the planner touches --------------------------
    def get_footprint(self, ref):
        return self.fp if ref == self.fp.ref else None

    def get_footprint_pads(self, fp):
        return list(self.pads)

    def get_pad_by_number(self, fp, number):
        return next((p for p in self.pads if p.number == number), None)

    def get_vias(self):
        return []

    def get_bounding_boxes(self, items):
        """The box KiCad OWES for each pad (the ideal one), optionally shifted
        by the measured Д1 offset — the defect, reproduced on demand."""
        self.bbox_batch_sizes.append(len(items))
        boxes = []
        for item in items:
            box = pad_area_of(item).bounds()
            box.pos = Vector2(box.pos.x + self.box_shift.x,
                              box.pos.y + self.box_shift.y)
            boxes.append(box)
        return boxes


def _config(diameter_mm: float = 0.5, margin_mm: float = 0.5,
            clearance_mm: float = 0.2) -> Config:
    return Config(
        layer="B.Cu", cells={}, chains=[], clone_placements=[],
        skip_existing_components=False,
        via_keepout_clearance_mm=clearance_mm,
        via_search_step_mm=0.1,
        via_search_max_radius_mm=3.0,
        via_search_n_directions=8,
        thermal_via_arrays=[ThermalViaArrayConfig(
            name="ad_dac_via_pad", pad="33", anchor_ref="IC2", net="GND",
            rows=4, cols=4, margin_mm=margin_mm, pattern="grid",
            drill_mm=0.3, diameter_mm=diameter_mm)],
    )


def _new_blocked(board: _FakeBoard, cfg: Config | None = None) -> list[int]:
    """The ideal-grid indices a via cannot use under the NEW rule — the very
    question _plan_thermal_vias asks per point, through the real
    ViaPlanner._build_keepout."""
    cfg = cfg or _config()
    tva = cfg.thermal_via_arrays[0]
    planner = ViaPlanner(board, cfg)
    points = compute_thermal_via_grid(board.pad("33"), tva.rows, tva.cols,
                                      tva.margin_mm)
    keepout = planner._build_keepout(board.fp, [], exclude={("IC2", tva.pad)})
    radius = tva.diameter_mm / 2.0 * MM
    return [i for i, p in enumerate(points) if not point_is_clear(p, radius, keepout)]


def _old_blocked(board: _FakeBoard, cfg: Config | None = None) -> list[int]:
    """The same indices under the OLD rule, rebuilt here exactly as the shipping
    code did it: KiCad's bounding box for every pad EXCEPT the thermal pad
    itself, inflated by the clearance, an axis-aligned Rect in board axes."""
    cfg = cfg or _config()
    tva = cfg.thermal_via_arrays[0]
    ring = [p for p in board.pads if p.number != tva.pad]
    keepout = build_keepout(board.get_bounding_boxes(ring),
                            cfg.via_keepout_clearance_mm, mm_per_unit=MM)
    points = compute_thermal_via_grid(board.pad("33"), tva.rows, tva.cols,
                                      tva.margin_mm)
    radius = tva.diameter_mm / 2.0 * MM
    return [i for i, p in enumerate(points) if not point_is_clear(p, radius, keepout)]


def _pad_local_um(board: _FakeBoard, position: Vector2) -> tuple[int, int]:
    """A via position as a pad-local offset in whole micrometres — the shape of
    the array as seen from the pad, so 0° and 315° can be compared as one
    drawing. Micrometres, not nanometres: the rotation primitive truncates to
    nm, and comparing raw nm would compare that truncation, not the drawing."""
    center = pad_area_of(board.pad("33")).center
    local = rotate_local_offset((position.x - center.x) / MM,
                                (position.y - center.y) / MM,
                                -board.fp.angle_deg)
    return (round(local.x / 1_000), round(local.y / 1_000))


# The 16 ideal grid points of pad 33 in pad-local micrometres: a 3.25 mm pad with
# margin 0.5 mm leaves ±1.125 / ±0.375 mm.
_IDEAL_LOCAL_UM = [(x, y) for x in (-1125, -375, 375, 1125)
                   for y in (-1125, -375, 375, 1125)]


def _thermal_vias(board: _FakeBoard, cfg: Config | None = None) -> list:
    vias = ViaPlanner(board, cfg or _config()).plan_vias([], [])
    return [v for v in vias if v.registry_key is not None]


class TestKeepoutInvarianceUnderRotation:
    """С5 — the same ideal grid points must be usable with the body at 0° and at
    315°, and the measured KiCad box shift must not change that."""

    def test_the_same_indices_are_blocked_at_0_and_at_315_degrees(self):
        at_zero = _new_blocked(_FakeBoard(angle_deg=0.0))
        at_315 = _new_blocked(_FakeBoard(angle_deg=315.0))
        # Measured on this fixture: with the pad's own geometry NOTHING blocks
        # Denis's 16 ideal points at 0° — and rotating the body must not change
        # which of them are blocked.
        assert at_zero == []
        assert at_315 == at_zero

    def test_the_measured_box_shift_does_not_change_the_new_result(self):
        shifted = _new_blocked(_FakeBoard(angle_deg=315.0,
                                          box_shift_mm=MEASURED_SHIFT_MM))
        assert shifted == _new_blocked(_FakeBoard(angle_deg=0.0))
        assert shifted == []

    def test_the_fixture_reproduces_the_live_defect_through_the_old_rule(self):
        """The other half of the guard: the OLD rule (KiCad's box as an
        axis-aligned Rect) blocks 12 of the 16 ideal points on exactly this
        fixture once the box is shifted — the live measurement Denis reported.
        Without this, "the new rule is invariant" would prove nothing."""
        assert _old_blocked(_FakeBoard(angle_deg=0.0)) == []
        # The same body at 315° with IDEAL boxes is still unblocked: the 12 come
        # from the SHIFT, not from the rotation.
        assert _old_blocked(_FakeBoard(angle_deg=315.0)) == []
        live = _old_blocked(_FakeBoard(angle_deg=315.0,
                                       box_shift_mm=MEASURED_SHIFT_MM))
        assert len(live) == 12

    def test_the_same_indices_are_blocked_when_the_grid_is_partly_occupied(self):
        """A second clearance where part of the grid is genuinely blocked at 0°
        by the ring pads: the SAME indices must be blocked at 315° — including
        with the shifted boxes (the new rule does not even look at them)."""
        cfg = _config(diameter_mm=0.9, margin_mm=0.2)
        at_zero = _new_blocked(_FakeBoard(angle_deg=0.0), cfg)
        assert at_zero != [], "fixture degenerate: this clearance must block something"
        assert _new_blocked(_FakeBoard(angle_deg=315.0), cfg) == at_zero
        assert _new_blocked(_FakeBoard(angle_deg=315.0,
                                       box_shift_mm=MEASURED_SHIFT_MM), cfg) == at_zero

    def test_plan_vias_keeps_all_16_vias_on_the_pad_under_rotation(self):
        board_0 = _FakeBoard(angle_deg=0.0)
        board_315 = _FakeBoard(angle_deg=315.0, box_shift_mm=MEASURED_SHIFT_MM)
        straight = _thermal_vias(board_0)
        turned = _thermal_vias(board_315)
        assert len(straight) == 16
        assert len(turned) == 16
        for board, vias in ((board_0, straight), (board_315, turned)):
            area = pad_area_of(board.pad("33"))
            assert [v for v in vias if not area.contains(v.position)] == []
            # every via is ON an ideal grid point (2 µm: the move would be a
            # whole search step of 0.1 mm if a point had been blocked)
            for via in vias:
                local = _pad_local_um(board, via.position)
                assert any(abs(local[0] - ix) <= 2 and abs(local[1] - iy) <= 2
                           for ix, iy in _IDEAL_LOCAL_UM), local
        # One and the same drawing, turned with the body: the pad-local offsets
        # of the 16 vias are identical at 0° and at 315°.
        assert (sorted(_pad_local_um(board_0, v.position) for v in straight)
                == sorted(_pad_local_um(board_315, v.position) for v in turned))

    def test_no_bbox_request_for_pads_that_have_their_own_area(self):
        """Э2's contract: a pad with its own area never reaches the adapter —
        and here every pad has one, so the request disappears entirely."""
        board = _FakeBoard(angle_deg=0.0)
        assert _new_blocked(board) is not None
        assert board.bbox_batch_sizes == []


class TestGridFollowsTheProjectRotation:
    """С6 — the array turns with the body the way the project turns things."""

    def _pad(self, angle_deg: float) -> Pad:
        return Pad(number="1", net_name="GND",
                   position=Vector2.from_xy_mm(10.0, 20.0),
                   size=Vector2.from_xy_mm(4.0, 2.0),
                   angle_rad=math.radians(angle_deg),
                   shape=PAD_SHAPE_RECT)

    def test_staggered_grid_at_90_degrees_equals_position_plus_rotate_ydown(self):
        points = compute_thermal_via_grid(self._pad(90.0), rows=2, cols=2,
                                          margin_mm=0.0, stagger=True)
        # local points of a 4x2 mm pad, staggered on the odd row
        local = [(-2.0, -1.0), (2.0, -1.0), (-1.0, 1.0), (3.0, 1.0)]
        expected = []
        for lx_mm, ly_mm in local:
            # cell_frame.rotate_ydown_mm: x' = x*cos + y*sin, y' = -x*sin + y*cos
            r = math.radians(90.0)
            rx = lx_mm * math.cos(r) + ly_mm * math.sin(r)
            ry = -lx_mm * math.sin(r) + ly_mm * math.cos(r)
            expected.append((10.0 + rx, 20.0 + ry))
        assert len(points) == 4
        for point, (ex_mm, ey_mm) in zip(points, expected):
            # 2 nm: the rotation primitive goes through mm floats and truncates,
            # as the previous inline arithmetic did too.
            assert abs(point.x - ex_mm * MM) <= 2, (point, ex_mm)
            assert abs(point.y - ey_mm * MM) <= 2, (point, ey_mm)
        # and the OTHER sign would have produced a different drawing
        assert [round(p.x / MM, 3) for p in points] == [9.0, 9.0, 11.0, 11.0]
        assert [round(p.y / MM, 3) for p in points] == [22.0, 18.0, 21.0, 17.0]

    def test_anchor_to_the_live_measurement_of_pad_1(self):
        """Pad 1 of IC2 sits at (−2.45, −1.75) mm from the body centre at 0° and
        at (−1.75, +2.45) mm at 90° (measured live on IC3)."""
        rotated = rotate_local_offset(-2.45, -1.75, 90.0)
        assert abs(rotated.x - (-1.75 * MM)) <= 2
        assert abs(rotated.y - (2.45 * MM)) <= 2


class TestGridIsCentredOnTheShape:
    """С7 — the grid is centred on the centre of the pad SHAPE, not on the hole."""

    def test_a_pad_with_an_offset_shape_is_gridded_around_the_shape(self):
        pad = Pad(number="1", net_name="GND",
                  position=Vector2.from_xy_mm(0.0, 0.0),
                  size=Vector2.from_xy_mm(1.0, 1.0),
                  angle_rad=0.0, shape=PAD_SHAPE_RECT,
                  offset=Vector2.from_xy_mm(0.5, 0.0))
        points = compute_thermal_via_grid(pad, rows=1, cols=1, margin_mm=0.0)
        assert len(points) == 1
        assert (points[0].x, points[0].y) == (int(0.5 * MM), 0)
        assert (points[0].x, points[0].y) != (pad.position.x, pad.position.y)

    def test_the_offset_is_turned_with_the_pad(self):
        pad = Pad(number="1", net_name="GND",
                  position=Vector2.from_xy_mm(10.0, 20.0),
                  size=Vector2.from_xy_mm(1.0, 1.0),
                  angle_rad=math.radians(90.0), shape=PAD_SHAPE_RECT,
                  offset=Vector2.from_xy_mm(1.0, 0.0))
        points = compute_thermal_via_grid(pad, rows=1, cols=1, margin_mm=0.0)
        assert len(points) == 1
        assert (points[0].x, points[0].y) == (10 * MM, 19 * MM)

    def test_a_pad_without_a_position_is_a_fatal_not_a_grid_at_the_origin(self):
        from kicadstamp.exceptions import GeometryError

        pad = Pad(number="1", net_name="GND", position=None,
                  size=Vector2.from_xy_mm(1.0, 1.0), shape=PAD_SHAPE_RECT)
        with pytest.raises(GeometryError):
            compute_thermal_via_grid(pad, rows=1, cols=1, margin_mm=0.0)
