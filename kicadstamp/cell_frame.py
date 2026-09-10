# kicadstamp/cell_frame.py
"""ONE "cell frame <-> world" transform for every reader of a live cluster.

2026-09-10 (plan_2026_09_10_marker_frame_and_sheets — the shared contract):
the paths that READ a live cluster must ALL express live geometry through the
SAME transform, and that transform must be derived FROM THE DATA — the cell's
stored offsets against the live deltas of the very same roles — never from a
placement record. Neither `config.clone_placements`, nor the trees, nor
`materialize_entity_placements`, nor `resolve_clone_context_live` takes part in
it (Denis, 2026-09-10: "если мы будем читать размещение читаемого и
перечитываемого кластера через размещение, то у нас и будут возникать те баги,
которые мы ловим").

The transform is exactly `clone_geometry.apply_clone_geometry`'s forward mapping,
inverted:

    world = placement_origin + flip_x(rotate_ydown(stored - A, theta))
    flip_x(x, y) = (-x, y) when mirror else (x, y)

where A is the cell's mount (the cell point that lands on the placement origin)
and `rotate_ydown` is the project's one rotation primitive
(`spoke_layout.rotate_local_offset`, KiCad's Y-down convention). A cell frame is
therefore (theta, mirror) plus the two world points that anchor it: the
placement origin and the mount A.

`fit_cell_frame` derives (theta, mirror) by fitting the stored offsets against
the live deltas over ALL matched roles AT ONCE — deliberately NOT from one
component's angle: a two-pin part is symmetric, its angle ambiguous by 180°
(measured 2026-09-10: four capacitors gave +90 while FB_PI_FLT gave -90, exactly
180° apart). Geometry has no such ambiguity. theta is orthogonal: it is snapped
to the nearest multiple of 90° when the fit sits inside a small tolerance, and
the worst per-role deviation is reported as `residual_mm` so a caller can warn
when the cluster is not a rigid copy of the cell (Denis's FB_PI_FLT, ~0.8 mm)
instead of silently "straightening" a genuine board edit; the angle itself is
ALWAYS snapped onto the orthogonal grid, so only the role that really moved
carries the deviation into the cell.

Pure: no board access, no Qt, no config objects — mm floats in and out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .domain.geometry import Vector2
from .geometry.clone_geometry import clone_rotation_from_component
from .utils.units import MM

# Worst per-role deviation (mm) still considered a rigid copy of the cell.
RIGID_TOLERANCE_MM = 0.05
# A pair shorter than this carries no usable direction — ignored by the fit
# (the reference/surrogate role itself is always (0,0) -> (0,0)).
_MIN_PAIR_MM = 1e-6


def normalize_deg(angle: float) -> float:
    """Normalise an angle to (-180, 180] — the project-wide convention
    (clone_geometry.clone_rotation_from_component, tree_position)."""
    return (angle + 180.0) % 360.0 - 180.0


def rotate_ydown_mm(x_mm: float, y_mm: float, rotation_deg: float) -> tuple[float, float]:
    """The SAME rotation `rotate_local_offset` performs (KiCad's Y-down
    convention: x' = x*cos + y*sin, y' = -x*sin + y*cos), as plain mm floats —
    no nm round-trip, so a fit can compare sub-micrometre residuals."""
    r = math.radians(rotation_deg)
    cos_r, sin_r = math.cos(r), math.sin(r)
    return (x_mm * cos_r + y_mm * sin_r, -x_mm * sin_r + y_mm * cos_r)


@dataclass(frozen=True)
class CellFit:
    """A rigid-frame fit: the rotation (already orthogonal) and the optional
    mirror, plus the worst per-role deviation that produced them."""
    rotation_deg: float
    mirror: bool
    residual_mm: float

    @property
    def is_rigid(self) -> bool:
        return self.residual_mm <= RIGID_TOLERANCE_MM


def _worst_deviation(pairs, rotation_deg: float, mirror: bool) -> float:
    """The largest |predicted - live| (mm) over every pair for this candidate
    frame — the fit's honesty measure."""
    worst = 0.0
    for sa, sc, lx, ly in pairs:
        px, py = rotate_ydown_mm(sa, sc, rotation_deg)
        if mirror:
            px = -px
        worst = max(worst, math.hypot(px - lx, py - ly))
    return worst


def reference_relative_pairs(reference: tuple[float, float, float, float],
                             others: Iterable[tuple[float, float, float, float]],
                             ) -> list[tuple[float, float, float, float]]:
    """Pairs expressed RELATIVE to one reference pair — (stored − stored_ref,
    live − live_ref). Translation cancels out, so the fit needs neither the
    mount A nor a placement origin: it can be run before either is known (the
    overlay's case). `reference` is (stored_along_mm, stored_across_mm,
    live_x_mm, live_y_mm) of the reference role."""
    sa0, sc0, lx0, ly0 = reference
    return [(sa - sa0, sc - sc0, lx - lx0, ly - ly0)
            for sa, sc, lx, ly in others]


