#!/usr/bin/env python3
"""GUI tests for "Instantiate from Cell…" (gui/docks/instantiate_cell_dialog.py,
2026-09-03, plan instantiate_from_entity): the dialog collects Cell/Entity name/
Sheet/Cluster + an opt-in "from selection" mode and validates before the dock
persists (staged) and appends the placement node.

The dialog only reads cfg.cells / cfg.entities, so the tests feed a lightweight
fake Config (a full load_config is exercised by tests/test_instantiate_cell.py).
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtWidgets import QDialog

import gui.docks.instantiate_cell_dialog as icd
from gui.docks.instantiate_cell_dialog import InstantiateCellDialog
from gui.docks.trees_dock import TreesDock
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict


def _cell(*roles):
    return SimpleNamespace(components=[SimpleNamespace(role=r) for r in roles])


def _cfg(cells=None, entities=None):
    return SimpleNamespace(
        cells=dict(cells or {}),
        entities=[SimpleNamespace(name=n) for n in (entities or [])],
    )


def _fp(x_mm, y_mm):
    return SimpleNamespace(position=SimpleNamespace(
        x=x_mm * 1_000_000.0, y=y_mm * 1_000_000.0))


def _sel(ref, role=None, cluster=None, sheet=None, fp=None):
    return SimpleNamespace(ref=ref, role=role, cluster=cluster,
                           sheet=sheet or [], fp=fp if fp is not None else _fp(0, 0))


def _open(main_window, cfg=None, *, selected=(), snapshot=(), cells=("c_pif",)):
    cfg = cfg or _cfg(cells={c: _cell("R1", "C1") for c in cells},
                      entities=["existing_entity"])
    return InstantiateCellDialog(
        main_window, cfg,
        cells=list(cells),
        sheets=["FPGA"],
        clusters=["PIF_1V2_VCCINT", "PIF_P2V5_VCCA"],
        selected=list(selected),
        snapshot=list(snapshot))


def test_manual_mode_is_default(main_window):
    dlg = _open(main_window)
    assert dlg.from_selection_check.isChecked() is False
    assert dlg.x_spin.value() == 0.0
    assert dlg.y_spin.value() == 0.0
    assert dlg.manual_xy() == (0.0, 0.0)
    assert dlg.from_selection() is False


def test_validation_rules(main_window):
    dlg = _open(main_window)
    # blank entity name -> rejected
    assert dlg.validate() is not None
    dlg.name_edit.setText("PIF_1V2_VCCINT")
    # duplicate entity name -> rejected
    dlg.name_edit.setText("existing_entity")
    assert dlg.validate() is not None
    # blank cluster -> rejected
    dlg.name_edit.setText("PIF_1V2_VCCINT")
    dlg.cluster_combo.setCurrentText("")
    assert dlg.validate() is not None
    # unknown cell -> rejected
    dlg.cluster_combo.setCurrentText("PIF_1V2_VCCINT")
    dlg.cell_combo.setCurrentText("no_such_cell")
    assert dlg.validate() is not None
    # fully valid -> accepted
    dlg.cell_combo.setCurrentText("c_pif")
    assert dlg.validate() is None


def test_result_getters(main_window):
    dlg = _open(main_window)
    dlg.cell_combo.setCurrentText("c_pif")
    dlg.name_edit.setText("PIF_1V2_VCCINT")
    dlg.sheet_combo.setCurrentText("FPGA")
    dlg.cluster_combo.setCurrentText("PIF_1V2_VCCINT")
    dlg.x_spin.setValue(3.5)
    dlg.y_spin.setValue(-2.0)
    assert dlg.result_cell() == "c_pif"
    assert dlg.entity_name() == "PIF_1V2_VCCINT"
    assert dlg.sheet() == "FPGA"
    assert dlg.cluster() == "PIF_1V2_VCCINT"
    assert dlg.manual_xy() == (3.5, -2.0)


def test_from_selection_single_cluster_adopts_cluster(main_window):
    dlg = _open(main_window, selected=[_sel("R1", role="R1",
                                            cluster="PIF_1V2_VCCINT")])
    dlg.from_selection_check.setChecked(True)
    assert dlg.from_selection() is True
    assert dlg.cluster() == "PIF_1V2_VCCINT"


def test_from_selection_multiple_clusters_warns_and_unchecks(main_window, monkeypatch):
    warnings = []
    monkeypatch.setattr(icd.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a)
                        or icd.QMessageBox.StandardButton.Ok)
    dlg = _open(main_window, selected=[_sel("R1", cluster="A"),
                                       _sel("C1", cluster="B")])
    dlg.from_selection_check.setChecked(True)
    assert warnings, "several clusters must warn"
    assert dlg.from_selection_check.isChecked() is False


def test_suitability_shows_missing_roles(main_window):
    dlg = _open(main_window,
                snapshot=[_sel("R1", role="R1", cluster="PIF_1V2_VCCINT")])
    dlg.cell_combo.setCurrentText("c_pif")
    dlg.cluster_combo.setCurrentText("PIF_1V2_VCCINT")
    # c_pif has roles R1+C1; the board snapshot only has R1 -> C1 missing.
    assert "C1" in dlg.suit_label.text()


def test_suitability_ok_when_all_roles_present(main_window):
    dlg = _open(main_window,
                snapshot=[_sel("R1", role="R1", cluster="PIF_1V2_VCCINT"),
                          _sel("C1", role="C1", cluster="PIF_1V2_VCCINT")])
    dlg.cell_combo.setCurrentText("c_pif")
    dlg.cluster_combo.setCurrentText("PIF_1V2_VCCINT")
    assert "C1" not in dlg.suit_label.text()
    assert "R1" not in dlg.suit_label.text()


# ── TreesDock flow (staged Entity + placement node + dirty) ─────────────────

def _dock(main_window, tmp_path):
    cfg_dict = {
        "cells": {},
        "entities": [
            {"name": "pif_p2v5_vcca", "cell": "c_pif", "cluster": "PIF_P2V5_VCCA"},
        ],
        "trees": [{
            "name": "fpga", "anchor": {"role": "FPGA"},
            "nodes": [{"ref": "pif_p2v5_vcca", "kind": "placement", "xy": [1.0, 2.0]}],
        }],
    }
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(cfg_dict), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    return dock, root


def test_instantiate_from_cell_stages_entity_and_node(main_window, tmp_path,
                                                      monkeypatch):
    """The dock flow (manual mode): a NEW Entity on the EXISTING Cell is staged
    into entities: and a top-level placement node is appended to the CURRENT
    tree + the dock is marked dirty — all STAGED (nothing on disk until Save)."""
    dock, root = _dock(main_window, tmp_path)
    assert [t.name for t in dock._trees] == ["fpga"]
    tree = dock._trees[0]
    fake = MagicMock()
    fake.exec.return_value = QDialog.DialogCode.Accepted
    fake.result_cell.return_value = "c_pif"
    fake.entity_name.return_value = "PIF_1V2_VCCINT"
    fake.cluster.return_value = "PIF_1V2_VCCINT"
    fake.sheet.return_value = "FPGA"
    fake.from_selection.return_value = False
    fake.manual_xy.return_value = (3.0, 4.0)
    monkeypatch.setattr(icd, "InstantiateCellDialog", lambda *a, **k: fake)

    dock._instantiate_from_cell([])

    assert dock._dirty is True
    assert any(n.ref == "PIF_1V2_VCCINT" and n.kind == "placement"
               for n in tree.nodes)
    data = sexp_to_dict(root.read_text(encoding="utf-8"))
    names = {e.get("name") for e in data.get("entities", [])}
    assert "PIF_1V2_VCCINT" in names
    node = next(n for n in data["trees"][0]["nodes"]
                if n.get("ref") == "PIF_1V2_VCCINT")
    assert list(node["xy"]) == [3.0, 4.0]
    # The new Entity carries no role-pinning: cluster/sheet is how Apply finds it.
    new_ent = next(e for e in data["entities"] if e.get("name") == "PIF_1V2_VCCINT")
    assert new_ent.get("cell") == "c_pif"
    assert new_ent.get("cluster") == "PIF_1V2_VCCINT"
    assert new_ent.get("sheet") == "FPGA"
    assert "refs" not in new_ent


def test_instantiate_from_cell_requires_real_anchor(main_window, tmp_path,
                                                    monkeypatch):
    """Plan §1.5: a tree with an auto/absent anchor must refuse the action in
    ANY positioning mode (soft block with the "Set anchor…" hint) — the dialog
    never opens, no node is added, nothing is staged."""
    import gui.docks.trees_dock as td_mod
    cfg_dict = {
        "cells": {},
        "entities": [
            {"name": "pif_p2v5_vcca", "cell": "c_pif", "cluster": "PIF_P2V5_VCCA"},
        ],
        "trees": [{
            "name": "fpga",  # no anchor key -> is_auto (no resolvable base)
            "nodes": [{"ref": "pif_p2v5_vcca", "kind": "placement", "xy": [1.0, 2.0]}],
        }],
    }
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(cfg_dict), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a)
                        or td_mod.QMessageBox.StandardButton.Ok)
    opened = []
    monkeypatch.setattr(icd, "InstantiateCellDialog",
                        lambda *a, **k: opened.append(True) or None)
    dock._instantiate_from_cell([])
    assert opened == [], "the dialog must not open for an auto-anchored tree"
    assert warnings, "an auto-anchored tree must warn with the Set-anchor hint"
    assert len(dock._trees[0].nodes) == 1   # unchanged
    assert dock._dirty is False
