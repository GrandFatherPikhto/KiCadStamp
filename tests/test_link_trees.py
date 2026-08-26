# tests/test_link_trees.py
"""Tests for kicadstamp/link_trees.py — the trees<->Config name-linking pass.

Pattern: build Record-bearing Config dataclasses directly (no YAML files, no
live load_config) + a small .trees string through load_trees, then assert on
the LinkedTree/LinkedNode/LinkedAnchor shape. Mirror of
tests/test_anchor_graph.py, per design_2026_08_26_link_trees.md's test plan.
"""
import pytest

from kicadstamp.config import (
    Config, ClonePlacement, CoordinatePlacement, NetTrace, Point, Rule,
)
from kicadstamp.exceptions import ValidationError
from kicadstamp.link_trees import link_trees
from kicadstamp.trees import load_trees


def _tree(text, name="trees.trees", tmp_path=None):
    """Parse a .trees string body (sans the outer kicadstamp-trees wrapper)
    into a list of Tree dataclasses."""
    assert tmp_path is not None
    path = tmp_path / name
    path.write_text("(kicadstamp-trees\n" + text + ")", encoding="utf-8")
    return load_trees(str(path))


# A minimal clone_placements/rules/coordinate_placements/points cfg with
# distinct, unambiguous names.
def _cfg(**overrides):
    cfg = Config(
        clone_placements=[
            ClonePlacement(cluster="CL_A", cell="c", xy=(0.0, 0.0)),
            ClonePlacement(cluster="CL_B", cell="c", xy=(1.0, 1.0)),
        ],
        rules=[
            Rule(net="GND", spokes=[]),
        ],
        coordinate_placements=[
            CoordinatePlacement(cluster="CP_C", role="CP_R"),
        ],
        points={
            "pnt": Point(name="pnt"),
        },
        net_traces=[
            NetTrace(net="NT_NET", anchor_role="ROLE_X"),
        ],
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ── node resolution ────────────────────────────────────────────────────────

def test_node_resolved_by_explicit_kind(tmp_path):
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (kind clone) (xy 1 2)))',
        tmp_path=tmp_path)
    linked = link_trees(cfg, trees)[0]
    n = linked.nodes[0]
    assert n.record is not None
    assert n.record.kind == "clone"
    assert n.record.name == "CL_A"
    assert n.is_external is False


def test_node_explicit_kind_not_found_is_fatal(tmp_path):
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "NO_SUCH") (kind clone) (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="not found in config"):
        link_trees(cfg, trees)


def test_node_auto_search_single_match(tmp_path):
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_B") (xy 1 2)))',  # no kind -> auto-search
        tmp_path=tmp_path)
    linked = link_trees(cfg, trees)[0]
    n = linked.nodes[0]
    assert n.record is not None
    assert n.record.kind == "clone"
    assert n.is_external is False


def test_node_auto_search_zero_matches_is_fatal(tmp_path):
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "NOWHERE") (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="kind external"):
        link_trees(cfg, trees)


def test_node_auto_search_ambiguous_is_fatal(tmp_path):
    # Same name in two different sections -> ambiguity -> fatal with a hint.
    cfg = _cfg(rules=[
        Rule(net="AMB", spokes=[]),
    ], clone_placements=[
        ClonePlacement(cluster="AMB", cell="c", xy=(0.0, 0.0)),
    ])
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "AMB") (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="ambiguous"):
        link_trees(cfg, trees)


def test_node_kind_external_never_touches_config(tmp_path):
    # A name that WOULD resolve to a real record — but (kind external) must
    # skip config resolution entirely (proves the bypass, not a "not found").
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (kind external) (xy 1 2)))',
        tmp_path=tmp_path)
    linked = link_trees(cfg, trees)[0]
    n = linked.nodes[0]
    assert n.record is None
    assert n.is_external is True


def test_node_auto_search_only_scans_4_placeable_kinds(tmp_path):
    # Name matches only a net_trace record (not a placeable section) -> a
    # kind-less node must NOT resolve to it -> fatal "not found".
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "NT_NET") (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="kind external"):
        link_trees(cfg, trees)


# ── anchor resolution ─────────────────────────────────────────────────────

def test_anchor_resolved_when_name_in_config(tmp_path):
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (ref "CL_A"))\n'
        '      (node (ref "CL_B") (xy 1 2)))',
        tmp_path=tmp_path)
    linked = link_trees(cfg, trees)[0]
    a = linked.anchor
    assert a.record is not None
    assert a.record.name == "CL_A"
    assert a.is_external is False
    assert a.is_origin is False


def test_anchor_zero_matches_is_silently_external(tmp_path):
    # An anchor pointing at a live-board component NOT in config is legal —
    # silently external, NO exception (asymmetric with nodes).
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (ref "CONN_PM5V"))\n'
        '      (node (ref "CL_B") (xy 1 2)))',
        tmp_path=tmp_path)
    linked = link_trees(cfg, trees)[0]
    a = linked.anchor
    assert a.record is None
    assert a.is_external is True
    assert a.is_origin is False


