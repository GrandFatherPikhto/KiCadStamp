# kicadstamp/diagnostics/deepseek_mutations_narrowing_cost_d_2026_10_08.py
"""Acceptance mutations for plan_2026_10_08_narrowing_net_traces_cost.md, part Д
(ONE action = ONE config read: «upgrade on disk skipped» reports once per dirty
epoch, and the graph is rebuilt at most once per update after a write),
DeepSeek, 2026-10-08.

Grown from deepseek_mutations_narrowing_cost_2026_10_08.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc` for the mutated file, the
`original.count(old) != 1` refusal (a non-unique template is a MISS, not a kill),
a verdict of «ПРОМАХ» when nothing red came back, and a control that MUST survive.

WHAT IS BEING PROVEN (the Д3′ cells live in
tests/gui/test_dock_hub_graph_changed_reads.py, cell С7; the Д2′ line cell in
tests/config/test_config_format_upgrade_on_disk.py):
  * M1  stage_write stops dropping the graph cache -> the staged edit is invisible
        to the next read (the FRESHNESS cell of Д3′);
  * M2  the «skipped» line goes back to INFO on EVERY call -> the ≤1-per-action
        property (Д2′/Д3′) fails, the live 32 lines return;
  * K1  a cosmetic comment change -> MUST survive.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_narrowing_cost_d_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

# The cell files (basenames resolved under tests/ by the rig).
UPDATES = ["test_dock_hub_graph_changed_reads.py"]
SKIPROW = ["test_dock_hub_graph_changed_reads.py",
           "test_config_format_upgrade_on_disk.py"]

WS = "kicadstamp/config_working_set.py"
UOD = "kicadstamp/config/upgrade_on_disk.py"

MUTATIONS = [
    # 1 — stage_write stops dropping the graph cache (Д3′ freshness): the next read
    # hands back the PRE-edit Config — the stale-tree defect of 04.09.
    ("M1 stage_write stops dropping the graph cache", WS,
     "        self._staged[resolved] = copy.deepcopy(data)\n"
     "        invalidate_graph_path(path)\n",
     "        self._staged[resolved] = copy.deepcopy(data)  # MUTATION: no drop\n",
     "die", UPDATES, ()),
    # 2 — the «skipped» line is INFO on EVERY call again (Д2′): the live 32 lines
    # per «Update from selection» come straight back.
    ("M2 the skipped line is INFO on every call", UOD,
     '        if WORKING_SET.note_skip_report("format"):\n',
     "        if True:  # MUTATION\n",
     "die", SKIPROW, ()),
    # K1 — a cosmetic comment change changes nothing: MUST survive.
    ("K1 a cosmetic comment change", WS,
     '        # A new dirty epoch: the "skipped" line must be reportable again.',
     '        # A new dirty epoch: the "skipped" line must be reportable AGAIN.',
     "survive", UPDATES, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
