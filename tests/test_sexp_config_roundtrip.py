# tests/test_sexp_config_roundtrip.py
"""Round-trip fidelity tests for the config dict <-> s-expr converter
(kicadstamp/config/sexp_format.py, the parallel .sexp config format).

The comparison helper _eq() is type-strict, exactly like
tests/test_sexp_roundtrip.py: Python treats 1 == 1.0 and Symbol == str as
True, so a naive equality check would silently pass a test that corrupted an
int into a float or a string into a bare atom. Checking type(a) is type(b)
FIRST is what makes the round-trip tests non-tautological.

Bijectivity contract: dict_to_sexp() omits any field whose value equals THAT
field's dataclass default (design grammar §3.1 — e.g. Config.place_components
defaults True, so a real `false` MUST survive; a default-valued `false`-field
like Rule.retired is legitimately dropped). So round-trips compare against
_strip_defaults(data) — the canonical dict the format is guaranteed to
reproduce. The §3.1 regression cases assert that non-default values
(place_components=False, via_search_n_directions=0) are NOT stripped.
"""
from pathlib import Path

import pytest

from kicadstamp.config.sexp_format import (
    _strip_defaults,
    dict_to_sexp,
    sexp_to_dict,
)
from kicadstamp.exceptions import ValidationError
from kicadstamp.utils.yaml_loader import safe_load

PROFILES_ROOT = Path(__file__).resolve().parents[1] / "profiles"
REAL_PROFILE = PROFILES_ROOT / "3ch-awg-tia-v103" / "3ch-awg-tia.yaml"


def _eq(a, b) -> bool:
    """Type-strict structural comparison (same as test_sexp_roundtrip.py):
    type identity is checked BEFORE any value comparison."""
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return (set(a) == set(b)
                and all(_eq(a[k], b[k]) for k in a))
    if isinstance(a, tuple):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    return a == b


def _roundtrip(data: dict) -> dict:
    """dict -> s-expr -> dict, type-strict equal to the default-stripped
    original (the bijectivity contract)."""
    back = sexp_to_dict(dict_to_sexp(data))
    assert _eq(back, _strip_defaults(data)), (
        "round-trip mismatch\nback:    {back!r}\nstripped:{stripped!r}"
        .format(back=back, stripped=_strip_defaults(data)))
    return back


# ── empty / root scalars ───────────────────────────────────────────────────

def test_empty_config():
    _roundtrip({})


def test_root_scalars():
    _roundtrip({
        "layer": "B.Cu",
        "schematic_dir": "sch",
        "registry_path": "registry",
        "track_registry_path": "tracks",
        "log_file": "logs/x.log",
        "operation_log_dir": "logs/operation",
        "board_name": "3CH-AWG-TIA-v102",
        "root_sheet": "root.kicad_sch",
        "schematic_files": ["a.kicad_sch", "b.kicad_sch"],
    })


def test_bool_and_numeric_scalars():
    _roundtrip({
        "place_components": False,
        "skip_existing_components": True,
        "via_keepout_clearance_mm": 0.3,
        "via_search_step_mm": 0.05,
        "via_search_max_radius_mm": 5.0,
        "via_search_n_directions": 16,
    })


# ── per-field default rule (§3.1 regression cases) ─────────────────────────

def test_non_default_false_flag_survives_roundtrip():
    """place_components defaults True (config/models.py). A real
    place_components: false is a NON-default value and must be written, or the
    read-back (data.get('place_components', True)) silently inverts the flag."""
    back = _roundtrip({"place_components": False})
    assert back["place_components"] is False


def test_non_default_zero_int_survives_roundtrip():
    """via_search_n_directions defaults 8 — a 0 is non-default and must not be
    dropped (second, non-bool example of the per-field rule)."""
    back = _roundtrip({"via_search_n_directions": 0})
    assert back["via_search_n_directions"] == 0
    assert type(back["via_search_n_directions"]) is int


def test_default_values_are_omitted():
    """A value equal to its field default is legitimately dropped (per-field
    rule). layer defaults 'F.Cu' — an explicit F.Cu is not reproduced."""
    back = sexp_to_dict(dict_to_sexp({"layer": "F.Cu"}))
    assert "layer" not in back
    # but a non-default layer IS reproduced
    back2 = sexp_to_dict(dict_to_sexp({"layer": "B.Cu"}))
    assert back2["layer"] == "B.Cu"


