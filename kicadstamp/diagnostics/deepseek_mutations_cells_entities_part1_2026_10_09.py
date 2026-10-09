# kicadstamp/diagnostics/deepseek_mutations_cells_entities_part1_2026_10_09.py
"""Acceptance mutations for plan_2026_10_09_cells_and_entities.md, ЧАСТЬ 1
(«подходят ли экземпляр и ячейка» + «Add entities…»), 2026-10-09.

Grown from deepseek_mutations_refresh_mixed_2026_10_05.py (rule 38): the SAME
machinery — basename-resolved tests under tests/, `_drop_pyc` for the mutated
file, the `original.count(old) != 1` refusal (a non-unique template is a MISS,
not a kill), a verdict of «ПРОМАХ» when nothing red came back, and a control
that MUST survive.

WHAT IS BEING PROVEN. The guards are tests/gui/docks/test_instance_candidates.py
(the pure rule), tests/gui/docks/test_add_entities_dialog.py (the table) and
tests/gui/docks/test_add_entities_menu.py (the menu, the handler, the door).

  * M1  нижняя граница снята (rôle shortfall not checked)   -> missing role kept
  * M2  кратность не проверяется (excess not checked)       -> excess kept
  * M3  лишняя роль запрещена (extra role flagged)          -> extra role refused
  * M4  занятые не помечены                                 -> taken not named
  * M5  кластер строго (segment match -> substring)         -> A/B swallows A/B2
  * M6  запись по одной правке на строку                    -> N write_data calls
  * M7  диалог до пересборки снимка (дверь обойдена)        -> dialog opened early
  * M8  занятые помечены (checkbox on a taken row)          -> taken selectable
  * M9  конфликт имени не блокирует OK                      -> OK always enabled
  * M10 неполные не считаются (counter silenced)            -> "K lack roles" lost
  * K1  cosmetic comment                                    -> MUST survive

Run with the main checkout's interpreter; point it at another tree with
`KICADSTAMP_ACCEPT_ROOT`. An optional row-name prefix filter takes the rest of
argv.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("KICADSTAMP_ACCEPT_ROOT", "."))


def _interpreter() -> str:
    local = ROOT / ".venv" / "bin" / "python"
    if local.exists():
        return str(local)
    env = os.environ.get("KICADSTAMP_PYTHON")
    return env if env else sys.executable


PY_BIN = _interpreter()

PURE = ["test_instance_candidates.py"]
DIALOG = ["test_add_entities_dialog.py"]
MENU = ["test_add_entities_menu.py"]

CAND = "gui/docks/instance_candidates.py"
DIALOG_MOD = "gui/docks/add_entities.py"
FLOW = "gui/docks/add_entities_flow.py"

MUTATIONS = [
    ("M1 нижняя граница снята (shortfall)", CAND,
     "        if got < want:",
     "        if False:  # MUTATION",
     "die", PURE, ()),
    ("M2 кратность не проверяется (excess)", CAND,
     "        if got > want:",
     "        if False:  # MUTATION",
     "die", PURE, ()),
    ("M3 лишняя роль запрещена", CAND,
     "    for role in sorted(cell_roles):",
     "    for role in sorted(set(cell_roles) | set(instance_roles)):  # MUTATION",
     "die", PURE, ()),
    ("M4 занятые не помечены", CAND,
     "        entity_name = taken(cluster, sheet) if taken is not None else None",
     "        entity_name = None  # MUTATION",
     "die", DIALOG + MENU, ()),
    ("M5 кластер строго (segment -> substring)", CAND,
     "               if p.cluster and cluster_prefix_match(p.cluster, cluster)]",
     "               if p.cluster and (cluster.lower() in p.cluster.lower())]  # MUTATION",
     "die", PURE, ()),
    ("M6 запись по одной правке на строку", FLOW,
     "    items.extend(dict(e) for e in entries)\n    write_data(path, data)",
     "    for _e in entries:  # MUTATION\n"
     "        items.append(dict(_e))\n"
     "        write_data(path, data)",
     "die", MENU, ()),
    ("M7 диалог до пересборки снимка (дверь)", FLOW,
     "    hub.refresh_snapshot_and_push(on_ready=_open)",
     "    _open()  # MUTATION",
     "die", MENU, ()),
    ("M8 занятые помечены (checkbox on taken)", DIALOG_MOD,
     "        if cand.taken:\n"
     "            # Not checkable: it is offered only so the user SEES it is taken.\n"
     "            item.setFlags(Qt.ItemFlag.ItemIsEnabled)\n",
     "        if cand.taken:\n"
     "            # Not checkable: it is offered only so the user SEES it is taken.\n"
     "            item.setFlags(Qt.ItemFlag.ItemIsEnabled"
     " | Qt.ItemFlag.ItemIsUserCheckable)  # MUTATION\n",
     "die", DIALOG, ()),
    ("M9 конфликт имени не блокирует OK", DIALOG_MOD,
     "        ok.setEnabled(checked_any and not problems)",
     "        ok.setEnabled(True)  # MUTATION",
     "die", DIALOG, ()),
    ("M10 неполные не считаются (counter)", DIALOG_MOD,
     "        if self._skipped:\n"
     "            self._counter.setText(_(\"{count} instances lack roles\").format(\n"
     "                count=len(self._skipped)))\n"
     "        else:\n"
     "            self._counter.setText(\"\")",
     "        self._counter.setText(\"\")  # MUTATION",
     "die", DIALOG, ()),
    ("K1 cosmetic comment (control)", CAND,
     "    out: list[InstanceCandidate] = []",
     "    out: list[InstanceCandidate] = []  # control",
     "survive", PURE, ()),
]


def _resolve_tests(basenames):
    """[repo-relative paths] for `basenames`, resolved under ROOT/tests.

    Refuses (SystemExit) when a name is not found, or is found TWICE (rule 38)."""
    found, problems = [], []
    for name in basenames:
        matches = sorted((ROOT / "tests").rglob(name))
        if len(matches) == 1:
            found.append(matches[0].relative_to(ROOT).as_posix())
        else:
            problems.append(f"{name}: {len(matches)} match(es)")
    if problems:
        raise SystemExit(
            "the acceptance rig cannot resolve its test list under tests/ — "
            "refusing to run with a blind or ambiguous T: " + "; ".join(problems))
    return found


def _drop_pyc(rel: str) -> None:
    """Drop the bytecode of the MUTATED file (the stale-.pyc trap, rule 38)."""
    f = ROOT / rel
    for cache in f.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{f.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def run(paths, extra=()):
    """(returncode, output); returncode None means the run did not finish."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([PY_BIN, "-m", "pytest", *paths, *extra, "-q",
                            "--no-header", "-p", "no:cacheprovider", "-n", "auto"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300,
                           env=env)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    return r.returncode, r.stdout + r.stderr


