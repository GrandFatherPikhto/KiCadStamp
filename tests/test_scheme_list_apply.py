"""Scheme List Apply/Redraw branch (plan_2026_09_05_scheme_list.md §4,
plan_2026_09_06_scheme_list_p4_apply.md;
design_2026_09_07_scheme_list_pivot.md) — pure planning tests over a mock
adapter: in-place + onto-sibling modes, the centre-frame + pivot geometry
(the record's pivot lands on the node position and the node rotation turns
the WHOLE region around the pivot; each element keeps its real absolute
angle — no anchor_rotation_deg compensation, so the d3326e4 double-rotation
bug class is gone by construction), the incomplete-twin single fatal, and
the canary that a scheme_list Entity never materializes into
ClonePlacement(cell=None).
"""
import pytest

from kicadstamp.config import Config, Entity
from kicadstamp.config.models import (
    SchemeListComponentRecord,
    SchemeListConfig,
    SchemeListTrackRecord,
    SchemeListViaRecord,
)
from kicadstamp.domain.board import Footprint, Pad
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.link_trees import link_trees
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.scheme_list_apply import (
    execute_scheme_list_plans,
    plan_all_scheme_lists,
    plan_scheme_list,
)
from kicadstamp.tree_position import curated_redraw_plan
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from kicadstamp.utils.units import MM

F = BoardLayer.BL_F_Cu

CH0 = "/Channel_0/AMP/+5V"
CH1 = "/Channel_1/AMP/+5V"
GND0 = "/Channel_0/AMP/GND"
GND1 = "/Channel_1/AMP/GND"

_TOL_NM = int(0.01 * MM)  # 0.01 mm


def _assert_xy_near(pos: Vector2, x_mm: float, y_mm: float) -> None:
    assert abs(pos.x - x_mm * MM) <= _TOL_NM
    assert abs(pos.y - y_mm * MM) <= _TOL_NM


def _fp(ref, x_mm, y_mm, angle=0.0, layer=F, chain=(), pad_nets=()):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy_mm(x_mm, y_mm),
                   angle_deg=angle, layer=layer, sheet_path_uuids=tuple(chain))
    fp._pad_nets = list(pad_nets)
    return fp


def _comp(ref, along, across, rot):
    return SchemeListComponentRecord(ref=ref, offset_along_mm=along,
                                     offset_across_mm=across, rotation_deg=rot)


def _rec(name="psu", pivot=(0.0, 0.0), components=None,
         vias=None, tracks=None, source_sheet=None):
    """A recorded Scheme List in the CENTRE frame with a pivot (default (0,0)
    = the region centre). No anchor component exists
    (design_2026_09_07_scheme_list_pivot.md)."""
    return SchemeListConfig(
        name=name, pivot=pivot, source_sheet=source_sheet,
        components=components or [_comp("R1", 0.0, 0.0, 0.0)],
        vias=vias or [], tracks=tracks or [])


class FakeAdapter:
    """Minimal adapter for the pure planner: footprints + pads (sheet names)."""

    def __init__(self, fps, tracks=None, vias=None):
        self._fps = list(fps)
        self._tracks = list(tracks or [])
        self._vias = list(vias or [])

    def get_footprints(self):
        return list(self._fps)

    def get_footprint(self, ref):
        for fp in self._fps:
            if fp.ref == ref:
                return fp
        return None

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_footprint_pads(self, fp):
        return [Pad(number="1", net_name=net, position=fp.position)
                for net in getattr(fp, "_pad_nets", ())]


def _moves_by_ref(plan):
    return {m.ref: m for m in plan.moves}


# ── in place ────────────────────────────────────────────────────────────────

