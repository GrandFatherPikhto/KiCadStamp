# kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b3b_2026_10_12.py
"""Acceptance mutations for Б5б-3b of
techdocs/handoff/deepseek/plan/plan_2026_10_08_remove_spokes.md — the dead
spoke-side via/track resolvers (``spoke_layout._resolve_via`` /
``_resolve_track`` / ``_net_from_role_resolved``) and the ``spoke_pad`` param
rename, DeepSeek, 2026-10-12.

Derived from deepseek_mutations_remove_spokes_b5b_2026_10_11.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing goes
red, and a control that MUST survive.

WHAT IS BEING PROVEN (rule of the run — «нет носителя» only after a mutation).
The removed spoke-side resolvers carried two properties: "a net_from_role via/
track takes its net from the pre-resolved map" and "net is not defaulted from a
rule net". Their LIVE copy is the clone path in clone_geometry.py —
``_net_from_resolved`` / ``_resolve_clone_via`` / ``_resolve_clone_track``, which
is what ClonePositionCalculator actually drives. This rig mutates THAT live copy
so the removal is not a silent loss of a guard:

  * M1  ``_net_from_resolved`` returns a constant  -> TestNetFromRoleInGeometry dies
  * M2  ``_resolve_clone_track`` ignores net_from_role -> the track cell dies
  * K1  a cosmetic comment                          -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b3b_2026_10_12.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

CLONE_GEOMETRY = "kicadstamp/geometry/clone_geometry.py"

GUARDS = ["test_clone_geometry.py"]

MUTATIONS = [
    # 1 — the pre-resolved map lookup returns a wrong net: every net_from_role
    # via/track cell of TestNetFromRoleInGeometry must go red.
    ("M1 net_from_role resolved map ignored", CLONE_GEOMETRY,
     "        return resolved_role_nets[(role, pad)]\n",
     "        return \"MUTATION\"  # MUTATION\n",
     "die", GUARDS, ()),
    # 2 — a net_from_role track treated as an ordinary net: the track cell must
    # go red (the branch is what the removed spoke _resolve_track duplicated).
    ("M2 clone track ignores net_from_role", CLONE_GEOMETRY,
     "    if track.net_from_role is not None:\n",
     "    if False:  # MUTATION\n",
     "die", GUARDS, ()),
    # K1 — a cosmetic comment edit in the mutated file: MUST survive.
    ("K1 a cosmetic comment", CLONE_GEOMETRY,
     "def _net_from_resolved(role: str, pad: str | None, resolved_role_nets: dict,\n",
     "def _net_from_resolved(role: str, pad: str | None, resolved_role_nets: dict,  # control\n",
     "survive", GUARDS, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
