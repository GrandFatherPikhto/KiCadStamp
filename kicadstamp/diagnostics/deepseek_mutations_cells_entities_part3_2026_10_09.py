# kicadstamp/diagnostics/deepseek_mutations_cells_entities_part3_2026_10_09.py
"""Acceptance mutations for plan_2026_10_09_cells_and_entities.md, ЧАСТЬ 3
(одиночный «Create entity»: выпадашка экземпляра), 2026-10-09.

Grown from deepseek_mutations_cells_entities_part1_2026_10_09.py (rule 38): the
SAME machinery — basename-resolved tests under tests/, `_drop_pyc` for the
mutated file, the `original.count(old) != 1` refusal (a non-unique template is a
MISS, not a kill), a verdict of «ПРОМАХ» when nothing red came back, and a
control that MUST survive.

WHAT IS BEING PROVEN. The guards are tests/gui/docks/test_create_entity_form.py
(the form: one combobox, rows, preselect) and tests/gui/docks/
test_create_entity_menu.py (the flow: the door, the rule, the record).

  * M1  занятый выбирается (taken row selectable)        -> taken offered twice
  * M2  неподходящие снова строки (others become rows)   -> huge list returns
  * M3  своё правило подбора (cell roles not applied)    -> every instance fits
  * M4  предвыбор при двух (preselect whenever any free) -> two preselected
  * M5  имя по умолчанию затирает правленое              -> typed name lost
  * K1  cosmetic comment                                 -> MUST survive

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

FORM = ["test_create_entity_form.py"]
MENU = ["test_create_entity_menu.py"]

DIALOG_MOD = "gui/docks/create_entity.py"
FLOW = "gui/docks/create_entity_flow.py"

MUTATIONS = [
    ("M1 занятый выбирается (taken selectable)", DIALOG_MOD,
     "                item.setFlags(Qt.ItemFlag.ItemIsEnabled)\n",
     "                item.setFlags(Qt.ItemFlag.ItemIsEnabled"
     " | Qt.ItemFlag.ItemIsSelectable)  # MUTATION\n",
     "die", FORM, ()),
    ("M2 неподходящие снова строки (others rows)", DIALOG_MOD,
     "        for cand in self._free:\n            combo.addItem(cand.label, cand)\n",
     "        for cand in self._free + self._others:  # MUTATION\n"
     "            combo.addItem(cand.label, cand)\n",
     "die", FORM, ()),
    ("M3 своё правило подбора (roles not applied)", FLOW,
     "            candidates = instance_candidates(parts, cell_roles, _taken)\n",
     "            candidates = instance_candidates(parts, (), _taken)  # MUTATION\n",
     "die", MENU, ()),
    ("M4 предвыбор при двух (preselect any free)", DIALOG_MOD,
     "        if len(self._free) == 1:\n",
     "        if self._free:  # MUTATION\n",
     "die", FORM, ()),
    ("K1 cosmetic comment (control)", DIALOG_MOD,
     "        self._candidates = list(candidates or ())",
     "        self._candidates = list(candidates or ())  # control",
     "survive", FORM, ()),
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
