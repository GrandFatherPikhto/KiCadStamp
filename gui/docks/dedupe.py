# gui/docks/dedupe.py
"""DedupePage — "Copper duplicates": duplicate vias/tracks on the live board,
listed as GROUP NODES and (optionally) cleaned.

Plan: plan_2026_10_01_dedupe_into_kicadstamp.md §2.3; moved into the Checks tab
by plan_2026_10_23_checks_tab_top_level.md (Ч1б, 23.10) and turned into group
nodes by its Ч2а (25.10, laid out by Д-1 of that acceptance): the flat table is
GONE and so is this panel's own tree. The group nodes are CHILDREN of the
"Copper duplicates" row in the PAGE's tree (gui/docks/checks.py), and this panel
owns only the QView on the right — a stack of two pages:

  * the ROW is selected -> Find, Copy, "Clear all" and the summary;
  * a GROUP node is selected -> that group's every field, "Highlight on board"
    and "Clear this group".

The page builds the nodes from `groups()` and calls `show_row()` / `show_group(i)`
on every selection change, so the two sides can never disagree about which group
is showing.

The data comes from the SAME core as the CLI (kicadstamp/board_dedupe.py): one
group per duplicate set, with type, net, position, layer, copy count, the UUID
kept and the UUIDs deleted, plus a warning when a group's drill/diameter differs.
"Копировать" puts on the clipboard the very text `kicadstamp dedupe` prints
(format_report) — byte for byte, so a report can be pasted into a bug report
without retyping. The tree is a second VIEW of those groups; the ONE canonical
TEXT stays format_report. The row's and the group's texts come from the core's own
``via_position_text``/``track_position_text``/``via_sizes``, so a node, the detail
and the report line for the same group name the same copper in the same order —
acceptance finding (plan_2026_10_01 §5.1).

DOOR RULE (techdocs/me/door.md): the board is read and written ONLY on the
worker (gui/worker.py's start_long_op), and the worker is handed the CONNECTION,
never the board. Inside the worker thread `connection.board` is a legal read —
the guard asks the UI thread for a sign, not every thread — so this panel needs
no ui_thread_board_read() sign and cannot freeze the window with IPC.
start_long_op also owns the shared kipy socket for the whole scan/clean, which
is why the poll tick cannot interleave into it.

"Clear all" and "Clear this group" are disabled while the list is empty OR no
project root is open: every removal is journaled to the root config's actions.log
(the FileHandler gui/dock_hub.py attaches for that config's log_file), and a
removal that cannot be journaled must not happen — the same rule the CLI enforces
by requiring --config. The check is repeated in both handlers.

"Highlight on board" draws the group's members as overlay markers on the user's
overlay layer (gui/overlay_markers.py, namespace `dedupe`): the operator's own
SELECTION on the board is never touched, and pressing it again replaces the same
markers instead of stacking new ones (the owner's key is the member's uuid).

EVERY door that changes the list — Highlight, Find, "Clear this group" and
"Clear all" — first drops the WHOLE `dedupe` namespace (`_forget_marks`, Д-2 of
the Ч2а acceptance): a mark must never outlive the scan that drew it. Only that
namespace is touched — a cell anchor or a point marker on the same overlay layer
belongs to somebody else and stays exactly where it was.

WHAT THE PAGE TELLS ITS HOST (`gui/docks/checks.py`, which owns the tree row and
the menu item): `changed` after anything a row glyph, a menu enablement or a
button depends on (a scan, a clear, a root, an op starting/ending), and
`finished_op` once an operation is READY — that is what lets "Run all" wait for
this row like it waits for a check process. `is_blocked()` is its half of the
page's ONE "is anything running?" answer (Д-1 of the Ч1б acceptance).
"""
import logging

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QFormLayout, QHBoxLayout, QLabel,
                             QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from kicadstamp.board_dedupe import (apply_dedupe, apply_summary, format_report,
                                     scan_copper_duplicates, track_position_text,
                                     via_position_text, via_sizes)
