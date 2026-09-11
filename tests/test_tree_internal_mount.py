# tests/test_tree_internal_mount.py
"""Tests for the INTERNAL mount method (plan_2026_09_11_internal_mount, task Г).

A mount node's anchor role may belong to a cell THIS tree places — then the base
is computed from the tree's OWN layout (base -> node path -> pose -> cell slot),
never read back from the board, so it cannot drift on repeated Redraw. A role
from OUTSIDE the tree keeps the LIVE method. Both are exercised side by side by
the `tests/fixtures/internal_mount/config.sexp` fixture.

The numeric claims (internal == live on a synchronous board, rotation follows
the tree, the angle handed to the subtree is the slot's) are asserted by NUMBER,
not by which helper was called (test 1 of the plan's Г.9).
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from kipy.board_types import FootprintInstance

from kicadstamp.cell_frame import CellFrame
from kicadstamp.config import Cell, Config, Entity, TemplateComponentSlot, load_config
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.cell_anchor import cell_mount_offset
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.tree_position import layout_tree_from_base, mount_node_base
from kicadstamp.trees import (
    Tree,
    TreeAnchor,
    TreeNode,
    check_mount_anchor_drift,
    find_role_placement_matches,
    resolve_internal_mount_match,
)
from kicadstamp.utils.units import MM

FIXTURES = Path(__file__).parent / "fixtures" / "internal_mount"

_ORIGIN = Vector2.from_xy(0, 0)

# (ref, role, cluster, x_mm, y_mm, angle_deg, layer, {pad: (dx_mm, dy_mm)})
_FAKE_FOOTPRINTS = (
    ("IC2", "DAC", "DAC_BUF", 220.55, 113.4881, 30.0, "F.Cu",
     {"3": (1.5, 2.5)}),
    ("U7", "EXT", "DAC_BUF", 232.575, 116.3506, 0.0, "F.Cu",
     {"1": (-1.0, 0.0)}),
)


def _fake_adapter():
    """The deterministic board the internal/live comparison runs against."""
    footprints = []
    for ref, role, cluster, x_mm, y_mm, angle, layer, pads in _FAKE_FOOTPRINTS:
        fp = MagicMock(spec=FootprintInstance)
        fp.ref = ref
        fp._role = role
        fp._cluster = cluster
        fp.layer = layer
        fp.position = Vector2.from_xy_mm(x_mm, y_mm)
        fp.angle_deg = angle
        fp._pads = [
            MagicMock(number=num, net_name="X",
                      position=Vector2.from_xy_mm(x_mm + dx, y_mm + dy))
            for num, (dx, dy) in pads.items()
        ]
        footprints.append(fp)

    adapter = MagicMock()
    adapter.get_footprints.return_value = footprints
    adapter.has_field.return_value = True

    def _field(fp, name):
        if name == "Role":
            return getattr(fp, "_role", None)
        if name == CLUSTER_FIELD_NAME:
            return getattr(fp, "_cluster", None)
        return None

    adapter.get_field_value.side_effect = _field
    adapter.get_selected_items.return_value = []
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in getattr(fp, "_pads", []) if p.number == num), None)
    return adapter


def _load_fixture():
    cfg, ctx = load_config(str(FIXTURES / "config.sexp"))
    return cfg, dict(ctx.sheet_names or {})


def _node_by_ref(tree, ref):
    stack = list(tree.nodes)
    while stack:
        n = stack.pop()
        if n.ref == ref:
            return n
        stack.extend(n.children)
    raise AssertionError(f"no node {ref!r}")


# ── in-memory builders (fine-grained numeric tests) ────────────────────────

def _cell(name, roles, layer="F.Cu"):
    return Cell(name=name, layer=layer, components=[
        TemplateComponentSlot(role=r, offset_along_mm=o, offset_across_mm=a,
                              angle_deg=ang)
        for r, o, a, ang in roles])


def _place(ref, xy=(0.0, 0.0), rot=0.0, children=()):
    return TreeNode(ref=ref, kind="placement", xy=xy, polar=None,
                    rotation=rot, name=None, group=None,
                    children=list(children))


def _mount(ref, role, children=(), pad=None, sheet=None, cluster=None):
    return TreeNode(ref=ref, kind="mount", xy=None, polar=None, rotation=0.0,
                    name=None, group=None, children=list(children),
                    anchor=TreeAnchor(role=role, anchor_sheet=sheet,
                                      anchor_cluster=cluster, anchor_pad=pad))


def _tree(nodes, anchor=None):
    return Tree(name="t", anchor=anchor or TreeAnchor(is_origin=True),
                nodes=list(nodes))


def _cfg(*, nodes, cells, entities, anchor=None):
    return Config(cells=cells, entities=list(entities),
                  trees=[_tree(nodes, anchor)])


# ── Г.9.1: the method is chosen by RESULT (a number), not by a helper ───────

def test_internal_is_chosen_for_a_role_the_tree_places():
    """A mount anchored to a role of a cell THIS tree places resolves from the
    TREE base, NOT from the live footprint — proven by choosing a tree base far
    from the live component: the result follows the tree."""
    cell = _cell("c", [("R", 0.0, 0.0, 0.0)])
    entity = Entity(name="E", cell="c", sheet="S", cluster="CL")
    cfg = _cfg(nodes=[_place("E"), _mount("M", "R", sheet="S", cluster="CL")],
               cells={"c": cell}, entities=[entity])
    adapter = _fake_adapter()
    # No live role "R" exists at all — if the internal method were not chosen,
    # the live read would fail; a tree base far from any footprint proves it.
    base = Vector2.from_xy_mm(10.0, 20.0)
    pos, rot = mount_node_base(cfg.trees[0].nodes[1], cfg.trees[0], base, 0.0,
                               adapter, cfg, {})
    assert (pos.x / MM, pos.y / MM) == (10.0, 20.0)
    assert rot == 0.0


def test_live_is_chosen_for_a_role_outside_the_tree():
    """A role no cell of this tree places stays the LIVE method — the result is
    the live footprint's position."""
    cell = _cell("c", [("R", 0.0, 0.0, 0.0)])
    entity = Entity(name="E", cell="c", sheet="S", cluster="CL")
    cfg = _cfg(nodes=[_place("E"), _mount("M", "EXT", sheet="Channel_0",
                                          cluster="DAC_BUF")],
               cells={"c": cell}, entities=[entity])
    adapter = _fake_adapter()
    pos, rot = mount_node_base(cfg.trees[0].nodes[1], cfg.trees[0], _ORIGIN, 0.0,
                               adapter, cfg, {})
    assert (pos.x / MM, pos.y / MM) == (232.575, 116.3506)
    assert rot == 0.0


