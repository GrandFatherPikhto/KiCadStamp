# kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b3c_2026_10_12.py
"""Acceptance mutations for Б5б-3c of
techdocs/handoff/deepseek/plan/plan_2026_10_08_remove_spokes.md — the removal of
the `--rule-net` / `rule_nets` extraction subsystem (CLI flag, profile key,
passthrough, the `both --rule-net and --param` fatal, the via/track net:null
writing; the format's typed free-field hint it motivated), DeepSeek, 2026-10-12.

Derived from deepseek_mutations_remove_spokes_b5b_2026_10_11.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing goes
red, and a control that MUST survive.

WHAT IS BEING PROVEN (rule of the run — «нет носителя» only after a mutation).
Removing the flag removed a way to write `net: null` for a via/track. That
"null" is not merely unused now — it is POISON: a ClonePlacement via/track with
net=None is FATAL at apply (clone_geometry.py). The surviving cell is
TestNetFromRoleAutoSuggest::test_via_on_a_builtin_rule_net_keeps_the_literal: a
GND via must come out as the LITERAL, never null, and without net_from_role.

  * M1  the extract via loop nulls a built-in rule net again  -> the cell dies
  * M2  extract classifies with an EMPTY rule set (not ROLELESS_NETS) -> GND maps to
        a role's pad via the multi-net path, so the cell dies
  * K1  a cosmetic comment                                    -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b3c_2026_10_12.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

EXTRACTION = "kicadstamp/template_extraction.py"

GUARDS = ["test_template_extraction.py"]

MUTATIONS = [
    # 1 — reintroduce the --rule-net "write null" behaviour driven by the
    # built-in rule set: a GND via becomes net: null and the cell goes red.
    ("M1 extract nulls a builtin rule net again", EXTRACTION,
     "        via_net = v.net_name\n"
     "        role_net = role_net_pad = None\n"
     "        if via_net is not None:\n",
     "        via_net = v.net_name\n"
     "        role_net = role_net_pad = None\n"
     "        if via_net in ROLELESS_NETS:  # MUTATION\n"
     "            via_net = None\n"
     "        elif via_net is not None:\n",
     "die", GUARDS, ()),
    # 2 — the extract classifier is handed an EMPTY rule set instead of the
    # built-in ROLELESS_NETS: GND stops being "no role needed" and resolves to the
    # cap role's pad (multi-net path), so the cell's literal assertion dies.
    ("M2 extract classifies with an empty rule set", EXTRACTION,
     "            role_net, role_net_pad = _suggest_net_from_role(\n"
     "                selection_role_nets, via_net, ROLELESS_NETS,\n"
     "                [(along_mm, across_mm)], components)\n",
     "            role_net, role_net_pad = _suggest_net_from_role(\n"
     "                selection_role_nets, via_net, set(),  # MUTATION\n"
     "                [(along_mm, across_mm)], components)\n",
     "die", GUARDS, ()),
    # K1 — a cosmetic comment edit in the mutated file: MUST survive.
    ("K1 a cosmetic comment", EXTRACTION,
     "    net_template_role = net_template_role or {}\n",
     "    net_template_role = net_template_role or {}  # control\n",
     "survive", GUARDS, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