# ── all 5 list sections ────────────────────────────────────────────────────

def test_rules_section():
    _roundtrip({
        "rules": [
            {
                "net": "+3V3_VCCIO",
                "anchor_role": "FPGA",
                "name": "vccio",
                "spokes": [
                    {"pad": "17", "cell": "fpga_pwr_bank",
                     "shift_x_mm": 1.2, "shift_y_mm": -1.5,
                     "rotation_deg": 90.0, "cluster": "FPGA_PWR_BANK"},
                    {"pad": "26", "cell": "fpga_pwr_bank",
                     "shift_x_mm": 1.2, "shift_y_mm": -3.5,
                     "rotation_deg": 90.0, "cluster": "FPGA_PWR_BANK"},
                ],
            },
        ],
    })


def test_clone_placements_section():
    _roundtrip({
        "clone_placements": [
            {
                "cluster": "CH0", "cell": "dac_buf", "xy": [0.0, 0.0],
                "rotation_deg": 90.0, "sheet": "Channel_0",
                "anchor_role": "FPGA", "name": "ch0",
                "params": {"CH": "0"},
            },
        ],
    })


def test_thermal_via_arrays_section():
    _roundtrip({
        "thermal_via_arrays": [
            {
                "name": "ad9707",
                "anchor_role": "AD9707",
                "pad": "EP", "net": "GND",
                "rows": 5, "cols": 5,
                "margin_mm": 0.4, "pattern": "grid",
                "drill_mm": 0.25, "diameter_mm": 0.5,
            },
        ],
    })


def test_coordinate_placements_section():
    _roundtrip({
        "coordinate_placements": [
            {
                "cluster": "CH0", "role": "R_FILT",
                "x_mm": 10.0, "y_mm": 20.0, "rotation_deg": 45.0,
            },
        ],
    })


def test_net_traces_section():
    _roundtrip({
        "net_traces": [
            {
                "net": "+3V3", "anchor_role": "FPGA",
                "anchor_sheet": "Channel_0", "anchor_pad": "17",
                "tracks": [
                    {"start_along_mm": 0.0, "start_across_mm": 0.0,
                     "end_along_mm": 1.0, "end_across_mm": 2.0,
                     "width_mm": 0.3, "net": "+3V3"},
                ],
                "vias": [
                    {"offset_along_mm": 1.0, "offset_across_mm": 2.0,
                     "net": "GND", "drill_mm": 0.3, "diameter_mm": 0.6},
                ],
            },
        ],
    })


def test_comment_field_roundtrip_all_sections():
    """comment is a plain optional str field on all 7 top-level entities — the
    schema-aware converter must pick it up automatically (NO sexp_format.py
    changes, handoff_2026_08_27_entity_comment_field.md §3), round-trip a
    non-None comment as a quoted string, and omit the None default (same
    per-field rule as every other optional field)."""
    data = {
        "cells": {
            "dac_buf": {"layer": "B.Cu", "comment": "cell note"},
        },
        "points": {
            "ldo_vin": {"anchor_role": "LDO1", "comment": "point note"},
        },
        "rules": [
            {"net": "+3V3", "anchor_role": "FPGA", "comment": "rule note"},
        ],
        "clone_placements": [
            {"cluster": "CH0", "cell": "dac_buf", "xy": [0.0, 0.0],
             "comment": "clone note"},
        ],
        "coordinate_placements": [
            {"cluster": "CH0", "role": "R_FILT", "x_mm": 1.0, "y_mm": 2.0,
             "rotation_deg": 0.0, "comment": "coord note"},
        ],
        "thermal_via_arrays": [
            {"name": "ad9707", "anchor_role": "AD9707", "comment": "tva note"},
        ],
        "net_traces": [
            {"net": "+3V3", "anchor_role": "FPGA", "comment": "trace note"},
        ],
    }
    back = _roundtrip(data)
    # non-None comments survive verbatim in every section ...
    assert back["cells"]["dac_buf"]["comment"] == "cell note"
    assert back["points"]["ldo_vin"]["comment"] == "point note"
    assert back["rules"][0]["comment"] == "rule note"
    assert back["clone_placements"][0]["comment"] == "clone note"
    assert back["coordinate_placements"][0]["comment"] == "coord note"
    assert back["thermal_via_arrays"][0]["comment"] == "tva note"
    assert back["net_traces"][0]["comment"] == "trace note"


