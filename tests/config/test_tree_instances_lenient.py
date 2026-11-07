#!/usr/bin/env python3
"""Н8 of plan_2026_11_07_tree_instances_dialog_and_subtree: a BROKEN
`tree_instances:` declaration must not lock the whole profile.

The live fact (HP-Power-v099, 07.11): a declaration whose template could not be
expanded made `load_config` refuse EVERYTHING; the Instances dialog never opened,
so the declaration could not be fixed with the very tool that owns it — the only
way out was File → Discard unsaved changes.

The rule: `expand_tree_instances(data, failures=[])` refuses PER DECLARATION and
adds NOTHING of a refused one; `load_config_lenient` is that mode behind the
loader and returns the refusals. The STRICT door (`load_config`) is unchanged,
and so are TreesDock and Save.
"""
from pathlib import Path

import pytest

from kicadstamp.config import load_config, load_config_lenient
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config.tree_instances import expand_tree_instances
from kicadstamp.exceptions import ValidationError

# The broken row: the POINT-anchored template's mount node has NO sheet in its
# anchor, which its expansion refuses ("without a sheet the role is ambiguous
# across the instances' sheets") — a refusal Н3 cannot derive away. The good row
# instantiates a role-anchored template.
_BROKEN = {"template": "point_tpl", "name": "bad", "sheet": "S2",
           "anchor": {"origin": True}}
_GOOD = {"template": "tpl", "name": "good", "sheet": "S2"}


def _data(instances) -> dict:
    return {
        "trees": [
            {"name": "tpl", "anchor": {"role": "DAC"},
             "nodes": [{"ref": "e1", "kind": "placement", "xy": [0.0, 0.0]}]},
            {"name": "point_tpl", "anchor": {"point": "P"},
             "nodes": [
                 {"ref": "e2", "kind": "placement", "xy": [0.0, 0.0]},
                 {"ref": "m1", "kind": "mount", "xy": [0.0, 0.0],
                  "anchor": {"role": "HOST"}},
             ]},
        ],
        "entities": [{"name": "e1", "cell": "c1", "sheet": "S"},
                     {"name": "e2", "cell": "c1", "sheet": "S"}],
        "cells": {"c1": {}},
        "points": {"P": {"xy": [0.0, 0.0]}},
        "tree_instances": list(instances),
    }


def _write(tmp_path: Path, instances) -> Path:
    p = tmp_path / "root.sexp"
    p.write_text(dict_to_sexp(_data(instances), format_number=2),
                 encoding="utf-8")
    return p


# ── the core: one declaration refuses, the rest materializes ────────────────

def test_a_refused_declaration_is_reported_and_the_rest_loads():
    failures: list[dict] = []

    out = expand_tree_instances(_data([_BROKEN, _GOOD]), failures=failures)

    assert [t["name"] for t in out["trees"]] == ["tpl", "point_tpl", "good"]
    assert len(failures) == 1
    row = failures[0]
    assert row["index"] == 1
    assert (row["template"], row["name"], row["sheet"]) == ("point_tpl", "bad", "S2")
    assert "no sheet in its anchor" in row["error"]


def test_a_refused_declaration_adds_nothing_of_its_own():
    """§7.2: the refusal is ATOMIC — no tree, no Entity copy, no net_traces
    record. The answer must equal a strict load without the row."""
    with_failure = expand_tree_instances(_data([_BROKEN, _GOOD]), failures=[])
    without = expand_tree_instances(_data([_GOOD]))

    assert with_failure["trees"] == without["trees"]
    assert with_failure["entities"] == without["entities"]
    assert with_failure["net_traces"] == without["net_traces"]
    assert "bad" not in {t["name"] for t in with_failure["trees"]}
    assert "e1__bad" not in {e["name"] for e in with_failure["entities"]}


def test_the_strict_mode_still_refuses_the_same_data():
    """Control: without the `failures` list the historical fatal is untouched."""
    with pytest.raises(ValidationError, match="no sheet in its anchor"):
        expand_tree_instances(_data([_GOOD, _BROKEN]))


def test_graph_wide_refusals_stay_fatal_even_in_the_lenient_mode():
    """Two declarations sharing one name are not "one declaration's" property:
    the whole section is wrong, so even the lenient door refuses."""
    twice = [dict(_GOOD), dict(_GOOD, template="point_tpl")]

    with pytest.raises(ValidationError, match="duplicate name"):
        expand_tree_instances(_data(twice), failures=[])


# ── К3а (8.1): EVERY refusal of ONE declaration is tolerable ───────────────

