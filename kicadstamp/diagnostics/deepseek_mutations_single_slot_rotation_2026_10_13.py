# kicadstamp/diagnostics/deepseek_mutations_single_slot_rotation_2026_10_13.py
"""Acceptance mutations for plan_2026_10_13_single_slot_rotation.md (a cell with
fewer than TWO usable slots recovers its rotation from the ONE component's live
angle, both in the live read and in the re-read frame), DeepSeek, 2026-10-13.

Grown from deepseek_mutations_trees_menus_2026_10_13.py (rules 38/43): the same
machinery — basename-resolved T under tests/, `_drop_pyc` for the mutated file,
the `original.count(old) != 1` refusal, a verdict of "ПРОМАХ" when nothing red
came back, and a control that MUST survive. Rows are keyed by the LINE they
mutate, not by a cell number (rule 43).

WHAT IS BEING PROVEN. The guards are tests/geometry/test_cell_frame_single_slot.py
(К1–К2 the inverse itself over 4 slot angles × 4 rotations × both sides, К3 the
delegation, К4 Denis's fact, К5 the mirrored branch, К6 the multi-part fit, К7–К8
the re-read frame), plus the older cell_frame mirror cells and the readers that
consume the frame.

  * M1  single_slot_rotation falls back to 0.0                 -> red (the bug itself)
  * M2  the mirrored branch is dropped (always unmirrored)     -> red (К1/К5)
  * M3  the READER skips the single-slot branch                -> red (Denis's fact)
  * M4  the RE-READ frame skips it                             -> red (К7)
  * M5  the "could not be determined" line goes silent         -> red (К8)
  * K1  a cosmetic comment                                     -> MUST survive

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

GUARDS = ["test_cell_frame_single_slot.py",
          "test_cell_frame_mirror.py",
          "test_spoke_cell_identification.py",
          "test_live_position.py"]

CELL_FRAME = "kicadstamp/cell_frame.py"
LIVE_POSITION = "gui/docks/live_position.py"

_DELEGATE = ("    return clone_rotation_from_component(live_angle_deg, "
             "slot_angle_deg, mirror)")

MUTATIONS = [
    ("M1 single-slot rotation falls back to 0.0", CELL_FRAME,
     _DELEGATE,
     "    return 0.0  # MUTATION",
     "die", GUARDS, ()),
    ("M2 the mirrored branch is dropped", CELL_FRAME,
     _DELEGATE,
     "    return clone_rotation_from_component(live_angle_deg, slot_angle_deg, "
     "False)  # MUTATION",
     "die", GUARDS, ()),
    ("M3 the live READER skips the single slot", LIVE_POSITION,
     "    if fit is None and len(role_to_fp) == 1:",
     "    if False and len(role_to_fp) == 1:  # MUTATION",
     "die", GUARDS, ()),
    ("M4 the RE-READ frame skips the single slot", CELL_FRAME,
     "    if fit is None and len(matched) == 1:",
     "    if False and len(matched) == 1:  # MUTATION",
     "die", GUARDS, ()),
    ("M5 the unknown-side line goes silent", CELL_FRAME,
     "            logger.warning(_(",
     "            logger.debug(_(  # MUTATION",
     "die", GUARDS, ()),
    ("K1 cosmetic comment (control)", CELL_FRAME,
     _DELEGATE,
     _DELEGATE.replace("mirror)", "mirror)  # control"),
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
