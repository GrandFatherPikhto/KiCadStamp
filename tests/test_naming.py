#!/usr/bin/env python3
"""Tests for name/--only identity.

ThermalViaArrayConfig.name/ClonePlacement.name — REQUIRED in YAML (the loader
fatals if missing), no fallback to thermal_<pad>/'?'.

Rule.name — OPTIONAL: falls back to net (rule_effective_name), since net is
not fit to be a grouping label (Cluster exists for that), but is perfectly
fine as the identity of a SINGLE rule when no explicit name is given. The
loader fatals if two rules resolve to the same effective identity (see
config/loader.py) — not a silent pick of one over the other.

See --only in kicadstamp_cli.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import (
    Rule, ThermalViaArrayConfig,
    rule_effective_name, thermal_via_array_effective_name, load_config,
)
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError


def _cfg(**sections) -> dict:
    """Base config dict (layer/rules/cells always present, like the old YAML
    MINIMAL); each optional section overrides the base default when given."""
    data = {"layer": "B.Cu", "rules": [], "cells": {}}
    for k, v in sections.items():
        if v is not None:
            data[k] = v
    return data


def _write(tmp_path, name, data) -> Path:
    p = tmp_path / name
    p.write_text(dict_to_sexp(data), encoding="utf-8")
    return p


class TestEffectiveNameAccessors:
    """rule_effective_name/thermal_via_array_effective_name — just .name
    for ThermalViaArrayConfig (the loader guarantees it's set for anything
    actually loaded from YAML); for Rule, .name or a fallback to .net."""

    def test_rule_effective_name_is_plain_name(self):
        rule = Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA", name="fpga_3v3_bank")
        assert rule_effective_name(rule) == "fpga_3v3_bank"

    def test_rule_effective_name_falls_back_to_net(self):
        rule = Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA")
        assert rule.name is None
        assert rule_effective_name(rule) == "+3V3_VCCIO"

    def test_thermal_effective_name_is_plain_name(self):
        tva = ThermalViaArrayConfig(retired=False, anchor_role="FPGA", pad="145", name="fpga_thermal")
        assert thermal_via_array_effective_name(tva) == "fpga_thermal"


def test_thermal_via_array_retired_defaults_false():
    """Constructed directly in Python (bypassing the YAML loader),
    ThermalViaArrayConfig's 'retired' field defaults to False — unified with
    Rule/ManualSpoke/ClonePlacement."""
    assert ThermalViaArrayConfig().retired is False


def test_thermal_via_arrays_absent_section_is_an_empty_list(tmp_path):
    """2026-08-02: thermal_via_array (one, always-present field with a
    special retired=True-when-absent sentinel) became thermal_via_arrays (a
    real list, default_factory=list) — an absent section is now simply an
    empty list, same as absent rules:/clone_placements:, no sentinel hack
    needed."""
    config_file = _write(tmp_path, "test.sexp", _cfg())
    cfg, _ = load_config(str(config_file))

    assert cfg.thermal_via_arrays == []


BASE_CONFIG = _cfg(
    thermal_via_arrays=[
        {"anchor_role": "FPGA", "pad": "145", "name": "fpga_thermal"},
    ],
    rules=[
        {"net": "+3V3_VCCIO", "anchor_role": "FPGA", "name": "fpga_3v3_bank",
         "spokes": [{"pad": "17", "cell": "cap_pair_standard"}]},
    ],
    cells={"cap_pair_standard": {"components": [], "vias": []}},
)


class TestNameLoadedFromConfig:
    """name: actually reaches Rule/ThermalViaArrayConfig from the config, not
    just accepted by the dataclass constructor (regression check on loader.py;
    s-expr fixtures since 2026-08-28, core_yaml_removal)."""

    def test_rule_name_loaded(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", BASE_CONFIG)
        cfg, _ = load_config(str(config_file))

        rule = cfg.rules[0]
        assert rule.name == "fpga_3v3_bank"
        assert rule_effective_name(rule) == "fpga_3v3_bank"

    def test_thermal_via_array_name_loaded(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", BASE_CONFIG)
        cfg, _ = load_config(str(config_file))

        assert cfg.thermal_via_arrays[0].name == "fpga_thermal"
        assert thermal_via_array_effective_name(cfg.thermal_via_arrays[0]) == "fpga_thermal"


class TestNameRequired:
    """Without name: — fatal, not a silent fallback/'?'. Two remaining
    places (Rule is the exception now, see TestRuleNameOptional below):
    every thermal_via_arrays entry, clone_placement (closes an old hole
    with a silent '?')."""

    def test_thermal_via_array_without_name_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", _cfg(
            thermal_via_arrays=[{"anchor_role": "FPGA", "pad": "145"}]))
        with pytest.raises(ValidationError):
            load_config(str(config_file))

    def test_absent_thermal_via_arrays_section_is_not_fatal(self, tmp_path):
        """Section absent from the config entirely — not the same as "present
        but without name" — nothing is being named here, no error, just an
        empty list (see test_thermal_via_arrays_absent_section_is_an_empty_list
        above)."""
        config_file = _write(tmp_path, "test.sexp", _cfg())
        cfg, _ = load_config(str(config_file))
        assert cfg.thermal_via_arrays == []

    def test_clone_placement_without_name_is_fatal(self, tmp_path):
        """role: is not a ClonePlacement key (it's the old name, pre-2026-08-24
        rename) — dict_to_sexp would fatal at SERIALIZE time, so hand-write the
        s-expr: the rejection is the parser's "unknown key in a record" fatal
        (a ValidationError either way)."""
        config_file = tmp_path / "test.sexp"
        config_file.write_text(
            "(kicadstamp-config\n"
            '  (layer "B.Cu")\n'
            "  (rules)\n"
            "  (cells)\n"
            "  (clone_placements\n"
            '    (clone_placement (role "SOMETHING") (xy 0.0 0.0))))\n',
            encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(str(config_file))


class TestRuleNameOptional:
    """Rule.name — the one exception from TestNameRequired: optional, net
    is a working fallback for the identity of a SINGLE rule (not a grouping
    mechanism)."""

    def test_rule_without_name_loads_fine(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", _cfg(
            rules=[{"net": "+3V3_VCCIO", "anchor_role": "FPGA"}]))
        cfg, _ = load_config(str(config_file))
        rule = cfg.rules[0]
        assert rule.name is None
        assert rule_effective_name(rule) == "+3V3_VCCIO"

    def test_two_rules_same_net_without_name_is_fatal(self, tmp_path):
        """Two anchors (e.g. two different ICs) on the same GND net without
        a distinguishing name: — an --only identity collision, must be
        caught at load time, not silently resolved in favour of either one."""
        config_file = _write(tmp_path, "test.sexp", _cfg(
            rules=[{"net": "GND", "anchor_role": "FPGA"},
                   {"net": "GND", "anchor_role": "GD32F470"}]))
        with pytest.raises(ValidationError):
            load_config(str(config_file))

    def test_two_rules_same_net_with_distinguishing_name_is_ok(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", _cfg(
            rules=[{"net": "GND", "anchor_role": "FPGA", "name": "fpga_gnd"},
                   {"net": "GND", "anchor_role": "GD32F470"}]))
        cfg, _ = load_config(str(config_file))
        assert rule_effective_name(cfg.rules[0]) == "fpga_gnd"
        assert rule_effective_name(cfg.rules[1]) == "GND"


class TestRuleRetired:
    """Rule.retired — symmetric with ManualSpoke.retired/ClonePlacement.retired/
    ThermalViaArrayConfig.retired, default False."""

    def test_default_is_not_retired(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", _cfg(
            rules=[{"net": "+3V3_VCCIO", "anchor_role": "FPGA"}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.rules[0].retired is False

    def test_retired_true_loaded_from_config(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", _cfg(
            rules=[{"net": "+3V3_VCCIO", "anchor_role": "FPGA", "retired": True}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.rules[0].retired is True


class TestRuleSkip:
    """Rule.skip / ManualSpoke.skip — orthogonal to retired (default False),
    the inline per-item counterpart of --only/--cluster (see
    drop_inactive_items in kicadstamp_cli.py, added 2026-07-29)."""

    def test_default_is_not_skip(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", _cfg(
            rules=[{"net": "+3V3_VCCIO", "anchor_role": "FPGA",
                    "spokes": [{"pad": "17", "cell": "t"}]}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.rules[0].skip is False
        assert cfg.rules[0].spokes[0].skip is False

    def test_skip_true_loaded_from_config_on_rule_and_spoke(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", _cfg(
            rules=[{"net": "+3V3_VCCIO", "anchor_role": "FPGA", "skip": True,
                    "spokes": [{"pad": "17", "cell": "t", "skip": True}]}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.rules[0].skip is True
        assert cfg.rules[0].spokes[0].skip is True

    def test_thermal_via_array_skip_true_loaded_from_config(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", _cfg(
            thermal_via_arrays=[{"anchor_role": "FPGA", "pad": "145",
                                 "name": "fpga_thermal", "skip": True}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.thermal_via_arrays[0].skip is True
