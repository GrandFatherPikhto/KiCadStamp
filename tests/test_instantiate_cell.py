#!/usr/bin/env python3
"""Config-level tests for the "Instantiate from Cell…" pure helpers
(2026-09-03, plan techdocs/handoff/deepseek/plan_2026_09_03_instantiate_from_entity.md):
gui/docks/tree_from_selection.py's build_instantiated_entity / selection_cluster /
selected_center_mm / cell_component_roles / missing_cluster_roles — the Qt-free
half of the TreesDock action that adds a NEW group reusing an EXISTING Cell.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from gui.docks.tree_from_selection import (
    build_instantiated_entity,
    cell_component_roles,
    missing_cluster_roles,
    selected_center_mm,
    selection_cluster,
)
from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp

MM_NM = 1_000_000.0  # 1 mm in nanometers (Footprint.position is in nm)


def _fp_nm(x_mm, y_mm):
    return SimpleNamespace(position=SimpleNamespace(
        x=x_mm * MM_NM, y=y_mm * MM_NM))


def _sel(ref, role=None, cluster=None, sheet=None, fp=None):
    return SimpleNamespace(
        ref=ref, role=role, cluster=cluster,
        sheet=sheet or [], fp=fp if fp is not None else _fp_nm(0, 0))


def _cell(*roles):
    return SimpleNamespace(components=[SimpleNamespace(role=r) for r in roles])


class TestBuildInstantiatedEntity:
    def test_shape_references_existing_cell_no_refs(self):
        ent = build_instantiated_entity("c_pif", "PIF_1V2_VCCINT",
                                        "PIF_1V2_VCCINT", "FPGA")
        assert ent == {"name": "PIF_1V2_VCCINT", "cell": "c_pif",
                       "cluster": "PIF_1V2_VCCINT", "sheet": "FPGA"}
        # Deliberately NO role-pinning fields: the new cluster's roles resolve
        # at Apply by (cluster, sheet), not from a (possibly absent) selection.
        assert "refs" not in ent
        assert "by_selection" not in ent

    def test_sheet_optional(self):
        ent = build_instantiated_entity("c_pif", "PIF_1V2_VCCINT",
                                        "PIF_1V2_VCCINT")
        assert ent == {"name": "PIF_1V2_VCCINT", "cell": "c_pif",
                       "cluster": "PIF_1V2_VCCINT"}

    def test_loads_as_entity_and_resolves_in_tree(self, tmp_path):
        """The produced dict is a valid entities: entry, and a hand-written
        trees: placement node referencing it loads end-to-end (the shape the
        dock stages before appending the node)."""
        ent = build_instantiated_entity("c_pif", "PIF_1V2_VCCINT",
                                        "PIF_1V2_VCCINT", "FPGA")
        data = {
            "cells": {},
            "entities": [
                {"name": "pif_p2v5_vcca", "cell": "c_pif",
                 "cluster": "PIF_P2V5_VCCA"},
                ent,
            ],
            "trees": [{
                "name": "fpga",
                "anchor": {"role": "FPGA"},
                "nodes": [
                    {"ref": "pif_p2v5_vcca", "kind": "placement",
                     "xy": [1.0, 2.0]},
                    {"ref": "PIF_1V2_VCCINT", "kind": "placement",
                     "xy": [3.0, 4.0]},
                ],
            }],
        }
        p = tmp_path / "t.sexp"
        p.write_text(dict_to_sexp(data), encoding="utf-8")
        cfg, _ = load_config(str(p))
        names = {e.name for e in cfg.entities}
        assert "PIF_1V2_VCCINT" in names
        ent_loaded = next(e for e in cfg.entities
                          if e.name == "PIF_1V2_VCCINT")
        assert ent_loaded.cell == "c_pif"
        assert ent_loaded.cluster == "PIF_1V2_VCCINT"
        assert ent_loaded.sheet == "FPGA"
        tree = next(t for t in cfg.trees if t.name == "fpga")
        refs = [n.ref for n in tree.nodes]
        assert "PIF_1V2_VCCINT" in refs


class TestSelectionCluster:
    def test_single_cluster(self):
        assert selection_cluster([_sel("R1", cluster="A"),
                                  _sel("C1", cluster="A")]) == "A"

    def test_no_cluster_returns_none(self):
        assert selection_cluster([_sel("R1", cluster=None)]) is None
        assert selection_cluster([]) is None

    def test_several_clusters_raises(self):
        with pytest.raises(ValueError):
            selection_cluster([_sel("R1", cluster="A"),
                               _sel("C1", cluster="B")])


class TestSelectedCenterMm:
    def test_mean_of_positions(self):
        sel = [_sel("R1", cluster="A", fp=_fp_nm(10.0, 20.0)),
               _sel("C1", cluster="A", fp=_fp_nm(20.0, 40.0))]
        cx, cy = selected_center_mm(sel)
        assert abs(cx - 15.0) < 1e-6
        assert abs(cy - 30.0) < 1e-6

    def test_single_is_its_own_coordinate(self):
        cx, cy = selected_center_mm([_sel("R1", fp=_fp_nm(3.0, 4.0))])
        assert abs(cx - 3.0) < 1e-6 and abs(cy - 4.0) < 1e-6

    def test_empty_returns_none(self):
        assert selected_center_mm([]) is None


class TestCellRolesAndFit:
    def test_component_roles(self):
        cell = _cell("R1", "C1", "R2")
        assert cell_component_roles(cell) == {"R1", "C1", "R2"}

    def test_missing_cluster_roles(self):
        cell = _cell("R1", "C1")
        board = [_sel("R1", role="R1", cluster="PIF_1V2_VCCINT")]
        assert missing_cluster_roles(cell, board) == ["C1"]

    def test_all_roles_present_fits(self):
        cell = _cell("R1", "C1")
        board = [_sel("R1", role="R1", cluster="X"),
                 _sel("C1", role="C1", cluster="X")]
        assert missing_cluster_roles(cell, board) == []

    def test_empty_board_reports_all_missing(self):
        cell = _cell("R1", "C1")
        assert missing_cluster_roles(cell, []) == ["C1", "R1"]
