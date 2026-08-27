# tests/test_tree_position.py
"""Tests for kicadstamp/tree_position.py — position resolution + curated
redraw planning for the s-expr trees layer (Phase 4,
design_2026_08_26_tree_position_resolution.md, Q1-Q5). Written by Claude,
not DeepSeek — per the implementation handoff's own agreement
(handoff_2026_08_27_sexp_trees_implementation.md, Phase 4).

node_offset()/node_position() are pure geometry — tested directly, no
adapter. resolve_record_live_position()/resolve_base_live_position() are
THIN DISPATCHERS over already-tested resolvers (ClonePositionCalculator,
ComponentResolver, resolve_point_chain, resolve_target_position/...) — those
resolvers have their own test suites (test_point_resolver.py,
test_coordinate_position_calculator.py, ...); here we monkeypatch them
inside kicadstamp.tree_position's own namespace and verify the DISPATCH
itself: the right resolver gets called with the right arguments, and the
result is combined correctly — not re-testing each resolver's internals.
curated_redraw_plan() is tested with hand-built LinkedTree/LinkedNode/
LinkedAnchor structures (pure data, no adapter, mirrors test_link_trees.py's
own "build Records directly" pattern) — DFS order, per-kind name emission
(Q3), and the parent-not-in-selection warning (Q4).
"""
import pytest

from kicadstamp.anchor_graph import Record
from kicadstamp.domain.geometry import Vector2
from kicadstamp.geometry.spoke_layout import local_to_absolute
from kicadstamp.link_trees import LinkedAnchor, LinkedNode, LinkedTree
from kicadstamp.trees import TreeAnchor, TreeNode
from kicadstamp.tree_position import (
    curated_redraw_plan,
    node_offset,
    node_position,
    resolve_record_live_position,
)
from kicadstamp.utils.units import MM

_ORIGIN = Vector2.from_xy(0, 0)


def _node_dc(ref="N", kind=None, xy=None, polar=None, rotation=0.0,
             name=None, group=None, children=None) -> TreeNode:
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=polar, rotation=rotation,
                    name=name, group=group, children=children or [])


def _record(kind, name, obj=None) -> Record:
    return Record(kind=kind, obj=obj if obj is not None else object(), name=name,
                  sheet=None, anchor_ref=None, anchor_role=None, anchor_sheet=None,
                  anchor_cluster=None, anchor_point=None, params={})


def _linked_node(ref, record=None, is_external=False, children=None) -> LinkedNode:
    return LinkedNode(node=_node_dc(ref=ref), record=record,
                      is_external=is_external, children=children or [])


# ═══════════════════════════════════════════════════════════════════════════
# node_offset / node_position — pure geometry
# ═══════════════════════════════════════════════════════════════════════════

def test_node_offset_xy_converts_mm_to_nm_flat():
    node = _node_dc(xy=(5.0, 2.0))
    off = node_offset(node)
    assert off.x == 5 * MM
    assert off.y == 2 * MM


def test_node_offset_neither_xy_nor_polar_is_zero():
    node = _node_dc(xy=None, polar=None)
    off = node_offset(node)
    assert off.x == 0 and off.y == 0


def test_node_offset_polar_zero_angle_is_along_x():
    """angle_deg=0 must give (radius_mm, 0) regardless of rotation direction
    convention — a convention-independent sanity check."""
    node = _node_dc(polar=(3.0, 0.0))
    off = node_offset(node)
    assert off.x == 3 * MM
    assert off.y == 0


def test_node_offset_polar_180_degrees_flips_x():
    """angle_deg=180 must give (-radius_mm, 0) in EITHER rotation direction
    convention — the other convention-independent sanity check."""
    node = _node_dc(polar=(3.0, 180.0))
    off = node_offset(node)
    assert off.x == pytest.approx(-3 * MM, abs=2)
    assert off.y == pytest.approx(0, abs=2)


def test_node_offset_polar_delegates_to_local_to_absolute():
    """For a generic angle, node_offset must match calling
    local_to_absolute(origin=0, radius, 0, angle) directly — verifies
    node_offset is a faithful delegate (same args, same primitive), not a
    reimplementation that could drift from the project's one true rotation
    convention."""
    node = _node_dc(polar=(7.5, 37.0))
    off = node_offset(node)
    expected = local_to_absolute(_ORIGIN, 7.5, 0.0, 37.0)
    assert off.x == expected.x
    assert off.y == expected.y


