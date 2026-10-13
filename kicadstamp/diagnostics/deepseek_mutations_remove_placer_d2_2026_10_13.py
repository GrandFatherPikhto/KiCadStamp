# kicadstamp/diagnostics/deepseek_mutations_remove_placer_d2_2026_10_13.py
"""Rule-38 harness for Д2 (plan_2026_10_08_remove_placer, "Приёмка Д1") — the
LEGACY-PREFIX protection, which is what the ОСТАНОВКА clause of Д2 is about.

Д2 removed the `anchor:` / `role:` / `point:` branches of `clone_anchor_id` and
the matching duplicate blocks of `check_no_duplicate_clone_anchors`: no PRODUCT
path builds a clone carrying `anchor_*`, so those forms could no longer be
produced. The prefixes THEMSELVES stay in `registry.PROTECTED_ANCHOR_PREFIXES`
(a registry ON DISK may hold such keys; `registry_upgrade._map_anchor_id` reads
them) — Denis, 12.10.

The plan's condition: "если разбор покажет, что защита этих префиксов держится
ТОЛЬКО на снимаемых клетках — ОСТАНОВКА с картой." The old killers
(`tests/placement/test_registry_anchor_prefix_protection.py`) were TRANSLATED
onto LITERAL keys, so this harness proves the protection is guarded WITHOUT any
builder — hence NO ОСТАНОВКА is needed:

  * P1 / P2 / P3 — dropping `point:` / `anchor:` / `role:` from
    `PROTECTED_ANCHOR_PREFIXES` must go RED, killed by the LITERAL-key cells
    (the builder is gone; the literal key is the guard). A GREEN verdict here
    would be the ОСТАНОВКА condition: the legacy protection would be unguarded.
  * C1 — a control that MUST die (renaming the live `name:` prefix), so the
    harness is known to be able to detect a genuine break in the same family.
  * C2 — a cosmetic control that MUST survive.

Grown from ``...remove_placer_d1_2026_10_13.py`` (rule 38: never start a harness
from a clean sheet) — same machinery, same protectors: ``if src.count(old) != 1``
refuses an ambiguous template; restore is from a COPY, never ``git checkout``.
TRAP (the Д1 lesson): ``count == 1`` proves UNIQUENESS, not that the template is a
whole line — a template must span the complete line it edits.

Run from the repo root:
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_remove_placer_d2_2026_10_13
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "kicadstamp/registry.py"
CPC = ROOT / "kicadstamp/placement/services/clone_position_calculator.py"

FULL_SUITE = ["--ignore=tests/integration_tests", "-m", "not integration",
              "-n", "auto"]

# The whole statement, so removing one entry leaves valid code and the template
# stays a WHOLE line (the Д1 lesson).
_TUPLE = ('PROTECTED_ANCHOR_PREFIXES = ("anchor:", "role:", "name:", "point:",\n'
          '                             "thermal:", "pad:", "net:")\n')

_NAME_RETURN = ('    return f"name:{record_key_part(clone_placement_effective_name('
                'clone), getattr(clone, \'uuid\', None))}"')

# (id, file, old, new, expect)
MUTATIONS = [
    ("P1 `point:` dropped from PROTECTED_ANCHOR_PREFIXES — the LITERAL-key cells "
     "must still catch it (no builder needed)", REG, _TUPLE,
     'PROTECTED_ANCHOR_PREFIXES = ("anchor:", "role:", "name:",\n'
     '                             "thermal:", "pad:", "net:")\n', "die"),
    ("P2 `anchor:` dropped — literal keys must catch it", REG, _TUPLE,
     'PROTECTED_ANCHOR_PREFIXES = ("role:", "name:", "point:",\n'
     '                             "thermal:", "pad:", "net:")\n', "die"),
    ("P3 `role:` dropped — literal keys must catch it", REG, _TUPLE,
     'PROTECTED_ANCHOR_PREFIXES = ("anchor:", "name:", "point:",\n'
     '                             "thermal:", "pad:", "net:")\n', "die"),
    ("C1 the LIVE name: prefix renamed (control — must be caught)",
     CPC, _NAME_RETURN, _NAME_RETURN.replace('f"name:', 'f"nqme:'), "die"),
    ("C2 cosmetic comment (control — must survive)", CPC,
     "logger = logging.getLogger(__name__)",
     "logger = logging.getLogger(__name__)  # control", "survive"),
]


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
            if expect == "die" and not red:
                failures.append(f"{mid}: stayed GREEN — MISS (the guard this "
                                f"mutation removes is not covered)")
            elif expect == "survive" and red:
                failures.append(f"{mid}: went RED ({_killers(proc)[:3]}) — the "
                                f"probe found a live guard, the finding changes")
            else:
                print(f"{mid}: {'RED' if red else 'survived'} "
                      f"(whole suite, exit {proc.returncode})")
                if red:
                    print("    killed by: " + ", ".join(_killers(proc)[:4]))
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
