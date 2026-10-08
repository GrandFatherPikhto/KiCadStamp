# kicadstamp/diagnostics/claude_mutations_accept_entities_1a_2026_10_08.py
"""Claude's acceptance rows for доделка 1а of plan_2026_10_05_entities_under_cells
(commits 1658aca5, 9cb99977, b3b57002) — on top of Demon's rig machinery.

  * C1 the orphan entity's mark is a WARNING, not Critical (the decided level)
  * C2 the read-only gate never disables the page (the hint alone remains)
  * C3 the read-only gate never shows its hint (the page is disabled silently)
  * C4 "placed by" groups by OWNER only — a chain and a clone_placement with the
       same name collapse into one mention
  * K1 a cosmetic comment — MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_entities_1a_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_entities_1a_2026_10_08 as ds

TREE = "gui/docks/entity_tree.py"
GATE = "gui/docks/cell_read_only.py"
T = ["test_entities_under_cells.py", "test_entity_index.py"]

ROWS = [
    ("C1 orphan entity mark is Warning", TREE,
     "QStyle.StandardPixmap.SP_MessageBoxCritical))",
     "QStyle.StandardPixmap.SP_MessageBoxWarning))  # MUTATION",
     "die", T, ()),
    ("C2 read-only gate never disables", GATE,
     "        tabs.setEnabled(not read_only)",
     "        tabs.setEnabled(True)  # MUTATION",
     "die", T, ()),
    ("C3 read-only gate never shows hint", GATE,
     "        self.note.setVisible(read_only)",
     "        self.note.setVisible(False)  # MUTATION",
     "die", T, ()),
    ("C4 placed-by groups by owner only", TREE,
     "            key = (_PLACED_BY_LABEL.get(pb.kind, pb.kind), owner)",
     "            key = (_PLACED_BY_LABEL.get(pb.kind, pb.kind) if False else '', owner)  # MUTATION",
     "die", T, ()),
    ("K1 cosmetic comment (control)", GATE,
     "class ReadOnlyGate:",
     "class ReadOnlyGate:  # control",
     "survive", T, ()),
]

if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