def fit_cell_frame(pairs: Iterable[tuple[float, float, float, float]],
                   mirror: bool | None = None) -> CellFit | None:
    """Fit (theta, mirror) from `pairs` of (stored_along_mm, stored_across_mm,
    live_dx_mm, live_dy_mm) — BOTH sides already expressed relative to the same
    anchor (the cell's mount A on the stored side, the placement origin on the
    live side). Zero-length pairs are ignored (no direction). Returns None when
    nothing usable is left; the caller then keeps the historical
    theta=0 / mirror=False frame.

    theta is the circular mean of the per-pair rotation, ALWAYS snapped to the
    nearest multiple of 90° — a cell frame is orthogonal by construction, and
    snapping is what makes an unaffected slot keep its stored offset when a
    NEIGHBOUR slot has genuinely been moved on the board (the deviation of such
    a cluster is then reported by residual_mm, not baked into the whole cell as
    a fraction-of-a-degree skew). Both mirror candidates are evaluated and the
    smaller residual wins (mirror=False on a tie — the conservative,
    today-compatible answer, and the only thing a single usable pair can ever
    say).

    `mirror` — pass it to CONSTRAIN the fit to a known side (the overlay knows
    the instance's side from the live footprint's layer and only needs theta
    from the geometry); leave it None to let the fit choose."""
    usable = [(sa, sc, lx, ly) for sa, sc, lx, ly in pairs
              if math.hypot(sa, sc) > _MIN_PAIR_MM
              and math.hypot(lx, ly) > _MIN_PAIR_MM]
    if not usable:
        return None

    candidates = (False, True) if mirror is None else (mirror,)
    best: CellFit | None = None
    for mirror in candidates:
        sin_sum = 0.0
        cos_sum = 0.0
        for sa, sc, lx, ly in usable:
            a_stored = math.atan2(sc, sa)
            # flip_x first (inverse of apply's _mirror_x) — a mirrored live
            # vector is un-flipped before it is compared with the stored one.
            a_live = math.atan2(ly, -lx) if mirror else math.atan2(ly, lx)
            # rotate_ydown SUBTRACTS theta from the direction angle.
            delta = a_stored - a_live
            sin_sum += math.sin(delta)
            cos_sum += math.cos(delta)
        rotation = math.degrees(math.atan2(sin_sum, cos_sum))
        rotation = normalize_deg(round(rotation / 90.0) * 90.0)
        candidate = CellFit(rotation_deg=rotation, mirror=mirror,
                            residual_mm=_worst_deviation(usable, rotation, mirror))
        if best is None or (candidate.residual_mm, candidate.mirror) < \
                (best.residual_mm, best.mirror):
            best = candidate
    return best


@dataclass(frozen=True)
class CellFrame:
    """The one "cell frame <-> world" transform.

    placement_origin — the world point (nm) the cell's MOUNT lands on, i.e.
    apply_clone_geometry's `origin`; mount — the cell's mount A in its own
    stored frame (mm); rotation_deg/mirror — the (orthogonal) frame orientation;
    residual_mm — how far the live cluster is from a rigid copy of the cell
    (0.0 when the frame was not fitted, e.g. the first extraction)."""
    placement_origin: Vector2
    rotation_deg: float = 0.0
    mirror: bool = False
    mount: tuple[float, float] = (0.0, 0.0)
    residual_mm: float = 0.0

    @classmethod
    def from_reference(cls, *, rotation_deg: float, mirror: bool,
                       mount: tuple[float, float],
                       stored_ref: tuple[float, float],
                       live_ref_mm: tuple[float, float],
                       residual_mm: float = 0.0) -> "CellFrame":
        """The frame of a LIVE instance, anchored on ONE reference slot: the
        placement origin is the live reference point minus the reference slot's
        rotation (the inverse of point_to_world_mm for that one slot). This is
        exactly clone_origin_from_component's math — but with theta supplied by
        fit_cell_frame instead of being inferred from one component's ANGLE."""
        ax, ay = mount
        rx, ry = rotate_ydown_mm(stored_ref[0] - ax, stored_ref[1] - ay,
                                 rotation_deg)
        if mirror:
            rx = -rx
        return cls(placement_origin=Vector2.from_xy_mm(live_ref_mm[0] - rx,
                                                      live_ref_mm[1] - ry),
                   rotation_deg=rotation_deg, mirror=mirror, mount=mount,
                   residual_mm=residual_mm)

    @property
    def is_rigid(self) -> bool:
        return self.residual_mm <= RIGID_TOLERANCE_MM

    def point_to_world_mm(self, along_mm: float, across_mm: float) -> tuple[float, float]:
        """A cell point (its stored bbox frame) -> world mm."""
        ax, ay = self.mount
        rx, ry = rotate_ydown_mm(along_mm - ax, across_mm - ay, self.rotation_deg)
        if self.mirror:
            rx = -rx
        return (self.placement_origin.x / MM + rx,
                self.placement_origin.y / MM + ry)

    def point_to_world_nm(self, along_mm: float, across_mm: float) -> tuple[int, int]:
        """Same as point_to_world_mm, as integer nm — what the copper matcher
        compares against live points."""
        x_mm, y_mm = self.point_to_world_mm(along_mm, across_mm)
        return (int(round(x_mm * MM)), int(round(y_mm * MM)))

    def point_to_cell(self, world_x_mm: float, world_y_mm: float) -> tuple[float, float]:
        """A world point -> the cell's own stored bbox frame (the inverse of
        point_to_world_mm — un-mirror first, then rotate back)."""
        ax, ay = self.mount
        rx = world_x_mm - self.placement_origin.x / MM
        ry = world_y_mm - self.placement_origin.y / MM
        if self.mirror:
            rx = -rx
        rx, ry = rotate_ydown_mm(rx, ry, -self.rotation_deg)
        return (rx + ax, ry + ay)

    def angle_to_cell(self, world_angle_deg: float) -> float:
        """A live footprint's orientation -> its cell-slot angle (live - theta,
        with clone_geometry's own mirrored rule), normalised to (-180, 180]."""
        return clone_rotation_from_component(
            world_angle_deg, self.rotation_deg, self.mirror)
