# tests/test_scheme_list_place.py
"""P6 "Place Scheme List" — pure (Qt-free) core tests (plan_2026_09_05_
scheme_list.md §6, handoff_2026_09_06_scheme_list_p6_staged.md Stage 1):
  * gui/docks/tree_from_selection.build_scheme_list_entity — the entities:
    dict shape for a NEW scheme_list-based Entity (scheme_list: instead of
    cell:, no cluster/refs/by_selection), sharing _entity_payload with
    build_instantiated_entity;
  * kicadstamp/config_writer.append_tree_child_node — append a placement node
    as a CHILD of an existing tree node (parent_ref) or as a new top-level
    node (parent_ref=None) of an EXISTING tree, NEVER a new tree; OSError on
    a missing tree / missing parent;
  * round-trip: a config carrying the scheme_list Entity + the appended node
    loads and link_trees resolves the node to the Entity (not cell=None).

The GUI widget tests (validation, DockHub wiring) live in
tests/gui/test_scheme_list_place.py (Stage 5).
"""
import json
from pathlib import Path

import pytest

from gui.docks.tree_from_selection import (
    build_instantiated_entity,
    build_scheme_list_entity,
)
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_writer import append_tree_child_node
from kicadstamp.config import load_config
from kicadstamp.link_trees import link_trees


def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path: Path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _read_back(path: Path, tree_name: str) -> dict:
    """The tree dict named tree_name in `path` (post-write reload)."""
    return next(t for t in _load(path)["trees"] if t["name"] == tree_name)


def _find_node(tree: dict, ref: str):
    """(parent_list, node_dict) where node_dict has ref == ref — the list is
    tree["nodes"] for a top-level node, else the owning node's "children".
    Returns (None, None) when absent."""
    def walk(nodes):
        for n in nodes or []:
            if n.get("ref") == ref:
                return nodes, n
            hit = walk(n.get("children") or [])
            if hit[1] is not None:
                return hit
        return None, None
    return walk(tree.get("nodes"))


class TestBuildSchemeListEntity:
    def test_shape_scheme_list_only_no_cell_cluster_refs(self):
        ent = build_scheme_list_entity("PSU_CH0", "psu", "Channel_0")
        assert ent == {"name": "PSU_CH0", "scheme_list": "psu", "sheet": "Channel_0"}
        # Deliberately NO cell/cluster/refs/by_selection: a recorded snapshot
        # already carries its literal refs/nets; role-pinning keys are fatal on
        # a scheme_list Entity at load (config/entries.py::_load_entity).
        assert "cell" not in ent
        assert "cluster" not in ent
        assert "refs" not in ent
        assert "by_selection" not in ent

    def test_sheet_optional_defaults_to_in_place(self):
        ent = build_scheme_list_entity("PSU_CH0", "psu")
        assert ent == {"name": "PSU_CH0", "scheme_list": "psu"}

    def test_shares_entity_payload_with_build_instantiated(self):
        """Both builders go through the same _entity_payload — the cell-based
        shape is unchanged (P5 regression guard)."""
        inst = build_instantiated_entity("c_psu", "PSU_CH0", "PSU", "Channel_0")
        assert inst == {"name": "PSU_CH0", "cell": "c_psu",
                        "cluster": "PSU", "sheet": "Channel_0"}
        sl = build_scheme_list_entity("PSU_CH0", "psu", "Channel_0")
        assert sl == {"name": "PSU_CH0", "scheme_list": "psu", "sheet": "Channel_0"}


def _placement_node(ref, x=1.0, y=2.0, rotation=None):
    node = {"ref": ref, "kind": "placement", "xy": [x, y]}
    if rotation:
        node["rotation"] = rotation
    return node


