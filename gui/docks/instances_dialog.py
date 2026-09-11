# gui/docks/instances_dialog.py
"""Tools -> "Instances..." dialog (2026-09-02, plan tree_instances P3; cluster
column 2026-09-03, plan tree_instances_cluster; Rotation + Anchor columns
2026-09-12, plan_2026_09_12_tree_instance_own_place §И.5).

Manages the `tree_instances:` short declarations of ONE template tree: pick a
template (a hand-written trees: entry — a generated instance can NOT be a
template, its geometry is already derived from its own template), edit the
{name, sheet, cluster?, rotation?, anchor?} row list (add/remove), OK
writes/updates the section through config_writer.upsert_tree_instances.
`cluster` is the OPTIONAL override column — blank means "not set" and the key is
omitted on save. `rotation`/`anchor` are the OPTIONAL own PLACE axes (§И.2): a
blank rotation cell and an unset anchor cell both mean "inherit the template's
own", and the key is omitted on save — never written as null/"".

The Anchor cell is a summary + a "…" button that opens the SHARED
AnchorFormWidget (gui/docks/trees_dock.py) — the same form the tree's own anchor
tab uses, deliberately NOT a second anchor form (the plan's §И.5 requirement;
the form is modal-agnostic, so embedding it costs no rework of trees_dock).

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
                             QVBoxLayout, QWidget)

from kicadstamp.anchor_graph import build_records
from kicadstamp.config_writer import upsert_tree_instances
from kicadstamp.i18n import _
from kicadstamp.trees import anchor_from_dict, anchor_to_dict

# Column indices (one place, so the header list and every reader agree).
_COL_NAME, _COL_SHEET, _COL_CLUSTER, _COL_ROTATION, _COL_ANCHOR = range(5)

# The record kinds an anchor's (ref ...) mode may name — the same set the
# AnchorFormWidget's kind filter offers (trees_dock._PLACEABLE_KINDS).
_ANCHOR_REF_KINDS = ("placement", "chain", "coordinate", "point", "clone")

# Returned by _edit_anchor() when the user cancelled — distinct from None,
# which means "inherit the template's anchor" (an explicit user choice).
_CANCELLED = object()


def _format_rotation(value: float | None) -> str:
    """A rotation cell's text: blank when not set, otherwise the number without
    a trailing ".0" (90.0 -> "90")."""
    return "" if value is None else f"{value:g}"


def _anchor_summary(anchor: dict | None) -> str:
    """One-line summary of a declaration's own anchor for the Anchor cell — the
    GRAMMAR's own keywords (origin / ref / external / self / role / point) plus
    the names it carries, deliberately NOT translated prose: it is the same
    vocabulary the config file and the tree anchor form use. "—" when the row
    has no own place (it inherits the template's)."""
    if not anchor:
        return "—"
    if anchor.get("origin"):
        text = "origin"
    elif anchor.get("self") is not None:
        ref = (anchor.get("self") or {}).get("ref")
        text = f"self {ref}" if ref else "self"
    elif anchor.get("ref") is not None:
        text = ("external " if anchor.get("external") else "ref ") + str(anchor["ref"])
    elif anchor.get("point") is not None:
        text = f"point {anchor['point']}"
    else:
        text = f"role {anchor.get('role')}"
        if anchor.get("sheet"):
            text += f" / {anchor['sheet']}"
    if anchor.get("shift"):
        text += " + shift"
    return text


class _AnchorCell(QWidget):
    """The Anchor column's cell widget: a read-only summary label + the "…"
    button that opens the SHARED AnchorFormWidget.

    The anchor dict lives HERE (not in a per-row side table), so it moves with
    the row automatically when the table inserts/removes rows — an index-keyed
    store would silently drift."""

    def __init__(self, dialog: "TreeInstancesDialog", anchor: dict | None = None):
        super().__init__()
        self._dialog = dialog
        self.anchor: dict | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        lay.addWidget(self.label, 1)
        self.button = QPushButton("…")
        self.button.setFixedWidth(28)
        self.button.setToolTip(_("Edit this instance's own place (anchor)"))
        self.button.clicked.connect(self._edit)
        lay.addWidget(self.button)
        self.set_anchor(anchor)

    def set_anchor(self, anchor: dict | None) -> None:
        self.anchor = anchor
        self.label.setText(_anchor_summary(anchor))

    def _edit(self) -> None:
        result = self._dialog._edit_anchor(self.anchor)
        if result is _CANCELLED:
            return
        self.set_anchor(result)


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
        self.setMinimumWidth(620)

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

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [_("Instance name"), _("Sheet"), _("Cluster"), _("Rotation"),
             _("Anchor")])
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
            self._set_row(i, {"name": ti.name, "sheet": ti.sheet,
                              "cluster": ti.cluster or "",
                              "rotation": _format_rotation(ti.rotation),
                              "anchor": ti.anchor})

    def _set_row(self, row: int, entry: dict) -> None:
        for col, key in enumerate(("name", "sheet", "cluster", "rotation")):
            item = QTableWidgetItem(str(entry.get(key, "")))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, col, item)
        cell = self.table.cellWidget(row, _COL_ANCHOR)
        if cell is None:
            cell = _AnchorCell(self)
            self.table.setCellWidget(row, _COL_ANCHOR, cell)
        cell.set_anchor(entry.get("anchor"))

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
        """The table's typed rows (blank name+sheet rows dropped).

        Keys are OMITTED when the axis is not set — never persisted as null/""
        — so declarations that do not need an axis stay clean on disk:
          - `cluster`: a blank cell means "not set";
          - `rotation`: a NUMBER parsed from the cell (the raw STRING when the
            cell is not a number, so _validate() can report it);
          - `anchor`: the dict from the row's Anchor cell (None -> omitted).
        """
        out = []
        for r in range(self.table.rowCount()):
            def _cell(c):
                item = self.table.item(r, c)
                return item.text().strip() if item is not None else ""
            name, sheet, cluster = _cell(0), _cell(1), _cell(2)
            if name or sheet:
                row = {"name": name, "sheet": sheet}
                if cluster:
                    row["cluster"] = cluster
                rotation = _cell(_COL_ROTATION)
                if rotation:
                    try:
                        row["rotation"] = float(rotation)
                    except ValueError:
                        row["rotation"] = rotation   # _validate reports it
                cell = self.table.cellWidget(r, _COL_ANCHOR)
                anchor = getattr(cell, "anchor", None)
                if anchor:
                    row["anchor"] = dict(anchor)
                out.append(row)
        return out

    # ── the shared anchor form (§И.5) ───────────────────────────────────

    def _anchor_candidates(self) -> list:
        """(kind, name) pairs for the anchor form's record/external mode —
        the same `build_records(cfg)` source TreesDock's own anchor form uses."""
        if self._cfg is None:
            return []
        return [(r.kind, r.name) for r in build_records(self._cfg)
                if r.kind in _ANCHOR_REF_KINDS]

    def _edit_anchor(self, current: dict | None):
        """Edit ONE row's own anchor in the SHARED AnchorFormWidget. Returns the
        new anchor dict, None for "inherit the template's anchor", or the
        _CANCELLED sentinel.

        The form is embedded with tree=None (a declaration has no suspension
        point or angle of its own — the Tree-settings box is hidden): its own
        "shift" row is unavailable there, so the row's EXISTING shift is carried
        over verbatim rather than silently dropped (the same "a disabled form
        never reinterprets the stored value" rule the form itself follows)."""
        from .trees_dock import AnchorFormWidget   # local: import-cycle-safe

        existing = anchor_from_dict(current, self.current_template() or "?") \
            if current else None
        dlg = QDialog(self)
        dlg.setWindowTitle(_("Instance anchor"))
        lay = QVBoxLayout(dlg)
        form = AnchorFormWidget(dlg, self._anchor_candidates(), cfg=self._cfg,
                                existing=existing)
        # No tree here: hide the tree-settings box AND the shift row (its reason
        # label talks about creating a tree, which does not apply to a row).
        form.settings_box.setVisible(False)
        form.shift_row.setVisible(False)
        form.shift_reason_label.setVisible(False)
        lay.addWidget(form)
        row = QHBoxLayout()
        inherit_btn = QPushButton(_("Inherit from template"))
        # Stable handles for tests/automation (the labels are translated).
        inherit_btn.setObjectName("inherit_anchor_button")
        inherit_btn.setToolTip(_(
            "Drop this row's own anchor — the instance goes back to standing "
            "where the template's anchor resolves."))
        ok_btn = QPushButton(_("OK"))
        ok_btn.setObjectName("anchor_ok_button")
        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.setObjectName("anchor_cancel_button")
        row.addWidget(inherit_btn)
        row.addStretch(1)
        row.addWidget(ok_btn)
        row.addWidget(cancel_btn)
        lay.addLayout(row)

        outcome = {"anchor": _CANCELLED}
        ok_btn.clicked.connect(lambda: dlg.accept())
        cancel_btn.clicked.connect(lambda: dlg.reject())
        inherit_btn.clicked.connect(lambda: (outcome.update(anchor=None),
                                             dlg.accept()))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return _CANCELLED
        if outcome["anchor"] is None:
            return None
        anchor, err = form.build_anchor()
        if err:
            QMessageBox.warning(self, dlg.windowTitle(), err)
            return _CANCELLED
        new = anchor_to_dict(anchor)
        if current and current.get("shift") is not None:
            new["shift"] = current["shift"]
        return new

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
            rotation = row.get("rotation")
            if rotation is not None and not isinstance(rotation, float):
                return _("Rotation must be a number — got {value!r}.").format(
                    value=rotation)
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
