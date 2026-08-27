# tests/test_sexp_config_loading.py
"""Loading tests for the parallel .sexp config format: a .sexp profile (and a
.sexp include: graph) must load into the SAME Config as the equivalent YAML,
and broken s-expr files surface as ValidationError — never a crash.

YAML remains the default format (config/includes.py::_load_config_file
selects the parser by extension); the existing test_config_includes.py/
test_config_models.py keep writing YAML as-is. These are the .sexp-specific
points on top."""
from pathlib import Path

import pytest

from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError


MINIMAL_SEXP = """\
(kicadstamp-config
  (layer "B.Cu")
  (place_components false)
  (rules
    (rule
      (net "+3V3_VCCIO")
      (anchor_role "FPGA")
      (spokes
        (spoke (pad "17") (cell "fpga_pwr_bank")
               (shift_x_mm 1.2) (shift_y_mm -1.5) (rotation_deg 90.0)
               (cluster "FPGA_PWR_BANK")))))
  (cells
    (cell "dac_buf"
      (layer "B.Cu")
      (components
        (component (role "DAC_BUF") (offset_along_mm 1.0)
                   (offset_across_mm 0.0) (angle_deg 0.0))))))
"""

MINIMAL_YAML = """\
layer: B.Cu
place_components: false
rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  spokes:
  - pad: '17'
    cell: fpga_pwr_bank
    shift_x_mm: 1.2
    shift_y_mm: -1.5
    rotation_deg: 90.0
    cluster: FPGA_PWR_BANK
cells:
  dac_buf:
    layer: B.Cu
    components:
    - role: DAC_BUF
      offset_along_mm: 1.0
      offset_across_mm: 0.0
      angle_deg: 0.0
"""


def _write(tmp_path, name, text) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_sexp_profile_loads_into_same_config_as_yaml(tmp_path):
    """load_config() on a .sexp file must produce the same Config as the
    equivalent YAML — the raw dict both formats express is identical, so the
    whole downstream (validators, dataclasses) sees the same data."""
    yaml_path = _write(tmp_path, "config.yaml", MINIMAL_YAML)
    sexp_path = _write(tmp_path, "config.sexp", MINIMAL_SEXP)

    cfg_yaml, _ = load_config(str(yaml_path))
    cfg_sexp, _ = load_config(str(sexp_path))

    assert cfg_sexp.layer == "B.Cu"
    assert cfg_sexp.place_components is False
    assert [r.net for r in cfg_sexp.rules] == ["+3V3_VCCIO"]
    assert cfg_sexp.rules[0].anchor_role == "FPGA"
    assert cfg_sexp.rules[0].spokes[0].pad == "17"
    assert cfg_sexp.rules[0].spokes[0].shift_x_mm == 1.2
    assert cfg_sexp.cells["dac_buf"].layer == "B.Cu"
    assert cfg_sexp.cells["dac_buf"].components[0].role == "DAC_BUF"

    # equality against the YAML-loaded Config: same layer, same rules count,
    # same cell, same spoke geometry
    assert cfg_sexp.layer == cfg_yaml.layer
    assert cfg_sexp.place_components == cfg_yaml.place_components
    assert len(cfg_sexp.rules) == len(cfg_yaml.rules)
    assert len(cfg_sexp.rules[0].spokes) == len(cfg_yaml.rules[0].spokes)
    assert cfg_sexp.cells["dac_buf"] == cfg_yaml.cells["dac_buf"]


def test_sexp_include_graph(tmp_path):
    """An include: graph written in .sexp resolves the same way as YAML: the
    root includes sub.sexp and both load into one merged Config."""
    _write(tmp_path, "sub.sexp", dict_to_sexp({
        "rules": [
            {"net": "+1V2_VCCINT", "anchor_role": "FPGA",
             "spokes": [{"pad": "5", "cell": "fpga_pwr_bank",
                         "shift_x_mm": 1.2, "shift_y_mm": 2.0}]},
        ],
    }))
    _write(tmp_path, "root.sexp", dict_to_sexp({
        "layer": "B.Cu",
        "rules": [
            {"net": "+3V3_VCCIO", "anchor_role": "FPGA",
             "spokes": [{"pad": "17", "cell": "fpga_pwr_bank",
                         "shift_x_mm": 1.2, "shift_y_mm": -1.5}]},
        ],
        "include": ["sub.sexp"],
    }))

    cfg, _ = load_config(str(tmp_path / "root.sexp"))
    # both rules merged: root's own first, then the include's
    assert [r.net for r in cfg.rules] == ["+3V3_VCCIO", "+1V2_VCCINT"]


