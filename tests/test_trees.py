# tests/test_trees.py
"""Tests for kicadstamp/trees.py — the pure syntactic s-expr "trees" loader
(no YAML, no Config, no record linking — that's link_trees, Phase 3).

Every fatal rule is tested on its own with a UNIQUE message match, not a
bare `pytest.raises(ValidationError)`: a too-strict exception assert can
pass by catching the WRONG validation error, which masks the real cause.
"""
import pytest

from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import Tree, TreeAnchor, TreeNode, load_trees, save_trees


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


def test_anchor_external_marker_is_parsed(tmp_path):
    """(anchor (ref "...") (external)) -> a live-board-only refdes anchor:
    is_external True, is_origin False — the anchor's OWN collision shield,
    symmetric to TreeNode's kind="external" (note_2026_08_28_...)."""
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (ref "U_FPGA") (external))
    (node (ref "R1") (xy 1.0 2.0))))"""
    a = load_trees(_write(tmp_path, text))[0].anchor
    assert a.ref == "U_FPGA"
    assert a.is_origin is False
    assert a.is_external is True


def test_anchor_origin_external_mutually_exclusive(tmp_path):
    """(origin) and (external) on one anchor is contradictory — fatal, never
    a silent pick (same discipline as the rest of the grammar)."""
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (anchor (origin) (external))
    (node (ref "R1") (xy 1.0 2.0))))"""
    with pytest.raises(ValidationError, match="mutually exclusive"):
        load_trees(_write(tmp_path, text))


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


def test_tree_missing_anchor_is_auto(tmp_path):
    """A tree with NO (anchor ...) is no longer fatal — it gets an AUTO anchor
    (2026-08-31, plan tree_self_anchor_from_entity): the base is derived at
    materialization time from the tree's own root Entity placement's cell zero
    slot. An explicit anchor is never required syntactically anymore."""
    text = """(kicadstamp-trees
  (tree
    (name "t")
    (node (ref "R1") (xy 1 1))))"""
    trees = load_trees(_write(tmp_path, text))
    assert len(trees) == 1
    assert trees[0].anchor.is_auto is True
    assert trees[0].anchor.ref is None
    assert trees[0].anchor.role is None
    assert trees[0].anchor.is_origin is False


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


# ── save_trees: inverse serializer + round-trip ────────────────────────────

def _tree_obj(name="t", anchor=None, nodes=None):
    return Tree(name=name,
                anchor=anchor if anchor is not None else TreeAnchor(ref=None, is_origin=True),
                nodes=nodes if nodes is not None else [])


def _node_obj(ref, kind=None, xy=None, polar=None, rotation=0.0, name=None,
              group=None, children=None):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=polar, rotation=rotation,
                    name=name, group=group, children=children or [])


def test_save_trees_roundtrip_origin_anchor(tmp_path):
    """A tree with an origin anchor, an xy node and a nested child round-trips
    to the identical dataclass list via save_trees -> load_trees."""
    trees = [_tree_obj(name="t1", nodes=[
        _node_obj(ref="R1", kind="clone", xy=(5.0, 2.0), rotation=45.0,
                  name="cap_in", group="power"),
        _node_obj(ref="R2", polar=(3.0, 45.0),
                  children=[_node_obj(ref="R2C", xy=(1.0, 0.0))]),
    ])]
    path = tmp_path / "rt.trees"
    save_trees(str(path), trees)
    assert load_trees(str(path)) == trees


def test_save_trees_roundtrip_ref_anchor(tmp_path):
    """A ref anchor (not origin) serializes as (anchor (ref ...)) and
    round-trips to the same TreeAnchor."""
    trees = [_tree_obj(name="t1",
                       anchor=TreeAnchor(ref="CONN_PM5V", is_origin=False),
                       nodes=[_node_obj(ref="R1", xy=(1.0, 2.0))])]
    path = tmp_path / "rt.trees"
    save_trees(str(path), trees)
    assert load_trees(str(path)) == trees


def test_save_trees_roundtrip_external_anchor(tmp_path):
    """An external (live-board-only) anchor serializes as (anchor (ref ...)
    (external)) and round-trips to the same TreeAnchor — the collision shield
    must survive save -> load (note_2026_08_28_tree_anchor_name_collision)."""
    trees = [_tree_obj(name="t1",
                       anchor=TreeAnchor(ref="U_FPGA", is_origin=False,
                                         is_external=True),
                       nodes=[_node_obj(ref="R1", xy=(1.0, 2.0))])]
    path = tmp_path / "rt.trees"
    save_trees(str(path), trees)
    assert "(external)" in path.read_text(encoding="utf-8")
    assert load_trees(str(path)) == trees


def test_save_trees_roundtrip_multiple_trees(tmp_path):
    """Several trees in one file keep their names and anchors."""
    trees = [
        _tree_obj(name="first", nodes=[_node_obj(ref="A", xy=(0.0, 0.0))]),
        _tree_obj(name="second",
                  anchor=TreeAnchor(ref="BASE", is_origin=False),
                  nodes=[_node_obj(ref="B", xy=(10.0, 20.0))]),
    ]
    path = tmp_path / "rt.trees"
    save_trees(str(path), trees)
    assert load_trees(str(path)) == trees


def test_save_trees_omits_default_value_fields(tmp_path):
    """Default-valued node fields (kind None, rotation 0.0, name None,
    group None) must NOT be written to the file — otherwise a round-trip test
    could 'pass' on serializer noise that the parser merely re-defaults.
    (The tree-level `(name "t1")` is required by the grammar and IS written —
    this checks the NODE's optional fields are omitted.)"""
    trees = [_tree_obj(name="t1", nodes=[_node_obj(ref="R1", xy=(1.0, 2.0))])]
    path = tmp_path / "rt.trees"
    save_trees(str(path), trees)
    text = path.read_text(encoding="utf-8")
    assert "(kind" not in text
    assert "(rotation" not in text
    assert "(group" not in text
    assert "(polar" not in text
    assert '(name "R1")' not in text  # the node's own name: would only appear if written
    # The only non-default node fields written are ref and xy.
    assert '(ref "R1")' in text
    assert "(xy 1.0 2.0)" in text
    assert '(name "t1")' in text  # the tree-level name is required and present


def test_save_trees_writes_non_default_fields(tmp_path):
    """When set, kind/rotation/name/group ARE written — the omission is per
    default value, not wholesale."""
    trees = [_tree_obj(name="t1", nodes=[
        _node_obj(ref="R1", kind="external", rotation=90.0,
                  name="ext", group="g"),
    ])]
    path = tmp_path / "rt.trees"
    save_trees(str(path), trees)
    text = path.read_text(encoding="utf-8")
    assert "(kind external)" in text
    assert "(rotation 90.0)" in text
    assert '(name "ext")' in text
    assert '(group "g")' in text
