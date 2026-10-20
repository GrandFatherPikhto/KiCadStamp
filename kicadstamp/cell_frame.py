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

The SECOND rule this module owns (2026-10-12, plan_2026_10_12_reread_frame_mirror):
`mirror_for_layer` — WHICH SIDE of the board an instance stands on. A cell frame
never CHOOSES a mirror from geometry: the side comes from the live footprint
against the cell's own `layer`, and a fit is merely CONSTRAINED by it. Every
reader of a live cluster must ask this one function instead of spelling the
comparison out (three copies drifted into the re-read defect of 2026-10-12).

The THIRD rule (2026-10-13, plan_2026_10_13_single_slot_rotation):
`fit_cell_frame` is for a cell with at least TWO usable slots. With fewer, nothing
can be fitted, and `single_slot_rotation` recovers the placement's own rotation
from the ONE component's live angle — the SAME inverse of the placement formula
(`clone_geometry.clone_rotation_from_component`), never a second copy of it. The
"never from one component's angle" caution above is about a MULTI-part cell, where
a symmetric part's angle is ambiguous by 180°; a single component has nothing to
disagree with.

Pure: no board access, no Qt, no config objects — mm floats in and out.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

from .domain.geometry import BoardLayer, Vector2
from .geometry.clone_geometry import clone_rotation_from_component
from .i18n import _
from .utils.layers import layer_to_str
from .utils.units import MM

logger = logging.getLogger(__name__)

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


def mirror_for_layer(fp_layer, cell_layer,
                     fallback: bool | None = False) -> bool | None:
    """Is the live instance MIRRORED relative to its cell? — i.e. is it standing
    on the OTHER SIDE of the board than the cell's own `layer` says.

    THE one host of that rule (2026-10-12): `clone_geometry` applies it at
    placement time, and the three readers that used to spell the comparison out
    — `cell_geometry_refresh._read_nested_live`, `live_position.
    _live_cluster_frame`, `tree_position._footprint_relative_mirror` — call this
    function now. Geometry NEVER decides the mirror: a fit is constrained by this
    answer, and the defect of 2026-10-12 (a re-read frame mirrored a cluster that
    stood on the cell's own side) is exactly what a second copy of the rule cost.

    `fallback` is returned when EITHER side is unknown — a duck-typed double whose
    `layer` is not a `BoardLayer`, or a cell whose layer is not a string (a config
    record that names no layer): the caller then keeps its historical behaviour
    instead of inventing a side. Both sides known is the ONLY case this function
    answers with the layer rule. The two callers' fallbacks differ on purpose and
    the TYPE says so: the pad/mount wrapper passes the placement's own config
    mirror (a `bool`), while the re-read frame passes `None` — "the fit stays
    free", which is exactly what an unknown side used to mean."""
    if not isinstance(fp_layer, BoardLayer) or not isinstance(cell_layer, str):
        return fallback
    return (fp_layer == BoardLayer.BL_B_Cu) != (cell_layer == "B.Cu")


def single_slot_rotation(live_angle_deg: float, slot_angle_deg: float,
                         mirror: bool) -> float:
    """The placement's own rotation recovered from the ONE component of a cell
    that has fewer than TWO usable slots (2026-10-13, plan_2026_10_13_single_slot_
    rotation — Denis: "Read current position" reported 0 for a part he had to turn
    by hand).

    A cell frame is normally FITTED from the stored offsets against the live
    deltas of every matched role — deliberately NOT from one component's angle,
    because a symmetric two-pin part's angle is ambiguous by 180° (see the module
    docstring). A cell with ONE usable slot has nothing to disagree WITH: its live
    angle is the only and exact source.

    The MATH is not written here: it is `clone_geometry.
    clone_rotation_from_component`, the ONE inverse of the placement's own
    `comp_angle` (apply_clone_geometry), so a single-slot read and a placement can
    never drift apart. `mirror` must come from `mirror_for_layer` (this module's
    other rule) and must be KNOWN: the mirrored branch of the formula is
    different, so a caller that cannot tell the side must not call this at all."""
    return clone_rotation_from_component(live_angle_deg, slot_angle_deg, mirror)


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


def pair_deviation_mm(pair: tuple[float, float, float, float],
                      rotation_deg: float, mirror: bool) -> float:
    """|predicted - live| (mm) for ONE reference-relative pair under this frame.

    The same arithmetic `_worst_deviation` maximises and `fit_cell_frame` reports
    as `residual_mm`; public because a refusal must be able to NAME the worst
    ROLES, not just the number (Ч1-А), and a second copy of this math would be a
    second answer to "how far is this slot"."""
    sa, sc, lx, ly = pair
    px, py = rotate_ydown_mm(sa, sc, rotation_deg)
    if mirror:
        px = -px
    return math.hypot(px - lx, py - ly)


def _worst_deviation(pairs, rotation_deg: float, mirror: bool) -> float:
    """The largest |predicted - live| (mm) over every pair for this candidate
    frame — the fit's honesty measure."""
    worst = 0.0
    for pair in pairs:
        worst = max(worst, pair_deviation_mm(pair, rotation_deg, mirror))
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
    # The PHYSICAL side of the instance and the cell's own layer, when the caller
    # knew them (frame_for_live_selection): the mirror line names both. None =
    # unknown — an engine caller that never had a layer.
    side_layer: str | None = None
    cell_layer: str | None = None

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


