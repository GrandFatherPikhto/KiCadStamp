# tests/test_pad_area.py
"""Guards for the pad's OWN geometry — Э1 (С1–С4, С11) of
plan_2026_09_15_pad_geometry_thermal_vias.

The pad area replaces KiCad's bounding box, which is measurably unusable for a
footprint rotated by an angle that is not a multiple of 90° (Д1: every pad of
IC2 at 315° came back shifted by 1.724 mm) and rotation-dependent for ANY
rotated pad (Д2: an axis-aligned box around a rotated rectangle is up to 2.6x
the copper area, so the same copper reads "occupied" at 45° and "free" at 0°).
"""
import logging
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kipy.board_types import Pad as KipyPad, PadStackShape

from kicadstamp.constants import (
    PAD_SHAPE_CHAMFERED,
    PAD_SHAPE_CIRCLE,
    PAD_SHAPE_CUSTOM,
    PAD_SHAPE_OVAL,
    PAD_SHAPE_RECT,
    PAD_SHAPE_ROUNDRECT,
    PAD_SHAPE_TRAPEZOID,
    PAD_SHAPE_UNKNOWN,
)
from kicadstamp.domain.board import Pad, pad_from_kipy
from kicadstamp.domain.geometry import Vector2
from kicadstamp.geometry.pad_area import pad_area_of, pad_angle_deg, warn_bbox_fallback

MM = 1_000_000
_LOGGER_NAME = "tests.test_pad_area"


def _pad(number="33", x_mm=0.0, y_mm=0.0, w_mm=0.30, h_mm=0.85, angle_deg=0.0,
         shape=PAD_SHAPE_RECT, offset_mm=None, trapezoid_delta_mm=None):
    """The AD9707's signal pad by default: 0.30 x 0.85 mm (measured live on IC2
    of the 3ch-awg-tia-v103 test board)."""
    return Pad(
        number=number, net_name="GND",
        position=Vector2.from_xy_mm(x_mm, y_mm),
        size=Vector2.from_xy_mm(w_mm, h_mm),
        angle_rad=math.radians(angle_deg),
        shape=shape,
        offset=Vector2.from_xy_mm(*offset_mm) if offset_mm else None,
        trapezoid_delta=(Vector2.from_xy_mm(*trapezoid_delta_mm)
                         if trapezoid_delta_mm else None),
    )


def _box_contains(box, x_nm, y_nm, tolerance_nm=1.0) -> bool:
    """A 1 nm tolerance: the box's own edges are int-truncated (the same
    truncation the project's rotation primitive does), so a corner that lands
    on an edge can sit one nanometre outside the stored bound. A missing
    `abs()` in bounds() is orders of magnitude larger than that."""
    return (box.pos.x - tolerance_nm <= x_nm <= box.pos.x + box.size.x + tolerance_nm
            and box.pos.y - tolerance_nm <= y_nm <= box.pos.y + box.size.y + tolerance_nm)


def _warnings(caplog) -> list:
    """Only this module's own log records — other loggers may have written to
    the same caplog capture."""
    return [r for r in caplog.records if r.name == _LOGGER_NAME]


def _rotate_ydown_mm(x_mm, y_mm, deg):
    """The project's own rotation primitive, inlined (same formula as
    cell_frame.rotate_ydown_mm and rotate_local_offset)."""
    r = math.radians(deg)
    return (x_mm * math.cos(r) + y_mm * math.sin(r),
            -x_mm * math.sin(r) + y_mm * math.cos(r))


