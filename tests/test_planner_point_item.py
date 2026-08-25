#!/usr/bin/env python3
"""Tests for PlacementPlanner.plan_item()'s point branch (Phase 3) — a Point
item must produce zero MoveCommands and populate resolved_points, and that
cache must be the SAME object shared with the calculators (not a copy), so
anchor_point: on Rule/ClonePlacement/thermal_via_array sees it populated."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2
from kipy.board_types import FootprintInstance

from kicadstamp.config import Config, Point
from kicadstamp.placement.planner import PlacementPlanner
from kicadstamp.placement.dependency_order import Item

MM = 1_000_000


def _make_fp(ref, x_mm=0.0, y_mm=0.0):
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    return fp


def _cfg():
    return Config(layer='F.Cu', cells={}, points={}, rules=[], clone_placements=[])


def test_point_item_returns_no_moves_and_populates_cache():
    fp = _make_fp("U5", x_mm=10.0, y_mm=20.0)
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fp]
    adapter.get_footprint.side_effect = lambda ref: fp if ref == "U5" else None

    planner = PlacementPlanner(adapter, _cfg())
    point = Point(name="my_point", anchor_ref="U5")
    item = Item(kind='point', obj=point, label="point 'my_point'",
               anchor_ref="U5", produces={"point:my_point"})

    moves = planner.plan_item(item)

    assert moves == []
    assert "my_point" in planner.resolved_points
    resolved = planner.resolved_points["my_point"]
    assert resolved.position.x == int(10.0 * MM)
    assert resolved.footprint is fp


def test_resolved_points_shared_with_calculators():
    """PlacementPlanner threads the SAME dict (by reference) into its
    calculators — populating it via plan_item() must be visible from there
    too, not a copy."""
    adapter = MagicMock()
    adapter.get_footprints.return_value = []
    planner = PlacementPlanner(adapter, _cfg())

    planner.resolved_points["x"] = object()

    assert planner.position_calc.resolved_points is planner.resolved_points
    assert planner.clone_calc.resolved_points is planner.resolved_points
    assert planner.via_planner.resolved_points is planner.resolved_points
    assert "x" in planner.position_calc.resolved_points
