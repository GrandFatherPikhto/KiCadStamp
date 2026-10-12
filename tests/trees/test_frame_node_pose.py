#!/usr/bin/env python3
"""The tree node's pose for a RE-READ frame (plan_2026_10_12_reread_frame_mirror).

Denis, 12.10.2026: re-reading `p5v_in_power` by selection mirrored the cell and
turned J1 by 180°. The fit is honest only for a RIGID copy of the cell; on a
rearranged cluster it GUESSED a mirror, and the cell — written with that frame —
was then placed by the tree node, which turned the whole cluster. The fix: a
non-rigid frame takes its rotation from the node that PLACES the instance, and
the mirror from the instance's physical side (cell_layer), never from geometry.

The pose is the number Apply uses — `base_rot + node.rotation` composed down the
tree from its live anchor (`tree_position.layout_tree_from_anchor` → `abs_rot`) —
NOT the node's own `rotation` field (Р11–Р13: that number is relative to its
base). Everything here is board-free: the trees use an `(origin)` anchor, which
resolves to (0,0)/0° without touching an adapter.
"""
import pytest

from kicadstamp import cell_geometry_refresh as mod
from kicadstamp import frame_node_pose as fnp
from kicadstamp.cell_frame import CellFrame, RIGID_TOLERANCE_MM
from kicadstamp.cell_geometry_refresh import build_refresh_plan
from kicadstamp.config import Cell, Config, Entity, TemplateComponentSlot
from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

MM = 1_000_000


# ── fakes ───────────────────────────────────────────────────────────────────

class _RoleAdapter:
    """{ref: role} — all `_cell_selection_context` needs from a board."""

    def __init__(self, roles):
        self.roles = roles

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return self.roles.get(fp.ref)
        return None


def _cell(roles, name="p5v_in"):
    return Cell(name=name, components=[
        TemplateComponentSlot(role=role, offset_along_mm=0.0,
                              offset_across_mm=0.0, angle_deg=0.0)
        for role in roles])


def _cfg(*trees, roles=("C_IN_BULK", "LED"), cell_name="p5v_in",
         entity="p5v_in_power", cluster="P5V_IN", sheet="Power"):
    return Config(
        cells={cell_name: _cell(roles, cell_name)},
        entities=[Entity(name=entity, cell=cell_name, cluster=cluster,
                         sheet=sheet)],
        trees=list(trees))


def _node(ref, rotation=0.0, xy=(0.0, 0.0), kind="placement", children=()):
    """A tree node with no display name/group (both are REQUIRED fields of the
    dataclass and neither takes part in geometry)."""
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rotation,
                    name=None, group=None, children=list(children))


def _tree(name="power", nodes=(), rotation=0.0):
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=list(nodes),
                rotation=rotation)


def _context(cfg):
    return fnp.FrameReadContext(cell_layer="F.Cu", cell_name="p5v_in",
                                cluster="P5V_IN", sheet="Power", config=cfg,
                                sheet_names={})


def _non_rigid_frame():
    """A frame whose fit is a GUESS: the residual is past RIGID_TOLERANCE_MM."""
    return CellFrame(placement_origin=Vector2.from_xy_mm(10.0, 10.0),
                     rotation_deg=-180.0, mirror=False, mount=(0.0, 0.0),
                     residual_mm=5.7)


# ── К10–К12: the pose is taken, or the refusal is NAMED ─────────────────────

def test_a_rearranged_frame_takes_the_rotation_apply_would_use():
    """К10 (plan_2026_10_12_reread_frame_mirror): the node's ABSOLUTE pose — the
    tree's own angle plus the node's — replaces the fitted rotation, and the Log
    names BOTH numbers so the rearrangement is visible."""
    cfg = _cfg(_tree(nodes=[_node("p5v_in_power", rotation=90.0)]))
    frame, lines = fnp.follow_the_tree_node(_non_rigid_frame(), _context(cfg),
                                            None, ["C_IN_BULK", "LED"])
    assert frame.rotation_deg == 90.0
    assert frame.mirror is False                 # the side rule is untouched
    assert len(lines) == 1
    assert "power/p5v_in_power" in lines[0]
    assert "90" in lines[0] and "-180" in lines[0]


