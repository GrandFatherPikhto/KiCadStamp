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
