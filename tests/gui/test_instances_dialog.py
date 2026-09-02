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

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict

from gui.docks.instances_dialog import TreeInstancesDialog


def _root(tmp_path, instances=None) -> Path:
    """A root config with one template tree (`dac_buf_tpl`, role anchor + an
    entity placement node) and the given tree_instances declarations."""
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
    p = tmp_path / "root.sexp"
    p.write_text(dict_to_sexp(data), encoding="utf-8")
    return p


def _open_dialog(main_window, tmp_path, instances=None):
    p = _root(tmp_path, instances)
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
