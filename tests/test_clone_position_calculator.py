#!/usr/bin/env python3
"""Plan 2026-09-07 (handoff absolute_clone_geometric_role_narrowing):
geometric role narrowing (resolve_roles_by_nets step 5 — physical proximity
to the anchor of THIS clone_placement, role_narrowing.py) was structurally
unreachable for Entity/tree_instances-materialized clones and any
absolute-coordinate ClonePlacement: _resolve_anchor returns None for them
(no anchor_ref/anchor_role/anchor_point), so resolve_roles_by_nets got
anchor_position=None and step 5 never fired — even though the clone's own
world position is already knowable from its xy.

Fix: _resolve_one_level computes a SEPARATE `role_narrowing_anchor` fallback
via clone_layout_origin (the exact function that will later place this
clone's origin) and passes it to resolve_roles_by_selection/
resolve_roles_by_nets ONLY. The real anchor_position used by
apply_clone_geometry is deliberately left untouched — feeding the fallback
back into geometry would double-apply clone.xy's shift (§0 trap).

These tests drive ClonePositionCalculator.compute_raw_positions end to end
(the path _resolve_anchor -> _resolve_one_level -> resolve_roles_by_nets ->
apply_clone_geometry) on a fake live board.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock

from kicadstamp.config import Config, ClonePlacement, Cell, TemplateComponentSlot
from kicadstamp.constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME
from kicadstamp.domain.geometry import Vector2, BoardLayer
from kicadstamp.domain.board import Footprint
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.clone_position_calculator import ClonePositionCalculator
from kicadstamp.placement.services.clone_role_resolver import resolve_roles_by_nets
from kicadstamp.placement.services.point_resolver import ResolvedPoint

MM = 1_000_000


def _make_fp(ref, role, nets, cluster=None, x_mm=0.0, y_mm=0.0):
    """A live-board footprint in domain units: ref/role/cluster/nets/position."""
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy(int(x_mm * MM), int(y_mm * MM)),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._cluster = cluster
    fp._nets = list(nets)
    return fp


def _field(fp, name):
    if name == ROLE_FIELD_NAME:
        return fp._role
    if name == CLUSTER_FIELD_NAME:
        return fp._cluster
    return None


def _pads(fp):
    """Sequential pad numbers 1..N; net i -> pad str(i) (same convention as
    test_clone_role_resolver.py)."""
    out = []
    for i, n in enumerate(fp._nets, start=1):
        p = MagicMock()
        p.number = str(i)
        p.net_name = n
        out.append(p)
    return out


def _adapter(fps):
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_field_value.side_effect = lambda fp, name: _field(fp, name)
    adapter.get_footprint_pads.side_effect = _pads
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in _pads(fp) if p.number == str(num)), None)
    adapter.get_selected_items.return_value = []
    return adapter


def _two_role_shared_net_cell():
    """A cell with TWO roles on the SAME shared power net, but at DIFFERENT
    local offsets from the clone origin (mirrors C_IN_BYPASS at (0,0) and
    C_IN_BULK at (10,0) inside one pif_* filter block)."""
    return Cell(name="filter_block", components=[
        TemplateComponentSlot(role="C_IN_BYPASS", offset_along_mm=0.0, offset_across_mm=0.0),
        TemplateComponentSlot(role="C_IN_BULK", offset_along_mm=10.0, offset_across_mm=0.0),
    ])


def _two_blocks_board():
    """Two physical filter blocks, all four parts on the shared +3V3 rail.
    Block A (PIF_AVDD) sits at (0,0); block B (PIF_DVDD) at (100,200) — the
    block the absolute clone below targets. Per role the two candidates differ
    ONLY by position (same net, no sheet/cluster signal that would disambiguate
    earlier), so step 5 proximity narrowing is the deciding signal."""
    return [
        _make_fp("C_A_BYPASS", "C_IN_BYPASS", ["+3V3"], cluster="PIF_AVDD",
                 x_mm=0.0, y_mm=0.0),
        _make_fp("C_A_BULK", "C_IN_BULK", ["+3V3"], cluster="PIF_AVDD",
                 x_mm=10.0, y_mm=0.0),
        _make_fp("C_B_BYPASS", "C_IN_BYPASS", ["+3V3"], cluster="PIF_DVDD",
                 x_mm=100.0, y_mm=200.0),
        _make_fp("C_B_BULK", "C_IN_BULK", ["+3V3"], cluster="PIF_DVDD",
                 x_mm=110.0, y_mm=200.0),
    ]


def _absolute_clone():
    """An ABSOLUTE-coordinate top-level ClonePlacement — the Entity/
    tree_instances shape: xy set, NO anchor_ref/anchor_role/anchor_point, so
    _resolve_anchor returns None (before this plan that meant resolve_roles_
    by_nets got anchor_position=None and step 5 never fired)."""
    return ClonePlacement(cluster="ch1_dac_buf", cell="filter_block",
                          xy=(100.0, 200.0),
                          nets={"C_IN_BYPASS": "+3V3", "C_IN_BULK": "+3V3"})


class TestAbsoluteCloneGeometricRoleNarrowing:
    def test_two_same_net_roles_resolve_by_proximity(self):
        """THE live scenario of the plan: an absolute clone whose two roles
        share one net (+3V3) and whose physical candidates differ only by
        position. Before the fix resolve_roles_by_nets got anchor_position=None
        and fatally failed on the ambiguity; now the clone's own world origin
        (clone.xy) reaches step 5 and each role resolves to its NEAREST
        physical candidate (block B = the target of xy)."""
        adapter = _adapter(_two_blocks_board())
        cell = _two_role_shared_net_cell()
        clone = _absolute_clone()
        calc = ClonePositionCalculator(adapter, Config(layer="F.Cu", cells={cell.name: cell}))

        placed, _vias, _tracks = calc.compute_raw_positions([clone])

        # Both roles of block B (the target), never block A's parts.
        by_dest = {p.dest: p.ref for p in placed}
        assert by_dest[Vector2.from_xy(int(100.0 * MM), int(200.0 * MM))] == "C_B_BYPASS"
        assert by_dest[Vector2.from_xy(int(110.0 * MM), int(200.0 * MM))] == "C_B_BULK"

    def test_negative_control_anchor_none_still_fatal(self):
        """Documents WHY the fallback matters: the same board/cell with
        anchor_position=None (the structurally-None value BEFORE this plan)
        leaves BOTH roles ambiguous and fatals — proving the end-to-end test
        above genuinely depends on the new role_narrowing_anchor (no earlier
        cascade step happens to narrow these candidates)."""
        adapter = _adapter(_two_blocks_board())
        clone = _absolute_clone()
        with pytest.raises(ValidationError, match="ambiguity"):
            resolve_roles_by_nets(adapter, _two_role_shared_net_cell(), clone,
                                  anchor_position=None)

    def test_insufficient_proximity_gap_still_fatal(self):
        """Regression: the 2x-gap guard of step 5 must keep refusing to guess.
        With the fallback ACTIVE, two candidates EQUIDISTANT from the computed
        role_narrowing_anchor stay ambiguous and raise the same fatal as today
        — the new code enables an existing, honest disambiguator; it does not
        turn a coin toss into a guess."""
        cell = Cell(name="one_role", components=[
            TemplateComponentSlot(role="C_IN_BULK"),
        ])
        fps = [
            _make_fp("C_L", "C_IN_BULK", ["+3V3"], x_mm=90.0, y_mm=200.0),
            _make_fp("C_R", "C_IN_BULK", ["+3V3"], x_mm=110.0, y_mm=200.0),
        ]
        adapter = _adapter(fps)
        clone = ClonePlacement(cluster="block_x", cell="one_role", xy=(100.0, 200.0),
                               nets={"C_IN_BULK": "+3V3"})
        calc = ClonePositionCalculator(adapter, Config(layer="F.Cu", cells={cell.name: cell}))

        with pytest.raises(ValidationError, match="ambiguity"):
            calc.compute_raw_positions([clone])


class TestGeometryUnchangedRegression:
    def test_absolute_clone_geometry_no_double_shift(self):
        """Regression for the §0 trap: role_narrowing_anchor must NOT leak
        into apply_clone_geometry. The absolute clone's real origin stays
        clone.xy applied ONCE — a double-shift bug would land this component
        at (205, 405) instead of (105, 205)."""
        cell = Cell(name="single", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=5.0, offset_across_mm=5.0),
        ])
        fps = [_make_fp("C1", "R1", ["NET1"], x_mm=105.0, y_mm=205.0)]
        adapter = _adapter(fps)
        clone = ClonePlacement(cluster="abs1", cell="single", xy=(100.0, 200.0),
                               nets={"R1": "NET1"})
        calc = ClonePositionCalculator(adapter, Config(layer="F.Cu", cells={cell.name: cell}))

        placed, _vias, _tracks = calc.compute_raw_positions([clone])

        assert len(placed) == 1
        assert placed[0].ref == "C1"
        assert placed[0].dest.x == int(105.0 * MM)
        assert placed[0].dest.y == int(205.0 * MM)

    def test_anchor_based_clone_unaffected(self):
        """Regression: for a clone WITH an anchor (the ordinary, non-Entity
        case) role_narrowing_anchor takes the `anchor_position is not None`
        branch — i.e. it equals the ORIGINAL anchor_position, so an ambiguity
        resolved by physical proximity uses the true anchor exactly as before
        this plan. Nothing about the anchored path may change."""
        cell = Cell(name="tpl", components=[
            TemplateComponentSlot(role="C_IN_BYPASS"),
        ])
        fps = [
            _make_fp("C_FAR", "C_IN_BYPASS", ["+3V3"], x_mm=0.0, y_mm=0.0),
            _make_fp("C_NEAR", "C_IN_BYPASS", ["+3V3"], x_mm=50.0, y_mm=60.0),
        ]
        adapter = _adapter(fps)
        anchor_fp = _make_fp("ANCHOR", "FPGA", ["OTHER"])
        clone = ClonePlacement(cluster="anchored", cell="tpl", xy=(0.0, 0.0),
                               anchor_point="A",
                               nets={"C_IN_BYPASS": "+3V3"})
        cfg = Config(layer="F.Cu", cells={cell.name: cell}, clone_placements=[clone])
        resolved_points = {
            "A": ResolvedPoint(position=Vector2.from_xy(int(50.0 * MM), int(60.0 * MM)),
                               footprint=anchor_fp),
        }
        calc = ClonePositionCalculator(adapter, cfg, resolved_points=resolved_points)

        placed, _vias, _tracks = calc.compute_raw_positions([clone])

        assert [p.ref for p in placed] == ["C_NEAR"]
