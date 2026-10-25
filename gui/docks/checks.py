# gui/docks/checks.py
"""The "Checks" PAGE — fourth page of the left tab row (Components | Config | Trees | Checks).

Plan: plan_2026_10_23_checks_tab_top_level.md (place named by Denis, 23.10) on top of
plan_2026_10_22_checks_tab.md. Shape: a TREE of the CLI checks on the left, the selected check's
QView on the right:

  * the tree has ONE row per check from `gui/checks_registry.CHECKS` — the verdict of its LAST
    run (never run / clean / problems / refused-or-crashed, by the exit code, the same vocabulary
    `exit_level` owns) and how long that run took. Error kinds and single errors are Ч2 (they
    need the scripts' `--json`); this page must not pretend to know them.
  * EVERY check keeps its OWN last output and status: selecting another row shows that check's
    report without re-running anything, and "Copy" hands over the SELECTED check's own bytes.
  * one process at a time — "Run" runs the SELECTED check, "Run all" queues every check in
    registry order and starts the next one only when the previous has finished.

What does NOT change from the accepted 22–23.10 tab: the checks stay CLI scripts and the page
only LAUNCHES them (own process, own KiCad connection, `-u`, `PYTHONIOENCODING=utf-8`,
`MergedChannels`, one incremental UTF-8 decoder per run with its tail flushed at the end), a
start never lands inside the GUI's own IPC transaction (`socket_busy` + exactly ONE deferred
retry), no modal windows (rule 43), and "Copy" never includes the page's own warning line.
"""
import codecs
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Optional

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt, pyqtSignal
from PyQt6.QtGui import QFontDatabase, QTextCursor
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPlainTextEdit,
                             QPushButton, QSplitter, QStackedWidget, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.i18n import _

from ..checks_registry import CHECKS, DEDUPE_ROW, command_for, exit_level
from ..worker import defer_while_socket_busy, socket_busy
from ._common import style_for_level
from .dedupe import DedupePage

logger = logging.getLogger(__name__)

#: The verdict glyph of a tree row. A check that never ran must not look clean, and a run that
#: crashed or was stopped must not look like "problems found" — both land on the error glyph by
#: `exit_level`, which is also what colours the status line.
_GLYPHS = {"success": "✔", "warn": "⚠", "error": "✖"}
_NOT_RUN = "·"


@dataclass
class CheckState:
    """What ONE check has to remember between runs — its own output, verdict and timing."""

    #: The script's OWN bytes (what "Copy" copies) — the page's warning line is not in it.
    output: str = ""
    #: The warning line (unsaved working set) prepended to the PANE only, for THIS run.
    warning: str = ""
    code: Optional[int] = None
    seconds: Optional[float] = None
    #: True while THIS check's process is alive.
    running: bool = False
    #: The status line of the last run (or of the refusal that stopped it from starting).
    message: str = ""
    level: Optional[str] = None
    #: True once a run has FINISHED (a refused start is not a run: the row keeps "not run").
    started: bool = False

    def glyph(self) -> str:
        """The row's verdict: a live run shows the ellipsis, a check that never FINISHED a run
        shows the neutral dot, everything else follows `exit_level` (so crashed/stopped is not
        "problems found")."""
        if self.running:
            return "…"
        if not self.started:
            return _NOT_RUN
        return _GLYPHS[exit_level(self.code)]


