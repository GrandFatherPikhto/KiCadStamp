# tests/test_clip_segment_to_box.py
"""Synthetic unit tests for the Liang-Barsky segment-vs-box clipping the
Scheme List "truncate" boundary action is built on (Part A of
design_2026_09_06_boundary_truncate_and_zones.md).

Pure geometry — no adapter, no GUI, no live board. The box coordinates are in
nanometres (domain Vector2/Box2 semantics); helpers build them from mm so the
test numbers read naturally.
"""
import pytest

from kicadstamp.domain.geometry import Box2, Vector2, clip_segment_to_box


def _box(x0_mm: float, y0_mm: float, x1_mm: float, y1_mm: float) -> Box2:
    """An axis-aligned box from two corner points in mm -> nm."""
    x0, y0 = int(x0_mm * 1e6), int(y0_mm * 1e6)
    x1, y1 = int(x1_mm * 1e6), int(y1_mm * 1e6)
    return Box2(pos=Vector2(min(x0, x1), min(y0, y1)),
                size=Vector2(abs(x1 - x0), abs(y1 - y0)))


def _pt(x_mm: float, y_mm: float) -> Vector2:
    return Vector2.from_xy_mm(x_mm, y_mm)


def _mm(v: Vector2) -> tuple[float, float]:
    return (v.x / 1e6, v.y / 1e6)


BOX = _box(0.0, 0.0, 10.0, 10.0)


def test_segment_fully_inside_is_unchanged():
    """A segment with both endpoints inside the box returns THE SAME objects
    (identity matters — the caller keeps Track/Via objects it already holds)."""
    start = _pt(2.0, 3.0)
    end = _pt(8.0, 7.0)
    clipped = clip_segment_to_box(start, end, BOX)
    assert clipped == (start, end)
    assert clipped[0] is start and clipped[1] is end


def test_segment_fully_outside_returns_none():
    # Both endpoints in the same half-plane to the right of the box.
    assert clip_segment_to_box(_pt(11.0, 2.0), _pt(20.0, 9.0), BOX) is None
    # Below the box.
    assert clip_segment_to_box(_pt(1.0, -3.0), _pt(5.0, -1.0), BOX) is None
    # Completely diagonal, far corner.
    assert clip_segment_to_box(_pt(20.0, 20.0), _pt(30.0, 30.0), BOX) is None


def test_crosses_single_edge_one_end_inside():
    """Start inside, end outside to the right -> clip only the end."""
    clipped = clip_segment_to_box(_pt(4.0, 5.0), _pt(20.0, 5.0), BOX)
    assert clipped is not None
    a, b = (_mm(p) for p in clipped)
    assert a == pytest.approx((4.0, 5.0))
    assert b[0] == pytest.approx(10.0)  # clamped to the right face
    assert b[1] == pytest.approx(5.0)


def test_passes_through_box_both_ends_outside():
    """A long track that runs across the box with both ends outside gets BOTH
    endpoints clipped onto the box faces."""
    clipped = clip_segment_to_box(_pt(-5.0, 5.0), _pt(15.0, 5.0), BOX)
    assert clipped is not None
    a, b = (_mm(p) for p in clipped)
    assert a[0] == pytest.approx(0.0) and a[1] == pytest.approx(5.0)
    assert b[0] == pytest.approx(10.0) and b[1] == pytest.approx(5.0)


def test_passes_through_box_diagonal():
    """Diagonal crossing — clip lands on two DIFFERENT faces."""
    clipped = clip_segment_to_box(_pt(-5.0, -5.0), _pt(15.0, 15.0), BOX)
    assert clipped is not None
    a, b = (_mm(p) for p in clipped)
    # y = x diagonal: enters at (0,0), leaves at (10,10).
    assert a == pytest.approx((0.0, 0.0))
    assert b == pytest.approx((10.0, 10.0))


def test_endpoint_exactly_on_boundary_is_inside():
    """An endpoint lying exactly on a face counts as inside (coordinate
    equality, not float noise) — same semantics as the boolean pre-filter."""
    clipped = clip_segment_to_box(_pt(0.0, 5.0), _pt(20.0, 5.0), BOX)
    assert clipped is not None
    a, b = (_mm(p) for p in clipped)
    assert a == pytest.approx((0.0, 5.0))
    assert b[0] == pytest.approx(10.0)
    assert b[1] == pytest.approx(5.0)


def test_degenerate_segment_on_boundary():
    """A zero-length (point) segment exactly on the box boundary counts as
    inside (returns the same point)."""
    pt = _pt(5.0, 10.0)  # bottom edge
    clipped = clip_segment_to_box(pt, pt, BOX)
    assert clipped == (pt, pt)


def test_degenerate_point_outside_returns_none():
    assert clip_segment_to_box(_pt(50.0, 50.0), _pt(50.0, 50.0), BOX) is None


def test_horizontal_segment_exactly_along_top_edge():
    """A track running exactly along the top face (y == max_y) is inside."""
    clipped = clip_segment_to_box(_pt(1.0, 10.0), _pt(9.0, 10.0), BOX)
    assert clipped is not None
    a, b = (_mm(p) for p in clipped)
    assert a == pytest.approx((1.0, 10.0))
    assert b == pytest.approx((9.0, 10.0))


def test_clip_rounds_to_nearest_nanometre():
    """The clip result is integer nm (Vector2), rounded — never float coords."""
    # 1/3 of the way across the box on the x-axis -> 3.3333.. mm.
    clipped = clip_segment_to_box(_pt(-5.0, 0.0), _pt(10.0, 0.0), BOX)
    assert clipped is not None
    a, _b = clipped
    assert isinstance(a, Vector2)
    assert a.x % 1 == 0  # integer nm
    # Entering face is x=0; the y is 0 -> the left face crossing is at x=0.
    assert a.x == 0


def test_parallel_but_outside_is_none():
    """Segment parallel to a face but entirely beyond it -> None."""
    # Vertical segment right of the box.
    assert clip_segment_to_box(_pt(12.0, -5.0), _pt(12.0, 20.0), BOX) is None


def test_degenerate_zero_width_box():
    """A zero-width (line) box still clips against the single face it spans."""
    line_box = _box(5.0, 0.0, 5.0, 10.0)  # vertical line at x=5
    clipped = clip_segment_to_box(_pt(-5.0, 4.0), _pt(15.0, 4.0), line_box)
    assert clipped is not None
    a, b = (_mm(p) for p in clipped)
    assert a == pytest.approx((5.0, 4.0))
    assert b == pytest.approx((5.0, 4.0))
