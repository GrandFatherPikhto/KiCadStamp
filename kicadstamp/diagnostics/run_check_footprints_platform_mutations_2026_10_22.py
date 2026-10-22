# kicadstamp/diagnostics/run_check_footprints_platform_mutations_2026_10_22.py
"""Mutation rows for Д-2 (acceptance of plan_2026_10_22_checks_tab, 22.10.2026).

The check's library-directory search asks KiCad where its SETTINGS live, and that answer is
platform-specific: Linux `~/.config/kicad/10.0`, Windows `%APPDATA%\\kicad\\10.0`, macOS
`~/Library/Preferences/kicad/10.0`. One owner (`_kicad_settings_dir`) serves both consumers —
the `kicad_common.json` variable lookup and the user's `fp-lib-table` — and each row below
removes one platform's branch, which must redden the cells that name it:

  M1  Windows keeps its settings in ~/.config again
  M2  macOS keeps its settings in ~/.config
  M3  cosmetic control (a comment must change nothing)

Safeguards, the same as every harness in this package: a template that matches more than once
is refused (a silent miss is indistinguishable from a healthy guard — rule 38); a row that
reddens NOTHING is a MISS; the control row must survive; every file is restored in `finally`
and the restoration is verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_check_footprints_platform_mutations_2026_10_22`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHECK = "tools/check_footprints_vs_library.py"

GUARDS = "tests/checks/test_check_footprints_lib_dir.py"

_WIN_BRANCH = ('    if sys.platform.startswith("win"):\n'
               '        # KiCad itself only ever uses %APPDATA%; a bare HOME is the fallback '
               'for a headless run.\n'
               '        base = os.environ.get("APPDATA") or str(Path.home())\n'
               '        return Path(base) / "kicad" / "10.0"\n')

_DARWIN_BRANCH = ('    if sys.platform == "darwin":\n'
                  '        return Path.home() / "Library" / "Preferences" / "kicad" / "10.0"\n')

MUTATIONS = [
    ("M1 Windows keeps its settings in ~/.config again", CHECK,
     _WIN_BRANCH,
     '    if False:  # MUTATION: every platform keeps its settings in ~/.config\n'
     '        base = os.environ.get("APPDATA") or str(Path.home())\n'
     '        return Path(base) / "kicad" / "10.0"\n',
     ["test_the_settings_directory_on_windows_is_appdata",
      "test_windows_without_appdata_falls_back_to_home",
      "test_the_users_fp_lib_table_is_read_from_the_windows_settings_directory",
      "test_the_common_json_is_the_settings_directory_plus_its_name"]),

    ("M2 macOS keeps its settings in ~/.config", CHECK,
     _DARWIN_BRANCH,
     '    if False:  # MUTATION: macOS is Linux\n'
     '        return Path.home() / "Library" / "Preferences" / "kicad" / "10.0"\n',
     ["test_the_settings_directory_on_macos_is_library_preferences",
      "test_the_common_json_is_the_settings_directory_plus_its_name"]),

    ("M3 cosmetic control (a comment must change nothing)", CHECK,
     '    return Path.home() / ".config" / "kicad" / "10.0"\n',
     '    return Path.home() / ".config" / "kicad" / "10.0"  # MUTATION: cosmetic\n',
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