class TestRectAreaBoundaries:
    """С1 — a rectangle at 0°: the boundary belongs to the pad, a step outside
    does not. Kills `<` instead of `<=` and a dropped margin."""

    def test_boundary_points_count_as_inside(self):
        area = pad_area_of(_pad(w_mm=4.0, h_mm=2.0))
        assert area.contains(Vector2.from_xy_mm(2.0, 1.0)) is True   # the corner itself
        assert area.contains(Vector2.from_xy_mm(0.0, -1.0)) is True  # an edge midpoint

    def test_a_step_outside_does_not(self):
        area = pad_area_of(_pad(w_mm=4.0, h_mm=2.0))
        assert area.contains(Vector2.from_xy_mm(2.001, 0.0)) is False
        assert area.contains(Vector2.from_xy_mm(0.0, -1.001)) is False

    def test_margin_grows_the_area_on_every_side(self):
        area = pad_area_of(_pad(w_mm=4.0, h_mm=2.0))
        assert area.contains(Vector2.from_xy_mm(2.5, 0.0)) is False
        assert area.contains(Vector2.from_xy_mm(2.5, 0.0), margin=0.5 * MM) is True
        assert area.contains(Vector2.from_xy_mm(2.5, 1.5), margin=0.5 * MM) is True
        assert area.contains(Vector2.from_xy_mm(2.5, 1.501), margin=0.5 * MM) is False


class TestRotatedPadIsNotItsAxisAlignedBox:
    """С2 — the 0.30 x 0.85 mm pad at 45°: a point inside its own axis-aligned
    box but outside the rotated rectangle must NOT read as inside. Kills
    "use the axis-aligned box instead of the pad's own axes"."""

    def test_axis_aligned_box_corner_is_outside_the_pad(self):
        area = pad_area_of(_pad(angle_deg=45.0))
        # 0.15*cos45 + 0.425*sin45 = 0.4066 mm — the corner of the AABB of this
        # rotated rectangle, i.e. inside KiCad's box but well off the copper.
        assert area.contains(Vector2.from_xy_mm(0.4065, 0.4065)) is False

    def test_a_point_on_the_pads_own_long_axis_is_inside(self):
        """Control: the area is not simply "smaller everywhere"."""
        area = pad_area_of(_pad(angle_deg=45.0))
        # local (0, +0.4) mm, rotated by the pad's own 45° -> (0.2828, 0.2828) mm
        assert area.contains(Vector2.from_xy_mm(0.2828, 0.2828)) is True

    def test_bounds_is_the_axis_aligned_box_around_the_rotated_area(self):
        """`bounds()` reproduces the measured 0.813 x 0.813 mm of Д2 and
        contains every turned corner of the pad."""
        area = pad_area_of(_pad(angle_deg=45.0))
        box = area.bounds()
        assert abs(box.size.x / MM - 0.813) < 0.001
        assert abs(box.size.y / MM - 0.813) < 0.001
        for lx_mm, ly_mm in ((0.15, 0.425), (0.15, -0.425),
                             (-0.15, 0.425), (-0.15, -0.425)):
            px_mm, py_mm = _rotate_ydown_mm(lx_mm, ly_mm, 45.0)
            assert _box_contains(box, px_mm * MM, py_mm * MM) is True


class TestShapeOffset:
    """С3 — a pad whose SHAPE is offset from its hole: the area is centred on
    the shape, rotated with the pad. Kills "ignore the offset" and "do not
    rotate the offset"."""

    def test_offset_is_rotated_with_the_pad(self):
        area = pad_area_of(_pad(x_mm=10.0, y_mm=20.0, angle_deg=90.0,
                                offset_mm=(1.0, 0.0)))
        assert (area.center.x, area.center.y) == (10 * MM, 19 * MM)
        assert area.contains(Vector2.from_xy_mm(10.0, 19.0)) is True
        assert area.contains(Vector2.from_xy_mm(10.0, 20.0)) is False

    def test_no_offset_keeps_the_area_on_the_hole(self):
        area = pad_area_of(_pad(x_mm=1.0, y_mm=2.0))
        assert (area.center.x, area.center.y) == (1 * MM, 2 * MM)


