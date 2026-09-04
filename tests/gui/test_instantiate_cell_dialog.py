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


def _open(main_window, cfg=None, *, selected=(), snapshot=(), cells=("c_pif",),
          fully_selected=()):
    cfg = cfg or _cfg(cells={c: _cell("R1", "C1") for c in cells},
                      entities=["existing_entity"])
    return InstantiateCellDialog(
        main_window, cfg,
        cells=list(cells),
        sheets=["FPGA"],
        clusters=["PIF_1V2_VCCINT", "PIF_P2V5_VCCA"],
        selected=list(selected),
        snapshot=list(snapshot),
        fully_selected=list(fully_selected))


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
    fake.is_new_cell.return_value = False  # tab 1 (existing Cell)
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


def _to_tab2(dlg):
    dlg.tabs.setCurrentIndex(1)
    return dlg


# ── Tab 2: "Extract new cell from selection" (2026-09-04) ────────────────

def test_tab2_single_fully_selected_autofills_addressing(main_window):
    """Tab 2 with exactly ONE fully-selected cluster autofills the shared
    cluster/sheet addressing from it (the same adoption "_on_from_selection_
    toggled" uses) and prefills the new Cell name from the cluster slug."""
    dlg = _open(main_window, fully_selected=[("PIF_AVDD", "Channel_1")])
    _to_tab2(dlg)
    assert dlg.is_new_cell() is True
    assert dlg.cluster() == "PIF_AVDD"
    assert dlg.sheet() == "Channel_1"
    assert dlg.new_cell_name() == "pif_avdd"
    assert dlg.result_cell() == "pif_avdd"      # single reading point for the dock
    assert dlg.absolute_origin() is False       # zero-slot is the default
    assert dlg.zero_slot_radio.isChecked()


def test_tab2_absolute_radio_and_warning_visibility(main_window):
    """'Absolute' geometry shows the non-fatal notice only when the node's
    'Take from selection' positioning is NOT also used; zero-slot never shows
    it, and it disappears on the existing-cell tab. A cluster-tagged selection
    keeps the "Take from selection" opt-in modal-free."""
    dlg = _open(main_window, fully_selected=[("PIF_AVDD", "Channel_1")],
                selected=[_sel("R1", role="R1", cluster="PIF_AVDD")])
    _to_tab2(dlg)
    assert dlg.geometry_warning_label.isVisibleTo(dlg) is False  # zero-slot default
    dlg.absolute_radio.setChecked(True)
    assert dlg.absolute_origin() is True
    assert dlg.geometry_warning_label.isVisibleTo(dlg) is True   # absolute + manual xy
    dlg.from_selection_check.setChecked(True)
    assert dlg.geometry_warning_label.isVisibleTo(dlg) is False  # paired correctly
    dlg.tabs.setCurrentIndex(0)
    assert dlg.is_new_cell() is False
    assert dlg.geometry_warning_label.isVisibleTo(dlg) is False


def test_tab2_zero_fully_selected_blocks_ok_and_validate(main_window):
    """STRICT (Denis, 2026-09-04): no fully-selected cluster -> the strict
    message (the SAME wording "Extract cluster..." uses) + OK disabled; the
    existing-cell tab 1 stays fully usable (OK re-enabled)."""
    dlg = _open(main_window)  # fully_selected empty
    _to_tab2(dlg)
    assert not dlg._ok_button.isEnabled()
    assert dlg.validate() is not None
    assert "fully selected" in dlg.tab2_status_label.text()
    assert "first." in dlg.tab2_status_label.text()
    dlg.tabs.setCurrentIndex(0)  # tab 1 unaffected
    assert dlg.is_new_cell() is False
    assert dlg._ok_button.isEnabled()


def test_tab2_several_fully_selected_blocks(main_window):
    """More than one fully-selected cluster is ambiguous (a Cell is extracted
    from exactly one) -> blocked, never a silent pick."""
    dlg = _open(main_window, fully_selected=[("PIF_AVDD", "Ch0"),
                                             ("PIF_CLKVDD", "Ch1")])
    _to_tab2(dlg)
    assert not dlg._ok_button.isEnabled()
    assert dlg.validate() is not None
    assert "exactly ONE" in dlg.tab2_status_label.text()


