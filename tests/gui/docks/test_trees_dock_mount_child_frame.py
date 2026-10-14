# tests/gui/docks/test_trees_dock_mount_child_frame.py
"""The FRAME a mount node's CHILDREN are stored in (the доделка of
plan_2026_10_14_tree_group_move_and_mount_rebase, boundary №3 of the Ч2
acceptance).

A mount node's own `xy`/`rotation` are stored against its ANCHOR; everything
UNDER it is stored against the node's POINT (anchor + xy, and its own
`rotation` on top) — that is exactly what the redraw composes
(`tree_position._node_path_pose` / `layout_tree_from_base`). The dock, however,
fed the ANCHOR pose to every child-facing read, so with `xy != 0` or
`rotation != 0` the number it wrote and the place the redraw read it back at
were frames apart.

These cells call the REAL functions — no `_resolve_node_base_pose` or
`_mount_anchor_pose` stand-in anywhere — so the two halves of the product answer
each other: the board state is built from the redraw plan (`layout_tree_from_
anchor`), and every path that reads or writes a child's numbers is asked to
agree with it. A frame mismatch shows up as millimetres, never as rounding.

WHAT IS PINNED DOWN HERE:

  * "Read current position" of a child returns the numbers it is STORED with;
  * a single and a group "Move to…" under a mount leave the children's PLANNED
    poses byte for byte where they were (the plan is the board);
  * "Change anchor from selection…" with the OLD support gone re-reads the
    children against the node's new POINT pose — the plan then reproduces where
    they really stand — and the Log prints the node's RESULTING axes (the new
    anchor's angle PLUS its own `rotation`);
  * "Group stays in place" keeps the whole group's planned poses while the
    stored numbers change;
  * the node FORM shows a child's offset in the same POINT frame.

NOT covered here, named rather than implied: the live board (Denis's checks),
the marker figures (`test_trees_dock_mount_point.py`) and the rigid-group
capture/apply pair, which uses ONE frame on both halves by construction and is
therefore not affected by this доделка.
"""
import pytest
from PyQt6.QtWidgets import QInputDialog

from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.i18n import _
from kicadstamp.tree_position import (layout_tree_from_anchor,
                                      local_offset_to_board_mm,
                                      mount_node_base, node_position)
from kicadstamp.utils.units import MM

import gui.docks.trees_dock as trees_dock_mod
from gui.docks.trees_mount_rebase import _AXES_FROM_NEW_ANCHOR

# The cross-import is the same one test_trees_dock_mount_point.py uses: the
# board stand-in and the dock loader already exist, and a second copy of either
# would drift. `sync_long_ops` is imported for its FIXTURE side effect alone.
from tests.gui.docks.test_trees_dock import (  # noqa: F401 — sync_long_ops is a fixture
    _ClusterAdapter,
    _dock_with,
    _live_fp,
    _node_form_for,
    sync_long_ops,
)
from tests.gui.docks.test_trees_dock_group_move import _row_node, _select_rows

#: Where the anchor components stand on the fake board, in mm / degrees.
ANCHOR_MM = (100.0, 50.0)
ANCHOR_DEG = 0.0
SECOND_MM = (20.0, 10.0)
SECOND_DEG = 30.0

#: The mount node's OWN numbers against its anchor: 5 mm along the anchor's X,
#: turned 45° — both non-zero, which is the whole point of the fixtures (with a
#: zero offset the two frames coincide and NOTHING is observable).
MOUNT_XY = (5.0, 0.0)
MOUNT_ROT = 45.0

#: The child's stored numbers — the frame the dock must read them in.
CHILD_XY = (2.0, 1.0)
CHILD_ROT = 30.0

MOUNT_CHILD_CFG = {
    "trees": [
        {"name": "t1", "anchor": {"origin": True},
         "nodes": [
             {"ref": "M1", "kind": "mount", "xy": list(MOUNT_XY),
              "rotation": MOUNT_ROT, "anchor": {"role": "FPGA"},
              "children": [
                  {"ref": "C1", "kind": "external", "xy": list(CHILD_XY),
                   "rotation": CHILD_ROT},
              ]},
         ]},
    ],
}

