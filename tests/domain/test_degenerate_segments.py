# tests/domain/test_degenerate_segments.py
"""The ONE rule for positionally unidentifiable segments (plan Ч1, 2026-10-19).

Denis's decision: a segment whose ends are closer than 2 × the comparison
tolerance is not identifiable positionally, so it is COLLAPSED (not written, its
neighbours pulled into one point), never merely discarded — the record's
connectivity must survive.

Cells are properties, not incidents: the boundary is pinned on both sides
(19 / 20 / 21 µm), the collapse is checked on a real chain, and the attractor
rule (a touched via wins over the midpoint) is checked separately.
"""
from kicadstamp.constants import DEGENERATE_SEGMENT_NM
from kicadstamp.degenerate_segments import (
    collapse_degenerate_segments,
    collapse_point,
    find_degenerate_segments,
    is_degenerate_segment,
    segment_midpoint,
)
from kicadstamp.domain.geometry import Vector2


def _pt(x_nm: int, y_nm: int) -> Vector2:
    return Vector2(x_nm, y_nm)


def _seg(x0: int, y0: int, x1: int, y1: int) -> tuple[Vector2, Vector2]:
    return (_pt(x0, y0), _pt(x1, y1))


# ── the boundary ────────────────────────────────────────────────────────────

def test_boundary_is_strictly_below_two_tolerances():
    """19 µm is unidentifiable, 20 µm is not — the boundary cell pins BOTH
    sides, so an off-by-one (<=) or a wrong multiplier goes red."""
    assert DEGENERATE_SEGMENT_NM == 20_000
    assert is_degenerate_segment(_pt(0, 0), _pt(19_000, 0))
    assert not is_degenerate_segment(_pt(0, 0), _pt(20_000, 0))
    assert not is_degenerate_segment(_pt(0, 0), _pt(21_000, 0))


def test_diagonal_length_is_euclidean():
    """6.0 / 8.5 / 8.3 µm of the 15.10 fact are diagonal — a per-axis or
    Manhattan check would disagree on both of these rows."""
    assert is_degenerate_segment(_pt(0, 0), _pt(5_900, 5_900))
    # 14/14 µm axes: 19.8 µm euclidean (unidentifiable), 28 µm Manhattan.
    assert is_degenerate_segment(_pt(0, 0), _pt(14_000, 14_000))
    # 15/15 µm axes: 21.2 µm euclidean — just past the threshold.
    assert not is_degenerate_segment(_pt(0, 0), _pt(15_000, 15_000))


def test_the_three_fact_segments_are_all_degenerate():
    """The exact registry geometry of the 15.10 fact."""
    fact = (
        _seg(77_978_400, 98_445_900, 77_974_200, 98_441_600),   # 6.0 µm
        _seg(77_974_200, 98_441_600, 77_982_700, 98_441_600),   # 8.5 µm
        _seg(77_978_400, 98_445_900, 77_984_300, 98_440_000),   # 8.3 µm
    )
    for segment in fact:
        assert is_degenerate_segment(*segment)


def test_find_degenerate_segments_reports_indices_and_nothing_else():
    segments = [_seg(0, 0, 1_000_000, 0), _seg(0, 0, 5_000, 0),
                _seg(0, 0, 2_000_000, 0)]
    assert find_degenerate_segments(segments) == [1]


# ── the collapse ────────────────────────────────────────────────────────────

def test_collapse_drops_the_segment_and_joins_its_neighbours():
    """A 8 µm stub between two real tracks: the stub is gone, both neighbours
    meet at the stub's midpoint, and the chain stays ONE component."""
    left = _seg(0, 0, 1_000_000, 0)
    stub = _seg(1_000_000, 0, 1_008_000, 0)
    right = _seg(1_008_000, 0, 2_000_000, 0)

    result = collapse_degenerate_segments([left, stub, right])

    assert len(result.segments) == 2
    assert result.kept_indices == [0, 2]
    assert len(result.collapsed) == 1
    midpoint = segment_midpoint(*stub)
    assert result.collapsed[0].collapsed_to == midpoint
    assert result.segments[0][1] == midpoint      # left now ends there
    assert result.segments[1][0] == midpoint      # right now starts there


