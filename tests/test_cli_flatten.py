#!/usr/bin/env python3
"""Tests for kicadstamp/flatten.py — the CLI `flatten` consolidation
(see techdocs/handoff/deepseek/plan_2026_08_21_flatten_and_single_file_gui.md):
merge a multi-file include: project into one self-contained YAML file, without
ever deleting the source files."""
import argparse
from pathlib import Path

import yaml

from kicadstamp.cli import cmd_flatten
from kicadstamp.flatten import flatten_config


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _build_project(tmp_path: Path) -> dict:
    """A 3-file include: project: root + components + fpga_spokes, each
    carrying a different mix of sections. Returns the physical paths."""
    root = tmp_path / "root.yaml"
    components = tmp_path / "components.yaml"
    spokes = tmp_path / "fpga_spokes.yaml"

    _write(root,
           "layer: B.Cu\n"
           "include:\n"
           "  - components.yaml\n"
           "  - fpga_spokes.yaml\n"
           "rules:\n"
           "  - net: +3V3\n"
           "    anchor_ref: U1\n"
           "    spokes: []\n")
    _write(components,
           "cells:\n"
           "  cap_pair:\n"
           "    placements: []\n"
           "points:\n"
           "  origin_a:\n"
           "    x_mm: 0.0\n"
           "    y_mm: 0.0\n")
    _write(spokes,
           "clone_placements:\n"
           "  - name: fpga_clone\n"
           "    cell: cap_pair\n"
           "thermal_via_arrays:\n"
           "  - name: fpga_thermal\n"
           "    pad: \"1\"\n")
    return {"root": root, "components": components, "spokes": spokes}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class TestFlattenMerge:
    def test_flatten_merges_all_sections_into_root(self, tmp_path):
        paths = _build_project(tmp_path)
        report = flatten_config(root=str(paths["root"]))
        assert any("Written to" in line for line in report)

        data = _load(paths["root"])
        # Every section from every file is present, the include: key is gone.
        assert data["layer"] == "B.Cu"
        assert data["rules"][0]["net"] == "+3V3"
        assert data["cells"]["cap_pair"]["placements"] == []
        assert data["points"]["origin_a"]["x_mm"] == 0.0
        assert data["clone_placements"][0]["name"] == "fpga_clone"
        assert data["thermal_via_arrays"][0]["name"] == "fpga_thermal"
        assert "include" not in data

    def test_header_comment_is_written(self, tmp_path):
        paths = _build_project(tmp_path)
        flatten_config(root=str(paths["root"]))
        text = paths["root"].read_text(encoding="utf-8")
        assert text.startswith("# flattened by kicadstamp flatten on ")

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
            ["components.yaml", "fpga_spokes.yaml", "root.yaml"]

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
        out = tmp_path / "flat.yaml"
        report = flatten_config(root=str(paths["root"]), output=str(out))
        assert any("Written to" in line for line in report)

        assert paths["root"].read_text(encoding="utf-8") == before
        data = _load(out)
        assert "include" not in data
        assert data["cells"]["cap_pair"]["placements"] == []
        assert data["clone_placements"][0]["name"] == "fpga_clone"

    def test_source_files_untouched_in_both_modes(self, tmp_path):
        paths = _build_project(tmp_path)
        comp_before = paths["components"].read_text(encoding="utf-8")
        spokes_before = paths["spokes"].read_text(encoding="utf-8")

        flatten_config(root=str(paths["root"]), dry_run=True)
        flatten_config(root=str(paths["root"]))
        flatten_config(root=str(paths["root"]),
                       output=str(tmp_path / "another.yaml"))

        assert paths["components"].read_text(encoding="utf-8") == comp_before
        assert paths["spokes"].read_text(encoding="utf-8") == spokes_before


class TestCmdFlatten:
    def test_cmd_flatten_wires_namespace_to_flatten_config(self, tmp_path):
        paths = _build_project(tmp_path)
        out = tmp_path / "flat.yaml"
        args = argparse.Namespace(root=str(paths["root"]), output=str(out),
                                  dry_run=False)
        report = cmd_flatten(args)
        assert any("Written to" in line for line in report)
        assert "include" not in _load(out)
