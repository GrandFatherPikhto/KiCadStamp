#!/usr/bin/env python3
"""Tests for Tools -> "Instances..." (gui/docks/instances_dialog.py, 2026-09-02
plan tree_instances P3): the modal dialog edits one template's `tree_instances:`
SHORT declarations ({name, sheet} rows, add/remove) and writes them through
config_writer.upsert_tree_instances — it generates nothing (materialization is
the next load's job).
"""
from pathlib import Path

import pytest

from kicadstamp.config import load_config, load_config_lenient
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError

from gui.docks.instances_dialog import TreeInstancesDialog

from tests.fakes.format3 import without_identity


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """У3.5 (в): the format-3 writer resolves a reference's UUID against the
    ACTIVE GRAPH ROOT. These cells write a self-contained config; the root is a
    path that does NOT exist, so the stamp indexes THIS write's own records
    (config/format3._build_format3_index) — the format-3 product path, no
    on-disk graph walked. Under format 2 (< 3) the root is never consulted."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)


def _root(tmp_path, instances=None, extra=None) -> Path:
    """A root config with one template tree (`dac_buf_tpl`, role anchor + an
    entity placement node) and the given tree_instances declarations. `extra`
    adds further top-level sections (e.g. points: for an anchor round-trip)."""
    data = {
        # The entity's `cell:` and a declaration's `anchor: {point: …}` are
        # format-3 references; the load resolves them, so the target records
        # must exist (their content is irrelevant here).
        "cells": {"c_dac": {}},
        "points": {"p_home": {"xy": [10.0, 20.0]}},
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
    p.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
    return p


def _open_dialog(main_window, tmp_path, instances=None, extra=None,
                 sheet_names=None):
    p = _root(tmp_path, instances, extra)
    cfg, _ctx = load_config(str(p))
    return TreeInstancesDialog(main_window, p, cfg,
                               sheet_names=sheet_names), p


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


def _point_template_root(tmp_path, instances) -> Path:
    """A POINT-anchored template plus the given declarations: a row without its
    own `anchor` is REFUSED at load (the live `switch_b` shape of Н7), so the
    strict loader refuses the whole file — the dialog's Н8 case."""
    data = {
        "cells": {"c_dac": {}},
        "points": {"p_home": {"xy": [10.0, 20.0]}},
        "entities": [{"name": "tpl_root", "cell": "c_dac", "sheet": "SW A"}],
        "trees": [{"name": "point_tpl", "anchor": {"point": "p_home"},
                   "nodes": [{"ref": "tpl_root", "kind": "placement",
                              "xy": [0.0, 0.0]}]}],
        "tree_instances": list(instances),
    }
    p = tmp_path / "root.sexp"
    p.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
    return p


def _open_lenient(main_window, p, template="point_tpl"):
    cfg, _ctx, failures = load_config_lenient(str(p))
    return TreeInstancesDialog(main_window, p, cfg,
                               preferred_template=template,
                               instance_failures=failures)


def test_a_refused_row_opens_marked_and_shows_its_text(main_window, tmp_path):
    """Н8 (7.11): the strict loader refuses this file, yet the dialog opens, the
    row is marked and its refusal is shown — not a modal, a label (п.43)."""
    p = _point_template_root(tmp_path, [
        {"template": "point_tpl", "name": "switch_b", "sheet": "SW B"}])
    with pytest.raises(ValidationError):
        load_config(str(p))

    dlg = _open_lenient(main_window, p)

    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 0).text().startswith("⚠ ")
    assert "refused on load" in dlg.table.item(0, 0).toolTip()

    dlg.table.selectRow(0)
    assert not dlg.refusal_label.isHidden()
    text = dlg.refusal_label.text()
    assert "switch_b" in text and "fix or delete" in text
    assert "must be role-anchored OR self-anchored" in text


