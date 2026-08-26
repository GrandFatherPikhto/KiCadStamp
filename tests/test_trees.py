# tests/test_trees.py
"""Tests for kicadstamp/trees.py — the pure syntactic s-expr "trees" loader
(no YAML, no Config, no record linking — that's link_trees, Phase 3).

Every fatal rule is tested on its own with a UNIQUE message match, not a
bare `pytest.raises(ValidationError)`: a too-strict exception assert can
pass by catching the WRONG validation error, which masks the real cause.
"""
import pytest

from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import Tree, TreeAnchor, TreeNode, load_trees


def _write(tmp_path, text, name="trees.trees"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


# The working example straight from the grammar design doc — it must parse
# without error and round-trip into the expected dataclass shape.
GRAMMAR_EXAMPLE = """(kicadstamp-trees
  (version 1)
  (tree
    (name "power_tree")
    (anchor (ref "CONN_PM5V"))
    (node
      (ref "AMS1117_REG")
      (kind clone)
      (xy 5.0 2.0)
      (rotation 0)
      (node (ref "C_OUT") (xy 1.0 0)))
    (node (ref "R_AROUND") (polar 3.0 45.0)))
  (tree
    (name "misc")
    (anchor (origin))
    (node (ref "R_DEBUG") (xy 100.0 50.0))))"""


# ── happy path ─────────────────────────────────────────────────────────────

def test_load_trees_parses_the_grammar_example(tmp_path):
    trees = load_trees(_write(tmp_path, GRAMMAR_EXAMPLE))
    assert len(trees) == 2

    power, misc = trees
    assert isinstance(power, Tree)
    assert power.name == "power_tree"
    assert power.anchor == TreeAnchor(ref="CONN_PM5V", is_origin=False)

    assert len(power.nodes) == 2
    ams, r_around = power.nodes
    assert isinstance(ams, TreeNode)
    assert ams.ref == "AMS1117_REG"
    assert ams.kind == "clone"
    assert ams.xy == (5.0, 2.0)
    assert ams.polar is None
    assert ams.rotation == 0.0
    assert len(ams.children) == 1
    assert ams.children[0].ref == "C_OUT"
    assert ams.children[0].xy == (1.0, 0.0)
    assert ams.children[0].kind is None

    assert r_around.ref == "R_AROUND"
    assert r_around.xy is None
    assert r_around.polar == (3.0, 45.0)

    assert misc.name == "misc"
    assert misc.anchor.is_origin is True
    assert misc.anchor.ref is None
    assert len(misc.nodes) == 1
    assert misc.nodes[0].ref == "R_DEBUG"
    assert misc.nodes[0].xy == (100.0, 50.0)


def test_anchor_ref_vs_origin_are_distinguished(tmp_path):
    """(anchor (ref ...)) -> ref + is_origin False; (anchor (origin)) ->
    ref None + is_origin True. The regression risk: treating (origin) as a
    ref or failing to clear ref on origin."""
    trees = load_trees(_write(tmp_path, GRAMMAR_EXAMPLE))
    assert trees[0].anchor.is_origin is False and trees[0].anchor.ref == "CONN_PM5V"
    assert trees[1].anchor.is_origin is True and trees[1].anchor.ref is None


def test_default_name_and_group_are_none(tmp_path):
    """name/group are optional UI labels — absent means None (not "")."""
    trees = load_trees(_write(tmp_path, GRAMMAR_EXAMPLE))
    assert trees[0].nodes[0].name is None
    assert trees[0].nodes[0].group is None


def test_name_and_group_labels_are_parsed(tmp_path):
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (ref "R1") (xy 1.0 2.0) (name "cap_in") (group "power"))))"""
    trees = load_trees(_write(tmp_path, text))
    n = trees[0].nodes[0]
    assert n.name == "cap_in"
    assert n.group == "power"


# ── fatal: duplicate tree names (rule 1) ──────────────────────────────────

def test_duplicate_tree_names_are_fatal(tmp_path):
    text = """(kicadstamp-trees
  (tree (name "t") (anchor (origin)) (node (ref "A") (xy 0 0)))
  (tree (name "t") (anchor (origin)) (node (ref "B") (xy 0 0))))"""
    with pytest.raises(ValidationError, match="duplicate tree name"):
        load_trees(_write(tmp_path, text))


# ── fatal: a ref appears in at most one node (rule 2) ─────────────────────

def test_same_ref_in_two_nodes_is_fatal(tmp_path):
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (ref "R1") (xy 0 0))
    (node (ref "R1") (xy 1 1))))"""
    with pytest.raises(ValidationError, match="already has a node"):
        load_trees(_write(tmp_path, text))


