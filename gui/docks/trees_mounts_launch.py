# gui/docks/trees_mounts_launch.py
"""Т6 of plan_2026_11_03_add_mounts_table — the ONE entry point of «Add mounts…».

TWO context-menu items open the SAME window, which is why this module exists:

    «Add mounts…» on a tree's ROOT row   -> `node=None`  -> mounts into the tree
    «Add mounts…» on a NODE              -> its children (Д1 of the plan)

Both call :func:`open_mounts_for`, so there is ONE place that decides the parent,
ONE place that prefills the address (Д1а) and ONE place that builds the window —
and :func:`add_mounts_item` is the ONE place that decides whether the item may be
offered at all (a copper container may hold only `net_trace`, so mounts there would
be refused on OK after the user had already ticked pads).

THE ADDRESS A NODE ALREADY KNOWS (Д1а, corrected in Т6.1). TWO spellings, because a
tree node carries its address in two different ways:

  * `kind` "mount" / "component" — the node has an ANCHOR, and a ROLE anchor names an
    instance directly: `(anchor (role "FPGA") (sheet "FPGA") (cluster "FPGA"))`;
  * `kind` "placement" — a CELL RECORD node ("FPGA/FPGA/FPGA" in the plan's own
    words) has NO anchor: the tree grammar accepts a nested `(anchor …)` on mount and
    component nodes only. Its address is the RECORD's, so the ref is looked up in the
    loaded config (`entities`, then `clone_placements`, through the ONE owner
    `selection_narrowing.record_by_name`) and `cluster` / `sheet` are read off it. The
    CELL is `record.cell` — never the record's own name, which coincides with a cell
    name only by accident.

The Role CANDIDATES are that cell's own roles, in the cell's own order, through the ONE
existing owner (`instance_candidates.cell_role_order`, the function the CELL and ENTITY
pages read); multiplicity is dropped for the picker only — `cell_role_order` repeats a
role once per slot, which is what slot matching needs and what a list of suggestions
does not. A cell with EXACTLY one role also fills the Role field: there is nothing to
choose.

THE WINDOW IS NOT KEPT. `open_mounts_dialog` sets `WA_DeleteOnClose` and the window
looks its tree up by NAME at OK time (§23), so a config reload between the click and
OK is harmless — nothing here has to outlive the click.
"""
from __future__ import annotations

from typing import Optional

from kicadstamp.i18n import _
from kicadstamp.selection_narrowing import record_by_name
from kicadstamp.trees import Tree, TreeNode

from .instance_candidates import cell_role_order
from .trees_mounts_dialog import open_mounts_dialog

__all__ = ["add_mounts_item", "cell_role_hints", "open_mounts_for", "prefill_address",
           "record_of"]


def record_of(dock, node: Optional[TreeNode]):
    """The config record a `placement` node names, or None.

    A node of any other kind places no record (a mount hangs off a role, a module
    points at another tree), so it has no record address at all."""
    name = getattr(node, "ref", None)
    if not name or getattr(node, "kind", None) != "placement":
        return None
    return record_by_name(getattr(dock, "_cfg", None), name)


def _roles_of(dock, record) -> list[str]:
    """The roles of the CELL `record` places, in the cell's order, each once."""
    cell_name = getattr(record, "cell", None)
    cfg = getattr(dock, "_cfg", None)
    cell = (getattr(cfg, "cells", None) or {}).get(cell_name) if cell_name else None
    if cell is None:
        return []
    roles = cell_role_order(getattr(cell, "components", None) or ())
    return list(dict.fromkeys(roles))


def cell_role_hints(dock, node: Optional[TreeNode]) -> list[str]:
    """Role suggestions for the click that came from `node` — the roles of the cell
    its record places (empty when the node names no record or the cell is unknown)."""
    record = record_of(dock, node)
    return _roles_of(dock, record) if record is not None else []


def prefill_address(dock, node: Optional[TreeNode]) -> dict:
    """The address `node` itself carries, as `open_mounts_dialog` keyword args.

    A ROLE anchor names an instance directly (Д2: a mount's anchor is always a role
    anchor); a `placement` node carries no anchor, so its RECORD supplies cluster and
    sheet, and the record's cell supplies the roles. Any other node — or a record the
    config does not carry — prefills nothing: the user types the address, exactly as
    without a click on a node. No refusal here; an empty address is not an error."""
    anchor = getattr(node, "anchor", None)
    if getattr(anchor, "mode", None) == "role":
        return {"role": anchor.role or None,
                "cluster": anchor.anchor_cluster or None,
                "sheet": anchor.anchor_sheet or None}
    record = record_of(dock, node)
    if record is None:
        return {}
    roles = _roles_of(dock, record)
    return {"role": roles[0] if len(roles) == 1 else None,
            "cluster": getattr(record, "cluster", None) or None,
            "sheet": getattr(record, "sheet", None) or None}


def add_mounts_item(menu, dock, tree: Tree, node: Optional[TreeNode] = None):
    """Append «Add mounts…» to `menu` and return the action — or None where the item
    would lie.

    ONE refusal: a `kind` "copper" CONTAINER may hold only `net_trace` children (the
    tree loader fatals on anything else), so mounts under it are refused on OK after
    the user has already ticked pads. Same idiom as `delete_action_text`: the caller
    writes `add_mounts_item(menu, self, tree, node)` and the dock stays one line."""
    if getattr(node, "kind", None) == "copper":
        return None
    action = menu.addAction(_("Add mounts…"))
    action.triggered.connect(lambda: open_mounts_for(dock, tree, node))
    return action


def open_mounts_for(dock, tree: Tree, node: Optional[TreeNode] = None) -> None:
    """Open the mounts table for `tree`, under `node` (None = the tree's root).

    The window is built on the dock's OWN state — its connection, its loaded cfg and
    the sheet names its context already resolved — so the click costs no board read
    and no config re-read. No connection is NOT refused here: the window itself says
    «the mounts table cannot be read: there is no board connection» on «Read pads»,
    in the window AND in the Log, which is where the user is looking."""
    address = prefill_address(dock, node)
    open_mounts_dialog(
        dock, dock=dock,
        connection=getattr(getattr(dock, "_main_window", None), "connection", None),
        cfg=getattr(dock, "_cfg", None),
        sheet_names=getattr(getattr(dock, "_ctx", None), "sheet_names", None) or {},
        tree_name=tree.name,
        parent_ref=getattr(node, "ref", None),
        role=address.get("role"), cluster=address.get("cluster"),
        sheet=address.get("sheet"), role_choices=cell_role_hints(dock, node))
