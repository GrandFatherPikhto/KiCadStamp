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
from kicadstamp.config import ClonePlacement, CoordinatePlacement
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.spoke_layout import local_to_absolute
from kicadstamp.link_trees import LinkedAnchor, LinkedNode, LinkedTree
from kicadstamp.trees import TreeAnchor, TreeNode
from kicadstamp.tree_position import (
    apply_rigid_override,
    capture_rigid_state,
    child_absolute_position,
    child_local_offset,
    curated_redraw_plan,
    curated_redraw_plan_forest,
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


def _linked_tree(name, anchor_ref=None, is_origin=False, nodes=None) -> LinkedTree:
    """A hand-built LinkedTree. origin anchor -> record None; ref anchor ->
    a synthetic placement Record (so cross-tree anchor edges resolve)."""
    anchor = LinkedAnchor(
        anchor=TreeAnchor(ref=anchor_ref, is_origin=is_origin),
        record=(_record("placement", anchor_ref) if anchor_ref and not is_origin else None),
        is_origin=is_origin,
        is_external=anchor_ref is None and not is_origin,
    )
    return LinkedTree(name=name, anchor=anchor, nodes=nodes or [])


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


def test_node_position_parent_rotation_applied_to_child_offset():
    """Plan 2026-08-29 (tree_live_rigid_redraw) §2 REVERSES design
    tree_position_resolution.md §1.3 by Denis's explicit request: the parent's
    rotation IS applied to the child's offset (the offset is expressed in the
    parent's LOCAL frame and rotated into the world before adding). This is
    the replacement for the old guard
    test_node_position_parent_rotation_never_applied_to_child_offset, which
    asserted the OPPOSITE — history preserved here and in the plan doc."""
    parent = Vector2.from_xy(10 * MM, 20 * MM)
    node = _node_dc(xy=(5.0, 0.0))            # offset 5 mm along X
    # KiCad Y-down convention: +90° maps (5,0) -> (0,-5).
    pos = node_position(node, parent, parent_rotation_deg=90.0)
    assert pos.x == 10 * MM
    assert pos.y == 20 * MM - 5 * MM


def test_node_position_flat_composition_default_rotation():
    """parent_rotation_deg defaults to 0.0 — the original flat composition is
    unchanged."""
    parent = Vector2.from_xy(10 * MM, 20 * MM)
    node = _node_dc(xy=(5.0, -3.0))
    pos = node_position(node, parent)
    assert pos.x == 15 * MM
    assert pos.y == 17 * MM


class TestRigidGroupRotationMath:
    """Plan 2026-08-29 §1/§4 — the pure capture->apply rigid-group math:
    child_local_offset captures the child's offset in the parent's LOCAL
    frame; child_absolute_position re-projects it into the parent's (possibly
    rotated) frame. Round-trip at the SAME rotation is identity; a changed
    rotation rotates the offset WITH the parent."""

    def _mm(self, x_mm, y_mm):
        return Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))

    def test_round_trip_same_rotation_is_identity(self):
        parent = self._mm(100.0, 50.0)
        child = self._mm(110.0, 45.0)
        local = child_local_offset(child, parent, 30.0)
        back = child_absolute_position(parent, 30.0, local)
        assert back.x == pytest.approx(child.x, abs=2)
        assert back.y == pytest.approx(child.y, abs=2)

    def test_rotation_applied_on_apply(self):
        """child offset (5,0) in the parent's frame; parent rotates 0->90 —
        the child's offset rotates WITH the parent (KiCad Y-down: +90° maps
        (5,0)->(0,-5), so the child lands 5 mm "down" from the parent)."""
        parent = self._mm(100.0, 50.0)
        child = self._mm(105.0, 50.0)
        local = child_local_offset(child, parent, 0.0)       # (5, 0) local
        new = child_absolute_position(parent, 90.0, local)   # rotated by 90
        assert new.x == pytest.approx(100.0 * MM, abs=2)
        assert new.y == pytest.approx(45.0 * MM, abs=2)

    def test_rotation_180_flips_offset(self):
        parent = self._mm(0.0, 0.0)
        child = self._mm(5.0, 0.0)
        local = child_local_offset(child, parent, 0.0)
        new = child_absolute_position(parent, 180.0, local)
        assert new.x == pytest.approx(-5.0 * MM, abs=2)
        assert new.y == pytest.approx(0.0 * MM, abs=2)

    def test_rotation_270_offset(self):
        """+270° in the KiCad Y-down convention maps (0,4) -> (-4,0)."""
        parent = self._mm(0.0, 0.0)
        child = self._mm(0.0, 4.0)
        local = child_local_offset(child, parent, 0.0)       # (0, 4) local
        new = child_absolute_position(parent, 270.0, local)  # -> (-4, 0)
        assert new.x == pytest.approx(-4.0 * MM, abs=2)
        assert new.y == pytest.approx(0.0 * MM, abs=2)

    def test_arbitrary_angle_round_trip(self):
        """Non-multiple-of-90 angle — the same discipline as the existing
        polar node_offset tests. KiCad Y-down convention:
        x' = py*sin + px*cos, y' = py*cos - px*sin."""
        import math
        parent = self._mm(30.0, -10.0)
        child = self._mm(35.0, -8.0)
        local = child_local_offset(child, parent, 33.0)
        # round-trip at the same rotation -> identity
        back = child_absolute_position(parent, 33.0, local)
        assert back.x == pytest.approx(child.x, abs=3)
        assert back.y == pytest.approx(child.y, abs=3)
        # capture at 0 + apply at 33 == rotate the (5,2) delta by 33
        local0 = child_local_offset(child, parent, 0.0)
        new = child_absolute_position(parent, 33.0, local0)
        rad = math.radians(33.0)
        px, py = 5.0, 2.0
        expected_x = parent.x + int((py * math.sin(rad) + px * math.cos(rad)) * MM)
        expected_y = parent.y + int((py * math.cos(rad) - px * math.sin(rad)) * MM)
        assert new.x == pytest.approx(expected_x, abs=3)
        assert new.y == pytest.approx(expected_y, abs=3)


