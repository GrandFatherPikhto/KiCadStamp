"""Scheme List Apply/Redraw branch (plan_2026_09_05_scheme_list.md §4,
plan_2026_09_06_scheme_list_p4_apply.md) — pure planning tests over a mock
adapter: in-place + onto-sibling modes, the anchor-rotation compensation
formula (a rotation round-trip regression — the d3326e4 double-rotation bug
class), the incomplete-twin single fatal, and the canary that a scheme_list
Entity never materializes into ClonePlacement(cell=None).
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
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.scheme_list_apply import plan_scheme_list
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


def _rec(name="psu", anchor_ref="R1", anchor_rot=0.0, components=None,
         vias=None, tracks=None, source_sheet=None):
    return SchemeListConfig(
        name=name, anchor_ref=anchor_ref, anchor_pad=None,
        anchor_rotation_deg=anchor_rot, source_sheet=source_sheet,
        components=components or [_comp(anchor_ref, 0.0, 0.0, anchor_rot)],
        vias=vias or [], tracks=tracks or [])


class FakeAdapter:
    """Minimal adapter for the pure planner: footprints + pads (sheet names)."""

    def __init__(self, fps):
        self._fps = list(fps)

    def get_footprints(self):
        return list(self._fps)

    def get_footprint_pads(self, fp):
        return [Pad(number="1", net_name=net, position=fp.position)
                for net in getattr(fp, "_pad_nets", ())]


def _moves_by_ref(plan):
    return {m.ref: m for m in plan.moves}


# ── in place ────────────────────────────────────────────────────────────────

class TestInPlace:
    def test_zero_rotation_simple_shift(self):
        # Anchor recorded at 0°, comp C1 offset (10,0); node at (100,50) rot 0.
        adapter = FakeAdapter([_fp("R1", 10, 10), _fp("C1", 20, 10)])
        rec = _rec(anchor_ref="R1", anchor_rot=0.0,
                   components=[_comp("R1", 0, 0, 0.0), _comp("C1", 10, 0, 0.0)])
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu"), rec, adapter,
                                Vector2.from_xy_mm(100, 50), 0.0)
        assert plan.mode == "in_place"
        moves = _moves_by_ref(plan)
        assert moves["R1"].position == Vector2.from_xy_mm(100, 50)
        assert moves["R1"].angle.degrees == 0.0
        assert moves["C1"].position == Vector2.from_xy_mm(110, 50)
        assert plan.ref_map == {"R1": "R1", "C1": "C1"}

    def test_anchor_captured_at_90_applied_at_0_compensates(self):
        """The d3326e4 double-rotation regression: a comp recorded as (10,0)
        with the anchor captured at 90° must land at node+(0,10) when the node
        rotation is 0 (rotate the raw offset by -anchor_rotation_deg first).
        The naive (no compensation) would put it at node+(10,0)."""
        adapter = FakeAdapter([_fp("R1", 10, 10), _fp("C1", 10, 20)])
        rec = _rec(anchor_ref="R1", anchor_rot=90.0,
                   components=[_comp("R1", 0, 0, 90.0), _comp("C1", 10, 0, 120.0)])
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu"), rec, adapter,
                                Vector2.from_xy_mm(50, 50), 0.0)
        moves = _moves_by_ref(plan)
        assert moves["R1"].position == Vector2.from_xy_mm(50, 50)
        # R(-90)·(10,0) = (0, +10) under the domain's Y-down rotation
        _assert_xy_near(moves["C1"].position, 50.0, 60.0)
        # anchor lands at node rotation; C1 relative angle 120-90=30 -> 30
        assert moves["R1"].angle.degrees == pytest.approx(0.0)
        assert moves["C1"].angle.degrees == pytest.approx(30.0)

    def test_relative_geometry_preserved_roundtrip(self):
        """Capture at A=90 (comp raw 120°, offset (10,0)); apply at T=30.
        Relative geometry to the anchor must be preserved: the comp offset is
        R(T-A)=R(-60)·(10,0) = (5, 8.66) and its angle is T+(120-90)=60."""
        adapter = FakeAdapter([_fp("R1", 0, 0), _fp("C1", 10, 0)])
        rec = _rec(anchor_ref="R1", anchor_rot=90.0,
                   components=[_comp("R1", 0, 0, 90.0), _comp("C1", 10, 0, 120.0)])
        plan = plan_scheme_list(Entity(name="E1", scheme_list="psu"), rec, adapter,
                                Vector2.from_xy_mm(200, 200), 30.0)
        moves = _moves_by_ref(plan)
        _assert_xy_near(moves["R1"].position, 200.0, 200.0)
        assert moves["R1"].angle.degrees == pytest.approx(30.0)
        # comp offset from the anchor = R(-60)·(10,0) = (5, 8.66), Y-down
        _assert_xy_near(moves["C1"].position, 205.0, 208.66)
        assert moves["C1"].angle.degrees == pytest.approx(60.0)

    def test_vias_and_tracks_literal_nets_in_place(self):
        adapter = FakeAdapter([_fp("R1", 10, 10), _fp("C1", 20, 10)])
        rec = _rec(
            anchor_ref="R1", anchor_rot=0.0,
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
            anchor_ref="R1s", anchor_rot=45.0, source_sheet="Channel_0",
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
        _assert_xy_near(moves["R1d"].position, 100.0, 200.0)
        assert moves["R1d"].angle.degrees == pytest.approx(0.0)
        # same geometry as in-place: R(-45)·(10,0) = (7.071, 7.071)
        _assert_xy_near(moves["C1d"].position, 107.071, 207.071)
        assert moves["C1d"].angle.degrees == pytest.approx(45.0)  # 90-45
        # nets remapped to the dst sheet
        assert plan.vias[0].net_name == CH1
        assert plan.tracks[0].net_name == GND1
        # inner-layer literal survives on the twin
        assert plan.tracks[0].layer == BoardLayer.BL_In1_Cu

    def test_incomplete_twin_is_one_fatal_list(self):
        # C1 has NO twin on Channel_1 (drop C1d)
        adapter = FakeAdapter([fp for fp in _twin_board() if fp.ref != "C1d"])
        rec = _rec(
            anchor_ref="R1s", anchor_rot=45.0, source_sheet="Channel_0",
            components=[_comp("R1s", 0, 0, 45.0), _comp("C1s", 10, 0, 90.0)])
        with pytest.raises(ValidationError, match="problem"):
            plan_scheme_list(Entity(name="E1", scheme_list="psu", sheet="Channel_1"),
                             rec, adapter, Vector2.from_xy_mm(100, 200), 0.0)

    def test_unknown_target_sheet_fatal(self):
        adapter = FakeAdapter(_twin_board())
        rec = _rec(anchor_ref="R1s", anchor_rot=0.0, source_sheet="Channel_0",
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
        scheme_lists=[_rec(anchor_ref="R1", components=[_comp("R1", 0, 0, 0.0)])],
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