# ── all 5 dict sections ────────────────────────────────────────────────────

def test_cells_section():
    _roundtrip({
        "cells": {
            "dac_buf": {
                "layer": "B.Cu",
                "components": [
                    {"role": "DAC_BUF", "offset_along_mm": 1.0,
                     "offset_across_mm": 0.0, "angle_deg": 0.0,
                     "net_template": "+3V3_AVDD"},
                ],
                "vias": [
                    {"offset_along_mm": 2.0, "offset_across_mm": 1.0,
                     "net": "GND"},
                ],
                "tracks": [
                    {"start_along_mm": 0.0, "start_across_mm": 0.0,
                     "end_along_mm": 3.0, "end_across_mm": 1.0},
                ],
                "anchor_xy": [3.0, 2.0],
            },
        },
    })


def test_points_section():
    _roundtrip({
        "points": {
            "ldo_vin": {
                "anchor_role": "LDO1",
                "shift_x_mm": 1.0, "shift_y_mm": 2.0,
            },
            "board_origin": {
                "xy": [0.0, 0.0],
            },
        },
    })


def test_extract_profiles_section_free_form():
    """extract_profiles has NO dataclass — type-driven fallback: str ->
    quoted, number -> bare, bool -> bare, dict -> pairs, list -> nodes."""
    _roundtrip({
        "extract_profiles": {
            "dac": {
                "name": "dac",
                "output": "cells_3ch.sexp",
                "raw_selection": True,
                "rule_nets": ["+3V3_AVDD", "+1V8"],
                "params": {"ch": "0", "gain_db": 6},
            },
        },
    })


def test_extract_profiles_single_rule_net_round_trips_as_list():
    """2026-08-28 (core_yaml_removal .sexp migration): a ONE-element
    rule_nets list used to round-trip as a bare STRING — _parse_free_field
    collapses a single atom, silently breaking single-rule-net profiles on
    reload (same class as sheet_templates' sheets). Fixed via
    _FREE_DICT_FIELD_TYPE's extract_profiles.rule_nets hint on both the
    serialize and parse sides."""
    _roundtrip({
        "extract_profiles": {
            "dac": {
                "output": "cells.sexp",
                "rule_nets": ["+3V3_VCCIO"],
            },
        },
    })


def test_clone_profiles_section_free_form():
    _roundtrip({
        "clone_profiles": {
            "ch0": {
                "net": "+3V3",
                "pcb": "3CH-AWG-TIA-v102.kicad_pcb",
                "channel": "0",
                "output": "clone_out.yaml",
            },
        },
    })


def test_sheet_templates_section():
    _roundtrip({
        "sheet_templates": {
            "channel": {
                "sheets": ["Channel_0", "Channel_1", "Channel_2"],
                "clone_placements": [
                    {"cluster": "CH{ch}", "cell": "dac_buf",
                     "xy": [0.0, 0.0]},
                ],
                "coordinate_placements": [
                    {"cluster": "CH{ch}", "role": "R_FILT",
                     "x_mm": 5.0, "y_mm": 5.0},
                ],
            },
        },
    })


def test_nested_dict_in_free_form_root_field():
    """Free-form root fields (outside the Config dataclass, e.g. fieldstool's
    fields:/renames:) may hold NESTED dict values — {"R1": {"Role": "X"}}.
    A nested dict serializes as recursive key-value pairs under its key and
    must round-trip unambiguously (fix from handoff_2026_08_27_
    sexp_config_fixes.md §2, which schematic_set_fields/rename_fields need
    against a .sexp root)."""
    _roundtrip({
        "root_sheet": "root.kicad_sch",
        "fields": {"R1": {"Role": "X"}, "C1": {"Value": "100n"}},
        "renames": {"Role": {"old": "A", "new": "B"}},
    })


# ── nested dict fields (pairs) ─────────────────────────────────────────────

def test_params_with_numeric_values():
    """params is dict[str, Any] — values may be numbers/bools, each pair's
    value serialized by its own type (FORK-E)."""
    _roundtrip({
        "clone_placements": [
            {"cluster": "CH0", "cell": "dac_buf", "xy": [0.0, 0.0],
             "params": {"CH": "0", "gain": 6, "bypass": True}},
        ],
    })


