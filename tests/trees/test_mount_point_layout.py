# tests/trees/test_mount_point_layout.py
"""What a mount node's OWN `xy`/`rotation` do to the REDRAW PLAN of its children
— boundary №1 of the Ч2 acceptance of
plan_2026_10_14_tree_group_move_and_mount_rebase (carried into Ч3 as its own
commit).

WHY THIS FILE EXISTS. Every layout cell that came before used a mount with
`xy = 0`, and with a zero offset the anchor frame and the node's POINT frame are
the same place — so nothing about APPLYING a mount's own offset and rotation was
observable at the layout level. The dock cells
(`tests/gui/docks/test_trees_dock_mount_child_frame.py`) did cover it, but they
measure it through the dock; a defect in the redraw's own composition would only
show up there by accident. Here the composition is measured where the redraw
does it (`layout_tree_from_anchor` -> `_node_path_pose` /
`layout_tree_from_base`), on the REAL functions, with no stand-in for any base.

THE NUMBERS ARE COMPUTED BY HAND and pinned as literals below — deliberately NOT
a copy of the product's arithmetic, which would agree with itself by
construction:

  * the tree anchor is `(origin)` -> base (0, 0) mm, angle 0°;
  * mount `M1`: a LIVE role anchor "FPGA" standing at (100, 50) mm with angle 0°;
    the node's OWN `xy = (5, 0)` mm and `rotation = 45°`;
  * child `C1`: `xy = (2, 1)` mm and `rotation = 30°`.

  Mount POINT = anchor + rotate((5, 0), 0°)          = (105, 50) mm
  Mount angle = anchor angle + own rotation          = 45°
  Child pose  = POINT + rotate((2, 1), 45°), angle 45° + 30° = 75°

  The rotation is the project's own (KiCad's Y-down formula, `Vector2.rotate`,
  a faithful port of `kipy`'s — NOT the textbook counter-clockwise one):
      x' = x·cosθ + y·sinθ,   y' = y·cosθ − x·sinθ
      rotate((2, 1), 45°) = (2·cos45 + 1·sin45, 1·cos45 − 2·sin45)
                          = (2.12132034, −0.70710678) mm
      -> (107.12132034, 49.29289322) mm at 75°.

  (A `rotation` sign error was NOT what this file was written to catch — it was
  caught instead when this literal was first computed by hand with the
  textbook formula and the cell failed: that is the hand-computation doing its
  job, so the formula is spelled out here rather than left to memory.)

  2 nm of tolerance on the position is the honest rounding noise: the pose
  travels mm -> nm through the storage grid and `rotate_local_offset` truncates
  each component once (`int(along_mm * MM)`). A FRAME mistake is millimetres
  wide, never nanometres — the defect measured in the dock cells read
  (7.12132, −0.707106) instead of (2, 1).
"""
from unittest.mock import MagicMock

import pytest
from kipy.board_types import FootprintInstance

from kicadstamp.config import Config
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.domain.geometry import Vector2
from kicadstamp.tree_position import (layout_tree_from_anchor, mount_node_base,
                                      node_position)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from kicadstamp.utils.units import MM

#: The live anchor component: (ref, role, x_mm, y_mm, angle_deg).
_ANCHOR = ("IC1", "FPGA", 100.0, 50.0, 0.0)

#: The mount node's OWN numbers against its anchor and its child's numbers
#: against the mount's POINT. Both are non-zero on purpose: with a zero `xy`
#: and `rotation` the two frames coincide and this file would measure nothing.
MOUNT_XY = (5.0, 0.0)
MOUNT_ROT = 45.0
CHILD_XY = (2.0, 1.0)
CHILD_ROT = 30.0

#: The hand-computed child pose in the redraw plan — see the module docstring.
EXPECTED_CHILD_MM = (107.12132034, 49.29289322)
EXPECTED_CHILD_DEG = 75.0


