# kicadstamp/diagnostics/claude_mutations_accept_f112_2026_09_29.py
"""Acceptance mutations for Ф1.12 (plan_2026_09_27_repo_and_tests_transformation).

Grown from the previous entry's rig, ``claude_mutations_accept_err_2026_09_25.py``
(rule 38: each acceptance rig grows out of the last), and aimed at the ONE thing
Ф1.12 changes: a bare `pytest` must not collect the live-board cells, and the
marker-contract guard must not go quiet behind that protection.

Two mutations MUST die, and both are the defect itself, restored quietly:

  M1  pytest.ini loses `addopts = -m "not integration"`. Then a bare `pytest`
      collects `tests/integration_tests/` again — `test_a_bare_run_collects_no_
      integration_cells` reports `bare != total - integration`, and
      `test_the_default_run_does_not_skip_the_three_kind_check` reports the
      missing default. Either red line is a kill.
  M2  the skip predicate reverts to "a NON-EMPTY markexpr means a user filter"
      (`if markexpr:`). The default run then treats its own default as a filter,
      and `test_the_default_run_does_not_skip_the_three_kind_check` fails — this
      is exactly the blindness the step exists to remove.

  M3  is a CONTROL that must SURVIVE: a cosmetic docstring edit changes no
      behaviour. If M3 went red the rig would be punishing the file, not the
      defect, and every "УБИТА" above would be suspect.

T is only ``tests/test_marker_contract.py`` on purpose: the two new cells are
self-contained (the bare-run cell spawns its own collect-only subprocesses), so a
red line lands without paying for the whole suite. The three-kind cell SKIPS in a
single-file run by design — that skip is not a kill, and the header above records
why it is not needed to catch M1/M2.

Run from the main checkout's interpreter (a worktree has no .venv of its own,
canon rule 41); point it at another tree with ``KICADSTAMP_ACCEPT_ROOT``.
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

T = ["tests/test_marker_contract.py", "tests/test_fakes_conformance.py",
     "tests/test_repo_hygiene.py", "tests/test_imprint_capture.py", "tests/gui/test_imprint.py"]

MUTATIONS = [
    # M1 — the protection removed: no addopts, so a bare `pytest` collects the
    # live-board cells again. MUST die.
    ("M1 drop addopts from pytest.ini", "pytest.ini",
     'addopts = -m "not integration"',
     "# addopts deliberately removed by the acceptance rig (M1)",
     "die"),

    # M2 — the guard blinded: the skip predicate goes back to "non-empty means a
    # user filter", which is TRUE for the default run now. MUST die.
    ("M2 skip on any non-empty markexpr", "tests/test_marker_contract.py",
     "    if markexpr != default_markexpr:",
     "    if markexpr:",
     "die"),

    # M3 — CONTROL: a docstring edit that changes nothing. MUST survive.
    ("M3 cosmetic docstring edit (control)", "tests/test_marker_contract.py",
     '"""The NAMES of the gui / integration / unit markers this item really carries.',
     '"""The NAMES of the gui/integration/unit markers this item really carries.',
     "survive"),
    # Claude acceptance, round 2 (Ф1.4e): the shared imprint double and its guards.
    # M4 — the shared double invents a read the real adapter does not have. MUST die
    # (conformance cell: nothing beyond the seam).
    ("M4 shared imprint double invents a method", "tests/fakes/imprint_adapter.py",
     "    def get_tracks(self):\n",
     "    def get_everything_at_once(self):\n        return []\n\n    def get_tracks(self):\n",
     "die"),
    # M5 — a test file defines its own copy of a name that lives in tests/fakes/.
    # MUST die (hygiene guard 3).
    ("M5 a local FakeImprintAdapter again", "tests/test_imprint_capture.py",
     "from tests.fakes.imprint_adapter import FakeImprintAdapter as _FakeAdapter  # noqa: E402\n",
     "from tests.fakes.imprint_adapter import FakeImprintAdapter as _FakeAdapter  # noqa: E402\n\n\n"
     "class FakeImprintAdapter(_FakeAdapter):\n    pass\n",
     "die"),
]


def _drop_pyc() -> None:
    """A stale .pyc can turn a kill into a false 'survived' (or the reverse) —
    measured while landing the earlier format rigs, hence the belt here."""
    for cache in (ROOT / "tests").rglob("__pycache__"):
        for f in cache.glob("test_marker_contract.*.pyc"):
            f.unlink(missing_ok=True)


def run(paths):
    _drop_pyc()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    r = subprocess.run([PY_BIN, "-m", "pytest", *paths, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True, timeout=600,
                       env=env)
    return r.returncode, r.stdout


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
            code, out = run(T)
            failed = [l for l in out.splitlines() if l.startswith("FAILED")]
            if code == 0:
                verdict, detail = "ВЫЖИЛА", "ни один сторож не покраснел"
            elif not failed or "no tests ran" in out:
                # Zero red lines with a non-zero exit is a MISS, never a kill:
                # the mutation never reached the cell it aimed at (rule 38).
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


if __name__ == "__main__":
    main()