def test_fixing_the_refused_row_clears_the_refusal(main_window, tmp_path):
    """Cell 1б in its STAGED form (§7.1): the broken row lives in the working
    set, the lenient load sees it, and setting the row's own anchor makes the
    next load clean — no Discard anywhere."""
    from kicadstamp.config_writer import upsert_tree_instances
    from kicadstamp.config_working_set import WORKING_SET, set_active_graph_root

    p = _point_template_root(tmp_path, [])
    set_active_graph_root(p)
    WORKING_SET.enabled = True
    try:
        upsert_tree_instances(p, "point_tpl", [
            {"name": "switch_b", "sheet": "SW B"}])
        assert "switch_b" not in p.read_text(encoding="utf-8")  # staged only

        dlg = _open_lenient(main_window, p)
        assert dlg.refusal_label is not None
        dlg.table.selectRow(0)
        assert not dlg.refusal_label.isHidden()

        # The row's own place — exactly what the live user had to add.
        dlg.table.cellWidget(0, 4).set_anchor({"origin": True})
        assert dlg._apply() is True

        _cfg, _ctx, failures = load_config_lenient(str(p))
        assert failures == []
    finally:
        WORKING_SET.clear()
        WORKING_SET.enabled = False
        set_active_graph_root(None)


def test_deleting_the_refused_row_clears_the_refusal(main_window, tmp_path):
    """Cell 1в: deleting the refused row is an ordinary write and the file loads
    strictly again."""
    p = _point_template_root(tmp_path, [
        {"template": "point_tpl", "name": "switch_b", "sheet": "SW B"}])

    dlg = _open_lenient(main_window, p)
    dlg.table.selectRow(0)
    dlg._remove_row()
    assert dlg._apply() is True
    assert _instances_on_disk(p) == []
    cfg, _ctx = load_config(str(p))  # strict door is happy again
    assert [t.name for t in cfg.trees] == ["point_tpl"]


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


# ── Н7/Н4: the row is PROVEN before it is written ──────────────────────────

def test_the_anchor_form_keeps_an_editable_shift_and_saves_it(
        main_window, tmp_path, monkeypatch):
    """Cell 6 (Н5): a declaration's own anchor has a SHIFT axis, and the shared
    form's shift row used to be hidden — the only way to express "+40 mm along
    y" was to invent a second point. The row is now visible and editable, and
    the value lands in the declaration as config (LOCAL) mm — the way
    (anchor (point "Origin" ...) (shift 0.0 40.0)) is written by hand."""
    from PyQt6.QtWidgets import QDialog

    import gui.docks.instances_dialog as id_mod

    dlg, p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("switch_b")
    dlg.table.item(0, 1).setText("SW B")

    def _fake_exec(self):
        form = dlg._last_anchor_form
        form.mode_combo.setCurrentIndex(form.mode_combo.findData("origin"))
        form.shift_x_edit.setText("0.0")
        form.shift_y_edit.setText("40.0")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(id_mod.QDialog, "exec", _fake_exec)

    anchor = dlg._edit_anchor(None)
    assert anchor["origin"] is True
    assert anchor["shift"] == [0.0, 40.0]

    # The row's own anchor cell holds it (here it is set directly — the nested
    # dialog is what normally does this through its "…" button).
    dlg.table.cellWidget(0, 4).set_anchor(anchor)
    assert dlg._apply() is True
    assert _instances_on_disk(p)[0]["anchor"]["shift"] == [0.0, 40.0]


