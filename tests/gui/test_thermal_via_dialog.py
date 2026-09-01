# tests/gui/test_thermal_via_dialog.py
"""Tests for the standalone (non-modal) ThermalViaDialog (2026-09-01, plan
plan_2026_09_01_thermal_via_dialog.md) — the thin QDialog shell that hosts the
single live ThermalViaArrayDock instance after the Thermal via dock page was
removed from DetailDock. The dialog itself is intentionally dumb: all thermal
via logic lives in ThermalViaArrayDock (covered by test_thermal_via_dock.py);
DockHub owns both the widget and the dialog and wires the routes (covered by
test_phase3_wiring.py)."""

from gui.docks.thermal_via import ThermalViaArrayDock
from gui.docks.thermal_via_dialog import ThermalViaDialog


def test_dialog_hosts_the_live_thermal_via_dock(main_window):
    dock = ThermalViaArrayDock(main_window)
    dialog = ThermalViaDialog(dock, main_window)

    assert dialog.thermal_via_dock is dock
    assert dock.parent() is dialog


def test_dialog_is_non_modal(main_window):
    """Non-modal (show(), never exec()) — the user can keep selecting on the
    board while the dialog is open, and the snapshot-watch tick keeps feeding
    the same live thermal_via_dock instance inside it."""
    dock = ThermalViaArrayDock(main_window)
    dialog = ThermalViaDialog(dock, main_window)

    assert dialog.isModal() is False


def test_dialog_title(main_window):
    dock = ThermalViaArrayDock(main_window)
    dialog = ThermalViaDialog(dock, main_window)

    assert dialog.windowTitle() == "Thermal via"


def test_closing_the_dialog_hides_not_destroys(main_window):
    """Closing via the window X hides the dialog (QDialog default in show()
    mode, no WA_DeleteOnClose) — the instance and its state survive for the
    next open."""
    dock = ThermalViaArrayDock(main_window)
    dialog = ThermalViaDialog(dock, main_window)

    dialog.show()
    assert dialog.isVisible()
    dialog.close()
    assert dialog.isHidden()
    # The dock instance is still alive, still parented to the dialog.
    assert dialog.thermal_via_dock is dock
