# kicadstamp/degenerate_segments.py
"""THE one rule for positionally unidentifiable track segments (plan Ч1).

Why one module (rule 45, 2026-10-19)
------------------------------------
A segment whose ends are closer than ``DEGENERATE_SEGMENT_NM``
(2 × ``POSITION_TOLERANCE_NM`` = 20 µm) cannot be identified POSITIONALLY: the
comparison tolerance is half of the segment itself, so a neighbouring stub and
"the very same segment" give identical answers. The 2026-10-15 fact showed the
same blind spot twice, with opposite signs:

  * "Дубли меди" grouped three DIFFERENT segments of one record (6.0 / 8.5 /
    8.3 µm around one point) as one duplicate group and deleted two of them
    (real copper lost);
  * ``track_matches`` accepted each of those three for any other (6 of 6
    ordered pairs), i.e. the positional pre-check could silently NOT draw a
    segment that was missing.

Denis's decision (19.10): the threshold is a COLLAPSE, not a discard — the
degenerate segment is not written, and the neighbouring ends are pulled into
ONE point (a pad/via it already touches, otherwise the segment's own midpoint),
so the record's connectivity survives.

Three consumers, and only these three, call in here:

  * ``kicadstamp/board_dedupe.py`` — width joins the group key, degenerate
    segments never join a group, they are listed on their own line and are
    never deleted;
  * ``kicadstamp/registry.py::track_matches`` — a degenerate live or planned
    segment is never "already there";
  * the capture/re-read paths (``internode_nodes._items_from_unit``,
    ``template_extraction``, ``cell_geometry_refresh._track_new_geo``,
    ``net_trace_extract``) — a degenerate segment is dropped and its
    neighbours are re-pointed by :func:`collapse_degenerate_segments`.

The module works on PLAIN POINT PAIRS in board units (nanometres), so no
domain DTO is privileged here and every caller keeps its own record shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .constants import DEGENERATE_SEGMENT_NM, POSITION_TOLERANCE_NM
from .domain.geometry import Vector2

__all__ = [
    "CollapsedSegment",
    "CollapseResult",
    "collapse_degenerate_segments",
    "collapse_point",
    "find_degenerate_segments",
    "is_degenerate_segment",
    "segment_midpoint",
    "segment_length_nm",
]

#: A point pair in board units (nanometres) — the shape every consumer already
#: has: a domain ``Track``'s start/end, a ``TrackCommand``'s start/end, or a
#: record's local ends converted to the same units.
Segment = tuple[Vector2, Vector2]


def segment_length_nm(start: Vector2, end: Vector2) -> float:
    """Euclidean length of the segment, in nanometres."""
    dx = end.x - start.x
    dy = end.y - start.y
    return (dx * dx + dy * dy) ** 0.5


def is_degenerate_segment(start: Vector2, end: Vector2) -> bool:
    """THE rule: the segment's ends are closer than 2 × the comparison
    tolerance, so its position cannot tell it apart from a neighbour.

    Strictly BELOW ``DEGENERATE_SEGMENT_NM``: a segment of exactly 20 µm is
    still identifiable (its ends are two tolerances apart, the boundary cell of
    the plan)."""
    return segment_length_nm(start, end) < DEGENERATE_SEGMENT_NM


def segment_midpoint(start: Vector2, end: Vector2) -> Vector2:
    """The point a degenerate segment collapses to when it touches no pad/via:
    its own midpoint (integer board units, so the written geometry stays exact
    — a half-nanometre is not representable)."""
    return Vector2((start.x + end.x) // 2, (start.y + end.y) // 2)


def _near(a: Vector2, b: Vector2, tolerance_nm: int = POSITION_TOLERANCE_NM) -> bool:
    return abs(a.x - b.x) <= tolerance_nm and abs(a.y - b.y) <= tolerance_nm


def collapse_point(start: Vector2, end: Vector2,
                   attractors: Iterable[Vector2] = ()) -> Vector2:
    """Where a degenerate segment's ends are pulled to.

    A pad/via the segment ALREADY touches wins (compared with the same
    positional tolerance every other match uses — ``POSITION_TOLERANCE_NM``);
    otherwise the segment's own midpoint. Both ends always land on the SAME
    point, which is what keeps the record connected."""
    for point in attractors:
        if _near(start, point) or _near(end, point):
            return Vector2(point.x, point.y)
    return segment_midpoint(start, end)


def find_degenerate_segments(segments: Sequence[Segment]) -> list[int]:
    """Indices of the degenerate segments — for the load-time warning and for
    probes; it changes nothing."""
    return [i for i, (start, end) in enumerate(segments)
            if is_degenerate_segment(start, end)]


@dataclass(frozen=True)
class CollapsedSegment:
    """One degenerate segment that was dropped, and where its ends went."""

    start: Vector2
    end: Vector2
    collapsed_to: Vector2


@dataclass(frozen=True)
class CollapseResult:
    """What :func:`collapse_degenerate_segments` decided.

    ``segments`` — the kept segments in input order, with the ends that touched
    a collapsed one re-pointed at its collapse point. ``kept_indices`` — the
    INPUT index of each of them, so a caller that carries per-segment data
    (``track_pads``, a role reference, a layer) keeps it aligned without
    searching. ``collapsed`` — the dropped segments, for the caller's Log line
    (one entry per drop, never silent).
    """

    segments: list[Segment]
    kept_indices: list[int]
    collapsed: list[CollapsedSegment]

    @property
    def changed(self) -> bool:
        return bool(self.collapsed)


def _point_to_segment_nm(point: Vector2, start: Vector2, end: Vector2) -> float:
    """Distance from a point to a segment, in board units (nanometres)."""
    dx, dy = end.x - start.x, end.y - start.y
    if dx == 0 and dy == 0:
        return segment_length_nm(point, start)
    t = ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    projection = Vector2(int(round(start.x + t * dx)), int(round(start.y + t * dy)))
    return segment_length_nm(point, projection)


def _touching(first: Segment, second: Segment) -> bool:
    """Do two segments touch within ONE positional tolerance?

    End-to-end and end-onto-the-middle both count: a stub chained to another stub
    (acceptance Д-2: 3 × 9 µm, 2 × 15 µm) is one physical place, and collapsing
    the members separately would tear the chain apart by exactly the stub
    length."""
    a1, a2 = first
    b1, b2 = second
    for point in (a1, a2):
        if (_near(point, b1) or _near(point, b2)
                or _point_to_segment_nm(point, b1, b2) <= POSITION_TOLERANCE_NM):
            return True
    for point in (b1, b2):
        if (_near(point, a1) or _near(point, a2)
                or _point_to_segment_nm(point, a1, a2) <= POSITION_TOLERANCE_NM):
            return True
    return False


def _touching_groups(segments: Sequence[Segment], indices: Sequence[int]) -> list[list[int]]:
    """Group the given (degenerate) indices into touching clusters.

    A chain A–B–C is ONE group, so its whole length collapses into one point
    instead of each member collapsing into its own (the Д-2 defect: 3 × 9 µm
    left a 9 µm gap between the neighbours)."""
    parent = {index: index for index in indices}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for position, first in enumerate(indices):
        for second in indices[position + 1:]:
            if _touching(segments[first], segments[second]):
                ra, rb = find(first), find(second)
                if ra != rb:
                    parent[ra] = rb

    groups: dict[int, list[int]] = {}
    for index in indices:
        groups.setdefault(find(index), []).append(index)
    return [groups[root] for root in sorted(groups)]


def _group_point(segments: Sequence[Segment], members: Sequence[int],
                 attractors: Sequence[Vector2]) -> Vector2:
    """Where a whole touching group collapses.

    An ATTRACTOR any member already touches wins — a pad or a via, whichever the
    caller can supply (one positional tolerance, as everywhere else). The cell
    extraction path passes pad centres AND vias; the inter-node re-read has only
    vias and the anchor point (its CopperUnit carries PadRefs, not coordinates).
    Otherwise the centre of the group's bounding box — which for a straight chain
    is the middle of the chain."""
    ends = [point for index in members for point in segments[index]]
    for point in ends:
        for attractor in attractors:
            if _near(point, attractor):
                return Vector2(attractor.x, attractor.y)
    xs = [point.x for point in ends]
    ys = [point.y for point in ends]
    return Vector2((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2)


def collapse_degenerate_segments(segments: Sequence[Segment], *,
                                 attractors: Iterable[Vector2] = ()) -> CollapseResult:
    """Drop every degenerate segment and pull its neighbours into one point.

    Why this shape (Denis, 19.10): the segment is never "thrown away" on its
    own — the ends of the neighbours that met it are RE-POINTED at the collapse
    point (a touched pad/via, else the midpoint). That keeps the number of
    connected components of the record's own copper unchanged, which is exactly
    what the Ч0 bridge measurements checked (``spoke_vdd_03`` 7/21,
    ``nrst…`` 0/11, ``heater_led_02…`` 10/11/15).

    TOUCHING degenerate segments collapse as ONE GROUP into ONE point
    (acceptance Д-2, 19.10): a chain of them is one physical place, so
    collapsing each member into its own midpoint would tear the neighbours apart
    by the chain's length (3 × 9 µm → a 9 µm gap; 2 × 15 µm → 15 µm).

    An end is re-pointed only when it lies within ONE positional tolerance of
    a dropped segment's end — the same rule every other match uses. Nothing else
    is touched, and the input sequence is never mutated.
    """
    degenerate = find_degenerate_segments(segments)
    if not degenerate:
        return CollapseResult(list(segments), list(range(len(segments))), [])

    attractor_list = list(attractors)
    groups = _touching_groups(segments, degenerate)
    # group point is computed from the ORIGINAL input, so two groups can never
    # re-point each other's ends in a chain.
    group_points = [_group_point(segments, members, attractor_list) for members in groups]

    # One note per dropped segment, IN INPUT ORDER (the caller's Log line and
    # `kept_indices` describe the same input list).
    point_by_index = {index: point
                      for members, point in zip(groups, group_points)
                      for index in members}
    collapsed = [CollapsedSegment(start=segments[index][0], end=segments[index][1],
                                  collapsed_to=point_by_index[index])
                 for index in degenerate]

    # Every end of every group member is a re-pointing target: `(member_end,
    # group_point)`. Built from the ORIGINAL input, so a kept neighbour can never
    # re-point another kept neighbour.
    repoint = [(end, point_by_index[index])
               for members, _point in zip(groups, group_points)
               for index in members for end in segments[index]]

    dropped = set(degenerate)
    kept: list[Segment] = []
    kept_indices: list[int] = []
    for index, (start, end) in enumerate(segments):
        if index in dropped:
            continue
        new_start, new_end = start, end
        for member_end, point in repoint:
            if _near(start, member_end):
                new_start = point
            if _near(end, member_end):
                new_end = point
        kept.append((new_start, new_end))
        kept_indices.append(index)

    return CollapseResult(kept, kept_indices, collapsed)
