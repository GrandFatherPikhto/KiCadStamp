# kicadstamp/diagnostics/deepseek_mutations_select_cell_2026_10_05.py
"""Demon's acceptance rows for Н5 "Select cell"
(plan_2026_10_04_refresh_mixed_cluster_selection; Denis 2026-10-05).

Guards live in tests/gui/test_select_cell.py, added to the shared machinery's
list below. Run on deepseek_mutations_refresh_mixed_2026_10_05.py's machinery
(count == 1 or НЕДЕЙСТВИТЕЛЬНА, _drop_pyc, PYTHONDONTWRITEBYTECODE, -n auto,
ПРОМАХ on zero reds, a control that MUST survive).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_select_cell_2026_10_05.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = rig.GUARDS + ["test_select_cell.py", "test_dialog_min_width.py"]
SELECT = "gui/select_cell.py"
EDITOR = rig.EDITOR
REFRESH = rig.REFRESH

ROWS = [
    ("S1 copper of another instance selected", SELECT,
     "        if not is_own_key(key, cell_identity, own_addresses, chosen_address, refs):\n"
     "            continue",
     "        if False:  # MUTATION\n            continue",
     "die", G, ()),
    ("S2 own copper not selected", SELECT,
     "            copper.append(item)",
     "            pass  # MUTATION",
     "die", G, ()),
    ("S3 missing registry uuids not counted", SELECT,
     "            missing += 1",
     "            pass  # MUTATION",
     "die", G, ()),
    ("S4 instance components not resolved", SELECT,
     "    footprints = resolve_context_footprints(\n"
     "        adapter, board_footprints, cluster, sheet, sheet_names)",
     "    footprints = []  # MUTATION",
     "die", G, ()),
    # ── Н5-1/Н5-2/Н5-4 (Н5а) ────────────────────────────────────────────────
    ("H5-1 config instances never used", EDITOR,
     "        instances = cell_instances(cfg, cell_name) if cfg is not None else []",
     "        instances = []  # MUTATION",
     "die", G, ()),
    ("H5-2 explicit instance overridden by remembered", SELECT,
     "    if cluster is not None:\n        return cluster, sheet",
     "    if False:  # MUTATION\n        return cluster, sheet",
     "die", G, ()),
    ("H5-4 tree nodes not narrowed", REFRESH,
     "            if not _addr_ok(a_cluster, a_sheet):\n                continue",
     "            if False:  # MUTATION\n                continue",
     "die", G, ()),
    ("K5 cosmetic comment (control)", SELECT,
     "    board_footprints = adapter.get_footprints()",
     "    board_footprints = adapter.get_footprints()  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
