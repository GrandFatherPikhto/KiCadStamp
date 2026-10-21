# kicadstamp/diagnostics/deepseek_mutations_role_lookup_cluster_2026_10_21.py
"""Mutation rows for Ч1 of plan_2026_10_21_role_lookup_steals_across_clusters.

Two decisions are pinned here, each with ONE host:

  1. a cluster named by the caller CONFINDS an anchor_role lookup at any
     candidate count — the filter is `role_narrowing.own_cluster_candidates`
     wired in `clone_role_resolver._confine_candidates_to_anchor_cluster`, the
     words are `role_conflict_reasons.role_scope_problems` (cells С1-С8 of
     tests/placement/test_role_lookup_cluster_confined.py);
  2. a board whose Role/Cluster fields KiCad renamed has ONE door —
     `role_cluster_damage.check_role_cluster_fields_intact`, called from
     `validation.run_all_checks` (cells С9-С16 of
     tests/config/test_role_cluster_damage_guard.py).

Every row removes exactly ONE of those decisions. Safeguards, as in the earlier
harnesses: a template that matches more than once is refused; a row that reddens
NOTHING is a MISS; the control row (a cosmetic comment) must survive; every file
is restored in `finally` and the restoration is verified by text.

Run: `.venv/bin/python kicadstamp/diagnostics/deepseek_mutations_role_lookup_cluster_2026_10_21.py`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RESOLVER = "kicadstamp/placement/services/clone_role_resolver.py"
DAMAGE = "kicadstamp/role_cluster_damage.py"
VALIDATION = "kicadstamp/validation.py"
PLANNER = "kicadstamp/net_trace_planner.py"

GUARDS = ("tests/placement/test_role_lookup_cluster_confined.py "
          "tests/config/test_role_cluster_damage_guard.py "
          "tests/placement/test_role_narrowing.py "
          "tests/net/test_net_trace_from_role.py "
          "tests/net/test_internode_capture_modules.py")

_CONFINE = ("    if not anchor_cluster or hint:\n"
            "        return list(candidates)\n"
            "    confined = own_cluster_candidates(adapter, candidates, anchor_cluster)\n")

MUTATIONS = [
    ("M1 the confinement is cascade-shaped again (only at 2+)", RESOLVER,
     _CONFINE,
     "    if not anchor_cluster or hint or len(candidates) <= 1:  # MUTATION: only 2+\n"
     "        return list(candidates)\n"
     "    confined = own_cluster_candidates(adapter, candidates, anchor_cluster)\n",
     ["test_c1_a_single_foreign_candidate_is_not_taken",
      "test_c2_a_single_untagged_candidate_is_not_taken",
      "test_c8_the_live_branch_confines_the_cluster_too"]),

    ("M2 the field is not checked (every candidate is 'own')", RESOLVER,
     "    confined = own_cluster_candidates(adapter, candidates, anchor_cluster)\n",
     "    confined = list(candidates)  # MUTATION: the Cluster field is not checked\n",
     ["test_c1_a_single_foreign_candidate_is_not_taken",
      "test_c2_a_single_untagged_candidate_is_not_taken",
      "test_c5_two_foreign_candidates_are_both_named_as_not_taken"]),

    ("M3 the old 'narrow only if it reduces the set' answer for a foreign set",
     RESOLVER,
     "    if not confined:\n"
     "        raise_role_outside_cluster(\n"
     "            label, anchor_role, anchor_cluster,\n"
     "            role_scope_problems(adapter, anchor_role, anchor_cluster, []))\n"
     "    return confined\n",
     "    if not confined:  # MUTATION: leaves the old cascade answer\n"
     "        return list(candidates)\n"
     "    return confined\n",
     ["test_c1_a_single_foreign_candidate_is_not_taken",
      "test_c5_two_foreign_candidates_are_both_named_as_not_taken"]),

    # С9 calls the guard function DIRECTLY — it pins the signature and the
    # words, not the doorway — so M4 is the only row that may move it. M5
    # removes the doorway itself and therefore kills the two paths that go
    # through it (С15 = ApplyPipeline.run, С16 = run_apply) plus the ordering
    # cell; expecting С9 here would have been a lie about what it guards.
    ("M4 the refusal stops naming the candidates (empty problems)", RESOLVER,
     "        raise_role_outside_cluster(\n"
     "            label, anchor_role, anchor_cluster,\n"
     "            role_scope_problems(adapter, anchor_role, anchor_cluster, []))\n",
     "        raise_role_outside_cluster(\n"
     "            label, anchor_role, anchor_cluster, [])  # MUTATION: no names\n",
     ["test_c1_a_single_foreign_candidate_is_not_taken",
      "test_c2_a_single_untagged_candidate_is_not_taken"]),

    ("M5 the guard is not called at all (run_all_checks)", VALIDATION,
     "    check_role_cluster_fields_intact(adapter)\n",
     "",
     ["test_c15_the_gui_redraw_path_refuses_before_any_move",
      "test_c16_the_cli_apply_path_refuses_with_the_same_text",
      "test_the_guard_is_checked_before_any_config_check"]),

    ("M6 the signature widens to 'Role/Cluster missing' without KiLib_Generator",
     DAMAGE,
     "        if not adapter.has_field(fp, KILIB_GENERATOR_FIELD_NAME):\n"
     "            continue\n",
     "        # MUTATION: the library field is not required\n",
     ["test_c11_an_untagged_part_is_silent"]),

    ("M7 the guard asks the VALUE in force instead of the physical field", DAMAGE,
     "        missing = [name for name in (ROLE_FIELD_NAME, CLUSTER_FIELD_NAME)\n"
     "                   if not adapter.has_field(fp, name)]\n",
     "        missing = [name for name in (ROLE_FIELD_NAME, CLUSTER_FIELD_NAME)\n"
     "                   if not adapter.get_field_value(fp, name)]  # MUTATION\n",
     ["test_c14_our_stored_value_does_not_hide_the_damage"]),

    # ── the FLAG (hint) — «Ответ Claude на находку Ч1» ──────────────────────
    # `test_net_from_role_narrowed_by_the_records_own_cluster` does NOT belong
    # here on purpose: its single hinted candidate is the same in both modes, so
    # it pins «the hint picks its instance», not «the flag reached the resolver».
    # Only a role whose copper end stands in ANOTHER cluster (the FPGA bridge)
    # can tell the two modes apart.
    ("M8 the hint flag is NOT passed by the copper end (_item_net_name)",
     PLANNER,
     "        label=label, quiet=quiet, cluster_is_hint=True)\n",
     "        label=label, quiet=quiet)  # MUTATION: the flag is not passed\n",
     ["test_the_new_record_is_anchored_on_the_side_it_redraws_from",
      "test_copper_between_clusters_that_share_a_role_gets_a_record_from_the_second_tier"]),

    ("M9 the resolver IGNORES the hint flag (always confines)", RESOLVER,
     "    if not anchor_cluster or hint:\n",
     "    if not anchor_cluster:  # MUTATION: the flag is ignored\n",
     ["test_c17_a_hint_never_refuses_a_unique_foreign_role",
      "test_the_new_record_is_anchored_on_the_side_it_redraws_from"]),

    ("M10 the resolver is ALWAYS soft (the addressing case lost)",
     RESOLVER,
     "    if not anchor_cluster or hint:\n",
     "    if not anchor_cluster or True:  # MUTATION: always a hint\n",
     ["test_c1_a_single_foreign_candidate_is_not_taken",
      "test_c2_a_single_untagged_candidate_is_not_taken",
      "test_c5_two_foreign_candidates_are_both_named_as_not_taken"]),

    ("K1 a cosmetic comment beside the filter (CONTROL)", RESOLVER,
     "    confined = own_cluster_candidates(adapter, candidates, anchor_cluster)\n",
     "    confined = own_cluster_candidates(adapter, candidates, anchor_cluster)  # control\n",
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