#: Two mounts, EACH with its own offset and rotation, one child under the first
#: — the shape a re-hang (single or group) is asked for.
MOVE_CFG = {
    "trees": [
        {"name": "t1", "anchor": {"origin": True},
         "nodes": [
             {"ref": "M1", "kind": "mount", "xy": list(MOUNT_XY),
              "rotation": MOUNT_ROT, "anchor": {"role": "FPGA"},
              "children": [
                  {"ref": "C1", "kind": "external", "xy": list(CHILD_XY),
                   "rotation": CHILD_ROT},
                  {"ref": "C2", "kind": "external", "xy": [3.0, -1.0],
                   "rotation": -20.0},
              ]},
             {"ref": "M2", "kind": "mount", "xy": [-3.0, 2.0],
              "rotation": 90.0, "anchor": {"role": "FPGA2"}},
         ]},
    ],
}

#: ONE mount carrying its group, for the re-anchor flows.
REBASE_CFG = {
    "trees": [
        {"name": "t1", "anchor": {"origin": True},
         "nodes": [
             {"ref": "M1", "kind": "mount", "xy": list(MOUNT_XY),
              "rotation": MOUNT_ROT, "anchor": {"role": "OLD_ROLE"},
              "children": [
                  {"ref": "C1", "kind": "external", "xy": list(CHILD_XY),
                   "rotation": CHILD_ROT},
                  {"ref": "C2", "kind": "external", "xy": [-1.0, 4.0],
                   "rotation": -15.0},
              ]},
         ]},
    ],
}


class _ChildFrameBoard(_ClusterAdapter):
    """_ClusterAdapter plus the surfaces the node reads and the Ч2 selection
    read need: the by-ref lookup an `external` child is read through, the pad
    list a selection classification asks for, and the selected items."""

    def __init__(self, footprints, selected=()):
        super().__init__(footprints)
        self._selected = list(selected)

    def get_footprint(self, ref):
        for fp in self._fps:
            if fp.ref == ref:
                return fp
        return None

    def get_footprint_pads(self, fp):
        return []

    def get_selected_items(self):
        return list(self._selected)


def _plain_fp(ref, position, angle_deg):
    """A footprint a NODE names by refdes (an `external` node): it carries no
    Role/Cluster at all, so it can never be mistaken for a mount's anchor."""
    fp = Footprint(ref=ref, uuid=f"u-{ref}", position=position,
                   angle_deg=float(angle_deg), layer=BoardLayer.BL_F_Cu)
    fp.role = None
    fp.cluster = None
    fp.sheet_path_uuids = []
    return fp


def _plan_poses(dock, tree, board):
    """`{ref: (pos, rot)}` the REDRAW plan gives for the tree's record nodes —
    the tree's own anchor plus the LIVE mount bases, with no stand-in anywhere.
    This is the "board state" every cell below measures against."""
    forest = {t.name: t for t in dock._trees}
    return layout_tree_from_anchor(tree, forest, adapter=board, cfg=dock._cfg,
                                   sheet_names={})


def _assert_same_pose(actual, expected, what):
    """`actual` and `expected` are the same place and the same angle.

    ±2 nm on the position, not an exact equality: a pose read off the board
    travels mm -> nm through the storage grid (and the composition re-rounds
    once), which is 1 nm of honest noise here — while a frame mistake is
    MILLIMETRES wide. The angle is float on both sides, so it stays tight."""
    assert actual[0].x == pytest.approx(expected[0].x, abs=2), what
    assert actual[0].y == pytest.approx(expected[0].y, abs=2), what
    assert actual[1] == pytest.approx(expected[1], abs=1e-6), what


def _pin_dock(dock, board, monkeypatch, *, pick=None):
    """Point the dock at the fake board, keep the widgets out of the way (the
    structural half rebuilds the tabs, which is not what these numbers are
    about) and answer the parent picker."""
    monkeypatch.setattr(dock, "_live_adapter", lambda: board)
    monkeypatch.setattr(dock, "_rebuild_tabs", lambda: None)
    if pick is not None:
        monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: (pick, True))


# ═══════════════════════════════════════════════════════════════════════════
# The measuring cell: Read current position must agree with the redraw
# ═══════════════════════════════════════════════════════════════════════════