def test_mixed_include_graph_yaml_sexp(tmp_path):
    """Each file parses by its own extension — a root .sexp may include a
    .yaml subsystem file and vice versa (the format is selected per file)."""
    _write(tmp_path, "sub.yaml", "clone_placements:\n"
                                 "- cluster: CH0\n  cell: dac_buf\n  xy: [0.0, 0.0]\n")
    _write(tmp_path, "root.sexp", dict_to_sexp({
        "clone_placements": [
            {"cluster": "ROOT", "cell": "dac_buf", "xy": [1.0, 1.0]},
        ],
        "include": ["sub.yaml"],
    }))

    cfg, _ = load_config(str(tmp_path / "root.sexp"))
    assert [c.cluster for c in cfg.clone_placements] == ["ROOT", "CH0"]


def test_walk_include_tree_reads_sexp(tmp_path):
    """walk_include_tree (the GUI's structure-preserving walk) reads .sexp
    files through the same extension switch."""
    from kicadstamp.config.includes import walk_include_tree

    _write(tmp_path, "sub.sexp", dict_to_sexp({
        "rules": [{"net": "N", "spokes": [{"pad": "1", "cell": "c"}]}],
    }))
    _write(tmp_path, "root.sexp", dict_to_sexp({
        "rules": [{"net": "M", "spokes": [{"pad": "2", "cell": "c"}]}],
        "include": ["sub.sexp"],
    }))

    tree = walk_include_tree(str(tmp_path / "root.sexp"))
    assert tree.path.name == "root.sexp"
    assert len(tree.children) == 1
    assert tree.children[0].path.name == "sub.sexp"
    # each node carries only its OWN directly-declared sections
    assert [r["net"] for r in tree.sections["rules"]] == ["M"]
    assert [r["net"] for r in tree.children[0].sections["rules"]] == ["N"]


def test_broken_sexp_invalid_top_level_raises_validation_error(tmp_path):
    p = _write(tmp_path, "broken.sexp", "(not-kicadstamp-config)\n")
    with pytest.raises(ValidationError, match="invalid top-level node"):
        load_config(str(p))


def test_broken_sexp_unbalanced_parens_raises_validation_error(tmp_path):
    p = _write(tmp_path, "broken2.sexp", "(kicadstamp-config\n  (layer \"B.Cu\")\n")
    with pytest.raises(ValidationError, match="parse error"):
        load_config(str(p))


def test_broken_sexp_unquoted_string_raises_validation_error(tmp_path):
    p = _write(tmp_path, "broken3.sexp",
               "(kicadstamp-config (layer B.Cu))\n")
    with pytest.raises(ValidationError, match="expected a quoted string"):
        load_config(str(p))


def test_load_profile_reads_sexp_profiles(tmp_path):
    """cli_extract.load_profile — the extract/clone-extract profiles reader —
    selects the parser by extension too."""
    from kicadstamp.cli_extract import load_profile

    _write(tmp_path, "profiles.sexp", dict_to_sexp({
        "extract_profiles": {
            "dac": {"name": "dac", "output": "cells.yaml", "raw_selection": True},
        },
    }))
    prof = load_profile(str(tmp_path / "profiles.sexp"), "extract_profiles",
                        "dac", root_defaults=["output"],
                        known_keys={"name", "output", "raw_selection"})
    assert prof["name"] == "dac"
    assert prof["raw_selection"] is True


def test_yaml_still_default_and_untouched(tmp_path):
    """The existing YAML path is unchanged — safe_load is still used for
    anything that is not .sexp."""
    yaml_path = _write(tmp_path, "config.yaml", MINIMAL_YAML)
    cfg, _ = load_config(str(yaml_path))
    assert cfg.layer == "B.Cu"
    assert cfg.place_components is False
