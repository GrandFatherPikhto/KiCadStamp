# kicadstamp/diagnostics/deepseek_mutations_remove_placer_d3_2026_10_13.py
"""Rule-38 harness for Д3 (plan_2026_10_08_remove_placer) — the retired tree-node
kind "clone".

Д3 removed "clone" from FOUR tables: `trees.KINDS`, `trees._PLACEMENT_KINDS`,
`gui/entity/page.py::_PLACEMENT_KINDS` and `format3._F3_NODE_KIND_TARGET`. The
justification is that a kind "clone" node is REFUSED at parse (it lives in
`RETIRED_NODE_KINDS`), so those entries are UNREACHABLE. This harness puts that
claim under measurement: re-adding each entry must be caught by SOMETHING (a cell
that truly covers the table) or SURVIVE (proof of unreachability — the same shape
as the Д1a row). A verdict of "survive" for an entry means no cell guards it,
i.e. it really is dead.

Grown from ``...remove_placer_d2_2026_10_13.py`` (rule 38) — same machinery:
`if src.count(old) != 1` refuses an ambiguous template; restore is from a COPY,
never `git checkout`. TRAP (the Д1 lesson): `count == 1` proves UNIQUENESS, not
that the template is a whole line.

Run from the repo root:
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_remove_placer_d3_2026_10_13
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREES = ROOT / "kicadstamp/trees.py"
PAGE = ROOT / "gui/entity/page.py"
FORMAT3 = ROOT / "kicadstamp/config/format3.py"

FULL_SUITE = ["--ignore=tests/integration_tests", "-m", "not integration",
              "-n", "auto"]

_KINDS = ('KINDS = ("placement", "coordinate", "net_trace", "point",\n'
          '         "external", "module", "mount", "copper", "component")\n')
_PLACEMENT_KINDS = '_PLACEMENT_KINDS = ("placement",)\n'
_PAGE_KINDS = '_PLACEMENT_KINDS = (None, "placement")\n'
_F3_TARGET = ('_F3_NODE_KIND_TARGET = {"placement": "entities",\n'
              '                        "coordinate": "coordinate_placements",\n'
              '                        "net_trace": "net_traces", "point": "points"}\n')

# (id, file, old, new, expect). expect in {"die", "survive"}.
# MEASURED 13.10 (this harness, whole suite each): K1/K2/K3 SURVIVE — restoring
# the dead entry is noticed by NOBODY, which is exactly the "вернули — никто не
# заметил" proof that the three entries are unreachable (a clone node is refused
# at parse). K4 DIES — `_F3_NODE_KIND_TARGET` is a §0 table under rule 35, so the
# two paired tables (test_config_format3_migration, test_form_identity) pin it.
MUTATIONS = [
    ("K1 \"clone\" restored in trees.KINDS — SURVIVES (dead entry)", TREES, _KINDS,
     _KINDS.replace('KINDS = ("placement"', 'KINDS = ("clone", "placement"'),
     "survive"),
    ("K2 \"clone\" restored in trees._PLACEMENT_KINDS — SURVIVES (dead entry)",
     TREES, _PLACEMENT_KINDS,
     '_PLACEMENT_KINDS = ("placement", "clone")\n', "survive"),
    ("K3 \"clone\" restored in gui/entity/page.py::_PLACEMENT_KINDS — SURVIVES "
     "(dead entry)", PAGE,
     _PAGE_KINDS, '_PLACEMENT_KINDS = (None, "placement", "clone")\n', "survive"),
    ("K4 \"clone\" -> clone_placements restored in _F3_NODE_KIND_TARGET — "
     "GUARDED by the rule-35 tables", FORMAT3,
     _F3_TARGET,
     _F3_TARGET.replace('{"placement": "entities",',
                        '{"placement": "entities", "clone": "clone_placements",'),
     "die"),
    ("C1 cosmetic comment (control — must survive)", TREES,
     '_LOCAL_REF_KINDS = ("module", "mount", "copper", "component")\n',
     '_LOCAL_REF_KINDS = ("module", "mount", "copper", "component")  # control\n',
     "survive"),
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
            if expect == "measure":
                print(f"{mid}: {'RED (guarded)' if red else 'SURVIVED (dead)'} "
                      f"(whole suite, exit {proc.returncode})")
                if red:
                    print("    killed by: " + ", ".join(_killers(proc)[:4]))
            elif expect == "die" and not red:
                failures.append(f"{mid}: stayed GREEN — MISS")
            elif expect == "survive" and red:
                failures.append(f"{mid}: went RED ({_killers(proc)[:3]})")
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
