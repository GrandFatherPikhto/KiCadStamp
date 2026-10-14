# gui/docks/trees_move_flow.py
"""The PURE half of the group "Move to…" of the trees dock — which nodes a
selection moves, and which parents may take them
(plan_2026_10_14_tree_group_move_and_mount_rebase, Ч1 п.2–п.7).

Why this module exists at all: `gui/docks/trees_dock.py` is a giant (7323 lines
before this plan, and the plan forbids growing it), so every NEW branch of the
group flow lands here instead. What lives here is deliberately the part that
needs no widgets, no adapter and no config: it is a walk over a `Tree`'s nodes,
so it can be read and tested as data.

What does NOT live here, and why (Д3 of the Ч0 review):

  * the pivot-ref RULE (`_pivot_ref_mount_parent`) stays in `trees_dock.py` and is
    handed in as the `pivot_rejects` callable — one expression of the rule, the
    same one the node form's Parent combo uses, and the same one the existing
    guards monkeypatch;
  * the offset arithmetic, the board reads and every widget (the parent picker,
    the messages) stay in the dock, which owns the door signs;
  * the copper kinds set is passed in (`TreesDock._COPPER_ROW_KINDS`), never
    re-listed here — a second copy of "which kinds are rows under the Copper
    fold" is exactly the drift this project refuses.

Ч1 п.3 ("выделены предок и его потомок → переносится только предок") and Ч1 п.7
("перенесённые узлы встают у нового родителя в своём прежнем взаимном порядке
документа") are both answered by ONE function, `group_roots`, whose walk order IS
the document order.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from kicadstamp.i18n import _
from kicadstamp.trees import Tree, TreeNode


def group_roots(tree: Tree, nodes: Sequence[TreeNode]) -> list[TreeNode]:
    """The nodes a selection really moves: every selected node that has NO
    selected ancestor, in DOCUMENT (pre-order) order.

    This is Ч1 п.3 and Ч1 п.7 in one walk. A selected descendant of a selected
    node rides with its ancestor (moving the ancestor moves the whole subtree, so
    moving the descendant again would be a second, contradictory instruction),
    and the returned order is the order the nodes come back in under their new
    parent — the document order the redraw reads as its tie-breaker, NOT the
    order the user happened to click rows in.

    Identity, not equality: `TreeNode` is a dataclass with value equality (and an
    unhashable list field), so ids are used for "is selected", exactly like the
    dock's own `_in_list`. A node that is not in `tree` at all is IGNORED rather
    than raising: the selection comes from the tree's own widget, so a foreign
    node means a bug somewhere else, and dropping it cannot corrupt the tree."""
    selected = {id(node) for node in nodes}
    if not selected:
        return []
    out: list[TreeNode] = []

    def _walk(children: Sequence[TreeNode], inside_selected: bool) -> None:
        for child in children:
            if id(child) in selected:
                if not inside_selected:
                    out.append(child)
                _walk(child.children, True)
            else:
                _walk(child.children, inside_selected)

    _walk(tree.nodes, False)
    return out


def forbidden_subtrees(tree: Tree, roots: Sequence[TreeNode]) -> list[TreeNode]:
    """Every node of `tree` that may NOT become the new parent of `roots`: each
    root itself and its whole subtree, in document order (Ч1 п.4 — "кандидаты в
    родители минус каждый выделенный узел и его поддерево").

    A node cannot be moved into its own subtree: that is a structural invariant
    (`_reparent_node` refuses it too), so the picker must not even offer it."""
    root_ids = {id(node) for node in roots}
    out: list[TreeNode] = []

    def _walk(children: Sequence[TreeNode], inside: bool) -> None:
        for child in children:
            within = inside or id(child) in root_ids
            if within:
                out.append(child)
            _walk(child.children, within)

    _walk(tree.nodes, False)
    return out


def move_candidates(
        tree: Tree, roots: Sequence[TreeNode],
        pivot_rejects: Callable[[TreeNode, Optional[TreeNode]],
                                Optional[TreeNode]],
) -> list[tuple[str, Optional[TreeNode]]]:
    """The parent picker's rows for a group: `("(top level)", None)` first (when
    the rule allows it), then every node a GROUP may be re-hung under, by ref.

    A candidate is offered only when the pivot-ref rule accepts it for EVERY node
    of the group: the rule pins a node's base to a live component, so a parent
    that would pin ONE member of the group would make the next config load a
    fatal (`_validate_tree_pivot_ref`, reason "mount-ancestor"), no matter what
    the other members say. `pivot_rejects(node, candidate)` is that rule —
    `trees_dock._pivot_ref_mount_parent`, handed in by the dock — and it is asked
    per node, never re-expressed here (Ч1 п.4).

    `roots` must be `group_roots`'s answer: forbidden parents are computed from it
    (a selected descendant is already covered by its ancestor's subtree).

    The rule is asked until the FIRST refusal for a candidate (one member being
    pinned settles that candidate, so asking the rest cannot change the answer):
    the walk stays one pass over the tree, times the group. What that gives up is
    naming WHICH member blocked a candidate — nothing in this plan needs that."""
    forbidden = forbidden_subtrees(tree, roots)
    forbidden_ids = {id(node) for node in forbidden}
    rows: list[tuple[str, Optional[TreeNode]]] = []
    if all(pivot_rejects(node, None) is None for node in roots):
        rows.append((_("(top level)"), None))

    def _walk(children: Sequence[TreeNode]) -> None:
        for child in children:
            if id(child) in forbidden_ids:
                continue        # its whole subtree is forbidden with it
            if all(pivot_rejects(node, child) is None for node in roots):
                rows.append((child.ref, child))
            _walk(child.children)

    _walk(tree.nodes)
    return rows


def split_untouchable(nodes: Sequence[TreeNode], copper_kinds
                      ) -> tuple[list[TreeNode], list[TreeNode]]:
    """`(movable, excluded)` for a group that is about to be moved: the nodes a
    "Move to…" may re-hang, and the ones it may not.

    A copper node (`net_trace`, and the legacy `copper` container) is a fold under
    the "Copper" pseudo-node and carries no position of its own — its copper is
    offset from its OWN anchor pad — so it has nothing to re-hang and no place in
    the group order (design §12.1, Ч1 п.5). The caller names the kinds
    (`TreesDock._COPPER_ROW_KINDS`) and reports the excluded ones in the Log: the
    user asked for a group move, so a member silently dropped from it would be a
    lie about what happened."""
    movable: list[TreeNode] = []
    excluded: list[TreeNode] = []
    for node in nodes:
        (excluded if node.kind in copper_kinds else movable).append(node)
    return movable, excluded
