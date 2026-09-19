# gui/docks/cell_refs_tab.py
"""The "Refs" tab of the cell-anchor editor — the role table that tags a board
selection from the OTHER side of the workflow.

Stage 2 of the spoke work: plan_2026_09_17_spoke_s2_role_table.md, design
design_2026_09_17_spoke_cell_editing.md §3 Р2/Р4/Р5, UI spec
plan/delta_2026_09_17_spoke_s2_refs_tab_ui.md.

Why it exists. Denis routes a pair of capacitors FIRST and invents the roles
afterwards, and a SPOKE cell's cluster holds the same Role many times over, so
until the components carry a Role "Fill from selection" on the Source tab has
nothing to identify ("role 'C_FPGA_BULK' of this cell has no footprint in
cluster 'FPGA_PWR_BANK'" — the FALSE message the design set out to remove). The
Role/Cluster panel cannot help either: "Tag selected" writes ONE role into EVERY
selected component. This tab gives a DIFFERENT role per component, plus the
cluster.

WHERE those values go changed on 2026-09-18 (plan_2026_09_18_field_overrides_store
Т5): the tab RECORDS them in the project's OVERRIDE STORE — one atomic save with a
backup — and that is its MAIN path. Our values win over the board in every
resolution, so they take effect at once and survive an F8; putting them on the
board as well is the explicit, named operation of Т5а ("Write to board" — the
second button, off the main path). Two consequences worth stating:

  * the write needs NO board and NO KiCad — it is a file write next to the
    profile (the "KiCad must be closed" rule belonged to the schematic splice);
  * it is SPARSE (С10/С11/Т5): only the rows whose value DIFFERS from what is in
    force get a record. Open the cell, look at the table, close it — the store
    gains nothing; filling it from the board wholesale is forbidden, because a
    mirror plus priority would freeze every later board edit.

Where the logic lives. Every decision (what the rows are, what differs, what the
batch is, what to warn about) is in gui/role_table_model.py, Qt-free and
adapter-free. This module is the WIDGET plus the READ worker, and it owns exactly
three things the model cannot: the table cells, the board READS and the
gui_state.json table.

The door (techdocs/me/door.md) is honoured the only way it can be in a widget:

  * the UI thread NEVER touches the adapter. Every BOARD operation — the two
    reads AND the explicit board write of Т5а — goes through start_long_op and
    runs on the worker thread; the rows of a restore, the warnings and the two
    buttons' states all come from the snapshot the page was fed (set_context),
    the store in force and gui_state.json — never from a live read;
  * the STORE path is not a board operation at all: no adapter, no socket, no
    worker (see write_to_store). That is what makes guard С8 meaningful — an
    adapter spy is simply never called by it;
  * before any board operation: socket_busy(connection) — the shared kipy REQ
    socket has exactly one owner, so a busy board is refused with a Log line,
    never queued;
  * the two write buttons follow TWO different questions and are therefore two
    separate controls: "Write to the store" follows can_write(rows) — does
    anything differ from the value IN FORCE — while "Write to board" follows
    can_write_to_board(rows) — does the BOARD lack anything this table has. Both
    ask for a project; only the board one also needs a live KiCad, which is
    refused at click time by _can_start, like the two reads.

What is deliberately NOT here: identifying the instance (that is the Source
tab's "Fill from selection"), erasing roles/cluster (Role/Cluster "Clear all"),
writing to the schematic (that is fieldstool's Apply / the CLI's
overrides-apply --to schematic), any modal window.
"""
import logging
from typing import Any, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QPushButton, QStyledItemDelegate,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.field_overrides import SOURCE_CELL_TABLE, symbol_uuid_of
from kicadstamp.i18n import _
from kicadstamp.sheet_names import resolve_sheet_path_names

from ..cell_edit_context import (
    remember_role_table,
    remembered_cell_refs,
    remembered_role_table,
)
from ..role_table_model import (
    SKIP_NO_CLUSTER_FIELD,
    SKIP_NO_ROLE_FIELD,
    SKIP_NOT_ON_BOARD,
    BoardRecord,
    add_ref,
    append_rows,
    apply_cluster_to_all,
    apply_overrides,
    build_override_updates,
    build_tag_updates,
    can_write,
    can_write_to_board,
    default_cluster,
    is_table_empty,
    records_from_items,
    refresh_board_values,
    remove_rows,
    replace_rows,
    role_choices,
    role_table_warnings,
    rows_from_refs,
    rows_from_state,
    skipped_text,
    table_to_state,
)
from ..worker import socket_busy, start_long_op
from ._common import (
    ERROR_STYLE as _ERROR_STYLE,
    SUCCESS_STYLE as _SUCCESS_STYLE,
    WARN_STYLE as _WARN_STYLE,
    configure_searchable,
    set_combo_items,
    show_message,
)

logger = logging.getLogger(__name__)

# Column indices — named so the cell wiring reads as the table does.
COL_REF = 0
COL_ROLE = 1
COL_CLUSTER = 2


# ────────────────────────────────────────────────────────────────────────────
# Worker functions (run on the worker thread via start_long_op — board IPC
# only, NO widget access)
# ────────────────────────────────────────────────────────────────────────────

def _sheet_chain(fp, sheet_names) -> tuple:
    """The footprint's sheet chain with readable names — the project's ONE
    resolution (kicadstamp.sheet_names.resolve_sheet_path_names). A resolution
    failure is not fatal: an empty chain narrows nothing and the row tooltip
    simply shows no sheet."""
    if not sheet_names:
        return tuple(getattr(fp, "sheet_path_uuids", None) or ())
    try:
        return tuple(resolve_sheet_path_names(fp, sheet_names) or ())
    except Exception:  # noqa: BLE001 — a best-effort chain, never fatal
        return ()


