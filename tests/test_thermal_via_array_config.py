#!/usr/bin/env python3
"""load_thermal_via_array — public single-entry validator extracted from
load_config()'s inline loop (2026-08-03), mirroring load_clone_placement's
existing public/private split — see test_clone_placement_config.py's
test_load_clone_placement_is_a_public_alias for the same shape."""

import pytest
from kicadstamp.exceptions import ValidationError


def test_load_thermal_via_array_is_a_public_alias():
    """gui/docks/thermal_via.py must use a public entry point, not the
    private _load_thermal_via_array; the alias lives in
    kicadstamp.config.__all__."""
    import kicadstamp.config as config

    assert "load_thermal_via_array" in config.__all__
    tva = config.load_thermal_via_array({"name": "t", "anchor_ref": "U1", "pad": "1"})
    assert tva.name == "t"
    assert isinstance(tva, config.ThermalViaArrayConfig)


def test_defaults_match_config_load_config_behavior():
    import kicadstamp.config as config

    tva = config.load_thermal_via_array({"name": "t", "anchor_ref": "U1", "pad": "1"})
    assert tva.net == "GND"
    assert tva.rows == 4 and tva.cols == 4
    assert tva.margin_mm == 0.5
    assert tva.pattern == "grid"
    assert tva.drill_mm == 0.3
    assert tva.diameter_mm == 0.5
    assert tva.retired is False and tva.skip is False


def test_missing_name_raises():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="without name"):
        config.load_thermal_via_array({"anchor_ref": "U1", "pad": "1"})


def test_anchor_point_with_anchor_ref_raises():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="anchor_point together with anchor_ref"):
        config.load_thermal_via_array(
            {"name": "t", "anchor_ref": "U1", "anchor_point": "p1", "pad": "1"})


def test_unknown_field_raises():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="unknown fields"):
        config.load_thermal_via_array({"name": "t", "anchor_ref": "U1", "pad": "1", "bogus": 1})