class TestAppendTreeChildNode:
    def test_top_level_parent_none_appends_to_tree_nodes(self, tmp_path):
        path = tmp_path / "root.sexp"
        _write(path, {"trees": [
            {"name": "main", "anchor": {"origin": True},
             "nodes": [{"ref": "PARENT", "kind": "placement", "xy": [0.0, 0.0]}]},
        ]})
        changed = append_tree_child_node(
            path, "main", None, _placement_node("E1", rotation=90.0))
        assert changed is True
        tree = _read_back(path, "main")
        nodes, node = _find_node(tree, "E1")
        assert nodes is tree["nodes"]  # top-level, NOT in any parent's children
        assert node["rotation"] == 90.0

    def test_child_by_ref_appends_to_parent_children(self, tmp_path):
        path = tmp_path / "root.sexp"
        _write(path, {"trees": [
            {"name": "main", "anchor": {"origin": True},
             "nodes": [{"ref": "PARENT", "kind": "placement", "xy": [0.0, 0.0]}]},
        ]})
        changed = append_tree_child_node(
            path, "main", "PARENT", _placement_node("E1", 3.0, 4.0, rotation=180.0))
        assert changed is True
        tree = _read_back(path, "main")
        nodes, node = _find_node(tree, "E1")
        # The node landed in the PARENT's children — NOT in tree["nodes"].
        assert nodes is not tree["nodes"]
        parent = next(n for n in tree["nodes"] if n["ref"] == "PARENT")
        assert node in parent["children"]
        assert node["xy"] == [3.0, 4.0]
        assert node["rotation"] == 180.0

    def test_child_under_deeply_nested_parent(self, tmp_path):
        path = tmp_path / "root.sexp"
        _write(path, {"trees": [
            {"name": "main", "anchor": {"origin": True},
             "nodes": [{"ref": "GP", "kind": "placement", "xy": [0.0, 0.0],
                        "children": [{"ref": "PARENT", "kind": "placement",
                                      "xy": [1.0, 1.0]}]}]},
        ]})
        append_tree_child_node(path, "main", "PARENT", _placement_node("E1"))
        tree = _read_back(path, "main")
        _, parent = _find_node(tree, "PARENT")
        _, e1 = _find_node(tree, "E1")
        assert e1 in parent["children"]

    def test_no_tree_is_os_error(self, tmp_path):
        path = tmp_path / "root.sexp"
        _write(path, {"trees": [
            {"name": "main", "anchor": {"origin": True}, "nodes": []},
        ]})
        with pytest.raises(OSError):
            append_tree_child_node(path, "nope", None, _placement_node("E1"))

    def test_no_parent_is_os_error(self, tmp_path):
        path = tmp_path / "root.sexp"
        _write(path, {"trees": [
            {"name": "main", "anchor": {"origin": True},
             "nodes": [{"ref": "PARENT", "kind": "placement", "xy": [0.0, 0.0]}]},
        ]})
        with pytest.raises(OSError):
            append_tree_child_node(path, "main", "MISSING", _placement_node("E1"))

    def test_non_list_trees_section_is_os_error(self, tmp_path):
        path = tmp_path / "root.json"
        path.write_text(json.dumps({"trees": "nope"}), encoding="utf-8")
        with pytest.raises(OSError):
            append_tree_child_node(path, "main", None, _placement_node("E1"))

    def test_preserves_other_trees_and_root_keys(self, tmp_path):
        path = tmp_path / "root.sexp"
        _write(path, {
            "trees": [
                {"name": "keep", "anchor": {"origin": True},
                 "nodes": [{"ref": "OTHER", "kind": "placement", "xy": [1.0, 1.0]}]},
            ],
            "entities": [{"name": "E0", "scheme_list": "psu"}],
        })
        append_tree_child_node(path, "keep", None, _placement_node("E1"))
        data = _load(path)
        assert data["entities"] == [{"name": "E0", "scheme_list": "psu"}]
        assert len(data["trees"]) == 1
        _, other = _find_node(data["trees"][0], "OTHER")
        assert other["xy"] == [1.0, 1.0]

    def test_identical_node_is_noop(self, tmp_path):
        path = tmp_path / "root.sexp"
        node = _placement_node("E1")
        _write(path, {"trees": [
            {"name": "main", "anchor": {"origin": True}, "nodes": [node]},
        ]})
        changed = append_tree_child_node(path, "main", None, dict(node))
        assert changed is False


def _scheme_record_dict(name="psu"):
    """A minimal valid scheme_lists: entry (anchor_ref among its own
    components) for the round-trip load."""
    return {
        "name": name,
        "anchor_ref": "R1",
        "source_sheet": "Channel_0",
        "anchor_rotation_deg": 0.0,
        "components": [
            {"ref": "R1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "rotation_deg": 0.0},
            {"ref": "C1", "offset_along_mm": 10.0, "offset_across_mm": 0.0,
             "rotation_deg": 0.0},
        ],
    }


class TestSchemeListEntityRoundTrip:
    def test_entity_and_appended_node_load_and_link(self, tmp_path):
        """The Stage-1 round-trip: build_scheme_list_entity + an appended
        placement node (child of an existing node) survive load_config and
        link_trees resolves the node to the Entity — never cell=None."""
        root = tmp_path / "root.sexp"
        # PARENT must itself be an existing Entity so link_trees resolves the
        # pre-existing placement node too (a placement ref always resolves).
        root.write_text(dict_to_sexp({
            "scheme_lists": [_scheme_record_dict()],
            "entities": [
                {"name": "PARENT", "cell": "c_parent"},
                build_scheme_list_entity("PSU_CH0", "psu", "Channel_0"),
            ],
            "trees": [{
                "name": "main", "anchor": {"origin": True},
                "nodes": [{"ref": "PARENT", "kind": "placement", "xy": [0.0, 0.0]}],
            }],
        }), encoding="utf-8")
        append_tree_child_node(root, "main", "PARENT",
                               _placement_node("PSU_CH0", 5.0, 6.0, rotation=90.0))
        cfg, _ = load_config(str(root))
        assert "PSU_CH0" in {e.name for e in cfg.entities}
        ent = next(e for e in cfg.entities if e.name == "PSU_CH0")
        assert ent.cell is None
        assert ent.scheme_list == "psu"
        linked = link_trees(cfg, cfg.trees)[0]
        nodes, node = _find_node(_read_back(root, "main"), "PSU_CH0")
        assert nodes is not None and node is not None

        def _all_ln(lnodes):
            for ln in lnodes:
                yield ln
                yield from _all_ln(ln.children)

        # link_trees resolves the appended CHILD node to the real Entity record
        # (never the cell machinery — record is never cell=None).
        ln = next(ln for ln in _all_ln(linked.nodes) if ln.node.ref == "PSU_CH0")
        assert ln.record is not None
        assert ln.record.name == "PSU_CH0"
