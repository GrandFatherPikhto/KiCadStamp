# tests/test_sibling_order.py
"""Sibling reordering of tree nodes (plan_2026_09_17_order_pass_and_component_node,
Э4, guard С11) and what it means for the redraw order (Э1's tie-breaker).

Reordering is a STRUCTURAL edit and nothing else: it moves one node up or down
among the list it already hangs in (its parent's children, or the tree's own
top-level list). The guards pin all three promises of Т4.2/Т4.3:

  * the tree's structure changes in exactly one way (the list order) — `xy`,
    `polar`, `rotation` and the parent of every node are untouched;
  * after Э1 that order IS the tie-breaker among INDEPENDENT nodes, so the plan
    follows it (moving a node up moves it earlier in the run);
  * a DEPENDENT node is never reordered by it — a node whose base is another
    node still applies after that node in either list order (Р4: sibling order
    is not a way to express a dependency).
"""
import logging
from unittest.mock import MagicMock

import pytest

from kicadstamp.config import ClonePlacement, Config
from kicadstamp.link_trees import link_trees
from kicadstamp.trees import Tree, TreeAnchor, TreeNode, tree_from_dict
from kicadstamp.tree_position import curated_redraw_plan_forest

from gui.docks.trees_dock import TreesDock


def _dock() -> TreesDock:
    """A dock with just enough wiring for the structural helpers: they touch
    `_mark_dirty`, `_find_parent`/`_in_list` (pure) and `_rebuild_tabs` (which
    we replace below), never the board."""
    dock = TreesDock.__new__(TreesDock)
    dock._mark_dirty = MagicMock()
    dock._rebuild_tabs = MagicMock()
    return dock


def _tree():
    """t
       ├── A (xy 1,1, rotation 10)
       │    └── X
       ├── B (xy 2,2, rotation 20)
       └── C (xy 3,3, rotation 30)
    """
    return tree_from_dict({
        "name": "t", "anchor": {"origin": True}, "nodes": [
            {"ref": "A", "kind": "clone", "xy": [1.0, 1.0], "rotation": 10.0,
             "children": [{"ref": "X", "kind": "clone", "xy": [0.5, 0.0]}]},
            {"ref": "B", "kind": "clone", "xy": [2.0, 2.0], "rotation": 20.0},
            {"ref": "C", "kind": "clone", "xy": [3.0, 3.0], "rotation": 30.0},
        ]})


def _refs(tree):
    return [n.ref for n in tree.nodes]


def _poses(tree):
    out = {}

    def walk(nodes):
        for n in nodes:
            out[n.ref] = (n.xy, n.polar, n.rotation)
            walk(n.children)

    walk(tree.nodes)
    return out


# ── С11: the change is structural only ───────────────────────────────────────

def test_moving_a_top_level_node_down_swaps_it_with_the_next_sibling():
    dock, tree = _dock(), _tree()
    assert dock._move_node_within_siblings(tree, tree.nodes[0], +1,
                                           defer_rebuild=False) is True
    assert _refs(tree) == ["B", "A", "C"]
    assert dock._mark_dirty.called and dock._rebuild_tabs.called


def test_moving_a_node_up_puts_it_before_its_previous_sibling():
    dock, tree = _dock(), _tree()
    assert dock._move_node_within_siblings(tree, tree.nodes[2], -1,
                                           defer_rebuild=False) is True
    assert _refs(tree) == ["A", "C", "B"]


def test_reordering_changes_nothing_but_the_order():
    """Т4.2: coordinates, polar, rotation, the parent link and the children of
    EVERY node are byte-for-byte what they were."""
    dock, tree = _dock(), _tree()
    before_poses = _poses(tree)
    before_children = {n.ref: [c.ref for c in n.children] for n in tree.nodes}
    dock._move_node_within_siblings(tree, tree.nodes[1], -1, defer_rebuild=False)
    assert _refs(tree) == ["B", "A", "C"]
    assert _poses(tree) == before_poses
    assert {n.ref: [c.ref for c in n.children] for n in tree.nodes} == before_children
    # The nested child is still nested, with its own pose.
    assert [n.ref for n in tree.nodes[1].children] == ["X"]


def test_a_nested_node_is_reordered_among_its_own_siblings_only():
    """A node inside a parent is reordered inside THAT list — its parent does
    not change (Т4.1: the structural discipline is one removal point, one
    rebuild point, and the node never leaves its parent)."""
    dock = _dock()
    tree = tree_from_dict({
        "name": "t", "anchor": {"origin": True}, "nodes": [
            {"ref": "P", "kind": "clone", "xy": [0.0, 0.0], "children": [
                {"ref": "c1", "kind": "clone", "xy": [1.0, 0.0]},
                {"ref": "c2", "kind": "clone", "xy": [2.0, 0.0]}]}]})
    parent = tree.nodes[0]
    assert dock._move_node_within_siblings(tree, parent.children[1], -1,
                                           defer_rebuild=False) is True
    assert [c.ref for c in parent.children] == ["c2", "c1"]
    assert _refs(tree) == ["P"]