class TestFallbackShapesAndWarning:
    """С4 — a shape without an area of its own falls back to KiCad's box, and
    the fallback warns only where that box may be shifted (a pad angle that is
    not a multiple of 90°). Kills "treat custom as a rectangle" and "warn
    always"."""

    def test_custom_and_unknown_have_no_area_of_their_own(self):
        assert pad_area_of(_pad(shape=PAD_SHAPE_CUSTOM)) is None
        assert pad_area_of(_pad(shape=PAD_SHAPE_UNKNOWN)) is None

    def test_a_pad_without_size_has_no_area_of_their_own(self):
        no_size = Pad(number="1", net_name=None, position=Vector2(0, 0))
        assert pad_area_of(no_size) is None

    def test_warns_for_a_fallback_pad_at_45_degrees(self, caplog):
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            warn_bbox_fallback(logging.getLogger(_LOGGER_NAME), "IC2",
                               _pad(shape=PAD_SHAPE_CUSTOM, angle_deg=45.0))
        warnings = _warnings(caplog)
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "IC2.33" in message and "45.00" in message

    @pytest.mark.parametrize("angle_deg", [0.0, 90.0, 180.0, 270.0])
    def test_stays_silent_at_a_multiple_of_90_degrees(self, caplog, angle_deg):
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            warn_bbox_fallback(logging.getLogger(_LOGGER_NAME), "IC2",
                               _pad(shape=PAD_SHAPE_CUSTOM, angle_deg=angle_deg))
        assert _warnings(caplog) == []

    def test_warns_at_315_degrees_too(self, caplog):
        """The rule is "a multiple of 90°", not "45° specifically" — 315° is
        Denis's live case (Д1)."""
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            warn_bbox_fallback(logging.getLogger(_LOGGER_NAME), "IC2",
                               _pad(shape=PAD_SHAPE_CUSTOM, angle_deg=315.0))
        assert len(_warnings(caplog)) == 1


class TestShapesBoundedBySize:
    """Every shape KiCad bounds by the `size` rectangle gets an area; the
    trapezoid's bound is grown by its delta."""

    def test_shapes_bounded_by_the_size_rectangle(self):
        for shape in (PAD_SHAPE_RECT, PAD_SHAPE_ROUNDRECT, PAD_SHAPE_CHAMFERED,
                      PAD_SHAPE_OVAL, PAD_SHAPE_CIRCLE):
            area = pad_area_of(_pad(w_mm=1.0, h_mm=0.5, shape=shape))
            assert area is not None, shape
            assert (area.half_w, area.half_h) == (0.5 * MM, 0.25 * MM), shape

    def test_trapezoid_grows_both_half_sizes_by_the_largest_delta(self):
        area = pad_area_of(_pad(w_mm=1.0, h_mm=1.0, shape=PAD_SHAPE_TRAPEZOID,
                                trapezoid_delta_mm=(0.2, 0.05)))
        assert area.half_w == 0.5 * MM + 0.2 * MM
        assert area.half_h == 0.5 * MM + 0.2 * MM

    def test_trapezoid_without_delta_is_its_size_rectangle(self):
        area = pad_area_of(_pad(w_mm=1.0, h_mm=1.0, shape=PAD_SHAPE_TRAPEZOID))
        assert (area.half_w, area.half_h) == (0.5 * MM, 0.5 * MM)


class TestPadDoubleWithoutTheNewFields:
    """С11 — tests/test_via_planner.py's `MagicMock(spec=kipy Pad)` pads set
    only number/position/size/angle_rad; they must read as a plain rectangle
    with no offset, or that file would need edits. Kills "mandatory fields
    without defaults"."""

    def test_shape_offset_and_delta_default_to_a_plain_rectangle(self):
        pad = MagicMock(spec=KipyPad)
        pad.number = "1"
        pad.position = Vector2.from_xy_mm(0.0, 0.0)
        pad.size = Vector2.from_xy_mm(4.0, 4.0)
        pad.angle_rad = 0.0
        area = pad_area_of(pad)
        assert area is not None
        assert (area.half_w, area.half_h) == (2 * MM, 2 * MM)
        assert (area.center.x, area.center.y) == (0, 0)
        assert area.angle_deg == 0.0

    def test_a_double_without_size_has_no_area(self):
        pad = MagicMock(spec=KipyPad)
        pad.number = "1"
        pad.position = Vector2.from_xy_mm(0.0, 0.0)
        assert pad_area_of(pad) is None


