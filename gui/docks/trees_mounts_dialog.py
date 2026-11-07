# gui/docks/trees_mounts_dialog.py
"""Т5 of plan_2026_11_03_add_mounts_table — the mounts table itself: ONE MODELESS
window (Д-4 of the review: no `exec()`, no `QEventLoop` of its own).

WHAT THE WINDOW SHOWS (Д2–Д6):

    Instance:  Ref [____]   Role [____]  Sheet [____]  Cluster [____]
    Name:      [fpga_vcca_{pad}]     Net contains… [____]   [Mark all visible] [Clear]
    ┌──┬─────┬──────────────┬──────────────────┬───────────┐
    │☐ │ Pad │ Net          │ Ref              │ «Уже есть» │
    └──┴─────┴──────────────┴──────────────────┴───────────┘
    status line                                       [Read pads] [OK] [Cancel]

* «Read pads» runs phase 1 (`trees_mounts_flow.start_read_pads`) on the WORKER: the
  button greys out, the status line says «reading…», and the table fills IN THIS
  WINDOW when the worker returns — no reopening, no second window (the alternative
  `cell_layers.py` uses — close, read, reopen — is what Д-4 rejected).
* The NAME column is the template (`{pad}`) EXCEPT where the user typed a name by
  hand: a hand-edited cell is never overwritten by a template change (Д5), and
  typing the computed name back makes the cell template-driven again.
* A row whose pad already carries a mount standing on it is GREY, has NO checkbox
  and shows the existing node's ref in the last column (Д6); the plan refuses such
  a pad anyway, so the greying is a courtesy and the rule is enforced below.
* The NET is read-only and only filters (rule 26: nets are never edited here).
* A TICK outlives the filter (§24 б): «Net contains…» narrows what is SHOWN, so the
  status line says «ticked N, M of them hidden by the filter» and OK writes them all.
* Editing the ADDRESS after a read DROPS that read (§24 в), and a result that comes
  back after the window is closed is dropped too (§24 а) — the read runs on a worker,
  so both can happen while this window is gone.

WHAT IT WRITES, AND WHY IT HOLDS NAMES AND NOT OBJECTS (§23 of the review). The
window is modeless, so it lives longer than one tick: while it is open the user can
switch tree tabs, delete the parent, or reload the config — which replaces every
`Tree` object. So it keeps the TREE NAME and `parent_ref`, never the objects, and
at OK it asks the dock for the tree that exists THEN (`trees_mounts_flow.
tree_by_name`), resolves the parent by its ref in that tree (`tree_mounts.
node_with_ref`), and hands THE SAME tree and THE SAME parent to `plan_mount_nodes
(parent_ref=…)` and `write_plan` — the plan refuses a place it did not check
(Т4.1(г)), so the two can never disagree.

EVERY refusal is one red line in the status area AND in the Log — never a
`QMessageBox` (rule 43), and the window stays open so the user can fix the field.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QCompleter, QDialog,
                             QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)

from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.tree_mounts import (MountAddress, MountRow, filter_rows,
                                    mount_name, node_with_ref, plan_mount_nodes)

from ..ui_utils import remember_dialog_size
from ..worker import qt_object_gone
from . import trees_mounts_flow

logger = logging.getLogger(__name__)

__all__ = ["MountsDialog", "open_mounts_dialog"]

#: The template a fresh window starts with: the pad number is the shortest useful
#: name, and Denis's own mounts are `fpga_vccio_{pad}`-shaped.
_DEFAULT_TEMPLATE = "{pad}"

_COLUMN_CHECK = 0
_COLUMN_PAD = 1
_COLUMN_NET = 2
_COLUMN_REF = 3
_COLUMN_HAVE = 4


class MountsDialog(QDialog):
    """The mounts table — see the module docstring for what it shows and writes."""

    def __init__(self, parent=None, *, dock, connection, cfg, sheet_names,
                 tree_name: str, parent_ref: Optional[str] = None,
                 ref: Optional[str] = None, role: Optional[str] = None,
                 cluster: Optional[str] = None, sheet: Optional[str] = None,
                 role_choices=()):
        super().__init__(parent)
        self.setWindowTitle(_("Add mounts…"))
        # MODELESS (Д-4): the dock stays usable, and this window survives a reload —
        # which is exactly why it remembers names and not objects.
        self.setModal(False)
        remember_dialog_size(self, 720, 520)

        self._dock = dock
        self._connection = connection
        self._cfg = cfg
        self._sheet_names = sheet_names
        self._tree_name = tree_name           # a NAME, never a Tree
        self._parent_ref = parent_ref         # a NAME, never a TreeNode
        self._active_op = None

        self.rows: list[MountRow] = []
        self.occupied: dict[str, str] = {}
        self.instance: dict = {}
        self._checked: set[str] = set()
        self._edited: dict[str, str] = {}
        self._filling = False
        # True while a REFUSAL owns the status area — the courtesy line about ticks
        # must never overwrite it (§24 б).
        self._refusal = False

        # Д1а: the roles of the cell the window was opened on — SUGGESTIONS for the
        # Role field (a completer, not a closed list: free text stays legal, the
        # same populate-don't-restrict rule the node dialog's pickers follow).
        self._role_choices = tuple(role_choices or ())
        self._build_ui(ref=ref, role=role, cluster=cluster, sheet=sheet)
        self.retranslate()

    # ── the window ────────────────────────────────────────────────────────

    def _build_ui(self, *, ref, role, cluster, sheet) -> None:
        layout = QVBoxLayout(self)

        address = QHBoxLayout()
        self.ref_edit = QLineEdit(ref or "")
        self.role_edit = QLineEdit(role or "")
        self.sheet_edit = QLineEdit(sheet or "")
        self.cluster_edit = QLineEdit(cluster or "")
        if self._role_choices:
            self.role_edit.setCompleter(QCompleter(list(self._role_choices), self))
        for label, widget in ((_("Ref:"), self.ref_edit), (_("Role:"), self.role_edit),
                              (_("Sheet:"), self.sheet_edit),
                              (_("Cluster:"), self.cluster_edit)):
            address.addWidget(QLabel(label))
            address.addWidget(widget, 1)
            widget.textChanged.connect(self._on_address_changed)
        layout.addLayout(address)

        naming = QHBoxLayout()
        self.template_edit = QLineEdit(_DEFAULT_TEMPLATE)
        self.template_edit.textChanged.connect(self._on_template_changed)
        self.net_filter_edit = QLineEdit("")
        self.net_filter_edit.setPlaceholderText(_("Net contains…"))
        self.net_filter_edit.textChanged.connect(lambda _text: self._rebuild())
        self.mark_all_button = QPushButton(_("Mark all visible"))
        self.mark_all_button.clicked.connect(lambda: self._set_all_visible(True))
        self.clear_button = QPushButton(_("Clear"))
        self.clear_button.clicked.connect(lambda: self._set_all_visible(False))
        naming.addWidget(QLabel(_("Name:")))
        naming.addWidget(self.template_edit, 2)
        naming.addWidget(self.net_filter_edit, 1)
        naming.addWidget(self.mark_all_button)
        naming.addWidget(self.clear_button)
        layout.addLayout(naming)

        self.table = QTableWidget(0, 5, self)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox()
        self.read_button = buttons.addButton(_("Read pads"),
                                             QDialogButtonBox.ButtonRole.ActionRole)
        self.read_button.clicked.connect(self.read_pads)
        self.ok_button = buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.clicked.connect(self.accept_plan)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def retranslate(self) -> None:
        """Header labels follow the LANGUAGE (the window is built once)."""
        self.table.setHorizontalHeaderLabels(
            ["", _("Pad"), _("Net"), _("Ref"), _("«Already there»")])

    # ── state the cells read ──────────────────────────────────────────────

    def visible_rows(self) -> list[MountRow]:
        """The rows the net filter leaves visible (Д4)."""
        return filter_rows(self.rows, self.net_filter_edit.text())

    def selected_pads(self) -> list[str]:
        """Every TICKED pad, visible or not — what OK will create (Д7, §24 б).

        The net filter narrows what is SHOWN and nothing else: a pad ticked under
        one filter must not be lost by typing another one. A taken pad is never
        selected, however it got ticked (Д6)."""
        return [row.pad for row in self.rows
                if row.pad in self._checked and row.pad not in self.occupied]

    def hidden_ticked(self) -> int:
        """How many ticked pads the net filter currently hides from the table."""
        visible = {row.pad for row in self.visible_rows()}
        return sum(1 for pad in self._checked
                   if pad not in visible and pad not in self.occupied)

    def _tree(self):
        """The dock's tree by NAME, at the moment of use (§23) — None if it is gone."""
        return trees_mounts_flow.tree_by_name(self._dock, self._tree_name)

    def _computed_name(self, pad: str) -> str:
        """The template's name for this pad, or the pad number when the template
        does not parse (a half-typed template is normal, not an error to shout at
        every keystroke — OK refuses in words instead)."""
        try:
            return mount_name(self.template_edit.text(), pad)
        except ValidationError:
            return pad

    # ── the table ─────────────────────────────────────────────────────────

    def _rebuild(self) -> None:
        visible = self.visible_rows()
        self._filling = True
        try:
            self.table.setRowCount(0)
            for row in visible:
                self._append_row(row)
        finally:
            self._filling = False
        # A template that does not parse is said ONCE, here, not per keystroke.
        try:
            mount_name(self.template_edit.text(), "1")
        except ValidationError as exc:
            self._tell(str(exc).strip().splitlines()[0])
        else:
            # NOT `self._refusal = False` here: a refusal (of the template, of OK) is
            # cleared by the action that ANSWERS it — a new read, a tick, an edit of
            # the template — never by a mere rebuild (§24 б).
            self._update_tick_status()

    def _update_tick_status(self) -> None:
        """Say what the filter hides from the ticks — never over a refusal (§24 б)."""
        if self._refusal:
            return
        hidden = self.hidden_ticked()
        self.status_label.setText(
            _("ticked {chosen}, {hidden} of them hidden by the filter").format(
                chosen=len(self.selected_pads()), hidden=hidden)
            if hidden else "")

    def _append_row(self, row: MountRow) -> None:
        index = self.table.rowCount()
        self.table.insertRow(index)

        taken_by = self.occupied.get(row.pad)
        check = QTableWidgetItem("")
        if taken_by is None:
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if row.pad in self._checked
                                else Qt.CheckState.Unchecked)
        else:
            check.setFlags(Qt.ItemFlag.ItemIsEnabled)      # greyed: no checkbox (Д6)
        self.table.setItem(index, _COLUMN_CHECK, check)

        pad = QTableWidgetItem(row.pad)
        pad.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(index, _COLUMN_PAD, pad)

        net = QTableWidgetItem(row.net)
        net.setFlags(Qt.ItemFlag.ItemIsEnabled)
        if row.conflicting_nets:
            net.setToolTip(_("this number is carried by pads on DIFFERENT nets: {nets}"
                             ).format(nets=", ".join(row.conflicting_nets)))
        self.table.setItem(index, _COLUMN_NET, net)

        name = QTableWidgetItem(self._edited.get(row.pad) or self._computed_name(row.pad))
        if taken_by is not None:
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)       # a taken row is not editable
        self.table.setItem(index, _COLUMN_REF, name)

        have = QTableWidgetItem(taken_by or "")
        have.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(index, _COLUMN_HAVE, have)

    def _pad_of_row(self, row_index: int) -> Optional[str]:
        pad = self.table.item(row_index, _COLUMN_PAD)
        return pad.text() if pad is not None else None

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        pad = self._pad_of_row(item.row())
        if pad is None:
            return
        if item.column() == _COLUMN_CHECK:
            if item.checkState() == Qt.CheckState.Checked:
                self._checked.add(pad)
            else:
                self._checked.discard(pad)
            self._refusal = False      # the user answered the refusal by ticking
            self._update_tick_status()
            return
        if item.column() == _COLUMN_REF:
            text = item.text().strip()
            if not text or text == self._computed_name(pad):
                self._edited.pop(pad, None)      # typed back to the computed name
            else:
                self._edited[pad] = text

    def _set_all_visible(self, checked: bool) -> None:
        """«Mark all visible» / «Clear» — the VISIBLE rows only, taken rows never."""
        for row in self.visible_rows():
            if row.pad in self.occupied:
                continue
            if checked:
                self._checked.add(row.pad)
            else:
                self._checked.discard(row.pad)
        if checked:
            self._refusal = False          # marking IS the answer to "nothing is ticked"
        self._rebuild()

    def _on_template_changed(self, _text: str) -> None:
        self._refusal = False              # the user is answering the template complaint
        self._rebuild()

    # ── «Read pads» (phase 1, on the worker) ──────────────────────────────

    def read_pads(self) -> None:
        tree = self._tree()
        if tree is None:
            self._tell(_("tree {name!r} is gone — reopen the mounts table").format(
                name=self._tree_name))
            return
        self.read_button.setEnabled(False)
        self.status_label.setText(_("reading…"))
        # §26 (Т5.1 review): the ADDRESS FIELDS are guard widgets too — a field edited
        # while the read runs would label the incoming rows with the wrong address.
        # The same LongOpController greys them and gives them back (and a window that
        # dies meanwhile is skipped, Т5.1а).
        self._active_op = trees_mounts_flow.start_read_pads(
            self._connection, cfg=self._cfg, sheet_names=self._sheet_names,
            tree=tree, widgets=(self.read_button, self.ref_edit, self.role_edit,
                                self.sheet_edit, self.cluster_edit),
            on_ready=self.filled, on_error=self._tell, **self._address_kwargs())
        if self._active_op is None:
            # Nothing was started (no board, or another operation holds the socket):
            # the status line already says why in the Log; the button comes back.
            self.read_button.setEnabled(True)
            self.status_label.setText(_("nothing was read — see the Log"))

    def _address_kwargs(self) -> dict:
        ref = self.ref_edit.text().strip()
        if ref:
            return {"ref": ref}
        return {"role": self.role_edit.text().strip() or None,
                "cluster": self.cluster_edit.text().strip() or None,
                "sheet": self.sheet_edit.text().strip() or None}

    def filled(self, result: dict) -> None:
        """Phase 2 (UI thread, worker stopped): the table fills IN THIS WINDOW."""
        if self._window_gone():
            logger.info("mounts: the pads arrived after the window was closed — dropped")
            return
        self.read_button.setEnabled(True)
        self.status_label.setText("")
        self.rows = list(result.get("rows") or [])
        self.occupied = dict(result.get("occupied") or {})
        self.instance = dict(result.get("instance") or {})
        self._checked.clear()
        self._edited.clear()
        self._refusal = False
        self._rebuild()

    # ── OK ────────────────────────────────────────────────────────────────

    def accept_plan(self) -> None:
        """Build the plan against the tree that EXISTS now and write it (Д7/§23)."""
        tree = self._tree()
        if tree is None:
            self._tell(_("tree {name!r} is gone — reopen the mounts table").format(
                name=self._tree_name))
            return
        if not self.instance:
            self._tell(_("read the pads first — the anchor is taken from the "
                         "instance that read them"))
            return
        address = MountAddress(role=self.instance.get("role"),
                               cluster=self.instance.get("cluster"),
                               sheet=self.instance.get("sheet"))
        try:
            plan = plan_mount_nodes(
                self.template_edit.text(), self.rows,
                selected=self.selected_pads(), address=address, tree=tree,
                edited={pad: name for pad, name in self._edited.items() if name},
                parent_ref=self._parent_ref, occupied=self.occupied)
        except ValidationError as exc:
            self._tell(str(exc))
            return
        if not plan.ok:
            self._tell(" ".join(plan.refusals))
            return
        parent = node_with_ref(tree, self._parent_ref) if self._parent_ref else None
        written = trees_mounts_flow.write_plan(self._dock, tree, parent, plan)
        if not written:
            self._tell(_("nothing was written — the mounts did not reach the tree"))
            return
        logger.info("mounts: wrote %d node(s) into tree %r", written, self._tree_name)
        self.accept()

    def _tell(self, text: str) -> None:
        """One red line in the window AND in the Log — never a modal (rule 43)."""
        if self._window_gone():
            logger.info("mounts: %s (the window is closed — the Log only)", text)
            return
        self._refusal = True
        logger.error(text)
        self.status_label.setText(text)

    def _window_gone(self) -> bool:
        """True once Qt has deleted this window (`WA_DeleteOnClose`).

        The read runs on a worker, so its result may come back AFTER the user closed
        the window (§24 а): touching a dead widget raises, and that is not an error
        in the operation — it is a result nobody wants any more."""
        return qt_object_gone(self)

    def _on_address_changed(self, _text: str = "") -> None:
        """The address was edited AFTER a read — that read is about a DIFFERENT part.

        §24 в: the rows, the instance and «уже есть» all describe the address the pads
        were read at, so they go, and OK says «read the pads first» until a new read."""
        if not self.instance and not self.rows:
            return
        self.rows = []
        self.occupied = {}
        self.instance = {}
        self._checked.clear()
        self._edited.clear()
        self._rebuild()
        self._tell(_("the address changed — read the pads again"))


def open_mounts_dialog(parent, *, dock, connection, cfg, sheet_names, tree_name,
                       parent_ref=None, ref=None, role=None, cluster=None,
                       sheet=None, role_choices=()) -> MountsDialog:
    """Build the window, connect it to the dock's lifetime and SHOW it (modeless).

    Returns the dialog so a caller (and a cell) can talk to it; the window deletes
    itself when closed, so nothing here has to be kept alive by the dock."""
    dialog = MountsDialog(parent, dock=dock, connection=connection, cfg=cfg,
                          sheet_names=sheet_names, tree_name=tree_name,
                          parent_ref=parent_ref, ref=ref, role=role,
                          cluster=cluster, sheet=sheet, role_choices=role_choices)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    dialog.show()
    return dialog
