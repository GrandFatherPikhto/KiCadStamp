# kicadstamp/geometry/clone_geometry.py
"""
clone_geometry.py — transforms a cell into absolute board coordinates
for ClonePlacement (TemplatePlacer), unlike spoke_layout.py:

  - origin = xy DIRECTLY (no pad, no shift — it is an
    absolute point, not an offset from something).
  - net of each via is resolved via net_resolution.resolve_net()
    (params + net_overrides) — there is NO concept of rule_net (ClonePlacement,
    unlike Rule/ManualSpoke, has no single "rule net" at all).
    via.net=None is FATAL here — there is no sensible default to fall back to,
    unlike in spoke_layout.py.
"""
from ..domain.geometry import Vector2

from ..config import (ClonePlacement, Cell, TemplateVia, TemplateTrack,
                      clone_placement_effective_name)
from ..exceptions import ValidationError, format_fatal_error
from ..net_resolution import resolve_net
from ..utils.units import MM
from .spoke_layout import (
    local_to_absolute, rotate_local_offset, ResolvedVia, ResolvedTrack, ComponentLayout, SpokeLayout,
)
from ..i18n import _


def _net_from_resolved(role: str, pad: str | None, resolved_role_nets: dict,
                       kind: str, clone: ClonePlacement, where: str) -> str:
    """Net for a net_from_role-bearing via/track from the pre-resolved dict.

    The calculator resolves every net_from_role BEFORE geometry (the
    "geometry does not touch the live board" boundary), so a missing key here
    is an internal-consistency failure, not a data problem — report it as such
    rather than silently defaulting.
    """
    try:
        return resolved_role_nets[(role, pad)]
    except KeyError:
        raise ValidationError(format_fatal_error(
            _("{kind} with net_from_role not resolved in cell {cell!r} ({name!r})")
            .format(kind=kind, cell=clone.cell, name=clone_placement_effective_name(clone)),
            [_("{where} has net_from_role={role!r}{pad_txt}, but it was not "
               "resolved before geometry — internal consistency: the calculator "
               "must resolve every net_from_role prior to apply_clone_geometry")
             .format(where=where, role=role,
                     pad_txt=f", pad={pad}" if pad is not None else "")]
        ))


def _resolve_clone_via(origin: Vector2, via: TemplateVia, rotation_deg: float,
                       clone: ClonePlacement, mirror: bool = False,
                       resolved_role_nets: dict | None = None) -> ResolvedVia:
    if via.net_from_role is not None:
        net = _net_from_resolved(via.net_from_role, via.net_from_role_pad,
                                 resolved_role_nets or {}, "via", clone,
                                 _("via at (along={along}, across={across})").format(
                                     along=via.offset_along_mm, across=via.offset_across_mm))
    else:
        if via.net is None:
            raise ValidationError(format_fatal_error(
                _("via without net in cell {cell!r} ({name!r})").format(
                    cell=clone.cell, name=clone_placement_effective_name(clone)),
                [_("via at (along={along}, across={across}) has no net — "
                   "ClonePlacement has no default rule net (unlike ManualSpoke), "
                   "so every via in a cloned cell must have a net explicitly set")
                 .format(along=via.offset_along_mm, across=via.offset_across_mm)]
            ))
        net = resolve_net(via.net, clone.params, clone.net_overrides)
    pos = local_to_absolute(origin, via.offset_along_mm, via.offset_across_mm, rotation_deg)
    if mirror:
        pos = _mirror_x(origin, pos)
    return ResolvedVia(
        position=pos,
        net=net,
        drill_mm=via.drill_mm,
        diameter_mm=via.diameter_mm,
    )


