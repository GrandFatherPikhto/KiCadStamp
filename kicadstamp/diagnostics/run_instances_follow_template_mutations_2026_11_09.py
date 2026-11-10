# kicadstamp/diagnostics/run_instances_follow_template_mutations_2026_11_09.py
"""Mutation rows for «a template's generated tabs follow it»
(plan_2026_11_09_tree_instances_follow_template).

The defect (live, 09.11): `switch_b`'s tab kept a node deleted from `switch_a` (and the Entity
copy in `cfg.entities`) until the whole trees section was reloaded — copies are generated at
LOAD and only the docks' `save` signals re-read them. The fix is a debounced refresh of the
GENERATED tabs. Each row removes exactly ONE of its decisions and must turn a NAMED cell red:

  M1  `_mark_dirty` no longer arms the refresh                 (trees_dock._mark_dirty)
  M2  the Entity dock's `saved` is not wired                    (wire_instance_refresh)
  M3  a new edit does not RESTART the quiet period              (note_change)
  M4  the refresh marks the buffer dirty / stages it             (_flush)
  M5  every refresh rebuilds ALL tabs                           (_flush)
  M6  the rebuilt page's `_node_items` rows are not dropped     (rebuild_clone_pages)
  M7  a refused expansion is swallowed silently                 (_flush)
  M8  the same refusal is said again in a row                   (_report_refusal)
  M9  an edit arriving during a long operation is DROPPED       (_flush)
  M10 the «no declarations» early exit is gone                  (note_change)

and the four defects of Claude's §2 (second acceptance):

  M12 the refresh does not ask for the always-merge              (§2.1 — the lost edit)
  M13 the refresh asks `_active_op is not None` again            (§2.2 — clones stop following)
  M14 `reload_root` speaks on the refresh path again             (§2.3 — two lines per refusal)
  M15 `_last_refusal` is not reset after a good expansion        (§2.4а)
  M16 `net_trace_dock.saved` is not wired                        (§2.4б)
  M17 a cosmetic control (a comment must change nothing)

and §3 (10.11 — the clone still lagged behind the template):

  M18 `refresh_ref_candidates` no longer arms the refresh         (trees_dock.refresh_ref_candidates)

and §3.2 (10.11 — the windows-latest run):

  M19 `confirm_sync_dialog` loses its own size key                (fieldstool_confirm_dialogs)

Safeguards, as in every harness of this package (rule 38): a template matching more than once is
REFUSED; a row that reddens nothing is a MISS, not a pass; the control must survive; every file
is restored in `finally` and the restoration is verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_instances_follow_template_mutations_2026_11_09`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REFRESH = "gui/docks/trees_instances_refresh.py"
DOCK = "gui/docks/trees_dock.py"
CONFIRM = "gui/fieldstool_confirm_dialogs.py"

# §3.2 rode in on the same commit, and its cell lives in the instances DIALOG suite — a row may
# only name cells the runner actually executes, so both files ride here.
GUARDS = ("tests/gui/docks/test_trees_instances_follow_template.py "
          "tests/gui/docks/test_instances_dialog.py")

MUTATIONS = [
    ("M1 _mark_dirty no longer arms the refresh", DOCK,
     "        self._dirty = True\n"
     "        self.note_instances_changed()\n",
     "        self._dirty = True  # MUTATION: the clones are never told\n",
     ["test_five_edits_are_one_config_read"]),

    ("M2 the Entity dock's saved is not wired", REFRESH,
     "    for dock in (hub.entity_dock, hub.cells_dock, hub.net_trace_dock):\n",
     "    for dock in (hub.cells_dock, hub.net_trace_dock):  # MUTATION\n",
     ["test_every_section_dock_reaches_the_refresh"]),

    ("M3 a new edit does not restart the quiet period", REFRESH,
     "        self._timer.start()          # a fresh event RESTARTS the single shot\n",
     "        if not self._timer.isActive():  # MUTATION: the first deadline wins\n"
     "            self._timer.start()\n",
     ["test_every_edit_restarts_the_single_shot"]),

    ("M4 the refresh marks the buffer dirty", REFRESH,
     "        dock._update_status_row()\n        return True\n",
     "        dock._mark_dirty()  # MUTATION: a refresh is not a user edit\n"
     "        dock._update_status_row()\n        return True\n",
     ["test_the_refresh_is_neither_a_dirty_edit_nor_a_stage"]),

    ("M5 every refresh rebuilds ALL tabs", REFRESH,
     "        elif not rebuild_clone_pages(dock, generated):\n"
     "            dock._rebuild_tabs()\n",
     "        else:  # MUTATION: one page-mate costs everybody their form\n"
     "            dock._rebuild_tabs()\n",
     ["test_a_template_page_and_its_open_form_survive_a_refresh"]),

    ("M6 the rebuilt page's rows are not dropped from _node_items", REFRESH,
     "        for ref, item in list(dock._node_items.items()):\n"
     "            if item.treeWidget() is tree_widget:\n"
     "                dock._node_items.pop(ref, None)\n",
     "",
     ["test_a_deleted_template_node_leaves_the_clone_at_once"]),

    ("M7 a refused expansion is swallowed silently", REFRESH,
     "        if not reloaded.ok:\n"
     "            self._report_refusal(reloaded.error)\n"
     "            return False                     # the clones keep their previous state\n",
     "        if not reloaded.ok:\n"
     "            return False                     # MUTATION: nothing said\n",
     ["test_a_refused_expansion_keeps_the_clones_and_says_it_once"]),

    ("M8 the same refusal is said again in a row", REFRESH,
     "        if text == self._last_refusal:\n            return\n",
     "",
     ["test_a_refused_expansion_keeps_the_clones_and_says_it_once"]),

    ("M9 an edit arriving during a long operation is dropped", REFRESH,
     "            self._timer.start()\n"
     "            return False\n",
     "            return False                     # MUTATION: the edit is lost\n",
     ["test_a_finished_operation_no_longer_blocks_the_refresh"]),

    ("M10 the «no declarations» early exit is gone", REFRESH,
     "        if dock._root_path is None or not declared_instance_names(dock._cfg):\n"
     "            return\n",
     "",
     ["test_without_declarations_nothing_is_read_and_no_timer_runs"]),

    # ── Claude's §2 (the second acceptance): the defects the first commit left ──
    ("M12 (§2.1) the refresh does not ask for the always-merge", REFRESH,
     "        reloaded = reload_root(dock, merge_always=True, quiet=True)\n",
     "        reloaded = reload_root(dock)  # MUTATION: a clean buffer is replaced\n",
     ["test_a_clean_buffer_refresh_keeps_the_templates_own_nodes"]),

    ("M13 (§2.2) the refresh asks `_active_op is not None` again", REFRESH,
     "        if long_op_running(getattr(dock, \"_active_op\", None)):\n",
     "        if getattr(dock, \"_active_op\", None) is not None:  # MUTATION\n",
     ["test_a_finished_operation_no_longer_blocks_the_refresh"]),

    ("M14 (§2.3) `reload_root` speaks on the refresh path again", REFRESH,
     "        reloaded = reload_root(dock, merge_always=True, quiet=True)\n",
     "        reloaded = reload_root(dock, merge_always=True)  # MUTATION\n",
     ["test_a_repeated_refusal_is_one_log_line"]),

    ("M15 (§2.4а) `_last_refusal` is not reset after a good expansion", REFRESH,
     "        self._last_refusal = None\n",
     "",
     ["test_the_same_refusal_after_a_good_expansion_is_said_again"]),

    ("M16 (§2.4б) `net_trace_dock.saved` is not wired", REFRESH,
     "    for dock in (hub.entity_dock, hub.cells_dock, hub.net_trace_dock):\n",
     "    for dock in (hub.entity_dock, hub.cells_dock):  # MUTATION\n",
     ["test_every_section_dock_reaches_the_refresh"]),

    ("M17 cosmetic control (a comment must change nothing)", REFRESH,
     "        self._timer = QTimer(dock)\n",
     "        self._timer = QTimer(dock)  # MUTATION: cosmetic\n",
     "survive"),

    # §3: every OTHER door lands in `WORKING_SET.stage_write` → the hub's debounced
    # `refresh_from_working_set` → `refresh_ref_candidates`, which never nudged the clone
    # refresh. Replaced, not deleted: the file's line count must stay what the ceiling says.
    ("M18 `refresh_ref_candidates` no longer arms the refresh", DOCK,
     "        # Every staging door in the app lands HERE (§3): the clone tabs read the same graph.\n"
     "        self.note_instances_changed()\n",
     "        # Every staging door in the app lands HERE (§3): the clone tabs read the same graph.\n"
     "        pass  # MUTATION: the clone tabs are never told\n",
     ["test_a_working_set_write_alone_reaches_the_clone",
      "test_a_config_tree_door_reaches_the_clone_refresh"]),

    # §3.2 (windows-latest): the cell no longer pins a literal 700×500, it pins that each key
    # carries ITS OWN window's size. With the key gone both windows write `dialog_size:QDialog`
    # and the cell's last assertion is what fails.
    ("M19 `confirm_sync_dialog` loses its own size key", CONFIRM,
     "    remember_dialog_size(dialog, 640, 440, key=\"FieldstoolConfirmSync\")\n",
     "    remember_dialog_size(dialog, 640, 440, key=None)  # MUTATION\n",
     ["test_the_two_anonymous_windows_use_their_own_size_keys"]),
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
