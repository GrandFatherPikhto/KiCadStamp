# kicadstamp/diagnostics/claude_mutations_accept_entities_2b_2026_10_08.py
"""Claude's acceptance rows for доделка 2б of plan_2026_10_05_entities_under_cells
(commits c2fd796e, a4b00299, b5cbb05c, 8197ecd3) — on top of Demon's rig machinery.

  * C1 a door whose entity name no longer resolves KEEPS the old record (the
       stale channel the clearing exists to prevent)
  * C2 "Edit cell..." of an entity leaf opens the page without `opened_from`
       (the page lands on the cell's last/first entity, not this one)
  * C3 the entity leaf's "Edit cell..." sends the CELL name as the entity
  * C4 `entity_named` forgets entities of imprints
  * C5 the gate disables the board buttons on EVERY open, not only read-only
  * K1 a cosmetic comment — MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_entities_2b_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_entities_2_2026_10_08 as ds

CHOICE = "gui/cell_entity_choice.py"
HUB = "gui/dock_hub.py"
TREE = "gui/docks/entity_tree.py"
INDEX = "gui/docks/entity_index.py"
GATE = "gui/docks/cell_read_only.py"
T = (["test_entities_under_cells.py", "test_entity_index.py"]
     + ds.CHOICE_TEST + ds.DROP_TEST)

ROWS = [
    ("C1 unresolved door name keeps the old record", CHOICE,
     "        remember_working_instance(root_path, cell_name, None)\n"
     "        return False\n",
     "        return False  # MUTATION\n",
     "die", T, ()),
    ("C2 Edit cell... drops opened_from", HUB,
     "load_entry(name, file_path, opened_from=entity)",
     "load_entry(name, file_path, opened_from=None)  # MUTATION",
     "die", T, ()),
    ("C3 Edit cell... sends the cell name as entity", TREE,
     "                e=entity.get(\"name\"), f=file_path:\n"
     "                self.cell_anchor_entity_requested.emit(n, f, e))",
     "                e=entity.get(\"cell\"), f=file_path:  # MUTATION\n"
     "                self.cell_anchor_entity_requested.emit(n, f, e))",
     "die", T, ()),
    ("C4 entity_named forgets imprint entities", INDEX,
     "        for table in (self.entities_by_cell, self.entities_by_imprint):",
     "        for table in (self.entities_by_cell,):  # MUTATION",
     "die", T, ()),
    ("C5 gate disables board buttons on every open", GATE,
     "        if read_only:\n            for button in board_buttons:",
     "        if True:  # MUTATION\n            for button in board_buttons:",
     "die", T, ()),
    ("K1 cosmetic comment (control)", GATE,
     "class ReadOnlyGate:",
     "class ReadOnlyGate:  # control",
     "survive", T, ()),
]


if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
