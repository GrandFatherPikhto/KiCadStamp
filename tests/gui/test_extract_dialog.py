# tests/gui/test_extract_dialog.py
"""Tests for the standalone (non-modal) ExtractDialog (2026-08-31, plan
extract_dialog_and_hide_existing.md) — the thin QDialog shell that hosts the
single live ExtractDock instance after the Extract dock page was removed from
DetailDock. The dialog itself is intentionally dumb: all extract logic lives in
ExtractDock (covered by test_extract_dock.py); DockHub owns both the widget and
the dialog and wires the routes (covered by test_phase3_wiring.py)."""

from gui.docks.extract import ExtractDock
from gui.docks.extract_dialog import ExtractDialog


def test_dialog_hosts_the_live_extract_dock(main_window):
    dock = ExtractDock(main_window)
    dialog = ExtractDialog(dock, main_window)

    assert dialog.extract_dock is dock
    assert dock.parent() is dialog


def test_dialog_is_non_modal(main_window):
    """Non-modal (show(), never exec()) — the user can keep selecting on the
    board while the dialog is open, and the selection-watch tick keeps feeding
    the same live extract_dock instance inside it."""
    dock = ExtractDock(main_window)
    dialog = ExtractDialog(dock, main_window)

    assert dialog.isModal() is False


def test_dialog_title(main_window):
    dock = ExtractDock(main_window)
    dialog = ExtractDialog(dock, main_window)

    assert dialog.windowTitle() == "Extract"


def test_closing_the_dialog_hides_not_destroys(main_window):
    """Closing via the window X hides the dialog (QDialog default in show()
    mode, no WA_DeleteOnClose) — the instance and its state survive for the
    next open."""
    dock = ExtractDock(main_window)
    dialog = ExtractDialog(dock, main_window)

    dialog.show()
    assert dialog.isVisible()
    dialog.close()
    assert dialog.isHidden()
    # The dock instance is still alive, still parented to the dialog.
    assert dialog.extract_dock is dock