def test_a_row_the_loader_would_refuse_is_not_written(main_window, tmp_path):
    """Cell 2 (Н7): a row without its own anchor under a POINT-anchored template
    is refused by the loader — so the dialog refuses it FIRST, with the loader's
    OWN words, and the file is untouched."""
    from kicadstamp.config.tree_instances import expand_tree_instances

    from gui.docks.instances_validation import proposed_graph

    p = _point_template_root(tmp_path, [])
    cfg, _ctx, _failures = load_config_lenient(str(p))
    dlg = TreeInstancesDialog(main_window, p, cfg, preferred_template="point_tpl")
    dlg._add_row()
    dlg.table.item(0, 0).setText("switch_b")
    dlg.table.item(0, 1).setText("SW B")

    assert dlg._apply() is False
    assert _instances_on_disk(p) == []

    # ONE rule: the dialog shows exactly what the loader would say about the
    # very same proposed rows (§7.7 of the plan — no second validation).
    failures: list[dict] = []
    expand_tree_instances(proposed_graph(p, "point_tpl", dlg.rows()),
                          failures=failures)
    assert [f["name"] for f in failures] == ["switch_b"]
    assert failures[0]["error"] == dlg.refusal_label.text()
    assert "must be role-anchored OR self-anchored" in dlg.refusal_label.text()


def test_a_row_with_its_own_anchor_is_written(main_window, tmp_path):
    """Cell 3 (Н7 control): with the anchor the same row is fine and the write
    goes through exactly as before."""
    p = _point_template_root(tmp_path, [])
    cfg, _ctx, _failures = load_config_lenient(str(p))
    dlg = TreeInstancesDialog(main_window, p, cfg, preferred_template="point_tpl")
    dlg._add_row()
    dlg.table.item(0, 0).setText("switch_b")
    dlg.table.item(0, 1).setText("SW B")
    dlg.table.cellWidget(0, 4).set_anchor({"origin": True})

    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "point_tpl", "name": "switch_b", "sheet": "SW B",
        "anchor": {"origin": True}}]


def test_a_sheet_with_no_template_cluster_warns_but_is_written(
        main_window, tmp_path, caplog):
    """Cell 10а (Н4): the sheet exists, yet no cluster the template generates is
    on it — a WARNING in the Log, never a refusal."""
    import logging

    dlg, p = _open_dialog(main_window, tmp_path,
                          sheet_names={"u1": "Channel_1"})
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1_dac_buf")
    dlg.table.item(0, 1).setText("Channel_1")

    with caplog.at_level(logging.WARNING):
        assert dlg._apply() is True

    assert any("no clusters of template" in r.message for r in caplog.records)
    assert _instances_on_disk(p) != []


def test_a_sheet_the_project_does_not_have_warns_but_is_written(
        main_window, tmp_path, caplog):
    """Cell 10б (Н4): the row names a sheet that is not in the project — again a
    WARNING only (the sheet may be created later; the write is not forbidden)."""
    import logging

    dlg, p = _open_dialog(main_window, tmp_path,
                          sheet_names={"u1": "Channel_1"})
    dlg._add_row()
    dlg.table.item(0, 0).setText("ch1_dac_buf")
    dlg.table.item(0, 1).setText("Channel_9")

    with caplog.at_level(logging.WARNING):
        assert dlg._apply() is True

    assert any("is not among the project's sheets" in r.message
               for r in caplog.records)
    assert _instances_on_disk(p) != []


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
    # The subject is the anchor's point NAME, not its uuid (which a lifted
    # format-3 declaration carries), so compare without the identity keys.
    assert without_identity(dlg.table.cellWidget(0, 4).anchor) == {"point": "p_home"}
    assert dlg.table.cellWidget(0, 4).label.text().startswith("point")
    assert without_identity(dlg.rows()) == [
        {"name": "ch1_dac_buf", "sheet": "Channel_1",
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
    trees_dock (NOT a second anchor form), and it builds with tree=None.
    The tree-only box (pivot/angle) stays hidden; the SHIFT row is now
    VISIBLE and editable (Н5 of plan_2026_11_07_tree_instances_dialog_and_
    subtree — the old "no own shift row" rule is what forced a second point).
    `QDialog.exec` is stubbed so the modal never opens."""
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
    assert seen["shift_hidden"] is False       # but it DOES have its own shift
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
    assert without_identity(dlg.table.cellWidget(0, 4).anchor) == {"point": "p_home"}
    assert dlg._apply() is True   # an untouched row stays untouched
    assert without_identity(_instances_on_disk(p)) == [{
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
