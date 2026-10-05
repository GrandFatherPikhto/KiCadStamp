# kicadstamp/geometry/copper_connect.py
"""Copper-to-copper connectivity by SHAPE, the way KiCad sees it (2026-10-05,
plan ``plan_2026_10_05_explode_r1_core.md`` Р1б-1).

A track is a CAPSULE (its segment grown by ``width/2``); a via is a DISC
(``diameter/2``). Two pieces of copper on a shared layer are connected when
their shapes OVERLAP — not when two points coincide. That is what catches
(a) a track end standing a few micrometres inside a via's ring and (b) a
T-junction, where a track ends on the middle of another.

Everything here is PURE and in NANOMETRES (float). The callers convert mm to nm
and decide LAYERS: a capsule carries the track's layer, a via disc spans every
layer. No board, no config, no side effects.
"""
from __future__ import annotations

import math

__all__ = [
    "point_segment_distance",
    "segment_segment_distance",
    "capsules_touch",
    "disc_touches_capsule",
    "discs_touch",
]


def point_segment_distance(px: float, py: float, ax: float, ay: float,
                           bx: float, by: float) -> float:
    """Distance (nm) from point P to segment AB, or to the nearer endpoint."""
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _cross(ox, oy, ax, ay, bx, by) -> float:
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)


def _on_segment(ox, oy, ax, ay, bx, by) -> bool:
    return (min(ax, bx) <= ox <= max(ax, bx)
            and min(ay, by) <= oy <= max(ay, by))


def _segments_intersect(ax, ay, bx, by, cx, cy, dx, dy) -> bool:
    d1 = _cross(cx, cy, dx, dy, ax, ay)
    d2 = _cross(cx, cy, dx, dy, bx, by)
    d3 = _cross(ax, ay, bx, by, cx, cy)
    d4 = _cross(ax, ay, bx, by, dx, dy)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    if d1 == 0 and _on_segment(ax, ay, cx, cy, dx, dy):
        return True
    if d2 == 0 and _on_segment(bx, by, cx, cy, dx, dy):
        return True
    if d3 == 0 and _on_segment(cx, cy, ax, ay, bx, by):
        return True
    if d4 == 0 and _on_segment(dx, dy, ax, ay, bx, by):
        return True
    return False


def segment_segment_distance(ax, ay, bx, by, cx, cy, dx, dy) -> float:
    """Distance (nm) between segments AB and CD (0.0 when they cross)."""
    if _segments_intersect(ax, ay, bx, by, cx, cy, dx, dy):
        return 0.0
    return min(
        point_segment_distance(ax, ay, cx, cy, dx, dy),
        point_segment_distance(bx, by, cx, cy, dx, dy),
        point_segment_distance(cx, cy, ax, ay, bx, by),
        point_segment_distance(dx, dy, ax, ay, bx, by),
    )


def capsules_touch(ax, ay, bx, by, w_ab: float,
                   cx, cy, dx, dy, w_cd: float) -> bool:
    """Two TRACK capsules (full widths ``w_ab``/``w_cd``, nm) overlap: the
    segment-to-segment distance is within ``(w_ab + w_cd) / 2``. Catches ends
    AND a T-junction (a zero-length segment is a point capsule)."""
    return segment_segment_distance(ax, ay, bx, by, cx, cy, dx, dy) \
        <= (w_ab + w_cd) / 2.0


def disc_touches_capsule(px: float, py: float, r: float,
                         ax, ay, bx, by, w: float) -> bool:
    """A DISC (centre P, radius ``r``) meets a TRACK capsule (full width ``w``):
    the centre-to-segment distance is within ``r + w / 2``. A via is through, so
    the caller does NOT pass a layer here."""
    return point_segment_distance(px, py, ax, ay, bx, by) <= r + w / 2.0


def discs_touch(px: float, py: float, r1: float,
                qx: float, qy: float, r2: float) -> bool:
    """Two DISCS overlap: the centre distance is within ``r1 + r2``."""
    return math.hypot(px - qx, py - qy) <= r1 + r2
