# gui/docks/instances_dialog.py
"""Tools -> "Instances..." dialog (2026-09-02, plan tree_instances P3).

Manages the `tree_instances:` short declarations of ONE template tree: pick a
template (a hand-written trees: entry — a generated instance can NOT be a
template, its geometry is already derived from its own template), edit the
{name, sheet} row list (add/remove), OK writes/updates the section through
config_writer.upsert_tree_instances.

The dialog GENERATES NOTHING and copies nothing — it only edits the short
declarations; materialization into full Tree + Entity records happens at the
NEXT load/Save (config/tree_instances.py::expand_tree_instances), exactly like
every other config section. TreesDock.reload_trees() after OK shows the new
read-only instance tabs.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                             QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)

from kicadstamp.config_writer import upsert_tree_instances
from kicadstamp.i18n import _


class TreeInstancesDialog(QDialog):
    """Modal editor for one template's `tree_instances:` rows.

    Built from an ALREADY-LOADED cfg (the caller loads it, so a broken config
    surfaces before the dialog opens) + the root config path the section is
    written back to."""

    def __init__(self, parent, root_path, cfg):
        super().__init__(parent)
        self._root_path = root_path
        self._cfg = cfg
        self.setWindowTitle(_("Tree instances"))
        self.setMinimumWidth(420)

        instance_names = {ti.name for ti in cfg.tree_instances}
        # A generated instance can't be a template (its geometry is derived
        # from ITS own template) — templates are the hand-written trees.
        self._templates = sorted(t.name for t in cfg.trees
                                 if t.name not in instance_names)
        self._tree_names = {t.name for t in cfg.trees}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_("Template tree (instances are read-only "
                                  "generated copies of it):")))
        self.template_combo = QComboBox()
        self.template_combo.addItems(self._templates)
        layout.addWidget(self.template_combo)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([_("Instance name"), _("Sheet")])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        row_buttons = QHBoxLayout()
        add_btn = QPushButton(_("Add row"))
        add_btn.clicked.connect(self._add_row)
        remove_btn = QPushButton(_("Remove row"))
        remove_btn.clicked.connect(self._remove_row)
        row_buttons.addWidget(add_btn)
        row_buttons.addWidget(remove_btn)
        row_buttons.addStretch(1)
        layout.addLayout(row_buttons)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.template_combo.currentTextChanged.connect(lambda _t: self._load_template())
        self._load_template()

    # ── rows ────────────────────────────────────────────────────────────

    def current_template(self) -> str:
        return self.template_combo.currentText()

    def _load_template(self) -> None:
        """Fill the table with the selected template's existing rows."""
        tpl = self.current_template()
        rows = [ti for ti in self._cfg.tree_instances if ti.template == tpl]
        self.table.setRowCount(len(rows))
        for i, ti in enumerate(rows):
            self._set_row(i, {"name": ti.name, "sheet": ti.sheet})

    def _set_row(self, row: int, entry: dict) -> None:
        for col, key in enumerate(("name", "sheet")):
            item = QTableWidgetItem(str(entry.get(key, "")))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, col, item)

    def _add_row(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_row(row, {"name": "", "sheet": ""})
        self.table.setCurrentCell(row, 0)

    def _remove_row(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def rows(self) -> list:
        """The table's {name, sheet} rows (blank name+sheet rows dropped)."""
        out = []
        for r in range(self.table.rowCount()):
            def _cell(c):
                item = self.table.item(r, c)
                return item.text().strip() if item is not None else ""
            name, sheet = _cell(0), _cell(1)
            if name or sheet:
                out.append({"name": name, "sheet": sheet})
        return out

    # ── validation + write ──────────────────────────────────────────────

    def _validate(self, rows: list):
        """Return a human-readable problem with `rows`, or None when valid."""
        tpl = self.current_template()
        if not tpl:
            return _("No tree is loaded to use as a template.")
        # This template's CURRENT instance names are about to be replaced by
        # this write — reusing one of them (an edit) is fine. Any OTHER
        # existing tree name (a hand-written tree, the template itself, or an
        # instance of a DIFFERENT template) would make the generated tree a
        # duplicate on the next load — reject it.
        replaced = {ti.name for ti in self._cfg.tree_instances
                    if ti.template == tpl}
        seen: set[str] = set()
        for row in rows:
            name, sheet = row.get("name", ""), row.get("sheet", "")
            if not name or not sheet:
                return _("Every instance row needs a non-empty instance name "
                         "and sheet.")
            if name in seen:
                return _("Instance name {name!r} is used twice for template "
                         "{template!r}.").format(name=name, template=tpl)
            seen.add(name)
            if name in self._tree_names and name not in replaced:
                return _("Instance name {name!r} already exists as a tree — "
                         "instance names must stay unique across all trees.")
        return None

    def _apply(self) -> bool:
        """Validate + write the section; True when written (dialog may close)."""
        rows = self.rows()
        problem = self._validate(rows)
        if problem is not None:
            QMessageBox.warning(self, self.windowTitle(), problem)
            return False
        upsert_tree_instances(self._root_path, self.current_template(), rows)
        return True

    def accept(self) -> None:
        if self._apply():
            super().accept()