def test_node_offset_own_rotation_field_does_not_affect_offset():
    """A node's own `rotation` rotates its OWN geometry later — it must NOT
    feed into the offset vector itself (design Q2). Same xy, different
    rotation -> identical offset."""
    a = node_offset(_node_dc(xy=(4.0, 1.0), rotation=0.0))
    b = node_offset(_node_dc(xy=(4.0, 1.0), rotation=90.0))
    assert (a.x, a.y) == (b.x, b.y)


def test_node_position_is_flat_composition():
    parent = Vector2.from_xy(10 * MM, 20 * MM)
    node = _node_dc(xy=(5.0, -3.0))
    pos = node_position(node, parent)
    assert pos.x == 15 * MM
    assert pos.y == 17 * MM


def test_node_position_parent_rotation_never_applied_to_child_offset():
    """There is no parent-rotation parameter at all in node_position's
    signature — this test exists as a guard: if someone "fixes" this by
    adding rotation-composition later, they must consciously break this
    signature/test, not silently slip it in (design §1.3, explicitly NOT a
    CellPlacement-style rotated-local-frame composition)."""
    import inspect
    params = list(inspect.signature(node_position).parameters)
    assert params == ["node", "parent_position"]


# ═══════════════════════════════════════════════════════════════════════════
# resolve_record_live_position — thin kind dispatcher (monkeypatched deps)
# ═══════════════════════════════════════════════════════════════════════════

def test_dispatch_clone_combines_anchor_and_shift(monkeypatch):
    import kicadstamp.tree_position as tp

    class _FakeCalc:
        def __init__(self, adapter, cfg, sheet_names, resolved_points):
            self.args = (adapter, cfg, sheet_names, resolved_points)

        def _resolve_anchor(self, obj):
            assert obj is rec.obj
            return Vector2.from_xy(10 * MM, 20 * MM)

    monkeypatch.setattr(tp, "ClonePositionCalculator", _FakeCalc)
    monkeypatch.setattr(tp, "clone_shift_mm", lambda obj: (1.0, -2.0))

    rec = _record("clone", "CL_A")
    pos = tp.resolve_record_live_position("adapter", "cfg", rec, "points", "sheets")
    assert pos.x == 10 * MM + 1 * MM
    assert pos.y == 20 * MM - 2 * MM


def test_dispatch_clone_absolute_mode_uses_origin_when_no_anchor(monkeypatch):
    """_resolve_anchor() returning None means absolute-coordinate mode — the
    shift is then relative to (0, 0), not left undefined."""
    import kicadstamp.tree_position as tp

    class _FakeCalc:
        def __init__(self, *a, **k):
            pass

        def _resolve_anchor(self, obj):
            return None

    monkeypatch.setattr(tp, "ClonePositionCalculator", _FakeCalc)
    monkeypatch.setattr(tp, "clone_shift_mm", lambda obj: (5.0, 5.0))

    rec = _record("clone", "CL_ABS")
    pos = tp.resolve_record_live_position("adapter", "cfg", rec, "points", "sheets")
    assert pos.x == 5 * MM
    assert pos.y == 5 * MM


def test_dispatch_point_uses_resolve_point_chain(monkeypatch):
    import kicadstamp.tree_position as tp

    calls = []

    class _FakeResolved:
        position = Vector2.from_xy(3 * MM, 4 * MM)

    def _fake_chain(adapter, points, name, sheet_names):
        calls.append((adapter, points, name, sheet_names))
        return _FakeResolved()

    monkeypatch.setattr(tp, "resolve_point_chain", _fake_chain)

    rec = _record("point", "pnt")

    class _Cfg:
        points = {"pnt": object()}

    pos = tp.resolve_record_live_position("adapter", _Cfg(), rec, "points_arg", "sheets_arg")
    assert pos.x == 3 * MM and pos.y == 4 * MM
    assert calls == [("adapter", {"pnt": _Cfg.points["pnt"]}, "pnt", "sheets_arg")]