def frame_for_live_selection(matched, footprints, role_to_ref, origin, mount,
                             cell_layer=None) -> CellFrame:
    """The ONE cell<->world frame for a LIVE selection (J.1): the origin is the
    surrogate's live position offset back by the mount, the rotation is FITTED
    from the stored offsets against the live deltas of EVERY matched role at once
    (never a placement, never one component's angle), and the mirror — when the
    caller knows the cell's layer — is the instance's PHYSICAL side
    (mirror_for_layer), never the fit's choice.

    Moved here from cell_geometry_refresh (2026-10-12, plan_2026_10_12_reread_
    frame_mirror, Г1 of its acceptance): the frame belongs to THIS module, and
    that giant only shrinks. `origin`/`mount` are the role match's own outputs
    (_cell_selection_context); with no usable pair (a first extraction, a
    single-component cell) the historical theta=0 / mirror=False frame is kept."""
    placement_origin = Vector2.from_xy(
        origin.x + int(round(mount[0] * MM)),
        origin.y + int(round(mount[1] * MM)))
    # The side of the INSTANCE: the surrogate's footprint IS the one standing on
    # `placement_origin` (the origin was built from it), so no second lookup rule
    # is invented here; the rule itself is the ONE host above. No known side ->
    # None -> the fit stays free.
    mirror: bool | None = None
    side_layer: str | None = None
    if cell_layer is not None:
        for fp in footprints:
            if fp.position.x == placement_origin.x \
                    and fp.position.y == placement_origin.y:
                mirror = mirror_for_layer(fp.layer, cell_layer, fallback=None)
                if isinstance(fp.layer, BoardLayer):
                    side_layer = layer_to_str(fp.layer)
                break
    ref_to_fp = {fp.ref: fp for fp in footprints}
    pairs: list[tuple[float, float, float, float]] = []
    for rec in matched:
        fp = ref_to_fp.get(role_to_ref.get(rec.get("role")))
        if fp is None:
            continue
        pairs.append((
            float(rec.get("offset_along_mm", 0.0)) - mount[0],
            float(rec.get("offset_across_mm", 0.0)) - mount[1],
            (fp.position.x - placement_origin.x) / MM,
            (fp.position.y - placement_origin.y) / MM))
    fit = fit_cell_frame(pairs, mirror=mirror)
    # Fewer than TWO matched slots -> no pair to fit, and the ONE component's live
    # angle is the only exact source (the rule and its math live in
    # single_slot_rotation). It needs a KNOWN side: without one the frame keeps
    # today's theta=0, said out loud instead of silently (plan rule 4).
    single_rot: float | None = None
    if fit is None and len(matched) == 1:
        only = matched[0]
        single_fp = ref_to_fp.get(role_to_ref.get(only.get("role")))
        if single_fp is None or mirror is None:
            logger.warning(_(
                "the rotation of the cell's single component could not be "
                "determined (no live footprint, or the board side is unknown) — "
                "0° is used, which may be wrong for a turned instance"))
        else:
            single_rot = single_slot_rotation(
                float(single_fp.angle_deg),
                float(only.get("angle_deg", 0.0)), bool(mirror))
    if fit is None:
        return CellFrame(placement_origin=placement_origin,
                         rotation_deg=single_rot if single_rot is not None else 0.0,
                         mirror=bool(mirror) if single_rot is not None else False,
                         mount=(mount[0], mount[1]), side_layer=side_layer,
                         cell_layer=cell_layer)
    return CellFrame(placement_origin=placement_origin,
                     rotation_deg=fit.rotation_deg, mirror=fit.mirror,
                     mount=(mount[0], mount[1]), residual_mm=fit.residual_mm,
                     side_layer=side_layer, cell_layer=cell_layer)


def frame_warnings(frame: CellFrame | None) -> list[str]:
    """The user-facing Log lines for a fitted frame — a non-rigid cluster, a
    turned instance and/or a MIRRORED one. Empty for None, for the historical
    unrotated rigid frame and for a fit that found nothing.

    Moved here with frame_for_live_selection (2026-10-12, Г1 of the plan's
    acceptance). The mirror line is the one Denis asked for, and its direction
    matters: the re-read UN-MIRRORS the instance's geometry into the cell's own
    frame — the CELL is not mirrored by the read."""
    if frame is None:
        return []
    warnings: list[str] = []
    if not frame.is_rigid:
        warnings.append(
            _("the selection is not a rigid copy of this cell (worst deviation "
              "{deviation} mm) — the geometry was re-read anyway")
            .format(deviation=f"{frame.residual_mm:.3f}"))
    if frame.rotation_deg:
        warnings.append(
            _("the instance is rotated {angle}° — the geometry was expressed in "
              "the cell's own frame, anchor_xy is left unchanged")
            .format(angle=f"{frame.rotation_deg:g}"))
    if frame.mirror:
        if frame.side_layer and frame.cell_layer:
            warnings.append(
                _("the instance stands on the other side of the board "
                  "({side} vs the cell's {cell_layer}) — its geometry was "
                  "un-mirrored into the cell's own frame").format(
                    side=frame.side_layer, cell_layer=frame.cell_layer))
        else:
            warnings.append(
                _("the instance is mirrored relative to the cell — its geometry "
                  "was un-mirrored into the cell's own frame"))
    return warnings
