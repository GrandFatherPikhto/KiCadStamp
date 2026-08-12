# kicadstamp/placement/services/coordinate_position_calculator.py
"""
coordinate_position_calculator.py — resolves CoordinatePlacement entries
(the "dumb placer", Denis 2026-08-12: see config/models.py's
CoordinatePlacement docstring for the full design rationale) into
MoveCommands. Unlike ClonePlacement/Rule, there is no template, no offset
list, no via/track — just "find this one existing footprint, move it to
this one absolute point with this one rotation".

Reuses the project's existing rotation/anchor-pad primitives rather than
reinventing them:
  - local_to_absolute() (geometry/spoke_layout.py) — the SAME rotation
    formula every other subsystem uses (kipy.geometry.Vector2.rotate()), so
    the polar mode's angle_deg follows the identical sign/axis convention
    as rotation_deg everywhere else in the project — not a second,
    independently-invented rotation. resolve_self_pad_anchor() below needs
    the same underlying Vector2.rotate() call too, but on ALREADY-native
    (not mm) offsets — see its _rotate_native() helper.
  - resolve_anchor_pad_position() (component_resolver.py) — for reading a
    pad's CURRENT absolute position; existing callers (ClonePlacement,
    Point) use it for a DIFFERENT, stationary anchor component, but the
    "read this pad's absolute position, fatal if it doesn't exist" part is
    identical, so it's reused as-is for our self-referential case too (see
    resolve_self_pad_anchor()'s own docstring for what's genuinely new).

No registry involvement anywhere here — see CoordinatePlacement's own
docstring for why a move is idempotent by construction, same as Rule/
ClonePlacement's own component moves in apply_pipeline.py's Phase 1.
"""
import logging

from kipy.board_types import FootprintInstance
from kipy.geometry import Vector2, Angle

from ...config import CoordinatePlacement, coordinate_placement_effective_name
from ...constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME
from ...geometry.spoke_layout import local_to_absolute
from ...i18n import _
from ...utils.units import MM
from ..commands import MoveCommand
from .clone_role_resolver import resolve_footprint_by_role, resolve_unique_footprint_by_fields
from .component_resolver import resolve_anchor_pad_position, resolve_footprint_by_ref
from .point_resolver import resolve_point_chain

logger = logging.getLogger(__name__)

_ORIGIN = Vector2.from_xy(0, 0)


def _rotate_native(vec: Vector2, angle_deg: float) -> Vector2:
    """Rotate an ALREADY-native-units Vector2 by angle_deg around (0,0) —
    the exact same kipy.geometry.Vector2.rotate() call rotate_local_offset()
    itself wraps (geometry/spoke_layout.py), used directly here because our
    inputs (a pad's offset from its footprint's origin) are already native
    units, not mm: round-tripping through rotate_local_offset's mm/int
    conversion twice (once to undo the current rotation, once to apply the
    new one) measurably loses precision — found via a unit test asserting
    exact cancellation for new_rotation_deg == current rotation, off by 1-2
    nanometres. Same rotation convention, no double rounding."""
    return vec.rotate(Angle.from_degrees(angle_deg), _ORIGIN)


def resolve_footprint_by_cluster_role(adapter, cluster: str, role: str, label: str) -> FootprintInstance:
    """Exact-match lookup — same convention as ClonePlacement's cluster:
    mode (resolve_by_cluster_tag in clone_role_resolver.py, 2026-08-06:
    "Cluster is meant to be unique per instance... a direct, unconditional
    field match — no narrowing cascade; ambiguity here is a tagging
    mistake, not something to resolve"). Deliberately EXACT equality on
    Cluster (not cluster_prefix_match) — that prefix-narrowing convention
    is for resolving ambiguity among several same-Role candidates inside a
    hierarchy, a different problem than identifying ONE specific,
    already-uniquely-tagged instance, which is what both this and
    resolve_by_cluster_tag actually do. Role is ALSO exact-matched (it
    already is everywhere in the project — see project's Role/Cluster
    architecture notes). Thin wrapper over the shared
    resolve_unique_footprint_by_fields (clone_role_resolver.py, 2026-08-12)."""
    return resolve_unique_footprint_by_fields(
        adapter, {ROLE_FIELD_NAME: role, CLUSTER_FIELD_NAME: cluster}, label)


