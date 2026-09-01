# gui/docks/reead_dialog.py
"""
ReReadDialog — "Tools -> Re-read selected..." (2026-08-31, plan
reead_selected_dialog.md).

A modal dialog that lists the FULLY-selected Clusters of the current board
selection (each with its sheet instance, the placing Entity and the Cell it
would be re-read into), with a checkbox per row (on by default). On OK,
ExtractDock reads back the checked rows via selected_rows() and batch re-reads
them (see ExtractDock.re_read_selected / _run_reead_selected).

The dialog is deliberately thin: the cluster/entity/cell/profile mapping is
pure logic in gui/docks/reead.py (tested without Qt); this file only renders
the rows and collects the checked ones.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHeaderView,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from kicadstamp.i18n import _

from .reead import ReReadCluster


def _single_line(text: object) -> str:
    """Collapse any whitespace (including newlines) to single spaces — a table
    cell must never render raw multi-line/file content (live 2026-08-31: a
    stray .pot header leaked into the first row)."""
    return " ".join(str(text).split())


class ReReadDialog(QDialog):
    """Modal dialog listing fully-selected Clusters with checkboxes."""

    def __init__(self, clusters: list[ReReadCluster], main_window):
        super().__init__(main_window)
        self.setWindowTitle(_("Re-read selected"))
        self.setObjectName("reead_dialog")
        self._clusters = list(clusters)

        layout = QVBoxLayout(self)
        self._table = QTableWidget(len(self._clusters), 5)
        # The checkbox column's header is a PLAIN empty string — never _(""):
        # gettext stores the .po file header under the empty msgid, so _("")
        # renders "Project-Id-Version: ... POT-Creation-Date: ..." as the first
        # column's header (found live 2026-08-31).
        self._table.setHorizontalHeaderLabels(
            ["", _("Cluster"), _("Sheet"), _("Entity"), _("Cell")])
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._checkboxes: list[QCheckBox] = []
        for row, c in enumerate(self._clusters):
            cb = QCheckBox()
            cb.setChecked(True)
            # An explicit (empty) item under the checkbox cell too — defensive:
            # a failed setCellWidget must never leak unrelated text into the
            # first column. All data cells are rendered single-line via
            # _single_line.
            self._table.setItem(row, 0, QTableWidgetItem(""))
            self._table.setCellWidget(row, 0, cb)
            self._checkboxes.append(cb)
            for col, text in enumerate(
                    (c.cluster, c.sheet or "-", c.entity_name or "-", c.cell), 1):
                self._table.setItem(row, col, QTableWidgetItem(_single_line(text)))
        layout.addWidget(self._table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_rows(self) -> list[ReReadCluster]:
        """The rows the user left checked, in dialog order."""
        return [c for c, cb in zip(self._clusters, self._checkboxes) if cb.isChecked()]
