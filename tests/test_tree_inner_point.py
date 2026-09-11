# tests/test_tree_inner_point.py
"""The tree's own INNER point and own angle — stage Б2
(plan_2026_09_11_tree_inner_point_and_rotation, design §3.1/§3.4/§V.7).

These tests cover the parts the older suites did not:
  * V.7.2 items 9-12 — the tree's `rotation` is a DОВОРОТ on top of the outer
    anchor's angle, turning the content around the tree's INNER point;
  * V.7.1 item 8 — a tree without mount nodes resolves its inner point OFFLINE
    (no live board at all);
  * V.7.3 item 14 — THE headline one: the LIVE layout path
    (`layout_tree_from_anchor` -> tree_layout_base) and the MATERIALIZATION
    path (`materialize_entity_placements` -> entity_placement._anchor_base)
    must agree bit-for-bit. This is where the two paths could silently drift.

The fake board below is deliberately rotated (20 deg) with a non-zero pivot,
so a missing rotation or a missing pivot-inversion shows up as a wrong number
rather than a coincidence.
"""
from unittest.mock import MagicMock

import pytest
from kipy.board_types import FootprintInstance

from kicadstamp.config import Cell, Config, Entity, TemplateComponentSlot
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.spoke_layout import local_to_absolute
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.tree_position import (
    layout_tree_from_anchor,
    layout_tree_from_base,
    tree_effective_base,
    tree_pivot_offset,
)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from kicadstamp.utils.units import MM

_ORIGIN = Vector2.from_xy(0, 0)

# (ref, role, cluster, x_mm, y_mm, angle_deg) — one anchor component, rotated.
_ANCHOR = ("IC1", "R", "CL", 100.0, 50.0, 20.0)


def _fake_adapter(anchor=_ANCHOR):
    fp = MagicMock(spec=FootprintInstance)
    ref, role, cluster, x_mm, y_mm, angle = anchor
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
            return cluster
        return None

    adapter.get_field_value.side_effect = _field
    adapter.get_selected_items.return_value = []
    adapter.get_pad_by_number.side_effect = lambda _fp, _num: None
    return adapter


def _node(ref, xy=None, rotation=0.0, kind=None, children=None):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rotation,
                    name=None, group=None, children=children or [])


def _tree(*, pivot_xy=None, pivot_polar=None, pivot_ref=None, rotation=0.0,
          nodes=None, anchor=None):
    return Tree(name="t", anchor=anchor or TreeAnchor(role="R", anchor_cluster="CL"),
                nodes=nodes if nodes is not None else [_node("E1", xy=(5.0, 0.0))],
                pivot_xy=pivot_xy, pivot_polar=pivot_polar, pivot_ref=pivot_ref,
                rotation=rotation)


def _mm(vec):
    return vec.x / MM, vec.y / MM


# ── V.7.1 item 8: the inner point is OFFLINE (no live board) ──────────────

def test_inner_point_resolves_without_a_live_board():
    """A tree with no mount nodes resolves pivot-xy / pivot-polar / pivot-ref
    with NO adapter at all — pure geometry (plan §V.1.2)."""
    assert _mm(tree_pivot_offset(_tree(pivot_xy=(2.0, 1.0)))) == (2.0, 1.0)
    assert tree_pivot_offset(_tree(pivot_polar=(5.0, 0.0))) == \
        local_to_absolute(_ORIGIN, 5.0, 0.0, 0.0)
    tree = _tree(pivot_ref="E1", nodes=[_node("E1", xy=(1.5, -2.5))])
    assert _mm(tree_pivot_offset(tree, {"t": tree})) == (1.5, -2.5)
    assert tree_pivot_offset(_tree()) == _ORIGIN     # absent = the tree's origin


# ── V.7.2: the tree's own angle is a DОВОРОТ around the inner point ───────

def test_rotation_zero_and_no_pivot_is_the_identity():
    """V.7.2 item 9: rotation 0 + no inner point -> the effective base IS the
    marker (every pre-existing tree keeps its old numbers)."""
    marker = Vector2.from_xy_mm(10.0, 5.0)
    assert tree_effective_base(_tree(), marker, 30.0) == (marker, 30.0)


