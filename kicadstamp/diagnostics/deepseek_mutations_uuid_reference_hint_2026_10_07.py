# kicadstamp/diagnostics/deepseek_mutations_uuid_reference_hint_2026_10_07.py
"""Acceptance rows for part 1 of plan_2026_10_05_uuid_tails (DeepSeek,
2026-10-07): the format-3 refusal of a dangling reference shows the reference's
NAME HINT and, when close, the "did you mean …?" suggestion — one shared rule
(`kicadstamp/config/name_hint.close_name_hint`), never a repair by name.

  * M1 the refusal detail is dropped            -> neither hint nor suggestion
  * M2 the suggestion is points-only            -> a `cells` typo loses it
  * M3 a valid name repairs a dangling uuid     -> the refusal never happens
  * M4 the name hint is not shown               -> only "did you mean" survives
  * K1 a cosmetic comment                       -> MUST survive

Guards: tests/config/test_format3_reference_hint.py (the four refusal shapes) and
tests/config/test_points_loading.py (the un-pinned suggestion cell).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_uuid_reference_hint_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

FORMAT3 = "kicadstamp/config/format3.py"
G = ["test_format3_reference_hint.py", "test_points_loading.py"]

ROWS = [
    # M1 — the whole detail (name hint + suggestion) is dropped: the refusal
    # names the uuid and nothing the user typed.
    ("M1 the refusal detail is dropped", FORMAT3,
     "            message += _reference_refusal_detail(hint, names_by_section.get(target, set()))",
     "            message += \"\"  # MUTATION",
     "die", G, ()),
    # M2 — the suggestion becomes a points-only special case: a `cells` typo
    # (or any other target) loses the hint.
    ("M2 the suggestion is points-only", FORMAT3,
     "            message += _reference_refusal_detail(hint, names_by_section.get(target, set()))",
     "            message += _reference_refusal_detail(\n"
     "                hint, names_by_section.get(target, set()) if target == \"points\" else set())  # MUTATION",
     "die", G, ()),
    # M3 — repair by name: a dangling reference whose name hint is present is
    # accepted instead of refused (§0 forbids exactly this).
    ("M3 a valid name repairs a dangling uuid", FORMAT3,
     "        if uuid not in uuids_by_section.get(target, set()):\n"
     "            # The base message keeps its msgid; the hint is appended from the ONE",
     "        if uuid not in uuids_by_section.get(target, set()) and not hint:  # MUTATION\n"
     "            # The base message keeps its msgid; the hint is appended from the ONE",
     "die", G, ()),
    # M4 — the name hint is not shown, only the suggestion survives.
    ("M4 the name hint is not shown", FORMAT3,
     "    return (_(\" (name hint: {hint!r})\").format(hint=hint)\n"
     "            + close_name_hint(hint, known_names))",
     "    return close_name_hint(hint, known_names)  # MUTATION",
     "die", G, ()),
    # K1 — cosmetic comment (control): must survive.
    ("K1 cosmetic comment (control)", FORMAT3,
     "logger = logging.getLogger(__name__)",
     "logger = logging.getLogger(__name__)  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