from kicadstamp.i18n import _
from kicadstamp.utils.layers import layer_to_str
from kicadstamp.utils.units import MM

from .. import overlay_markers
from ..worker import start_long_op
from ._common import ERROR_STYLE, SUCCESS_STYLE, show_message

logger = logging.getLogger(__name__)

def group_label(fields: dict) -> str:
    """ONE line for a group NODE of the page's tree: type, net and position in the
    CORE's own words, the copy count, and the drill/diameter warning when the
    group's members differ (a mismatch does NOT split a group).

    The kept/deleted UUIDs are NOT here — they belong to the group's QView, which
    shows every field. The node and the copied report name the same copper in the
    same order because both read the core's builders through `_group_fields`
    (§5.1 of the 01.10 plan)."""
    label = (f"{fields['kind']}  {fields['net']}  {fields['position']}  "
             f"×{fields['copies']}")
    if fields["warning"]:
        label = f"{label}  ⚠ {fields['warning']}"
    return label


def _group_fields(kind: str, group) -> dict:
    """Every field of ONE duplicate group, in the words the CORE owns.

    The tree node, the group's QView and the copied report all read THIS dict (and
    the core's position/size builders through it), so the three cannot drift — the
    very defect §5.1 of the 01.10 plan was about."""
    head = group[0]
    sizes = via_sizes(group) if kind == "via" else ()
    return {
        "kind": kind,
        "net": head.net_name or "?",
        "position": (via_position_text(head) if kind == "via"
                     else track_position_text(head)),
        "layer": "" if kind == "via" else layer_to_str(head.layer),
        "copies": len(group),
        "keep": head.uuid,
        "delete": ", ".join(member.uuid for member in group[1:]),
        # Untranslated ON PURPOSE: this is the report's own wording for the same
        # group (the row and the copied text must read as one thing, §5.1).
        "warning": f"drill/diameter: {sizes}" if len(sizes) > 1 else "",
    }


