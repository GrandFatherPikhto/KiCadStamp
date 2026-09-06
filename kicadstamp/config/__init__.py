# kicadstamp/config/__init__.py
"""
config/__init__.py — re-export of models.py + loader.py. The public interface
of the package has NOT CHANGED with this refactoring: any existing
`from kicadstamp.config import Config` / `from ...config import ClonePlacement`
etc. throughout the rest of the project continues to work exactly as before — prior
to the refactoring kicadstamp/config.py was a module, now kicadstamp/config/ is
a package with the same set of names at the top level.
"""
from .models import (
    ThermalViaArrayConfig,
    TemplateVia,
    TemplateComponentSlot,
    TemplateTrack,
    Cell,
    CellPlacement,
    ManualSpoke,
    Chain,
    ClonePlacement,
    CoordinatePlacement,
    NetTrace,
    Entity,
    SchemeListConfig,
    TreeInstance,
    Config,
    chain_effective_name,
    thermal_via_array_effective_name,
    coordinate_placement_effective_name,
    clone_placement_effective_name,
    net_trace_effective_name,
    entity_effective_name,
    scheme_list_effective_name,
)
from .points import Point
from ..runtime_context import RuntimeContext
from .loader import (
    load_config,
    _load_template_via,
    _load_template_track,
    _load_template_component_slot,
    _load_cell,
    _load_cell_placement,
    _load_point,
    _load_manual_spoke,
    _load_chain,
    _load_clone_placement,
    _load_thermal_via_array,
    _load_coordinate_placement,
    _load_net_trace,
    _load_entity,
    _load_scheme_list,
    _load_tree,
    _load_tree_instance,
    _check_layer_value,
)

# Backward-compat aliases for the 2026-09-01 Rule -> Chain rename — any
# existing external importer/script referencing the old names keeps working.
Rule = Chain
rule_effective_name = chain_effective_name
load_rule = _load_chain

# load_cell/load_template_component_slot/load_template_via/
# load_template_track/load_cell_placement (2026-08-06) — the cell editor's
# own validators, same "gui/ must not import the private names" reasoning
# as load_clone_placement/load_rule/load_point above.
load_cell = _load_cell
load_template_component_slot = _load_template_component_slot
load_template_via = _load_template_via
load_template_track = _load_template_track
load_cell_placement = _load_cell_placement
# load_net_trace (2026-08-21) — same pattern, for a future net_traces GUI dock.
load_net_trace = _load_net_trace
# load_entity (2026-08-30, Entity/Placement split) — same pattern, for the
# future Entities dock / PlacerDock Source tab.
load_entity = _load_entity
# load_scheme_list (2026-09-06, scheme_list) — same pattern, for the future
# Scheme List form's single-record validation/rebuild.
load_scheme_list = _load_scheme_list
# load_tree (2026-08-27, trees-in-config) — same pattern, for the TreesDock's
# Save round-trip validation of a single tree against the root config.
load_tree = _load_tree
# load_tree_instance (2026-09-02, tree_instances) — same pattern, for the
# P3 Tools "Instances..." dialog's single-entry validation/rebuild.
load_tree_instance = _load_tree_instance

# Public aliases for the loader entry points the GUI uses to validate/rebuild
# a single entry (Phase 4.2 — gui/ must not import the private names).
load_clone_placement = _load_clone_placement
load_thermal_via_array = _load_thermal_via_array
load_coordinate_placement = _load_coordinate_placement
# load_point takes (name, data) — points: is a dict section (keyed by name),
# unlike the list-of-dicts thermal_via_arrays/clone_placements above, whose
# own dict already carries its name inline (see _load_point's signature).
load_point = _load_point
# load_chain/load_manual_spoke — Chain's own extracted validator
# (2026-08-05, see loader.py's _load_chain docstring) + the pre-existing
# per-spoke one, both needed by gui/docks/chain.py to validate a Chain and
# its individual spokes the same clean way Save/Redraw validate everything
# else here.
load_chain = _load_chain
load_manual_spoke = _load_manual_spoke

__all__ = [
    "ThermalViaArrayConfig",
    "TemplateVia",
    "TemplateComponentSlot",
    "TemplateTrack",
    "Cell",
    "CellPlacement",
    "Point",
    "ManualSpoke",
    "Chain",
    "ClonePlacement",
    "CoordinatePlacement",
    "NetTrace",
    "Entity",
    "SchemeListConfig",
    "TreeInstance",
    "Config",
    "RuntimeContext",
    "load_config",
    "load_cell",
    "load_template_component_slot",
    "load_template_via",
    "load_template_track",
    "load_cell_placement",
    "load_clone_placement",
    "load_thermal_via_array",
    "load_coordinate_placement",
    "load_net_trace",
    "load_entity",
    "load_scheme_list",
    "load_tree",
    "load_tree_instance",
    "load_point",
    "load_chain",
    "load_manual_spoke",
    "entity_effective_name",
    "scheme_list_effective_name",
    "chain_effective_name",
    "thermal_via_array_effective_name",
    "coordinate_placement_effective_name",
    "clone_placement_effective_name",
    "net_trace_effective_name",
]