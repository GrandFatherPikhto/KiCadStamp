# kicadstamp/diagnostics/deepseek_mutations_select_cell_split_2026_10_05.py
"""Demon's acceptance rows for «Select cell components» / «Select cell» with the
recorded copper (plan_2026_10_05_select_cell_split.md; Denis 2026-10-05).

Guards: tests/registry/test_registry_match.py (the shared matcher),
tests/pipeline/test_plan_copper.py (one copper planner), and
tests/gui/test_select_cell_copper.py (core / worker / menu / door).

Run on deepseek_mutations_refresh_mixed_2026_10_05.py's machinery (count == 1 or
НЕДЕЙСТВИТЕЛЬНА, `_drop_pyc`, PYTHONDONTWRITEBYTECODE, `-n auto`, ПРОМАХ on zero
reds, a control that MUST survive) — grown from it, not written fresh (rule 38).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_select_cell_split_2026_10_05.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

MATCH = "kicadstamp/registry_match.py"
REGISTRY = "kicadstamp/registry.py"
PIPE = "kicadstamp/apply_pipeline.py"
SELECT = "gui/select_cell.py"
GUI = "gui/select_cell_copper.py"
EDITOR = "gui/docks/cell_editor.py"

MATCH_G = ["test_registry_match.py", "test_select_cell_copper.py"]
SELECT_G = ["test_select_cell_copper.py"]

ROWS = [
    ("C1 components worker also selects copper", GUI,
     "        if footprints:\n            adapter.select_items(list(footprints))",
     "        if footprints:\n"
     "            adapter.select_items(list(footprints) + list(adapter.get_vias())\n"
     "                                 + list(adapter.get_tracks()))  # MUTATION",
     "die", SELECT_G, ()),
    ("C2 geometry tier disabled (registry only)", MATCH,
     "        if live is None:",
     "        if live is None and False:  # MUTATION",
     "die", MATCH_G, ()),
    ("C3 geometry steals foreign-owned copper", MATCH,
     "            blocked = owned | taken",
     "            blocked = taken  # MUTATION",
     "die", MATCH_G, ()),
    ("C4 registry tier disabled (geometry re-finds)", MATCH,
     "        entry = reg.entries.get(key) if key else None",
     "        entry = None  # MUTATION",
     "die", MATCH_G, ()),
    # (An "adoption also claims a registry tier hit" row was DROPPED: with the
    # `key in reg.entries` guard still standing it changes nothing observable —
    # a registry-tier key is skipped by that guard before the tier matters. The
    # property is held by the matcher rows C2–C4.)
    ("C5 the components button carries copper", EDITOR,
     "            lambda checked=False: self._on_select_cell(with_copper=False))",
     "            lambda checked=False: self._on_select_cell(with_copper=True))  # MUTATION",
     "die", SELECT_G, ()),
    ("C10 the adoption prefilter is removed (stale key steals)", REGISTRY,
     "    for m in match_planned_copper(reg, candidates, live_items=live_items):",
     "    for m in match_planned_copper(reg, planned_cmds, live_items=live_items):  # MUTATION",
     "die", ["test_registry_match.py"], ()),
    ("C6 recorded-but-absent is not counted", SELECT,
     "        if m.live is None:\n            missing += 1\n            continue",
     "        if m.live is None:\n            pass  # MUTATION",
     "die", SELECT_G, ()),
    ("C7 the read path writes the registry", SELECT,
     "    trk_reg = TrackRegistry(adapter, trk_path)",
     "    trk_reg = TrackRegistry(adapter, trk_path)\n"
     "    via_reg._save_entries(via_reg.entries)  # MUTATION",
     "die", SELECT_G, ()),
    ("C8 dry run bypasses plan_copper (second planner)", PIPE,
     "        vias, tracks = self.plan_copper()\n        moves = self.planned_moves",
     "        moves = self.planner.plan_items(self.items)\n"
     "        vias = self.planner.plan_vias()\n"
     "        tracks = self.planner.plan_tracks()  # MUTATION",
     "die", ["test_plan_copper.py"], ()),
    ("C9 the UI-thread board handle returns to the payload", EDITOR,
     "            \"refs\": refs,\n            \"with_copper\": with_copper,\n        }",
     "            \"refs\": refs,\n            \"with_copper\": with_copper,\n"
     "            \"board\": getattr(connection, \"board\", None),  # MUTATION\n        }",
     "die", SELECT_G, ()),
    ("K1 cosmetic comment (control)", MATCH,
     "TIER_REGISTRY = \"registry\"",
     "TIER_REGISTRY = \"registry\"  # control",
     "survive", MATCH_G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
