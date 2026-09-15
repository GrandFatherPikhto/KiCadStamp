# kicadstamp/geometry/pad_area.py
"""pad_area.py — the pad's own copper area, in the PAD's own axes.

Why this module exists (measured live 2026-09-15, defect Д1 of
plan_2026_09_15_pad_geometry_thermal_vias.md): with a footprint rotated by an
angle that is not a multiple of 90°, KiCad's get_item_bounding_box returns the
box of EVERY pad of that footprint shifted by one and the same offset
(measured: IC2 at 315° -> (−0.899, −1.470) mm = 1.724 mm for all 33 pads), while
the pad's own ``position``, ``padstack.copper_layers[0].size`` and
``padstack.angle`` stay correct. Taking that box for "where the copper is" moves
the keepout off the pad (thermal vias leave the pad), makes an Extract
connectivity test miss a track end that lies ON the pad, and breaks the
inter-node copper attachment.

The box is the wrong SHAPE even where it is not shifted (Д2): it is an
axis-aligned box around a ROTATED rectangle — a 0.30x0.85 mm pad at 45° becomes
0.813x0.813 mm, 2.6x the copper area — so the same copper reads "occupied" at
45° and "free" at 0°. The area built here lives in the pad's axes and is
therefore invariant under the footprint's rotation: a pad body and the via array
inside it rotate together, as Denis requires.

Nothing here reads the board: every input is a field of an already-read pad
(kicadstamp/domain/board.py:Pad), so this module adds no IPC traffic at all.
Rotation uses the project's own primitive — ``rotate_local_offset``
(geometry/spoke_layout.py), the same ``Vector2.rotate`` / Y-down formula as
``cell_frame.rotate_ydown_mm``. It is imported from spoke_layout rather than
from cell_frame on purpose: cell_frame imports ``geometry.clone_geometry``, so
importing it from inside the geometry package would re-enter this package's own
__init__ (import cycle).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..constants import (
    PAD_SHAPE_CHAMFERED,
    PAD_SHAPE_CIRCLE,
    PAD_SHAPE_CUSTOM,
    PAD_SHAPE_OVAL,
    PAD_SHAPE_RECT,
    PAD_SHAPE_ROUNDRECT,
    PAD_SHAPE_TRAPEZOID,
    PAD_SHAPE_UNKNOWN,
)
from ..domain.geometry import Box2, Vector2
from ..i18n import _
from ..utils.units import MM
from .spoke_layout import rotate_local_offset

__all__ = [
    "PadArea",
    "pad_area_of",
    "pad_angle_deg",
    "warn_bbox_fallback",
]

# The shape vocabulary itself lives in kicadstamp/constants.py (PAD_SHAPE_*):
# it is a field value of the domain DTO, and domain/board.py must not import
# this package (geometry/__init__ -> thermal_grid -> domain.board is a cycle).

# Shapes whose copper is BOUNDED BY the `size` rectangle. roundrect/chamfered/
# oval/circle only lose corners relative to it, so the rectangle is a superset —
# exactly the conservatism a keepout wants ("the via must not touch the pad").
_SHAPES_WITH_OWN_AREA = frozenset((
    PAD_SHAPE_RECT, PAD_SHAPE_ROUNDRECT, PAD_SHAPE_CHAMFERED, PAD_SHAPE_OVAL,
    PAD_SHAPE_CIRCLE, PAD_SHAPE_TRAPEZOID,
))

# `custom` (arbitrary primitives) and `unknown` have no bounding rectangle of
# their own — the caller falls back to KiCad's box and is warned when that box
# cannot be trusted (see warn_bbox_fallback).
_SHAPES_WITHOUT_OWN_AREA = frozenset((PAD_SHAPE_CUSTOM, PAD_SHAPE_UNKNOWN))

# A pad angle that is a multiple of this is axis-aligned in the board frame:
# KiCad's own box is then a faithful (if padded) box, so the fallback is silent.
RIGHT_ANGLE_DEG = 90.0


@dataclass(frozen=True)
class PadArea:
    """A pad's copper as a rectangle in the PAD's own axes (nm).

    ``center`` is the centre of the pad SHAPE — ``position`` + the pad's own
    ``offset`` rotated by the pad angle — not the hole centre, which may differ
    for a pad with an offset shape (Д3).
    """

    center: Vector2
    half_w: float
    half_h: float
    angle_deg: float

    def _local(self, point: Vector2) -> tuple[float, float]:
        """Board point (nm) -> (lx, ly) in this pad's own axes (nm)."""
        dx_mm = (point.x - self.center.x) / MM
        dy_mm = (point.y - self.center.y) / MM
        local = rotate_local_offset(dx_mm, dy_mm, -self.angle_deg)
        return local.x, local.y

    def contains(self, point: Vector2, margin: float = 0.0) -> bool:
        """True when `point` is inside the area, grown by `margin` on every
        side. The margin is applied in the PAD's axes, not the board's."""
        lx, ly = self._local(point)
        return abs(lx) <= self.half_w + margin and abs(ly) <= self.half_h + margin

    def blocks_via(self, point: Vector2, via_radius: float) -> bool:
        """True when a via centre at `point` with radius `via_radius` would
        touch this pad — the same conservative "square via corner" test the old
        ``Rect`` did, but in the pad's axes, so it does not change with the
        footprint's rotation (Д2)."""
        return self.contains(point, margin=via_radius)

    def inflated(self, margin: float) -> "PadArea":
        """A copy grown by `margin` on every side (immutable — the caller keeps
        the pad's own area intact for other consumers)."""
        return PadArea(center=self.center,
                       half_w=self.half_w + margin,
                       half_h=self.half_h + margin,
                       angle_deg=self.angle_deg)

    def bounds(self) -> Box2:
        """The axis-aligned board-frame box around the (rotated) area — for
        consumers that genuinely need an AABB, not for "is the pad here?"."""
        r = math.radians(self.angle_deg)
        cos_r, sin_r = math.cos(r), math.sin(r)
        half_x = abs(self.half_w * cos_r) + abs(self.half_h * sin_r)
        half_y = abs(self.half_w * sin_r) + abs(self.half_h * cos_r)
        return Box2(
            pos=Vector2(int(self.center.x - half_x), int(self.center.y - half_y)),
            size=Vector2(int(2 * half_x), int(2 * half_y)),
        )

    def __repr__(self) -> str:
        return (f"PadArea(center={self.center!r}, half=({self.half_w:.0f}, "
                f"{self.half_h:.0f}), angle={self.angle_deg:.2f})")


