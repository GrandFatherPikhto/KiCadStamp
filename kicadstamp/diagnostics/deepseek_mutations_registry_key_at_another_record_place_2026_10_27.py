# kicadstamp/diagnostics/deepseek_mutations_registry_key_at_another_record_place_2026_10_27.py
"""Mutation rows for Ч1 of plan_2026_10_27_registry_key_at_another_record_place.

The promise (Denis, 27.10): a pair that is AWAY from its own record but EXACTLY at
ANOTHER record's place of the same instance — that record ACCEPTING the item's net —
is a SHIFTED NUMBER at any net, and a released key whose record found no copper of
its own is DROPPED, so reconcile can never delete the real owner's copper. One row
per half of that sentence:

  M1  the neighbour's net is not asked at all          -> a neighbour on ANOTHER net
                                                          (or with no net) releases
                                                          a key that is not its own
  M2  a neighbour of ANOTHER KIND may witness          -> a via's one point is
                                                          compared with a track's two
                                                          ends (the Ч0 IndexError)
  M3  the companion is not written                     -> a released key keeps a
                                                          foreign uuid for reconcile
  M3b the companion asks the CLAIMS, not the bindings  -> an ambiguous claim keeps a
                                                          released key, and one uuid
                                                          ends up under two keys (Д-1)
  M4  the companion drops REBOUND keys too             -> a repaired rotation loses the
                                                          bindings it just made
  M5  the registry-only door keeps the net-only rule   -> that door keeps a pair every
                                                          other door frees
  M6  the record's OWN net is not asked               -> the net-only half of the
                                                          rule disappears
  K1  cosmetic control (a comment on a def line)       -> NOTHING may redden

Safeguards as everywhere in this package (rule 38): a template matching more than
once is refused, a row that reddens NOTHING is a MISS, the control must survive, and
every touched file is restored in `finally` with the restore verified byte for byte.

Run: `.venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_registry_key_at_another_record_place_2026_10_27`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GUARDS = "tests/net/test_registry_key_at_another_record_place.py"
MATCH = "kicadstamp/registry_match.py"
ADOPT = "kicadstamp/adopt_at_current_place.py"
ABSENT = "kicadstamp/absent_copper_prune.py"

CELL_RING = "test_the_ring_is_released_and_every_record_finds_its_own_copper"
CELL_ROTATION = "test_a_full_rotation_of_eighteen_records_is_repaired"
CELL_NEIGHBOUR_NET = "test_a_neighbour_with_another_net_does_not_release_the_key"
CELL_NEIGHBOUR_SILENT = "test_a_neighbour_without_a_declared_net_is_not_a_witness"
CELL_KIND = "test_a_record_of_another_kind_is_never_a_witness"
CELL_DROP = "test_a_released_key_whose_record_found_nothing_is_dropped"
CELL_REDRAW = "test_redraw_leaves_no_nobodys_copper_behind"
CELL_REGISTRY_DOOR = "test_the_registry_only_door_drops_a_neighbour_place_pair"
CELL_AT_PLACE = "test_at_the_planned_place_the_rotation_moves_nothing"
CELL_OTHER_NET = "test_away_on_another_net_at_nobodys_place_is_still_shifted"
CELL_AMBIGUOUS = "test_a_released_key_with_an_ambiguous_claim_is_still_dropped"

#: The net gate of the place rule — the three lines M1 removes.
NET_GATE = ("    if not declared or expected is None:\n"
            "        return False\n"
            "    return getattr(live_item, \"net_name\", None) == expected\n")

#: The same-kind filter of the frame vocabulary (M2 removes the kind half).
KIND_GATE = ("        if slot == own or slot[0] != kind:\n"
             "            continue\n")

#: The companion's write (M3 removes it) and its skip of BOUND keys (M4 removes it;
#: M3b asks the CLAIMS instead of the bindings — the Д-1 defect of the acceptance).
DROP_WRITE = ("        if write:\n"
              "            regs[kind].forget_key(key)\n")
DROP_SKIP_REBOUND = ("        if (kind, key) in bound_keys:\n"
                     "            continue                     # the record IS bound to its own copper\n")
DROP_SKIP_CLAIMED = ("        if (kind, key) in key_to_item:  # MUTATION: claims, not bindings\n"
                     "            continue\n")

#: The registry-only door's shared verdict (M5 puts its own old rule back).
REGISTRY_RULE = ("            net_agrees = expected is None or same_net(live_item, rec, expected)\n"
                 "            if pair_is_shifted(at_place=False, other_record_place=other_place,\n"
                 "                               net_agrees=net_agrees):\n")

#: The at-current-place pass's own net fact (M6 stops asking it).
OWN_NET = ("        net_agrees = not (declared and expected is not None\n"
           "                          and item.net_name != expected)\n")

RULE_DEF = "def pair_is_shifted(*, at_place: bool, other_record_place: bool,"

MUTATIONS = [
    # 1. Without the neighbour's net a place match alone releases the key.
    ("M1 the neighbour's net is not asked", MATCH, NET_GATE,
     "    return True  # MUTATION: any neighbour accepts any net\n",
     [CELL_NEIGHBOUR_NET, CELL_NEIGHBOUR_SILENT]),

    # 2. Another KIND is allowed to witness: a via's one point meets a track's two.
    ("M2 a neighbour of another kind may witness", ABSENT, KIND_GATE,
     "        if slot == own:  # MUTATION: another kind may witness\n"
     "            continue\n",
     [CELL_KIND]),

    # 3. The companion is not written: the released key keeps a foreign uuid.
    ("M3 the companion is not written", ADOPT, DROP_WRITE,
     "        # MUTATION: the released key is kept\n",
     [CELL_DROP]),

    # 3b. The companion asks the CLAIMS instead of the BINDINGS (Д-1 of the
    # acceptance): an ambiguous claim counts as «found its own copper», the released
    # key stays, and the uuid it holds ends up under two keys.
    ("M3b the companion asks the claims, not the bindings", ADOPT,
     DROP_SKIP_REBOUND, DROP_SKIP_CLAIMED,
     [CELL_AMBIGUOUS]),

    # 4. The companion drops even the keys that DID rebind their record.
    ("M4 rebound keys are dropped too", ADOPT, DROP_SKIP_REBOUND,
     "        # MUTATION: even a rebound key is dropped\n",
     [CELL_RING, CELL_ROTATION, CELL_AT_PLACE, CELL_REDRAW]),

    # 5. The registry-only door falls back to the net-only rule of 26.10.
    ("M5 the registry-only door keeps the old rule", ABSENT, REGISTRY_RULE,
     "            if expected is not None and not same_net(live_item, rec, expected):"
     "  # MUTATION\n",
     [CELL_REGISTRY_DOOR]),

    # 6. The record's OWN net is not asked: the net-only half of the rule disappears,
    # so a pair away on another net on nobody's place keeps its key.
    ("M6 the record's own net is not asked", ADOPT, OWN_NET,
     "        net_agrees = True  # MUTATION: the own net is not compared\n",
     [CELL_OTHER_NET]),

    # K1 — the control: a comment changes, the behaviour does not.
    ("K1 cosmetic control (a comment on a def line)", MATCH, RULE_DEF,
     RULE_DEF + "  # MUTATION: cosmetic",
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
    print(f"mutated: {MATCH}, {ADOPT}, {ABSENT}")
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
