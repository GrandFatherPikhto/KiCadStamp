# kicadstamp/diagnostics/deepseek_mutations_adopt_at_current_place_2026_10_07.py
"""Acceptance mutations for plan_2026_10_07_adopt_at_current_place (step 1:
«adopt the copper at its CURRENT place, before the redraw»), DeepSeek, 2026-10-07.

Machinery — deepseek_mutations_refresh_mixed_2026_10_05 (rule 38): the
``original.count(old) != 1`` refusal (a blind template is «НЕДЕЙСТВИТЕЛЬНА»),
``_drop_pyc``, ``PYTHONDONTWRITEBYTECODE=1``, ``-n auto``, a verdict of «ПРОМАХ»
when nothing red came back, and a control that MUST survive. Every row maps to a
line of the plan's own mutation list:

  * M1 the adopted key is built DIFFERENTLY from the planner's  -> «key ⊆ plan keys»
                                                                   cell goes red
  * M2 a LIVE entry's uuid is rebound                           -> the move is lost
  * M3 copper owned by another registry entry is taken          -> foreign copper
  * M4 one record matched by TWO live items: the first is taken -> ambiguity lost
  * M4b two records claiming ONE live item: the first is taken  -> ambiguity lost
  * M5 a NON-RIGID frame is not skipped                         -> the fpga case
  * M6 dry_run WRITES the registry                              -> file changed
  * M7 the tolerance is x100                                     -> 3 mm copper taken
  * M8 non-clone items (chains/points) are walked               -> crash / foreign
  * K1 a cosmetic comment                                        -> MUST survive

NOT text-expressible as a row (checked elsewhere, named here so the list is
honest): «the pass runs AFTER the component move» is an ORDER property of the
call site in ``apply_pipeline._execute`` (between the registries and Phase 0) —
a text replacement cannot relocate a call; it is guarded by
``tests/repo``-style reading of the order in the step note, and by construction
(the pass reads the board before the first ``execute_moves``). The net_traces /
chains / thermal «not touched» boundary is the M8 scope guard: only
``kind == "clone"`` items are ever walked.

Run with the main checkout's interpreter; an optional row-name prefix filter
takes the rest of argv."""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

ADOPT = "kicadstamp/adopt_at_current_place.py"
G = ["test_adopt_at_current_place.py"]

MUTATIONS = [
    ("M1 adopted key built differently", ADOPT,
     "            key = make_registry_key(anchor_id, cell_key_part, role, index)",
     "            key = make_registry_key(anchor_id, cell_name, role, index)  # MUTATION",
     "die", G, ()),
    ("M2 a live entry's uuid is rebound", ADOPT,
     '            if entry is not None and getattr(entry, "uuid", None) in live_uuid[kind]:',
     "            if entry is not None and False:  # MUTATION",
     "die", G, ()),
    ("M3 foreign-owned copper is taken", ADOPT,
     '                       if getattr(item, "uuid", None) not in owned\n'
     "                       and pair_agrees_with_record(points, item, frame, RIGID_TOLERANCE_MM)]",
     "                       and pair_agrees_with_record(points, item, frame, RIGID_TOLERANCE_MM)]",
     "die", G, ()),
    ("M4 one record / two items taken", ADOPT,
     "            if len(matches) > 1:",
     "            if False:  # MUTATION",
     "die", G, ()),
    ("M4b two records / one item taken", ADOPT,
     '        if len(uuid_to_keys.get(getattr(item, "uuid", None), ())) > 1:\n'
     "            continue",
     "        if False:  # MUTATION\n            continue",
     "die", G, ()),
    ("M5 a non-rigid frame is not skipped", ADOPT,
     "        if frame.residual_mm > RIGID_TOLERANCE_MM:",
     "        if False:  # MUTATION",
     "die", G, ()),
    ("M6 dry_run writes the registry", ADOPT,
     "        if write:\n            regs[kind].adopt_live(key, _entry_from_live(kind, item))",
     "        if True:  # MUTATION\n            regs[kind].adopt_live(key, _entry_from_live(kind, item))",
     "die", G, ()),
    ("M7 tolerance x100", ADOPT,
     "                       and pair_agrees_with_record(points, item, frame, RIGID_TOLERANCE_MM)]",
     "                       and pair_agrees_with_record(points, item, frame, RIGID_TOLERANCE_MM * 100)]",
     "die", G, ()),
    ("M8 non-clone items are walked", ADOPT,
     '    clones = [it.obj for it in (items or ()) if getattr(it, "kind", None) == "clone"]',
     '    clones = [it.obj for it in (items or ()) if getattr(it, "kind", None) in ("clone", "chain")]',
     "die", G, ()),
    ("K1 cosmetic comment (control)", ADOPT,
     "    report = AdoptionReport()\n    if not clones:",
     "    report = AdoptionReport()  # control\n    if not clones:",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