class TestRigidGroupCaptureApply:
    """Plan 2026-08-29 §1/§4 — the capture->apply wiring helpers:
    _node_parent_map builds the parent index, capture_rigid_state snapshots
    each selected node's local offset + relative rotation BEFORE any move, and
    apply_rigid_override re-projects them into the parent's CURRENT frame at
    apply time. Live resolvers are monkeypatched (thin dispatchers already
    tested elsewhere in this file)."""

    def _tree(self, anchor_ref="FPGA", is_origin=False):
        anchor = LinkedAnchor(anchor=TreeAnchor(ref=anchor_ref, is_origin=is_origin),
                              record=None, is_origin=is_origin, is_external=not is_origin)
        child = LinkedNode(node=_node_dc(ref="D1", kind="clone"),
                           record=_record("clone", "D1"), is_external=False, children=[])
        return LinkedTree(name="fpga", anchor=anchor, nodes=[child])

    def _monkeypatch_live(self, monkeypatch, positions, rotations):
        import kicadstamp.tree_position as tp

        def fake_pos(adapter, cfg, ref, record, resolved_points, sheet_names):
            return positions[ref]

        def fake_rot(adapter, cfg, ref, record, sheet_names):
            return rotations[ref]

        monkeypatch.setattr(tp, "resolve_base_live_position", fake_pos)
        monkeypatch.setattr(tp, "resolve_base_rotation_deg", fake_rot)

    def test_parent_map_external_anchor(self):
        import kicadstamp.tree_position as tp
        assert tp._node_parent_map(self._tree())["D1"] == ("FPGA", None, True)

    def test_parent_map_origin_anchor(self):
        import kicadstamp.tree_position as tp
        assert tp._node_parent_map(self._tree(anchor_ref=None, is_origin=True))["D1"] \
            == (None, None, False)

    def test_capture_then_apply_follows_parent_translation_and_rotation(self, monkeypatch):
        """Parent moves (100->150) AND rotates 0->90 between capture and apply:
        the child follows — its captured local offset (5,0) re-projects to
        (0,-5) in the parent's new frame (KiCad Y-down)."""
        positions = {"FPGA": Vector2.from_xy(100 * MM, 50 * MM),
                     "D1": Vector2.from_xy(105 * MM, 50 * MM)}
        rotations = {"FPGA": 0.0, "D1": 0.0}
        self._monkeypatch_live(monkeypatch, positions, rotations)

        captures, parent_map = capture_rigid_state("adapter", "cfg", self._tree(), ["D1"], {})
        assert parent_map["D1"] == ("FPGA", None, True)
        cap = captures["D1"]
        assert cap.local_offset.x == 5 * MM
        assert cap.local_offset.y == 0
        assert cap.relative_rotation == pytest.approx(0.0)

        positions["FPGA"] = Vector2.from_xy(150 * MM, 50 * MM)
        rotations["FPGA"] = 90.0
        override = apply_rigid_override("adapter", "cfg", "FPGA", None, cap, {})
        assert override.position.x == 150 * MM
        assert override.position.y == 50 * MM - 5 * MM
        assert override.rotation_deg == pytest.approx(90.0)

    def test_child_relative_rotation_preserved(self, monkeypatch):
        """Child's own rotation relative to the parent is preserved across the
        parent's rotation change: child 30 vs parent 10 -> relative 20; parent
        now 90 -> child 110."""
        positions = {"FPGA": Vector2.from_xy(0, 0), "D1": Vector2.from_xy(5 * MM, 0)}
        rotations = {"FPGA": 10.0, "D1": 30.0}
        self._monkeypatch_live(monkeypatch, positions, rotations)

        captures, _pm = capture_rigid_state("adapter", "cfg", self._tree(), ["D1"], {})
        cap = captures["D1"]
        assert cap.relative_rotation == pytest.approx(20.0)

        rotations["FPGA"] = 90.0
        override = apply_rigid_override("adapter", "cfg", "FPGA", None, cap, {})
        assert override.rotation_deg == pytest.approx(110.0)

    def test_origin_anchor_parent_is_absolute_origin(self, monkeypatch):
        """An origin anchor (ref=None) is the absolute (0,0) point with 0.0
        rotation — the child's offset is its own absolute position."""
        positions = {"D1": Vector2.from_xy(7 * MM, 9 * MM)}
        rotations = {"D1": 0.0}
        self._monkeypatch_live(monkeypatch, positions, rotations)
        tree = self._tree(anchor_ref=None, is_origin=True)

        captures, parent_map = capture_rigid_state("adapter", "cfg", tree, ["D1"], {})
        assert parent_map["D1"] == (None, None, False)
        cap = captures["D1"]
        assert cap.local_offset.x == 7 * MM
        assert cap.local_offset.y == 9 * MM


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


