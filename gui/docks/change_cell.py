# gui/docks/change_cell.py
"""ChangeCellDialog — WHICH cell an entity should stand on
(plan_2026_10_09_cells_and_entities, part 2).

The item behind it generalizes the orphan's old "Point to cell…" to ANY cell
entity ("Change cell…"), and the rows come from
``instance_candidates.choose_cells`` via ``change_cell_flow.cell_choices`` — the
SAME rule the Entity page's Cell combobox uses (Денис, 09.10.2026): only FITTING
cells and the entity's CURRENT cell are listed, the rest are not a huge list at
all but ONE grey line under it ("K other cells do not fit", their reasons in the
tooltip). The current cell is always offered; when it does not fit its own row
says so ("current, does not fit: role CAP: 0 of 1").

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
from .instance_candidates import cell_row_label, others_line, others_tooltip

_GREY = QColor("#888888")


class ChangeCellDialog(QDialog):
    """Pick the cell of an entity. Outcome via :meth:`result_data`."""

    def __init__(self, parent, cells: Sequence, *, orphan: bool = False,
                 others=()) -> None:
        super().__init__(parent)
        self._orphan = bool(orphan)
        # The cells that do not fit and are NOT the current one — never rows, only
        # the one grey line below the list (and its tooltip).
        self._others = tuple(others or ())
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

        # The ONE grey line: how many cells were left out, reasons in the tooltip.
        self._others_label = QLabel(others_line(self._others))
        self._others_label.setObjectName("change_cell_others")
        self._others_label.setWordWrap(True)
        self._others_label.setStyleSheet("color: #888888;")
        self._others_label.setToolTip(others_tooltip(self._others))
        self._others_label.setVisible(bool(self._others))
        layout.addWidget(self._others_label)

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
        """One offered cell: fitting (selectable) or the entity's OWN cell that
        does not fit (greyed, its row says "current, does not fit: <reason>"). An
        orphan offers EVERY cell, selectable — the fit was never checked."""
        selectable = bool(cand.fits) or self._orphan
        item = QListWidgetItem(cell_row_label(cand))
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
