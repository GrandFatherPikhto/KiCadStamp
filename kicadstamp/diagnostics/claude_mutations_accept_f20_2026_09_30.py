# kicadstamp/diagnostics/claude_mutations_accept_f113_2026_09_30.py
"""Acceptance mutations for Ф2.0 of plan_2026_09_27_repo_and_tests_transformation
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

T = ["tests/test_repo_hygiene.py", "tests/test_marker_contract.py",
     "tests/test_diagnostics_module_names.py", "tests/test_i18n.py",
     "tests/test_sexp_config_roundtrip.py", "tests/test_config_format_version.py"]

MUTATIONS = [
    # P1 — the root one level off: the anchor must refuse loudly. MUST die.
    ("P1 REPO_ROOT points at tests/", "tests/paths.py",
     "REPO_ROOT = TESTS_ROOT.parent", "REPO_ROOT = TESTS_ROOT", "die"),
    # P2 — the same wrong root with the anchor switched off: do the guards that read
    # the tree go blind SILENTLY? A survivor means the anchor is the only line.
    ("P2 wrong root, anchor off", "tests/paths.py",
     "\nif _MISSING:\n", "\nREPO_ROOT = TESTS_ROOT\nif False:\n", "unknown"),
    # P3 — CONTROL: a comment edit. MUST survive.
    ("P3 cosmetic comment (control)", "tests/paths.py",
     "#: `tests/` — the package this module lives in.",
     "#: `tests/` - the package this module lives in.", "survive"),
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
    for name, rel, old, new, expect in MUTATIONS:
        f = ROOT / rel
        original = f.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{name:<38} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} шаблон встречается {n} раз")
            continue
        try:
            f.write_text(original.replace(old, new, 1), encoding="utf-8")
            _drop_pyc(rel)
            code, out = run(T)
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
            f.write_text(original, encoding="utf-8")
            _drop_pyc(rel)


if __name__ == "__main__":
    main()
