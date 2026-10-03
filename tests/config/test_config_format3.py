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

from kicadstamp.config.loader import load_config
from kicadstamp.config_writer import write_config_file
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
    data["entities"][0]["cell"] = "WRONG/NAME"        # the NAME hint lies
    text = dict_to_sexp(data, format_number=3)
    assert sexp_to_dict(text) == data                 # both values preserved
    p = tmp_path / "config.sexp"
    _write_sexp(p, data)
    load_config(str(p))   # must not raise: the check resolves by UUID, not the hint


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
    assert "references uuid" in str(e.value)
    assert str(p) in str(e.value)          # Н7: the place names the record's file


def test_valid_format3_config_loads(format3, tmp_path):
    """The control: the same data WITHOUT a broken UUID loads cleanly."""
    p = tmp_path / "config.sexp"
    _write_sexp(p, _data())
    cfg, _ = load_config(str(p))
    assert cfg.cells["Power/LDO/ldo"].uuid == D_CELL
    assert cfg.folders["cells"]["Power/LDO"] == D_FOLDER_L


# ── Н8: the sections/ref forms the first cut missed ────────────────────────

def _dangling(format3, tmp_path, data, needle="references uuid"):
    p = tmp_path / "config.sexp"
    _write_sexp(p, data)
    with pytest.raises(ValidationError) as e:
        load_config(str(p))
    assert needle in str(e.value)
    assert str(p) in str(e.value)


def test_chain_spoke_cell_reference_uuid(format3, tmp_path):
    """R4: chains[*].spokes[].cell -> cells. Round-trips and dangles."""
    data = {"cells": {"c": {"layer": "B.Cu", "uuid": D_CELL}},
            "chains": [{"net": "GND", "name": "ch", "anchor_ref": "IC1",
                        "uuid": D_CHAIN,
                        "spokes": [{"pad": "1", "cell": "c", "cell_uuid": D_CELL}]}]}
    assert sexp_to_dict(dict_to_sexp(data, format_number=3)) == data
    data["chains"][0]["spokes"][0]["cell_uuid"] = det_uuid("nope")
    _dangling(format3, tmp_path, data)


def test_nested_cell_placement_reference_uuid(format3, tmp_path):
    """R5: cells[*].clone_placements[].cell -> cells. Round-trips and dangles."""
    data = {"cells": {"c": {"layer": "B.Cu", "uuid": D_CELL},
                      "parent": {"layer": "B.Cu", "uuid": D_POINT,
                                 "clone_placements": [{"name": "n1", "cell": "c",
                                                       "cell_uuid": D_CELL}]}}}
    assert sexp_to_dict(dict_to_sexp(data, format_number=3)) == data
    data["cells"]["parent"]["clone_placements"][0]["cell_uuid"] = det_uuid("nope")
    _dangling(format3, tmp_path, data)


def test_entity_imprint_reference_uuid(format3, tmp_path):
    """R6: entities[].imprint -> imprints. Round-trips and dangles."""
    data = {"imprints": [{"name": "imp", "uuid": D_POINT,
                          "components": [{"ref": "C1"}]}],
            "entities": [{"name": "E", "imprint": "imp", "imprint_uuid": D_POINT,
                          "uuid": D_ENTITY}]}
    assert sexp_to_dict(dict_to_sexp(data, format_number=3)) == data
    data["entities"][0]["imprint_uuid"] = det_uuid("nope")
    _dangling(format3, tmp_path, data)


def test_json_thermal_via_array_anchor_point_uuid(format3, tmp_path):
    """J2: the JSON side of a thermal_via_array's anchor_point_uuid."""
    data = {"points": {"p": {"anchor_ref": "IC1", "uuid": D_POINT}},
            "thermal_via_arrays": [{"name": "tva", "anchor_point": "p",
                                    "anchor_point_uuid": D_POINT, "uuid": D_CELL}]}
    p = tmp_path / "config.json"
    p.write_text(json.dumps({**data, "version": 3}), encoding="utf-8")
    cfg, _ = load_config(str(p))
    assert cfg.thermal_via_arrays[0].anchor_point_uuid == D_POINT


def test_json_writer_roundtrip_carries_uuid(format3, tmp_path):
    """The JSON WRITE side (not just reading) keeps the UUIDs."""
    p = tmp_path / "config.json"
    write_config_file(str(p), _data())
    cfg, _ = load_config(str(p))
    assert cfg.entities[0].uuid == D_ENTITY
    assert cfg.entities[0].cell_uuid == D_CELL
    assert cfg.folders["cells"]["Power"] == D_FOLDER_P


def test_duplicate_full_name_across_the_graph_is_fatal(format3, tmp_path):
    """Н4(б): two same-named records in ONE section, in two files of the graph."""
    root = tmp_path / "root.sexp"
    sub = tmp_path / "sub.sexp"
    _write_sexp(sub, {"entities": [{"name": "E", "cell": "c", "uuid": D_ENTITY}]})
    _write_sexp(root, {"include": ["sub.sexp"],
                       "cells": {"c": {"layer": "B.Cu", "uuid": D_CELL}},
                       "entities": [{"name": "E", "cell": "c", "uuid": D_POINT}]})
    with pytest.raises(ValidationError) as e:
        load_config(str(root))
    assert "duplicate full name" in str(e.value)
    assert "root.sexp" in str(e.value) and "sub.sexp" in str(e.value)


