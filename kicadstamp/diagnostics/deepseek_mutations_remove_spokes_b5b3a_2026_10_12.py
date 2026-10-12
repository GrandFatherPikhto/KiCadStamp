# kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b3a_2026_10_12.py
"""Acceptance mutations for Б5б-3a of
techdocs/handoff/deepseek/plan/plan_2026_10_08_remove_spokes.md — restore the
"slot layer overrides cell layer" cell on ClonePositionCalculator after the
spoke-side copy (manual_position_calculator) was removed, DeepSeek, 2026-10-12.

Derived from deepseek_mutations_remove_spokes_b5b_2026_10_11.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing goes
red, and a control that MUST survive.

WHAT IS BEING PROVEN. The cell lives in
tests/placement/test_clone_position_calculator.py (TestComponentSlotLayer). The
LIVE rule it guards is clone_position_calculator.py:599
`slot_layer = comp_layout.slot_layer or cell.layer`. The mutation below is
exactly the one the acceptance of Б5б-1/Б5б-2 found ALIVE (a mutant build gave
6929 passed) — here it must die.

  * M1  `slot_layer = comp_layout.slot_layer or cell.layer` -> `cell.layer`
        (the slot's OWN layer ignored — the mutation that survived acceptance)
        must go red
  * K1  a cosmetic comment — MUST survive

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b3a_2026_10_12.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

CALCULATOR = "kicadstamp/placement/services/clone_position_calculator.py"

GUARDS = ["test_clone_position_calculator.py"]

MUTATIONS = [
    # 1 — the slot's own absolute layer is dropped, the cell layer used instead:
    # a B.Cu slot in an F.Cu cell silently lands on F, and the mirror cases flip
    # the wrong side. TestComponentSlotLayer rows 1 and 3 must go red.
    ("M1 slot's own layer ignored (cell.layer wins)", CALCULATOR,
     "            slot_layer = comp_layout.slot_layer or cell.layer\n",
     "            slot_layer = cell.layer  # MUTATION\n",
     "die", GUARDS, ()),
    # K1 — a cosmetic comment edit in the mutated file: MUST survive.
    ("K1 a cosmetic comment", CALCULATOR,
     "            slot_layer = comp_layout.slot_layer or cell.layer\n",
     "            slot_layer = comp_layout.slot_layer or cell.layer  # control\n",
     "survive", GUARDS, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
