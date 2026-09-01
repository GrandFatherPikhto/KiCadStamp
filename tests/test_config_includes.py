#!/usr/bin/env python3
"""Tests for kicadstamp/config/includes.py — generic `include:` for splitting
a profile config into subsystem files (extract_profiles + clone_placements +
rules + cells together, unlike per-section *_file keys). s-expr fixtures since
2026-08-28 (core_yaml_removal)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import load_config
from kicadstamp.config.includes import walk_include_tree
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError

MINIMAL = {"cells": {"one_role": {"components": [
    {"role": "THE_ROLE", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
     "angle_deg": 0.0}]}}}


def _write(tmp_path, name, data) -> Path:
    p = tmp_path / name
    p.write_text(dict_to_sexp(data), encoding="utf-8")
    return p


def test_include_merges_clone_placements_and_rules(tmp_path):
    _write(tmp_path, "sub.sexp", {**MINIMAL, "clone_placements": [
        {"cluster": "from_sub", "cell": "one_role", "xy": [1.0, 2.0]}]})

    root = _write(tmp_path, "root.sexp", {
        "include": ["sub.sexp"],
        "clone_placements": [
            {"cluster": "from_root", "cell": "one_role", "xy": [0.0, 0.0]}]})

    cfg, _ = load_config(str(root))
    names = {cp.cluster for cp in cfg.clone_placements}
    assert names == {"from_root", "from_sub"}
    assert "one_role" in cfg.cells


def test_include_merges_thermal_via_arrays(tmp_path):
    """2026-08-02: thermal_via_arrays: generalized from a single always-root
    field to a real list section — must now split across include: files the
    same way rules:/clone_placements: already do (a second IC needing
    thermal vias, e.g. AD9707 per-channel, shouldn't have to crowd into the
    root config just because the first one — FPGA — historically did)."""
    _write(tmp_path, "sub.sexp", {"thermal_via_arrays": [
        {"name": "from_sub", "anchor_ref": "U9", "pad": "1"}]})

    root = _write(tmp_path, "root.sexp", {
        "include": ["sub.sexp"],
        "thermal_via_arrays": [
            {"name": "from_root", "anchor_ref": "U1", "pad": "1"}]})

    cfg, _ = load_config(str(root))
    names = {tva.name for tva in cfg.thermal_via_arrays}
    assert names == {"from_root", "from_sub"}


def test_include_merges_coordinate_placements(tmp_path):
    _write(tmp_path, "sub.sexp", {"coordinate_placements": [
        {"cluster": "FPGA_PERIPH", "role": "R18", "x_mm": 1.0, "y_mm": 2.0,
         "rotation_deg": 0.0}]})

    root = _write(tmp_path, "root.sexp", {
        "include": ["sub.sexp"],
        "coordinate_placements": [
            {"cluster": "FPGA_PERIPH", "role": "R19", "x_mm": 3.0, "y_mm": 4.0,
             "rotation_deg": 0.0}]})

    cfg, _ = load_config(str(root))
    roles = {cp.role for cp in cfg.coordinate_placements}
    assert roles == {"R18", "R19"}


def test_duplicate_coordinate_placement_name_across_includes_is_fatal(tmp_path):
    """Two entries deriving the SAME default name (cluster/role) from
    different files must collide fatally, same as an explicit duplicate
    name would — the default-name path isn't a free pass around the
    uniqueness requirement."""
    _write(tmp_path, "sub.sexp", {"coordinate_placements": [
        {"cluster": "FPGA_PERIPH", "role": "R18", "x_mm": 1.0, "y_mm": 2.0,
         "rotation_deg": 0.0}]})

    root = _write(tmp_path, "root.sexp", {
        "include": ["sub.sexp"],
        "coordinate_placements": [
            {"cluster": "FPGA_PERIPH", "role": "R18", "x_mm": 3.0, "y_mm": 4.0,
             "rotation_deg": 0.0}]})

    with pytest.raises(ValidationError, match="duplicate name"):
        load_config(str(root))


def test_duplicate_thermal_via_array_name_across_includes_is_fatal(tmp_path):
    _write(tmp_path, "sub.sexp", {"thermal_via_arrays": [
        {"name": "dup", "anchor_ref": "U9", "pad": "1"}]})

    root = _write(tmp_path, "root.sexp", {
        "include": ["sub.sexp"],
        "thermal_via_arrays": [
            {"name": "dup", "anchor_ref": "U1", "pad": "1"}]})

    with pytest.raises(ValidationError, match="duplicate name"):
        load_config(str(root))


def test_include_merges_cells_across_multiple_files(tmp_path):
    # cells_file:/cell_files: (a separate, older mechanism for external Cell
    # files) were folded into include: 2026-08-02 — an external Cell file is
    # now just another include:'d file, wrapped in its own cells: key, same
    # as this one.
    _write(tmp_path, "sub.sexp", {"cells": {"from_include": {"components": [
        {"role": "R1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0}]}}})
    _write(tmp_path, "ext_templates.sexp", {"cells": {
        "from_other_include": {"components": [
            {"role": "R2", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "angle_deg": 0.0}]}}})

    root = _write(tmp_path, "root.sexp", {
        "include": ["sub.sexp", "ext_templates.sexp"]})

    cfg, _ = load_config(str(root))
    assert set(cfg.cells.keys()) == {"from_include", "from_other_include"}


def test_duplicate_template_key_across_includes_is_fatal(tmp_path):
    _write(tmp_path, "a.sexp", {"cells": {"dup": {"components": [
        {"role": "R1"}]}}})
    _write(tmp_path, "b.sexp", {"cells": {"dup": {"components": [
        {"role": "R2"}]}}})

    root = _write(tmp_path, "root.sexp", {"include": ["a.sexp", "b.sexp"]})

    with pytest.raises(ValidationError, match="duplicate"):
        load_config(str(root))


def test_unsupported_key_in_included_file_is_fatal(tmp_path):
    """layer:/thermal_via_array:/schematic_dir:/etc. inside an included file
    have no defined multi-file merge rule — previously silently computed
    then dropped by the caller (only _LIST_SECTIONS/_DICT_SECTIONS are
    pulled up), a real bug hit live on boards/3ch-awg-tia (layer: in
    rules/fpga_spokes.yaml, thermal_via_array: in fpga_thermal_vias.yaml)."""
    _write(tmp_path, "sub.sexp", {"layer": "B.Cu", "chains": []})

    root = _write(tmp_path, "root.sexp", {"include": ["sub.sexp"]})

    with pytest.raises(ValidationError, match="not supported inside an included file"):
        load_config(str(root))


def test_same_key_is_fine_at_the_root_file_itself(tmp_path):
    """The same key (layer:) IS supported when set directly on the root
    config file (not inside an included file) — only the included-file case
    is fatal, this must keep working exactly as before."""
    _write(tmp_path, "sub.sexp", MINIMAL)

    root = _write(tmp_path, "root.sexp", {
        "layer": "B.Cu", "include": ["sub.sexp"]})

    cfg, _ = load_config(str(root))
    assert cfg.layer == "B.Cu"


def test_disabled_include_is_skipped_before_existence_check(tmp_path):
    root = _write(tmp_path, "root.sexp", {
        "include": [{"path": "does_not_exist.sexp", "enabled": False}],
        **MINIMAL,
    })

    cfg, _ = load_config(str(root))
    assert cfg.clone_placements == []


def test_cycle_is_fatal(tmp_path):
    _write(tmp_path, "a.sexp", {"include": ["b.sexp"]})
    _write(tmp_path, "b.sexp", {"include": ["a.sexp"]})

    root = _write(tmp_path, "root.sexp", {"include": ["a.sexp"]})

    with pytest.raises(ValidationError, match="cycle detected"):
        load_config(str(root))


def test_diamond_reinclude_is_deduplicated_not_fatal(tmp_path):
    """d.sexp is included from two unrelated branches (b and c) — a diamond,
    not a cycle. Must load cleanly, with d's cells: merged exactly once (not
    duplicated, and not fatal on a false "duplicate key" against itself)."""
    _write(tmp_path, "d.sexp", MINIMAL)
    _write(tmp_path, "b.sexp", {"include": ["d.sexp"]})
    _write(tmp_path, "c.sexp", {"include": ["d.sexp"]})

    root = _write(tmp_path, "root.sexp", {"include": ["b.sexp", "c.sexp"]})

    cfg, _ = load_config(str(root))
    assert list(cfg.cells.keys()) == ["one_role"]


def test_dict_section_used_as_list_is_fatal(tmp_path):
    """Real mistake hit live: extract_profiles: (a mapping) accidentally
    renamed to clone_placements: (a list section). NOTE: in s-expr this
    malformed shape fails at PARSE time (the parser expects
    (clone_placement ...) records under clone_placements:), with the s-expr
    "unknown key in a record" fatal rather than resolve_includes' later
    "must be a list" — same protection, earlier/different message."""
    sub = tmp_path / "sub.sexp"
    sub.write_text(
        "(kicadstamp-config\n"
        "  (clone_placements\n"
        "    (some_profile\n"
        '      (params (PWR_IN "+5V")))))\n',
        encoding="utf-8")

    root = _write(tmp_path, "root.sexp", {"include": ["sub.sexp"]})

    with pytest.raises(ValidationError):
        load_config(str(root))


def test_nested_include_is_merged(tmp_path):
    _write(tmp_path, "c.sexp", {**MINIMAL, "clone_placements": [
        {"cluster": "from_c", "cell": "one_role", "xy": [0.0, 0.0]}]})
    _write(tmp_path, "b.sexp", {"include": ["c.sexp"]})

    root = _write(tmp_path, "root.sexp", {"include": ["b.sexp"]})

    cfg, _ = load_config(str(root))
    assert [cp.cluster for cp in cfg.clone_placements] == ["from_c"]


# ── walk_include_tree() — structure-preserving, GUI Config tree (2026-08-03) ──

def test_walk_single_file_no_includes(tmp_path):
    root = _write(tmp_path, "root.sexp", MINIMAL)

    node = walk_include_tree(str(root))

    assert node.path == root.resolve()
    assert list(node.sections["cells"].keys()) == ["one_role"]
    assert node.sections["chains"] == []
    assert node.children == []


def test_walk_does_not_merge_child_content_into_parent(tmp_path):
    _write(tmp_path, "sub.sexp", MINIMAL)
    root = _write(tmp_path, "root.sexp", {"include": ["sub.sexp"]})

    node = walk_include_tree(str(root))

    assert node.sections["cells"] == {}  # root itself declares nothing
    assert len(node.children) == 1
    child = node.children[0]
    assert child.path == (tmp_path / "sub.sexp").resolve()
    assert list(child.sections["cells"].keys()) == ["one_role"]


def test_walk_recurses_into_nested_includes(tmp_path):
    _write(tmp_path, "c.sexp", MINIMAL)
    _write(tmp_path, "b.sexp", {"include": ["c.sexp"]})
    root = _write(tmp_path, "root.sexp", {"include": ["b.sexp"]})

    node = walk_include_tree(str(root))

    assert len(node.children) == 1
    b = node.children[0]
    assert b.path == (tmp_path / "b.sexp").resolve()
    assert len(b.children) == 1
    c = b.children[0]
    assert c.path == (tmp_path / "c.sexp").resolve()
    assert list(c.sections["cells"].keys()) == ["one_role"]


def test_walk_cycle_is_fatal(tmp_path):
    _write(tmp_path, "a.sexp", {"include": ["b.sexp"]})
    _write(tmp_path, "b.sexp", {"include": ["a.sexp"]})

    root = _write(tmp_path, "root.sexp", {"include": ["a.sexp"]})

    with pytest.raises(ValidationError, match="cycle detected"):
        walk_include_tree(str(root))


def test_walk_diamond_is_not_deduped_shown_twice(tmp_path):
    """Unlike resolve_includes(), walk_include_tree() never merges — a file
    reached from two branches is walked and shown independently both times,
    no dedup needed since there's nothing to protect from a false collision."""
    _write(tmp_path, "d.sexp", MINIMAL)
    _write(tmp_path, "b.sexp", {"include": ["d.sexp"]})
    _write(tmp_path, "c.sexp", {"include": ["d.sexp"]})

    root = _write(tmp_path, "root.sexp", {"include": ["b.sexp", "c.sexp"]})

    node = walk_include_tree(str(root))

    assert len(node.children) == 2
    b, c = node.children
    assert b.children[0].path == c.children[0].path == (tmp_path / "d.sexp").resolve()
    assert b.children[0] is not c.children[0]  # two independent walks, not shared/cached


def test_walk_disabled_include_yields_no_child(tmp_path):
    root = _write(tmp_path, "root.sexp", {
        "include": [{"path": "does_not_exist.sexp", "enabled": False}]})

    node = walk_include_tree(str(root))

    assert node.children == []
