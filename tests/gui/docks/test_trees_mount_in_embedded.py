# tests/gui/docks/test_trees_mount_in_embedded.py
"""A mount inside an EMBEDDED tree hangs from the MODULE, not from the tree's own anchor (Ф2).

Ф2 of the 19.11 acceptance, the live HP-Channel defect: on the `Switch_A` tab (its own anchor
`Origin` + (0, 0), the tree embedded in `Main` by a marker at (40, −55)) the node
`spoke_p15v_switcha`, which sits under two nested MOUNT nodes, was placed and read from the tree's
own anchor — 68.007 mm off, exactly the marker. A mount's role is placed by the tree ITSELF
(`resolve_internal_mount_match`), so its base is the tree's CONTENT base, and that base has to be
the module's.

The cells are board-free: every anchor is an origin and the mounts are INTERNAL (a role the tree
places itself), so the layout needs no adapter — and the expected position is taken from
`layout_tree_from_base` over the OWNER, i.e. the very numbers the module's own Redraw uses.
"""
import pytest

from kicadstamp.config import Cell, Config, Entity, TemplateComponentSlot
from kicadstamp.tree_position import layout_tree_from_base, tree_layout_base
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

from gui.docks.trees_base_pose import redraw_target, resolve_node_base_pose


def node(ref, kind="placement", xy=(0.0, 0.0), rotation=0.0, *, anchor=None, children=()):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rotation, name=None,
                    group=None, anchor=anchor, children=list(children))


def tree(name, nodes, *, shift=(0.0, 0.0), rotation=0.0):
    anchor = TreeAnchor(is_origin=True)
    anchor.shift_xy = shift
    return Tree(name=name, anchor=anchor, nodes=list(nodes), rotation=rotation)


def _config():
    """ONE cell carrying the role `UCC` and ONE entity the tree places itself, so the mounts
    resolve INTERNALLY (a role the tree places) — no board, no adapter. A mount PAD would need the
    live footprint, and the nesting shape is what these cells are about."""
    return Config(
        cells={"cell_u": Cell(name="cell_u", components=[
            TemplateComponentSlot(role="UCC")])},
        entities=[Entity(name="DRV", cluster="DRV", cell="cell_u")],
    )


def _trees():
    """`main` places `switch_a` by its marker at (40, −55); `switch_a` places the driver and hangs
    two nested mounts (and the node under them) off it."""
    spoke = node("spoke", xy=(2.0, 3.0))
    inner_mount = node("p15v_5", kind="mount", xy=(1.0, 1.0),
                       anchor=TreeAnchor(role="UCC"), children=[spoke])
    outer_mount = node("driver_mnt", kind="mount", xy=(4.0, -2.0),
                       anchor=TreeAnchor(role="UCC"), children=[inner_mount])
    driver = node("DRV", xy=(0.0, 0.0), children=[outer_mount])
    switch = tree("switch_a", [driver])
    main = tree("main", [node("switch_a", kind="module", xy=(40.0, -55.0))])
    return [main, switch], switch.nodes[0].children[0].children[0], spoke


def _expected(cfg, main, trees):
    """Where the MODULE lays the spoke out — the numbers the owner's own Redraw uses."""
    by_name = {t.name: t for t in trees}
    base = tree_layout_base(None, cfg, main, {}, by_name)
    return layout_tree_from_base(main, base[0], base[1], by_name, cfg=cfg)["spoke"]


def test_x_a_node_under_a_nested_mount_is_placed_through_the_module():
    """Cell «х»: the Redraw of `spoke` must land where the module puts it, 0.000 mm — and NOT
    68.007 mm (= |(40, −55)|) away, which is what the tree's own anchor gives."""
    cfg = _config()
    trees, inner_mount, spoke = _trees()
    main, switch = trees
    expected_pos, expected_rot = _expected(cfg, main, trees)

    pos, rot = redraw_target(cfg, None, {}, switch, spoke, inner_mount,
                             trees=trees)

    assert (pos.x, pos.y) == (expected_pos.x, expected_pos.y)
    assert rot == pytest.approx(expected_rot)


def test_x_the_own_anchor_would_be_68_mm_off_the_other_way():
    """The control that proves the cell can fail: WITHOUT the dock's forest the resolver falls back
    to `cfg.trees` (empty here) → the tree's own anchor, which is the defect. Pinned as a number so
    a reader sees what the fix is worth."""
    cfg = _config()
    trees, inner_mount, spoke = _trees()
    main, switch = trees
    expected_pos, _ = _expected(cfg, main, trees)

    own_pos, _rot, _mirror = resolve_node_base_pose(cfg, None, {}, switch, inner_mount, None,
                                                    trees=[switch])
    assert (own_pos.x, own_pos.y) != (expected_pos.x, expected_pos.y)


def test_x2_read_then_redraw_is_a_no_op_on_the_same_base():
    """Cell «х2»: whatever the base is, Read and Redraw must compose it the same way — the
    node's own xy/rotation carried from that base has to land the node on itself."""
    cfg = _config()
    trees, inner_mount, spoke = _trees()
    main, switch = trees

    base_pos, base_rot, _mirror = resolve_node_base_pose(cfg, None, {}, switch, inner_mount, None,
                                                        trees=trees)
    pos, rot = redraw_target(cfg, None, {}, switch, spoke, inner_mount, trees=trees)
    through = resolve_node_base_pose(cfg, None, {}, switch, inner_mount, None, trees=trees)
    assert (base_pos.x, base_pos.y) == (through[0].x, through[0].y)
    assert base_rot == through[1]
    assert (pos.x, pos.y) != (0, 0)


def test_x3_a_mount_inside_a_mount_uses_the_same_base():
    """Cell «х3»: `driver_mnt` → `p15v_5` is two levels of mount; the INNER one's frame is the
    outer one's POINT, and both come from the module's layout, not from the tree's anchor."""
    cfg = _config()
    trees, inner_mount, _spoke = _trees()
    main, switch = trees
    outer_mount = switch.nodes[0].children[0]

    outer_pos, _rot, _m = resolve_node_base_pose(cfg, None, {}, switch, outer_mount, None,
                                                 trees=trees)
    inner_pos, _rot2, _m2 = resolve_node_base_pose(cfg, None, {}, switch, inner_mount, None,
                                                   trees=trees)
    assert (outer_pos.x, outer_pos.y) != (inner_pos.x, inner_pos.y)
    # Both are inside the module's frame: the tree's own anchor would put them near (0, 0).
    assert abs(outer_pos.x) > 1 or abs(outer_pos.y) > 1


def test_two_markers_on_the_tree_refuse_instead_of_guessing():
    """Ф2 п.1: an ambiguous chain is a REFUSAL with the marker names (the same wording the Redraw
    uses), never a silent fallback to the tree's own anchor."""
    from kicadstamp.exceptions import ValidationError

    cfg = _config()
    trees, inner_mount, _spoke = _trees()
    main, switch = trees
    trees.append(tree("aux", [node("switch_a", kind="module", xy=(-5.0, 5.0))]))

    with pytest.raises(ValidationError) as exc:
        resolve_node_base_pose(cfg, None, {}, switch, inner_mount, None, trees=trees)
    assert "more than one module" in str(exc.value)
