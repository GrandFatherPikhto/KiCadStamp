#!/usr/bin/env python3
"""П.8 tests: dry-run produces a structured, printable report (list of lines)
instead of the library printing to stdout directly. Covers:

  - ApplyPipeline._dry_run(): returns List[str], stores it on
    self.dry_run_report, and prints nothing itself (capsys).
  - ApplyPipeline.run(): returns the report when dry_run=True, None otherwise.
  - run_apply()/cmd_apply(): propagate the report up to the CLI layer.
  - author.apply_config() returns it; author_cli.cli_main() prints it.
  - kicadstamp_cli.main() (_dispatch) prints it for `apply --dry-run`.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock, patch

from kicadstamp.domain.geometry import Vector2, Angle
from kicadstamp.domain.geometry import BoardLayer

from kicadstamp.apply_pipeline import ApplyPipeline, RunOptions, cmd_apply, run_apply
from kicadstamp.author import apply_config
from kicadstamp.author_cli import cli_main
from kicadstamp.config import Config
from kicadstamp.placement.commands import MoveCommand, TrackCommand, ViaCommand

MM = 1_000_000


class _FakePlanner:
    """Planner stand-in: returns one command of each kind so the report has
    all three sections (moves / vias / tracks)."""

    def plan_items(self, items):
        return [MoveCommand(ref="C1",
                            position=Vector2.from_xy(int(51.123 * MM), int(22.5 * MM)),
                            angle=Angle.from_degrees(90.0), layer=BoardLayer.BL_F_Cu)]

    def plan_vias(self):
        return [ViaCommand(position=Vector2.from_xy(int(10 * MM), int(20 * MM)),
                           drill_mm=0.3, diameter_mm=0.6, net_name="GND", owner_ref="C2")]

    def plan_tracks(self):
        return [TrackCommand(start=Vector2.from_xy(int(1 * MM), int(2 * MM)),
                             end=Vector2.from_xy(int(3 * MM), int(4 * MM)),
                             width_mm=0.25, net_name="+5V", layer=BoardLayer.BL_F_Cu,
                             owner_ref="C3")]


def _pipeline(dry_run=True):
    cfg = Config(layer='F.Cu', cells={},
                 rules=[], clone_placements=[])
    pipeline = ApplyPipeline("board.sexp", dry_run=dry_run, preloaded_cfg=cfg)
    pipeline.items = [SimpleNamespace(label="rule_A"), SimpleNamespace(label="rule_B")]
    pipeline.planner = _FakePlanner()
    return pipeline


class TestDryRunReportIsStructured:
    def test_returns_list_of_lines_and_prints_nothing(self, capsys):
        pipeline = _pipeline()
        report = pipeline._dry_run()
        out = capsys.readouterr().out
        assert isinstance(report, list)
        assert all(isinstance(line, str) for line in report)
        assert out == ""  # the library itself must not print (П.8)
        assert pipeline.dry_run_report is report

    def test_report_content(self):
        report = _pipeline()._dry_run()
        text = "\n".join(report)
        assert "\n=== DRY RUN ===" in text
        assert "Order: rule_A -> rule_B" in text
        assert "  C1: (51.123, 22.500) mm, angle=90.0°" in text
        assert "  via for C2: (10.000, 20.000) mm, net=GND" in text
        assert ("  track for C3: (1.000, 2.000) -> (3.000, 4.000) mm, "
                "net=+5V, width=0.25 mm") in text


class TestRunReturnsReport:
    def test_dry_run_returns_report(self):
        pipeline = ApplyPipeline("x.sexp", dry_run=True)
        with patch.object(ApplyPipeline, "_load_config"), \
             patch.object(ApplyPipeline, "_filter_config"), \
             patch.object(ApplyPipeline, "_connect_adapter"), \
             patch.object(ApplyPipeline, "_validate"), \
             patch.object(ApplyPipeline, "_resolve_order"), \
             patch.object(ApplyPipeline, "_create_planner"), \
             patch.object(ApplyPipeline, "_dry_run", return_value=["a", "b"]) as m_dry:
            result = pipeline.run()
        assert result == ["a", "b"]
        m_dry.assert_called_once_with()

    def test_execute_run_returns_none(self):
        pipeline = ApplyPipeline("x.sexp", dry_run=False)
        with patch.object(ApplyPipeline, "_load_config"), \
             patch.object(ApplyPipeline, "_filter_config"), \
             patch.object(ApplyPipeline, "_connect_adapter"), \
             patch.object(ApplyPipeline, "_validate"), \
             patch.object(ApplyPipeline, "_resolve_order"), \
             patch.object(ApplyPipeline, "_create_planner"), \
             patch.object(ApplyPipeline, "_execute") as m_exec, \
             patch.object(ApplyPipeline, "_dry_run") as m_dry:
            result = pipeline.run()
        assert result is None
        m_exec.assert_called_once_with()
        m_dry.assert_not_called()


class TestRunApplyAndCmdApplyPropagateReport:
    def test_run_apply_returns_pipeline_run_report(self):
        fake = MagicMock()
        fake.run.return_value = ["report-line"]
        with patch("kicadstamp.apply_pipeline.ApplyPipeline", return_value=fake) as m_cls:
            result = run_apply(RunOptions(config_path="x.sexp", dry_run=True))
        assert result == ["report-line"]
        m_cls.assert_called_once()

    def test_cmd_apply_returns_run_apply_result(self):
        with patch("kicadstamp.apply_pipeline.run_apply", return_value=["r"]) as m:
            result = cmd_apply(SimpleNamespace(config="c.sexp", timeout_ms=1, batch_size=2,
                                               dry_run=True, no_collision_check=True,
                                               collision_margin=0.3))
        assert result == ["r"]
        assert m.call_args.args[0].config_path == "c.sexp"
        assert m.call_args.args[0].dry_run is True


class TestAuthorReportPlumbing:
    def test_apply_config_returns_run_apply_result(self):
        with patch("kicadstamp.author.run_apply", return_value=["report-line"]) as m:
            result = apply_config(Config(layer='F.Cu', cells={}, rules=[]), "board.sexp",
                                  dry_run=True)
        assert result == ["report-line"]
        assert m.call_args.args[0].dry_run is True
        assert m.call_args.kwargs["cfg"] is not None

    def test_cli_main_prints_report_on_dry_run(self, tmp_path, capsys):
        out = tmp_path / "gen.sexp"

        def build():
            return []

        with patch("kicadstamp.author_cli.load_config", return_value=("cfg", "ctx")), \
             patch("kicadstamp.author_cli.apply_config", return_value=["line1", "line2"]) as m:
            cli_main(build, str(out), "root.sexp", argv=["--apply", "--dry-run"])
        captured = capsys.readouterr()
        assert "line1\nline2" in captured.out
        assert m.call_args.kwargs["dry_run"] is True


class TestKicadstampCliPrintsReport:
    def test_apply_dry_run_prints_report(self, monkeypatch, capsys):
        from kicadstamp import cli_main
        monkeypatch.setattr(sys, "argv", ["kicadstamp_cli.py", "apply", "cfg.sexp", "--dry-run"])
        monkeypatch.setattr(cli_main, "cmd_apply",
                            lambda args, cfg=None, ctx=None: ["rep1", "rep2"])
        code = cli_main.main()
        assert code == 0
        assert "rep1\nrep2" in capsys.readouterr().out
