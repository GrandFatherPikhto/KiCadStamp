#!/usr/bin/env python3
"""Cells for kicadstamp/tree_plan_warnings.py — the ONE HOST of "does the redraw
take this node's pose from the tree plan" (`plan_pose_subject`) and the two
wordings of the parent warning that depend on it.

Acceptance of 21.10 (plan_2026_10_20_cells_save_keeps_trees_cfg_fresh, Д-3/Д-5):
the rule used to be decided in TWO places (the drift module and
parent_pose_comes_from_plan) that disagreed about an internal mount, the FOREST
planner spelled the old wording out by hand, and neither wording had a cell at
all — both mutations back to the old "current position" survived.

Why a file of its own: tests/trees/test_tree_position.py is 2457 lines, and the
mutation harness runs its guards once per row. The parent-warning cells live
here, next to the module they pin; `curated_redraw_plan` / `..._forest` are
called through their public seams, with real Config/Tree data (no board, no Qt).
"""
from types import SimpleNamespace

from kicadstamp.config import (
    Cell,
    ClonePlacement,
    Config,
    Entity,
    TemplateComponentSlot,
)
from kicadstamp.link_trees import link_trees
from kicadstamp.tree_plan_warnings import plan_pose_subject
from kicadstamp.tree_position import (
    curated_redraw_plan,
    curated_redraw_plan_forest,
)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

# The historical wording, spelled out BY HAND on purpose: a cell that took it
# from the product would move with any mutation of it and could never catch one.
OLD_TEXT = ("Node {ref!r} will be redrawn from the current position of {parent!r} "
            "(not in selection); if {parent!r} moved, {ref!r} will land from the "
            "old point")

# The plan-borne wording's fingerprint: what the old text must NOT say.
PLAN_PHRASE = "'s place in the tree"


# ── builders ────────────────────────────────────────────────────────────────

def _tree_node(ref, kind="placement", children=(), anchor=None):
    return TreeNode(ref=ref, kind=kind, xy=None, polar=None, rotation=0.0,
                    name=None, group=None, children=list(children),
                    anchor=anchor)


def _placement_tree(nodes, name="t"):
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=list(nodes))


def _cell(name, role="R"):
    return Cell(name=name, components=[TemplateComponentSlot(role=role)])


def _entity_cfg():
    """P places C: both are Entities of the SAME tree, so the tree node's kind and
    the record's kind both say "placement" — the host's plan-borne case."""
    cell = _cell("e_cell")
    tree = _placement_tree([
        _tree_node("P", children=[_tree_node("C")]),
    ])
    cfg = Config(cells={"e_cell": cell},
                 entities=[Entity(name="P", cell="e_cell"),
                           Entity(name="C", cell="e_cell")],
                 trees=[tree])
    return cfg, link_trees(cfg, cfg.trees)[0]


def _clone_cfg():
    """A clone parent: its pose is read LIVE, so the old wording is the truth."""
    cell = _cell("cl_cell")
    tree = _placement_tree([
        _tree_node("P", kind="clone", children=[_tree_node("C", kind="clone")]),
    ])
    cfg = Config(cells={"cl_cell": cell},
                 clone_placements=[
                     ClonePlacement(cluster="P", cell="cl_cell", xy=(0.0, 0.0)),
                     ClonePlacement(cluster="C", cell="cl_cell", xy=(1.0, 1.0)),
                 ],
                 trees=[tree])
    return cfg, link_trees(cfg, cfg.trees)[0]


def _mount_cfg():
    """COMP places a cell carrying role FPGA; a mount anchored to that role hangs
    beside it. `resolve_internal_mount_match` classifies that as INTERNAL (the
    role belongs to a cell THIS tree places), so the mount's children are laid
    from the tree pose of COMP.

    Note on reachability (measured while writing the cells): the classifier's
    `_PLACEMENT_KINDS` is ("placement",), so a match's node is ALWAYS a placement
    node and the drift module's "plan pose cannot be read" branch only bites on a
    linked tree whose record disagrees with its node — a defensive case with its
    own cell in test_tree_place_drift.py, not a shape a loaded profile produces."""
    component_cell = _cell("comp_cell", role="FPGA")
    child_cell = _cell("child_cell", role="R_CHILD")
    tree = _placement_tree([
        _tree_node("COMP"),
        _tree_node("m1", kind="mount", anchor=TreeAnchor(role="FPGA"),
                   children=[_tree_node("E_PIF")]),
    ])
    cfg = Config(cells={"comp_cell": component_cell,
                        "child_cell": child_cell},
                 entities=[Entity(name="COMP", cell="comp_cell"),
                           Entity(name="E_PIF", cell="child_cell")],
                 trees=[tree])
    return cfg, link_trees(cfg, cfg.trees)[0]


def _by_ref(linked, ref):
    def walk(nodes):
        for node in nodes:
            if node.node.ref == ref:
                return node
            found = walk(node.children)
            if found is not None:
                return found
        return None
    return walk(linked.nodes)


# ── the ONE host: plan_pose_subject ─────────────────────────────────────────

def test_the_host_answers_a_placement_node_with_its_own_record():
    """kind "placement" AND record kind "placement" — the Entity's "live"
    resolution IS the tree, so the subject is the node itself."""
    cfg, linked = _entity_cfg()
    node = _by_ref(linked, "P")

    assert plan_pose_subject(cfg, node, "t") == ("P", node.record)
    assert node.record.kind == "placement"


