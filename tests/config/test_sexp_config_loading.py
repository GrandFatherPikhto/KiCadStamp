# tests/test_sexp_config_loading.py
"""Loading tests for the .sexp config format (and .json, the other supported
format): a .sexp profile (and a .sexp/.json include: graph) loads into the
expected Config, and broken s-expr files surface as ValidationError — never
a crash. YAML was removed from the config graph 2026-08-28 (core_yaml_removal),
so a .yaml root is now a fatal, not the default."""
from pathlib import Path

import pytest

from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError


MINIMAL_DATA = {
    "layer": "B.Cu",
    "place_components": False,
    "clone_placements": [
        {"name": "CH0", "cluster": "CH0", "cell": "dac_buf", "xy": [1.2, -1.5]},
    ],
    "cells": {
        "dac_buf": {"layer": "B.Cu", "components": [
            {"role": "DAC_BUF", "offset_along_mm": 1.0, "offset_across_mm": 0.0,
             "angle_deg": 0.0}]},
        "fpga_pwr_bank": {},
    },
}

# The s-expr the WRITER emits for MINIMAL_DATA — so the reader below is exercised
# against real writer output, not a hand-copy that could drift from the grammar.
MINIMAL_SEXP = dict_to_sexp(MINIMAL_DATA, format_number=2)

