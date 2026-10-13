# kicadstamp/diagnostics/deepseek_mutations_ch1v6_placement_2026_10_13.py
"""Rule-38 harness for the Ч1в-6 slice (plan_2026_10_08_remove_placer).

WHY THIS SLICE EXISTS (acceptance of Ч1в-5, Claude 12.10): the class А cells of
``tests/placement/test_clone_placement_config.py`` may not simply be deleted in
Ч1 — each was re-checked by Ч1б's rule: "the rule ALSO lives in ``_load_entity``
and NO live cell guards it → translate the cell onto ``entities:`` (class Б);
the rule is clone-only → the cell stays class А".

The MEASUREMENT ran first, over the WHOLE suite
(``KICADSTAMP_MUT_FULL=1``), one ``_load_entity`` rule disabled per row:

  * E1 ignore_selection      → killers: NONE
  * E2 by_selection          → killers: NONE
  * E3 by_selection + nets   → killers: NONE
  * E4 exactly-one-of cell:/imprint: → ``tests/imprint/test_imprint_config.py::
    test_entity_requires_exactly_one_of_cell_and_imprint`` (ALREADY guarded)
  * E5 entity unknown keys   → killers: NONE

So four cells were translated onto the live carriers, and this harness now
verifies the translation did NOT defang them (rule: a translation that leaves
the cell alive is not a translation). E4's row keeps the measurement on record:
the rule is guarded, so the ``without cell`` cell stays class А.

Grown from ``deepseek_mutations_ch1v5_placement_2026_10_12.py`` (rule 38: never
start a harness from a clean sheet) — the same machinery and the SAME
protectors:

  * ``if src.count(old) != 1`` — a template that is not unique refuses to run
    (``_load_clone_placement`` carries near-identical tails, so every template
    below spans the entity-only lines that make it unambiguous);
  * a verdict with ZERO red tests is a MISS, not a kill;
  * restore is from a COPY, never ``git checkout`` (the tree holds this slice's
    uncommitted work).

Rows:
  * E1 — ``_load_entity`` stops carrying ``ignore_selection``.
  * E2 — ``_load_entity`` stops carrying ``by_selection``.
  * E3 — ``_load_entity`` stops refusing ``by_selection: true`` with ``nets``.
  * E4 — ``_load_entity`` stops requiring exactly one of ``cell:``/``imprint:``.
  * E5 — ``_load_entity`` stops checking unknown keys.
  * C1 — a cosmetic comment. MUST survive.

Run from the repo root (one or more row ids; default all):
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_ch1v6_placement_2026_10_13 [E1 ...]
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ENTRIES = ROOT / "kicadstamp/config/entries.py"

CONFIG_TESTS = "tests/placement/test_clone_placement_config.py"
IMPRINT_TESTS = "tests/imprint/test_imprint_config.py"

# The targeted run is the default (fast, and it names the cell that MUST go red).
# The measurement that chose those cells ran the whole suite: set
# KICADSTAMP_MUT_FULL=1 to reproduce it (each row then prints every FAILED cell).
FULL_SUITE = ["--ignore=tests/integration_tests", "-m", "not integration",
              "-n", "auto"]

# Entity-only anchor: `by_selection=by_selection,` followed by `refs=` exists in
# _load_entity ONLY (_load_clone_placement writes refs= BEFORE by_selection=).
_ENTITY_TAIL = (
    "        skip=data.get('skip', False),\n"
    "        ignore_selection=data.get('ignore_selection', False),\n"
    "        by_selection=by_selection,\n"
    "        refs=data.get('refs', {}) or {},\n"
)

# (id, file, old, new, expect, target test node(s))
MUTATIONS = [
    ("E1 ignore_selection ignored", ENTRIES,
     _ENTITY_TAIL,
     "        skip=data.get('skip', False),\n"
     "        ignore_selection=False,  # MUTATION: ignore_selection ignored\n"
     "        by_selection=by_selection,\n"
     "        refs=data.get('refs', {}) or {},\n",
     "die", [f"{CONFIG_TESTS}::test_ignore_selection_defaults_false_and_can_be_set_true"]),
    ("E2 by_selection ignored", ENTRIES,
     "        ignore_selection=data.get('ignore_selection', False),\n"
     "        by_selection=by_selection,\n"
     "        refs=data.get('refs', {}) or {},\n",
     "        ignore_selection=data.get('ignore_selection', False),\n"
     "        by_selection=False,  # MUTATION: by_selection ignored\n"
     "        refs=data.get('refs', {}) or {},\n",
     "die", [f"{CONFIG_TESTS}::test_by_selection_flag"]),
    ("E3 by_selection+nets conflict not refused", ENTRIES,
     "    by_selection = bool(data.get('by_selection', False))\n"
     "    if by_selection and nets:\n"
     "        raise ValidationError(format_fatal_error(\n"
     "            _(\"by_selection: true with non-empty nets in entity {name!r}\").format(name=name),",
     "    by_selection = bool(data.get('by_selection', False))\n"
     "    if False:  # MUTATION: conflict not refused\n"
     "        raise ValidationError(format_fatal_error(\n"
     "            _(\"by_selection: true with non-empty nets in entity {name!r}\").format(name=name),",
     "die", [f"{CONFIG_TESTS}::test_by_selection_and_nets_conflict_raises"]),
    ("E4 exactly-one-of cell/imprint not required (guarded elsewhere)", ENTRIES,
     "    if (cell is None) == (imprint is None):\n"
     "        raise ValidationError(format_fatal_error(\n"
     "            _(\"entity {name!r} needs exactly one of cell:/imprint:\").format(name=name),",
     "    if False:  # MUTATION: content requirement removed\n"
     "        raise ValidationError(format_fatal_error(\n"
     "            _(\"entity {name!r} needs exactly one of cell:/imprint:\").format(name=name),",
     "die", [f"{IMPRINT_TESTS}::test_entity_requires_exactly_one_of_cell_and_imprint"]),
    ("E5 entity unknown keys accepted", ENTRIES,
     "    check_unknown_keys(data, _ENTITY_KNOWN_KEYS,\n"
     "                       _(\"unknown fields in entity {name!r}\").format(name=name))",
     "    if False:  # MUTATION: unknown keys accepted\n"
     "        check_unknown_keys(data, _ENTITY_KNOWN_KEYS,\n"
     "                           _(\"unknown fields in entity {name!r}\").format(name=name))",
     "die", [f"{CONFIG_TESTS}::test_an_unknown_field_in_an_entity_is_refused_on_the_json_path"]),
    ("C1 cosmetic comment (control)", ENTRIES,
     "def _load_entity(data: dict[str, Any]) -> Entity:",
     "def _load_entity(data: dict[str, Any]) -> Entity:  # control",
     "survive", [f"{CONFIG_TESTS}::test_by_selection_flag"]),
]


def _drop_pyc(path: Path) -> None:
    """Drop the bytecode of the MUTATED file — a same-second mtime with stale
    bytecode is the classic rig trap (rule 38)."""
    for cache in path.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{path.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def _run(tests) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    suite = FULL_SUITE if os.environ.get("KICADSTAMP_MUT_FULL") else []
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider",
         "--tb=no", *suite, *tests],
        cwd=str(ROOT), capture_output=True, text=True, env=env)


def _killers(proc: subprocess.CompletedProcess) -> list[str]:
    """Every ``FAILED <nodeid>`` line of the short summary — the cells that went
    red. In the measurement mode (whole suite) this is the whole answer to "does
    ANY live cell guard this rule"; an EMPTY list means nothing does."""
    out: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("FAILED "):
            out.append(line[len("FAILED "):].split(" - ")[0].strip())
    return sorted(set(out))


