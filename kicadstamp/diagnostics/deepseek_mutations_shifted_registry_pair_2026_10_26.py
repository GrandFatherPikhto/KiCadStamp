# kicadstamp/diagnostics/deepseek_mutations_shifted_registry_pair_2026_10_26.py
"""Mutation rows for Ч1 of plan_2026_10_26_select_cell_shifted_registry_pair.

The promise (rule §2 as Denis revised it on 26.10 — «место решает первым»): a
registry pair whose live item is AWAY from the planned place AND on another net is
a SHIFTED NUMBER — it owns nothing and claims nothing — while a pair AT the place
is ownership even when a field differs, and a pair away on the SAME net stays
today's «selected anyway». One row per half of that sentence:

  M1  the net is not compared at all                     -> the shift is never seen
  M2  the PLACE is not asked first                       -> hand-edited copper is
                                                            thrown out as "shifted"
  M3  the width/identity is asked instead of the net     -> "away, another width" is
                                                            misfiled as shifted
  M4  a shifted key still OCCUPIES the key               -> the record loses its copper
  M5  every owned uuid is released (not only this call's) -> a foreign key loses its item
  M6  an "edited on the board" pair is counted as "away"  -> the line lies about why
  K1  cosmetic control (a comment on a def line)          -> NOTHING may redden

Safeguards as everywhere in this package (rule 38): a template matching more than
once is refused, a row that reddens NOTHING is a MISS, the control must survive,
and every touched file is restored in `finally` with the restore verified byte for
byte (two files are mutated by different rows, so each row restores its own).

Run: `.venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_shifted_registry_pair_2026_10_26`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GUARDS = "tests/net/test_shifted_registry_pair.py"
MATCH = "kicadstamp/registry_match.py"
SELECT = "gui/select_cell.py"

CELL_SHIFT = "test_a_shifted_key_neither_owns_its_item_nor_blocks_the_real_one"
CELL_SHIFT_NOT_SELECTED = "test_the_shifted_pair_itself_is_not_selected"
CELL_EDITED = "test_a_pair_at_the_place_with_another_width_is_ownership"
CELL_AWAY = "test_a_pair_away_on_the_same_net_is_selected_anyway"
CELL_OUTSIDE = "test_a_key_outside_the_call_keeps_its_ownership"
CELL_REGISTRY_ONLY = "test_the_registry_only_door_drops_only_the_shifted_pair"
CELL_DRY_RUN = "test_the_dry_run_map_gives_both_records_their_own_copper"

#: The two lines of the verdict, and the one of the matcher's tier 1.
VERDICT = ("    if same_place(match.live, match.command):\n"
           "        return (PAIR_AGREES if same_identity(match.live, match.command)\n"
           "                else PAIR_EDITED)\n"
           "    return (PAIR_AWAY if same_net(match.live, match.command) else PAIR_SHIFTED)\n")

NET_RETURN = '    return getattr(live_item, "net_name", None) == expected\n'

TIER1 = "        if entry is not None and entry.uuid not in released:\n"

RELEASE = "    released = set(shifted_entries(reg, planned, live_items=live_items).values())\n"

EDITED_COUNT = ("            if pair_verdict(m) == PAIR_EDITED:\n"
                "                edited += 1\n"
                "            else:\n"
                "                refused += 1\n")

NET_DEF = "def same_net(live_item, planned, net: Optional[str] = None) -> bool:"

MUTATIONS = [
    # 1. Without the net the rule cannot see a shifted pair at all.
    ("M1 the net is not compared", MATCH, NET_RETURN,
     "    return True  # MUTATION: every pair agrees on its net\n",
     [CELL_SHIFT, CELL_REGISTRY_ONLY]),

    # 2. The place decides first; without it the hand-edited copper is thrown out
    # (and so is copper that is away with another width). The registry-only door
    # answers by the CELL FRAME (`pair_agrees_with_record`), not by this function,
    # so it is not expected to redden here — M1 covers it through the shared net.
    ("M2 the place is not asked first", MATCH, VERDICT,
     "    if same_identity(match.live, match.command):\n"
     "        return PAIR_AGREES  # MUTATION: the place is not consulted\n"
     "    return PAIR_SHIFTED\n",
     [CELL_EDITED, CELL_AWAY]),

    # 3. The identity (width/layer) is asked where the NET must be asked.
    ("M3 identity asked instead of the net", MATCH, VERDICT,
     "    if same_place(match.live, match.command):\n"
     "        return (PAIR_AGREES if same_identity(match.live, match.command)\n"
     "                else PAIR_EDITED)\n"
     "    return (PAIR_AWAY if same_identity(match.live, match.command)\n"
     "            else PAIR_SHIFTED)  # MUTATION: away + another width = shifted\n",
     [CELL_AWAY]),

    # 4. The shifted key keeps occupying the key: the record never looks further.
    ("M4 a shifted key still occupies the key", MATCH, TIER1,
     "        if entry is not None:  # MUTATION: the shifted key is not skipped\n",
     [CELL_SHIFT, CELL_SHIFT_NOT_SELECTED]),

    # 5. Ownership weakened for EVERY entry, not only for this call's commands.
    ("M5 every owned uuid is released", MATCH, RELEASE,
     "    released = {e.uuid for e in reg.entries.values()}  # MUTATION\n",
     [CELL_OUTSIDE]),

    # 6. The reason a pair is selected must not lie.
    ("M6 edited pairs counted as away", SELECT, EDITED_COUNT,
     "            refused += 1  # MUTATION: edited pairs counted as away\n",
     [CELL_EDITED]),

    # K1 — the control: a comment changes, the behaviour does not.
    ("K1 cosmetic control (a comment on a def line)", MATCH, NET_DEF,
     NET_DEF + "  # MUTATION: cosmetic",
     "survive"),
]


def _sweep_pycache() -> None:
    for directory in ROOT.rglob("__pycache__"):
        shutil.rmtree(directory, ignore_errors=True)


def _run(guards: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    _sweep_pycache()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *guards.split(), "-q", "-rf",
             "-p", "no:cacheprovider"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT: the guard run did not finish within 600 s"
    return proc.returncode, proc.stdout + proc.stderr


def _failed_ids(out: str) -> list:
    return [line.split(" ", 1)[1].split(" - ")[0]
            for line in out.splitlines() if line.startswith("FAILED ")]


def _verdict(name: str, failed: list, expect) -> int:
    """Print one row's verdict; return 1 for a miss, 0 for as-expected."""
    if expect == "survive":
        if failed:
            print(f"[MISS] {name}: a cosmetic change reddened {failed}")
            return 1
        print(f"[OK] {name}: survived, as it must")
        return 0
    if not failed:
        print(f"[MISS] {name}: NO failed test — the mutation kills nothing")
        return 1
    missing = [e for e in expect if not any(e in f for f in failed)]
    if missing:
        print(f"[MISS] {name}: expected these to redden, they did not: {missing}\n"
              f"  actually failed: {failed}")
        return 1
    print(f"[KILL] {name}: {len(failed)} red — {failed}")
    return 0


def main() -> int:
    guards = os.environ.get("GUARDS", GUARDS)
    print(f"guards : {guards}")
    print(f"mutated: {MATCH}, {SELECT}")
    code, out = _run(guards)
    if code != 0:
        print("BASELINE IS RED — fix that first:\n", out[-2000:])
        return 2
    print("baseline green")

    misses = 0
    cache: dict[Path, str] = {}
    for name, rel, old, new, expect in MUTATIONS:
        path = ROOT / rel
        original = cache.setdefault(path, path.read_text(encoding="utf-8"))
        if original.count(old) != 1:
            print(f"[SKIP/MISS] {name}: the template matches "
                  f"{original.count(old)} times — refusing")
            misses += 1
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            _code, out = _run(guards)
            misses += _verdict(name, _failed_ids(out), expect)
        finally:
            path.write_text(original, encoding="utf-8")
            assert path.read_text(encoding="utf-8") == original, \
                f"restore FAILED for {rel}"

    _sweep_pycache()
    print(f"\nverdict: {len(MUTATIONS) - misses} as expected, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