def test_nested_dict_fields_nets_net_overrides_refs():
    _roundtrip({
        "clone_placements": [
            {"cluster": "CH0", "cell": "dac_buf", "xy": [0.0, 0.0],
             "nets": {"VIN": "+5V"},
             "net_overrides": {"VIN": "+3V3"},
             "refs": {"C_IN_BULK": "C20"}},
        ],
        "cells": {
            "composite": {
                "clone_placements": [
                    {"name": "nested", "cell": "dac_buf", "xy": [1.0, 1.0],
                     "params": {"CH": "0"},
                     "nets": {"VIN": "+5V"}},
                ],
            },
        },
    })


# ── include ────────────────────────────────────────────────────────────────

def test_include_string_entries():
    _roundtrip({
        "rules": [
            {"net": "+3V3", "spokes": [
                {"pad": "1", "cell": "c1", "shift_x_mm": 0.5}]},
        ],
        "include": ["sub1.sexp", "sub2.sexp"],
    })


def test_include_path_enabled_entries():
    _roundtrip({
        "include": [
            "sub1.sexp",
            {"path": "sub2.sexp", "enabled": False},
            {"path": "sub3.sexp", "enabled": True},
        ],
    })


# ── YAML equivalence on a real profile ─────────────────────────────────────

def test_real_profile_yaml_equivalence():
    """The SAME dict that yaml.safe_load returns must survive dict_to_sexp +
    sexp_to_dict (compared against the default-stripped canonical form — the
    s-expr format legitimately omits explicitly-written default values like
    retired: false / skip: false)."""
    if not REAL_PROFILE.exists():
        pytest.skip(f"real profile not present: {REAL_PROFILE}")
    data = safe_load(REAL_PROFILE.read_text(encoding="utf-8")) or {}
    back = sexp_to_dict(dict_to_sexp(data))
    assert _eq(back, _strip_defaults(data))
    # sanity: the real profile actually exercises all section kinds
    for section in ("rules", "clone_placements", "cells", "points",
                    "thermal_via_arrays", "coordinate_placements",
                    "net_traces", "extract_profiles"):
        assert section in data, f"real profile lost section {section}"


# ── fatal rules (§1.5 / design grammar §5), each with a unique match ───────

def _match(text: str, pattern: str):
    with pytest.raises(ValidationError, match=pattern):
        sexp_to_dict(text)


def test_fatal_invalid_top_level_node():
    _match("(something-else)", r"invalid top-level node")


def test_fatal_unknown_key_in_record():
    _match(
        "(kicadstamp-config (rules (rule (net \"x\") (spokes "
        "(spoke (pad \"1\") (cell \"c\") (shift_x_mm 0.5))) (bogus 1))))",
        r"unknown key")


def test_fatal_unquoted_string_in_string_field():
    # (net +3V3) — bare atom where a quoted string is required
    _match(
        "(kicadstamp-config (rules (rule (net +3V3) (spokes "
        "(spoke (pad \"1\") (cell \"c\") (shift_x_mm 0.5))))))",
        r"expected a quoted string, got a bare atom")


def test_fatal_wrong_atom_count_in_tuple():
    _match(
        "(kicadstamp-config (clone_placements "
        "(clone_placement (cluster \"CH0\") (cell \"dac_buf\") (xy 1.0))))",
        r"expected exactly 2 numbers")


def test_fatal_true_where_number_expected():
    _match(
        "(kicadstamp-config (rules (rule (net \"x\") (spokes "
        "(spoke (pad \"1\") (cell \"c\") (shift_x_mm true))))))",
        r"expected a number, got a bare atom")


def test_fatal_number_where_bool_expected():
    _match(
        "(kicadstamp-config (place_components 1))",
        r"expected true/false, got a number")


def test_fatal_duplicate_name_in_dict_section():
    _match(
        "(kicadstamp-config (cells (cell \"a\" (layer \"F.Cu\")) "
        "(cell \"a\" (layer \"B.Cu\"))))",
        r"duplicate name")


def test_fatal_quoted_string_where_bool_expected():
    _match(
        '(kicadstamp-config (place_components "false"))',
        r"expected true/false, got a quoted string")


def test_fatal_wrong_pair_shape():
    _match(
        "(kicadstamp-config (clone_placements "
        "(clone_placement (cluster \"CH0\") (cell \"dac_buf\") (xy 0.0 0.0) "
        "(params (\"a\" \"b\" \"c\")))))",
        r"expected a key-value pair")