def test_dispatch_placement_raises_validation_error_not_assertion():
    """Bug gate (2026-08-31, plan read_position_entity_parent_crash): an Entity
    (record kind "placement") carries NO record-level position — its live
    position is resolved from the TREE at apply time (Phase 4). Asking for a
    record-only live position of one must raise the USER-FACING ValidationError
    (the GUI Read-position handlers turn it into a QMessageBox warning), NEVER
    an AssertionError that escapes a GUI callback uncaught. The same kind is
    the ONLY such place: the sibling rotation dispatcher (resolve_record_
    rotation_deg) already honestly returns None for placement."""
    rec = _record("placement", "ENT_A")
    with pytest.raises(ValidationError, match="entity placement live position"):
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
    returned second value (ABSOLUTE mode — anchor-relative rotation is covered
    by test_dispatch_coordinate_rotation_anchor_relative_uses_rotation_rule,
    since FORK-1 moved to redraw-select time)."""
    import kicadstamp.tree_position as tp

    monkeypatch.setattr(tp, "_has_external_anchor", lambda cp: False)
    monkeypatch.setattr(tp, "resolve_target_position",
                        lambda cp: (Vector2.from_xy(1, 1), 90.0))
    cp = CoordinatePlacement(cluster="CP1", role="R", x_mm=1.0, y_mm=1.0)
    rec = _record("coordinate", "CP1", obj=cp)
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


# ── FORK-1 at redraw-select time (plan_2026_08_28_fork1_move_to_redraw_time.md) ──

def _record_with_inline_anchor(kind, name, field="anchor_role", value="FPGA"):
    """A Record whose obj carries an inline anchor (as a real config record
    would) — the redraw-time FORK-1 conflict state. Uses ClonePlacement, which
    already has every _INLINE_ANCHOR_FIELDS attribute."""
    obj = ClonePlacement(cluster=name, cell="c", xy=(0.0, 0.0))
    setattr(obj, field, value)
    return _record(kind, name, obj=obj)


def test_plan_selected_node_with_inline_anchor_emitted_with_warning():
    """REVERSED 2026-08-29 (plan_2026_08_29_fork1_rigid_redraw_override.md): a
    SELECTED node whose record carries an inline anchor IS emitted into `names`
    — rigid-redraw's PositionOverride is NON-persistent, so the record's own
    anchor_role keeps working for the regular (non-tree) Apply/Redraw and no
    persistent "two sources of truth" conflict exists. The warning is
    INFORMATIONAL, not a skip; the child still redraws (parent before child).
    Old behaviour (pre-2026-08-29): skipped with a "remove the inline anchor"
    warning — replaced, history preserved here and in the plan doc."""
    child = _linked_node("R_OUT", record=_record("clone", "R_OUT"))
    conflict_node = _linked_node(
        "CH2_DAC_BUF",
        record=_record_with_inline_anchor("clone", "CH2_DAC_BUF"),
        children=[child])
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    tree = LinkedTree(name="t", anchor=anchor, nodes=[conflict_node])

    names, warnings = curated_redraw_plan(tree, {"CH2_DAC_BUF", "R_OUT"})
    # Parent (conflict node) emitted before its child, and the conflict node IS
    # emitted (was skipped before 2026-08-29).
    assert names == ["CH2_DAC_BUF", "R_OUT"]
    assert any("also has its own" in w for w in warnings)
    assert any("TEMPORARILY" in w for w in warnings)


def test_plan_conflict_node_unselected_no_warning():
    """An unselected conflict node emits nothing and warns nothing — the
    conflict only matters at the moment of an actual redraw."""
    conflict_node = _linked_node(
        "CH2_DAC_BUF", record=_record_with_inline_anchor("clone", "CH2_DAC_BUF"))
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    tree = LinkedTree(name="t", anchor=anchor, nodes=[conflict_node])

    names, warnings = curated_redraw_plan(tree, set())
    assert names == []
    assert warnings == []


def test_plan_node_without_inline_anchor_emits_normally():
    """The main path is unchanged: a selected node whose record has no inline
    anchor emits into `names` as usual."""
    node = _linked_node("CL_A", record=_record("clone", "CL_A"))
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    tree = LinkedTree(name="t", anchor=anchor, nodes=[node])

    names, warnings = curated_redraw_plan(tree, {"CL_A"})
    assert names == ["CL_A"]
    assert warnings == []


# ── §4: coordinate-kind base rotation no longer trusts the old FORK-1 guarantee ──

def test_dispatch_coordinate_rotation_anchor_relative_uses_rotation_rule(monkeypatch):
    """FORK-1 moved to redraw-select time, so a coordinate-kind record used as
    a BASE may legally carry an inline anchor (anchor-relative mode). Its
    rotation must follow the SAME rule the move builder applies at plan time
    (rotation_deg if set, else angle_deg in polar-offset, else 0.0) — NOT the
    absolute-only resolve_target_position (which reads None/absent absolute
    fields and would assert or return a wrong None)."""
    import kicadstamp.tree_position as tp

    # Cartesian-offset anchor-relative, no explicit rotation -> default 0.0.
    cp = CoordinatePlacement(cluster="CP", role="R", anchor_role="FPGA",
                             x_mm=5.0, y_mm=2.0, rotation_deg=None)
    rec = _record("coordinate", "CP/R", obj=cp)
    assert tp.resolve_record_rotation_deg("adapter", "cfg", rec, "sheets") == 0.0

    # Polar-offset anchor-relative, no explicit rotation -> angle_deg.
    cp2 = CoordinatePlacement(cluster="CP2", role="R", anchor_role="FPGA",
                              radius_mm=3.0, angle_deg=45.0, rotation_deg=None)
    rec2 = _record("coordinate", "CP2/R", obj=cp2)
    assert tp.resolve_record_rotation_deg("adapter", "cfg", rec2, "sheets") == 45.0

    # Explicit rotation_deg wins regardless of the offset mode.
    cp3 = CoordinatePlacement(cluster="CP3", role="R", anchor_role="FPGA",
                              x_mm=1.0, y_mm=1.0, rotation_deg=90.0)
    rec3 = _record("coordinate", "CP3/R", obj=cp3)
    assert tp.resolve_record_rotation_deg("adapter", "cfg", rec3, "sheets") == 90.0


# ═══════════════════════════════════════════════════════════════════════════
# curated_redraw_plan_forest — global order over a FOREST of trees
# (plan 3.2, design §6: cross-tree anchor edges)
# ═══════════════════════════════════════════════════════════════════════════

def test_forest_parent_before_child_across_trees():
    """Two independent origin-anchored trees: parent-before-child holds within
    each, and both trees' orders merge (no cross-tree edge)."""
    t1 = _linked_tree("t1", is_origin=True, nodes=[
        _linked_node("A", record=_record("placement", "A"),
                     children=[_linked_node("A1", record=_record("placement", "A1"))])])
    t2 = _linked_tree("t2", is_origin=True, nodes=[
        _linked_node("B", record=_record("placement", "B"),
                     children=[_linked_node("B1", record=_record("placement", "B1"))])])
    names, warnings = curated_redraw_plan_forest([t1, t2], {"A", "A1", "B", "B1"})
    assert names.index("A") < names.index("A1")
    assert names.index("B") < names.index("B1")
    assert set(names) == {"A", "A1", "B", "B1"}


