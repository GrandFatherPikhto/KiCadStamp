# kicadstamp/diagnostics/run_checks_tab_mutations_2026_10_22.py
"""Mutation rows for the Checks tab (plan_2026_10_22_checks_tab, Ч1).

The tab is a launcher, and a launcher's defects are quiet: a report that looks fine while a
warning line was dropped, a green status over a refusal, a "crashed" label on a run the user
stopped, or a check started against no project at all. Each row below removes exactly ONE of
those decisions and must turn a named cell red:

  M1  the status colour stops following the exit code   (gui/docks/checks.py `_on_finished`)
  M2  stderr is no longer merged into the pane          (gui/docks/checks.py `_launch`)
  M3  a run the user STOPPED is reported as a crash     (gui/docks/checks.py `_on_finished`)
  M4  a check may be built with no project at all       (gui/checks_registry.py `command_for`)
  M5  cosmetic control (a comment must change nothing)

Safeguards, the same as every harness in this package: a template that matches more than once
is refused (a silent miss is indistinguishable from a healthy guard — rule 38); a row that
reddens NOTHING is a MISS; the control row must survive; every file is restored in `finally`
and the restoration is verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_checks_tab_mutations_2026_10_22`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHECKS = "gui/docks/checks.py"
REGISTRY = "gui/checks_registry.py"

GUARDS = "tests/gui/docks/test_checks_dock.py tests/gui/test_checks_registry.py"

MUTATIONS = [
    ("M1 every exit code looks clean", CHECKS,
     "                exit_level(code))\n",
     "                exit_level(0))  # MUTATION: every code looks clean\n",
     ["test_the_pane_is_the_scripts_own_bytes_and_exit_1_is_yellow",
      "test_exit_zero_is_green_and_exit_two_is_red"]),

    ("M2 stderr is not merged into the pane", CHECKS,
     "        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)\n",
     "        process.setProcessChannelMode("
     "QProcess.ProcessChannelMode.SeparateChannels)  # MUTATION\n",
     ["test_the_pane_is_the_scripts_own_bytes_and_exit_1_is_yellow",
      "test_copy_hands_over_the_script_bytes",
      "test_a_dirty_working_set_warns_in_the_pane_but_not_in_the_copied_text"]),

    ("M3 a stopped run is reported as a crash", CHECKS,
     "        if stopped:\n            self._show_status(\n"
     '                _("{name}: stopped after {seconds:.1f} s").format(\n',
     "        if False:  # MUTATION: a stopped run is reported as a crash\n"
     "            self._show_status(\n"
     '                _("{name}: stopped after {seconds:.1f} s").format(\n',
     ["test_stop_kills_the_running_check_and_says_so"]),

    ("M4 a check may be built with no project", REGISTRY,
     "    if profile is None:\n        return None, "
     '_("Open a project first — the checks run against the profile\'s config file.")\n',
     "    if False:  # MUTATION: a run against nothing is allowed\n        return None, "
     '_("Open a project first — the checks run against the profile\'s config file.")\n',
     ["test_no_project_is_refused_before_anything_is_built"]),

    ("M5 cosmetic control (a comment must change nothing)", CHECKS,
     "        running = self.is_running()\n",
     "        running = self.is_running()  # MUTATION: cosmetic\n",
     "survive"),
]


def _sweep_pycache() -> None:
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def _run(guards: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    _sweep_pycache()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *guards.split(), "-q", "-rf",
             "-p", "no:cacheprovider"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT: the guard run did not finish within 300 s"
    return proc.returncode, proc.stdout + proc.stderr


def _failed_ids(out: str) -> list:
    return [line.split(" ", 1)[1].split(" - ")[0]
            for line in out.splitlines() if line.startswith("FAILED ")]


def main() -> int:
    guards = os.environ.get("GUARDS", GUARDS)
    print(f"guards: {guards}")
    code, out = _run(guards)
    if code != 0:
        print("BASELINE IS RED — fix that first:\n", out[-2000:])
        return 2
    print("baseline green")

    misses = 0
    for name, rel, old, new, expect in MUTATIONS:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"[SKIP/MISS] {name}: the template matches "
                  f"{original.count(old)} times — refusing")
            misses += 1
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            code, out = _run(guards)
            failed = _failed_ids(out)
            if expect == "survive":
                if failed:
                    print(f"[MISS] {name}: a cosmetic change reddened {failed}")
                    misses += 1
                else:
                    print(f"[OK] {name}: survived, as it must")
                continue
            if not failed:
                print(f"[MISS] {name}: NO failed test — the mutation kills "
                      f"nothing (exit {code})\n{out[-800:]}")
                misses += 1
                continue
            missing = [e for e in expect if not any(e in f for f in failed)]
            if missing:
                print(f"[MISS] {name}: expected these to redden, they did not: "
                      f"{missing}\n  actually failed: {failed}")
                misses += 1
            else:
                print(f"[KILL] {name}: {len(failed)} red — {failed}")
        finally:
            path.write_text(original, encoding="utf-8")
            restored = path.read_text(encoding="utf-8")
            assert restored == original, f"restore FAILED for {rel}"
    _sweep_pycache()
    print(f"\nverdict: {len(MUTATIONS) - misses} as expected, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
