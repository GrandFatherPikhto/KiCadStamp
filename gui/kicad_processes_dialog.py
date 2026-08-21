# gui/kicad_processes_dialog.py
"""
KicadProcessesDialog — a user-triggered picker for listing/force-closing
KiCad processes. Built for the case found live 2026-08-03: a crashed/frozen
kicad.exe (Windows "Not Responding") stayed running alongside a fresh one
and blocked the fresh one's IPC connection — previously the only fix was
alt-tabbing to Task Manager by hand.

Deliberately NOT automatic: this project already decided (fieldstool's
Apply gate, docs/fieldstool.md) that closing/killing KiCad is always a user
instruction, never something the tool decides on its own — kipy 0.7.1 has
no way to check any KiCad process for unsaved changes, so an automatic
"this one looks stuck, kill it" heuristic could destroy real work. This
dialog only lists what kicadstamp.schematic_safety.list_kicad_processes()
reports and kills whichever single PID the user explicitly selects and
confirms — see kill_kicad_process()'s own docstring for the same point.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                              QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from kicadstamp.i18n import _
from kicadstamp.schematic_safety import KicadProcessInfo, kill_kicad_process, list_kicad_processes

_PID_ROLE = Qt.ItemDataRole.UserRole


class KicadProcessesDialog(QDialog):
    # Poll cadence while waiting for a killed PID to vanish from the process
    # list, and how many re-checks to make before giving up (see _on_kill()).
    _CLOSE_POLL_INTERVAL_MS = 200
    _CLOSE_POLL_MAX_ATTEMPTS = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("KiCad processes"))
        self.resize(440, 260)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            _("Select a KiCad process to force-close. This never happens automatically — "
              "only what you pick and confirm here, e.g. a crashed/\"Not Responding\" session "
              "that's still blocking a fresh KiCad's connection.")))

        self.list = QListWidget()
        layout.addWidget(self.list)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton(_("Refresh"))
        self.refresh_button.clicked.connect(self._refresh)
        button_row.addWidget(self.refresh_button)
        self.kill_button = QPushButton(_("Force-close selected"))
        self.kill_button.clicked.connect(self._on_kill)
        button_row.addWidget(self.kill_button)
        close_button = QPushButton(_("Close"))
        close_button.clicked.connect(self.reject)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        # Single-shot timer that re-checks whether a force-closed PID is really
        # gone before refreshing — see _on_kill()/_poll_closed_pid().
        self._close_poll_timer = QTimer(self)
        self._close_poll_timer.setSingleShot(True)
        self._close_poll_timer.setInterval(self._CLOSE_POLL_INTERVAL_MS)
        self._close_poll_timer.timeout.connect(self._poll_closed_pid)
        self._pending_kill_pid = None
        self._kill_poll_attempts = 0

        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        processes = list_kicad_processes()
        if not processes:
            self.message_label.setText(_("No KiCad process found."))
            return
        self.message_label.setText("")
        for proc in processes:
            item = QListWidgetItem(self._label_for(proc))
            item.setData(_PID_ROLE, proc.pid)
            self.list.addItem(item)

    @staticmethod
    def _label_for(proc: KicadProcessInfo) -> str:
        parts = [_("PID {pid}").format(pid=proc.pid)]
        if proc.status:
            parts.append(proc.status)
        if proc.title:
            parts.append(proc.title)
        return " — ".join(parts)

    def _on_kill(self) -> None:
        item = self.list.currentItem()
        if item is None:
            self.message_label.setText(_("Pick a process from the list first."))
            return
        pid = item.data(_PID_ROLE)
        confirmed = QMessageBox.question(
            self, _("Force-close KiCad"),
            _("Force-close KiCad process {pid}? Any unsaved changes in that process are "
              "lost — this cannot be undone.").format(pid=pid))
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            kill_kicad_process(pid)
        except RuntimeError as e:
            self.message_label.setText(_("Could not close PID {pid}: {error}").format(
                pid=pid, error=str(e)))
            return
        # The OS only *accepted* the kill request — taskkill's exit code means
        # "termination requested", SIGKILL only sends a signal. The PID can
        # linger in the process table for a second or two, so refreshing right
        # here would race against that and show the killed process still in the
        # list. Wait for the PID to actually disappear before refreshing.
        self._begin_waiting_for_close(pid)

    def _begin_waiting_for_close(self, pid: int) -> None:
        self._pending_kill_pid = pid
        self._kill_poll_attempts = 0
        self.refresh_button.setEnabled(False)
        self.kill_button.setEnabled(False)
        self.message_label.setText(_("Closing PID {pid}…").format(pid=pid))
        self._close_poll_timer.start()

    def _poll_closed_pid(self) -> None:
        pid = self._pending_kill_pid
        if pid is None:
            return
        still_listed = any(proc.pid == pid for proc in list_kicad_processes())
        if not still_listed:
            self._finish_waiting_for_close()
            return
        self._kill_poll_attempts += 1
        if self._kill_poll_attempts >= self._CLOSE_POLL_MAX_ATTEMPTS:
            self._finish_waiting_for_close(
                _("PID {pid} did not confirm closing within a reasonable time.")
                .format(pid=pid))
            return
        self._close_poll_timer.start()

    def _finish_waiting_for_close(self, message=None) -> None:
        self._close_poll_timer.stop()
        self._pending_kill_pid = None
        self._kill_poll_attempts = 0
        self.refresh_button.setEnabled(True)
        self.kill_button.setEnabled(True)
        self._refresh()
        if message is not None:
            self.message_label.setText(message)
