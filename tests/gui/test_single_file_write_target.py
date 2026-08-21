#!/usr/bin/env python3
"""Regression tests for the single-file GUI write target (2026-08-21, plan
flatten_and_single_file_gui): the entity docks no longer ask "which file do I
write to" — every NEW record goes to the project ROOT file, while READING still
sees entries from every included file of the include: graph."""
import yaml

from gui.docks.cell_editor import CellDock
from gui.docks.extract import ExtractDock
from gui.docks.placer import PlacerDock
from gui.docks.points import PointsDock
from gui.docks.rules import RuleDock
from gui.docks.thermal_via import ThermalViaArrayDock


def _write(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _load(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _make_project(tmp_path):
    """A root file including one subsystem file with pre-existing entries."""
    sub = tmp_path / "sub.yaml"
    _write(sub, {
        "cells": {"included_cell": {"components": [], "vias": [], "tracks": [], "layer": "F.Cu"}},
        "points": {"included_point": {"xy": [0.0, 0.0]}},
    })
    root = tmp_path / "root.yaml"
    _write(root, {"include": ["sub.yaml"]})
    return root, sub


class TestWriteTargetsFollowRoot:
    def test_all_docks_target_the_root_file(self, main_window, tmp_path):
        root, _sub = _make_project(tmp_path)

        thermal = ThermalViaArrayDock(main_window)
        thermal.set_root_path(root)
        assert thermal._path == root

        points = PointsDock(main_window)
        points.set_root_path(root)
        assert points._path == root

        cells = CellDock(main_window)
        cells.set_root_path(root)
        assert cells._path == root

        placer = PlacerDock(main_window)
        placer.set_root_path(root)
        assert placer._cells_path == root
        assert placer._placer_path == root

        rules = RuleDock(main_window)
        rules.set_root_path(root)
        assert rules._path == root

        extract = ExtractDock(main_window)
        extract.set_root_path(root)
        assert extract._target_path == root
        assert extract._profile_path == root
        assert extract._placer_path == root


class TestNewRecordsGoToRootNotTheInclude:
    def test_points_save_lands_in_root(self, main_window, tmp_path):
        root, sub = _make_project(tmp_path)
        dock = PointsDock(main_window)
        dock.set_root_path(root)

        dock.name_edit.setText("new_point")
        dock.origin_mode_combo.setCurrentIndex(0)  # Absolute XY
        dock.x_edit.setText("1")
        dock.y_edit.setText("2")
        dock._on_save()

        assert "new_point" in _load(root)["points"]
        assert "new_point" not in (_load(sub).get("points") or {})

    def test_thermal_via_save_lands_in_root(self, main_window, tmp_path):
        root, sub = _make_project(tmp_path)
        dock = ThermalViaArrayDock(main_window)
        dock.set_root_path(root)

        dock.name_edit.setText("new_thermal")
        dock.pad_edit.setText("1")
        dock.origin_widget.load(mode="anchor", ref="U1")
        dock._on_save()

        names = [e["name"] for e in _load(root).get("thermal_via_arrays", [])]
        assert names == ["new_thermal"]
        assert "thermal_via_arrays" not in _load(sub)

    def test_cell_save_lands_in_root(self, main_window, tmp_path):
        root, sub = _make_project(tmp_path)
        dock = CellDock(main_window)
        dock.set_root_path(root)

        dock.new_cell(root)
        dock.name_edit.setText("new_cell")
        dock.comp_role_edit.setCurrentText("A")
        dock._on_add_component()
        dock._on_save()

        assert "new_cell" in _load(root)["cells"]
        assert "new_cell" not in (_load(sub).get("cells") or {})


class TestReadingStillSeesIncludedFiles:
    def test_autocompletes_cover_the_whole_include_graph(self, main_window, tmp_path):
        root, _sub = _make_project(tmp_path)

        placer = PlacerDock(main_window)
        placer.set_root_path(root)
        cell_names = [placer.cell_combo.itemText(i) for i in range(placer.cell_combo.count())]
        assert "included_cell" in cell_names

        points = PointsDock(main_window)
        points.set_root_path(root)
        point_names = [points.point_edit.itemText(i) for i in range(points.point_edit.count())]
        assert "included_point" in point_names

        thermal = ThermalViaArrayDock(main_window)
        thermal.set_root_path(root)
        thermal_point_names = [thermal.point_edit.itemText(i)
                               for i in range(thermal.point_edit.count())]
        assert "included_point" in thermal_point_names
