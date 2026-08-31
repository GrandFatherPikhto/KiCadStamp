# gui/docks/profile_import.py
"""Edit > Import from profile... (2026-08-31, plan_2026_08_31_copy_cell_
entity_from_profile.md) — copy one Cell/Entity/Rule from ANOTHER profile into
the CURRENT project BY VALUE (no include: link, no live reference). The actual
copy + dependency closure + collision logic lives in the core module
kicadstamp/config/profile_copy.py; this file is the thin picker dialog:

  1. choose a source .sexp/.json file,
  2. see every Cell/Entity/Rule in it (name + a recognizable descriptor),
  3. pick one and copy it into the current project root.

On a name collision (or any other refusal) the backend raises BEFORE writing
anything, and the dialog shows the clear message in a QMessageBox — never a
silent fail, never a bare exception with no text.
"""
import logging
from pathlib import Path
from typing import Callable, List, Optional

from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout,
                              QHeaderView, QLabel, QMessageBox, QPushButton,
                              QTableWidget, QTableWidgetItem, QVBoxLayout)

from kicadstamp.config.profile_copy import (
    copy_cell, copy_entity, copy_rule, list_importable)
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ._common import ERROR_STYLE as _ERROR_STYLE, show_message

logger = logging.getLogger(__name__)

_KIND_LABEL = {"cell": _("Cell"), "entity": _("Entity"), "rule": _("Rule")}


def _copy_one(kind: str, source_path: Path, name: str, target_path: Path) -> List[str]:
    """Dispatch one picker row to the matching backend copy_* function. Returns
    the copied dependency names (cells for cell/entity; cells+points for rule)."""
    if kind == "cell":
        return copy_cell(source_path, name, target_path, target_root=target_path)
    if kind == "entity":
        return copy_entity(source_path, name, target_path, target_root=target_path)
    return copy_rule(source_path, name, target_path, target_root=target_path)


def _summary(kind: str, name: str, copied: List[str]) -> str:
    """Human-readable success message: the imported record + its closure."""
    deps = [n for n in copied]
    if not deps:
        return _("Imported {kind} {name!r}").format(kind=_KIND_LABEL[kind], name=name)
    return _("Imported {kind} {name!r} with dependencies: {deps}").format(
        kind=_KIND_LABEL[kind], name=name, deps=", ".join(deps))


class ProfileImportDialog(QDialog):
    """Modal picker: source file -> table of importable records -> copy one."""

    def __init__(self, parent, root_path: Path, on_imported: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self._root_path = root_path
        self._on_imported = on_imported
        self._source_path: Optional[Path] = None
        self._rows: List[dict] = []

        self.setWindowTitle(_("Import from profile..."))
        self.resize(520, 380)

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

        # Table of importable records
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([_("Type"), _("Name"), _("Info")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._selection_changed)
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
            self.table.setItem(row, 0, QTableWidgetItem(_KIND_LABEL[item["kind"]]))
            self.table.setItem(row, 1, QTableWidgetItem(item["name"]))
            self.table.setItem(row, 2, QTableWidgetItem(item.get("info") or ""))
        self.import_button.setEnabled(False)

    # ── Selection / import ───────────────────────────────────────────────

    def _selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        self.import_button.setEnabled(self._source_path is not None and bool(rows))

    def _selected_row(self) -> Optional[dict]:
        rows = self.table.selectionModel().selectedRows()
        if not rows or self._source_path is None:
            return None
        return self._rows[rows[0].row()]

    def _import(self) -> None:
        item = self._selected_row()
        if item is None:
            return
        try:
            copied = _copy_one(item["kind"], self._source_path, item["name"], self._root_path)
        except (ValidationError, OSError) as e:
            QMessageBox.warning(self, _("Import failed"), str(e))
            return
        show_message(_summary(item["kind"], item["name"], copied), "", logger)
        QMessageBox.information(
            self, _("Import from profile..."),
            _summary(item["kind"], item["name"], copied))
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
