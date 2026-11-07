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


def test_a_row_whose_template_was_renamed_is_reachable_and_marked(
        main_window, tmp_path):
    """К3а (8.1): Rename/Delete in Trees never touches these rows, so a row may
    point at a template that is gone. It is OFFERED as a template here (else the
    row could not be reached at all), opens marked and shows why."""
    p = _root(tmp_path, [{"template": "renamed_away", "name": "ch1",
                          "sheet": "Channel_1"}])
    cfg, _ctx, failures = load_config_lenient(str(p))

    dlg = TreeInstancesDialog(main_window, p, cfg,
                              preferred_template="renamed_away",
                              instance_failures=failures)

    assert "renamed_away" in dlg._templates
    assert dlg.current_template() == "renamed_away"
    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 0).text().startswith("⚠ ")
    dlg.table.selectRow(0)
    assert "not found" in dlg.refusal_label.text()


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


def test_fixing_the_refused_row_clears_the_refusal(main_window, tmp_path,
                                                   monkeypatch):
    """Cell 1б in its STAGED form (§7.1): the broken row lives in the working
    set, the lenient load sees it, and setting the row's own anchor makes the
    next load clean — no Discard anywhere."""
    from kicadstamp.config_writer import upsert_tree_instances
    from kicadstamp.config_working_set import WORKING_SET, set_active_graph_root

    p = _point_template_root(tmp_path, [])
    set_active_graph_root(p)
    monkeypatch.setattr(WORKING_SET, "enabled", True)
    try:
        upsert_tree_instances(p, "point_tpl", [
            {"name": "switch_b", "sheet": "SW B"}])
        assert "switch_b" not in p.read_text(encoding="utf-8")  # staged only

        dlg = _open_lenient(main_window, p)
        assert dlg.refusal_label is not None
        dlg.table.selectRow(0)
        assert not dlg.refusal_label.isHidden()

        # The row's own place — exactly what the live user had to add.
        dlg.table.cellWidget(0, 3).set_anchor({"origin": True})
        assert dlg._apply() is True

        _cfg, _ctx, failures = load_config_lenient(str(p))
        assert failures == []
    finally:
        WORKING_SET.clear()
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
    dlg.table.cellWidget(0, 3).set_anchor(anchor)
    assert dlg._apply() is True
    assert _instances_on_disk(p)[0]["anchor"]["shift"] == [0.0, 40.0]


def test_a_declaration_anchor_shift_is_editable_on_the_first_open(
        main_window, tmp_path, monkeypatch):
    """А1/А4 (plan_2026_11_07_dialog_sizes_and_row_shift): on the LIVE dialog the
    shift row was dead on the FIRST open — choosing Point made the form re-run
    its board-frame refresh, and with no tree it disabled the row with "create
    the tree first", so `0, 40` could not be typed at all. The cell above missed
    it because a DISABLED QLineEdit still accepts setText(). The rule now lives
    in the form (declaration=True), and the value is read verbatim."""
    from PyQt6.QtWidgets import QDialog

    import gui.docks.instances_dialog as id_mod

    dlg, _p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("switch_b")
    dlg.table.item(0, 1).setText("SW B")
    seen = {}

    def fake_exec(self):
        form = dlg._last_anchor_form
        form.mode_combo.setCurrentIndex(form.mode_combo.findData("point"))
        form.point_edit.setCurrentText("p_home")
        seen["enabled"] = (form.shift_x_edit.isEnabled(),
                           form.shift_y_edit.isEnabled())
        seen["reason"] = form.shift_reason_label.text()
        form.shift_x_edit.setText("0")
        form.shift_y_edit.setText("40")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(id_mod.QDialog, "exec", fake_exec)
    anchor = dlg._edit_anchor(None)

    assert seen["enabled"] == (True, True), (
        "the shift row must be editable on the FIRST open, not from the second")
    assert seen["reason"] == ""
    assert anchor["point"] == "p_home"
    assert anchor["shift"] == [0.0, 40.0]