def test_own_rotation_is_added_on_top_of_the_anchor_angle():
    """V.7.2 items 10/11: the tree's rotation is a DОВОРОТ (increment), not a
    replacement — the anchor supplies the base angle and the tree adds to it."""
    marker = Vector2.from_xy_mm(10.0, 0.0)
    _pos, rot = tree_effective_base(_tree(rotation=30.0), marker, 0.0)
    assert rot == pytest.approx(30.0)              # 0 + 30
    _pos, rot = tree_effective_base(_tree(rotation=30.0), marker, -90.0)
    assert rot == pytest.approx(-60.0)             # -90 + 30, NOT a replacement


def test_rotation_turns_around_the_inner_point_not_the_origin():
    """V.7.2 item 12: with a non-zero inner point the inversion differs from
    "rotate around the frame origin" — the named point lands on the marker
    (asserted through the LAYOUT, so the test states the invariant rather than
    a convention-dependent sign)."""
    marker = Vector2.from_xy_mm(10.0, 0.0)
    # "P" sits AT the inner point, so it must land exactly on the marker.
    tree = _tree(pivot_xy=(2.0, 0.0), rotation=90.0,
                 nodes=[_node("P", xy=(2.0, 0.0))])
    pos, rot = tree_effective_base(tree, marker, 0.0)
    assert rot == pytest.approx(90.0)
    out = layout_tree_from_base(tree, pos, rot)
    assert _mm(out["P"][0]) == pytest.approx((10.0, 0.0), abs=1e-6)
    # ... and the base itself is NOT the marker (the naive "rotate about the
    # frame origin" answer would have left it there).
    assert _mm(pos) != pytest.approx((10.0, 0.0))


def test_unknown_anchor_rotation_falls_back_to_the_trees_own_angle():
    """V.7.2 item 13 / §V.2.3: a (point ...) anchor carries NO orientation, so
    the angle comes from the tree's own EXPLICIT field — the old silent zero in
    gui/docks/cascade.py is replaced by this single documented place."""
    marker = Vector2.from_xy_mm(1.0, 1.0)
    _pos, rot = tree_effective_base(_tree(rotation=45.0), marker, None)
    assert rot == pytest.approx(45.0)


# ── V.7.3 item 14: the LIVE path and MATERIALIZATION must agree ───────────

def _placed_cfg(*, tree_rotation=0.0, pivot_xy=None):
    cell = Cell(name="c", components=[TemplateComponentSlot(role="R")])
    entity = Entity(name="E1", cell="c")
    tree = Tree(name="t", anchor=TreeAnchor(role="R", anchor_cluster="CL"),
                nodes=[_node("E1", xy=(5.0, 2.0), kind="placement")],
                pivot_xy=pivot_xy, rotation=tree_rotation)
    return Config(cells={"c": cell}, entities=[entity], trees=[tree])


@pytest.mark.parametrize("tree_rotation,pivot_xy", [
    (0.0, None),          # the pre-2026-09-11 baseline
    (30.0, None),         # own angle only
    (0.0, (2.0, 1.0)),    # inner point only
    (35.0, (2.0, 1.0)),   # both at once
])
def test_live_layout_and_materialization_agree(tree_rotation, pivot_xy):
    """V.7.3 item 14 — THE headline test of stage Б2.

    The live curated path (layout_tree_from_anchor -> tree_layout_base) and the
    materialization path (materialize_entity_placements -> _anchor_base) are two
    DIFFERENT anchor resolvers; a tree inner point / own angle applied in only
    one of them is exactly how the two would drift apart unnoticed. The anchor
    footprint is deliberately rotated (20 deg) so a dropped angle is visible.
    """
    adapter = _fake_adapter()
    cfg = _placed_cfg(tree_rotation=tree_rotation, pivot_xy=pivot_xy)
    forest = {t.name: t for t in cfg.trees}

    live = layout_tree_from_anchor(cfg.trees[0], forest,
                                   adapter=adapter, cfg=cfg, sheet_names={})
    live_pos, live_rot = live["E1"]

    materialized = materialize_entity_placements(adapter, cfg, {})
    assert len(materialized) == 1
    clone = materialized[0]
    mat_pos = Vector2.from_xy(int(clone.xy[0] * MM), int(clone.xy[1] * MM))

    assert _mm(live_pos) == pytest.approx((clone.xy[0], clone.xy[1]), abs=1e-6)
    assert live_rot == pytest.approx(clone.rotation_deg, abs=1e-9)
    # ... and the anchor really was rotated, so the equality above is not the
    # trivial "everything is 0" case.
    assert abs(live_rot) > 1.0
    assert _mm(mat_pos) != pytest.approx((5.0, 2.0))