class TestInPlace:
    def test_zero_rotation_simple_shift(self):
        # Pivot = (0,0) (the default), comp offsets (0,0)/(10,0); node rot 0
        # reproduces the record exactly: element world = node_pos + offset.
        adapter = FakeAdapter([_fp("R1", 10, 10), _fp("C1", 20, 10)])
        rec = _rec(components=[_comp("R1", 0, 0, 0.0), _comp("C1", 10, 0, 0.0)])
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu"), rec, adapter,
                                Vector2.from_xy_mm(100, 50), 0.0)
        assert plan.mode == "in_place"
        moves = _moves_by_ref(plan)
        assert moves["R1"].position == Vector2.from_xy_mm(100, 50)
        assert moves["R1"].angle.degrees == 0.0
        assert moves["C1"].position == Vector2.from_xy_mm(110, 50)
        assert plan.ref_map == {"R1": "R1", "C1": "C1"}

    def test_node_rotation_turns_region_around_pivot(self):
        """A non-zero node rotation turns the WHOLE region rigidly around the
        pivot: element world = node_pos + Rot(node_rot)·(offset - pivot),
        element angle = stored_absolute_angle + node_rot. Here pivot (0,0),
        node_rot 90 -> (10,0) maps to (0,-10) (domain Y-down rotation)."""
        adapter = FakeAdapter([_fp("R1", 10, 10), _fp("C1", 10, 20)])
        rec = _rec(pivot=(0.0, 0.0),
                   components=[_comp("R1", 0, 0, 30.0), _comp("C1", 10, 0, 45.0)])
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu"), rec, adapter,
                                Vector2.from_xy_mm(50, 50), 90.0)
        moves = _moves_by_ref(plan)
        # R1 sits at the pivot -> lands on node_pos whatever the node rotation
        _assert_xy_near(moves["R1"].position, 50.0, 50.0)
        assert moves["R1"].angle.degrees == pytest.approx(120.0)  # 30+90
        # Rot(90)·(10,0) = (0,-10) under the domain's Y-down rotation
        _assert_xy_near(moves["C1"].position, 50.0, 40.0)
        assert moves["C1"].angle.degrees == pytest.approx(135.0)  # 45+90

    def test_tilted_region_reproduced_exactly_at_node_rot_zero(self):
        """The no-double-rotation gate (design_2026_09_07 p.3.4): a region
        whose elements were captured at NON-zero absolute angles is reproduced
        EXACTLY as captured at node_rot=0 — the recorded angles are kept, never
        compensated against any anchor (that is what used to double-rotate)."""
        adapter = FakeAdapter([_fp("R1", 10, 10), _fp("C1", 10, 20)])
        rec = _rec(pivot=(0.0, 0.0),
                   components=[_comp("R1", 0, 0, 30.0), _comp("C1", 10, 0, 45.0)])
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu"), rec, adapter,
                                Vector2.from_xy_mm(50, 50), 0.0)
        moves = _moves_by_ref(plan)
        # node_rot=0 -> world = node_pos + (offset - pivot); angles untouched
        _assert_xy_near(moves["R1"].position, 50.0, 50.0)
        assert moves["R1"].angle.degrees == pytest.approx(30.0)
        _assert_xy_near(moves["C1"].position, 60.0, 50.0)
        assert moves["C1"].angle.degrees == pytest.approx(45.0)

    def test_nondefault_pivot_lands_on_node_pos(self):
        """The record's `pivot` is the point that lands on the node position:
        an element whose stored offset EQUALS the pivot lands exactly at
        node_pos; elements on the other side of the pivot land mirrored around
        it. pivot (3,0) with node_pos (100,50): stored (5,0) -> dx 2 -> (102,50),
        stored (1,0) -> dx -2 -> (98,50) (node_rot 0)."""
        adapter = FakeAdapter([_fp("R1", 10, 10), _fp("C1", 20, 10)])
        rec = _rec(pivot=(3.0, 0.0),
                   components=[_comp("R1", 3, 0, 0.0), _comp("C1", 5, 0, 0.0)])
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu"), rec, adapter,
                                Vector2.from_xy_mm(100, 50), 0.0)
        moves = _moves_by_ref(plan)
        # the stored element at the pivot lands exactly on node_pos
        _assert_xy_near(moves["R1"].position, 100.0, 50.0)
        _assert_xy_near(moves["C1"].position, 102.0, 50.0)

    def test_rotation_turns_around_nondefault_pivot(self):
        """pivot (3,0), node_rot 90: stored (5,0) -> dx (2,0) -> Rot90=(0,-2)
        -> (100,48); stored (3,0) [at pivot] stays at node_pos (100,50)."""
        adapter = FakeAdapter([_fp("R1", 10, 10), _fp("C1", 20, 10)])
        rec = _rec(pivot=(3.0, 0.0),
                   components=[_comp("R1", 3, 0, 0.0), _comp("C1", 5, 0, 0.0)])
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu"), rec, adapter,
                                Vector2.from_xy_mm(100, 50), 90.0)
        moves = _moves_by_ref(plan)
        _assert_xy_near(moves["R1"].position, 100.0, 50.0)  # on the pivot
        _assert_xy_near(moves["C1"].position, 100.0, 48.0)  # Rot90(2,0)=(0,-2)
        assert moves["C1"].angle.degrees == pytest.approx(90.0)

    def test_vias_and_tracks_literal_nets_in_place(self):
        adapter = FakeAdapter([_fp("R1", 10, 10), _fp("C1", 20, 10)])
        rec = _rec(
            components=[_comp("R1", 0, 0, 0.0), _comp("C1", 10, 0, 0.0)],
            vias=[SchemeListViaRecord(offset_along_mm=10.0, drill_mm=0.3,
                                      diameter_mm=0.6, net=CH0)],
            tracks=[SchemeListTrackRecord(start_along_mm=0.0, start_across_mm=0.0,
                                          end_along_mm=10.0, end_across_mm=0.0,
                                          width_mm=0.25, layer="F.Cu", net=CH0)],
            source_sheet="Channel_0")
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu", sheet=""), rec,
                                adapter, Vector2.from_xy_mm(100, 50), 0.0)
        assert plan.mode == "in_place"
        assert len(plan.vias) == 1 and plan.vias[0].net_name == CH0
        assert plan.vias[0].position == Vector2.from_xy_mm(110, 50)
        assert len(plan.tracks) == 1
        t = plan.tracks[0]
        assert t.net_name == CH0 and t.layer == F
        assert t.start == Vector2.from_xy_mm(100, 50)
        assert t.end == Vector2.from_xy_mm(110, 50)
        assert t.registry_key == "scheme_list:E1:track:0"


