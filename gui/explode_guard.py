# gui/explode_guard.py
"""The "clusters are exploded" state and lock (Р2, plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md``; design РЗ7).

ONE ``ExplodeGuard`` per main window. ``active`` is taken from the JOURNAL on
disk (``kicadstamp.explode_journal.journal_status``) for the connected board —
NEVER a flag in memory, so a restart (or a crash) with a live journal is already
"exploded" before anything is clicked, and deleting the journal by hand clears
the lock at the next check.

While active it does two things:

* installs the WORKER gate (``gui.worker.set_long_op_gate``) so EVERY board
  operation that is not the tab's own (or "Select cell", or, in Р3, the cell
  re-read) is refused with a red line — menu sweeps, redraws, Apply and reads by
  other docks all pass through ``gui/worker.py:start_long_op``, so one hook
  closes them all;
* tells the window (a ``changed`` signal) to pin the Config right view to the
  "Разнос" page and to disable the Config tree / left tabs.

Р3: it also answers ``transfer_enabled(cell_name, cluster, sheet)`` — whether the
cell re-read should TRANSFER (not subtract) the selected ``net_traces`` copper —
from the same journal.

Qt-thin by design: this module holds state and one signal; it imports no widgets
and no dock. ``gui/worker.py`` never imports it — the gate is installed as a
plain callable, so the dependency flows only downward.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from kicadstamp.i18n import _

from . import worker

logger = logging.getLogger(__name__)

__all__ = ["ExplodeGuard"]


class ExplodeGuard(QObject):
    """See the module docstring. Owned by the window's DockHub."""

    # True when the state became "exploded", False when it cleared.
    changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._journal = None
        self._active = False
        # Install the gate for the WHOLE app. `_gate` is a bound method, so
        # detach() clears THIS guard's gate only (two guards in one process —
        # the GUI test suite builds many windows).
        worker.set_long_op_gate(self._gate)

    # ── state ───────────────────────────────────────────────────────────────
    @property
    def active(self) -> bool:
        return self._active

    @property
    def journal(self) -> Optional[dict]:
        return self._journal

    @property
    def cell_name(self):
        return (self._journal or {}).get("cell")

    def refusal_line(self) -> str:
        """The red line the worker gate returns while exploded — the ONE
        wording for the lock (the worker logs it and calls on_error with it)."""
        return _("Clusters are exploded — press \"Put back\" first "
                 "(the \"Explode\" tab)")

    def _gate(self):
        return self.refusal_line() if self._active else None

    # ── state from the journal ──────────────────────────────────────────────
    #
    # The guard NEVER reads the board itself: the door forbids a UI-thread board
    # read, and the journal path depends on the board IDENTITY (an IPC read). The
    # journal is read on a WORKER (gui/docks/explode_page.explode_state_worker)
    # and handed here as a ready answer.
    def apply_journal(self, journal) -> bool:
        """A KNOWN board's journal: present -> exploded, absent -> not exploded.
        This is the ONLY way the lock is CLEARED.

        Emits ``changed`` ONLY when the state flips; returns the new ``active``."""
        return self.set_from_journal(journal)

    def apply_unknown(self) -> bool:
        """The board IDENTITY could not be read (a busy socket, a stand-in): the
        state is LEFT EXACTLY AS IT IS.

        A missing journal must never be read as "not exploded": an unreadable
        identity would then DROP the lock while the clusters are still shifted
        aside, and the whole board would be wide open. Returns the unchanged
        ``active``."""
        return self._active

    def set_from_journal(self, journal) -> bool:
        """Set the state from a journal dict ALREADY known (a worker read, or a
        successful explode/restore). Same flip signal as :meth:`apply_journal`."""
        self._journal = journal or None
        new = self._journal is not None
        if new != self._active:
            self._active = new
            self.changed.emit(new)
        return self._active

    # ── Р3: does a re-read TRANSFER the net_traces copper? ───────────────────
    def transfer_enabled(self, cell_name, cluster, sheet) -> bool:
        """True ⇔ a live journal for this board AND its (cell, cluster, sheet)
        is the one being re-read (design РЗ8/РЗ9). Called on the UI thread; the
        boolean goes into the read payload so any read door answers alike."""
        if not self._active or not self._journal:
            return False
        if str(self._journal.get("cell") or "") != str(cell_name or ""):
            return False
        if str(self._journal.get("cluster") or "") != str(cluster or ""):
            return False
        return (self._journal.get("sheet") or None) == (sheet or None)

    def take_journal(self) -> Optional[dict]:
        """The current journal dict (for "Показать журнал"), and forget it."""
        journal = self._journal
        self.set_from_journal(None)
        return journal

    # ── teardown ────────────────────────────────────────────────────────────
    def detach(self) -> None:
        """Take the worker gate back down — the window is going away. Only
        clears it if it is still OURS (see the constructor)."""
        if worker.current_long_op_gate() == self._gate:
            worker.set_long_op_gate(None)
