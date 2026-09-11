#!/usr/bin/env python3
"""Tests for Tools -> "Instances..." (gui/docks/instances_dialog.py, 2026-09-02
plan tree_instances P3): the modal dialog edits one template's `tree_instances:`
SHORT declarations ({name, sheet} rows, add/remove) and writes them through
config_writer.upsert_tree_instances — it generates nothing (materialization is
the next load's job).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict

from gui.docks.instances_dialog import TreeInstancesDialog


def _root(tmp_path, instances=None, extra=None) -> Path:
    """A root config with one template tree (`dac_buf_tpl`, role anchor + an
    entity placement node) and the given tree_instances declarations. `extra`
    adds further top-level sections (e.g. points: for an anchor round-trip)."""
    data = {
        "entities": [
            {"name": "dac_buf", "cell": "c_dac", "cluster": "DAC_BUF"},
        ],
        "trees": [{
            "name": "dac_buf_tpl", "anchor": {"role": "DAC_BUF"},
            "nodes": [{"ref": "dac_buf", "kind": "placement", "xy": [1.0, 2.0]}],
        }],
        "tree_instances": instances or [],
    }
    data.update(extra or {})
    p = tmp_path / "root.sexp"
    p.write_text(dict_to_sexp(data), encoding="utf-8")
    return p


def _open_dialog(main_window, tmp_path, instances=None, extra=None):
    p = _root(tmp_path, instances, extra)
    cfg, _ctx = load_config(str(p))
    return TreeInstancesDialog(main_window, p, cfg), p


def _instances_on_disk(p) -> list:
    return list(sexp_to_dict(p.read_text(encoding="utf-8"))
                .get("tree_instances") or [])


def test_template_list_excludes_instance_trees(main_window, tmp_path):
    """P3: a generated instance can NOT be a template — only hand-written trees
    are offered in the template picker."""
    dlg, _p = _open_dialog(main_window, tmp_path, [
        {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}])
    assert dlg._templates == ["dac_buf_tpl"]
    assert dlg.template_combo.count() == 1
    assert dlg.current_template() == "dac_buf_tpl"


def test_table_prefilled_with_existing_rows(main_window, tmp_path):
    dlg, _p = _open_dialog(main_window, tmp_path, [
        {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}])
    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 0).text() == "ch1_dac_buf"
    assert dlg.table.item(0, 1).text() == "Channel_1"
    assert dlg.rows() == [{"name": "ch1_dac_buf", "sheet": "Channel_1"}]


def test_create_row_writes_section(main_window, tmp_path):
    dlg, p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1_dac_buf")
    dlg.table.item(0, 1).setText("Channel_1")
    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}]
    cfg, _ = load_config(str(p))
    assert any(t.name == "ch1_dac_buf" for t in cfg.trees)


def test_edit_row_changes_sheet(main_window, tmp_path):
    dlg, p = _open_dialog(main_window, tmp_path, [
        {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}])
    dlg.table.item(0, 1).setText("Channel_1_NEW")
    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1_NEW"}]


def test_remove_row_deletes_declaration(main_window, tmp_path):
    dlg, p = _open_dialog(main_window, tmp_path, [
        {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}])
    dlg.table.selectRow(0)
    dlg._remove_row()
    assert dlg.rows() == []
    assert dlg._apply() is True
    assert _instances_on_disk(p) == []


def test_duplicate_name_is_rejected_without_write(main_window, tmp_path, monkeypatch):
    import gui.docks.instances_dialog as id_mod
    warnings = []
    monkeypatch.setattr(id_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a)
                        or id_mod.QMessageBox.StandardButton.Ok)
    dlg, p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("dup")
    dlg.table.item(0, 1).setText("Channel_1")
    dlg._add_row()
    dlg.table.item(1, 0).setText("dup")
    dlg.table.item(1, 1).setText("Channel_2")
    assert dlg._apply() is False
    assert warnings, "duplicate name must explain the problem"
    assert _instances_on_disk(p) == []


def test_blank_row_is_rejected(main_window, tmp_path, monkeypatch):
    import gui.docks.instances_dialog as id_mod
    warnings = []
    monkeypatch.setattr(id_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a)
                        or id_mod.QMessageBox.StandardButton.Ok)
    dlg, p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1")
    dlg.table.item(0, 1).setText("")   # blank sheet
    assert dlg._apply() is False
    assert warnings
    assert _instances_on_disk(p) == []


# ── cluster column (2026-09-03, plan tree_instances_cluster) ────────────────


def test_table_has_five_columns_with_cluster_rotation_and_anchor(main_window, tmp_path):
    """2026-09-12 (§И.5): two more columns — Rotation and Anchor — appended
    AFTER the cluster column, so the existing indices 0/1/2 are unchanged."""
    dlg, _p = _open_dialog(main_window, tmp_path)
    assert dlg.table.columnCount() == 5
    assert dlg.table.horizontalHeaderItem(0).text() == "Instance name"
    assert dlg.table.horizontalHeaderItem(1).text() == "Sheet"
    assert dlg.table.horizontalHeaderItem(2).text() == "Cluster"
    assert dlg.table.horizontalHeaderItem(3).text() == "Rotation"
    assert dlg.table.horizontalHeaderItem(4).text() == "Anchor"


def test_existing_cluster_loaded_into_third_column(main_window, tmp_path):
    dlg, _p = _open_dialog(main_window, tmp_path, [
        {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
         "cluster": "CLUST_A"}])
    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 0).text() == "ch1_dac_buf"
    assert dlg.table.item(0, 1).text() == "Channel_1"
    assert dlg.table.item(0, 2).text() == "CLUST_A"
    assert dlg.rows() == [{"name": "ch1_dac_buf", "sheet": "Channel_1",
                           "cluster": "CLUST_A"}]


def test_row_with_cluster_writes_cluster_key(main_window, tmp_path):
    dlg, p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1_dac_buf")
    dlg.table.item(0, 1).setText("Channel_1")
    dlg.table.item(0, 2).setText("CLUST_A")
    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
        "cluster": "CLUST_A"}]


def test_row_with_blank_cluster_omits_the_key(main_window, tmp_path):
    dlg, p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1_dac_buf")
    dlg.table.item(0, 1).setText("Channel_1")
    dlg.table.item(0, 2).setText("   ")   # blank cluster
    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}]


def test_blank_cluster_is_not_required(main_window, tmp_path, monkeypatch):
    """Cluster is OPTIONAL — a valid name+sheet row with an empty cluster cell
    must NOT be rejected (only name/sheet are required), and saves without a
    cluster key."""
    import gui.docks.instances_dialog as id_mod
    warnings = []
    monkeypatch.setattr(id_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a)
                        or id_mod.QMessageBox.StandardButton.Ok)
    dlg, p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1")
    dlg.table.item(0, 1).setText("Channel_1")
    dlg.table.item(0, 2).setText("")   # blank cluster
    assert dlg._apply() is True
    assert not warnings
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1", "sheet": "Channel_1"}]


# ── Rotation + Anchor columns (2026-09-12, plan_..._tree_instance_own_place §И.5) ──


def test_existing_place_and_angle_loaded_into_the_new_columns(main_window, tmp_path):
    """An existing declaration's own anchor/rotation are shown in the two new
    cells (number formatted without a trailing .0; the anchor summarised)."""
    dlg, _p = _open_dialog(main_window, tmp_path, [
        {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
         "anchor": {"point": "p_home"}, "rotation": 90}])
    assert dlg.table.item(0, 3).text() == "90"
    assert dlg.table.cellWidget(0, 4).anchor == {"point": "p_home"}
    assert dlg.table.cellWidget(0, 4).label.text().startswith("point")
    assert dlg.rows() == [{"name": "ch1_dac_buf", "sheet": "Channel_1",
                           "rotation": 90.0, "anchor": {"point": "p_home"}}]


def test_rotation_cell_writes_a_number_and_a_blank_cell_omits_the_key(
        main_window, tmp_path):
    """A typed rotation reaches the config as a NUMBER; a blank cell means
    "inherit the template's angle" and the key is dropped (never null/"")."""
    dlg, p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1_dac_buf")
    dlg.table.item(0, 1).setText("Channel_1")
    dlg.table.item(0, 3).setText("45.5")
    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf",
        "sheet": "Channel_1", "rotation": 45.5}]

    # ... clearing the cell drops the key on the next write
    dlg.table.item(0, 3).setText("")
    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}]


def test_non_numeric_rotation_is_rejected_without_write(main_window, tmp_path,
                                                        monkeypatch):
    import gui.docks.instances_dialog as id_mod
    warnings = []
    monkeypatch.setattr(id_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a)
                        or id_mod.QMessageBox.StandardButton.Ok)
    dlg, p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1_dac_buf")
    dlg.table.item(0, 1).setText("Channel_1")
    dlg.table.item(0, 3).setText("ninety")
    assert dlg._apply() is False
    assert warnings, "a non-numeric rotation must explain the problem"
    assert _instances_on_disk(p) == []


def test_edit_anchor_really_embeds_the_shared_anchor_form(main_window, tmp_path,
                                                          monkeypatch):
    """§И.5's core requirement: the editor is the SHARED AnchorFormWidget from
    trees_dock (NOT a second anchor form), it builds with tree=None, and its
    tree-only rows (Tree settings box, Shift) are hidden. `QDialog.exec` is
    stubbed so the modal never opens — the real construction runs."""
    from PyQt6.QtWidgets import QDialog
    from gui.docks import trees_dock

    dlg, _p = _open_dialog(main_window, tmp_path)
    seen = {}

    def fake_exec(self):
        form = self.findChild(trees_dock.AnchorFormWidget)
        seen["form"] = form
        seen["settings_hidden"] = form.settings_box.isHidden()
        seen["shift_hidden"] = form.shift_row.isHidden()
        seen["shift_reason_hidden"] = form.shift_reason_label.isHidden()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    result = dlg._edit_anchor(None)

    assert isinstance(seen["form"], trees_dock.AnchorFormWidget)
    assert seen["settings_hidden"] is True     # a declaration has no pivot/angle
    assert seen["shift_hidden"] is True        # and no own shift row
    assert seen["shift_reason_hidden"] is True
    assert result == {"origin": True}          # the form's own default mode


def test_edit_anchor_inherit_button_returns_none(main_window, tmp_path,
                                                 monkeypatch):
    """"Inherit from template" is an EXPLICIT choice (None), distinct from
    Cancel (_CANCELLED)."""
    from PyQt6.QtWidgets import QDialog, QPushButton
    from gui.docks.instances_dialog import _CANCELLED

    dlg, _p = _open_dialog(main_window, tmp_path)

    def fake_exec(self):
        self.findChild(QPushButton, "inherit_anchor_button").click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    assert dlg._edit_anchor({"point": "p_home"}) is None
    assert dlg._edit_anchor(None) is not _CANCELLED

    def fake_reject(self):
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", fake_reject)
    assert dlg._edit_anchor(None) is _CANCELLED


def test_anchor_cell_button_writes_the_chosen_place(main_window, tmp_path, monkeypatch):
    """The Anchor cell's "…" button lands the editor's result on the row AND on
    disk (the editor itself is stubbed here — the real one is covered by
    test_edit_anchor_really_embeds_the_shared_anchor_form)."""
    dlg, p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1_dac_buf")
    dlg.table.item(0, 1).setText("Channel_1")
    monkeypatch.setattr(TreeInstancesDialog, "_edit_anchor",
                        lambda self, current: {"origin": True})
    dlg.table.cellWidget(0, 4).button.click()
    assert dlg.table.cellWidget(0, 4).anchor == {"origin": True}
    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf",
        "sheet": "Channel_1", "anchor": {"origin": True}}]


def test_anchor_cell_cancel_keeps_the_previous_place(main_window, tmp_path,
                                                     monkeypatch):
    from gui.docks.instances_dialog import _CANCELLED
    dlg, p = _open_dialog(main_window, tmp_path, [
        {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
         "anchor": {"point": "p_home"}}])
    monkeypatch.setattr(TreeInstancesDialog, "_edit_anchor",
                        lambda self, current: _CANCELLED)
    dlg.table.cellWidget(0, 4).button.click()
    assert dlg.table.cellWidget(0, 4).anchor == {"point": "p_home"}
    assert dlg._apply() is True   # an untouched row stays untouched
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf",
        "sheet": "Channel_1", "anchor": {"point": "p_home"}}]


def test_inherit_from_template_clears_the_anchor_key(main_window, tmp_path,
                                                     monkeypatch):
    """"Inherit from template" returns None for the row -> the anchor key is
    REMOVED from the declaration (while the row's rotation survives)."""
    dlg, p = _open_dialog(main_window, tmp_path, [
        {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
         "anchor": {"point": "p_home"}, "rotation": 30}])
    monkeypatch.setattr(TreeInstancesDialog, "_edit_anchor",
                        lambda self, current: None)
    dlg.table.cellWidget(0, 4).button.click()
    assert dlg.table.cellWidget(0, 4).anchor is None
    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf",
        "sheet": "Channel_1", "rotation": 30.0}]


def test_anchor_written_by_the_dialog_materializes_on_the_point(main_window,
                                                              tmp_path, monkeypatch):
    """End to end through the DIALOG: the written declaration re-opens and the
    generated instance tree really stands on the declared point (the config is
    re-loaded, not just re-read as text)."""
    dlg, p = _open_dialog(main_window, tmp_path, extra={
        "points": {"p_home": {"xy": [10.0, 20.0]}}})
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1_dac_buf")
    dlg.table.item(0, 1).setText("Channel_1")
    monkeypatch.setattr(TreeInstancesDialog, "_edit_anchor",
                        lambda self, current: {"point": "p_home"})
    dlg.table.cellWidget(0, 4).button.click()
    assert dlg._apply() is True

    from kicadstamp.tree_position import layout_tree_from_anchor
    from kicadstamp.utils.units import MM
    cfg, _ctx = load_config(str(p))
    tree = next(t for t in cfg.trees if t.name == "ch1_dac_buf")
    assert tree.anchor.point == "p_home"
    pos, _rot = layout_tree_from_anchor(
        tree, adapter=None, cfg=cfg, sheet_names={})["dac_buf__ch1_dac_buf"]
    assert (pos.x / MM, pos.y / MM) == pytest.approx((11.0, 22.0))
