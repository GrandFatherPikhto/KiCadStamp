# kicadstamp/diagnostics/deepseek_mutations_fieldstool_stale_2026_10_11.py
"""Acceptance mutations for plan_2026_10_11_fieldstool_stale_schematic.md —
fieldstool judging a schematic read taken before the last Eeschema save,
DeepSeek, 2026-10-11.

Derived from deepseek_mutations_remove_spokes_b5b_2026_10_11.py (rule 38),
whose machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME
guards: basename-resolved tests under tests/, `_drop_pyc`, the
`original.count(old) != 1` refusal (a non-unique template is a MISS, not a
kill), ПРОМАХ when nothing goes red, and a control that MUST survive.

WHAT IS BEING PROVEN. The window used to read the sheet hierarchy once, so
Pending/Apply/Sync judged a read taken BEFORE the last Eeschema save. It now
re-reads itself, cheaply (stat probe on the ~2s poll, Apply, Sync), and
Apply/Sync decide on the recomputed diff. The guards are
tests/gui/docks/test_fieldstool_window.py (C1/C2/C3/C4/C4-sync/C5 + the failure
and button cells) and tests/gui/test_schema_model.py (the signature probe).

  * M1  the stat check removed -> never re-read (Rescan only)   -> C1/C3/C4 die
  * M2  re-read on EVERY trigger (staleness ignored)            -> C2 dies
  * M3  Apply without the freshness gate                        -> C4 dies
  * M4  Sync without the freshness gate                         -> C4-sync dies
  * M5  a window restored on the all-mismatch Apply refusal     -> C5 dies
  * K1  a cosmetic comment                                      -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_fieldstool_stale_2026_10_11.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

FRESHNESS = "gui/schematic_freshness.py"
WINDOW = "gui/fieldstool_window.py"

GUARDS = ["test_fieldstool_window.py", "test_schema_model.py"]

# The one cheap-probe line the freshness module owns (unique in its file).
STALE_CHECK = ("        if self._signature is None or not signature_is_stale(self._signature):\n"
               "            return None, None\n")

MUTATIONS = [
    # 1 — the stat check is gone: the schematic is only re-read by the Rescan
    # BUTTON again. A sheet saved after the window read stays unnoticed, so a
    # stale mismatch never clears (C1), a new sheet is never picked up (C3) and
    # Apply judges the stale read (C4) -> all red.
    ("M1 the stat check removed (Rescan only)", FRESHNESS,
     STALE_CHECK,
     "        return None, None  # MUTATION: never re-read (Rescan only)\n",
     "die", GUARDS, ()),
    # 2 — staleness ignored: EVERY trigger re-reads. Correct results, but the
    # poll tick now walks the hierarchy (reading every sheet's text) on every
    # tick — C2's spy sees calls != [] -> red.
    ("M2 re-read on every trigger", FRESHNESS,
     STALE_CHECK,
     "        pass  # MUTATION: always re-read\n",
     "die", GUARDS, ()),
    # 3 — Apply skips the freshness gate: it decides on whatever the last read
    # was, so a save since then makes it answer "Nothing to apply" wrongly
    # -> C4 red.
    ("M3 Apply without the freshness gate", WINDOW,
     "        # files changed. No board needed (Apply runs with KiCad closed anyway).\n"
     "        self._maybe_reread_schematic()\n"
     "        # Apply requires KiCad closed anyway (check below), so there is no\n",
     "        # files changed. No board needed (Apply runs with KiCad closed anyway).\n"
     "        pass  # MUTATION: no freshness check in Apply\n"
     "        # Apply requires KiCad closed anyway (check below), so there is no\n",
     "die", GUARDS, ()),
    # 4 — Sync's twin of M3: the stale read swallows the whole action
    # -> C4-sync red.
    ("M4 Sync without the freshness gate", WINDOW,
     "        made on a read taken before the last Eeschema save.\"\"\"\n"
     "        self._maybe_reread_schematic()\n"
     "        syncable = [e for e in self._pending_edits if not e.mismatched]\n",
     "        made on a read taken before the last Eeschema save.\"\"\"\n"
     "        pass  # MUTATION: no freshness check in Sync\n"
     "        syncable = [e for e in self._pending_edits if not e.mismatched]\n",
     "die", GUARDS, ()),
    # 5 — a window (QMessageBox.warning) restored on the all-mismatch Apply
    # refusal, which rule 43 and the plan moved to a Log line -> C5 red.
    ("M5 a window restored on the mismatch refusal", WINDOW,
     "                      count=len(mismatched)),\n"
     "                _ERROR_STYLE, logger)\n"
     "            return\n"
     "        if mismatched:\n",
     "                      count=len(mismatched)),\n"
     "                _ERROR_STYLE, logger)\n"
     "            QMessageBox.warning(self, _(\"Nothing to apply\"), \"mutated\")  # MUTATION\n"
     "            return\n"
     "        if mismatched:\n",
     "die", GUARDS, ()),
    # K1 — a cosmetic comment in the mutated file: MUST survive.
    ("K1 a cosmetic comment", WINDOW,
     "        # The schematic re-reads itself when its files change (plan_2026_10_11_\n",
     "        # The schematic re-reads itself when its files change (plan_2026_10_11_  # control\n",
     "survive", GUARDS, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