def test_dispatch_rule_no_anchor_pad_uses_footprint_centre(monkeypatch):
    import kicadstamp.tree_position as tp

    class _FakeFp:
        position = Vector2.from_xy(50 * MM, 60 * MM)

    class _FakeResolver:
        def __init__(self, adapter, cfg, sheet_names):
            pass

        def resolve_anchor_fp(self, anchor_ref, anchor_role, anchor_sheet,
                              anchor_cluster, label=""):
            return _FakeFp()

    monkeypatch.setattr(tp, "ComponentResolver", _FakeResolver)

    obj = object()
    rec = Record(kind="rule", obj=obj, name="R1", sheet=None, anchor_ref="U1",
                anchor_role=None, anchor_sheet=None, anchor_cluster=None,
                anchor_point=None, params={})
    pos = tp.resolve_record_live_position("adapter", "cfg", rec, "points", "sheets")
    assert pos.x == 50 * MM and pos.y == 60 * MM


def test_dispatch_rule_with_anchor_pad_narrows_to_pad(monkeypatch):
    import kicadstamp.tree_position as tp

    class _FakeFp:
        position = Vector2.from_xy(50 * MM, 60 * MM)

    class _FakeResolver:
        def __init__(self, *a, **k):
            pass

        def resolve_anchor_fp(self, *a, **k):
            return _FakeFp()

    calls = []

    def _fake_pad_pos(adapter, fp, anchor_pad, label):
        calls.append((fp, anchor_pad, label))
        return Vector2.from_xy(51 * MM, 61 * MM)

    monkeypatch.setattr(tp, "ComponentResolver", _FakeResolver)
    monkeypatch.setattr(tp, "resolve_anchor_pad_position", _fake_pad_pos)

    class _Obj:
        anchor_pad = "1"

    rec = Record(kind="rule", obj=_Obj(), name="R1", sheet=None, anchor_ref="U1",
                anchor_role=None, anchor_sheet=None, anchor_cluster=None,
                anchor_point=None, params={})
    pos = tp.resolve_record_live_position("adapter", "cfg", rec, "points", "sheets")
    assert pos.x == 51 * MM and pos.y == 61 * MM
    assert calls[0][1] == "1" and calls[0][2] == "R1"


def test_dispatch_coordinate_absolute_mode(monkeypatch):
    import kicadstamp.tree_position as tp

    monkeypatch.setattr(tp, "_has_external_anchor", lambda cp: False)
    monkeypatch.setattr(tp, "resolve_target_position",
                        lambda cp: (Vector2.from_xy(7 * MM, 8 * MM), 0.0))

    rec = _record("coordinate", "CP1")
    pos = tp.resolve_record_live_position("adapter", "cfg", rec, "points", "sheets")
    assert pos.x == 7 * MM and pos.y == 8 * MM


def test_dispatch_coordinate_anchor_relative_mode(monkeypatch):
    import kicadstamp.tree_position as tp

    calls = []
    monkeypatch.setattr(tp, "_has_external_anchor", lambda cp: True)

    def _fake_external_anchor(adapter, cp, points, sheet_names, label):
        calls.append((adapter, cp, points, sheet_names, label))
        return Vector2.from_xy(100 * MM, 100 * MM)

    monkeypatch.setattr(tp, "_resolve_external_anchor", _fake_external_anchor)
    monkeypatch.setattr(tp, "_anchor_offset_mm", lambda cp: (2.0, -1.0))

    class _Cfg:
        points = {"pnt": object()}

    rec = _record("coordinate", "CP2")
    pos = tp.resolve_record_live_position("adapter", _Cfg(), rec, "points", "sheets")
    assert pos.x == 102 * MM and pos.y == 99 * MM
    assert calls == [("adapter", rec.obj, _Cfg.points, "sheets", "CP2")]


def test_dispatch_unreachable_kind_raises_assertion_error():
    """Defense in depth: link_trees only ever produces node/anchor records
    with kind in clone/rule/coordinate/point (net_trace/thermal_via are
    excluded from the 4 placeable sections) — but if a future change ever
    lets one through, this dispatcher must fail loudly, not silently return
    a wrong position."""
    rec = _record("thermal_via", "TVA1")
    with pytest.raises(AssertionError, match="thermal_via"):
        resolve_record_live_position("adapter", "cfg", rec, "points", "sheets")


