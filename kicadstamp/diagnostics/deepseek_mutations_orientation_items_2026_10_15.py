# kicadstamp/diagnostics/deepseek_mutations_orientation_items_2026_10_15.py
"""Acceptance mutations for plan_2026_10_15_footprint_3d_model_loss Ч1 (the kipy
orientation shim: a footprint rotation must not lose the definition items kipy's own
setter drops), Demon, 2026-10-15.

Grown from the Demon's rig (rule 38), unchanged and reused as-is: basename-resolved T
under tests/, `_drop_pyc` for the mutated file, the `original.count(old) != 1` refusal,
"ПРОМАХ" when nothing red came back, a row-name filter that matches no row, a control that
MUST survive, and `-n auto`.

WHAT IS BEING PROVEN. The guard is tests/kicad/test_adapter_orientation_items.py.

  * M1 the restore is dropped altogether      -> К1 red (the model stays gone) and К10 red
                                                (the sent proto has no model)
  * M2 only Footprint3DModel is restored      -> К4 red (the text box is lost: the rule is
                                                by branch, not by name)
  * M3 the zero-angle shortcut is removed     -> К2 red (a zero delta rebuilds the list) and
                                                К9 red (a 270-vs--90 write)
  * M4 dropped items appended, not slotted    -> К5 red (the order of definition.items)
  * M5 the adapter bypasses the shim          -> К3 red (a pure move strips the model) and
                                                К10 red (it never reaches the proto)
  * K  a cosmetic comment                     -> MUST survive

Run: .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_orientation_items_2026_10_15.py
An optional row-name prefix filter takes the rest of argv.
"""
from kicadstamp.diagnostics import deepseek_mutations_tree_reread_modules_2026_10_05 as rig

SAFETY = "kicadstamp/kicad/kipy_footprint_safety.py"
ADAPTER = "kicadstamp/kicad/adapter.py"
GUARDS = ["test_adapter_orientation_items.py"]

ROWS = [
    ("M1 the restore is dropped altogether", SAFETY,
     "    survivors = {id(item) for item in after}\n"
     "    missing = [item for item in before\n"
     "               if not isinstance(item, BoardShape) and id(item) not in survivors]\n",
     "    survivors = {id(item) for item in after}\n"
     "    missing = []  # MUTATION\n",
     "die", GUARDS, ()),
    ("M2 only Footprint3DModel is restored", SAFETY,
     "    missing = [item for item in before\n"
     "               if not isinstance(item, BoardShape) and id(item) not in survivors]",
     "    missing = [item for item in before\n"
     "               if type(item).__name__ == \"Footprint3DModel\"\n"
     "               and id(item) not in survivors]  # MUTATION",
     "die", GUARDS, ()),
    ("M3 the zero-angle shortcut is removed", SAFETY,
     "    if target == current:\n        return 0",
     "    if target == current and False:  # MUTATION\n        return 0",
     "die", GUARDS, ()),
    ("M4 dropped items appended, not slotted", SAFETY,
     "    if len(after) == expected_survivors:",
     "    if False:  # MUTATION",
     "die", GUARDS, ()),
    ("M5 the adapter bypasses the shim", ADAPTER,
     "            restored = set_footprint_orientation(\n"
     "                kipy_item, KipyAngle.from_degrees(dto.angle_deg))",
     "            kipy_item.orientation = KipyAngle.from_degrees(dto.angle_deg)  # MUTATION\n"
     "            restored = 0",
     "die", GUARDS, ()),
    ("K cosmetic comment (control)", SAFETY,
     "    if not missing:\n        return 0",
     "    if not missing:\n        return 0  # control",
     "survive", GUARDS, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