def _resolve_clone_track(origin: Vector2, track: TemplateTrack, rotation_deg: float,
                         clone: ClonePlacement, tpl_layer: str,
                         mirror: bool = False,
                         resolved_role_nets: dict | None = None) -> ResolvedTrack:
    if track.net_from_role is not None:
        net = _net_from_resolved(track.net_from_role, track.net_from_role_pad,
                                 resolved_role_nets or {}, "track", clone,
                                 _("track (along={s_along},{s_across} -> "
                                   "{e_along},{e_across})").format(
                                     s_along=track.start_along_mm, s_across=track.start_across_mm,
                                     e_along=track.end_along_mm, e_across=track.end_across_mm))
    else:
        if track.net is None:
            raise ValidationError(format_fatal_error(
                _("track without net in cell {cell!r} ({name!r})").format(
                    cell=clone.cell, name=clone_placement_effective_name(clone)),
                [_("track (along={s_along},{s_across} -> {e_along},{e_across}) has no net — "
                   "every track in a cloned cell must have a net, just like vias")
                 .format(s_along=track.start_along_mm, s_across=track.start_across_mm,
                         e_along=track.end_along_mm, e_across=track.end_across_mm)]
            ))
        net = resolve_net(track.net, clone.params, clone.net_overrides)
    start = local_to_absolute(origin, track.start_along_mm, track.start_across_mm, rotation_deg)
    end = local_to_absolute(origin, track.end_along_mm, track.end_across_mm, rotation_deg)
    layer = track.layer or tpl_layer
    if mirror:
        start = _mirror_x(origin, start)
        end = _mirror_x(origin, end)
        layer = 'F.Cu' if layer == 'B.Cu' else 'B.Cu'
    return ResolvedTrack(
        start=start,
        end=end,
        width_mm=track.width_mm,
        net=net,
        layer=layer,
    )


def _mirror_x(origin: Vector2, p: Vector2) -> Vector2:
    """X‑mirror of a point relative to the vertical axis through origin."""
    return Vector2.from_xy(2 * origin.x - p.x, p.y)


def clone_shift_mm(clone) -> tuple[float, float]:
    """(x, y) mm of a clone placement's offset in its OWN (unrotated) frame —
    the single source of truth for registry identity / duplicate-anchor keys
    (clone_anchor_id in clone_position_calculator.py and
    check_no_duplicate_clone_anchors in validation.py). Cartesian xy is used
    directly; polar (radius_mm/angle_deg) is converted via the SAME
    rotate_local_offset(radius, 0, angle) primitive apply_clone_geometry uses
    at parent_rotation_deg=0 — so a polar clone's offset can never be misread
    as the loader's default (0, 0), and two polar clones on the same anchor
    with different radii do not collapse to one registry identity. Works for
    CellPlacement too (no polar fields -> its xy, unchanged)."""
    radius_mm = getattr(clone, "radius_mm", None)
    if radius_mm is not None:
        v = rotate_local_offset(radius_mm, 0.0, getattr(clone, "angle_deg", 0.0))
        return (v.x / MM, v.y / MM)
    return (clone.xy[0], clone.xy[1])


def clone_layout_origin(clone: ClonePlacement,
                        anchor_position: Vector2 | None,
                        parent_rotation_deg: float = 0.0) -> Vector2:
    """World-space position of a clone placement's cell-local (0,0) — EXACTLY
    the `origin` apply_clone_geometry() computes (same shift composition via
    rotate_local_offset, same anchor+shift addition), without resolving any
    cell content (no role_to_ref, no vias/tracks).

    Split out (2026-08-25, handoff composite_cell_autodetect_and_cycle_guard)
    so ExtractDock's Sub-placements can convert an existing top-level
    placement's world origin into a new cell's local xy without re-running the
    whole geometry. parent_rotation_deg defaults to 0.0 (a top-level
    ClonePlacement has no parent frame) and mirrors apply_clone_geometry's
    composition for completeness — a nested CellPlacement's xy IS expressed in
    the parent's rotated frame, same rule as everywhere else in this module.

    anchor_position is resolved EXTERNALLY (live board reads stay in the
    caller, geometry does not touch the board — the same boundary
    apply_clone_geometry documents)."""
    radius_mm = getattr(clone, "radius_mm", None)
    if radius_mm is not None:
        shift = rotate_local_offset(radius_mm, 0.0,
                                    getattr(clone, "angle_deg", 0.0) + parent_rotation_deg)
    else:
        shift = rotate_local_offset(clone.xy[0], clone.xy[1], parent_rotation_deg)
    if anchor_position is not None:
        return Vector2.from_xy(anchor_position.x + shift.x, anchor_position.y + shift.y)
    return shift


