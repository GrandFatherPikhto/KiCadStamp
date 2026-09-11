#!/usr/bin/env python3
"""`tree_instances:` after the mount-node rework (plan_2026_09_11_tree_instances
_and_converter_safety, tasks В.2 / В.3 / В.4).

Three groups:

* the CONVERTER resolving a module node that points at a generated INSTANCE
  (В.2) — its pivot belongs to the TEMPLATE tree, and a ref naming neither a
  tree nor an instance is a STOP, not the old silent `continue`;
* a MOUNT node inside a template (В.3): the ref is not suffixed, the anchor's
  sheet (and, only with it, the declaration's cluster) is substituted when it
  names the template's own sheet, kept verbatim when it names a foreign one,
  and a sheetless anchor is a fatal;
* the template's pivot_ref following the node renames (В.4).

The frozen pair under tests/fixtures/tree_instances_mount/ is READ here (both
files are in git); the CONVERTED file is what the converter must reproduce
byte-for-byte.

The fixture's mount anchors deliberately name roles (HOST / FOREIGN) that are
NOT in any cell the tree places, or the load-time drift guard (trees.py::
check_mount_anchor_drift) would refuse the config — see the report's note.
"""
import logging
from pathlib import Path

import pytest

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config.tree_instances import expand_tree_instances
from kicadstamp.exceptions import ValidationError
from kicadstamp.tree_mount_convert import convert_config_file, convert_trees_dict
from kicadstamp.trees import tree_from_dict

FIXTURES = Path(__file__).parent / "fixtures" / "tree_instances_mount"


# ── helpers ────────────────────────────────────────────────────────────────

def _tree(data: dict, name: str) -> dict:
    return next(t for t in data["trees"] if t["name"] == name)


def _first_mount(tree: dict) -> dict:
    """The first kind "mount" node of a generated tree (depth-first)."""
    stack = list(tree.get("nodes") or [])
    while stack:
        node = stack.pop(0)
        if node.get("kind") == "mount":
            return node
        stack = list(node.get("children") or []) + stack
    raise AssertionError("no mount node in the generated tree")


def _pivot_keys_on_nodes(trees: list) -> list:
    """(tree, node-ref) for every node that still carries a pivot-* key — must
    be EMPTY after conversion (the inner point lives on the TREE)."""
    bad: list = []

    def walk(nodes: list, tree_name: str) -> None:
        for node in nodes:
            if any(k.startswith("pivot") for k in node):
                bad.append((tree_name, node.get("ref")))
            walk(node.get("children") or [], tree_name)

    for tree in trees:
        walk(tree.get("nodes") or [], tree["name"])
    return bad


def _auto_template_with_mount(mount_anchor: dict, *, pivot_ref=None) -> dict:
    """A minimal AUTO-anchored template (one top-level placement node) with a
    mount node `H1` wrapping `E1`, plus one tree_instances declaration."""
    template: dict = {
        "name": "tpl",
        "nodes": [{
            "ref": "ROOT", "kind": "placement", "xy": [0.0, 0.0],
            "children": [{
                "ref": "H1", "kind": "mount", "anchor": dict(mount_anchor),
                "children": [{"ref": "E1", "kind": "placement", "xy": [1.0, 0.0]}],
            }],
        }],
    }
    if pivot_ref is not None:
        template["pivot_ref"] = pivot_ref
    return {
        "trees": [template],
        "entities": [{"name": "ROOT", "cell": "c", "sheet": "Own"},
                     {"name": "E1", "cell": "c", "sheet": "Own"}],
        "cells": {"c": {"components": [{"role": "R1"}]}},
        "tree_instances": [{"template": "tpl", "name": "tpl_a", "sheet": "Own_a",
                            "cluster": "CL"}],
    }


def _role_template(pivot_xy=None, pivot_polar=None, pivot_ref=None,
                   rotation=None, nodes=None) -> dict:
    """A role-anchored template (with an anchor.shift) + one declaration, so
    the instance's inheritance of pivot/rotation/shift can be checked."""
    tree: dict = {
        "name": "tpl",
        "anchor": {"role": "HOST", "sheet": "Own", "shift": [0.5, 0.25]},
        "nodes": nodes if nodes is not None
                 else [{"ref": "E1", "kind": "placement", "xy": [1.0, 2.0]}],
    }
    if pivot_xy is not None:
        tree["pivot_xy"] = pivot_xy
    if pivot_polar is not None:
        tree["pivot_polar"] = pivot_polar
    if pivot_ref is not None:
        tree["pivot_ref"] = pivot_ref
    if rotation is not None:
        tree["rotation"] = rotation
    return {
        "trees": [tree],
        "entities": [{"name": "E1", "cell": "c", "sheet": "Own"}],
        "cells": {"c": {"components": [{"role": "R1"}]}},
        "tree_instances": [{"template": "tpl", "name": "tpl_a", "sheet": "Own_a"}],
    }


