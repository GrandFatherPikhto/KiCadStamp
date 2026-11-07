# gui/docks/cell_editor_dialogs.py
"""The two small dialogs of the Cell editor: the "Add selected copper" import
PREVIEW and the "Copy placement from cell..." donor picker.

Moved VERBATIM out of gui/docks/cell_editor.py on 07.11 (plan
plan_2026_11_07_dialog_sizes_and_row_shift, part Б): that module is a giant that
may only shrink (rule 45), and a dialog is a window with its own size to
remember. `gui/docks/cell_editor.py` re-exports both classes, so
`gui.docks.cell_editor._ImportPreviewDialog` (which the cells patch) keeps
working unchanged.
"""
from __future__ import annotations

from typing import List

from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                             QDialogButtonBox, QFormLayout, QHeaderView,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from kicadstamp.i18n import _

from ._common import configure_searchable

class _ImportPreviewDialog(QDialog):
    """Read-only "these NEW via/track records will be appended" preview for one
    ImportPlan. One Kind/Position/Net table; Apply appends the listed records
    to the dock's _vias/_tracks — the actual extend happens in the caller AFTER
    this dialog returns Accepted, so mutation + autostage stay in the dock's
    normal path."""

    def __init__(self, rows: List[List[str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Add selected copper"))
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels([_("Kind"), _("Position"), _("Net")])
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for row_index, row in enumerate(rows):
            table.insertRow(row_index)
            for col, value in enumerate(row):
                table.setItem(row_index, col, QTableWidgetItem(str(value)))
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox()
        apply_button = buttons.addButton(_("Apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        apply_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class _CopyPlacementDialog(QDialog):
    """MINIMAL donor picker for one "Copy placement from cell..." (2026-09-06,
    Denis: "В диалоге максимум что должно быть — комбобокс"): a searchable
    combobox of the cells that FIT the target by role set plus Copy/Cancel —
    nothing else, no preview tables. Writes nothing itself; the caller applies
    the plan after this returns Accepted (mutation + autostage stay in the
    dock's normal path)."""

    def __init__(self, candidates: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Copy placement from cell"))
        self.resize(460, 130)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.source_combo = QComboBox()
        configure_searchable(self.source_combo)
        form.addRow(_("Copy from cell:"), self.source_combo)
        layout.addLayout(form)
        buttons = QDialogButtonBox()
        self.copy_button = buttons.addButton(
            _("Copy"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.copy_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        names = sorted(candidates)
        if names:
            self.source_combo.addItems(names)
            self.source_combo.setCurrentIndex(0)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self._on_source_changed()

    def _on_source_changed(self) -> None:
        self.copy_button.setEnabled(bool(self.source_combo.currentText().strip()))

    def source(self) -> str:
        """The chosen donor cell's name."""
        return self.source_combo.currentText().strip()
