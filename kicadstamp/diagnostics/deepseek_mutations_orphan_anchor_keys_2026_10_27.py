# kicadstamp/diagnostics/deepseek_mutations_orphan_anchor_keys_2026_10_27.py
"""Mutation rows for Ч1 of plan_2026_10_27_orphan_anchor_keys_own_nothing.

The promise (Denis, 27.10 — «сирота — по ЛЮБОМУ префиксу; prune уже удаляет по этому
определению для всех»): a registry key whose record is gone from the config (1) owns
nothing when READ, (2) does not occupy its item in the at-current-place pass or in
`match_planned_copper`, and (3) prune never names for deletion a uuid a LIVE key of
this run holds — nor one the board does not carry. One row per half of that sentence:

  M1  an orphan key owns its copper again at READ      -> the 18 subtracted items of
                                                          v103 come back
  M2  an orphan uuid stays in the pass's `owned`       -> the record standing on that
                                                          copper is never adopted
  M2b `match_planned_copper` ignores `orphan_uuids`    -> the selector keeps the item
                                                          blocked by a dead key
  M3  prune deletes the uuid a live key holds          -> a run deletes the copper its
                                                          own record has just bound
  M4  prune names a DEAD uuid for deletion             -> a board write on a missing id
                                                          and a Log that lies
  M4b prune repeats a uuid already in `to_delete`      -> one `remove_by_ids` packet
                                                          carries the same id twice (Д-1
                                                          of the acceptance)
  M5  «orphan» is judged for `name:` only              -> a `net:` key of a removed
                                                          record is subtracted again
  M6  the anchor set is built from nothing             -> every key is an orphan: a LIVE
                                                          record loses its copper
  K1  cosmetic control (a comment on a def line)       -> NOTHING may redden

Safeguards as everywhere in this package (rule 38): a template matching more than once
is refused, a row that reddens NOTHING is a MISS, the control must survive, and every
touched file is restored in `finally` with the restore verified byte for byte.

Run: `.venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_orphan_anchor_keys_2026_10_27`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GUARDS = "tests/net/test_orphan_anchor_keys_own_nothing.py"
NARROWING = "kicadstamp/selection_narrowing.py"
ADOPT = "kicadstamp/adopt_at_current_place.py"
MATCH = "kicadstamp/registry_match.py"
REGISTRY = "kicadstamp/registry.py"
ANCHORS = "kicadstamp/anchor_ids.py"

CELL_READ = "test_copper_under_an_orphan_key_is_read_and_named"
CELL_LINE = "test_the_orphan_line_names_the_anchor_and_says_what_happened"
CELL_LIVE_RECORD = "test_copper_under_a_live_records_key_is_still_subtracted"
CELL_NET_ORPHAN = "test_a_net_orphan_is_read_like_a_name_orphan"
CELL_PASS = "test_the_current_place_pass_adopts_copper_an_orphan_key_held"
CELL_MATCHER = "test_match_planned_copper_without_orphan_uuids_is_unchanged"
CELL_LIVE_UUID = "test_prune_does_not_delete_a_uuid_a_live_key_of_this_run_holds"
CELL_KNOWN_ANCHOR = "test_a_known_anchor_filtered_out_is_not_pruned"
CELL_DEAD_UUID = "test_a_dead_uuid_is_not_named_for_deletion"
CELL_UUID_ONCE = "test_one_uuid_is_named_for_deletion_once"

#: The read door's orphan branch (M1 removes it).
READ_BRANCH = ("        if key in orphan_set:\n"
               "            # The key's record is GONE from the config: it owns nothing, so this\n"
               "            # copper is read, not subtracted — and the caller says so in its own\n"
               "            # line (`CopperSubtraction.orphan`).\n"
               "            kept.append(item)\n"
               "            label = _anchor_label(key)\n"
               "            orphan[label] = orphan.get(label, 0) + 1\n"
               "            continue\n")

#: The pass's subtraction of orphan uuids from `owned` (M2 removes it).
PASS_OWNED = ("    owned -= set(orphan_uuids(via_reg.entries, track_reg.entries,\n"
              "                              config_anchor_ids(cfg)))\n")

#: The matcher's subtraction of the same set (M2b removes it).
MATCH_OWNED = ("    owned = {e.uuid for e in reg.entries.values()} - set(orphan_uuids or ())\n")

#: The prune's two guards: a uuid a LIVE key holds (M3), a uuid the board lacks (M4).
PRUNE_LIVE = ("            if entry.uuid in live_uuids:\n"
              "                logger.info(_(\"  prune: {key} no longer appears in config — the key \"\n"
              "                              \"is dropped, the item ({uuid}) stays: a key of this \"\n"
              "                              \"run holds it\")\n"
              "                            .format(key=key, uuid=entry.uuid))\n"
              "                continue\n")
PRUNE_DEAD = "            if entry.uuid not in live_by_uuid:\n"
PRUNE_DUPLICATE = "            if entry.uuid in to_delete:\n"

#: The orphan definition (M5 narrows it to `name:` — the mirror of Denis's M6 note).
ORPHAN_DEF = "    return anchor_part(key) not in anchors\n"

#: The anchor builder's result (M6 empties it).
ANCHORS_RETURN = "    return ids\n"

CONTROL = "def reconcile(self, planned_cmds,\n"

MUTATIONS = [
    # 1. An orphan key owns its copper again: the read subtracts it.
    ("M1 an orphan key owns its copper again at READ", NARROWING, READ_BRANCH,
     "        # MUTATION: an orphan key owns its copper again\n",
     [CELL_READ, CELL_LINE, CELL_NET_ORPHAN]),

    # 2. The pass keeps an orphan's uuid in `owned`: the record standing on that
    # copper is never adopted, and the same run's prune deletes it.
    ("M2 an orphan uuid stays in the pass's owned", ADOPT, PASS_OWNED,
     "    # MUTATION: an orphan uuid keeps its ownership in the pass\n",
     [CELL_PASS]),

    # 2b. The matcher ignores the set: a dead key blocks the item it still points at.
    ("M2b match_planned_copper ignores orphan_uuids", MATCH, MATCH_OWNED,
     "    owned = {e.uuid for e in reg.entries.values()}  # MUTATION\n",
     [CELL_MATCHER]),

    # 3. Prune deletes the uuid a live key of this run holds.
    ("M3 prune deletes the uuid a live key holds", REGISTRY, PRUNE_LIVE,
     "            # MUTATION: a uuid a live key holds is deleted anyway\n",
     [CELL_LIVE_UUID]),

    # 4. Prune names a dead uuid for deletion (a board write on a missing id).
    ("M4 prune names a dead uuid for deletion", REGISTRY, PRUNE_DEAD,
     "            if False:  # MUTATION: a dead uuid is named for deletion\n",
     [CELL_DEAD_UUID]),

    # 4b. Prune repeats a uuid the command loop already named for deletion (Д-1 of
    # the acceptance): one remove_by_ids packet carries the id twice.
    ("M4b prune repeats a uuid already in to_delete", REGISTRY, PRUNE_DUPLICATE,
     "            if False:  # MUTATION: the uuid is named a second time\n",
     [CELL_UUID_ONCE]),

    # 5. «Orphan» judged for `name:` only (Denis's M6): a `net:` key of a removed
    # record is «another record's copper» again.
    ("M5 «orphan» is judged for name: only", ANCHORS, ORPHAN_DEF,
     "    return (anchor_part(key).startswith(\"name:\")  # MUTATION\n"
     "            and anchor_part(key) not in anchors)\n",
     [CELL_NET_ORPHAN]),

    # 6. The anchor set is built from nothing: EVERY key is an orphan — the mirror
    # defect, where a live record loses its copper.
    ("M6 the anchor set is built from nothing", ANCHORS, ANCHORS_RETURN,
     "    return set()  # MUTATION: every key is an orphan\n",
     [CELL_LIVE_RECORD, CELL_KNOWN_ANCHOR]),

    # K1 — the control: a comment changes, the behaviour does not.
    ("K1 cosmetic control (a comment on a def line)", REGISTRY, CONTROL,
     CONTROL + "                  # MUTATION: cosmetic\n",
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
    print(f"mutated: {NARROWING}, {ADOPT}, {MATCH}, {REGISTRY}, {ANCHORS}")
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