class DedupePage(QWidget):
    """Find, list (as group nodes), copy, highlight and remove duplicate copper."""

    #: Something the row glyph or a menu item depends on has changed.
    changed = pyqtSignal()
    #: This row's operation is over (a scan or a clear, succeeded or refused) —
    #: what "Run all" waits for.
    finished_op = pyqtSignal()
    #: A scan replaced the group list: the PAGE rebuilds its child nodes from
    #: `groups()` (Д-1 — the nodes live in the page's tree, not in this panel).
    groups_changed = pyqtSignal()

    def __init__(self, main_window, connection=None):
        super().__init__()
        self._main_window = main_window
        self._connection = (connection if connection is not None
                            else getattr(main_window, "connection", None))
        #: The project root — its config's actions.log is where removals are
        #: journaled, so without it the clearing doors stay disabled.
        self._root_path = None
        #: The last scan's canonical report TEXT (what "Копировать" copies).
        self._report_text = ""
        self._active_op = None
        #: True from the moment an operation is started until its UI half runs —
        #: the panel's own answer to "is something running here?".
        self._running = False
        #: "Is anything running on the CHECKS PAGE?" — injected by the page
        #: (`ChecksPage.__init__`) so the panel can stand down while a check
        #: process is alive. ONE rule, both directions: the page asks the panel
        #: (`ChecksPage.is_running`) and the panel asks the page (this provider).
        self._page_busy = lambda: False
        #: The last scan, ONE dict per group, in tree order (`_group_fields`).
        self._groups: list = []
        #: The scan's OWN group objects, index-aligned with `_groups` — what the
        #: highlight needs for coordinates (vias first, then tracks).
        self._raw: tuple = ()
        #: The index of the group whose QView is showing — the panel's half of the
        #: ONE selection fact (the page's tree owns the other half, Д-1).
        self._selected = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        # TWO QViews, one per kind of selection in the page's tree: the row (its
        # page-level doors) and one group (its fields and its two doors).
        self.stack = QStackedWidget()
        self.row_page = self._build_row_page()
        self.group_page = self._build_group_page()
        self.stack.addWidget(self.row_page)
        self.stack.addWidget(self.group_page)
        layout.addWidget(self.stack, 1)
        self.show_row()

    def _build_row_page(self) -> QWidget:
        """The "Copper duplicates" ROW's QView: the page-level doors and the
        summary — what the panel offered before it knew about groups."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.find_button = QPushButton(_("Find"))
        self.find_button.clicked.connect(self._on_find)
        row.addWidget(self.find_button)
        self.copy_button = QPushButton(_("Copy"))
        self.copy_button.clicked.connect(self._on_copy)
        row.addWidget(self.copy_button)
        self.clear_button = QPushButton(_("Clear all"))
        self.clear_button.clicked.connect(self._on_clear)
        row.addWidget(self.clear_button)
        self.summary_label = QLabel("")
        row.addWidget(self.summary_label, 1)
        layout.addLayout(row)
        hint = QLabel(_("Select a group under this row to see its fields, mark it "
                        "on the board or clear just it."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _build_group_page(self) -> QWidget:
        """The QView of ONE group: every field the core knows, and the two doors
        that act on that group alone."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.detail_kind = QLabel("")
        self.detail_net = QLabel("")
        self.detail_position = QLabel("")
        self.detail_layer = QLabel("")
        self.detail_copies = QLabel("")
        self.detail_keep = QLabel("")
        self.detail_delete = QLabel("")
        self.detail_warning = QLabel("")
        for value in (self.detail_keep, self.detail_delete):
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_warning.setWordWrap(True)
        # The very words the table's headers used (no colons invented): they are in
        # both catalogs already, and the group's QView must name a field the same way
        # the row it came from did.
        form.addRow(_("Type"), self.detail_kind)
        form.addRow(_("Net"), self.detail_net)
        form.addRow(_("Position"), self.detail_position)
        form.addRow(_("Layer"), self.detail_layer)
        form.addRow(_("Copies"), self.detail_copies)
        form.addRow(_("Keep"), self.detail_keep)
        form.addRow(_("Delete"), self.detail_delete)
        form.addRow(_("Warning"), self.detail_warning)
        layout.addLayout(form)

        row = QHBoxLayout()
        self.highlight_button = QPushButton(_("Highlight on board"))
        self.highlight_button.setToolTip(
            _("Mark this group's copper on the board's overlay layer — your own "
              "board selection is left alone"))
        self.highlight_button.clicked.connect(self._on_highlight)
        row.addWidget(self.highlight_button)
        self.clear_group_button = QPushButton(_("Clear this group"))
        self.clear_group_button.setToolTip(
            _("Remove this group's extra copies — the same journal and the same "
              "refusals as Clear all"))
        self.clear_group_button.clicked.connect(self._on_clear_group)
        row.addWidget(self.clear_group_button)
        row.addStretch(1)
        layout.addLayout(row)
        self._show_group(None)
        return page

    # ── state ───────────────────────────────────────────────────────────────

    def set_root_path(self, path) -> None:
        """Remember which project root is open — called by the Checks page on
        every root_changed, the same contract every dock follows."""
        self._root_path = path
        self._update_buttons()

    def has_groups(self) -> bool:
        """True when the last scan found at least one duplicate group — the
        row's ⚠/✔ verdict and the first condition of every clearing door."""
        return bool(self._groups)

    def group_count(self) -> int:
        return len(self._groups)

    def is_running(self) -> bool:
        """True while a scan, a clear or a highlight of THIS panel is in flight."""
        return self._running

    def set_page_busy_provider(self, provider) -> None:
        """Tell the panel how to ask "is anything running on the page?".

        The page owns ONE such question (`ChecksPage.is_running`) and this is the
        panel's half of it: while a CHECK runs there is one KiCad/socket owner and
        one operator, so the panel's doors stand down exactly like the page's do
        during a scan (Д-1 of the Ч1б acceptance)."""
        self._page_busy = provider
        self._update_buttons()

    def is_blocked(self) -> bool:
        """True when the panel must not start anything: its own operation or the
        page's (a running check)."""
        return self._running or bool(self._page_busy())

    def can_clear(self) -> bool:
        """Everything a clearing door needs — the ONE rule "Clear all", "Clear this
        group", the menu item and the buttons share: nothing in flight (either
        side), a root to journal into, and a found list."""
        return (not self.is_blocked()) and self.has_groups() \
            and self._root_path is not None

    def refresh_buttons(self) -> None:
        """Re-read the page's busy state — the page calls it whenever ITS own
        run starts or ends, so the panel cannot keep stale enabled buttons."""
        self._update_buttons()

    def groups(self) -> tuple:
        """The last scan, ONE dict per group in the core's own order (vias, then
        tracks) — what the page builds its child nodes from (`group_label`)."""
        return tuple(self._groups)

    def selected_group(self):
        """The last scan's dict for the group whose QView is showing, or None."""
        if self._selected is None or self._selected >= len(self._groups):
            return None
        return self._groups[self._selected]

    def show_row(self) -> None:
        """The page selected the ROW: show the page-level doors and forget which
        group was being looked at (an idempotent call — the page also makes it
        when a rescan took the selected group away)."""
        self._selected = None
        self._show_group(None)
        self.stack.setCurrentWidget(self.row_page)
        self._update_buttons()

    def show_group(self, index) -> None:
        """The page selected the group node at `index`: show that group's fields
        and its two doors.

        An index that no longer exists falls back to the ROW — a stale node must
        never show the fields of some OTHER group."""
        if not isinstance(index, int) or not 0 <= index < len(self._groups):
            self.show_row()
            return
        self._selected = index
        self._show_group(self._groups[index])
        self.stack.setCurrentWidget(self.group_page)
        self._update_buttons()

    def _update_buttons(self) -> None:
        interactive = not self.is_blocked()
        selected = self.selected_group() is not None
        self.find_button.setEnabled(interactive)
        self.copy_button.setEnabled(interactive and bool(self._report_text))
        self.clear_button.setEnabled(self.can_clear())
        self.highlight_button.setEnabled(interactive and selected)
        self.clear_group_button.setEnabled(self.can_clear() and selected)

    def _op_started(self) -> None:
        self._running = True
        self._update_buttons()
        self.changed.emit()

    def _op_ended(self) -> None:
        self._running = False
        self._update_buttons()
        self.changed.emit()
        self.finished_op.emit()

    # ── the two QViews ──────────────────────────────────────────────────────

    def _show_group(self, fields) -> None:
        """Fill the group QView from ONE dict of `_group_fields`, or blank it."""
        values = fields or {}
        self.detail_kind.setText(values.get("kind", ""))
        self.detail_net.setText(values.get("net", ""))
        self.detail_position.setText(values.get("position", ""))
        self.detail_layer.setText(values.get("layer", ""))
        self.detail_copies.setText(
            str(values["copies"]) if values else "")
        self.detail_keep.setText(values.get("keep", ""))
        self.detail_delete.setText(values.get("delete", ""))
        self.detail_warning.setText(values.get("warning", ""))

    # ── buttons ─────────────────────────────────────────────────────────────

    def find(self) -> None:
        """THE door every caller uses: the Find button, the Checks menu item and
        "Run all". A refused start (no board) starts nothing and says so; a page
        that is busy (a check running) refuses too, just as its buttons do."""
        if self.is_blocked():
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
        """Clear ALL — THE door of the "Clear all" button and of the menu's "Clear
        copper duplicates". Belt and braces: the button is disabled in both refused
        states, and a programmatic call must not be able to delete unjournaled
        copper — or to start while a check runs (Д-1)."""
        if not self.can_clear():
            return
        if not getattr(self._connection, "is_connected", False):
            show_message(_("Connect to KiCad first."), ERROR_STYLE, logger)
            return
        self._op_started()
        self._active_op = start_long_op(
            self._connection,
            (self.find_button, self.clear_button, self.clear_group_button),
            self._clear_worker, self._finish_clear, self._on_op_failed,
            {"connection": self._connection},
            busy_text=_("removing duplicate copper"))

    def clear_selected_group(self) -> None:
        """Clear the SELECTED group — the same core call, journal and refusals as
        `clear()`, restricted to one group by its kept UUID (Ч2а)."""
        fields = self.selected_group()
        if fields is None or not self.can_clear():
            return
        if not getattr(self._connection, "is_connected", False):
            show_message(_("Connect to KiCad first."), ERROR_STYLE, logger)
            return
        self._op_started()
        self._active_op = start_long_op(
            self._connection,
            (self.find_button, self.clear_button, self.clear_group_button),
            self._clear_one_worker, self._finish_clear, self._on_op_failed,
            {"connection": self._connection,
             "kind": fields["kind"], "keep": fields["keep"]},
            busy_text=_("removing duplicate copper"))

    def highlight_selected_group(self) -> None:
        """Mark the SELECTED group on the board's overlay layer (Ч2а).

        A read of nothing and a write of overlay shapes only: the operator's own
        board selection is never touched (rule: "выделение человека не трогать"),
        and the owner keeps one marker per member uuid, so a second press replaces
        its own shapes instead of stacking."""
        fields = self.selected_group()
        if fields is None or self.is_blocked():
            return
        if not getattr(self._connection, "is_connected", False):
            show_message(_("Connect to KiCad first."), ERROR_STYLE, logger)
            return
        index = self._selected
        group = (self._raw[index]
                 if index is not None and index < len(self._raw) else None)
        if group is None:
            return
        markers = _marker_points(fields, group)
        self._op_started()
        self._active_op = start_long_op(
            self._connection,
            (self.highlight_button,),
            self._highlight_worker, self._finish_highlight, self._on_op_failed,
            {"connection": self._connection, "markers": markers},
            busy_text=_("marking the board"))

    # The button slots keep their historic names: the cells drive them directly
    # and there is no second body behind them.
    def _on_find(self) -> None:
        self.find()

    def _on_copy(self) -> None:
        self.copy()

    def _on_clear(self) -> None:
        self.clear()

    def _on_clear_group(self) -> None:
        self.clear_selected_group()

    def _on_highlight(self) -> None:
        self.highlight_selected_group()

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
    def _forget_marks(adapter) -> None:
        """Take THIS panel's overlay marks off the board and out of the map.

        ONE place for the rule (Д-2 of the Ч2а acceptance): a mark describes a
        scan, so every door that changes the list — Highlight, Find, "Clear this
        group", "Clear all" — drops the WHOLE `dedupe` namespace FIRST, or a
        deleted track keeps a circle nobody will ever remove.

        Only that namespace: a cell anchor or a point marker on the same overlay
        layer belongs to another consumer (E.2.1) and stays exactly where it was.
        Without a live board the owner still forgets the keys, and a
        visualisation must never fail the scan."""
        try:
            overlay_markers.OverlayMarkerOwner().remove_namespace(
                adapter, overlay_markers.NS_DEDUPE)
        except Exception:  # noqa: BLE001 — a visualisation must never raise
            logger.debug("the duplicate marks could not be removed", exc_info=True)

    @staticmethod
    def _scan_worker(payload):
        adapter, error = DedupePage._worker_adapter(payload.get("connection"))
        if error:
            return {"error": error}
        via_groups, track_groups, short_tracks = scan_copper_duplicates(adapter)
        # The list the old marks described is being replaced — including the case
        # where the new list is EMPTY: a clean board has nothing left to mark.
        DedupePage._forget_marks(adapter)
        return {"report": format_report(via_groups, track_groups, short_tracks),
                "via_groups": via_groups, "track_groups": track_groups,
                "short_tracks": short_tracks}

    @staticmethod
    def _clear_worker(payload):
        adapter, error = DedupePage._worker_adapter(payload.get("connection"))
        if error:
            return {"error": error}
        # The marks point at copper that is about to be deleted (Д-2).
        DedupePage._forget_marks(adapter)
        # Re-scan inside the worker: the displayed list may be stale by the time
        # Clear is clicked, and the count must describe what was actually
        # deleted, not what was shown a minute ago.
        # short_tracks are deliberately NOT passed on: they are not duplicates
        # and Clear must never delete them (plan Ч1).
        via_groups, track_groups, _short_tracks = scan_copper_duplicates(adapter)
        groups = len(via_groups) + len(track_groups)
        removed = apply_dedupe(adapter, via_groups, track_groups)
        return {"removed": removed, "groups": groups}

    @staticmethod
    def _clear_one_worker(payload):
        """ONE group: the same re-scan and the same core call, narrowed by the
        group's KEPT uuid (its identity in the words of the report)."""
        adapter, error = DedupePage._worker_adapter(payload.get("connection"))
        if error:
            return {"error": error}
        DedupePage._forget_marks(adapter)
        via_groups, track_groups, _short = scan_copper_duplicates(adapter)
        kind, keep = payload.get("kind"), payload.get("keep")
        pool = via_groups if kind == "via" else track_groups
        group = next((g for g in pool if g and g[0].uuid == keep), None)
        if group is None:
            return {"error": _("That group is no longer on the board — run Find "
                               "again.")}
        removed = apply_dedupe(adapter,
                               [group] if kind == "via" else [],
                               [group] if kind == "track" else [])
        return {"removed": removed, "groups": 1}

    @staticmethod
    def _highlight_worker(payload):
        adapter, error = DedupePage._worker_adapter(payload.get("connection"))
        if error:
            return {"error": error}
        # Highlight B AFTER A: A's circles go first (Д-2). "Replace, never stack"
        # has to hold across GROUPS, not only across presses of the same group.
        DedupePage._forget_marks(adapter)
        owner = overlay_markers.OverlayMarkerOwner()
        markers = payload.get("markers", ())
        drawn = 0
        for key, x_mm, y_mm in markers:
            if owner.ensure_marker(adapter, key, x_mm, y_mm):
                drawn += 1
        return {"drawn": drawn, "total": len(markers)}

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
        via_groups = result.get("via_groups", ())
        track_groups = result.get("track_groups", ())
        self._raw = tuple(via_groups) + tuple(track_groups)
        self._groups = ([_group_fields("via", group) for group in via_groups]
                        + [_group_fields("track", group) for group in track_groups])
        extra = sum(group["copies"] - 1 for group in self._groups)
        self.summary_label.setText(
            _("Duplicate groups: {groups}; extra copies: {extra}").format(
                groups=len(self._groups), extra=extra))
        # The PAGE owns the nodes (Д-1): it rebuilds them from `groups()` and
        # decides where the selection lands (by the kept uuid). This panel only
        # fills the QView it is asked for.
        self.groups_changed.emit()
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

    def _finish_highlight(self, result) -> None:
        if result.get("error"):
            show_message(result["error"], ERROR_STYLE, logger)
            self._op_ended()
            return
        show_message(
            _("Marked {drawn} of {total} duplicate item(s) on the board.").format(
                drawn=result.get("drawn", 0), total=result.get("total", 0)),
            SUCCESS_STYLE, logger)
        self._op_ended()


def _marker_points(fields, group) -> list:
    """(key, x_mm, y_mm) per MEMBER of one group, on the group's own kind.

    A via is a point; a track's marker sits at its MIDDLE (its two ends belong to
    the pads it connects, so the middle is the least misleading single point). The
    key is the member's uuid — the owner then keeps exactly one marker per member."""
    points = []
    for member in group:
        key = overlay_markers.dedupe_marker_key(member.uuid)
        if fields["kind"] == "via":
            x_mm = member.position.x / MM
            y_mm = member.position.y / MM
        else:
            x_mm = (member.start.x + member.end.x) / 2 / MM
            y_mm = (member.start.y + member.end.y) / 2 / MM
        points.append((key, x_mm, y_mm))
    return points
