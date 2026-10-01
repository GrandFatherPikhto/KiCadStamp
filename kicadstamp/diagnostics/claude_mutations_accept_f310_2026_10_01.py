# kicadstamp/diagnostics/claude_mutations_accept_f310_2026_10_01.py
"""Acceptance mutations for Ф3.10 of plan_2026_09_27_repo_and_tests_transformation
(tests/repo/test_i18n_import_guard.py replaces tools/_check_i18n.py), Claude,
2026-10-01. Grown from claude_mutations_accept_f39_2026_10_01.py (rule 38).

One row per shipping package and import form (relative in kicadstamp/, absolute
in gui/ and mcp_server/), one row for `_` bound from a NON-i18n module, one row
for the guard's "defensive" `import <...>.i18n as _` branch — which binds a
MODULE to `_`, so `_("...")` would raise TypeError — and a control.

Previous rig docstring follows.
Acceptance mutations for Ф3.9 of plan_2026_09_27_repo_and_tests_transformation
(the session QApplication must outlive the `qapp` fixture's generator frame),
2026-10-01. Grown from claude_mutations_accept_f37_2026_10_01.py (rule 38): the
same machinery — basename-resolved T, `_drop_pyc` for the mutated file, a verdict
of "ПРОМАХ" when nothing red came back, a refused filter that matches no row —
with the table narrowed to the step being accepted. The parent's rows stay in the
parent (Ф3.7/Ф3.8 are accepted on their own rigs).

WHAT IS BEING PROVEN HERE. Ф3.9's fix is `_qapp_keepalive`, and Ф3.9's rig is
test code, so the rows mutate THE RIG:

  * F4 — the assignment removed. This is the defect itself: the fixture's local
    is the only owner again, the application dies at session teardown, and both
    cells must say so (`test_the_qapp_fixture_holds_...` directly,
    `test_the_application_is_still_alive_when_the_session_is_over` through its
    inner run's witness).
  * F5 — the holder fixture hands out something that is not its own module. The
    guard cell is supposed to read the module it is GIVEN, so this must kill it;
    the mutation wording follows the trap that actually appeared while the step
    was built (a module found by name or by file path is a different copy — see
    the docstring in tests/gui/test_qapp_keepalive.py).
  * C1 — a cosmetic comment edit. MUST survive.

The counter (`handoff/probe_2026_10_01_segv_rate.sh` + `…_segv_subset.txt`) is NOT
part of this rig: it is a witness of the defect, not a ruler for it — measured
01.10.2026, the same baseline that crashed 13/20 runs early in the session
stopped crashing at all in 60 later runs (0/20 three times), i.e. the rate is not
stationary on this machine. The two cells above are the deterministic form of the
same property.

Run with the main checkout's interpreter (a worktree has no .venv, canon rule 41);
point it at another tree with ``KICADSTAMP_ACCEPT_ROOT``. An optional row-name
prefix filter takes the rest of argv.
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

# Ф3.3: resolved by BASENAME under tests/ — the rigs outlive the domain moves.
# A name that resolves to zero or two files is a finding, not a warning (rule 38).
_T_BASENAMES = ["test_i18n_import_guard.py"]

MUTATIONS = [
    ("I1 kicadstamp/ relative import dropped", "kicadstamp/anchor_graph.py",
     "\nfrom .i18n import _\n", "\n", "die", _T_BASENAMES, ()),
    ("I2 gui/ absolute import dropped", "gui/dock_hub.py",
     "\nfrom kicadstamp.i18n import _\n", "\n", "die", _T_BASENAMES, ()),
    ("I3 mcp_server/ absolute import dropped", "mcp_server/handlers.py",
     "\nfrom kicadstamp.i18n import _\n", "\n", "die", _T_BASENAMES, ()),
    # `_` bound, but from gettext directly, not from the project's i18n module.
    ("I4 _ from a non-i18n module", "kicadstamp/anchor_graph.py",
     "\nfrom .i18n import _\n", "\nfrom gettext import gettext as _\n",
     "die", _T_BASENAMES, ()),
    # The guard's defensive branch: `import kicadstamp.i18n as _` binds the MODULE;
    # `_("...")` is then a TypeError at call time. Does anything notice?
    ("I5 _ is the i18n MODULE", "mcp_server/handlers.py",
     "\nfrom kicadstamp.i18n import _\n", "\nimport kicadstamp.i18n as _\n",
     "unknown", _T_BASENAMES + ["test_mcp_server.py", "test_mcp_error_contract.py"], ()),
    ("C1 cosmetic comment (control)", "tests/repo/test_i18n_import_guard.py",
     "def test_every_module_that_calls_gettext_imports_it():",
     "def test_every_module_that_calls_gettext_imports_it():  # control",
     "survive", _T_BASENAMES, ()),
]


def _resolve_tests(basenames):
    """[repo-relative paths] for `basenames`, resolved under ROOT/tests.

    Refuses (SystemExit) when a name is not found, or is found TWICE: a blind T
    would make every mutation verdict vacuous, and an ambiguous one would run the
    wrong file — both are failures, not warnings (rule 38)."""
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
    """Drop the bytecode of the MUTATED file — a same-length control mutation
    with a same-second mtime is exactly the stale-.pyc trap (rule 38)."""
    f = ROOT / rel
    for cache in f.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{f.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def run(paths, extra=()):
    """(returncode, output); returncode None means the run did not finish."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([PY_BIN, "-m", "pytest", *paths, *extra, "-q", "--no-header",
                            "-p", "no:cacheprovider"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300,
                           env=env)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    return r.returncode, r.stdout + r.stderr


def main():
    # Optional filters: `… f39 F4` runs only the rows whose name starts with one
    # of them. A filter that matches NOTHING is a refusal, not an empty table.
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
            print(f"{name:<44} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} шаблон встречается {n} раз")
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
                # A crash at the end of the run is not a verdict on its own: the
                # Ф3.9 defect kills the whole process, and a green report with a
                # 139 exit would mean the guard never saw it (rule 38).
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
