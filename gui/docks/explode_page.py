# gui/docks/explode_page.py
"""The "Разнос" tab (Р2, plan ``plan_2026_10_05_explode_r2_r3_tab_and_reread.md``;
design §2). A Config right-QView page: the read-only plan (plan_explode), the
area/gap fields, the "Уедут" line, the inter-cluster table with its ticks, and
the Разнести / Перечитать / Вернуть buttons.

Every board op here runs on the worker through gui/worker.py:start_long_op with
``allowed_while_exploded=True`` — this tab is exactly the one place allowed to
touch the board while the clusters are shifted aside; everything else is refused
by the gate ExplodeGuard installs.

Р2 leaves "Перечитать ячейку по выделению" DISABLED (Р3 wires it). The plan's own
text formatter is shared with the CLI (kicadstamp/explode_format.py).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel,
                             QMessageBox, QPushButton, QTreeWidget,
                             QTreeWidgetItem, QTreeWidgetItemIterator,
                             QVBoxLayout, QWidget)

from kicadstamp.i18n import _

from .. import settings
from ..worker import start_long_op
from ._common import ERROR_STYLE, SUCCESS_STYLE, WARN_STYLE, show_message

logger = logging.getLogger(__name__)

_TEE_BG = QColor(255, 250, 205)      # yellow — tee / multi rows
_MARGIN_KEY = "explode_margin_mm"
_GAP_KEY = "explode_gap_mm"
_COLUMNS = 5


# ── worker functions (pure: adapter + plain args, no widgets) ────────────────

def plan_worker(adapter, config_path, cell, cluster, sheet, margin, gap,
                overrides) -> Any:
    """Worker: load the config and build the plan. Read-only."""
    from kicadstamp.config.loader import load_config
    from kicadstamp.explode import plan_explode
    cfg, ctx = load_config(config_path)
    return plan_explode(
        adapter, cfg, str(config_path), cell, cluster, sheet,
        dict(getattr(ctx, "sheet_names", {}) or {}),
        margin_mm=margin, gap_mm=gap, tick_overrides=dict(overrides or {}))


def explode_worker(adapter, plan) -> list:
    """Worker: journal first, then shift in one transaction (core)."""
    from kicadstamp.explode_journal import explode
    return explode(adapter, plan)


def restore_worker(adapter) -> list:
    """Worker: put everything back by the recorded absolute positions."""
    from kicadstamp.explode import ExplodeError
    from kicadstamp.explode_journal import journal_path, restore
    path = journal_path(adapter)
    if not path.is_file():
        raise ExplodeError(_("no explode journal for this board — nothing to restore"))
    return restore(adapter, path)


def select_worker(adapter, item) -> None:
    """Worker: highlight ONE piece on the board (like "Select cell")."""
    adapter.select_items([item])


class ExplodePage(QWidget):
    """See the module docstring. Owned by DockHub; one per window."""

    def __init__(self, main_window, config_tree_dock, guard, parent=None):
        super().__init__(parent)
        self._main_window = main_window
        self._config_tree_dock = config_tree_dock
        self._guard = guard
        self._root_path = None
        self._cell_name: Optional[str] = None
        self._cluster: Optional[str] = None
        self._sheet = None
        self._plan = None
        self._tick_overrides: dict = {}
        self._active_op = None
        self._building = False
        self._exploded_from_journal = False
        self._build_ui()
        self._guard.changed.connect(self._set_exploded_ui)
        self._set_exploded_ui(self._guard.active)

    # ── construction ────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.header_label = QLabel(_("Разнос: ячейка не выбрана"))
        self.header_label.setObjectName("explode_header")
        layout.addWidget(self.header_label)

        # The post-crash / exploded banner.
        self.banner = QWidget()
        banner_row = QHBoxLayout(self.banner)
        banner_row.setContentsMargins(0, 0, 0, 0)
        self.banner_label = QLabel("")
        banner_row.addWidget(self.banner_label, 1)
        self.banner_restore = QPushButton(_("Вернуть"))
        self.banner_show = QPushButton(_("Показать журнал"))
        self.banner_forget = QPushButton(_("Забыть журнал"))
        for b in (self.banner_restore, self.banner_show, self.banner_forget):
            banner_row.addWidget(b)
        layout.addWidget(self.banner)
        self.banner.setVisible(False)

        fields = QHBoxLayout()
        fields.addWidget(QLabel(_("Поле области:")))
        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0.0, 100.0)
        self.margin_spin.setSuffix(_(" мм"))
        self.margin_spin.setValue(float(settings.state.get(_MARGIN_KEY, 5.0) or 5.0))
        fields.addWidget(self.margin_spin)
        fields.addWidget(QLabel(_("Зазор:")))
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0.0, 100.0)
        self.gap_spin.setSuffix(_(" мм"))
        self.gap_spin.setValue(float(settings.state.get(_GAP_KEY, 5.0) or 5.0))
        fields.addWidget(self.gap_spin)
        self.recalc_button = QPushButton(_("Пересчитать"))
        fields.addWidget(self.recalc_button)
        fields.addStretch(1)
        layout.addLayout(fields)

        self.leaves_label = QLabel("")
        self.leaves_label.setWordWrap(True)
        layout.addWidget(self.leaves_label)

        layout.addWidget(QLabel(_("Межкластерная медь у ячейки:")))
        self.tree = QTreeWidget()
        self.tree.setColumnCount(_COLUMNS)
        self.tree.setHeaderLabels([
            _("Kind"), _("Net"), _("Layer"), _("Length"), _("Touches")])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.explode_button = QPushButton(_("Разнести"))
        self.reread_button = QPushButton(_("Перечитать ячейку по выделению"))
        self.reread_button.setEnabled(False)          # Р3 wires this
        self.reread_button.setToolTip(_("Р3"))
        self.restore_button = QPushButton(_("Вернуть"))
        for b in (self.explode_button, self.reread_button, self.restore_button):
            buttons.addWidget(b, 1)
        layout.addLayout(buttons)

        self.recalc_button.clicked.connect(self._recalculate)
        self.explode_button.clicked.connect(self._explode)
        self.restore_button.clicked.connect(self._restore_clicked)
        self.banner_restore.clicked.connect(self._restore_clicked)
        self.banner_show.clicked.connect(self._show_journal)
        self.banner_forget.clicked.connect(self._forget_journal)
        self.margin_spin.valueChanged.connect(self._on_field_changed)
        self.gap_spin.valueChanged.connect(self._on_field_changed)
        self.tree.itemChanged.connect(self._on_tick_changed)
        self.tree.itemClicked.connect(self._on_row_clicked)

    # ── doors ───────────────────────────────────────────────────────────────
    def set_root_path(self, path) -> None:
        self._root_path = path

    @property
    def exploded_from_journal(self) -> bool:
        """True when the open state came from the journal on disk (a restart
        after a crash), not from a plan built here."""
        return self._exploded_from_journal

    def open_instance(self, name: str, cluster=None, sheet=None) -> None:
        """The ONE entry point of every door: show this cell's instance here."""
        self._cell_name = name
        self._cluster = cluster
        self._sheet = sheet
        self._exploded_from_journal = False
        self._guard.refresh(self._adapter())
        self._update_header()
        self._recalculate()

    def open_from_journal(self, journal) -> None:
        """Post-crash / restart: the tab is already exploded (from the journal).
        The board is shifted aside, so a fresh plan would describe the WRONG
        board — the table is left read-only and the banner summarises the
        journal instead."""
        self._exploded_from_journal = True
        self._cell_name = (journal or {}).get("cell")
        self._cluster = (journal or {}).get("cluster")
        self._sheet = (journal or {}).get("sheet")
        self._plan = None
        self._rebuild()
        self._update_header()
        self._set_exploded_ui(True)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _connection(self):
        return getattr(self._main_window, "connection", None)

    def _adapter(self):
        board = getattr(self._connection(), "board", None)
        return getattr(board, "adapter", None)

    def _guard_widgets(self):
        return (self.margin_spin, self.gap_spin, self.recalc_button,
                self.explode_button, self.restore_button)

    def _where(self) -> str:
        if self._sheet:
            return f"{self._cluster}/{self._sheet}"
        return str(self._cluster or "?")

    def _update_header(self) -> None:
        state = _("разнесено") if self._guard.active else _("собрано")
        if self._cell_name:
            self.header_label.setText(_(
                "Разнос: {cell} — {where}   [состояние: {state}]").format(
                    cell=self._cell_name, where=self._where(), state=state))
        else:
            self.header_label.setText(_("Разнос: ячейка не выбрана"))

    # ── plan ────────────────────────────────────────────────────────────────
    def _on_field_changed(self, _value) -> None:
        self._recalculate()

    def _recalculate(self) -> None:
        if self._guard.active or self._cell_name is None:
            return
        adapter = self._adapter()
        if adapter is None or self._root_path is None:
            show_message(_("Set the project root and connect to the board first."),
                         ERROR_STYLE, logger)
            return
        settings.state.set(_MARGIN_KEY, float(self.margin_spin.value()))
        settings.state.set(_GAP_KEY, float(self.gap_spin.value()))
        self._active_op = start_long_op(
            self._connection(), self._guard_widgets(), plan_worker,
            self._finish_plan, self._on_op_failed,
            adapter, str(self._root_path), self._cell_name, self._cluster,
            self._sheet, float(self.margin_spin.value()),
            float(self.gap_spin.value()), dict(self._tick_overrides),
            busy_text=_("planning"), allowed_while_exploded=True)

    def _finish_plan(self, plan) -> None:
        self._plan = plan
        self._rebuild()
        for warning in plan.warnings:
            show_message(warning, WARN_STYLE, logger)

    def _rebuild(self) -> None:
        self._building = True
        try:
            self.tree.clear()
            plan = self._plan
            if plan is None:
                self.leaves_label.setText("")
                return
            leaves = ", ".join(
                _("{where} ({fps} деталей, {cu} кусков меди)").format(
                    where=inst.label, fps=len(inst.footprints),
                    cu=len(inst.copper))
                for inst in plan.instances)
            self.leaves_label.setText(_("Уедут: {leaves}").format(
                leaves=leaves or _("(никто)")))
            groups: dict = {}
            for piece in plan.table:
                groups.setdefault(piece.record, []).append(piece)
            for record, pieces in groups.items():
                top = QTreeWidgetItem(self.tree)
                top.setText(0, _("net_traces {name}   ({n})").format(
                    name=record, n=len(pieces)))
                top.setFirstColumnSpanned(True)
                top.setExpanded(True)
                for piece in pieces:
                    row = QTreeWidgetItem(top)
                    row.setText(0, piece.kind)
                    row.setText(1, piece.net)
                    row.setText(2, piece.layer)
                    row.setText(3, f"{piece.length_mm:.1f}")
                    row.setText(4, piece.touches)
                    row.setCheckState(
                        0, Qt.CheckState.Checked if piece.ticked
                        else Qt.CheckState.Unchecked)
                    row.setData(0, Qt.ItemDataRole.UserRole, piece.uuid)
                    row.setData(0, Qt.ItemDataRole.UserRole + 1, piece.item)
                    self._make_checkable(row, not self._guard.active)
                    if piece.touches in ("tee", "multi"):
                        for col in range(_COLUMNS):
                            row.setBackground(col, QBrush(_TEE_BG))
        finally:
            self._building = False
        self._update_header()
        self._set_exploded_ui(self._guard.active)

    @staticmethod
    def _make_checkable(item: QTreeWidgetItem, checkable: bool) -> None:
        """Ticks are editable ONLY before "Разнести" — the checkable flag is
        what lets the user toggle them at all."""
        flags = item.flags()
        if checkable:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        else:
            flags &= ~Qt.ItemFlag.ItemIsUserCheckable
        item.setFlags(flags)

    def _on_tick_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._building or self._guard.active:
            return
        uuid = item.data(0, Qt.ItemDataRole.UserRole)
        if not uuid:
            return
        self._tick_overrides[uuid] = item.checkState(0) == Qt.CheckState.Checked
        self._recalculate()

    def _on_row_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        live = item.data(0, Qt.ItemDataRole.UserRole + 1)
        adapter = self._adapter()
        if live is None or adapter is None:
            return
        self._active_op = start_long_op(
            self._connection(), (), select_worker, lambda _r: None,
            self._on_op_failed, adapter, live,
            allowed_while_exploded=True)

    # ── explode / restore ───────────────────────────────────────────────────
    def _explode(self) -> None:
        if self._plan is None or self._guard.active:
            return
        adapter = self._adapter()
        if adapter is None:
            show_message(_("Connect to the board first."), ERROR_STYLE, logger)
            return
        self._active_op = start_long_op(
            self._connection(), self._guard_widgets(), explode_worker,
            self._finish_explode, self._on_op_failed, adapter, self._plan,
            busy_text=_("Разнести"), allowed_while_exploded=True)

    def _finish_explode(self, lines) -> None:
        for line in lines:
            show_message(line, SUCCESS_STYLE, logger)
        self._guard.refresh(self._adapter())
        self._update_header()
        self._set_exploded_ui(self._guard.active)

    def _restore_clicked(self) -> None:
        self.request_restore()

    def request_restore(self, on_success=None, on_error=None) -> None:
        """Run the restore on the worker; call on_success() on success (used by
        the exit path to close AFTER the board is back)."""
        adapter = self._adapter()
        if adapter is None:
            message = _("Connect to the board first.")
            show_message(message, ERROR_STYLE, logger)
            if on_error is not None:
                on_error(message)
            return

        def _ok(lines):
            for line in lines:
                show_message(line, SUCCESS_STYLE, logger)
            self._guard.refresh(self._adapter())
            self._finish_restore_ui()
            if on_success is not None:
                on_success()

        def _err(message):
            self._on_op_failed(message)
            if on_error is not None:
                on_error(message)

        self._active_op = start_long_op(
            self._connection(), self._guard_widgets(), restore_worker,
            _ok, _err, adapter,
            busy_text=_("Вернуть"), allowed_while_exploded=True)

    def _finish_restore_ui(self) -> None:
        self._exploded_from_journal = False
        if self._cell_name is not None:
            self._recalculate()
        else:
            self._update_header()
        self._set_exploded_ui(self._guard.active)

    def _show_journal(self) -> None:
        journal = self._guard.journal or {}
        show_message(_(
            "explode journal: board {board}, cell {cell} on {cluster}/{sheet}, "
            "{when}, {n} item(s)").format(
                board=journal.get("board", "?"), cell=journal.get("cell", "?"),
                cluster=journal.get("cluster", "?"),
                sheet=journal.get("sheet") or "-", when=journal.get("time", "?"),
                n=len(journal.get("items", {}) or {})), WARN_STYLE, logger)

    def _forget_journal(self) -> None:
        if QMessageBox.question(
                self, _("Забыть журнал?"),
                _("The board stays shifted aside — the recorded positions will "
                  "be LOST. Continue?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        from kicadstamp.explode_journal import journal_path
        try:
            journal_path(self._adapter()).unlink()
        except OSError:
            pass
        show_message(_("explode journal deleted"), WARN_STYLE, logger)
        self._guard.refresh(self._adapter())
        self._exploded_from_journal = False
        self._update_header()
        self._set_exploded_ui(self._guard.active)

    def _on_op_failed(self, message: str) -> None:
        show_message(message, ERROR_STYLE, logger)

    # ── lock ────────────────────────────────────────────────────────────────
    def _set_exploded_ui(self, active: bool) -> None:
        self.explode_button.setEnabled(not active and self._plan is not None)
        self.restore_button.setEnabled(active)
        self.recalc_button.setEnabled(not active)
        self.margin_spin.setEnabled(not active)
        self.gap_spin.setEnabled(not active)
        # Ticks are editable only BEFORE "Разнести": clear the checkable flag on
        # every piece row while the clusters are shifted aside. setFlags emits
        # itemChanged, so this runs under the SAME guard as the rebuild — else
        # _on_tick_changed would treat it as a user tick and re-plan forever.
        prev_building = self._building
        self._building = True
        try:
            iterator = QTreeWidgetItemIterator(self.tree)
            while iterator.value():
                item = iterator.value()
                if item.data(0, Qt.ItemDataRole.UserRole):
                    self._make_checkable(item, not active)
                iterator += 1
        finally:
            self._building = prev_building
        self.banner.setVisible(bool(active))
        if active:
            journal = self._guard.journal or {}
            self.banner_label.setText(_(
                "Кластеры разнесены {when} — не сохраняй плату KiCad; "
                "сначала «Вернуть»").format(when=journal.get("time", "?")))
        self._update_header()
