# kicadstamp/diagnostics/run_mount_and_record_same_name_mutations_2026_11_16.py
"""Mutation rows for plan_2026_11_14_mount_and_record_may_share_a_name.

A mount is a point of reference and a positioned node PLACES a record, so the two
are told apart by kind and uuid rather than by name (Denis, 14.11). Each row
removes exactly ONE decision of that change and must turn a NAMED cell red:

  M1  the row registry is keyed by the node's NAME again   (trees_dock)
  M2  a local name goes back into `selected_refs`          (trees_accept)
  M3  the loader forbids the pair for a `mount` again      (trees_local_refs)
  M4  the form greys a MOUNT's name again                  (trees_node_form)
  M5  duplicate local names are not counted                (trees_local_refs)
  M6  a checked row hands in only its OWN ref              (trees_node_row)
  M7  a cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once
is REFUSED; a row that reddens nothing is a MISS; the control must survive; every
file is restored in `finally` and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_mount_and_record_same_name_mutations_2026_11_16`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DOCK = "gui/docks/trees_dock.py"
NODE_ROW = "gui/docks/trees_node_row.py"
ACCEPT = "gui/docks/trees_accept.py"
FORM = "gui/docks/trees_node_form.py"
LOCAL_REFS = "kicadstamp/trees_local_refs.py"

GUARDS = ("tests/gui/docks/test_mount_and_record_same_name.py "
          "tests/gui/docks/test_trees_used_refs_by_uuid.py "
          "tests/trees tests/net/test_extra_copper_s0.py")

MUTATIONS = [
    ("M1 the row registry is keyed by the node's NAME again", DOCK,
     "        self._node_items[id(node)] = (node, item)\n",
     "        self._node_items[node.ref] = (node, item)  # MUTATION\n",
     ["test_g1_two_trees_one_name_give_one_key_per_node",
      "test_g2_a_checked_mount_gives_the_records_of_its_subtree"]),

    # Cell б cannot witness this one ON PURPOSE: the mount and its child share the
    # very same name there, so the set reads `{"X"}` either way. г2 (a mount `X`
    # with a child `CH`) and з (an empty subtree) are the cells that can.
    ("M2 a local name goes back into `selected_refs`", ACCEPT,
     "        if current.ref and current.kind not in NAME_ONLY_KINDS:\n",
     "        if current.ref:  # MUTATION: a mount's name is a record again\n",
     ["test_g2_a_checked_mount_gives_the_records_of_its_subtree",
      "test_z_redraw_sets_carry_no_local_names"]),

    ("M3 the loader forbids the pair for a `mount` again", LOCAL_REFS,
     "        None,   # a mount MAY share its name with a positioned node (16.11.2026)\n",
     "        _(\"tree {tree!r}: mount node ref(s) {refs} collide with a \"\n"
     "          \"positioned node of the same tree\"),  # MUTATION\n",
     ["test_a_the_pair_in_one_tree_loads_both_parsers",
      "test_z2_a_record_named_like_a_MOUNT_of_the_same_tree_is_offered"]),

    ("M4 the form greys a MOUNT's name again", FORM,
     "        if place is None and local is not None and local.refusal(data[0], name):\n",
     "        if place is None and local is not None:  # MUTATION: grey without asking\n",
     ["test_z2_a_record_named_like_a_MOUNT_of_the_same_tree_is_offered"]),

    ("M5 duplicate local names are not counted", LOCAL_REFS,
     "                counts[n.ref] = counts.get(n.ref, 0) + 1\n",
     "                counts[n.ref] = 1  # MUTATION: a duplicate is invisible\n",
     ["test_d_two_mounts_of_one_name_are_still_refused"]),

    ("M6 a checked row hands in only its OWN ref", NODE_ROW,
     "            refs.update(subtree_refs(node))\n",
     "            refs.update({node.ref})  # MUTATION: no subtree, local names in\n",
     ["test_g2_a_checked_mount_gives_the_records_of_its_subtree"]),

    ("M7 cosmetic control (a comment must change nothing)", DOCK,
     "        selected_refs = checked_record_refs(self)\n",
     "        selected_refs = checked_record_refs(self)  # MUTATION: cosmetic\n",
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
