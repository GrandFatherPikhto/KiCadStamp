# gui/docks/dedupe.py
"""DedupeDock — "Дубли меди": duplicate vias/tracks on the live board, listed
and (optionally) cleaned.

Plan: techdocs/handoff/deepseek/plan/plan_2026_10_01_dedupe_into_kicadstamp.md,
§2.3. The panel is a bottom QDockWidget TABIFIED WITH THE LOG (gui/dock_hub.py),
so its View-menu entry comes from Qt's own ready-made toggleViewAction, like
every other real dock — no separate switch is invented here.

The data comes from the SAME core as the CLI (kicadstamp/board_dedupe.py): one
row per duplicate group with type, net, position, layer, copy count, the UUID
kept and the UUIDs deleted, plus a warning when a group's drill/diameter
differs. "Копировать" puts on the clipboard the very text `kicadstamp dedupe`
prints (format_report) — byte for byte, so a report can be pasted into a bug
report without retyping. The table is a second VIEW of those groups; the ONE
canonical TEXT stays format_report, and the panel never assembles it again.

DOOR RULE (techdocs/me/door.md): the board is read and written ONLY on the
worker (gui/worker.py's start_long_op), and the worker is handed the CONNECTION,
never the board. Inside the worker thread `connection.board` is a legal read —
the guard asks the UI thread for a sign, not every thread — so this dock needs
no ui_thread_board_read() sign and cannot freeze the window with IPC.
start_long_op also owns the shared kipy socket for the whole scan/clean, which
is why the poll tick cannot interleave into it.

"Очистить" is disabled while the list is empty OR no project root is open: every
removal is journaled to the root config's actions.log (the FileHandler
gui/dock_hub.py attaches for that config's log_file), and a removal that cannot
be journaled must not happen — the same rule the CLI enforces by requiring
--config. The check is repeated in the handler, so a programmatic call cannot
bypass the disabled button.
"""
import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QDockWidget,
                             QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from kicadstamp.board_dedupe import (apply_dedupe, apply_summary,
                                     format_report, scan_copper_duplicates)
from kicadstamp.i18n import _
from kicadstamp.utils.layers import layer_to_str
from kicadstamp.utils.units import MM

from ..worker import start_long_op
from ._common import ERROR_STYLE, SUCCESS_STYLE, show_message

logger = logging.getLogger(__name__)

#: Table headers. "Type"/"Keep"/"Delete" name the same three things the report
#: prints, so a reader can line the table up with the text they copied.
_COLUMNS = ("Type", "Net", "Position", "Layer", "Copies", "Keep", "Delete",
            "Warning")


def _fmt_mm(value_nm: int) -> str:
    """Millimetres with the report's own 4-decimal shape (the table must not
    round differently from the text Denis pastes into a bug report)."""
    return f"{value_nm / MM:.4f}"


