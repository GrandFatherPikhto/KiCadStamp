# gui/docks/imprint_refs_tab.py
"""The imprint's "Roles" tab — the role table of a recorded imprint, and the
"Convert to cell" button that turns it into a Cell template.

Plan: `plan_2026_09_18_scheme_list_to_cell_and_capture.md` (Д2, Р12/Р17/Р20);
design `design_2026_09_17_spoke_cell_editing.md` §10/§10а.

Why it exists. Denis's live case (2026-09-18): the imprint `zummer` was recorded
and then the path stopped — an imprint carries literal refdes and no roles, so
there was nothing to clone and no way to see what it was made of. This tab is the
visible beginning of the "imprint -> cell" path: the record's own components, one
Role cell each, one cluster for the whole imprint, and the button that builds
`cells:` out of it.

WHERE the values go (Р17/Р18): the table writes ONLY into this project's override
store — our values win over the board, take effect at once and survive an F8 —
and NOTHING is written onto the board or into the schematic. That is why this tab
needs no KiCad at all: the store is a file next to the profile
(`utils.paths.overrides_path_for_config`), and the write is one atomic save with
a backup. Guard С12 is exactly that: an adapter spy is never called, neither on
recording nor by the table at all.

WHAT the table reads. Only what the page already holds: the imprint record (its
literal refs and geometry), the board snapshot the page was fed (for the board's
own Role values and the symbol uuids — never a live adapter call) and the
override store. The rows are the RECORD's components, in the record's order;
"Take selection"/"Add selection" have no meaning here (an imprint is a fixed
snapshot), so this is deliberately NOT the cell editor's widget with a flag: it
shares the MODEL (`gui/role_table_model.py`), the table and the cell delegate
(`gui/docks/cell_refs_tab.py`) and nothing else.

The CLUSTER is ONE field above the table (Р20, Денис 2026-09-18: «Кластер один на
отпечаток») — there is no cluster column at all, so two different clusters in one
imprint cannot even be typed (guard С23). The field doubles as the cell's
cluster: a cell is cloned BY its cluster, and the conversion refuses without one.

С9 (the other half of the button): "Convert to cell" writes `cells:` into the
ROOT config and touches NOTHING else — not the entity (`imprint:` stays, the
record keeps working as it did), not the board, not the store. Switching the
entity to `cell:` is the dangerous action (the copper can double on the next
redraw) and stays with stage 4's dry run.
"""
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.config import load_cell
from kicadstamp.config_writer import merge_write
from kicadstamp.exceptions import ValidationError
from kicadstamp.field_overrides import SOURCE_IMPRINT_TABLE
from kicadstamp.i18n import _
from kicadstamp.imprint_cell import cell_name_for_cluster, imprint_to_cell_plan
from kicadstamp.utils.paths import overrides_path_for_config

from ..role_table_model import (
    apply_cluster_to_all,
    apply_overrides,
    build_override_updates,
    can_write,
    default_cluster,
    records_from_items,
    rows_from_state,
    skipped_text,
)
from ._common import (
    ERROR_STYLE as _ERROR_STYLE,
    SUCCESS_STYLE as _SUCCESS_STYLE,
    WARN_STYLE as _WARN_STYLE,
    show_message,
)
from .cell_refs_tab import COL_REF, COL_ROLE, RefsTable, _ComboDelegate

logger = logging.getLogger(__name__)


