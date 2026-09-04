# tests/gui/test_cell_dialog.py
"""Tests for the standalone (non-modal) CellDialog (2026-09-04, plan
plan_2026_09_04_celldock_to_dialog.md) — the thin QDialog shell that hosts the
single live CellDock instance after the Cells page was removed from
DetailDock. The dialog itself is intentionally dumb: all cell logic lives in
CellDock (covered by test_cell_editor.py); DockHub owns both the widget and
the dialog and wires the routes (covered by test_phase3_wiring.py)."""

from gui.docks.cell_dialog import CellDialog
from gui.docks.cell_editor import CellDock


def test_dialog_hosts_the_live_cell_dock(main_window):
    dock = CellDock(main_window)
    dialog = CellDialog(dock, main_window)

    assert dialog.cell_dock is dock
    assert dock.parent() is dialog


def test_dialog_is_non_modal(main_window):
    """Non-modal (show(), never exec()) — the user can keep selecting on the
    board while the dialog is open, and the snapshot-watch tick keeps feeding
    the same live cell_dock instance inside it."""
    dock = CellDock(main_window)
    dialog = CellDialog(dock, main_window)

    assert dialog.isModal() is False


def test_dialog_title(main_window):
    dock = CellDock(main_window)
    dialog = CellDialog(dock, main_window)

    assert dialog.windowTitle() == "Edit Cell"


def test_closing_the_dialog_hides_not_destroys(main_window):
    """Closing via the window X hides the dialog (QDialog default in show()
    mode, no WA_DeleteOnClose) — the instance and its state survive for the
    next open."""
    dock = CellDock(main_window)
    dialog = CellDialog(dock, main_window)

    dialog.show()
    assert dialog.isVisible()
    dialog.close()
    assert dialog.isHidden()
    # The dock instance is still alive, still parented to the dialog.
    assert dialog.cell_dock is dock