def main(argv: list[str]) -> int:
    wanted = set(argv) or {m[0].split()[0] for m in MUTATIONS}
    rows = [m for m in MUTATIONS if m[0].split()[0] in wanted]
    if not rows:
        print(f"no rows match: {sorted(wanted)}")
        return 1

    backups: dict[Path, Path] = {}
    for path in {m[1] for m in rows}:
        bak = path.with_suffix(path.suffix + ".mutbak")
        shutil.copy(path, bak)
        backups[path] = bak

    failures: list[str] = []
    try:
        for mid, path, old, new, expect, targets in rows:
            src = path.read_text(encoding="utf-8")
            count = src.count(old)
            if count != 1:
                failures.append(f"{mid}: template count {count} != 1")
                continue
            path.write_text(src.replace(old, new), encoding="utf-8")
            _drop_pyc(path)
            proc = _run(targets)
            red = proc.returncode != 0
            killers = _killers(proc)
            if expect == "die":
                if not red:
                    failures.append(f"{mid}: MISS — no target cell went red")
                elif killers and not set(killers) <= set(targets):
                    failures.append(
                        f"{mid}: an UNEXPECTED cell went red: "
                        f"{sorted(set(killers) - set(targets))}")
                else:
                    print(f"{mid}: KILLED")
            elif expect == "survive":
                if red:
                    failures.append(f"{mid}: control was killed by {killers}")
                else:
                    print(f"{mid}: survived (control)")
            else:  # measurement mode
                print(f"=== {mid} (exit {proc.returncode}) ===")
                print("killers: " + (", ".join(killers) if killers else "NONE"))
            path.write_text(src, encoding="utf-8")
            _drop_pyc(path)
    finally:
        for path, bak in backups.items():
            path.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
            bak.unlink(missing_ok=True)
            _drop_pyc(path)

    if failures:
        print("\n" + "\n".join(failures))
        return 1
    print("all mutations behaved as expected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