# ── onto sibling ────────────────────────────────────────────────────────────

def _twin_board():
    """Two twin sheets Channel_0 (U_S) / Channel_1 (U_D). Each component has a
    DISTINCT inner key (path[1:] suffix SUB_R/SUB_C) — exactly like real cloned
    sheets, where the twin discriminator sits at path[0] and the inner key
    (the symbol chain) is unique per component."""
    U_S, U_D, SUB_R, SUB_C = "U_S", "U_D", "SUB_R", "SUB_C"
    return [
        _fp("R1s", 10, 10, angle=45.0, chain=(U_S, SUB_R), pad_nets=[CH0]),
        _fp("C1s", 20, 10, angle=90.0, chain=(U_S, SUB_C), pad_nets=[CH0, GND0]),
        _fp("R1d", 500, 500, angle=0.0, chain=(U_D, SUB_R), pad_nets=[CH1]),
        _fp("C1d", 520, 500, angle=0.0, chain=(U_D, SUB_C), pad_nets=[CH1, GND1]),
    ]


class TestOntoSibling:
    def test_twin_refs_and_net_remap(self):
        adapter = FakeAdapter(_twin_board())
        rec = _rec(
            source_sheet="Channel_0",
            components=[_comp("R1s", 0, 0, 45.0), _comp("C1s", 10, 0, 90.0)],
            vias=[SchemeListViaRecord(offset_along_mm=10.0, drill_mm=0.3,
                                      diameter_mm=0.6, net=CH0)],
            tracks=[SchemeListTrackRecord(start_along_mm=0.0, start_across_mm=0.0,
                                          end_along_mm=10.0, end_across_mm=0.0,
                                          width_mm=0.25, layer="In1.Cu", net=GND0)],
        )
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu", sheet="Channel_1"),
                                rec, adapter, Vector2.from_xy_mm(100, 200), 0.0)
        assert plan.mode == "onto_sibling"
        # twins are the targets — NOT the recorded source refs
        assert plan.ref_map == {"R1s": "R1d", "C1s": "C1d"}
        moves = _moves_by_ref(plan)
        assert set(moves) == {"R1d", "C1d"}
        # node_rot 0 + pivot (0,0): the region lands as captured — angles kept
        # absolute (NO anchor-rotation compensation in the centre-frame model).
        _assert_xy_near(moves["R1d"].position, 100.0, 200.0)
        assert moves["R1d"].angle.degrees == pytest.approx(45.0)
        _assert_xy_near(moves["C1d"].position, 110.0, 200.0)
        assert moves["C1d"].angle.degrees == pytest.approx(90.0)
        # nets remapped to the dst sheet
        assert plan.vias[0].net_name == CH1
        assert plan.tracks[0].net_name == GND1
        # inner-layer literal survives on the twin
        assert plan.tracks[0].layer == BoardLayer.BL_In1_Cu

    def test_unknown_track_layer_name_is_not_silently_f_cu(self):
        """plan_2026_09_12_strict_copper_layers.md Э4.2 (scheme_lists path).

        A scheme_lists track layer is a free STRING (deliberately not the F/B
        enum — see SchemeListTrackRecord), so a hand-edited record can name
        anything. On the WRITE path an unknown name must be a fatal: the old
        tolerant `layer_from_str` fallback quietly made it F.Cu."""
        adapter = FakeAdapter(_twin_board())
        rec = _rec(
            source_sheet="Channel_0",
            components=[_comp("R1s", 0, 0, 45.0)],
            tracks=[SchemeListTrackRecord(start_along_mm=0.0, start_across_mm=0.0,
                                          end_along_mm=10.0, end_across_mm=0.0,
                                          width_mm=0.25, layer="Top.Cu", net=GND0)],
        )
        with pytest.raises(ValidationError):
            plan_scheme_list(Entity(name="E1", scheme_list="psu", sheet="Channel_1"),
                             rec, adapter, Vector2.from_xy_mm(100, 200), 0.0)

    def test_twin_node_rotation_turns_region_around_pivot(self):
        """Onto a sibling the node rotation turns the region the same rigid way
        (node_rot 90 -> (10,0) maps to (0,-10), angles +90)."""
        adapter = FakeAdapter(_twin_board())
        rec = _rec(
            source_sheet="Channel_0",
            components=[_comp("R1s", 0, 0, 45.0), _comp("C1s", 10, 0, 90.0)])
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu", sheet="Channel_1"),
                                rec, adapter, Vector2.from_xy_mm(100, 200), 90.0)
        moves = _moves_by_ref(plan)
        _assert_xy_near(moves["R1d"].position, 100.0, 200.0)
        assert moves["R1d"].angle.degrees == pytest.approx(135.0)  # 45+90
        _assert_xy_near(moves["C1d"].position, 100.0, 190.0)  # Rot90(10,0)
        assert moves["C1d"].angle.degrees == pytest.approx(180.0)  # 90+90

    def test_incomplete_twin_is_one_fatal_list(self):
        # C1 has NO twin on Channel_1 (drop C1d)
        adapter = FakeAdapter([fp for fp in _twin_board() if fp.ref != "C1d"])
        rec = _rec(
            source_sheet="Channel_0",
            components=[_comp("R1s", 0, 0, 45.0), _comp("C1s", 10, 0, 90.0)])
        with pytest.raises(ValidationError, match="problem"):
            plan_scheme_list(Entity(name="E1", scheme_list="psu", sheet="Channel_1"),
                             rec, adapter, Vector2.from_xy_mm(100, 200), 0.0)

    def test_unknown_target_sheet_fatal(self):
        adapter = FakeAdapter(_twin_board())
        rec = _rec(source_sheet="Channel_0",
                   components=[_comp("R1s", 0, 0, 0.0), _comp("C1s", 10, 0, 0.0)])
        with pytest.raises(ValidationError, match="target sheet"):
            plan_scheme_list(Entity(name="E1", scheme_list="psu", sheet="Channel_9"),
                             rec, adapter, Vector2.from_xy_mm(100, 200), 0.0)