def test_reserve_internal_match_classifies_none_for_an_external_role():
    cell = _cell("c", [("R", 0.0, 0.0, 0.0)])
    entity = Entity(name="E", cell="c", sheet="S", cluster="CL")
    cfg = _cfg(nodes=[_place("E"), _mount("M", "OTHER")],
               cells={"c": cell}, entities=[entity])
    assert resolve_internal_mount_match(cfg, cfg.trees[0], cfg.trees[0].nodes[1]) is None
    matches = find_role_placement_matches(cfg, cfg.trees[0], "R")
    assert [m.node.ref for m in matches] == ["E"]


# ── Г.9.2 / Г.6.1: internal == live on a synchronous board ─────────────────

def test_internal_equals_live_on_a_synchronous_board_with_a_pad():
    """With the tree base set exactly where the live component sits, the internal
    pad read must give the SAME number the live method gives — a measurement of
    the whole Г.3 maths (sign, mirror, slot angle)."""
    cfg, sheet_names = _load_fixture()
    adapter = _fake_adapter()
    tree = cfg.trees[0]
    mount = _node_by_ref(tree, "M_dac_pad3")
    ic = next(fp for fp in adapter.get_footprints() if fp.ref == "IC2")
    base_pos, base_rot = ic.position, ic.angle_deg

    internal, internal_rot = mount_node_base(
        mount, tree, base_pos, base_rot, adapter, cfg, sheet_names)
    live, live_rot = mount_node_base(
        mount, None, base_pos, base_rot, adapter, cfg, sheet_names)
    assert abs(internal.x - live.x) <= 1
    assert abs(internal.y - live.y) <= 1
    assert internal_rot == live_rot


# ── Г.9.3 / Г.6.2: rotation 0/90/180/270 ───────────────────────────────────