class ImprintRefsTab(QWidget):
    """The Roles tab of the imprint page (a plain QWidget, like every other
    Config right-QView page)."""

    # After `cells:` was written — the page refreshes the Config tree with it.
    saved = pyqtSignal()

    def __init__(self, main_window=None, connection=None, parent=None):
        super().__init__(parent)
        self._main_window = main_window
        self._connection = connection
        self._root_path: Optional[Path] = None
        self._imprint_name: Optional[str] = None
        self._record: Any = None
        self._records: list = []
        self._overrides = None
        self._rows: list = []
        self._loading = False
        # One Qt object per tab, never one per cell per render (the rule the
        # cell table learned the hard way — see cell_refs_tab._style_item).
        self._grey_brush = QBrush(QColor("#888888"))
        # Fired after a store record (Т5): DockHub re-reads that store wherever
        # another pane holds a copy and recomputes Pending changes.
        self.on_overrides_written = None
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # ONE cluster for the whole imprint (Р20) — no column, no per-row cell.
        cluster_row = QHBoxLayout()
        cluster_row.addWidget(QLabel(_("Cluster of this imprint:")))
        self._cluster_edit = QLineEdit()
        self._cluster_edit.setToolTip(
            _("The CLUSTER of this imprint — one for the whole record: a cell is "
              "cloned by its cluster, so the conversion needs exactly one. It is "
              "recorded for every component of the table."))
        self._cluster_edit.textChanged.connect(self._on_cluster_changed)
        cluster_row.addWidget(self._cluster_edit, 1)
        layout.addLayout(cluster_row)

        self._table = RefsTable(0, 2)
        self._table.setHorizontalHeaderLabels([_("Ref"), _("Role")])
        self._table.setSelectionBehavior(RefsTable.SelectionBehavior.SelectRows)
        # 1, not 0: Qt treats an explicit 0 as "unset" and the default
        # minimumSizeHint would squeeze the panel above.
        self._table.setMinimumHeight(1)
        self._role_delegate = _ComboDelegate((), self._table)
        self._table.setItemDelegateForColumn(COL_ROLE, self._role_delegate)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table, 1)

        buttons = QHBoxLayout()
        self._write_button = QPushButton(_("Write to the store"))
        self._write_button.setToolTip(
            _("Record the Roles that differ from what is in force into this "
              "project's override store — OUR values win over the board, take "
              "effect at once and survive an F8. Nothing is written to the "
              "board, and the imprint record itself is not changed."))
        self._write_button.clicked.connect(self.write_to_store)
        self._write_button.setEnabled(False)
        buttons.addWidget(self._write_button)
        self._convert_button = QPushButton(_("Convert to cell"))
        self._convert_button.setToolTip(
            _("Build a cells: entry out of this imprint: its components with the "
              "Roles of the table, its vias and tracks, and the one cluster. "
              "Written into the ROOT config only — the entity keeps pointing at "
              "the imprint (switching it is a separate, later step). Refuses, "
              "listing every reason, while a Role is missing or repeated or the "
              "cluster is empty."))
        self._convert_button.clicked.connect(self.convert_to_cell)
        self._convert_button.setEnabled(False)
        buttons.addWidget(self._convert_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        note = QLabel(_("The Roles of this imprint: the record carries literal "
                        "refdes and no roles, so fill them here — they are "
                        "RECORDED for this project (our values win over the "
                        "board) and never written onto it. One cluster for the "
                        "whole imprint; “Convert to cell” needs every Role, no "
                        "repeats, and that cluster."))
        note.setWordWrap(True)
        layout.addWidget(note)

    # ── Context fed by the page (UI thread, no board read) ────────────────

    def set_context(self, root_path, imprint_name, record, records=None,
                    overrides=None) -> None:
        """Point the tab at one imprint record, handing it everything the page
        already holds: the ROOT, the record (its refs and geometry), the board
        SNAPSHOT the page was fed and the override store in force.

        Opening another imprint (or another project) rebuilds the rows from the
        record's own components; any other call only re-reads the board columns
        and the store columns, so nothing the user typed is lost."""
        changed = (imprint_name != self._imprint_name
                   or root_path != self._root_path)
        store_changed = overrides is not None and overrides is not self._overrides
        if overrides is not None:
            self._overrides = overrides
        new_records = None if records is None else list(records)
        if (not changed and not store_changed
                and (new_records is None or self._same_records(new_records))):
            return
        self._root_path = root_path
        self._imprint_name = imprint_name
        self._record = record
        if new_records is not None:
            self._records = new_records
        if changed:
            self._rows = self._restore_rows()
            self._set_cluster_text(self._initial_cluster())
        else:
            self._rows = self._with_board_values(self._rows)
        self._render()

    def set_overrides(self, overrides) -> None:
        """The store in force changed without a context change (another pane
        recorded, or the project opened/closed): re-render, which is where our
        columns are re-read."""
        if overrides is self._overrides:
            return
        self._overrides = overrides
        self._render()

    def set_snapshot(self, records) -> None:
        """A new board read arrived (the page's polled snapshot): refresh the
        board columns and keep everything the user typed."""
        self.set_context(self._root_path, self._imprint_name, self._record,
                         records, None)

    def clear(self) -> None:
        """Nothing is loaded (the page was cleared)."""
        self._imprint_name = None
        self._record = None
        self._rows = []
        self._set_cluster_text("")
        self._render()

    # ── Read-only views (the page's and the tests' business) ──────────────

    @property
    def rows(self) -> list:
        return self._rows

    def row_refs(self) -> list:
        return [r.ref for r in self._rows]

    def cluster_text(self) -> str:
        return self._cluster_edit.text().strip()

    def status_text(self) -> str:
        return self._status.text()

    def write_button_enabled(self) -> bool:
        return self._write_button.isEnabled()

    def convert_button_enabled(self) -> bool:
        return self._convert_button.isEnabled()

    def _role_combo_items(self) -> list:
        return self._role_delegate.choices() if self._role_delegate else []

    def roles_by_ref(self) -> dict:
        """{ref: role} the table currently stands for: the typed value, else the
        value IN FORCE (our store's, else the board's) — the same rule the
        conversion and the store batch use."""
        out = {}
        for row in self._rows:
            role = (row.role or "").strip() or (row.base_role or "")
            if role:
                out[row.ref] = role
        return out

    def suggested_cell_name(self) -> str:
        """The name the conversion would use — for the page's own label/tests."""
        return cell_name_for_cluster(self.cluster_text())

    @staticmethod
    def _record_key(record) -> tuple:
        return (record.ref, record.role or "", record.cluster or "",
                bool(record.role_field_exists),
                bool(record.cluster_field_exists))

    def _same_records(self, records) -> bool:
        return ([self._record_key(r) for r in records]
                == [self._record_key(r) for r in self._records])

    # ── Rows ──────────────────────────────────────────────────────────────

    def _restore_rows(self) -> list:
        """The rows of the just-opened imprint: its RECORD's components, with
        the board columns and our stored values taken from what the page already
        fed in (never the board)."""
        refs = [str(c.ref) for c in (getattr(self._record, "components", None)
                                     or ())]
        state = {"cluster": "", "rows": [{"ref": ref} for ref in refs]}
        rows, _cluster = rows_from_state(state, self._records)
        return apply_overrides(rows, self._overrides)

    def _with_board_values(self, rows) -> list:
        """The same rows after another snapshot tick: only the board columns and
        our store columns are re-read, the user's cells are untouched."""
        by_ref = {r.ref: r for r in self._records}
        out = []
        for row in rows:
            record = by_ref.get(row.ref)
            if record is None:
                out.append(replace(row, board_role=None, board_cluster=None,
                                   on_board=False))
            else:
                out.append(replace(
                    row, board_role=record.role, board_cluster=record.cluster,
                    role_field_exists=record.role_field_exists,
                    cluster_field_exists=record.cluster_field_exists,
                    on_board=True,
                    symbol_uuid=record.symbol_uuid or row.symbol_uuid))
        return apply_overrides(out, self._overrides)

    def _initial_cluster(self) -> str:
        """What the cluster field opens with: the value IN FORCE of the first
        row that has one (our store's wins over the board — Т2), else the
        cluster the rows agree on."""
        for row in self._rows:
            if (row.base_cluster or "").strip():
                return str(row.base_cluster).strip()
        return default_cluster(self._rows, "")

    def _set_cluster_text(self, text) -> None:
        self._loading = True
        try:
            self._cluster_edit.setText(str(text or ""))
        finally:
            self._loading = False

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render(self) -> None:
        """Rebuild the table from the rows: Ref (read-only) + Role (editable).
        There is no cluster column on purpose (С23) — the single field above is
        the imprint's only cluster."""
        self._loading = True
        try:
            self._table.clearContents()
            self._table.setRowCount(len(self._rows))
            for index, row in enumerate(self._rows):
                ref_item = QTableWidgetItem(row.ref)
                ref_item.setFlags(ref_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                ref_item.setToolTip(self._row_tooltip(row))
                role_item = QTableWidgetItem(
                    (row.role or "").strip() or (row.base_role or ""))
                role_item.setToolTip(self._row_tooltip(row))
                if not row.on_board:
                    role_item.setForeground(self._grey_brush)
                self._table.setItem(index, COL_REF, ref_item)
                self._table.setItem(index, COL_ROLE, role_item)
        finally:
            self._loading = False
        self._role_delegate.set_choices(self._role_choices())
        self._refresh_status()

    def _role_choices(self) -> list:
        """The Role editor's suggestions: the roles of the last board read, in a
        stable order — a hint only (free text is what makes a NEW role)."""
        return sorted({(r.role or "").strip() for r in self._records
                       if (r.role or "").strip()})

    def _row_tooltip(self, row) -> str:
        parts = []
        if row.sheet:
            sheet = "/".join(str(s) for s in row.sheet if s)
            if sheet:
                parts.append(_("sheet: {sheet}").format(sheet=sheet))
        parts.append(_("role on board: {role}").format(role=row.board_role or "—"))
        if not row.on_board:
            parts.append(_("not in the last board read — the refdes has no "
                           "symbol uuid, so its Role cannot be recorded"))
        return "\n".join(parts)

    def _refresh_status(self) -> None:
        if self._imprint_name is None:
            self._status.setText(_("Pick an Imprint in the Config tree."))
            self._status.setStyleSheet("")
            self._write_button.setEnabled(False)
            self._convert_button.setEnabled(False)
            return
        self._status.setText("")
        self._status.setStyleSheet("")
        # The store button needs a PROJECT (recording is a file write next to the
        # profile — no KiCad, no adapter: guard С12) and something that differs.
        self._write_button.setEnabled(can_write(self._rows)
                                      and self._overrides is not None)
        self._convert_button.setEnabled(self._record is not None)

    def _show(self, text: str, style: str = "") -> None:
        self._status.setText(text)
        self._status.setStyleSheet(style)
        show_message(text, style, logger)

    # ── Table edits ───────────────────────────────────────────────────────

    def _on_item_changed(self, item) -> None:
        if self._loading:
            return
        index, column = item.row(), item.column()
        if index >= len(self._rows) or column != COL_ROLE:
            return
        row = self._rows[index]
        typed = item.text()
        # A cell holding the value in force means "leave it alone" — the model's
        # empty cell (see gui/role_table_model.py), so re-typing an identical
        # value records nothing and touching the table stays free of effects.
        row.role = "" if typed.strip() == (row.base_role or "") else typed
        self._rows = apply_overrides(self._rows, self._overrides)
        self._refresh_status()

    def _on_cluster_changed(self, _text: str) -> None:
        """The single cluster field IS the imprint's cluster: every row carries
        it (Р20). Empty means "no cluster" — and the conversion refuses then."""
        if self._loading:
            return
        self._rows, _unfilled = apply_cluster_to_all(self._rows,
                                                     self.cluster_text())
        self._refresh_status()

    # ── Recording into the store (no board, no KiCad) ─────────────────────

    def _store_path(self):
        if self._root_path is None:
            return None
        return overrides_path_for_config(str(self._root_path))

    def _reload_store(self) -> bool:
        """Re-read the store from its FILE before recording into it: another
        pane in this process may have recorded since we were handed our copy,
        and a write must never be based on a stale picture of the file."""
        path = self._store_path()
        if path is None:
            return False
        from kicadstamp.field_overrides import load_field_overrides
        self._overrides = load_field_overrides(str(path))
        return True

    def write_to_store(self) -> None:
        """Record every Role/cluster that differs from the value in force into
        the project's override store — ONE atomic save, keyed by symbol uuid,
        never a board write (С12) and never a change of the imprint record."""
        if self._imprint_name is None:
            return
        if not self._reload_store():
            self._show(_("Open a project first — the override store lives next "
                         "to its profile config."), _WARN_STYLE)
            return
        plan = build_override_updates(self._rows)
        if not plan.updates:
            message = _("nothing to record — every Role already matches what is "
                        "in force")
            if plan.skipped:
                message += " " + _("skipped: {refs}").format(
                    refs=skipped_text(plan.skipped))
            self._show(message, _WARN_STYLE)
            return
        by_uuid = {r.symbol_uuid: r.ref for r in self._rows if r.symbol_uuid}
        for symbol_uuid, field, value in plan.updates:
            self._overrides.set(symbol_uuid, by_uuid.get(symbol_uuid, ""),
                                field, value, SOURCE_IMPRINT_TABLE)
        try:
            self._overrides.save()
        except (OSError, ValidationError) as e:
            self._show(_("Could not save the override store: {error}").format(
                error=e), _ERROR_STYLE)
            return
        self._rows = apply_overrides(self._rows, self._overrides)
        message = _("{count} value(s) recorded for this project — they win over "
                    "the board and survive an F8").format(count=len(plan.updates))
        if plan.skipped:
            message += "; " + _("skipped: {refs}").format(
                refs=skipped_text(plan.skipped))
        self._refresh_status()
        self._show(message, _SUCCESS_STYLE)
        if self.on_overrides_written:
            self.on_overrides_written()

    # ── Convert to cell (config only, nothing else) ───────────────────────

    def convert_to_cell(self) -> None:
        """Build a `cells:` entry from this imprint and merge it into the ROOT
        config. Refuses (with EVERY reason at once, and writes nothing) while a
        Role is missing or repeated or the cluster is empty (С2); the entity is
        NOT touched (С9) — it keeps pointing at the imprint."""
        if self._imprint_name is None or self._record is None:
            self._show(_("Open an Imprint record first."), _WARN_STYLE)
            return
        if self._root_path is None:
            self._show(_("Open a project first — the cell is written next to its "
                         "profile config."), _WARN_STYLE)
            return
        plan = imprint_to_cell_plan(self._record, self.roles_by_ref(),
                                    self.cluster_text())
        if plan["problems"]:
            self._show(_("cannot convert to a cell: {reasons}").format(
                reasons="; ".join(plan["problems"])), _ERROR_STYLE)
            return
        name, cells = plan["name"], plan["cells"]
        try:
            load_cell(name, cells[name])  # validate BEFORE any write
        except ValidationError as e:
            self._show(_("cannot convert to a cell: {error}").format(error=e),
                       _ERROR_STYLE)
            return
        try:
            merge_write(Path(self._root_path), {"cells": cells}, section="cells")
        except (ValidationError, OSError) as e:
            self._show(_("Convert to cell failed: {error}").format(error=e),
                       _ERROR_STYLE)
            return
        summary = plan["summary"]
        self._show(
            _("Cell {name!r} created from imprint {imprint!r} — {components} "
              "component(s), {vias} via(s), {tracks} track(s). The entity is NOT "
              "switched: it still points at the imprint.").format(
                name=name, imprint=self._imprint_name,
                components=summary["components"], vias=summary["vias"],
                tracks=summary["tracks"]),
            _SUCCESS_STYLE)
        self.saved.emit()

    # ── Helpers for the page ──────────────────────────────────────────────

    @staticmethod
    def records_from_snapshot(snapshot) -> list:
        """The page's snapshot as role-table records — offered here so the page
        does not have to import the model for one call."""
        return records_from_items(snapshot)