class ChecksPage(QWidget):
    """Tree of checks + the selected check's view; one check process at a time."""

    #: One finished run: (script, exit code or None, seconds). Emitted for a crashed, unstarted
    #: and user-stopped run too — a caller waiting on a run must never hang.
    finished = pyqtSignal(object, object, float)
    #: Anything the menu needs to re-read (a project opened, a run started or ended).
    state_changed = pyqtSignal()

    def __init__(self, main_window, connection=None, *, scripts_root=None, python=None):
        super().__init__()
        #: NOT a dock any more (23.10): the page is a `left_tabs` page, so it carries no
        #: objectName/`toggleViewAction` of its own.
        self._main_window = main_window
        self._connection = (connection if connection is not None
                            else getattr(main_window, "connection", None))
        #: The profile's config FILE — what `root_metadata.root_path` holds, and the one
        #: argument every check script accepts. None = no project open = nothing to run.
        self._root_path = None
        #: Test seams: a tree that carries `tools/` and the interpreter to run. Production
        #: passes neither (the tree is found by `checks_registry.find_repo_root`).
        self._scripts_root = scripts_root
        self._python = python
        self._process = None
        #: The script the running process belongs to.
        self._current: Optional[str] = None
        self._started_at = 0.0
        self._stopping = False
        #: "Run all" order still to start — one process at a time, the next starts on finish.
        self._queue: list = []
        #: How many queued checks a Stop dropped — named in the status line, never silent.
        self._queue_cancelled = 0
        #: ONE incremental UTF-8 decoder per run (Д-1 of the 22.10 acceptance).
        self._decoder = None
        self._states = {check.script: CheckState() for check in CHECKS}
        #: The FIFTH row is the copper-duplicates panel (see DEDUPE_ROW): its
        #: verdict is "not scanned yet" / "no duplicates" / "N groups", read off
        #: the panel itself, never invented here.
        self._dedupe_scanned = False
        self.dedupe = DedupePage(main_window, connection=self._connection)
        self.dedupe.changed.connect(self._on_dedupe_changed)
        self.dedupe.finished_op.connect(self._on_dedupe_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        layout.addWidget(splitter, 1)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels([_("Check"), _("Last run")])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        # 1, not 0 — Qt treats an explicit 0 as "unset" (gui/docks/log_panel.py:157).
        self.tree.setMinimumWidth(1)
        self._items = {}
        for check in CHECKS:
            item = QTreeWidgetItem([check.title, ""])
            item.setToolTip(0, check.tooltip)
            item.setData(0, Qt.ItemDataRole.UserRole, check.script)
            self.tree.addTopLevelItem(item)
            self._items[check.script] = item
        # The panel row, AFTER the four CLI checks (Ч1б): not a script, so it has
        # no process of its own — its verdict comes from the panel's last scan.
        dedupe_item = QTreeWidgetItem([_("Copper duplicates"), ""])
        dedupe_item.setToolTip(
            0, _("Duplicate vias/tracks on the live board — find them, copy the "
                 "report, or clear the extra copies"))
        dedupe_item.setData(0, Qt.ItemDataRole.UserRole, DEDUPE_ROW)
        self.tree.addTopLevelItem(dedupe_item)
        self._items[DEDUPE_ROW] = dedupe_item
        self.tree.currentItemChanged.connect(self._on_current_changed)
        splitter.addWidget(self.tree)

        view = QWidget()
        view_layout = QVBoxLayout(view)
        view_layout.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        self.run_button = QPushButton(_("Run"))
        self.run_button.setToolTip(_("Run the SELECTED check"))
        self.run_button.clicked.connect(self._on_run_clicked)
        row.addWidget(self.run_button)
        self.run_all_button = QPushButton(_("Run all"))
        self.run_all_button.setToolTip(_("Run every check, one after another"))
        self.run_all_button.clicked.connect(self.run_all)
        row.addWidget(self.run_all_button)
        self.stop_button = QPushButton(_("Stop"))
        self.stop_button.setToolTip(_("Stop the running check"))
        self.stop_button.clicked.connect(self.stop)
        row.addWidget(self.stop_button)
        self.copy_button = QPushButton(_("Copy"))
        self.copy_button.setToolTip(_("Copy the SELECTED check's report exactly as the script "
                                      "printed it"))
        self.copy_button.clicked.connect(self.copy)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        view_layout.addLayout(row)

        self.status_label = QLabel("")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        view_layout.addWidget(self.status_label)

        # The two facts a report cannot tell by itself: it is a snapshot of the START, and a
        # live check needs this profile's board open. Permanent, not a one-off message.
        self.hint_label = QLabel(
            _("A check reads the board as of the moment it starts — changes made while it "
              "runs are not in its report."))
        self.hint_label.setWordWrap(True)
        view_layout.addWidget(self.hint_label)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output.setFont(QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont))
        # A QPlainTextEdit scrolls its own content, so its natural minimumSizeHint must not
        # floor the page: 1, NOT 0 (Qt treats 0 as "unset"; see gui/docks/log_panel.py:157).
        self.output.setMinimumHeight(1)
        # The pane is a STACK: page 0 is the selected CLI check's own bytes, page 1 is the
        # copper-duplicates panel — the row's QView IS that panel, buttons and table
        # untouched (Ч1б). One stack, so a row switch can never show the wrong one.
        self.pane = QStackedWidget()
        self.pane.addWidget(self.output)
        self.pane.addWidget(self.dedupe)
        view_layout.addWidget(self.pane, 1)
        splitter.addWidget(view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        first = self.tree.topLevelItem(0)
        if first is not None:
            self.tree.setCurrentItem(first)
        self._update_rows()
        self._update_buttons()

    # ── the checks, as data ─────────────────────────────────────────────────

    @staticmethod
    def check_for(script: str):
        """The registry row of `script`, or None — the ONE lookup every door shares."""
        return next((c for c in CHECKS if c.script == script), None)

    def state_of(self, script: str) -> CheckState:
        return self._states[script]

    def selected_script(self) -> Optional[str]:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def select(self, script: str) -> None:
        """Make `script` the selected row — the menu's door and the tests' handle."""
        item = self._items.get(script)
        if item is not None:
            self.tree.setCurrentItem(item)

    def run_from_menu(self, script: str) -> None:
        """A "Checks" menu item: show that check's row, then run IT — never the first one."""
        self.select(script)
        self.run(script)

    # ── state ───────────────────────────────────────────────────────────────

    def set_root_path(self, path) -> None:
        """Remember which project root is open — called by DockHub on every root_changed,
        the same contract every other dock follows. No project → nothing to check.

        The copper-duplicates panel reads the root too (its removals are journaled into
        that config's actions.log), so the ONE root notification reaches it from HERE —
        the hub has no dedupe dock to tell any more (Ч1б)."""
        self._root_path = path
        self.dedupe.set_root_path(path)
        self._update_buttons()
        self.state_changed.emit()

    @property
    def has_project(self) -> bool:
        return self._root_path is not None

    def is_running(self) -> bool:
        """True while a check process OR the copper-duplicates panel is busy — the ONE
        definition the buttons, the menu and "Run all" use (one thing at a time across the
        whole page)."""
        return self._process is not None or self.dedupe.is_running()

    def can_run(self) -> bool:
        """Everything "Run"/"Run all"/a menu item needs: a project and no live process."""
        return self.has_project and not self.is_running()

    def _update_buttons(self) -> None:
        runnable = self.can_run()
        # "Run"/"Copy" mean a CLI check's own process and bytes: on the panel row the
        # panel carries its own Find/Copy/Clear, so these two stay off there rather than
        # running some other check behind the user's back.
        cli_row = self.selected_script() != DEDUPE_ROW
        self.run_button.setEnabled(runnable and cli_row)
        self.run_all_button.setEnabled(runnable)
        self.stop_button.setEnabled(self._process is not None)
        self._update_copy()

    def _update_copy(self) -> None:
        script = self.selected_script()
        if script == DEDUPE_ROW:
            # The panel's Copy is right there in its own row; this button would be a
            # second door onto the same clipboard write, so it stays off.
            self.copy_button.setEnabled(False)
            return
        self.copy_button.setEnabled(
            bool(script is not None and self._states[script].output))

    def _dedupe_glyph(self) -> str:
        """The panel row's verdict: scanning / never scanned / no duplicates / N groups."""
        if self.dedupe.is_running():
            return "…"
        if not self._dedupe_scanned:
            return _NOT_RUN
        return _GLYPHS["warn"] if self.dedupe.has_groups() else _GLYPHS["success"]

    def _update_rows(self) -> None:
        for check in CHECKS:
            state = self._states[check.script]
            item = self._items[check.script]
            item.setText(0, f"{state.glyph()} {check.title}")
            if state.running:
                item.setText(1, _("running…"))
            elif state.seconds is None:
                item.setText(1, "")
            else:
                item.setText(1, _("{seconds:.0f} s").format(seconds=state.seconds))
        panel = self._items[DEDUPE_ROW]
        panel.setText(0, f"{self._dedupe_glyph()} {_('Copper duplicates')}")
        if self.dedupe.is_running():
            panel.setText(1, _("running…"))
        elif not self._dedupe_scanned:
            panel.setText(1, "")
        else:
            panel.setText(1, _("{n} groups").format(n=self.dedupe.group_count()))

    def _on_current_changed(self, _current, _previous) -> None:
        """Another row selected: show ITS last report and status — never re-run anything.
        The panel row shows the copper-duplicates panel itself."""
        script = self.selected_script()
        if script == DEDUPE_ROW:
            self.pane.setCurrentWidget(self.dedupe)
            self._show_status("", None)
            self._update_buttons()
            return
        self.pane.setCurrentWidget(self.output)
        state = self._states[script] if script is not None else CheckState()
        self._show_status(state.message, state.level)
        self._render_pane(state)
        self._update_buttons()

    def _render_pane(self, state: CheckState) -> None:
        """The pane is the check's own bytes; the working-set warning rides on TOP of them
        and stays out of `state.output` (what "Copy" hands over)."""
        text = (state.warning + "\n" if state.warning else "") + state.output
        self.output.setPlainText(text)

    def _show_status(self, text: str, level) -> None:
        """One status line: the text plus the colour of its level (None = no colour, i.e.
        "running", which is neither good nor bad yet)."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(style_for_level(level) if level else "")

    def _set_message(self, script: str, text: str, level) -> None:
        state = self._states[script]
        state.message = text
        state.level = level
        if self.selected_script() == script:
            self._show_status(text, level)
        self._update_rows()

    # ── the copper-duplicates row ───────────────────────────────────────────

    def _on_dedupe_changed(self) -> None:
        """The panel's state moved (a scan started or ended, a root arrived): the row
        glyph, the buttons and the menu's Clear item all read it from here."""
        self._update_rows()
        self._update_buttons()
        self.state_changed.emit()

    def _on_dedupe_finished(self) -> None:
        """A scan or a clear of the panel is over: the row counts as SCANNED (its verdict
        is its own last scan) and "Run all" moves on to whatever is queued after it."""
        self._dedupe_scanned = True
        self._update_rows()
        self._start_next()

    # ── buttons ─────────────────────────────────────────────────────────────

    def _on_run_clicked(self) -> None:
        script = self.selected_script()
        if script is not None:
            self.run(script)

    def copy(self) -> None:
        """Exactly the SELECTED check's own output — never the page's warning line, never
        the report of whichever check happened to run last. On the panel row it hands the
        panel's own report over, the same bytes its Copy button copies."""
        script = self.selected_script()
        if script is None:
            return
        if script == DEDUPE_ROW:
            self.dedupe.copy()
            return
        QApplication.clipboard().setText(self._states[script].output)

    def stop(self) -> None:
        """Kill the running check and drop whatever "Run all" had not started yet."""
        if self._process is None:
            return
        cancelled = len(self._queue)
        self._queue.clear()
        self._stopping = True
        self._queue_cancelled = cancelled
        self._process.kill()

    def run_all(self) -> None:
        """Queue EVERY check in registry order, then the copper-duplicates SEARCH.

        The panel's row is queued as a search and only as a search: clearing copper is a
        WRITE that rewrites the board, and "Run all" must never be able to do that behind
        the user's back (Ч1б)."""
        if not self.can_run():
            return
        self._queue = [check.script for check in CHECKS] + [DEDUPE_ROW]
        self._start_next()

    def _start_next(self) -> None:
        if not self._queue or self.is_running():
            return
        script = self._queue.pop(0)
        self.select(script)
        self.run(script)

    def run(self, script: str) -> None:
        """THE door every caller uses (Run button, "Run all", the menu item, the tests): one
        process at a time, and a start never lands inside the GUI's own IPC transaction.

        The copper-duplicates row is not a script: `run` on it means its SEARCH (the same
        door its own Find button and the menu item use) — never a clear."""
        if script == DEDUPE_ROW:
            if not self.can_run():
                return
            self.dedupe.find()
            return
        check = self.check_for(script)
        if check is None or not self.can_run():
            return
        # The script reads the LIVE board. One deferred retry covers the ~400 ms
        # selection-poll tick that holds the socket 16.4 % of the time; a second refusal is a
        # real refusal.
        defer_while_socket_busy(self._connection, (), lambda: self._launch(check),
                                lambda: self._warn_busy(script), owner=self)

    def _end_queue(self, text: str) -> str:
        """End the whole "Run all" because this check could not start, and say so.

        THE one place the rest of the queue is dropped on a refusal: a busy socket, a missing
        script and a process that failed to start all mean "nothing after this will run", so a
        later single Run must NOT resume the dropped queue. The wording is the one Stop uses —
        a dropped queue that looked like "all clean" is exactly the report this page refuses
        to print."""
        queued = len(self._queue)
        self._queue.clear()
        if not queued:
            return text
        return text + " " + _("The rest of the run is cancelled.")

    def _warn_busy(self, script: str) -> None:
        """The deferred attempt met a busy socket again — say so, do not run half-blind."""
        text = self._end_queue(_("KiCad is busy — try again in a moment."))
        self._set_message(script, text, "error")
        logger.info("Check %s: not started — %s", script, text)
        self._update_buttons()
        self.state_changed.emit()

    # ── the run ─────────────────────────────────────────────────────────────

    def _launch(self, check) -> None:
        script = check.script
        argv, reason = command_for(check, self._root_path,
                                   root=self._scripts_root, python=self._python)
        if argv is None:
            state = self._states[script]
            state.output = ""
            state.warning = ""
            state.seconds = None
            state.started = False
            state.running = False
            reason = self._end_queue(reason)
            self._set_message(script, reason, "error")
            self._render_pane(state)
            logger.warning("Check %s: %s", script, reason)
            self._update_buttons()
            self.state_changed.emit()
            return

        state = self._states[script]
        state.output = ""
        state.warning = ""
        state.seconds = None
        state.running = True
        self._current = script
        self._stopping = False
        self._queue_cancelled = 0
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        if WORKING_SET.is_dirty():
            # The scripts read the profile FROM DISK; the GUI's unsaved edits are not there.
            # Prepended to the PANE only — "Copy" still hands over the script's own bytes.
            state.warning = _("The check reads the SAVED profile — unsaved config changes "
                              "are not visible to it.")

        process = QProcess(self)
        # One stream: stdout and stderr in the order they arrive, like a terminal.
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._on_output)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)

        self._process = process
        self._started_at = perf_counter()
        self._set_message(script, _("{name} is running…").format(name=check.title), None)
        if self.selected_script() == script:
            self._render_pane(state)
        self._update_buttons()
        self.state_changed.emit()
        process.start(argv[0], argv[1:])

    def _on_output(self) -> None:
        process = self._process
        if process is None or self._decoder is None or self._current is None:
            return
        chunk = self._decoder.decode(bytes(process.readAllStandardOutput()))
        self._append_output(chunk)

    def _append_output(self, chunk: str) -> None:
        """Append one already-decoded piece to the CURRENT check's report (kept bytes:
        `state.output`) and to the pane. ONE place, so the text "Copy" hands over can never
        drift from what is shown."""
        if not chunk or self._current is None:
            return
        state = self._states[self._current]
        state.output += chunk
        if self.selected_script() != self._current:
            return
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(chunk)

    def _flush_decoder(self) -> None:
        """Hand the decoder's tail (`final=True`) to the report and forget it: the tail belongs
        to THIS run, and this run is over.

        A character split across two reads is NOT the reason — the next read completes it
        (Д-1). The tail survives only when the process ENDED in the middle of a character:
        crashed, stopped, or its output cut off by an abrupt exit. The next read never comes,
        so without the flush those bytes vanish in silence and the report ends as if the script
        had stopped at a clean boundary. Flushed with `errors="replace"` they become U+FFFD —
        "the output was cut off here", which is true, instead of a silence that looks like
        health.

        The reference is dropped so a run's decoder cannot outlive it; a late read is refused
        on `_process is None` anyway, so that line is hygiene, not the guard.
        """
        decoder, self._decoder = self._decoder, None
        if decoder is not None:
            self._append_output(decoder.decode(b"", True))

    def _on_finished(self, code, status) -> None:
        process = self._process
        if process is None or self._current is None:
            return
        script = self._current
        self._process = None
        process.deleteLater()
        # BEFORE `_current` is cleared: the tail belongs to THIS check's report (the Д-3 cell
        # went red the moment the flush ran after the clear — `_append_output` had no target).
        self._flush_decoder()
        self._current = None
        state = self._states[script]
        state.running = False
        state.started = True
        state.code = code
        state.seconds = perf_counter() - self._started_at
        check = self.check_for(script)
        stopped, self._stopping = self._stopping, False
        cancelled, self._queue_cancelled = getattr(self, "_queue_cancelled", 0), 0

        if stopped:
            if cancelled:
                # "Run all" was running: the rest of the queue was dropped, not started. ONE
                # whole sentence, not a glued fragment — a translator must see the context.
                text = _("{name}: stopped after {seconds:.1f} s — the rest of the run is "
                         "cancelled").format(name=check.title, seconds=state.seconds)
            else:
                text = _("{name}: stopped after {seconds:.1f} s").format(
                    name=check.title, seconds=state.seconds)
            self._set_message(script, text, "error")
            logger.info("Check %s: stopped by the user after %.1f s", script, state.seconds)
        elif status == QProcess.ExitStatus.CrashExit:
            self._set_message(script, _("{name}: crashed (exit code {code}) after "
                                        "{seconds:.1f} s").format(
                name=check.title, code=code, seconds=state.seconds), "error")
            logger.warning("Check %s: crashed, exit code %s, %.1f s",
                           script, code, state.seconds)
        elif code in (0, 1, 2):
            words = {0: _("clean"), 1: _("problems found"), 2: _("refused")}[code]
            self._set_message(script, _("{name}: {words} — exit {code}, {seconds:.1f} s").format(
                name=check.title, words=words, code=code, seconds=state.seconds),
                exit_level(code))
            # The one Log line per run — the same wording the docs use.
            logger.info("Check %s: exit %s (%.1f s)", script, code, state.seconds)
        else:
            self._set_message(script, _("{name}: unexpected exit code {code} after "
                                        "{seconds:.1f} s").format(
                name=check.title, code=code, seconds=state.seconds), "error")
            logger.warning("Check %s: unexpected exit code %s (%.1f s)",
                           script, code, state.seconds)
        self._update_buttons()
        self._update_copy()
        self.state_changed.emit()
        self.finished.emit(script, code, state.seconds)
        self._start_next()

    def _on_error(self, error) -> None:
        """`FailedToStart` only: no interpreter, no script, no permission — a red line with the
        reason.

        Every OTHER error (Crashed, Timedout, WriteError, ReadError) comes TOGETHER with
        `finished`, and `_on_finished` owns that report — including the ordinary case of a run
        the user stopped, which Qt reports as CrashExit + Crashed. Reporting it here turned a
        deliberate Stop into "could not start" (measured 22.10.2026).
        """
        if error != QProcess.ProcessError.FailedToStart:
            return
        process = self._process
        if process is None or self._current is None:
            return
        script = self._current
        reason = process.errorString()
        self._process = None
        self._current = None
        process.deleteLater()
        state = self._states[script]
        state.running = False
        state.started = False
        text = self._end_queue(_("{name}: could not start — {reason}").format(
            name=self.check_for(script).title, reason=reason))
        self._set_message(script, text, "error")
        logger.error("Check %s: could not start — %s", script, reason)
        self._update_buttons()
        self.state_changed.emit()
        self.finished.emit(script, None, 0.0)