def pad_angle_deg(pad: Any) -> float:
    """The pad's ABSOLUTE angle in degrees.

    ``padstack.angle`` already carries the footprint's own rotation (measured
    15.09.2026: IC2 at 0° has padstack angles of 90°, IC3 at 90° has 180°), so
    it is used as-is — nothing is composed with the footprint angle here.
    """
    return math.degrees(getattr(pad, "angle_rad", 0.0) or 0.0)


def pad_area_of(pad: Any) -> PadArea | None:
    """The pad's OWN area, or None when the caller must fall back to KiCad's
    bounding box (``custom``/``unknown`` shape, or a padstack without a usable
    ``size``).

    A pad double that carries no ``shape`` attribute at all reads as a plain
    rectangle with no offset: tests/test_via_planner.py's
    ``MagicMock(spec=kipy Pad)`` pads set only number/position/size/angle_rad,
    and they must keep working with no edits (С11). That is a different case
    from an explicitly reported ``unknown`` shape, which has no own area.
    """
    size = getattr(pad, "size", None)
    if size is None:
        return None
    try:
        if float(size.x) <= 0.0 or float(size.y) <= 0.0:
            return None
    except (AttributeError, TypeError, ValueError):
        return None

    shape = getattr(pad, "shape", None)
    if shape is None:
        shape = PAD_SHAPE_RECT
    if shape in _SHAPES_WITHOUT_OWN_AREA or shape not in _SHAPES_WITH_OWN_AREA:
        return None

    center = _pad_area_center(pad)
    if center is None:
        return None

    half_w = float(size.x) / 2.0
    half_h = float(size.y) / 2.0
    if shape == PAD_SHAPE_TRAPEZOID:
        # The trapezoid's true outline depends on how KiCad reads
        # trapezoid_delta, so the bound is deliberately conservative: BOTH
        # half-sizes grow by max(|dx|, |dy|), which contains the shape under
        # either reading of the delta axes.
        grow = _trapezoid_grow_nm(pad)
        half_w += grow
        half_h += grow

    return PadArea(center=center, half_w=half_w, half_h=half_h,
                   angle_deg=pad_angle_deg(pad))


def _pad_area_center(pad: Any) -> Vector2 | None:
    """Centre of the pad SHAPE: ``position`` + the pad's own ``offset``
    (nm, in the pad's axes) rotated by the pad angle. An absent offset (or
    None) means a zero offset."""
    position = getattr(pad, "position", None)
    if position is None:
        return None
    offset = getattr(pad, "offset", None)
    if offset is None:
        return Vector2(position.x, position.y)
    try:
        rotated = rotate_local_offset(float(offset.x) / MM,
                                      float(offset.y) / MM,
                                      pad_angle_deg(pad))
    except (AttributeError, TypeError, ValueError):
        return Vector2(position.x, position.y)
    return Vector2(position.x + rotated.x, position.y + rotated.y)


def _trapezoid_grow_nm(pad: Any) -> float:
    delta = getattr(pad, "trapezoid_delta", None)
    if delta is None:
        return 0.0
    try:
        return max(abs(float(delta.x)), abs(delta.y))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def warn_bbox_fallback(logger, ref: str, pad: Any) -> None:
    """Warn that KiCad's bounding box is being used for a pad that has no own
    area, when that box cannot be trusted.

    The shift measured on 15.09.2026 (Д1) appears only for a footprint angle
    that is not a multiple of 90°; at 0/90/180/270° the box was exact, so the
    fallback stays silent there. The caller passes its own logger — this module
    has no logging policy of its own.
    """
    angle = pad_angle_deg(pad)
    if abs(angle % RIGHT_ANGLE_DEG) < 1e-6:
        return
    logger.warning(
        _("Pad {ref}.{pad}: no own pad area (shape {shape}) — falling back to "
          "KiCad's bounding box, which may be shifted at this pad angle "
          "({angle:.2f}°)").format(
            ref=ref, pad=getattr(pad, "number", "?"),
            shape=getattr(pad, "shape", None) or "?", angle=angle))
