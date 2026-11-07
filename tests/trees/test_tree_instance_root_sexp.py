#!/usr/bin/env python3
"""Acceptance 8.2 (plan_2026_11_07_tree_instances_dialog_and_subtree): `root`
through a REAL `.sexp` file, not only a dict.

The blocker was that the s-expr reader takes a record's keys from
`models.TreeInstance` and the field did not exist, so a hand-written
`(root (ref "..."))` loaded as "unknown key in a record" while docs described
the form. These cells go through `load_config`, the writer and the dialog's own
upsert.
"""
from pathlib import Path

import pytest

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_writer import upsert_tree_instances


def _data(instances) -> dict:
    return {
        "cells": {
            "c_main": {"components": [{"role": "R_MAIN"}]},
            "c_sub": {"components": [{"role": "R_SUB"}]},
        },
        "entities": [
            {"name": "driver", "cell": "c_main", "sheet": "SW A"},
            {"name": "sw", "cell": "c_sub", "sheet": "SW A"},
            {"name": "other", "cell": "c_sub", "sheet": "SW A"},
        ],
        "trees": [{
            "name": "tpl", "anchor": {"role": "R_MAIN"},
            "nodes": [
                {"ref": "driver", "kind": "placement", "xy": [10.0, 0.0],
                 "children": [{"ref": "sw", "kind": "placement",
                               "xy": [2.0, 0.0]}]},
                {"ref": "other", "kind": "placement", "xy": [0.0, 5.0]},
            ],
        }],
        "tree_instances": list(instances),
    }


def _write(tmp_path: Path, instances, format_number: int = 2) -> Path:
    p = tmp_path / "root.sexp"
    p.write_text(dict_to_sexp(_data(instances), format_number=format_number),
                 encoding="utf-8")
    return p


_ROW = {"template": "tpl", "name": "b", "sheet": "SW B",
        "anchor": {"origin": True}}


def test_root_from_a_sexp_file_instantiates_the_subtree(tmp_path):
    """The blocker itself: a hand-written (root ...) LOADS."""
    p = _write(tmp_path, [dict(_ROW, root={"ref": "driver"})])

    cfg, _ctx = load_config(str(p))

    copy = next(t for t in cfg.trees if t.name == "b")
    assert [n.ref for n in copy.nodes] == ["driver__b"]
    assert {e.name for e in cfg.entities if e.name.endswith("__b")} == {
        "driver__b", "sw__b"}
    declaration = next(ti for ti in cfg.tree_instances if ti.name == "b")
    assert declaration.root["ref"] == "driver"


def test_the_root_round_trips_through_sexp(tmp_path):
    """Written and read back — the same declaration, by meaning."""
    data = _data([dict(_ROW, root={"ref": "driver"})])
    text = dict_to_sexp(data, format_number=2)
    assert "(root" in text and '(ref "driver")' in text

    back = sexp_to_dict(text)["tree_instances"][0]
    # Reading LIFTS to format 3, so the reference also carries its uuid — the
    # NAME is the part the declaration wrote and must come back unchanged.
    assert back["root"]["ref"] == "driver"


def test_a_root_name_hint_is_normalized_by_its_uuid(tmp_path):
    """Format 3: the name beside the uuid is a HINT; the real target name comes
    from the uuid (the same rule every other reference follows). Built by
    lifting a format-2 file (every record gets its uuid), then spoiling the
    hint inside `root`."""
    p = _write(tmp_path, [dict(_ROW, root={"ref": "driver"})])
    lifted = dict_to_sexp(sexp_to_dict(p.read_text(encoding="utf-8"),
                                      path=str(p)), format_number=3)
    # Layout-independent: the writer pretty-prints, whitespace does not matter
    # to the reader, so compare and rewrite the FLAT text.
    flat = " ".join(lifted.split())
    assert '(root (ref "driver" (uuid ' in flat
    p.write_text(flat.replace('(root (ref "driver"',
                              '(root (ref "WRONG_HINT"', 1), encoding="utf-8")

    cfg, _ctx = load_config(str(p))

    declaration = next(ti for ti in cfg.tree_instances if ti.name == "b")
    assert declaration.root["ref"] == "driver"
    assert declaration.root["ref_uuid"]


def test_the_dialog_write_keeps_the_root_of_a_row_it_does_not_edit(tmp_path):
    """Н6 + §7.4: `root` is not one of the dialog's editable axes, so an
    ordinary OK on another field must not drop it (WRITE OVER, NOT INSTEAD)."""
    from kicadstamp.config_working_set import set_active_graph_root

    p = _write(tmp_path, [dict(_ROW, root={"ref": "driver"})])
    set_active_graph_root(p)
    try:
        changed = upsert_tree_instances(p, "tpl", [
            {"template": "tpl", "name": "b", "sheet": "SW B_NEW",
             "anchor": {"origin": True}}])
    finally:
        set_active_graph_root(None)
    assert changed is True

    row = sexp_to_dict(p.read_text(encoding="utf-8"))["tree_instances"][0]
    assert row["sheet"] == "SW B_NEW"
    assert row["root"]["ref"] == "driver"


def test_a_root_without_a_uuid_still_loads_in_format_2(tmp_path):
    """Control: the plain name form is legal under format 2 (no uuid table)."""
    p = _write(tmp_path, [dict(_ROW, root={"ref": "sw"})])

    cfg, _ctx = load_config(str(p))

    copy = next(t for t in cfg.trees if t.name == "b")
    assert [n.ref for n in copy.nodes] == ["sw__b"]
    assert copy.nodes[0].xy == (12.0, 0.0)
