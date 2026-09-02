"""Tests for the "Tools -> Extract tree..." modal dialog (2026-09-01, plan
extract_selection_as_tree.md) — the thin 3-tab renderer in
gui/docks/tree_from_selection_dialog.py. All mapping/validation logic is
tested separately in test_tree_from_selection.py; this file only pins the
widget behaviour (rows, checkboxes, master, prefill, blocking OK)."""
from PyQt6.QtWidgets import QDialog, QMessageBox

from gui.docks import tree_from_selection_dialog as tfsd
from gui.docks.reead import ReReadCluster
from gui.docks.tree_from_selection import InterClusterNet
from gui.docks.tree_from_selection_dialog import TreeFromSelectionDialog
from kicadstamp.trees import TreeAnchor


def _clusters():
    return [
        ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1", entity_name="CH1_PIF_AVDD",
                      cell="dac_pif_avdd", profile_key=None, refs=["R1"]),
        ReReadCluster(cluster="PIF_CLKVDD", sheet="Channel_1", entity_name="CH1_PIF_CLKVDD",
                      cell="dac_pif_clkvdd", profile_key=None, refs=["R2"]),
    ]


def _nets():
    return [
        InterClusterNet(net="SHARED", track_count=2, via_count=1),
        InterClusterNet(net="OTHER", track_count=0, via_count=3),
    ]


def _prefills():
    return {
        0: TreeAnchor(role="DAC", anchor_sheet="Channel_1", anchor_cluster="PIF_AVDD"),
        1: TreeAnchor(role="DAC", anchor_sheet="Channel_1", anchor_cluster="PIF_CLKVDD"),
    }


# ── structure ─────────────────────────────────────────────────────────────

def test_three_tabs_with_expected_headers(main_window):
    dialog = TreeFromSelectionDialog(_clusters(), _nets(), [], parent=main_window)
    assert dialog.tabs.count() == 3
    assert dialog.tabs.tabText(0) == "Clusters"
    assert dialog.tabs.tabText(1) == "Anchor"
    assert dialog.tabs.tabText(2) == "Tracks and vias between clusters"


def test_clusters_tab_rows_and_default_checks(main_window):
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window)
    assert dialog._table.rowCount() == 2
    assert all(cb.isChecked() for cb in dialog._checkboxes)
    assert dialog._table.item(0, 1).text() == "PIF_AVDD"
    assert dialog._table.item(0, 2).text() == "Channel_1"
    assert dialog._table.item(0, 3).text() == "CH1_PIF_AVDD"
    assert dialog._table.item(0, 4).text() == "dac_pif_avdd"


def test_selected_clusters_respects_unchecked(main_window):
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window)
    dialog._checkboxes[1].setChecked(False)
    rows = dialog.selected_clusters()
    assert len(rows) == 1
    assert rows[0].cell == "dac_pif_avdd"


# ── tree name validation ──────────────────────────────────────────────────

def test_ok_blocked_on_empty_name(main_window, monkeypatch):
    warnings = []
    monkeypatch.setattr(tfsd.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a[2]))
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window,
                                     prefills=_prefills())
    dialog._on_ok()
    assert warnings and "empty" in warnings[0].lower()
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_ok_blocked_on_duplicate_name_when_update_declined(main_window, monkeypatch):
    """Phase E: an existing name now asks "update?" — declining (No, safe
    default) still blocks; nothing is accepted."""
    questions = []
    monkeypatch.setattr(tfsd.QMessageBox, "warning",
                        lambda *a, **k: None)
    monkeypatch.setattr(tfsd.QMessageBox, "question",
                        lambda *a, **k: questions.append(a[2])
                        or QMessageBox.StandardButton.No)
    dialog = TreeFromSelectionDialog(_clusters(), [], ["power_tree"],
                                     parent=main_window, prefills=_prefills())
    dialog.tree_name_edit.setText("power_tree")
    dialog._on_ok()
    assert questions and "already exists" in questions[0]
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_ok_duplicate_name_confirmed_updates_existing(main_window, monkeypatch):
    """Phase E: confirming the update proceeds (the tree is rebuilt from the
    current selection and replaces the old one in dock_hub)."""
    monkeypatch.setattr(tfsd.QMessageBox, "warning",
                        lambda *a, **k: None)
    monkeypatch.setattr(tfsd.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog = TreeFromSelectionDialog(_clusters(), [], ["power_tree"],
                                     parent=main_window, prefills=_prefills())
    dialog.tree_name_edit.setText("power_tree")
    dialog.root_cluster_combo.setCurrentIndex(0)  # prefills the role
    dialog._on_ok()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_ok_blocked_without_role(main_window, monkeypatch):
    warnings = []
    monkeypatch.setattr(tfsd.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a[2]))
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window)
    dialog.tree_name_edit.setText("power_tree")
    # No root-cluster selected and no role typed -> role is empty.
    dialog._on_ok()
    assert warnings and "Role is required" in warnings[0]


def test_ok_accepted_with_valid_input(main_window, monkeypatch):
    monkeypatch.setattr(tfsd.QMessageBox, "warning",
                        lambda *a, **k: None)
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window,
                                     prefills=_prefills())
    dialog.tree_name_edit.setText("power_tree")
    dialog.root_cluster_combo.setCurrentIndex(0)  # prefills the role
    dialog._on_ok()
    assert dialog.result() == QDialog.DialogCode.Accepted


# ── anchor tab ────────────────────────────────────────────────────────────

