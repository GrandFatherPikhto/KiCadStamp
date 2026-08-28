#!/usr/bin/env python3
"""Tests for kicadstamp/config/sheet_templates.py — the `sheet_templates:`
dict section that declares a group of clone_placements/coordinate_placements
once and instantiates it once per reused sheet instance (plan_2026_08_16_
sheet_templates.md, Этап 0).

2026-08-28, core_yaml_removal: fixtures are s-expr via dict_to_sexp. (This
file is also why sexp_format.py's _parse_dict_section applies
_SHEET_TEMPLATE_FIELD_TYPE on the parse side too — a one-element
sheets: [Channel_0] list would otherwise round-trip as a bare string.)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config.sheet_templates import expand_sheet_templates
from kicadstamp.exceptions import ValidationError


def _minimal_cell(name: str = "c") -> dict:
    return {
        "cells": {
            name: {
                "components": [{
                    "role": "R1",
                    "offset_along_mm": 0.0,
                    "offset_across_mm": 0.0,
                    "angle_deg": 0.0,
                }],
            },
        },
    }


def _write(tmp_path, name, data) -> Path:
    p = tmp_path / name
    p.write_text(dict_to_sexp(data), encoding="utf-8")
    return p


# ── len(sheets) >= 2: identity always generated, Cluster tag untouched ───────

def test_multi_sheet_generates_identity_and_substitutes(tmp_path):
    """3 sheets -> 3 coordinate_placements + 3 clone_placements. Explicit
    identity fields are OVERWRITTEN with f"{sheet}_{base}"; clone name:
    (Cluster tag) is preserved as-is; sheet:/anchor_sheet: self and $SHEET in
    params:/nets: are substituted per sheet."""
    root = _write(tmp_path, "root.sexp", {
        **_minimal_cell(),
        "sheet_templates": {
            "channel": {
                "sheets": ["Channel_0", "Channel_1", "Channel_2"],
                "coordinate_placements": [
                    {"cluster": "OP_AMP", "role": "OP_AMP", "name": "op_amp",
                     "sheet": "self", "x_mm": 9.0, "y_mm": 0.0,
                     "rotation_deg": 270.0,
                     "anchor_role": "AD_DAC", "anchor_sheet": "self"},
                ],
                "clone_placements": [
                    {"cluster": "PIF_AVDD", "cell": "c", "sheet": "self",
                     "xy": [2.0, 1.0], "rotation_deg": 90.0,
                     "anchor_role": "AD_DAC", "anchor_pad": "18",
                     "anchor_cluster": "AD_DAC", "anchor_sheet": "self",
                     "params": {"FB_PI_FLT": "/$SHEET/DAC/+3V3_AVDD"},
                     "nets": {"C_IN_BULK": "+3V3",
                              "C_OUT_BULK": "/$SHEET/DAC/+3V3_AVDD"}},
                ],
            },
        },
    })

    cfg, _ = load_config(str(root))

    coord = {cp.name: cp for cp in cfg.coordinate_placements}
    assert set(coord) == {"Channel_0_op_amp", "Channel_1_op_amp", "Channel_2_op_amp"}
    assert coord["Channel_0_op_amp"].sheet == "Channel_0"
    assert coord["Channel_0_op_amp"].anchor_sheet == "Channel_0"
    assert coord["Channel_1_op_amp"].sheet == "Channel_1"
    assert coord["Channel_1_op_amp"].anchor_sheet == "Channel_1"

    clones = {cp.name: cp for cp in cfg.clone_placements}
    assert set(clones) == {"Channel_0_PIF_AVDD", "Channel_1_PIF_AVDD", "Channel_2_PIF_AVDD"}
    # cluster (the Cluster tag) is identical across copies — never touched.
    assert clones["Channel_0_PIF_AVDD"].cluster == "PIF_AVDD"
    assert clones["Channel_1_PIF_AVDD"].cluster == "PIF_AVDD"
    assert clones["Channel_0_PIF_AVDD"].sheet == "Channel_0"
    assert clones["Channel_0_PIF_AVDD"].anchor_sheet == "Channel_0"
    assert clones["Channel_1_PIF_AVDD"].params["FB_PI_FLT"] == "/Channel_1/DAC/+3V3_AVDD"
    assert clones["Channel_2_PIF_AVDD"].nets["C_OUT_BULK"] == "/Channel_2/DAC/+3V3_AVDD"
    assert clones["Channel_0_PIF_AVDD"].nets["C_IN_BULK"] == "+3V3"


# ── len(sheets) == 1: identity literal, no prefix ────────────────────────────

def test_single_sheet_identity_is_literal(tmp_path):
    """1 sheet -> identity taken literally from the template (the byte-identical
    regression contract: CH0_PIF_AVDD must survive untouched)."""
    root = _write(tmp_path, "root.sexp", {
        **_minimal_cell(),
        "sheet_templates": {
            "channel": {
                "sheets": ["Channel_0"],
                "clone_placements": [
                    {"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD", "cell": "c",
                     "sheet": "self", "xy": [2.0, 1.0], "anchor_role": "AD_DAC"},
                ],
            },
        },
    })

    cfg, _ = load_config(str(root))
    assert len(cfg.clone_placements) == 1
    cp = cfg.clone_placements[0]
    assert cp.cluster == "PIF_AVDD"
    assert cp.name == "CH0_PIF_AVDD"
    assert cp.sheet == "Channel_0"


def test_single_sheet_coordinate_default_name(tmp_path):
    """1 sheet, no explicit name -> falls back to the same default the
    non-templated path uses (cluster/role), no prefix, no new default."""
    root = _write(tmp_path, "root.sexp", {
        "sheet_templates": {
            "t": {
                "sheets": ["Channel_0"],
                "coordinate_placements": [
                    {"cluster": "OP_AMP", "role": "OP_AMP",
                     "x_mm": 9.0, "y_mm": 0.0, "rotation_deg": 270.0,
                     "anchor_role": "AD_DAC"},
                ],
            },
        },
    })

    cfg, _ = load_config(str(root))
    assert len(cfg.coordinate_placements) == 1
    cp = cfg.coordinate_placements[0]
    assert cp.name is None  # effective name is the default OP_AMP/OP_AMP
    assert cp.sheet == "Channel_0"  # §1.0: own sheet auto-filled even for a single sheet


def test_sheet_autofill_without_explicit_sheet(tmp_path):
    """§1.0 (anchor dependency tree): the own-identity `sheet` is auto-filled
    from the loop sheet name with NO `sheet:` written in the template — for
    multi-sheet (and, per the plan, for a single sheet alike)."""
    root = _write(tmp_path, "root.sexp", {
        "sheet_templates": {
            "t": {
                "sheets": ["Channel_0", "Channel_1"],
                "coordinate_placements": [
                    {"cluster": "OP_AMP", "role": "OP_AMP", "name": "op_amp",
                     "x_mm": 9.0, "y_mm": 0.0, "rotation_deg": 270.0,
                     "anchor_role": "AD_DAC"},
                ],
            },
        },
    })

    cfg, _ = load_config(str(root))
    by_name = {cp.name: cp for cp in cfg.coordinate_placements}
    assert set(by_name) == {"Channel_0_op_amp", "Channel_1_op_amp"}
    assert by_name["Channel_0_op_amp"].sheet == "Channel_0"
    assert by_name["Channel_1_op_amp"].sheet == "Channel_1"
    # anchor_sheet is NOT auto-filled (only explicit `self` is substituted).
    assert by_name["Channel_0_op_amp"].anchor_sheet is None


# ── FPGA without anchor_sheet stays without ─────────────────────────────────

def test_no_autofill_of_anchor_sheet(tmp_path):
    """anchor_role: FPGA with no anchor_sheet: in the template must NOT gain
    one after expansion (only explicit 'self' is substituted)."""
    root = _write(tmp_path, "root.sexp", {
        "sheet_templates": {
            "t": {
                "sheets": ["Channel_0", "Channel_1"],
                "coordinate_placements": [
                    {"cluster": "FPGA", "role": "FPGA",
                     "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0,
                     "anchor_role": "FPGA"},
                ],
            },
        },
    })

    cfg, _ = load_config(str(root))
    assert len(cfg.coordinate_placements) == 2
    for cp in cfg.coordinate_placements:
        assert cp.anchor_role == "FPGA"
        assert cp.anchor_sheet is None


# ── include: merge (fatal on duplicate template key) ─────────────────────────

def test_include_merges_sheet_templates(tmp_path):
    _write(tmp_path, "sub.sexp", {
        **_minimal_cell(),
        "sheet_templates": {
            "from_sub": {
                "sheets": ["Channel_0"],
                "coordinate_placements": [
                    {"cluster": "OP_AMP", "role": "OP_AMP", "name": "sub_op_amp",
                     "x_mm": 1.0, "y_mm": 0.0, "rotation_deg": 0.0,
                     "anchor_role": "AD_DAC"},
                ],
            },
        },
    })

    root = _write(tmp_path, "root.sexp", {
        "include": ["sub.sexp"],
    })

    cfg, _ = load_config(str(root))
    names = {cp.name for cp in cfg.coordinate_placements}
    assert names == {"sub_op_amp"}


def test_include_duplicate_template_key_is_fatal(tmp_path):
    _write(tmp_path, "sub.sexp", {
        "sheet_templates": {"t": {"sheets": ["Channel_0"]}},
    })

    root = _write(tmp_path, "root.sexp", {
        "include": ["sub.sexp"],
        "sheet_templates": {"t": {"sheets": ["Channel_1"]}},
    })

    with pytest.raises(ValidationError, match="duplicate sheet_templates key"):
        load_config(str(root))


# ── collision after expansion falls into _check_duplicate_names ─────────────

def test_post_expansion_collision_is_fatal(tmp_path):
    """Two single-sheet templates whose literal identities collide on the same
    sheet must fall into the existing coordinate duplicate-name check with a
    readable error (nothing re-invented)."""
    root = _write(tmp_path, "root.sexp", {
        "sheet_templates": {
            "a": {
                "sheets": ["Channel_0"],
                "coordinate_placements": [
                    {"cluster": "OP_AMP", "role": "OP_AMP", "name": "channel0_op_amp",
                     "x_mm": 9.0, "y_mm": 0.0, "rotation_deg": 0.0,
                     "anchor_role": "AD_DAC"},
                ],
            },
            "b": {
                "sheets": ["Channel_0"],
                "coordinate_placements": [
                    {"cluster": "OP_AMP", "role": "OP_AMP", "name": "channel0_op_amp",
                     "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0,
                     "anchor_role": "AD_DAC"},
                ],
            },
        },
    })

    with pytest.raises(ValidationError, match="duplicate name"):
        load_config(str(root))


# ── template shape validation ────────────────────────────────────────────────

def test_template_missing_sheets_is_fatal():
    with pytest.raises(ValidationError, match="non-empty list"):
        expand_sheet_templates({"sheet_templates": {"t": {"clone_placements": []}}})


def test_template_unsupported_section_is_fatal():
    with pytest.raises(ValidationError, match="unsupported"):
        expand_sheet_templates({
            "sheet_templates": {
                "t": {"sheets": ["Channel_0"], "rules": [{"net": "X", "spokes": []}]}
            }
        })


def test_expand_consumes_section():
    data = {"sheet_templates": {"t": {"sheets": ["Channel_0"]}}, "clone_placements": []}
    out = expand_sheet_templates(data)
    assert "sheet_templates" not in out       # consumed in the result
    assert "sheet_templates" in data          # input is NOT mutated (new dict returned)