def test_anchor_ambiguous_is_fatal(tmp_path):
    cfg = _cfg(rules=[
        Rule(net="AMB", spokes=[]),
    ], clone_placements=[
        ClonePlacement(cluster="AMB", cell="c", xy=(0.0, 0.0)),
    ])
    trees = _tree(
        '(tree (name "t") (anchor (ref "AMB"))\n'
        '      (node (ref "CL_B") (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="ambiguous"):
        link_trees(cfg, trees)


def test_anchor_origin_never_resolves(tmp_path):
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_B") (xy 1 2)))',
        tmp_path=tmp_path)
    linked = link_trees(cfg, trees)[0]
    a = linked.anchor
    assert a.record is None
    assert a.is_external is False
    assert a.is_origin is True


# ── FORK-1: inline-anchor conflict on a tree-placed record ────────────────

def test_fork1_inline_anchor_ref_is_fatal(tmp_path):
    cfg = _cfg(clone_placements=[
        ClonePlacement(cluster="CL_A", cell="c", xy=(0.0, 0.0), anchor_ref="IC1"),
    ])
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="inline anchor"):
        link_trees(cfg, trees)


def test_fork1_inline_anchor_role_is_fatal(tmp_path):
    cfg = _cfg(rules=[
        Rule(net="GND", spokes=[], anchor_role="MCU"),
    ])
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "GND") (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="inline anchor"):
        link_trees(cfg, trees)


def test_fork1_inline_anchor_point_is_fatal(tmp_path):
    cfg = _cfg(coordinate_placements=[
        CoordinatePlacement(cluster="CP_C", role="CP_R", anchor_point="pnt"),
    ])
    # Effective --only name of a CoordinatePlacement is cluster/role
    # (coordinate_placement_effective_name), so the tree ref must use it.
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CP_C/CP_R") (kind coordinate) (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="inline anchor"):
        link_trees(cfg, trees)


def test_fork1_anchor_origin_reads_from_obj(tmp_path):
    """anchor_origin is a Point-only field NOT copied onto Record — it must be
    read from rec.obj (getattr), or a point node with anchor_origin would pass
    the FORK-1 check silently."""
    cfg = _cfg(points={
        "pnt": Point(name="pnt", anchor_origin="grid"),
    })
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "pnt") (kind point) (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="inline anchor"):
        link_trees(cfg, trees)


def test_fork1_node_without_inline_anchor_is_fine(tmp_path):
    """A record with NO inline anchor (absolute xy placement) placed by a tree
    is the LEGAL case the tree layer exists to cover — not fatal."""
    cfg = _cfg()  # CL_A has no inline anchor
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2)))',
        tmp_path=tmp_path)
    linked = link_trees(cfg, trees)[0]
    assert linked.nodes[0].record is not None


# ── structure ─────────────────────────────────────────────────────────────

def test_nested_children_all_resolved(tmp_path):
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2)\n'
        '            (node (ref "CL_B") (xy 3 4)\n'
        '                  (node (ref "pnt") (kind point) (xy 5 6)))))',
        tmp_path=tmp_path)
    linked = link_trees(cfg, trees)[0]
    a = linked.nodes[0]
    assert a.record.name == "CL_A"
    assert a.children[0].record.name == "CL_B"
    assert a.children[0].children[0].record.name == "pnt"


def test_linked_tree_preserves_original_dataclasses(tmp_path):
    """Linked wrappers must hold the SAME Tree/TreeNode objects load_trees
    returned (wrap, not mutate) — identity check."""
    cfg = _cfg()
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2)))',
        tmp_path=tmp_path)
    linked = link_trees(cfg, trees)[0]
    assert linked.anchor.anchor is trees[0].anchor
    assert linked.nodes[0].node is trees[0].nodes[0]


def test_node_refers_retired_record_is_fatal(tmp_path):
    """retired: true records are dropped from build_records' index, so a node
    referring to one gets "not found" -> fatal (deliberate: retired = "does
    not exist on the board right now")."""
    cfg = _cfg(clone_placements=[
        ClonePlacement(cluster="GONE", cell="c", xy=(0.0, 0.0), retired=True),
    ])
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "GONE") (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="not found"):
        link_trees(cfg, trees)


def test_by_key_collision_is_fatal(tmp_path):
    """Two clone_placements with one effective name (clone-name uniqueness is
    NOT guaranteed by load_config) -> building by_key is fatal, not a silent
    overwrite."""
    cfg = _cfg(clone_placements=[
        ClonePlacement(cluster="DUP", cell="c", xy=(0.0, 0.0)),
        ClonePlacement(cluster="DUP", cell="c", xy=(1.0, 1.0)),
    ])
    trees = _tree(
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "DUP") (kind clone) (xy 1 2)))',
        tmp_path=tmp_path)
    with pytest.raises(ValidationError, match="multiple records with key"):
        link_trees(cfg, trees)


def test_multiple_trees_linked_independently(tmp_path):
    cfg = _cfg()
    trees = _tree(
        '(tree (name "one") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2)))\n'
        '(tree (name "two") (anchor (origin))\n'
        '      (node (ref "CL_B") (xy 3 4)))',
        tmp_path=tmp_path)
    linked = link_trees(cfg, trees)
    assert [t.name for t in linked] == ["one", "two"]
    assert linked[0].nodes[0].record.name == "CL_A"
    assert linked[1].nodes[0].record.name == "CL_B"
