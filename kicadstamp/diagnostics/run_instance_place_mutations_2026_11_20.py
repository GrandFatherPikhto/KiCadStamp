# kicadstamp/diagnostics/run_instance_place_mutations_2026_11_20.py
"""Mutation rows for "a tree that loses its last marker keeps the place" — the INSTANCE
branch (Ф4 of plan_2026_11_19_forest_index_keys_and_instances, decision «в» of 19.11).

The contract the rows attack: a generated `tree_instances` copy keeps NOTHING. Its place
lives in the DECLARATION, the dock never writes that section (`_do_save` drops every
generated tree), so a write into the copy would be lost by the next expansion while the
Log claimed success. Nothing is written, `_mark_dirty` is not called, and ONE yellow line
carries the numbers for Tools → Trees → Instances….

  M1 the instance branch is gone (a copy is written like any other tree)
  K  cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once is REFUSED; a
row that reddens nothing is a MISS; the control must survive; every file is restored in `finally`
and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_instance_place_mutations_2026_11_20`
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

KEEP = "gui/docks/trees_keep_place.py"

GUARDS = "tests/gui/docks/test_trees_keep_place.py -k z3"

MUTATIONS = [
    ("M1 a copy is written like any other tree", KEEP,
     "    inst = instance_of(dock, tree)\n"
     "    if inst is not None:\n"
     "        logger.warning(instance_line(plan, inst))\n"
     "        return False\n"
     "    if is_generated(dock, tree):\n"
     "        logger.warning(_generated_gone_line(plan))\n"
     "        return False\n",
     "    inst = None  # MUTATION: no instance branch\n"
     "    if inst is not None:\n"
     "        logger.warning(instance_line(plan, inst))\n"
     "        return False\n"
     "    if False:  # MUTATION: the sticky mark no longer matters\n"
     "        logger.warning(_generated_gone_line(plan))\n"
     "        return False\n",
     ["test_z3_an_instance_keeps_nothing_and_says_the_numbers"]),

    ("K cosmetic control", KEEP,
     "def _write_place(dock, plan: _LostPlace) -> bool:\n",
     "# a comment changes nothing\n"
     "def _write_place(dock, plan: _LostPlace) -> bool:\n",
     []),
]


def _run(guards: str = GUARDS) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               KICAD_API_SOCKET="ipc:///tmp/claude-nonexistent.sock")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *guards.split(), "-x", "-q", "--no-header"],
        cwd=str(ROOT), env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    red = 0
    misses = []
    for row in MUTATIONS:
        name, rel, old, new, expected = row[:5]
        guards = row[5] if len(row) > 5 else GUARDS
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        hits = text.count(old)
        if hits != 1:
            print(f"[SKIP/MISS] {name}: matches {hits} times — template must match exactly once")
            misses.append(name)
            continue
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        try:
            code, out = _run(guards)
        finally:
            path.write_text(text, encoding="utf-8")
            restored = path.read_text(encoding="utf-8") == text
        if not restored:
            print(f"[FAIL] {name}: the file was NOT restored")
            return 2
        if expected:
            failed = [cell for cell in expected if f"FAILED {cell}" not in out
                      and f"::{cell}" not in out]
            if failed or code == 0:
                print(f"[MISS] {name}: nothing went red ({failed or 'whole run green'})")
                misses.append(name)
            else:
                red += 1
                print(f"[red] {name}")
        else:
            if code == 0:
                print(f"[control survives] {name}")
            else:
                print(f"[MISS] {name}: the cosmetic control reddened the run")
                misses.append(name)
    print(f"\nverdict: {red} as expected, {len(misses)} miss(es)")
    for miss in misses:
        print(f"  - {miss}")
    return 0 if not misses else 1


if __name__ == "__main__":
    raise SystemExit(main())
