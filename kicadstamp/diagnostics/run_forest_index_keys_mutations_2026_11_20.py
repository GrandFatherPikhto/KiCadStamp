# kicadstamp/diagnostics/run_forest_index_keys_mutations_2026_11_20.py
"""Mutation rows for "the forest index keys LOCAL names by IDENTITY"
(plan_2026_11_19_forest_index_keys_and_instances, Ф3).

The contract the rows attack: `link_trees.forest_index` keys every node by `id(node)` for
the parent/owner/top-level maps, and only a NAME that is unique across the forest (a record
ref, a component vertex's name) stays a string in `by_ref`. A `tree_instances` clone keeps
`mount`/`copper`/`component` names un-suffixed, so a ref-keyed map silently keeps the LAST
clone's node — the live HP-Channel line of 19.11 (46.6 mm).

  M1 the index is keyed by the bare `ref` again (the last clone wins)
  K  cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once is REFUSED; a
row that reddens nothing is a MISS; the control must survive; every file is restored in `finally`
and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_forest_index_keys_mutations_2026_11_20`
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LINK = "kicadstamp/link_trees.py"

GUARDS = "tests/trees/test_tree_position.py -k clone_pair"

MUTATIONS = [
    ("M1 the index is keyed by the bare ref", LINK,
     "            key = id(ln)\n",
     "            key = ln.node.ref  # MUTATION: the bare ref — the last clone wins\n",
     ["test_clone_pair_keeps_each_mounts_own_parent_and_tree"]),

    ("K cosmetic control", LINK,
     "def forest_index(linked_trees) -> ForestIndex:\n",
     "# a comment changes nothing\n"
     "def forest_index(linked_trees) -> ForestIndex:\n",
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
