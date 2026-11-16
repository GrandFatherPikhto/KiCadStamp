# kicadstamp/diagnostics/run_mount_and_record_s1_mutations_2026_11_16.py
"""Mutation rows for §1 of plan_2026_11_14_mount_and_record_may_share_a_name —
the name-based lookups INSIDE one tree.

A mount and a record may carry one name in one tree (16.11.2026); every place
that found a node BY NAME now either takes the node/path it was given or says
which of the two it means. Each row removes exactly ONE such decision:

  M1  `_probe_tree` finds the copied node by NAME again      (trees_node_row)
  M2  a tree-level ref validator lets file ORDER decide       (trees)
  M3  the writer picks the first parent by name again         (config_writer)
  M4  `item_of_ref` returns the first row by name again       (trees_selection)
  M5  a cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once
is REFUSED; a row that reddens nothing is a MISS; the control must survive; every
file is restored in `finally` and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_mount_and_record_s1_mutations_2026_11_16`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

NODE_ROW = "gui/docks/trees_node_row.py"
LOCAL_REFS = "kicadstamp/trees_local_refs.py"
WRITER = "kicadstamp/config_writer.py"
SELECTION = "gui/docks/trees_selection.py"

GUARDS = ("tests/gui/docks/test_mount_and_record_same_name.py "
          "tests/trees tests/config/test_config_format3_writer.py "
          "tests/gui/docks/test_imprint_place_gui.py")

MUTATIONS = [
    ("M1 `_probe_tree` finds the copied node by NAME again", NODE_ROW,
     "    probe_node = node_at_path(probe, node_path(tree, node))\n",
     "    probe_node = _node_by_ref(probe, node.ref)  # MUTATION: by NAME\n",
     ["test_i_editing_the_child_under_the_mount_of_its_own_name"]),

    ("M2 a tree-level ref validator lets file ORDER decide", LOCAL_REFS,
     "        if node.kind not in NAME_ONLY_KINDS:\n"
     "            return node\n"
     "        fallback = fallback or node\n",
     "        return node  # MUTATION: file order decides\n",
     ["test_m_tree_level_refs_name_the_record_in_BOTH_orders"]),

    ("M3 the writer picks the first parent by name again", WRITER,
     "        if len(hits) > 1:\n",
     "        if False:  # MUTATION: pick the first one by file order\n",
     ["test_n_the_writer_refuses_an_ambiguous_parent_and_takes_the_kind"]),

    ("M4 `item_of_ref` returns the first row by name again", SELECTION,
     "            if data.kind not in NAME_ONLY_KINDS:\n"
     "                return item\n"
     "            fallback = fallback if fallback is not None else item\n",
     "            return item  # MUTATION: by NAME\n",
     ["test_o_selecting_the_record_by_name_picks_the_record_row"]),

    ("M5 cosmetic control (a comment must change nothing)", NODE_ROW,
     "    if probe_node is None:\n",
     "    if probe_node is None:  # MUTATION: cosmetic\n",
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