def test_service_section_record_without_uuid_is_fatal(format3, tmp_path):
    """Н6: extract_profiles/clone_profiles are §0 records and need a UUID too."""
    _dangling(format3, tmp_path,
               {"extract_profiles": {"ep": {"rule_nets": ["+3V3"]}}},
               needle="has no uuid")


def test_folder_row_in_an_included_file_is_merged(format3, tmp_path):
    """Н3/В39: a folder row may stand in an included file (not only the root)."""
    root = tmp_path / "root.sexp"
    sub = tmp_path / "sub.sexp"
    _write_sexp(sub, {"folders": {"cells": {"Power": D_FOLDER_P}},
                      "cells": {"c": {"layer": "B.Cu", "uuid": D_CELL}}})
    _write_sexp(root, {"include": ["sub.sexp"], "cells": {}})
    cfg, _ = load_config(str(root))
    assert cfg.folders["cells"]["Power"] == D_FOLDER_P


def test_folder_uuid_mismatch_across_files_is_fatal(format3, tmp_path):
    """В39: one folder path in a section has ONE uuid; a mismatch names BOTH files."""
    root = tmp_path / "root.sexp"
    sub = tmp_path / "sub.sexp"
    _write_sexp(sub, {"folders": {"cells": {"Power": D_FOLDER_L}}})
    _write_sexp(root, {"include": ["sub.sexp"],
                       "folders": {"cells": {"Power": D_FOLDER_P}}})
    with pytest.raises(ValidationError) as e:
        load_config(str(root))
    assert "ONE uuid" in str(e.value)
    assert "root.sexp" in str(e.value) and "sub.sexp" in str(e.value)


def test_tree_node_reference_uuid(format3, tmp_path):
    """Н2: a tree node's (ref "E" (uuid "…")) survives and dangles."""
    data = {"cells": {"c": {"layer": "B.Cu", "uuid": D_CELL}},
            "entities": [{"name": "E", "cell": "c", "cell_uuid": D_CELL,
                          "uuid": D_ENTITY}],
            "trees": [{"name": "t", "anchor": {"origin": True},
                       "nodes": [{"ref": "E", "kind": "placement",
                                  "ref_uuid": D_ENTITY, "xy": [0.0, 0.0]}]}]}
    text = dict_to_sexp(data, format_number=3)
    assert f'(uuid "{D_ENTITY}")' in text      # nested in the node's ref
    assert "ref_uuid" not in text              # the JSON sibling key is not s-expr
    assert sexp_to_dict(text) == data
    data["trees"][0]["nodes"][0]["ref_uuid"] = det_uuid("nope")
    _dangling(format3, tmp_path, data)


def _wrap3(body: str) -> str:
    return "(kicadstamp-config\n  (version 3)\n" + body + ")\n"


# ── П1/П2 ──────────────────────────────────────────────────────────────────

def test_duplicate_full_name_within_one_file_is_fatal(format3, tmp_path):
    """П1: same name + different UUIDs in ONE file — still a duplicate (Р43)."""
    data = {"cells": {"c": {"layer": "B.Cu", "uuid": D_CELL}},
            "clone_placements": [
                {"cluster": "K1", "cell": "c", "cell_uuid": D_CELL,
                 "xy": [0.0, 0.0], "name": "same", "uuid": D_ENTITY},
                {"cluster": "K2", "cell": "c", "cell_uuid": D_CELL,
                 "xy": [1.0, 0.0], "name": "same", "uuid": D_FOLDER_P}]}
    p = tmp_path / "config.sexp"
    _write_sexp(p, data)
    with pytest.raises(ValidationError) as e:
        load_config(str(p))
    assert "duplicate full name" in str(e.value)


def test_unnamed_record_is_fatal_not_a_phantom_duplicate(format3, tmp_path):
    """П2: two unnamed chains in two files -> "no name", not "duplicate name"."""
    root = tmp_path / "root.sexp"
    sub = tmp_path / "sub.sexp"
    _write_sexp(sub, {"chains": [{"net": "GND", "anchor_ref": "IC2", "spokes": []}]})
    _write_sexp(root, {"include": ["sub.sexp"],
                       "chains": [{"net": "GND", "anchor_ref": "IC1", "spokes": []}]})
    with pytest.raises(ValidationError) as e:
        load_config(str(root))
    assert "without a name" in str(e.value)
    assert "duplicate full name" not in str(e.value)


# ── П3: one cell per mutation that survived the second acceptance ──────────

def test_folder_uuid_equal_to_a_record_uuid_is_fatal(format3, tmp_path):
    """R2f: a folder UUID colliding with a record UUID."""
    _dangling(format3, tmp_path,
               {"cells": {"c": {"layer": "B.Cu", "uuid": D_CELL}},
                "folders": {"cells": {"Power": D_CELL}}},
               needle="duplicate uuid")