class TestPadFromKipyCarriesTheShape:
    """pad_from_kipy fills shape/offset/trapezoid_delta from
    padstack.copper_layers[0] — the whole point of the fields is that the area
    can then be derived with no further board access."""

    def test_every_kipy_shape_maps_onto_the_domain_vocabulary(self):
        expected = {
            PadStackShape.PSS_RECTANGLE: PAD_SHAPE_RECT,
            PadStackShape.PSS_ROUNDRECT: PAD_SHAPE_ROUNDRECT,
            PadStackShape.PSS_CHAMFEREDRECT: PAD_SHAPE_CHAMFERED,
            PadStackShape.PSS_OVAL: PAD_SHAPE_OVAL,
            PadStackShape.PSS_CIRCLE: PAD_SHAPE_CIRCLE,
            PadStackShape.PSS_TRAPEZOID: PAD_SHAPE_TRAPEZOID,
            PadStackShape.PSS_CUSTOM: PAD_SHAPE_CUSTOM,
            PadStackShape.PSS_UNKNOWN: PAD_SHAPE_UNKNOWN,
        }
        for kipy_shape, domain_shape in expected.items():
            pad = pad_from_kipy(_kipy_pad(shape=kipy_shape))
            assert pad.shape == domain_shape, kipy_shape

    def test_mapped_pads_carry_their_size_offset_and_delta(self):
        pad = pad_from_kipy(_kipy_pad(shape=PadStackShape.PSS_RECTANGLE,
                                      offset_nm=(1_000_000, 0),
                                      delta_nm=(0, 0),
                                      angle_deg=180.0))
        assert pad.size == Vector2(300_000, 850_000)
        assert pad.offset == Vector2(1_000_000, 0)
        assert pad.trapezoid_delta == Vector2(0, 0)
        assert pad_angle_deg(pad) == 180.0
        # and the area really is derived from them: the pad sits at
        # (1000, 2000) nm and its 1 mm offset, turned by 180°, lands the shape
        # centre 1 mm to the -X side of it.
        area = pad_area_of(pad)
        assert (area.center.x, area.center.y) == (1000 - 1_000_000, 2000)

    def test_a_layer_that_reports_no_shape_keeps_it_unknown(self):
        """A hand-made double with only a size reads as a plain rectangle
        (shape None); an explicitly UNKNOWN shape has no area of its own. The
        two cases must not be conflated."""
        double = pad_from_kipy(_kipy_pad(shape=None))
        assert double.shape is None
        assert pad_area_of(double) is not None
        assert pad_area_of(pad_from_kipy(_kipy_pad(shape=PadStackShape.PSS_UNKNOWN))) is None


def _kipy_pad(shape=None, size_nm=(300_000, 850_000), offset_nm=(0, 0),
              delta_nm=(0, 0), angle_deg=None):
    """A kipy-shaped double for pad_from_kipy — no real kipy objects, so the
    mapping is tested for what it maps, not for what kipy happens to fill in."""
    layer = SimpleNamespace(size=Vector2(*size_nm),
                            offset=Vector2(*offset_nm),
                            trapezoid_delta=Vector2(*delta_nm))
    if shape is not None:
        layer.shape = shape
    padstack = SimpleNamespace(copper_layers=[layer])
    if angle_deg is not None:
        padstack.angle = SimpleNamespace(to_radians=lambda: math.radians(angle_deg))
    return SimpleNamespace(number="33", net=None, padstack=padstack,
                           position=Vector2(1000, 2000))