def test_tab2_validate_requires_name_and_blocks_collision(main_window):
    """Tab 2 validate: a blank new-Cell name and a name colliding with an
    existing cfg.cells entry both block; a valid name + entity name passes."""
    dlg = _open(main_window, fully_selected=[("PIF_AVDD", "Channel_1")])
    _to_tab2(dlg)
    dlg.name_edit.setText("PIF_AVDD_ENT")
    assert dlg.validate() is None            # prefilled "pif_avdd" is fresh
    dlg.new_cell_name_edit.setText("c_pif")  # exists in the _open default cfg
    assert dlg.validate() is not None
    assert "already exists" in dlg.validate()
    dlg.new_cell_name_edit.setText("")       # blank
    assert dlg.validate() is not None


def test_tab2_validate_rejects_address_mismatch(main_window):
    """On tab 2 the Entity's addressing must match the fully-selected cluster
    the Cell is extracted from — a silent retype away from it is refused."""
    dlg = _open(main_window, fully_selected=[("PIF_AVDD", "Channel_1")])
    _to_tab2(dlg)
    dlg.name_edit.setText("PIF_AVDD_ENT")
    dlg.cluster_combo.setCurrentText("SOME_OTHER")
    problem = dlg.validate()
    assert problem is not None
    assert "must match" in problem


# ── manual origin override (2026-09-04, plan extract_origin_pad_restore) ──

def _tab2_with_roles(main_window, roles=("R1", "DAC")):
    """Tab 2 with a fully-selected cluster whose selected footprints carry the
    given roles (the source of the manual-origin role combo)."""
    dlg = _open(main_window, fully_selected=[("PIF_AVDD", "Channel_1")],
                selected=[_sel(f"r{i}", role=r, cluster="PIF_AVDD")
                          for i, r in enumerate(roles)])
    _to_tab2(dlg)
    return dlg


def test_tab2_origin_override_unchecked_is_automatic(main_window):
    """Checkbox off -> origin_override() == (None, None): the manual override is
    an OPT-IN — today's automatic zero-slot detection stays the default."""
    dlg = _tab2_with_roles(main_window)
    assert dlg.origin_override() == (None, None)
    assert dlg.origin_override_check.isChecked() is False


def test_tab2_origin_override_returns_chosen_role_and_pad(main_window):
    """Checkbox on + a Role picked (+ optional Pad) -> origin_override() returns
    exactly them (the automatic detection is bypassed)."""
    dlg = _tab2_with_roles(main_window)
    dlg.origin_override_check.setChecked(True)
    assert dlg.origin_widget.isVisibleTo(dlg)
    combo = dlg.origin_widget.anchor_role_edit
    combo.setCurrentText("DAC")
    dlg.origin_widget.anchor_pad_edit.setText("A1")
    assert dlg.origin_override() == ("DAC", "A1")


def test_tab2_origin_override_empty_role_blocks_validate(main_window):
    """Checkbox on + no Ref/Role -> the widget's own build() error surfaces as a
    FATAL through validate() (never a swallowed 'empty = ok'): the dialog must
    not accept an override the picker itself considers invalid."""
    dlg = _tab2_with_roles(main_window, roles=())
    dlg.name_edit.setText("PIF_AVDD_ENT")
    dlg.origin_override_check.setChecked(True)
    problem = dlg.validate()
    assert problem is not None
    assert "Ref or Role" in problem


def test_tab2_origin_override_absolute_mode_never_applies(main_window):
    """In "Absolute" geometry the manual override never applies (it has its own
    origin via selected_center_mm): origin_override() returns (None, None) even
    when the checkbox is checked and a role is picked (plan §3/§1.2)."""
    dlg = _tab2_with_roles(main_window)
    dlg.absolute_radio.setChecked(True)
    dlg.origin_override_check.setChecked(True)
    dlg.origin_widget.anchor_role_edit.setCurrentText("DAC")
    # The widget stays hidden under Absolute (independent of the checkbox)…
    assert dlg.origin_widget.isVisibleTo(dlg) is False
    # …and the getter agrees — it repeats the visibility condition explicitly.
    assert dlg.origin_override() == (None, None)


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


# ── Tab-2 TreesDock flow: extract + stage a brand-new Cell (2026-09-04) ───

def _detect_one_cluster(monkeypatch, cluster="PIF_AVDD", sheet="Channel_1"):
    """Stub the dock's strict detection to report exactly ONE fully-selected
    cluster (the same list fully_selected_clusters would return for a full
    selection)."""
    from gui.docks.reead import ReReadCluster
    monkeypatch.setattr(
        "gui.docks.reead.fully_selected_clusters",
        lambda *a, **k: [ReReadCluster(cluster=cluster, sheet=sheet,
                                       entity_name=None, cell=cluster.lower(),
                                       profile_key=None, refs=["R1", "U1"])])


