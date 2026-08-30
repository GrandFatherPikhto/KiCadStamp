# tests/test_upsert_entity_placement.py
"""Direct unit tests for config_writer.upsert_entity_placement (phase 5.2,
stage 2): the trees:-node writer behind PlacerDock's "save Origin = write
node.xy/node.anchor". Position lives ONLY in trees, so the write must keep
the link_trees "a ref appears in at most one node" invariant — a changed
anchor MOVES the node to the matching tree, never duplicates it."""
import json
from pathlib import Path

import pytest

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_writer import upsert_entity_placement


def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path: Path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _find(trees, ref):
    """(tree_dict, node_dict) of the node whose ref == ref, or (None, None)."""
    for tree in trees:

        def walk(nodes):
            for n in nodes or []:
                if n.get("ref") == ref:
                    return n
                hit = walk(n.get("children") or [])
                if hit:
                    return hit
            return None

        hit = walk(tree.get("nodes"))
        if hit:
            return tree, hit
    return None, None


def test_creates_a_new_origin_tree_when_none_matches(tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {"trees": []})
    changed = upsert_entity_placement(path, "E1", {"mode": "xy", "x": 5.0, "y": 2.0})
    assert changed is True
    tree, node = _find(_load(path)["trees"], "E1")
    assert tree["anchor"] == {"origin": True}
    assert node == {"ref": "E1", "kind": "placement", "xy": [5.0, 2.0]}


def test_updates_existing_node_in_place(tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {"trees": [
        {"name": "flat", "anchor": {"origin": True},
         "nodes": [{"ref": "E1", "kind": "placement", "xy": [1.0, 1.0]}]},
    ]})
    changed = upsert_entity_placement(path, "E1", {"mode": "xy", "x": 9.0, "y": -4.0})
    assert changed is True
    trees = _load(path)["trees"]
    assert len(trees) == 1  # no duplicate tree, no duplicate node
    _, node = _find(trees, "E1")
    assert node["xy"] == [9.0, -4.0]


def test_moves_node_when_anchor_changes(tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {"trees": [
        {"name": "flat", "anchor": {"origin": True},
         "nodes": [{"ref": "E1", "kind": "placement", "xy": [1.0, 1.0]}]},
    ]})
    upsert_entity_placement(path, "E1",
                            {"mode": "point", "point": "P1",
                             "shift_x": 0.0, "shift_y": 0.0})
    trees = _load(path)["trees"]
    tree, node = _find(trees, "E1")
    assert tree["anchor"] == {"point": "P1"}
    assert node["xy"] == [0.0, 0.0]
    # exactly one tree holds E1 — the origin tree no longer does
    holding = [t for t in trees if _find([t], "E1")[1] is not None]
    assert len(holding) == 1


def test_preserves_other_trees_nodes_and_root_keys(tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {
        "trees": [
            {"name": "keep", "anchor": {"origin": True},
             "nodes": [{"ref": "OTHER", "kind": "placement", "xy": [1.0, 1.0]}]},
        ],
        "entities": [{"name": "E1", "cell": "c"}],
    })
    upsert_entity_placement(path, "E1", {"mode": "xy", "x": 0.0, "y": 0.0})
    data = _load(path)
    assert data["entities"] == [{"name": "E1", "cell": "c"}]  # other keys preserved
    # E1 shares the EXISTING (origin)-anchored "keep" tree (matching anchor —
    # no duplicate tree), while OTHER's own node is untouched.
    assert len(data["trees"]) == 1
    keep_tree, keep_node = _find(data["trees"], "OTHER")
    assert keep_tree["name"] == "keep"
    assert keep_node["xy"] == [1.0, 1.0]
    _, e1_node = _find(data["trees"], "E1")
    assert e1_node["xy"] == [0.0, 0.0]


def test_rotation_and_polar_are_written(tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {"trees": []})
    upsert_entity_placement(path, "E1", {"mode": "xy", "radius": 3.0, "angle": 45.0},
                            rotation=90.0)
    _, node = _find(_load(path)["trees"], "E1")
    assert node["polar"] == [3.0, 45.0]
    assert node["rotation"] == 90.0


def test_role_anchor_with_narrowing_fields(tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {"trees": []})
    upsert_entity_placement(
        path, "E1",
        {"mode": "anchor", "role": "FPGA", "sheet": "Channel_0", "cluster": "CH0",
         "pad": "A1", "shift_x": 0.5, "shift_y": -0.5})
    tree, node = _find(_load(path)["trees"], "E1")
    assert tree["anchor"] == {"role": "FPGA", "sheet": "Channel_0",
                              "cluster": "CH0", "pad": "A1"}
    assert node["xy"] == [0.5, -0.5]


def test_non_list_trees_section_is_os_error(tmp_path):
    path = tmp_path / "root.json"
    path.write_text(json.dumps({"trees": "nope"}), encoding="utf-8")
    with pytest.raises(OSError):
        upsert_entity_placement(path, "E1", {"mode": "xy", "x": 0.0, "y": 0.0})
