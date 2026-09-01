# tests/gui/test_chain_dialog.py
"""Tests for the standalone (non-modal) ChainDialog (2026-09-01, plan
plan_2026_09_01_rules_to_chains.md) — the thin QDialog shell that hosts the
single live ChainDock instance after the Rules dock page was removed from
DetailDock. The dialog itself is intentionally dumb: all chain logic lives in
ChainDock (covered by test_chain_dock.py); DockHub owns both the widget and
the dialog and wires the routes (covered by test_phase3_wiring.py)."""

from gui.docks.chain import ChainDock
from gui.docks.chain_dialog import ChainDialog


def test_dialog_hosts_the_live_chain_dock(main_window):
    dock = ChainDock(main_window)
    dialog = ChainDialog(dock, main_window)

    assert dialog.chain_dock is dock
    assert dock.parent() is dialog


def test_dialog_is_non_modal(main_window):
    """Non-modal (show(), never exec()) — the user can keep selecting on the
    board while the dialog is open, and the snapshot-watch tick keeps feeding
    the same live chain_dock instance inside it."""
    dock = ChainDock(main_window)
    dialog = ChainDialog(dock, main_window)

    assert dialog.isModal() is False


def test_dialog_title(main_window):
    dock = ChainDock(main_window)
    dialog = ChainDialog(dock, main_window)

    assert dialog.windowTitle() == "Chain"


def test_closing_the_dialog_hides_not_destroys(main_window):
    """Closing via the window X hides the dialog (QDialog default in show()
    mode, no WA_DeleteOnClose) — the instance and its state survive for the
    next open."""
    dock = ChainDock(main_window)
    dialog = ChainDialog(dock, main_window)

    dialog.show()
    assert dialog.isVisible()
    dialog.close()
    assert dialog.isHidden()
    # The dock instance is still alive, still parented to the dialog.
    assert dialog.chain_dock is dock
