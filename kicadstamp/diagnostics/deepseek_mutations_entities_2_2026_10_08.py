# kicadstamp/diagnostics/deepseek_mutations_entities_2_2026_10_08.py
"""Acceptance mutations for PART 2 of plan_2026_10_05_entities_under_cells.md
(the "Entity" dropdown: the address list, the working instance, the pinned read,
the empty dropdown), DeepSeek, 2026-10-08.

Grown from deepseek_mutations_entities_1a_2026_10_08.py (rule 38), whose machinery
is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing turns
red, and a control that MUST survive.

WHAT IS BEING PROVEN (the cells live in tests/gui/ and tests/selection/):
  * test_cell_entity_choice.py         — the address list, the store, read_instance;
  * test_cell_anchor_entity_dropdown.py — the page's dropdown, the pinned read, the
    publication of the working instance, the empty dropdown (п.3);
  * test_selection_narrowing.py        — the prelude pinned to an entity address.

  * M1 the working instance is ignored -> the remembered context answers anyway
  * M2 a pick is not flagged as "not a manual edit" (the defect the first version
       of this change really had: it erased the cell's identified refs)
  * M3 "Manual…" disappears for a cell placed by a spoke
  * M4 a cell nobody places offers "Manual…" again
  * M5 the write no longer says the change reaches every entity
  * M6 the explicit address is ignored by the prelude (the refusal is gone)
  * M7 an entity with no pins reads as {} again (доделка 2б, п.1 — the live
       "Place marker" fatal: the reader branches on `is not None`)
  * M8 the door stops publishing its entity (2б, п.4 — the action then reads the
       channel the page was LAST on)
  * M9 the gate stops disabling the board buttons (2б, п.3 — the guard reads
       Qt's WA_ForceDisabled, so it cannot be satisfied by the tab being off)
  * K1 a cosmetic comment -> MUST survive

NOT here on purpose:
  * "the write goes to the ENTITY's file" — part 2 does not write there at all; the
    page's ONE write path (`_write_entry`) is part-1/pre-part-2 code with its own
    cells, and a mutation of it would prove THOSE, not this part;
  * "the non-rigid warning is dropped" — п.7's warning is not wired yet (the
    residual is a board-frame measurement and needs its own worker leg), so there is
    no cell to redden: a mutation with no guard is a miss by construction.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_entities_2_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

CHOICE_TEST = ["test_cell_entity_choice.py"]
DROP_TEST = ["test_cell_anchor_entity_dropdown.py"]
SEL_TEST = ["test_selection_narrowing.py"]

# The worker modules (basenames resolved under tests/ by the rig).
CHOICE = "gui/cell_entity_choice.py"
PICKER = "gui/docks/cell_entity_picker.py"
MIXIN = "gui/docks/cell_instance_mixin.py"
MIXED = "gui/mixed_selection.py"
HUB = "gui/dock_hub.py"
READ_ONLY = "gui/docks/cell_read_only.py"
TREE_TEST = ["test_entities_under_cells.py"]

MUTATIONS = [
    # 1 — the working instance is ignored: the reader falls back to whatever the
    # cell has remembered, i.e. the whole point of the dropdown is lost.
    ("M1 the working instance is ignored", CHOICE,
     "    address = working_instance(root_path, cell_name)\n",
     "    address = None  # MUTATION\n",
     "die", CHOICE_TEST + DROP_TEST, ()),
    # 2 — a pick stops being flagged as "one of OUR writes": the page's own
    # combos handler then reads it as a manual Cluster/Sheet edit and ERASES the
    # cell's identified refs (this is the live defect the cells caught).
    ("M2 a pick is not flagged as our own write", PICKER,
     "        self._applying = entity is not None\n",
     "        self._applying = False  # MUTATION\n",
     "die", DROP_TEST, ()),
    # 3 — "Manual…" disappears for a cell placed by a spoke: today's spoke
    # behaviour (hand-typed fields + the remembered pair) is taken away.
    ("M3 Manual disappears for a spoke cell", MIXIN,
     "        return build_choices(entities)\n",
     "        return build_choices(entities, manual=False)  # MUTATION\n",
     "die", DROP_TEST, ()),
    # 4 — a cell nobody places offers "Manual…" again (п.3): fields that can read
    # nothing invite the user in.
    ("M4 a cell nobody places offers Manual again", MIXIN,
     "        if not entities and index is not None and not index.placed_by_cell(uuid):\n",
     "        if False:  # MUTATION\n",
     "die", DROP_TEST, ()),
    # 5 — the write stops saying that it reaches every entity of the cell (п.6).
    ("M5 the write no longer says every entity", MIXIN,
     "            show_message(write_applies_line(self._cell_name, row),\n"
     "                         SUCCESS_STYLE, logger)\n",
     "            pass  # MUTATION\n",
     "die", DROP_TEST, ()),
    # 6 — the prelude ignores the explicit address: a selection of ANOTHER
    # instance is read silently (and the yellow line for an untagged one is gone).
    ("M6 the explicit address is ignored by the prelude", MIXED,
     "    if expected_address is not None and expected_address.cluster:\n",
     "    if False:  # MUTATION\n",
     "die", SEL_TEST, ()),
    # 7 — an entity that pins NO refs is turned back into an EMPTY map: the live
    # frame reader then takes the "identified by refs" path with nothing to
    # identify and dies as a stale identification ("Place marker", 2б, п.1).
    ("M7 an entity with no pins reads as an empty map", CHOICE,
     "    if not isinstance(raw, dict):\n        return None\n",
     "    if not isinstance(raw, dict):\n        return {}  # MUTATION\n",
     "die", CHOICE_TEST + DROP_TEST, ()),
    # 8 — the door stops publishing the entity it came from: the action that
    # follows reads whatever entity the page was last on (2б, п.4).
    ("M8 the door does not publish its entity", HUB,
     "    pin_working_instance(root, name, entity, index)\n",
     "    pass  # MUTATION\n",
     "die", TREE_TEST, ()),
    # 9 — the gate stops disabling the board buttons: they are then only off
    # because their tab is off — the green-for-the-wrong-reason guard Denis
    # refused (2б, п.3).
    ("M9 the gate leaves the board buttons alone", READ_ONLY,
     "        if read_only:\n"
     "            for button in board_buttons:\n"
     "                if button is not None:\n"
     "                    button.setEnabled(False)\n",
     "        pass  # MUTATION\n",
     "die", TREE_TEST, ()),
    # K1 — a cosmetic comment changes nothing: MUST survive.
    ("K1 a cosmetic comment", CHOICE,
     "# The three kinds of an address row.",
     "# The three KINDS of an address row.", "survive", CHOICE_TEST, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