def _child_setup(main_window, tmp_path):
    """(dock, tree, mount, child, board) for MOUNT_CHILD_CFG, with the child
    sitting on the board EXACTLY where the redraw plan puts it."""
    dock, _root = _dock_with(main_window, tmp_path, MOUNT_CHILD_CFG)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    child = mount.children[0]
    board = _ChildFrameBoard([
        _live_fp("IC1", "FPGA", None, *ANCHOR_MM, angle=ANCHOR_DEG)])
    child_pos, child_rot = _plan_poses(dock, tree, board)[child.ref]
    board._fps.append(_plain_fp(child.ref,
                                Vector2.from_xy(int(child_pos.x), int(child_pos.y)),
                                child_rot))
    return dock, tree, mount, child, board


def test_read_current_position_of_a_mount_child_returns_the_stored_numbers(
        main_window, tmp_path):
    """The доделка's measuring cell: with the child standing on the board at
    the pose the REDRAW composes, "Read current position" must hand back the
    very numbers the child is stored with.

    That is the round trip the defect broke: the dock resolved the base of a
    mount's child as the mount's ANCHOR, so the numbers it wrote were measured
    from the wrong point and the redraw — which composes the node's POINT — put
    the child somewhere else entirely (measured red: offset (7.12132, -0.707106)
    instead of (2, 1), angle 75° instead of 30°).

    Mutation check: return the anchor pose from the mount branch of
    `_resolve_node_base_pose` and this cell fails again on both halves.
    """
    dock, tree, mount, child, board = _child_setup(main_window, tmp_path)

    offset, rotation = trees_dock_mod._resolve_live_offset(
        dock._cfg, board, {}, tree, mount, child.ref, child.kind)

    # Both halves are measured BEFORE the assertion, so a red run names the
    # angle as well as the offset — a partial regression (position right,
    # angle still taken from the anchor) must not read as "one number off".
    position_ok = offset == pytest.approx(CHILD_XY, abs=1e-6)
    angle_ok = rotation == pytest.approx(CHILD_ROT, abs=1e-6)
    assert position_ok and angle_ok, (
        "the read must hand back the numbers the child is STORED with — both "
        "are measured in the mount's POINT frame: got offset={offset!r} "
        "rotation={rotation!r}, expected {xy!r} / {rot!r}".format(
            offset=offset, rotation=rotation, xy=CHILD_XY, rot=CHILD_ROT))


def test_the_fixture_really_separates_the_two_frames(main_window, tmp_path):
    """The measuring cell above is only worth anything while the two candidate
    frames DIFFER: the anchor pose and the node's point must be different
    places (and different angles) for this fixture.

    Both halves come from the product's own functions (`mount_node_base` for
    the anchor, `node_position` for the composition the point is built from),
    so this cell cannot rot into a tautology of its own arithmetic."""
    dock, tree, mount, child, board = _child_setup(main_window, tmp_path)
    anchor_pos, anchor_rot = mount_node_base(mount, tree, Vector2.from_xy(0, 0),
                                             0.0, board, dock._cfg, {})
    through_anchor = node_position(child, anchor_pos, anchor_rot)
    child_pos, _child_rot = _plan_poses(dock, tree, board)[child.ref]

    assert (through_anchor.x, through_anchor.y) != (child_pos.x, child_pos.y), \
        "the fixture must separate the anchor frame from the node's point"
    assert mount.rotation != 0.0, "…and it must turn the child's frame as well"


# ═══════════════════════════════════════════════════════════════════════════
# Both "Move to…" entries: nothing may move on the board
# ═══════════════════════════════════════════════════════════════════════════

def test_a_single_move_under_a_rotated_mount_keeps_the_planned_pose(
        main_window, tmp_path, monkeypatch):
    """A single "Move to…" of a child from one offset+rotated mount to another:
    the child's PLANNED pose (what a redraw would put on the board) must be
    identical before and after — both mounts' own numbers are non-zero, so the
    re-hang really has to re-express the child through two different POINT
    frames.

    Mutation check: resolve the old/new base from the anchors instead of the
    points and the child lands millimetres away, at another angle."""
    dock, _root = _dock_with(main_window, tmp_path, MOVE_CFG)
    tree = dock._current_tree()
    mount_a, mount_b = tree.nodes
    child = mount_a.children[0]
    board = _ChildFrameBoard([
        _live_fp("IC1", "FPGA", None, *ANCHOR_MM, angle=ANCHOR_DEG),
        _live_fp("IC2", "FPGA2", None, *SECOND_MM, angle=SECOND_DEG)])
    before = _plan_poses(dock, tree, board)[child.ref]
    _pin_dock(dock, board, monkeypatch, pick=mount_b.ref)

    dock._move_node_flow(tree, child)

    assert mount_b.children == [child], "the structural half really moved it"
    after = _plan_poses(dock, tree, board)[child.ref]
    _assert_same_pose(after, before,
                      "the child must stay exactly where it was on the board — "
                      "position AND angle")


