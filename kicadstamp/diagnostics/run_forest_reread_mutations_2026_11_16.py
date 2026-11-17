# kicadstamp/diagnostics/run_forest_reread_mutations_2026_11_16.py
"""Mutation rows for the DECISION half of «Reread positions from board»
(plan_2026_11_16_reread_forest_from_board, commit 1).

The contract the rows attack: the anchor follows the BIGGEST group of equally
shifted voters and only when it is strictly the biggest; `keep anchors` never
touches it; an unmoved node never enters the file; a node that could not be read
is a row with a reason; the written value keeps the node's own shape.

  M1  no anchor choice at all (absorb mode returns None)
  M2  the anchor follows the SMALLEST group
  M3  the tie rule is dropped (a tie silently becomes the first group)
  M4  `keep anchors` still moves the anchor
  M5  the "did not move" threshold is a thousand times bigger
  M6  the angle is compared without modulo 360
  M7  the Polar shape is lost (always xy)
  M8  a skip loses its row (silent absence)
  M9  the absorbed translation is not subtracted from the node
  M10 cosmetic control (a comment must change nothing)

Rows M13–M16 attack the WIRING (`gui/docks/trees_reread_board.py`) and run the
wiring cells instead — the acceptance of 17.11 found that this file had no mutations
at all, so nothing here could have caught the `source` ordering defect (Ф1):

  M13 `source` is taken from `inside` BEFORE the kind of base (Ф1 comes back)
  M14 the content of a module is read against its MARKER instead of its EFF
  M15 the sync marker loses the module's own move
  M16 the raw anchor point keeps the anchor's own shift (double count)

Safeguards as in every harness here (rule 38): a template matching more than once
is REFUSED; a row that reddens nothing is a MISS; the control must survive; every
file is restored in `finally` and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_forest_reread_mutations_2026_11_16`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DECIDE = "kicadstamp/forest_reread.py"

GUARDS = "tests/trees/test_forest_reread.py"

WIRING = "gui/docks/trees_reread_board.py"

WIRING_GUARDS = "tests/gui/docks/test_trees_reread_board.py"

MUTATIONS = [
    ("M1 no anchor choice at all", DECIDE,
     "    if not groups:\n"
     "        return AnchorChoice(None, _(\"anchor kept: no node of this tree votes\"))\n",
     "    if True:  # MUTATION: never absorb anything\n"
     "        return AnchorChoice(None, _(\"anchor kept: no node of this tree votes\"))\n",
     ["test_a2_the_biggest_group_gives_the_anchor_delta"]),

    ("M2 the anchor follows the SMALLEST group", DECIDE,
     "    biggest = groups[0]\n",
     "    biggest = groups[-1]  # MUTATION: smallest\n",
     ["test_a2_the_biggest_group_gives_the_anchor_delta"]),

    ("M3 the tie rule is dropped", DECIDE,
     "    if rivals and rivals[0].size >= biggest.size:\n",
     "    if False:  # MUTATION: a tie silently becomes the first group\n",
     ["test_a3_two_equal_groups_leave_the_anchor_alone"]),

    ("M4 keep anchors still moves the anchor", DECIDE,
     "    if override == OVERRIDE_KEEP:\n"
     "        return AnchorChoice(None, _(\"anchor kept: chosen for this tree\"))\n"
     "    if mode == MODE_KEEP:\n"
     "        return AnchorChoice(None, _(\"anchor kept: blocks were moved by hand\"))\n",
     "    # MUTATION: keep still absorbs\n",
     ["test_a5_keep_anchors_never_touches_the_anchor"]),

    ("M5 the did-not-move threshold is a thousand times bigger", DECIDE,
     "        moved = (abs(new_offset[0] - read.stored_offset[0]) > MM_TOLERANCE\n"
     "                 or abs(new_offset[1] - read.stored_offset[1]) > MM_TOLERANCE)\n",
     "        moved = (abs(new_offset[0] - read.stored_offset[0]) > MM_TOLERANCE * 1000\n"
     "                 or abs(new_offset[1] - read.stored_offset[1]) > MM_TOLERANCE * 1000)\n",
     # A 1 mm move must be written; 0.003 mm is below BOTH thresholds, so the
     # micrometre cell cannot see this mutation (it stays green, and that is right).
     ["test_b_one_node_shifted_by_a_millimetre_is_written"]),

    ("M6 the angle is compared without modulo 360", DECIDE,
     "                  and abs(relative_rotation_deg(read.rotation_deg,\n"
     "                                               read.stored_rotation)) > DEG_TOLERANCE)\n",
     "                  and abs(read.rotation_deg - read.stored_rotation) > DEG_TOLERANCE)\n",
     ["test_i_a_node_at_180_degrees_gets_no_rotation_change"]),

    ("M7 the Polar shape is lost", DECIDE,
     "    return to_polar(offset) if polar else offset\n",
     "    return offset  # MUTATION: always xy\n",
     ["test_the_polar_shape_survives_the_reread"]),

    ("M8 a skip loses its row", DECIDE,
     "        if read.error is not None:\n"
     "            plan.skipped.append(Skip(read.tree, read.ref, read.error))\n"
     "            continue\n",
     "        if read.error is not None:  # MUTATION: silent absence\n"
     "            continue\n",
     ["test_j_a_node_that_could_not_be_read_is_skipped_with_its_reason"]),

    ("M9 the absorbed translation is not subtracted", DECIDE,
     "        new_offset = (read.offset_mm[0] - read.absorb_mm[0],\n"
     "                      read.offset_mm[1] - read.absorb_mm[1])\n",
     "        new_offset = (read.offset_mm[0],  # MUTATION: absorb ignored\n"
     "                      read.offset_mm[1])\n",
     ["test_a2_the_node_is_read_against_the_base_with_the_new_shift"]),

    ("M11 the tie sentence goes out unformatted", DECIDE,
     "            \"anchor kept: {n} groups of voters disagree equally — read each node\"\n"
     "        ).format(n=tied))\n",
     "            \"anchor kept: {n} groups of voters disagree equally — read each node\"\n"
     "        ))  # MUTATION: a literal {n}\n",
     ["test_a3_two_equal_groups_leave_the_anchor_alone"]),

    ("M12 a turned node loses its rotation in the row", DECIDE,
     "            was_rotation=read.stored_rotation,\n"
     "            now_rotation=read.rotation_deg))\n",
     "            ))  # MUTATION: rotation lost\n",
     ["test_a_node_that_was_only_turned_still_gets_a_row"]),

    ("M10 cosmetic control (a comment must change nothing)", DECIDE,
     "    biggest = groups[0]\n",
     "    biggest = groups[0]  # MUTATION: cosmetic\n",
     "survive"),

    # ── the WIRING (Ф1–Ф2 of the 17.11 acceptance) ──────────────────────────

    ("M13 source is taken from `inside` before the kind of base", WIRING,
     "    if base[0] == \"node\" or node.kind == \"mount\":\n"
     "        source = \"none\"\n"
     "    elif inside is not None:\n"
     "        source = [\"module\", inside]\n"
     "    elif base[0] == \"anchor\":\n"
     "        source = \"anchor\"\n",
     "    if inside is not None:  # MUTATION: inside first\n"
     "        source = [\"module\", inside]\n"
     "    elif base[0] == \"anchor\" and node.kind != \"mount\":\n"
     "        source = \"anchor\"\n",
     ["test_f1_a_child_of_module_content_absorbs_nothing"], WIRING_GUARDS),

    ("M14 module content is read against the MARKER", WIRING,
     "            _visit(out, ctx, owner, child, list(child.nodes),\n"
     "                   (\"pose\", eff, float(pose[4])), node.ref, depth + 1)\n",
     "            _visit(out, ctx, owner, child, list(child.nodes),\n"
     "                   (\"pose\", marker, float(pose[2])), node.ref, depth + 1)\n",
     ["test_module_content_is_read_against_the_EFF_base_not_the_marker"],
     WIRING_GUARDS),

    ("M15 the sync marker loses the module's own move", WIRING,
     "        marker = (emb[\"marker_mm\"][0] + anchor[0] + moved[0],\n"
     "                  emb[\"marker_mm\"][1] + anchor[1] + moved[1])\n",
     "        marker = (emb[\"marker_mm\"][0] + anchor[0],\n"
     "                  emb[\"marker_mm\"][1] + anchor[1])  # MUTATION\n",
     ["test_the_marker_is_taken_AFTER_the_owner_shift_and_the_module_move"],
     WIRING_GUARDS),

    ("M16 the raw anchor point keeps the anchor's own shift", WIRING,
     "    return ((base_pos.x - shift.x) / MM, (base_pos.y - shift.y) / MM)\n",
     "    return (base_pos.x / MM, base_pos.y / MM)  # MUTATION: double count\n",
     ["test_the_raw_anchor_point_drops_the_anchors_own_shift"], WIRING_GUARDS),
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
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT: the guard run did not finish within 600 s"
    return proc.returncode, proc.stdout + proc.stderr


def _failed_ids(out: str) -> list:
    return [line.split(" ", 1)[1].split(" - ")[0]
            for line in out.splitlines() if line.startswith("FAILED ")]


def main() -> int:
    guards = os.environ.get("GUARDS", GUARDS)
    print(f"guards: {guards}")
    code, out = _run(guards)
    if code != 0:
        print("BASELINE IS RED — fix that first:\n", out[-3000:])
        return 2
    print("baseline green")

    misses = 0
    for row in MUTATIONS:
        name, rel, old, new, expect = row[:5]
        # A row may bring its OWN cells (the wiring rows do): the decision half and
        # the wiring are guarded by different files, and a mutation must redden the
        # cells that actually pin the line it breaks.
        row_guards = row[5] if len(row) > 5 else guards
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"[SKIP/MISS] {name}: the template matches "
                  f"{original.count(old)} times — refusing")
            misses += 1
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            code, out = _run(row_guards)
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
                      f"nothing (exit {code})\n{out[-1200:]}")
                misses += 1
                continue
            missing = [e for e in expect if not any(e in f for f in failed)]
            if missing:
                print(f"[MISS] {name}: expected these to redden, they did not: "
                      f"{missing}\n  actually failed: {failed}")
                misses += 1
            else:
                print(f"[KILL] {name}: {len(failed)} red — {failed[:4]}")
        finally:
            path.write_text(original, encoding="utf-8")
            restored = path.read_text(encoding="utf-8")
            assert restored == original, f"restore FAILED for {rel}"
    _sweep_pycache()
    print(f"\nverdict: {len(MUTATIONS) - misses} as expected, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
