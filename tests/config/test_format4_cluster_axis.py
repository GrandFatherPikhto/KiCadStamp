#!/usr/bin/env python3
"""К8 / Н9 (plan_2026_11_07_tree_instances_dialog_and_subtree §6.6, §7.5):
the 3 -> 4 converter and the retired `cluster:` axis of a declaration.

An instance changes the SHEET only — a cluster belongs to the RECORD, so the
declaration key is gone from format 4. A format-3 file may still carry it, and
the converter DROPS it where it provably changed nothing and REFUSES, naming the
declaration, where it did something. Both halves are pinned here, dict-level and
through a real `.sexp` file.
"""
from pathlib import Path

import pytest

from kicadstamp.config import load_config
from kicadstamp.config.format_version import CURRENT_FORMAT, upgrade_data
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError

from tests.fakes.format3 import mint_format3_files


def _data(instance: dict, *, template: dict | None = None,
          entities: list | None = None) -> dict:
    """A SELF-anchored template (no role anchor) whose placement records carry
    the given clusters — the ch0_dac_buf shape when the clusters differ."""
    tree = template if template is not None else {
        "name": "tpl", "anchor": {"self": {"ref": "main"}},
        "nodes": [
            {"ref": "main", "kind": "placement",
             "children": [{"ref": "sub", "kind": "placement"}]},
        ],
    }
    return {
        "trees": [tree],
        "entities": entities if entities is not None else [
            {"name": "main", "cell": "c", "sheet": "Own", "cluster": "DAC_BUF"},
            {"name": "sub", "cell": "c", "sheet": "Own", "cluster": "PIF_AVDD"},
        ],
        "cells": {"c": {"components": [{"role": "R1"}]}},
        "tree_instances": [instance],
    }


_COLUMNS = ("template", "name", "sheet", "cluster")


def _lift(data: dict) -> dict:
    """The 3 -> 4 step alone, on a copy: a pure dict question."""
    return upgrade_data(data, 3, "<config>")


# ── the step REMOVES the key where it changed nothing ──────────────────────

def test_the_step_removes_the_axis_of_a_composite_self_anchored_template():
    """Cell 14 (§7.5): the live `ch1_dac_buf` shape — a self-anchored template
    whose records spread over several clusters. The per-copy override was
    skipped by the composite guard there, and the anchor is no role anchor, so
    the axis did NOTHING and the key goes."""
    data = _data({"template": "tpl", "name": "ch1", "sheet": "Channel_1",
                  "cluster": "DAC_BUF"})

    out = _lift(data)

    assert "cluster" not in out["tree_instances"][0]
    assert out["tree_instances"][0]["sheet"] == "Channel_1"


def test_the_step_removes_the_axis_when_it_equals_the_records_cluster():
    """A HOMOGENEOUS template whose single cluster IS the declaration's: the
    per-copy override rewrote every copy to the value it already had."""
    data = _data(
        {"template": "tpl", "name": "ch1", "sheet": "Channel_1",
         "cluster": "GRP"},
        entities=[{"name": "main", "cell": "c", "sheet": "Own", "cluster": "GRP"},
                  {"name": "sub", "cell": "c", "sheet": "Own", "cluster": "GRP"}])

    out = _lift(data)

    assert "cluster" not in out["tree_instances"][0]


def test_a_declaration_without_the_axis_is_left_alone():
    data = _data({"template": "tpl", "name": "ch1", "sheet": "Channel_1"})

    assert _lift(data) == data


# ── ... and REFUSES it where it did something ──────────────────────────────

def test_the_step_refuses_a_homogeneous_override():
    """Cell 15 (§7.5): the mount fixture's own shape — both records are `GRP`
    and the declaration rewrote them to `GRP_a`. Dropping the key would change
    what the instance IS, so the file is refused by NAME."""
    data = _data(
        {"template": "tpl", "name": "tpl_a", "sheet": "Own_a",
         "cluster": "GRP_a"},
        entities=[{"name": "main", "cell": "c", "sheet": "Own", "cluster": "GRP"},
                  {"name": "sub", "cell": "c", "sheet": "Own", "cluster": "GRP"}])

    with pytest.raises(ValidationError) as caught:
        _lift(data)

    text = str(caught.value)
    assert "tpl_a" in text
    assert "homogeneous" in text and "'GRP_a'" in text
    # the input dict is NOT touched (the step works on a deep copy)
    assert data["tree_instances"][0]["cluster"] == "GRP_a"


