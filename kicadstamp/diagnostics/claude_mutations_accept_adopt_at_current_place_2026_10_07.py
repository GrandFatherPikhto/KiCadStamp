# kicadstamp/diagnostics/claude_mutations_accept_adopt_at_current_place_2026_10_07.py
"""Claude's own acceptance rows for 23850521 (plan_2026_10_07_adopt_at_current_place,
step 1: adopt cell copper at its current place before the redraw), 2026-10-07.

The Demon's rig mutates only the pass itself. These rows mutate its WIRING and
the stale-key rebind — the two-machine case that motivated the task:

  * N1 _execute never calls the pass (the redraw is as before: a trail)
  * N2 the dry run never calls the pass ("would adopt N" lost)
  * N3 a STALE key (uuid gone from the board) is never rebound (two machines)
  * K2 cosmetic comment -> MUST survive

Guards widened beyond the pass's own cell file to the whole tests/ tree for N1/N2
(an end-to-end cell, if any, lives outside test_adopt_at_current_place.py).

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_adopt_at_current_place_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_adopt_at_current_place_2026_10_07 as ds

ADOPT = ds.ADOPT
PIPE = "kicadstamp/apply_pipeline.py"
G = ds.G

ROWS = [
    ("N1 _execute never calls the pass", PIPE,
     "        adopt_cell_copper_at_current_place(\n"
     "            self.adapter, self.cfg, self.items, registry, track_registry, write=True)",
     "        pass  # MUTATION",
     "die", G, ()),
    ("N2 the dry run never calls the pass", PIPE,
     "        adoption = adopt_cell_copper_at_current_place(\n"
     "            self.adapter, self.cfg, self.items, registry, track_registry,\n"
     "            write=False)",
     "        from .adopt_at_current_place import AdoptionReport as _AR\n"
     "        adoption = _AR()  # MUTATION",
     "die", G, ()),
    ("N3 a stale key is never rebound", ADOPT,
     '            if entry is not None and getattr(entry, "uuid", None) in live_uuid[kind]:',
     "            if entry is not None:  # MUTATION",
     "die", G, ()),
    ("K2 cosmetic comment (control)", ADOPT,
     "    key_to_item: dict = {}          # (kind, key) -> live_item\n",
     "    key_to_item: dict = {}          # (kind, key) -> live_item  # control\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
