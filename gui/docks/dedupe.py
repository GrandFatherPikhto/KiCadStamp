# gui/docks/dedupe.py
"""DedupePage — "Copper duplicates": duplicate vias/tracks on the live board,
listed and (optionally) cleaned.

Plan: techdocs/handoff/deepseek/plan/plan_2026_10_01_dedupe_into_kicadstamp.md,
§2.3; MOVED 23.10 into the Checks tab (plan_2026_10_23_checks_tab_top_level.md,
Ч1б): it used to be a bottom QDockWidget tabified with the Log, and Denis asked
for the bottom-right panel to go — the panel is now the QView of the FIFTH row
of the Checks tree (`gui/docks/checks.py`), with its own Find / Copy / Clear and
no change of behaviour. A page, not a dock: no objectName, no
`toggleViewAction`, nothing in the View menu.

The data comes from the SAME core as the CLI (kicadstamp/board_dedupe.py): one
row per duplicate group with type, net, position, layer, copy count, the UUID
kept and the UUIDs deleted, plus a warning when a group's drill/diameter
differs. "Копировать" puts on the clipboard the very text `kicadstamp dedupe`
prints (format_report) — byte for byte, so a report can be pasted into a bug
report without retyping. The table is a second VIEW of those groups; the ONE
canonical TEXT stays format_report. The row's position and drill numbers come
from the core's own ``via_position_text``/``track_position_text``/``via_sizes``,
so a row and the report line for the same group name the same copper in the same
order — acceptance finding (plan_2026_10_01 §5.1): the panel used to format its
own, and a track stored (5,0)->(1,0) read as (5,0)->(1,0) in the table while the
copied text said (1,0)->(5,0).

DOOR RULE (techdocs/me/door.md): the board is read and written ONLY on the
worker (gui/worker.py's start_long_op), and the worker is handed the CONNECTION,
never the board. Inside the worker thread `connection.board` is a legal read —
the guard asks the UI thread for a sign, not every thread — so this panel needs
no ui_thread_board_read() sign and cannot freeze the window with IPC.
start_long_op also owns the shared kipy socket for the whole scan/clean, which
is why the poll tick cannot interleave into it.

"Очистить" is disabled while the list is empty OR no project root is open: every
removal is journaled to the root config's actions.log (the FileHandler
gui/dock_hub.py attaches for that config's log_file), and a removal that cannot
be journaled must not happen — the same rule the CLI enforces by requiring
--config. The check is repeated in the handler, so a programmatic call cannot
bypass the disabled button.

WHAT THE PAGE TELLS ITS HOST (the Checks page, which owns the tree row and the
menu item): `changed` after anything a row glyph or a menu enablement depends on
(a scan, a clear, a root, an op starting/ending), and `finished` once a scan or a
clear is READY — that is what lets "Run all" wait for this row like it waits for
a check process.
"""
import logging

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout,
                             QHeaderView, QLabel, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.board_dedupe import (apply_dedupe, apply_summary,
                                     format_report, scan_copper_duplicates,
                                     track_position_text, via_position_text,
                                     via_sizes)
from kicadstamp.i18n import _
from kicadstamp.utils.layers import layer_to_str

from ..worker import start_long_op
from ._common import ERROR_STYLE, SUCCESS_STYLE, show_message

logger = logging.getLogger(__name__)

#: Table headers. Each one is its OWN literal `_()` call, NOT `_(c)` over a
#: tuple of bare strings: pybabel extracts literals, so the `_(c)` form was
#: invisible to it and five of the eight headers stayed English in the Russian
#: UI (acceptance finding, plan_2026_10_01 §5.2). `_(c)` also cannot be seen by
#: the source-tree completeness cell — the tuple of literals can.
_COLUMNS = (_("Type"), _("Net"), _("Position"), _("Layer"), _("Copies"),
            _("Keep"), _("Delete"), _("Warning"))


