# kicadstamp/diagnostics/deepseek_mutations_explode_r2b_2026_10_05.py
"""Acceptance mutations for Р2б of plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md`` — the connect-time order
(overlay reconcile BEFORE the explode-state read), the QUIET automatic state
read, and the exit window while exploded. DeepSeek, 2026-10-05.

Machinery (rule 38) is the shared one from
``deepseek_mutations_refresh_mixed_2026_10_05.py``: basename-resolved guards under
tests/, ``_drop_pyc`` for the mutated file, the ``count != 1`` refusal, a
``ПРОМАХ`` when nothing red came back, and a control that MUST survive.

The guard is tests/gui/test_explode_page.py (the connect order cell, the quiet
failure cell, the exit-while-exploded door cell).

  * Q13 the state read runs BEFORE the overlay reconcile again (Р2б-1 regression)
  * Q14 the automatic state read goes RED on failure (Р2б-2)
  * Q15 a failed automatic read DROPS the lock (apply_journal(None)) (Р2б-2)
  * Q16 a real quit while exploded asks nothing (Р2б-3; also in the Р2 harness)
  * K1  a cosmetic comment — MUST survive
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = ["test_explode_page.py"]
DOCK_HUB = "gui/dock_hub.py"
PAGE = "gui/docks/explode_page.py"
MAIN = "gui/main_window.py"

ROWS = [
    ("Q13 state read before the overlay reconcile", DOCK_HUB,
     "        from .worker import start_long_op\n"
     "        board = getattr(connection, \"board\", None)\n"
     "        adapter = getattr(board, \"adapter\", None) "
     "if board is not None else None\n"
     "        if adapter is None or getattr(connection, \"long_op_active\", False):",
     "        self.refresh_explode_state()  # MUTATION: the old order\n"
     "        from .worker import start_long_op\n"
     "        board = getattr(connection, \"board\", None)\n"
     "        adapter = getattr(board, \"adapter\", None) "
     "if board is not None else None\n"
     "        if adapter is None or getattr(connection, \"long_op_active\", False):",
     "die", G, ()),
    ("Q14 the automatic read goes red on failure", PAGE,
     "            self._on_state_read_failed if quiet else self._on_op_failed,",
     "            self._on_op_failed,  # MUTATION",
     "die", G, ()),
    ("Q15 a failed automatic read drops the lock", PAGE,
     "        logger.debug(\"explode state read failed: %s\", message)\n"
     "        self._guard.apply_unknown()",
     "        logger.debug(\"explode state read failed: %s\", message)\n"
     "        self._guard.apply_journal(None)  # MUTATION",
     "die", G, ()),
    ("Q16 a real quit while exploded asks nothing", MAIN,
     "        if not self._return_clusters_before_quit(self.close):\n"
     "            event.ignore()\n"
     "            return",
     "        # MUTATION: quit without the return window",
     "die", G, ()),
    ("K1 cosmetic comment (control)", PAGE,
     "class ExplodePage(QWidget):",
     "class ExplodePage(QWidget):  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
