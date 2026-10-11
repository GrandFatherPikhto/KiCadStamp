# kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b_2026_10_11.py
"""Acceptance mutations for Б5б of
techdocs/handoff/deepseek/plan/plan_2026_10_08_remove_spokes.md — the fix-up that
restores the snapshot-threading cell for a RECORD parent (row 4) after the chain
kind was retired, DeepSeek, 2026-10-11.

Derived from deepseek_mutations_remove_spokes_b4_2026_10_11.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing goes
red, and a control that MUST survive.

WHAT IS BEING PROVEN. After chains were retired, `tree_position.
_anchor_base_live_position`'s REF forward of `snapshot=` is INERT (a ref anchor
auto-resolves only over `_PLACEABLE_KINDS`, none of which owns a snapshot) — so
the mutation "drop that forward" is EQUIVALENT, not a lost guard (row M2 SURVIVES
on purpose, and the cell that used to guard it, the chain-based row 2b, is gone).
The LIVE guard is row 4, restored on a NET_TRACE record: its position pays one
`ComponentResolver(snapshot=…)` sweep, its rotation is inert.

  * M1  `read_record_live_pose` drops `snapshot=` on its position call — row 4
        (net_trace) sweeps again and must go red
  * M2  `_anchor_base_live_position`'s REF forward is cut — EQUIVALENT: nothing
        goes red (the REASON row 2b is not restored); MUST SURVIVE
  * K1  a cosmetic comment — MUST survive

The guard set is tests/trees/test_tree_position_snapshot_threading.py (row 4 lives
there).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b_2026_10_11.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

LIVE_POSITION = "gui/docks/live_position.py"
TREE_POSITION = "kicadstamp/tree_position.py"

GUARDS = ["test_tree_position_snapshot_threading.py"]

MUTATIONS = [
    # 1 — the record-parent position call loses the snapshot: a net_trace parent
    # sweeps the board again on its position resolve, so row 4 fails.
    ("M1 read_record_live_pose drops the snapshot", LIVE_POSITION,
     "    pos = resolve_base_live_position(adapter, cfg, ref, record, {}, sheet_names,\n"
     "                                     snapshot=snapshot)\n",
     "    pos = resolve_base_live_position(adapter, cfg, ref, record, {}, sheet_names)  # MUTATION\n",
     "die", GUARDS, ()),
    # 2 — the REF branch's snapshot forward is cut. EQUIVALENT after the chain
    # kind died: no auto-searchable kind reaches a snapshot-taking leaf, so no
    # cell can go red. This row makes the equivalence MEASURED (row 2b is gone).
    ("M2 (equivalent) REF snapshot forward cut", TREE_POSITION,
     "        pos = resolve_base_live_position(adapter, cfg, anchor.ref, record, {},\n"
     "                                         sheet_names, snapshot=snapshot)\n",
     "        pos = resolve_base_live_position(adapter, cfg, anchor.ref, record, {},\n"
     "                                         sheet_names)  # MUTATION (equivalent)\n",
     "survive", GUARDS, ()),
    # K1 — a cosmetic comment edit in the mutated file: MUST survive.
    ("K1 a cosmetic comment", LIVE_POSITION,
     "    # snapshot (Т2-4а of plan_2026_09_22_live_adapter_class): forwarded so the\n",
     "    # snapshot (Т2-4а of plan_2026_09_22_live_adapter_class): forwarded so the  # control\n",
     "survive", GUARDS, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
