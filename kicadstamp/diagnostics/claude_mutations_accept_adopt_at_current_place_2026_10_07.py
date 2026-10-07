# kicadstamp/diagnostics/claude_mutations_accept_adopt_at_current_place_2026_10_07.py
"""Claude's own acceptance rows for 23850521 (plan_2026_10_07_adopt_at_current_place,
step 1: adopt cell copper at its current place before the redraw), 2026-10-07.

The Demon's rig mutates only the pass itself. These rows mutate its WIRING and
the stale-key rebind — the two-machine case that motivated the task:

  * N1 _execute never calls the pass (the redraw is as before: a trail)
  * N2 the dry run never calls the pass ("would adopt N" lost)
  * N3 a STALE key (uuid gone from the board) is never rebound (two machines)
  * R3 an UNRESOLVABLE net_from_role is adopted anyway (chain guard bypassed)
  * K2 cosmetic comment -> MUST survive

Guards widened beyond the pass's own cell file to the whole tests/ tree for N1/N2
(an end-to-end cell, if any, lives outside test_adopt_at_current_place.py).

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_adopt_at_current_place_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_adopt_at_current_place_2026_10_07 as ds

ADOPT = ds.ADOPT
PIPE = "kicadstamp/apply_pipeline.py"
PRUNE = "kicadstamp/absent_copper_prune.py"
CALC = "kicadstamp/placement/services/clone_position_calculator.py"
G = ds.G
# N1/N2 mutate the WIRING, so their guards include the pipeline cell file (1а):
# the module file alone cannot see a deleted call.
PIPE_G = ds.G + ["test_adopt_at_current_place_pipeline.py"]

ROWS = [
    ("N1 _execute never calls the pass", PIPE,
     "        adopt_cell_copper_at_current_place(\n"
     "            self.adapter, self.cfg, self.items, registry, track_registry, write=True,\n"
     "            sheet_names=self.sheet_names, position_overrides=self.position_overrides)",
     "        pass  # MUTATION",
     "die", PIPE_G, ()),
    ("N2 the dry run never calls the pass", PIPE,
     "        adoption = adopt_cell_copper_at_current_place(\n"
     "            self.adapter, self.cfg, self.items, registry, track_registry,\n"
     "            write=False, sheet_names=self.sheet_names,\n"
     "            position_overrides=self.position_overrides)",
     "        from .adopt_at_current_place import AdoptionReport as _AR\n"
     "        adoption = _AR()  # MUTATION",
     "die", PIPE_G, ()),
    ("N3 a stale key is never rebound", ADOPT,
     '            if entry is not None and getattr(entry, "uuid", None) in live_uuid[kind]:',
     "            if entry is not None:  # MUTATION",
     "die", G, ()),
    # ── 1б (d33d2453): what the Demon's M9-M11 leave unguarded ─────────────
    ("R1 refs of a role map are ROLES again", PRUNE,
     "        (own_refs.values() if isinstance(own_refs, Mapping) else own_refs)",
     "        own_refs  # MUTATION",
     "die", G, ()),
    ("R2 role_refs_of ignores the override", CALC,
     "        if position_override:\n"
     "            # The planner's own convention (compute_raw_positions)",
     "        if False:  # MUTATION\n"
     "            # The planner's own convention (compute_raw_positions)",
     "die", G, ()),
    ("R3 an unresolved net_from_role is adopted", ADOPT,
     "        except ValidationError:\n            return True, None",
     "        except ValidationError:\n            return False, None  # MUTATION",
     "die", G, ()),
    ("K2 cosmetic comment (control)", ADOPT,
     "    key_to_item: dict = {}          # (kind, key) -> live_item\n",
     "    key_to_item: dict = {}          # (kind, key) -> live_item  # control\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