def read_selection_rows_worker(payload: dict) -> dict:
    """The board half of "Take selection"/"Add selection": refresh the board,
    read the selected footprints and turn each one into a BoardRecord (Role,
    Cluster, the resolved sheet chain and — via adapter.has_field — whether the
    footprint carries each field AT ALL, which is what makes a grey, unwritable
    row possible without a second read later).

    Everything that touches the board happens HERE. The board is REFRESHED
    first: the poll tick is a no-op while connected, so a selection read from an
    old cache could miss a component the user just placed."""
    adapter = payload["adapter"]
    sheet_names = dict(payload.get("sheet_names") or {})
    adapter.refresh_board()
    footprints = [item for item in (adapter.get_selected_items() or ())
                  if isinstance(item, Footprint)]
    if not footprints:
        raise ValidationError(format_fatal_error(
            _("nothing is selected on the board — select the components to tag"),
            [_("select the components of one instance (a spoke pair, say) in the "
               "PCB editor, then press the button again")]))

    def record(fp):
        # symbol_uuid (Т5): the store's key for this very component — read here,
        # where the live footprint is at hand, so a recorded value can never be
        # attached by refdes alone (an F8 re-annotation would move it).
        return BoardRecord(
            ref=fp.ref,
            role=adapter.get_field_value(fp, ROLE_FIELD_NAME),
            cluster=adapter.get_field_value(fp, CLUSTER_FIELD_NAME),
            sheet=_sheet_chain(fp, sheet_names),
            role_field_exists=adapter.has_field(fp, ROLE_FIELD_NAME),
            cluster_field_exists=adapter.has_field(fp, CLUSTER_FIELD_NAME),
            symbol_uuid=symbol_uuid_of(fp),
        )

    return {"records": [record(fp) for fp in footprints]}


def write_role_table_worker(payload: dict) -> dict:
    """The board half of "Write to board" (Т5а): resolve every ref of the batch
    to a LIVE footprint, drop the ones that cannot take the field, and send the
    rest as ONE set_field_values_bulk commit (KiCad's own Ctrl+Z then takes the
    whole table back — the same reasoning as RoleClusterTreeDock._run_tag).

    Removed in Т5 (the tab's own path became a file write) and restored in Т5а,
    unchanged in substance: the board write is still wanted — as an EXPLICIT
    action, for the tools that read the fields off the board (BOM, net classes,
    design §4.2). What it never does is touch the store: recording is the other
    button's job.

    The footprints come from a FRESH board read by ref, never from the row
    objects the UI built: those were read earlier and may be stale. The
    has_field check is repeated here PER FIELD for the same reason the snapshot
    cannot be trusted for it — a live board that lost the field would otherwise
    roll back every other component in the batch. An adapter failure comes back
    as {"error": ...} (the dock's own convention), so the socket is still
    released by start_long_op's normal path."""
    adapter = payload["adapter"]
    updates = list(payload.get("updates") or ())
    skipped = list(payload.get("skipped") or [])
    by_ref: dict = {}
    for fp in adapter.get_footprints():
        by_ref.setdefault(getattr(fp, "ref", None), fp)

    batch = []
    for ref, field, value in updates:
        fp = by_ref.get(ref)
        if fp is None:
            skipped.append((ref, SKIP_NOT_ON_BOARD))
            continue
        if not adapter.has_field(fp, field):
            skipped.append((ref, SKIP_NO_ROLE_FIELD if field == ROLE_FIELD_NAME
                            else SKIP_NO_CLUSTER_FIELD))
            continue
        batch.append((fp, field, value))
    if batch:
        touched = {fp.ref for fp, _field, _value in batch}
        try:
            adapter.set_field_values_bulk(
                batch, _("Tag roles on {count} component(s)").format(
                    count=len(touched)))
        except ValidationError as e:
            return {"error": str(e), "skipped": skipped}
    return {"count": len({fp.ref for fp, _field, _value in batch}),
            "skipped": skipped}


# ────────────────────────────────────────────────────────────────────────────
# The table (a QTableWidget subclass only for the Delete key)
# ────────────────────────────────────────────────────────────────────────────

class RefsTable(QTableWidget):
    """The three-column table. A subclass for ONE reason: Qt's Delete key, which
    the plan promises alongside the "Remove row" button (Р2а)."""

    delete_pressed = pyqtSignal()

    def keyPressEvent(self, event):  # noqa: N802 — Qt override
        if event.key() == Qt.Key.Key_Delete:
            self.delete_pressed.emit()
            return
        super().keyPressEvent(event)


class _ComboDelegate(QStyledItemDelegate):
    """The cell editor of a "to write" column: an editable combo offering the
    values that column knows, free text allowed (a Role the cell does not have
    yet is exactly how a new one is typed).

    A DELEGATE, and not a cell widget (QTableWidget.setCellWidget) — that is a
    hard-won decision. With cell widgets the full GUI run aborted the
    interpreter ("Fatal Python error: Aborted / Segmentation fault", while
    garbage-collecting; measured 2026-09-17 on this very suite, see
    done/done_2026_09_17_spoke_s2_role_table.md) no matter how the widgets were
    organised — created per render, pooled, with or without their completer.
    A delegate never attaches anything to Python: Qt creates the editor when the
    user opens the cell and owns it in C++, so the table holds no Python-owned
    widget at all. Two everyday benefits come free: a snapshot tick can no
    longer delete the editor the user is typing into, and no editor is built for
    rows nobody edits.
    """

    def __init__(self, choices=(), parent=None):
        super().__init__(parent)
        self._choices = list(choices)

    def choices(self) -> list:
        return list(self._choices)

    def set_choices(self, choices) -> None:
        self._choices = list(choices)

    def createEditor(self, parent, option, index):  # noqa: N802 — Qt override
        combo = QComboBox(parent)
        combo.setEditable(True)
        configure_searchable(combo)
        combo.addItems(self._choices)
        return combo

    def setEditorData(self, editor, index):  # noqa: N802 — Qt override
        editor.setCurrentText(str(index.data(Qt.ItemDataRole.EditRole) or ""))

    def setModelData(self, editor, model, index):  # noqa: N802 — Qt override
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):  # noqa: N802
        editor.setGeometry(option.rect)


