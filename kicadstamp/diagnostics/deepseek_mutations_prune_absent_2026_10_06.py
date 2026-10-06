# kicadstamp/diagnostics/deepseek_mutations_prune_absent_2026_10_06.py
"""Demon's acceptance rows for the corrected Н4 п.5 (plan_2026_10_06_
prune_absent_cell_copper.md; Claude, 2026-10-06).

Guards: tests/gui/docks/test_prune_absent_cell_copper.py (worker path), plus the
regression cells of tests/selection/ (divide_unpaired_records /
apply_live_copper_rule). Run on deepseek_mutations_refresh_mixed_2026_10_05.py's
machinery (count == 1 or НЕДЕЙСТВИТЕЛЬНА, `_drop_pyc`, PYTHONDONTWRITEBYTECODE,
`-n auto`, ПРОМАХ on zero reds, a control that MUST survive) — grown from it,
not written fresh (rule 38).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_prune_absent_2026_10_06.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

EDITOR = "gui/docks/cell_editor.py"
NARROW = "kicadstamp/selection_narrowing.py"
ABSENT = "kicadstamp/absent_copper_prune.py"
G = ["test_prune_absent_cell_copper.py", "test_selection_narrowing.py",
     "test_refresh_by_cluster_h4a.py"]

ROWS = [
    # M1 — Дефект 1 comes back: the record lists handed to the rule are empty,
    # so the index never resolves and every unpaired record is judged absent.
    ("M1 the record lists are not the cell's own", EDITOR,
     "    return list(apply_live_copper_rule(\n"
     "        plan, ctx, payload.get(\"vias\") or [], payload.get(\"tracks\") or [],\n"
     "        presence=presence, record_name=record))",
     "    return list(apply_live_copper_rule(\n"
     "        plan, ctx, [], [],\n"
     "        presence=presence, record_name=record))  # MUTATION",
     "die", G, ()),
    # M2 — "no live pair -> gone" is decided by the registry ALONE (geometry
    # ignored): a record whose uuid went stale is deleted although its copper
    # is on the board.
    ("M2 geometry is ignored (registry only)", NARROW,
     "        on_board = bool(uuid) and uuid in live\n"
     "        if not on_board and presence is not None:\n"
     "            on_board = presence.has(kind, rec.get(\"role\"), index)",
     "        on_board = bool(uuid) and uuid in live  # MUTATION\n"
     "        if not on_board and presence is not None:\n"
     "            on_board = False",
     "die", G, ()),
    # M3 — only geometry decides (the registry half dropped): a record the
    # registry still knows as live is deleted when geometry happens to miss.
    ("M3 the registry half is dropped (geometry only)", NARROW,
     "        on_board = bool(uuid) and uuid in live",
     "        on_board = False  # MUTATION",
     "die", G, ()),
    # M4 — an empty dry run ("could not check") deletes instead of keeping.
    ("M4 a could-not-check dry run deletes", NARROW,
     "    if presence is not None and not presence.checked:",
     "    if False:  # MUTATION",
     "die", G, ()),
    # M5 — the IMPORT door's call of the rule is not fixed (old 2-arg form).
    # The "м1" comment only stands over the import call, so this row hits it and
    # not the refresh one (the two calls are otherwise identical).
    ("M5 the import call is not fixed", EDITOR,
     "            # м1: the board-copper lines (unreadable board, \"could not check\")\n"
     "            # come from ONE place on BOTH doors. Import never deletes, so only a\n"
     "            # failed read or a could-not-check verdict can produce a line here.\n"
     "            if prelude is not None and prelude.copper_ctx is not None:\n"
     "                for line in _prune_absent_cell_copper(plan, prelude, payload, cfg):",
     "            if prelude is not None and prelude.copper_ctx is not None:\n"
     "                for line in apply_live_copper_rule(plan, prelude.copper_ctx):  # MUTATION",
     "die", G, ()),
    # K1 — cosmetic comment (control): must survive.
    ("K1 cosmetic comment (control)", ABSENT,
     "__all__ = [\n    \"BoardCopperPresence\",\n    \"instance_copper_presence\",\n]",
     "__all__ = [\n    \"BoardCopperPresence\",\n    \"instance_copper_presence\",\n]  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
