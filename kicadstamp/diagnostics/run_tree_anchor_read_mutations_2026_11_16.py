# kicadstamp/diagnostics/run_tree_anchor_read_mutations_2026_11_16.py
"""Mutation rows for plan_2026_11_16_tree_anchor_read_current_position — the
«Read current position» button of a TREE ANCHOR.

The contract is «after Read, a redraw moves nothing»: the shift only is corrected,
the delta is measured live-minus-laid-out, converted at the ANCHOR's own angle, and
only TOP-LEVEL self-placing nodes vote. Each row breaks exactly ONE of those:

  M1  the delta's sign is flipped                    (tree_anchor_read)
  M2  the delta is added in the BOARD frame          (tree_anchor_read)
  M3  children vote as well                          (trees_anchor_read)
  M4  the agreement tolerance is a thousand times bigger (tree_anchor_read)
  M5  a turned node is not refused                   (tree_anchor_read)
  M6  the button writes a node's `xy` instead of the shift (trees_anchor_read)
  M7  a cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once
is REFUSED; a row that reddens nothing is a MISS («zero red = MISS»); the control
must survive; every file is restored in `finally` and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_tree_anchor_read_mutations_2026_11_16`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MATH = "kicadstamp/tree_anchor_read.py"
WIRING = "gui/docks/trees_anchor_read.py"

GUARDS = ("tests/trees/test_tree_anchor_read.py "
          "tests/gui/docks/test_trees_anchor_read.py "
          "tests/gui/docks/test_trees_dock.py")

MUTATIONS = [
    ("M1 the delta's sign is flipped", MATH,
     "        return (self.live_mm[0] - self.predicted_mm[0],\n"
     "                self.live_mm[1] - self.predicted_mm[1])\n",
     "        return (self.predicted_mm[0] - self.live_mm[0],  # MUTATION: sign\n"
     "                self.predicted_mm[1] - self.live_mm[1])\n",
     ["test_a_a_moved_origin_gives_the_opposite_shift"]),

    ("M2 the delta is added in the BOARD frame", MATH,
     "    delta = board_offset_to_local_mm(first_delta, anchor_rot_deg)\n",
     "    delta = first_delta  # MUTATION: no frame conversion\n",
     ["test_b_a_turned_anchor_converts_the_delta_into_its_own_frame"]),

    ("M3 children vote as well", WIRING,
     "    for node in tree.nodes:\n",
     "    _voters = list(tree.nodes)  # MUTATION: children vote\n"
     "    _i = 0\n"
     "    while _i < len(_voters):\n"
     "        _voters.extend(_voters[_i].children or [])\n"
     "        _i += 1\n"
     "    for node in _voters:\n",
     ["test_z_children_are_not_asked_and_do_not_vote"]),

    ("M4 the agreement tolerance is a thousand times bigger", MATH,
     "MM_TOLERANCE = 0.005\n",
     "MM_TOLERANCE = 0.005 * 1000  # MUTATION: anything agrees\n",
     ["test_v_nodes_that_did_not_move_together_are_refused"]),

    ("M5 a turned node is not refused", MATH,
     "    turned = [vote for vote in votes\n"
     "              if vote.live_rot_deg is not None\n"
     "              and abs(vote.live_rot_deg - vote.predicted_rot_deg) > DEG_TOLERANCE]\n",
     "    turned = []  # MUTATION: an angle is not checked\n",
     ["test_g_a_turned_node_is_refused_with_its_angle"]),

    ("M6 the button writes a node's xy instead of the shift", WIRING,
     "    form.shift_x_edit.setText(str(round(board_shift[0], 6)))\n"
     "    form.shift_y_edit.setText(str(round(board_shift[1], 6)))\n",
     "    for _node in (form._tree.nodes if form._tree is not None else []):\n"
     "        _node.xy = (outcome.shift[0], outcome.shift[1])  # MUTATION: node xy\n",
     ["test_the_outcome_writes_the_shift_fields_and_never_a_node_xy"]),

    ("M7 cosmetic control (a comment must change nothing)", MATH,
     "    first_delta = deltas[0][1]\n",
     "    first_delta = deltas[0][1]  # MUTATION: cosmetic\n",
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
