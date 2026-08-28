# tests/test_comment_field.py
"""comment — optional free-form note field on all 7 top-level config
entities (Cell/Rule/ClonePlacement/CoordinatePlacement/
ThermalViaArrayConfig/NetTrace/Point, handoff_2026_08_27_entity_comment_field.md).
A plain schema field (NOT a syntactic comment): survives the s-expr/YAML
dict round-trip and shows up in the GUI. Here: read through load_config(),
absent -> None, like every other optional field."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp

FULL_DATA = {
    "layer": "B.Cu",
    "cells": {"c1": {"layer": "B.Cu", "comment": "cell note", "components": []}},
    "points": {"p1": {"anchor_role": "LDO1", "comment": "point note"}},
    "rules": [{"net": "+3V3", "anchor_role": "FPGA", "comment": "rule note",
               "spokes": []}],
    "clone_placements": [{"cluster": "CH0", "cell": "c1", "xy": [0.0, 0.0],
                          "comment": "clone note"}],
    "coordinate_placements": [{"cluster": "CH0", "role": "R_FILT",
                               "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0,
                               "comment": "coord note"}],
    "thermal_via_arrays": [{"name": "tva1", "anchor_role": "AD9707",
                            "comment": "tva note"}],
    "net_traces": [{"net": "+3V3", "anchor_role": "FPGA",
                    "comment": "trace note"}],
}


def _strip_comments(value):
    """Same config WITHOUT any comment: — every .comment must be None."""
    if isinstance(value, dict):
        return {k: _strip_comments(v) for k, v in value.items() if k != "comment"}
    if isinstance(value, list):
        return [_strip_comments(x) for x in value]
    return value


NO_COMMENT_DATA = _strip_comments(FULL_DATA)


def _load(data: dict, tmp_path, name="cfg.sexp"):
    p = tmp_path / name
    p.write_text(dict_to_sexp(data), encoding="utf-8")
    cfg, _ = load_config(str(p))
    return cfg


def test_comment_read_for_all_7_entities(tmp_path):
    cfg = _load(FULL_DATA, tmp_path)
    assert cfg.cells["c1"].comment == "cell note"
    assert cfg.points["p1"].comment == "point note"
    assert cfg.rules[0].comment == "rule note"
    assert cfg.clone_placements[0].comment == "clone note"
    assert cfg.coordinate_placements[0].comment == "coord note"
    assert cfg.thermal_via_arrays[0].comment == "tva note"
    assert cfg.net_traces[0].comment == "trace note"


def test_absent_comment_defaults_to_none(tmp_path):
    cfg = _load(NO_COMMENT_DATA, tmp_path)
    assert cfg.cells["c1"].comment is None
    assert cfg.points["p1"].comment is None
    assert cfg.rules[0].comment is None
    assert cfg.clone_placements[0].comment is None
    assert cfg.coordinate_placements[0].comment is None
    assert cfg.thermal_via_arrays[0].comment is None
    assert cfg.net_traces[0].comment is None
