# kicadstamp/geometry/keepout.py

import math

from ..domain.geometry import Vector2

"""
* Rect class — AABB rectangle, used to represent keepout zones.
* from_bbox — constructs a Rect from a Box2 obtained from the adapter, with clearance margin.
* from_circle — approximates a circle as a square (for vias).
* intersects — checks overlap of two Rects.
* point_is_clear — checks whether a point is free (the via circle of radius via_radius
  does not intersect any keepout rectangle).
* build_keepout — takes a list of bounding boxes and builds a list of Rects with clearance_mm
  (used to create keepout areas from existing components and vias).
* find_free_point — searches for a free point around the ideal position, expanding in rings.
  Respects a preferred direction. Used to place vias while avoiding collisions.
"""

class Rect:
    """Simple AABB rectangle in board coordinates (nm)."""

    def __init__(self, min_x: float, min_y: float, max_x: float, max_y: float):
        self.min_x, self.min_y, self.max_x, self.max_y = min_x, min_y, max_x, max_y

    @classmethod
    def from_bbox(cls, bbox, clearance: int = 0) -> "Rect":
        """Builds a Rect from a Box2 (see adapter.get_bounding_boxes), with clearance on each side."""
        return cls(
            bbox.pos.x - clearance, bbox.pos.y - clearance,
            bbox.pos.x + bbox.size.x + clearance, bbox.pos.y + bbox.size.y + clearance,
        )

    @classmethod
    def from_circle(cls, center: Vector2, radius: float) -> "Rect":
        """Rough (but simple and fast) square approximation of a circle — for vias
        with their small diameter this is sufficient; no exact circle‑vs‑rect test needed."""
        return cls(center.x - radius, center.y - radius, center.x + radius, center.y + radius)

    def intersects(self, other: "Rect") -> bool:
        return not (self.max_x < other.min_x or other.max_x < self.min_x or
                    self.max_y < other.min_y or other.max_y < self.min_y)

    def __repr__(self):
        return f"Rect({self.min_x}, {self.min_y}, {self.max_x}, {self.max_y})"


def point_is_clear(point: Vector2, via_radius: float, keepout: list[Rect]) -> bool:
    """True if the via circle of radius via_radius around point does not intersect any keepout rectangle."""
    via_box = Rect.from_circle(point, via_radius)
    return not any(via_box.intersects(r) for r in keepout)


def build_keepout(bboxes, clearance_mm: float, mm_per_unit: int = 1_000_000) -> list[Rect]:
    """
    Builds a list of Rects from bounding boxes (see adapter.get_bounding_boxes),
    with clearance_mm on each side. None elements (bbox unavailable for a particular
    pad/footprint) are silently skipped — calling code may log this separately if needed.
    """
    clearance = int(clearance_mm * mm_per_unit)
    rects = []
    for bbox in bboxes:
        if bbox is None:
            continue
        rects.append(Rect.from_bbox(bbox, clearance))
    return rects


def find_free_point(
    ideal: Vector2,
    keepout: list[Rect],
    via_radius: float,
    preferred_direction: tuple[float, float] | None = None,
    step_mm: float = 0.1,
    max_radius_mm: float = 3.0,
    mm_per_unit: int = 1_000_000,
    n_directions: int = 8,
) -> Vector2 | None:
    """
    Searches for the nearest free point (not intersecting keepout) around ideal
    in expanding rings: first ideal itself, then rings of radius step_mm, 2*step_mm,
    ... up to max_radius_mm.

    On each ring, it first tries preferred_direction (if set — e.g. "towards the
    zone centre" for default GND vias), then n_directions points evenly around the
    circle. The first matching point is returned immediately — not necessarily the
    globally nearest, but guaranteed to be within the current (smallest checked) ring.

    Returns None if no free spot is found within max_radius_mm — calling code should
    treat this as a warning/error, not try to place the via arbitrarily.
    """
    step = step_mm * mm_per_unit
    max_radius = max_radius_mm * mm_per_unit

    if point_is_clear(ideal, via_radius, keepout):
        return ideal

    ring = step
    while ring <= max_radius + 1e-6:
        candidates_deg: list[float] = []
        if preferred_direction is not None:
            pdx, pdy = preferred_direction
            candidates_deg.append(math.degrees(math.atan2(pdy, pdx)))
        for i in range(n_directions):
            candidates_deg.append(360.0 * i / n_directions)

        for deg in candidates_deg:
            rad = math.radians(deg)
            candidate = Vector2.from_xy(
                int(ideal.x + ring * math.cos(rad)),
                int(ideal.y + ring * math.sin(rad)),
            )
            if point_is_clear(candidate, via_radius, keepout):
                return candidate

        ring += step

    return None


def find_free_point_along_line(
    ideal: Vector2,
    keepout: list[Rect],
    via_radius: float,
    line_direction: tuple[float, float],
    step_mm: float = 0.1,
    max_radius_mm: float = 3.0,
    mm_per_unit: int = 1_000_000,
) -> Vector2 | None:
    """
    Searches for a free point along a straight line through ideal,
    with direction line_direction (unit vector).
    First checks ideal, then steps out in both directions with step_mm.
    Returns the first free point found, or None.
    """
    step = int(step_mm * mm_per_unit)
    max_radius = int(max_radius_mm * mm_per_unit)
    dx, dy = line_direction

    if point_is_clear(ideal, via_radius, keepout):
        return ideal

    # Check both directions
    for sign in (1, -1):
        r = step
        while r <= max_radius:
            candidate = Vector2.from_xy(
                int(ideal.x + sign * r * dx),
                int(ideal.y + sign * r * dy)
            )
            if point_is_clear(candidate, via_radius, keepout):
                return candidate
            r += step
    return None