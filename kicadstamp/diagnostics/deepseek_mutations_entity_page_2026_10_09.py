# kicadstamp/diagnostics/deepseek_mutations_entity_page_2026_10_09.py
"""Acceptance mutations for plan_2026_10_09_entity_page.md, ШАГ 1
(страница сущности: перенос в gui/entity/page.py + комбобокс ячейки на потоке
change_cell_flow), 2026-10-09.

Grown from deepseek_mutations_cells_entities_part1_2026_10_09.py (rule 38): the
SAME machinery — basename-resolved tests under tests/, `_drop_pyc` for the
mutated file, the `original.count(old) != 1` refusal (a non-unique template is a
MISS, not a kill), a verdict of «ПРОМАХ» when nothing red came back, and a
control that MUST survive.

WHAT IS BEING PROVEN. The guards are tests/gui/docks/test_entity_page_cell_combo.py
(the combobox page) and tests/gui/docks/test_entity_page.py (the moved record
editor).

  * M1  комбобокс пишет своей записью мимо apply_cell_change  -> обход потока
  * M2  текущая неподходящая ячейка не серая (enabled)      -> выбор неверной
  * M3  нет снимка -> подбор включается (orphan снят)        -> список сжался
  * M4  текущая ячейка не выбрана (pos всегда 0)            -> чужая текущая
  * M5  неподходящие снова в списке (choose_cells)          -> чужие в списке
  * M6  текущая ячейка не помечена (current=False)          -> текущей нет
  * K1  cosmetic comment (control)                          -> MUST survive

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

COMBO = ["test_entity_page_cell_combo.py", "test_entity_page.py"]
PART2 = ["test_change_cell.py", "test_entities_under_cells.py"]
# The cells that actually exercise a non-fitting OTHER cell (the combo page and
# the pure rule) — PART2 alone has no such cell, so M5 would survive there.
CHOICE = ["test_instance_candidates.py", "test_entity_page_cell_combo.py"]

PAGE = "gui/entity/page.py"
FLOW = "gui/docks/change_cell_flow.py"
PURE = "gui/docks/instance_candidates.py"

MUTATIONS = [
    ("M1 комбобокс пишет мимо apply_cell_change", PAGE,
     "        apply_cell_change(hub, self._entity_data, self._entity_file, chosen)\n",
     "        from pathlib import Path as _P  # MUTATION\n"
     "        from ..docks._common import upsert_list_entry as _up  # MUTATION\n"
     "        _d = dict(self._entity_data)  # MUTATION\n"
     "        _d[\"cell\"] = chosen  # MUTATION\n"
     "        _up(_P(self._entity_file), \"entities\", _d,\n"
     "            key_fn=lambda e: e.get(\"name\"))  # MUTATION\n",
     "die", COMBO, ()),
    ("M2 текущая неподходящая не серая", PAGE,
     "            if item is not None and not (cand.fits or choices.orphan):\n",
     "            if False:  # MUTATION\n",
     "die", COMBO, ()),
    ("M3 нет снимка -> подбор включается", FLOW,
     "    if cfg is None or not instance:\n",
     "    if False:  # MUTATION\n",
     "die", COMBO, ()),
    ("M4 текущая ячейка не выбрана", PAGE,
     "        pos = combo.findData(current) if current else -1\n",
     "        pos = 0  # MUTATION\n",
     "die", COMBO, ()),
    ("M5 неподходящие снова в списке", PURE,
     "        if cand.fits or cand.current:\n",
     "        if True:  # MUTATION\n",
     "die", CHOICE, ()),
    ("M6 текущая ячейка не помечена", PURE,
     "                             reason=reason, current=(cell.name == current))\n",
     "                             reason=reason, current=False)  # MUTATION\n",
     "die", COMBO, ()),
    ("K1 cosmetic comment (control)", PAGE,
     "        self._cell_orphan = choices.orphan\n",
     "        self._cell_orphan = choices.orphan  # control\n",
     "survive", COMBO, ()),
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
