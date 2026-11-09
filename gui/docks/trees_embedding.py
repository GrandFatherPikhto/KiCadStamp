# gui/docks/trees_embedding.py
"""Which tree places another one — and the ONE sentence that refuses a redraw of the placed one.

Plan plan_2026_11_09_node_redraw_from_live_parent, §3 (Denis, 09.11): a tree embedded by a
`module` node has no board base of its own. Its content is placed THROUGH that module, in the
OWNING tree's frame, and the tab of the embedded tree knows nothing about the module — so every
redraw started there lays the tree out from its own anchor, i.e. from the Grid Origin. Measured
live (HP-Power-v099, `switch_a` embedded by `main`'s module at xy (32, −35)):

* the tree layout puts `driver_sw_a` at (72.4540, 139.9041) — the Grid Origin point — while the
  module puts it at (104.4540, 104.9041);
* "Redraw whole tree" on the embedded tab moves the whole shoulder by (−32.000, +35.000) mm;
* "Redraw selected" does the same, and NOT only when the top node is selected: a `placement`
  parent's base comes from the TREE (`resolve_entity_live_position`), so even a child-only
  selection re-projects onto the Grid Origin — measured (−32.000, +35.000) mm for
  `conn_out_sw_a` alone (see the report of that commit).

Consequences, all of them REFUSALS with one wording: the top node of such a tree (a single-node
Redraw), "Redraw whole tree" from its tab, and "Redraw selected" on that tab. Nothing is started
and nothing is written — the answer is a log line naming the module and the owning tree.

A REFUSAL IS NOT A FALLBACK: laying the content out from the embedded tree's own anchor is
exactly what moved the shoulder into the corner of the board.

The module also owns the reverse scan itself (`module_nodes`, `embedding_marker`), because three
callers ask it now — the tree render ("embedded in X"), the node redraw and the two tree-level
redraws — and a second copy of "which tree embeds me" is how the tab and the refusal would drift.
"""
from __future__ import annotations

import logging
from typing import Optional

from kicadstamp.i18n import _
from kicadstamp.trees import Tree, TreeNode

from ._common import ERROR_STYLE, show_message

logger = logging.getLogger(__name__)

__all__ = [
    "embedding_marker",
    "module_nodes",
    "refuse_embedded_tree",
    "refusal_line",
]


def module_nodes(tree: Tree) -> list[TreeNode]:
    """Every kind=="module" node of `tree` at ANY depth — the markers that EMBED other trees
    (a module node's ref IS a tree name, plan 2026-09-02 P4)."""
    found: list[TreeNode] = []
    stack = list(tree.nodes)
    while stack:
        node = stack.pop()
        if node.kind == "module":
            found.append(node)
        stack.extend(node.children or [])
    return found


def embedding_marker(trees, tree: Tree) -> Optional[tuple[str, str]]:
    """(owner tree name, marker ref) of the module node that embeds `tree`, or None when nobody
    embeds it. The owner is the FIRST tree in `trees` order that carries such a marker — the
    same reverse scan `TreesDock._embedded_in` uses for its "embedded in X" rows."""
    for owner in trees:
        if owner.name == tree.name:
            continue
        for marker in module_nodes(owner):
            if marker.ref == tree.name:
                return (owner.name, marker.ref)
    return None


def refusal_line(tree_name: str, marker_ref: str, owner_name: str, *,
                 node_ref: Optional[str] = None) -> str:
    """THE sentence that refuses a redraw of a module-placed tree.

    `node_ref` is given for a single-node Redraw ("node 'x'"); None means the whole tree (the
    tree-level redraws). One wording, one place — the tab, the menu, the form and the worker all
    say the same thing, and the reader is sent to the module that actually places the tree."""
    subject = (_("node {node!r}").format(node=node_ref) if node_ref is not None
               else _("the whole tree"))
    return _(
        "Redraw of {subject}: tree {tree!r} is placed by module {marker!r} in tree {owner!r} — "
        "place it through that module, not from this tab.").format(
        subject=subject, tree=tree_name, marker=marker_ref, owner=owner_name)


def refuse_embedded_tree(trees, tree, *, node_ref: Optional[str] = None) -> bool:
    """True — and ONE Log line — when `tree` is placed by a module; the caller then stops.

    THE one place all three redraws make the refusal (a single node, the whole tree, a
    selection), so they cannot word it differently or forget it: `trees_dock` calls this and
    returns. `node_ref` names a single node; None means the whole tree or the selection."""
    marker = embedding_marker(trees, tree)
    if marker is None:
        return False
    owner, marker_ref = marker
    show_message(refusal_line(tree.name, marker_ref, owner, node_ref=node_ref),
                 ERROR_STYLE, logger)
    return True
