from .executor import BatchExecutor
from .planner import PlacementPlanner
from .commands import MoveCommand, ViaCommand, PlacedComponentInfo

# Public facade of the placement domain (2026-08-28, architecture review
# revisit, plan_2026_08_28_placement_public_facade.md; unblocks Phase 2 of
# plan_2026_08_28_auto_nets_full_automation.md §7.3). Additive only — external
# consumers (GUI docks placer/extract/points, apply_pipeline, net_trace_extract,
# net_trace_planner, tree_position, validation, tools) get a sanctioned door
# into the domain instead of importing placement.services.<module>.<name>
# directly. Existing direct placement.services.* imports are untouched and
# remain valid; no files were moved.
from .services.board_items_resolver import (
    clone_world_origin,
    resolve_clone_board_items,
)
from .services.clone_position_calculator import clone_anchor_id
from .services.clone_role_resolver import (
    clone_uses_selection_mode,
    resolve_roles_by_nets,
    resolve_roles_by_selection,
    resolve_single_role_candidate,
    suggest_role_nets_from_cluster,
)
from .services.point_resolver import resolve_point_chain
from .services.coordinate_position_calculator import _anchor_offset_mm

# Public alias for the one private name already crossing the package boundary
# (tree_position.py imports _anchor_offset_mm) — same pattern config/__init__.py
# uses (load_clone_placement = _load_clone_placement): gui/ and external
# consumers must not touch the private name.
anchor_offset_mm = _anchor_offset_mm

__all__ = [
    "BatchExecutor",
    "PlacementPlanner",
    "MoveCommand",
    "ViaCommand",
    "PlacedComponentInfo",
    "anchor_offset_mm",
    "clone_anchor_id",
    "clone_uses_selection_mode",
    "clone_world_origin",
    "resolve_clone_board_items",
    "resolve_point_chain",
    "resolve_roles_by_nets",
    "resolve_roles_by_selection",
    "resolve_single_role_candidate",
    "suggest_role_nets_from_cluster",
]