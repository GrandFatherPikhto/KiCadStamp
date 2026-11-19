# gui/docks/trees_embedding.py
"""WHICH tree places another one — one scan, one chain, one sentence about a refusal.

Plan plan_2026_11_09_node_redraw_from_live_parent, §3 (Denis, 09.11): a tree embedded by a
`module` node has no board base of its own. Its content is placed THROUGH that module, in the
OWNING tree's frame, and the tab of the embedded tree knows nothing about the module — so every
redraw started there used to lay the tree out from its own anchor, i.e. from the Grid Origin.
Measured live (HP-Power-v099, `switch_a` embedded by `main`'s module at xy (32, −35)):

* the tree layout puts `driver_sw_a` at (72.4540, 139.9041) — the Grid Origin point — while the
  module puts it at (104.4540, 104.9041);
* "Redraw whole tree" on the embedded tab moves the whole shoulder by (−32.000, +35.000) mm;
* "Redraw selected" does the same, and NOT only when the top node is selected: a `placement`
  parent's base comes from the TREE (`resolve_entity_live_position`), so even a child-only
  selection re-projects onto the Grid Origin — measured (−32.000, +35.000) mm for
  `conn_out_sw_a` alone (see the report of that commit).

A REFUSAL IS NOT A FALLBACK, and since plan_2026_11_15_embedded_tree_redraw_through_module the
tab does not fall back either: it works in the MODULE's coordinates (`placement_chain` names the
markers to activate, `kicadstamp.tree_position.curated_forest_module_content` lays the content
out from the owner's live anchor). What is left to refuse (Denis, 19.11, Р2/Р4):

* a tree placed by TWO OR MORE markers (same or different owners) — which place to take is not
  knowable, so `several_markers_line` names every marker;
* a host whose own base cannot be resolved at all — never "take the tree's own anchor instead",
  that is the defect this module exists for.

This module is the RULE and its WORDING, and nothing else: it stays Qt-free (the AST cell pins
that) so that the answer "who places this tree, through what chain" can be asked from a worker,
a test or a probe without a widget. Performing a refusal — the ONE `show_message` — and starting
the redraw belong to `gui.docks.trees_module_redraw`.

The module also owns the reverse scan itself (`module_nodes`, `placements`, `embedding_marker`,
`placement_chain`), because the tree render ("embedded in X"), the two tree-level redraws, the
node redraw, the anchor form and the position READ all ask it — a second copy of "which tree
embeds me" is how the tab and the refusal would drift.
"""
from __future__ import annotations

import dataclasses
from typing import Optional

from kicadstamp.i18n import _
from kicadstamp.trees import Tree, TreeNode

__all__ = [
    "Placement",
    "chain_selection",
    "embedding_marker",
    "flow_root",
    "module_nodes",
    "placement_chain",
    "placements",
    "several_markers_line",
]


def module_nodes(tree: Tree) -> list[TreeNode]:
    """Every kind=="module" node of `tree` at ANY depth — the markers that EMBED other trees
    (a module node's ref IS a tree name, plan 2026-09-02 P4).

    Depth matters: a marker may hang under another node of the owner tree (its children are
    ordinary stage-1 records of the OWNER, never content of the embedded tree), so the scan
    walks the whole node tree instead of the top level."""
    found: list[TreeNode] = []
    stack = list(tree.nodes)
    while stack:
        node = stack.pop()
        if node.kind == "module":
            found.append(node)
        stack.extend(node.children or [])
    return found


@dataclasses.dataclass(frozen=True)
class Placement:
    """ONE `module` marker that places a tree: `marker` (its ref, i.e. a tree name) sits in
    `owner` and points at `tree`."""

    tree: str
    owner: str
    marker: str

    def where(self) -> str:
        """`'power' in 'main'` — the wording every line uses, so a reader never has to guess
        which of the two names is the host."""
        return "'{marker}' in '{owner}'".format(marker=self.marker, owner=self.owner)


