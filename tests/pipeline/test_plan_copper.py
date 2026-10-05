# tests/pipeline/test_plan_copper.py
"""СЦ-2 guard (plan_2026_10_05_select_cell_split): ``ApplyPipeline.plan_copper``
is the ONE copper-planning entry.

Property: the dry-run report and the read-only "Select cell" path plan copper
through the SAME public method, so a preview and a selection cannot disagree.
The report itself is unchanged — its golden assertions stay in
``tests/pipeline/test_dry_run_report.py`` (never edited to pass, §33); here we
pin the DELEGATION only.
"""
from types import SimpleNamespace

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import Config
from kicadstamp.domain.geometry import Angle, BoardLayer, Vector2
from kicadstamp.placement.commands import TrackCommand, ViaCommand


def _pipeline():
    cfg = Config(layer='F.Cu', cells={}, chains=[], clone_placements=[])
    pipeline = ApplyPipeline("board.sexp", dry_run=True, preloaded_cfg=cfg)
    pipeline.items = []
    return pipeline


def test_plan_copper_returns_the_planners_copper():
    pipeline = _pipeline()
    pipeline.planner = SimpleNamespace(plan_items=lambda items: ["M"],
                                       plan_vias=lambda: ["V"], plan_tracks=lambda: ["T"])
    assert pipeline.plan_copper() == (["V"], ["T"])
    # moves ride along for the dry run, which is the only caller needing them
    assert pipeline.planned_moves == ["M"]


def test_dry_run_plans_copper_through_plan_copper():
    """The dry run calls ``plan_copper()`` — one host, not a second inline
    planner — and renders its vias/tracks into the report."""
    pipeline = _pipeline()
    pipeline.planner = SimpleNamespace(plan_items=lambda items: [])
    via = ViaCommand(position=Vector2.from_xy(0, 0), drill_mm=0.3,
                     diameter_mm=0.6, net_name="GND", owner_ref="C2")
    track = TrackCommand(start=Vector2.from_xy(0, 0), end=Vector2.from_xy(1_000_000, 0),
                         width_mm=0.25, net_name="+5V", layer=BoardLayer.BL_F_Cu,
                         owner_ref="C3")
    calls = []

    def fake_plan_copper():
        calls.append(1)
        return [via], [track]

    pipeline.plan_copper = fake_plan_copper
    text = "\n".join(pipeline._dry_run())
    assert calls == [1]
    assert "  via for C2: (0.000, 0.000) mm, net=GND" in text
    assert "  track for C3: (0.000, 0.000) -> (1.000, 0.000) mm, net=+5V, width=0.25 mm" in text
    assert Angle is not None  # keep the geometry import used by the report path