# ── В.6.2: the converter and generated instances ───────────────────────────

def test_converter_drops_a_zero_pivot_on_an_instance_module_node():
    """Б3.2 §В.2.2: an instance inherits its template's (default) inner point,
    so a zero pivot is a no-op and is simply dropped — the case that used to
    leave the pivot on the node and break the serializer."""
    data = {
        "trees": [
            {"name": "tpl", "nodes": [{"ref": "E1", "kind": "placement",
                                       "xy": [0, 0]}]},
            {"name": "parent", "anchor": {"origin": True}, "nodes": [
                {"ref": "inst_x", "kind": "module", "pivot_xy": [0, 0]}]},
        ],
        "tree_instances": [{"template": "tpl", "name": "inst_x", "sheet": "S1"}],
    }
    converted, report = convert_trees_dict(data)
    node = converted["trees"][1]["nodes"][0]
    assert "pivot_xy" not in node
    assert report["instance_pivots_dropped"] == 1
    assert report["pivots_moved"] == 0
    sexp_to_dict(dict_to_sexp(converted))          # now serializable


def test_converter_stops_on_a_nonzero_pivot_over_an_instance():
    data = {
        "trees": [
            {"name": "tpl", "nodes": [{"ref": "E1", "kind": "placement",
                                       "xy": [0, 0]}]},
            {"name": "parent", "anchor": {"origin": True}, "nodes": [
                {"ref": "inst_x", "kind": "module", "pivot_xy": [1, 2]}]},
        ],
        "tree_instances": [{"template": "tpl", "name": "inst_x", "sheet": "S1"}],
    }
    with pytest.raises(ValidationError,
                       match="instance of tree 'tpl'"):
        convert_trees_dict(data)


def test_converter_stops_on_an_unknown_module_ref():
    """§В.2.2: previously a silent `continue`, after which dict_to_sexp failed
    with an unrelated message."""
    data = {"trees": [{"name": "t", "anchor": {"origin": True}, "nodes": [
        {"ref": "ghost", "kind": "module", "pivot_xy": [1, 2]}]}]}
    with pytest.raises(ValidationError, match="references neither a trees"):
        convert_trees_dict(data)


def test_the_fixture_converts_expands_and_is_idempotent(tmp_path):
    """§В.2.3: end-to-end on the frozen fixture — parent tree + auto template +
    two declarations, all embeddings `(pivot-xy 0 0)`."""
    out = tmp_path / "converted.sexp"
    convert_config_file(root=str(FIXTURES / "config.sexp"), output=str(out))

    # byte-for-byte the committed reference
    assert out.read_bytes() == (FIXTURES / "config.converted.sexp").read_bytes()

    # readable by the NORMAL reader, no pivot-* left ON A NODE
    data = sexp_to_dict(out.read_text(encoding="utf-8"))
    assert _pivot_keys_on_nodes(data["trees"]) == []
    # ... the zero inner point legitimately moved onto the TREE
    assert _tree(data, "tpl")["pivot_xy"] == [0.0, 0.0]

    # instances expand (mount nodes and all)
    expanded = expand_tree_instances(data)
    names = {t["name"] for t in expanded["trees"]}
    assert {"tpl_a", "tpl_b"} <= names
    assert _tree(expanded, "tpl_a")["pivot_xy"] == [0.0, 0.0]
    tree_from_dict(_tree(expanded, "tpl_a"))       # the generated tree loads

    # idempotent
    again = tmp_path / "again.sexp"
    convert_config_file(root=str(out), output=str(again))
    assert again.read_bytes() == out.read_bytes()


# ── В.6.3: a mount node inside a template ──────────────────────────────────

def test_mount_ref_is_not_suffixed_but_its_children_are():
    out = expand_tree_instances(
        _auto_template_with_mount({"role": "HOST", "sheet": "Own"}))
    tree = _tree(out, "tpl_a")
    assert tree["nodes"][0]["ref"] == "ROOT__tpl_a"     # placed node: suffixed
    mount = _first_mount(tree)
    assert mount["ref"] == "H1"                        # mount: NOT suffixed
    assert mount["children"][0]["ref"] == "E1__tpl_a"
    _pivot_keys_on_nodes([tree])                       # no leftover pivots


def test_mount_anchor_sheet_and_cluster_are_substituted_by_value():
    """§В.3.2(б)/(в): a sheet EQUAL to the template's own follows the instance,
    and the declaration's cluster follows WITH it — by VALUE, not merely "no
    exception"."""
    out = expand_tree_instances(
        _auto_template_with_mount({"role": "HOST", "sheet": "Own",
                                   "cluster": "GRP"}))
    assert _first_mount(_tree(out, "tpl_a"))["anchor"] == {
        "role": "HOST", "sheet": "Own_a", "cluster": "CL"}