def test_the_pose_is_the_applied_accumulation_not_the_nodes_own_field():
    """Д1 of the plan (plan_2026_10_12_reread_frame_mirror): the number taken is
    `base_rot + node.rotation` composed down the tree from the tree's own angle —
    Apply's own arithmetic (entity_placement._walk) — NOT the node's `rotation`
    field, which is relative to its base (Р11–Р13). Tree 45° + parent 30° + node
    15° is 90° on the board; a reader that took the field would say 15°."""
    cfg = _cfg(_tree(rotation=45.0, nodes=[
        _node("group_a", kind="coordinate", rotation=30.0, children=[
            _node("p5v_in_power", rotation=15.0)])]))
    pose, reason = fnp.node_pose_for_instance(
        cfg, None, {}, cell_name="p5v_in", roles=["C_IN_BULK", "LED"],
        cluster="P5V_IN", sheet="Power")
    assert reason is None
    assert pose.rotation_deg == 90.0
    assert pose.where == "power/p5v_in_power"


def test_many_roles_of_one_cell_do_not_make_several_nodes():
    """К10 (plan_2026_10_12_reread_frame_mirror): the node is found per ROLE, so
    a two-role cell reaches the SAME node twice. That is one placement, not an
    ambiguity — the guard would otherwise refuse every ordinary cell."""
    cfg = _cfg(_tree(nodes=[_node("p5v_in_power", rotation=90.0)]))
    pose, reason = fnp.node_pose_for_instance(
        cfg, None, {}, cell_name="p5v_in", roles=["C_IN_BULK", "LED"],
        cluster="P5V_IN", sheet="Power")
    assert reason is None and pose.rotation_deg == 90.0


def test_no_node_keeps_the_fit_and_says_the_rotation_was_guessed():
    """К11 (plan_2026_10_12_reread_frame_mirror): with no tree at all the fitted
    rotation stays (rule 2 of the plan: "как сейчас") — and the Log says it was a
    GUESS, with the reason, instead of letting it pass as measured."""
    cfg = _cfg()                                  # no trees, cell + entity only
    frame, lines = fnp.follow_the_tree_node(_non_rigid_frame(), _context(cfg),
                                            None, ["C_IN_BULK", "LED"])
    assert frame.rotation_deg == -180.0
    assert len(lines) == 1 and "guessed" in lines[0]
    assert "no tree node places this instance" in lines[0]


def test_a_non_rigid_frame_without_a_named_cell_still_says_it_guessed():
    """К11b (Г3 of the plan's acceptance, plan_2026_10_12_reread_frame_mirror):
    the "guessed" line does NOT depend on the caller naming the cell — an unnamed
    cell is one of frame_node_pose's own REFUSALS, and its reason must be printed
    like any other. A gate on `cell_name` here would swallow exactly the case the
    line exists for."""
    cfg = _cfg(_tree(nodes=[_node("p5v_in_power", rotation=90.0)]))
    context = fnp.FrameReadContext(cell_layer="F.Cu", cell_name=None,
                                   cluster="P5V_IN", sheet="Power", config=cfg,
                                   sheet_names={})
    frame, lines = fnp.follow_the_tree_node(_non_rigid_frame(), context, None,
                                            ["C_IN_BULK"])
    assert frame.rotation_deg == -180.0
    assert len(lines) == 1 and "guessed" in lines[0]
    assert "does not know the cell's name" in lines[0]


def test_two_nodes_are_a_named_refusal_not_a_coin_toss():
    """К12 (plan_2026_10_12_reread_frame_mirror): TWO nodes place the same
    instance — the read may not pick one, so the fit stays and the Log names
    BOTH candidates (Д3: откат к подгонке + строка с причиной)."""
    cfg = _cfg(_tree(name="power", nodes=[_node("p5v_in_power", rotation=90.0)]),
               _tree(name="spare", nodes=[_node("p5v_in_power", rotation=180.0)]))
    frame, lines = fnp.follow_the_tree_node(_non_rigid_frame(), _context(cfg),
                                            None, ["C_IN_BULK", "LED"])
    assert frame.rotation_deg == -180.0
    assert len(lines) == 1
    assert "2 tree nodes" in lines[0]
    assert "power/p5v_in_power" in lines[0] and "spare/p5v_in_power" in lines[0]


# ── К13/К14: the cases the read cannot answer cheaply ───────────────────────

