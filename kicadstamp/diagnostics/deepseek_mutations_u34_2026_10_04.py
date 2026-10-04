# kicadstamp/diagnostics/deepseek_mutations_u34_2026_10_04.py
"""Rule-28 / rule-38 mutation harness for the У3.4 заход (plan §7, Б): the
through-cell proves a format-2 graph lifted by the PRODUCT open path keeps the
second apply empty.

Grown from ``deepseek_mutations_u33_2026_10_04.py`` (rule 38): the same
machinery — the ``count != 1`` template refusal, restore from a COPY (never
``git checkout``: the tree holds this заход's uncommitted work), a zero-red
verdict reported as a MISS rather than accepted.

Run from the repo root:

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_u34_2026_10_04.py

Rows (the §7 "Строки мутаций" the through-cell closes):

  * M1 — ``upgrade_graph_on_disk`` removed from ``load_config``: the graph is
    not lifted on open.
  * M2 — ``upgrade_registries_on_disk`` removed from ``load_config``: the copper
    registry stays name-keyed.
  * M3 — the on-disk sweep lifts EVERY file every open (the ``read_version <
    current`` no-op guard dropped): the second open writes.
  * M4 — ``record_key_part`` returns the name under the format-3 gate: the
    second apply wants to create.
  * M5 — the 2 -> 3 step is not registered (``STEPS[2]`` gone): the through-cell
    reddens, not only the step's own cells.
  * M6 — a section dropped from the step's record walk (``_F3_LIST_SECTIONS``
    without ``net_traces``): the through-cell reddens.
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
PY_BIN = sys.executable

LD = ROOT / "kicadstamp/config/loader.py"
UOD = ROOT / "kicadstamp/config/upgrade_on_disk.py"
REG = ROOT / "kicadstamp/registry.py"
FV = ROOT / "kicadstamp/config/format_version.py"
F3 = ROOT / "kicadstamp/config/format3.py"

TF = "tests/config/test_format3_through_real_step.py"
THROUGH = f"{TF}::test_a_format2_graph_lifted_on_open_keeps_the_second_apply_empty"
REOPEN = f"{TF}::test_a_second_open_after_the_lift_writes_nothing"

# (id, file, old, new, expect, target test node(s))
MUTATIONS = [
    ("M1 graph lift removed from load_config",
     LD,
     "    upgrade_graph_on_disk(path)",
     "    pass  # MUTATION",
     "die", [THROUGH]),
    ("M2 registry lift removed from load_config",
     LD,
     "    upgrade_registries_on_disk(path, result[0])",
     "    pass  # MUTATION",
     "die", [THROUGH]),
    ("M3 every open lifts every file (no-op guard dropped)",
     UOD,
     "        if read_version(path) < current_format():",
     "        if True:  # MUTATION",
     "die", [REOPEN]),
    ("M4 registry record key falls back to the name",
     REG,
     "        return uuid",
     "        return name  # MUTATION",
     "die", [THROUGH]),
    ("M5 the 2 -> 3 step is not registered",
     FV,
     "    2: _step_2_to_3,",
     "    # 2: _step_2_to_3,  # MUTATION",
     "die", [THROUGH]),
    ("M6 a section drops out of the step's record walk",
     F3,
     '                     "coordinate_placements", "net_traces", "entities",\n'
     '                     "imprints")',
     '                     "coordinate_placements", "entities",\n'
     '                     "imprints")',
     "die", [THROUGH]),
    ("C1 cosmetic comment (control)",
     LD,
     "def _load_config_uncached(path: str) -> tuple[Config, RuntimeContext]:",
     "def _load_config_uncached(path: str) -> tuple[Config, RuntimeContext]:  # control",
     "survive", [THROUGH]),
]

BACKUP_DIR = ROOT / "kicadstamp/diagnostics/.u34_backups"


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
        [PY_BIN, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--tb=no", *tests],
        cwd=str(ROOT), capture_output=True, text=True, env=env)


def main() -> int:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = {LD, UOD, REG, FV, F3}
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
    print("all mutations killed; product restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
