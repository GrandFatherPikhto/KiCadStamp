#!/usr/bin/env python3
"""Tests for kicadstamp/flatten.py — the CLI `flatten` consolidation
(see techdocs/handoff/deepseek/plan_2026_08_21_flatten_and_single_file_gui.md):
merge a multi-file include: project into one self-contained s-expr file,
without ever deleting the source files (s-expr since 2026-08-28,
core_yaml_removal — flatten writes dict_to_sexp unconditionally)."""
import argparse
from pathlib import Path

from kicadstamp.cli import cmd_flatten
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.flatten import flatten_config


def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _build_project(tmp_path: Path) -> dict:
    """A 3-file include: project: root + components + fpga_spokes, each
    carrying a different mix of sections. Returns the physical paths."""
    root = tmp_path / "root.sexp"
    components = tmp_path / "components.sexp"
    spokes = tmp_path / "fpga_spokes.sexp"

    _write(root, {
        "layer": "B.Cu",
        "include": ["components.sexp", "fpga_spokes.sexp"],
        "rules": [{"net": "+3V3", "anchor_role": "FPGA", "spokes": []}],
    })
    _write(components, {
        "cells": {"cap_pair": {"components": []}},
        "points": {"origin_a": {}},
    })
    _write(spokes, {
        "clone_placements": [{"name": "fpga_clone", "cell": "cap_pair"}],
        "thermal_via_arrays": [{"name": "fpga_thermal", "pad": "1"}],
    })
    return {"root": root, "components": components, "spokes": spokes}


def _load(path: Path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


class TestFlattenMerge:
    def test_flatten_merges_all_sections_into_root(self, tmp_path):
        paths = _build_project(tmp_path)
        report = flatten_config(root=str(paths["root"]))
        assert any("Written to" in line for line in report)

        data = _load(paths["root"])
        # Every section from every file is present, the include: key is gone.
        assert data["layer"] == "B.Cu"
        assert data["rules"][0]["net"] == "+3V3"
        assert "cap_pair" in data["cells"]          # empty cell -> default-stripped {}
        assert "origin_a" in data["points"]         # empty point -> default-stripped {}
        assert data["clone_placements"][0]["name"] == "fpga_clone"
        assert data["thermal_via_arrays"][0]["name"] == "fpga_thermal"
        assert "include" not in data

    def test_empty_sections_are_not_emitted(self, tmp_path):
        paths = _build_project(tmp_path)
        flatten_config(root=str(paths["root"]))
        data = _load(paths["root"])
        # Nothing in this project contributes coordinate_placements:/
        # net_traces:/extract_profiles:/clone_profiles:/sheet_templates: —
        # they must not be written back as empty noise.
        assert "coordinate_placements" not in data
        assert "net_traces" not in data
        assert "extract_profiles" not in data
        assert "clone_profiles" not in data
        assert "sheet_templates" not in data


class TestFlattenDryRun:
    def test_dry_run_writes_nothing(self, tmp_path):
        paths = _build_project(tmp_path)
        before = paths["root"].read_text(encoding="utf-8")
        report = flatten_config(root=str(paths["root"]), dry_run=True)
        assert any("Would write to" in line for line in report)
        # Root untouched (still has include:), no new file appeared.
        assert paths["root"].read_text(encoding="utf-8") == before
        assert sorted(p.name for p in tmp_path.iterdir()) == \
            ["components.sexp", "fpga_spokes.sexp", "root.sexp"]

    def test_dry_run_reports_sections_and_target(self, tmp_path):
        paths = _build_project(tmp_path)
        report = flatten_config(root=str(paths["root"]), dry_run=True)
        joined = "\n".join(report)
        for section in ("rules", "cells", "points",
                        "clone_placements", "thermal_via_arrays"):
            assert section in joined
        assert "Would write to" in joined


class TestFlattenOutputPath:
    def test_output_new_path_does_not_touch_root(self, tmp_path):
        paths = _build_project(tmp_path)
        before = paths["root"].read_text(encoding="utf-8")
        out = tmp_path / "flat.sexp"
        report = flatten_config(root=str(paths["root"]), output=str(out))
        assert any("Written to" in line for line in report)

        assert paths["root"].read_text(encoding="utf-8") == before
        data = _load(out)
        assert "include" not in data
        assert "cap_pair" in data["cells"]
        assert data["clone_placements"][0]["name"] == "fpga_clone"

    def test_source_files_untouched_in_both_modes(self, tmp_path):
        paths = _build_project(tmp_path)
        comp_before = paths["components"].read_text(encoding="utf-8")
        spokes_before = paths["spokes"].read_text(encoding="utf-8")

        flatten_config(root=str(paths["root"]), dry_run=True)
        flatten_config(root=str(paths["root"]))
        flatten_config(root=str(paths["root"]),
                       output=str(tmp_path / "another.sexp"))

        assert paths["components"].read_text(encoding="utf-8") == comp_before
        assert paths["spokes"].read_text(encoding="utf-8") == spokes_before


class TestCmdFlatten:
    def test_cmd_flatten_wires_namespace_to_flatten_config(self, tmp_path):
        paths = _build_project(tmp_path)
        out = tmp_path / "flat.sexp"
        args = argparse.Namespace(root=str(paths["root"]), output=str(out),
                                  dry_run=False)
        report = cmd_flatten(args)
        assert any("Written to" in line for line in report)
        assert "include" not in _load(out)
