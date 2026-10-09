# gui/docks/cell_tree_wiring.py
"""The Config tree's own wiring, lifted out of the giant dock_hub.py (rule 45:
giants only shrink) — the hub keeps one import and two calls.

Two things live here:

  * the SNAPSHOT REFRESHER injection (S.3.2,
    plan_2026_09_11_stale_snapshot_role_lists.md): the tree dock's dialogs read
    their Role/Cluster candidates lazily from ``connection.snapshot``, so it gets
    the SAME "rebuild the snapshot, then distribute it" operation the other docks
    receive through ``push_snapshot`` — injected instead of reached for through
    ``main_window._dock_hub`` (see ``TreesDock.set_snapshot_refresher``);
  * the two cell actions of plan_2026_10_09_cells_and_entities — the batch
    "Add entities…" and "Change cell…" — connected STRAIGHT to their flows (one
    line each), so neither needs a delegate method in the hub.
"""
from functools import partial


def inject_snapshot_refresher(hub) -> None:
    """Hand the tree dock the ONE "rebuild the board snapshot, then distribute"
    operation (DockHub.refresh_snapshot_and_push)."""
    hub.trees_dock.set_snapshot_refresher(hub.refresh_snapshot_and_push)


def connect_cell_tree_actions(hub) -> None:
    """Connect the Config tree's cell actions to their flows.

    The flows are imported HERE (lazily) so this module stays a thin wiring leaf
    and adds no import edge to the hub's own import time.
    """
    from .add_entities_flow import add_entities_from_tree
    from .change_cell_flow import change_cell_for_entity
    hub.config_tree_dock.add_entities_requested.connect(
        partial(add_entities_from_tree, hub))
    hub.config_tree_dock.change_cell_requested.connect(
        partial(change_cell_for_entity, hub))
