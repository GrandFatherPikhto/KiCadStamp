# kicadstamp/placement/commands.py

from dataclasses import dataclass


from ..domain.geometry import BoardLayer
from ..domain.geometry import Vector2, Angle


@dataclass
class MoveCommand:
    ref: str
    position: Vector2
    angle: Angle
    layer: BoardLayer
    # Which placement level resolved this component (clone_position_calculator
    # _resolve_one_level's placement_label) — lets callers (e.g.
    # placer.py::_tag_cluster) tell the top-level placement's OWN components
    # apart from those dragged in via nested CellPlacements. Empty for
    # ManualSpoke/manual moves (they never recurse into nested cells).
    owner_ref: str = ""


@dataclass
class ViaCommand:
    position: Vector2
    drill_mm: float
    diameter_mm: float
    net_name: str
    owner_ref: str
    registry_key: str | None = None  # see registry.py — None means "not participating in the registry"


@dataclass
class TrackCommand:
    start: Vector2
    end: Vector2
    width_mm: float
    net_name: str
    layer: BoardLayer
    owner_ref: str
    registry_key: str | None = None  # see registry.py — None means "not participating in the registry"


@dataclass
class PlacedComponentInfo:
    """
    Information about one placed component — carried over from planner.py
    (via ManualPositionCalculator) to via_planner.py (for keepout and
    skip_existing_components idempotency). Vias are no longer here —
    all vias (spoke-level and component-level) are now pure geometry,
    computed directly as ViaCommand in the same pass as the position
    (see geometry/spoke_layout.py, ComponentPool is consumed once).
    """
    ref: str
    dest: Vector2
    angle_deg: float
    # Layer of THIS component (per-placement side of ClonePlacement).
    # None = inherit the planner's global target_layer.
    layer: BoardLayer | None = None
    # Which placement level resolved this component — see MoveCommand.owner_ref.
    owner_ref: str = ""
