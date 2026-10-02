# tests/config/test_config_format3.py
"""Format-3 grammar/model and load-check cells — plan У1 (variant B, §У1.2).

Cells (rule 35 table): section x (record with / without uuid) x format
(s-expr / JSON); a reference whose name-hint LIES round-trips both values and
does not trip the UUID check (U1 does not reconcile name and UUID); each У1.3
fatal is its own cell asserting the place text.

Only tests pin the build to format 3 (fixture ``format3``); in the product a
format-3 file is still refused, so every check here sleeps outside these tests.
"""
import json

import pytest

from kicadstamp.config.loader import _check_format3_graph, load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError
from tests.fakes.format3 import det_uuid, format3  # noqa: F401 (fixture import)

D_CELL = det_uuid("cell")
D_POINT = det_uuid("point")
D_ENTITY = det_uuid("entity")
D_CHAIN = det_uuid("chain")
D_FOLDER_P = det_uuid("folder-Power")
D_FOLDER_L = det_uuid("folder-Power/LDO")


def _data() -> dict:
    """A minimal format-3 dict: a dict section (cells) with a uuid and a folder
    table, a points entry, an entity referencing the cell, and a chain
    referencing the point. No default-valued keys (the writer omits those)."""
    return {
        "cells": {"Power/LDO/ldo": {"layer": "B.Cu", "uuid": D_CELL}},
        "points": {"p1": {"anchor_ref": "IC1", "uuid": D_POINT}},
        "entities": [{"name": "E1", "cell": "Power/LDO/ldo",
                      "cell_uuid": D_CELL, "uuid": D_ENTITY}],
        "chains": [{"net": "GND", "name": "ch1", "anchor_point": "p1",
                    "anchor_point_uuid": D_POINT, "uuid": D_CHAIN,
                    "spokes": []}],
        "folders": {"cells": {"Power": D_FOLDER_P, "Power/LDO": D_FOLDER_L}},
    }


def _write_sexp(path, data) -> None:
    path.write_text(dict_to_sexp(data, format_number=3), encoding="utf-8")


# ── grammar round-trip (s-expr) ────────────────────────────────────────────

def test_sexp_roundtrip_carries_record_uuid_ref_uuid_and_folders(format3):
    data = _data()
    text = dict_to_sexp(data, format_number=3)
    assert "(version 3)" in text
    assert f'(uuid "{D_CELL}")' in text          # cell record identity
    assert f'(uuid "{D_ENTITY}")' in text        # entity record identity
    assert "(folders" in text                    # folder table is a section child
    assert sexp_to_dict(text) == data


def test_reference_node_nests_uuid_beside_the_name(format3):
    text = dict_to_sexp(_data(), format_number=3)
    # (cell "Power/LDO/ldo" (uuid "…")) — the UUID is NESTED in the reference
    # node; the sibling JSON key <field>_uuid is never written in s-expr.
    assert f'(uuid "{D_CELL}")' in text
    assert "cell_uuid" not in text
    assert "anchor_point_uuid" not in text
    assert '"Power/LDO/ldo"' in text


def test_record_without_uuid_roundtrips_and_stays_uuidless(format3):
    """U1 is additive: the serializer does not invent a UUID; the 'must have a
    UUID' rule is a LOAD check (see the fatal cell below), not a write rule."""
    data = {"cells": {"c": {"layer": "B.Cu"}}}
    text = dict_to_sexp(data, format_number=3)
    assert "uuid" not in text
    assert sexp_to_dict(text) == data


def test_lying_name_hint_roundtrips_both_values_and_does_not_fatal(format3, tmp_path):
    """The name-hint may lie while the UUID is valid: the writer round-trips
    BOTH exactly as read (no reconciling in U1), and the format-3 UUID check
    resolves by UUID, never by the hint — so it does not fatal."""
    data = _data()
    data["entities"][0]["cell"] = "WRONG/NAME"        # hint lies
    text = dict_to_sexp(data, format_number=3)
    assert sexp_to_dict(text) == data                 # both values preserved
    _check_format3_graph(data, str(tmp_path / "probe.sexp"))   # must not raise


# ── grammar (JSON): record key "uuid", reference sibling key ───────────────

def test_json_shape_record_uuid_and_reference_sibling_key(format3, tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({**_data(), "version": 3}), encoding="utf-8")
    cfg, _ = load_config(str(p))
    assert cfg.cells["Power/LDO/ldo"].uuid == D_CELL
    assert cfg.entities[0].uuid == D_ENTITY
    assert cfg.entities[0].cell_uuid == D_CELL
    assert cfg.chains[0].anchor_point_uuid == D_POINT
    assert cfg.folders["cells"]["Power"] == D_FOLDER_P


# ── У1.3 fatals, each with its own place text ──────────────────────────────

def test_record_without_uuid_is_fatal(format3, tmp_path):
    p = tmp_path / "config.sexp"
    _write_sexp(p, {"cells": {"c": {"layer": "F.Cu"}}})
    with pytest.raises(ValidationError) as e:
        load_config(str(p))
    assert "no uuid" in str(e.value)
    assert str(p) in str(e.value)


def test_duplicate_uuid_in_the_graph_is_fatal(format3, tmp_path):
    p = tmp_path / "config.sexp"
    _write_sexp(p, {"cells": {"a": {"layer": "F.Cu", "uuid": D_CELL},
                              "b": {"layer": "F.Cu", "uuid": D_CELL}}})
    with pytest.raises(ValidationError) as e:
        load_config(str(p))
    assert "duplicate uuid" in str(e.value)
    assert str(p) in str(e.value)


def test_dangling_reference_uuid_is_fatal(format3, tmp_path):
    data = _data()
    data["entities"][0]["cell_uuid"] = det_uuid("does-not-exist")
    p = tmp_path / "config.sexp"
    _write_sexp(p, data)
    with pytest.raises(ValidationError) as e:
        load_config(str(p))
    assert "does not exist" in str(e.value)
    assert str(p) in str(e.value)


def test_valid_format3_config_loads(format3, tmp_path):
    """The control: the same data WITHOUT a broken UUID loads cleanly."""
    p = tmp_path / "config.sexp"
    _write_sexp(p, _data())
    cfg, _ = load_config(str(p))
    assert cfg.cells["Power/LDO/ldo"].uuid == D_CELL
    assert cfg.folders["cells"]["Power/LDO"] == D_FOLDER_L
