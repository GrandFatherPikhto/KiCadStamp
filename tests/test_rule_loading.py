#!/usr/bin/env python3
"""Tests for load_rule() — the standalone per-entry Rule validator
extracted 2026-08-05 from load_config()'s own inline loop (see
loader.py's _load_rule docstring), needed by gui/docks/rules.py to
validate a single Rule the same way Save/Redraw validate everything else.
The cross-rule name/net collision check stays load_config-only — see
tests/test_naming.py for that coverage, unaffected by this extraction."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import load_rule
from kicadstamp.exceptions import ValidationError


def test_anchor_role_rule_loads():
    rule = load_rule({"net": "+3V3", "anchor_role": "FPGA", "spokes": []})
    assert rule.net == "+3V3"
    assert rule.anchor_role == "FPGA"
    assert rule.spokes == []
    assert rule.name is None
    assert rule.retired is False
    assert rule.skip is False


def test_anchor_ref_with_sheet_and_cluster_and_spokes():
    rule = load_rule({
        "net": "+3V3", "anchor_role": "FPGA", "anchor_sheet": "Channel_1",
        "anchor_cluster": "PWR_BANK", "name": "fpga_3v3", "retired": True, "skip": True,
        "spokes": [{"pad": "17", "cell": "cap_pair", "shift_x_mm": 1.2, "rotation_deg": 90.0}],
    })
    assert rule.anchor_sheet == "Channel_1"
    assert rule.anchor_cluster == "PWR_BANK"
    assert rule.name == "fpga_3v3"
    assert rule.retired is True
    assert rule.skip is True
    assert len(rule.spokes) == 1
    assert rule.spokes[0].pad == "17"
    assert rule.spokes[0].cell == "cap_pair"
    assert rule.spokes[0].shift_x_mm == 1.2
    assert rule.spokes[0].rotation_deg == 90.0


def test_anchor_point_rule_loads():
    rule = load_rule({"net": "+3V3", "anchor_point": "fpga_center", "spokes": []})
    assert rule.anchor_point == "fpga_center"
    assert rule.anchor_ref is None
    assert rule.anchor_role is None


def test_rule_sheet_reads_and_defaults_to_none():
    """§1.0 (anchor dependency tree): Rule gains its own optional `sheet` —
    the same nature as ClonePlacement.sheet / CoordinatePlacement.sheet.
    Read from YAML, defaults to None; configs written before this field
    continue to load unchanged (purely additive)."""
    with_sheet = load_rule(
        {"net": "+3V3", "anchor_role": "FPGA", "sheet": "Channel_0", "spokes": []})
    assert with_sheet.sheet == "Channel_0"

    without_sheet = load_rule({"net": "+3V3", "anchor_role": "FPGA", "spokes": []})
    assert without_sheet.sheet is None


def test_anchor_ref_and_role_together_is_fatal():
    with pytest.raises(ValidationError, match="mutually exclusive"):
        load_rule({"net": "+3V3", "anchor_ref": "U3", "anchor_role": "FPGA", "spokes": []})


def test_anchor_sheet_without_role_is_fatal():
    with pytest.raises(ValidationError, match="anchor_sheet without anchor_role"):
        load_rule({"net": "+3V3", "anchor_ref": "U3", "anchor_sheet": "Channel_1", "spokes": []})


def test_anchor_point_with_anchor_role_is_fatal():
    with pytest.raises(ValidationError, match="mutually exclusive"):
        load_rule({"net": "+3V3", "anchor_role": "FPGA", "anchor_point": "p1", "spokes": []})


def test_no_anchor_at_all_is_fatal():
    with pytest.raises(ValidationError, match="without anchor_ref/anchor_role/anchor_point"):
        load_rule({"net": "+3V3", "spokes": []})


def test_unknown_key_is_fatal():
    with pytest.raises(ValidationError, match="unknown fields"):
        load_rule({"net": "+3V3", "anchor_role": "FPGA", "spokes": [], "bogus_field": 1})


def test_spokes_default_to_empty_list_when_omitted():
    rule = load_rule({"net": "+3V3", "anchor_role": "FPGA"})
    assert rule.spokes == []


def test_polar_spoke_fields_load():
    rule = load_rule({
        "net": "+3V3", "anchor_role": "FPGA",
        "spokes": [{"pad": "17", "cell": "cap_pair", "radius_mm": 5.0, "angle_deg": 37.0}],
    })
    spoke = rule.spokes[0]
    assert spoke.radius_mm == 5.0
    assert spoke.angle_deg == 37.0
    assert spoke.shift_x_mm == 0.0
    assert spoke.shift_y_mm == 0.0


def test_spoke_shift_and_polar_together_is_fatal():
    with pytest.raises(ValidationError, match="mutually exclusive"):
        load_rule({
            "net": "+3V3", "anchor_role": "FPGA",
            "spokes": [{"pad": "17", "cell": "t", "shift_x_mm": 1.0,
                        "radius_mm": 5.0, "angle_deg": 0.0}],
        })


def test_spoke_partial_polar_is_fatal():
    with pytest.raises(ValidationError, match="polar mode needs BOTH"):
        load_rule({
            "net": "+3V3", "anchor_role": "FPGA",
            "spokes": [{"pad": "17", "cell": "t", "radius_mm": 5.0}],
        })


# ── rules: -> chains: read alias (2026-09-01, Rule -> Chain rename) ────────
#
# Old profiles still carrying the legacy `rules:` section key must load
# unchanged: normalize_section_aliases() maps it to `chains:` in every raw
# reader (config/includes.py::_load_config_file, config_writer._read_data,
# gui/yaml_io.load_data, tools/sexp_config_convert._read_dict), and the sexp
# parser maps `(rules ...)` -> `(chains ...)` at parse time.

def test_alias_legacy_rules_key_loads_as_chains(tmp_path):
    from kicadstamp.config import load_config
    from kicadstamp.config.sexp_format import dict_to_sexp

    # A profile written with the LEGACY key, as .sexp on disk.
    legacy = {"cells": {}, "rules": [
        {"net": "+3V3", "anchor_role": "FPGA", "spokes": [
            {"pad": "17", "cell": "c", "shift_x_mm": 1.2}]},
    ]}
    p = tmp_path / "legacy.sexp"
    p.write_text(dict_to_sexp(legacy), encoding="utf-8")

    cfg, _ = load_config(str(p))
    assert len(cfg.chains) == 1
    assert cfg.chains[0].net == "+3V3"
    assert cfg.chains[0].anchor_role == "FPGA"
    assert cfg.chains[0].spokes[0].pad == "17"


def test_alias_legacy_rules_sexp_tag_parses(tmp_path):
    from kicadstamp.config.sexp_format import sexp_to_dict

    data = sexp_to_dict(
        '(kicadstamp-config (rules (chain (net "x") '
        '(spokes (spoke (pad "1") (cell "c"))))))'
    )
    assert data["chains"][0]["net"] == "x"


def test_alias_both_keys_in_one_file_is_fatal(tmp_path):
    import json

    from kicadstamp.config import load_config

    # JSON/YAML carry the raw dict into normalize_section_aliases, which must
    # fatal when a file has BOTH the legacy and the canonical key (ambiguous).
    # (In .sexp the parser collapses `(rules ...)` into `chains` at parse time,
    # so the two cannot coexist there — the fatal applies to the raw-dict path.)
    p = tmp_path / "both.json"
    p.write_text(json.dumps({"cells": {}, "rules": [], "chains": []}),
                 encoding="utf-8")

    with pytest.raises(ValidationError, match="both"):
        load_config(str(p))


def test_alias_legacy_json_key_loads_as_chains(tmp_path):
    import json

    from kicadstamp.config import load_config

    p = tmp_path / "legacy.json"
    p.write_text(json.dumps({"cells": {}, "rules": [
        {"net": "GND", "anchor_ref": "U1", "spokes": []}]}), encoding="utf-8")

    cfg, _ = load_config(str(p))
    assert [c.net for c in cfg.chains] == ["GND"]