def test_inner_point_lands_exactly_on_the_live_anchor():
    """The invariant itself: with an inner point, THAT point of the tree lands
    on the anchor's live position (plan §V.2.2) — the same for the standalone
    path and for the embedded one (layout_tree_from_base's module branch)."""
    adapter = _fake_adapter()
    cfg = _placed_cfg(pivot_xy=(5.0, 2.0))     # == the node's own offset
    forest = {t.name: t for t in cfg.trees}
    live = layout_tree_from_anchor(cfg.trees[0], forest,
                                   adapter=adapter, cfg=cfg, sheet_names={})
    pos, _rot = live["E1"]
    # The anchor is at (100, 50) mm (role R, rotated 20 deg) -> the node whose
    # local offset IS the inner point must land exactly there.
    assert _mm(pos) == pytest.approx((100.0, 50.0), abs=1e-6)


def test_module_ref_cannot_be_the_inner_point(tmp_path):
    """A module node places no record of its own, so the layout cannot resolve
    its position — the grammar rejects it at load (early, not at apply time)."""
    from kicadstamp.trees import tree_from_dict
    with pytest.raises(ValidationError, match="cannot resolve yet"):
        tree_from_dict({"name": "t", "pivot_ref": "m",
                        "nodes": [{"ref": "m", "kind": "module"}]})


# ── P.1 (plan_2026_09_11_pivot_ref_mount_ancestor): a mount node's SUBTREE is ─
# ── barred as a handle, but a branch OUTSIDE it must still resolve LOCALLY. ───

def test_pivot_ref_outside_the_mount_subtree_stays_local(monkeypatch):
    """P.5.1 item 4 — a tree that HAS mount nodes may still hang its handle on a
    branch WITHOUT a mount ancestor. `tree_pivot_offset` must then return that
    node's LOCAL offset, NOT the absolute board position its mount siblings
    resolve to (plan §P.1.2: the mixed map is exactly the bug being closed).
    Checked by NUMBER, not by the mere absence of an exception."""
    import kicadstamp.tree_position as tp

    class _FakeFp:
        position = Vector2.from_xy(300 * MM, 400 * MM)   # deliberately NOT local
        angle_deg = 90.0

    class _FakeResolver:
        def __init__(self, *a, **k):
            pass

        def resolve_anchor_fp(self, *a, **k):
            return _FakeFp()

    monkeypatch.setattr(tp, "ComponentResolver", _FakeResolver)

    mount = _node("M1", kind="mount")
    mount.anchor = TreeAnchor(role="R", anchor_cluster="CL")
    mount.children = [_node("E1", kind="placement", xy=(1.0, 1.0))]
    free = _node("P1", xy=(2.0, 3.0), children=[_node("P2", xy=(0.5, 0.0))])
    tree = _tree(pivot_ref="P2", nodes=[mount, free])

    offset = tree_pivot_offset(tree, {"t": tree}, adapter=object(),
                               cfg=None, sheet_names={})
    # LOCAL (2.0, 3.0) + child (0.5, 0.0); a leaked absolute mount base would
    # have put this near (300+, 400+) mm.
    assert _mm(offset) == pytest.approx((2.5, 3.0), abs=1e-9)