def test_points_to_points_dangling_is_fatal(format3, tmp_path):
    """R7: points -> points anchor_point_uuid is checked for dangling."""
    _dangling(format3, tmp_path,
               {"points": {"a": {"anchor_ref": "IC1", "uuid": D_POINT},
                           "b": {"anchor_point": "a",
                                 "anchor_point_uuid": det_uuid("nope"),
                                 "uuid": D_ENTITY}}})
    ok = tmp_path / "ok.sexp"
    _write_sexp(ok, {"points": {"a": {"anchor_ref": "IC1", "uuid": D_POINT},
                                "b": {"anchor_point": "a",
                                      "anchor_point_uuid": D_POINT,
                                      "uuid": D_ENTITY}}})
    load_config(str(ok))                      # the control: a valid chain loads


def test_nested_tree_node_reference_uuid(format3, tmp_path):
    """R9: a CHILD node's ref_uuid is walked, not only the top-level node."""
    data = {"cells": {"c": {"layer": "B.Cu", "uuid": D_CELL}},
            "entities": [{"name": "E", "cell": "c", "cell_uuid": D_CELL,
                          "uuid": D_ENTITY},
                         {"name": "E2", "cell": "c", "cell_uuid": D_CELL,
                          "uuid": D_FOLDER_P}],
            "trees": [{"name": "t", "anchor": {"origin": True},
                       "nodes": [{"ref": "E", "kind": "placement",
                                  "ref_uuid": D_ENTITY, "xy": [0.0, 0.0],
                                  "children": [{"ref": "E2", "kind": "placement",
                                                "ref_uuid": D_FOLDER_P,
                                                "xy": [1.0, 0.0]}]}]}]}
    assert sexp_to_dict(dict_to_sexp(data, format_number=3)) == data
    data["trees"][0]["nodes"][0]["children"][0]["ref_uuid"] = det_uuid("nope")
    _dangling(format3, tmp_path, data)


def test_dangling_ref_in_an_included_file_names_that_file(format3, tmp_path):
    """F3: the fatal place is the INCLUDED file, not the graph root (Н7)."""
    root = tmp_path / "root.sexp"
    sub = tmp_path / "sub.sexp"
    _write_sexp(sub, {"entities": [{"name": "E", "cell": "c",
                                    "cell_uuid": det_uuid("nope"),
                                    "uuid": D_ENTITY}]})
    _write_sexp(root, {"include": ["sub.sexp"],
                       "cells": {"c": {"layer": "B.Cu", "uuid": D_CELL}}})
    with pytest.raises(ValidationError) as e:
        load_config(str(root))
    assert "sub.sexp" in str(e.value)
    assert "root.sexp" not in str(e.value)


def test_a_node_ref_may_only_carry_a_uuid_child(format3):
    """T2: an extra child on (ref …) is a fatal, never a silent drop."""
    text = _wrap3('  (trees\n    (tree\n      (name "t")\n      (anchor (origin))\n'
                  '      (node (ref "E" (bogus "x")) (kind placement) (xy 0.0 0.0))\n'
                  '    )\n  )\n')
    with pytest.raises(ValidationError) as e:
        sexp_to_dict(text)
    assert "may follow a node's ref" in str(e.value)


def test_a_local_kind_node_may_not_carry_a_uuid(format3):
    """T3: module/mount/copper/component reference no record — uuid is fatal."""
    text = _wrap3('  (trees\n    (tree\n      (name "t")\n      (anchor (origin))\n'
                  '      (node (ref "m" (uuid "00000000-0000-0000-0000-000000000000"))'
                  ' (kind mount) (anchor (role "R")))\n'
                  '    )\n  )\n')
    with pytest.raises(ValidationError) as e:
        sexp_to_dict(text)
    assert "carries no uuid" in str(e.value)


# ── У2.0: the diamond include, walked ONCE by the format-3 record check ─────

def test_diamond_include_loads_without_a_phantom_duplicate(format3, tmp_path):
    """У2.0 (the tail of У1; kills mutation D1 in loader._f3_files).

    r -> a, b -> common, and the SHARED file carries a record.
    walk_include_tree does NOT dedupe a diamond on purpose, so without
    _f3_files' own by-path dedup the common file contributes its record twice
    and the duplicate-name / duplicate-UUID check false-fatals on a healthy
    graph. A diamond is legitimate reuse (includes.py), not a contradiction."""
    root = tmp_path / "r.sexp"
    a = tmp_path / "a.sexp"
    b = tmp_path / "b.sexp"
    common = tmp_path / "common.sexp"
    _write_sexp(common, {"entities": [{"name": "E", "cell": "c",
                                       "cell_uuid": D_CELL, "uuid": D_ENTITY}]})
    _write_sexp(a, {"include": ["common.sexp"]})
    _write_sexp(b, {"include": ["common.sexp"]})
    _write_sexp(root, {"include": ["a.sexp", "b.sexp"],
                       "cells": {"c": {"layer": "B.Cu", "uuid": D_CELL}}})
    cfg, _ = load_config(str(root))
    assert [e.name for e in cfg.entities] == ["E"]