@pytest.mark.parametrize("rot", [0.0, 90.0, 180.0, 270.0])
def test_a_tree_rotation_reaches_the_mount_subtree(rot):
    """The mount's child rotates WITH the tree, and the offset from the mount
    rotates too — checked by numbers for all four quadrants. The LIVE method on
    a fixed board would not follow the tree base rotation."""
    cell = _cell("c", [("R", 0.0, 0.0, 0.0)])
    child_cell = _cell("child", [("PIF", 0.0, 0.0, 0.0)])
    e_dac = Entity(name="E", cell="c", sheet="S", cluster="CL")
    e_pif = Entity(name="E2", cell="child", sheet="S", cluster="CL")
    cfg = _cfg(
        nodes=[_place("E"),
               _mount("M", "R", sheet="S", cluster="CL",
                      children=[_place("E2", xy=(1.0, 0.0))])],
        cells={"c": cell, "child": child_cell}, entities=[e_dac, e_pif])
    base = Vector2.from_xy_mm(5.0, 7.0)
    laid = layout_tree_from_base(cfg.trees[0], base, rot, None,
                                 adapter=None, cfg=cfg, sheet_names={})
    expected_offset = rotate_local_offset(1.0, 0.0, rot)
    got = laid["E2"][0]
    assert got.x == base.x + expected_offset.x
    assert got.y == base.y + expected_offset.y
    assert laid["E2"][1] == rot


# ── Г.9.5: slot.angle_deg is added to the angle the mount hands down ───────

def test_the_subtree_angle_includes_the_slot_angle():
    cell = _cell("c", [("R", 0.0, 0.0, 90.0)])
    entity = Entity(name="E", cell="c", sheet="S", cluster="CL")
    cfg = _cfg(nodes=[_place("E"), _mount("M", "R", sheet="S", cluster="CL")],
               cells={"c": cell}, entities=[entity])
    _pos, rot = mount_node_base(cfg.trees[0].nodes[1], cfg.trees[0], _ORIGIN,
                                0.0, None, cfg, {})
    assert rot == 90.0


# ── Г.9.4: the internal slot pose matches CellFrame directly ───────────────

@pytest.mark.parametrize("mirror", [False, True])
def test_internal_slot_pose_matches_cellframe(mirror):
    """The slot's world point/angle the internal method computes are EXACTLY
    what the canonical cell-frame transform gives (the same brick
    materialization uses)."""
    cell = _cell("c", [("R", 2.5, -1.5, 30.0)])
    entity = Entity(name="E", cell="c", sheet="S", cluster="CL", mirror=mirror)
    # The placement node sits at (0,0), so the cell's placement origin IS the
    # tree base — the CellFrame below is built from that same origin.
    cfg = _cfg(nodes=[_place("E", xy=(0.0, 0.0), rot=0.0),
                      _mount("M", "R", sheet="S", cluster="CL")],
               cells={"c": cell}, entities=[entity])
    base = Vector2.from_xy_mm(50.0, 60.0)
    pos, rot = mount_node_base(cfg.trees[0].nodes[1], cfg.trees[0], base, 0.0,
                               None, cfg, {})
    frame = CellFrame(placement_origin=base, rotation_deg=0.0, mirror=mirror,
                      mount=cell_mount_offset(cell))
    ex_mm, ey_mm = frame.point_to_world_mm(2.5, -1.5)
    assert (pos.x / MM, pos.y / MM) == (ex_mm, ey_mm)
    expected_rot = ((180.0 - 30.0) % 360.0) if mirror else 30.0
    assert rot == expected_rot


# ── Г.9.6: idempotency — a repeated materialization does not move ──────────

def test_materialization_is_idempotent_with_an_internal_mount():
    cfg, sheet_names = _load_fixture()
    adapter = _fake_adapter()
    first = materialize_entity_placements(adapter, cfg, sheet_names)
    second = materialize_entity_placements(adapter, cfg, sheet_names)
    assert [(c.name, c.xy, c.rotation_deg) for c in first] == \
           [(c.name, c.xy, c.rotation_deg) for c in second]


# ── Г.9.7: a cycle is a clear fatal, not recursion ─────────────────────────

def test_a_mount_that_depends_on_itself_is_a_cycle_fatal():
    """The placing node sits UNDER the mount node, so the mount's base would
    depend on its own result — a fatal at load and at resolve time."""
    cell = _cell("c", [("R", 0.0, 0.0, 0.0)])
    entity = Entity(name="E", cell="c", sheet="S", cluster="CL")
    mount = _mount("M", "R", sheet="S", cluster="CL", children=[_place("E")])
    cfg = _cfg(nodes=[mount], cells={"c": cell}, entities=[entity])
    with pytest.raises(ValidationError, match="cycle"):
        check_mount_anchor_drift(cfg)
    with pytest.raises(ValidationError, match="cycle"):
        mount_node_base(mount, cfg.trees[0], _ORIGIN, 0.0, None, cfg, {})


