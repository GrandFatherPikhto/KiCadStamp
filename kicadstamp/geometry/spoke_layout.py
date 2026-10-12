# kicadstamp/geometry/spoke_layout.py
"""
spoke_layout.py — local-frame geometry primitives (along/across -> absolute)
shared by the clone/tree expansion paths. The spoke-specific
apply_spoke_geometry was removed with the spoke mechanism
(plan_2026_10_08_remove_spokes), and its now-unreachable legacy via/track
resolvers (which inherited the removed rule_net) went with it; what remains
are the primitives the clone/geometry layer builds on.

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

from ..utils.units import MM

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


@dataclass
class ResolvedTrack:
    """Fully resolved straight track segment — both points are absolute, net is not None."""
    start: Vector2
    end: Vector2
    width_mm: float
    net: str
    layer: str  # 'F.Cu' | 'In1.Cu'..'In30.Cu' | 'B.Cu', absolute — already resolved (own or cell layer, with mirror considered: outer pair swapped, inner kept)


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
    tracks: list[ResolvedTrack] = field(default_factory=list)  # filled by ClonePlacement (clone_geometry.py)