def test_a_declaration_anchor_shift_survives_mode_switches(
        main_window, tmp_path, monkeypatch):
    """А2/А3: every mode switch re-runs the board-frame refresh, and a tree-less
    form used to disable the row there AND rewrite the fields from the (empty)
    stored shift. Point -> Origin -> Point must keep the row editable and keep
    what the user typed — otherwise the value silently disappears mid-edit."""
    from PyQt6.QtWidgets import QDialog

    import gui.docks.instances_dialog as id_mod

    dlg, _p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("switch_b")
    dlg.table.item(0, 1).setText("SW B")
    seen = {}

    def fake_exec(self):
        form = dlg._last_anchor_form
        form.mode_combo.setCurrentIndex(form.mode_combo.findData("point"))
        form.point_edit.setCurrentText("p_home")
        form.shift_x_edit.setText("0")
        form.shift_y_edit.setText("40")
        steps = []
        for mode in ("origin", "point"):
            form.mode_combo.setCurrentIndex(form.mode_combo.findData(mode))
            steps.append((mode, form.shift_x_edit.isEnabled(),
                          form.shift_y_edit.isEnabled(),
                          form.shift_reason_label.text(),
                          form.shift_x_edit.text(), form.shift_y_edit.text()))
        seen["steps"] = steps
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(id_mod.QDialog, "exec", fake_exec)
    anchor = dlg._edit_anchor(None)

    for mode, x_enabled, y_enabled, reason, x_text, y_text in seen["steps"]:
        assert x_enabled is True and y_enabled is True, mode
        assert reason == "", mode
        assert (x_text, y_text) == ("0", "40"), mode
    assert anchor["shift"] == [0.0, 40.0]


def test_the_anchor_window_remembers_its_size_between_opens(
        main_window, tmp_path, monkeypatch):
    """Б8 (part Б, the live complaint): «окно якоря каждый раз открывается
    слопнутым». Both halves of the pair: with NOTHING remembered the window
    comes up at its DESIRED default (not Qt's squeezed one), and with a
    remembered size it comes up exactly where it was left."""
    from PyQt6.QtWidgets import QDialog

    import gui.docks.instances_dialog as id_mod
    from gui import settings

    dlg, _p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("switch_b")
    dlg.table.item(0, 1).setText("SW B")
    seen = []

    def fake_exec(self):
        seen.append((self.width(), self.height()))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(id_mod.QDialog, "exec", fake_exec)
    settings.state.set("dialog_size:InstanceAnchor", None)
    dlg._edit_anchor(None)
    assert seen[-1] == (640, 520), "nothing remembered -> the DESIRED default"

    settings.state.set("dialog_size:InstanceAnchor", [700, 500])
    dlg._edit_anchor(None)
    assert seen[-1] == (700, 500), "a remembered size must win over the default"


def test_the_two_anonymous_windows_use_their_own_size_keys(
        main_window, tmp_path, monkeypatch):
    """Б7 (part Б): an anonymous `QDialog` has no class name to key its size on —
    without an explicit key EVERY such window in the app would write the same
    `dialog_size:QDialog` entry, and one window's size would move another's."""
    from PyQt6.QtWidgets import QDialog

    from gui import settings
    from gui.fieldstool_confirm_dialogs import confirm_sync_dialog

    dlg, _p = _open_dialog(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("switch_b")
    dlg.table.item(0, 1).setText("SW B")

    def fake_exec(self):
        self.show()
        self.resize(700, 500)
        self.hide()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    dlg._edit_anchor(None)
    confirm_sync_dialog(main_window, [])

    assert settings.state.get("dialog_size:InstanceAnchor") == [700, 500]
    assert settings.state.get("dialog_size:FieldstoolConfirmSync") == [700, 500]
    assert settings.state.get("dialog_size:QDialog") is None, (
        "the two anonymous windows must not share the generic class key")


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
    # Н7 polish: the dialog shows the loader's OWN hint, and its first exit is
    # the row's own Anchor column — the one action this very dialog offers.
    assert "give this instance its own anchor" in dlg.refusal_label.text()
    assert "Anchor column" in dlg.refusal_label.text()


def test_a_row_with_its_own_anchor_is_written(main_window, tmp_path):
    """Cell 3 (Н7 control): with the anchor the same row is fine and the write
    goes through exactly as before."""
    p = _point_template_root(tmp_path, [])
    cfg, _ctx, _failures = load_config_lenient(str(p))
    dlg = TreeInstancesDialog(main_window, p, cfg, preferred_template="point_tpl")
    dlg._add_row()
    dlg.table.item(0, 0).setText("switch_b")
    dlg.table.item(0, 1).setText("SW B")
    dlg.table.cellWidget(0, 3).set_anchor({"origin": True})

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


def test_table_has_four_axes_besides_name_and_sheet(main_window, tmp_path):
    """Rotation and Anchor (2026-09-12, §И.5) plus the Root column (8.5). The
    Cluster column is GONE (Н9, format 4): an instance changes the sheet only."""
    dlg, _p = _open_dialog(main_window, tmp_path)
    assert dlg.table.columnCount() == 5
    assert dlg.table.horizontalHeaderItem(0).text() == "Instance name"
    assert dlg.table.horizontalHeaderItem(1).text() == "Sheet"
    assert dlg.table.horizontalHeaderItem(2).text() == "Rotation"
    assert dlg.table.horizontalHeaderItem(3).text() == "Anchor"
    assert dlg.table.horizontalHeaderItem(4).text() == "Root"


# ── Rotation + Anchor columns (2026-09-12, plan_..._tree_instance_own_place §И.5) ──


def test_existing_place_and_angle_loaded_into_the_new_columns(main_window, tmp_path):
    """An existing declaration's own anchor/rotation are shown in the two new
    cells (number formatted without a trailing .0; the anchor summarised)."""
    dlg, _p = _open_dialog(main_window, tmp_path, [
        {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
         "anchor": {"point": "p_home"}, "rotation": 90}])
    assert dlg.table.item(0, 2).text() == "90"
    # The subject is the anchor's point NAME, not its uuid (which a lifted
    # format-3 declaration carries), so compare without the identity keys.
    assert without_identity(dlg.table.cellWidget(0, 3).anchor) == {"point": "p_home"}
    assert dlg.table.cellWidget(0, 3).label.text().startswith("point")
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
    dlg.table.item(0, 2).setText("45.5")
    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf",
        "sheet": "Channel_1", "rotation": 45.5}]

    # ... clearing the cell drops the key on the next write
    dlg.table.item(0, 2).setText("")
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
    dlg.table.item(0, 2).setText("ninety")
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
    dlg.table.cellWidget(0, 3).button.click()
    assert dlg.table.cellWidget(0, 3).anchor == {"origin": True}
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
    dlg.table.cellWidget(0, 3).button.click()
    assert without_identity(dlg.table.cellWidget(0, 3).anchor) == {"point": "p_home"}
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
    dlg.table.cellWidget(0, 3).button.click()
    assert dlg.table.cellWidget(0, 3).anchor is None
    assert dlg._apply() is True
    assert _instances_on_disk(p) == [{
        "template": "dac_buf_tpl", "name": "ch1_dac_buf",
        "sheet": "Channel_1", "rotation": 30.0}]


