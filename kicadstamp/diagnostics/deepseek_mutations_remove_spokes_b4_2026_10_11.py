# kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b4_2026_10_11.py
"""Acceptance mutations for Б4 of
techdocs/handoff/deepseek/plan/plan_2026_10_08_remove_spokes.md (Д1: a config
file that still carries `chains:` / the legacy `rules:` is REFUSED while its text
is parsed, and nothing rewrites such a file), DeepSeek, 2026-10-11.

Derived from claude_mutations_accept_spokes_ch1_2026_10_09.py (rule 38), whose
machinery is deepseek_mutations_spokes_ch1_2026_10_08.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the
`original.count(old) != 1` refusal (a non-unique template is a MISS, not a kill),
ПРОМАХ when nothing goes red, and a control that MUST survive.

WHAT IS BEING PROVEN (kicadstamp/config/retired_sections.py — the ONE point the
loader, the writer, the on-disk lift, the GUI and the CLI all pass through):

  * M1 the refusal is cut (the function never fires) — a file with `chains:` is
       read and the next write drops the section without a word
  * M2 an EMPTY section is refused too — the documented "accepted, dropped by the
       next write" behaviour breaks
  * M3 the parse stops naming the file — the refusal cannot say WHICH file of the
       include graph carries the section
  * K1 a cosmetic comment in the refusal TABLE -> MUST survive

The guard set is tests/config/test_retired_sections.py (the Д1 axis), plus the
cells that pin the surrounding contracts: the on-disk lift, the s-expr reader and
the include graph.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b4_2026_10_11.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

RETIRED = "kicadstamp/config/retired_sections.py"
INCLUDES = "kicadstamp/config/includes.py"

GUARDS = ["test_retired_sections.py", "test_config_format_upgrade_on_disk.py",
          "test_sexp_config_loading.py", "test_config_includes.py"]

MUTATIONS = [
    # 1 — the refusal never fires: `(chains …)` is not a config section any more,
    # so it falls through to the free-form branch and the next write of that file
    # drops the section silently (the data loss Д1 exists to prevent).
    ("M1 the refusal is cut", RETIRED,
     "    label = RETIRED_SECTIONS.get(key)\n"
     "    if label is None:\n"
     "        return False\n",
     "    label = None\n"
     "    if True:  # MUTATION: no refusal\n"
     "        return False\n",
     "die", GUARDS, ()),
    # 2 — an EMPTY section is refused as well: the documented counterpart (an
    # empty section is accepted and simply does not survive into the dict) breaks,
    # so every file that ever carried an empty `(chains)` becomes unreadable.
    ("M2 an empty section is refused", RETIRED,
     "    if not _section_node_is_empty(node):\n",
     "    if True:  # MUTATION: an empty section is refused too\n",
     "die", GUARDS, ()),
    # 3 — the parse no longer names the file: the refusal still fires, but a graph
    # of includes cannot tell the human WHICH file carries the section.
    ("M3 the refusal loses the file path", INCLUDES,
     "                sexp_to_dict(f.read(), path=str(path)) or {}, str(path))",
     "                sexp_to_dict(f.read()) or {}, str(path))  # MUTATION\n",
     "die", GUARDS, ()),
    # 4 — the DICT entry is cut: the .json reader (which never goes through
    # sexp_to_dict) hands the section on, and the next write of that file drops
    # it. The s-expr path still refuses via the node entry — the two entries
    # cover DIFFERENT readers, so this one dies on the JSON cell.
    ("M4 the dict entry is cut", RETIRED,
     "    for key, label in RETIRED_SECTIONS.items():\n"
     "        if key not in data:\n"
     "            continue\n",
     "    if True:  # MUTATION: the dict entry never fires\n"
     "        return\n"
     "    for key, label in RETIRED_SECTIONS.items():\n"
     "        if key not in data:\n"
     "            continue\n",
     "die", GUARDS, ()),
    # K1 — a cosmetic comment in the refusal TABLE: MUST survive.
    ("K1 a cosmetic comment in the table", RETIRED,
     'RETIRED_SECTIONS: dict[str, str] = {\n    "chains": "chains",',
     'RETIRED_SECTIONS: dict[str, str] = {  # control\n    "chains": "chains",',
     "survive", GUARDS, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