def test_collapse_prefers_a_touched_via_over_the_midpoint():
    stub = _seg(1_000_000, 0, 1_008_000, 0)
    via = _pt(1_000_000, 0)                        # ON the stub's start
    assert collapse_point(*stub, attractors=[via]) == via


def test_collapse_ignores_a_distant_attractor():
    stub = _seg(1_000_000, 0, 1_008_000, 0)
    far = _pt(9_000_000, 0)
    assert collapse_point(*stub, attractors=[far]) == segment_midpoint(*stub)


def test_collapse_is_a_noop_without_degenerate_segments():
    segments = [_seg(0, 0, 1_000_000, 0), _seg(1_000_000, 0, 2_000_000, 0)]
    result = collapse_degenerate_segments(segments)
    assert result.segments == segments
    assert result.kept_indices == [0, 1]
    assert not result.changed


def test_collapse_does_not_mutate_the_input():
    stub = _seg(1_000_000, 0, 1_008_000, 0)
    left = _seg(0, 0, 1_000_000, 0)
    original = [left, stub]
    collapse_degenerate_segments(original)
    assert original == [left, stub]


def test_collapse_keeps_a_three_way_joint_connected():
    """Two neighbours on each side of the stub: every end that met it lands on
    the same point, so four ends become one node instead of two."""
    a = _seg(0, 0, 1_000_000, 0)
    b = _seg(0, 500_000, 1_000_000, 0)
    stub = _seg(1_000_000, 0, 1_008_000, 0)
    c = _seg(1_008_000, 0, 2_000_000, 0)
    result = collapse_degenerate_segments([a, b, stub, c])
    midpoint = segment_midpoint(*stub)
    assert [seg[1] for seg in result.segments[:2]] == [midpoint, midpoint]
    assert result.segments[2][0] == midpoint


# ── acceptance Д-2 (19.10): touching stubs are ONE group ────────────────────

def test_a_chain_of_three_stubs_collapses_into_one_point():
    """Д-2: 3 × 9 µm between two real tracks — the neighbours must MEET, not keep
    a 9 µm gap (the measured defect of the first delivery)."""
    left = _seg(0, 0, 1_000_000, 0)
    stub1 = _seg(1_000_000, 0, 1_009_000, 0)
    stub2 = _seg(1_009_000, 0, 1_018_000, 0)
    stub3 = _seg(1_018_000, 0, 1_027_000, 0)
    right = _seg(1_027_000, 0, 2_000_000, 0)

    result = collapse_degenerate_segments([left, stub1, stub2, stub3, right])

    assert len(result.segments) == 2
    assert result.segments[0][1] == result.segments[1][0]
    assert len({note.collapsed_to for note in result.collapsed}) == 1
    assert len(result.collapsed) == 3


def test_two_15um_stubs_meet_at_one_point():
    """2 × 15 µm — the other measured row of Д-2 (a 15 µm gap before)."""
    left = _seg(0, 0, 1_000_000, 0)
    stub1 = _seg(1_000_000, 0, 1_015_000, 0)
    stub2 = _seg(1_015_000, 0, 1_030_000, 0)
    right = _seg(1_030_000, 0, 2_000_000, 0)

    result = collapse_degenerate_segments([left, stub1, stub2, right])

    assert result.segments[0][1] == result.segments[1][0]
    assert result.segments[0][1].x == (1_000_000 + 1_030_000) // 2


def test_stub_and_right_angle_neighbour_are_one_group():
    """A stub that meets a normal track mid-segment touches it: the end of that
    track is re-pointed too, so the junction survives."""
    left = _seg(0, 0, 1_000_000, 0)
    stub = _seg(1_000_000, 0, 1_008_000, 0)
    result = collapse_degenerate_segments([left, stub])
    point = result.segments[0][1]
    assert point == segment_midpoint(*stub)


def test_distant_stubs_collapse_separately():
    """Not every pair of degenerate segments is one place: two far-apart stubs
    keep their own collapse points."""
    near = _seg(0, 0, 8_000, 0)
    far = _seg(1_000_000, 0, 1_008_000, 0)
    result = collapse_degenerate_segments([near, far])
    assert len(result.collapsed) == 2
    assert result.collapsed[0].collapsed_to != result.collapsed[1].collapsed_to
