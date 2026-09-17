# tests/gui/test_cell_dialog.py
"""Tests for the standalone (non-modal) CellDialog (2026-09-04, plan
plan_2026_09_04_celldock_to_dialog.md) — the thin QDialog shell that hosts the
single live CellDock instance after the Cells page was removed from
DetailDock. The dialog itself is intentionally dumb: all cell logic lives in
CellDock (covered by test_cell_editor.py); DockHub owns both the widget and
the dialog and wires the routes (covered by test_phase3_wiring.py)."""

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QGuiApplication

from gui import settings
from gui.docks.cell_dialog import CellDialog
from gui.docks.cell_editor import CellDock
from gui.ui_utils import restore_dialog_size


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


def _capped_by_screen(dialog, size):
    """`size` clipped by the available geometry of the screen the dialog will
    appear on — derived from the SAME two calls the production helper makes
    (gui/ui_utils.resize_dialog_within_screen: `dialog.screen()`, else
    `primaryScreen()`, then `availableGeometry()`).

    Spelled out on the test side on purpose: an assertion that states the rule
    is machine-independent, while asserting the raw remembered pair is an
    assumption that holds only while the size fits the screen."""
    screen = dialog.screen() or QGuiApplication.primaryScreen()
    if screen is None:                       # headless without a real screen
        return list(size)
    available = screen.availableGeometry()
    return [min(size[0], available.width()), min(size[1], available.height())]


class _Screen:
    """Just the one call resize_dialog_within_screen makes — no QScreen to fake
    (the same six-line double lives in tests/gui/test_ui_utils.py; test modules
    are not imported from one another)."""

    def __init__(self, width: int, height: int):
        self._available = QRect(0, 0, width, height)

    def availableGeometry(self) -> QRect:
        return self._available


def test_dialog_size_is_remembered_and_restored(main_window):
    """Э3 (plan_2026_09_12_node_dialog_usability): the dialog's size is stored
    in gui/gui_state.json (NOT in the .sexp config) when it is hidden, keyed by
    the class name, and the NEXT instance opens at that size — capped by the
    screen like any other desired size.

    The expectation is the remembered size CLIPPED BY THE SCREEN, not the raw
    pair: the restored size goes through the same cap a constant does ("the
    remembered size is a wish, the screen wins", Э3.2), so on a screen narrower
    than the size the dialog was left at, the next instance is legitimately
    smaller. The raw pair was a machine-dependent assumption — it held only
    while the dialog's own minimumSizeHint() fit the screen, and on Windows it
    did not (1310 px; plan_2026_09_17_dialog_saver_and_cell_dialog_test §1.2).
    fb5103e removed that floor (1310 -> 578 px), which made the failure
    disappear on its own; the assumption is removed HERE so the next layout
    cannot bring it back."""
    dock = CellDock(main_window)
    first = CellDialog(dock, main_window)
    first.resize(640, 480)
    first.show()
    expected = [first.width(), first.height()]
    assert settings.state.get("dialog_size:CellDialog") is None   # nothing yet
    first.hide()
    assert settings.state.get("dialog_size:CellDialog") == expected

    second = CellDialog(dock, main_window)
    assert [second.width(), second.height()] == _capped_by_screen(second,
                                                                 expected)


def test_a_remembered_size_bigger_than_the_screen_comes_back_capped(main_window):
    """Р5 of the plan: a size saved on a big monitor must not hang off a smaller
    screen — the remembered size is a WISH, the screen wins (Э3.2). The guard
    for exactly that rule, through the REAL CellDialog.

    The screen is injected through restore_dialog_size's own `screen=`
    parameter — the seam that exists "so tests do not have to fake a QScreen".
    Patching `CellDialog.screen` instead would hand a fake screen to Qt's own
    internals for the widget's whole life, a much bigger hammer than this rule
    needs. The dialog is NOT shown, so the clipped numbers are exact: a SHOWN
    dialog's width is floored by its own minimumSizeHint(), which would hide the
    cap on any machine whose fonts demand more than the injected width."""
    dock = CellDock(main_window)
    dialog = CellDialog(dock, main_window)
    settings.state.set("dialog_size:CellDialog", [5000, 5000])

    restore_dialog_size(dialog, 720, 600, screen=_Screen(600, 400))

    assert (dialog.width(), dialog.height()) == (600, 400)