class DedupeDock(QDockWidget):
    """Bottom dock: find, list, copy and remove duplicate copper."""

    def __init__(self, main_window, connection=None):
        super().__init__(_("Copper duplicates"), main_window)
        # A stable objectName is what QMainWindow.saveState()/restoreState()
        # map a saved layout blob back to (same reason LogDock sets one).
        self.setObjectName("dedupe_dock")
        self._main_window = main_window
        self._connection = (connection if connection is not None
                            else getattr(main_window, "connection", None))
        #: The project root — its config's actions.log is where removals are
        #: journaled, so without it "Очистить" stays disabled.
        self._root_path = None
        #: The last scan's canonical report TEXT (what "Копировать" copies).
        self._report_text = ""
        self._active_op = None

        container = QWidget()
        layout = QVBoxLayout(container)
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
        self.table.setHorizontalHeaderLabels([_(c) for c in _COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)
        self.setWidget(container)

        self._update_buttons()

    # ── state ───────────────────────────────────────────────────────────────

    def set_root_path(self, path) -> None:
        """Remember which project root is open — called by DockHub on every
        root_changed / discard, the same contract every other dock follows."""
        self._root_path = path
        self._update_buttons()

    def _has_groups(self) -> bool:
        return self.table.rowCount() > 0

    def _update_buttons(self) -> None:
        self.copy_button.setEnabled(bool(self._report_text))
        self.clear_button.setEnabled(self._has_groups()
                                     and self._root_path is not None)

    # ── buttons ─────────────────────────────────────────────────────────────

    def _on_find(self) -> None:
        if not getattr(self._connection, "is_connected", False):
            show_message(_("Connect to KiCad first."), ERROR_STYLE, logger)
            return
        self._active_op = start_long_op(
            self._connection,
            (self.find_button, self.clear_button),
            self._scan_worker, self._finish_scan, self._on_op_failed,
            {"connection": self._connection},
            busy_text=_("reading the board"))

    def _on_copy(self) -> None:
        """Put EXACTLY the last report text on the clipboard — the same bytes
        `kicadstamp dedupe` prints (board_dedupe.format_report)."""
        QApplication.clipboard().setText(self._report_text)

    def _on_clear(self) -> None:
        # Belt and braces: the button is disabled in both refused states, and a
        # programmatic call must not be able to delete unjournaled copper.
        if not self._has_groups() or self._root_path is None:
            return
        if not getattr(self._connection, "is_connected", False):
            show_message(_("Connect to KiCad first."), ERROR_STYLE, logger)
            return
        self._active_op = start_long_op(
            self._connection,
            (self.find_button, self.clear_button),
            self._clear_worker, self._finish_clear, self._on_op_failed,
            {"connection": self._connection},
            busy_text=_("removing duplicate copper"))

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
        adapter, error = DedupeDock._worker_adapter(payload.get("connection"))
        if error:
            return {"error": error}
        via_groups, track_groups = scan_copper_duplicates(adapter)
        return {"report": format_report(via_groups, track_groups),
                "via_groups": via_groups, "track_groups": track_groups}

    @staticmethod
    def _clear_worker(payload):
        adapter, error = DedupeDock._worker_adapter(payload.get("connection"))
        if error:
            return {"error": error}
        # Re-scan inside the worker: the displayed list may be stale by the time
        # Clear is clicked, and the count must describe what was actually
        # deleted, not what was shown a minute ago.
        via_groups, track_groups = scan_copper_duplicates(adapter)
        groups = len(via_groups) + len(track_groups)
        removed = apply_dedupe(adapter, via_groups, track_groups)
        return {"removed": removed, "groups": groups}

    # ── UI halves ───────────────────────────────────────────────────────────

    def _on_op_failed(self, message: str) -> None:
        show_message(_("Operation failed: {error}").format(error=message),
                     ERROR_STYLE, logger)

    def _finish_scan(self, result) -> None:
        if result.get("error"):
            show_message(result["error"], ERROR_STYLE, logger)
            return
        self._report_text = result.get("report", "")
        self._fill_table(result.get("via_groups", ()),
                         result.get("track_groups", ()))
        self._update_buttons()

    def _finish_clear(self, result) -> None:
        if result.get("error"):
            show_message(result["error"], ERROR_STYLE, logger)
            return
        summary = apply_summary(result["removed"], result["groups"])
        show_message(summary, SUCCESS_STYLE, logger)
        # Auto-rescan, deferred by ONE event-loop turn: this handler runs while
        # the operation that just finished is still releasing the shared socket,
        # and worker.py's start() refuses a start on a busy socket (one deferred
        # retry later). A zero-delay singleShot runs after that release.
        QTimer.singleShot(0, self._on_find)

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
        same plain "via"/"track" the report prints, so the table and the copied
        text read as one thing."""
        via = group[0]
        pos = f"({_fmt_mm(via.position.x)}, {_fmt_mm(via.position.y)})"
        sizes = sorted({(round(v.drill_mm, 4), round(v.diameter_mm, 4))
                        for v in group})
        warning = f"drill/diameter: {sizes}" if len(sizes) > 1 else ""
        return ("via", via.net_name or "?", pos, "", str(len(group)),
                group[0].uuid, ", ".join(v.uuid for v in group[1:]), warning)

    @staticmethod
    def _track_row(group):
        track = group[0]
        pos = (f"({_fmt_mm(track.start.x)}, {_fmt_mm(track.start.y)}) -> "
               f"({_fmt_mm(track.end.x)}, {_fmt_mm(track.end.y)})")
        return ("track", track.net_name or "?", pos,
                layer_to_str(track.layer), str(len(group)),
                group[0].uuid, ", ".join(t.uuid for t in group[1:]), "")