def apply_clone_geometry(
    clone: ClonePlacement,
    cell: Cell,
    role_to_ref: dict[str, str],
    anchor_position: Vector2 | None = None,
    mirror: bool = False,
    parent_rotation_deg: float = 0.0,
    resolved_role_nets: dict | None = None,
) -> SpokeLayout:
    """
    Computes absolute positions of everything in the cell for a specific
    ClonePlacement. role_to_ref is already resolved EXTERNALLY (see
    clone_role_resolver.py).

    anchor_position — absolute anchor point (pad centre or footprint centre from
    anchor_ref/anchor_pad), resolved EXTERNALLY (the calculator goes to the
    adapter, geometry does not touch the live board). If set — origin_x/y_mm
    act as a FLAT shift from it (without rotation, exactly like shift in
    ManualSpoke); rotation_deg rotates only the cell contents. If None —
    origin_x/y_mm remain absolute board coordinates, as before.

    parent_rotation_deg (Phase 4, recursive Cell, default 0.0 — every
    existing top-level call site is unaffected) — the ALREADY-ACCUMULATED
    rotation of the parent frame this clone is nested inside (0.0 for a
    top-level ClonePlacement, which has no parent). When nonzero: clone.xy
    is a shift EXPRESSED IN THE PARENT'S OWN (rotated) frame — "5mm to my
    right" means something different if the parent itself is rotated — so
    the shift itself must be rotated by parent_rotation_deg before being
    added to anchor_position (which the caller has already resolved to the
    parent's own world-space origin), and the cell's own contents rotate by
    the SUM of parent_rotation_deg + clone.rotation_deg, not clone.rotation_deg
    alone. See ClonePositionCalculator's recursive resolver for the caller.

    The polar mode (radius_mm/angle_deg, an optional alternative to xy — see
    config/models.py) goes through the SAME parent_rotation_deg composition:
    shift = rotate_local_offset(radius_mm, 0.0, angle_deg + parent_rotation_deg).
    This is what keeps nested Cells (Phase 4 recursion) correct with a polar
    shift — skipping the + parent_rotation_deg here would silently misplace
    them, exactly the failure the plan's "КЛЮЧЕВОЕ различие" warns about.
    """
    # getattr for the polar fields: apply_clone_geometry is ALSO called with a
    # nested CellPlacement (Phase 4 recursion — see clone_position_calculator.py),
    # which has xy/rotation_deg but no radius_mm/angle_deg. A nested placement
    # never carries polar offsets, so it must fall through to the Cartesian
    # path unchanged.
    radius_mm = getattr(clone, "radius_mm", None)
    if radius_mm is not None:
        shift = rotate_local_offset(radius_mm, 0.0,
                                    getattr(clone, "angle_deg", 0.0) + parent_rotation_deg)
    else:
        shift = rotate_local_offset(clone.xy[0], clone.xy[1], parent_rotation_deg)
    if anchor_position is not None:
        origin = Vector2.from_xy(anchor_position.x + shift.x, anchor_position.y + shift.y)
    else:
        origin = shift
    rotation_deg = parent_rotation_deg + clone.rotation_deg

    def place(along_mm: float, across_mm: float) -> Vector2:
        p = local_to_absolute(origin, along_mm, across_mm, rotation_deg)
        return _mirror_x(origin, p) if mirror else p

    def comp_angle(angle_deg: float) -> float:
        phi = angle_deg + rotation_deg
        return (180.0 - phi) % 360.0 if mirror else phi

    layout = SpokeLayout(origin=origin)
    layout.vias = [_resolve_clone_via(origin, v, rotation_deg, clone, mirror,
                                      resolved_role_nets) for v in cell.vias]
    layout.tracks = [_resolve_clone_track(origin, t, rotation_deg, clone, cell.layer, mirror,
                                          resolved_role_nets) for t in cell.tracks]

    for slot in cell.components:
        ref = role_to_ref.get(slot.role)
        if ref is None:
            continue
        layout.components.append(ComponentLayout(
            ref=ref,
            role=slot.role,
            position=place(slot.offset_along_mm, slot.offset_across_mm),
            angle_deg=comp_angle(slot.angle_deg),
            vias=[_resolve_clone_via(origin, v, rotation_deg, clone, mirror,
                                     resolved_role_nets) for v in slot.vias],
            slot_layer=slot.layer,
        ))

    return layout