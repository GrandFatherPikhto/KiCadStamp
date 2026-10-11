# kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b1_2026_10_11.py
"""Acceptance mutations for Б5б-1 of
techdocs/handoff/deepseek/plan/plan_2026_10_08_remove_spokes.md — the sub-commit
that deletes the dead ``manual_position_calculator`` (its only product consumer
was ``planner.position_calc``, unused after под-4а), DeepSeek, 2026-10-11.

Derived from deepseek_mutations_remove_spokes_b5b_2026_10_11.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing goes
red, and a control that MUST survive.

WHAT IS BEING PROVEN. Deleting the calculator gives up nothing unguarded: the
one property that lived on it and did NOT leave with it — a real calculator's
registry keys surviving reconcile across runs — was moved in this commit to the
live carrier ClonePositionCalculator (test_registry_integration.py); M2 cuts a
key part on THAT carrier and the moved cell must go red. The registry's own
'pad:'/'point:' prefix protection cells survive the removal (the builder is gone,
the legacy keys are not), so M1/M3 prove those cells still bite.

  * M1  PROTECTED_ANCHOR_PREFIXES drops "pad:"   — the two kept pad cells go red
  * M2  clone_anchor_id loses the xy offset      — the moved integration cell
        (test_registry_integration.py) fails on its expected key
  * M3  PROTECTED_ANCHOR_PREFIXES drops "point:" — the linkage cell goes red
  * K1  a cosmetic comment                       — MUST survive

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b1_2026_10_11.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

REGISTRY = "kicadstamp/registry.py"
CLONE_CALC = "kicadstamp/placement/services/clone_position_calculator.py"

PREFIXES = ("PROTECTED_ANCHOR_PREFIXES = (\"anchor:\", \"role:\", \"name:\", \"point:\",\n"
            "                             \"thermal:\", \"pad:\", \"net:\")\n")

GUARDS = ["test_registry_rule_protection.py"]
INTEGRATION = ["test_registry_integration.py"]
LINKAGE = ["test_registry_anchor_prefix_protection.py"]

MUTATIONS = [
    # 1 — 'pad:' no longer recognised by reconcile: the two kept cells (legacy
    # spoke keys stay protected) must go red.
    ("M1 PROTECTED prefixes lose pad:", REGISTRY,
     PREFIXES,
     "PROTECTED_ANCHOR_PREFIXES = (\"anchor:\", \"role:\", \"name:\", \"point:\",\n"
     "                             \"thermal:\", \"net:\")  # MUTATION\n",
     "die", GUARDS, ()),
    # 2 — the moved carrier loses the xy offset in its anchor_ref key: the
    # integration cell's expected_key (which pins the WHOLE key) fails.
    ("M2 clone_anchor_id drops the xy offset", CLONE_CALC,
     "    if clone.anchor_ref is not None:\n"
     "        return f\"anchor:{clone.anchor_ref}:{clone.anchor_pad or ''}:{ox:.4f}:{oy:.4f}\"\n",
     "    if clone.anchor_ref is not None:\n"
     "        return f\"anchor:{clone.anchor_ref}:{clone.anchor_pad or ''}\"  # MUTATION\n",
     "die", INTEGRATION, ()),
    # 3 — 'point:' no longer recognised: the linkage cell that pairs builders to
    # the protection list must go red.
    ("M3 PROTECTED prefixes lose point:", REGISTRY,
     PREFIXES,
     "PROTECTED_ANCHOR_PREFIXES = (\"anchor:\", \"role:\", \"name:\",\n"
     "                             \"thermal:\", \"pad:\", \"net:\")  # MUTATION\n",
     "die", LINKAGE, ()),
    # K1 — a cosmetic comment in a mutated file: MUST survive.
    ("K1 a cosmetic comment", REGISTRY,
     "# (imprint: NOT in this list, and not a gap — imprint copper never reaches a\n",
     "# (imprint: NOT in this list, and not a gap — imprint copper never reaches a  # control\n",
     "survive", GUARDS, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
