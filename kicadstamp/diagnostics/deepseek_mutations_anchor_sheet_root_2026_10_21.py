# kicadstamp/diagnostics/deepseek_mutations_anchor_sheet_root_2026_10_21.py
"""Mutation rows for Ч1 of plan_2026_10_21_root_sheet_anchor_on_flat_schematic.

Two decisions are pinned here, each with ONE host:

  1. the ROOT marker "/" is not a sheet-dictionary USER — the exclusion lives in
     `validation.check_anchor_sheet_configured`, through
     `sheet_names.is_root_sheet` (cells К1-К10 of
     tests/config/test_anchor_sheet_root_sheet.py);
  2. "the dictionary is empty" has TWO causes and ONE wording host —
     `sheet_names.empty_dictionary_hint`, called by the config check AND by
     `sheet_names.sheet_narrowing_disabled_line`, the sentence the GUI's V.3 Log
     line prints verbatim (gui/dock_hub._warn_if_sheet_narrowing_disabled).

Every row removes exactly ONE of those decisions. Safeguards, as in the earlier
harnesses: a template that matches more than once is refused; a row that reddens
NOTHING is a MISS; the control row (a cosmetic comment) must survive; every file
is restored in `finally` and the restoration is verified by text.

Run: `.venv/bin/python kicadstamp/diagnostics/deepseek_mutations_anchor_sheet_root_2026_10_21.py`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

VALIDATION = "kicadstamp/validation.py"
NAMES = "kicadstamp/sheet_names.py"

GUARDS = "tests/config/test_anchor_sheet_root_sheet.py"

# The two branches of the check, together: the root exclusion is ONE fact and
# removing it from only one branch would leave the other's cells green.
_USERS = ('    users = [clone_placement_effective_name(c)\n'
          '             for c in cfg.clone_placements\n'
          '             if not c.retired and c.anchor_sheet\n'
          '             and not is_root_sheet(c.anchor_sheet)]\n'
          '    net_trace_users = [nt.net for nt in cfg.net_traces\n'
          '                       if not nt.retired and nt.anchor_sheet\n'
          '                       and not is_root_sheet(nt.anchor_sheet)]\n')

_HINT_TAIL = ('    if schematic_dir or schematic_files:\n'
              '        return _("the schematic has no sub-sheets (it is a single-page "\n')

_LOG_LINE = ('    return _("sheet names cannot be resolved, so sheet-based narrowing "\n'
             '             "(Extract cluster/tree, anchor_sheet) cannot work: {hint}"\n'
             '             ).format(hint=empty_dictionary_hint(schematic_dir, schematic_files))\n')

MUTATIONS = [
    ("M1 the root marker is a dictionary user again", VALIDATION,
     _USERS,
     '    users = [clone_placement_effective_name(c)\n'
     '             for c in cfg.clone_placements\n'
     '             if not c.retired and c.anchor_sheet]\n'
     '    net_trace_users = [nt.net for nt in cfg.net_traces\n'
     '                       if not nt.retired and nt.anchor_sheet]\n',
     ["test_root_sheet_anchor_is_not_a_dictionary_user_on_a_flat_schematic",
      "test_root_sheet_anchor_is_silent_even_without_any_schematic_source"]),

    ("M2 ANY name passes when the map is empty", VALIDATION,
     "    if _sn or not (users or net_trace_users):\n",
     "    if True:  # MUTATION: any name passes when the map is empty\n",
     ["test_a_non_root_anchor_on_a_flat_schematic_is_refused_with_the_single_page_reason",
      "test_a_non_root_anchor_without_any_schematic_source_is_refused_with_the_config_reason",
      "test_a_placeholder_anchor_is_not_the_root"]),

    ("M3 the configured case falls back to the old 'need schematic_dir' text", NAMES,
     _HINT_TAIL,
     '    if False:  # MUTATION: the configured case keeps the old text\n'
     '        return _("the schematic has no sub-sheets (it is a single-page "\n',
     ["test_the_hint_names_the_real_cause",
      "test_a_non_root_anchor_on_a_flat_schematic_is_refused_with_the_single_page_reason"]),

    ("M4 cosmetic control (a comment must change nothing)", VALIDATION,
     "    if _sn or not (users or net_trace_users):\n",
     "    if _sn or not (users or net_trace_users):  # MUTATION: cosmetic\n",
     "survive"),

    ("M5 the V.3 Log line stops taking the cause from the host", NAMES,
     _LOG_LINE,
     '    return _("This config has no schematic_dir/schematic_files — sheet names "\n'
     '             "cannot be resolved, so sheet-based narrowing (Extract "\n'
     '             "cluster/tree, anchor_sheet) is disabled.")  # MUTATION\n',
     ["test_the_log_line_takes_its_cause_from_the_same_host"]),
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
