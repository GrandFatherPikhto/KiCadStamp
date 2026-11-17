# gui/docks/trees_reread_dialog.py
"""«Reread positions from board…» — the table, and the ONLY place it writes.

Plan plan_2026_11_16_reread_forest_from_board, commit 1, К1-А5. The read and the
arithmetic live in `trees_reread_board` (ONE board pass, then `build_plan` with no
board at all); this module shows what that plan would change and applies it to the
CONFIG BUFFER when the user says so.

WHAT APPLY TOUCHES: the trees of the open project, in memory — an anchor's
`shift_xy`, a node's `xy`/`polar`/`rotation` — and nothing else. The board is never
written, and Save is still the user's own action (the dock's `_mark_dirty` marks the
buffer, exactly like any other tree edit). Cancel writes nothing at all — zero
changes, not even a partial one: `apply_plan` runs only on Accepted.

THE TWO INTENTIONS are the switch at the top. «Origin moved» lets the biggest group
of equally shifted voters give each tree's new anchor shift; «Blocks moved by hand»
keeps every anchor and reads each node on its own. Both are decided by
`build_plan`, so flipping the switch re-renders the SAME read — the board is not
asked twice (that is what makes the switch honest: two reads could see two boards).

WHAT THE TABLE SHOWS: one row per anchor (with the per-tree «anchor follows» box),
one per node that will be written (in the node's OWN shape — a Polar node stays
«r ∠ φ»), one per embedded anchor synced to its module, and one per skip WITH ITS
REASON. A skip is never a silent absence: a tree whose anchor or content could not
be read is the thing the user must know about before trusting the rest.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QHBoxLayout, QLabel, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.forest_reread import MODE_ABSORB, MODE_KEEP, OVERRIDE_KEEP
from kicadstamp.i18n import _
from kicadstamp.tree_anchor_read import DEG_TOLERANCE
from kicadstamp.tree_position import relative_rotation_deg
from kicadstamp.trees import _walk_nodes

from ..ui_utils import remember_dialog_size
from ..worker import socket_busy, start_long_op
from ._common import ERROR_STYLE as _ERROR_STYLE, show_message
from .trees_reread_board import build_plan, read_forest

logger = logging.getLogger(__name__)

_MODE_ROWS = (
    (MODE_ABSORB, _("Origin moved — absorb in anchors")),
    (MODE_KEEP, _("Blocks moved by hand — keep anchors")),
)


def _mm(pair) -> str:
    return "({:.3f}, {:.3f})".format(pair[0], pair[1])


def _shown(change) -> str:
    if change.polar:
        return "{} ∠ {:.3f}°".format(round(change.now[0], 3),
                                     round(change.now[1], 3))
    return _mm(change.now)


def _was(change) -> str:
    if change.polar:
        return "{} ∠ {:.3f}°".format(round(change.was[0], 3),
                                     round(change.was[1], 3))
    return _mm(change.was)


def _delta(change) -> str:
    text = _mm(change.delta)
    if change.turned:
        text += _(" · angle Δ {deg}°").format(
            deg=round(change.now_rotation - change.was_rotation, 3))
    return text


# ── what Apply does to the BUFFER (never to the board) ──────────────────────

def _node_of(tree, ref):
    if tree is None:
        return None
    for node in _walk_nodes(list(tree.nodes or [])):
        if node.ref == ref:
            return node
    return None


def _value(number: float) -> float:
    """A number as the CONFIG file should hold it: six decimals, and `-0.0` folded
    into `0.0`.

    Without this the profile ended up with `19.999999`, `-30.000001`, `-0.0` and
    `-1.5031179999999997` after two Applys (Ф6 of the 17.11 acceptance): the noise
    is invisible in the dialog (three decimals there) but it IS what the file says,
    and `-0.0` reads as a sign nobody typed. `round(x, 6) + 0.0` is the whole fix —
    adding a positive zero to a negative zero yields a positive zero."""
    return round(float(number), 6) + 0.0


def _rotation_to_write(change) -> Optional[float]:
    """The angle to store: the STORED one when the live one equals it modulo 360.

    A part standing at 180° is read back as −180° by the board, and writing the
    READ value rewrote an untouched angle as `-180.0` in the profile (Ф6: `fpga_
    vccint_29`, `fpga_vccio_26`, `pif_1v2_vccint`, `pif_3v3_vdda` …). Both spellings
    are the same pose, so the file keeps what it had — no diff, no review noise."""
    live = change.now_rotation
    stored = change.was_rotation
    if live is None:
        return None
    if (stored is not None
            and abs(relative_rotation_deg(live, stored)) <= DEG_TOLERANCE):
        return _value(stored)
    return _value(live)


def _set_shift(tree, shift) -> bool:
    anchor = getattr(tree, "anchor", None)
    if anchor is None:
        return False
    anchor.shift_xy = (_value(shift[0]), _value(shift[1]))
    return True


def apply_plan(dock, plan) -> int:
    """Write the plan into the trees of the open project's buffer. Returns how many
    values were written. NOTHING here touches the board, and nothing runs on a
    Cancel — the caller only ever passes an accepted plan.

    Every number goes through `_value` and every angle through `_rotation_to_write`:
    what lands in the file is what the next Save writes, so the profile must not
    collect float tails or a flipped `180 → −180` for a node nobody moved."""
    trees = {tree.name: tree for tree in (getattr(dock, "_trees", None) or [])}
    written = 0
    for row in list(plan.anchors) + list(plan.synced):
        if not row.moved:
            continue
        if _set_shift(trees.get(row.tree), row.now):
            written += 1
    for change in plan.changes:
        node = _node_of(trees.get(change.tree), change.ref)
        if node is None:
            continue
        pair = (_value(change.now[0]), _value(change.now[1]))
        if change.polar:
            node.polar = pair
            node.xy = None
        else:
            node.xy = pair
            node.polar = None
        rotation = _rotation_to_write(change)
        if rotation is not None:
            node.rotation = rotation
        written += 1
    return written


def _report(plan) -> str:
    trees = {change.tree for change in plan.changes}
    return _(
        "Reread positions: {n} node(s) changed in {m} tree(s), {k} skipped"
    ).format(n=plan.changed, m=len(trees), k=plan.skipped_count)


# ── the table ───────────────────────────────────────────────────────────────

class RereadForestDialog(QDialog):
    """The plan, re-decided in place on every flip of the switch or a per-tree box."""

    def __init__(self, parent, reads_raw: dict):
        super().__init__(parent)
        self.setWindowTitle(_("Reread positions from board"))
        self._reads = reads_raw
        self._mode = MODE_ABSORB
        # «Keep this tree's anchor» choices. Deliberately NOT called `_overrides`:
        # that name means the field-override STORE to the GUI's AST guard
        # (tests/overrides/test_overrides_store_reload.py), and this dict has
        # nothing to do with it — it holds tree NAMES the user unticked.
        self._kept_trees: dict = {}
        self.plan = build_plan(reads_raw, mode=self._mode)
        self._boxes: dict = {}

        layout = QVBoxLayout(self)
        self.mode_combo = QComboBox(self)
        for value, text in _MODE_ROWS:
            self.mode_combo.addItem(text, value)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(_("Mode:"), self))
        mode_row.addWidget(self.mode_combo, 1)
        layout.addLayout(mode_row)
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(
            [_("Tree"), _("Node"), _("Was → Now"), _("Δ"), _("Note")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(self)
        self.apply_button = buttons.addButton(_("Apply to buffer"),
                                              QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # A table of numbers is worth remembering its size for (plan_2026_11_07
        # dialog_sizes_and_row_shift, part Б): every dialog in gui/ must pair
        # restore with persist, or it is "slopnuty" again on the second launch.
        remember_dialog_size(self, 900, 480)
        self._refresh()

    # -- state ------------------------------------------------------------
    def _on_mode_changed(self, _index: int) -> None:
        self._mode = self.mode_combo.currentData() or MODE_ABSORB
        self._refresh()

    def _on_follow_toggled(self, tree: str, checked: bool) -> None:
        if checked:
            self._kept_trees.pop(tree, None)
        else:
            self._kept_trees[tree] = OVERRIDE_KEEP
        self._refresh()

    def _refresh(self) -> None:
        """Rebuild the table from the SAME read — the only thing that changed is
        the decision, and the board is not asked anything."""
        self.plan = build_plan(self._reads, mode=self._mode,
                               overrides=self._kept_trees)
        self.hint.setText(_(
            "The whole forest was read once. Flipping the mode or a tree's box "
            "re-decides the same read — nothing is written until Apply, and the "
            "board itself is never touched."))
        self.table.setRowCount(0)
        self._boxes = {}
        empty = self.plan.empty and not self.plan.synced_moved
        if empty:
            self._add_row(["", "", "", "", _("nothing moved — nothing to write")])
        for row in self.plan.anchors:
            index = self._add_row([row.tree, _("anchor shift"),
                                   "{} → {}".format(_mm(row.was), _mm(row.now)),
                                   _mm((row.now[0] - row.was[0],
                                        row.now[1] - row.was[1])), row.reason])
            box = QCheckBox(_("anchor follows"), self)
            box.setChecked(row.tree not in self._kept_trees)
            box.toggled.connect(
                lambda checked, name=row.tree: self._on_follow_toggled(name, checked))
            self.table.setCellWidget(index, 4, box)
            self._boxes[row.tree] = box
        for row in self.plan.synced:
            self._add_row([row.tree, _("embedded anchor synced to module"),
                           "{} → {}".format(_mm(row.was), _mm(row.now)),
                           _mm((row.now[0] - row.was[0],
                                row.now[1] - row.was[1])), ""])
        for change in self.plan.changes:
            self._add_row([change.tree, change.ref,
                           "{} → {}".format(_was(change), _shown(change)),
                           _delta(change), ""])
        for skip in self.plan.skipped:
            self._add_row([skip.tree, skip.ref, "—", "", skip.reason])
        self.table.resizeColumnsToContents()
        self.apply_button.setEnabled(not empty)

    def _add_row(self, cells: list) -> int:
        index = self.table.rowCount()
        self.table.insertRow(index)
        for column, text in enumerate(cells):
            item = QTableWidgetItem(str(text))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(index, column, item)
        return index


# ── the entry point the menu calls ──────────────────────────────────────────

def _trees_dock_of(owner):
    hub = getattr(owner, "_dock_hub", None)
    dock = getattr(hub, "trees_dock", None) if hub is not None else None
    if dock is not None:
        return dock
    return owner if hasattr(owner, "_trees") else None


def _snapshot_of(dock):
    """The connection's cached whole-board snapshot — the SAME identity half a form's
    `_live_snapshot` hands to the anchor read. The DOCK has no `_live_snapshot` (that
    door lives on the forms), so the module-level `_connection_snapshot` is used;
    without it every role or parent resolve in the forest walks the board on its own
    (Ф4 of the acceptance)."""
    from .trees_dock import _connection_snapshot
    window = getattr(dock, "_main_window", None)
    return _connection_snapshot(getattr(window, "connection", None))


def open_reread_table(dock, reads_raw: dict) -> bool:
    """Show the table; apply on Accepted, write NOTHING on Cancel.

    The dock is the parent WINDOW when it is one — a dock double in the cells is
    not a QWidget, and the table must still be buildable there."""
    dialog = RereadForestDialog(dock if isinstance(dock, QWidget) else None,
                                reads_raw)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    written = apply_plan(dock, dialog.plan)
    dock._mark_dirty()
    dock._rebuild_tabs()
    logger.info(_report(dialog.plan))
    return written > 0


def _finish(dock, result: dict) -> None:
    dock._active_op = None
    open_reread_table(dock, result)


def _failed(dock, message: str) -> None:
    dock._active_op = None
    show_message(_("Reread positions failed: {error}").format(error=message),
                 _ERROR_STYLE, logger)


def run_reread_positions(owner, *, trigger=None) -> Optional[bool]:
    """Tools → Trees → «Reread positions from board…». ONE read in the worker (the
    dock's shared adapter rides in the payload, exactly like the node reread), then
    the table on the UI thread. False/None when there is nothing to read."""
    dock = _trees_dock_of(owner)
    if dock is None:
        return None
    trees = list(getattr(dock, "_trees", None) or [])
    if not trees:
        show_message(_("There are no trees to reread yet."), _ERROR_STYLE, logger)
        return False
    adapter = dock._live_adapter()
    if adapter is None:
        show_message(_("No live board connection — connect KiCad first."),
                     _ERROR_STYLE, logger)
        return False
    if socket_busy(dock._main_window.connection):
        # The ~400 ms selection tick owns the shared socket: refuse instead of
        # interleaving two transactions (the same call the node reread makes).
        return False
    context = getattr(dock, "_ctx", None)
    payload = {
        "adapter": adapter,
        "cfg": dock._cfg,
        "trees": trees,
        # A COPY: no live mapping crosses the thread boundary.
        "sheet_names": dict(getattr(context, "sheet_names", None) or {}),
        # ONE snapshot for the whole forest: it reaches the layout, every
        # `_live_offset` and every `child_live_pose` (Ф4).
        "snapshot": _snapshot_of(dock),
    }
    dock._active_op = start_long_op(
        dock._main_window.connection, (trigger,) if trigger is not None else (),
        read_forest, lambda result: _finish(dock, result),
        lambda message: _failed(dock, message), payload,
        busy_text=_("reading the board"))
    return True