def test_the_step_refuses_a_role_anchored_template():
    """A role anchor is an EXTERNAL live search — the axis narrowed it too, and
    nothing in the file can prove that was harmless."""
    template = {"name": "tpl", "anchor": {"role": "R1", "sheet": "Own"},
                "nodes": [{"ref": "main", "kind": "placement"}]}
    data = _data({"template": "tpl", "name": "ch1", "sheet": "Channel_1",
                  "cluster": "DAC_BUF"}, template=template,
                 entities=[{"name": "main", "cell": "c", "sheet": "Own",
                            "cluster": "DAC_BUF"}])

    with pytest.raises(ValidationError, match="role-anchored"):
        _lift(data)


def test_the_step_refuses_an_address_on_the_templates_own_sheet():
    """A mount node anchored to the template's OWN sheet had its `cluster`
    rewritten by the same declaration key."""
    template = {
        "name": "tpl", "anchor": {"self": {"ref": "main"}},
        "nodes": [{"ref": "main", "kind": "placement", "children": [
            {"ref": "m1", "kind": "mount",
             "anchor": {"role": "R_OUT", "sheet": "Own"}}]}],
    }
    data = _data({"template": "tpl", "name": "ch1", "sheet": "Channel_1",
                  "cluster": "DAC_BUF"}, template=template,
                 entities=[{"name": "main", "cell": "c", "sheet": "Own",
                            "cluster": "DAC_BUF"}])

    with pytest.raises(ValidationError, match="addresses 'm1'"):
        _lift(data)


def test_the_step_refuses_when_the_template_is_not_in_this_file():
    """A declaration pointing at a template that lives in ANOTHER file of the
    graph cannot be judged here — and guessing is forbidden."""
    data = _data({"template": "elsewhere", "name": "ch1", "sheet": "Channel_1",
                  "cluster": "DAC_BUF"})

    with pytest.raises(ValidationError, match="not in this file"):
        _lift(data)


# ── through a REAL .sexp file ──────────────────────────────────────────────

def _write(tmp_path: Path, data: dict, format_number: int = 3) -> Path:
    """A REAL file carrying the number it claims: at 3 every record needs its
    uuid, so the dict is minted through the same test stub the other cells use."""
    if format_number >= 3:
        data = mint_format3_files({"cluster.sexp": data})["cluster.sexp"]
    p = tmp_path / "cluster.sexp"
    p.write_text(dict_to_sexp(data, format_number=format_number),
                 encoding="utf-8")
    return p


def test_a_format_three_file_loses_the_key_on_load(tmp_path):
    """The product path: parse (which still ACCEPTS the retired key) -> the
    3 -> 4 step -> the key is gone and the instance materializes as before."""
    p = _write(tmp_path, _data({"template": "tpl", "name": "ch1",
                                "sheet": "Channel_1", "cluster": "DAC_BUF"}))

    cfg, _ctx = load_config(str(p))

    text = p.read_text(encoding="utf-8")
    assert f"(version {CURRENT_FORMAT})" in text
    # the DECLARATION lost the key; the ENTITY records keep their own clusters
    row = sexp_to_dict(text)["tree_instances"][0]
    assert "cluster" not in row
    assert {e["cluster"] for e in sexp_to_dict(text)["entities"]} == {
        "DAC_BUF", "PIF_AVDD"}
    declaration = next(ti for ti in cfg.tree_instances if ti.name == "ch1")
    assert declaration.sheet == "Channel_1"
    copy = next(t for t in cfg.trees if t.name == "ch1")
    assert [n.ref for n in copy.nodes] == ["main__ch1"]


def test_a_file_the_step_refuses_is_left_byte_for_byte(tmp_path):
    """A refusal must not touch the file — the lift writes only what it proved
    it may write, and the `.bak` is not made either."""
    data = _data(
        {"template": "tpl", "name": "ch1", "sheet": "Channel_1",
         "cluster": "OTHER"},
        entities=[{"name": "main", "cell": "c", "sheet": "Own", "cluster": "GRP"},
                  {"name": "sub", "cell": "c", "sheet": "Own", "cluster": "GRP"}])
    p = _write(tmp_path, data)
    before = p.read_bytes()

    with pytest.raises(ValidationError, match="homogeneous"):
        load_config(str(p))

    assert p.read_bytes() == before
    assert not list(tmp_path.glob("*.bak*"))


def test_the_retired_key_is_still_parseable_on_its_own():
    """The reader must ACCEPT the retired key into the raw dict — otherwise the
    converter above could never run (the parse would die first). A CURRENT
    format file carrying it is refused by the loader instead."""
    data = _data({"template": "tpl", "name": "ch1", "sheet": "Channel_1",
                  "cluster": "DAC_BUF"})
    text = dict_to_sexp(data, format_number=3)

    raw = sexp_to_dict(text, upgrade=False)

    assert raw["tree_instances"][0]["cluster"] == "DAC_BUF"
    assert _COLUMNS[3] in raw["tree_instances"][0]
