#!/usr/bin/env python3
"""Tests for the Point config schema — points: loading/validation
(kicadstamp/config/points.py, loader.py's _load_point + cross-validation),
and includes.py duplicate-name detection. See handoff_2026_07_31_consolidated.md
for the design (Point as a named, reusable anchor + optional shift/xy).

2026-08-28, core_yaml_removal: fixtures are s-expr via dict_to_sexp — the
config graph reads .sexp/.json only (the one hand-written s-expr below exists
because dict_to_sexp itself would fatal on the unknown key at serialize time)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError


def _cfg(points=None, rules=None, cells=None, clone_placements=None,
         thermal_via_arrays=None) -> dict:
    """Base config dict with the standard minimal sections (layer/rules/cells);
    each optional section overrides the base default when given."""
    data = {"layer": "B.Cu", "rules": [], "cells": {}}
    if points is not None:
        data["points"] = points
    if rules is not None:
        data["rules"] = rules
    if cells is not None:
        data["cells"] = cells
    if clone_placements is not None:
        data["clone_placements"] = clone_placements
    if thermal_via_arrays is not None:
        data["thermal_via_arrays"] = thermal_via_arrays
    return data


def _write(tmp_path, name, data) -> Path:
    p = tmp_path / name
    p.write_text(dict_to_sexp(data), encoding="utf-8")
    return p


class TestPointBasicLoading:
    def test_anchor_ref_point_loads(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"fpga_center": {"anchor_role": "FPGA"}}))
        cfg, _ = load_config(str(config_file))

        assert "fpga_center" in cfg.points
        p = cfg.points["fpga_center"]
        assert p.name == "fpga_center"
        assert p.anchor_role == "FPGA"
        assert p.shift_x_mm == 0.0
        assert p.shift_y_mm == 0.0

    def test_anchor_ref_with_pad_and_shift(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"ch2_dac_origin": {
                "anchor_ref": "U5", "anchor_pad": "12",
                "shift_x_mm": 2.5, "shift_y_mm": -1.0}}))
        cfg, _ = load_config(str(config_file))

        p = cfg.points["ch2_dac_origin"]
        assert p.anchor_ref == "U5"
        assert p.anchor_pad == "12"
        assert p.shift_x_mm == 2.5
        assert p.shift_y_mm == -1.0

    def test_literal_xy_point_loads(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"board_origin": {"xy": [10.0, 20.0]}}))
        cfg, _ = load_config(str(config_file))

        p = cfg.points["board_origin"]
        assert p.xy == (10.0, 20.0)
        assert p.anchor_ref is None

    def test_chained_anchor_point_loads(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"fpga_center": {"anchor_role": "FPGA"},
                    "fpga_offset": {"anchor_point": "fpga_center", "shift_x_mm": 5.0}}))
        cfg, _ = load_config(str(config_file))

        assert cfg.points["fpga_offset"].anchor_point == "fpga_center"

    def test_unknown_field_is_fatal(self, tmp_path):
        """typo_field: is not a Point key — dict_to_sexp would fatal at
        SERIALIZE time (unknown key), so write the s-expr by hand: the
        rejection is the parser's "unknown key in a record" fatal (same
        protection, different message from the YAML loader's
        "unknown fields in point")."""
        config_file = tmp_path / "bad.sexp"
        config_file.write_text(
            "(kicadstamp-config\n"
            '  (layer "B.Cu")\n'
            "  (rules)\n"
            "  (cells)\n"
            "  (points\n"
            '    (point "bad" (anchor_role "FPGA") (typo_field 1))))\n',
            encoding="utf-8")
        with pytest.raises(ValidationError, match="unknown key"):
            load_config(str(config_file))


class TestPointMutualExclusion:
    def test_no_base_at_all_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"empty": {}}))
        with pytest.raises(ValidationError, match="has no anchor"):
            load_config(str(config_file))

    def test_anchor_ref_and_anchor_point_together_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"a": {"anchor_role": "FPGA"},
                    "b": {"anchor_ref": "U5", "anchor_point": "a"}}))
        with pytest.raises(ValidationError, match="more than one anchor base"):
            load_config(str(config_file))

    def test_xy_and_anchor_point_together_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"a": {"anchor_role": "FPGA"},
                    "b": {"xy": [1.0, 2.0], "anchor_point": "a"}}))
        with pytest.raises(ValidationError, match="more than one anchor base"):
            load_config(str(config_file))

    def test_shift_on_literal_xy_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"bad": {"xy": [1.0, 2.0], "shift_x_mm": 1.0}}))
        with pytest.raises(ValidationError, match="shift on a literal xy point"):
            load_config(str(config_file))

    def test_anchor_ref_and_anchor_role_together_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"bad": {"anchor_ref": "U5", "anchor_role": "FPGA"}}))
        with pytest.raises(ValidationError, match="anchor_ref and anchor_role together"):
            load_config(str(config_file))


class TestBoardOriginPoint:
    """anchor_origin (added 2026-08-06, Denis: "точка 0,0 -- это левый
    верхний угол листа, никак не origin") — the board's own live grid/
    drill-place origin, read via kipy (adapter.get_board_origin), not a
    config-file literal like xy."""

    def test_drill_origin_point_loads(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"board_zero": {"anchor_origin": "drill"}}))
        cfg, _ = load_config(str(config_file))

        p = cfg.points["board_zero"]
        assert p.anchor_origin == "drill"
        assert p.xy is None

    def test_grid_origin_point_loads(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"board_zero": {"anchor_origin": "grid"}}))
        cfg, _ = load_config(str(config_file))
        assert cfg.points["board_zero"].anchor_origin == "grid"

    def test_invalid_anchor_origin_value_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"bad": {"anchor_origin": "page"}}))
        with pytest.raises(ValidationError, match="invalid anchor_origin"):
            load_config(str(config_file))

    def test_anchor_origin_and_xy_together_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"bad": {"anchor_origin": "drill", "xy": [1.0, 2.0]}}))
        with pytest.raises(ValidationError, match="more than one anchor base"):
            load_config(str(config_file))

    def test_shift_on_anchor_origin_is_allowed(self, tmp_path):
        """Unlike xy (a literal you'd just edit directly instead), anchor_origin
        is a LIVE board value — shift is the only way to offset it."""
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"offset_from_drill": {"anchor_origin": "drill", "shift_x_mm": 5.0}}))
        cfg, _ = load_config(str(config_file))
        assert cfg.points["offset_from_drill"].shift_x_mm == 5.0

    def test_rule_anchor_point_to_board_origin_is_fatal(self, tmp_path):
        """Same wall as xy — a board origin has no pads to look up spoke.pad
        from."""
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"board_zero": {"anchor_origin": "drill"}},
            rules=[{"net": "GND", "anchor_point": "board_zero", "spokes": []}]))
        with pytest.raises(ValidationError, match="has no footprint to anchor on"):
            load_config(str(config_file))

    def test_clone_placement_anchor_point_to_board_origin_is_fine(self, tmp_path):
        """ClonePlacement only ever needs a coordinate."""
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"board_zero": {"anchor_origin": "drill"}},
            cells={"one_role": {"components": [{"role": "THE_ROLE"}]}},
            clone_placements=[{"cluster": "cp1", "cell": "one_role",
                               "anchor_point": "board_zero"}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.clone_placements[0].anchor_point == "board_zero"


class TestAnchorPointOnConsumers:
    def test_clone_placement_anchor_point_loads(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"fpga_center": {"anchor_role": "FPGA"}},
            cells={"one_role": {"components": [{"role": "THE_ROLE"}]}},
            clone_placements=[{"cluster": "cp1", "cell": "one_role",
                               "anchor_point": "fpga_center", "rotation_deg": 0.0}]))
        cfg, _ = load_config(str(config_file))

        assert cfg.clone_placements[0].anchor_point == "fpga_center"

    def test_clone_placement_anchor_point_with_shifted_point_is_fine(self, tmp_path):
        """ClonePlacement only ever needs a coordinate — a shifted point is OK."""
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"shifted": {"anchor_role": "FPGA", "shift_x_mm": 5.0}},
            cells={"one_role": {"components": [{"role": "THE_ROLE"}]}},
            clone_placements=[{"cluster": "cp1", "cell": "one_role",
                               "anchor_point": "shifted"}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.clone_placements[0].anchor_point == "shifted"

    def test_clone_placement_anchor_point_with_anchor_ref_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"fpga_center": {"anchor_role": "FPGA"}},
            cells={"one_role": {"components": [{"role": "THE_ROLE"}]}},
            clone_placements=[{"cluster": "cp1", "cell": "one_role",
                               "anchor_ref": "U5", "anchor_point": "fpga_center"}]))
        with pytest.raises(ValidationError, match="anchor_point together with anchor_ref/anchor_role"):
            load_config(str(config_file))

    def test_clone_placement_anchor_point_with_anchor_pad_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"fpga_center": {"anchor_role": "FPGA"}},
            cells={"one_role": {"components": [{"role": "THE_ROLE"}]}},
            clone_placements=[{"cluster": "cp1", "cell": "one_role",
                               "anchor_pad": "1", "anchor_point": "fpga_center"}]))
        with pytest.raises(ValidationError, match="anchor_point together with anchor_pad"):
            load_config(str(config_file))

    def test_rule_anchor_point_loads_with_footprint_eligible_point(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"fpga_center": {"anchor_role": "FPGA"}},
            rules=[{"net": "GND", "anchor_point": "fpga_center", "spokes": []}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.rules[0].anchor_point == "fpga_center"

    def test_rule_anchor_point_with_shifted_point_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"shifted": {"anchor_role": "FPGA", "shift_x_mm": 5.0}},
            rules=[{"net": "GND", "anchor_point": "shifted", "spokes": []}]))
        with pytest.raises(ValidationError, match="has no footprint to anchor on"):
            load_config(str(config_file))

    def test_rule_anchor_point_with_xy_literal_point_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"literal": {"xy": [1.0, 2.0]}},
            rules=[{"net": "GND", "anchor_point": "literal", "spokes": []}]))
        with pytest.raises(ValidationError, match="has no footprint to anchor on"):
            load_config(str(config_file))

    def test_rule_anchor_point_through_chain_to_shifted_point_is_fatal(self, tmp_path):
        """Footprint-eligibility walks the WHOLE anchor_point chain, not just
        the immediately-referenced point."""
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"base": {"anchor_role": "FPGA", "shift_x_mm": 5.0},
                    "alias": {"anchor_point": "base"}},
            rules=[{"net": "GND", "anchor_point": "alias", "spokes": []}]))
        with pytest.raises(ValidationError, match="has no footprint to anchor on"):
            load_config(str(config_file))

    def test_rule_anchor_point_through_chain_of_shift_free_points_is_fine(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"base": {"anchor_role": "FPGA"},
                    "alias": {"anchor_point": "base"}},
            rules=[{"net": "GND", "anchor_point": "alias", "spokes": []}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.rules[0].anchor_point == "alias"

    def test_rule_anchor_point_with_anchor_ref_together_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"fpga_center": {"anchor_role": "FPGA"}},
            rules=[{"net": "GND", "anchor_ref": "U5",
                    "anchor_point": "fpga_center", "spokes": []}]))
        with pytest.raises(ValidationError, match="anchor_point together with anchor_ref/anchor_role"):
            load_config(str(config_file))

    def test_thermal_via_array_anchor_point_loads(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"fpga_center": {"anchor_role": "FPGA"}},
            thermal_via_arrays=[{"name": "fpga_thermal", "anchor_point": "fpga_center",
                                 "pad": "145"}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.thermal_via_arrays[0].anchor_point == "fpga_center"

    def test_thermal_via_array_anchor_point_with_shifted_point_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"shifted": {"anchor_role": "FPGA", "shift_x_mm": 5.0}},
            thermal_via_arrays=[{"name": "fpga_thermal", "anchor_point": "shifted",
                                 "pad": "145"}]))
        with pytest.raises(ValidationError, match="has no footprint to anchor on"):
            load_config(str(config_file))


class TestAnchorPointCrossReference:
    def test_unknown_anchor_point_name_is_fatal(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            cells={"one_role": {"components": [{"role": "THE_ROLE"}]}},
            clone_placements=[{"cluster": "cp1", "cell": "one_role",
                               "anchor_point": "does_not_exist"}]))
        with pytest.raises(ValidationError, match="not found in points"):
            load_config(str(config_file))

    def test_unknown_anchor_point_name_suggests_close_match(self, tmp_path):
        config_file = _write(tmp_path, "point.sexp", _cfg(
            points={"fpga_center": {"anchor_role": "FPGA"}},
            cells={"one_role": {"components": [{"role": "THE_ROLE"}]}},
            clone_placements=[{"cluster": "cp1", "cell": "one_role",
                               "anchor_point": "fpga_centre"}]))
        with pytest.raises(ValidationError, match="did you mean 'fpga_center'"):
            load_config(str(config_file))


def test_duplicate_point_key_across_includes_is_fatal(tmp_path):
    _write(tmp_path, "a.sexp", {"points": {"dup": {"anchor_role": "FPGA"}}})
    _write(tmp_path, "b.sexp", {"points": {"dup": {"anchor_role": "MCU"}}})

    root = _write(tmp_path, "root.sexp", {"include": ["a.sexp", "b.sexp"]})

    with pytest.raises(ValidationError, match="duplicate"):
        load_config(str(root))