# ── the Root column (Н6 + acceptance 8.5) ──────────────────────────────────


def _multi_root(tmp_path, instances=None, pivot_ref=None) -> Path:
    """A template with a real PATH: driver -> sw and driver -> m1 (a MOUNT), a
    placement `under_mount` BELOW that mount, plus a sibling `other`. The Root
    column must offer the placement nodes whose ancestors are placements too —
    `m1` and everything under it has no config pose (acceptance 8.3)."""
    tree = {
        "name": "tpl", "anchor": {"role": "DAC_BUF", "sheet": "SW A"},
        "nodes": [
            {"ref": "driver", "kind": "placement", "xy": [10.0, 0.0],
             "children": [
                 {"ref": "sw", "kind": "placement", "xy": [2.0, 0.0]},
                 {"ref": "m1", "kind": "mount", "xy": [3.0, 0.0],
                  "anchor": {"role": "R_SW", "sheet": "SW A"},
                  "children": [{"ref": "under_mount", "kind": "placement",
                                "xy": [1.0, 1.0]}]},
             ]},
            {"ref": "other", "kind": "placement", "xy": [0.0, 5.0]},
        ],
    }
    if pivot_ref is not None:
        tree["pivot_ref"] = pivot_ref
    # `R_SW` is placed by ONE cell of the tree — the mount anchor's drift check
    # refuses an internal role the tree places in more than one cell.
    data = {
        "cells": {"c_dac": {"components": [{"role": "DAC_BUF"}]},
                  "c_sw": {"components": [{"role": "R_SW"}]}},
        "entities": [
            {"name": "driver", "cell": "c_dac", "sheet": "SW A",
             "cluster": "DAC_BUF"},
            {"name": "sw", "cell": "c_sw", "sheet": "SW A", "cluster": "DAC_BUF"},
            {"name": "under_mount", "cell": "c_dac", "sheet": "SW A",
             "cluster": "DAC_BUF"},
            {"name": "other", "cell": "c_dac", "sheet": "SW A",
             "cluster": "DAC_BUF"},
        ],
        "trees": [tree],
        "tree_instances": list(instances or []),
    }
    p = tmp_path / "root.sexp"
    p.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
    return p


def _open_multi(main_window, tmp_path, instances=None, pivot_ref=None,
                lenient=False):
    p = _multi_root(tmp_path, instances, pivot_ref)
    if lenient:
        # A row the strict loader already refuses (a root under a mount, say):
        # the dialog still opens on it — that is Н8, and the row must be
        # reachable to be fixed.
        cfg, _ctx, failures = load_config_lenient(str(p))
        return TreeInstancesDialog(main_window, p, cfg,
                                   instance_failures=failures), p
    cfg, _ctx = load_config(str(p))
    return TreeInstancesDialog(main_window, p, cfg), p