def test_same_ref_in_nested_and_top_level_nodes_is_fatal(tmp_path):
    """Rule 2 is global across the whole file, not per-branch — a ref nested
    under one node and reused as another node's sibling is still a conflict."""
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (ref "R1") (xy 0 0) (node (ref "R2") (xy 1 0)))
    (node (ref "R2") (xy 2 0))))"""
    with pytest.raises(ValidationError, match="already has a node"):
        load_trees(_write(tmp_path, text))


def test_ref_reused_as_tree_anchor_is_allowed(tmp_path):
    """The ONE legal reuse (rule 2): the same ref may be a tree anchor — an
    anchor is a base, not something the tree "places". Must NOT be fatal."""
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (ref "R1"))
    (node (ref "R1") (xy 1 1))
    (node (ref "R2") (xy 2 2))))"""
    trees = load_trees(_write(tmp_path, text))
    assert trees[0].anchor.ref == "R1"
    assert trees[0].nodes[0].ref == "R1"


# ── fatal: xy / polar mutually exclusive, each exactly 2 numbers (rule 3) ─

def test_xy_and_polar_together_is_fatal(tmp_path):
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (ref "R1") (xy 1 1) (polar 3.0 45.0))))"""
    with pytest.raises(ValidationError, match="mutually exclusive"):
        load_trees(_write(tmp_path, text))


def test_xy_with_wrong_number_count_is_fatal(tmp_path):
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (ref "R1") (xy 1))))"""
    with pytest.raises(ValidationError, match="exactly two numbers"):
        load_trees(_write(tmp_path, text))


def test_xy_with_non_numeric_value_is_fatal(tmp_path):
    """A Symbol in place of a number (e.g. an unquoted name) is not a valid
    coordinate."""
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (ref "R1") (xy 1 foo))))"""
    with pytest.raises(ValidationError, match="exactly two numbers"):
        load_trees(_write(tmp_path, text))


def test_polar_with_three_numbers_is_fatal(tmp_path):
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (ref "R1") (polar 3.0 45.0 6.0))))"""
    with pytest.raises(ValidationError, match="exactly two numbers"):
        load_trees(_write(tmp_path, text))


# ── fatal: kind must be one of the whitelist (rule 4) ─────────────────────

def test_invalid_kind_is_fatal(tmp_path):
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (ref "R1") (kind bogus) (xy 1 1))))"""
    with pytest.raises(ValidationError, match="invalid kind"):
        load_trees(_write(tmp_path, text))


def test_all_valid_kinds_are_accepted(tmp_path):
    for kind in ("clone", "rule", "coordinate", "point", "external"):
        text = f"""(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (ref "R1") (kind {kind}) (xy 1 1))))"""
        trees = load_trees(_write(tmp_path, text, name=f"{kind}.trees"))
        assert trees[0].nodes[0].kind == kind


# ── fatal: rotation must be a number ──────────────────────────────────────

def test_non_numeric_rotation_is_fatal(tmp_path):
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (ref "R1") (xy 1 1) (rotation sideways))))"""
    with pytest.raises(ValidationError, match="rotation must be a number"):
        load_trees(_write(tmp_path, text))


# ── fatal: structural requirements on the file / tree / node ──────────────

def test_non_trees_top_level_is_fatal(tmp_path):
    text = """(something-else (a 1))"""
    with pytest.raises(ValidationError, match="top level must be"):
        load_trees(_write(tmp_path, text))


def test_tree_missing_anchor_is_fatal(tmp_path):
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (node (ref "R1") (xy 1 1))))"""
    with pytest.raises(ValidationError, match="missing an \\(anchor"):
        load_trees(_write(tmp_path, text))


def test_tree_missing_name_is_fatal(tmp_path):
    text = """(kicadstamp-trees
  (tree
    (anchor (origin))
    (node (ref "R1") (xy 1 1))))"""
    with pytest.raises(ValidationError, match="missing a \\(name"):
        load_trees(_write(tmp_path, text))


def test_node_missing_ref_is_fatal(tmp_path):
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin))
    (node (xy 1 1))))"""
    with pytest.raises(ValidationError, match="missing a \\(ref"):
        load_trees(_write(tmp_path, text))


# ── regression guard: tree `name` not clobbered across trees ──────────────

def test_multiple_trees_keep_their_own_names(tmp_path):
    """The classic bug from the first load_trees attempt: a shared `name`
    variable clobbered by the parse loop, so every tree came back with the
    LAST name. Two trees with distinct names must keep them distinct."""
    text = """(kicadstamp-trees
  (tree (name "first") (anchor (origin)) (node (ref "A") (xy 0 0)))
  (tree (name "second") (anchor (origin)) (node (ref "B") (xy 0 0))))"""
    trees = load_trees(_write(tmp_path, text))
    assert [t.name for t in trees] == ["first", "second"]
