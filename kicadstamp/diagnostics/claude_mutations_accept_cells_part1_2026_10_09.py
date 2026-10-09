# kicadstamp/diagnostics/claude_mutations_accept_cells_part1_2026_10_09.py
"""Claude's acceptance rows for PART 1 of plan_2026_10_09_cells_and_entities
(commits 82215885 … 1d4bae41) — on top of Demon's rig machinery.

  * C1 OK stays enabled while a name conflicts
  * C2 the door converter skips the sheet re-resolution
  * K1 a cosmetic comment — MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_cells_part1_2026_10_09.py
"""
from kicadstamp.diagnostics import deepseek_mutations_cells_entities_part1_2026_10_09 as ds

T = ["test_instance_candidates.py", "test_add_entities_dialog.py", "test_add_entities_menu.py"]

ROWS = [
    ("C1 OK enabled despite a name conflict", "gui/docks/add_entities.py",
     "        ok.setEnabled(checked_any and not problems)\n",
     "        ok.setEnabled(checked_any)  # MUTATION\n",
     "die", T, ()),
    ("C2 no sheet re-resolution at the door", "gui/docks/instance_candidates.py",
     "    resolved = snapshot_with_resolved_sheets(list(snapshot or ()),",
     "    resolved = (lambda s, *_a, **_k: s)(list(snapshot or ()),  # MUTATION",
     "die", T, ()),
    ("K1 cosmetic comment (control)", "gui/docks/add_entities_flow.py",
     "def _write_chosen(hub, cfg, files, cell_name, file_path, chosen) -> None:\n",
     "def _write_chosen(hub, cfg, files, cell_name, file_path, chosen) -> None:  # control\n",
     "survive", T, ()),
]


if __name__ == "__main__":
    ds.MUTATIONS = ROWS
    ds.main()
