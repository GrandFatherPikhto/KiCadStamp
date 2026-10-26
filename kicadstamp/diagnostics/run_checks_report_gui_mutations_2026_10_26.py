# kicadstamp/diagnostics/run_checks_report_gui_mutations_2026_10_26.py
"""Mutation rows for Ч2б-2 — the GUI's THREE levels of a check report (sections, problems, and
the two doors of a problem's QView).

Guards: `tests/gui/docks/test_checks_dock.py` (T1–T10, T13) and
`tests/gui/docks/test_trees_dock_select_node.py` (T11, T11b, T12). Each row removes ONE promise
and must turn a NAMED cell red:

  M53 the section nodes come from the printed TEXT, not from the JSON   (gui/docks/checks_report.py)
  M54 "Highlight on board" also SELECTS the parts on the board         (gui/docks/checks_report.py)
  M55 the node door ignores `to_node.node` and opens the tree root     (gui/docks/checks_report.py)
  M60 the previous report file is NOT removed before a run             (gui/docks/checks_report.py)
  M61 an empty section gets a node with (0)                            (gui/docks/checks_report.py)
  M62 the selection is restored by POSITION, not by the problem's key   (gui/docks/checks_report.py)
  M63 the previous marks are not dropped before drawing                (gui/docks/checks_report.py)
  M65 the walk does not expand the parents of the row it lands on      (gui/docks/trees_selection.py)
  M66 the door does not bring the Trees page to the front              (gui/docks/checks_report.py)
  M67 cosmetic control (a comment must change nothing)

Д-1 of the Ч2б-2 acceptance moved both bodies OUT of the giants: the walk is
`trees_selection.select_tree_node(dock, tree, node)` and the page switch is the hub's own
`show_left_page`, composed by `checks_report.jump_to_node`. Rows M65/M66 followed the CODE,
not the old file names — a row aimed at a line that no longer exists reports a MISS and
teaches nothing.

Why a second harness and not rows in `run_check_json_mutations_2026_10_26.py`: these rows are
guarded by GUI cells under a Qt event loop, and their source is a different file (the reader and
the QViews, not the scripts' report writer). One harness per step keeps each run short enough to
be run on every change, and its docstring names exactly which promises are pinned where.

Safeguards as everywhere in this package: a template that matches more than once is refused
(rule 38), a row that reddens NOTHING is a MISS, the control must survive, and every file is
restored in `finally` with the restore verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_checks_report_gui_mutations_2026_10_26`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHECKS_PAGE = "gui/docks/checks.py"
REPORT_SIDE = "gui/docks/checks_report.py"
TREES_SELECTION = "gui/docks/trees_selection.py"

GUARDS = ("tests/gui/docks/test_checks_dock.py "
          "tests/gui/docks/test_trees_dock_select_node.py")

CELL_SECTIONS = "test_the_tree_shows_a_row_per_section_and_per_problem"
CELL_EMPTY = "test_an_empty_section_gets_no_row"
CELL_KEEP_KEY = "test_a_rerun_keeps_the_selected_problem_by_its_key"
CELL_STALE_FILE = "test_the_previous_reports_rows_are_gone_before_the_next_run"
CELL_HIGHLIGHT = "test_highlight_marks_the_refs_and_selects_nothing"
CELL_ASKS_WALK = "test_the_door_hands_the_node_to_the_tree_selection"
CELL_SAYS_MISS = "test_a_node_that_is_not_in_the_tree_is_said_not_guessed"
CELL_MARKS_FIRST = "test_the_previous_marks_are_dropped_before_drawing"
CELL_SELECT_NODE = "test_select_tree_node_selects_the_row_and_expands_its_parents"
CELL_DOOR_PAGE = "test_the_door_brings_the_trees_page_and_lands_on_the_node"

MUTATIONS = [
    # The whole point of Ч2б-2: the tree is built from the MACHINE report, never parsed out of
    # the printed text. The mutation does exactly what a "just split the output lines" GUI would
    # do — one section, one problem per printed line.
    ("M53 the sections come from the printed text", REPORT_SIDE,
     '        for section_index, section in sections_with_problems(state.report):\n',
     '        text = {"sections": [{"id": "text", "title": "Report",  # MUTATION: from text\n'
     '                              "problems": [{"key": line, "kind": "",\n'
     '                                            "text": line}\n'
     '                                           for line in state.output.splitlines()\n'
     '                                           if line.strip()]}]}\n'
     '        for section_index, section in sections_with_problems(text):\n',
     [CELL_SECTIONS]),

    # "Highlight" is a VISUALISATION: it marks parts and never touches the operator's selection.
    ("M54 Highlight also selects the parts on the board", REPORT_SIDE,
     '        key = overlay_markers.check_marker_key(ref)\n',
     '        key = overlay_markers.check_marker_key(ref)\n'
     '        adapter.select_items([fp])  # MUTATION: moves the operator\'s selection\n',
     [CELL_HIGHLIGHT]),

    # The page hands the hub BOTH halves of `to_node`. Opening the tree root is the cheapest way
    # to look like a jump while landing somewhere else, so BOTH cells that assert the address
    # must redden — the success one and the miss one (measured: the miss cell used to pin only
    # the WORDING of a False and stayed green, so it now asserts what the hub was ASKED too).
    ("M55 the node door opens the tree root", REPORT_SIDE,
     '    if not select_tree_node(dock, tree, node):\n',
     '    if not select_tree_node(dock, tree, tree):  # MUTATION: the tree root\n',
     [CELL_ASKS_WALK, CELL_SAYS_MISS]),

    # A run that writes no report must not inherit the previous run's tree. (The first draft of
    # this row put `pass` BEFORE the removal and removed nothing — the miss it reported was the
    # harness's own, not a hole in the cells. A mutation must really take the promise away.)
    ("M60 the previous report file stays", REPORT_SIDE,
     '    try:\n        os.remove(path)\n    except FileNotFoundError:\n        pass\n',
     '    try:\n        logger.debug("the older report stays: %s", path)  # MUTATION\n'
     '    except FileNotFoundError:\n        pass\n',
     [CELL_STALE_FILE]),

    # Decision of Denis (26.10, answer 4): an empty section gets NO node.
    ("M61 an empty section gets a node", REPORT_SIDE,
     '        if isinstance(section, dict) and section.get("problems"):\n',
     '        if isinstance(section, dict):  # MUTATION: empty sections too\n',
     [CELL_EMPTY]),

    # The stable `key` is what makes a re-run follow the FINDING; a position would follow
    # whatever took its place.
    ("M62 the selection is restored by position", REPORT_SIDE,
     '                if problem is not None and problem.get("key") == key:\n',
     '                if problem is not None:  # MUTATION: the first row, not the key\n',
     [CELL_KEEP_KEY]),

    # The marks describe the problem selected when they were DRAWN (the Д-2 lesson of Ч2а).
    ("M63 the previous marks are not dropped", REPORT_SIDE,
     '    try:\n        owner.remove_namespace(adapter, overlay_markers.NS_CHECK)\n'
     '    except Exception:  # noqa: BLE001 — a visualisation must never fail the door\n',
     '    try:\n        logger.debug("the previous marks stay")  # MUTATION\n'
     '    except Exception:  # noqa: BLE001 — a visualisation must never fail the door\n',
     [CELL_MARKS_FIRST]),

    # A selected row nobody can see is not a jump.
    ("M65 the walk leaves the parents collapsed", TREES_SELECTION,
     '    parent = item.parent()\n'
     '    while parent is not None:\n'
     '        parent.setExpanded(True)\n'
     '        parent = parent.parent()\n'
     '    select_only(widget, item)\n',
     '    select_only(widget, item)  # MUTATION: the parents stay collapsed\n',
     [CELL_SELECT_NODE]),

    # The door shows the Trees page FIRST — a jump to a hidden tab is a jump nobody sees.
    ("M66 the door does not show the Trees page", REPORT_SIDE,
     '    hub.show_left_page(dock)\n'
     '    if not select_tree_node(dock, tree, node):\n',
     '    if not select_tree_node(dock, tree, node):  # MUTATION: no page switch\n',
     [CELL_DOOR_PAGE]),

    ("M67 cosmetic control (a comment must change nothing)", REPORT_SIDE,
     '    path = Path(directory) / f"{Path(script).stem}.json"\n',
     '    path = Path(directory) / f"{Path(script).stem}.json"  # MUTATION: cosmetic\n',
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
