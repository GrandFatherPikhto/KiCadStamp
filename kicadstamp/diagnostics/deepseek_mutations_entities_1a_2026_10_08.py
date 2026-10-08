# kicadstamp/diagnostics/deepseek_mutations_entities_1a_2026_10_08.py
"""Acceptance mutations for the 1a follow-up of plan_2026_10_05_entities_under_cells.md
(ромб include:, выделение из другого файла, вынос read-only, подсказка «placed
by», значок сироты, пункты меню), DeepSeek, 2026-10-08.

Grown from deepseek_mutations_entities_under_cells_2026_10_08.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a miss, not a kill), ПРОМАХ on zero reds, and a
control that MUST survive.

WHAT IS BEING PROVEN (the cells live in tests/gui/docks/):
  * test_entity_index.py        — the diamond index cell (п.1);
  * test_entities_under_cells.py — the diamond tree cell, the two selection cells
    (п.2), the placed-by hint (п.4), the orphan's mark (п.5), and the menu table
    (п.6: three points × two sources).

  * M1 the file is walked TWICE again (diamond)        -> п.1 cells
  * M2 the placer count is dropped                     -> п.4 cell
  * M3 the orphan loses its mark                       -> п.5 cell
  * M4 a leaf's identity takes the ANCESTOR's file     -> п.2 cell (same names)
  * M5 the CELL's "Select cell" sends nothing          -> п.6 row (cell)
  * M6 the ENTITY's "Select cell" keeps no instance    -> п.6 row (entity)
  * K1 a cosmetic comment                              -> MUST survive

п.3 (the read-only mode moved out of the giant) has no mutation here on purpose:
its guard is the file size itself (D8/rule 45), which is read off `wc -l`, not
reddened by a test.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_entities_1a_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

ROWS_TEST = ["test_entities_under_cells.py"]
IDX_TEST = ["test_entity_index.py"]

# The worker modules (basenames resolved under tests/ by the rig).
EIDX = "gui/docks/entity_index.py"
ET = "gui/docks/entity_tree.py"
CFG = "gui/docks/config_tree.py"

MUTATIONS = [
    # 1 — the diamond comes back: one file collected once per include entry.
    ("M1 the same file is walked twice again", EIDX,
     "        if node.path in seen:\n"
     "            continue\n",
     "        if False:  # MUTATION\n"
     "            continue\n",
     "die", IDX_TEST + ROWS_TEST, ()),
    # 2 — the placer count is lost: one mention, but no "(3)".
    ("M2 the placer count is dropped", ET,
     "            if count > 1:\n",
     "            if False:  # MUTATION\n",
     "die", ROWS_TEST, ()),
    # 3 — the orphan keeps its hint but loses the MARK (п.5).
    ("M3 the orphan loses its mark", ET,
     "            leaf.setIcon(0, self.style().standardIcon(\n"
     "                QStyle.StandardPixmap.SP_MessageBoxCritical))\n",
     "            pass  # MUTATION: hint only\n",
     "die", ROWS_TEST, ()),
    # 4 — the leaf identity takes the visible ANCESTOR's file: two same-named
    # entities of one cell become indistinguishable.
    ("M4 the leaf identity takes the ancestor file", CFG,
     "            return (\"leaf\", file_ctx[0], data[1], name)",
     "            return (\"leaf\", file_ctx[1], data[1], name)  # MUTATION",
     "die", ROWS_TEST, ()),
    # 5 — the CELL's "Select cell" sends nothing (the defect the text-reading
    # guards used to miss: `lambda: None` at entity_tree.py).
    ("M5 the cell's Select cell sends nothing", ET,
     "        select_action.triggered.connect(\n"
     "            lambda: self.cell_select_requested.emit(\n"
     "                old_name, file_path, None, None))\n",
     "        select_action.triggered.connect(lambda: None)  # MUTATION\n",
     "die", ROWS_TEST, ()),
    # 6 — the ENTITY's "Select cell" loses its instance (config_tree.py).
    ("M6 the entity's Select cell loses its instance", CFG,
     "                    select_action = menu.addAction(_(\"Select cell\"))\n"
     "                    select_action.setObjectName(\"select_cell_action\")\n"
     "                    select_action.triggered.connect(\n"
     "                        lambda checked=False, n=entity.get(\"cell\"),\n"
     "                        c=entity.get(\"cluster\"), s=entity.get(\"sheet\"):\n"
     "                        self.cell_select_requested.emit(n, None, c, s))\n",
     "                    select_action = menu.addAction(_(\"Select cell\"))\n"
     "                    select_action.setObjectName(\"select_cell_action\")\n"
     "                    select_action.triggered.connect(\n"
     "                        lambda checked=False, n=entity.get(\"cell\"):\n"
     "                        self.cell_select_requested.emit(n, None, None, None))\n",
     "die", ROWS_TEST, ()),
    # control — a cosmetic comment MUST survive.
    ("K1 cosmetic comment (control)", ET,
     "    def _placed_by_text(self, placed) -> str:",
     "    def _placed_by_text(self, placed) -> str:  # control",
     "survive", ROWS_TEST, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