def placements(trees, tree: Tree) -> list[Placement]:
    """EVERY marker of the forest that places `tree`, in `trees` order.

    ALL of them, not the first: the plan's Р2/Р4 keep the refusal for "two or more markers"
    (which place to take is not knowable), and that refusal has to name every one. Callers that
    only need "is it placed at all" ask `embedding_marker`; callers that need to WORK in the
    module's frame (a redraw, a read, the anchor form) take `placement_chain`."""
    found: list[Placement] = []
    for owner in trees or ():
        if owner.name == tree.name:
            continue                     # a self-marker is a config fatal, never a placement
        for marker in module_nodes(owner):
            if marker.ref == tree.name:
                found.append(Placement(tree=tree.name, owner=owner.name, marker=marker.ref))
    return found


def embedding_marker(trees, tree: Tree) -> Optional[tuple[str, str]]:
    """(owner tree name, marker ref) of the module node that embeds `tree`, or None when nobody
    embeds it. The owner is the FIRST tree in `trees` order — the same reverse scan
    `TreesDock._embedded_in` uses for its "embedded in X" rows, and the truth the tab's
    "embedded in" line and the read tooltip need (they name ONE place, not the whole chain)."""
    found = placements(trees, tree)
    if not found:
        return None
    return (found[0].owner, found[0].marker)


def placement_chain(trees, tree: Tree) -> list[Placement]:
    """The markers to activate so that `tree` is laid out where its host really puts it —
    INNERMOST first, the free (nobody-placed) owner last.

    One entry per level: the marker of `tree` in its owner, then the marker of that owner in
    ITS owner, and so on until a tree nobody embeds (`flow_root`). Empty when nobody embeds
    `tree` — its own anchor is the truth then.

    `kicadstamp.tree_position.curated_forest_module_content` lays a flow root's whole reachable
    content in ONE pass, but it only knows an owner is itself placed when THAT owner's marker is
    in `selected_refs` too: activating one level alone would take the host's own anchor (the
    Ч0 п.3 measurement on synthetic `outer → mid → inner`: `selected_refs={"inner"}` yields
    `flow_roots=['mid']`, i.e. the wrong base). Activating the chain and applying only the names
    of the tree in question is the plan's Р2.

    A marker cycle can not exist in a loaded config (`link_trees` rejects it), but a dock BUFFER
    mid-edit may hold one; the walk therefore stops on a revisit instead of looping, and the
    caller gets a short chain — its flow root is then whatever the walk reached, which is the
    honest "we cannot do better" answer rather than a hang."""
    chain: list[Placement] = []
    seen: set[str] = {tree.name}
    current = tree
    while True:
        found = placements(trees, current)
        if not found:
            return chain
        step = found[0]
        if step.owner in seen:
            return chain                 # buffer-only cycle: stop, do not loop
        chain.append(step)
        seen.add(step.owner)
        owner = next((t for t in (trees or ()) if t.name == step.owner), None)
        if owner is None:
            return chain
        current = owner


def flow_root(trees, tree: Tree) -> Optional[str]:
    """The tree whose LIVE anchor the content of `tree` has to be laid from: the last owner of
    `placement_chain`, or None when `tree` is placed by nobody (it is its own root)."""
    chain = placement_chain(trees, tree)
    return chain[-1].owner if chain else None


def chain_selection(trees, tree: Tree) -> set[str]:
    """`selected_refs` that activates the whole chain — the marker names only.

    Handed to `run_curated_forest_redraw`: the planner turns every marker into its content, so
    the stage-2 layout sees the same frames the host's own Redraw would. What actually gets
    APPLIED is narrowed separately (`only_names`), because the content of the host trees is
    computed, not moved."""
    return {step.marker for step in placement_chain(trees, tree)}


def several_markers_line(tree_name: str, found: list[Placement]) -> str:
    """The refusal for a tree placed by TWO OR MORE markers (plan п. 5, Р2): names every one.

    Which place to take is genuinely unknown — the two owners may themselves sit in different
    frames — so this is not a wording of "pick the first", it is the answer "remove the extra
    markers": the config stays legal, only this tab's redraw stops."""
    return _(
        "Tree {tree!r} is placed by more than one module ({where}) — which place to take is not "
        "clear, so nothing is redrawn from this tab. Remove the extra module markers and try "
        "again.").format(
        tree=tree_name,
        where=", ".join(step.where() for step in found))