def test_a_group_move_under_a_rotated_mount_keeps_the_planned_poses(
        main_window, tmp_path, monkeypatch):
    """The same claim for the GROUP entry: two children of the offset+rotated
    mount, ONE new parent, and neither of them moves a nanometre or a degree.

    Mutation check: as above — the anchor frame moves both of them."""
    dock, _root = _dock_with(main_window, tmp_path, MOVE_CFG)
    tree = dock._current_tree()
    mount_a, mount_b = tree.nodes
    moved = list(mount_a.children)
    board = _ChildFrameBoard([
        _live_fp("IC1", "FPGA", None, *ANCHOR_MM, angle=ANCHOR_DEG),
        _live_fp("IC2", "FPGA2", None, *SECOND_MM, angle=SECOND_DEG)])
    planned = _plan_poses(dock, tree, board)
    before = {node.ref: planned[node.ref] for node in moved}
    _pin_dock(dock, board, monkeypatch, pick=mount_b.ref)
    _select_rows(dock, *[node.ref for node in moved])

    dock._move_nodes_flow(tree, _row_node(dock, moved[0].ref))

    assert [node.ref for node in mount_b.children] == ["C1", "C2"]
    assert mount_a.children == []
    after = _plan_poses(dock, tree, board)
    for node in moved:
        _assert_same_pose(after[node.ref], before[node.ref],
                          f"{node.ref} must stay exactly where it was on the board")


# ═══════════════════════════════════════════════════════════════════════════
# Ч2 "Change anchor from selection…": the group is held still through the point
# ═══════════════════════════════════════════════════════════════════════════

def _rebase_boards(dock, tree, mount, child_refs):
    """(board_with_old, board_without_old, baseline) for REBASE_CFG.

    `board_with_old` has BOTH anchors (it is what the baseline — where the group
    stands today — is computed from); `board_without_old` has only the NEW one
    (the case the action exists for) and still holds the children EXACTLY where
    the baseline put them. Only the boards differ: the action is asked about the
    very same board state, seen through a support that is no longer there."""
    children = [c for c in mount.children if c.ref in child_refs]
    new_fp = _live_fp("IC_NEW", "NEW_ROLE", "CL_NEW", *SECOND_MM,
                      angle=SECOND_DEG)
    old_fp = _live_fp("IC_OLD", "OLD_ROLE", None, *ANCHOR_MM, angle=ANCHOR_DEG)
    # The SELECTION is the same on both boards (the new support); only the old
    # support's presence differs.
    board_with_old = _ChildFrameBoard([old_fp, new_fp], selected=[new_fp])
    baseline = _plan_poses(dock, tree, board_with_old)
    for child in children:
        pos, rot = baseline[child.ref]
        board_with_old._fps.append(
            _plain_fp(child.ref, Vector2.from_xy(int(pos.x), int(pos.y)), rot))

    board_without_old = _ChildFrameBoard(
        [fp for fp in board_with_old._fps if fp.ref != "IC_OLD"],
        selected=[new_fp])
    return board_with_old, board_without_old, baseline


