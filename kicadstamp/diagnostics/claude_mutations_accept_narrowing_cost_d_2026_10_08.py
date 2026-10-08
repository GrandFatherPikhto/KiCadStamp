# kicadstamp/diagnostics/claude_mutations_accept_narrowing_cost_d_2026_10_08.py
"""Claude's acceptance rows for PART Д of plan_2026_10_08_narrowing_net_traces_cost
(commits 30c28931 … 0d46f070) — on top of Demon's rig machinery.

  * C1 clear() no longer re-arms the "skipped" line — after Save/Discard the next
       dirty epoch stays silent at INFO
  * C2 the registry-schema side ignores the epoch — INFO on every call again
  * C3 every stage_write re-arms the line — one INFO per edit, not per epoch
  * K1 a cosmetic comment — MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_narrowing_cost_d_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_narrowing_cost_d_2026_10_08 as ds

REG = "kicadstamp/config/registry_upgrade.py"
T = ["test_dock_hub_graph_changed_reads.py", "test_config_format_upgrade_on_disk.py",
     "test_config_working_set.py", "test_registry_upgrade_on_disk.py"]

ROWS = [
    ("C1 clear() does not re-arm the skipped line", ds.WS,
     "        self._skip_reported.clear()\n",
     "        pass  # MUTATION\n",
     "die", T, ()),
    ("C2 the registry side is INFO on every call", REG,
     '        if WORKING_SET.note_skip_report("registry schema"):\n',
     "        if True:  # MUTATION\n",
     "die", T, ()),
    ("C3 every stage_write re-arms the line", ds.WS,
     "        self._staged[resolved] = copy.deepcopy(data)\n"
     "        invalidate_graph_path(path)\n",
     "        self._staged[resolved] = copy.deepcopy(data)\n"
     "        invalidate_graph_path(path)\n"
     "        self._skip_reported.clear()  # MUTATION\n",
     "die", T, ()),
    ("K1 cosmetic comment (control)", REG,
     "        # Д2′: the SAME text, but INFO only on the FIRST report of THIS kind in\n",
     "        # Д2′: the SAME text, but INFO only on the FIRST report of THIS kind in  (control)\n",
     "survive", T, ()),
]


if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