@pytest.mark.parametrize("bad_row,needle", [
    ({"template": "no_such_tree", "name": "bad", "sheet": "S2"}, "not found"),
    ({"template": "tpl", "name": "bad"}, "missing required sheet"),
    ({"template": "tpl", "name": "bad", "sheet": "S2", "anchor": "x"},
     "non-mapping anchor"),
    ({"template": "tpl", "name": "point_tpl", "sheet": "S2"},
     "duplicate tree name"),
    ({"template": "tpl", "name": "bad", "sheet": "S2", "cluster": ""},
     "empty cluster"),
    ({"template": "tpl", "name": "bad", "sheet": "S2", "params": "x"},
     "non-mapping params"),
    ({"template": "tpl", "name": "bad", "sheet": "S2", "rotation": "90"},
     "non-numeric rotation"),
    ({"template": "tpl", "name": "bad", "sheet": "S2", "root": "driver"},
     "root: must be a mapping"),
    ("not-a-dict", "must be a mapping"),
])
def test_every_refusal_of_one_declaration_is_tolerable(bad_row, needle):
    """§8.1: a rename or a delete in Trees does not touch the rows that point at
    the old template, so "template not found" is ordinary work — and it used to
    kill the whole profile, dialog included. Every per-row refusal is recorded
    and the rest of the graph materializes; the answer is byte-identical to the
    strict load of the file without that row."""
    failures: list[dict] = []

    out = expand_tree_instances(_data([bad_row, _GOOD]), failures=failures)

    assert len(failures) == 1
    assert needle in failures[0]["error"]
    assert "bad" not in {t["name"] for t in out["trees"]}
    assert "good" in {t["name"] for t in out["trees"]}
    assert out["trees"] == expand_tree_instances(_data([_GOOD]))["trees"]


def test_the_strict_mode_still_raises_for_every_one_of_them():
    """Control: without the failures list the same row is the same fatal."""
    with pytest.raises(ValidationError, match="template tree 'no_such_tree'"):
        expand_tree_instances(_data([
            {"template": "no_such_tree", "name": "bad", "sheet": "S2"}]))


# ── the loader: the lenient door ───────────────────────────────────────────

def test_load_config_lenient_opens_on_a_file_the_strict_door_refuses(tmp_path):
    p = _write(tmp_path, [_BROKEN, _GOOD])

    with pytest.raises(ValidationError):
        load_config(str(p))

    cfg, _ctx, failures = load_config_lenient(str(p))
    assert [f["name"] for f in failures] == ["bad"]
    assert [t.name for t in cfg.trees] == ["tpl", "point_tpl", "good"]
    # The raw section survives, so the dialog still SEES the broken row.
    assert [ti.name for ti in cfg.tree_instances] == ["bad", "good"]


def test_the_lenient_answer_is_not_cached_under_the_strict_key(tmp_path):
    """The two doors must never shadow each other: the same path answers
    strictly for `load_config` and leniently for `load_config_lenient`."""
    p = _write(tmp_path, [_BROKEN, _GOOD])

    with pytest.raises(ValidationError):
        load_config(str(p))
    cfg, _ctx, failures = load_config_lenient(str(p))
    assert failures and [t.name for t in cfg.trees] == ["tpl", "point_tpl", "good"]
    with pytest.raises(ValidationError):
        load_config(str(p))


def test_a_broken_row_in_the_working_set_is_seen_and_can_be_fixed(
        tmp_path, monkeypatch):
    """§7.1 of the plan — the LIVE case: the broken row was staged, not on disk.
    The lenient load reads the same graph Save would flush, so the row shows up
    as a refusal and disappears as soon as it is corrected (cells 1б)."""
    from kicadstamp.config_writer import upsert_tree_instances
    from kicadstamp.config_working_set import WORKING_SET, set_active_graph_root

    p = _write(tmp_path, [_GOOD])
    # The format-3 writer resolves references against the ACTIVE graph root —
    # the dialog's own fixture does the same.
    set_active_graph_root(p)
    monkeypatch.setattr(WORKING_SET, "enabled", True)
    try:
        upsert_tree_instances(p, "point_tpl", [{
            "name": "bad", "sheet": "S2", "anchor": {"origin": True}}])
        assert WORKING_SET.is_dirty()
        # The row is staged, so the file on disk still has only the good one.
        assert "bad" not in p.read_text(encoding="utf-8")

        _cfg, _ctx, failures = load_config_lenient(str(p))
        assert [f["name"] for f in failures] == ["bad"]

        upsert_tree_instances(p, "point_tpl", [])
        _cfg, _ctx, failures = load_config_lenient(str(p))
        assert failures == []
    finally:
        WORKING_SET.clear()
        set_active_graph_root(None)