def test_a_module_embedded_tree_is_refused_by_name():
    """К13 (plan_2026_10_12_reread_frame_mirror): the node's own tree is ALSO
    embedded by another tree through a module node — the standalone layout and
    the composed one pose it differently, and nothing in the config says which
    one the next Apply picks. A reason, not a guess."""
    inner = _tree(name="inner", nodes=[_node("p5v_in_power", rotation=90.0)])
    outer = _tree(name="outer", nodes=[_node("inner", kind="module", rotation=45.0)])
    cfg = _cfg(outer, inner)
    pose, reason = fnp.node_pose_for_instance(
        cfg, None, {}, cell_name="p5v_in", roles=["C_IN_BULK", "LED"],
        cluster="P5V_IN", sheet="Power")
    assert pose is None
    assert "embedded" in reason and "outer" in reason


def test_a_mount_ancestor_is_refused_by_name():
    """К14 (plan_2026_10_12_reread_frame_mirror): the node hangs under a mount
    node whose base is read live (or internally) — a separate story, named as
    such instead of being folded into this read."""
    cfg = _cfg(_tree(nodes=[_node("mnt", kind="mount", children=[
        _node("p5v_in_power", rotation=90.0)])]))
    pose, reason = fnp.node_pose_for_instance(
        cfg, None, {}, cell_name="p5v_in", roles=["C_IN_BULK", "LED"],
        cluster="P5V_IN", sheet="Power")
    assert pose is None
    assert "mount" in reason


# ── К15: a rigid frame asks no tree at all ──────────────────────────────────

def test_a_rigid_frame_looks_no_node_up(monkeypatch):
    """К15 (plan_2026_10_12_reread_frame_mirror): the lookup is LAZY — on a rigid
    copy the fit IS the placement's rotation, so the read must not pay a live
    anchor read (or touch the tree library at all). The planted bomb proves the
    call never happens; the contrast with the non-rigid case above proves the
    guard is not vacuous."""
    calls = []

    def _bomb(*args, **kwargs):
        calls.append(args)
        raise AssertionError("a rigid frame must not look a node up")

    monkeypatch.setattr(fnp, "node_pose_for_instance", _bomb)
    cfg = _cfg(_tree(nodes=[_node("p5v_in_power", rotation=90.0)]))
    rigid = CellFrame(placement_origin=Vector2.from_xy_mm(10.0, 10.0),
                      rotation_deg=90.0, residual_mm=RIGID_TOLERANCE_MM / 2,
                      mount=(0.0, 0.0))
    frame, lines = fnp.follow_the_tree_node(rigid, _context(cfg), None,
                                            ["C_IN_BULK"])
    assert frame is rigid and lines == [] and calls == []


# ── К16: Denis's own case, board-free ───────────────────────────────────────

# The instance Denis re-read at 17:22 on 2026-10-12 (profile
# Heating-Table-Controller-STM32, cell `p5v_in`, entity `p5v_in_power`, tree
# `power` with an (origin) anchor and ONE node at (rotation 90.0)).
#
# Stored offsets — the cell exactly as the BUGGY re-read left it (the profile's
# own `p5v_in`; measured: they reproduce the live cluster to 0.000 mm under
# theta=-90 / mirror=True, which IS the defect — a re-read frame that chose the
# mirror because the layout was rearranged).
_STORED = {
    "C_IN_BULK": (0.0, 0.0, -90.0),
    "LED": (8.575, -3.6, 0.0),
    "R_LED": (8.4625, -6.8, -180.0),
    "C_IN_BYPASS": (9.6, -0.2375, -90.0),
    "CONN": (0.0, -7.9, 0.0),
}
# The live cluster BEFORE the re-read (operational/operation_20261012_172720.json,
# `original_position`/`original_angle_deg` of every component Denis had moved).
# The refs are matched to roles by the cell's own geometry: the stored offsets
# above reproduce these very deltas under the buggy frame — measured, not
# assumed. (The sixth slot, FUSE, was NOT moved, so the operation dump carries no
# position for it; the frame question does not need it.)
_LIVE = {
    "C_IN_BULK": ("C4", 71.6, 75.7, 0.0),
    "LED": ("D1", 68.0, 84.275, -90.0),
    "R_LED": ("R1", 64.8, 84.1625, 90.0),
    "C_IN_BYPASS": ("C5", 71.3625, 85.3, 0.0),
    "CONN": ("J1", 63.7, 75.7, -90.0),
}