def test_root_cluster_selection_prefills_anchor_fields(main_window):
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window,
                                     prefills=_prefills())
    dialog.root_cluster_combo.setCurrentIndex(1)  # CH1_PIF_CLKVDD
    assert dialog.sheet_edit.currentText() == "Channel_1"
    assert dialog.cluster_edit.currentText() == "PIF_CLKVDD"
    assert dialog.role_edit.currentText() == "DAC"


def test_manual_narrowing_builds_tree_anchor(main_window):
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window,
                                     role_candidates=["DAC", "CAP"],
                                     cluster_candidates=["PIF_AVDD"])
    dialog.sheet_edit.setCurrentText("Channel_0")
    dialog.cluster_edit.setCurrentText("PIF_AVDD")
    dialog.role_edit.setCurrentText("CAP")
    dialog.pad_edit.setText("3")
    anchor = dialog.build_anchor()
    assert anchor.role == "CAP"
    assert anchor.anchor_sheet == "Channel_0"
    assert anchor.anchor_cluster == "PIF_AVDD"
    assert anchor.anchor_pad == "3"


def test_sheet_combo_lists_names_not_uuid_keys(main_window):
    """Regression 2026-09-02 (dock_hub passes sheet_names as a {uuid: Sheetname}
    dict): the Anchor-tab Sheet combo must show the READABLE names
    (Channel_0/…), never the uuid keys — `list(dict)` returns keys."""
    dialog = TreeFromSelectionDialog(
        _clusters(), [], [], parent=main_window,
        sheet_names={"sheet-1111-0000": "Channel_0", "sheet-2222-0000": "Channel_1"})
    items = [dialog.sheet_edit.itemText(i) for i in range(dialog.sheet_edit.count())]
    assert items == ["Channel_0", "Channel_1"]


def test_manual_narrowing_overrides_existing_cluster_anchor(main_window):
    """Picking a root cluster prefills the "existing cluster anchor", but a
    manual narrowing afterwards wins in build_anchor()."""
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window,
                                     prefills=_prefills())
    dialog.root_cluster_combo.setCurrentIndex(0)
    assert dialog.role_edit.currentText() == "DAC"
    dialog.role_edit.setCurrentText("CAP")
    assert dialog.build_anchor().role == "CAP"


# ── nets tab ──────────────────────────────────────────────────────────────

def test_nets_tab_rows_and_counts(main_window):
    dialog = TreeFromSelectionDialog(_clusters(), _nets(), [], parent=main_window)
    assert dialog._net_table.rowCount() == 2
    assert dialog._net_table.item(0, 1).text() == "SHARED"
    assert dialog._net_table.item(0, 2).text() == "2"
    assert dialog._net_table.item(0, 3).text() == "1"
    assert all(cb.isChecked() for cb in dialog._net_checkboxes)


def test_nets_master_select_all_deselect(main_window):
    dialog = TreeFromSelectionDialog(_clusters(), _nets(), [], parent=main_window)
    dialog._net_master.setChecked(False)
    assert not any(cb.isChecked() for cb in dialog._net_checkboxes)
    dialog._net_master.setChecked(True)
    assert all(cb.isChecked() for cb in dialog._net_checkboxes)


def test_nets_master_reflects_partial_rows(main_window):
    dialog = TreeFromSelectionDialog(_clusters(), _nets(), [], parent=main_window)
    dialog._net_checkboxes[1].setChecked(False)
    assert dialog._net_master.checkState().name == "PartiallyChecked"
    dialog._net_checkboxes[1].setChecked(True)
    assert dialog._net_master.checkState().name == "Checked"


def test_selected_nets_respects_unchecked(main_window):
    dialog = TreeFromSelectionDialog(_clusters(), _nets(), [], parent=main_window)
    dialog._net_checkboxes[0].setChecked(False)
    nets = dialog.selected_nets()
    assert len(nets) == 1
    assert nets[0].net == "OTHER"


def test_empty_nets_tab_offers_no_master(main_window):
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window)
    assert dialog._net_master is None
    assert dialog.selected_nets() == []


# ── cell-error marking + blocking ─────────────────────────────────────────

def test_cell_error_marks_row_and_blocks_ok(main_window, monkeypatch):
    warnings = []
    monkeypatch.setattr(tfsd.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a[2]))
    errors = ["", "cluster 'GHOST': no Entity"]
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window,
                                     cluster_errors=errors, prefills=_prefills())
    # The invalid row's Cluster cell is marked red.
    fg = dialog._table.item(1, 1).foreground().color()
    assert fg.red() > 100 and fg.green() < 100  # red-ish

    dialog.tree_name_edit.setText("power_tree")
    dialog.root_cluster_combo.setCurrentIndex(0)
    dialog._on_ok()  # row 1 is still checked -> blocked
    assert warnings and "PIF_CLKVDD" in warnings[0]
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_cell_error_ignored_when_row_unchecked(main_window, monkeypatch):
    monkeypatch.setattr(tfsd.QMessageBox, "warning",
                        lambda *a, **k: None)
    errors = ["", "cluster 'GHOST': no Entity"]
    dialog = TreeFromSelectionDialog(_clusters(), [], [], parent=main_window,
                                     cluster_errors=errors, prefills=_prefills())
    dialog._checkboxes[1].setChecked(False)  # uncheck the invalid row
    dialog.tree_name_edit.setText("power_tree")
    dialog.root_cluster_combo.setCurrentIndex(0)
    dialog._on_ok()
    assert dialog.result() == QDialog.DialogCode.Accepted