def resolve_target_position(cp: CoordinatePlacement) -> tuple[Vector2, float]:
    """(target point, final rotation_deg) from EITHER of CoordinatePlacement's
    two mutually-exclusive position modes (config/entries.py already
    guarantees exactly one is fully populated — no validation repeated
    here). Polar's point is computed via local_to_absolute(center, radius,
    0, angle) — i.e. "a purely along-axis offset, rotated by angle_deg" —
    the exact same primitive/convention as every cell's own along/across
    offsets, so angle_deg's rotation direction is guaranteed consistent
    with rotation_deg's meaning everywhere else.

    Defensive (2026-08-12, Group 2 item 12): the "x_mm set => Cartesian,
    else polar" invariant is enforced by the LOADER, not the dataclass —
    directly-constructed objects (tests) with a half-filled polar mode
    (radius_mm set, angle_deg None) would otherwise silently use angle_deg's
    None as if it were 0.0, or crash on a bare TypeError. Fail loudly
    instead."""
    assert (cp.x_mm is not None) != (cp.center_x_mm is not None), (
        f"CoordinatePlacement {coordinate_placement_effective_name(cp)!r}: "
        "exactly one of x_mm/y_mm (Cartesian) or center_x_mm/center_y_mm "
        "(polar) must be set — this should have been caught at load time")
    if cp.x_mm is not None:
        target = Vector2.from_xy(int(cp.x_mm * MM), int(cp.y_mm * MM))
        rotation_deg = cp.rotation_deg
    else:
        center = Vector2.from_xy(int(cp.center_x_mm * MM), int(cp.center_y_mm * MM))
        target = local_to_absolute(center, cp.radius_mm, 0.0, cp.angle_deg)
        rotation_deg = cp.rotation_deg if cp.rotation_deg is not None else cp.angle_deg
    return target, rotation_deg


def resolve_self_pad_anchor(adapter, fp: FootprintInstance, pad_number: str,
                            target: Vector2, new_rotation_deg: float, label: str) -> Vector2:
    """Where must fp's OWN origin end up so that, after rotating to
    new_rotation_deg, pad `pad_number` lands exactly on `target`? This is
    genuinely new geometry — resolve_anchor_pad_position (reused below for
    the "read a pad's current absolute position, fatal if missing" part)
    exists only for a DIFFERENT, stationary anchor component elsewhere in
    the codebase; nothing resolves a footprint's own pad offset against ITS
    OWN upcoming rotation.

    Method: read the pad's CURRENT offset from fp's CURRENT origin (world
    frame), un-rotate it by fp's CURRENT orientation to get the offset in
    the footprint's own local/unrotated frame (a fixed property of its
    footprint layout, independent of where it's sitting right now), then
    re-rotate that local offset by the NEW rotation and subtract it from
    the target — the new origin. Both rotations go through _rotate_native()
    — the same kipy.geometry.Vector2.rotate() call/convention
    rotate_local_offset() itself uses, just without its mm round-trip
    (unnecessary here — see _rotate_native's own docstring)."""
    pad_position = resolve_anchor_pad_position(adapter, fp, pad_number, label)
    world_offset = Vector2.from_xy(pad_position.x - fp.position.x, pad_position.y - fp.position.y)
    local_offset = _rotate_native(world_offset, -fp.orientation.degrees)
    new_offset = _rotate_native(local_offset, new_rotation_deg)
    return Vector2.from_xy(target.x - new_offset.x, target.y - new_offset.y)


def _has_external_anchor(cp: CoordinatePlacement) -> bool:
    """True when cp is in the ANCHOR-RELATIVE mode (2026-08-12, Group 0
    consolidation): one of anchor_ref/anchor_role/anchor_point identifies a
    DIFFERENT, stationary component/point, and x_mm/y_mm or radius_mm/angle_deg
    are an OFFSET from it. Distinct from the self-referential `anchor == 'pad'`
    (which only ever coexists with the absolute modes)."""
    return (cp.anchor_ref is not None or cp.anchor_role is not None
            or cp.anchor_point is not None)


