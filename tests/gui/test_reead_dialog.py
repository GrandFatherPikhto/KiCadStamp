# tests/gui/test_reead_dialog.py
"""Tests for the "Tools -> Re-read selected..." modal dialog (2026-08-31, plan
reead_selected_dialog.md) — a thin table of fully-selected Clusters with
checkboxes, returning the checked rows via selected_rows()."""
from gui.docks.reead import ReReadCluster
from gui.docks.reead_dialog import ReReadDialog


def _clusters():
    return [
        ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1", entity_name="CH1_PIF_AVDD",
                      cell="dac_pif_avdd", profile_key="dac_pif_avdd", refs=["R1"]),
        ReReadCluster(cluster="PIF_CLKVDD", sheet="Channel_1", entity_name="CH1_PIF_CLKVDD",
                      cell="dac_pif_clkvdd", profile_key=None, refs=["R2"]),
    ]


def test_dialog_rows_and_default_checks(main_window):
    dialog = ReReadDialog(_clusters(), main_window)

    assert dialog._table.rowCount() == 2
    assert all(cb.isChecked() for cb in dialog._checkboxes)
    assert dialog._table.item(0, 1).text() == "PIF_AVDD"
    assert dialog._table.item(0, 2).text() == "Channel_1"
    assert dialog._table.item(0, 3).text() == "CH1_PIF_AVDD"
    assert dialog._table.item(0, 4).text() == "dac_pif_avdd"


def test_selected_rows_reflects_unchecked(main_window):
    dialog = ReReadDialog(_clusters(), main_window)
    dialog._checkboxes[1].setChecked(False)

    rows = dialog.selected_rows()

    assert len(rows) == 1
    assert rows[0].cell == "dac_pif_avdd"


def test_unchecking_all_returns_empty(main_window):
    dialog = ReReadDialog(_clusters(), main_window)
    for cb in dialog._checkboxes:
        cb.setChecked(False)

    assert dialog.selected_rows() == []


def test_first_column_header_is_plain_empty_not_po_header(main_window):
    """Found live 2026-08-31: the checkbox column's header was _("") — gettext
    resolves the empty msgid to the .po FILE HEADER (Project-Id-Version,
    POT-Creation-Date, ...), so the first column's header rendered the .pot
    content. The header must be a plain empty string, never run through _()."""
    dialog = ReReadDialog(_clusters(), main_window)

    header = dialog._table.horizontalHeaderItem(0)
    assert header is not None
    assert header.text() == ""
    assert "Project-Id-Version" not in header.text()
    # The other columns keep their (translated) labels.
    assert dialog._table.horizontalHeaderItem(1).text()
    assert dialog._table.horizontalHeaderItem(4).text()
