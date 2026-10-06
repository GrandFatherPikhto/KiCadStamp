# kicadstamp/diagnostics/deepseek_mutations_subtract_selection_2026_10_06.py
"""Demon's acceptance rows for the С-2 CORE («Subtract selected copper», plan
plan_2026_10_06_prune_absent_cell_copper, intermediate review of 5eb0df00).

  * M1 the key's role part is ignored again  -> a COMPONENT's via removes the
                                                cell-level record with the same index
  * K1 a cosmetic comment                    -> MUST survive

Grown from deepseek_mutations_prune_absent_2026_10_06.py's machinery (rule 38) —
`count == 1` or НЕДЕЙСТВИТЕЛЬНА, `_drop_pyc`, PYTHONDONTWRITEBYTECODE, `-n auto`, a
ПРОМАХ verdict on zero reds, and a control that MUST survive.

Guard: tests/selection/test_subtract_selection.py (the core cells; the GUI half of
the action is a later commit).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_subtract_selection_2026_10_06.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

SUBTRACT = "kicadstamp/subtract_selection.py"
G = ["test_subtract_selection.py"]

ROWS = [
    # M1 — the defect the intermediate review found: the record is resolved from
    # the FLAT cell list, so the key's role part (the level) is thrown away and a
    # component's via 0 removes the cell's via 0.
    ("M1 the key's role part is ignored again", SUBTRACT,
     "        bucket = _record_bucket(kind, role_part, cell_vias, cell_tracks, by_role)",
     '        bucket = (cell_vias if kind == "via" else cell_tracks)  # MUTATION',
     "die", G, ()),
    # K1 — cosmetic comment (control): must survive.
    ("K1 cosmetic comment (control)", SUBTRACT,
     "    removed: tuple = ()",
     "    removed: tuple = ()  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
