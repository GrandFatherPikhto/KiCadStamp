# gui/docks/add_entities.py
"""AddEntitiesDialog — the batch form behind the Config tree's context-menu
item "Add entities…" on a CELLS leaf (plan_2026_10_09_cells_and_entities,
part 1, Денис 09.10: "спиц питания будет много", one by one is pointless).

It sits NEXT TO "Create entity", it does not replace it: that one still makes a
single record with hand-typed Cluster/Sheet, this one makes one record for EVERY
checked instance of the cell in one go.

The rows are the instances of the board that FIT the cell —
``instance_candidates`` (gui/docks/instance_candidates.py) is the ONE rule and
this dialog never restates it:

  * a fitting, free instance gets a checkbox and an editable name;
  * a fitting instance that is already an entity ("taken") is shown GREYED with
    "already: <name>" and NO checkbox — it is offered so the user sees it is
    accounted for, never offered to be made twice;
  * a NON-fitting instance is not a row at all: it is counted in the line above
    the table ("K instances lack roles") — counted, never silently lost.

The dialog owns NO filesystem and NO board: it is handed the candidates and the
set of entity names already used across the include graph, and hands back a list
of (name, cluster, sheet) rows the CALLER writes in one working-set edit. Its
own checks (name required / already used / duplicated inside the table) are a
convenience; the caller re-checks against the graph once the dialog closes.

No QMessageBox on purpose (rule 43): a name conflict paints the row red and
disables OK, and a red hint line says which name clashes — the dialog the user
is working in never gets an "OK" window thrown on top of it.
"""
from __future__ import annotations

import dataclasses
from collections import Counter
from typing import Iterable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)

from kicadstamp.i18n import _

from ..ui_utils import persist_dialog_size, restore_dialog_size

# Column order: checkbox | name | cluster | sheet | state.
COL_CHECK, COL_NAME, COL_CLUSTER, COL_SHEET, COL_STATE = range(5)

_GREY = QColor("#888888")
_RED = QColor("#a00000")


@dataclasses.dataclass(frozen=True)
class ChosenEntity:
    """One checked row as the caller stores it."""
    name: str
    cluster: str
    sheet: Optional[str] = None


def default_entity_names(candidates: Iterable) -> dict:
    """{candidate -> default entity name} for the candidates given.

    The Cluster tag in lower case (``FPGA_VCCIO_139`` -> ``fpga_vccio_139``),
    and when the SAME cluster stands on several sheets of the batch, the sheet
    is appended (``<cluster>_<sheet>``) — one cluster on one sheet keeps the
    bare slug, so the common case reads cleanly. The slug rule itself is the
    project's ONE (``tree_from_selection.cluster_cell_name``), never a copy.

    Keyed by the candidate OBJECT (frozen dataclass — hashable), so the dialog
    can look a default up by row identity.
    """
    from .tree_from_selection import cluster_cell_name

    cands = list(candidates or ())
    per_cluster = Counter(c.cluster for c in cands)
    out: dict = {}
    for c in cands:
        base = cluster_cell_name(c.cluster)
        if per_cluster[c.cluster] > 1 and c.sheet:
            base = "{base}_{sheet}".format(
                base=base, sheet=cluster_cell_name(c.sheet))
        out[c] = base
    return out


