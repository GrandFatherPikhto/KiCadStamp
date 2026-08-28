#!/usr/bin/env python3
"""Regression tests for the 2026-08-21 review fix: editing an EXISTING entry
that lives in a non-root included file must write the edit back to THAT file,
not duplicate/leave the record in the root (which previously made load_config
fatal on a duplicate dict key, or silently split a list record)."""
from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from gui.docks.cell_editor import CellDock
from gui.docks.net_trace import NetTraceDock
from gui.docks.placer import PlacerDock
from gui.docks.points import PointsDock
from gui.docks.rules import RuleDock
from gui.docks.thermal_via import ThermalViaArrayDock


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


class TestEditingIncludedEntryWritesBackToItsFile:
    def test_cell(self, main_window, tmp_path):
        sub = tmp_path / "sub.sexp"
        _write(sub, {"cells": {"included_cell": {
            "components": [{"role": "A"}], "vias": [], "tracks": [], "layer": "F.Cu"}}})
        root = tmp_path / "root.sexp"
        _write(root, {"include": ["sub.sexp"]})

        dock = CellDock(main_window)
        dock.set_root_path(root)
        dock.load_entry("included_cell", sub)
        dock.layer_combo.setCurrentIndex(1)  # F.Cu -> B.Cu
        dock._on_save()

        assert _load(sub)["cells"]["included_cell"]["layer"] == "B.Cu"
        assert "cells" not in _load(root)
        load_config(str(root))  # must not fatal

    def test_point(self, main_window, tmp_path):
        sub = tmp_path / "sub.sexp"
        _write(sub, {"points": {"included_point": {"xy": [0.0, 0.0]}}})
        root = tmp_path / "root.sexp"
        _write(root, {"include": ["sub.sexp"]})

        dock = PointsDock(main_window)
        dock.set_root_path(root)
        dock.load_entry("included_point")
        dock.x_edit.setText("5")
        dock._on_save()

        assert _load(sub)["points"]["included_point"]["xy"] == [5.0, 0.0]
        assert "points" not in _load(root)
        load_config(str(root))

    def test_rule(self, main_window, tmp_path):
        sub = tmp_path / "sub.sexp"
        _write(sub, {"rules": [{"net": "+3V3", "anchor_role": "FPGA", "spokes": []}]})
        root = tmp_path / "root.sexp"
        _write(root, {"include": ["sub.sexp"]})

        dock = RuleDock(main_window)
        dock.set_root_path(root)
        dock.load_entry({"net": "+3V3", "anchor_role": "FPGA", "spokes": []})
        dock.retired_checkbox.setChecked(True)
        dock._on_save()

        assert _load(sub)["rules"][0]["retired"] is True
        assert "rules" not in _load(root)
        load_config(str(root))

    def test_thermal_via(self, main_window, tmp_path):
        sub = tmp_path / "sub.sexp"
        _write(sub, {"thermal_via_arrays": [{"name": "t1", "pad": "1", "anchor_ref": "U1"}]})
        root = tmp_path / "root.sexp"
        _write(root, {"include": ["sub.sexp"]})

        dock = ThermalViaArrayDock(main_window)
        dock.set_root_path(root)
        dock.load_entry({"name": "t1", "pad": "1", "anchor_ref": "U1"})
        dock.rows_edit.setText("8")
        dock._on_save()

        assert _load(sub)["thermal_via_arrays"][0]["rows"] == 8
        assert "thermal_via_arrays" not in _load(root)
        load_config(str(root))

    def test_net_trace(self, main_window, tmp_path):
        sub = tmp_path / "sub.sexp"
        _write(sub, {"net_traces": [{"net": "DAC_DB0", "anchor_role": "AD_DAC"}]})
        root = tmp_path / "root.sexp"
        _write(root, {"include": ["sub.sexp"]})

        dock = NetTraceDock(main_window)
        dock.set_root_path(root)
        dock.load_entry({"net": "DAC_DB0", "anchor_role": "AD_DAC"})
        dock.retired_checkbox.setChecked(True)
        dock._on_save()

        assert _load(sub)["net_traces"][0]["retired"] is True
        assert "net_traces" not in _load(root)
        load_config(str(root))

    def test_clone_placement(self, main_window, tmp_path):
        sub = tmp_path / "sub.sexp"
        _write(sub, {"clone_placements": [{"cluster": "p1", "cell": "c1"}]})
        root = tmp_path / "root.sexp"
        _write(root, {
            "cells": {"c1": {"components": [], "vias": [], "tracks": [], "layer": "F.Cu"}},
            "include": ["sub.sexp"],
        })

        dock = PlacerDock(main_window)
        dock.set_root_path(root)
        dock.load_placement({"cluster": "p1", "cell": "c1"})
        dock.rotation_edit.setText("45")
        dock._do_save()

        assert _load(sub)["clone_placements"][0]["rotation_deg"] == 45
        assert "clone_placements" not in _load(root)
        load_config(str(root))
