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

ROWS = [
    ("S1 copper of another instance selected", SELECT,
     "        if not _is_own_key(key, cell_identity, own_addresses, chosen_address, refs):\n"
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
    ("K5 cosmetic comment (control)", SELECT,
     "    board_footprints = adapter.get_footprints()",
     "    board_footprints = adapter.get_footprints()  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