MINIMAL_YAML = """\
layer: B.Cu
place_components: false
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


def test_sexp_profile_loads(tmp_path):
    """load_config() on a .sexp file produces the expected Config — the raw
    dict the format expresses goes through the same downstream (validators,
    dataclasses). (The YAML-equivalence half of this test was removed with
    core_yaml_removal 2026-08-28 — YAML is no longer a config format.)"""
    sexp_path = _write(tmp_path, "config.sexp", MINIMAL_SEXP)

    cfg_sexp, _ = load_config(str(sexp_path))

    assert cfg_sexp.layer == "B.Cu"
    assert cfg_sexp.place_components is False
    assert [c.cluster for c in cfg_sexp.clone_placements] == ["CH0"]
    assert cfg_sexp.clone_placements[0].cell == "dac_buf"
    assert cfg_sexp.clone_placements[0].xy == (1.2, -1.5)
    assert cfg_sexp.cells["dac_buf"].layer == "B.Cu"
    assert cfg_sexp.cells["dac_buf"].components[0].role == "DAC_BUF"


def test_sexp_include_graph(tmp_path):
    """An include: graph written in .sexp resolves the same way as YAML: the
    root includes sub.sexp and both load into one merged Config."""
    # У3.5 К3, row 7: an UNNAMED record in EACH file makes the 2->3 converter
    # mint the SAME name in both, which the include merge fatals on; a format-3
    # record needs a name, so the source gives one.
    _write(tmp_path, "sub.sexp", dict_to_sexp({
        "clone_placements": [
            {"name": "SUB_CL", "cluster": "SUB_CL", "cell": "fpga_pwr_bank",
             "xy": [1.2, 2.0]},
        ],
    }, format_number=2))
    _write(tmp_path, "root.sexp", dict_to_sexp({
        "layer": "B.Cu",
        "cells": {"fpga_pwr_bank": {}},
        "clone_placements": [
            {"name": "ROOT_CL", "cluster": "ROOT_CL", "cell": "fpga_pwr_bank",
             "xy": [1.2, -1.5]},
        ],
        "include": ["sub.sexp"],
    }, format_number=2))

    cfg, _ = load_config(str(tmp_path / "root.sexp"))
    # both list sections merged: root's own first, then the include's
    assert [c.cluster for c in cfg.clone_placements] == ["ROOT_CL", "SUB_CL"]


def test_mixed_include_graph_sexp_json(tmp_path):
    """Each file parses by its own extension — a root .sexp may include a
    .json subsystem file (the format is selected per file; YAML was removed
    2026-08-28, so a mixed graph mixes only the two supported formats)."""
    import json
    _write(tmp_path, "sub.json", json.dumps({
        "clone_placements": [{"name": "CH0", "cluster": "CH0", "cell": "dac_buf",
                              "xy": [0.0, 0.0]}],
    }))
    _write(tmp_path, "root.sexp", dict_to_sexp({
        "cells": {"dac_buf": {}},
        "clone_placements": [
            {"name": "ROOT", "cluster": "ROOT", "cell": "dac_buf", "xy": [1.0, 1.0]},
        ],
        "include": ["sub.json"],
    }, format_number=2))

    cfg, _ = load_config(str(tmp_path / "root.sexp"))
    assert [c.cluster for c in cfg.clone_placements] == ["ROOT", "CH0"]


def test_walk_include_tree_reads_sexp(tmp_path):
    """walk_include_tree (the GUI's structure-preserving walk) reads .sexp
    files through the same extension switch."""
    from kicadstamp.config.includes import walk_include_tree

    _write(tmp_path, "sub.sexp", dict_to_sexp({
        "clone_placements": [{"name": "SUB", "cluster": "SUB", "cell": "c",
                              "xy": [0.0, 0.0]}],
    }, format_number=2))
    _write(tmp_path, "root.sexp", dict_to_sexp({
        "clone_placements": [{"name": "ROOT", "cluster": "ROOT", "cell": "c",
                              "xy": [1.0, 1.0]}],
        "include": ["sub.sexp"],
    }, format_number=2))

    tree = walk_include_tree(str(tmp_path / "root.sexp"))
    assert tree.path.name == "root.sexp"
    assert len(tree.children) == 1
    assert tree.children[0].path.name == "sub.sexp"
    # each node carries only its OWN directly-declared sections
    assert [r["cluster"] for r in tree.sections["clone_placements"]] == ["ROOT"]
    assert [r["cluster"] for r in tree.children[0].sections["clone_placements"]] == ["SUB"]


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
    selects the parser by extension too.

    У3.5 Ф1: the known-key set is the PRODUCT's EXTRACT_PROFILE_KNOWN_KEYS, not
    a duplicate list — the reader lifts a format-2 profile to CURRENT_FORMAT,
    which under format 3 stamps `uuid` on the record. A hand-copied subset
    without `uuid` would fatal on a format-3 file ("unknown fields … 'uuid'"),
    making the cell fail for a reason outside its subject (parser selection).
    """
    from kicadstamp.cli_extract import EXTRACT_PROFILE_KNOWN_KEYS, load_profile

    _write(tmp_path, "profiles.sexp", dict_to_sexp({
        "extract_profiles": {
            "dac": {"name": "dac", "output": "cells.yaml", "raw_selection": True},
        },
    }, format_number=2))
    prof = load_profile(str(tmp_path / "profiles.sexp"), "extract_profiles",
                        "dac", root_defaults=["output"],
                        known_keys=EXTRACT_PROFILE_KNOWN_KEYS)
    assert prof["name"] == "dac"
    assert prof["raw_selection"] is True


def test_yaml_config_is_fatal(tmp_path):
    """2026-08-28, core_yaml_removal: a .yaml root is no longer a config
    format — load_config fatals with the convert-to-sexp message."""
    yaml_path = _write(tmp_path, "config.yaml", MINIMAL_YAML)
    with pytest.raises(ValidationError, match="sexp_config_convert.py"):
        load_config(str(yaml_path))


def test_retired_tree_node_kind_is_refused_with_file_tree_and_ref(tmp_path):
    """Д1 (2026-10-10, plan_2026_10_08_remove_spokes): a tree node of a kind
    that no longer exists (`chain` / legacy `rule`) is refused at the ONE
    "text -> dict" point (sexp_format.sexp_to_dict), and the message names the
    FILE, the TREE and the node's REF — never a bare "invalid kind"."""
    p = tmp_path / "root.sexp"
    p.write_text(
        '(kicadstamp-config (version 3)\n'
        '  (trees (tree (name "t") (anchor (origin))\n'
        '    (node (ref "CH") (kind chain)))))\n',
        encoding="utf-8")
    with pytest.raises(ValidationError) as excinfo:
        load_config(str(p))
    msg = str(excinfo.value)
    assert "retired kind 'chain'" in msg
    assert str(p) in msg          # the FILE
    assert "tree 't'" in msg      # the TREE
    assert "'CH'" in msg          # the node's REF


def test_a_valid_tree_node_kind_still_loads(tmp_path):
    """Control for the cell above: a tree whose node kind still exists loads."""
    p = tmp_path / "root.sexp"
    p.write_text(
        '(kicadstamp-config (version 3)\n'
        '  (trees (tree (name "t") (anchor (origin))\n'
        '    (node (ref "U3") (kind external) (xy 1 2)))))\n',
        encoding="utf-8")
    cfg, _ctx = load_config(str(p))
    assert cfg.trees[0].nodes[0].kind == "external"
