# tests/gui/docks/test_trees_chain.py
"""The chain helper: the frame a tree's content lives in (Ф1/Ф2 of the 19.11 acceptance).

`chain_frame` is what makes "a tree that loses its last marker keeps its place" true for a NESTED
host, and later what makes a mount node inside an embedded tree hang from the module. Both fixes
depend on the same composition, so it is pinned here as numbers — and pinned to say None (never to
guess) the moment the chain is ambiguous.
"""
import pytest

from kicadstamp.forest_reread import to_polar
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

from gui.docks import trees_chain as tc


def node(ref, kind="clone", xy=(0.0, 0.0), polar=None, rotation=0.0):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=polar, rotation=rotation,
                    name=None, group=None, children=[])


def tree(name, nodes, *, shift=(0.0, 0.0), rotation=0.0, origin=True):
    anchor = TreeAnchor(is_origin=True) if origin else TreeAnchor(point="Origin")
    anchor.shift_xy = shift
    return Tree(name=name, anchor=anchor, nodes=list(nodes), rotation=rotation)


def _three(shift=(10.0, 5.0), rotation=90.0):
    power = tree("power", [])
    main = tree("main", [node("power", kind="module", xy=(6.0, -64.0))], shift=(1.0, 1.0))
    top = tree("top", [node("main", kind="module", xy=(20.0, 0.0))],
               shift=shift, rotation=rotation)
    return [top, main, power]


# ── a tree nobody places: its own anchor, unchanged ───────────────────────

def test_a_free_trees_frame_is_its_own_anchor():
    trees = _three()
    assert tc.chain_frame(trees, trees[0]) == ((10.0, 5.0), 90.0)


def test_the_root_of_a_free_tree_is_itself():
    trees = _three()
    assert tc.chain_root(trees, trees[0]) is trees[0]


# ── the composition, outermost first ──────────────────────────────────────

def test_the_frame_of_a_placed_tree_composes_shift_and_turn():
    """`top` at (10, 5) turned 90° with its marker at (20, 0): the content frame is (10, −15)
    turned 90° — Y-down +90° maps (20, 0) to (0, −20)."""
    trees = _three()
    assert tc.chain_frame(trees, trees[1]) == ((10.0, -15.0), 90.0)


def test_two_levels_compose_down_to_the_leaves():
    """A third level: (10, −15) turned 90° with the marker at (6, −64) → (−54, −21), still 90°."""
    trees = _three()
    assert tc.chain_frame(trees, trees[2]) == ((-54.0, -21.0), 90.0)


def test_the_root_of_a_nested_tree_is_the_outermost_one():
    trees = _three()
    assert tc.chain_root(trees, trees[2]) is trees[0]
    assert tc.chain_root(trees, trees[1]) is trees[0]


def test_a_POLAR_marker_composes_exactly_like_the_xy_one():
    """The composition goes through `node_position`, so a marker stored as (radius, angle) in the
    KiCad sign (`to_polar`, the same conversion the node form shows) must land on the same frame as
    the same marker stored as xy — a second formula here would drift from the layout."""
    trees = _three()
    polar_trees = _three()
    polar_trees[1].nodes = [node("power", kind="module", xy=None,
                                 polar=to_polar((6.0, -64.0)))]
    (px, py), prot = tc.chain_frame(polar_trees, polar_trees[2])
    (ax, ay), arot = tc.chain_frame(trees, trees[2])
    # The polar round trip costs nanometres, not millimetres.
    assert (px, py) == pytest.approx((ax, ay), abs=1e-3)
    assert prot == arot


# ── where the answer is None, never a guess ───────────────────────────────

def test_two_markers_on_the_tree_itself_are_refused():
    trees = _three()
    trees.append(tree("aux", [node("power", kind="module", xy=(-5.0, 5.0))]))
    assert tc.chain_frame(trees, trees[2]) is None


def test_two_markers_on_a_host_are_refused():
    trees = _three()
    trees.append(tree("aux", [node("main", kind="module", xy=(-5.0, 5.0))]))
    assert tc.chain_frame(trees, trees[2]) is None


def test_a_role_anchor_in_the_chain_is_refused():
    """A role/self/ref anchor resolves against the LIVE board — nothing about it can be composed
    without a read, so the helper says None instead of treating it as (0, 0)."""
    power = tree("power", [])
    role_owner = Tree(name="main", anchor=TreeAnchor(role="SW_ROLE"),
                      nodes=[node("power", kind="module", xy=(6.0, -64.0))])
    assert tc.chain_frame([role_owner, power], power) is None


def test_two_different_points_are_refused():
    power = tree("power", [], origin=False)          # point "Origin"
    other = tree("main", [], origin=True)            # origin
    other.nodes = [node("power", kind="module", xy=(6.0, -64.0))]
    assert tc.chain_frame([other, power], power) is None


def test_the_same_named_point_on_both_ends_is_accepted():
    power = tree("power", [], origin=False)
    main = tree("main", [], origin=False)
    main.nodes = [node("power", kind="module", xy=(6.0, -64.0))]
    assert tc.chain_frame([main, power], power) == ((6.0, -64.0), 0.0)
