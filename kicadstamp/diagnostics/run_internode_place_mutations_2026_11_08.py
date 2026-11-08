# kicadstamp/diagnostics/run_internode_place_mutations_2026_11_08.py
"""Mutation rows for «cell copper by PLACE» (plan_2026_11_08_internode_cell_copper_by_place).

The defect: the registry key is written only on REDRAW, so everything READ and not yet redrawn
was handed to the inter-node classifier as its copper — measured live on HP-Power-v099, 20 of 83
pieces across dcdc24p15n5 / driver / spoke_signal. The fix adds a read-only second pass; each row
below removes exactly ONE of its four decisions and must turn a NAMED cell red:

  M1  the place pass is gone                                 (internode_cell_copper.tree_cell_copper)
  M2  the net gate is gone — another net on that place is taken  (…_give_by_place)
  M3  the rigidity gate is gone — a non-rigid frame is trusted    (…tree_cell_copper)
  M4  a slot the KEY already answered is asked again by place      (…tree_cell_copper)
  M5  a cosmetic control (a comment must change nothing)

Safeguards, as in every harness of this package (rule 38): a template matching more than once is
REFUSED; a row that reddens nothing is a MISS, not a pass; the control must survive; every file is
restored in `finally` and the restoration is verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_internode_place_mutations_2026_11_08`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CELL_COPPER = "kicadstamp/internode_cell_copper.py"

GUARDS = "tests/net/test_internode_cell_copper.py"

MUTATIONS = [
    ("M1 the place pass is gone", CELL_COPPER,
     "        if unanswered and live.get(\"footprint\"):\n",
     "        if False:  # MUTATION: no second pass — a read cell owns nothing\n",
     ["test_a_keyless_record_gives_its_copper_back_by_place",
      "test_a_dead_key_gives_the_new_piece_at_the_same_place_back"]),

    ("M2 the net gate is gone", CELL_COPPER,
     "            if not same_net(item, rec, expected):\n"
     "                # Another net on that place is NOT this record's copper: it stays\n"
     "                # inter-node, and the next record may still ask about the same piece.\n"
     "                continue\n",
     "",
     ["test_a_piece_on_another_net_at_the_same_place_stays_internode"]),

    ("M3 the rigidity gate is gone", CELL_COPPER,
     "            if frame is None or frame.residual_mm > RIGID_TOLERANCE_MM:\n",
     "            if frame is None:  # MUTATION: a non-rigid frame is trusted\n",
     ["test_a_non_rigid_frame_gives_nothing_by_place_and_says_so_once"]),

    # M4 mutates the guard that is LOAD-BEARING. «A slot the key answered is not asked by
    # place» is protected twice (the subtraction below AND the `taken` set), so removing the
    # subtraction alone is an EQUIVALENT mutant — measured here on 08.11: the keyed-slot cell
    # stayed green. The `taken` set is what a doubled count would slip past, so the row
    # removes THAT, and the cell pins one piece matched by two records.
    ("M4 one piece is counted twice", CELL_COPPER,
     "            if uuid is None or uuid in taken:\n",
     "            if uuid is None:  # MUTATION: the same piece may be counted twice\n",
     ["test_one_piece_matching_two_records_is_given_back_once"]),

    ("M5 cosmetic control (a comment must change nothing)", CELL_COPPER,
     "    out = CellCopperMap()\n",
     "    out = CellCopperMap()  # MUTATION: cosmetic\n",
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
