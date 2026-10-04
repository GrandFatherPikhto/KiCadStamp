# kicadstamp/diagnostics/deepseek_mutations_u31_2026_10_04.py
"""Rule-28 mutation harness for the У3.1 заход (step `_step_2_to_3`, plan §7).

Each row mutates ONE line of the PRODUCT in place, runs the У3.1 cells that must
notice it, and restores the file from a COPY. It never calls `git checkout`: the
working tree holds the (uncommitted) work of this заход, and a checkout would
wipe it. Run from the repo root:

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_u31_2026_10_04.py

Rows (all from plan §7 "Строки мутаций"):

  * M1 — the seed carries the file path          (a cross-file reference dangles)
  * M2 — the reference seed uses a wrong target  (the target table mixed up)
  * M3 — a record that already has a UUID is reseeded
  * M4 — one reference form is dropped           (points)
  * M5 — folder rows are not created
  * M6 — an unnamed record is not named          (В36)
  * M7 — a tree node without a kind is not refused (Р-У3.3)

A surviving mutant is a hole in the cells, not a green light.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FV = ROOT / "kicadstamp/config/format_version.py"
BAK = ROOT / "kicadstamp/diagnostics/.u31_fv_backup.py"

TESTS = "tests/config/test_config_format3_migration.py"

# (id, old, new, target test node(s))
MUTATIONS = [
    ("M1 seed carries the file path",
     '        ref.holder[ref.uuid_field] = migration_uuid(ref.target, hint)',
     '        ref.holder[ref.uuid_field] = migration_uuid(ref.target, f"{ctx.path}|{hint}")',
     [f"{TESTS}::test_a_reference_across_files_resolves_after_both_are_lifted",
      f"{TESTS}::test_every_reference_form_gets_the_target_seed"]),
    ("M2 reference seed target table mixed up",
     '        ref.holder[ref.uuid_field] = migration_uuid(ref.target, hint)',
     '        ref.holder[ref.uuid_field] = migration_uuid("cells", hint)',
     [f"{TESTS}::test_every_reference_form_gets_the_target_seed"]),
    ("M3 a record with a UUID is reseeded",
     '        if uuid or name is None:',
     '        if name is None:',
     [f"{TESTS}::test_a_record_that_already_has_a_uuid_is_never_touched"]),
    ("M4 one reference form dropped (points)",
     '        hint = ref.holder.get(ref.name_field)',
     '        hint = ref.holder.get(ref.name_field)\n'
     '        if ref.target == "points":\n'
     '            continue',
     [f"{TESTS}::test_every_reference_form_gets_the_target_seed"]),
    ("M5 folders are not created",
     '    if rows:',
     '    if False:',
     [f"{TESTS}::test_a_folder_path_gets_the_migration_folder_seed",
      f"{TESTS}::test_one_folder_path_in_two_files_gets_one_uuid"]),
    ("M6 an unnamed record gets no name",
     '        _mint_unnamed(data, section, ctx.path)',
     '        pass',
     [f"{TESTS}::test_an_unnamed_record_is_minted_and_warned",
      f"{TESTS}::test_two_unnamed_records_are_numbered_001_and_002"]),
    ("M7 a node without a kind is not refused",
     '                refuse_step(2, _(\n'
     '                    "the kind of tree node {ref!r} — without it the converter "\n'
     '                    "cannot tell which section its ref points into").format(\n'
     '                        ref=node.get("ref")), ctx.path)',
     '                pass',
     [f"{TESTS}::test_a_tree_node_without_a_kind_is_refused_with_its_place"]),
]


def _run(tests) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--tb=no", *tests],
        cwd=str(ROOT), capture_output=True, text=True, env=env)


def main() -> int:
    shutil.copy(FV, BAK)
    failures: list[str] = []
    try:
        for mid, old, new, targets in MUTATIONS:
            src = FV.read_text(encoding="utf-8")
            count = src.count(old)
            if count != 1:
                failures.append(f"{mid}: template count {count} != 1")
                continue
            FV.write_text(src.replace(old, new), encoding="utf-8")
            res = _run(targets)
            FV.write_text(src, encoding="utf-8")
            # A zero-red verdict is a MUTATION MISS, not a kill (rule 38).
            killed = res.returncode != 0 and "failed" in res.stdout
            summary = (res.stdout.strip().splitlines() or ["<no output>"])[-1]
            print(f"{'KILL' if killed else 'MISS '}  {mid}  -> {summary}")
            if not killed:
                failures.append(mid)
    finally:
        shutil.copy(BAK, FV)
        BAK.unlink(missing_ok=True)

    print()
    if failures:
        print("PROBLEM (surviving/missed):", *failures, sep="\n  ")
        return 1
    print("all mutations killed; product restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
