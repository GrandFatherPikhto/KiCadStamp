# tests/explode/test_explode_format.py
"""Cells for the SHARED "Разнос" text formatter (Р2-1, plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md``).

The CLI and the GUI "Разнос" page must render the very same lines — there is ONE
formatter, `kicadstamp/explode_format.py`; the CLI's `_format_plan` is that
function (a thin alias), never a copy.
"""
from kicadstamp import explode_cli
from kicadstamp import explode_format
from kicadstamp.domain.geometry import Box2, Vector2
from kicadstamp.explode import ExplodePlan, MovedInstance, NetTracePiece


def _plan():
    return ExplodePlan(
        cell_name="dac_buf", cluster="CELL", sheet=None, instance=(),
        area=Box2(pos=Vector2(0, 0), size=Vector2(2_000_000, 2_000_000)),
        instances=(MovedInstance(cluster="PIF", sheet=None, refs=("C1",),
                                 footprints=(object(),), copper=(),
                                 vector=(1_000_000, 0)),),
        table=(NetTracePiece(record="rec", kind="track", net="N", layer="F.Cu",
                             length_mm=3.2, uuid="u", touches="PIF",
                             ticked=True, item=object()),),
        moves=())


def test_plan_lines_name_instances_and_ticks():
    text = "\n".join(explode_format.format_plan(_plan()))
    assert "dac_buf" in text and "PIF" in text
    assert "[x]" in text and "touches: PIF" in text


def test_status_none_and_named():
    assert "no explode journal" in explode_format.format_status(None)[0]
    journal = {"time": "2026-10-05 12:00:00", "cell": "dac_buf",
               "cluster": "CELL", "sheet": None, "items": {"a": 1, "b": 2}}
    line = explode_format.format_status(journal)[0]
    assert "dac_buf" in line and "2 item" in line


def test_cli_alias_is_the_shared_function_no_copy():
    """The CLI must NOT keep its own formatter — its alias IS the shared one."""
    assert explode_cli._format_plan is explode_format.format_plan
    assert explode_cli._format_status is explode_format.format_status