class DedupePage(QWidget):
    """Find, list, copy and remove duplicate copper — the Checks tab's fifth row."""

    #: Something the row glyph or a menu item depends on has changed.
    changed = pyqtSignal()
    #: This row's operation is over (a scan or a clear, succeeded or refused) —
    #: what "Run all" waits for.
    finished_op = pyqtSignal()

    def __init__(self, main_window, connection=None):
        super().__init__()
        self._main_window = main_window
        self._connection = (connection if connection is not None
                            else getattr(main_window, "connection", None))
        #: The project root — its config's actions.log is where removals are
        #: journaled, so without it "Очистить" stays disabled.
        self._root_path = None
        #: The last scan's canonical report TEXT (what "Копировать" copies).
        self._report_text = ""
        self._active_op = None
        #: True from the moment an operation is started until its UI half runs —
        #: the panel's own answer to "is something running?" (`can_clear` and the
        #: menu's Clear item both read it).
        self._running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        row = QHBoxLayout()
        self.find_button = QPushButton(_("Find"))
        self.find_button.clicked.connect(self._on_find)
        row.addWidget(self.find_button)
        self.copy_button = QPushButton(_("Copy"))
        self.copy_button.clicked.connect(self._on_copy)
        row.addWidget(self.copy_button)
        self.clear_button = QPushButton(_("Clear"))
        self.clear_button.clicked.connect(self._on_clear)
        row.addWidget(self.clear_button)
        self.summary_label = QLabel("")
        row.addWidget(self.summary_label, 1)
        layout.addLayout(row)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        self._update_buttons()

    # ── state ───────────────────────────────────────────────────────────────

    def set_root_path(self, path) -> None:
        """Remember which project root is open — called by the Checks page on
        every root_changed, the same contract every dock follows."""
        self._root_path = path
        self._update_buttons()

    def has_groups(self) -> bool:
        """True when the last scan found at least one duplicate group — the
        row's ⚠/✔ verdict and Clear's first condition."""
        return self.table.rowCount() > 0

    def group_count(self) -> int:
        return self.table.rowCount()

    def is_running(self) -> bool:
        """True while a scan or a clear of THIS panel is in flight."""
        return self._running

    def can_clear(self) -> bool:
        """Everything "Clear" needs — the ONE rule the button and the menu item
        share: no operation in flight, a root to journal into, and a found list."""
        return (not self._running) and self.has_groups() \
            and self._root_path is not None

    def _update_buttons(self) -> None:
        interactive = not self._running
        self.find_button.setEnabled(interactive)
        self.copy_button.setEnabled(interactive and bool(self._report_text))
        self.clear_button.setEnabled(self.can_clear())

    def _op_started(self) -> None:
        self._running = True
        self._update_buttons()
        self.changed.emit()

    def _op_ended(self) -> None:
        self._running = False
        self._update_buttons()
        self.changed.emit()
        self.finished_op.emit()

    # ── buttons ─────────────────────────────────────────────────────────────

    def find(self) -> None:
        """THE door every caller uses: the Find button, the Checks menu item and
        "Run all". A refused start (no board) starts nothing and says so."""
        if self._running:
            return
        if not getattr(self._connection, "is_connected", False):
            show_message(_("Connect to KiCad first."), ERROR_STYLE, logger)
            return
        self._op_started()
        self._active_op = start_long_op(
            self._connection,
            (self.find_button, self.clear_button),
            self._scan_worker, self._finish_scan, self._on_op_failed,
            {"connection": self._connection},
            busy_text=_("reading the board"))

    def copy(self) -> None:
        """Put EXACTLY the last report text on the clipboard — the same bytes
        `kicadstamp dedupe` prints (board_dedupe.format_report)."""
        QApplication.clipboard().setText(self._report_text)

    def clear(self) -> None:
        """THE door every caller uses (the Clear button and the menu's "Clear
        copper duplicates"). Belt and braces: the button is disabled in both
        refused states, and a programmatic call must not be able to delete
        unjournaled copper."""
        if not self.can_clear():
            return
        if not getattr(self._connection, "is_connected", False):
            show_message(_("Connect to KiCad first."), ERROR_STYLE, logger)
            return
        self._op_started()
        self._active_op = start_long_op(
            self._connection,
            (self.find_button, self.clear_button),
            self._clear_worker, self._finish_clear, self._on_op_failed,
            {"connection": self._connection},
            busy_text=_("removing duplicate copper"))

    # The button slots keep their historic names: the Ctrl-less cells drive them
    # directly and there is no second body behind them.
    def _on_find(self) -> None:
        self.find()

    def _on_copy(self) -> None:
        self.copy()

    def _on_clear(self) -> None:
        self.clear()

    # ── worker halves (no widget is touched here) ────────────────────────────

    @staticmethod
    def _worker_adapter(connection):
        """(adapter, error) for the worker thread. ``connection.board`` is read
        HERE, on the worker — the door permits that without a sign, and that is
        exactly why the payload carries the connection instead of the board."""
        board = getattr(connection, "board", None) if connection is not None else None
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            return None, _("Connect to KiCad first.")
        return adapter, None

    @staticmethod
    def _scan_worker(payload):
        adapter, error = DedupePage._worker_adapter(payload.get("connection"))
        if error:
            return {"error": error}
        via_groups, track_groups, short_tracks = scan_copper_duplicates(adapter)
        return {"report": format_report(via_groups, track_groups, short_tracks),
                "via_groups": via_groups, "track_groups": track_groups,
                "short_tracks": short_tracks}

    @staticmethod
    def _clear_worker(payload):
        adapter, error = DedupePage._worker_adapter(payload.get("connection"))
        if error:
            return {"error": error}
        # Re-scan inside the worker: the displayed list may be stale by the time
        # Clear is clicked, and the count must describe what was actually
        # deleted, not what was shown a minute ago.
        # short_tracks are deliberately NOT passed on: they are not duplicates
        # and Clear must never delete them (plan Ч1).
        via_groups, track_groups, _short_tracks = scan_copper_duplicates(adapter)
        groups = len(via_groups) + len(track_groups)
        removed = apply_dedupe(adapter, via_groups, track_groups)
        return {"removed": removed, "groups": groups}

    # ── UI halves ───────────────────────────────────────────────────────────

    def _on_op_failed(self, message: str) -> None:
        show_message(_("Operation failed: {error}").format(error=message),
                     ERROR_STYLE, logger)
        self._op_ended()

    def _finish_scan(self, result) -> None:
        if result.get("error"):
            show_message(result["error"], ERROR_STYLE, logger)
            self._op_ended()
            return
        self._report_text = result.get("report", "")
        self._fill_table(result.get("via_groups", ()),
                         result.get("track_groups", ()))
        self._op_ended()

    def _finish_clear(self, result) -> None:
        if result.get("error"):
            show_message(result["error"], ERROR_STYLE, logger)
            self._op_ended()
            return
        summary = apply_summary(result["removed"], result["groups"])
        show_message(summary, SUCCESS_STYLE, logger)
        self._op_ended()
        # Auto-rescan, deferred by ONE event-loop turn: this handler runs while
        # the operation that just finished is still releasing the shared socket,
        # and worker.py's start() refuses a start on a busy socket (one deferred
        # retry later). A zero-delay singleShot runs after that release.
        QTimer.singleShot(0, self.find)

    def _fill_table(self, via_groups, track_groups) -> None:
        rows = [self._via_row(group) for group in via_groups]
        rows += [self._track_row(group) for group in track_groups]
        self.table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, text in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(text))
        extra = (sum(len(g) - 1 for g in via_groups)
                 + sum(len(g) - 1 for g in track_groups))
        self.summary_label.setText(
            _("Duplicate groups: {groups}; extra copies: {extra}").format(
                groups=len(rows), extra=extra))

    @staticmethod
    def _via_row(group):
        """One table row for a via group. The type word is deliberately the
        same plain "via"/"track" the report prints, and the position and drill
        numbers come from the core's own builders, so the row and the copied
        text read as one thing (§5.1)."""
        via = group[0]
        sizes = via_sizes(group)
        warning = f"drill/diameter: {sizes}" if len(sizes) > 1 else ""
        return ("via", via.net_name or "?", via_position_text(via), "",
                str(len(group)), group[0].uuid,
                ", ".join(v.uuid for v in group[1:]), warning)

    @staticmethod
    def _track_row(group):
        track = group[0]
        return ("track", track.net_name or "?", track_position_text(track),
                layer_to_str(track.layer), str(len(group)),
                group[0].uuid, ", ".join(t.uuid for t in group[1:]), "")