# ── canary + loader guard ───────────────────────────────────────────────────

def _origin_tree(nodes):
    return Tree(name="t", anchor=TreeAnchor(is_origin=True), nodes=nodes)


def _node(ref, xy=None, kind="placement", rotation=0.0):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rotation,
                    name=None, group=None, children=[])


def test_canary_materialize_never_emits_clone_with_cell_none():
    """plan §4 canary (a): a config whose ONLY placement is a scheme_list
    Entity materializes to ZERO ClonePlacements (the cell path skips it) — a
    scheme_list Entity can never become ClonePlacement(cell=None)."""
    cfg = Config(
        scheme_lists=[_rec(components=[_comp("R1", 0, 0, 0.0)])],
        entities=[Entity(name="E1", scheme_list="psu", sheet="Channel_0")],
        trees=[_origin_tree([_node(ref="E1", xy=(5.0, 2.0))])],
    )
    clones = materialize_entity_placements(None, cfg, {})
    assert clones == []
    assert all(c.cell is not None for c in clones)  # vacuously true, no clones


def test_materialize_skips_scheme_list_but_keeps_cell_entity():
    """A cell-based Entity in the SAME forest still materializes; only the
    scheme_list Entity is skipped (never a ClonePlacement(cell=None))."""
    cfg = Config(
        entities=[Entity(name="Ecell", cell="c"),
                  Entity(name="Esl", scheme_list="psu")],
        trees=[_origin_tree([_node(ref="Ecell", xy=(1.0, 1.0)),
                             _node(ref="Esl", xy=(9.0, 9.0))])],
    )
    clones = materialize_entity_placements(None, cfg, {})
    assert [c.name for c in clones] == ["Ecell"]
    assert all(c.cell is not None for c in clones)