def test_the_ends_refuse_and_the_menu_predicate_agrees():
    """С11: there is no sibling to swap with at either end — the operation
    refuses (and changes nothing) and `_has_movable_sibling` says the same, so
    the disabled menu item and the refusal cannot disagree."""
    dock, tree = _dock(), _tree()
    first, last = tree.nodes[0], tree.nodes[-1]
    assert dock._has_movable_sibling(tree, first, -1) is False
    assert dock._has_movable_sibling(tree, first, +1) is True
    assert dock._has_movable_sibling(tree, last, +1) is False
    assert dock._move_node_within_siblings(tree, first, -1) is False
    assert dock._move_node_within_siblings(tree, last, +1) is False
    assert _refs(tree) == ["A", "B", "C"]
    assert not dock._mark_dirty.called


def test_a_node_that_is_not_in_the_tree_is_refused(caplog):
    """The helpers are identity-based and honest: a node of ANOTHER tree (or a
    detached one) is refused with a Log line, never silently appended."""
    dock = _dock()
    tree = _tree()
    stranger = _tree()
    stranger_node = stranger.nodes[0]
    with caplog.at_level(logging.WARNING, logger="gui.docks.trees_dock"):
        assert dock._move_node_within_siblings(tree, stranger_node, +1) is False
    assert "not a node of this tree" in caplog.text
    assert _refs(tree) == ["A", "B", "C"]


def test_the_reorder_uses_the_same_removal_point_as_the_rehang():
    """Т4.1: `_detach_node` is THE one removal point — the re-hang and the
    reorder both go through it, so a structural edit cannot lose a node through
    a value-equality `list.remove()` (TreeNode is a dataclass WITH equality:
    two equal-but-distinct siblings exist in real trees)."""
    dock = _dock()
    # Built BY HAND: the parser refuses two nodes with one ref (rule 2), yet
    # TreeNode's VALUE equality makes such a pair exactly what the identity-based
    # removal protects against — `list.remove(second)` would drop `first`.
    def equal_node():
        return TreeNode(ref="same", kind="clone", xy=(1.0, 1.0), polar=None,
                        rotation=0.0, name=None, group=None, children=[])

    tree = Tree(name="t", anchor=TreeAnchor(is_origin=True),
                nodes=[equal_node(), equal_node()])
    first, second = tree.nodes[0], tree.nodes[1]
    assert first == second and first is not second
    siblings, index = dock._detach_node(tree, second)
    assert index == 1 and len(siblings) == 1
    assert siblings[0] is first          # the EQUAL node survived
    assert tree.nodes == [first]


# ── С11/Т4.3: what the reorder means for the redraw ─────────────────────────

def _independent_cfg():
    return Config(
        clone_placements=[ClonePlacement(cluster=n, cell="c", xy=(0.0, 0.0))
                          for n in ("z_first", "a_second")],
        trees=[tree_from_dict({
            "name": "t", "anchor": {"origin": True}, "nodes": [
                {"ref": "z_first", "kind": "clone", "xy": [1.0, 1.0]},
                {"ref": "a_second", "kind": "clone", "xy": [2.0, 2.0]},
            ]})])


def test_the_plan_follows_the_sibling_order():
    """Т4.3/С2 through the REAL planner: two independent nodes are applied in the
    list order, and moving one up changes the run's order — the alphabet does
    not decide (the refs were chosen so it would say the opposite)."""
    cfg = _independent_cfg()
    linked = link_trees(cfg, cfg.trees)
    names, _w = curated_redraw_plan_forest(linked, {"z_first", "a_second"})
    assert names == ["z_first", "a_second"]

    dock = _dock()
    tree = cfg.trees[0]
    assert dock._move_node_within_siblings(tree, tree.nodes[1], -1,
                                           defer_rebuild=False) is True
    linked = link_trees(cfg, cfg.trees)
    names, _w = curated_redraw_plan_forest(linked, {"z_first", "a_second"})
    assert names == ["a_second", "z_first"]


def test_a_dependent_node_is_not_reordered_by_the_sibling_order():
    """С11/Р4: a node ANCHORED on another node applies after it in either list
    order — reordering siblings must never be able to break a dependency."""
    cfg = Config(
        clone_placements=[ClonePlacement(cluster=n, cell="c", xy=(0.0, 0.0))
                          for n in ("D0", "E")],
        trees=[tree_from_dict({
            "name": "host", "anchor": {"origin": True},
            "nodes": [{"ref": "D0", "kind": "clone", "xy": [0.0, 0.0]}]}),
            tree_from_dict({
                "name": "dep", "anchor": {"ref": "D0"},
                "nodes": [{"ref": "E", "kind": "clone", "xy": [1.0, 0.0]}]})])
    linked = link_trees(cfg, cfg.trees)
    names, _w = curated_redraw_plan_forest(linked, {"D0", "E"})
    assert names == ["D0", "E"]

    # Put the dependent tree FIRST in the forest: the anchor edge still decides.
    cfg.trees.reverse()
    linked = link_trees(cfg, cfg.trees)
    names, _w = curated_redraw_plan_forest(linked, {"D0", "E"})
    assert names == ["D0", "E"]
