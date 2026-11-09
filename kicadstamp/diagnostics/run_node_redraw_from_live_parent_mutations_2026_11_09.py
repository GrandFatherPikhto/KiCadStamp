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

GUARDS = "tests/gui/docks/test_tree_node_redraw_from_live_parent.py"

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

    ("M6 the top of an embedded tree is not refused", DOCK,
     "            if self._embedded_tree_refused(tree, node_ref=node.ref):\n"
     "                return\n",
     "            if False:  # MUTATION\n"
     "                return\n",
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

    ("M10 the dock's `sheet_names` are replaced by `{}`", DOCK,
     "            \"sheet_names\": dict(self._ctx.sheet_names if self._ctx is not None else {}),\n",
     "            \"sheet_names\": {},  # MUTATION\n",
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
