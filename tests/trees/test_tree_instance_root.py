#!/usr/bin/env python3
"""Н6 of plan_2026_11_07_tree_instances_dialog_and_subtree: an instance from a
NODE, not from the whole tree.

    (tree_instance
      (template "switch_a")
      (root (ref "driver_sw_a" (uuid "…")))   ; this node and everything under it
      (name "switch_b")
      (sheet "SW B")
      (anchor (point "Origin") (shift 0.0 40.0)))

The copy holds the named node and its descendants; the rest of the template is
neither copied nor touched. The root keeps ITS OWN place along the path from the
template's anchor (the declaration's own `anchor` only replaces the template's
anchor), and a reference pointing outside the copied subtree is a fatal — the
copy stands on another sheet where those parts do not exist.

Cells: 7 (what is copied), 7б (the pose), 8 (every outward reference), 9 (no
`root` — the historical behaviour).
"""
import pytest

from kicadstamp.config.tree_instances import expand_tree_instances
from kicadstamp.exceptions import ValidationError


def _data(instances) -> dict:
    """driver (10, 0) with a child sw (2, 0), a sibling other (0, 5), and a
    mount node under driver that looks INSIDE the template (sheet SW A)."""
    return {
        "cells": {
            "c_main": {"components": [{"role": "R_MAIN"}]},
            "c_sub": {"components": [{"role": "R_SUB"}]},
        },
        "entities": [
            {"name": "driver", "cell": "c_main", "sheet": "SW A", "cluster": "ARM"},
            {"name": "sw", "cell": "c_sub", "sheet": "SW A", "cluster": "ARM"},
            {"name": "other", "cell": "c_sub", "sheet": "SW A", "cluster": "ARM"},
        ],
        "trees": [{
            "name": "tpl", "anchor": {"role": "R_MAIN"},
            "nodes": [
                {"ref": "driver", "kind": "placement", "xy": [10.0, 0.0],
                 "children": [
                     {"ref": "sw", "kind": "placement", "xy": [2.0, 0.0]},
                     {"ref": "m1", "kind": "mount", "xy": [0.0, 0.0],
                      "anchor": {"role": "R_MAIN", "sheet": "SW A"}},
                 ]},
                {"ref": "other", "kind": "placement", "xy": [0.0, 5.0]},
            ],
        }],
        "tree_instances": list(instances),
    }


def _row(**extra) -> dict:
    row = {"template": "tpl", "name": "b", "sheet": "SW B",
           "anchor": {"origin": True}}
    row.update(extra)
    return row


def _copy(out: dict, name: str = "b") -> dict:
    return next(t for t in out["trees"] if t["name"] == name)


def _live_pose(nodes, root_ref):
    """(x_mm, y_mm, rotation_deg) of `root_ref` by the LIVE rule: `node_position`
    chained down the path, the rotation accumulated exactly as `entity_placement`
    composes it (§8.4 — a cell must never lean on a literal alone, or the
    mutation `if rot:` -> `if False:` inside `subtree_pose` survives it)."""
    from kicadstamp.domain.geometry import Vector2
    from kicadstamp.tree_position import node_position
    from kicadstamp.trees import TreeNode
    from kicadstamp.utils.units import MM

    def walk(children, pos, rot):
        for node in children:
            tree_node = TreeNode(
                ref=node["ref"], kind=node.get("kind"),
                xy=tuple(node["xy"]) if node.get("xy") is not None else None,
                polar=(tuple(node["polar"])
                       if node.get("polar") is not None else None),
                rotation=float(node.get("rotation") or 0.0),
                name=None, group=None)
            here = node_position(tree_node, pos, rot)
            own_rot = rot + tree_node.rotation
            if node["ref"] == root_ref:
                return here, own_rot
            found = walk(node.get("children") or [], here, own_rot)
            if found is not None:
                return found
        return None

    pos, rot = walk(nodes, Vector2.from_xy(0, 0), 0.0)
    return pos.x / MM, pos.y / MM, rot


def _rounded(nodes, root_ref) -> tuple[list, float]:
    x, y, rot = _live_pose(nodes, root_ref)
    return [round(x, 6), round(y, 6)], round(rot, 6)


# ── 7: only the root and its subtree are copied ────────────────────────────

