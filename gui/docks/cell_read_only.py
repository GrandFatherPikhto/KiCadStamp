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

    def apply(self, tabs: QTabWidget, read_only: bool) -> None:
        """Show/restore the editable page for the current open.

        A read-only open disables the tabs (nothing stages) and shows the hint;
        any other open restores both. Never touches anything else on the page —
        what is INSIDE the tabs is the tabs' business."""
        tabs.setEnabled(not read_only)
        self.note.setVisible(read_only)
