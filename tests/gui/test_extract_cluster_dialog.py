# tests/gui/test_extract_cluster_dialog.py
"""Tests for Tools -> "Extract cluster..." dialog
(gui/docks/extract_cluster_dialog.py, 2026-09-03, plan extract_cluster_entity):
the small modal dialog picks ONE fully-selected Cluster from the current
selection and collects the chosen Entity name for the shared
create_cell_and_entity_for_cluster step. It writes NOTHING itself — DockHub
owns the write, so the full "empty list warning / successful write + refresh"
flows live in test_phase3_wiring.py next to the "Extract tree..." DockHub tests.

Covers: single-cluster list, auto-name prefill (editable for a NEW Entity),
read-only prefill for an EXISTING (cluster, sheet)-matched Entity (reuse,
never a duplicate), duplicate/empty-name rejection on OK without accepting.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtWidgets import QDialog

from gui.docks.extract_cluster_dialog import ExtractClusterDialog
from gui.docks.reead import ReReadCluster
from kicadstamp.config import Config
from kicadstamp.config.models import Cell, Entity


def _cfg(entities=None, cells=None):
    return Config(
        entities=entities or [],
        cells={c.name: c for c in (cells or [])},
        trees=[],
        chains=[],
    )


def _new_cluster(cluster="DAC_BUF", sheet="Channel_0", refs=None):
    """A fully-selected cluster with NO matching Entity (auto-derives)."""
    return ReReadCluster(cluster=cluster, sheet=sheet, entity_name=None,
                         cell="dac_buf", profile_key=None,
                         refs=refs or ["U7"])


def _existing_cluster(entity_name="CH1_PIF_AVDD", cluster="PIF_AVDD",
                      sheet="Channel_1", cell="dac_pif_avdd"):
    """A fully-selected cluster whose Entity already exists in cfg.entities."""
    return ReReadCluster(cluster=cluster, sheet=sheet, entity_name=entity_name,
                         cell=cell, profile_key=None, refs=["R1"])


def test_cluster_list_has_one_row_per_cluster_single_selection(main_window):
    dlg = ExtractClusterDialog(
        main_window,
        [_new_cluster("DAC_BUF", "Channel_0"), _new_cluster("PIF_AVDD", "Channel_1")],
        _cfg())
    assert dlg.cluster_list.count() == 2
    assert (dlg.cluster_list.selectionMode()
            is dlg.cluster_list.SelectionMode.SingleSelection)
    assert dlg.selected_cluster().cluster == "DAC_BUF"


def test_new_cluster_prefills_editable_auto_name(main_window):
    """A cluster with no Entity -> the auto-derived Entity name prefilled and
    EDITABLE (resolve_cluster_entity's cluster+sheet instance name)."""
    cfg = _cfg(entities=[Entity(name="dac_buf", cell="dac_buf")])
    dlg = ExtractClusterDialog(main_window, [_new_cluster("DAC_BUF", "Channel_0")], cfg)
    assert dlg.existing is False
    assert dlg.entity_name_edit.isReadOnly() is False
    assert dlg.entity_name() == "dac_buf_channel_0"


def test_existing_entity_field_is_read_only_and_reused(main_window):
    """A cluster whose (cluster, sheet) Entity already exists -> READ-ONLY
    prefill (reuse — a second Entity for the same instance would duplicate)."""
    cfg = _cfg(entities=[Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                                cluster="PIF_AVDD", sheet="Channel_1")])
    dlg = ExtractClusterDialog(main_window, [_existing_cluster()], cfg)
    assert dlg.existing is True
    assert dlg.entity_name_edit.isReadOnly() is True
    assert dlg.entity_name() == "CH1_PIF_AVDD"


def test_switching_cluster_toggles_read_only_and_prefill(main_window):
    """Picking a different cluster re-runs resolve_cluster_entity: an existing
    Entity -> read-only, a fresh cluster -> editable auto name."""
    cfg = _cfg(entities=[Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                                cluster="PIF_AVDD", sheet="Channel_1")])
    existing = _existing_cluster()
    fresh = _new_cluster("DAC_BUF", "Channel_0")
    dlg = ExtractClusterDialog(main_window, [existing, fresh], cfg)
    dlg.cluster_list.setCurrentRow(1)
    assert dlg.existing is False
    assert dlg.entity_name_edit.isReadOnly() is False
    assert dlg.entity_name() == "dac_buf_channel_0"
    dlg.cluster_list.setCurrentRow(0)
    assert dlg.existing is True
    assert dlg.entity_name_edit.isReadOnly() is True
    assert dlg.entity_name() == "CH1_PIF_AVDD"