def test_only_the_root_and_its_descendants_are_copied():
    out = expand_tree_instances(_data([_row(root={"ref": "driver"})]))

    copy = _copy(out)
    assert [n["ref"] for n in copy["nodes"]] == ["driver__b"]
    assert [n["ref"] for n in copy["nodes"][0]["children"]] == ["sw__b", "m1"]
    copies = {e["name"] for e in out["entities"] if e["name"].endswith("__b")}
    assert copies == {"driver__b", "sw__b"}
    assert "other__b" not in copies         # the sibling is NOT copied
    # The mount looks INSIDE the template and is re-sheeted to the instance —
    # which proves old_sheet came from the ROOT's record (§7.3).
    mount = copy["nodes"][0]["children"][1]
    assert mount["anchor"]["sheet"] == "SW B"


def test_the_roots_sheet_wins_over_the_sibling_sheets():
    """§7.3: `old_sheet` is the ROOT record's sheet — a sibling on another sheet
    must not make the answer ambiguous (Н3 never even runs)."""
    data = _data([_row(root={"ref": "driver"})])
    data["entities"][2]["sheet"] = "Elsewhere"     # `other`

    out = expand_tree_instances(data)

    mount = _copy(out)["nodes"][0]["children"][1]
    assert mount["anchor"]["sheet"] == "SW B"


# ── 7б: the root keeps its own pose along the path ─────────────────────────

def test_the_root_stands_where_it_stood_in_the_template():
    """The path pose: driver (10, 0) + sw (2, 0) = (12, 0). A declaration's own
    anchor does NOT move the node — it replaces the template's anchor only
    (§7.3 of the plan)."""
    out = expand_tree_instances(_data([_row(root={"ref": "sw"})]))

    copy = _copy(out)
    assert [n["ref"] for n in copy["nodes"]] == ["sw__b"]
    assert copy["nodes"][0]["xy"] == [12.0, 0.0]
    assert copy["anchor"] == {"origin": True}


def test_the_root_pose_accumulates_the_parent_rotation():
    """A 90-deg parent turns the child's frame: the root's own accumulated
    rotation is 90 even though its stored `rotation` is absent — and its XY is
    checked against the live rule, so skipping the parent-frame rotation is
    caught (§8.4)."""
    data = _data([_row(root={"ref": "sw"})])
    data["trees"][0]["nodes"][0]["rotation"] = 90.0

    copy = _copy(expand_tree_instances(data))["nodes"][0]

    assert copy["rotation"] == 90.0
    xy, rot = _rounded(data["trees"][0]["nodes"], "sw")
    assert copy["xy"] == xy
    assert copy["rotation"] == rot


def test_the_root_pose_matches_the_live_rule_through_two_rotations_and_a_polar_hop():
    """Depth 3, two rotated ancestors and a POLAR offset: the copy's xy/rotation
    are the live rule's numbers, not literals (§8.4)."""
    data = _data([_row(root={"ref": "deep"})])
    data["entities"].append({"name": "deep", "cell": "c_sub", "sheet": "SW A",
                             "cluster": "ARM"})
    nodes = data["trees"][0]["nodes"]
    nodes[0]["rotation"] = 90.0
    nodes[0]["children"][0]["rotation"] = 30.0            # sw
    nodes[0]["children"][0]["children"] = [
        {"ref": "deep", "kind": "placement", "polar": [4.0, 45.0]}]

    copy = _copy(expand_tree_instances(data))["nodes"][0]
    xy, rot = _rounded(nodes, "deep")

    assert copy["xy"] == xy
    assert copy["rotation"] == rot == 120.0
    assert copy["xy"] != [0.0, 0.0]        # the polar hop contributed


def test_a_declared_anchor_and_shift_land_on_the_copy_not_on_the_root():
    """The declaration's own anchor (here origin + shift 0/40) replaces the
    template's anchor; the root node keeps its path pose, so the whole subtree is
    the template's plus that shift at MATERIALIZATION time."""
    row = _row(root={"ref": "driver"},
               anchor={"origin": True, "shift": [0.0, 40.0]})

    copy = _copy(expand_tree_instances(_data([row])))

    assert copy["anchor"] == {"origin": True, "shift": [0.0, 40.0]}
    assert copy["nodes"][0]["xy"] == [10.0, 0.0]