def test_forest_cross_tree_anchor_edge():
    """Tree A is anchored on node X of tree B (cross-tree anchoring, §9.3):
    X must be applied before A's top-level node — the unified forest parent
    map expresses the cross edge via A's anchor ref."""
    t_b = _linked_tree("tB", is_origin=True, nodes=[
        _linked_node("X", record=_record("placement", "X"),
                     children=[_linked_node("X1", record=_record("placement", "X1"))])])
    # tA's anchor ref == "X" (a selected node in tB)
    t_a = _linked_tree("tA", anchor_ref="X", nodes=[
        _linked_node("A1", record=_record("placement", "A1"))])
    names, warnings = curated_redraw_plan_forest([t_b, t_a], {"X", "X1", "A1"})
    assert names.index("X") < names.index("A1")
    assert names.index("X") < names.index("X1")
    assert set(names) == {"X", "X1", "A1"}


def test_forest_point_and_external_not_emitted():
    """point/external nodes are walked as bases (parents) but never emitted
    into names — same rule as the per-tree curated_redraw_plan."""
    t = _linked_tree("t", is_origin=True, nodes=[
        _linked_node("PT", record=_record("point", "PT"),
                     children=[_linked_node("A1", record=_record("placement", "A1"))]),
        _linked_node("EXT", is_external=True,
                     children=[_linked_node("B1", record=_record("placement", "B1"))]),
    ])
    names, warnings = curated_redraw_plan_forest([t], {"A1", "B1"})
    assert set(names) == {"A1", "B1"}


