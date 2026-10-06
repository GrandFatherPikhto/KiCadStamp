# tests/gui/test_subtract_copper.py
"""The «Subtract selected copper» REPORT — its pure half (С-2а-3 of
plan_2026_10_06_prune_absent_cell_copper; Denis 2026-10-06).

`subtract_report_lines(result)` turns a worker result into ``[(text, level)]``
with no widget and no Qt type in sight: the CellDock maps the level onto its own
message style (`gui.docks._common.style_for_level`) and prints the line. These
cells pin every branch of the worker's answer and the LEVEL of every line —
including that a removed record is named by the ONE formatter the refresh/import
report already uses (`record_report_line`), never a second copy.

What the CELLS of the dock pin instead (tests/gui/docks/test_subtract_selected_copper.py):
that the report is actually USED, and that the removed records leave BOTH levels
of the cell (С-2а-1).
"""
import pytest

from gui.docks._common import LEVEL_STYLE
from gui.subtract_copper import subtract_report_lines


def _track(net, across):
    return {"net": net, "layer": "F.Cu", "width_mm": 0.25,
            "start_along_mm": 0.0, "start_across_mm": across,
            "end_along_mm": 1.0, "end_across_mm": across}


def _via(across):
    return {"net": "N", "offset_along_mm": 0.0, "offset_across_mm": across,
            "drill_mm": 0.3, "diameter_mm": 0.6}


def test_every_removed_record_is_named_then_the_summary(qapp):
    """One «- track …» WARN line per removed record, in the worker's order, then
    the green summary counting them — never a bare counter."""
    track = _track("GND", 2.0)

    lines = subtract_report_lines({
        "cell": "dac_buf", "removed": [("track", track)], "not_ours": 0})

    assert len(lines) == 2, lines
    text, level = lines[0]
    assert level == "warn"
    assert text.startswith("- track GND F.Cu w=0.25"), text
    assert lines[1] == ("subtracted 1 record(s) — Save to write the change",
                        "success")


def test_a_removed_via_is_named_as_a_via(qapp):
    """The kind comes from the result TUPLE, not from the record's shape: a via
    record is a «- via …» line and a track record is a «- track …» one."""
    lines = subtract_report_lines({
        "cell": "dac_buf",
        "removed": [("via", _via(1.5)), ("track", _track("N", 0.0))],
        "not_ours": 0})

    assert [line[0].startswith(prefix) for line, prefix in
            zip(lines, ("- via ", "- track ", "subtracted 2"))] == [True] * 3
    assert [line[1] for line in lines] == ["warn", "warn", "success"]


def test_nothing_removed_is_green_and_the_ignored_count_is_a_warning(qapp):
    """A selection that names no record of the cell: the "nothing to subtract"
    line is SUCCESS (the action did its job) and the ignored count is a separate
    WARN line — the ignore is never silent."""
    lines = subtract_report_lines({
        "cell": "dac_buf", "removed": [], "not_ours": 2})

    assert lines == [
        ("nothing to subtract — the selection holds no copper record of cell "
         "'dac_buf'", "success"),
        ("2 selected item(s) are not records of cell 'dac_buf' — ignored", "warn"),
    ]


def test_nothing_removed_and_nothing_ignored_is_one_line(qapp):
    """The negative half of the line above: with nothing ignored the report does
    NOT grow a "0 selected item(s) are not records" line."""
    lines = subtract_report_lines({
        "cell": "dac_buf", "removed": [], "not_ours": 0})

    assert lines == [
        ("nothing to subtract — the selection holds no copper record of cell "
         "'dac_buf'", "success"),
    ]


def test_an_empty_dry_run_is_the_red_could_not_match_line(qapp):
    """The check could not run (a refused tree, an unrealized record, a chain-only
    placement): ONE red line, never "nothing to subtract"."""
    lines = subtract_report_lines({"cell": "dac_buf", "empty": True})

    assert lines == [
        ("could not match the selection to the cell's records — the dry run "
         "planned nothing", "error"),
    ]


def test_only_components_selected_names_the_count_in_warning(qapp):
    """Copper only: a components-only selection is a WARN line naming how many
    components were ignored."""
    lines = subtract_report_lines({
        "cell": "dac_buf", "no_copper": True, "components": 3})

    assert lines == [
        ("no copper is selected — nothing was subtracted "
         "(3 component(s) are ignored)", "warn"),
    ]


def test_two_matching_instances_are_refused_with_both_labels(qapp):
    """The ambiguity refusal names the instance labels and tells the user what to
    do — red, and alone (nothing was removed)."""
    lines = subtract_report_lines({
        "cell": "dac_buf",
        "ambiguous": ["DAC_BUF on Channel_0", "DAC_BUF on Channel_1"]})

    assert len(lines) == 1, lines
    text, level = lines[0]
    assert level == "error"
    assert "matches 2 instances of cell 'dac_buf'" in text
    assert "DAC_BUF on Channel_0; DAC_BUF on Channel_1" in text
    assert "subtract again" in text


def test_an_error_is_one_red_line_per_problem(qapp):
    """A collected fatal arrives as a multi-line block: every problem gets its own
    red Log line (Н4.7), never a dialog and never one glued blob."""
    lines = subtract_report_lines({
        "cell": "dac_buf",
        "error": "FATAL: something\n\n  - the first problem\n  - the second "
                 "problem\n"})

    assert [level for _text, level in lines] == ["error", "error", "error"]
    assert [text for text, _level in lines] == [
        "FATAL: something", "- the first problem", "- the second problem"]


@pytest.mark.parametrize("result", [
    {"cell": "c", "removed": [], "not_ours": 0},
    {"cell": "c", "empty": True},
    {"cell": "c", "no_copper": True, "components": 1},
    {"cell": "c", "ambiguous": ["a", "b"]},
    {"cell": "c", "error": "boom"},
])
def test_every_level_is_one_the_dock_can_map(qapp, result):
    """The report is Qt-free: it names a LEVEL, and every level it can name is one
    `gui.docks._common.LEVEL_STYLE` knows — a new level invented here would print
    in the wrong colour (or crash the dock), and this cell says so."""
    levels = {level for _text, level in subtract_report_lines(result)}

    assert levels
    assert levels <= set(LEVEL_STYLE)
