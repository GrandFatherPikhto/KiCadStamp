# kicadstamp/diagnostics/run_checks_tab_mutations_2026_10_22.py
"""Mutation rows for the Checks tab (plan_2026_10_22_checks_tab, Ч1).

The tab is a launcher, and a launcher's defects are quiet: a report that looks fine while a
warning line was dropped, a green status over a refusal, a "crashed" label on a run the user
stopped, or a check started against no project at all. Each row below removes exactly ONE of
those decisions and must turn a named cell red:

  M1  the status colour stops following the exit code   (gui/docks/checks.py `_on_finished`)
  M2  stderr is no longer merged into the pane          (gui/docks/checks.py `_launch`)
  M3  a run the user STOPPED is reported as a crash     (gui/docks/checks.py `_on_finished`)
  M4  a check may be built with no project at all       (gui/checks_registry.py `command_for`)
  M5  every pipe chunk is decoded on its own (Д-1)     (gui/docks/checks.py `_on_output`)
  M6  the tail of a run that ended mid-character is
      never flushed (Д-3)                               (gui/docks/checks.py `_on_finished`)
  M7  the flush is not final — `decode(b"", False)`     (gui/docks/checks.py `_flush_decoder`)
  M8  every check's output goes to the FIRST one        (gui/docks/checks.py `_append_output`)
  M9  Copy ignores the selected row                     (gui/docks/checks.py `copy`)
  M10 a finished run does not touch its row             (gui/docks/checks.py `_update_rows`)
  M11 "Run" runs the FIRST check, not the selected      (gui/docks/checks.py `_on_run_clicked`)
  M12 the page is not in the left tab row (23.10)       (gui/dock_hub.py `__init__`)
  M13 `--write` reaches the duplicate-mounts command    (gui/checks_registry.py `command_for`)
  M14 "Run all" launches every check at once            (gui/docks/checks.py `run_all`)
  M15 Stop keeps the queue                              (gui/docks/checks.py `stop`)
  M16 a menu item runs the FIRST check                  (gui/checks_menu.py `build_checks_menu`)
  M17 a busy socket leaves the queue alone              (gui/docks/checks.py `_warn_busy`)
  M18 a missing script leaves the queue alone           (gui/docks/checks.py `_launch`)
  M19 a failed start leaves the queue alone             (gui/docks/checks.py `_on_error`)
  M21 the page is announced as a bottom dock again       (gui/dock_hub.py `__init__`, Ч1б)
  M22 "Run all" CLEARS copper before it runs             (gui/docks/checks.py `run_all`, Ч1б)
  M23 the menu's Clear is on with nothing found          (gui/checks_menu.py, Ч1б)
  M25 the page's "is anything running?" ignores the panel (gui/docks/checks.py `is_running`)
  M26 the panel ignores a running CHECK                  (gui/docks/dedupe.py `_update_buttons`)
  M28 every group node shows the FIRST group's texts     (gui/docks/checks.py `_rebuild_dedupe_groups`)
  M29 "Clear this group" clears EVERYTHING               (gui/docks/dedupe.py `_clear_one_worker`)
  M30 "Clear this group" ignores a busy page             (gui/docks/dedupe.py `clear_selected_group`)
  M31 "Highlight on board" marks only the first member    (gui/docks/dedupe.py `_marker_points`)
  M32 a GROUP node shows the ROW's QView                  (gui/docks/checks.py `_on_current_changed`)
  M33 "Highlight" keeps the previous group's marks        (gui/docks/dedupe.py `_highlight_worker`)
  M34 "Find" keeps the marks of the list it replaced      (gui/docks/dedupe.py `_scan_worker`)
  M35 "Clear this group" keeps the marks                  (gui/docks/dedupe.py `_clear_one_worker`)
  M36 cosmetic control (a comment must change nothing)

M6 and M7 arrived with acceptance Д-3 (23.10.2026); M8–M16 with the page move (Ч1 of
plan_2026_10_23_checks_tab_top_level, 23.10); M21–M23 with Ч1б of the same plan (the
copper-duplicates panel moving into the page); M25/M26 with Д-1 of the Ч1б acceptance (one
"something runs" answer, asked in BOTH directions); M28–M31 with Ч2а (the duplicate GROUPS as
tree nodes, with "Clear this group" and "Highlight on board"); M32–M35 with Д-1 and Д-2 of the
Ч2а acceptance (the nodes moved into the PAGE's tree, and every list change clears the
`dedupe` overlay namespace). M28 was re-aimed at `_rebuild_dedupe_groups` by Д-1 — the panel's
own `_fill_tree` it used to name no longer exists. The control row has been renumbered six
times and is always LAST: M6 -> M8 (Д-3) -> M17 (page move) -> M20 (Д-1/Д-2) ->
M24 (Ч1б) -> M27 (Д-1 Ч1б) -> M32 (Ч2а) -> M36 (Д-1/Д-2 Ч2а).

Safeguards, the same as every harness in this package: a template that matches more than once
is refused (a silent miss is indistinguishable from a healthy guard — rule 38); a row that
reddens NOTHING is a MISS; the control row must survive; every file is restored in `finally`
and the restoration is verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_checks_tab_mutations_2026_10_22`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHECKS = "gui/docks/checks.py"
REGISTRY = "gui/checks_registry.py"

GUARDS = ("tests/gui/docks/test_checks_dock.py tests/gui/test_checks_registry.py "
          "tests/gui/docks/test_dedupe_dock.py")

MUTATIONS = [
    ("M1 every exit code looks clean", CHECKS,
     "                exit_level(code))\n",
     "                exit_level(0))  # MUTATION: every code looks clean\n",
     ["test_the_pane_is_the_scripts_own_bytes_and_exit_1_is_yellow",
      "test_exit_zero_is_green_and_exit_two_is_red"]),

    ("M2 stderr is not merged into the pane", CHECKS,
     "        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)\n",
     "        process.setProcessChannelMode("
     "QProcess.ProcessChannelMode.SeparateChannels)  # MUTATION\n",
     ["test_the_pane_is_the_scripts_own_bytes_and_exit_1_is_yellow",
      "test_copy_hands_over_the_selected_scripts_bytes",
      "test_a_dirty_working_set_warns_in_the_pane_but_not_in_the_copied_text"]),

    ("M3 a stopped run is reported as a crash", CHECKS,
     "        if stopped:\n",
     "        if False:  # MUTATION: a stopped run is reported as a crash\n",
     ["test_stop_kills_the_running_check_and_says_so"]),

    ("M4 a check may be built with no project", REGISTRY,
     "    if profile is None:\n        return None, "
     '_("Open a project first — the checks run against the profile\'s config file.")\n',
     "    if False:  # MUTATION: a run against nothing is allowed\n        return None, "
     '_("Open a project first — the checks run against the profile\'s config file.")\n',
     ["test_no_project_is_refused_before_anything_is_built"]),

    # Д-1 (acceptance 22.10.2026): a chunk that ends mid-character must still decode whole.
    ("M5 every pipe chunk is decoded on its own", CHECKS,
     "        chunk = self._decoder.decode(bytes(process.readAllStandardOutput()))\n",
     '        chunk = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")'
     "  # MUTATION\n",
     ["test_a_character_split_across_two_pipe_reads_survives"]),

    # Д-3 (acceptance 23.10.2026): a run that ENDED mid-character keeps its tail in the decoder;
    # `final=True` is the only thing that turns those bytes into the honest U+FFFD.
    ("M6 the decoder tail is never flushed", CHECKS,
     "        self._flush_decoder()\n        self._current = None\n",
     "        self._current = None  # MUTATION: the tail goes down with the run\n",
     ["test_a_character_cut_off_by_the_run_end_is_shown_as_a_replacement_char"]),

    ("M7 the flush is not final (decode(b\"\", False))", CHECKS,
     '            self._append_output(decoder.decode(b"", True))\n',
     '            self._append_output(decoder.decode(b"", False))  # MUTATION\n',
     ["test_a_character_cut_off_by_the_run_end_is_shown_as_a_replacement_char"]),

    # Ч1 of plan_2026_10_23_checks_tab_top_level: the page's own promises — one output per check,
    # Copy of the SELECTED one, a row that follows the last run, "Run" of the SELECTED check, the
    # page in the left tab row, a dry duplicate-mounts run, "Run all" as a QUEUE and a Stop that
    # drops it, and a menu item that runs its OWN check.
    ("M8 every check's output goes to the first one", CHECKS,
     "        state = self._states[self._current]\n"
     "        state.output += chunk\n",
     "        state = next(iter(self._states.values()))  # MUTATION: one shared report\n"
     "        state.output += chunk\n",
     ["test_each_check_keeps_its_own_output_and_copy_follows_the_selection"]),

    # Ч1б added a DEDUPE_ROW branch to `copy`, so the template now spells the whole body —
    # the mutation must still replace ONLY the "which row" decision.
    ("M9 Copy ignores the selected row", CHECKS,
     "        script = self.selected_script()\n        if script is None:\n"
     "            return\n        if script == DEDUPE_ROW:\n"
     "            self.dedupe.copy()\n            return\n"
     "        QApplication.clipboard().setText(self._states[script].output)\n",
     "        script = next((s for s, st in self._states.items() if st.output), None)\n"
     "        if script is None:  # MUTATION: copies the first check that ran\n"
     "            return\n        if script == DEDUPE_ROW:\n"
     "            self.dedupe.copy()\n            return\n"
     "        QApplication.clipboard().setText(self._states[script].output)\n",
     ["test_each_check_keeps_its_own_output_and_copy_follows_the_selection"]),

    ("M10 a finished run does not touch its row", CHECKS,
     "        for check in CHECKS:\n            state = self._states[check.script]\n",
     "        for check in ():  # MUTATION: the tree stops following the runs\n"
     "            state = self._states[check.script]\n",
     ["test_the_row_glyph_follows_the_exit_code"]),

    ("M11 \"Run\" runs the FIRST check", CHECKS,
     "        script = self.selected_script()\n        if script is not None:\n",
     "        script = CHECKS[0].script  # MUTATION: whatever is selected\n"
     "        if script is not None:\n",
     ["test_each_check_keeps_its_own_output_and_copy_follows_the_selection"]),

    ("M12 the page is not in the left tab row", "gui/dock_hub.py",
     '        self.left_tabs.addTab(self.checks_page, _("Checks"))\n',
     "        pass  # MUTATION: the Checks page never reaches the tab row\n",
     ["test_the_page_is_the_fourth_left_tab_and_not_a_bottom_dock"]),

    ("M13 --write reaches the duplicate-mounts command", REGISTRY,
     "    return [python or sys.executable, \"-u\", str(script), str(profile)], \"\"\n",
     "    extra = [\"--write\"] if \"merge_duplicate_mounts\" in check.script else []\n"
     "    return [python or sys.executable, \"-u\", str(script), str(profile), *extra], \"\"  "
     "# MUTATION\n",
     ["test_the_merge_check_is_a_dry_run_and_its_code_colours_the_row"]),

    ("M14 \"Run all\" launches every check at once", CHECKS,
     "        self._queue = [check.script for check in CHECKS] + [DEDUPE_ROW]\n"
     "        self._start_next()\n",
     "        for check in CHECKS:  # MUTATION: a fan-out instead of a queue\n"
     "            self._launch(check)\n",
     ["test_run_all_runs_the_checks_one_after_another"]),

    ("M15 Stop keeps the queue", CHECKS,
     "        cancelled = len(self._queue)\n        self._queue.clear()\n",
     "        cancelled = len(self._queue)  # MUTATION: the rest starts anyway\n",
     ["test_stop_mid_queue_stops_the_current_and_drops_the_rest"]),

    ("M16 a menu item runs the FIRST check", "gui/checks_menu.py",
     "            lambda _checked=False, script=check.script: page.run_from_menu(script))\n",
     "            lambda _checked=False, script=CHECKS[0].script: page.run_from_menu(script))  "
     "# MUTATION\n",
     ["test_the_checks_menu_sits_between_tools_and_view_and_runs_its_own_check"]),

    # Д-1 of the Ч1 acceptance (23.10): a refused start ends the WHOLE "Run all". Each row kills
    # the queue on ONE path only, so a path left undone names itself in the failure.
    ("M17 a busy socket leaves the queue alone", CHECKS,
     "        text = self._end_queue(_(\"KiCad is busy — try again in a moment.\"))\n",
     "        text = _(\"KiCad is busy — try again in a moment.\")  "
     "# MUTATION: the queue survives\n",
     ["test_a_busy_socket_twice_cancels_the_rest_of_the_run"]),

    ("M18 a missing script leaves the queue alone", CHECKS,
     "            reason = self._end_queue(reason)\n",
     "            reason = reason  # MUTATION: the queue survives\n",
     ["test_a_missing_script_cancels_the_rest_of_the_run"]),

    ("M19 a failed start leaves the queue alone", CHECKS,
     "        text = self._end_queue(_(\"{name}: could not start — {reason}\").format(\n"
     "            name=self.check_for(script).title, reason=reason))\n",
     "        text = _(\"{name}: could not start — {reason}\").format(  "
     "# MUTATION: the queue survives\n"
     "            name=self.check_for(script).title, reason=reason)\n",
     ["test_a_start_that_fails_cancels_the_rest_of_the_run"]),

    # Ч1б (23.10): the copper-duplicates panel is a ROW of this page now, its Clear is a WRITE
    # and "Run all" must never be able to reach it. One row per promise.
    ("M21 the page is announced as a bottom dock again", "gui/dock_hub.py",
     "        self.docks = [self.log_dock]\n",
     "        self.docks = [self.log_dock, self.log_dock]  "
     "# MUTATION: a second bottom panel is back\n",
     ["test_the_page_is_the_fourth_left_tab_and_not_a_bottom_dock",
      "test_the_panel_is_the_fifth_row_of_the_checks_page_and_not_a_bottom_dock"]),

    ("M22 \"Run all\" CLEARS copper before it runs", CHECKS,
     "        self._queue = [check.script for check in CHECKS] + [DEDUPE_ROW]\n",
     "        self.dedupe.clear()  # MUTATION: the run rewrites the board\n"
     "        self._queue = [check.script for check in CHECKS] + [DEDUPE_ROW]\n",
     ["test_run_all_searches_for_duplicates_and_never_clears"]),

    # Д-1 made the menu reuse the PANEL's own rule (`can_clear` already asks the page), so the
    # template is that ONE call — a second, hand-written condition here was the drift.
    ("M23 the menu's Clear is on with nothing found", "gui/checks_menu.py",
     "        clear_dedupe.setEnabled(page.dedupe.can_clear())\n",
     "        clear_dedupe.setEnabled(True)  # MUTATION: offered with nothing to clear\n",
     ["test_the_menu_follows_the_page_and_the_clear_follows_the_panel"]),

    # Д-1 of the Ч1б acceptance (25.10): ONE "something runs on the page", asked both ways.
    # Each row silences ONE half, so the half left undone names itself in the failure.
    ("M25 the page's \"is anything running?\" ignores the panel", CHECKS,
     "        return self._process is not None or self.dedupe.is_running()\n",
     "        return self._process is not None  # MUTATION: the panel's work is invisible\n",
     ["test_a_running_panel_turns_the_check_doors_off"]),

    ("M26 the panel ignores a running CHECK", "gui/docks/dedupe.py",
     "        interactive = not self.is_blocked()\n",
     "        interactive = not self._running  # MUTATION: a check run is invisible here\n",
     ["test_a_running_check_turns_the_panels_doors_off"]),

    # Ч2а (25.10): the groups are tree nodes; the group's own two doors act on ONE group.
    # Д-1 of the Ч2а acceptance moved the nodes into the PAGE's tree, so M28 now removes the
    # decision in `_rebuild_dedupe_groups` (the panel's `_fill_tree` it used to name is gone).
    ("M28 every group node shows the FIRST group's texts", CHECKS,
     "        for index, fields in enumerate(self.dedupe.groups()):\n",
     "        for index, fields in enumerate(  # MUTATION: every node is the first group\n"
     "                [self.dedupe.groups()[0]] if self.dedupe.groups() else []):\n",
     ["test_find_fills_the_group_nodes_with_the_core_groups_and_report"]),

    ("M29 \"Clear this group\" clears EVERYTHING", "gui/docks/dedupe.py",
     "        removed = apply_dedupe(adapter,\n"
     "                               [group] if kind == \"via\" else [],\n"
     "                               [group] if kind == \"track\" else [])\n",
     "        removed = apply_dedupe(adapter, via_groups, track_groups)  "
     "# MUTATION: the whole board\n",
     ["test_clear_this_group_removes_only_that_groups_extras"]),

    ("M30 \"Clear this group\" ignores a busy page", "gui/docks/dedupe.py",
     "        fields = self.selected_group()\n"
     "        if fields is None or not self.can_clear():\n",
     "        fields = self.selected_group()\n"
     "        if fields is None:  # MUTATION: a check run does not stop a removal\n",
     ["test_clear_this_group_does_nothing_while_a_check_runs"]),

    ("M31 \"Highlight on board\" marks only the first member", "gui/docks/dedupe.py",
     "    for member in group:\n",
     "    for member in group[:1]:  # MUTATION: the rest of the group stays unmarked\n",
     ["test_highlight_marks_the_groups_members_and_selects_nothing"]),

    # Д-1 of the Ч2а acceptance: three kinds of selection in ONE tree, and the pane follows the
    # KIND. This row makes a group node fall through to the check pane.
    ("M32 a GROUP node shows the ROW's (check) QView", CHECKS,
     "        if isinstance(role, tuple) and role[0] == DEDUPE_GROUP_ROLE:\n"
     "            self._show_dedupe_group(role[1])\n"
     "            return\n",
     "        if False:  # MUTATION: a group is shown as a check's bytes\n"
     "            self._show_dedupe_group(role[1])\n"
     "            return\n",
     ["test_the_row_shows_the_page_doors_and_a_group_shows_its_fields"]),

    # Д-2 of the Ч2а acceptance: the `dedupe` namespace is dropped by EVERY list change. Each
    # row silences ONE door, so the door left undone names itself in the failure.
    ("M33 \"Highlight\" keeps the previous group's marks", "gui/docks/dedupe.py",
     "        # Highlight B AFTER A: A's circles go first (Д-2). \"Replace, never stack\"\n"
     "        # has to hold across GROUPS, not only across presses of the same group.\n"
     "        DedupePage._forget_marks(adapter)\n",
     "        # MUTATION: B is drawn on top of A's circles\n",
     ["test_highlight_replaces_the_previous_groups_marks_before_drawing"]),

    ("M34 \"Find\" keeps the marks of the list it replaced", "gui/docks/dedupe.py",
     "        # The list the old marks described is being replaced — including the case\n"
     "        # where the new list is EMPTY: a clean board has nothing left to mark.\n"
     "        DedupePage._forget_marks(adapter)\n",
     "        # MUTATION: the old marks describe copper that may be gone\n",
     ["test_find_takes_the_previous_highlight_off_and_spares_other_spaces"]),

    ("M35 \"Clear this group\" keeps the marks", "gui/docks/dedupe.py",
     "        DedupePage._forget_marks(adapter)\n"
     "        via_groups, track_groups, _short = scan_copper_duplicates(adapter)\n",
     "        via_groups, track_groups, _short = scan_copper_duplicates(adapter)"
     "  # MUTATION: the marks outlive the copper\n",
     ["test_both_clears_take_the_marks_off"]),

    ("M36 cosmetic control (a comment must change nothing)", CHECKS,
     "        runnable = self.can_run()\n",
     "        runnable = self.can_run()  # MUTATION: cosmetic\n",
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