# ────────────────────────────────────────────────────────────────────────────
# The widget
# ────────────────────────────────────────────────────────────────────────────

class RefsTabWidget(QWidget):
    """The "Refs" tab: take a board selection, give every component its own Role
    and the cluster, write the whole lot in one commit.

    The page feeds it the context it already has (set_context: the project root,
    the cell, the cell's roles, the board snapshot and the config's sheet map) —
    the widget never reaches for the board by itself on the UI thread.
    """

    def __init__(self, main_window=None, connection=None, parent=None):
        super().__init__(parent)
        self._main_window = main_window
        self._connection = connection
        self._root_path = None
        self._cell_name: Optional[str] = None
        self._cell_roles: list = []
        self._role_choices: list = []
        self._records: list = []
        self._sheet_names: dict = {}
        self._rows: list = []
        # The project's OVERRIDE STORE (Т5) — pushed in by the page with
        # set_context/set_overrides, exactly like the snapshot. None until a
        # project is open: then the rows simply have no store columns, which is
        # the pre-store table (Т7).
        self._overrides = None
        # The cell editors are DELEGATES (see _ComboDelegate), created once and
        # owned by the table — no Python-held widget per row, ever.
        self._role_delegate: Optional[_ComboDelegate] = None
        self._cluster_delegate: Optional[_ComboDelegate] = None
        # A refdes typed into the placeholder row, applied on the next event
        # loop turn (never from inside the item's own change signal).
        self._pending_hand_ref: str = ""
        # The signature of the last rendered table (see _render_signature).
        self._render_signature_cached: tuple = ()
        self._active_op = None
        self._loading = False
        # Fired after a successful RECORD (Т5) — DockHub wires it to the two
        # things a store change needs: every OTHER holder of that store re-reads
        # it, and Pending changes is recomputed. (A board refresh is requested
        # too, but nothing on the board changed: it is only how this GUI keeps
        # its snapshot fresh.)
        self.on_overrides_written = None
        # ... and the BOARD half of the same news (Т5а): fired after an explicit
        # "Write to board", where the board really did change — DockHub wires it
        # to MainWindow.request_refresh, exactly like the Role/Cluster tree and
        # fieldstool hooks (the poll tick never refreshes on its own once
        # connected, so without it the written Roles would stay invisible to
        # Pending changes until a manual Refresh).
        self.on_board_written = None
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        cluster_row = QHBoxLayout()
        cluster_row.addWidget(QLabel(_("Cluster to write:")))
        self._cluster_combo = QComboBox()
        self._cluster_combo.setEditable(True)
        configure_searchable(self._cluster_combo)
        self._cluster_combo.setToolTip(
            _("The cluster to write onto the rows below. “Apply to all rows” "
              "puts it into every row that can take it; an empty field leaves "
              "the cluster alone."))
        self._cluster_combo.currentTextChanged.connect(self._on_cluster_field_changed)
        cluster_row.addWidget(self._cluster_combo, 1)
        self._apply_cluster_button = QPushButton(_("Apply to all rows"))
        self._apply_cluster_button.setToolTip(
            _("Fill the cluster above into every row of the table that has a "
              "Cluster field."))
        self._apply_cluster_button.clicked.connect(
            lambda: self.apply_cluster_to_all_rows())
        cluster_row.addWidget(self._apply_cluster_button)
        layout.addLayout(cluster_row)

        self._table = RefsTable(0, 3)
        self._table.setHorizontalHeaderLabels(
            [_("Ref"), _("Role"), _("Cluster to write")])
        header = self._table.horizontalHeader()
        for column in (COL_REF, COL_ROLE, COL_CLUSTER):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection)
        # 1, not 0: Qt treats an explicit 0 as "unset", and the default
        # minimumSizeHint would otherwise squeeze the panel above it (the same
        # rule Pending's table follows, see test_no_widget_height_squeezing).
        self._table.setMinimumHeight(1)
        self._table.delete_pressed.connect(self.remove_selected_rows)
        # The cell editors, as delegates — see _ComboDelegate for why these are
        # NOT cell widgets.
        # A delegate is a QObject: giving it the TABLE as its Qt parent keeps
        # its lifetime tied to the C++ owner, so neither can outlive the other
        # (a Python-only reference to a delegate whose C++ table is gone is a
        # dangling pointer, and the reverse leaks one).
        self._role_delegate = _ComboDelegate((), self._table)
        self._cluster_delegate = _ComboDelegate((), self._table)
        self._table.setItemDelegateForColumn(COL_ROLE, self._role_delegate)
        self._table.setItemDelegateForColumn(COL_CLUSTER, self._cluster_delegate)
        self._table.itemChanged.connect(self._on_item_changed)
        # The two item fonts (see _style_item) — created once per tab, never
        # per cell.
        self._plain_font = QFont(self._table.font())
        self._bold_font = QFont(self._table.font())
        self._bold_font.setBold(True)
        # The grey of an unwritable cell, built once for the same reason as the
        # fonts above: one Qt object per tab instead of one per cell per render.
        self._grey_brush = QBrush(QColor("#888888"))
        layout.addWidget(self._table, 1)

        buttons = QHBoxLayout()
        self._take_button = QPushButton(_("Take selection"))
        self._take_button.setToolTip(
            _("Read the components selected on the board and REPLACE the table "
              "with them (their Role and Cluster come from the board)."))
        self._take_button.clicked.connect(self.take_selection)
        buttons.addWidget(self._take_button)
        self._add_button = QPushButton(_("Add selection"))
        self._add_button.setToolTip(
            _("Read the components selected on the board and APPEND the ones "
              "the table does not have yet."))
        self._add_button.clicked.connect(self.add_selection)
        buttons.addWidget(self._add_button)
        self._remove_button = QPushButton(_("Remove row"))
        self._remove_button.setToolTip(
            _("Drop the selected rows from the table (Delete does the same). "
              "The board and the identified refs are not touched."))
        self._remove_button.clicked.connect(self.remove_selected_rows)
        buttons.addWidget(self._remove_button)
        self._clear_button = QPushButton(_("Clear"))
        self._clear_button.setToolTip(_("Empty the whole table."))
        self._clear_button.clicked.connect(self.clear_rows)
        buttons.addWidget(self._clear_button)
        layout.addLayout(buttons)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._write_button = QPushButton(_("Write to the store"))
        self._write_button.setToolTip(
            _("Record the values that differ from what is in force into this "
              "project's override store — OUR values win over the board, take "
              "effect at once and survive an F8. Nothing is written to the "
              "board; that is the separate “Write to board” action. One atomic "
              "save with a backup."))
        self._write_button.clicked.connect(self.write_to_store)
        self._write_button.setEnabled(False)
        layout.addWidget(self._write_button)

        # Т5а: the other direction, explicit and named. The store is the source
        # of truth, but the BOARD is what foreign tools read (BOM, net classes),
        # so putting our values back onto it stays possible — off the main path,
        # as this button. A BOARD operation (live KiCad, the socket gate, the
        # worker thread), and it never touches the store.
        self._board_write_button = QPushButton(_("Write to board"))
        self._board_write_button.setToolTip(
            _("Put this table's values ONTO the live board in ONE commit — a "
              "single Ctrl+Z in KiCad takes the whole table back. Only what "
              "differs from the board is written; a footprint whose field is "
              "missing there is skipped by name. The override store is not "
              "touched: this action is for the tools that read the board, not "
              "the source of truth."))
        self._board_write_button.clicked.connect(self.write_to_board)
        self._board_write_button.setEnabled(False)
        layout.addWidget(self._board_write_button)

        note = QLabel(_("Tag roles from a board selection: take the selection, "
                        "give every component its Role — the batch is RECORDED "
                        "for this project (our values win over the board), and "
                        "reaches the board only through the explicit “Write to "
                        "board” button."))
        note.setWordWrap(True)
        layout.addWidget(note)

    # ── Context fed by the page (UI thread, no board read) ────────────────

    def set_context(self, root_path, cell_name, cell_roles, records=None,
                    sheet_names=None, overrides=None) -> None:
        """Point the tab at a cell and hand it the snapshot the page already
        holds. Nothing here touches the board (door rule 6): the board columns,
        the suggestions and the warnings all come from `records` — the page's own
        read — gui_state.json and the override store of the open project.

        `overrides` (Optional[kicadstamp.field_overrides.FieldOverrides]) is the
        store in force (Т5). It counts as a change only when it is a DIFFERENT
        object (the project switched): the page hands the same one on every form
        reload, and an unchanged store must never force a re-render.

        Opening ANOTHER cell (or another project) rebuilds the rows: the saved
        table of that cell, else the refs of its identified instance, else an
        empty table. Any other call (a snapshot tick, a form reload) only
        re-reads the board columns, so the user's own cells survive."""
        cell_changed = (cell_name != self._cell_name
                        or root_path != self._root_path)
        store_changed = overrides is not None and overrides is not self._overrides
        if overrides is not None:
            self._overrides = overrides
        new_records = None if records is None else list(records)
        # Early exit on an UNCHANGED context: the page calls this on every form
        # reload, and re-rendering would throw away whatever the user is typing
        # in a Role or Cluster cell (Qt deletes the editor under the cursor).
        if (not cell_changed and not store_changed
                and list(cell_roles or ()) == self._cell_roles
                and (new_records is None or self._same_records(new_records))):
            return
        self._root_path = root_path
        self._cell_name = cell_name
        self._cell_roles = list(cell_roles or ())
        self._role_choices = role_choices(self._cell_roles)
        if sheet_names is not None:
            self._sheet_names = dict(sheet_names or {})
        if new_records is not None:
            self._records = new_records
        if cell_changed:
            rows, cluster = self._restore_rows()
            self._rows = rows
            self._set_cluster_text(cluster)
        else:
            self._rows = refresh_board_values(self._rows, self._records)
        self._loading = True
        try:
            set_combo_items(self._cluster_combo, self._cluster_values())
        finally:
            self._loading = False
        self._render()

    def set_snapshot(self, records) -> None:
        """A new board read arrived: refresh the board columns and the cluster
        suggestions, keep everything the user typed.

        An UNCHANGED read is a no-op: the ~2s poll re-pushes the same snapshot
        essentially every tick, and rebuilding the table would delete the editor
        the user is typing into (the same "early exit on an unchanged list" idea
        set_combo_items follows)."""
        new_records = list(records or ())
        if self._same_records(new_records):
            return
        self._records = new_records
        self._rows = refresh_board_values(self._rows, self._records)
        self._loading = True
        try:
            set_combo_items(self._cluster_combo, self._cluster_values())
        finally:
            self._loading = False
        self._render()

    def set_cell_roles(self, cell_roles) -> None:
        """The cell's own roles changed (its entry was edited): re-fill the Role
        suggestions, keep the table as it is."""
        cell_roles = list(cell_roles or ())
        if cell_roles == self._cell_roles:
            return
        self._cell_roles = cell_roles
        self._role_choices = role_choices(self._cell_roles)
        self._render()

    def fill_from_refs(self, role_to_ref) -> None:
        """The Source tab's "Fill from selection" identified an instance: show it
        here as a table — but ONLY into an empty one (Р2). A table the user has
        already filled in is theirs, and overwriting it would silently discard
        work the identification knows nothing about."""
        if not is_table_empty(self._rows):
            return
        rows = rows_from_refs(role_to_ref or {}, self._records)
        if not rows:
            return
        self._rows = rows
        self._set_cluster_text(default_cluster(rows, self.cluster_text()))
        self._render()
        self._after_table_change()

    # ── Read-only views of the state (the tests' and the page's business) ──

    @property
    def rows(self) -> list:
        return self._rows

    def row_refs(self) -> list:
        return [r.ref for r in self._rows]

    def status_text(self) -> str:
        return self._status.text()

    def cluster_text(self) -> str:
        return self._cluster_combo.currentText().strip()

    def write_button_enabled(self) -> bool:
        return self._write_button.isEnabled()

    def board_write_button_enabled(self) -> bool:
        """The BOARD button's state — a DIFFERENT question from
        write_button_enabled (see can_write_to_board in gui/role_table_model.py)."""
        return self._board_write_button.isEnabled()

    def _role_combo_items(self) -> list:
        """The choices the Role editor offers (guard С11: THIS cell's roles, in
        the cell's own order — read straight off the delegate)."""
        return self._role_delegate.choices() if self._role_delegate else []

    @staticmethod
    def _record_key(record) -> tuple:
        return (record.ref, record.role or "", record.cluster or "",
                bool(record.role_field_exists),
                bool(record.cluster_field_exists))

    def _same_records(self, records) -> bool:
        """True when this board read says exactly what the previous one said."""
        return ([self._record_key(r) for r in records]
                == [self._record_key(r) for r in self._records])

    # ── The store in force (Т5) ───────────────────────────────────────────

    def set_overrides(self, overrides) -> None:
        """The store in force changed without a context change — another pane
        recorded into it, or the project opened/closed. Re-render, which is where
        our columns are re-read (see _render/_overlaid), so the table, its
        "differs" marks and the write button follow our values at once."""
        if overrides is self._overrides:
            return
        self._overrides = overrides
        self._render()

    def _overlaid(self, rows) -> list:
        """The rows with OUR store columns filled — the ONE place the store
        touches the table, so every path that builds or re-reads rows goes
        through it (see apply_overrides in gui/role_table_model.py for the rule:
        no store, no uuid or no record all leave the columns None, and None is
        what makes the board the base again)."""
        return apply_overrides(list(rows or []), self._overrides)

    def _reload_store(self) -> bool:
        """Re-read the store from its FILE before recording into it, so a write
        always starts from what is actually on disk: the other holder in this
        process (the fieldstool window) may have recorded since we were handed
        our copy. False when there is no store at all (no project open)."""
        if self._overrides is None:
            return False
        path = getattr(self._overrides, "path", None)
        if path is None:
            return True
        from kicadstamp.field_overrides import load_field_overrides
        self._overrides = load_field_overrides(str(path))
        return True

    # ── Rendering ─────────────────────────────────────────────────────────

    def _restore_rows(self) -> tuple:
        """(rows, cluster field) for a cell that was just opened: the saved
        table, else the remembered refs of its identified instance, else an
        empty table. Pure state + snapshot — never the board (guard С2л)."""
        saved = remembered_role_table(self._root_path, self._cell_name)
        if saved:
            rows, cluster = rows_from_state(saved, self._records)
            if rows:
                # The saved cluster field is the user's own choice; the rows win
                # when they all agree on one cluster (the pre-fill rule of Р3),
                # which is also what makes a saved table come back as it was.
                return rows, default_cluster(rows, cluster)
        refs = remembered_cell_refs(self._root_path, self._cell_name)
        if refs:
            rows = rows_from_refs(refs, self._records)
            if rows:
                return rows, default_cluster(rows, "")
        return [], ""

    def _cluster_values(self) -> list:
        """The cluster combo's suggestions: every cluster of the last board read
        (the snapshot the page was fed), plus whatever is typed right now."""
        values = sorted({(r.cluster or "").strip() for r in self._records
                         if (r.cluster or "").strip()})
        current = self.cluster_text()
        if current and current not in values:
            values.append(current)
        return values

    def _set_cluster_text(self, text) -> None:
        self._loading = True
        try:
            self._cluster_combo.setCurrentText(str(text or ""))
        finally:
            self._loading = False

    def _row_tooltip(self, row) -> str:
        """Everything the three narrow columns cannot show: the sheet, the
        board's own Role and cluster, and why a cell is grey."""
        parts = []
        if row.sheet:
            sheet = "/".join(str(s) for s in row.sheet if s)
            if sheet:
                parts.append(_("sheet: {sheet}").format(sheet=sheet))
        parts.append(_("role on board: {role}").format(
            role=row.board_role or "—"))
        parts.append(_("cluster on board: {cluster}").format(
            cluster=row.board_cluster or "—"))
        if not row.on_board:
            parts.append(_("not on the board snapshot — press Refresh"))
        if not row.role_field_exists:
            parts.append(_("no Role field on this footprint — cannot be tagged"))
        if not row.cluster_field_exists:
            parts.append(_("no Cluster field on this footprint — the cluster "
                           "will not be written"))
        return "\n".join(parts)

    def _style_item(self, item: QTableWidgetItem, differs: bool) -> None:
        """Mark a to-write cell that DIFFERS from the board. Bold, not a colour:
        the theme has no "changed" colour and inventing one here would fight the
        colour-scheme setting (gui/color_schemes.py).

        The two fonts are built ONCE (see _build_ui) and handed to every item.
        Asking each item for `item.font()`, bolding that copy and setting it back
        was measured to abort the interpreter under the full GUI test run
        ("Fatal Python error: Aborted", while garbage-collecting, 2026-09-17):
        that call returns a fresh Python-wrapped QFont per cell per render, and
        with a few hundred of them per test session the collector walked into a
        damaged heap. Two shared fonts keep the count at exactly two per tab."""
        item.setFont(self._bold_font if differs else self._plain_font)

    @staticmethod
    def _read_only(flags):
        """`flags` without the editable bit — a cell the user cannot type into
        (the Ref column, and any field the footprint does not carry)."""
        return flags & ~Qt.ItemFlag.ItemIsEditable

    def _ref_item(self, row) -> QTableWidgetItem:
        item = QTableWidgetItem(row.ref)
        item.setFlags(self._read_only(item.flags()))
        item.setToolTip(self._row_tooltip(row))
        return item

    def _to_write_item(self, row, value, exists: bool, differs: bool
                       ) -> QTableWidgetItem:
        """One "to write" cell: the value, bold when it differs from the board,
        grey and NOT editable when the footprint has no such field (design
        §2.3 — H1-H4 have no Role field, R37 has no Cluster field)."""
        item = QTableWidgetItem(value or "")
        if not exists:
            item.setFlags(self._read_only(item.flags()))
            item.setForeground(self._grey_brush)
        item.setToolTip(self._row_tooltip(row))
        self._style_item(item, differs)
        return item

    def _placeholder_item(self) -> QTableWidgetItem:
        """The last row: type a refdes by hand (checked against the page's own
        snapshot, never the board)."""
        item = QTableWidgetItem("")
        item.setToolTip(_("Add a component by its refdes — checked against the "
                          "last board read, no board access needed."))
        self._style_item(item, False)
        return item

    def _render_signature(self) -> tuple:
        """Everything a rendered CELL shows. An UNCHANGED signature means the
        table itself already looks right, so the REBUILD is skipped — the same
        "early exit on unchanged input" idea the combo refills use, and the
        cheapest way to keep the Python heap quiet on a project with hundreds of
        components (the GUI pushes a snapshot every ~2s).

        It governs the rebuild ONLY. The delegate hints and the status line are
        deliberately kept out of this signature and refreshed on every render —
        see _render."""
        return tuple(
            (r.ref, r.role, r.cluster, r.board_role, r.board_cluster,
             r.role_field_exists, r.cluster_field_exists, r.on_board)
            for r in self._rows)

    def _render(self) -> None:
        """Rebuild the table from the rows, then the placeholder row — and, on
        EVERY call, the two things that live OUTSIDE the table: the delegate
        hints and the status/write-button line (Р3, plan 2а §1.2).

        The signature decides the REBUILD only, and skipping an unchanged rebuild
        is not just an optimisation: it is what keeps a snapshot tick from
        deleting the editor the user is typing into (stage 2 Δ9) on a project
        where the GUI pushes a whole-board snapshot every ~2s.

        The hints and the status must NOT ride on that signature. Two everyday
        cases leave the rows untouched, so no rebuild ever happens — yet both
        have to be visible at once:
          * the cell's OWN ROLES changed (the entry was edited and the form
            reloaded, which feeds set_context the new roles), and the Role editor
            must offer them without reopening the cell;
          * the ADAPTER appeared (KiCad connected late), and "Write to board" must
            follow the board it now has.
        Both are guarded (С2/С3), and both were live bugs before this change.

        The cell editors are DELEGATES (see _ComboDelegate): the table holds no
        Python-owned widget, so nothing here can be destroyed by a garbage
        collection — which is what used to abort the interpreter under the full
        GUI run."""
        # OUR values go into the rows first (Т5): every comparison below — the
        # "differs" marks, the warnings, can_write — must be against the value IN
        # FORCE, or the table would promise a record the resolver ignores.
        self._rows = self._overlaid(self._rows)
        signature = self._render_signature()
        if signature != self._render_signature_cached:
            self._render_signature_cached = signature
            row_count_changed = self._table.rowCount() != len(self._rows) + 1
            self._loading = True
            try:
                self._table.clearContents()
                self._table.setRowCount(len(self._rows) + 1)
                for index, row in enumerate(self._rows):
                    self._table.setItem(index, COL_REF, self._ref_item(row))
                    self._table.setItem(index, COL_ROLE, self._to_write_item(
                        row, row.role, row.role_field_exists, row.role_differs))
                    self._table.setItem(index, COL_CLUSTER,
                                        self._to_write_item(
                                            row, row.cluster,
                                            row.cluster_field_exists,
                                            row.cluster_differs))
                self._table.setItem(len(self._rows), COL_REF,
                                    self._placeholder_item())
            finally:
                self._loading = False
            if row_count_changed:
                self._table.resizeColumnsToContents()
        # Outside the signature on purpose (Р3): what the columns OFFER, and
        # whether the batch can be written at all. The values themselves live in
        # the items (the model), which is the one source of truth.
        self._role_delegate.set_choices(self._role_choices)
        self._cluster_delegate.set_choices(self._cluster_values())
        self._refresh_status()

    def _refresh_status(self) -> None:
        """The Р4 warnings (status strip + Log are one action: _show) and the two
        write buttons' states."""
        if self._cell_name is None:
            self._status.setText(_("Pick a Cell in the Config tree."))
            self._status.setStyleSheet("")
            self._write_button.setEnabled(False)
            self._board_write_button.setEnabled(False)
            return
        warnings = role_table_warnings(self._rows, self._cell_roles,
                                       self._cell_name)
        self._status.setText("  ".join(warnings))
        self._status.setStyleSheet(_WARN_STYLE if warnings else "")
        # The STORE button needs a PROJECT, not a board (Т5): recording is a file
        # write next to the profile, and it must work with KiCad closed — that is
        # the whole point of the store (guard С8: no adapter on this path).
        self._write_button.setEnabled(can_write(self._rows)
                                     and self._overrides is not None)
        # ... and the BOARD button needs no store at all: it asks whether the BOARD
        # lacks something this table has (Т5а). Whether KiCad is up is answered at
        # CLICK time by _can_start, like the two reads — so coming back to life
        # does not need a re-render.
        self._board_write_button.setEnabled(can_write_to_board(self._rows))

    def _show(self, text: str, style: str = "") -> None:
        """One status line and one Log record — the project's no-modals rule."""
        self._status.setText(text)
        self._status.setStyleSheet(style)
        show_message(text, style, logger)

    # ── Cell edits ────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """A cell was committed (the delegate wrote its editor's value back into
        the model). The row is found by the ITEM's own index — no per-row object
        is captured anywhere, and no editor widget passes through this slot."""
        if self._loading:
            return
        index, column = item.row(), item.column()
        if index >= len(self._rows):
            # The placeholder row: a refdes typed by hand.
            if column == COL_REF:
                self._pending_hand_ref = item.text()
                QTimer.singleShot(0, self._apply_hand_ref)
            return
        row = self._rows[index]
        if column == COL_ROLE:
            row.role = item.text()
            self._style_item(item, row.role_differs)
            self._after_table_change()
        elif column == COL_CLUSTER:
            row.cluster = item.text()
            self._style_item(item, row.cluster_differs)
            self._after_table_change()

    def _on_cluster_field_changed(self, _text: str) -> None:
        """The shared cluster field itself is user input and is remembered (Р2б);
        it does NOT silently rewrite the rows — "Apply to all rows" is the
        explicit way to do that (Денис 2026-09-17)."""
        if self._loading:
            return
        self._remember()

    def _apply_hand_ref(self) -> None:
        """The refdes typed into the placeholder row, applied OUTSIDE the item's
        own change signal: adding a row rebuilds the table, and rebuilding a
        table while one of its items is emitting is asking for trouble."""
        text = (self._pending_hand_ref or "").strip()
        self._pending_hand_ref = ""
        if not text or self._cell_name is None:
            return
        self.add_ref_by_hand(text)

    def _after_table_change(self) -> None:
        """Every table edit ends the same way: our columns are re-read, the
        warnings/button state are recomputed and what the user typed is
        remembered."""
        self._rows = self._overlaid(self._rows)
        self._refresh_status()
        self._remember()

    def _remember(self) -> None:
        if self._root_path is None or self._cell_name is None:
            return
        remember_role_table(self._root_path, self._cell_name,
                            table_to_state(self._rows, self.cluster_text()))

    # ── Public actions (also the buttons' slots) ──────────────────────────

    def add_ref_by_hand(self, ref: str) -> None:
        """A refdes typed into the placeholder row: checked against the snapshot
        (never the board) and refused by name when unknown or duplicated."""
        rows, error = add_ref(self._rows, ref, self._records)
        if error:
            self._show(error, _ERROR_STYLE)
            return
        self._rows = rows
        self._render()
        self._after_table_change()

    def remove_selected_rows(self) -> None:
        """Drop the selected rows from the TABLE only — the board and the
        remembered refs of the instance are untouched (Р2а/С2д)."""
        if self._cell_name is None:
            return
        refs = self._selected_refs()
        if not refs:
            return
        self._rows = remove_rows(self._rows, refs)
        self._render()
        self._after_table_change()

    def clear_rows(self) -> None:
        """Empty the table — including the remembered copy, or the deleted rows
        would come back on the next open."""
        if self._cell_name is None:
            return
        self._rows = []
        self._render()
        self._after_table_change()
        self._show(_("the table is empty"), _SUCCESS_STYLE)

    def apply_cluster_to_all_rows(self, cluster: Optional[str] = None) -> None:
        """The group fill (Денис 2026-09-17): put the chosen cluster into every
        row that can take it. Rows without a Cluster field stay as they are and
        are named in the status — they cannot be tagged by cluster, and silently
        dropping them would hide the fact."""
        if self._cell_name is None:
            return
        value = self.cluster_text() if cluster is None else str(cluster)
        self._set_cluster_text(value)
        self._rows, unfilled = apply_cluster_to_all(self._rows, value)
        if unfilled:
            self._show(
                _("{count} rows have no Cluster field and were not filled: "
                  "{refs}").format(count=len(unfilled),
                                   refs=", ".join(unfilled)), _WARN_STYLE)
        self._render()
        self._after_table_change()

    def select_rows(self, indexes) -> None:
        """Select rows by index — the button/Delete path acts on the selection."""
        self._table.clearSelection()
        for index in indexes or ():
            if 0 <= index < len(self._rows):
                self._table.selectRow(index)

    def _selected_refs(self) -> set:
        indexes = {i.row() for i in self._table.selectionModel().selectedRows()} \
            if self._table.selectionModel() is not None else set()
        return {self._rows[i].ref for i in indexes if i < len(self._rows)}

    # ── The two board operations (worker thread, door rules 3 and 4) ──────

    def _adapter(self):
        """The live adapter through the connection's PUBLIC board property —
        never a cached handle (door rule 2)."""
        board = getattr(self._connection, "board", None)
        if board is None:
            return None
        return getattr(board, "adapter", None)

    def _guard_widgets(self) -> list:
        """Every button a long-running operation must disable. The two board
        operations share the one kipy socket, so both write buttons join the
        guard: a second click during a batch would queue a second commit."""
        return [self._take_button, self._add_button, self._write_button,
                self._board_write_button]

    def _can_start(self, what: str) -> bool:
        """The ONE gate both board operations go through: a live board and a
        free shared socket, else a Log line and no operation at all."""
        if self._adapter() is None:
            self._show(_("No live board — {what} needs KiCad.").format(
                what=what), _WARN_STYLE)
            return False
        if socket_busy(self._connection):
            self._show(
                _("the board is busy (another operation is reading it) — try "
                  "again in a moment"), _WARN_STYLE)
            return False
        return True

    def take_selection(self) -> None:
        """Read the board selection and REPLACE the table with it."""
        self._start_read_op(replace=True)

    def add_selection(self) -> None:
        """Read the board selection and APPEND the components the table does not
        have yet (a ref already there keeps its row and the user's edits)."""
        self._start_read_op(replace=False)

    def _start_read_op(self, replace: bool) -> None:
        if self._cell_name is None:
            return
        if not self._can_start(_("reading the selection")):
            return
        payload = {"adapter": self._adapter(),
                   "sheet_names": dict(self._sheet_names or {}),
                   "cell_name": self._cell_name}
        self._active_op = start_long_op(
            self._connection, self._guard_widgets(), read_selection_rows_worker,
            lambda result: self._finish_read(result, replace),
            self._on_op_failed, payload)

    def _finish_read(self, result: dict, replace: bool) -> None:
        records = list(result.get("records") or ())
        self._active_op = None
        self._rows = replace_rows(records) if replace \
            else append_rows(self._rows, records)
        if replace:
            self._set_cluster_text(default_cluster(self._rows, self.cluster_text()))
        self._render()
        self._after_table_change()
        self._show(_("took {count} component(s) from the selection").format(
            count=len(self._rows)) if replace
            else _("added {count} component(s) to the table").format(
                count=len(records)), _SUCCESS_STYLE)

    def write_to_store(self) -> None:
        """Record every difference into the project's OVERRIDE STORE (Т5).

        NOT a board operation: no adapter, no socket, no worker, no KiCad — the
        store is a file next to the profile config, and the store itself writes
        it atomically with a backup (С13). Guard С8 is exactly this: an adapter
        spy is never called from here.

        The batch is SPARSE (build_override_updates: only the rows whose value
        differs from what is in force) and keyed by symbol uuid; a row without one
        is refused by name (С12) instead of being recorded under an invented key.

        The store is re-read from disk FIRST (_reload_store): the other holder in
        this process may have recorded since we were handed our copy, and a write
        must never be based on a stale picture of the file."""
        if self._cell_name is None:
            return
        if not self._reload_store():
            self._show(_("Open a project first — the override store lives next "
                         "to its profile config."), _WARN_STYLE)
            return
        plan = build_override_updates(self._rows)
        if not plan.updates:
            message = _("nothing to record — every value already matches what "
                        "is in force")
            if plan.skipped:
                message += " " + _("skipped: {refs}").format(
                    refs=skipped_text(plan.skipped))
            self._show(message, _WARN_STYLE)
            return
        by_uuid = {r.symbol_uuid: r.ref for r in self._rows if r.symbol_uuid}
        for symbol_uuid, field, value in plan.updates:
            self._overrides.set(symbol_uuid, by_uuid.get(symbol_uuid, ""),
                                field, value, SOURCE_CELL_TABLE)
        try:
            self._overrides.save()
        except (OSError, ValidationError) as e:
            self._show(_("Could not save the override store: {error}").format(
                error=e), _ERROR_STYLE)
            return
        self._rows = self._overlaid(self._rows)
        message = _("{count} value(s) recorded for this project — they win over "
                    "the board and survive an F8; writing them ONTO the board is "
                    "a separate action (“Write to board”)").format(
                        count=len(plan.updates))
        if plan.skipped:
            message += "; " + _("skipped: {refs}").format(
                refs=skipped_text(plan.skipped))
        # Р6, unchanged by Т5: the instance is still NOT identified automatically —
        # the table holds OUR values and pins nothing until the user says so on the
        # Source tab, so the reminder stays in the sentence (the reason used to be
        # "KiCad may hand back the OLD field value over IPC"; now it is simply that
        # recording a value and IDENTIFYING an instance are two separate acts).
        message += " — " + _("press “Fill from selection” on the Source tab to "
                             "pin this instance")
        # The state refresh comes FIRST: it writes the Р4 warnings into the very
        # same status strip, so the record's own sentence has to be the last word.
        self._after_table_change()
        self._show(message, _SUCCESS_STYLE)
        if self.on_overrides_written:
            self.on_overrides_written()

    def write_to_board(self) -> None:
        """Put this table's values ONTO the live board (Т5а) — ONE bulk commit.

        T5 took the board write OUT of the main path (recording into the store is
        what makes our values take effect), but never out of the tool: the board
        is the carrier FOREIGN tools read (BOM, net classes — design §4.2), so
        the write stays as this explicit, named action.

        It is a BOARD operation, with the board's own rules: a live KiCad and the
        shared socket gate (_can_start), the worker thread (the UI thread never
        touches the adapter) and the has_field skip-per-field discipline inside
        the worker. The STORE is not touched — recording is the other button."""
        if self._cell_name is None:
            return
        plan = build_tag_updates(self._rows)
        if not plan.updates:
            message = _("nothing to write — the roles and the cluster already "
                        "match the board")
            if plan.skipped:
                message += " " + _("skipped: {refs}").format(
                    refs=skipped_text(plan.skipped))
            self._show(message, _WARN_STYLE)
            return
        if not self._can_start(_("writing to the board")):
            return
        payload = {"adapter": self._adapter(), "cell_name": self._cell_name,
                   "updates": plan.updates, "skipped": plan.skipped}
        self._active_op = start_long_op(
            self._connection, self._guard_widgets(), write_role_table_worker,
            self._finish_write, self._on_op_failed, payload)

    def _finish_write(self, result: dict) -> None:
        self._active_op = None
        if result.get("error"):
            self._show(_("Write failed: {error}").format(error=result["error"]),
                       _ERROR_STYLE)
            return
        message = _("{count} component(s) written to the board").format(
            count=result.get("count") or 0)
        skipped = result.get("skipped") or []
        if skipped:
            message += "; " + _("skipped: {refs}").format(
                refs=skipped_text(skipped))
        # Р6, the same reminder the record path gives: writing a value pins no
        # instance — the Source tab's "Fill from selection" does that.
        message += " — " + _("press “Fill from selection” on the Source tab to "
                             "pin this instance")
        # The state refresh comes FIRST (the Р4 warnings go into the same strip),
        # so this sentence is the last word.
        self._after_table_change()
        self._show(message, _SUCCESS_STYLE)
        if self.on_board_written:
            self.on_board_written()

    def _on_op_failed(self, message: str) -> None:
        self._active_op = None
        self._show(message, _ERROR_STYLE)
