# gui/docks/checks.py
"""ChecksDock — "Checks": a LAUNCHER for the three read-only CLI checks (`tools/check_*.py`).

Plan: techdocs/handoff/deepseek/plan/plan_2026_10_22_checks_tab.md. The checks stay where they
are: CLI scripts, the one source of truth (docs/check_scripts.md). This dock runs the same
script in a SEPARATE PROCESS and shows its output — it carries no check logic of its own, so
the tab and the terminal can never disagree about what a check means:

  * `QProcess` with `MergedChannels` — stdout and stderr in the order the OS hands them over,
    exactly the interleaving a terminal shows (the config loader's warnings travel through
    `logging`, i.e. stderr);
  * the child is started with `-u` (a Python whose stdout is a PIPE block-buffers in 8 KB
    chunks: without it the report would appear only when the process ends) and with
    `PYTHONIOENCODING=utf-8`, so decoding never depends on the parent's locale;
  * the bytes are decoded once and kept: "Copy" puts on the clipboard EXACTLY what the script
    wrote, and the warning line the dock may prepend is deliberately NOT part of it;
  * one run at a time. A second start is impossible while one runs (buttons are disabled and
    the handler re-checks), and a start never lands in the middle of the GUI's own IPC
    transaction: `socket_busy` + exactly ONE deferred retry, the project's usual seam — the
    script reads the LIVE board, and the GUI may be writing it right now.
  * the exit code is what the status line speaks: 0 clean (green), 1 problems found (yellow),
    2 refused (red); anything else — a crash, "could not start", "stopped" — is red with the
    reason. `gui/checks_registry.exit_level` owns that vocabulary;
  * one Log line per run (`Check <script>: exit <code> (<seconds> s)`), no modal windows: the
    pane and the Log ARE the report.

Nothing here reads the board: the check has its own KiCad connection, in its own process, so
the board door does not grow and the window cannot freeze on IPC.

The dock is a bottom QDockWidget TABIFIED WITH THE LOG (`gui/dock_hub.py`), the same shape as
the copper-duplicates panel, so its View-menu entry is Qt's own toggleViewAction.
"""
import codecs
import logging
from time import perf_counter

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt, pyqtSignal
from PyQt6.QtGui import QFontDatabase, QTextCursor
from PyQt6.QtWidgets import (QApplication, QDockWidget, QHBoxLayout, QLabel,
                             QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.i18n import _

from ..checks_registry import CHECKS, command_for, exit_level, find_repo_root
from ..worker import defer_while_socket_busy, socket_busy
from ._common import style_for_level

logger = logging.getLogger(__name__)


class ChecksDock(QDockWidget):
    """Bottom dock: run one CLI check at a time, show its output, colour its exit code."""

    #: One finished run: (button title, exit code or None, seconds). Emitted for a crashed,
    #: unstarted and user-stopped run too — a caller waiting on a run must never hang.
    finished = pyqtSignal(object, object, float)

    def __init__(self, main_window, connection=None, *, scripts_root=None, python=None):
        super().__init__(_("Checks"), main_window)
        # A stable objectName is what QMainWindow.saveState()/restoreState() map a saved
        # layout blob back to (same reason LogDock and DedupeDock set one).
        self.setObjectName("checks_dock")
        self._main_window = main_window
        self._connection = (connection if connection is not None
                            else getattr(main_window, "connection", None))
        #: The profile's config FILE — what `root_metadata.root_path` holds, and the one
        #: argument all three scripts accept. None = no project open = buttons disabled.
        self._root_path = None
        #: Test seams: a tree that carries `tools/` and the interpreter to run. Production
        #: passes neither (the tree is found by `checks_registry.find_repo_root`).
        self._scripts_root = scripts_root
        self._python = python
        self._process = None
        self._check = None
        self._started_at = 0.0
        self._stopping = False
        #: The SCRIPT's own bytes (what "Copy" copies) — the warning line below is not in it.
        self._output = ""
        #: ONE incremental UTF-8 decoder per run. A pipe may deliver a chunk that ends in the
        #: middle of a multi-byte character ("—", "→", or any Cyrillic in a ru report), and
        #: decoding each chunk on its own turned the halves into U+FFFD — the pane and "Copy"
        #: then carried a broken character (acceptance finding Д-1, 22.10.2026).
        self._decoder = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        row = QHBoxLayout()
        # One button per check, each label/tooltip its OWN literal `_()` (in the registry):
        # pybabel extracts literals, so a `_(check.title)` at runtime would be invisible.
        self.check_buttons = {}
        for check in CHECKS:
            button = QPushButton(check.title)
            button.setToolTip(check.tooltip)
            button.clicked.connect(lambda _checked=False, c=check: self._on_check(c))
            self.check_buttons[check.script] = button
            row.addWidget(button)
        self.stop_button = QPushButton(_("Stop"))
        self.stop_button.setToolTip(_("Stop the running check"))
        self.stop_button.clicked.connect(self._on_stop)
        row.addWidget(self.stop_button)
        self.copy_button = QPushButton(_("Copy"))
        self.copy_button.setToolTip(_("Copy the report exactly as the script printed it"))
        self.copy_button.clicked.connect(self._on_copy)
        row.addWidget(self.copy_button)
        layout.addLayout(row)

        self.status_label = QLabel("")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # The two facts a report cannot tell by itself: it is a snapshot of the START, and a
        # live check needs this profile's board open. Permanent, not a one-off message.
        self.hint_label = QLabel(
            _("A check reads the board as of the moment it starts — changes made while it "
              "runs are not in its report."))
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output.setFont(QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont))
        # The same rule LogDock follows (gui/docks/log_panel.py:161): a QPlainTextEdit
        # scrolls its own content, so its natural minimumSizeHint must not floor how small
        # the shared BOTTOM AREA can shrink. Measured 22.10.2026: without this the tab's
        # minimum height was 174 px and `resizeDocks([log], [200])` was clamped to it,
        # breaking the exact-delta guard of tests/gui/test_dock_sizing.py (174 vs 171).
        # 1, NOT 0 — Qt treats an explicit 0 as "unset" and falls back to the layout hint.
        self.output.setMinimumHeight(1)
        layout.addWidget(self.output, 1)
        self.setWidget(container)

        self._update_buttons()

    # ── state ───────────────────────────────────────────────────────────────

    def set_root_path(self, path) -> None:
        """Remember which project root is open — called by DockHub on every root_changed,
        the same contract every other dock follows. No project → nothing to check."""
        self._root_path = path
        self._update_buttons()

    def is_running(self) -> bool:
        """True while a check process is alive — the ONE definition used by the buttons."""
        return self._process is not None

    def _update_buttons(self) -> None:
        running = self.is_running()
        has_project = self._root_path is not None
        for button in self.check_buttons.values():
            button.setEnabled(has_project and not running)
        self.stop_button.setEnabled(running)
        self.copy_button.setEnabled(bool(self._output))

    def _show_status(self, text: str, level) -> None:
        """One status line: the text plus the colour of its level (None = no colour, i.e.
        "running", which is neither good nor bad yet)."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(style_for_level(level) if level else "")

    # ── buttons ─────────────────────────────────────────────────────────────

    def _on_check(self, check) -> None:
        if self.is_running() or self._root_path is None:
            return
        # Never start in the middle of the GUI's own IPC transaction: the script reads the
        # LIVE board. One deferred retry covers the ~400 ms selection-poll tick that holds
        # the socket 16.4 % of the time; a second refusal is a real refusal.
        defer_while_socket_busy(self._connection, (), lambda: self._launch(check),
                                self._warn_busy, owner=self)

    def _warn_busy(self) -> None:
        """The deferred attempt met a busy socket again — say so, do not run half-blind."""
        text = _("KiCad is busy — try again in a moment.")
        self._show_status(text, "error")
        logger.info("Check: not started — %s", text)

    def _on_stop(self) -> None:
        """Kill the running check. `finished` still arrives (CrashExit) and says "stopped"."""
        if self._process is None:
            return
        self._stopping = True
        self._process.kill()

    def _on_copy(self) -> None:
        """Exactly the script's own output — never the dock's warning line."""
        QApplication.clipboard().setText(self._output)

    # ── the run ─────────────────────────────────────────────────────────────

    def _launch(self, check) -> None:
        argv, reason = command_for(check, self._root_path,
                                   root=self._scripts_root, python=self._python)
        if argv is None:
            self._output = ""
            self.output.clear()
            self._show_status(reason, "error")
            logger.warning("Check %s: %s", check.script, reason)
            self._update_buttons()
            return

        self._check = check
        self._stopping = False
        self._output = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.output.clear()
        if WORKING_SET.is_dirty():
            # The scripts read the profile FROM DISK; the GUI's unsaved edits are not there.
            # Prepended to the PANE only — "Copy" still hands over the script's own bytes.
            self._warning = _("The check reads the SAVED profile — unsaved config changes "
                              "are not visible to it.")
            self.output.setPlainText(self._warning + "\n")

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
        self._show_status(_("{name} is running…").format(name=check.title), None)
        self._update_buttons()
        process.start(argv[0], argv[1:])

    def _on_output(self) -> None:
        process = self._process
        if process is None or self._decoder is None:
            return
        chunk = self._decoder.decode(bytes(process.readAllStandardOutput()))
        self._append_output(chunk)

    def _append_output(self, chunk: str) -> None:
        """Append one already-decoded piece to the report (kept bytes: `_output`) and to the
        pane. ONE place, so the text "Copy" hands over can never drift from what is shown."""
        if not chunk:
            return
        self._output += chunk
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
        if process is None:
            return
        self._process = None
        process.deleteLater()
        self._flush_decoder()
        seconds = perf_counter() - self._started_at
        check = self._check
        stopped, self._stopping = self._stopping, False
        self._update_buttons()

        if stopped:
            self._show_status(
                _("{name}: stopped after {seconds:.1f} s").format(
                    name=check.title, seconds=seconds), "error")
            logger.info("Check %s: stopped by the user after %.1f s", check.script, seconds)
        elif status == QProcess.ExitStatus.CrashExit:
            self._show_status(
                _("{name}: crashed (exit code {code}) after {seconds:.1f} s").format(
                    name=check.title, code=code, seconds=seconds), "error")
            logger.warning("Check %s: crashed, exit code %s, %.1f s",
                           check.script, code, seconds)
        elif code in (0, 1, 2):
            words = {0: _("clean"), 1: _("problems found"), 2: _("refused")}[code]
            self._show_status(
                _("{name}: {words} — exit {code}, {seconds:.1f} s").format(
                    name=check.title, words=words, code=code, seconds=seconds),
                exit_level(code))
            # The one Log line per run — the same wording the docs use.
            logger.info("Check %s: exit %s (%.1f s)", check.script, code, seconds)
        else:
            self._show_status(
                _("{name}: unexpected exit code {code} after {seconds:.1f} s").format(
                    name=check.title, code=code, seconds=seconds), "error")
            logger.warning("Check %s: unexpected exit code %s (%.1f s)",
                           check.script, code, seconds)
        self.finished.emit(check.title, code, seconds)

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
        if process is None:
            return
        reason = process.errorString()
        self._process = None
        process.deleteLater()
        self._update_buttons()
        self._show_status(
            _("{name}: could not start — {reason}").format(
                name=self._check.title, reason=reason), "error")
        logger.error("Check %s: could not start — %s", self._check.script, reason)
        self.finished.emit(self._check.title, None, 0.0)

    def closeEvent(self, event) -> None:
        """Closing the tab stops a running check: its output has nowhere to go, and a
        report nobody can read is worse than an honest "stopped"."""
        if self._process is not None:
            self._on_stop()
        super().closeEvent(event)
