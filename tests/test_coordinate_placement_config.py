#!/usr/bin/env python3
"""load_coordinate_placement — public single-entry validator for
CoordinatePlacement (the "dumb placer", 2026-08-12), mirroring
load_thermal_via_array's public/private split — see
test_thermal_via_array_config.py for the same shape."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.exceptions import ValidationError


def test_load_coordinate_placement_is_a_public_alias():
    import kicadstamp.config as config

    assert "load_coordinate_placement" in config.__all__
    cp = config.load_coordinate_placement(
        {"cluster": "FPGA_PERIPH", "role": "R18", "x_mm": 10.0, "y_mm": 20.0, "rotation_deg": 0.0})
    assert cp.cluster == "FPGA_PERIPH" and cp.role == "R18"
    assert isinstance(cp, config.CoordinatePlacement)


def test_defaults_match_config_load_config_behavior():
    import kicadstamp.config as config

    cp = config.load_coordinate_placement(
        {"cluster": "FPGA_PERIPH", "role": "R18", "x_mm": 10.0, "y_mm": 20.0, "rotation_deg": 0.0})
    assert cp.name is None  # effective name is derived, see coordinate_placement_effective_name
    assert cp.anchor == "center"
    assert cp.anchor_pad is None
    assert cp.retired is False and cp.skip is False


def test_effective_name_defaults_to_cluster_slash_role():
    import kicadstamp.config as config

    cp = config.load_coordinate_placement(
        {"cluster": "FPGA_PERIPH", "role": "R18", "x_mm": 10.0, "y_mm": 20.0, "rotation_deg": 0.0})
    assert config.coordinate_placement_effective_name(cp) == "FPGA_PERIPH/R18"


def test_explicit_name_wins_over_default():
    import kicadstamp.config as config

    cp = config.load_coordinate_placement(
        {"cluster": "FPGA_PERIPH", "role": "R18", "name": "my_row",
         "x_mm": 10.0, "y_mm": 20.0, "rotation_deg": 0.0})
    assert config.coordinate_placement_effective_name(cp) == "my_row"


def test_missing_cluster_or_role_raises():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="missing cluster/role"):
        config.load_coordinate_placement({"role": "R18", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0})
    with pytest.raises(ValidationError, match="missing cluster/role"):
        config.load_coordinate_placement({"cluster": "X", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0})


def test_cartesian_needs_both_x_and_y():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="needs BOTH x_mm and y_mm"):
        config.load_coordinate_placement(
            {"cluster": "X", "role": "R1", "x_mm": 1.0, "rotation_deg": 0.0})


def test_cartesian_needs_explicit_rotation_deg():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="needs an explicit rotation_deg"):
        config.load_coordinate_placement({"cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0})


def test_polar_needs_all_four_fields():
    import kicadstamp.config as config

    # Message text consolidated 2026-08-12 (Group 3): the half-populated
    # polar fatal now comes from the shared _load_mutually_exclusive_position
    # ("polar mode needs all of: center_x_mm, ...").
    with pytest.raises(ValidationError, match="polar mode needs all of"):
        config.load_coordinate_placement(
            {"cluster": "X", "role": "R1", "center_x_mm": 0.0, "center_y_mm": 0.0, "radius_mm": 5.0})


def test_polar_angle_becomes_rotation_by_default():
    import kicadstamp.config as config

    cp = config.load_coordinate_placement({
        "cluster": "X", "role": "R1",
        "center_x_mm": 0.0, "center_y_mm": 0.0, "radius_mm": 5.0, "angle_deg": 45.0,
    })
    assert cp.rotation_deg is None  # resolved later by the geometry layer, not the loader
    assert cp.angle_deg == 45.0


def test_polar_rotation_override_is_kept_separate_from_angle():
    import kicadstamp.config as config

    cp = config.load_coordinate_placement({
        "cluster": "X", "role": "R1",
        "center_x_mm": 0.0, "center_y_mm": 0.0, "radius_mm": 5.0, "angle_deg": 45.0,
        "rotation_deg": 0.0,
    })
    assert cp.angle_deg == 45.0
    assert cp.rotation_deg == 0.0


def test_cartesian_and_polar_together_is_fatal():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="both Cartesian"):
        config.load_coordinate_placement({
            "cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0,
            "center_x_mm": 0.0, "center_y_mm": 0.0, "radius_mm": 5.0, "angle_deg": 45.0,
        })


def test_no_position_at_all_is_fatal():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="has no position"):
        config.load_coordinate_placement({"cluster": "X", "role": "R1"})


def test_anchor_pad_requires_anchor_pad_field():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="anchor: pad needs anchor_pad"):
        config.load_coordinate_placement(
            {"cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0,
             "anchor": "pad"})


def test_anchor_pad_without_anchor_pad_mode_is_fatal():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="anchor_pad set but anchor is 'center'"):
        config.load_coordinate_placement(
            {"cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0,
             "anchor_pad": "1"})


def test_invalid_anchor_value_is_fatal():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="anchor must be 'center' or 'pad'"):
        config.load_coordinate_placement(
            {"cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0,
             "anchor": "bogus"})


def test_anchor_pad_mode_round_trips():
    import kicadstamp.config as config

    cp = config.load_coordinate_placement(
        {"cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 90.0,
         "anchor": "pad", "anchor_pad": "2"})
    assert cp.anchor == "pad"
    assert cp.anchor_pad == "2"


def test_unknown_field_raises():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="unknown fields"):
        config.load_coordinate_placement(
            {"cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0, "bogus": 1})


def test_retired_and_skip_round_trip():
    import kicadstamp.config as config

    cp = config.load_coordinate_placement(
        {"cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0,
         "retired": True, "skip": True})
    assert cp.retired is True
    assert cp.skip is True


# ── Anchor-relative mode (2026-08-12, Group 0 consolidation) ────────────────

def test_anchor_point_cartesian_offset_loads():
    """anchor_point + x_mm/y_mm — the migrated root placements (all
    `anchor_point: Origin` + xy, 1:1 from ClonePlacement's cluster: mode):
    x_mm/y_mm become the OFFSET from the point, rotation_deg stays raw
    (default 0.0 resolved by the calculator)."""
    import kicadstamp.config as config

    cp = config.load_coordinate_placement({
        "cluster": "Conn_pm5v", "role": "Conn_pm5v",
        "anchor_point": "Origin", "x_mm": 10.0, "y_mm": -70.0, "rotation_deg": 270.0,
    })
    assert cp.anchor_point == "Origin"
    assert cp.x_mm == 10.0 and cp.y_mm == -70.0
    assert cp.rotation_deg == 270.0
    assert cp.center_x_mm is None and cp.center_y_mm is None
    assert cp.anchor_ref is None and cp.anchor_role is None


def test_anchor_point_without_offset_defaults_zero():
    import kicadstamp.config as config

    cp = config.load_coordinate_placement({
        "cluster": "X", "role": "R1", "anchor_point": "Origin",
    })
    assert cp.x_mm == 0.0 and cp.y_mm == 0.0
    assert cp.rotation_deg is None


def test_anchor_ref_polar_offset_rotation_defaults_to_angle():
    import kicadstamp.config as config

    cp = config.load_coordinate_placement({
        "cluster": "X", "role": "R1",
        "anchor_ref": "IC1", "radius_mm": 5.0, "angle_deg": 37.0,
    })
    assert cp.anchor_ref == "IC1"
    assert cp.radius_mm == 5.0 and cp.angle_deg == 37.0
    assert cp.rotation_deg is None  # defaults to angle_deg in the calculator
    assert cp.center_x_mm is None and cp.center_y_mm is None


def test_anchor_pad_names_the_anchor_components_pad():
    """In anchor-relative mode anchor_pad is the ANCHOR component's pad
    (Rule/ClonePlacement semantics — the offset is measured from it), not the
    self-referential one."""
    import kicadstamp.config as config

    cp = config.load_coordinate_placement({
        "cluster": "X", "role": "R1",
        "anchor_role": "FPGA", "anchor_pad": "A17", "x_mm": 2.0, "y_mm": 0.0,
    })
    assert cp.anchor_role == "FPGA"
    assert cp.anchor_pad == "A17"


def test_anchor_together_with_center_is_fatal():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="anchor together with center"):
        config.load_coordinate_placement({
            "cluster": "X", "role": "R1", "anchor_ref": "IC1",
            "center_x_mm": 0.0, "center_y_mm": 0.0, "radius_mm": 5.0, "angle_deg": 45.0,
        })


def test_anchor_with_self_referential_pad_is_fatal():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="anchor: pad"):
        config.load_coordinate_placement({
            "cluster": "X", "role": "R1", "anchor_ref": "IC1",
            "x_mm": 1.0, "y_mm": 2.0, "anchor": "pad",
        })


def test_anchor_ref_and_anchor_role_together_is_fatal():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="anchor_ref and anchor_role together"):
        config.load_coordinate_placement({
            "cluster": "X", "role": "R1",
            "anchor_ref": "IC1", "anchor_role": "FPGA", "x_mm": 0.0, "y_mm": 0.0,
        })


def test_anchor_sheet_without_anchor_role_is_fatal():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="anchor_sheet without anchor_role"):
        config.load_coordinate_placement({
            "cluster": "X", "role": "R1",
            "anchor_sheet": "Channel_0", "x_mm": 0.0, "y_mm": 0.0,
        })


def test_anchor_point_together_with_anchor_pad_is_fatal():
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="anchor_point together with anchor_pad"):
        config.load_coordinate_placement({
            "cluster": "X", "role": "R1",
            "anchor_point": "Origin", "anchor_pad": "1", "x_mm": 0.0, "y_mm": 0.0,
        })


def test_anchor_pad_without_anchor_in_absolute_mode_is_fatal():
    """With no anchor_ref/anchor_role/anchor_point the entry is in an ABSOLUTE
    mode, where anchor_pad means the SELF-referential pad — which requires
    anchor: pad, so a bare anchor_pad is rejected."""
    import kicadstamp.config as config

    with pytest.raises(ValidationError, match="anchor_pad set but anchor is 'center'"):
        config.load_coordinate_placement({
            "cluster": "X", "role": "R1", "anchor_pad": "1",
            "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0,
        })
