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

The Root column (Н6 + acceptance 8.5) picks WHICH node of the template the copy
starts from: a combo of the template's placeable nodes, its first entry
"— whole tree —" meaning "no `root`". It is an ordinary editable axis of the same
merge rule, so the pre-write proof (instances_validation) checks it exactly like
every other column.

The dialog GENERATES NOTHING and copies nothing — it only edits the short
declarations; materialization into full Tree + Entity records happens at the
NEXT load/Save (config/tree_instances.py::expand_tree_instances), exactly like
every other config section. TreesDock.reload_trees() after OK shows the new
read-only instance tabs.

A BROKEN declaration no longer locks the dialog (Н8, 07.11): the caller opens it
through `load_config_lenient` and passes `instance_failures` — the strict loader
refuses the whole profile, and the only way out used to be File → Discard
unsaved changes. A refused row is marked "⚠", carries the refusal in its tooltip
and shows the full text under the table; fixing or deleting it is an ordinary
write through the same `upsert_tree_instances`.
"""
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                             QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from kicadstamp.anchor_graph import build_records
from kicadstamp.config_writer import upsert_tree_instances
from kicadstamp.i18n import _
from kicadstamp.trees import anchor_from_dict, anchor_to_dict

from ..ui_utils import persist_dialog_size, restore_dialog_size
from .instances_validation import (expansion_refusal, proposed_graph,
                                   sheet_warnings)

logger = logging.getLogger(__name__)

# Column indices (one place, so the header list and every reader agree). Root is
# APPENDED last (8.5 of the acceptance) so the anchor cell keeps its index — the
# axis is "which node of the template to copy", not a place axis.
_COL_NAME, _COL_SHEET, _COL_CLUSTER, _COL_ROTATION, _COL_ANCHOR, _COL_ROOT = range(6)

# The record kinds an anchor's (ref ...) mode may name — the same set the
# AnchorFormWidget's kind filter offers (trees_dock._PLACEABLE_KINDS).
_ANCHOR_REF_KINDS = ("placement", "coordinate", "point", "clone")

# Returned by _edit_anchor() when the user cancelled — distinct from None,
# which means "inherit the template's anchor" (an explicit user choice).
_CANCELLED = object()


def _format_rotation(value: float | None) -> str:
    """A rotation cell's text: blank when not set, otherwise the number without
    a trailing ".0" (90.0 -> "90")."""
    return "" if value is None else f"{value:g}"


def _shift_from_fields(form) -> tuple[tuple[float, float] | None, str | None]:
    """The shift row of the shared form as a DECLARATION stores it — config
    (LOCAL) mm — or (None, error).

    The form's own `build_anchor()` deliberately answers None for a row: it
    converts the field through the ANCHOR's live angle and has no Tree here, so
    its fields are not board-frame values at all. A declaration's `(shift x y)`
    IS the local offset (the plan's own example: `(shift 0.0 40.0)`), so the row
    reads the fields verbatim — the one place this dialog interprets them."""
    tx = form.shift_x_edit.text().strip()
    ty = form.shift_y_edit.text().strip()
    if tx == "" and ty == "":
        return None, None
    try:
        sx = float(tx) if tx != "" else 0.0
        sy = float(ty) if ty != "" else 0.0
    except ValueError:
        return None, _("Shift: X and Y must be numbers.")
    if sx == 0.0 and sy == 0.0:
        return None, None
    return (sx, sy), None


def root_candidates(tree) -> list:
    """The template's nodes a `root:` may name — [{ref, ref_uuid?}] — in tree
    order (8.5 of the acceptance).

    Only a PLACEMENT node whose ancestors are placements too: any other kind has
    no offset in the config (a mount's base is the live board's part, a
    component/copper node is addressed by a role or a point), and the loader
    refuses such a root — offering it here would be offering a refusal."""
    out: list[dict] = []
    if tree is None:
        return out

    def walk(nodes, blocked: bool) -> None:
        for node in nodes or []:
            kind = getattr(node, "kind", None)
            placement = kind in (None, "placement")
            if placement and not blocked and getattr(node, "ref", None):
                entry = {"ref": node.ref}
                if getattr(node, "ref_uuid", None):
                    entry["ref_uuid"] = node.ref_uuid
                out.append(entry)
            walk(getattr(node, "children", None) or [], blocked or not placement)

    walk(getattr(tree, "nodes", None) or [], False)
    return out


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

    def __init__(self, parent, root_path, cfg, preferred_template=None,
                 instance_failures=None, sheet_names=None):
        super().__init__(parent)
        self._root_path = root_path
        self._cfg = cfg
        # {uuid: Sheetname} from the project's schematic — the Н4 check needs
        # the list of sheets that EXIST; empty = a profile without a schematic,
        # where the sheet check is skipped rather than guessed.
        self._sheet_names = dict(sheet_names or {})
        # {(template, instance name): {index, template, name, sheet, error}} —
        # the declarations the LENIENT expansion refused (Н8). The same
        # cfg.tree_instances carries them (the raw key is parsed regardless), so
        # the row is theirs to fix or delete.
        self._failures = {(f.get("template"), f.get("name")): f
                          for f in (instance_failures or [])}
        self._row_refusals: dict[int, dict] = {}
        # The shared anchor form of the LAST opened row dialog (kept for tests
        # and for the shift row's own reading; one at a time, the dialog is
        # modal).
        self._last_anchor_form = None
        self.setWindowTitle(_("Tree instances"))
        self.setMinimumWidth(620)
        # Э3 (plan_2026_09_12_node_dialog_usability): remember the size between
        # launches. No default constant here (Qt's own size until the user
        # resizes it once), and the width floor above still applies.
        restore_dialog_size(self)
        persist_dialog_size(self)

        instance_names = {ti.name for ti in cfg.tree_instances}
        # A generated instance can't be a template (its geometry is derived
        # from ITS own template) — templates are the hand-written trees.
        self._templates = sorted(t.name for t in cfg.trees
                                 if t.name not in instance_names)
        self._tree_names = {t.name for t in cfg.trees}
        # К3а (8.1): Rename/Delete in Trees never touches the rows that point at
        # the old template, so a row may name a template that is GONE. Offer that
        # name here anyway — otherwise the refused row is unreachable and cannot
        # be fixed at all. The pre-write proof keeps refusing it until it is.
        self._templates = sorted(set(self._templates) | {
            ti.template for ti in cfg.tree_instances
            if ti.template and ti.template not in self._tree_names})

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_("Template tree (instances are read-only "
                                  "generated copies of it):")))
        self.template_combo = QComboBox()
        self.template_combo.addItems(self._templates)
        # The tree ROOT's context menu opens this dialog FOR its own tree
        # (plan_2026_10_13_trees_menus_cleanup Ч2) and preselects it as the
        # template — when it can be one (a generated instance cannot).
        if preferred_template in self._templates:
            self.template_combo.setCurrentText(preferred_template)
        layout.addWidget(self.template_combo)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [_("Instance name"), _("Sheet"), _("Cluster"), _("Rotation"),
             _("Anchor"), _("Root")])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        # The refusal of the selected row, in full (Н8). A Log line instead of a
        # window is the house rule (п.43): this label is the dialog's own.
        self.refusal_label = QLabel("")
        self.refusal_label.setWordWrap(True)
        self.refusal_label.setVisible(False)
        layout.addWidget(self.refusal_label)

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
        self.table.itemSelectionChanged.connect(
            lambda: self._show_refusal(self.table.currentRow()))
        self._load_template()

    # ── rows ────────────────────────────────────────────────────────────

    def current_template(self) -> str:
        return self.template_combo.currentText()

    def _load_template(self) -> None:
        """Fill the table with the selected template's existing rows."""
        tpl = self.current_template()
        rows = [ti for ti in self._cfg.tree_instances if ti.template == tpl]
        self._row_refusals = {}
        self.table.setRowCount(len(rows))
        for i, ti in enumerate(rows):
            refusal = self._failures.get((tpl, ti.name))
            if refusal is not None:
                self._row_refusals[i] = refusal
            self._set_row(i, {"name": ti.name, "sheet": ti.sheet,
                              "cluster": ti.cluster or "",
                              "rotation": _format_rotation(ti.rotation),
                              "anchor": ti.anchor, "root": ti.root,
                              "refused": refusal})
        self._show_refusal(self.table.currentRow() if rows else -1)

    def _show_refusal(self, row: int) -> None:
        """Put the selected row's load refusal under the table (Н8), or hide the
        label when the row loaded fine."""
        refusal = self._row_refusals.get(row)
        if refusal is None:
            self.refusal_label.setText("")
            self.refusal_label.setVisible(False)
            return
        self.refusal_label.setText(_(
            "Instance {name!r} is refused on load and is NOT materialized — "
            "fix or delete this row: {error}").format(
                name=refusal.get("name"), error=refusal.get("error")))
        self.refusal_label.setVisible(True)

    def _set_row(self, row: int, entry: dict) -> None:
        refusal = entry.get("refused")
        for col, key in enumerate(("name", "sheet", "cluster", "rotation")):
            text = str(entry.get(key, ""))
            if key == "name" and refusal is not None:
                text = "⚠ " + text
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            if key == "name" and refusal is not None:
                item.setToolTip(_("refused on load: {error}").format(
                    error=refusal.get("error")))
            self.table.setItem(row, col, item)
        cell = self.table.cellWidget(row, _COL_ANCHOR)
        if cell is None:
            cell = _AnchorCell(self)
            self.table.setCellWidget(row, _COL_ANCHOR, cell)
        cell.set_anchor(entry.get("anchor"))
        self._set_root_cell(row, entry.get("root"))

    def _root_choices(self) -> list:
        """The nodes the current template offers as a `root` — recomputed when
        the template picker changes, never cached (the tree may be edited in
        another dock in the meantime)."""
        if self._cfg is None:
            return []
        tree = next((t for t in self._cfg.trees
                     if t.name == self.current_template()), None)
        return root_candidates(tree)

    def _set_root_cell(self, row: int, root: dict | None) -> None:
        """Fill one row's Root combo: "— whole tree —" (no `root`), then the
        template's placeable nodes, then — when the stored `root` names none of
        them — the stored value itself. That last item matters: a hand-written
        root must never be silently replaced by the first entry just because the
        template changed under it (the pre-write proof will refuse it by name)."""
        combo = self.table.cellWidget(row, _COL_ROOT)
        if not isinstance(combo, QComboBox):
            combo = QComboBox()
            combo.setToolTip(_("Copy only this node of the template and "
                               "everything under it"))
            self.table.setCellWidget(row, _COL_ROOT, combo)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(_("— whole tree —"), None)
        for entry in self._root_choices():
            combo.addItem(entry["ref"], dict(entry))
        wanted = (root or {}).get("ref")
        if wanted and combo.findText(wanted) < 0:
            combo.addItem(wanted, dict(root))
        if wanted:
            combo.setCurrentIndex(max(0, combo.findText(wanted)))
        combo.blockSignals(False)

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
                root_cell = self.table.cellWidget(r, _COL_ROOT)
                root = (root_cell.currentData()
                        if isinstance(root_cell, QComboBox) else None)
                if root:
                    row["root"] = dict(root)
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
        # No tree here: the tree-settings box is hidden, but the SHIFT row stays
        # VISIBLE and EDITABLE (Н5, 07.11) — a declaration's own anchor has a
        # shift axis (`(shift x y)`, config/local mm), and hiding it forced the
        # user to invent a separate `Origin_B` point. The form's reason label is
        # about creating a tree, which does not apply to a row.
        self._last_anchor_form = form
        form.settings_box.setVisible(False)
        form.shift_x_edit.setEnabled(True)
        form.shift_y_edit.setEnabled(True)
        form.shift_reason_label.setVisible(False)
        if existing is not None and existing.shift_xy is not None:
            form.shift_x_edit.setText(str(existing.shift_xy[0]))
            form.shift_y_edit.setText(str(existing.shift_xy[1]))
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
        shift, shift_err = _shift_from_fields(form)
        if shift_err:
            self._show_problem(shift_err)
            return _CANCELLED
        new = anchor_to_dict(anchor)
        if shift is not None:
            new["shift"] = [shift[0], shift[1]]
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
        """Validate + PROVE + write the section; True when written.

        The proof is the loader's own expansion run in memory over the graph
        "as it will be after the write" (Н7): a row the next load would refuse
        is shown here and NOT written. The sheet/cluster lines (Н4) are warnings
        and never block the write — they go to the Log (п.43: no modals)."""
        rows = self.rows()
        problem = self._validate(rows)
        if problem is not None:
            QMessageBox.warning(self, self.windowTitle(), problem)
            return False
        template = self.current_template()
        data = proposed_graph(self._root_path, template, rows)
        refusal = expansion_refusal(data, template, rows)
        if refusal is not None:
            self._show_problem(refusal)
            return False
        for line in sheet_warnings(rows, template, data, self._sheet_names):
            logger.warning(line)
        upsert_tree_instances(self._root_path, template, rows)
        return True

    def _show_problem(self, text: str) -> None:
        """Show a refusal IN the dialog (the current row is not written and the
        dialog stays open) and repeat it in the Log as an error."""
        logger.error(text)
        self.refusal_label.setText(text)
        self.refusal_label.setVisible(True)

    def accept(self) -> None:
        if self._apply():
            super().accept()