def _denis_case():
    components = [{"role": role, "offset_along_mm": along,
                   "offset_across_mm": across, "angle_deg": angle}
                  for role, (along, across, angle) in _STORED.items()]
    footprints = [
        Footprint(ref=ref, uuid=f"uuid-{ref}",
                  position=Vector2.from_xy_mm(x, y), angle_deg=angle,
                  layer=BoardLayer.BL_F_Cu)
        for _role, (ref, x, y, angle) in _LIVE.items()]
    adapter = _RoleAdapter({ref: role for role, (ref, *_r) in _LIVE.items()})
    cfg = _cfg(_tree(name="power", nodes=[
        _node("p5v_in_power", rotation=90.0, xy=(17.0, -75.0))]),
        roles=tuple(_STORED))
    return components, footprints, adapter, cfg


def _frame_of(components, footprints, adapter, cfg, **kw):
    return mod._frame_and_context(
        components, footprints, adapter, "refresh", "C_IN_BULK",
        cell_layer=kw.pop("cell_layer", "F.Cu"),
        node_context=_context(cfg), **kw)[0]


def test_the_re_read_does_not_mirror_a_cluster_on_the_cells_own_side():
    """К16a (plan_2026_10_12_reread_frame_mirror): THE defect. On Denis's own
    data the FREE fit still chooses mirror=True / -90° (residual 0.000 mm — the
    stored cell is what that buggy frame wrote). With the cell's layer known the
    mirror is the instance's PHYSICAL side, so the mirror candidate is not
    offered at all, the fit comes out non-rigid, and the frame follows the tree
    node: 90°, no mirror."""
    components, footprints, adapter, cfg = _denis_case()
    free = _frame_of(components, footprints, adapter, cfg, cell_layer=None)
    assert free.mirror is True and free.rotation_deg == -90.0
    assert free.is_rigid                      # the defect's own numbers, exactly

    frame = _frame_of(components, footprints, adapter, cfg)
    assert frame.mirror is False
    assert frame.rotation_deg == 90.0         # the tree node's pose, not the fit
    assert not frame.is_rigid                 # and it says the layout was moved


def test_the_re_read_cell_placed_by_the_same_node_reproduces_live():
    """К16b (plan_2026_10_12_reread_frame_mirror): the guarantee the fix owes
    Denis — the re-read cell, placed by the SAME tree node, reproduces the live
    positions AND angles (0.01 mm / 0.01°). The frame follows the node, so the
    stored offsets and the applied frame are two halves of one mapping again."""
    components, footprints, adapter, cfg = _denis_case()
    frame = _frame_of(components, footprints, adapter, cfg)
    by_role = {rec["role"]: geo
               for rec, geo in mod.build_refresh_plan(
                   components, [], [], footprints, [], [], adapter,
                   origin_role="C_IN_BULK", cell_layer="F.Cu", config=cfg,
                   cell_name="p5v_in", chosen_cluster="P5V_IN",
                   chosen_sheet="Power").component_updates}
    for role, (ref, x, y, angle) in _LIVE.items():
        wx, wy = frame.point_to_world_mm(by_role[role]["offset_along_mm"],
                                         by_role[role]["offset_across_mm"])
        assert wx == pytest.approx(x, abs=0.01), role
        assert wy == pytest.approx(y, abs=0.01), role
        # No mirror in the frame, so the applied angle is the stored one + theta.
        applied = (by_role[role]["angle_deg"] + frame.rotation_deg) % 360.0
        assert (applied - angle) % 360.0 in (0.0,) or \
            abs((applied - angle + 180.0) % 360.0 - 180.0) < 0.01, role


def test_the_re_read_says_in_the_log_that_the_frame_followed_the_node():
    """К17 (plan_2026_10_12_reread_frame_mirror): the user is TOLD. The plan
    gave the wording: "the cluster was rearranged; the frame follows the tree
    node …, the fit suggested …". Silence here is the half of the defect that
    cost Denis an evening."""
    components, footprints, adapter, cfg = _denis_case()
    plan = build_refresh_plan(components, [], [], footprints, [], [], adapter,
                              origin_role="C_IN_BULK", cell_layer="F.Cu",
                              config=cfg, cell_name="p5v_in",
                              chosen_cluster="P5V_IN", chosen_sheet="Power")
    assert any("follows the tree node power/p5v_in_power" in w
               for w in plan.warnings), plan.warnings
    assert any("not a rigid copy" in w for w in plan.warnings)