def _fake_adapter(anchor=_ANCHOR):
    """The minimal board the LIVE mount resolve asks: one footprint carrying the
    Role field. Same shape as the other layout cells' stand-in."""
    fp = MagicMock(spec=FootprintInstance)
    ref, role, x_mm, y_mm, angle = anchor
    fp.ref = ref
    fp.position = Vector2.from_xy_mm(x_mm, y_mm)
    fp.angle_deg = angle
    fp._pads = []

    adapter = MagicMock()
    adapter.get_footprints.return_value = [fp]
    adapter.has_field.return_value = True

    def _field(fp_, name):
        if name == "Role":
            return role
        if name == CLUSTER_FIELD_NAME:
            return None
        return None

    adapter.get_field_value.side_effect = _field
    adapter.get_selected_items.return_value = []
    adapter.get_pad_by_number.side_effect = lambda _fp, _num: None
    return adapter


def _tree():
    """The tree described in the module docstring: an origin anchor, ONE live
    mount carrying its own offset and rotation, and ONE child under it."""
    child = TreeNode(ref="C1", kind="external", xy=CHILD_XY, polar=None,
                     rotation=CHILD_ROT, name=None, group=None)
    mount = TreeNode(ref="M1", kind="mount", xy=MOUNT_XY, polar=None,
                     rotation=MOUNT_ROT, name=None, group=None,
                     children=[child],
                     anchor=TreeAnchor(role=_ANCHOR[1]))
    return Tree(name="t", anchor=TreeAnchor(is_origin=True), nodes=[mount])


def test_a_mounts_own_offset_and_rotation_place_its_child():
    """The redraw plan puts the mount's child at the HAND-COMPUTED pose:
    (105.70710678, 52.12132034) mm at 75°.

    Both halves are measured before the assertion, so a partial regression
    (position right, angle still the anchor's) cannot read as "one number off".
    Mutation check: build the mount's point WITHOUT its `xy`/`rotation`
    (`mount_node_base` alone, i.e. what the dock used to hand to children) and
    this cell fails by millimetres — the defect the parent plan fixed.
    """
    tree = _tree()
    adapter = _fake_adapter()
    cfg = Config(trees=[tree])

    laid = layout_tree_from_anchor(tree, {tree.name: tree}, adapter=adapter,
                                   cfg=cfg, sheet_names={})
    pos, rot = laid["C1"]

    position_mm = (pos.x / MM, pos.y / MM)
    position_ok = position_mm == pytest.approx(EXPECTED_CHILD_MM, abs=2e-6)
    angle_ok = rot == pytest.approx(EXPECTED_CHILD_DEG, abs=1e-9)
    assert position_ok and angle_ok, (
        "the mount's own xy and rotation must place its child at the "
        "hand-computed point — got {pos!r} / {rot!r}, expected {exp_pos!r} / "
        "{exp_rot!r}".format(pos=position_mm, rot=rot,
                             exp_pos=EXPECTED_CHILD_MM,
                             exp_rot=EXPECTED_CHILD_DEG))


def test_the_fixture_really_separates_the_two_frames():
    """The cell above is only worth anything while the two candidate frames
    DIFFER for this fixture: the mount's ANCHOR pose (the frame its own numbers
    are stored in) must be a different place for the child than the node's
    POINT (the frame its children hang from).

    Both poses come from the product's own functions (`mount_node_base` for the
    anchor, `node_position` for the composition the point is built from), so
    this cell cannot rot into a tautology of the test's own arithmetic."""
    tree = _tree()
    mount = tree.nodes[0]
    child = mount.children[0]
    adapter = _fake_adapter()
    cfg = Config(trees=[tree])

    anchor_pos, anchor_rot = mount_node_base(mount, tree, Vector2.from_xy(0, 0),
                                             0.0, adapter, cfg, {})
    through_anchor = node_position(child, anchor_pos, anchor_rot)
    child_pos, _child_rot = layout_tree_from_anchor(
        tree, {tree.name: tree}, adapter=adapter, cfg=cfg, sheet_names={})["C1"]

    assert (through_anchor.x, through_anchor.y) != (child_pos.x, child_pos.y), \
        "the fixture must separate the anchor frame from the node's point"
    assert mount.rotation != 0.0, \
        "…and it must turn the child's frame as well"
