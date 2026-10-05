# tests/geometry/test_copper_connect.py
"""Cells for the shape-based copper connectivity (plan
``plan_2026_10_05_explode_r1_core.md`` Р1б-1): a track is a capsule (width), a
via a disc (diameter). Everything is in nanometres; layers are the callers' job.
"""
from kicadstamp.geometry.copper_connect import (
    capsules_touch,
    disc_touches_capsule,
    discs_touch,
    point_segment_distance,
    segment_segment_distance,
)

MM = 1_000_000


def test_point_segment_distance_perpendicular_and_beyond():
    assert point_segment_distance(0, 5, -10, 0, 10, 0) == 5
    assert point_segment_distance(20, 0, -10, 0, 10, 0) == 10   # beyond the end


def test_segments_that_cross_are_zero_apart():
    assert segment_segment_distance(0, 0, 10, 10, 0, 10, 10, 0) == 0.0


def test_a_track_end_5um_from_a_via_centre_is_connected():
    """Р1б-1(a) — the live numbers: a track end a few micrometres inside a via's
    ring. Point-exact matching misses it; the disc/capsule test does not."""
    assert disc_touches_capsule(5 * 1000, 0, 300 * 1000,
                                0, 0, 1 * MM, 0, 250 * 1000)


def test_a_t_junction_connects():
    """Р1б-1(b) — a track ending on the MIDDLE of another (no shared endpoint)."""
    assert capsules_touch(0, 0, 4 * MM, 0, 250 * 1000,
                          2 * MM, 0, 2 * MM, 3 * MM, 250 * 1000)


def test_parallel_thin_tracks_with_a_gap_do_not_touch():
    """Р1б-1(c) — 0.1 mm apart, thin: (0.05 + 0.05)/2 = 0.05 < 0.1 -> apart."""
    assert not capsules_touch(0, 0, 4 * MM, 0, 50 * 1000,
                              0, 100 * 1000, 4 * MM, 100 * 1000, 50 * 1000)


def test_discs_touch_by_radii():
    assert discs_touch(0, 0, 300 * 1000, 500 * 1000, 0, 300 * 1000)
    assert not discs_touch(0, 0, 300 * 1000, 700 * 1000, 0, 300 * 1000)