def test_a_foreign_sheet_mount_anchor_is_kept_verbatim(caplog):
    with caplog.at_level(logging.INFO):
        out = expand_tree_instances(
            _auto_template_with_mount({"role": "FOREIGN", "sheet": "Shared",
                                       "cluster": "GRP"}))
    assert _first_mount(_tree(out, "tpl_a"))["anchor"] == {
        "role": "FOREIGN", "sheet": "Shared", "cluster": "GRP"}
    assert "Shared" in caplog.text


def test_a_sheetless_mount_anchor_is_a_fatal():
    with pytest.raises(ValidationError, match="no sheet in its anchor"):
        expand_tree_instances(_auto_template_with_mount({"role": "HOST"}))


def test_two_instances_get_their_own_mount_anchor_sheets():
    """Regression on «all three channels mounted to channel 0»: each instance
    must narrow to ITS OWN sheet, never the template's."""
    data = _auto_template_with_mount({"role": "HOST", "sheet": "Own",
                                      "cluster": "GRP"})
    data["tree_instances"] = [
        {"template": "tpl", "name": "tpl_a", "sheet": "Own_a", "cluster": "A"},
        {"template": "tpl", "name": "tpl_b", "sheet": "Own_b", "cluster": "B"},
    ]
    out = expand_tree_instances(data)
    assert _first_mount(_tree(out, "tpl_a"))["anchor"]["sheet"] == "Own_a"
    assert _first_mount(_tree(out, "tpl_a"))["anchor"]["cluster"] == "A"
    assert _first_mount(_tree(out, "tpl_b"))["anchor"]["sheet"] == "Own_b"
    assert _first_mount(_tree(out, "tpl_b"))["anchor"]["cluster"] == "B"


def test_a_template_without_mounts_expands_as_before():
    """Regression fence: the ordinary placement path is untouched."""
    out = expand_tree_instances(_role_template())
    tree = _tree(out, "tpl_a")
    assert tree["nodes"][0]["ref"] == "E1__tpl_a"
    assert tree["anchor"]["sheet"] == "Own_a"
    assert {e["name"] for e in out["entities"]} >= {"E1", "E1__tpl_a"}


# ── В.6.4: inheritance and pivot_ref ───────────────────────────────────────

def test_instance_inherits_pivot_rotation_and_shift():
    out = expand_tree_instances(_role_template(pivot_xy=[1.5, -2.5], rotation=30.0))
    tree = _tree(out, "tpl_a")
    assert tree["pivot_xy"] == [1.5, -2.5]
    assert tree["rotation"] == 30.0
    assert tree["anchor"]["shift"] == [0.5, 0.25]     # inherited, not rewritten
    assert tree["anchor"]["sheet"] == "Own_a"


def test_instance_inherits_a_polar_pivot():
    out = expand_tree_instances(_role_template(pivot_polar=[3.0, 45.0]))
    assert _tree(out, "tpl_a")["pivot_polar"] == [3.0, 45.0]


def test_pivot_ref_on_a_placement_node_follows_the_suffix():
    out = expand_tree_instances(_role_template(pivot_ref="E1"))
    tree = _tree(out, "tpl_a")
    assert tree["pivot_ref"] == "E1__tpl_a"
    tree_from_dict(tree)                             # names a real node -> loads


def test_pivot_ref_on_a_net_trace_node_follows_the_net_rewrite():
    data = _role_template(
        pivot_ref="/Own/GRP/N",
        nodes=[{"ref": "E1", "kind": "placement", "xy": [0, 0]},
               {"ref": "/Own/GRP/N", "kind": "net_trace"}])
    data["net_traces"] = [{"net": "/Own/GRP/N", "anchor_role": "H", "anchor_sheet": "Own"}]
    out = expand_tree_instances(data)
    tree = _tree(out, "tpl_a")
    assert tree["pivot_ref"] == "/Own_a/GRP/N"
    tree_from_dict(tree)


def test_pivot_ref_naming_a_missing_node_fatals_at_expansion():
    with pytest.raises(ValidationError, match="names no node of the template"):
        expand_tree_instances(_role_template(pivot_ref="NOPE"))


def test_pivot_ref_on_the_auto_templates_root_follows_the_suffix():
    """The root of an AUTO template is eligible (it is not under the mount), so
    its pivot_ref must follow the same suffix as the node."""
    out = expand_tree_instances(
        _auto_template_with_mount({"role": "HOST", "sheet": "Own"},
                                  pivot_ref="ROOT"))
    tree = _tree(out, "tpl_a")
    assert tree["pivot_ref"] == "ROOT__tpl_a"
    tree_from_dict(tree)
