# kicadstamp/diagnostics/deepseek_mutations_subtract_selection_2026_10_06.py
"""Demon's acceptance rows for the С-2 CORE («Subtract selected copper», plan
plan_2026_10_06_prune_absent_cell_copper, intermediate review of 5eb0df00).

  * M1 the key's role part is ignored again  -> a COMPONENT's via removes the
                                                cell-level record with the same index
  * M2 the geometry tier is dropped          -> only a registry uuid can pair a record
  * M3 an empty dry run is "nothing to subtract" -> an unchecked read deletes nothing
                                                silently instead of saying so
  * M4 the components-only guard is lost     -> a components-only selection looks like
                                                "nothing to subtract"
  * K1 a cosmetic comment                    -> MUST survive

(The plan's fourth row, «Import started subtracting», needs no row of its own: the
import door has its own guards — tests/gui/docks/test_cell_editor.py,
test_cell_editor_mixed_selection.py, tests/selection/ — and they stay green.)

Grown from deepseek_mutations_prune_absent_2026_10_06.py's machinery (rule 38) —
`count == 1` or НЕДЕЙСТВИТЕЛЬНА, `_drop_pyc`, PYTHONDONTWRITEBYTECODE, `-n auto`, a
ПРОМАХ verdict on zero reds, and a control that MUST survive.

Guard: tests/selection/test_subtract_selection.py (the core cells; the GUI half of
the action is a later commit).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_subtract_selection_2026_10_06.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

SUBTRACT = "kicadstamp/subtract_selection.py"
ABSENT = "kicadstamp/absent_copper_prune.py"
WORKER = "gui/subtract_copper.py"
EDITOR = "gui/docks/cell_editor.py"
G = ["test_subtract_selection.py", "test_subtract_selected_copper.py"]

ROWS = [
    # M1 — the defect the intermediate review found: the record is resolved from
    # the FLAT cell list, so the key's role part (the level) is thrown away and a
    # component's via 0 removes the cell's via 0.
    ("M1 the key's role part is ignored again", SUBTRACT,
     "        bucket = _record_bucket(kind, role_part, cell_vias, cell_tracks, by_role)",
     '        bucket = (cell_vias if kind == "via" else cell_tracks)  # MUTATION',
     "die", G, ()),
    # M2 — the exact-geometry tier (for copper whose registry uuid went stale) is
    # dropped: only a registry uuid can pair a record.
    ("M2 the geometry tier is dropped (uuid only)", ABSENT,
     "        for m in match_planned_copper(reg, list(cmds or ()), live_items=live):",
     "        for m in match_planned_copper(reg, list(cmds or ()), live_items=[]):  # MUTATION",
     "die", G, ()),
    # M3 — the worker treats an empty dry run as an ordinary empty answer instead of
    # "we could not check": the red line disappears and the cell is not told.
    ("M3 an empty dry run looks like 'nothing to subtract'", WORKER,
     "        if record_map.empty:",
     "        if False:  # MUTATION",
     "die", G, ()),
    # M4 — the components-only guard is lost: a selection of components alone stops
    # being named and reads as "nothing to subtract".
    ("M4 the components-only guard is lost", EDITOR,
     "        if result.get(\"no_copper\"):",
     "        if False:  # MUTATION",
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
