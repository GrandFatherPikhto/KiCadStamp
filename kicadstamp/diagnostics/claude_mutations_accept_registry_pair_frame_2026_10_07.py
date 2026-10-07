# kicadstamp/diagnostics/claude_mutations_accept_registry_pair_frame_2026_10_07.py
"""Acceptance rows for 1fef70ea (plan_2026_10_07_registry_pair_frame_check:
take a registry pair only where the record puts the copper), Claude, 2026-10-07.

The Demon ran his six mutations by hand and committed no rig, so this file is
the reproducible one: his rows re-made (S1-S5) plus Claude's own (S6-S8).
Machinery — deepseek_mutations_refresh_mixed_2026_10_05 (count == 1 or
НЕДЕЙСТВИТЕЛЬНА, _drop_pyc, PYTHONDONTWRITEBYTECODE, -n auto, ПРОМАХ, control).

  * S1 dry-run tier-1 check off (a registry pair off its planned place taken)
  * S2 registry-only place check off
  * S3 an orphan via key (index past the list end) taken
  * S4 a non-rigid frame taken
  * S5 tolerance x100
  * S6 the yellow "not checked" reasons never reach the Subtract report
  * S7 the rigid gate compares with the wrong side (< instead of >)
  * S8 an orphan TRACK key taken
  * K1 cosmetic comment -> MUST survive
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

A = "kicadstamp/absent_copper_prune.py"
M = "kicadstamp/registry_match.py"
W = "gui/subtract_copper.py"
G = ["test_registry_pair_frame.py", "test_subtract_selected_copper.py",
     "test_select_cell.py", "test_select_cell_copper.py",
     "test_subtract_selection.py", "test_subtract_copper.py"]

ROWS = [
    ("S1 dry-run tier-1 check off", M,
     "    return bool(reg._live_matches(match.live, match.command))",
     "    return True  # MUTATION",
     "die", G, ()),
    ("S2 registry-only place check off", A,
     "    return _max_point_distance(expect, _live_points(live_item)) <= tol_mm",
     "    return True  # MUTATION",
     "die", G, ()),
    ("S3 orphan via key taken", A,
     "        if not 0 <= index < len(records):\n            return None\n        rec = records[index]",
     "        index = min(index, len(records) - 1)  # MUTATION\n        rec = records[index]",
     "die", G, ()),
    ("S4 non-rigid frame taken", A,
     "    if frame is None or frame.residual_mm > RIGID_TOLERANCE_MM:",
     "    if frame is None:  # MUTATION",
     "die", G, ()),
    ("S5 tolerance x100", A,
     "        if not pair_agrees_with_record(points, live_item, frame,\n"
     "                                       RIGID_TOLERANCE_MM):",
     "        if not pair_agrees_with_record(points, live_item, frame,\n"
     "                                       RIGID_TOLERANCE_MM * 100):  # MUTATION",
     "die", G, ()),
    ("S6 reasons never reach the report", W,
     "            orphan_keys=int(result.get(\"orphan_keys\") or 0)):\n        lines.append((note, \"warn\"))",
     "            orphan_keys=int(result.get(\"orphan_keys\") or 0)):\n        pass  # MUTATION",
     "die", G, ()),
    ("S7 rigid gate on the wrong side", A,
     "    if frame is None or frame.residual_mm > RIGID_TOLERANCE_MM:",
     "    if frame is None or frame.residual_mm < RIGID_TOLERANCE_MM:  # MUTATION",
     "die", G, ()),
    ("S8 orphan track key taken", A,
     "    if not 0 <= index < len(tracks):\n        return None",
     "    index = min(index, len(tracks) - 1)  # MUTATION",
     "die", G, ()),
    ("K1 cosmetic comment (control)", A,
     "    by_record: dict = {}\n    disagreed = orphan = 0\n",
     "    by_record: dict = {}  # control\n    disagreed = orphan = 0\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
