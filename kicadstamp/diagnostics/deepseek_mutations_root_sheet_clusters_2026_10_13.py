# kicadstamp/diagnostics/deepseek_mutations_root_sheet_clusters_2026_10_13.py
"""Acceptance mutations for plan_2026_10_13_root_sheet_clusters.md, Ч1 — the
ROOT sheet as the (Cluster, Sheet) instance's own address, DeepSeek, 2026-10-14.

Grown from deepseek_mutations_single_slot_rotation_2026_10_13.py (rules 38/43):
the same machinery — basenames resolved under tests/, `_drop_pyc` for the mutated
file, the `original.count(old) != 1` refusal, a verdict of "ПРОМАХ" when nothing
red came back, and a control that MUST survive. Rows are keyed by the LINE they
mutate, not by a cell number (rule 43).

WHAT IS BEING PROVEN. ONE owner (kicadstamp/sheet_names.py: ROOT_SHEET, the
predicate sheet_in_path, the identity sheet_of_names, the labels instance_label /
sheet_display) and its callers (gui/docks/reead.py's three gates, the placement
narrowing, the extraction, the display).

  * M1  sheet_in_path loses the ROOT branch                      -> red (the bug:
        a root cluster is refused / never matches)
  * M2  an UNRESOLVED chain passes for the root                  -> red (the
        mirror-image bug: a sub-sheet cluster swallowed by the root one)
  * M3  the identity of an EMPTY chain goes back to None         -> red (the
        report Denis saw)
  * M4  match_entity goes back to entry-membership               -> red (a root
        Entity could never be found again)
  * M5  the snapshot membership goes back to "sheet in chain"     -> red (the root
        group is accepted and then dropped as an unknown pair)
  * M6  the placement narrowing skips the root marker            -> red (a root
        Entity takes sub-sheet components: the case Ч1 п.2 forbids)
  * M7  the extraction writes the marker into the Entity NAME    -> red
  * M8  instance_label joins the marker into "CLUSTER//"         -> red
  * M9  sheet_display shows the raw marker instead of "(root sheet)" -> red
  * M10 the reserved-name WARNING goes silent (debug)             -> red (доделка
        of the acceptance: a sheet literally named "/" must be NAMED once)
  * K1  a cosmetic comment                                       -> MUST survive

Run with the main checkout's interpreter; point it at another tree with
`KICADSTAMP_ACCEPT_ROOT` and at another interpreter with `KICADSTAMP_PYTHON`
(the venv's `bin/python` symlink may point at a DIFFERENT system python after an
upgrade — then `.venv` has no pytest and every row would look "killed"). An
optional row-name prefix filter takes the rest of argv.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("KICADSTAMP_ACCEPT_ROOT", "."))


def _interpreter() -> str:
    """KICADSTAMP_PYTHON wins; else the tree's own venv; else this interpreter."""
    env = os.environ.get("KICADSTAMP_PYTHON")
    if env:
        return env
    local = ROOT / ".venv" / "bin" / "python"
    return str(local) if local.exists() else sys.executable


PY_BIN = _interpreter()

GUARDS = ["test_sheet_names_address.py",
          "test_reead.py",
          "test_selection_narrowing.py",
          "test_cell_instance.py",
          "test_tree_from_selection.py",
          "test_instance_candidates.py",
          "test_extract_selection_diagnostics.py",
          "test_explode_plan.py",
          "test_net_trace_single_board_read.py"]

SHEET_NAMES = "kicadstamp/sheet_names.py"
REEAD = "gui/docks/reead.py"
CELL_INSTANCE = "kicadstamp/cell_instance.py"
TREE_FROM_SELECTION = "gui/docks/tree_from_selection.py"

_IN_PATH_TAIL = ('    names = tuple(names)\n'
                 '    if sheet == ROOT_SHEET:\n'
                 '        return not names\n'
                 '    return any(name == sheet for name in names)')

_IDENTITY_TAIL = ('    names = tuple(names)\n'
                  '    if not names:\n'
                  '        return ROOT_SHEET')

_LABEL_TAIL = ('    if not sheet or sheet == ROOT_SHEET:\n'
               '        return str(name)\n'
               '    return f"{name}/{sheet}"')

_DISPLAY_TAIL = ('    if is_root_sheet(sheet):\n'
                 '        return _("(root sheet)")\n'
                 '    return str(sheet) if sheet else absent')

