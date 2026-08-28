# tests/test_trees_in_config.py
"""Core tests for the trees: section of the config (design_2026_08_27_trees_in_
config_file.md): trees.py's dict bridges (tree_to_dict/tree_from_dict/
tree_from_sexp), config/entries.py's _load_tree, include-graph merging of the
trees list section, name/ref uniqueness across the whole graph, the sexp
round-trip of the trees section, and the tools/trees_to_config.py migrator.

Trees live in the SAME config file (root .sexp or .json, or an include:'d
subsystem file), not in a separate *.trees file — that is the whole point of
the design (FORK-1/2/4/5).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.config.entries import _load_tree
from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import _strip_defaults, dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import (
    Tree, TreeAnchor, TreeNode,
    load_trees, save_trees, tree_from_dict, tree_from_sexp, tree_to_dict,
    tree_to_sexp,
)

from tools.trees_to_config import main, migrate

# ── trees.py dict bridges (FORK-2 Variant B) ───────────────────────────────

def _sample_tree() -> Tree:
    return Tree(
        name="power_tree",
        anchor=TreeAnchor(ref="CONN_PM5V", is_origin=False),
        nodes=[
            TreeNode(ref="AMS1117_REG", kind="clone", xy=(5.0, 2.0), polar=None,
                     rotation=0.0, name=None, group=None,
                     children=[TreeNode(ref="C_OUT", kind=None, xy=(1.0, 0), polar=None,
                                        rotation=0.0, name=None, group=None, children=[])]),
            TreeNode(ref="R_AROUND", kind=None, xy=None, polar=(3.0, 45.0),
                     rotation=90.0, name="around", group="g", children=[]),
        ],
    )


def test_tree_to_dict_roundtrip():
    t = _sample_tree()
    d = tree_to_dict(t)
    assert d["name"] == "power_tree"
    assert d["anchor"] == {"ref": "CONN_PM5V"}
    assert d["nodes"][0]["kind"] == "clone"
    assert d["nodes"][0]["xy"] == [5.0, 2.0]
    assert d["nodes"][0]["children"][0]["ref"] == "C_OUT"
    assert d["nodes"][1]["polar"] == [3.0, 45.0]
    assert d["nodes"][1]["rotation"] == 90.0
    # default-valued fields are omitted (no-noise), so round-trip is canonical
    assert tree_from_dict(d) == t


def test_tree_to_dict_omits_defaults():
    t = Tree(name="plain", anchor=TreeAnchor(ref=None, is_origin=True),
             nodes=[TreeNode(ref="N", kind=None, xy=None, polar=None,
                             rotation=0.0, name=None, group=None, children=[])])
    d = tree_to_dict(t)
    # The node itself survives (ref is mandatory), but every default-valued
    # FIELD is omitted: kind/xy/polar/rotation/name/group/children.
    assert d == {"name": "plain", "anchor": {"origin": True},
                 "nodes": [{"ref": "N"}]}
    assert tree_from_dict(d) == t


def test_anchor_external_dict_roundtrip():
    """An external anchor round-trips through the dict bridge (FORK-2 Variant
    B) — tree_to_dict emits external: true, tree_from_dict reads it back, so
    the collision shield survives the config inlay (note_2026_08_28_...)."""
    t = Tree(name="t", anchor=TreeAnchor(ref="U_FPGA", is_origin=False,
                                         is_external=True), nodes=[])
    d = tree_to_dict(t)
    assert d["anchor"] == {"ref": "U_FPGA", "external": True}
    assert tree_from_dict(d) == t


def test_tree_from_sexp_matches_load_trees():
    """tree_from_sexp on one (tree ...) node gives the same Tree that
    load_trees yields for the same node inside a file."""
    import tempfile, os
    t = _sample_tree()
    node = tree_to_sexp(t)
    parsed = tree_from_sexp(node, set(), set(), "test")
    assert parsed == t
    # and save/load round-trip of the whole file still works (back-compat path)
    p = tempfile.mktemp(suffix=".trees")
    save_trees(p, [t])
    assert load_trees(p) == [t]
    os.unlink(p)


# ── _load_tree (config/entries.py) ─────────────────────────────────────────

def test_load_tree_wraps_dict_bridge():
    d = {"name": "t", "anchor": {"ref": "A"},
         "nodes": [{"ref": "N", "xy": [1.0, 2.0]}]}
    tree = _load_tree(d)
    assert tree.name == "t"
    assert tree.nodes[0].ref == "N"
    assert tree.nodes[0].xy == (1.0, 2.0)


def test_load_tree_unknown_key_fatal():
    with pytest.raises(ValidationError, match="unknown fields in trees entry"):
        _load_tree({"name": "t", "anchor": {"ref": "A"}, "bogus": 1})


def test_load_tree_node_unknown_key_fatal():
    with pytest.raises(ValidationError, match="unknown fields in"):
        _load_tree({"name": "t", "anchor": {"ref": "A"},
                    "nodes": [{"ref": "N", "bogus": 1}]})


def test_load_tree_mutually_exclusive_xy_polar_fatal():
    with pytest.raises(ValidationError, match="xy and polar are mutually exclusive"):
        _load_tree({"name": "t", "anchor": {"ref": "A"},
                    "nodes": [{"ref": "N", "xy": [1.0, 2.0], "polar": [1.0, 2.0]}]})


# ── load_config: trees section in .yaml and .sexp ──────────────────────────

def _write(tmp_path, name, text) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


SEXP_WITH_TREES = dict_to_sexp({
    "layer": "B.Cu",
    "trees": [
        {"name": "power_tree", "anchor": {"ref": "CONN_PM5V"},
         "nodes": [{"ref": "AMS1117_REG", "kind": "clone", "xy": [5.0, 2.0],
                    "children": [{"ref": "C_OUT", "xy": [1.0, 0]}]},
                   {"ref": "R_AROUND", "polar": [3.0, 45.0], "rotation": 90.0}]},
        {"name": "misc", "anchor": {"origin": True},
         "nodes": [{"ref": "R_DEBUG", "xy": [100.0, 50.0]}]},
    ],
})


def test_sexp_roundtrip_trees_section(tmp_path):
    data = {"layer": "B.Cu",
            "trees": [
                {"name": "power_tree", "anchor": {"ref": "CONN_PM5V"},
                 "nodes": [{"ref": "AMS1117_REG", "kind": "clone", "xy": [5.0, 2.0],
                            "children": [{"ref": "C_OUT", "xy": [1.0, 0]}]},
                           {"ref": "R_AROUND", "polar": [3.0, 45.0], "rotation": 90.0}]},
                {"name": "misc", "anchor": {"origin": True},
                 "nodes": [{"ref": "R_DEBUG", "xy": [100.0, 50.0]}]},
            ]}
    s = dict_to_sexp(data)
    assert s.strip().startswith("(kicadstamp-config")
    assert "(trees" in s
    back = sexp_to_dict(s)
    assert back == _strip_defaults(data)
    assert back["trees"][0]["nodes"][0]["ref"] == "AMS1117_REG"
    assert back["trees"][1]["anchor"] == {"origin": True}


def test_load_config_sexp_trees(tmp_path):
    p = _write(tmp_path, "cfg.sexp", SEXP_WITH_TREES)
    cfg, _ = load_config(str(p))
    assert [t.name for t in cfg.trees] == ["power_tree", "misc"]
    assert cfg.trees[0].nodes[0].ref == "AMS1117_REG"
    assert cfg.trees[0].nodes[0].children[0].ref == "C_OUT"
    assert cfg.trees[0].nodes[1].polar == (3.0, 45.0)
    assert cfg.trees[1].anchor.is_origin is True


def test_load_config_yaml_trees_is_fatal(tmp_path):
    """The old YAML shape of the same trees section is no longer a config the
    core reads: a .yaml root is a fatal with the sexp_config_convert hint
    (2026-08-28, core_yaml_removal) — never a silent YAML load."""
    p = tmp_path / "cfg.yaml"
    p.write_text("trees:\n  - name: power_tree\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="YAML config support has been removed"):
        load_config(str(p))


# ── include: merging of the trees list section (FORK-4) ────────────────────

def test_include_merges_trees_sections(tmp_path):
    _write(tmp_path, "sub.sexp", dict_to_sexp({
        "trees": [{"name": "sub_tree", "anchor": {"origin": True},
                   "nodes": [{"ref": "SUB_N", "xy": [0.0, 0.0]}]}],
    }))
    _write(tmp_path, "root.sexp", dict_to_sexp({
        "trees": [{"name": "root_tree", "anchor": {"origin": True},
                   "nodes": [{"ref": "ROOT_N", "xy": [0.0, 0.0]}]}],
        "include": ["sub.sexp"],
    }))
    cfg, _ = load_config(str(tmp_path / "root.sexp"))
    assert [t.name for t in cfg.trees] == ["root_tree", "sub_tree"]


def test_duplicate_tree_name_across_include_graph_fatal(tmp_path):
    """The same tree name arriving via include: from another file is a fatal
    (unique names across the WHOLE include graph, not per file)."""
    _write(tmp_path, "sub.sexp", dict_to_sexp({
        "trees": [{"name": "same", "anchor": {"origin": True},
                   "nodes": [{"ref": "A", "xy": [0.0, 0.0]}]}],
    }))
    _write(tmp_path, "root.sexp", dict_to_sexp({
        "trees": [{"name": "same", "anchor": {"origin": True},
                   "nodes": [{"ref": "B", "xy": [1.0, 1.0]}]}],
        "include": ["sub.sexp"],
    }))
    with pytest.raises(ValidationError, match="duplicate"):
        load_config(str(tmp_path / "root.sexp"))


def test_duplicate_node_ref_across_include_graph_fatal(tmp_path):
    """A record's ref appearing in a node of a tree in ANOTHER included file is
    a fatal (single seen_refs shared across the whole graph)."""
    _write(tmp_path, "sub.sexp", dict_to_sexp({
        "trees": [{"name": "t1", "anchor": {"origin": True},
                   "nodes": [{"ref": "DUP", "xy": [0.0, 0.0]}]}],
    }))
    _write(tmp_path, "root.sexp", dict_to_sexp({
        "trees": [{"name": "t2", "anchor": {"origin": True},
                   "nodes": [{"ref": "DUP", "xy": [1.0, 1.0]}]}],
        "include": ["sub.sexp"],
    }))
    with pytest.raises(ValidationError, match="already has a node"):
        load_config(str(tmp_path / "root.sexp"))


# ── tools/trees_to_config.py migrator (FORK-5 §5.3) ────────────────────────

def test_migrator_moves_old_trees_into_root_config(tmp_path):
    old = tmp_path / "old.trees"
    save_trees(str(old), [_sample_tree()])
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"layer": "B.Cu"}), encoding="utf-8")

    trees = migrate(root, [old])
    assert [t["name"] for t in trees] == ["power_tree"]

    from kicadstamp.config_writer import read_data, write_data
    data = read_data(root)
    data["trees"] = trees
    write_data(root, data)

    cfg, _ = load_config(str(root))
    assert [t.name for t in cfg.trees] == ["power_tree"]
    assert cfg.trees[0].nodes[0].ref == "AMS1117_REG"


def test_migrator_missing_trees_file_raises(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({}), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="not found"):
        migrate(root, [tmp_path / "nope.trees"])


def test_migrator_self_verify_catches_duplicate_tree_name(tmp_path, monkeypatch, capsys):
    """Two old .trees files sharing a tree name: main() must NOT report
    success (non-zero), because the merged root config fails load_config()
    (duplicate name across the merged trees: section). Root is still written
    and a backup exists — the self-verify does not roll back (recovery is the
    .bak from backup_file()), matching tools/sexp_config_convert.py's "never
    silently report success on a broken result" discipline."""
    old_a = tmp_path / "a.trees"
    save_trees(str(old_a), [_sample_tree()])  # name="power_tree"

    # Same tree name, but node refs unique within the merge — so the failure
    # is the duplicate-NAME check, not a node-ref collision.
    twin = Tree(name="power_tree", anchor=TreeAnchor(ref="CONN_OTHER", is_origin=False),
                nodes=[TreeNode(ref="N_OTHER", kind=None, xy=(0.0, 0.0), polar=None,
                                rotation=0.0, name=None, group=None, children=[])])
    old_b = tmp_path / "b.trees"
    save_trees(str(old_b), [twin])

    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"layer": "B.Cu"}), encoding="utf-8")

    monkeypatch.setattr(sys, "argv",
                        ["trees_to_config", str(root), str(old_a), str(old_b)])
    assert main() == 1

    # No rollback: the (broken) root is still written ...
    assert root.exists()
    # ... but the pre-migration root was backed up for recovery.
    assert list(tmp_path.glob("root.sexp.bak.*"))

    err = capsys.readouterr().err
    assert "fails to load" in err
    assert "duplicate" in err
    assert "backup" in err
