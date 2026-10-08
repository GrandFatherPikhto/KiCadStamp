# kicadstamp/diagnostics/claude_mutations_accept_entities_part3_2026_10_08.py
"""Claude's acceptance rows for PART 3 of plan_2026_10_05_entities_under_cells
(commits ea591bae … b3d06a0c) — on top of Demon's rig machinery.

  * C1 a door whose entity the index no longer knows forgets the leaf's own
       (cluster, sheet) — the action falls back to the store
  * C2 "Subtract selected copper" ignores the door's address
  * C3 the fast entity items are wired to each other's engines (Add ↔ Update)
  * K1 a cosmetic comment — MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_entities_part3_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_entities_part3_2026_10_08 as ds

DOORS = "gui/entity_doors.py"
SUB = "gui/subtract_copper.py"
TREE = "gui/docks/entity_tree.py"
T = ["test_entities_part3_address.py", "test_entities_under_cells.py",
     "test_cell_entity_choice.py", "test_cell_anchor_entity_dropdown.py",
     "test_subtract_copper.py", "test_subtract_selected_copper.py",
     "test_cell_editor.py"]

ROWS = [
    ("C1 stale entity name loses the leaf's own address", DOORS,
     "    if entity_name and (cluster or sheet):\n",
     "    if False:  # MUTATION\n",
     "die", T, ()),
    ("C2 subtract ignores the door's address", SUB,
     # retargeted 08.10 after 90807597 (subtract moved onto instance_for_read)
     "                                     dock.name_edit.text().strip(),\n"
     "                                     expected_address)\n",
     "                                     dock.name_edit.text().strip(),\n"
     "                                     None)  # MUTATION\n",
     "die", T, ()),
    ("C3 Update and Add items swap engines", TREE,
     "                (\"refresh_from_selection_action\", _(\"Update from selection...\"),\n"
     "                 self.cell_refresh_requested),\n"
     "                (\"import_from_selection_action\", _(\"Add selected copper...\"),\n"
     "                 self.cell_import_requested),\n",
     "                (\"refresh_from_selection_action\", _(\"Update from selection...\"),\n"
     "                 self.cell_import_requested),  # MUTATION\n"
     "                (\"import_from_selection_action\", _(\"Add selected copper...\"),\n"
     "                 self.cell_refresh_requested),\n",
     "die", T, ()),
    ("K1 cosmetic comment (control)", DOORS,
     "def door_address(hub, entity_name, cluster=None,",
     "def door_address(hub, entity_name, cluster=None,  # control",
     "survive", T, ()),
]


if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
