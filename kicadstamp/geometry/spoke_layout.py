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


def _resolve_via(origin: Vector2, via: TemplateVia, rotation_deg: float, rule_net: str,
                 ax_mm: float = 0.0, ay_mm: float = 0.0) -> ResolvedVia:
    return ResolvedVia(
        position=local_to_absolute(origin, via.offset_along_mm - ax_mm,
                                   via.offset_across_mm - ay_mm, rotation_deg),
        net=via.net or rule_net,
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
                    ax_mm: float = 0.0, ay_mm: float = 0.0) -> ResolvedTrack:
    """net=None inherits rule_net — same convention as _resolve_via (see its docstring).
    ManualSpoke does not support mirror (unlike ClonePlacement), so the layer is
    simply its own or the cell layer, without inversion."""
    return ResolvedTrack(
        start=local_to_absolute(origin, track.start_along_mm - ax_mm,
                                track.start_across_mm - ay_mm, rotation_deg),
        end=local_to_absolute(origin, track.end_along_mm - ax_mm,
                              track.end_across_mm - ay_mm, rotation_deg),
        width_mm=track.width_mm,
        net=track.net or rule_net,
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
) -> SpokeLayout:
    """
    Computes absolute positions of EVERYTHING in the cell for this spoke,
    including vias at both levels — pure geometry, no access to the live board.
    role_to_ref is already resolved EXTERNALLY (see component_pool.py) — this
    function does not decide which ref to assign to which role, only geometry.
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

    layout.vias = [_resolve_via(origin, v, spoke.rotation_deg, rule_net, ax_mm, ay_mm)
                   for v in cell.vias]
    layout.tracks = [_resolve_track(origin, t, spoke.rotation_deg, rule_net, cell.layer,
                                    ax_mm, ay_mm) for t in cell.tracks]

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
            vias=[_resolve_via(origin, v, spoke.rotation_deg, rule_net, ax_mm, ay_mm)
                  for v in slot.vias],
            slot_layer=slot.layer,
        ))

    return layout