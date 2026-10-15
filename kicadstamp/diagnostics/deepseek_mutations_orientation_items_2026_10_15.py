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
  * M5 the adapter bypasses the guard        -> К3 red (a pure move strips the model) and
                                                К10 red (it never reaches the proto); the
                                                mutation restores the pre-Ч1 raw sync, so it
                                                also names the line Д-2 moved out of the
                                                adapter
  * K  a cosmetic comment                     -> MUST survive

Ч2 MUTATIONS (the rotation arithmetic; guards are tests/kicad/test_kipy_vector_rotate.py):

  * N1 round() goes back to int()            -> the 45-degree cell red (that cell's point is
                                                chosen where round() and int() disagree);
                                                the quarter turns stay exact, because their
                                                arms never reach the fallback
  * N2 the quarter-turn arms fall back to kipy's arithmetic -> the reproducing point and the
                                                pad cell red
  * N3 the patch is never installed          -> the reproducing point, the pad cell and the
                                                installation cell red (the ORIGINAL canary
                                                stays green: it measures kipy, not us)

NOTE on the plan's wording "remove the 90-degree arm -> the C_0603 cells go red": MEASURED,
that mutation is harmless on its own — the fallback's round() is exact for quarter turns too
(the float error never survives a round to the nearest integer at these magnitudes). So the
row that kills the arms has to give them kipy's arithmetic (N2) instead. A cell that leaned
on the fallback alone would have been green against the unfixed kipy.

Run: .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_orientation_items_2026_10_15.py
An optional row-name prefix filter takes the rest of argv.
"""
from kicadstamp.diagnostics import deepseek_mutations_tree_reread_modules_2026_10_05 as rig

SAFETY = "kicadstamp/kicad/kipy_footprint_safety.py"
ADAPTER = "kicadstamp/kicad/adapter.py"
GUARDS = ["test_adapter_orientation_items.py"]
ROTATE_GUARDS = ["test_kipy_vector_rotate.py"]

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
    ("M5 the adapter bypasses the guard", ADAPTER,
     "            restored = sync_dto_to_kipy(dto, kipy_item)",
     "            kipy_item.position = KipyVector2.from_xy(dto.position.x, dto.position.y)  # MUTATION\n"
     "            kipy_item.orientation = kipy.geometry.Angle.from_degrees(dto.angle_deg)  # MUTATION\n"
     "            kipy_item.layer = layer_to_kipy(dto.layer)  # MUTATION\n"
     "            restored = 0",
     "die", GUARDS, ()),
    ("N1 round() goes back to int()", SAFETY,
     "    self.x = round(dy * sin_angle + dx * cos_angle) + center.x\n"
     "    self.y = round(dy * cos_angle - dx * sin_angle) + center.y",
     "    self.x = int(dy * sin_angle + dx * cos_angle) + center.x  # MUTATION\n"
     "    self.y = int(dy * cos_angle - dx * sin_angle) + center.y  # MUTATION",
     "die", ROTATE_GUARDS, ()),
    ("N2 quarter-turn arms take kipy's arithmetic", SAFETY,
     "    if degrees == 90.0:\n"
     "        self.x = dy + center.x\n"
     "        self.y = -dx + center.y\n"
     "        return self",
     "    if degrees == 90.0:  # MUTATION: kipy's own maths, truncation included\n"
     "        self.x = int(dy + dx * 6.123233995736766e-17) + center.x\n"
     "        self.y = int(-dx + dy * 6.123233995736766e-17) + center.y\n"
     "        return self",
     "die", ROTATE_GUARDS, ()),
    ("N3 the patch is never installed", SAFETY,
     "    KipyVector2.rotate = _rotate_exact",
     "    pass  # MUTATION",
     "die", ROTATE_GUARDS, ()),
    ("K cosmetic comment (control)", SAFETY,
     "    if not missing:\n        return 0",
     "    if not missing:\n        return 0  # control",
     "survive", GUARDS, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
