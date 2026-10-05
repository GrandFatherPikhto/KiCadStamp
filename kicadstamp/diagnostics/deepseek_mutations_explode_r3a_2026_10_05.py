# kicadstamp/diagnostics/deepseek_mutations_explode_r3a_2026_10_05.py
"""Acceptance mutations for Р3а-1…Р3а-3 of plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md``. DeepSeek, 2026-10-05.

Machinery (rule 38) is the shared one from
``deepseek_mutations_refresh_mixed_2026_10_05.py``.

  * Q21 the plan is applied BEFORE the transfer checks (a refusal would then be a
        半-applied transfer: the record keeps the piece, the cell lost it)
  * Q22 the read transfers even when it resolved to ANOTHER instance (Р3а-3)
  * Q23 the journal rides into the read even for another instance (Р3а-3, the
        UI gate in DockHub)
  * Q24 transfer_enabled ignores the address (Р3а-3, the guard's own rule)
  * Q25 the transferred piece is DROPPED from the read (Р3а-3: the piece must
        stay in the plan AND be named — else the record gives it away and the
        cell never receives it)
  * K1/K2 cosmetic comments — MUST survive

PARTIAL BY DESIGN (named for the acceptance): the graph-lookup row for Р3а-2
(“the record's file is always the root”) has no cell yet — the 0/0 cells of Р3а-4
are what will exercise it. Until then this rig covers the ORDER and the Р3а-3
address gate.
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = ["test_explode_page.py", "test_explode_transfer.py"]
GUI_GATE = ["test_explode_page.py"]
KERNEL_GATE = ["test_refresh_by_cluster_h4a.py"]
CELL_EDITOR = "gui/docks/cell_editor.py"
TRANSFER = "kicadstamp/explode_transfer.py"
GLUE = "gui/mixed_selection.py"
DOCK_HUB = "gui/dock_hub.py"
GUARD = "gui/explode_guard.py"
NARROWING = "kicadstamp/selection_narrowing.py"

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
    ("Q22 the read transfers even for another instance", GLUE,
     "    transfer_ok = bool(\n"
     "        explode_transfer\n"
     "        and journal_is_the_read_instance(explode_journal, cell_name,\n"
     "                                         chosen_cluster, chosen_sheet))",
     "    transfer_ok = bool(explode_transfer)  # MUTATION",
     "die", KERNEL_GATE, ()),
    ("Q23 the journal rides along for any instance", DOCK_HUB,
     "        enabled = bool(guard is not None\n"
     "                       and guard.transfer_enabled(name, cluster, sheet))",
     "        enabled = bool(guard is not None)  # MUTATION",
     "die", GUI_GATE, ()),
    ("Q24 transfer_enabled ignores the address", GUARD,
     "        return journal_is_the_read_instance(self._journal, cell_name, cluster,\n"
     "                                            sheet)",
     "        return True  # MUTATION",
     "die", GUI_GATE, ()),
    ("Q25 the transferred piece is dropped from the read", NARROWING,
     "        kept.append(item)                      # the piece STAYS in the read...\n"
     "        if tr is not None:\n"
     "            transfers.append(tr)               # ...and is named for the transfer",
     "        if tr is None:  # MUTATION\n"
     "            kept.append(item)\n"
     "        else:\n"
     "            transfers.append(tr)",
     "die", KERNEL_GATE, ()),
    ("K1 cosmetic comment (control)", TRANSFER,
     "def _section_of(kind: str) -> str:",
     "def _section_of(kind: str) -> str:  # control",
     "survive", G, ()),
    ("K2 cosmetic comment in the glue (control)", GLUE,
     "def _component_roles(components) -> set:",
     "def _component_roles(components) -> set:  # control",
     "survive", KERNEL_GATE, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