def main():
    only = sys.argv[1:]
    rows = [row for row in MUTATIONS
            if not only or any(row[0].startswith(prefix) for prefix in only)]
    if only and not rows:
        raise SystemExit(f"no mutation row matches {only} — nothing was measured")
    print(f"корень: {ROOT}")
    print(f"строк: {len(rows)} из {len(MUTATIONS)}")
    print(f"{'мутация':<44} {'ожидал':<9} {'вердикт':<16} что покраснело")
    print("-" * 125)
    for name, rel, old, new, expect, tests, extra in rows:
        f = ROOT / rel
        original = f.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{name:<44} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} "
                  f"шаблон встречается {n} раз")
            continue
        try:
            f.write_text(original.replace(old, new, 1), encoding="utf-8")
            _drop_pyc(rel)
            code, out = run(_resolve_tests(tests), extra)
            failed = [l for l in out.splitlines() if l.startswith("FAILED")]
            if code is None:
                verdict, detail = "ЗАВИСЛА", "прогон не уложился в 300 с"
            elif code == 0:
                verdict, detail = "ВЫЖИЛА", "ни один сторож не покраснел"
            elif not failed or "no tests ran" in out:
                verdict, detail = "ПРОМАХ", f"ноль красных при выходе {code}"
            else:
                verdict = "УБИТА"
                detail = f"{len(failed)}: " + "; ".join(
                    l.split(" ")[1].split("::")[-1] for l in failed[:4])
            flag = ""
            if expect == "die" and verdict != "УБИТА":
                flag = "  <<< НАХОДКА: ждали смерти"
            if expect == "survive" and verdict == "УБИТА":
                flag = "  <<< НАХОДКА: ждали выживания"
            print(f"{name:<44} {expect:<9} {verdict:<16} {detail}{flag}")
        finally:
            f.write_text(original, encoding="utf-8")
            _drop_pyc(rel)


if __name__ == "__main__":
    main()
