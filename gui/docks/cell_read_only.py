# gui/docks/cell_read_only.py
"""The read-only mode of the cell page (3б, plan_2026_10_05_entities_under_cells).

A cell with NO entity is a second kind of orphan; when nothing places it either
it is a DRAWING, and the cell page opens for READING only: the editable page is
disabled and a hint says why. The rule is small, but it lived inside the
2 000-line `gui/docks/cell_anchor_view.py` — and rule 45 (a giant only shrinks)
moved it out, leaving the giant the WIRING of the flag (one construction, one
call per load).

The note is its own widget, added to whatever layout the page hands over, so the
only thing the page keeps is `self._read_only_gate`. `apply` is the ONE
application point, called after EVERY load — a later read-write open has to
restore the page, and two call sites for one rule is how the two drift apart.

Qt is imported here (this is a widget), but nothing else: the module knows
nothing about cells, entities or the tree, only about "this page is read-only".
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QTabWidget

from kicadstamp.i18n import _


class ReadOnlyGate:
    """Keeps a page's read-only mode in ONE place: the hint + the disable.

    Constructed with the layout the hint belongs to, applied per load with the
    page's tab widget and the flag the caller decided."""

    def __init__(self, layout) -> None:
        self.note = QLabel(_("create an entity to edit this cell"))
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        layout.addWidget(self.note)
        # What the last `apply` was given — `reapply` needs no second flag on the
        # page, and ONE place keeps owning the rule.
        self._tabs = None
        self._board_buttons: tuple = ()
        self._read_only = False

    def apply(self, tabs: QTabWidget, read_only: bool,
              board_buttons: tuple = ()) -> None:
        """Show/restore the editable page for the current open.

        A read-only open disables the tabs (nothing stages) and shows the hint;
        any other open restores both.

        `board_buttons` (2б, п.3): a read-only open ALSO turns the page's board
        buttons off EXPLICITLY. They live inside the tabs, so switching the tab
        off already takes them away — but a guard that watched only the tab
        widget would be green for the wrong reason, and the rule Denis asked for
        is "these buttons are OFF for a cell nobody places" (a cell with no
        entity and no placer is a drawing: there is no instance to read).

        Only DISABLING happens here. On a normal open the page has just set every
        button by its own rules (`_reload_form` runs immediately before this
        call, and this gate is applied last on purpose), so re-enabling anything
        here would undo a deliberate off (a marker that is not on the board, a
        cell with no components)."""
        self._tabs = tabs
        self._board_buttons = tuple(board_buttons)
        self._read_only = bool(read_only)
        tabs.setEnabled(not self._read_only)
        self.note.setVisible(self._read_only)
        if self._read_only:
            self._disable_buttons()

    def reapply(self) -> None:
        """Run the remembered rule again — for a REFILL that did not come through
        `load_entry` (2в, п.6: `set_root_path` reloads the form too, and its own
        `_reload_form` re-enables every board button).

        The gate keeps what it was last applied with, so the page needs no second
        flag of its own and the rule still lives in ONE place. A no-op before the
        first `apply` (nothing to remember) and on a read-write page."""
        if self._tabs is None or not self._read_only:
            return
        self._tabs.setEnabled(False)
        self.note.setVisible(True)
        self._disable_buttons()

    def _disable_buttons(self) -> None:
        for button in self._board_buttons:
            if button is not None:
                button.setEnabled(False)