def test_duplicate_new_name_rejected_without_accept(main_window, monkeypatch):
    """A NEW Entity renamed to an existing Entity's name -> warning, the dialog
    does NOT accept (no write can follow a rejected OK)."""
    import gui.docks.extract_cluster_dialog as ecd
    cfg = _cfg(entities=[Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                                cluster="PIF_AVDD", sheet="Channel_1")])
    dlg = ExtractClusterDialog(main_window, [_new_cluster("DAC_BUF", "Channel_0")], cfg)
    assert dlg.existing is False
    dlg.entity_name_edit.setText("CH1_PIF_AVDD")  # collide with the existing Entity
    warnings = []
    monkeypatch.setattr(ecd.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a[2])
                        or ecd.QMessageBox.StandardButton.Ok)
    dlg._on_ok()
    assert warnings, "duplicate name must explain the problem"
    assert dlg.result() != QDialog.DialogCode.Accepted


def test_empty_name_rejected_without_accept(main_window, monkeypatch):
    import gui.docks.extract_cluster_dialog as ecd
    dlg = ExtractClusterDialog(main_window, [_new_cluster()], _cfg())
    dlg.entity_name_edit.clear()
    warnings = []
    monkeypatch.setattr(ecd.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a[2])
                        or ecd.QMessageBox.StandardButton.Ok)
    dlg._on_ok()
    assert warnings, "empty name must explain the problem"
    assert dlg.result() != QDialog.DialogCode.Accepted


def test_valid_new_name_accepts(main_window):
    """A NEW cluster with its (edited) unique name -> OK accepts; the caller
    (DockHub) then runs create_cell_and_entity_for_cluster with this name."""
    dlg = ExtractClusterDialog(main_window, [_new_cluster("DAC_BUF", "Channel_0")], _cfg())
    dlg.entity_name_edit.setText("my_dac_buf")
    dlg._on_ok()
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert dlg.selected_cluster().cluster == "DAC_BUF"
    assert dlg.entity_name() == "my_dac_buf"


# ── manual origin override (2026-09-04, plan extract_origin_pad_restore §4) ──

def _sel_fp(ref, role):
    """A Selected-like footprint the dialog reads for roles (.ref, .role)."""
    from types import SimpleNamespace
    return SimpleNamespace(ref=ref, role=role)


def _open_with_footprints(main_window, refs=("U7", "U8")):
    """ExtractClusterDialog with the cluster's refs matched by caller-provided
    selection footprints (the role combo source)."""
    return ExtractClusterDialog(
        main_window,
        [_new_cluster("DAC_BUF", "Channel_0", refs=list(refs))],
        _cfg(),
        selection_footprints=[_sel_fp("U7", "DAC"), _sel_fp("U8", "BUF")])


def test_extract_origin_override_unchecked_is_automatic(main_window):
    """Checkbox off -> origin_override() == (None, None): automatic zero-slot
    detection stays the default (opt-in principle)."""
    dlg = _open_with_footprints(main_window)
    assert dlg.origin_override() == (None, None)
    assert dlg.origin_override_check.isChecked() is False
    # The cluster's own roles are what the combo is fed from (U7->DAC, U8->BUF).
    assert dlg.origin_widget.anchor_role_edit.count() == 2


def test_extract_origin_override_returns_chosen_role_and_pad(main_window):
    """Checkbox on + a Role picked (+ optional Pad) -> origin_override() returns
    exactly them, bypassing the automatic unique-role detection."""
    dlg = _open_with_footprints(main_window)
    dlg.origin_override_check.setChecked(True)
    assert dlg.origin_widget.isVisibleTo(dlg)
    dlg.origin_widget.anchor_role_edit.setCurrentText("BUF")
    dlg.origin_widget.anchor_pad_edit.setText("B2")
    assert dlg.origin_override() == ("BUF", "B2")


def test_extract_origin_override_empty_role_blocks_validate(main_window):
    """Checkbox on + no Ref/Role -> the widget's own build() error is a FATAL
    through _validate(): the dialog never accepts an override the picker itself
    considers invalid."""
    dlg = _open_with_footprints(main_window, refs=("U7",))
    # Empty the role combo (set_known_roles([], []) simulates a cluster whose
    # selection carries no roles).
    dlg.origin_widget.set_known_roles([], [])
    dlg.origin_override_check.setChecked(True)
    problem = dlg._validate()
    assert problem is not None
    assert "Ref or Role" in problem