MUTATIONS = [
    ("M1 sheet_in_path loses the ROOT branch", SHEET_NAMES,
     _IN_PATH_TAIL,
     '    names = tuple(names)\n'
     '    return any(name == sheet for name in names)  # MUTATION',
     "die", GUARDS, ()),
    ("M2 an unresolved chain passes for the ROOT", SHEET_NAMES,
     "        return not names\n"
     "    return any(name == sheet for name in names)",
     "        return not any(n for n in names)  # MUTATION\n"
     "    return any(name == sheet for name in names)",
     "die", GUARDS, ()),
    ("M3 an EMPTY chain's identity goes back to None", SHEET_NAMES,
     _IDENTITY_TAIL,
     '    names = tuple(names)\n'
     '    if not names:\n'
     '        return None  # MUTATION',
     "die", GUARDS, ()),
    ("M4 match_entity goes back to entry-membership", REEAD,
     "        if e.cluster == cluster and e.sheet and sheet_in_path("
     "fp_sheet or (), e.sheet):",
     "        if e.cluster == cluster and e.sheet and e.sheet in "
     "(fp_sheet or ()):  # MUTATION",
     "die", GUARDS, ()),
    ("M5 membership goes back to 'sheet in chain'", REEAD,
     "                            if s.cluster == cluster\n"
     "                            and sheet_in_path(_sheet_chain(s, "
     "sheet_names), sheet)]",
     "                            if s.cluster == cluster\n"
     "                            and sheet in _sheet_chain(s, sheet_names)]  "
     "# MUTATION",
     "die", GUARDS, ()),
    ("M6 the placement narrowing skips the root marker", CELL_INSTANCE,
     "        if members and sheet:",
     '        if members and sheet and sheet != "/":  # MUTATION',
     "die", GUARDS, ()),
    ("M7 the marker leaks into the Entity name", TREE_FROM_SELECTION,
     "    if sheet and not is_root_sheet(sheet):",
     "    if sheet:  # MUTATION",
     "die", GUARDS, ()),
    ("M8 instance_label joins the marker", SHEET_NAMES,
     _LABEL_TAIL,
     '    if not sheet:\n'
     '        return str(name)\n'
     '    return f"{name}/{sheet}"  # MUTATION',
     "die", GUARDS, ()),
    ("M9 sheet_display shows the raw marker", SHEET_NAMES,
     _DISPLAY_TAIL,
     '    return str(sheet) if sheet else absent  # MUTATION',
     "die", GUARDS, ()),
    ("M10 the reserved-name warning goes silent", SHEET_NAMES,
     "    _reserved_name_warned = True\n"
     "    logger.warning(_(",
     "    _reserved_name_warned = True\n"
     "    logger.debug(_(  # MUTATION",
     "die", GUARDS, ()),
    ("K1 cosmetic comment (control)", SHEET_NAMES,
     "        return not names\n",
     "        return not names  # control\n",
     "survive", GUARDS, ()),
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
    """(returncode, output) for the WHOLE guard list — returncode None means a
    run did not finish.

    ONE pytest process PER guard file, deliberately. The older rigs hand every
    file to one process; with this checkout's `.venv` driven through
    KICADSTAMP_PYTHON (the venv's symlinks can point at a different system
    python after an upgrade), some combinations of a `tests/gui/docks/...` file
    FOLLOWED BY a `tests/gui/...` file lose that directory's local conftest.py:
    the two MainWindow-fixture cells of tests/gui/test_extract_selection_
    diagnostics.py then ERROR at setup, which the verdict logic would read as a
    survivor's neighbour ("ПРОМАХ" instead of "ВЫЖИЛА"). Measured on the CLEAN
    tree too, so it is the environment, never a mutation: per-file runs are
    immune, and a red is still a red. Outputs are concatenated, the WORST exit
    code is returned, and every red still names its own file (rule 38)."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    worst, chunks = 0, []
    for path in paths:
        try:
            r = subprocess.run([PY_BIN, "-m", "pytest", path, *extra, "-q",
                                "--no-header", "-p", "no:cacheprovider",
                                "-n", "auto"],
                               cwd=ROOT, capture_output=True, text=True,
                               timeout=300, env=env)
        except subprocess.TimeoutExpired:
            return None, "\n".join(chunks) + "\nTIMEOUT: " + path
        worst = max(worst, r.returncode)
        chunks.append(f"### {path} (exit {r.returncode})\n" + r.stdout + r.stderr)
    return worst, "\n".join(chunks)


def main():
    only = sys.argv[1:]
    rows = [row for row in MUTATIONS
            if not only or any(row[0].startswith(prefix) for prefix in only)]
    if only and not rows:
        raise SystemExit(f"no mutation row matches {only} — nothing was measured")
    print(f"корень: {ROOT}")
    print(f"интерпретатор: {PY_BIN}")
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
