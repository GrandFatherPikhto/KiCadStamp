# kicadstamp/diagnostics/claude_mutations_accept_refused_tree_2026_10_07.py
"""Claude's own acceptance rows for f3e116d0 (plan_2026_10_07_refused_tree_matching:
a refused tree's copper is matched by the registry alone), 2026-10-07.

Reuses deepseek_mutations_refused_tree_matching_2026_10_07.py's machinery/guards:

  * R1 the "not checked" count ignores the keys the registry DID have
  * R2 the instance's own refs are dropped (anchor:<ref> keys never claimed)
  * K2 cosmetic comment -> MUST survive
"""
from kicadstamp.diagnostics import deepseek_mutations_refused_tree_matching_2026_10_07 as ds

A = "kicadstamp/absent_copper_prune.py"
G = sorted(set(ds.G_CORE_SELECT + ds.G_WORKER + ds.G_REPORT))

ROWS = [
    ("R1 not-checked ignores the checked keys", A,
     "    without_registry = len(_cell_record_slots(cell) - checked)\n",
     "    without_registry = len(_cell_record_slots(cell))  # MUTATION\n",
     "die", G, ()),
    ("R2 own refs dropped", A,
     "    return footprints, cell_identity, own_addresses, chosen_address, refs\n",
     "    return footprints, cell_identity, own_addresses, chosen_address, frozenset()  # MUTATION\n",
     "die", G, ()),
    ("K2 cosmetic comment (control)", A,
     "    checked: set = set()\n",
     "    checked: set = set()  # control\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