def test_scheme_list_entity_mirror_layer_fatal():
    """P4.2 guard: mirror/layer on a scheme_list Entity is a v1-unsupported
    config (no mirror formula in the Apply branch) — fatal at load."""
    from kicadstamp.config import load_entity
    from kicadstamp.exceptions import ValidationError as VE
    with pytest.raises(VE, match="scheme_list-based"):
        load_entity({"name": "E1", "scheme_list": "psu", "mirror": True})
    with pytest.raises(VE, match="scheme_list-based"):
        load_entity({"name": "E1", "scheme_list": "psu", "layer": "B.Cu"})


# ── P4.4: forest collection + aggregate planning + execution ────────────────

def _scheme_cfg(entity=None, tree_nodes=None, rec=None):
    rec = rec or _rec(components=[_comp("R1", 0, 0, 0.0)])
    ent = entity if entity is not None else Entity(name="E1", scheme_list="psu")
    return Config(scheme_lists=[rec], entities=[ent],
                  trees=[_origin_tree(tree_nodes or [_node(ref="E1", xy=(5.0, 2.0),
                                                          rotation=45.0)])])


class TestForestPlanning:
    def test_collect_origin_anchored_node_pos_and_rot(self):
        """The tree collector computes the scheme node's ABSOLUTE pos/rot the
        same way cell materialization does (origin anchor + node xy/rotation);
        the record's element at the pivot lands on that pos and gets the node
        rotation added to its stored angle."""
        adapter = FakeAdapter([_fp("R1", 10, 10)])
        plans = plan_all_scheme_lists(adapter, _scheme_cfg(), {})
        assert len(plans) == 1
        p = plans[0]
        assert p.entity_name == "E1"
        assert p.mode == "in_place"
        move = p.moves[0]
        assert move.ref == "R1"
        _assert_xy_near(move.position, 5.0, 2.0)   # node xy (5,2) mm
        assert move.angle.degrees == pytest.approx(45.0)  # 0 + node rotation

    def test_only_filter_narrows_to_entity(self):
        adapter = FakeAdapter([_fp("R1", 10, 10)])
        cfg = _scheme_cfg()
        assert len(plan_all_scheme_lists(adapter, cfg, {}, only=["E1"])) == 1
        assert len(plan_all_scheme_lists(adapter, cfg, {}, only=["OTHER"])) == 0

    def test_no_scheme_entities_is_empty(self):
        adapter = FakeAdapter([_fp("R1", 10, 10)])
        cfg = Config(entities=[Entity(name="E1", cell="c")], trees=[])
        assert plan_all_scheme_lists(adapter, cfg, {}) == []


class TestExecutionIdempotency:
    def test_already_placed_move_and_no_copper_is_a_noop(self):
        """Re-apply/Redraw when everything already sits at the target does not
        touch the executor — positional idempotency (P4 plan §0.6)."""
        # R1 already at the node target (5,2)
        adapter = FakeAdapter([_fp("R1", 5, 2, angle=45.0)])
        plans = plan_all_scheme_lists(adapter, _scheme_cfg(), {})
        failed = execute_scheme_list_plans(adapter, plans)
        assert failed == ([], [], [])


def test_gui_redraw_plan_emits_scheme_list_node_not_dropped():
    """P5 gate D — the GUI Trees-Redraw path plans a checked node through
    curated_redraw_plan and then runs ONE ApplyPipeline --only per emitted
    name (gui/docks/cascade.py run_curated_tree_redraw), which is where the P4
    scheme_list branch executes. A scheme_list placement node's Entity name
    must REACH that plan (not be swallowed as a cell=None record / not be
    silently skipped), so nothing crashes and nothing is dropped."""
    cfg = _scheme_cfg()
    linked = link_trees(cfg, cfg.trees)
    assert len(linked) == 1
    node = linked[0].nodes[0]
    # link_trees resolves the scheme_list Entity node to a real record (its
    # own kind, NOT the cell machinery) — the record is never cell=None.
    assert node.record is not None
    assert node.record.name == "E1"
    names, warnings = curated_redraw_plan(linked[0], {"E1"})
    assert names == ["E1"]
    assert node.node.ref == "E1"
