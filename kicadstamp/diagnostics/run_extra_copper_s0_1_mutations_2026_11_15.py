# kicadstamp/diagnostics/run_extra_copper_s0_1_mutations_2026_11_15.py
"""Mutation rows for §0.1 of plan_2026_11_10_extra_copper_by_selection — the two
holes Denis proved in the acceptance of `38bff313`.

Each row removes exactly ONE decision and must turn a NAMED cell red:

  M2b  the «touches the selection» test is set-vs-set again  (internode_capture)
  M8   the Reread door no longer passes `taken`              (internode_capture)
  M9   the node door no longer passes `taken`                (trees_dock)
  M10  the write no longer refuses a taken name              (internode_capture)
  M11  a cosmetic control (a comment must change nothing)

Rows M2b and M8/M10 live in the same file on purpose: they are the two halves of
one question — «may this name stand here?» — asked before the write and again at
the write. The old rig (`run_extra_copper_s0_mutations_2026_11_14`) keeps the §0
rows; its M2/M6 templates were updated in this same commit, because §0.1 changed
the lines they patch.

Safeguards as in every harness here (rule 38): a template matching more than once
is REFUSED; a row that reddens nothing is a MISS; the control must survive; every
file is restored in `finally` and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_extra_copper_s0_1_mutations_2026_11_15`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CAPTURE = "kicadstamp/internode_capture.py"
DOCK = "gui/docks/trees_dock.py"

GUARDS = ("tests/net/test_extra_copper_s0.py "
          "tests/gui/docks/test_trees_extra_copper_s0.py "
          "tests/net/test_internode_capture.py")

MUTATIONS = [
    ("M2b the «touches the selection» test is set-vs-set again", CAPTURE,
     "        if narrowed and not any(sig & selection_pads for sig in candidates):\n",
     "        if narrowed and not (candidates & selection_pads):  # MUTATION\n",
     ["test_0b2_a_record_the_selection_TOUCHES_is_still_missing"]),

    ("M8 the Reread door does not pass `taken`", CAPTURE,
     "            free = free_node_ref(\"net_trace\", tree.name, record.name, tree.nodes,\n"
     "                                 taken=existing_names)\n",
     "            free = free_node_ref(\"net_trace\", tree.name, record.name,  # MUTATION\n"
     "                                 tree.nodes)\n",
     ["test_06v_a_new_record_skips_a_record_name_the_config_already_has"]),

    ("M9 the node door does not pass `taken`", DOCK,
     "                node.ref = free_node_ref(node.kind, tree.name, node.ref,\n"
     "                                         tree.nodes, taken=self._used_refs(),\n"
     "                                         start=1)\n",
     "                node.ref = free_node_ref(node.kind, tree.name, node.ref,  # MUTATION\n"
     "                                         tree.nodes, start=1)\n",
     ["test_the_auto_number_avoids_a_name_a_node_already_holds",
      "test_the_auto_number_skips_a_local_name_of_the_same_tree"]),

    ("M10 the write no longer refuses a taken name", CAPTURE,
     "        if c.identity in present:\n",
     "        if False:  # MUTATION: the record is dropped in silence again\n",
     ["test_06d_apply_refuses_to_drop_an_added_record_under_a_taken_name"]),

    ("M11 cosmetic control (a comment must change nothing)", CAPTURE,
     "            plan.not_in_selection.append(identity)\n",
     "            plan.not_in_selection.append(identity)  # MUTATION: cosmetic\n",
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
