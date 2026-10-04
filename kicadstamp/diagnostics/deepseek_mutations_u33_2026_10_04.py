# kicadstamp/diagnostics/deepseek_mutations_u33_2026_10_04.py
"""Rule-28 / rule-38 mutation harness for the У3.3 заход (Р-У3.5, plan §7):
refuse the on-disk lift on a failed `.bak` (the Syncthing half was withdrawn).

Grown from ``deepseek_mutations_u32_2026_10_04.py`` (rule 38): the same
machinery — the ``count != 1`` template refusal, restore from a COPY (never
``git checkout``: the tree holds this заход's uncommitted work), a zero-red
verdict reported as a MISS rather than accepted. Unlike У3.2, the subject is
the PRODUCT (two files), so the rig mutates one file per row.

Run from the repo root:

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_u33_2026_10_04.py

Rows (the §7 "Строки мутаций" closed here):

  * M3 — a `.bak` failure in the graph sweep is log-and-continue again
    (".bak-сбой не отказ", config side).
  * M4 — the same in the registry sweep.
  * M7 — the format-3 stub's folder walk skips LIST sections (Z2b).
  * M8 — a section is dropped from the stub's own section table.
  * C1 — a cosmetic comment edit. MUST survive.

A surviving mutant is a hole in the cells, not a green light.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

UOD = ROOT / "kicadstamp/config/upgrade_on_disk.py"
REG = ROOT / "kicadstamp/config/registry_upgrade.py"
STUB = ROOT / "tests/fakes/format3.py"

TESTS_CFG = "tests/config/test_config_format_upgrade_on_disk.py"
TESTS_REG = "tests/placement/test_registry_upgrade_on_disk.py"
TESTS_MIG = "tests/config/test_config_format3_migration.py"

# (id, file, old, new, expect, target test node(s))
MUTATIONS = [
    ("M3 graph .bak failure is log-and-continue again",
     UOD,
     '            raise ValidationError(format_fatal_error(\n'
     '                _("config file {path}: the previous version could not be saved as a backup ({error}) — the format upgrade is refused and the file is left as it is").format(path=path, error=e),\n'
     '                [_("check the permissions and the free space of the directory, then open the profile again")])) from e',
     '            logger.error(_("config file {path}: the format upgrade failed ({error}) — the file is left as it is; the next open will try again").format(path=path, error=e))\n'
     '            continue',
     "die",
     [f"{TESTS_CFG}::test_a_backup_that_cannot_be_taken_refuses_the_lift_and_leaves_the_file"]),
    ("M4 registry .bak failure is log-and-continue again",
     REG,
     '            raise ValueError(\n'
     '                "registry {path}: the previous version could not be saved as a "\n'
     '                "backup ({error}) — the schema upgrade is refused and the file "\n'
     '                "is left as it is".format(path=path, error=e)) from e',
     '            logger.error(\n'
     '                "registry {path}: the schema upgrade failed ({error}) — the file "\n'
     '                "is left as it is; the next open will try again"\n'
     '                .format(path=path, error=e))\n'
     '            continue',
     "die",
     [f"{TESTS_REG}::test_a_registry_backup_that_cannot_be_taken_refuses_the_lift"]),
    ("M7 the stub folder walk skips LIST sections",
     STUB,
     '                for prefix in _folder_prefixes(rec.get("name") or ""):',
     '                for prefix in _folder_prefixes(""):',
     "die",
     [f"{TESTS_MIG}::test_the_format3_stub_mints_folders_for_list_sections_too"]),
    ("M8 a section is dropped from the stub table",
     STUB,
     '                  "coordinate_placements", "net_traces", "entities", "imprints")',
     '                  "coordinate_placements", "net_traces", "imprints")',
     "die",
     [f"{TESTS_MIG}::test_the_format3_stub_section_tables_match_the_product"]),
    ("C1 cosmetic comment (control)",
     UOD,
     "def _graph_files(root: str | Path) -> list[Path]:",
     "def _graph_files(root: str | Path) -> list[Path]:  # control",
     "survive",
     [f"{TESTS_CFG}::test_a_repeat_open_writes_nothing_and_parses_nothing_for_the_number"]),
]

BACKUP_DIR = ROOT / "kicadstamp/diagnostics/.u33_backups"


def _drop_pyc(f: Path) -> None:
    """Drop the bytecode of the MUTATED file — a same-second mtime with stale
    bytecode is the classic rig trap (rule 38)."""
    for cache in f.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{f.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def _run(tests) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--tb=no", *tests],
        cwd=str(ROOT), capture_output=True, text=True, env=env)


def main() -> int:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = {UOD, REG, STUB}
    backups = {f: BACKUP_DIR / f.name for f in files}
    for f, b in backups.items():
        shutil.copy(f, b)
    failures: list[str] = []
    try:
        for mid, target, old, new, expect, tests in MUTATIONS:
            src = target.read_text(encoding="utf-8")
            count = src.count(old)
            if count != 1:
                failures.append(f"{mid}: template count {count} != 1")
                continue
            target.write_text(src.replace(old, new), encoding="utf-8")
            _drop_pyc(target)
            res = _run(tests)
            target.write_text(src, encoding="utf-8")
            _drop_pyc(target)
            # A zero-red verdict is a MUTATION MISS, not a kill (rule 38).
            killed = res.returncode != 0 and "failed" in res.stdout
            verdict = ("KILL" if killed else "MISS ") if expect == "die" \
                else ("SURVIVE" if not killed else "KILL(bad)")
            summary = (res.stdout.strip().splitlines() or ["<no output>"])[-1]
            print(f"{verdict:<9} {mid}  -> {summary}")
            if (expect == "die") != killed:
                failures.append(mid)
    finally:
        for f, b in backups.items():
            shutil.copy(b, f)
            _drop_pyc(f)
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)

    print()
    if failures:
        print("PROBLEM (surviving/missed):", *failures, sep="\n  ")
        return 1
    print("all mutations killed; product and stub restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
