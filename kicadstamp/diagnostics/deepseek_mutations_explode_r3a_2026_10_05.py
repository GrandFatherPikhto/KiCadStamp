# kicadstamp/diagnostics/deepseek_mutations_explode_r3a_2026_10_05.py
"""Acceptance mutations for Р3а-1/Р3а-2 of plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md``. DeepSeek, 2026-10-05.

Machinery (rule 38) is the shared one from
``deepseek_mutations_refresh_mixed_2026_10_05.py``.

  * Q21 the plan is applied BEFORE the transfer checks (a refusal would then be a
        半-applied transfer: the record keeps the piece, the cell lost it)
  * K1  a cosmetic comment — MUST survive

PARTIAL BY DESIGN (named for the acceptance): the graph-lookup row for Р3а-2
(“the record's file is always the root”) has no cell yet — the 0/0 cells of Р3а-4
are what will exercise it. Until then this rig covers only the ORDER.
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = ["test_explode_page.py", "test_explode_transfer.py"]
CELL_EDITOR = "gui/docks/cell_editor.py"
TRANSFER = "kicadstamp/explode_transfer.py"

ROWS = [
    ("Q21 the plan is applied before the transfer checks", CELL_EDITOR,
     "        cfg, entry_files, refusals = self._explode_transfer_context(transfers)\n"
     "        if refusals:\n"
     "            for line in refusals:\n"
     "                self._show_message(line, _ERROR_STYLE)\n"
     "            return\n"
     "        updated, added, removed = self._apply_refresh_plan(plan)",
     "        cfg, entry_files, refusals = self._explode_transfer_context(transfers)\n"
     "        updated, added, removed = self._apply_refresh_plan(plan)  # MUTATION",
     "die", G, ()),
    ("K1 cosmetic comment (control)", TRANSFER,
     "def _section_of(kind: str) -> str:",
     "def _section_of(kind: str) -> str:  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