# ═══════════════════════════════════════════════════════════════════════════
# resolve_base_live_position — external-vs-record entry point
# ═══════════════════════════════════════════════════════════════════════════

def test_base_position_external_ref_skips_kind_dispatch_entirely(monkeypatch):
    import kicadstamp.tree_position as tp

    calls = []

    def _fake_by_ref(adapter, ref, label):
        calls.append((adapter, ref, label))

        class _Fp:
            position = Vector2.from_xy(9 * MM, 9 * MM)
        return _Fp()

    monkeypatch.setattr(tp, "resolve_footprint_by_ref", _fake_by_ref)

    def _boom(*a, **k):
        raise AssertionError("must not be called for an external ref")
    monkeypatch.setattr(tp, "resolve_record_live_position", _boom)

    pos = tp.resolve_base_live_position("adapter", "cfg", "FPGA1", None, "points", "sheets")
    assert pos.x == 9 * MM and pos.y == 9 * MM
    assert calls == [("adapter", "FPGA1", "FPGA1")]


def test_base_position_real_record_delegates_to_dispatcher(monkeypatch):
    import kicadstamp.tree_position as tp

    rec = _record("clone", "CL_A")
    sentinel = Vector2.from_xy(1, 2)
    calls = []

    def _fake_dispatch(adapter, cfg, r, resolved_points, sheet_names):
        calls.append((adapter, cfg, r, resolved_points, sheet_names))
        return sentinel

    monkeypatch.setattr(tp, "resolve_record_live_position", _fake_dispatch)

    pos = tp.resolve_base_live_position("adapter", "cfg", "CL_A", rec, "points", "sheets")
    assert pos is sentinel
    assert calls == [("adapter", "cfg", rec, "points", "sheets")]


# ═══════════════════════════════════════════════════════════════════════════
# resolve_record_rotation_deg / resolve_base_rotation_deg — rotation twin of
# the position dispatcher above (same thin-kind-dispatcher discipline; rule/
# external are the ONLY kinds that touch the live board for rotation)
# ═══════════════════════════════════════════════════════════════════════════

def test_rotation_clone_reads_rotation_deg_straight_from_record():
    """clone's CURRENT rotation already lives in config (ClonePlacement.
    rotation_deg) — no adapter call needed at all (None adapter must work)."""
    import kicadstamp.tree_position as tp

    class _Obj:
        rotation_deg = 42.0

    rec = _record("clone", "CL_A", obj=_Obj())
    assert tp.resolve_record_rotation_deg(None, "cfg", rec, "sheets") == 42.0


def test_rotation_coordinate_via_resolve_target_position(monkeypatch):
    """coordinate's rotation comes from resolve_target_position's already-
    returned second value (tree-placed records never carry an inline anchor
    per FORK-1, so it is always the absolute branch)."""
    import kicadstamp.tree_position as tp

    monkeypatch.setattr(tp, "resolve_target_position",
                        lambda cp: (Vector2.from_xy(1, 1), 90.0))
    rec = _record("coordinate", "CP1")
    assert tp.resolve_record_rotation_deg(None, "cfg", rec, "sheets") == 90.0


def test_rotation_rule_via_live_footprint_angle(monkeypatch):
    """rule has no rotation field of its own — read the anchor footprint's
    LIVE angle_deg (the genuinely-live branch)."""
    import kicadstamp.tree_position as tp

    class _FakeFp:
        angle_deg = 33.0

    class _FakeResolver:
        def __init__(self, adapter, cfg, sheet_names):
            self.args = (adapter, cfg, sheet_names)

        def resolve_anchor_fp(self, anchor_ref, anchor_role, anchor_sheet,
                              anchor_cluster, label=""):
            assert (anchor_ref, anchor_role, anchor_sheet, anchor_cluster) == \
                ("U1", None, None, None)
            assert label == "R1"
            return _FakeFp()

    monkeypatch.setattr(tp, "ComponentResolver", _FakeResolver)

    rec = Record(kind="rule", obj=object(), name="R1", sheet=None,
                 anchor_ref="U1", anchor_role=None, anchor_sheet=None,
                 anchor_cluster=None, anchor_point=None, params={})
    assert tp.resolve_record_rotation_deg("adapter", "cfg", rec, "sheets") == 33.0


