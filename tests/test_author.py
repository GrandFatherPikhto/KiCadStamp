#!/usr/bin/env python3
"""Tests for kicadstamp/author.py — build ClonePlacement/Rule in Python,
dump back to YAML, or feed straight into the apply pipeline."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import patch

import yaml

from kicadstamp.config import ClonePlacement, Config, ManualSpoke, Rule, load_config
from kicadstamp.author import (_prune_defaults, apply_config, dump_clone_placements,
                               dump_rules, dump_template)
from kicadstamp.author_cli import cli_main
from kicadstamp.apply_pipeline import RunOptions


class TestPruneDefaults:
    def test_drops_default_valued_fields(self):
        cp = ClonePlacement(name="c", cell="t", xy=(1.0, 2.0))
        d = _prune_defaults(cp)
        assert "rotation_deg" not in d      # default 0.0
        assert "retired" not in d           # default False
        assert "nets" not in d              # default_factory dict, empty

    def test_keeps_required_fields_regardless_of_value(self):
        cp = ClonePlacement(name="c", cell="t", xy=(0.0, 0.0))
        d = _prune_defaults(cp)
        # xy has no default at all -> always present, even though (0.0, 0.0)
        # also happens to be a "natural" default-looking value.
        assert d["name"] == "c"
        assert d["xy"] == [0.0, 0.0]

    def test_keeps_non_default_fields(self):
        cp = ClonePlacement(name="c", cell="t", xy=(1.0, 2.0),
                            rotation_deg=90.0, nets={"X": "NET_A"})
        d = _prune_defaults(cp)
        assert d["rotation_deg"] == 90.0
        assert d["nets"] == {"X": "NET_A"}

    def test_recurses_into_rule_spokes(self):
        rule = Rule(net="GND", spokes=[ManualSpoke(pad="1", cell="t", shift_x_mm=2.0)])
        d = _prune_defaults(rule)
        assert d["spokes"] == [{"pad": "1", "cell": "t", "shift_x_mm": 2.0}]


class TestDumpRoundTrip:
    def test_clone_placements_round_trip(self, tmp_path):
        clones = [
            ClonePlacement(name="channel_0_ad9707", cell="ad_dac",
                           anchor_role="FPGA", anchor_sheet="Channel_{channel}",
                           nets={"AD_DAC": "/Channel_{channel}/DAC/DAC_OUT_P"},
                           params={"channel": 0},
                           xy=(0.0, 25.0), rotation_deg=270.0),
        ]
        out = tmp_path / "generated.yaml"
        dump_clone_placements(clones, str(out))

        cfg, _ = load_config(str(out))
        assert len(cfg.clone_placements) == 1
        loaded = cfg.clone_placements[0]
        original = clones[0]
        assert loaded.name == original.name
        assert loaded.cell == original.cell
        assert loaded.anchor_role == original.anchor_role
        assert loaded.anchor_sheet == original.anchor_sheet
        assert loaded.nets == original.nets
        assert loaded.params == original.params
        assert loaded.xy == original.xy
        assert loaded.rotation_deg == original.rotation_deg

    def test_rules_round_trip(self, tmp_path):
        rules = [
            Rule(net="+3V3_VCCIO", name="+3V3_VCCIO", anchor_role="FPGA",
                spokes=[ManualSpoke(pad="17", cell="cap_pair_standard",
                                    shift_y_mm=-0.5, rotation_deg=90.0, cluster="FPGA_PWR_BANK")]),
        ]
        out = tmp_path / "generated_rules.yaml"
        dump_rules(rules, str(out))

        cfg, _ = load_config(str(out))
        assert len(cfg.rules) == 1
        loaded = cfg.rules[0]
        assert loaded.net == "+3V3_VCCIO"
        assert loaded.anchor_role == "FPGA"
        assert len(loaded.spokes) == 1
        assert loaded.spokes[0].pad == "17"
        assert loaded.spokes[0].shift_y_mm == -0.5
        assert loaded.spokes[0].cluster == "FPGA_PWR_BANK"

    def test_minimal_clone_placement_omits_defaults_in_yaml_text(self, tmp_path):
        """Sanity check on the actual written text, not just the round-trip —
        confirms the YAML stays close to hand-written minimal style."""
        clones = [ClonePlacement(name="c", cell="t", xy=(1.0, 2.0))]
        out = tmp_path / "generated.yaml"
        dump_clone_placements(clones, str(out))
        text = out.read_text(encoding="utf-8")
        assert "rotation_deg" not in text
        assert "retired" not in text


class TestApplyConfig:
    def test_builds_runoptions_with_every_field_run_apply_reads(self):
        """Regression guard: if run_apply grows a new RunOptions field, this
        test must be updated too — otherwise apply_config would silently
        stop forwarding it and fail at runtime."""
        cfg = Config()
        with patch("kicadstamp.author.run_apply") as mock_run_apply:
            apply_config(cfg, "my_run.yaml", dry_run=True, only=["a"], cluster=["b"],
                        timeout_ms=1234, batch_size=5, no_collision_check=True,
                        collision_margin=0.5)

        mock_run_apply.assert_called_once()
        options = mock_run_apply.call_args.args[0]
        assert isinstance(options, RunOptions)
        assert mock_run_apply.call_args.kwargs["cfg"] is cfg
        assert options.config_path == "my_run.yaml"
        assert options.dry_run is True
        assert options.only == ["a"]
        assert options.cluster == ["b"]
        assert options.timeout_ms == 1234
        assert options.batch_size == 5
        assert options.no_collision_check is True
        assert options.collision_margin == 0.5

    def test_defaults_match_cli_defaults(self):
        cfg = Config()
        with patch("kicadstamp.author.run_apply") as mock_run_apply:
            apply_config(cfg, "my_run.yaml")

        options = mock_run_apply.call_args.args[0]
        assert options.dry_run is False
        assert options.only is None
        assert options.cluster is None
        assert options.no_collision_check is False
        assert options.collision_margin == 0.2

    def test_forwards_ctx_to_run_apply(self):
        """Regression: ctx (RuntimeContext, carries sheet_names built from
        schematic_dir) must reach run_apply — otherwise anchor_sheet-based
        clone_placements fatal with "sheet name dictionary is empty" even
        though schematic_dir was set and parsed correctly."""
        cfg = Config()
        ctx = object()
        with patch("kicadstamp.author.run_apply") as mock_run_apply:
            apply_config(cfg, "my_run.yaml", ctx=ctx)

        assert mock_run_apply.call_args.kwargs["ctx"] is ctx

    def test_ctx_defaults_to_none(self):
        cfg = Config()
        with patch("kicadstamp.author.run_apply") as mock_run_apply:
            apply_config(cfg, "my_run.yaml")

        assert mock_run_apply.call_args.kwargs["ctx"] is None


class TestCliMain:
    """cli_main — the shared --apply/--dry-run entry point every
    boards/*/scripts/*.py generator uses instead of copy-pasting its own
    argparse block (see dac_channels.py/dac_pi_filter.py)."""

    @staticmethod
    def _build():
        return [ClonePlacement(name="c", cell="t", xy=(1.0, 2.0))]

    def test_without_apply_only_writes_output(self, tmp_path):
        out = tmp_path / "generated.yaml"
        with patch("kicadstamp.author_cli.load_config") as mock_load_config, \
             patch("kicadstamp.author_cli.apply_config") as mock_apply_config:
            cli_main(self._build, str(out), "root.yaml", argv=[])

        assert out.exists()
        mock_load_config.assert_not_called()
        mock_apply_config.assert_not_called()

    def test_apply_dry_run_loads_root_config_and_forwards_dry_run(self, tmp_path):
        """Regression: cli_main used to discard load_config()'s ctx (RuntimeContext,
        carries sheet_names) as `_ctx` and never forward it to apply_config(), so
        anchor_sheet-based clone_placements would fatal with "sheet name dictionary
        is empty" even though sheet_names had been built correctly."""
        out = tmp_path / "generated.yaml"
        with patch("kicadstamp.author_cli.load_config") as mock_load_config, \
             patch("kicadstamp.author_cli.apply_config") as mock_apply_config:
            mock_load_config.return_value = ("cfg-sentinel", "ctx-sentinel")
            cli_main(self._build, str(out), "root.yaml", argv=["--apply", "--dry-run"])

        mock_load_config.assert_called_once_with("root.yaml")
        mock_apply_config.assert_called_once_with(
            "cfg-sentinel", "root.yaml", ctx="ctx-sentinel", dry_run=True)

    def test_apply_without_dry_run_forwards_dry_run_false(self, tmp_path):
        out = tmp_path / "generated.yaml"
        with patch("kicadstamp.author_cli.load_config") as mock_load_config, \
             patch("kicadstamp.author_cli.apply_config") as mock_apply_config:
            mock_load_config.return_value = ("cfg-sentinel", None)
            cli_main(self._build, str(out), "root.yaml", argv=["--apply"])

        assert mock_apply_config.call_args.kwargs["dry_run"] is False

    def test_creates_missing_parent_directories(self, tmp_path):
        out = tmp_path / "nested" / "dir" / "generated.yaml"
        with patch("kicadstamp.author_cli.load_config"), \
             patch("kicadstamp.author_cli.apply_config"):
            cli_main(self._build, str(out), "root.yaml", argv=[])

        assert out.exists()


class TestDumpTemplate:
    def test_writes_template_dict_wrapped_in_cells(self, tmp_path):
        """Wrapped under 'cells:' since 2026-08-02 (cells_file:/cell_files:
        folded into include:, which expects the same wrapped shape as an
        inline cells: block)."""
        template_dict = {"cap_pair_standard": {"components": [
            {"role": "C_IN_BULK", "offset_along_mm": 0.0, "offset_across_mm": 0.0, "angle_deg": 0.0},
        ]}}
        out = tmp_path / "cell.yaml"
        dump_template(template_dict, str(out))

        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert loaded == {"cells": template_dict}

    def test_overwrites_rather_than_merges(self, tmp_path):
        """Unlike cmd_extract's merge-into-existing behaviour, dump_template
        always overwrites — a script regenerating its own dedicated file
        should get a clean result, not accumulate stale entries."""
        out = tmp_path / "cell.yaml"
        dump_template({"old_name": {"components": []}}, str(out))
        dump_template({"new_name": {"components": []}}, str(out))

        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert loaded == {"cells": {"new_name": {"components": []}}}
