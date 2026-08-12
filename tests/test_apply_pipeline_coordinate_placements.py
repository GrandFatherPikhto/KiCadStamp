#!/usr/bin/env python3
"""ApplyPipeline._execute()'s Phase 0 — coordinate_placements ("dumb
placer") — runs BEFORE Phase 1's dependency-order move loop, via the
SAME BatchExecutor.execute_moves() no new executor, no registry
involvement (see coordinate_position_calculator.py / CoordinatePlacement's
own docstrings)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock, patch

from kipy.board_types import BoardLayer
from kipy.geometry import Angle, Vector2

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import Config, CoordinatePlacement
from kicadstamp.placement.commands import MoveCommand

MM = 1_000_000


def _pipeline(coordinate_placements):
    cfg = Config(layer='F.Cu', cells={}, rules=[], clone_placements=[],
                 coordinate_placements=coordinate_placements)
    pipeline = ApplyPipeline("board.yaml", preloaded_cfg=cfg)
    pipeline.adapter = MagicMock()
    pipeline.items = []
    pipeline.planner = MagicMock()
    pipeline.planner.plan_item.return_value = []
    pipeline.planner.plan_vias.return_value = []
    pipeline.planner.plan_tracks.return_value = []
    pipeline.all_anchor_ids = set()
    return pipeline


def _patched_executors():
    return (patch("kicadstamp.apply_pipeline.BatchExecutor"),
           patch("kicadstamp.apply_pipeline.PlacementRegistry"),
           patch("kicadstamp.apply_pipeline.TrackRegistry"))


def test_coordinate_placements_are_moved_before_phase1(monkeypatch):
    cp = CoordinatePlacement(cluster="FPGA_PERIPH", role="R18",
                             x_mm=10.0, y_mm=20.0, rotation_deg=0.0)
    pipeline = _pipeline([cp])

    fake_move = MoveCommand(ref="R18", position=Vector2.from_xy(int(10 * MM), int(20 * MM)),
                            angle=Angle.from_degrees(0.0), layer=BoardLayer.BL_F_Cu)
    call_order = []

    def fake_build_coordinate_moves(adapter, coordinate_placements, points=None, sheet_names=None):
        assert coordinate_placements == [cp]
        call_order.append("build_coordinate_moves")
        return [fake_move]

    monkeypatch.setattr("kicadstamp.apply_pipeline.build_coordinate_moves", fake_build_coordinate_moves)

    p_exec, p_reg, p_track_reg = _patched_executors()
    with p_exec as MockExecutorCls, p_reg, p_track_reg:
        mock_executor = MockExecutorCls.return_value

        def fake_execute_moves(moves, **kwargs):
            call_order.append(("execute_moves", list(moves)))
            return []

        mock_executor.execute_moves.side_effect = fake_execute_moves
        mock_executor.execute_vias.return_value = []
        mock_executor.execute_tracks.return_value = []

        pipeline._execute()

    # Phase 0 (coordinate_placements) happened, and BEFORE Phase 1's own
    # (empty, in this test) move loop / the board refresh that follows it.
    assert call_order[0] == "build_coordinate_moves"
    assert call_order[1] == ("execute_moves", [fake_move])
    assert pipeline.adapter.refresh_board.called


def test_no_coordinate_placements_skips_phase0_entirely(monkeypatch):
    pipeline = _pipeline([])

    called = []
    monkeypatch.setattr("kicadstamp.apply_pipeline.build_coordinate_moves",
                        lambda *a, **kw: called.append(1) or [])

    p_exec, p_reg, p_track_reg = _patched_executors()
    with p_exec as MockExecutorCls, p_reg, p_track_reg:
        mock_executor = MockExecutorCls.return_value
        mock_executor.execute_moves.return_value = []
        mock_executor.execute_vias.return_value = []
        mock_executor.execute_tracks.return_value = []

        pipeline._execute()

    assert called == []


def test_dry_run_report_includes_coordinate_placements(monkeypatch):
    from types import SimpleNamespace

    cp = CoordinatePlacement(cluster="FPGA_PERIPH", role="R18",
                             x_mm=10.0, y_mm=20.0, rotation_deg=90.0)
    cfg = Config(layer='F.Cu', cells={}, rules=[], clone_placements=[],
                 coordinate_placements=[cp])
    pipeline = ApplyPipeline("board.yaml", dry_run=True, preloaded_cfg=cfg)
    pipeline.items = [SimpleNamespace(label="rule_A")]

    class _FakePlanner:
        def plan_items(self, items):
            return []

        def plan_vias(self):
            return []

        def plan_tracks(self):
            return []

    pipeline.planner = _FakePlanner()

    fake_move = MoveCommand(ref="R18", position=Vector2.from_xy(int(10 * MM), int(20 * MM)),
                            angle=Angle.from_degrees(90.0), layer=BoardLayer.BL_F_Cu)
    monkeypatch.setattr("kicadstamp.apply_pipeline.build_coordinate_moves",
                        lambda adapter, coordinate_placements, points=None, sheet_names=None: [fake_move])

    report = pipeline._dry_run()
    text = "\n".join(report)

    assert "Coordinate placements" in text
    assert "  R18: (10.000, 20.000) mm, angle=90.0°" in text


def test_dry_run_report_omits_coordinate_placements_section_when_empty():
    from types import SimpleNamespace

    cfg = Config(layer='F.Cu', cells={}, rules=[], clone_placements=[], coordinate_placements=[])
    pipeline = ApplyPipeline("board.yaml", dry_run=True, preloaded_cfg=cfg)
    pipeline.items = [SimpleNamespace(label="rule_A")]

    class _FakePlanner:
        def plan_items(self, items):
            return []

        def plan_vias(self):
            return []

        def plan_tracks(self):
            return []

    pipeline.planner = _FakePlanner()

    report = pipeline._dry_run()
    text = "\n".join(report)

    assert "Coordinate placements" not in text
