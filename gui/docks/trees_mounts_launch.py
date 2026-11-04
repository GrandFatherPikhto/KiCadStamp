# gui/docks/trees_mounts_launch.py
"""Т6 of plan_2026_11_03_add_mounts_table — the ONE entry point of «Add mounts…».

TWO context-menu items open the SAME window, which is why this module exists:

    «Add mounts…» on a tree's ROOT row   -> `node=None`  -> mounts into the tree
    «Add mounts…» on a NODE              -> its children (Д1 of the plan)

Both call :func:`open_mounts_for`, so there is ONE place that decides the parent,
ONE place that prefills the address (Д1а) and ONE place that builds the window. The
dock itself only adds the two menu lines (rule 45: `trees_dock.py` does not grow).

THE ADDRESS A NODE ALREADY KNOWS (Д1а). A node that places a cell record carries the
record's own address in its anchor — `(anchor (role "FPGA") (sheet "FPGA")
(cluster "FPGA"))` — so Role, Sheet and Cluster come straight from it. The Role
CANDIDATES are the cell's own roles, in the cell's own order, through the ONE
existing owner of that question (`instance_candidates.cell_role_order`, the same
function the CELL and ENTITY pages read); this module never re-derives them.
Multiplicity is dropped for the picker only: `cell_role_order` repeats a role once
per slot (that is what slot matching needs), while a list of suggestions that says
"FPGA" thirty times helps nobody.

THE WINDOW IS NOT KEPT. `open_mounts_dialog` sets `WA_DeleteOnClose` and the window
looks its tree up by NAME at OK time (§23), so a config reload between the click and
OK is harmless — nothing here has to outlive the click.

Both items are absent where they would lie: a `net_trace` node (its copper lives in a
record, it has no position of its own to hang mounts from) and a read-only generated
instance (its geometry is owned by the template) never reach this module — their
menus return earlier in `trees_dock._on_context_menu`.
"""
from __future__ import annotations

from typing import Optional

from kicadstamp.trees import Tree, TreeNode

from .instance_candidates import cell_role_order
from .trees_mounts_dialog import open_mounts_dialog

__all__ = ["cell_role_hints", "open_mounts_for", "prefill_address"]


def prefill_address(node: Optional[TreeNode]) -> dict:
    """The address `node` itself carries, as `open_mounts_dialog` keyword args.

    Only a ROLE anchor names an instance (Д2: a mount's anchor is always a role
    anchor), so any other anchor — a record ref, a point, the origin, the self mode —
    prefills nothing: the user types the address, exactly as without a click on a
    node."""
    anchor = getattr(node, "anchor", None)
    if getattr(anchor, "mode", None) != "role":
        return {}
    return {"role": anchor.role or None,
            "cluster": anchor.anchor_cluster or None,
            "sheet": anchor.anchor_sheet or None}


def cell_role_hints(dock, node: Optional[TreeNode]) -> list[str]:
    """The roles of the cell `node` names, first use each, cell order — suggestions
    only (the Role field stays free text)."""
    name = getattr(node, "ref", None)
    cfg = getattr(dock, "_cfg", None)
    cell = (getattr(cfg, "cells", None) or {}).get(name) if name else None
    if cell is None:
        return []
    roles = cell_role_order(getattr(cell, "components", None) or ())
    return list(dict.fromkeys(roles))


def open_mounts_for(dock, tree: Tree, node: Optional[TreeNode] = None) -> None:
    """Open the mounts table for `tree`, under `node` (None = the tree's root).

    The window is built on the dock's OWN state — its connection, its loaded cfg and
    the sheet names its context already resolved — so the click costs no board read
    and no config re-read. No connection is NOT refused here: the window itself says
    «the mounts table cannot be read: there is no board connection» on «Read pads»,
    in the window AND in the Log, which is where the user is looking."""
    address = prefill_address(node)
    open_mounts_dialog(
        dock, dock=dock,
        connection=getattr(getattr(dock, "_main_window", None), "connection", None),
        cfg=getattr(dock, "_cfg", None),
        sheet_names=getattr(getattr(dock, "_ctx", None), "sheet_names", None) or {},
        tree_name=tree.name,
        parent_ref=getattr(node, "ref", None),
        role=address.get("role"), cluster=address.get("cluster"),
        sheet=address.get("sheet"), role_choices=cell_role_hints(dock, node))
