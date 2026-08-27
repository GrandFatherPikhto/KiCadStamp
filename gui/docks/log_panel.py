# gui/docks/log_panel.py
"""
LogDock — a read-only, copyable/searchable log panel docked at the bottom
of the window, fed by a logging.Handler attached to the ROOT logger (not
just kicadstamp.*) — every existing logger.info/warning/exception() call
anywhere in the backend (kicadstamp/, kicadstamp/placement/, etc.) shows
up here for free, with zero changes to those call sites. Closes a real
gap: things like extract_template_from_selection()'s "N nets from
--net-template on pads" warning previously only ever reached the console
(or nowhere, if the GUI wasn't launched from one), invisible in the GUI
itself.

Requested live 2026-08-01: dock message_labels were fine for a one-line
status next to the button that produced it, but the user wanted a plain
scrollable panel for everything else — "редактировать нельзя, копировать
и искать -- можно" (can't edit, but can copy and search). A read-only
QPlainTextEdit already behaves exactly like that (selection/copy work,
typing doesn't); the Find row adds the "искать" half explicitly, since
QPlainTextEdit has no built-in find UI of its own.

Since 2026-08-13 the inline message_label is gone from EVERY dock entirely
(Denis: "Нам вообще на плашке не надо выводов лога. Пусть всё валится в
окошко лога") — the docks' show_message() now only routes to THIS panel,
which is the single place a dock's status message ends up.

setup_logging() (kicadstamp/logging_setup.py) already sets the ROOT
logger's level to DEBUG unconditionally and relies on each individual
HANDLER's own level to filter what actually gets shown (see its console
StreamHandler) — the Verbose checkbox here follows the same pattern:
toggles THIS handler's level between INFO/DEBUG, never the root logger's,
so it can't accidentally silence or unmute the console/file handlers
kicadstamp_gui.py already set up.

Since 2026-08-15 (queue-based logging rework, see
techdocs/handoff/plan_2026_08_15_queue_based_logging.md) the dock's handler
is attached to the live QueueListener (get_log_listener) whenever one
exists — its single listener thread formats/writes records, so logging can
never block the calling thread on a handler lock — falling back to a direct
root-logger attachment when no listener is configured (unit tests, no
setup_logging call). Either way the Qt signal below still marshals the
resulting line onto the UI thread.
"""
import html
import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import (QCheckBox, QDockWidget, QHBoxLayout, QLineEdit,
                              QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from kicadstamp.i18n import _
from kicadstamp.logging_setup import get_log_listener

_LEVEL_COLOR = {
    logging.DEBUG: "#888888",
    logging.WARNING: "#a86a00",
    logging.ERROR: "#aa0000",
    logging.CRITICAL: "#aa0000",
}


class _QtLogHandler(logging.Handler):
    def __init__(self, dock: "LogDock"):
        super().__init__()
        self._dock = dock

    def emit(self, record: logging.LogRecord) -> None:
        # FIXED (2026-08-04): this used to call self._dock.append_line()
        # directly, on the assumption that logging always happens on the
        # same thread as the GUI ("no QThread here" — true when this
        # comment was written, false since gui/worker.py's start_long_op
        # moved poll/Extract/Redraw/Stage/Clear all onto background
        # QThreads). A logger.info/warning/... call from ANY of that
        # backgrounded code runs this emit() ON THAT WORKER THREAD, and
        # append_line() touches a QPlainTextEdit — a QWidget may only be
        # touched from the UI thread; doing so from a worker thread is
        # undefined behavior in Qt. Found live: a silent Windows access
        # violation with a full Python traceback landing exactly here
        # (apply_pipeline.py's logger.info() inside PlacerDock's
        # backgrounded Redraw). Routed through a signal instead — emitting a
        # signal is thread-safe from any thread, and Qt automatically queues
        # the connected slot onto the RECEIVER's thread (the dock, always
        # the UI thread) when the emitting thread differs, so append_line()
        # itself only ever runs on the UI thread regardless of which thread
        # logged the message.
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self._dock.log_line.emit(message, record.levelno)


class LogDock(QDockWidget):
    # See _QtLogHandler.emit()'s docstring — the thread-safe bridge from
    # "logged on any thread" to "appended on the UI thread".
    log_line = pyqtSignal(str, int)

    def __init__(self, main_window, verbose: bool = False):
        super().__init__(_("Log"), main_window)
        self._main_window = main_window

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        # ONE toolbar row (2026-08-27, handoff log_dock_single_row_toolbar):
        # LogDock used to stack two always-visible rows (Verbose/Auto-scroll/
        # Clear, then Find/Prev/Next) above and below the text view — after
        # e587cdc/22b6af2 removed the false minimum floor on the text view
        # itself, that second row became the dominant remaining floor on how
        # small the dock (tabified with PendingChangesDock) can shrink.
        # Merged into a single QHBoxLayout — same widgets, same signal wiring,
        # only the layout structure changes. The Find line edit is now the one
        # stretchy element (more useful than a bare spacer).
        row = QHBoxLayout()
        self.verbose_checkbox = QCheckBox(_("Verbose"))
        self.verbose_checkbox.toggled.connect(self._on_verbose_toggled)
        row.addWidget(self.verbose_checkbox)
        # Auto-scroll (2026-08-15, plan_2026_08_15_log_dock_autoscroll.md):
        # QPlainTextEdit only auto-scrolls when the view was already at the
        # bottom before appending — once the user scrolls up to read history,
        # new lines no longer pull the view down. Checked by default: the
        # panel behaves like continuous auto-scroll; unchecking restores
        # Qt's plain "don't yank the reader" behavior.
        self.autoscroll_checkbox = QCheckBox(_("Auto-scroll"))
        self.autoscroll_checkbox.setChecked(True)
        row.addWidget(self.autoscroll_checkbox)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText(_("Find..."))
        self.find_edit.returnPressed.connect(lambda: self._find(backward=False))
        row.addWidget(self.find_edit, 1)
        find_prev_button = QPushButton(_("Prev"))
        find_prev_button.clicked.connect(lambda: self._find(backward=True))
        row.addWidget(find_prev_button)
        find_next_button = QPushButton(_("Next"))
        find_next_button.clicked.connect(lambda: self._find(backward=False))
        row.addWidget(find_next_button)

        clear_button = QPushButton(_("Clear"))
        clear_button.clicked.connect(lambda: self.text.clear())
        row.addWidget(clear_button)
        layout.addLayout(row)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(10000)  # cap growth over a long-running session
        # 2026-08-27: QPlainTextEdit already scrolls its own content (own
        # viewport/scrollbar) — no reason for its default minimumSizeHint to floor
        # how small the dock can shrink (Denis: wants near-full collapse; the log
        # already mirrors to the console it was launched from). Deliberately NOT a
        # QScrollArea wrap — that widget has its own scrolling; wrapping again
        # would be the nested-scroll/squish anti-pattern avoided for the other
        # docks in the detail_panel.py QScrollArea handoff.
        #
        # setMinimumHeight(1), NOT 0 (handoff log_dock_min_height_fix2): Qt
        # treats an explicit minimum of exactly 0 as "unset" (the same sentinel as
        # never calling setMinimumHeight at all), so the layout silently keeps
        # falling back to minimumSizeHint() and setMinimumHeight(0) changes
        # nothing — verified live, a minimal QPlainTextEdit-in-QVBoxLayout's
        # effective layout minimum stays at its natural size with 0 but drops
        # measurably with 1. 1 is the smallest value Qt actually honors as an
        # explicit override.
        self.text.setMinimumHeight(1)
        layout.addWidget(self.text, 1)

        self.setWidget(container)

        self.log_line.connect(self.append_line)
        self._handler = _QtLogHandler(self)
        self._handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
        self._handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        self._attach_handler()
        self.verbose_checkbox.setChecked(verbose)
        # Phase 4.3 — the handler lives on the ROOT logger, so a window torn
        # down without an explicit close (tests, crash, re-parenting) would
        # otherwise leak it there; detach on destroy as well as on
        # closeEvent, and let teardown fixtures call remove_handler() too.
        self.destroyed.connect(lambda *_: self.remove_handler())

    def _attach_handler(self) -> None:
        """Attach this dock's handler either to the live QueueListener (when
        setup_logging() has started one) or directly to the ROOT logger when
        no listener exists (unit tests, no setup_logging call) — idempotent
        across both paths, so __init__/showEvent can call it freely."""
        if self._handler is None:
            return
        listener = get_log_listener()
        if listener is not None:
            if self._handler not in listener.handlers:
                listener.handlers = listener.handlers + (self._handler,)
        else:
            root = logging.getLogger()
            if self._handler not in root.handlers:
                root.addHandler(self._handler)

    def _detach_handler(self) -> None:
        """Detach this dock's handler from BOTH the ROOT logger and the live
        QueueListener (whichever path it was attached through) — idempotent,
        safe to call from closeEvent, from the destroyed signal and from
        teardown fixtures. Phase 4.3."""
        if self._handler is None:
            return
        root = logging.getLogger()
        if self._handler in root.handlers:
            root.removeHandler(self._handler)
        listener = get_log_listener()
        if listener is not None and self._handler in listener.handlers:
            listener.handlers = tuple(
                h for h in listener.handlers if h is not self._handler)

    def remove_handler(self) -> None:
        """Detach this dock's handler (idempotent — safe to call from
        closeEvent, from the destroyed signal and from teardown fixtures).
        Phase 4.3."""
        self._detach_handler()

    def closeEvent(self, event) -> None:
        # A closed dock is hidden, not destroyed — detach now so a window
        # torn down while the dock is closed doesn't leak its handler.
        self.remove_handler()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        # Re-attach on (re)show: remove_handler() is about teardown hygiene,
        # not about permanently silencing a dock the user merely closed and
        # re-opened from the View menu.
        self._attach_handler()
        super().showEvent(event)

    def _on_verbose_toggled(self, checked: bool) -> None:
        self._handler.setLevel(logging.DEBUG if checked else logging.INFO)

    def _find(self, backward: bool) -> None:
        query = self.find_edit.text()
        if not query:
            return
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        found = self.text.find(query, flags)
        if not found:
            # Wrap around: retry once from the start/end of the document.
            cursor = self.text.textCursor()
            cursor.movePosition(cursor.MoveOperation.End if backward else cursor.MoveOperation.Start)
            self.text.setTextCursor(cursor)
            self.text.find(query, flags)

    def append_line(self, message: str, levelno: int) -> None:
        # FIXED (2026-08-20): this used to call appendPlainText() for
        # uncolored lines and appendHtml() for colored ones. appendHtml()
        # leaves the text cursor's *current char format* set to the last
        # inserted span's color; appendPlainText() inserts using that same
        # cursor's current char format rather than resetting to the
        # document/palette default — so every line after an ERROR/WARNING
        # kept rendering red/orange until something else reset the cursor.
        # Always going through appendHtml (with no inline color on the
        # uncolored path, so the span just inherits the widget's palette
        # color) keeps every line self-contained and avoids the leak.
        color = _LEVEL_COLOR.get(levelno)
        style = f' style="color:{color}"' if color else ""
        # appendHtml() does NOT preserve \n as a line break (HTML collapses
        # whitespace outside a whitespace-preserving container) — a
        # multi-line message (e.g. format_fatal_error()'s bulleted dump)
        # would otherwise render as one run-on line. white-space:pre-wrap
        # fixes it without <pre>'s forced monospace font (verified live
        # against QTextDocument — both preserve \n identically).
        self.text.appendHtml(
            f'<div style="white-space:pre-wrap"><span{style}>'
            f'{html.escape(message)}</span></div>'
        )
        # Auto-scroll (2026-08-15, plan_2026_08_15_log_dock_autoscroll.md):
        # force the view to the bottom on every line while the checkbox is
        # checked. Deliberately scrollbar.setValue(maximum()), NOT
        # moveCursor(QTextCursor.End) — the latter also moves the text cursor
        # (affects selection/search), the scrollbar only touches scrolling.
        if self.autoscroll_checkbox.isChecked():
            scrollbar = self.text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