def test_rotation_point_returns_none():
    """point has no rotation concept by design (config/points.py) — None, not
    a fabricated 0 (the caller must treat None as "not available")."""
    import kicadstamp.tree_position as tp
    rec = _record("point", "PNT")
    assert tp.resolve_record_rotation_deg(None, "cfg", rec, "sheets") is None


def test_rotation_unreachable_kind_raises_assertion_error():
    """Same defense in depth as the position dispatcher: net_trace/thermal_via
    must never reach the rotation dispatcher — fail loudly, not silently 0."""
    import kicadstamp.tree_position as tp
    rec = _record("thermal_via", "TVA1")
    with pytest.raises(AssertionError, match="thermal_via"):
        tp.resolve_record_rotation_deg(None, "cfg", rec, "sheets")


def test_base_rotation_external_uses_footprint_angle(monkeypatch):
    """record is None -> external ref, live footprint's own angle_deg — the
    kind dispatcher must NOT be called at all."""
    import kicadstamp.tree_position as tp

    calls = []

    def _fake_by_ref(adapter, ref, label):
        calls.append((adapter, ref, label))

        class _Fp:
            angle_deg = 12.5
        return _Fp()

    monkeypatch.setattr(tp, "resolve_footprint_by_ref", _fake_by_ref)

    def _boom(*a, **k):
        raise AssertionError("must not be called for an external ref")
    monkeypatch.setattr(tp, "resolve_record_rotation_deg", _boom)

    assert tp.resolve_base_rotation_deg("adapter", "cfg", "FPGA1", None, "sheets") == 12.5
    assert calls == [("adapter", "FPGA1", "FPGA1")]


def test_base_rotation_real_record_delegates_to_dispatcher(monkeypatch):
    """record is not None -> resolve_record_rotation_deg (thin delegation)."""
    import kicadstamp.tree_position as tp

    rec = _record("clone", "CL_A")
    calls = []

    def _fake_dispatch(adapter, cfg, r, sheet_names):
        calls.append((adapter, cfg, r, sheet_names))
        return 7.0

    monkeypatch.setattr(tp, "resolve_record_rotation_deg", _fake_dispatch)
    assert tp.resolve_base_rotation_deg("adapter", "cfg", "CL_A", rec, "sheets") == 7.0
    assert calls == [("adapter", "cfg", rec, "sheets")]


# ═══════════════════════════════════════════════════════════════════════════
# relative_rotation_deg — the (a - b + 180) % 360 - 180 normalization
# ═══════════════════════════════════════════════════════════════════════════

def test_relative_rotation_deg_simple():
    import kicadstamp.tree_position as tp
    assert tp.relative_rotation_deg(30.0, 10.0) == 20.0


def test_relative_rotation_deg_wraps_negative():
    """350 vs 10: 350 - 10 = 340 -> wraps to -20 (short way round the circle)."""
    import kicadstamp.tree_position as tp
    assert tp.relative_rotation_deg(350.0, 10.0) == pytest.approx(-20.0)


def test_relative_rotation_deg_wraps_positive():
    """10 vs 350: 10 - 350 = -340 -> wraps to +20."""
    import kicadstamp.tree_position as tp
    assert tp.relative_rotation_deg(10.0, 350.0) == pytest.approx(20.0)


# ═══════════════════════════════════════════════════════════════════════════
# curated_redraw_plan — DFS order, name emission (Q3), warnings (Q4)
# ═══════════════════════════════════════════════════════════════════════════

def test_plan_emits_selected_clone_node_no_warning_when_anchor_selected():
    anchor = LinkedAnchor(anchor=TreeAnchor(ref="CONN", is_origin=False),
                          record=_record("clone", "CONN"), is_origin=False,
                          is_external=False)
    node = _linked_node("AMS", record=_record("clone", "AMS"))
    tree = LinkedTree(name="t", anchor=anchor, nodes=[node])

    names, warnings = curated_redraw_plan(tree, {"CONN", "AMS"})
    assert names == ["AMS"]
    assert warnings == []