# ── Г.9.8: an unresolvable ambiguity is a fatal listing the candidates ─────

def test_ambiguity_between_two_cells_is_a_fatal():
    cell_a = _cell("a", [("R", 0.0, 0.0, 0.0)])
    cell_b = _cell("b", [("R", 0.0, 0.0, 0.0)])
    entities = [Entity(name="E1", cell="a", sheet="S", cluster="CL"),
                Entity(name="E2", cell="b", sheet="S", cluster="CL")]
    cfg = _cfg(nodes=[_place("E1"), _place("E2"), _mount("M", "R")],
               cells={"a": cell_a, "b": cell_b}, entities=entities)
    with pytest.raises(ValidationError, match="more than one cell"):
        check_mount_anchor_drift(cfg)
    with pytest.raises(ValidationError, match="more than one cell"):
        mount_node_base(cfg.trees[0].nodes[2], cfg.trees[0], _ORIGIN, 0.0,
                        None, cfg, {})


# ── Г.9.9: offline internal mounts ─────────────────────────────────────────

def test_internal_mount_without_a_pad_needs_no_adapter():
    cell = _cell("c", [("R", 0.0, 0.0, 0.0)])
    entity = Entity(name="E", cell="c", sheet="S", cluster="CL")
    cfg = _cfg(nodes=[_place("E"), _mount("M", "R", sheet="S", cluster="CL")],
               cells={"c": cell}, entities=[entity])
    base = Vector2.from_xy_mm(1.0, 2.0)
    pos, _rot = mount_node_base(cfg.trees[0].nodes[1], cfg.trees[0], base, 0.0,
                                None, cfg, {})
    assert (pos.x / MM, pos.y / MM) == (1.0, 2.0)


def test_internal_mount_with_a_pad_without_an_adapter_is_a_clear_refusal():
    cell = _cell("c", [("R", 0.0, 0.0, 0.0)])
    entity = Entity(name="E", cell="c", sheet="S", cluster="CL")
    cfg = _cfg(nodes=[_place("E"),
                      _mount("M", "R", sheet="S", cluster="CL", pad="3")],
               cells={"c": cell}, entities=[entity])
    with pytest.raises(ValidationError, match="live board"):
        mount_node_base(cfg.trees[0].nodes[1], cfg.trees[0], _ORIGIN, 0.0,
                        None, cfg, {})


# ── Г.9.11: the load guard accepts the internal shape ──────────────────────

def test_guard_accepts_the_internal_fixture_config():
    cfg, _sheet_names = _load_fixture()
    check_mount_anchor_drift(cfg)          # must not raise
    # …and the internal mount still resolves end to end.
    adapter = _fake_adapter()
    ic = next(fp for fp in adapter.get_footprints() if fp.ref == "IC2")
    pos, _rot = mount_node_base(
        _node_by_ref(cfg.trees[0], "M_dac_pad3"), cfg.trees[0],
        ic.position, ic.angle_deg, adapter, cfg, {})
    live, _lrot = mount_node_base(
        _node_by_ref(cfg.trees[0], "M_dac_pad3"), None,
        ic.position, ic.angle_deg, adapter, cfg, {})
    assert abs(pos.x - live.x) <= 1 and abs(pos.y - live.y) <= 1


def test_module_crossing_internal_mount_is_a_clear_stop():
    """A role reachable only through an embedded tree has no defined pose yet —
    a clear stop, never a silently wrong base (discovered shape, documented)."""
    inner_cell = _cell("inner", [("R", 0.0, 0.0, 0.0)])
    inner_entity = Entity(name="EI", cell="inner", sheet="S", cluster="CL")
    inner_tree = Tree(name="inner_tree", anchor=TreeAnchor(is_origin=True),
                      nodes=[_place("EI")])
    outer_tree = Tree(name="t", anchor=TreeAnchor(is_origin=True),
                      nodes=[TreeNode(ref="inner_tree", kind="module", xy=(0.0, 0.0),
                                      polar=None, rotation=0.0, name=None, group=None,
                                      children=[]),
                             _mount("M", "R", sheet="S", cluster="CL")])
    cfg = Config(cells={"inner": inner_cell}, entities=[inner_entity],
                 trees=[outer_tree, inner_tree])
    with pytest.raises(ValidationError, match="EMBEDDED tree"):
        check_mount_anchor_drift(cfg)