def test_forest_cross_tree_cycle_is_fatal():
    """Two trees whose anchors point into each other's selected nodes form a
    cycle — must fail loudly (Kahn leaves them unreachable), not silently."""
    t_a = _linked_tree("tA", anchor_ref="B1", nodes=[
        _linked_node("A1", record=_record("placement", "A1"))])
    t_b = _linked_tree("tB", anchor_ref="A1", nodes=[
        _linked_node("B1", record=_record("placement", "B1"))])
    from kicadstamp.exceptions import ValidationError
    with pytest.raises(ValidationError, match="cycle"):
        curated_redraw_plan_forest([t_a, t_b], {"A1", "B1"})


# ═══════════════════════════════════════════════════════════════════════════
# Bug #5: a tree rigid-redraw PositionOverride must reach RULE nodes too —
# not just placement/clone nodes (the "spokes"). Before the fix, plan_item's
# rule branch never forwarded position_overrides to ManualPositionCalculator,
# so a tree-redrawn rule resolved through its own anchor_role/anchor_ref and
# silently ignored the tree-computed position.
# ═══════════════════════════════════════════════════════════════════════════

def test_mixed_tree_rule_override_lands_on_override_not_own_anchor(monkeypatch):
    """Bug #5 gate: a MIXED tree (placement node + rule node) rigid-group
    redraw. capture_rigid_state + apply_rigid_override produce a
    PositionOverride for the RULE node; ApplyPipeline(position_overrides=...)
    forwards it through PlacementPlanner.plan_item to ManualPositionCalculator,
    and the rule lands on the override position — NOT on the position its own
    anchor (an fpga at (100,100)) resolves to."""
    import kicadstamp.tree_position as tp
    from unittest.mock import MagicMock

    from kipy.board_types import FootprintInstance, Pad

    from kicadstamp.apply_pipeline import ApplyPipeline
    from kicadstamp.config import (Cell, Config, ManualSpoke, Rule,
                                   TemplateComponentSlot)
    from kicadstamp.placement.dependency_order import Item

    # ── 1. Mixed tree: origin anchor -> placement node + rule node ──
    anchor = LinkedAnchor(anchor=TreeAnchor(ref=None, is_origin=True),
                          record=None, is_origin=True, is_external=False)
    placement_node = LinkedNode(
        node=_node_dc(ref="CL_A", kind="placement"),
        record=_record("placement", "CL_A"), is_external=False, children=[])
    rule_node = LinkedNode(
        node=_node_dc(ref="RULE_N", kind="rule"),
        record=_record("rule", "RULE_N"), is_external=False, children=[])
    tree = LinkedTree(name="t", anchor=anchor, nodes=[placement_node, rule_node])

    # ── 2. Capture rigid state BEFORE any move (live resolvers mocked) ──
    positions = {"CL_A": Vector2.from_xy(5 * MM, 0),
                 "RULE_N": Vector2.from_xy(10 * MM, 0)}
    rotations = {"CL_A": 0.0, "RULE_N": 0.0}

    def fake_pos(adapter, cfg, ref, record, resolved_points, sheet_names):
        return positions[ref]

    def fake_rot(adapter, cfg, ref, record, sheet_names):
        return rotations[ref]

    monkeypatch.setattr(tp, "resolve_base_live_position", fake_pos)
    monkeypatch.setattr(tp, "resolve_base_rotation_deg", fake_rot)

    captures, parent_map = capture_rigid_state("adapter", "cfg", tree,
                                               ["CL_A", "RULE_N"], {})
    assert parent_map["RULE_N"] == (None, None, False)  # origin anchor
    # Origin anchor -> the override is simply the node's own absolute offset.
    override = apply_rigid_override("adapter", "cfg", None, None,
                                    captures["RULE_N"], {})
    assert override.position.x == 10 * MM
    assert override.position.y == 0
    assert override.rotation_deg == pytest.approx(0.0)

    # ── 3. Config: the rule's OWN anchor is an fpga at (100, 100) ──
    def _pad(number, net_name, x_mm=0.0, y_mm=0.0):
        pad = MagicMock(spec=Pad)
        pad.number = number
        pad.net_name = net_name
        pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
        return pad

    def _fp(ref, role, x_mm=0.0, y_mm=0.0, pads=()):
        fp = MagicMock(spec=FootprintInstance)
        fp.ref = ref
        fp._role = role
        fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
        fp.angle_deg = 0.0
        fp._pads = list(pads)
        return fp

    # Anchor fpga at (100,100) with its spoke pad at (101,100) — WITHOUT the
    # override the rule would land there (its own anchor_role).
    fpga = _fp("FPGA1", "R_FPGA", x_mm=100.0, y_mm=100.0,
               pads=[_pad("1", "RULE_N", x_mm=101.0, y_mm=100.0)])
    # The cell's component pool: one component with Role=R1 on net RULE_N.
    comp = _fp("C1", "R1", pads=[_pad("1", "RULE_N")])

    adapter = MagicMock()
    all_fps = [fpga, comp]
    adapter.get_footprints.return_value = all_fps
    adapter.get_footprint.side_effect = lambda ref: next(
        (f for f in all_fps if f.ref == ref), None)
    adapter.get_field_value.side_effect = (
        lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None)
    adapter.get_footprint_pads.side_effect = lambda fp: list(getattr(fp, "_pads", []))
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in getattr(fp, "_pads", []) if p.number == num), None)
    adapter.get_selected_items.return_value = []

    cell = Cell(name="tpl", layer="F.Cu",
                components=[TemplateComponentSlot(role="R1", offset_along_mm=0.0,
                                                  offset_across_mm=0.0, angle_deg=0.0)])
    rule = Rule(net="RULE_N", anchor_role="R_FPGA",
                spokes=[ManualSpoke(pad="1", cell="tpl")])
    cfg = Config(layer="F.Cu", cells={"tpl": cell}, rules=[rule])

    # ── 4. ApplyPipeline with the override (bug #5 path) ──
    pipeline = ApplyPipeline("board.yaml", preloaded_cfg=cfg,
                             position_overrides={"RULE_N": override})
    pipeline.adapter = adapter
    pipeline._create_planner()
    assert pipeline.planner.position_overrides == {"RULE_N": override}

    # ── 5. Plan the rule item -> must land on the override, not (100,100) ──
    pipeline.planner.begin_planning()
    item = Item(kind="rule", obj=rule, label="rule 'RULE_N'",
                anchor_ref="FPGA1", produces=set())
    moves = pipeline.planner.plan_item(item)
    assert len(moves) == 1
    # Override origin (10,0) + the pad's +x 1mm local offset -> (11,0).
    assert moves[0].position.x == 11 * MM
    assert moves[0].position.y == 0
