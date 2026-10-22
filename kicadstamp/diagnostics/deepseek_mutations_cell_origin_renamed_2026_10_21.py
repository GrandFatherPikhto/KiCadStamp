# kicadstamp/diagnostics/deepseek_mutations_cell_origin_renamed_2026_10_21.py
"""Mutation rows for plan_2026_10_21_cell_refresh_origin_role_renamed: refreshing
a cell "from the selection" when the role that used to be the cell's ORIGIN was
renamed (or removed) on the board.

One rule, one host (kicadstamp/cell_origin.py) plus the two write/read facts it
owns: WHICH matched role anchors the translation, and whether the plan leaves the
cell a measurable origin. Each row removes exactly ONE decision that the cells
are supposed to pin.

Safeguards as in the earlier harnesses: a template that matches more than once is
refused; a row that reddens NOTHING is a MISS; the CONTROL rows must survive;
every file is restored in `finally`, verified by text.

WHY there is no "flip the surrogate order" CONTROL row. The determinism claim
("on a rigid instance the choice of surrogate cannot change the plan") is pinned
by the CELL `test_the_surrogate_choice_cannot_change_a_rigid_plan`, which calls
the frame with two explicit surrogates. A rig row that flipped the order inside
the shared host was TRIED and reddened a NESTED cell
(`test_nested_read_captures_the_nested_rotation_and_mirror`) — and that is
correct behaviour, not a gap: a nested placement with a single usable slot has no
geometry to FIT a rotation from, so its angle legitimately comes from the chosen
reference (`_read_nested_live`'s single-role branch). The claim therefore holds
for the REFRESH (which always fits), not for a one-slot nested read — recorded in
the plan's "found on the way".

Run: `.venv/bin/python kicadstamp/diagnostics/deepseek_mutations_cell_origin_renamed_2026_10_21.py`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ORIGIN = "kicadstamp/cell_origin.py"
REFRESH = "kicadstamp/cell_geometry_refresh.py"
FRAME = "kicadstamp/cell_frame.py"
DOCK = "gui/docks/cell_editor.py"
PROBE = ("kicadstamp/diagnostics/"
         "claude_probe_cell_origin_side_and_v2_anchor_2026_10_22.py")
GUARDS = ("tests/selection/test_cell_refresh_origin_renamed.py "
          "tests/trees/test_cell_geometry_refresh.py "
          "tests/geometry/test_cell_frame_single_slot.py "
          "tests/geometry/test_cell_frame_mirror.py "
          "tests/gui/docks/test_cell_editor.py " + PROBE)

MUTATIONS = [
    ("M1 the origin role is required again (the fact's own refusal)",
     ORIGIN,
     "    for component in components:\n"
     "        role = component.get(\"role\")\n"
     "        if role in role_to_ref:\n",
     "    for component in components:\n"
     "        role = component.get(\"role\")\n"
     "        if False:  # MUTATION: the origin role must still be there\n",
     ["test_a_renamed_origin_role_no_longer_stops_the_update",
      "test_a_v2_cell_whose_mount_role_is_missing_from_the_plan_is_refused"]),

    ("M2 the write precondition is dropped (a cell may be left unmeasurable)",
     ORIGIN,
     "    if origin_role is None:\n"
     "        require_one_zero_origin(planned)\n",
     "    if origin_role is None:\n"
     "        pass  # MUTATION: no check that a legacy cell keeps its origin\n",
     ["test_a_plan_that_empties_the_origin_is_refused_with_the_read_only_verdict",
      "test_two_components_on_the_origin_are_refused_too",
      "test_a_plan_that_leaves_no_component_on_the_origin_is_refused_in_words"]),

    ("M3 the copper classifier gets the OLD component list (the new role is invisible)",
     REFRESH,
     "    planned_components = plan_components(components, component_updates,\n"
     "                                         new_component_records,\n"
     "                                         removed_component_records)\n",
     "    planned_components = components  # MUTATION: the pre-update list\n",
     ["test_the_recreated_copper_takes_the_new_role_not_its_neighbour"]),

    ("M4 the refusal goes back to the placement footer (\"Fix the config\")",
     REFRESH,
     "        raise ValidationError(format_read_fatal(\n"
     "            _(\"cannot refresh cell geometry from the current selection\"),\n"
     "            all_problems))\n",
     "        raise ValidationError(format_fatal_error(\n"
     "            _(\"cannot refresh cell geometry from the current selection\"),\n"
     "            all_problems))  # MUTATION\n",
     ["test_a_role_problem_carries_the_read_only_verdict"]),

    ("K1 a cosmetic comment in the refresh host (CONTROL)", REFRESH,
     "    planned_components = plan_components(components, component_updates,\n",
     "    planned_components = plan_components(components, component_updates,  # control\n",
     "survive"),

    ("K2 a cosmetic comment in the host (CONTROL)", ORIGIN,
     "    own = (origin_role if origin_role is not None\n",
     "    own = (origin_role if origin_role is not None  # control\n",
     "survive"),

    # ── Д-1…Д-4 of the Ч2 rework (2026-10-22) ───────────────────────────────

    ("M5 the side is compared with the CELL's layer again (Д-1)",
     FRAME,
     "        mirror = instance_mirror(ref_fp.layer, ref_slot.get(\"layer\"), "
     "cell_layer)\n",
     "        mirror = mirror_for_layer(ref_fp.layer, cell_layer, fallback=None)  "
     "# MUTATION\n",
     ["test_a_whole_cluster_with_a_slot_on_its_own_side_is_updated[True]",
      "test_a_v2_cell_keeps_its_offsets_by_a_slot_on_its_own_side[True]"]),

    ("M6 side_layer taken from the CELL, not the instance (Д-1)",
     FRAME,
     "            side_layer = mirror_layer(cell_layer) if mirror else cell_layer\n",
     "            side_layer = cell_layer  # MUTATION: never the instance's side\n",
     ["test_a_mirrored_instance_still_names_the_other_side"]),

    ("M7 the plan never hands the v2 anchor over (Д-2)",
     REFRESH,
     "        new_anchor_role=transfer[0] if transfer else None,\n",
     "        new_anchor_role=None,  # MUTATION: no transfer announced\n",
     ["test_a_v2_cells_anchor_role_passes_to_the_part_that_stands_on_it",
      "test_a_pad_anchor_transfers_only_when_the_new_part_carries_that_pad",
      "test_p2_v2_cell_survives_a_second_refresh_of_the_same_selection"]),

    ("M8 an ambiguous mount point is resolved by picking the first (Д-2)",
     ORIGIN,
     "    if len(slots) > 1:\n",
     "    if False:  # MUTATION: ambiguity resolved by picking\n",
     ["test_two_roles_on_the_mount_point_are_refused"]),

    ("M9 no preference for the cell's OWN origin role (Д-3, Claude's mutation A)",
     ORIGIN,
     "        if role == own and role in role_to_ref:\n",
     "        if False and role == own and role in role_to_ref:  # MUTATION\n",
     ["test_the_cells_own_origin_anchors_a_non_rigid_legacy_instance",
      "test_the_v2_mount_role_anchors_a_non_rigid_instance"]),

    ("M10 the nested read demands a canonical cell (Д-4, Claude's mutation F)",
     REFRESH,
     "        role_to_ref, cell.anchor_role, require_canonical=False)\n",
     "        role_to_ref, cell.anchor_role, require_canonical=True)  # MUTATION\n",
     ["test_a_nested_legacy_cell_without_a_zero_slot_is_still_read"]),

    ("M11 the dock forgets the transferred anchor role (Д-2е)",
     DOCK,
     "        if plan.new_anchor_role:\n",
     "        if False:  # MUTATION: the dead anchor name stays in the record\n",
     ["test_refresh_writes_the_transferred_anchor_role"]),

    ("M12 the pad of the new part is not checked (Д-2з)",
     ORIGIN,
     "    return fp is not None and adapter.get_pad_by_number(fp, pad) is not None\n",
     "    return fp is not None  # MUTATION: standing on the point is enough\n",
     ["test_a_pad_anchor_of_a_replacement_part_is_refused"]),

    ("K3 a cosmetic comment in the transfer host (CONTROL)", ORIGIN,
     "    mount, _problem = mount_of(current, origin_role)\n",
     "    mount, _problem = mount_of(current, origin_role)  # control\n",
     "survive"),
]


def _sweep_pycache() -> None:
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def _run(guards: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    _sweep_pycache()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *guards.split(), "-q", "-rf",
             "-p", "no:cacheprovider"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT: the guard run did not finish within 300 s"
    return proc.returncode, proc.stdout + proc.stderr


def _failed_ids(out: str) -> list:
    return [line.split(" ", 1)[1].split(" - ")[0]
            for line in out.splitlines() if line.startswith("FAILED ")]


def main() -> int:
    guards = os.environ.get("GUARDS", GUARDS)
    print(f"guards: {guards}")
    code, out = _run(guards)
    if code != 0:
        print("BASELINE IS RED — fix that first:\n", out[-2000:])
        return 2
    print("baseline green")

    misses = 0
    for name, rel, old, new, expect in MUTATIONS:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"[SKIP/MISS] {name}: the template matches "
                  f"{original.count(old)} times — refusing")
            misses += 1
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            code, out = _run(guards)
            failed = _failed_ids(out)
            if expect == "survive":
                if failed:
                    print(f"[MISS] {name}: a control reddened {failed}")
                    misses += 1
                else:
                    print(f"[OK] {name}: survived, as it must")
                continue
            if not failed:
                print(f"[MISS] {name}: NO failed test — the mutation kills "
                      f"nothing (exit {code})\n{out[-800:]}")
                misses += 1
                continue
            missing = [e for e in expect if not any(e in f for f in failed)]
            if missing:
                print(f"[MISS] {name}: expected these to redden, they did not: "
                      f"{missing}\n  actually failed: {failed}")
                misses += 1
            else:
                print(f"[KILL] {name}: {len(failed)} red — {failed}")
        finally:
            path.write_text(original, encoding="utf-8")
            restored = path.read_text(encoding="utf-8")
            assert restored == original, f"restore FAILED for {rel}"
    _sweep_pycache()
    print(f"\nverdict: {len(MUTATIONS) - misses} as expected, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
