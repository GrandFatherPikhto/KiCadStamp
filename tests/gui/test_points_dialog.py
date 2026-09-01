# tests/gui/test_points_dialog.py
"""Tests for the standalone (non-modal) PointsDialog (2026-09-01, plan
plan_2026_09_01_points_dialog.md) — the thin QDialog shell that hosts the
single live PointsDock instance after the Points dock page was removed from
DetailDock. The dialog itself is intentionally dumb: all points logic lives in
PointsDock (covered by test_points_dock.py); DockHub owns both the widget and
the dialog and wires the routes (covered by test_phase3_wiring.py)."""

from gui.docks.points import PointsDock
from gui.docks.points_dialog import PointsDialog


def test_dialog_hosts_the_live_points_dock(main_window):
    dock = PointsDock(main_window)
    dialog = PointsDialog(dock, main_window)

    assert dialog.points_dock is dock
    assert dock.parent() is dialog


def test_dialog_is_non_modal(main_window):
    """Non-modal (show(), never exec()) — the user can keep selecting on the
    board while the dialog is open, and the snapshot-watch tick keeps feeding
    the same live points_dock instance inside it."""
    dock = PointsDock(main_window)
    dialog = PointsDialog(dock, main_window)

    assert dialog.isModal() is False


def test_dialog_title(main_window):
    dock = PointsDock(main_window)
    dialog = PointsDialog(dock, main_window)

    assert dialog.windowTitle() == "Points"


def test_closing_the_dialog_hides_not_destroys(main_window):
    """Closing via the window X hides the dialog (QDialog default in show()
    mode, no WA_DeleteOnClose) — the instance and its state survive for the
    next open."""
    dock = PointsDock(main_window)
    dialog = PointsDialog(dock, main_window)

    dialog.show()
    assert dialog.isVisible()
    dialog.close()
    assert dialog.isHidden()
    # The dock instance is still alive, still parented to the dialog.
    assert dialog.points_dock is dock
