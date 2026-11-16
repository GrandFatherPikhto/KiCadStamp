# gui/docks/trees_menu_actions.py
"""The Tools → Trees menu entry that needs a module of its own.

`gui/main_window.py` is at its Rule-45 ceiling (1052 lines, ratchet
`tests/repo/test_file_size_ceilings.py`), so a NEW entry cannot be spelled out
there any more: it is built here and the menu bar keeps ONE line.

`add_reread_positions_action` also SETS the attribute on the window
(`window.reread_positions_action`) instead of returning it to be assigned: the
assignment line would be too long to fit and the file may not grow. The attribute
name is part of the contract — cells and callers reach the entry through it.

The dock slot is looked up on the ACTION, not captured here: `tree_action`'s
short-lived version of this file captured `self._dock_hub.create_tree` at build
time, which silently broke every cell that monkeypatches the dock AFTER the window
exists (the real dialog opened instead of the double). Late binding is the
contract of a menu entry; it is not traded for a saved line.

Tools → Trees → «Reread positions from board…» itself: ONE read of the whole
forest, then the table that decides it — written to the config buffer only on
Apply, and never to the board (plan_2026_11_16_reread_forest_from_board).
"""
from __future__ import annotations

from PyQt6.QtGui import QAction

from kicadstamp.i18n import _

from .trees_reread_dialog import run_reread_positions


def add_reread_positions_action(window, menu) -> QAction:
    """Add «Reread positions from board…» to `menu` and set it on `window`.

    The action itself travels back into the flow as `trigger`, so it can serve as
    the operation's guard widget (Э2, plan_2026_09_12_busy_indicator)."""
    action = QAction(_("Reread positions from board..."), window)
    action.triggered.connect(
        lambda: run_reread_positions(window, trigger=action))
    menu.addAction(action)
    window.reread_positions_action = action
    return action
