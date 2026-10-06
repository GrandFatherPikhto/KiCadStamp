# kicadstamp/diagnostics/deepseek_mutations_prune_absent_2026_10_06.py
"""Demon's acceptance rows for С-1 — "Update from selection" is STRICTLY the
selection (plan_2026_10_06_prune_absent_cell_copper.md; Denis + Claude,
2026-10-06).

The soft Н4 п.5 rule of 2026-10-05 (a record whose copper is still on the board
is kept and named) is WITHDRAWN: a cell record with no pair in the (narrowed)
selection is DELETED on EVERY path of Refresh; Import stays additive. The rows
below map to the plan's own С-1 mutation list:

  * M1 strictness lost on the BY-CLUSTER path    -> the cluster read refuses
  * M2 the strict flag is lost EVERYWHERE        -> the clean path refuses
  * M3 the "selection has no copper" line is gone -> the red warning disappears
  * M4 the strict итог line is gone              -> the Log never says what went
  * M5 Import goes through the refresh plan      -> Import starts deleting
  * C3 the "no copper" line fires with copper    -> the red line lies (Claude)
  * K1 a cosmetic comment                        -> MUST survive

Guards: tests/gui/docks/test_prune_absent_cell_copper.py (the С-1 worker cells),
tests/gui/docks/test_cell_editor_mixed_selection.py (the two doors) and
tests/gui/docks/test_cell_editor.py (the finish-handler report). Run on
deepseek_mutations_refresh_mixed_2026_10_05.py's machinery (count == 1 or
НЕДЕЙСТВИТЕЛЬНА, `_drop_pyc`, PYTHONDONTWRITEBYTECODE, `-n auto`, ПРОМАХ on zero
reds, a control that MUST survive) — grown from it, not written fresh (rule 38).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_prune_absent_2026_10_06.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

EDITOR = "gui/docks/cell_editor.py"
ABSENT = "kicadstamp/absent_copper_prune.py"
G = ["test_prune_absent_cell_copper.py", "test_cell_editor_mixed_selection.py",
     "test_cell_editor.py"]

ROWS = [
    # M1 — the strict deletion is lost on the by-cluster path: an unpaired record
    # becomes a count fatal again, so the cluster read REFUSES instead of
    # deleting (the soft-mode parameter itself is gone — С-1).
    ("M1 strictness lost on the by-cluster path", EDITOR,
     "                remove_missing=True,",
     "                remove_missing=prelude is None,  # MUTATION",
     "die", G, ()),
    # M2 — the strict flag is lost on EVERY path: an unpaired record is a count
    # fatal again, so the clean read refuses instead of deleting.
    ("M2 the strict flag is lost everywhere", EDITOR,
     "                remove_missing=True,",
     "                remove_missing=False,  # MUTATION",
     "die", G, ()),
    # M3 — the "selection has no copper" warning is dropped: the deletion still
    # happens but the red line never reaches the Log.
    ("M3 the no-copper warning is gone", EDITOR,
     "            if not read_copper and removed_copper:",
     "            if False:  # MUTATION",
     "die", G, ()),
    # M4 — the strict итог line is dropped: the per-record lines still stand, but
    # the Log never summarises WHY they went.
    ("M4 the strict summary line is gone", EDITOR,
     "        if removed_copper:\n"
     "            self._show_message(\n"
     "                _(\"removed {count} record(s) not in the selection\").format(\n"
     "                    count=removed_copper),\n"
     "                _WARN_STYLE)",
     "        if False:  # MUTATION\n"
     "            self._show_message(\n"
     "                _(\"removed {count} record(s) not in the selection\").format(\n"
     "                    count=removed_copper),\n"
     "                _WARN_STYLE)",
     "die", G, ()),
    # M5 — the IMPORT door is routed through the refresh plan: Import would
    # DELETE the records with no pair instead of only adding.
    ("M5 Import goes through the refresh plan", EDITOR,
     "            plan = build_import_plan(\n"
     "                payload[\"components\"], payload[\"vias\"], payload[\"tracks\"],\n"
     "                plan_footprints, plan_vias, plan_tracks, adapter,\n"
     "                origin_role=payload.get(\"origin_role\"),\n"
     "                cell_layer=payload.get(\"cell_layer\"),\n"
     "                reconcile_components=prelude is not None)",
     "            plan = build_refresh_plan(  # MUTATION: Import deletes too\n"
     "                payload[\"components\"], payload[\"vias\"], payload[\"tracks\"],\n"
     "                plan_footprints, plan_vias, plan_tracks, adapter,\n"
     "                origin_role=payload.get(\"origin_role\"),\n"
     "                add_new_copper=True,\n"
     "                remove_missing=True,\n"
     "                cell_layer=payload.get(\"cell_layer\"),\n"
     "                reconcile_components=prelude is not None)",
     "die", G, ()),
    # C3 (Claude's С-1 acceptance finding): the red "no copper" line fires even
    # when the selection HOLDS copper. The worker cells carry the NEGATIVE assert
    # for it — that is the only thing that catches this.
    ("C3 the no-copper line fires with copper present", EDITOR,
     "            if not read_copper and removed_copper:",
     "            if removed_copper:  # MUTATION",
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
