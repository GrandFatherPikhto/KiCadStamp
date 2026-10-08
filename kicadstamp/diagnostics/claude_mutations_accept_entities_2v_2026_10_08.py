# kicadstamp/diagnostics/claude_mutations_accept_entities_2v_2026_10_08.py
"""Claude's acceptance rows for доделка 2в of plan_2026_10_05_entities_under_cells
(commits 42faf3a5 … 23e31518) — on top of Demon's rig machinery.

  * C2 "Edit cell..." drops opened_from (survived in 2б — 2в п.3 must kill it now)
  * C6 the open page's row is selected but never APPLIED (no publish, no fields)
  * C8 a cell placed by a spoke/chain counts as "unplaced" for CellDock
  * C9 a cell WITH an entity counts as "unplaced" for CellDock
  * K1 a cosmetic comment — MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_entities_2v_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_entities_2_2026_10_08 as ds

DOORS = "gui/entity_doors.py"
MIXIN = "gui/docks/cell_instance_mixin.py"
T = (["test_entities_under_cells.py", "test_entity_index.py"]
     + ds.CHOICE_TEST + ds.DROP_TEST)

ROWS = [
    ("C2 Edit cell... drops opened_from", DOORS,
     "                                    opened_from=entity_name)",
     "                                    opened_from=None)  # MUTATION",
     "die", T, ()),
    ("C6 open page row selected but not applied", MIXIN,
     "            return False\n        self._entity_gate.choose()\n        return True\n",
     "            return False\n        return True  # MUTATION\n",
     "die", T, ()),
    ("C8 spoke-placed cell counts as unplaced", DOORS,
     "    return (not index.has_entity_for_cell(uuid)\n"
     "            and not index.placed_by_cell(uuid))",
     "    return (not index.has_entity_for_cell(uuid))  # MUTATION",
     "die", T, ()),
    ("C9 cell with entity counts as unplaced", DOORS,
     "    return (not index.has_entity_for_cell(uuid)\n"
     "            and not index.placed_by_cell(uuid))",
     "    return (not index.placed_by_cell(uuid))  # MUTATION",
     "die", T, ()),
    ("K1 cosmetic comment (control)", DOORS,
     "def pin_door_instance(hub, cell_name, entity_name) -> bool:",
     "def pin_door_instance(hub, cell_name, entity_name) -> bool:  # control",
     "survive", T, ()),
]


if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
