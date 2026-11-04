# kicadstamp/diagnostics/deepseek_mutations_in_place_entity_2026_11_04.py
"""Mutation rows for plan_2026_10_28_entity_outside_trees_in_place (commit Б).

The promise (Denis, 28.10): a record that NO tree places is READ at its live
place — its pose, its role map, its refusals — and a record a tree DOES place is
decided by the tree, byte for byte. Every row below removes one half of that
sentence and must redden the cells of tests/trees/
test_entity_placement_in_place.py that pin it:

  M1   the branch answers [] always            -> nothing materializes (1, 2, 8, 11)
  M2   the pipeline never arms the branch      -> the flag does nothing (2, 7)
  M3   a record a TREE places goes in too      -> the live read is made twice (2)
  M4   --only/--cluster are not asked          -> the board is read for a record
                                                  the run never requested (3, 12)
  M5   the dry-run gate is dropped             -> a real Apply may write (7)
  M6   the role map is not carried             -> the twin swap is back (1, 11)
  M7   the adapter is not checked              -> every config-only caller crashes (9)
  M8   the selection question is not asked      -> a record appears without the
                                                  user's selection (10)
  M9   strict=False                            -> a guessed pose becomes copper (13)
  M10  the mirror guard is dropped             -> copper on the other side (14)
  M11  one WARNING is downgraded to DEBUG      -> the reason is invisible again,
                                                  which is what §2 forbids (4)
  K1   cosmetic control (a comment)            -> NOTHING may redden

Safeguards as everywhere in this package (rule 38): a template matching more than
once is refused, a row that reddens NOTHING is a MISS, the control must survive,
and every touched file is restored in `finally` with the restore verified byte for
byte.

Run: `.venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_in_place_entity_2026_11_04`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GUARDS = "tests/trees/test_entity_placement_in_place.py"
IN_PLACE = "kicadstamp/placement/in_place_entities.py"
ENTITY = "kicadstamp/placement/entity_placement.py"
PIPE = "kicadstamp/apply_pipeline.py"

C_ONE = "test_a_record_outside_trees_is_materialized_at_its_live_place"
C_TREE = "test_a_record_a_tree_places_is_not_read_live_again"
C_AXES = "test_the_selection_axes_are_asked_before_any_board_read"
C_NO_PLACE = "test_a_record_with_no_live_place_is_one_warning_and_the_rest_proceed"
C_FLAG = "test_the_flag_off_is_todays_behaviour_and_on_needs_a_dry_run"
C_NONE = "test_without_a_board_the_branch_is_silent"
C_SELECTION = "test_a_cell_that_needs_a_selection_is_refused_with_one_warning"
C_TWINS = "test_two_instances_of_one_cell_each_carry_their_own_refs"
C_NORIGID = "test_the_strict_read_refuses_a_non_rigid_cluster_for_real"
C_MIRROR = "test_a_mirror_disagreement_is_refused_with_one_warning"
C_TREES0 = "test_the_hp_power_shape_with_no_trees_materializes_every_record"

#: The branch's front door — both guards live on one line pair.
FRONT_DOOR = ('    if adapter is None or not getattr(cfg, "entities", None):\n'
              "        return []\n")
#: The pipeline's gate.
GATE = "    if in_place:\n"
#: "A tree places it — the tree decides".
TREE_WINS = ("        if _find_entity_node(linked, entity.name):\n"
             "            continue                       # a tree places it: the tree decides\n")
#: The two selection axes.
AXES = ("    if only_set is not None \\\n"
        "            and not ({entity.name, entity.cluster or entity.name} & only_set):\n"
        "        return False\n")
#: The read-only gate of the flag.
DRY_GATE = ("        if in_place_entities and not dry_run:\n"
            '            raise ValueError(_("in_place_entities is read-only: pass dry_run=True"))\n')
#: The role map the frame was fitted against.
REFS = "                                    refs=dict(frame.role_to_ref))\n"
#: The selection question.
ASKED = ("        if clone_uses_selection_mode(probe, adapter=adapter, cell=cell,\n"
         "                                     sheet_names=sheet_names):\n")
#: The rigidity of the read.
STRICT = "sheet_names, strict=True, refresh=False)\n"
#: The side the record and the board must agree on.
MIRROR = '        if bool(getattr(entity, "mirror", False)) != frame.mirror:\n'

CONTROL = "def _wanted(entity, only_set: set | None, cluster_paths: list | None) -> bool:\n"

#: The ONE warning that carries a live-read REFUSAL (a ValidationError): the line
#: §2 of the plan requires to be VISIBLE — the very reason «Select cell» used to
#: say "reason — in the Log above" and show nothing.
WARN_LINE = ('            logger.warning(\n'
             '                _("Entity materialization: {name!r} is placed by no tree and has "\n'
             '                  "no live place ({reason}) — its copper is not planned")\n'
             "                .format(name=entity.name, reason=exc))\n")

MUTATIONS = [
    ("M1 the branch answers [] always", IN_PLACE, FRONT_DOOR,
     "    return []  # MUTATION: no record outside trees ever materializes\n",
     [C_ONE, C_TREE, C_TREES0, C_TWINS]),

    ("M2 the pipeline never arms the branch", ENTITY, GATE,
     "    if False:  # MUTATION: the flag does nothing\n",
     [C_TREE, C_FLAG]),

    ("M3 a record a tree places is materialized again", IN_PLACE, TREE_WINS,
     "        # MUTATION: the tree no longer decides\n",
     [C_TREE]),

    ("M4 the selection axes are not asked", IN_PLACE, AXES,
     "    return True  # MUTATION: --only/--cluster ignored\n",
     [C_AXES]),

    ("M5 the dry-run gate is dropped", PIPE, DRY_GATE,
     "        pass  # MUTATION: the reading flag is armed for a real run too\n",
     [C_FLAG]),

    ("M6 the role map is not carried into the clone", IN_PLACE, REFS,
     "                                    refs={})  # MUTATION: no role map\n",
     [C_ONE, C_TWINS]),

    ("M7 the adapter is not checked", IN_PLACE, FRONT_DOOR,
     '    if not getattr(cfg, "entities", None):  # MUTATION: adapter unchecked\n'
     "        return []\n",
     [C_NONE]),

    ("M8 the selection question is not asked", IN_PLACE, ASKED,
     "        if False:  # MUTATION: the cell's own mode is never asked\n",
     [C_SELECTION]),

    ("M9 the read is not strict", IN_PLACE, STRICT,
     "sheet_names, strict=False, refresh=False)  # MUTATION\n",
     [C_NORIGID]),

    ("M10 the mirror guard is dropped", IN_PLACE, MIRROR,
     "        if False:  # MUTATION: the record and the board may disagree\n",
     [C_MIRROR]),

    ("M11 the skip reason is downgraded to DEBUG", IN_PLACE,
     WARN_LINE,
     '            logger.debug(\n'
     '                _("Entity materialization: {name!r} is placed by no tree and has "\n'
     '                  "no live place ({reason}) — its copper is not planned")\n'
     "                .format(name=entity.name, reason=exc))\n",
     [C_NO_PLACE]),

    # K1 — the control: a comment changes, the behaviour does not.
    ("K1 cosmetic control (a comment on a def line)", IN_PLACE, CONTROL,
     CONTROL + "    # MUTATION: cosmetic\n",
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
    print(f"mutated: {IN_PLACE}, {ENTITY}, {PIPE}")
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
