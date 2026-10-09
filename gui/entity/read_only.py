# gui/entity/read_only.py
"""The read-only mode of the ENTITY page (step 5, T5-4 of
plan_2026_10_09_entity_page; moved here from gui/docks/cell_read_only.py).

An entity whose instance is KNOWABLY absent — a dangling graph, or a board that
does not carry it — or whose record is not in the project file YET opens its page
for READING only: the tabs that TOUCH THE BOARD (Explode, Refs, Anchor) are
disabled and a hint says why, while "Справка" stays usable — its Cell combobox is
EXACTLY how an orphan is fixed (Денис, 09.10.2026).

Per-TAB, never the whole QTabWidget: disabling the widget would take the Cell
combobox with it (the trap Денис named), so the rule works on the WIDGETS the page
hands in (plain `indexOf`, so a tab that is not built yet is simply skipped).
`apply` is the ONE application point, and the page calls it after EVERY load AND
after every refill (a board-snapshot push — gui/entity/page.py
`_refresh_cell_state`). Nothing is remembered between calls, so no second entry
point is needed: the former `reapply` is deleted (Д2 of
plan_2026_10_09_entity_page).

Qt is imported here (this is a widget), but nothing else: the module knows nothing
about cells, entities or the tree, only "these tabs touch the board, turn them
off, and say why".
"""
from __future__ import annotations

from typing import Iterable

from PyQt6.QtWidgets import QLabel, QTabWidget

from kicadstamp.i18n import _


class ReadOnlyGate:
    """Keeps a page's read-only mode in ONE place: the hint + the per-tab disable.

    Constructed with the layout the hint belongs to, applied per load with the
    page's tab widget, the board-touching WIDGETS and the flag."""

    def __init__(self, layout) -> None:
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        layout.addWidget(self.note)

    def apply(self, tabs: QTabWidget, read_only: bool,
              board_widgets: Iterable = (), reason: str = "",
              home_index: int = 0) -> None:
        """Show/restore the page for the current open — the ONE entry point.

        A read-only open disables the BOARD tabs and shows the hint; any other
        open restores them. `board_widgets` are the widgets whose tab touches the
        board (Explode, Refs, Anchor) — a widget that is not a tab (not built, or
        removed) is skipped. `reason` is the hint text (hidden when empty).
        `home_index` is where the view is sent when the CURRENT tab is one of the
        disabled ones ("Справка"), so the user never faces a dead tab.

        Only DISABLING/re-enabling the BOARD tabs happens here; the page has just
        set every tab by its own rules, and the Cell combobox on "Справка" is
        deliberately left alone. Nothing is REMEMBERED between calls — the page
        calls `apply` again with the fresh flag on every load and every push, so
        the deleted `reapply` had no state of its own to own."""
        widgets = tuple(board_widgets)   # a generator must not be consumed twice
        # Read the CURRENT tab BEFORE disabling: Qt itself moves the view off a
        # tab the moment it is disabled (to the nearest enabled one), so asking
        # afterwards would miss the board tab the user was actually on.
        current = tabs.currentWidget()
        was_board = current is not None and current in widgets
        enabled = not read_only
        for widget in widgets:
            index = tabs.indexOf(widget) if widget is not None else -1
            if index >= 0:
                tabs.setTabEnabled(index, enabled)
        if read_only and was_board:
            tabs.setCurrentIndex(home_index)
        self.note.setText(reason or "")
        self.note.setVisible(bool(read_only) and bool(self.note.text()))


def reason_for(state: str, unsaved: bool = False) -> str:
    """The read-only hint of an ENTITY page, by WHY (Денис, 09.10.2026).

    `state` is ``CellChoices.state`` ("orphan_graph" / "orphan_board"); `unsaved`
    is the page's own "the record is not in the file yet" sign and wins when both
    hold. Empty for a healthy ("checked") page — the caller then shows no hint.
    """
    if unsaved:
        return _("this entity is not saved in the project yet — save it first; "
                 "the board tabs stay off until then")
    if state == "orphan_graph":
        return _("this entity's cell is missing from the project — pick a cell "
                 "to fix it")
    if state == "orphan_board":
        return _("this entity's instance is not on the board — pick a fitting "
                 "cell")
    return ""
