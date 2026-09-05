# kicadstamp/geometry/spoke_layout.py
"""
spoke_layout.py — expands a spoke cell into absolute board coordinates.

Order of application (established in discussion with the user):
  1. Shift (shift_x_mm, shift_y_mm) from the FPGA pad centre to the spoke origin —
     a plain translation, WITHOUT rotation.
  2. Rotation of the resulting origin (and all cell contents) by rotation_deg —
     as a single rigid body.

Both steps are in ordinary KiCad coordinates. The internal cell contents
(along/across) are described once at rotation_deg=0 (the reference board) and
are the same for any spoke using this cell — the rotation at the specific
spoke completely eliminates the need to manually adjust sign offsets for a
particular package.

Uses the SAME rotation formula as the rest of the project
(kicadstamp.domain.geometry.Vector2.rotate(), a faithful port of the kipy
formula, empirically confirmed earlier for the flip convention) — does not
reinvent rotation on its own.

CHANGED (KiCadStamp, generalised vias): previously component‑level vias
("GND via") were computed from the REAL ground pad of the already‑placed
component — requiring live board reading after the move commit. Now vias
(at both spoke and component level) are ALWAYS pure geometry from the spoke
origin, using the same formula as the component position itself. No more
dependency on the live board for vias.
"""
from dataclasses import dataclass, field

from ..domain.geometry import Vector2, Angle

from ..config import ManualSpoke, Cell, TemplateVia, TemplateTrack
from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _
from ..utils.units import MM
from .cell_anchor import cell_mount_offset

_ORIGIN = Vector2.from_xy(0, 0)


def rotate_local_offset(along_mm: float, across_mm: float, rotation_deg: float) -> Vector2:
    """
    Rotates the local vector (along, across) by rotation_deg around (0,0) —
    no translation, just a rotated offset vector in nanometres.
    """
    local_vec = Vector2.from_xy(int(along_mm * MM), int(across_mm * MM))
    return local_vec.rotate(Angle.from_degrees(rotation_deg), _ORIGIN)


def local_to_absolute(origin: Vector2, along_mm: float, across_mm: float, rotation_deg: float) -> Vector2:
    """origin (already after shift) + rotated local offset (along, across)."""
    rotated = rotate_local_offset(along_mm, across_mm, rotation_deg)
    return Vector2.from_xy(origin.x + rotated.x, origin.y + rotated.y)


@dataclass
class ResolvedVia:
    """Fully resolved via — absolute position, net is not None."""
    position: Vector2
    net: str
    drill_mm: float
    diameter_mm: float


def _net_from_role_resolved(role: str, pad: str | None, resolved_role_nets: dict,
                            kind: str, cell_name: str) -> str:
    """Net for a net_from_role-bearing via/track from the pre-resolved dict
    {(role, pad): net}. The calculator resolves every net_from_role BEFORE
    geometry (the "geometry does not touch the live board" boundary — same
    contract as clone_geometry._net_from_resolved on the ClonePlacement path),
    so a missing key is an internal-consistency failure, NOT a data problem:
    falling back to rule_net here would re-plan a role-assigned net (e.g. GND
    on pad 2 of a bypass role) as the chain rail — exactly the Bug 3
    GND-duplication this fixes. Report as fatal rather than silently guessing.
    """
    try:
        return resolved_role_nets[(role, pad)]
    except KeyError:
        raise ValidationError(format_fatal_error(
            _("{kind} with net_from_role not resolved in cell {cell!r}")
            .format(kind=kind, cell=cell_name),
            [_("{kind} has net_from_role={role!r}{pad_txt}, but it was not "
               "resolved before geometry — internal consistency: the caller "
               "must resolve every net_from_role prior to apply_spoke_geometry "
               "(mirror ClonePositionCalculator._resolve_role_nets)")
             .format(kind=kind, role=role,
                     pad_txt=f", pad={pad}" if pad is not None else "")]
        ))


def _resolve_via(origin: Vector2, via: TemplateVia, rotation_deg: float, rule_net: str,
                 ax_mm: float = 0.0, ay_mm: float = 0.0,
                 resolved_role_nets: dict | None = None,
                 cell_name: str = "") -> ResolvedVia:
    """A net_from_role-bearing via takes its net from the pre-resolved
    {(role, pad): net} map; otherwise net=None inherits rule_net (the chain
    rail). Prior to Bug 3 (2026-09-05) net_from_role was ignored here and every
    via was planned as `via.net or rule_net` — so a GND-assigned via of a
    bypass role (net_from_role=ROLE, pad='2') was planned as the chain RAIL,
    the registry stored the rail net, live copper was GND, adopt/pre-check
    honestly refused and a second GND copy was created.
    resolved_role_nets=None keeps the legacy pure-geometry behaviour (net_from_role
    ignored, net or rule_net used); a provided dict makes the lookup STRICT — a
    net_from_role item missing from it is an internal-consistency error."""
    if via.net_from_role is not None and resolved_role_nets is not None:
        net = _net_from_role_resolved(via.net_from_role, via.net_from_role_pad,
                                      resolved_role_nets, "via", cell_name)
    else:
        net = via.net or rule_net
    return ResolvedVia(
        position=local_to_absolute(origin, via.offset_along_mm - ax_mm,
                                   via.offset_across_mm - ay_mm, rotation_deg),
        net=net,
        drill_mm=via.drill_mm,
        diameter_mm=via.diameter_mm,
    )


@dataclass
class ResolvedTrack:
    """Fully resolved straight track segment — both points are absolute, net is not None."""
    start: Vector2
    end: Vector2
    width_mm: float
    net: str
    layer: str  # 'F.Cu' | 'B.Cu', absolute — already resolved (own or cell layer, with mirror considered)


