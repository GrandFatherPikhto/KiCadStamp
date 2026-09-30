# kicadstamp/diagnostics/claude_mutations_accept_f113_2026_09_30.py
"""Acceptance mutations for Ф1.13 (plan_2026_09_27, section at the end).

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

T = ["tests/test_pynng_safety.py"]

MOD = "kicadstamp/kicad/pynng_safety.py"

#: The GC dispatch, exactly as it stands in the fixed product.
GC_DISPATCH = ('    try:\n'
               '        threading.Thread(target=_run, daemon=True,\n'
               '                         name="pynng.Socket.close(gc)").start()\n'
               '    except RuntimeError:')

#: The GC worker's tail, exactly as it stands in the fixed product.
GC_FINISH = ('        finally:\n'
             '            with _gc_lock:\n'
             '                _gc_finished += 1\n')

#: The reload-safe capture of the true original close.
SAFE_CAPTURE = ('_installed_close = pynng.nng.Socket.close\n'
                '_original_close = getattr(_installed_close, "_kicadstamp_original",\n'
                '                          _installed_close)')

#: The __del__ patch block.
DEL_PATCH = ('if not getattr(pynng.nng.Socket.__del__, "_kicadstamp_gc_bounded", False):\n'
             '    _gc_del._kicadstamp_gc_bounded = True\n'
             '    pynng.nng.Socket.__del__ = _gc_del')

MUTATIONS = [
    # M1 — the join comes back to the GC path. MUST die (the cell measures that
    # the GC path returns at once).
    ("M1 restore join on the GC path", MOD,
     GC_DISPATCH,
     '    try:\n'
     '        _t = threading.Thread(target=_run, daemon=True,\n'
     '                              name="pynng.Socket.close(gc)")\n'
     '        _t.start()\n'
     '        _t.join(_CLOSE_TIMEOUT_S)\n'
     '    except RuntimeError:',
     "die"),

    # M2 — logging comes back to the GC path, through the MODULE logger. MUST die.
    ("M2 module-log on the GC path", MOD,
     GC_FINISH,
     '        finally:\n'
     '            logger.warning("pynng Socket.close() (gc) finished")\n'
     '            with _gc_lock:\n'
     '                _gc_finished += 1\n',
     "die"),

    # M3 — CONTROL: a docstring edit that changes nothing. MUST survive.
    ("M3 cosmetic docstring edit (control)", MOD,
     '"""``Socket.__del__`` — the GC path: fire-and-forget, no join, no logging.',
     '"""``Socket.__del__`` — the GC path: fire-and-forget; no join, no logging.',
     "survive"),

    # M4 (Claude) — the __del__ patch is never installed. MUST die: the fix would
    # be inert, the GC finalizer would call the ORIGINAL close.
    ("M4 __del__ patch not installed", MOD,
     DEL_PATCH,
     'if False and not getattr(pynng.nng.Socket.__del__, "_kicadstamp_gc_bounded",\n'
     '                         False):\n'
     '    _gc_del._kicadstamp_gc_bounded = True\n'
     '    pynng.nng.Socket.__del__ = _gc_del',
     "die"),

    # M5 (Claude) — the original is captured WITHOUT the reload guard. MUST die:
    # after a reload both paths go through the explicit wrapper (the convoy).
    ("M5 unsafe original capture (reload)", MOD,
     SAFE_CAPTURE,
     '_installed_close = pynng.nng.Socket.close\n'
     '_original_close = _installed_close',
     "die"),

    # M6 (Claude) — the GC path logs through the ROOT logger. MUST die: a
    # handler's lock is shared by EVERY logger, so "our logger is quiet" is not
    # the property; only an any-logger cell catches this.
    ("M6 root-log on the GC path", MOD,
     GC_FINISH,
     '        finally:\n'
     '            logging.getLogger().warning("pynng Socket.close() (gc) done")\n'
     '            with _gc_lock:\n'
     '                _gc_finished += 1\n',
     "die"),
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
