# gui/docks/profile_import.py
"""Edit > Import from profile... (2026-08-31, plan_2026_08_31_copy_cell_
entity_from_profile.md) — copy one or more Cell/Entity/Rule from ANOTHER
profile into the CURRENT project BY VALUE (no include: link, no live
reference). The actual copy + dependency closure + collision logic lives in the
core module kicadstamp/config/profile_copy.py; this file is the thin picker
dialog:

  1. choose a source .sexp/.json file,
  2. see every Cell/Entity/Rule in it (name + a recognizable descriptor),
  3. tick the records to import (multi-select, one checkbox per row) and copy
     them all into the current project root in one atomic pass.

On a name collision (or any other refusal) the backend raises BEFORE writing
anything, and the dialog shows the clear message in a QMessageBox — never a
silent fail, never a bare exception with no text.
"""
import logging
from pathlib import Path
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout,
                              QHeaderView, QLabel, QMessageBox, QPushButton,
                              QTableWidget, QTableWidgetItem, QVBoxLayout)

from kicadstamp.config.profile_copy import copy_items, list_importable
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ._common import ERROR_STYLE as _ERROR_STYLE, show_message

logger = logging.getLogger(__name__)

_KIND_LABEL = {"cell": _("Cell"), "entity": _("Entity"), "rule": _("Rule")}


def _summary_many(items: List[dict], result: dict, source_name: str) -> str:
    """Human-readable multi-line success message: the imported records + the
    total dependency cells/points the backend copied for them."""
    lines = [_("Imported from {path}:").format(path=source_name)]
    for item in items:
        lines.append(_("  {kind} {name!r}").format(
            kind=_KIND_LABEL[item["kind"]], name=item["name"]))
    n_cells = len(result["cells"])
    n_points = len(result["points"])
    if n_cells or n_points:
        lines.append(_("({cells} cell(s), {points} point(s) copied)")
                     .format(cells=n_cells, points=n_points))
    return "\n".join(lines)


class ProfileImportDialog(QDialog):
    """Modal picker: source file -> table of importable records (tick the ones
    to import) -> copy all checked records in one atomic pass."""

    def __init__(self, parent, root_path: Path, on_imported: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self._root_path = root_path
        self._on_imported = on_imported
        self._source_path: Optional[Path] = None
        self._rows: List[dict] = []

        self.setWindowTitle(_("Import from profile..."))
        self.resize(560, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Source row: Browse -> file label
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel(_("Source:")))
        self.source_label = QLabel(_("(none)"))
        source_row.addWidget(self.source_label, 1)
        self.browse_button = QPushButton(_("Browse..."))
        self.browse_button.clicked.connect(self._browse)
        source_row.addWidget(self.browse_button)
        layout.addLayout(source_row)

        # Table of importable records — one checkbox per row (multi-select),
        # not a single selection (Denis, 2026-08-31: "множественное выделение,
        # например галочками").
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", _("Type"), _("Name"), _("Info")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        # Buttons
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.import_button = QPushButton(_("Import"))
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._import)
        buttons.addWidget(self.import_button)
        self.close_button = QPushButton(_("Close"))
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    # ── Source loading ───────────────────────────────────────────────────

    def _browse(self) -> None:
        start = str(self._root_path.parent) if self._root_path else ""
        chosen, _filter = QFileDialog.getOpenFileName(
            self, _("Import from profile..."), start, "Config files (*.sexp *.json)")
        if not chosen:
            return
        try:
            self._rows = list_importable(chosen)
        except ValidationError as e:
            QMessageBox.warning(self, _("Import from profile..."), str(e))
            return
        self._source_path = Path(chosen)
        self.source_label.setText(self._source_path.name)
        self._populate_table()
        if not self._rows:
            QMessageBox.information(
                self, _("Import from profile..."),
                _("No importable entries (Cell/Entity/Rule) in this file."))

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._rows))
        for row, item in enumerate(self._rows):
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(_KIND_LABEL[item["kind"]]))
            self.table.setItem(row, 2, QTableWidgetItem(item["name"]))
            self.table.setItem(row, 3, QTableWidgetItem(item.get("info") or ""))
        self.import_button.setEnabled(False)

    # ── Selection / import ───────────────────────────────────────────────

    def _on_item_changed(self, item) -> None:
        if item.column() == 0:
            self.import_button.setEnabled(self._source_path is not None and bool(self._checked_rows()))

    def _checked_rows(self) -> List[dict]:
        if self._source_path is None:
            return []
        out: List[dict] = []
        for row in range(self.table.rowCount()):
            check_item = self.table.item(row, 0)
            if check_item is not None and check_item.checkState() == Qt.CheckState.Checked:
                out.append(self._rows[row])
        return out

    def _import(self) -> None:
        items = self._checked_rows()
        if not items or self._source_path is None:
            return
        try:
            result = copy_items(self._source_path, items, self._root_path,
                                target_root=self._root_path)
        except (ValidationError, OSError) as e:
            QMessageBox.warning(self, _("Import failed"), str(e))
            return
        message = _summary_many(items, result, self._source_path.name)
        show_message(message, "", logger)
        QMessageBox.information(self, _("Import from profile..."), message)
        if self._on_imported is not None:
            self._on_imported()
        self.accept()


def run_import_dialog(main_window) -> None:
    """Edit > Import from profile... entry point (wired in gui/main_window.py).
    Needs a current project root to import INTO — if none is set, tell the
    user and stop (there is nowhere to write the copy)."""
    root_path = main_window.root_metadata_dock.root_path
    if root_path is None:
        show_message(_("Set the project root first."), _ERROR_STYLE, logger)
        return

    def _after_import() -> None:
        # The copy changed the root file's content — refresh the Config tree
        # and broadcast graph_changed so every graph-derived combo re-reads it
        # (same broadcast DockHub wires for the entity docks' saved signals).
        tree = main_window.config_tree_dock
        tree.refresh()
        tree.graph_changed.emit()

    ProfileImportDialog(main_window, root_path, on_imported=_after_import).exec()