def _resolve_external_anchor(adapter, cp: CoordinatePlacement, points, sheet_names,
                             label: str) -> Vector2:
    """Absolute position of cp's OTHER-component anchor — anchor_ref/anchor_role
    (+ anchor_sheet/anchor_cluster, narrowed exactly like ClonePlacement/Rule's
    anchors via the shared resolve_footprint_by_ref / resolve_footprint_by_role)
    or anchor_point (resolved standalone via resolve_point_chain, since
    coordinate_placements run in Phase 0, before the planner populates its own
    resolved_points). anchor_pad narrows to one pad of the resolved anchor
    footprint (resolve_anchor_pad_position — the same helper ClonePlacement's
    _resolve_anchor uses). Returns the anchor point in native units."""
    if cp.anchor_point is not None:
        resolved = resolve_point_chain(adapter, points, cp.anchor_point, sheet_names)
        return resolved.position
    if cp.anchor_ref is not None:
        fp = resolve_footprint_by_ref(adapter, cp.anchor_ref, label)
    else:
        fp = resolve_footprint_by_role(adapter, cp.anchor_role, cp.anchor_sheet,
                                       cp.anchor_cluster, sheet_names, label)
    if cp.anchor_pad is None:
        return fp.position
    return resolve_anchor_pad_position(adapter, fp, cp.anchor_pad, label)


def _anchor_offset_mm(cp: CoordinatePlacement) -> tuple[float, float]:
    """(offset_x_mm, offset_y_mm) of the anchor-relative offset: the literal
    x_mm/y_mm in Cartesian mode, or local_to_absolute(0, radius, 0, angle) —
    "radius along the X axis, rotated by angle_deg", the same primitive as
    every cell's along/across offsets — in polar mode."""
    if cp.radius_mm is not None:
        offset = local_to_absolute(_ORIGIN, cp.radius_mm, 0.0, cp.angle_deg)
        return offset.x / MM, offset.y / MM
    return cp.x_mm or 0.0, cp.y_mm or 0.0


def build_coordinate_moves(adapter, coordinate_placements: list[CoordinatePlacement],
                           points=None, sheet_names=None) -> list[MoveCommand]:
    """The whole module in one call — CoordinatePlacement entries in,
    MoveCommands out, ready for MoveExecutor.execute_moves() (no new
    executor needed, see apply_pipeline.py's Phase 0 integration).

    Anchor-relative entries (anchor_ref/anchor_role/anchor_point) additionally
    need: points — cfg.points ({name: Point}), to resolve an anchor_point
    reference standalone (Phase 0 runs before the planner populates its
    resolved_points); sheet_names — {uuid: name}, for anchor_role narrowing.
    Both optional — the absolute modes never touch them."""
    moves = []
    for cp in coordinate_placements:
        label = coordinate_placement_effective_name(cp)
        fp = resolve_footprint_by_cluster_role(adapter, cp.cluster, cp.role, label)
        if _has_external_anchor(cp):
            # ANCHOR-RELATIVE: target = anchor position (+ its anchor_pad) + offset.
            anchor_pos = _resolve_external_anchor(adapter, cp, points or {},
                                                  sheet_names or {}, label)
            dx_mm, dy_mm = _anchor_offset_mm(cp)
            target = Vector2.from_xy(anchor_pos.x + int(dx_mm * MM),
                                     anchor_pos.y + int(dy_mm * MM))
            # Default rotation: angle_deg in polar-offset mode (spoke-style,
            # same as the fixed-centre polar), 0.0 in Cartesian-offset mode —
            # resolved here, loader stores the raw value (or None).
            rotation_deg = (cp.rotation_deg if cp.rotation_deg is not None
                            else (cp.angle_deg if cp.radius_mm is not None else 0.0))
            origin = target
        else:
            target, rotation_deg = resolve_target_position(cp)
            origin = (resolve_self_pad_anchor(adapter, fp, cp.anchor_pad, target, rotation_deg, label)
                      if cp.anchor == 'pad' else target)
        moves.append(MoveCommand(
            ref=fp.reference_field.text.value,
            position=origin,
            angle=Angle.from_degrees(rotation_deg),
            layer=fp.layer,
        ))
        logger.info(_("[{label}] {ref} -> ({x:.3f}, {y:.3f}) mm, {angle}°")
                    .format(label=label, ref=fp.reference_field.text.value,
                            x=origin.x / MM, y=origin.y / MM, angle=rotation_deg))
    return moves
