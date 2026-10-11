# kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b2_2026_10_11.py
"""Acceptance mutations for Б5б-2 of
techdocs/handoff/deepseek/plan/plan_2026_10_08_remove_spokes.md — the sub-commit
that removes the dead ``geometry/spoke_layout.apply_spoke_geometry`` (its only
product consumer was the deleted manual_position_calculator), DeepSeek,
2026-10-11.

Derived from deepseek_mutations_remove_spokes_b5b1_2026_10_11.py (rule 38),
whose machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME
guards: basename-resolved tests under tests/, `_drop_pyc`, the
`original.count(old) != 1` refusal (a non-unique template is a MISS, not a
kill), ПРОМАХ when nothing goes red, and a control that MUST survive.

WHAT IS BEING PROVEN. Every property the removed apply_spoke_geometry cells
guarded is carried by a surviving host: the SHARED primitives (local_to_absolute
/ rotate_local_offset — M1, M2) and the clone expansion
(apply_clone_geometry — M3, checked by the moved/kept test_cell_anchor_geometry
cells). Cutting each host line must go red.

  * M1  local_to_absolute drops the origin translation   -> primitive cells die
  * M2  rotate_local_offset ignores the angle            -> primitive cells die
  * M3  apply_clone_geometry drops the A subtraction     -> clone anchor cells die
  * K1  a cosmetic comment                               -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_remove_spokes_b5b2_2026_10_11.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

SPOKE = "kicadstamp/geometry/spoke_layout.py"
CLONE_GEOM = "kicadstamp/geometry/clone_geometry.py"

PRIMITIVE_GUARDS = ["test_spoke_layout.py"]
CLONE_GUARDS = ["test_cell_anchor_geometry.py"]

MUTATIONS = [
    # 1 — local_to_absolute loses its translation: origin + rotated -> rotated only.
    ("M1 local_to_absolute drops the origin", SPOKE,
     "    return Vector2.from_xy(origin.x + rotated.x, origin.y + rotated.y)\n",
     "    return Vector2.from_xy(rotated.x, rotated.y)  # MUTATION\n",
     "die", PRIMITIVE_GUARDS, ()),
    # 2 — rotate_local_offset ignores its angle: no rotation at all.
    ("M2 rotate_local_offset ignores the angle", SPOKE,
     "    return local_vec.rotate(Angle.from_degrees(rotation_deg), _ORIGIN)\n",
     "    return local_vec.rotate(Angle.from_degrees(0.0), _ORIGIN)  # MUTATION\n",
     "die", PRIMITIVE_GUARDS, ()),
    # 3 — the clone carrier stops subtracting the mount A: the anchor cells go red.
    ("M3 apply_clone_geometry drops the A subtraction", CLONE_GEOM,
     "        p = local_to_absolute(origin, along_mm - ax_mm, across_mm - ay_mm, rotation_deg)\n",
     "        p = local_to_absolute(origin, along_mm, across_mm, rotation_deg)  # MUTATION\n",
     "die", CLONE_GUARDS, ()),
    # K1 — a cosmetic comment in a mutated file: MUST survive.
    ("K1 a cosmetic comment", SPOKE,
     "    return Vector2.from_xy(origin.x + rotated.x, origin.y + rotated.y)\n",
     "    return Vector2.from_xy(origin.x + rotated.x, origin.y + rotated.y)  # control\n",
     "survive", PRIMITIVE_GUARDS, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
