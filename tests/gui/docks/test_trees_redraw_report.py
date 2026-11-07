#!/usr/bin/env python3
"""The wording of a finished curated Redraw — `gui/docks/trees_redraw_report.py`
(Н2, plan_2026_11_07_tree_instances_dialog_and_subtree §6.9 К2).

The fact: `TreesDock._finish_redraw` printed "Redraw: 0/0 ok" when the worker
came back EMPTY, i.e. exactly when materialization had skipped the tree because
its anchor failed — the Log said why, the dock said "ok". An empty plan is not a
success.

Qt-free: the module is pure functions over the worker's own result shape
([(name, good, error), ...] plus the warning list).
"""
from gui.docks.trees_redraw_report import redraw_log_lines, redraw_status_text


def test_an_empty_redraw_is_a_warning_never_an_ok_line():
    lines = redraw_log_lines([])

    assert [level for level, _text in lines] == ["warning"]
    assert "nothing was planned" in lines[0][1]
    assert "ok" not in lines[0][1]


def test_the_empty_redraw_points_at_the_log_where_the_skipped_tree_is_named():
    """The reason (tree + node) is written by materialization BEFORE this line:
    the report must send the user there instead of inventing a second reason."""
    text = redraw_log_lines([])[0][1]

    assert "see the Log above" in text


def test_a_non_empty_redraw_keeps_the_historical_lines():
    """Control: a real result prints exactly what it printed before — one INFO
    "ok" line, plus the WARNING count when something failed."""
    results = [("a", True, None), ("b", True, None)]

    lines = redraw_log_lines(results)

    assert lines == [("info", "Redraw: 2/2 ok")]


def test_a_failed_record_adds_its_warning_line():
    results = [("a", True, None), ("b", False, "boom")]

    lines = redraw_log_lines(results)

    assert [level for level, _t in lines] == ["info", "warning"]
    assert "1 record(s) failed" in lines[1][1]


def test_the_status_of_an_empty_redraw_says_nothing_was_planned():
    assert redraw_status_text([], []) == "nothing was planned — see the log"


def test_the_status_keeps_the_warning_count_line_and_the_ok_line():
    results = [("a", True, None)]

    assert redraw_status_text(results, ["w"]) == "1 warning(s) — see log"
    assert redraw_status_text(results, []) == "Redraw: 1/1 ok"
