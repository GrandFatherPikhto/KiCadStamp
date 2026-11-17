# kicadstamp/diagnostics/run_redraw_drift_mutations_2026_11_17.py
"""Mutation rows for the Redraw move guard
(plan_2026_11_16_reread_forest_from_board, commit 2).

The contract the rows attack: a Redraw that would shift parts by more than
`DRIFT_MM` ASKS first, «Cancel» costs zero transactions, and ALL FOUR GUI doors ask
through the ONE function.

  M1 the distance threshold is a thousand times bigger (nothing is ever named)
  M2 the journal stays silent (the measurement is thrown away)
  M3 one door skips the journal (the node/module door)
  M5 the walk is limited to the door's OWN tree (К2-Ф7 comes back)
  M6 a `module` marker is not expanded (its content goes unnamed)
  M7 a door branches on the journal again (К2-Ф9 comes back)
  M4 cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once is
REFUSED; a row that reddens nothing is a MISS; the control must survive; every file is
restored in `finally` and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_redraw_drift_mutations_2026_11_17`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DRIFT = "gui/docks/redraw_drift.py"

DOOR = "gui/docks/trees_accept.py"

GUARDS = "tests/gui/docks/test_redraw_drift.py"

MUTATIONS = [
    ("M1 the distance threshold is a thousand times bigger", DRIFT,
     "DRIFT_MM = 0.1\n",
     "DRIFT_MM = 100.0  # MUTATION: nothing is ever named\n",
     ["test_f7_the_module_door_names_the_content_it_activates"]),

    ("M2 the journal stays silent", DRIFT,
     "    if rows:\n"
     "        logger.warning(_line(rows))\n",
     "    if rows:\n"
     "        pass  # MUTATION: the measurement is thrown away\n",
     ["test_f7_the_module_door_names_the_content_it_activates"]),

    ("M3 the node/module door skips the journal", DOOR,
     "    log_redraw_drift(dock, dock._tree_of_node(node), {node.ref})\n",
     "",
     ["test_f9_the_four_doors_are_still_the_four"]),

    ("M5 the walk is limited to the door's own tree", DRIFT,
     "    index = _index(forest)\n"
     "    rows: list[DriftRow] = []\n",
     "    index = {n.ref: n for n in tree.nodes}  # MUTATION: only this tree\n"
     "    rows: list[DriftRow] = []\n",
     ["test_f7_the_module_door_names_the_content_it_activates"]),

    ("M6 a module marker is not expanded", DRIFT,
     "    forest = _forest(cfg)\n"
     "    wanted = expand_modules(refs, forest)\n"
     "    try:\n",
     "    forest = _forest(cfg)\n"
     "    wanted = set(refs)  # MUTATION: markers not expanded\n"
     "    try:\n",
     ["test_f7_the_module_door_names_the_content_it_activates"]),

    ("M7 a door branches on the journal again", "gui/docks/trees_dock.py",
     "        log_redraw_drift(self, target, selected_refs)\n",
     "        if not log_redraw_drift(self, target, selected_refs):\n"
     "            return  # MUTATION: the door refuses again\n",
     ["test_f9_every_door_calls_the_journal_and_never_branches_on_it"]),

    ("M4 cosmetic control (a comment must change nothing)", DRIFT,
     "DRIFT_MM = 0.1\n",
     "DRIFT_MM = 0.1  # MUTATION: cosmetic\n",
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
                print(f"[MISS] {name}: NO failed test — the mutation kills nothing "
                      f"(exit {code})\n{out[-1200:]}")
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
