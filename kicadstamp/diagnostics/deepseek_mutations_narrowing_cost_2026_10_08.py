# kicadstamp/diagnostics/deepseek_mutations_narrowing_cost_2026_10_08.py
"""Acceptance mutations for plan_2026_10_08_narrowing_net_traces_cost.md, parts
А–Г (the read is cheap, says what it took, Select cell answers about the current
place, a staged write says it is staged), DeepSeek, 2026-10-08.

Grown from deepseek_mutations_entities_part3_2026_10_08.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing turns
red, and a control that MUST survive.

WHAT IS BEING PROVEN (the cells live in tests/net/, tests/gui/ and
tests/gui/docks/):
  * test_net_trace_single_board_read.py — ONE board read, ONE walk per narrowing;
  * test_net_trace_net_filter.py        — a record of another net is not planned;
  * test_select_cell_copper.py          — the pair away from the plan IS selected,
                                          and the current-place copper is taken;
  * test_adopt_at_current_place_pipeline.py — the dry run KEEPS its report;
  * test_config_writer.py / test_chain_dock.py — the staged wording;
  * test_cell_editor_mixed_selection.py — the selection becomes the read;
  * test_read_outcome.py                — the line and its reasons.

  * M1  the board is read inside the matcher again (А1)
  * M2  the net filter is removed (А2)
  * M3  the filter drops a record of the SELECTED net too (А2)
  * M4  the subtraction half re-walks the records (А1, the cost comes back)
  * M5  Select cell keeps the strict place check (Б1)
  * M6  the current-place copper is not taken any more (Б2)
  * M7  the dry run does not keep its adoption report (Б1/Б2 wiring)
  * M8  a staged write claims the file again (В)
  * M9  the read does not replace the selection (Г)
  * M10 the read line forgets the skip reasons (Г)
  * K1  a cosmetic docstring change -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_narrowing_cost_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

ONE_READ = ["test_net_trace_single_board_read.py"]
NET_FILTER = ["test_net_trace_net_filter.py"]
SELECT = ["test_select_cell_copper.py"]
PIPELINE = ["test_adopt_at_current_place_pipeline.py"]
STAGED = ["test_config_writer.py", "test_chain_dock.py"]
MIXED = ["test_cell_editor_mixed_selection.py"]
OUTCOME = ["test_read_outcome.py"]

# The modules (basenames resolved under tests/ by the rig).
NTP = "kicadstamp/net_trace_planner.py"
SN = "kicadstamp/selection_narrowing.py"
MIXEDSEL = "gui/mixed_selection.py"
SELECTCELL = "gui/select_cell.py"
PIPE = "kicadstamp/apply_pipeline.py"
# доделка: the В rule moved to its own module (config_writer re-exports it), so
# M8 follows the RULE, not the file that happens to re-export it.
WRITER = "kicadstamp/config_stage_report.py"
OUT = "gui/read_outcome.py"

MUTATIONS = [
    # 1 — the matcher reads the board itself again (А1): the given read is thrown
    # away, and a narrowing with K records reads the whole board K times.
    ("M1 the matcher reads the board again", NTP,
     "    live = read_live_copper(adapter) if live is None else live\n",
     "    live = read_live_copper(adapter)  # MUTATION\n",
     "die", ONE_READ, ()),
    # 2 — the net filter is gone (А2): every record of the project is planned for
    # a one-cell read again (the live 73-records × one read shape).
    ("M2 the net filter is removed", SN,
     "        if filterable:\n"
     "            # quiet=True (доделка 2 of plan_2026_10_08_narrowing_net_traces_cost):\n"
     "            # this question is asked of EVERY record for ONE read, and the role\n"
     "            # search's `role_narrowing` lines were 2 184 of the live Log for one\n"
     "            # click. DEBUG here; the redraw keeps them at INFO.\n"
     "            nets, resolvable = record_nets(adapter, nt, sheet_names, quiet=True)\n"
     "            if resolvable and nets and not (nets & wanted):\n"
     "                skipped += 1\n"
     "                continue\n",
     "        if False:  # MUTATION: no net filter\n"
     "            nets, resolvable = record_nets(adapter, nt, sheet_names, quiet=True)\n"
     "            if resolvable and nets and not (nets & wanted):\n"
     "                skipped += 1\n"
     "                continue\n",
     "die", NET_FILTER, ()),
    # 3 — the filter is too eager: a record of the SELECTED net is dropped too, so
    # copper that IS the selection's own is no longer subtracted/transferred.
    ("M3 the filter drops the own net too", SN,
     "            if resolvable and nets and not (nets & wanted):\n"
     "                skipped += 1\n"
     "                continue\n",
     "            if resolvable and nets:  # MUTATION\n"
     "                skipped += 1\n"
     "                continue\n",
     "die", NET_FILTER, ()),
    # 4 — the subtraction half stops taking the ready map (А1): every list walks
    # the records (and reads the board) on its own again.
    ("M4 the subtraction half re-walks", MIXEDSEL,
     "        net_v = subtract_net_trace_copper(\n"
     "            list(sub_v.kept), net_traces, adapter, via_entries=via_entries,\n"
     "            track_entries=track_entries, sheet_names=sheet_names,\n"
     "            owned=owned, notes=net_notes)\n"
     "        net_t = subtract_net_trace_copper(\n"
     "            list(sub_t.kept), net_traces, adapter, via_entries=via_entries,\n"
     "            track_entries=track_entries, sheet_names=sheet_names,\n"
     "            owned=owned, notes=net_notes)\n",
     "        net_v = subtract_net_trace_copper(  # MUTATION\n"
     "            list(sub_v.kept), net_traces, adapter, via_entries=via_entries,\n"
     "            track_entries=track_entries, sheet_names=sheet_names)\n"
     "        net_t = subtract_net_trace_copper(  # MUTATION\n"
     "            list(sub_t.kept), net_traces, adapter, via_entries=via_entries,\n"
     "            track_entries=track_entries, sheet_names=sheet_names)\n",
     "die", ONE_READ, ()),
    # 5 — Select cell keeps the strict place check (Б1): the live 08.10 defect —
    # 0 copper selected while the board carries it.
    ("M5 Select cell keeps the strict place check", SELECTCELL,
     "        sheet_names=sheet_names, own_refs=own_refs, place_check=False)\n",
     "        sheet_names=sheet_names, own_refs=own_refs)  # MUTATION\n",
     "die", SELECT, ()),
    # 6 — the current-place copper is not taken (Б2): the answer the planned-place
    # tiers cannot give after a read that was never redrawn.
    ("M6 the current-place copper is not taken", SELECTCELL,
     "    for _kind, _key, item in at_current_place or ():\n",
     "    for _kind, _key, item in ():  # MUTATION\n",
     "die", SELECT, ()),
    # 7 — the dry run throws its adoption report away (the seam of Б): nothing to
    # hand over, so the consumer falls back to the PLANNED place.
    ("M7 the dry run keeps no report", PIPE,
     "        self.at_current_place, adoption_lines = dry_run_adoption(\n"
     "            self.adapter, self.cfg, self.items, registry, track_registry,\n"
     "            sheet_names=self.sheet_names, position_overrides=self.position_overrides)\n",
     "        _dropped, adoption_lines = dry_run_adoption(\n"
     "            self.adapter, self.cfg, self.items, registry, track_registry,\n"
     "            sheet_names=self.sheet_names, position_overrides=self.position_overrides)\n"
     "        self.at_current_place = None  # MUTATION\n",
     "die", PIPELINE, ()),
    # 8 — a staged write claims the file again (В): the lie Denis read at 19:40:39.
    ("M8 a staged write claims the file", WRITER,
     "    if not is_staged_write(path):\n"
     "        return physical\n",
     "    return physical  # MUTATION\n",
     "die", STAGED, ()),
    # 9 — the read no longer replaces the selection (Г): the user cannot see what
    # was read, which is the whole point of the action.
    ("M9 the read does not replace the selection", OUT,
     "    if outcome.replaced:\n",
     "    if False:  # MUTATION\n",
     "die", MIXED, ()),
    # 10 — the read line forgets WHY items were skipped (Г): the count remains,
    # the reasons vanish.
    ("M10 the line forgets the skip reasons", OUT,
     '    if skipped:\n'
     '        line += _("; skipped {count}: {reasons}").format(\n',
     '    if False:  # MUTATION\n'
     '        line += _("; skipped {count}: {reasons}").format(\n',
     "die", OUTCOME, ()),
    # K1 — a cosmetic docstring change changes nothing: MUST survive.
    ("K1 a cosmetic docstring change", OUT,
     '"""What a read TOOK out of the selection',
     '"""What a read TOOK OUT of the selection', "survive", OUTCOME, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
