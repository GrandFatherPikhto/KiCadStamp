# kicadstamp/diagnostics/claude_mutations_accept_f3_2026_10_01.py
"""Acceptance mutations for Ф3.0–Ф3.6 of plan_2026_09_27_repo_and_tests_transformation
(Windows-neutral tests, --reverse-order, probes renamed, the format-number probe
invalidation W4, the board-door and wrapper path checks), Claude, 2026-10-01.
Grown from claude_mutations_accept_f20_2026_09_30.py (rule 38).

Two extensions over the parent:
- each mutation carries its OWN test basenames (Ф3 touches several domains; one
  shared T would run the slow subprocess cells for every row);
- `old is None` means CREATE `rel` with `new` as its content (the "no test_*.py
  under kicadstamp/" sentinel watches file names, not file text); the file must
  not exist beforehand and is removed afterwards.

Windows-only branches (the win32 skip order, the drive-letter regex,
QT_FORCE_STDERR_LOGGING, `-s` in the capture control) cannot be killed on Linux —
they are pinned by the Windows leg of Actions, not by this rig.

Previous rig docstring follows.
Acceptance mutations for Ф2.0 of plan_2026_09_27_repo_and_tests_transformation
(tests/paths.py: tree-absolute paths, the blind-path anchor), Claude, 2026-09-30.
Grown from claude_mutations_accept_f113_2026_09_30.py (rule 38).

Previous rig docstring follows.
Acceptance mutations for Ф1.13 (plan_2026_09_27, section at the end).

The defect: `pynng_safety._bounded_close` was the ONLY bound, used for both the
explicit `close()` and `Socket.__del__`. The GC path runs (via GC) from INSIDE
`logging.Handler.handle` — i.e. under the handler's own lock — so a 2 s `join`
there stalls the whole process's logging, and the WARNING takes that same lock
again. Measured 2026-09-30: a logging convoy, tests failing in DIFFERENT cells run
to run, and the run aborting without a summary (a core dump).

The fix (Claude's answer, 30.09) splits the paths: `Socket.__del__` dispatches the
close on a daemon thread with NO join and NO logging (abandoned closes counted in
`gc_close_stats`), while the explicit `close()` keeps the bounded wait and the one
WARNING.

M1–M3 are Demon's; M4–M6 were added in Claude's review of the accepted step
(30.09) and the `_drop_pyc` guard was widened there: it now drops the bytecode of
the MUTATED file, product module included, because the control mutation is
same-length — exactly the stale-.pyc trap.

Every mutation MUST die; only M3 (the control) MUST survive.

Run with the main checkout's interpreter (a worktree has no .venv, canon rule 41);
point it at another tree with ``KICADSTAMP_ACCEPT_ROOT``.
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

# Ф3.3: the rigs outlive the domain moves, so T is resolved by BASENAME under
# tests/ instead of naming flat paths (Ф2 moved every file). Basenames are unique
# across the suite (the Ф2 rule), so a name that resolves to zero or two files is
# itself a finding: a silent miss would make every verdict below vacuous.
_T_BASENAMES = [
    "test_repo_hygiene.py", "test_marker_contract.py",
    "test_diagnostics_module_names.py", "test_i18n.py",
    "test_sexp_config_roundtrip.py", "test_config_format_version.py",
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


T = _resolve_tests(_T_BASENAMES)  # parent's list, kept for growth; rows name their own

HOOK = ["test_slot_exception_hook.py", "test_qt_slot_exception_capture.py"]

MUTATIONS = [
    # Ф3.1-а — died_of_a_crash, POSIX branch: never a crash. MUST die (controls).
    ("F1 died_of_a_crash -> False", "tests/fakes/process_exit.py",
     "return returncode < 0 or returncode == 134", "return False", "die", HOOK),
    # The helper's STRICTNESS: everything is a crash (an ordinary failure, exit 1,
    # included). Is that pinned anywhere? Unknown going in.
    ("F2 died_of_a_crash -> True", "tests/fakes/process_exit.py",
     "return returncode < 0 or returncode == 134", "return True", "unknown", HOOK),
    # Ф3.1-б — --reverse-order does nothing / sorts forward. MUST die.
    ("R1 --reverse-order is a no-op", "tests/conftest.py",
     "items.sort(key=lambda item: item.path.as_posix(), reverse=True)", "pass",
     "die", ["test_reverse_order_option.py"]),
    ("R2 --reverse-order sorts forward", "tests/conftest.py",
     "items.sort(key=lambda item: item.path.as_posix(), reverse=True)",
     "items.sort(key=lambda item: item.path.as_posix(), reverse=False)",
     "die", ["test_reverse_order_option.py"]),
    # Ф3.2 — a test_*.py appears under kicadstamp/. MUST die.
    ("P1 test_*.py under kicadstamp/", "kicadstamp/diagnostics/test_zz_accept_f3.py",
     None, '"""Created by the Ф3 acceptance rig; removed afterwards."""\n',
     "die", ["test_repo_hygiene.py"]),
    # Ф3.2 — a renamed probe's docstring names its OLD file. MUST die.
    ("P2 probe docstring names old file", "kicadstamp/diagnostics/probe_create_one_via.py",
     "probe_create_one_via.py — minimal", "test_create_one_via.py — minimal",
     "die", ["test_diagnostics_module_names.py"]),
    # Ф3.6 — the board door names ITSELF instead of the caller. MUST die.
    ("D1 door names its own file", "gui/connection.py",
     'site = f"{frame.f_code.co_filename}:{frame.f_lineno}"',
     'site = f"{_OWN_FILE}:{frame.f_lineno}"', "die", ["test_board_door_guard.py"]),
    # Ф3.6 W1 — entry_site stops skipping gui/worker.py frames. MUST die.
    ("W1 entry_site keeps the wrapper", "gui/slot_exception_hook.py",
     "if not _is_wrapper_frame(tb.tb_frame.f_code.co_filename):", "if True:",
     "die", ["test_slot_exception_hook.py"]),
    # Ф3.6 W4 — the writer stops invalidating the probe / the eviction is empty.
    ("N1 writer skips invalidate_probe", "kicadstamp/config_writer.py",
     "\n    invalidate_probe(target)\n", "\n    pass\n",
     "die", ["test_config_format_version.py"]),
    ("N2 invalidate_probe evicts nothing", "kicadstamp/config/format_version.py",
     "            del _probe_cache[key]", "            pass",
     "die", ["test_config_format_version.py"]),
    # CONTROL: a comment edit in the shared helper. MUST survive.
    ("C1 cosmetic comment (control)", "tests/fakes/process_exit.py",
     "# POSIX: died of a signal (negative)", "# POSIX: died of a signal (negative.)",
     "survive", HOOK),
]


def _drop_pyc(rel: str) -> None:
    """Drop the bytecode of the MUTATED file — the product module included, not
    only the tests. A same-length control mutation with a same-second mtime is
    exactly the stale-.pyc trap the earlier format rigs hit (Claude, 30.09)."""
    f = ROOT / rel
    for cache in f.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{f.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def run(paths):
    """(returncode, output); returncode None means the run did not finish.

    A mutation that HANGS must not abort the whole rig mid-table (seen once,
    30.09: a 300 s timeout left the table unfinished and hid the verdicts): the
    hang is reported as its own verdict, and for a "die" expectation it is a
    FINDING — a hang is not the fast red the cell is supposed to give.
    """
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([PY_BIN, "-m", "pytest", *paths, "-q", "--no-header",
                            "-p", "no:cacheprovider"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300,
                           env=env)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    return r.returncode, r.stdout + r.stderr


def main():
    print(f"корень: {ROOT}")
    print(f"{'мутация':<38} {'ожидал':<9} {'вердикт':<16} что покраснело")
    print("-" * 125)
    for name, rel, old, new, expect, tests in MUTATIONS:
        f = ROOT / rel
        create = old is None
        if create:
            if f.exists():
                print(f"{name:<38} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} файл уже существует")
                continue
            original = None
        else:
            original = f.read_text(encoding="utf-8")
            n = original.count(old)
            if n != 1:
                print(f"{name:<38} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} шаблон встречается {n} раз")
                continue
        try:
            f.write_text(new if create else original.replace(old, new, 1), encoding="utf-8")
            _drop_pyc(rel)
            code, out = run(_resolve_tests(tests))
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
            print(f"{name:<38} {expect:<9} {verdict:<16} {detail}{flag}")
        finally:
            if create:
                f.unlink(missing_ok=True)
            else:
                f.write_text(original, encoding="utf-8")
            _drop_pyc(rel)


if __name__ == "__main__":
    main()