def test_the_root_column_offers_placement_nodes_only(main_window, tmp_path):
    """First entry is "— whole tree —" (= no `root`), then every placement node in
    tree order; the mount `m1` is NOT offered (a mount's place comes from the
    board, so the loader refuses a root under or at it)."""
    dlg, _p = _open_multi(main_window, tmp_path)
    dlg._add_row()

    combo = dlg.table.cellWidget(0, 4)
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "— whole tree —", "driver", "sw", "other"]
    assert combo.currentIndex() == 0
    assert combo.currentData() is None


def test_a_chosen_root_reaches_the_declaration_with_its_uuid(main_window, tmp_path):
    """Cell: the selected node lands in the declaration, and under format 3 the
    reference carries its uuid; the next load copies THAT subtree only."""
    dlg, p = _open_multi(main_window, tmp_path)
    dlg._add_row()
    dlg.table.item(0, 0).setText("b")
    dlg.table.item(0, 1).setText("SW B")
    combo = dlg.table.cellWidget(0, 4)
    combo.setCurrentIndex(combo.findText("driver"))

    assert dlg._apply() is True

    row = _instances_on_disk(p)[0]
    assert row["root"]["ref"] == "driver"
    assert row["root"]["ref_uuid"], "format 3 must carry the reference's uuid"

    cfg, _ctx = load_config(str(p))
    copy = next(t for t in cfg.trees if t.name == "b")
    assert [n.ref for n in copy.nodes] == ["driver__b"]


def test_whole_tree_removes_the_root_key(main_window, tmp_path):
    """Cell: picking "— whole tree —" on an existing row drops `root` — the same
    blank-cell contract as a cleared Cluster or Rotation."""
    dlg, p = _open_multi(main_window, tmp_path, [
        {"template": "tpl", "name": "b", "sheet": "SW B",
         "root": {"ref": "driver"}}])
    combo = dlg.table.cellWidget(0, 4)
    assert combo.currentText() == "driver"

    combo.setCurrentIndex(0)

    assert dlg._apply() is True
    assert "root" not in _instances_on_disk(p)[0]


def test_a_stored_root_the_template_no_longer_offers_is_kept_and_refused(
        main_window, tmp_path):
    """The combo shows a stored `root` that is not among the template's placeable
    nodes (`under_mount` hangs under a mount), so an edit of ANOTHER column never
    silently rewrites it into "whole tree" — and the pre-write proof refuses it
    by the ancestor's name instead."""
    dlg, p = _open_multi(main_window, tmp_path, [
        {"template": "tpl", "name": "b", "sheet": "SW B",
         "root": {"ref": "under_mount"}}], lenient=True)

    combo = dlg.table.cellWidget(0, 4)
    assert combo.currentText() == "under_mount"
    assert dlg.rows()[0]["root"]["ref"] == "under_mount"

    dlg.table.item(0, 1).setText("SW B2")
    assert dlg._apply() is False
    assert "hangs under mount node 'm1'" in dlg.refusal_label.text()
    assert _instances_on_disk(p)[0]["sheet"] == "SW B"


def test_a_root_leaving_a_reference_outside_the_subtree_is_not_written(
        main_window, tmp_path):
    """Cell: the Н7 proof covers the Root column too — the loader's own "outside
    the copied subtree" text (the template's pivot_ref points at the sibling we
    are leaving behind) is shown IN the dialog and nothing reaches the disk."""
    dlg, p = _open_multi(main_window, tmp_path, pivot_ref="other")
    dlg._add_row()
    dlg.table.item(0, 0).setText("b")
    dlg.table.item(0, 1).setText("SW B")
    combo = dlg.table.cellWidget(0, 4)
    combo.setCurrentIndex(combo.findText("driver"))

    assert dlg._apply() is False

    assert "outside the copied subtree" in dlg.refusal_label.text()
    dlg.table.selectRow(0)
    assert _instances_on_disk(p) == []


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
    dlg.table.cellWidget(0, 3).button.click()
    assert dlg._apply() is True

    from kicadstamp.tree_position import layout_tree_from_anchor
    from kicadstamp.utils.units import MM
    cfg, _ctx = load_config(str(p))
    tree = next(t for t in cfg.trees if t.name == "ch1_dac_buf")
    assert tree.anchor.point == "p_home"
    pos, _rot = layout_tree_from_anchor(
        tree, adapter=None, cfg=cfg, sheet_names={})["dac_buf__ch1_dac_buf"]
    assert (pos.x / MM, pos.y / MM) == pytest.approx((11.0, 22.0))