# ── 8: every reference that points OUTSIDE the subtree is a fatal ──────────

def test_a_pivot_ref_outside_the_subtree_is_refused():
    data = _data([_row(root={"ref": "driver"})])
    data["trees"][0]["pivot_ref"] = "other"

    with pytest.raises(ValidationError, match="outside the copied subtree"):
        expand_tree_instances(data)


def test_a_self_anchor_ref_outside_the_subtree_is_refused():
    data = _data([_row(root={"ref": "driver"})])
    data["trees"][0]["anchor"] = {"self": {"ref": "other"}}

    with pytest.raises(ValidationError, match="outside the copied subtree"):
        expand_tree_instances(data)


def test_a_mount_that_anchors_a_role_the_subtree_does_not_place_is_refused():
    data = _data([_row(root={"ref": "driver"})])
    data["trees"][0]["nodes"][0]["children"][1]["anchor"]["role"] = "R_OTHER"

    with pytest.raises(ValidationError, match="does not place"):
        expand_tree_instances(data)


def test_a_net_trace_whose_role_is_outside_the_subtree_is_refused():
    data = _data([_row(root={"ref": "driver"})])
    data["trees"][0]["nodes"][0]["children"].append(
        {"ref": "nt", "kind": "net_trace"})
    data["net_traces"] = [{"name": "nt", "anchor_role": "R_OTHER", "tracks": []}]

    with pytest.raises(ValidationError, match="does not place"):
        expand_tree_instances(data)


def test_a_root_that_names_no_node_is_refused():
    with pytest.raises(ValidationError, match="names no node of template"):
        expand_tree_instances(_data([_row(root={"ref": "nope"})]))


def test_a_root_that_is_not_a_placement_node_is_refused():
    with pytest.raises(ValidationError, match="is not a placement node"):
        expand_tree_instances(_data([_row(root={"ref": "m1"})]))


def test_a_root_under_a_mount_node_is_refused():
    """§8.3: a mount node's base is the LIVE board's part, not `driver + 3`, so a
    path through it cannot be composed from the config — the old code accepted
    this and put the copy at the wrong place (xy [14.0, 1.0] for a mount at
    (3, 0) under driver at (10, 0))."""
    data = _data([_row(root={"ref": "under_mount"})])
    data["entities"].append({"name": "under_mount", "cell": "c_sub",
                             "sheet": "SW A", "cluster": "ARM"})
    data["trees"][0]["nodes"][0]["children"][1]["xy"] = [3.0, 0.0]
    data["trees"][0]["nodes"][0]["children"][1]["children"] = [
        {"ref": "under_mount", "kind": "placement", "xy": [1.0, 1.0]}]

    with pytest.raises(ValidationError, match="hangs under mount node 'm1'"):
        expand_tree_instances(data)


def test_the_mount_ancestor_message_says_what_to_do_instead():
    data = _data([_row(root={"ref": "under_mount"})])
    data["entities"].append({"name": "under_mount", "cell": "c_sub",
                             "sheet": "SW A", "cluster": "ARM"})
    data["trees"][0]["nodes"][0]["children"][1]["children"] = [
        {"ref": "under_mount", "kind": "placement", "xy": [1.0, 1.0]}]

    with pytest.raises(ValidationError) as caught:
        expand_tree_instances(data)

    text = str(caught.value)
    assert "root 'under_mount'" in text
    assert "place comes from the board" in text
    assert "name the mount's parent or a node above it" in text


def test_a_malformed_root_is_refused():
    with pytest.raises(ValidationError, match="must be a mapping"):
        expand_tree_instances(_data([_row(root="driver")]))
    with pytest.raises(ValidationError, match="names no node"):
        expand_tree_instances(_data([_row(root={})]))


# ── 9: no `root` — the historical behaviour, unchanged ─────────────────────

def test_without_a_root_the_whole_template_is_copied():
    out = expand_tree_instances(_data([_row()]))

    copy = _copy(out)
    assert [n["ref"] for n in copy["nodes"]] == ["driver__b", "other__b"]
    assert copy["nodes"][0]["xy"] == [10.0, 0.0]
    assert copy["nodes"][1]["xy"] == [0.0, 5.0]
    assert {e["name"] for e in out["entities"] if e["name"].endswith("__b")} == {
        "driver__b", "sw__b", "other__b"}