def _new_cell_fake(monkeypatch, *, name="pif_avdd", absolute=False,
                   origin_override=(None, None)):
    """The InstantiateCellDialog double answering as tab 2."""
    fake = MagicMock()
    fake.exec.return_value = QDialog.DialogCode.Accepted
    fake.is_new_cell.return_value = True
    fake.result_cell.return_value = name
    fake.new_cell_name.return_value = name
    fake.entity_name.return_value = "PIF_AVDD_ENT"
    fake.cluster.return_value = "PIF_AVDD"
    fake.sheet.return_value = "Channel_1"
    fake.absolute_origin.return_value = absolute
    fake.from_selection.return_value = False
    fake.manual_xy.return_value = (1.0, 2.0)
    fake.origin_override.return_value = origin_override
    monkeypatch.setattr(icd, "InstantiateCellDialog", lambda *a, **k: fake)
    return fake


def test_instantiate_from_cell_extracts_and_stages_new_cell(
        main_window, tmp_path, monkeypatch):
    """Tab 2 end-to-end (strict): the dock detects the ONE fully-selected
    cluster, extracts the NEW Cell through the helper, stages it into cells:,
    then stages the Entity ADDRESSING that cluster + the placement node — all
    staged, nothing on disk until Save."""
    import gui.docks.trees_dock as td_mod
    from types import SimpleNamespace
    from gui.docks.reead import ReReadCluster
    dock, root = _dock(main_window, tmp_path)
    tree = dock._trees[0]
    main_window.connection.board = SimpleNamespace(adapter=object())
    _detect_one_cluster(monkeypatch)
    _new_cell_fake(monkeypatch)
    helper_calls = []
    fake_cell = {"pif_avdd": {"components": [{"role": "R1"}]}}
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_new_cell_for_instantiation",
        lambda *a, **k: helper_calls.append((a, k)) or fake_cell)

    dock._instantiate_from_cell([], [])

    assert helper_calls, "the extraction helper must be called on tab 2"
    # The helper gets the detected ReReadCluster (strict full-selection), the
    # raw selection items and the zero-slot geometry mode.
    _adapter, c, cell_name, _sel, _raw = helper_calls[0][0]
    assert isinstance(c, ReReadCluster) and c.cluster == "PIF_AVDD"
    assert helper_calls[0][1]["absolute"] is False
    assert dock._dirty is True
    assert any(n.ref == "PIF_AVDD_ENT" and n.kind == "placement"
               for n in tree.nodes)
    data = sexp_to_dict(root.read_text(encoding="utf-8"))
    assert data["cells"]["pif_avdd"]["components"] == [{"role": "R1"}]
    new_ent = next(e for e in data["entities"] if e.get("name") == "PIF_AVDD_ENT")
    assert new_ent.get("cell") == "pif_avdd"
    assert new_ent.get("cluster") == "PIF_AVDD"      # addressing == extracted cluster
    assert new_ent.get("sheet") == "Channel_1"
    node = next(n for n in data["trees"][0]["nodes"]
                if n.get("ref") == "PIF_AVDD_ENT")
    assert list(node["xy"]) == [1.0, 2.0]


def test_instantiate_from_cell_new_cell_name_collision_warns_without_write(
        main_window, tmp_path, monkeypatch):
    """A tab-2 name colliding with an EXISTING Cell is refused with a warning —
    the existing Cell is never silently overwritten; nothing is staged."""
    import gui.docks.trees_dock as td_mod
    from types import SimpleNamespace
    cfg_dict = {
        "cells": {"pif_avdd": {"components": []}},   # the name already exists
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
    main_window.connection.board = SimpleNamespace(adapter=object())
    _detect_one_cluster(monkeypatch)
    _new_cell_fake(monkeypatch)  # new_cell_name == "pif_avdd" == an existing cell
    called = []
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_new_cell_for_instantiation",
        lambda *a, **k: called.append(True) or {})
    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a)
                        or td_mod.QMessageBox.StandardButton.Ok)

    dock._instantiate_from_cell([], [])

    assert warnings and "already exists" in str(warnings[0])
    assert called == [], "the extractor must not run for a colliding name"
    assert dock._dirty is False
    data = sexp_to_dict(root.read_text(encoding="utf-8"))
    assert len(data["entities"]) == 1          # unchanged
    assert len(data["trees"][0]["nodes"]) == 1  # unchanged
