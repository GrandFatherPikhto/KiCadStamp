# kicadstamp/diagnostics/deepseek_mutations_u32_2026_10_04.py
"""Rule-28 mutation harness for the У3.2 заход (Р-У3.4, plan §7): the format-3
test STUB must mint on the product seed.

The subject is ``tests/fakes/format3.py`` — a TEST stub the product never
imports — so the file mutated is that one, and the guard is the У3.2 cell in
``tests/config/test_config_format3_migration.py``. Run from the repo root:

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_u32_2026_10_04.py

Grown from ``deepseek_mutations_u31_2026_10_04.py`` (rule 38): the same
machinery — the ``count != 1`` template refusal, restore from a COPY (never
``git checkout``: the tree holds this заход's uncommitted work), a zero-red
verdict reported as a MISS rather than accepted.

Rows:

  * Z1 — the stub mints on its OWN private namespace again (…00ab): the stub and
    the real 2 -> 3 step then disagree about the same record (the plan §7 row
    "заглушка на своём семени").
  * Z2 — the stub mints no folder rows: a stub-built graph would then differ
    from the converter's output (no В39 table).
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
STUB = ROOT / "tests/fakes/format3.py"
BAK = ROOT / "kicadstamp/diagnostics/.u32_stub_backup.py"

TESTS = "tests/config/test_config_format3_migration.py"
GUARD = f"{TESTS}::test_the_format3_stub_seed_is_the_product_seed"

# (id, old, new, expect, target test node(s))
MUTATIONS = [
    ("Z1 the stub mints on its own seed",
     '        return migration_uuid(section, name)',
     '        return str(__import__("uuid").uuid5(__import__("uuid").UUID('
     '"00000000-0000-0000-0000-0000000000ab"), str(n)))',
     "die", [GUARD]),
    ("Z2 the stub mints no folder rows",
     '        if not rows:\n            continue',
     '        if True:\n            continue',
     "die", [GUARD]),
    ("C1 cosmetic comment (control)",
     "def det_uuid(n) -> str:",
     "def det_uuid(n) -> str:  # control",
     "survive", [GUARD]),
]


def _drop_pyc(rel: str) -> None:
    """Drop the bytecode of the MUTATED file — a same-second mtime with stale
    bytecode is the classic rig trap (rule 38)."""
    f = ROOT / rel
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
    shutil.copy(STUB, BAK)
    failures: list[str] = []
    rel = str(STUB.relative_to(ROOT))
    try:
        for mid, old, new, expect, targets in MUTATIONS:
            src = STUB.read_text(encoding="utf-8")
            count = src.count(old)
            if count != 1:
                failures.append(f"{mid}: template count {count} != 1")
                continue
            STUB.write_text(src.replace(old, new), encoding="utf-8")
            _drop_pyc(rel)
            res = _run(targets)
            STUB.write_text(src, encoding="utf-8")
            _drop_pyc(rel)
            # A zero-red verdict is a MUTATION MISS, not a kill (rule 38).
            killed = res.returncode != 0 and "failed" in res.stdout
            verdict = ("KILL" if killed else "MISS ") if expect == "die" \
                else ("SURVIVE" if not killed else "KILL(bad)")
            summary = (res.stdout.strip().splitlines() or ["<no output>"])[-1]
            print(f"{verdict:<9} {mid}  -> {summary}")
            if (expect == "die") != killed:
                failures.append(mid)
    finally:
        shutil.copy(BAK, STUB)
        _drop_pyc(rel)
        BAK.unlink(missing_ok=True)

    print()
    if failures:
        print("PROBLEM (surviving/missed):", *failures, sep="\n  ")
        return 1
    print("all mutations killed; stub restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