def test_the_host_refuses_a_placement_node_whose_record_disagrees():
    """Both halves must say "placement": a disagreement is a bug somewhere else
    and must NOT turn into a warning about a live pose."""
    cfg, linked = _entity_cfg()
    node = _by_ref(linked, "P")
    node.record = SimpleNamespace(kind="clone", name="P")

    assert plan_pose_subject(cfg, node, "t") is None


def test_the_host_answers_a_clone_node_with_nothing():
    """A clone is read LIVE by the redraw: the nodes below it follow it, so there
    is nothing to warn about."""
    cfg, linked = _clone_cfg()

    assert plan_pose_subject(cfg, _by_ref(linked, "P"), "t") is None


def test_the_host_answers_the_placing_component_of_an_internal_mount():
    """The subject of a mount is the node that PLACES the anchored role — never
    the mount node (which owns no record at all). The record is not resolved by
    the host: it only holds the PLAIN tree, and the caller with a forest index
    looks the record up there (that is why it answers None for it)."""
    cfg, linked = _mount_cfg()

    assert plan_pose_subject(cfg, _by_ref(linked, "m1"), "t") == ("COMP", None)


def test_the_host_refuses_a_live_mount():
    """A role OUTSIDE the tree makes the mount LIVE: its base comes from the
    board, so the children follow it."""
    cfg, linked = _mount_cfg()
    mount = _by_ref(linked, "m1")
    mount.node.anchor = TreeAnchor(role="NOT_IN_THIS_TREE")

    assert plan_pose_subject(cfg, mount, "t") is None


def test_the_host_refuses_a_mount_without_a_config():
    """`cfg is None` (a caller with no config in hand) — the mount case then
    answers None, i.e. the historical wording, never a wrong claim."""
    cfg, linked = _mount_cfg()

    assert plan_pose_subject(None, _by_ref(linked, "m1"), "t") is None


def test_the_host_refuses_a_mount_whose_plain_tree_is_unknown():
    """A tree name that cfg does not hold: no classification is possible, and a
    guess would be a wrong warning."""
    cfg, linked = _mount_cfg()

    assert plan_pose_subject(cfg, _by_ref(linked, "m1"), "other-tree") is None


# ── the two wordings through curated_redraw_plan ────────────────────────────

def test_a_placement_parent_is_named_as_the_trees_place():
    """Д-3т1: an Entity parent's pose comes from the tree, so "current position"
    was false — the wording says "place in the tree"."""
    cfg, linked = _entity_cfg()

    names, warnings = curated_redraw_plan(linked, {"C"}, cfg)

    assert names == ["C"]
    assert len(warnings) == 1
    assert PLAN_PHRASE in warnings[0]
    assert "current position" not in warnings[0]


def test_a_mount_parent_is_named_as_the_trees_place():
    """Д-3т2: an INTERNAL mount lays its children from the tree pose of the
    component that places it — the same promise, and the label is the mount."""
    cfg, linked = _mount_cfg()

    names, warnings = curated_redraw_plan(linked, {"E_PIF"}, cfg)

    assert names == ["E_PIF"]
    assert len(warnings) == 1
    assert PLAN_PHRASE in warnings[0] and "'m1'" in warnings[0]
    assert "current position" not in warnings[0]


def test_a_clone_parent_keeps_the_old_wording_byte_for_byte():
    """Д-3т3: a clone IS read from the board, so its wording is the historical
    one — byte for byte, not "almost the same"."""
    cfg, linked = _clone_cfg()

    _names, warnings = curated_redraw_plan(linked, {"C"}, cfg)

    assert warnings == [OLD_TEXT.format(ref="C", parent="P")]


def test_a_mount_without_cfg_keeps_the_old_wording_byte_for_byte():
    """Д-3т4: with no config the mount cannot be classified, and the warning must
    fall back to the historical sentence instead of claiming the tree."""
    cfg, linked = _mount_cfg()

    _names, warnings = curated_redraw_plan(linked, {"E_PIF"})

    assert warnings == [OLD_TEXT.format(ref="E_PIF", parent="m1")]


# ── the same rule in the FOREST planner (Д-5) ───────────────────────────────

def test_the_forest_planner_names_a_placement_parent_as_the_trees_place():
    """Д-5: the forest planner used to spell the old wording out by hand, so a
    forest redraw promised "current position" for a pose that comes from the
    tree. It now asks the same host."""
    cfg, linked = _entity_cfg()

    names, warnings = curated_redraw_plan_forest([linked], {"C"}, cfg=cfg)

    assert names == ["C"]
    assert len(warnings) == 1
    assert PLAN_PHRASE in warnings[0]
    assert "current position" not in warnings[0]


def test_the_forest_planner_keeps_the_old_wording_for_a_clone_parent():
    """The other side of the same gate: a live parent keeps the historical
    sentence byte for byte in the forest planner too."""
    cfg, linked = _clone_cfg()

    _names, warnings = curated_redraw_plan_forest([linked], {"C"}, cfg=cfg)

    assert warnings == [OLD_TEXT.format(ref="C", parent="P")]


def test_the_forest_planner_without_cfg_keeps_the_old_wording_for_a_mount():
    """`cfg` is optional on the forest API: a caller that has none gets the
    historical wording for a mount, never a wrong claim."""
    _cfg, linked = _mount_cfg()

    _names, warnings = curated_redraw_plan_forest([linked], {"E_PIF"})

    assert warnings == [OLD_TEXT.format(ref="E_PIF", parent="m1")]
