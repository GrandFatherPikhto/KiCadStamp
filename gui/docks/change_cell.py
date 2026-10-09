# gui/docks/change_cell.py
"""ChangeCellDialog — WHICH cell an entity should stand on
(plan_2026_10_09_cells_and_entities, part 2).

The item behind it generalizes the orphan's old "Point to cell…" to ANY cell
entity ("Change cell…"), and the rows are ``cell_candidates`` — the SAME
function "Add entities…" uses, in its reverse direction: fitting cells first
(selectable), the rest greyed out with the reason that decided it ("role CAP:
1 of 2"), so the user SEES why a cell is out of reach instead of meeting an
empty list. No cell is ever hidden: an unfitting one is offered greyed.

An ORPHAN — an entity whose instance is not on the board at all (no cluster, or
nothing of it in the snapshot) — cannot have its fit checked: EVERY cell is
offered and a yellow line says so out loud ("no instance on the board — fit not
checked"). That is a warning, never a refusal (the user may legitimately point a
not-yet-placed entity at a cell).

Qt only here; the list, the reasons and the "is this instance" side live in
gui/docks/instance_candidates.py.
"""
from __future__ import annotations

from typing import Optional, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget,
                             QListWidgetItem, QVBoxLayout)

from kicadstamp.i18n import _

from ..ui_utils import persist_dialog_size, restore_dialog_size

_GREY = QColor("#888888")


class ChangeCellDialog(QDialog):
    """Pick the cell of an entity. Outcome via :meth:`result_data`."""

    def __init__(self, parent, cells: Sequence, *, orphan: bool = False) -> None:
        super().__init__(parent)
        self._orphan = bool(orphan)
        self.setWindowTitle(_("Change cell"))
        self.setMinimumWidth(420)
        restore_dialog_size(self)
        persist_dialog_size(self)

        layout = QVBoxLayout(self)
        if self._orphan:
            warning = QLabel(_("no instance on the board — fit not checked"))
            warning.setObjectName("change_cell_fit_warning")
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #a60;")
            layout.addWidget(warning)

        self._list = QListWidget(self)
        self._list.setObjectName("change_cell_list")
        layout.addWidget(self._list)
        for cand in cells or ():
            self._add_row(cand)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                         | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._list.currentItemChanged.connect(lambda *_: self._revalidate())

        if self._list.count():
            # Preselect the first SELECTABLE row (a fitting cell), so a plain OK
            # works when there is only one choice.
            for i in range(self._list.count()):
                if self._list.item(i).flags() & Qt.ItemFlag.ItemIsSelectable:
                    self._list.setCurrentRow(i)
                    break
        self._revalidate()

    # ── Rows ────────────────────────────────────────────────────────────

    def _add_row(self, cand) -> None:
        """A fitting cell (selectable) or a greyed one with its reason. An
        orphan offers EVERY cell: the fit was never checked."""
        selectable = bool(cand.fits) or self._orphan
        label = cand.name if selectable and not cand.reason else (
            "{name} — {reason}".format(name=cand.name, reason=cand.reason)
            if cand.reason else cand.name)
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, cand.name)
        if not selectable:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setForeground(QBrush(_GREY))
        self._list.addItem(item)

    # ── Result ──────────────────────────────────────────────────────────

    def result_data(self) -> Optional[str]:
        item = self._list.currentItem()
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsSelectable):
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _revalidate(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(bool(self.result_data()))

    def accept(self) -> None:
        self._revalidate()
        if self._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
            super().accept()
