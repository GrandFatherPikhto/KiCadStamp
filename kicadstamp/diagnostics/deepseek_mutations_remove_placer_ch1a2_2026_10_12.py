# kicadstamp/diagnostics/deepseek_mutations_remove_placer_ch1a2_2026_10_12.py
"""Acceptance mutations for Ч1а-2 of
techdocs/handoff/deepseek/plan/plan_2026_10_08_remove_placer.md — removing the
`clone-plan` command (plan_clone_placements / clone_placements_to_dict) while
KEEPING `cloner/plan.py::verify_channel_net_mapping`, the one function a live
module still uses. DeepSeek, 2026-10-12.

Derived from deepseek_mutations_remove_spokes_b5b3c_2026_10_12.py (rule 38),
whose machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME
guards: basename-resolved tests under tests/, `_drop_pyc`, the
`original.count(old) != 1` refusal (a non-unique template is a MISS, not a kill),
ПРОМАХ when nothing goes red, and a control that MUST survive.

WHAT IS BEING PROVEN (rule of the run — «нет носителя» only after a mutation).

  * The verifier is LIVE: `channel_copy.py` (Clone Sheets) calls it, and the
    kept cells in test_cloner_plan.py guard its diagnostic output. M1 guts it to
    always return [] -> the non-isomorphic cell dies.
  * `verify_channel_net_mapping` is the ONLY live face of plan.py: M2 makes
    channel_copy import the removed `plan_clone_placements` instead -> ImportError
    in the live consumer, so its guard goes red. (Nothing else imports plan.py.)
  * K1  a cosmetic comment in plan.py -> MUST survive.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_remove_placer_ch1a2_2026_10_12.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

PLAN = "kicadstamp/cloner/plan.py"
CHANNEL_COPY = "kicadstamp/channel_copy.py"

PLAN_GUARDS = ["test_cloner_plan.py"]
COPY_GUARDS = ["test_channel_copy.py"]

MUTATIONS = [
    # 1 — the verifier stops verifying (always clean): the non-isomorphic cell
    # in test_cloner_plan.py must go red.
    ("M1 verify_channel_net_mapping always returns []", PLAN,
     "    diagnostics: list[str] = []\n"
     "    if not source_role_nets or not target_role_nets:\n"
     "        return diagnostics\n",
     "    return []  # MUTATION\n"
     "    diagnostics: list[str] = []\n"
     "    if not source_role_nets or not target_role_nets:\n"
     "        return diagnostics\n",
     "die", PLAN_GUARDS, ()),
    # 2 — the live consumer STOPS calling the verifier: channel_copy's own guard
    # (test_mismatched_channel_reports_diagnostic) goes red. Proves the verifier
    # is a live face of plan.py that channel_copy actually uses.
    ("M2 channel_copy drops the live verifier call", CHANNEL_COPY,
     "    return verify_channel_net_mapping(src_role_nets, dst_role_nets,\n"
     "                                      src_channel, dst_channel)\n",
     "    return []  # MUTATION: the live verifier call is dropped\n",
     "die", COPY_GUARDS, ()),
    # K1 — a cosmetic comment edit: MUST survive.
    ("K1 a cosmetic comment in plan.py", PLAN,
     "from ..net_matching import Graph, match_template_to_target\n",
     "from ..net_matching import Graph, match_template_to_target  # control\n",
     "survive", PLAN_GUARDS, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