class AddEntitiesDialog(QDialog):
    """The batch table. Outcome via :meth:`result_data` after Accepted —
    the pattern every dialog of this project follows (see create_entity.py)."""

    def __init__(self, parent, cell_name: str, candidates: Iterable,
                 existing_names: Iterable) -> None:
        super().__init__(parent)
        self._cell_name = cell_name
        self._existing_names = set(existing_names or ())
        self._fitting = [c for c in (candidates or ()) if c.fits]
        self._skipped = [c for c in (candidates or ()) if not c.fits]
        self._defaults = default_entity_names(self._fitting)
        self._loading = True

        self.setWindowTitle(_("Add entities"))
        self.setMinimumWidth(560)
        restore_dialog_size(self)
        persist_dialog_size(self)

        layout = QVBoxLayout(self)

        self._counter = QLabel("")
        self._counter.setObjectName("add_entities_counter")
        layout.addWidget(self._counter)

        self._table = QTableWidget(len(self._fitting), 5, self)
        self._table.setObjectName("add_entities_table")
        self._table.setHorizontalHeaderLabels(
            ["", _("Name"), _("Cluster"), _("Sheet"), _("State")])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(
            self._table.EditTrigger.DoubleClicked
            | self._table.EditTrigger.SelectedClicked
            | self._table.EditTrigger.EditKeyPressed)
        layout.addWidget(self._table)

        self._hint = QLabel("")
        self._hint.setObjectName("add_entities_hint")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #a00000;")
        layout.addWidget(self._hint)

        row = QHBoxLayout()
        self._all_button = QPushButton(_("All"))
        self._all_button.setObjectName("add_entities_all")
        self._none_button = QPushButton(_("None"))
        self._none_button.setObjectName("add_entities_none")
        self._all_button.clicked.connect(lambda: self._set_all(True))
        self._none_button.clicked.connect(lambda: self._set_all(False))
        row.addWidget(self._all_button)
        row.addWidget(self._none_button)
        row.addStretch(1)
        layout.addLayout(row)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                         | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._fill_table()
        self._loading = False
        self._table.itemChanged.connect(self._on_item_changed)
        self._revalidate()

    # ── Building ────────────────────────────────────────────────────────

    def _fill_table(self) -> None:
        for i, cand in enumerate(self._fitting):
            self._table.setItem(i, COL_CHECK, self._check_item(cand))
            self._table.setItem(i, COL_NAME, self._name_item(cand))
            for col, text in ((COL_CLUSTER, cand.cluster),
                              (COL_SHEET, cand.sheet or _("(no sheet)")),
                              (COL_STATE, self._state_text(cand))):
                item = QTableWidgetItem(str(text))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if cand.taken:
                    item.setForeground(QBrush(_GREY))
                self._table.setItem(i, col, item)
        self._table.resizeColumnsToContents()
        if self._skipped:
            self._counter.setText(_("{count} instances lack roles").format(
                count=len(self._skipped)))
        else:
            self._counter.setText("")

    def _check_item(self, cand) -> QTableWidgetItem:
        item = QTableWidgetItem()
        if cand.taken:
            # Not checkable: it is offered only so the user SEES it is taken.
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        else:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled
                          | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
        return item

    def _name_item(self, cand) -> QTableWidgetItem:
        item = QTableWidgetItem(self._defaults.get(cand, ""))
        if cand.taken:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setForeground(QBrush(_GREY))
        else:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
        return item

    @staticmethod
    def _state_text(cand) -> str:
        if cand.taken:
            return _("already: {name}").format(name=cand.entity_name)
        return ""

    # ── Filling / reading ───────────────────────────────────────────────

    def _is_checkable(self, row: int) -> bool:
        item = self._table.item(row, COL_CHECK)
        return bool(item is not None
                    and item.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def _is_checked(self, row: int) -> bool:
        item = self._table.item(row, COL_CHECK)
        return bool(item is not None
                    and item.checkState() == Qt.CheckState.Checked)

    def _set_all(self, checked: bool) -> None:
        self._loading = True
        try:
            for row in range(self._table.rowCount()):
                if self._is_checkable(row):
                    self._table.item(row, COL_CHECK).setCheckState(
                        Qt.CheckState.Checked if checked
                        else Qt.CheckState.Unchecked)
        finally:
            self._loading = False
        self._revalidate()

    def _on_item_changed(self, _item) -> None:
        if self._loading:
            return
        self._revalidate()

    def checked_rows(self) -> List[ChosenEntity]:
        """The CHECKED rows, in table order, name stripped."""
        out: List[ChosenEntity] = []
        for row in range(self._table.rowCount()):
            if not self._is_checked(row):
                continue
            cand = self._fitting[row]
            name_item = self._table.item(row, COL_NAME)
            name = (name_item.text() if name_item is not None else "").strip()
            out.append(ChosenEntity(name=name, cluster=cand.cluster,
                                    sheet=cand.sheet))
        return out

    # ── Validation (red rows + OK gate; no QMessageBox, rule 43) ─────────

    def _problems(self) -> dict:
        """{row: reason} for every CHECKED row that cannot be written — an
        empty name, a name already used in the graph, or a name used twice
        inside this very table."""
        names: dict = {}
        counts: Counter = Counter()
        for r in range(self._table.rowCount()):
            if not self._is_checked(r):
                continue
            item = self._table.item(r, COL_NAME)
            name = (item.text() if item is not None else "").strip()
            names[r] = name
            if name:
                counts[name] += 1
        problems: dict = {}
        for r, name in names.items():
            if not name:
                problems[r] = _("Name is required.")
            elif name in self._existing_names:
                problems[r] = _(
                    "An entity named {name!r} already exists.").format(name=name)
            elif counts[name] > 1:
                problems[r] = _(
                    "Name {name!r} is used twice in the list.").format(name=name)
        return problems

    def _revalidate(self) -> None:
        problems = self._problems()
        for r in range(self._table.rowCount()):
            name_item = self._table.item(r, COL_NAME)
            if name_item is None or self._fitting[r].taken:
                continue
            name_item.setForeground(QBrush(_RED) if r in problems else QBrush())
        checked_any = any(self._is_checked(r)
                          for r in range(self._table.rowCount()))
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(checked_any and not problems)
        if problems:
            self._hint.setText(next(iter(problems.values())))
        elif not checked_any:
            self._hint.setText(_("Check the instances to add."))
        else:
            self._hint.setText("")

    # ── Result ──────────────────────────────────────────────────────────

    def result_data(self) -> List[ChosenEntity]:
        """The checked rows — what the caller writes in ONE working-set edit."""
        return self.checked_rows()

    def accept(self) -> None:
        self._revalidate()
        if self._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
            super().accept()
