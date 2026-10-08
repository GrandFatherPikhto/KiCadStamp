# kicadstamp/diagnostics/deepseek_mutations_entities_part3_2026_10_08.py
"""Acceptance mutations for PART 3 of plan_2026_10_05_entities_under_cells.md
(the address lives only where it is visible: the entity leaf's items, the Tools
legs, the cell page's dropdown), DeepSeek, 2026-10-08.

Grown from deepseek_mutations_entities_2_2026_10_08.py (rule 38), whose machinery
is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing turns
red, and a control that MUST survive.

WHAT IS BEING PROVEN (the cells live in tests/gui/docks/ and tests/selection/):
  * test_entities_part3_address.py — the door's address, the entity leaf's items,
    the reads, the subtract flow, the absent cell-leaf items, the untouched board
    handle;
  * test_phase3_wiring.py          — the Tools legs (entity vs cell leaf);
  * test_selection_narrowing.py    — the refusal naming BOTH addresses.

  * M1 the read body takes the remembered store instead of the door's address
  * M2 a board item is back on the CELL leaf (the live defect of 08.10)
  * M3 the refusal forgets the selection's own address (п.6)
  * M4 the read asks the board handle again instead of `is_connected` (п.7)
  * M5 the entity leaf's item names no entity (the address is lost)
  * M6 the Tools leg works a cell leaf again instead of refusing (п.5)
  * K1 a cosmetic comment -> MUST survive

NOT here on purpose:
  * "the item is on the WRONG entity of the cell" — the item carries ONE name and
    the door resolves exactly it; a wrong-name mutation is M5's neighbour and
    needs no second row (the same cell reddens);
  * "`pin_door_instance` stops publishing" — that is 2б's rule with its own row in
    the previous rig; часть 3 changed WHO reads the address, not the store's job.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_entities_part3_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

PART3_TEST = ["test_entities_part3_address.py"]
WIRING_TEST = ["test_phase3_wiring.py"]
SEL_TEST = ["test_selection_narrowing.py"]

# The modules (basenames resolved under tests/ by the rig).
CHOICE = "gui/cell_entity_choice.py"
DOORS = "gui/entity_doors.py"
TREE = "gui/docks/entity_tree.py"
EDITOR = "gui/docks/cell_editor.py"
MIXED = "gui/mixed_selection.py"
HUB = "gui/dock_hub.py"

MUTATIONS = [
    # 1 — the read body stops taking the door's address: the store answers again,
    # which is exactly the invisible address часть 3, п.2 removes.
    ("M1 the read takes the store instead of the address", EDITOR,
     "        # часть 3, п.2: the door's own address wins; else the page's row.\n"
     "        instance = instance_for_read(self._root_path,\n"
     "                                     self.name_edit.text().strip(),\n"
     "                                     expected_address)\n",
     "        instance = self._read_instance()  # MUTATION\n",
     "die", PART3_TEST, ()),
    # 2 — a board item is back on the CELL leaf (часть 3, п.3): the address is
    # again taken where it is not named.
    ("M2 a board item is back on the cell leaf", TREE,
     "        # 2026-09-06 (plan copy_placement_from_cell): the OFFLINE\n",
     "        menu.addAction(_(\"Update from selection...\")).triggered.connect(  # MUTATION\n"
     "            lambda: None)\n"
     "        # 2026-09-06 (plan copy_placement_from_cell): the OFFLINE\n",
     "die", PART3_TEST, []),
    # 3 — the refusal names the entity alone (часть 3, п.6): the user cannot see
    # what the selection was taken for.
    ("M3 the refusal forgets the selection's address", MIXED,
     "                not_the_entity_line(expected_address.entity_name,\n"
     "                                    own_cluster, own_sheet),\n",
     "                not_the_entity_line(expected_address.entity_name, \"\", \"\"),\n",
     "die", SEL_TEST, ()),
    # 4 — presence is asked of the board HANDLE again (часть 3, п.7): the live
    # 08.10 log carried an ERROR on every read because of exactly this.
    ("M4 the read asks the board handle again", EDITOR,
     "        if connection is None or not getattr(connection, \"is_connected\", False):\n"
     "            self._show_message(_(\"Connect to KiCad first.\"), _ERROR_STYLE)\n"
     "            return\n"
     "        if not self._components:\n"
     "            self._show_message(_(\"Load a cell with components first.\"), _ERROR_STYLE)\n"
     "            return\n"
     "        if self._path is None:\n"
     "            self._show_message(_(\"Set the project root first.\"), _ERROR_STYLE)\n"
     "            return\n"
     "        if self._active_op is not None:\n"
     "            return\n"
     "        # Э2а: an EMPTY layer set deletes every track record of this cell on\n",
     "        if getattr(connection, \"board\", None) is None:  # MUTATION\n"
     "            self._show_message(_(\"Connect to KiCad first.\"), _ERROR_STYLE)\n"
     "            return\n"
     "        if not self._components:\n"
     "            self._show_message(_(\"Load a cell with components first.\"), _ERROR_STYLE)\n"
     "            return\n"
     "        if self._path is None:\n"
     "            self._show_message(_(\"Set the project root first.\"), _ERROR_STYLE)\n"
     "            return\n"
     "        if self._active_op is not None:\n"
     "            return\n"
     "        # Э2а: an EMPTY layer set deletes every track record of this cell on\n",
     "die", PART3_TEST, ()),
    # 5 — the entity leaf's item stops naming its entity (часть 3, п.1): the door
    # then has no address at all.
    ("M5 the entity item names no entity", TREE,
     "        return (entity.get(\"cell\"), None, entity.get(\"cluster\"),\n"
     "                entity.get(\"sheet\"), entity.get(\"name\"))\n",
     "        return (entity.get(\"cell\"), None, entity.get(\"cluster\"),\n"
     "                entity.get(\"sheet\"), None)  # MUTATION\n",
     "die", PART3_TEST, ()),
    # 6 — the Tools leg acts on a CELL leaf again (часть 3, п.5): it reads the
    # remembered instance where the tree shows no address.
    ("M6 the Tools leg works a cell leaf again", HUB,
     "        if not entity:\n"
     "            show_message(_(\"pick an entity of cell {cell!r}\").format(cell=name), \"\",\n"
     "                         logging.getLogger(__name__))\n"
     "            return None\n",
     "        if False:  # MUTATION\n"
     "            return None\n",
     "die", WIRING_TEST, ()),
    # K1 — a cosmetic comment changes nothing: MUST survive.
    ("K1 a cosmetic comment", CHOICE,
     "# The three kinds of an address row.",
     "# The three KINDS of an address row.", "survive", PART3_TEST, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
