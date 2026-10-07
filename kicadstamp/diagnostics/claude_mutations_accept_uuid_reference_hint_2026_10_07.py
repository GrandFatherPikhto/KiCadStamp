# kicadstamp/diagnostics/claude_mutations_accept_uuid_reference_hint_2026_10_07.py
"""Claude's own acceptance rows for 6fb179ad (plan_2026_10_05_uuid_tails part 1:
the format-3 dangling-reference refusal suggests the close name), 2026-10-07.

Reuses the machinery of deepseek_mutations_uuid_reference_hint_2026_10_07.py:

  * F1 the target section's names are never collected -> no suggestion
  * F2 the suggestion has no cutoff (always suggests something)
  (F3 "the format-2 loader refusal loses its suggestion" was dropped after
  4ddc1e0f removed that unreachable branch — its template no longer exists)
  * K2 cosmetic comment -> MUST survive

    KICADSTAMP_ACCEPT_ROOT=<tree> .venv/bin/python \
        kicadstamp/diagnostics/claude_mutations_accept_uuid_reference_hint_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_uuid_reference_hint_2026_10_07 as ds

G = ds.G

ROWS = [
    ("F1 target names never collected", "kicadstamp/config/format3.py",
     "            names_by_section.setdefault(section, set()).add(name)\n",
     "            pass  # MUTATION\n",
     "die", G, ()),
    ("F2 suggestion without a cutoff", "kicadstamp/config/name_hint.py",
     "sorted(known_names or ()), n=1)",
     "sorted(known_names or ()), n=1, cutoff=0.0)",
     "die", G, ()),
    ("K2 cosmetic comment (control)", "kicadstamp/config/name_hint.py",
     "__all__ = [\"close_name_hint\"]\n",
     "__all__ = [\"close_name_hint\"]  # control\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
