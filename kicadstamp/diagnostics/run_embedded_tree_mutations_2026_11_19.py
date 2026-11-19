# kicadstamp/diagnostics/run_embedded_tree_mutations_2026_11_19.py
"""Mutation rows for the embedded-tree Redraw
(plan_2026_11_15_embedded_tree_redraw_through_module, Т1-Т6).

The contract the rows attack: a Redraw started on the tab of a tree that a `module` marker places
happens in the MODULE's frame (the whole chain of markers is the selection, only the asked names
are applied), the anchor form is read-only and shows that frame, the loss of the last marker writes
the place into the tree's own anchor, and the READ of a top node measures against the module too.

  M1 the chain stops at the first level (nesting falls back to the host's own anchor)
  M2 the narrowing is dropped (the whole module is applied instead of the selection)
  M3 a door goes back to the ordinary path (the tab lays the tree out from its own anchor)
  M4 two markers are not refused (the tab guesses which place to take)
  M5 the write path no longer refuses a locked anchor (the tree's own anchor is overwritten)
  M6 the kept place loses the owner's turn (a rotated owner lands the content askew)
  M7 the read of a top node falls back to the tree's own anchor (the (−1, +4) mm defect)
  M8 cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once is REFUSED; a
row that reddens nothing is a MISS; the control must survive; every file is restored in `finally`
and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_embedded_tree_mutations_2026_11_19`
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHAIN = "gui/docks/trees_chain.py"
EMBED = "gui/docks/trees_embedding.py"
MODULE_REDRAW = "gui/docks/trees_module_redraw.py"
OWNER_PLACE = "gui/docks/trees_owner_place.py"
KEEP_PLACE = "gui/docks/trees_keep_place.py"
CASCADE = "gui/docks/cascade.py"

GUARDS = "tests/gui/docks/test_trees_embedding.py tests/gui/docks/test_trees_module_redraw.py " \
         "tests/gui/docks/test_trees_owner_place.py tests/gui/docks/test_trees_keep_place.py " \
         "tests/gui/docks/test_cascade.py tests/gui/docks/test_trees_chain.py"

MUTATIONS = [
    ("M1 the chain stops at the first level", EMBED,
     "        chain.append(step)\n"
     "        seen.add(step.owner)\n"
     "        owner = next((t for t in (trees or ()) if t.name == step.owner), None)\n"
     "        if owner is None:\n"
     "            return chain\n"
     "        current = owner\n",
     "        chain.append(step)\n"
     "        return chain  # MUTATION: one level only\n",
     ["test_the_chain_walks_every_level_up_to_the_free_root"]),

    ("M2 the narrowing is dropped", CASCADE,
     "    if only_names is not None:\n"
     "        planned = set(names)\n",
     "    if False:  # MUTATION: the whole module is applied\n"
     "        planned = set(names)\n",
     ["test_only_names_applies_just_the_asked_part_AT_THE_MODULE_PLACE"]),

    ("M3 a door goes back to its own anchor", MODULE_REDRAW,
     "    chain = placement_chain(dock._trees, tree)\n"
     "    if not chain:\n"
     "        return None\n",
     "    chain = placement_chain(dock._trees, tree)\n"
     "    return None  # MUTATION: the ordinary path lays the tree out from its own anchor\n",
     ["test_an_embedded_tree_answers_True_and_hands_over_the_chain"]),

    ("M4 two markers are not refused", MODULE_REDRAW,
     "    if len(found) < 2:\n"
     "        return False\n",
     "    if True:  # MUTATION: the tab guesses which place to take\n"
     "        return False\n",
     ["test_two_markers_refuse_and_name_every_one"]),

    ("M5 a locked anchor form still writes", OWNER_PLACE,
     "    place = getattr(form, \"_owner_place\", None)\n"
     "    if place is None:\n"
     "        return None\n",
     "    place = None  # MUTATION: the tree's own anchor is overwritten\n"
     "    if place is None:\n"
     "        return None\n",
     ["test_k1_the_locked_form_writes_NOTHING_and_the_anchor_survives"]),

    ("M6 the chain stops composing the offsets", CHAIN,
     "        pos = (landed.x / MM, landed.y / MM)\n",
     "        pos = pos  # MUTATION: the marker's offset is not composed\n",
     ["test_m2_the_kept_place_follows_the_whole_chain"]),

    ("M7 the read falls back to the tree's own anchor", OWNER_PLACE,
     "    chain = placement_chain(trees, tree)\n"
     "    if not chain:\n"
     "        return None\n"
     "    by_name = {t.name: t for t in trees}\n",
     "    chain = placement_chain(trees, tree)\n"
     "    return None  # MUTATION: read against the tree's own anchor\n"
     "    by_name = {t.name: t for t in trees}\n",
     ["test_u_the_top_node_of_an_embedded_tree_measures_against_the_MODULE"]),

    ("M9 the place-keeper takes the owner's OWN anchor", KEEP_PLACE,
     "    frame = chain_frame(trees, owner)\n"
     "    if frame is None:\n"
     "        return None\n",
     "    frame = (tuple(getattr(root.anchor, \"shift_xy\", None) or (0.0, 0.0)),\n"
     "             float(getattr(owner, \"rotation\", 0.0) or 0.0))  # MUTATION: Ф1 comes back\n",
     ["test_m2_the_kept_place_follows_the_whole_chain"]),

    ("M8 cosmetic control", EMBED,
     "def module_nodes(tree: Tree) -> list[TreeNode]:\n",
     "# a comment changes nothing\n"
     "def module_nodes(tree: Tree) -> list[TreeNode]:\n",
     []),
]


def _run() -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               KICAD_API_SOCKET="ipc:///tmp/claude-nonexistent.sock")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *GUARDS.split(), "-x", "-q", "--no-header"],
        cwd=str(ROOT), env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    red = 0
    misses = []
    for row in MUTATIONS:
        name, rel, old, new, expected = row
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        hits = text.count(old)
        if hits != 1:
            print(f"[SKIP/MISS] {name}: matches {hits} times — template must match exactly once")
            misses.append(name)
            continue
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        try:
            code, out = _run()
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
