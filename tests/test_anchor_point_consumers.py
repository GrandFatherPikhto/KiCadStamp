#!/usr/bin/env python3
"""Tests for anchor_point: actually resolving on the three consumers
(ClonePositionCalculator, ManualPositionCalculator, ViaPlanner) — Phase 3
step 3, see handoff_2026_07_31_consolidated.md. resolved_points is
pre-populated directly here (as if an earlier dependency_order.py level had
already resolved it), the same contract plan_item()'s point branch produces
— see test_point_resolver.py for resolve_point() itself, and
test_planner_point_item.py for the plan_item() wiring."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2
from kipy.board_types import Pad, FootprintInstance

from kicadstamp.config import (
    Config, ClonePlacement, Rule, ManualSpoke, Cell, TemplateComponentSlot,
    ThermalViaArrayConfig,
)
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.clone_position_calculator import ClonePositionCalculator
from kicadstamp.placement.services.manual_position_calculator import ManualPositionCalculator
from kicadstamp.placement.services.via_planner import ViaPlanner
from kicadstamp.placement.services.point_resolver import ResolvedPoint

MM = 1_000_000


def _make_pad(number, x_mm, y_mm, net_name="NET1"):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net_name = net_name
    return pad


def _make_fp(ref, role=None, pads=()):
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = ref
    fp._role = role
    fp._pads = list(pads)
    return fp


class TestClonePlacementAnchorPoint:
    def test_position_is_point_position_plus_xy_shift(self):
        anchor_fp = _make_fp("FPGA")
        resolved_points = {
            "fpga_center": ResolvedPoint(
                position=Vector2.from_xy(int(50.0 * MM), int(60.0 * MM)),
                footprint=anchor_fp,
            )
        }
        cell = Cell(name="tpl", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=0.0, offset_across_mm=0.0, angle_deg=0.0)
        ])
        clone = ClonePlacement(cluster="cp1", cell="tpl", xy=(2.0, -1.0),
                               anchor_point="fpga_center")
        cfg = Config(layer="F.Cu", cells={"tpl": cell}, clone_placements=[clone])

        c1 = _make_fp("C1", role="R1")
        adapter = MagicMock()
        adapter.get_footprints.return_value = [c1]
        adapter.get_selected_items.return_value = []
        adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)

        calc = ClonePositionCalculator(adapter, cfg, resolved_points=resolved_points)
        placed, _vias, _tracks = calc.compute_raw_positions([clone])

        assert len(placed) == 1
        # origin = point position (50, 60) + flat shift (xy = 2, -1)
        assert placed[0].dest.x == int(52.0 * MM)
        assert placed[0].dest.y == int(59.0 * MM)
        # ClonePositionCalculator never touches the point's footprint/adapter
        # anchor lookup — no get_footprint call for an anchor_ref/anchor_role.
        adapter.get_footprint.assert_not_called()

    def test_lazily_resolves_missing_point_from_cfg_points(self):
        """Bug #6 (2026-08-31): with an EMPTY resolved_points (a tree-position
        caller — _anchor_base / the rigid-group redraw — resolving a BASE before
        the planner's Phase 1 ever ran), ClonePositionCalculator must resolve the
        anchor_point ON DEMAND via resolve_point_chain instead of a raw KeyError.
        The result is cached, so a repeat lookup is a plain dict hit."""
        from kicadstamp.config import Point
        point = Point(name="Origin", xy=(10.0, 20.0))
        clone = ClonePlacement(cluster="cp1", cell="tpl", xy=(0.0, 0.0),
                               anchor_point="Origin")
        cfg = Config(layer="F.Cu", points={"Origin": point},
                     clone_placements=[clone])
        adapter = MagicMock()

        calc = ClonePositionCalculator(adapter, cfg, resolved_points={})
        assert calc.resolved_points == {}
        pos = calc._resolve_anchor(clone)
        assert pos == Vector2.from_xy(int(10.0 * MM), int(20.0 * MM))
        # the lazily-resolved point is now cached for subsequent lookups.
        assert calc.resolved_points["Origin"].position == pos

    def test_missing_point_is_clear_validation_error_not_keyerror(self):
        """Bug #6: an anchor_point naming a point ABSENT from cfg.points must be
        a clear ValidationError (fatal at the boundary, resolve_point_chain's
        "anchor_point not found"), never a raw KeyError."""
        clone = ClonePlacement(cluster="cp1", cell="tpl", xy=(0.0, 0.0),
                               anchor_point="NOPE")
        cfg = Config(layer="F.Cu", points={}, clone_placements=[clone])
        calc = ClonePositionCalculator(MagicMock(), cfg, resolved_points={})
        with pytest.raises(ValidationError, match="not found"):
            calc._resolve_anchor(clone)


class TestRuleAnchorPoint:
    def test_uses_points_footprint_to_look_up_spoke_pad(self):
        pad1 = _make_pad("1", x_mm=5.0, y_mm=5.0)
        anchor_fp = _make_fp("FPGA", pads=[pad1])
        resolved_points = {
            "fpga_center": ResolvedPoint(position=anchor_fp_position(), footprint=anchor_fp)
        }
        cell = Cell(name="tpl", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=0.0, offset_across_mm=0.0, angle_deg=0.0)
        ])
        rule = Rule(net="NET1", anchor_point="fpga_center",
                   spokes=[ManualSpoke(pad="1", cell="tpl")])
        cfg = Config(layer="F.Cu", cells={"tpl": cell}, chains=[rule])

        c1 = _make_fp("C1", role="R1", pads=[_make_pad("1", 0, 0, "NET1")])
        adapter = MagicMock()
        adapter.get_footprints.return_value = [c1]
        adapter.get_footprint_pads.side_effect = lambda fp: list(getattr(fp, "_pads", []))
        adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None
        adapter.get_pad_by_number.side_effect = lambda fp, num: next(
            (p for p in getattr(fp, "_pads", []) if p.number == num), None)
        adapter.get_selected_items.return_value = []

        calc = ManualPositionCalculator(adapter, cfg, resolved_points=resolved_points)
        placed, _vias, _tracks = calc.compute_raw_positions([rule])

        assert len(placed) == 1
        assert placed[0].ref == "C1"
        # spoke origin = anchor_fp's pad "1" position (5, 5) mm
        assert placed[0].dest.x == int(5.0 * MM)
        assert placed[0].dest.y == int(5.0 * MM)
        # Never resolved anchor_ref/anchor_role — no such fields were set.
        adapter.get_footprint.assert_not_called()

    def test_point_with_no_footprint_is_a_defensive_fatal(self):
        """Unreachable via normal loading (config/loader.py rejects this at
        load time) — but if resolved_points ever gets here without a
        footprint some other way, this must fail loudly, not with an
        AttributeError deep in spoke geometry."""
        resolved_points = {"shifted": ResolvedPoint(position=Vector2.from_xy(0, 0), footprint=None)}
        rule = Rule(net="NET1", anchor_point="shifted", spokes=[])
        cfg = Config(layer="F.Cu", cells={}, chains=[rule])
        adapter = MagicMock()

        calc = ManualPositionCalculator(adapter, cfg, resolved_points=resolved_points)
        with pytest.raises(ValidationError, match="has no footprint"):
            calc.compute_raw_positions([rule])


def anchor_fp_position():
    return Vector2.from_xy(int(100.0 * MM), int(100.0 * MM))


class TestThermalViaArrayAnchorPoint:
    def test_resolves_to_points_footprint(self):
        anchor_fp = _make_fp("FPGA")
        resolved_points = {"fpga_center": ResolvedPoint(position=anchor_fp_position(), footprint=anchor_fp)}
        tva = ThermalViaArrayConfig(name="fpga_thermal", anchor_point="fpga_center", pad="145")
        cfg = Config(layer="F.Cu", cells={}, thermal_via_arrays=[tva])
        adapter = MagicMock()

        planner = ViaPlanner(adapter, cfg, resolved_points=resolved_points)
        result = planner._resolve_thermal_anchor(tva)

        assert result is anchor_fp
        adapter.get_footprint.assert_not_called()

    def test_retired_still_wins_over_anchor_point(self):
        resolved_points = {"fpga_center": ResolvedPoint(position=Vector2.from_xy(0, 0),
                                                         footprint=_make_fp("FPGA"))}
        tva = ThermalViaArrayConfig(name="fpga_thermal", anchor_point="fpga_center",
                                    pad="145", retired=True)
        cfg = Config(layer="F.Cu", cells={}, thermal_via_arrays=[tva])
        adapter = MagicMock()

        planner = ViaPlanner(adapter, cfg, resolved_points=resolved_points)
        assert planner._resolve_thermal_anchor(tva) is None

    def test_point_with_no_footprint_is_a_defensive_fatal(self):
        resolved_points = {"shifted": ResolvedPoint(position=Vector2.from_xy(0, 0), footprint=None)}
        tva = ThermalViaArrayConfig(name="fpga_thermal", anchor_point="shifted", pad="145")
        cfg = Config(layer="F.Cu", cells={}, thermal_via_arrays=[tva])
        adapter = MagicMock()

        planner = ViaPlanner(adapter, cfg, resolved_points=resolved_points)
        with pytest.raises(ValidationError, match="has no footprint"):
            planner._resolve_thermal_anchor(tva)
