# kicadstamp/diagnostics/run_node_redraw_from_live_parent_mutations_2026_11_09.py
"""Mutation rows for «a child node's Redraw is placed from its parent ON THE BOARD»
(plan_2026_11_09_node_redraw_from_live_parent).

The defect (live, 09.11, HP-Power-v099, tree `switch_a`, node `conn_out_sw_a`): the Read
resolved the parent from the board (69.994, 8.014 — right), while Redraw of the same numbers let
`ApplyPipeline(only=[ref])` lay the node out from the tree's anchor and the Grid Origin, which put
the pads below the board. Each row removes exactly ONE decision of the fix and must turn a NAMED
cell red:

  M1  the base is the TREE's, not the live parent's           (trees_base_pose.redraw_target)
  M2  the base's rotation is ignored in the offset            (redraw_target)
  M3  the node's own `rotation` is not read                   (redraw_target)
  M4  an unreadable parent FALLS BACK to the tree layout       (cascade._node_position_override)
  M5  a MIRRORED base is not refused                          (redraw_target)
  M6  the top of a module-embedded tree is not refused         (trees_dock._redraw_edited_node)
  M7  the override is not handed to the pipeline               (cascade.run_single_node_redraw_worker)

and the three holes of Claude's §2 (second acceptance):

  M9  the `polar` branch of the node's offset is not read       (trees_base_pose.node_offset_of)
  M10 the dock's `sheet_names` are replaced by `{}`             (trees_dock._redraw_edited_node)
  M11 «a node but no adapter» is not refused                    (cascade._node_position_override)

and §3 (a module-placed tree is not redrawn from its own tab):

  M12 «Redraw whole tree» on an embedded tree is not refused     (trees_dock._on_redraw_whole_tree)
  M13 «Redraw selected» on an embedded tree is not refused       (trees_dock._run_curated_redraw)
  M14 the refusal fires on a tree NOBODY embeds                  (trees_embedding.refuse_embedded_tree)
  M15 a cosmetic control (a comment must change nothing)

and §4 (Denis, 10.11 — the fourth door, the lying form and the cell holes):

  M16 a `mount` SUBTREE on an embedded tree is not refused       (trees_accept.redraw_subtree)
  M17 the node form claims «Redraw started» whatever the dock said (trees_accept.redraw_form)
  M18 «Redraw whole tree» returns True on a REFUSAL              (trees_dock._on_redraw_whole_tree)
  M19 «Redraw whole tree» returns False on SUCCESS               (trees_dock._on_redraw_whole_tree)
  M20 «Redraw selected» uses the ACTIVE tab, not the given tree   (trees_dock._run_curated_redraw)
  M21 `module_nodes` looks at the top level only                  (trees_embedding.module_nodes)
  M22 `embedding_marker` looks at the FIRST owner only            (trees_embedding.embedding_marker)
  M23 the refusal sentence drops the node's name                  (trees_embedding.refusal_line)
  M24 `reload_root` ALWAYS merges (the clean branch is dead)       (trees_instances_sync.reload_root)
  M25 `refuse` emits BEFORE it marks the op done                  (worker.LongOpController.refuse)
  M26 `_refuse_busy` releases AFTER the emit                      (worker.LongOpController._refuse_busy)
  M27 `_retire` does not mark the op done                         (worker.LongOpController._retire)

Safeguards, as in every harness of this package (rule 38): a template matching more than once is
REFUSED; a row that reddens nothing is a MISS, not a pass; the control must survive; every file is
restored in `finally` and the restoration is verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_node_redraw_from_live_parent_mutations_2026_11_09`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BASE = "gui/docks/trees_base_pose.py"
CASCADE = "gui/docks/cascade.py"
DOCK = "gui/docks/trees_dock.py"
EMBEDDING = "gui/docks/trees_embedding.py"
ACCEPT = "gui/docks/trees_accept.py"
SYNC = "gui/docks/trees_instances_sync.py"
WORKER = "gui/worker.py"

# §4 put the fields of this plan into two more suites: the instance-tab refresh (the
# clean-buffer branch of `reload_root`) and the controller's `running` property. All three ride
# in ONE guard string because a row may only name cells the runner actually executes.
GUARDS = ("tests/gui/docks/test_tree_node_redraw_from_live_parent.py "
          "tests/gui/docks/test_trees_instances_follow_template.py "
          "tests/gui/test_worker.py")

MUTATIONS = [
    ("M1 the base comes from the TREE, not the live parent", BASE,
     "            cfg, adapter, sheet_names, tree, parent_node, base_anchor,\n"
     "            snapshot=snapshot)\n",
     "            cfg, adapter, sheet_names, tree, None, base_anchor,\n"
     "            snapshot=snapshot)  # MUTATION: the parent is ignored\n",
     ["test_a_read_then_redraw_does_not_move_the_child",
      "test_b_the_tree_base_has_no_influence_on_a_child"]),

    ("M2 the base's rotation is ignored in the offset", BASE,
     "    dx_mm, dy_mm = local_offset_to_board_mm(node_offset_of(node), deg)\n",
     "    dx_mm, dy_mm = local_offset_to_board_mm(node_offset_of(node), 0.0)  # MUTATION\n",
     ["test_g_a_rotated_parent_is_composed_and_the_rotation_is_read"]),

    ("M3 the node's own rotation is not read", BASE,
     "    rotation_deg = local_rotation_to_board_deg(float(node.rotation or 0.0), deg)\n",
     "    rotation_deg = local_rotation_to_board_deg(0.0, deg)  # MUTATION\n",
     ["test_g_a_rotated_parent_is_composed_and_the_rotation_is_read"]),

    ("M4 an unreadable parent falls back to the tree layout", CASCADE,
     "    position, rotation_deg = redraw_target(\n"
     "        cfg, adapter, payload.get(\"sheet_names\") or {}, tree, node,\n"
     "        payload.get(\"parent_node\"), base_anchor=payload.get(\"base_anchor\"))\n",
     "    try:  # MUTATION: the old, wrong fallback\n"
     "        position, rotation_deg = redraw_target(\n"
     "            cfg, adapter, payload.get(\"sheet_names\") or {}, tree, node,\n"
     "            payload.get(\"parent_node\"), base_anchor=payload.get(\"base_anchor\"))\n"
     "    except ValidationError:\n"
     "        return None\n",
     ["test_d_a_parent_missing_from_the_board_is_refused"]),

    ("M5 a MIRRORED base is not refused", BASE,
     "    if mirrored:\n",
     "    if mirrored and False:\n",
     ["test_e_a_mirrored_parent_is_refused"]),

    # §4.2/§4.3 moved the node's own Redraw VERBATIM into `trees_accept.redraw_node` (the dock
    # only delegates), so the two rows below follow the code they measure.
    ("M6 the top of an embedded tree is not refused", ACCEPT,
     "        if refuse_embedded_tree(dock._trees, tree, node_ref=node.ref):\n"
     "            return False\n",
     "        if False:  # MUTATION\n"
     "            return False\n",
     ["test_zh_the_top_of_an_embedded_tree_is_refused_with_the_marker"]),

    ("M7 the override is not handed to the pipeline", CASCADE,
     "            position_overrides={ref: override} if override is not None else None\n",
     "            position_overrides=None  # MUTATION\n",
     ["test_a_read_then_redraw_does_not_move_the_child",
      "test_z_the_top_of_a_plain_tree_still_comes_from_its_anchor"]),

    ("M9 the `polar` branch of the offset is not read", BASE,
     "    if node.polar is not None:\n",
     "    if node.polar is not None and False:  # MUTATION\n",
     ["test_a_polar_node_is_placed_from_its_polar_offset"]),

    ("M10 the dock's `sheet_names` are replaced by `{}`", ACCEPT,
     "        \"sheet_names\": dict(dock._ctx.sheet_names if dock._ctx is not None else {}),\n",
     "        \"sheet_names\": {},  # MUTATION\n",
     ["test_the_dock_hands_the_worker_its_sheet_names"]),

    ("M11 «a node but no adapter» is not refused", CASCADE,
     "    if tree is None or cfg is None or adapter is None:\n",
     "    if tree is None or cfg is None:  # MUTATION: the base has no board to read\n",
     ["test_a_payload_naming_a_node_without_an_adapter_is_refused"]),

    ("M12 «Redraw whole tree» on an embedded tree is not refused", DOCK,
     "        if self._embedded_tree_refused(tree):\n"
     "            return False\n",
     "        if False:  # MUTATION\n"
     "            return False\n",
     ["test_a_whole_tree_redraw_of_an_embedded_tree_is_refused_from_both_doors"]),

    ("M13 «Redraw selected» on an embedded tree is not refused", DOCK,
     "        if target is not None and self._embedded_tree_refused(target):\n"
     "            return\n",
     "        if False:  # MUTATION\n"
     "            return\n",
     ["test_redraw_selected_on_an_embedded_tree_is_refused"]),

    ("M14 the refusal fires on a tree NOBODY embeds", EMBEDDING,
     "    marker = embedding_marker(trees, tree)\n"
     "    if marker is None:\n"
     "        return False\n",
     "    marker = embedding_marker(trees, tree)\n"
     "    if marker is None:\n"
     "        marker = (\"?\", \"?\")  # MUTATION\n",
     ["test_a_whole_tree_redraw_of_a_plain_tree_still_starts"]),

    ("M15 cosmetic control (a comment must change nothing)", CASCADE,
     "    node = payload.get(\"node\")\n",
     "    node = payload.get(\"node\")  # MUTATION: cosmetic\n",
     "survive"),

    # ── §4.1: the FOURTH door — a subtree inside an embedded tree ──────────
    # Replacing a decision with a FALSE guard (never deleting a line) keeps every file's line
    # count intact: a deletion trips the two-way ratchet in
    # tests/repo/test_file_size_ceilings.py and the row would then be measured against a build
    # that no longer matches its own ceiling.
    ("M16 a `mount` SUBTREE on an embedded tree is not refused", ACCEPT,
     "    if refuse_embedded_tree(dock._trees, tree, node_ref=node.ref):\n"
     "        return False                     # handled here: refused, nothing started\n",
     "    if False:                            # MUTATION\n"
     "        return False\n",
     ["test_a_mount_subtree_on_an_embedded_tree_is_refused"]),

    # ── §4.2: the forms must tell the truth about what the dock did ────────
    ("M17 the node form claims «Redraw started» whatever the dock said", ACCEPT,
     "    if dock._redraw_edited_node(node):\n",
     "    if True:  # MUTATION: the old lie\n",
     ["test_the_node_form_says_refused_and_started"]),

    ("M18 «Redraw whole tree» returns True on a REFUSAL", DOCK,
     "        if self._embedded_tree_refused(tree):\n"
     "            return False\n",
     "        if self._embedded_tree_refused(tree):\n"
     "            return True  # MUTATION\n",
     ["test_the_anchor_form_says_refused_and_started"]),

    ("M19 «Redraw whole tree» returns False on SUCCESS", DOCK,
     "        self._run_forest_redraw_with_refs(set(collect_tree_refs(tree)), trigger)\n"
     "        return True\n",
     "        self._run_forest_redraw_with_refs(set(collect_tree_refs(tree)), trigger)\n"
     "        return False  # MUTATION\n",
     ["test_the_anchor_form_says_refused_and_started"]),

    # ── §4.3: the cell holes Denis's mutations walked through ──────────────
    ("M20 «Redraw selected» uses the ACTIVE tab, not the given tree", DOCK,
     "        target = tree if tree is not None else self._current_tree()\n",
     "        target = self._current_tree()  # MUTATION\n",
     ["test_redraw_selected_uses_the_tree_it_is_given_not_the_active_tab"]),

    ("M21 `module_nodes` looks at the top level only", EMBEDDING,
     "        stack.extend(node.children or [])\n",
     "        pass  # MUTATION: no descent\n",
     ["test_a_module_nested_under_another_node_still_places_its_tree"]),

    ("M22 `embedding_marker` looks at the FIRST owner only", EMBEDDING,
     "    for owner in trees:\n",
     "    for owner in list(trees)[:1]:  # MUTATION\n",
     ["test_the_owner_tree_need_not_be_first_in_the_list"]),

    ("M23 the refusal sentence drops the node's name", EMBEDDING,
     "    subject = (_(\"node {node!r}\").format(node=node_ref) if node_ref is not None\n",
     "    subject = (_(\"node {node!r}\").format(node=node_ref) if False  # MUTATION\n",
     ["test_zh_the_top_of_an_embedded_tree_is_refused_with_the_marker"]),

    ("M24 `reload_root` ALWAYS merges (the clean branch is dead)", SYNC,
     "    if not dock._dirty and not merge_always:\n",
     "    if False:  # MUTATION: always merge\n",
     ["test_reload_trees_on_a_clean_buffer_takes_an_external_edit"]),

    # ── §4.3: `running` — the answer the docks ask ────────────────────────
    ("M25 `refuse` emits BEFORE it marks the op done", WORKER,
     "        self._done = True\n"
     "        self.failed.emit(message)\n",
     "        self.failed.emit(message)\n"
     "        self._done = True  # MUTATION\n",
     ["test_refuse_is_already_not_running_inside_on_error"]),

    ("M26 `_refuse_busy` releases AFTER the emit", WORKER,
     "        self._release()\n"
     "        self.failed.emit(_(\"the board is busy — try again in a moment\"))\n",
     "        self.failed.emit(_(\"the board is busy — try again in a moment\"))\n"
     "        self._release()  # MUTATION\n",
     ["test_a_refused_retry_is_not_running_inside_on_error"]),

    ("M27 `_retire` does not mark the op done", WORKER,
     "        self._done = True\n"
     "        try:\n"
     "            self.thread_stopped.emit()\n",
     "        self._done = False  # MUTATION\n"
     "        try:\n"
     "            self.thread_stopped.emit()\n",
     ["test_an_abandoned_start_is_not_running"]),
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
