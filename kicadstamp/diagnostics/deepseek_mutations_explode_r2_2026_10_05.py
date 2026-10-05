# kicadstamp/diagnostics/deepseek_mutations_explode_r2_2026_10_05.py
"""Acceptance mutations for Р2 of plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md`` — the "Разнос" tab, the lock
and the exit window. DeepSeek, 2026-10-05.

Machinery (rule 38) is the shared one from
``deepseek_mutations_refresh_mixed_2026_10_05.py``: basename-resolved guards under
tests/, ``_drop_pyc`` for the mutated file, the ``count != 1`` refusal, a
``ПРОМАХ`` when nothing red came back, and a control that MUST survive.

The guards are tests/gui/test_explode_page.py (the tab, the lock, the exit window,
the post-crash state) and tests/gui/test_explode_worker_gate.py (the worker gate).

  * Q1  the lock does not pin the current tab to "Explode" while exploded
  * Q2  the start_long_op gate does not check `active`
  * Q3  "exploded" is a flag in memory, not the journal (a restart loses the lock)
  * Q4  a real quit while exploded asks nothing
  * Q5  "Restore and quit" closes even when the restore FAILED
  * Q6  ticks stay editable after "Explode"
  * Q7  a tee / multi row is not highlighted
  * Q8  the lock disables the WHOLE left-tabs container (Р2а-1 blocker)
  * Q9  an unknown board identity is read as "no journal" (Р2а-2 blocker)
  * Q10 board is read on the UI thread again (_adapter) (Р2а-2 door)
  * Q11 explode writes a journal under "(unknown board)" (Р2а-2)
  * Q12 "Explode…" takes the first record instead of the submenu (Р2а-3)
  * K1  a cosmetic comment — MUST survive
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = ["test_explode_page.py", "test_explode_worker_gate.py",
     "test_explode_journal.py"]
DOCK_HUB = "gui/dock_hub.py"
WORKER = "gui/worker.py"
GUARD = "gui/explode_guard.py"
MAIN = "gui/main_window.py"
PAGE = "gui/docks/explode_page.py"
JOURNAL = "kicadstamp/explode_journal.py"

ROWS = [
    ("Q1 the lock does not show the explode tab", DOCK_HUB,
     "            view.select_explode_tab()",
     "            pass  # MUTATION",
     "die", G, ()),
    ("Q2 the gate does not check active", WORKER,
     "    if not allowed_while_exploded:",
     "    if False:  # MUTATION",
     "die", G, ()),
    ("Q3 exploded is memory, not the journal", GUARD,
     "        self._journal = journal or None\n"
     "        new = self._journal is not None",
     "        self._journal = journal or None\n"
     "        new = False  # MUTATION",
     "die", G, ()),
    ("Q4 a real quit while exploded asks nothing", MAIN,
     "        if not self._return_clusters_before_quit(self.close):\n"
     "            event.ignore()\n"
     "            return",
     "        # MUTATION: quit without the return window",
     "die", G, ()),
    ("Q5 restore-and-quit closes on a FAILED restore", MAIN,
     "        self._dock_hub.explode_page.request_restore(on_success=_ok)",
     "        self._dock_hub.explode_page.request_restore("
     "on_success=_ok, on_error=_ok)  # MUTATION",
     "die", G, ()),
    ("Q6 ticks stay editable after Explode", PAGE,
     "                self._make_checkable(item, not active)",
     "                self._make_checkable(item, True)  # MUTATION",
     "die", G, ()),
    ("Q7 a tee row is not highlighted", PAGE,
     "                    if piece.touches in (\"tee\", \"multi\"):",
     "                    if False:  # MUTATION",
     "die", G, ()),
    ("Q8 the lock disables the whole container", DOCK_HUB,
     "        self.left_tabs.tabBar().setEnabled(unlocked)",
     "        self.left_tabs.setEnabled(unlocked)  # MUTATION",
     "die", G, ()),
    ("Q9 unknown identity read as no-journal", PAGE,
     "        if kind == \"unknown\":\n"
     "            self._guard.apply_unknown()          # LEAVE the lock as it is\n"
     "            return",
     "        if False:  # MUTATION\n"
     "            self._guard.apply_unknown()\n"
     "            return",
     "die", G, ()),
    ("Q10 board read on the UI thread again", PAGE,
     "        if not self._connected() or self._root_path is None:",
     "        adapter = getattr(getattr(self._connection(), \"board\", None),\n"
     "                          \"adapter\", None)  # MUTATION\n"
     "        if adapter is None or self._root_path is None:",
     "die", G, ()),
    ("Q11 explode writes under (unknown board)", JOURNAL,
     "    if board_identity(adapter) == UNKNOWN_BOARD:\n"
     "        raise ExplodeError(_(\n"
     "            \"cannot read the board identity — refusing to explode: the journal \"\n"
     "            \"would be shared by every board\"))",
     "    if False:  # MUTATION\n"
     "        raise ExplodeError(\"never\")",
     "die", G, ()),
    ("Q12 explode door takes the first record", DOCK_HUB,
     "                pick_instance(\n"
     "                    self.main_window, choice.candidates,\n"
     "                    lambda c, s: self._open_explode(name, file_path, c, s))\n"
     "                return",
     "                pass  # MUTATION: the door does not ask\n"
     "                return",
     "die", G, ()),
    ("K1 cosmetic comment (control)", PAGE,
     "class ExplodePage(QWidget):",
     "class ExplodePage(QWidget):  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
