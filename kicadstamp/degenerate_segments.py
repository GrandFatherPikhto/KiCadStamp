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


def collapse_degenerate_segments(segments: Sequence[Segment], *,
                                 attractors: Iterable[Vector2] = ()) -> CollapseResult:
    """Drop every degenerate segment and pull its neighbours into one point.

    Why this shape (Denis, 19.10): the segment is never "thrown away" on its
    own — the ends of the neighbours that met it are RE-POINTED at
    :func:`collapse_point` (a touched pad/via, else the midpoint). That keeps
    the number of connected components of the record's own copper unchanged,
    which is exactly what the Ч0 bridge measurements checked
    (``spoke_vdd_03`` 7/21, ``nrst…`` 0/11, ``heater_led_02…`` 10/11/15).

    An end is re-pointed only when it lies within ONE positional tolerance of
    the dropped segment's end — the same rule every other match uses. Nothing
    else is touched, and the input sequence is never mutated.
    """
    degenerate = find_degenerate_segments(segments)
    if not degenerate:
        return CollapseResult(list(segments), list(range(len(segments))), [])

    attractor_list = list(attractors)
    dropped: dict[int, CollapsedSegment] = {}
    # The mapping is built from the ORIGINAL input, so two dropped segments can
    # never re-point each other's ends in a chain.
    for index in degenerate:
        start, end = segments[index]
        dropped[index] = CollapsedSegment(
            start=start, end=end,
            collapsed_to=collapse_point(start, end, attractor_list))

    kept: list[Segment] = []
    kept_indices: list[int] = []
    for index, (start, end) in enumerate(segments):
        if index in dropped:
            continue
        new_start, new_end = start, end
        for note in dropped.values():
            if _near(start, note.start) or _near(start, note.end):
                new_start = note.collapsed_to
            if _near(end, note.start) or _near(end, note.end):
                new_end = note.collapsed_to
        kept.append((new_start, new_end))
        kept_indices.append(index)

    return CollapseResult(kept, kept_indices, [dropped[i] for i in degenerate])
