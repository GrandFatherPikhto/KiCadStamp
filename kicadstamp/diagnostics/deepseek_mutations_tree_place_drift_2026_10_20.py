# kicadstamp/diagnostics/deepseek_mutations_tree_place_drift_2026_10_20.py
"""Mutation rows for point 2 of plan_2026_10_20_cells_save_keeps_trees_cfg_fresh:
the warning a PARTIAL curated redraw owes when an ancestor it will NOT move is
itself off its place in the tree.

One rule, one host (kicadstamp/tree_plan_warnings.plan_pose_subject — since the
acceptance of 21.10, Д-3), the check itself in gui/docks/tree_place_drift.py and
its wiring in gui/docks/cascade.py. Each row removes exactly ONE decision that
the cells of tests/trees/test_tree_plan_warnings.py,
tests/gui/docks/test_tree_place_drift.py and tests/gui/docks/test_cascade.py are
supposed to pin — a decision nobody's cell defends is a decision that will drift.

Safeguards (as in the earlier harnesses): a template that matches more than once
is refused; a row that reddens NOTHING is a MISS; the control row (a cosmetic
comment) must survive; every file is restored in `finally` and the restoration is
verified by text before the next row.

Run: `.venv/bin/python kicadstamp/diagnostics/deepseek_mutations_tree_place_drift_2026_10_20.py`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DRIFT = "gui/docks/tree_place_drift.py"
CASCADE = "gui/docks/cascade.py"
WARNINGS = "kicadstamp/tree_plan_warnings.py"
TREE_POS = "kicadstamp/tree_position.py"
GUARDS = ("tests/gui/docks/test_tree_place_drift.py "
          "tests/gui/docks/test_cascade.py "
          "tests/trees/test_tree_plan_warnings.py")

MUTATIONS = [
    ("M1 the run's own names stop mattering (a redrawn ancestor is warned too)",
     DRIFT,
     "                if ref not in wanted:\n",
     "                if True:  # MUTATION: a redrawn ancestor is warned about too\n",
     ["test_an_ancestor_inside_the_run_is_silent",
      "test_a_mounts_placing_component_that_is_redrawn_is_silent"]),

    ("M2 the tolerance is a hundred times looser", DRIFT,
     "_TOL_NM = int(RIGID_TOLERANCE_MM * MM)\n",
     "_TOL_NM = int(RIGID_TOLERANCE_MM * MM * 100)  # MUTATION\n",
     ["test_a_shifted_ancestor_names_every_selected_child_once",
      "test_the_threshold_is_compared_in_whole_nanometres"]),

    ("M3 the turn is never a deviation", DRIFT,
     "    turned = turn is not None and abs(turn) > ANGLE_TOLERANCE_DEG\n",
     "    turned = False  # MUTATION: the angle is never a deviation\n",
     ["test_a_turned_ancestor_is_named"]),

    ("M4 an internal mount is never plan-borne (the host's mount branch)",
     WARNINGS,
     "    if kind != \"mount\" or cfg is None:\n        return None\n",
     "    if True:  # MUTATION: an internal mount is never a subject\n"
     "        return None\n",
     ["test_a_mount_parent_is_named_as_the_trees_place",
      "test_the_host_answers_the_placing_component_of_an_internal_mount"]),

    ("M5 only the NEAREST ancestor is examined", DRIFT,
     "        parent_ref = parent.get(name)\n        while parent_ref is not None:\n",
     "        parent_ref = parent.get(name)\n"
     "        if parent_ref is not None:  # MUTATION: nearest ancestor only\n",
     ["test_the_walk_goes_past_an_unshifted_father"]),

    ("M6 the live read is not STRICT (a guessed pose becomes a number)", DRIFT,
     "        live = read_record_live_pose(adapter, cfg, ref, record, sheet_names,\n"
     "                                     strict=True)\n",
     "        live = read_record_live_pose(adapter, cfg, ref, record, sheet_names,\n"
     "                                     strict=False)  # MUTATION\n",
     ["test_a_non_rigid_cluster_reports_the_residual_and_no_deviation"]),

    ("M7 the line never reaches the TREE run's warnings (only the logger)", CASCADE,
     "        for line in drift_lines:\n            logger.warning(line)\n"
     "        warnings.extend(drift_lines)\n"
     "        captures, parent_map = capture_rigid_state(adapter, cfg, tree, names, sheet_names)\n",
     "        for line in drift_lines:\n            logger.warning(line)\n"
     "        captures, parent_map = capture_rigid_state(adapter, cfg, tree, names, sheet_names)\n",
     ["test_the_place_drift_line_reaches_the_warnings_and_the_log"]),

    ("M8 the turn is not normalised (plan 360 vs board 0 becomes -360)", DRIFT,
     "        turn = normalize_deg(float(live.rotation_deg) - float(used_deg))\n",
     "        turn = float(live.rotation_deg) - float(used_deg)  # MUTATION: no wrap\n",
     ["test_a_full_turn_between_the_plan_and_the_board_is_no_deviation"]),

    ("M9 the FOREST lines never reach the run's warnings", CASCADE,
     "        warnings.extend(drift_lines)\n\n"
     "        # Stage 2 (design P3 D5): lay each NORMAL flow root from its LIVE anchor and\n",
     "        # Stage 2 (design P3 D5): lay each NORMAL flow root from its LIVE anchor and\n",
     ["test_the_place_drift_line_reaches_the_forest_warnings_and_the_log"]),

    ("M10 the FOREST entry never calls the check at all", CASCADE,
     "        drift_lines = place_drift_lines(adapter, cfg, linked, names, sheet_names)\n",
     "        drift_lines = []  # MUTATION: the check is not called at all\n",
     ["test_the_place_drift_line_reaches_the_forest_warnings_and_the_log"]),

    ("M11 a placement node is never plan-borne (the host's placement branch)",
     WARNINGS,
     "    kind = getattr(node, \"kind\", None)\n    if kind == \"placement\":\n",
     "    kind = getattr(node, \"kind\", None)\n"
     "    if False:  # MUTATION: a placement node is never plan-borne\n",
     ["test_a_placement_parent_is_named_as_the_trees_place",
      "test_the_host_answers_a_placement_node_with_its_own_record",
      "test_the_forest_planner_names_a_placement_parent_as_the_trees_place"]),

    ("M12 the wording ignores from_plan (always the historical sentence)", WARNINGS,
     "    if from_plan:\n",
     "    if False:  # MUTATION: always the historical wording\n",
     ["test_a_placement_parent_is_named_as_the_trees_place",
      "test_a_mount_parent_is_named_as_the_trees_place"]),

    ("M13 membership is tested on the WALKED node, not on the subject", DRIFT,
     "                if ref not in wanted:\n",
     "                if parent_ref not in wanted:  # MUTATION: the walked node\n",
     ["test_a_mounts_placing_component_that_is_redrawn_is_silent"]),

    ("M14 the deviation is the largest axis, not the distance", DRIFT,
     "    distance_nm = math.hypot(dx, dy)\n",
     "    distance_nm = max(abs(dx), abs(dy))  # MUTATION: the largest axis\n",
     ["test_a_diagonal_shift_is_reported_as_the_distance",
      "test_both_axes_below_the_tolerance_can_still_be_a_deviation"]),

    ("M15 the FOREST planner always passes from_plan=False", TREE_POS,
     "                plan_pose_subject(cfg, index.node_index.get(p),\n"
     "                                  index.owner_map.get(p)) is not None))\n",
     "                plan_pose_subject(cfg, index.node_index.get(p),\n"
     "                                  index.owner_map.get(p)) is not None\n"
     "                and False))  # MUTATION: never from the plan\n",
     ["test_the_forest_planner_names_a_placement_parent_as_the_trees_place"]),

    ("M16 the FOREST entry does not hand cfg to the planner (Д-6)", CASCADE,
     "    names, warnings = curated_redraw_plan_forest(\n"
     "        linked, selected_refs, copper_deps=copper_deps, cfg=cfg)\n",
     "    names, warnings = curated_redraw_plan_forest(\n"
     "        linked, selected_refs, copper_deps=copper_deps)  # MUTATION: no cfg\n",
     ["test_the_forest_redraw_hands_cfg_to_the_planner_for_a_mount_parent"]),

    ("K1 a cosmetic comment in the host (CONTROL)", DRIFT,
     "_TOL_NM = int(RIGID_TOLERANCE_MM * MM)\n",
     "_TOL_NM = int(RIGID_TOLERANCE_MM * MM)  # control\n",
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
                    print(f"[MISS] {name}: a cosmetic change reddened {failed}")
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