def _resolve_track(origin: Vector2, track: TemplateTrack, rotation_deg: float,
                    rule_net: str, template_layer: str,
                    ax_mm: float = 0.0, ay_mm: float = 0.0,
                    resolved_role_nets: dict | None = None,
                    cell_name: str = "") -> ResolvedTrack:
    """A net_from_role-bearing track takes its net from the pre-resolved
    {(role, pad): net} map; otherwise net=None inherits rule_net — the same
    convention as _resolve_via (see its docstring for the Bug 3 GND-duplication
    rationale and the None-vs-provided-dict strictness contract). ManualSpoke
    does not support mirror (unlike ClonePlacement), so the layer is simply its
    own or the cell layer, without inversion."""
    if track.net_from_role is not None and resolved_role_nets is not None:
        net = _net_from_role_resolved(track.net_from_role, track.net_from_role_pad,
                                      resolved_role_nets, "track", cell_name)
    else:
        net = track.net or rule_net
    return ResolvedTrack(
        start=local_to_absolute(origin, track.start_along_mm - ax_mm,
                                track.start_across_mm - ay_mm, rotation_deg),
        end=local_to_absolute(origin, track.end_along_mm - ax_mm,
                              track.end_across_mm - ay_mm, rotation_deg),
        width_mm=track.width_mm,
        net=net,
        layer=track.layer or template_layer,
    )


@dataclass
class ComponentLayout:
    ref: str
    role: str
    position: Vector2
    angle_deg: float
    vias: list[ResolvedVia] = field(default_factory=list)
    slot_layer: str = None     # absolute slot layer ('F.Cu'/'B.Cu'), None = cell layer


@dataclass
class SpokeLayout:
    origin: Vector2                                  # spoke origin (after shift, before rotation)
    vias: list[ResolvedVia] = field(default_factory=list)     # spoke‑level vias (formerly power_via)
    components: list[ComponentLayout] = field(default_factory=list)
    tracks: list[ResolvedTrack] = field(default_factory=list)  # filled by both ClonePlacement (clone_geometry.py) and ManualSpoke (below)


def apply_spoke_geometry(
    pad_position: Vector2,
    spoke: ManualSpoke,
    cell: Cell,
    rule_net: str,
    role_to_ref: dict[str, str],
    resolved_role_nets: dict | None = None,
) -> SpokeLayout:
    """
    Computes absolute positions of EVERYTHING in the cell for this spoke,
    including vias at both levels — pure geometry, no access to the live board.
    role_to_ref is already resolved EXTERNALLY (see component_pool.py) — this
    function does not decide which ref to assign to which role, only geometry.
    resolved_role_nets — {(role, pad): net} pre-resolved EXTERNALLY for every
    net_from_role-bearing via/track in the cell (Bug 3 spoke-path fix,
    2026-09-05: the caller resolves these live BEFORE geometry — see
    ManualPositionCalculator._resolve_role_nets — preserving the boundary
    above). None/empty = the historical behaviour: net_from_role is ignored
    and every via/track falls back to `net or rule_net`.
    """
    if spoke.radius_mm is not None:
        # Polar mode (config/entries.py guarantees radius_mm AND angle_deg are
        # both set together): origin = pad centre + "radius_mm along the X axis,
        # rotated by angle_deg" — the SAME local_to_absolute primitive every
        # other subsystem uses (see CoordinatePlacement's polar mode), and,
        # like the Cartesian shift, a plain translation from the pad with NO
        # parent_rotation (spokes have no parent frame).
        origin = local_to_absolute(pad_position, spoke.radius_mm, 0.0, spoke.angle_deg)
    else:
        # Cartesian shift (default): a raw translation from the pad centre,
        # without rotation (see module docstring, order of application).
        origin = Vector2.from_xy(
            pad_position.x + int(spoke.shift_x_mm * MM),
            pad_position.y + int(spoke.shift_y_mm * MM),
        )

    # The cell's mount point A (bbox-anchored frame, design_2026_09_05 v2): the
    # stored offsets are ALWAYS in the cell's bbox frame and A is subtracted at
    # placement so A coincides with the spoke origin. Absent anchor -> (0,0).
    ax_mm, ay_mm = cell_mount_offset(cell)
    layout = SpokeLayout(origin=origin)

    layout.vias = [_resolve_via(origin, v, spoke.rotation_deg, rule_net, ax_mm, ay_mm,
                                resolved_role_nets=resolved_role_nets, cell_name=cell.name)
                   for v in cell.vias]
    layout.tracks = [_resolve_track(origin, t, spoke.rotation_deg, rule_net, cell.layer,
                                    ax_mm, ay_mm,
                                    resolved_role_nets=resolved_role_nets, cell_name=cell.name)
                     for t in cell.tracks]

    for slot in cell.components:
        ref = role_to_ref.get(slot.role)
        if ref is None:
            continue
        layout.components.append(ComponentLayout(
            ref=ref,
            role=slot.role,
            position=local_to_absolute(origin, slot.offset_along_mm - ax_mm,
                                       slot.offset_across_mm - ay_mm, spoke.rotation_deg),
            angle_deg=slot.angle_deg + spoke.rotation_deg,
            vias=[_resolve_via(origin, v, spoke.rotation_deg, rule_net, ax_mm, ay_mm,
                               resolved_role_nets=resolved_role_nets, cell_name=cell.name)
                  for v in slot.vias],
            slot_layer=slot.layer,
        ))

    return layout