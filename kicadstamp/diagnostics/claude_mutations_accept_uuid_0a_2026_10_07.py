# kicadstamp/diagnostics/claude_mutations_accept_uuid_0a_2026_10_07.py
"""Claude's own acceptance rows for the 0а rework inside f9de56c8
(plan_2026_10_05_uuid_tails: Save after a rename edits the record in place),
2026-10-07. Reuses deepseek_mutations_accept_uuid_0a_2026_10_07.py's guards:

  * H1 an entry WITHOUT a uuid no longer replaces by name (appends instead)
  * H2 an entry without a uuid over a uuid-carrying record is refused
  * H3 the clone Save drops the remembered uuid
  * H4 the coordinate Save drops the remembered uuid
  * K2 cosmetic comment -> MUST survive

    KICADSTAMP_ACCEPT_ROOT=<tree> .venv/bin/python \
        kicadstamp/diagnostics/claude_mutations_accept_uuid_0a_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_accept_uuid_0a_2026_10_07 as ds

W = "kicadstamp/config_writer.py"
P = "gui/docks/placer.py"
G = ds.G

ROWS = [
    ("H1 no-uuid entry no longer replaces by name", W,
     "    elif by_identity is not None:\n        items[by_identity] = entry\n",
     "    elif False:  # MUTATION\n        items[by_identity] = entry\n",
     "die", G, ()),
    ("H2 no-uuid entry over a uuid record refused", W,
     "            if uuid and existing_entry.get(\"uuid\"):\n                taken = i\n",
     "            if existing_entry.get(\"uuid\"):  # MUTATION\n                taken = i\n",
     "die", G, ()),
    ("H3 clone Save drops the uuid", P,
     "            return\n        if identity_uuid:\n            entry[\"uuid\"] = identity_uuid\n",
     "            return\n        if False:  # MUTATION\n            entry[\"uuid\"] = identity_uuid\n",
     "die", G, ()),
    ("H4 coordinate Save drops the uuid", P,
     "        if self.is_coordinate:\n            if identity_uuid:\n",
     "        if self.is_coordinate:\n            if False:  # MUTATION\n",
     "die", G, ()),
    ("K2 cosmetic comment (control)", W,
     "    taken = None            # a name-matched record of ANOTHER uuid (conflict)\n",
     "    taken = None            # a name-matched record of ANOTHER uuid (conflict) # control\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