def test_a_reanchor_with_the_old_support_gone_lands_the_group_where_it_stands(
        main_window, tmp_path, monkeypatch, caplog):
    """The case Ч2 exists for, on the REAL functions: the old support is not on
    the board any more, so the mount's numbers stay as they are, its axes come
    from the new anchor, and every direct child is re-read — in the POINT frame.

    The claim is the round trip: after the action the redraw plan puts both
    children exactly where the board holds them, and the Log prints the node's
    RESULTING axes — the new anchor's 30° PLUS the node's own 45°, never the bare
    base angle (the 2026-10-14 доделка's little detail).

    Mutation check: re-read the children against the ANCHOR and the plan misses
    the node's own xy/rotation — the children land elsewhere; drop the node's
    `rotation` from the Log line and the angle assertion fails at 30."""
    dock, _root = _dock_with(main_window, tmp_path, REBASE_CFG)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    numbers = (mount.xy, mount.polar, mount.rotation)
    board_with_old, board, baseline = _rebase_boards(dock, tree, mount,
                                                     {"C1", "C2"})
    monkeypatch.setattr(dock, "_live_adapter", lambda: board)
    monkeypatch.setattr(dock, "_reload_node_form", lambda: None)
    monkeypatch.setattr(QInputDialog, "getItem",
                        lambda *a, **k: (_("Group stays in place"), True))

    caplog.clear()
    dock._mount_anchor_flow(tree, mount)

    assert mount.anchor.role == "NEW_ROLE"
    assert (mount.xy, mount.polar, mount.rotation) == numbers, \
        "with no old place to hold it by, the node's OWN numbers stay as they are"
    after = _plan_poses(dock, tree, board)
    for child in mount.children:
        _assert_same_pose(after[child.ref], baseline[child.ref],
                          f"{child.ref}: the re-read must reproduce exactly "
                          "where it stands")
    told = [r.message for r in caplog.records]
    assert _AXES_FROM_NEW_ANCHOR.format(mount="M1", angle="75") in told, \
        f"the node's RESULTING axes (new anchor 30° + its own 45°) — got {told!r}"


def test_stays_in_place_keeps_the_planned_group_while_the_numbers_change(
        main_window, tmp_path, monkeypatch):
    """The other half of Ч2 on the real functions: the OLD support IS on the
    board, so the numbers are re-expressed against the new anchor and the WHOLE
    group — children included — keeps its planned poses.

    Mutation check: re-express the node's own numbers against the wrong frame
    (its point instead of its anchor) and the group jumps by the node's offset;
    leave the children's frame out of the picture and the redraw turns them by
    the node's rotation."""
    dock, _root = _dock_with(main_window, tmp_path, REBASE_CFG)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    board_with_old, board, baseline = _rebase_boards(dock, tree, mount,
                                                     {"C1", "C2"})
    monkeypatch.setattr(dock, "_live_adapter", lambda: board_with_old)
    monkeypatch.setattr(dock, "_reload_node_form", lambda: None)
    monkeypatch.setattr(QInputDialog, "getItem",
                        lambda *a, **k: (_("Group stays in place"), True))
    before_numbers = (mount.xy, mount.polar, mount.rotation)
    before_children = [(c.xy, c.polar, c.rotation) for c in mount.children]

    dock._mount_anchor_flow(tree, mount)

    assert mount.anchor.role == "NEW_ROLE"
    assert (mount.xy, mount.polar, mount.rotation) != before_numbers, \
        "the numbers are re-expressed through the new anchor…"
    assert [(c.xy, c.polar, c.rotation) for c in mount.children] == \
        before_children, \
        "…while the children ride on the node and are not touched AT ALL"
    after = _plan_poses(dock, tree, board_with_old)
    for child in mount.children:
        _assert_same_pose(after[child.ref], baseline[child.ref],
                          f"{child.ref}: the whole group must keep its place")


# ═══════════════════════════════════════════════════════════════════════════
# The node FORM shows a child in the same POINT frame
# ═══════════════════════════════════════════════════════════════════════════

def test_the_form_shows_a_mount_child_offset_in_the_point_frame(
        main_window, tmp_path, monkeypatch):
    """A child of an offset+rotated mount, opened in the node form: the board
    frame the form displays is the POINT frame (the one a save would be read
    from), so the shown numbers are the stored ones turned by the node's OWN
    axes — not by the bare anchor.

    Mutation check: hand the form the anchor pose and the shown pair comes back
    turned 45° short."""
    dock, _root = _dock_with(main_window, tmp_path, MOUNT_CHILD_CFG)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    child = mount.children[0]
    board = _ChildFrameBoard([
        _live_fp("IC1", "FPGA", None, *ANCHOR_MM, angle=ANCHOR_DEG)])
    frames = trees_dock_mod._mount_anchor_pose(dock._cfg, board, {}, tree, mount)

    form = _node_form_for(dock, tree, mount, existing=child, adapter=board)

    bx, by = local_offset_to_board_mm(child.xy, frames.point_rot)
    fields, err = form.offset_widget.build()
    assert err is None
    assert (fields["x"], fields["y"]) == (bx, by), \
        "the form must show the child's stored offset in the node's POINT frame"
