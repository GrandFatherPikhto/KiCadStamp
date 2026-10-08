# kicadstamp/diagnostics/claude_mutations_accept_narrowing_cost_2026_10_08.py
"""Claude's acceptance rows for parts А–Г of plan_2026_10_08_narrowing_net_traces_cost
(commits 1b45d75f … 04cafe28) — on top of Demon's rig machinery.

  * C1 a record whose net does NOT resolve is reported resolvable — the filter may
       then drop a record whose registry copper sits in the selection
  * C2 a selected piece with NO net no longer switches the net filter off
  * C3 a read that took nothing still rewrites the selection (clears it)
  * K1 a cosmetic comment — MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_narrowing_cost_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_narrowing_cost_2026_10_08 as ds

T = (ds.ONE_READ + ds.NET_FILTER + ds.SELECT + ds.PIPELINE + ds.MIXED + ds.OUTCOME
     + ["test_selection_narrowing.py"])

ROWS = [
    ("C1 unresolvable net counts as resolvable", ds.NTP,
     "        except Exception:  # noqa: BLE001 — an unresolvable chain is a normal answer\n"
     "            resolvable = False\n",
     "        except Exception:  # noqa: BLE001 — an unresolvable chain is a normal answer\n"
     "            pass  # MUTATION\n",
     "die", T, ()),
    ("C2 a net-less piece keeps the filter on", ds.SN,
     "    filterable = bool(wanted) and \"\" not in wanted\n",
     "    filterable = bool(wanted)  # MUTATION\n",
     "die", T, ()),
    ("C3 an empty read still rewrites the selection", ds.OUT,
     "    if outcome.replaced:\n        try:\n            adapter.select_items(",
     "    if True:  # MUTATION\n        try:\n            adapter.select_items(",
     "die", T, ()),
    ("K1 cosmetic comment (control)", ds.OUT,
     "def replace_selection(adapter, *, footprints, vias, raw_tracks, plan_footprints,",
     "def replace_selection(adapter, *, footprints, vias, raw_tracks, plan_footprints,  # control",
     "survive", T, ()),
]


if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
