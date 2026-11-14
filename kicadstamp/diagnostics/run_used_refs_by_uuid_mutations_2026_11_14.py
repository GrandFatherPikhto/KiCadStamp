# kicadstamp/diagnostics/run_used_refs_by_uuid_mutations_2026_11_14.py
"""Mutation rows for «"used" records in the node window — no mounts, and by UUID»
(plan_2026_11_14_used_refs_by_uuid).

The defect (live, 14.11): in tree `fpga` the mounts `fpga_vccint_5`, … carry the same
names as the ENTITIES of cell `fpga_spoke`, so every one of those entities was greyed in
the node form and could not be picked. Two causes: the GUI kept its OWN copy of the
local-kind list and it was missing "mount"; occupancy was compared by BARE NAME instead
of the record's uuid. Each row removes exactly ONE decision and must turn a NAMED cell
red:

  M1  "mount" is not a local kind again                         (trees.LOCAL_REF_KINDS)
  M2  the GUI keeps its own copy of that list again             (trees_node_form)
  M3  the record's uuid is ignored — comparison by name again   (UsedRefs.place)
  M4  a node without a uuid holds its name in EVERY section     (used_ref_index)
  M5  the local kinds are counted by the auto-numbering again   (used_ref_index)
  M6  a cosmetic control (a comment must change nothing)

and §1 (acceptance of f5b02f80 — ONE tree, both directions of the local-name rule):

  M7  the FORWARD direction is gone (a record named like a local node, trees_local_refs)
  M8  the greyed row loses its OWN hint and gets the generic one  (trees_node_form)
  M9  the REVERSE direction is gone (a mount renamed onto a positioned node)
  M10 a second cosmetic control (a comment must change nothing)

Safeguards, as in every harness of this package (rule 38): a template matching more than
once is REFUSED; a row that reddens nothing is a MISS, not a pass; the control must
survive; every file is restored in `finally` and the restoration is verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_used_refs_by_uuid_mutations_2026_11_14`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TREES = "kicadstamp/trees.py"
LOCAL_REFS = "kicadstamp/trees_local_refs.py"
FORM = "gui/docks/trees_node_form.py"

# Cells а–ж + г2 ride on the new suite; the older "used refs" cells (the Ч3 ones that
# already lived in the dock suite) must stay green too, so both files are guarded.
GUARDS = ("tests/gui/docks/test_trees_used_refs_by_uuid.py "
          "tests/gui/docks/test_trees_dock.py")

MUTATIONS = [
    ("M1 a mount is not a local kind again", TREES,
     'LOCAL_REF_KINDS = ("module", "mount", "copper", "component")\n',
     'LOCAL_REF_KINDS = ("module", "copper", "component")  # MUTATION\n',
     ["test_a_a_mount_and_an_entity_of_one_name_do_not_block_each_other",
      "test_e_unique_ref_does_not_renumber_beside_a_mount",
      "test_zh_the_local_kind_list_has_one_owner"]),

    ("M2 the GUI keeps its own copy of the local-kind list", FORM,
     "LOCAL_REF_KINDS = _LOADER_LOCAL_REF_KINDS\n",
     'LOCAL_REF_KINDS = ("module", "copper", "component")  # MUTATION: own copy\n',
     ["test_a_a_mount_and_an_entity_of_one_name_do_not_block_each_other",
      "test_zh_the_local_kind_list_has_one_owner"]),

    ("M3 the record's uuid is ignored (comparison by name)", FORM,
     "        uuid = record[1] if record is not None else None\n",
     "        uuid = None  # MUTATION: the record's uuid is ignored\n",
     ["test_v_a_real_repeat_of_the_same_record_stays_greyed_and_refused"]),

    ("M4 a node without a uuid holds its name in EVERY section", FORM,
     "                elif node.kind is None:\n",
     "                elif True:  # MUTATION: every section holds that name\n",
     ["test_g_an_old_node_without_a_uuid_holds_its_own_section_only"]),

    ("M5 the local kinds are counted by the auto-numbering again", FORM,
     "            if node.kind not in LOCAL_REF_KINDS:\n",
     "            if True:  # MUTATION: a mount's name counts as taken\n",
     ["test_e_unique_ref_does_not_renumber_beside_a_mount",
      "test_a_a_mount_and_an_entity_of_one_name_do_not_block_each_other"]),

    ("M6 cosmetic control (a comment must change nothing)", FORM,
     "        by_name.setdefault(node.ref, place)\n",
     "        by_name.setdefault(node.ref, place)  # MUTATION: cosmetic\n",
     "survive"),

    # ── §1: the target tree's local names, in BOTH directions ──────────────
    ("M7 the FORWARD direction is gone (a record named like a local node)",
     LOCAL_REFS,
     "    kind = local_ref_kind(nodes, ref)\n"
     "    if kind is None:\n",
     "    kind = None  # MUTATION: the forward direction is gone\n"
     "    if kind is None:\n",
     ["test_z_a_record_named_like_a_local_node_of_the_SAME_tree_is_refused"]),

    ("M8 the greyed row loses its OWN hint", FORM,
     "        text = used_ref_text(*place) if place is not None else \\\n"
     "            local_ref_text(holder, name)\n",
     "        text = (used_ref_text(*place) if place is not None\n"
     "                else \"MUTATION: no own hint\")  # MUTATION\n",
     ["test_z_a_record_named_like_a_local_node_of_the_SAME_tree_is_refused"]),

    ("M9 the REVERSE direction is gone (a mount renamed onto a positioned node)",
     LOCAL_REFS,
     "    if any(n is not own and n.kind != kind and n.ref == ref for n in _walk(nodes)):\n",
     "    if False:  # MUTATION: the reverse direction is gone\n",
     ["test_m_a_local_node_renamed_onto_a_positioned_node_is_refused"]),

    ("M10 cosmetic control (a comment must change nothing)", LOCAL_REFS,
     "    not_unique, collides = pair\n",
     "    not_unique, collides = pair  # MUTATION: cosmetic\n",
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