def test_plan_warns_when_config_anchor_not_in_selection():
    anchor = LinkedAnchor(anchor=TreeAnchor(ref="CONN", is_origin=False),
                          record=_record("clone", "CONN"), is_origin=False,
                          is_external=False)
    node = _linked_node("AMS", record=_record("clone", "AMS"))
    tree = LinkedTree(name="t", anchor=anchor, nodes=[node])

    names, warnings = curated_redraw_plan(tree, {"AMS"})  # CONN not selected
    assert names == ["AMS"]
    assert len(warnings) == 1
    assert "AMS" in warnings[0] and "CONN" in warnings[0]


def test_plan_origin_anchor_never_warns():
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    node = _linked_node("R_DEBUG", record=_record("clone", "R_DEBUG"))
    tree = LinkedTree(name="t", anchor=anchor, nodes=[node])

    names, warnings = curated_redraw_plan(tree, {"R_DEBUG"})
    assert names == ["R_DEBUG"]
    assert warnings == []


def test_plan_external_anchor_never_warns():
    anchor = LinkedAnchor(anchor=TreeAnchor(ref="FPGA1", is_origin=False),
                          record=None, is_origin=False, is_external=True)
    node = _linked_node("AMS", record=_record("clone", "AMS"))
    tree = LinkedTree(name="t", anchor=anchor, nodes=[node])

    names, warnings = curated_redraw_plan(tree, {"AMS"})
    assert names == ["AMS"]
    assert warnings == []


def test_plan_point_node_walked_but_never_emitted():
    """A point node is a legal live base for its children (apply_only_filter
    has no points support at all — points can never be redrawn themselves),
    so it must be walked (children still resolve/emit) but never appear in
    `names`, selected or not."""
    child = _linked_node("R_OUT", record=_record("clone", "R_OUT"))
    point_node = _linked_node("PNT", record=_record("point", "PNT"), children=[child])
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    tree = LinkedTree(name="t", anchor=anchor, nodes=[point_node])

    names, warnings = curated_redraw_plan(tree, {"PNT", "R_OUT"})
    assert names == ["R_OUT"]
    assert warnings == []


def test_plan_external_node_walked_but_never_emitted():
    child = _linked_node("R_OUT", record=_record("clone", "R_OUT"))
    ext_node = _linked_node("FPGA1", record=None, is_external=True, children=[child])
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    tree = LinkedTree(name="t", anchor=anchor, nodes=[ext_node])

    names, warnings = curated_redraw_plan(tree, {"FPGA1", "R_OUT"})
    assert names == ["R_OUT"]
    assert warnings == []


def test_plan_unselected_node_not_emitted_but_still_walked_for_children():
    """A node that's NOT selected must not appear in `names`, but its
    (selected) children must still be reached — and must warn, since their
    live parent base wasn't just redrawn."""
    child = _linked_node("C_OUT", record=_record("clone", "C_OUT"))
    parent = _linked_node("AMS", record=_record("clone", "AMS"), children=[child])
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    tree = LinkedTree(name="t", anchor=anchor, nodes=[parent])

    names, warnings = curated_redraw_plan(tree, {"C_OUT"})  # AMS not selected
    assert names == ["C_OUT"]
    assert len(warnings) == 1
    assert "C_OUT" in warnings[0] and "AMS" in warnings[0]


def test_plan_dfs_order_parent_strictly_before_child():
    grandchild = _linked_node("GC", record=_record("clone", "GC"))
    child = _linked_node("C", record=_record("clone", "C"), children=[grandchild])
    parent = _linked_node("P", record=_record("clone", "P"), children=[child])
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    tree = LinkedTree(name="t", anchor=anchor, nodes=[parent])

    names, warnings = curated_redraw_plan(tree, {"P", "C", "GC"})
    assert names == ["P", "C", "GC"]
    assert warnings == []


def test_plan_multiple_top_level_branches_processed_independently():
    a = _linked_node("A", record=_record("clone", "A"))
    b = _linked_node("B", record=_record("clone", "B"))
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    tree = LinkedTree(name="t", anchor=anchor, nodes=[a, b])

    names, warnings = curated_redraw_plan(tree, {"A", "B"})
    assert names == ["A", "B"]
    assert warnings == []


def test_plan_no_selection_emits_nothing():
    node = _linked_node("A", record=_record("clone", "A"))
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    tree = LinkedTree(name="t", anchor=anchor, nodes=[node])

    names, warnings = curated_redraw_plan(tree, set())
    assert names == []
    assert warnings == []
