# kicadstamp/diagnostics/deepseek_mutations_refused_tree_matching_2026_10_07.py
"""Demon's acceptance rows for plan_2026_10_07_refused_tree_matching.

When the drift guard refused the tree that places a cell instance, the recording
is not materialized, the dry run plans nothing, and «Subtract selected copper»
falls back to the REGISTRY ONLY to build the map «record (kind, role_part,
index) -> live copper uuid». The drift guard, ApplyPipeline and
entity_placement are NOT touched; the registry, the config and the board are
only READ. The rows below map to the plan's own mutation list:

  * M1 the empty run goes back to {"empty": True} without the registry
  * M2 the registry map takes FOREIGN keys (filter off)
  * M3 the registry map does not check the uuid is on the board
  * M4 `kind` taken from the wrong registry (via <-> track)
  * M5 the registry path is enabled on a NON-empty run too
  * M6 the "registry only" line is not printed
  * M7 the "not checked" line is lost
  * M8 `select_cell_targets` grows its own filter copy again (structural)
  * K1 a cosmetic comment -> MUST survive

Guards: tests/selection/test_subtract_selection.py (the registry-only core map),
tests/gui/docks/test_subtract_selected_copper.py (the worker cells on a REAL
refused-tree config), tests/gui/test_subtract_copper.py (the report) and
tests/gui/test_select_cell.py (the structural single-filter guard). Run on
deepseek_mutations_refresh_mixed_2026_10_05.py's machinery (count == 1 or
НЕДЕЙСТВИТЕЛЬНА, `_drop_pyc`, PYTHONDONTWRITEBYTECODE, `-n auto`, ПРОМАХ on zero
reds, a control that MUST survive) — grown from it, not written fresh (rule 38).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_refused_tree_matching_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

ABSENT = "kicadstamp/absent_copper_prune.py"
SELECT = "gui/select_cell.py"
WORKER = "gui/subtract_copper.py"

G_CORE = ["test_subtract_selection.py"]
G_CORE_SELECT = ["test_subtract_selection.py", "test_select_cell.py"]
G_WORKER = ["test_subtract_selected_copper.py"]
G_REPORT = ["test_subtract_copper.py", "test_subtract_selected_copper.py"]
G_STRUCT = ["test_select_cell.py"]

# The fallback block of run_subtract_worker — the SAME text, two mutations (M1
# never falls back, M5 always falls back).
_FALLBACK_OLD = (
    "            if record_map.empty:\n"
    "                # The dry run planned nothing (a refused tree / an unrealized\n"
    "                # record): fall back to the registry for the SAME instance.\n"
    "                record_map = registry_record_copper_map(\n"
    "                    adapter, root, cfg, cell_name, cluster, sheet,\n"
    "                    sheet_names=sheet_names)")

ROWS = [
    # M1 — the registry fallback is removed: an empty dry run goes back to red.
    ("M1 empty run without the registry", WORKER, _FALLBACK_OLD,
     _FALLBACK_OLD.replace("            if record_map.empty:",
                           "            if False:  # MUTATION"), "die", G_WORKER, ()),
    # M2 — the ownership filter is off: another cell's key is claimed.
    ("M2 registry map takes foreign keys", ABSENT,
     "        if not is_own_key(key, cell_identity, own_addresses, "
     "chosen_address, refs):\n            continue",
     "        if False:  # MUTATION\n            continue",
     "die", G_CORE_SELECT, ()),
    # M3 — a key whose uuid is NOT on the board is claimed anyway.
    ("M3 uuid not checked against the board", ABSENT,
     "        if uuid in live_uuids[kind]:\n"
     "            by_record[(kind, role_part, index)] = uuid",
     "        by_record[(kind, role_part, index)] = uuid  # MUTATION",
     "die", G_CORE, ()),
    # M4 — the two registries are swapped: a via key is filed as a track.
    ("M4 kind from the wrong registry", ABSENT,
     "    for kind, entries in ((\"via\", via_entries), (\"track\", track_entries)):",
     "    for kind, entries in ((\"track\", via_entries), (\"via\", "
     "track_entries)):  # MUTATION",
     "die", G_CORE_SELECT, ()),
    # M5 — the registry path is taken even when the dry run DID plan (tier 2 lost).
    ("M5 registry path on a non-empty run", WORKER, _FALLBACK_OLD,
     _FALLBACK_OLD.replace("            if record_map.empty:",
                           "            if True:  # MUTATION"), "die", G_WORKER, ()),
    # M6 — the "matched by the registry only" line is dropped.
    ("M6 registry-only line dropped", WORKER,
     "    if result.get(\"source\") == \"registry\" and "
     "result.get(\"planned\", 0) > 0:",
     "    if False:  # MUTATION", "die", G_REPORT, ()),
    # M7 — the "not checked" count is lost.
    ("M7 not-checked line lost", WORKER,
     "    if result.get(\"not_checked\"):",
     "    if False:  # MUTATION", "die", G_REPORT, ()),
    # M8 — the filter loop returns INSIDE select_cell_targets (a second copy).
    ("M8 second filter copy in Select cell", SELECT,
     "    for kind, _role_part, _index, uuid in own_registry_entries(",
     "    _ = is_own_key(\"\", None, own_addresses, chosen_address, refs)  # MUTATION\n"
     "    for kind, _role_part, _index, uuid in own_registry_entries(",
     "die", G_STRUCT, ()),
    # K1 — cosmetic comment (control): must survive.
    ("K1 cosmetic comment (control)", ABSENT,
     "    by_record: dict = {}\n    checked: set = set()",
     "    by_record: dict = {}\n    checked: set = set()  # control",
     "survive", G_CORE, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
