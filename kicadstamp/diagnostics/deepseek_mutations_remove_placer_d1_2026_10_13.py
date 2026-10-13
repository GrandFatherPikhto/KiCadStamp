# kicadstamp/diagnostics/deepseek_mutations_remove_placer_d1_2026_10_13.py
"""Rule-38 harness for Д1 (plan_2026_10_08_remove_placer, "Приёмка Ч1в-6 + Ч1 + Ч3").

WHAT IS BEING PROVEN. Ч1 made `clone_placements` an EMPTY CARRIER in the loader
(`clone_placements: list = []` — the section is refused while the text is
parsed), which left two loops over it unreachable dead code:

  * the layer/mirror cross-validation (`for cp in clone_placements: … raise …`),
  * `_check_anchor_point` for clone placements.

Claude's acceptance measured that a `raise` inside them survives the WHOLE suite
(U1/U2) — the loops never execute. Д1 removed them; this harness keeps the proof
on record and makes it re-runnable: re-inserting the loop WITH a `raise` must
survive the whole suite, i.e. NOTHING guards it, because nothing reaches it.

That is a "proof of unreachability" row, not a "die" row: the same shape the
spokes b5b harness used for its EQUIVALENT row (rule 38). A die-row verdict with
zero red tests would be a MISS — here a red test would mean the "removed" loop
is actually reachable, which is the opposite finding.

Grown from ``deepseek_mutations_ch1v6_placement_2026_10_13.py`` (rule 38: never
start a harness from a clean sheet) — same machinery, same protectors:
``if src.count(old) != 1`` refuses an ambiguous template; restore is from a COPY,
never ``git checkout``.

Run from the repo root:
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_remove_placer_d1_2026_10_13
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOADER = ROOT / "kicadstamp/config/loader.py"

FULL_SUITE = ["--ignore=tests/integration_tests", "-m", "not integration",
              "-n", "auto"]

_CARRIER = "    clone_placements: list = []\n"

# (id, file, old, new, expect)
MUTATIONS = [
    ("D1a the removed clone loop is unreachable — re-inserting it with a raise survives",
     LOADER,
     _CARRIER,
     _CARRIER +
     "    for _clone_probe in clone_placements:  # MUTATION: reachability probe\n"
     "        raise AssertionError('the retired clone_placements carrier is never filled')\n",
     "survive"),
    ("D1b cosmetic comment (control)", LOADER,
     "logger = logging.getLogger(__name__)",
     "logger = logging.getLogger(__name__)  # control", "survive"),
]

# LESSON (13.10, self-caught while running this harness): the first control
# template was "def load_config(" — a PREFIX of the real line
# `def load_config(path: str) -> tuple[Config, RuntimeContext]:`, so the
# substring replace cut the signature in half and the control went red with a
# SyntaxError inside the imported module (7 cells across two unrelated files).
# `if src.count(old) != 1` proves UNIQUENESS, not that the template is a WHOLE
# line — a control template must span the complete line it edits.


def _drop_pyc(path: Path) -> None:
    for cache in path.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{path.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def _run(tests) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider",
         "--tb=no", *FULL_SUITE, *tests],
        cwd=str(ROOT), capture_output=True, text=True, env=env)


def _killers(proc) -> list[str]:
    return sorted({ln.strip()[len("FAILED "):].split(" - ")[0].strip()
                   for ln in proc.stdout.splitlines() if ln.strip().startswith("FAILED ")})


def main() -> int:
    backups = {}
    for path in {m[1] for m in MUTATIONS}:
        bak = path.with_suffix(path.suffix + ".mutbak")
        shutil.copy(path, bak)
        backups[path] = bak
    failures = []
    try:
        for mid, path, old, new, expect in MUTATIONS:
            src = path.read_text(encoding="utf-8")
            count = src.count(old)
            if count != 1:
                failures.append(f"{mid}: template count {count} != 1 — REFUSED")
                continue
            path.write_text(src.replace(old, new), encoding="utf-8")
            _drop_pyc(path)
            proc = _run([])
            red = proc.returncode != 0
            if expect == "survive" and red:
                failures.append(f"{mid}: went RED ({_killers(proc)[:3]}) — the "
                                f"probe found a live guard, the finding changes")
            else:
                print(f"{mid}: {'survived' if not red else 'RED'} "
                      f"(whole suite, exit {proc.returncode})")
            path.write_text(src, encoding="utf-8")
            _drop_pyc(path)
    finally:
        for path, bak in backups.items():
            path.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
            bak.unlink(missing_ok=True)
            _drop_pyc(path)
    if failures:
        print("\n".join(failures))
        return 1
    print("all mutations behaved as expected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
