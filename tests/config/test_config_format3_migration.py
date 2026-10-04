# tests/config/test_config_format3_migration.py
"""У3.1 — the 2 -> 3 converter step `_step_2_to_3` and the ONE Р-1 seed builder.

The step is a PURE function of ONE file (Р-1 reopened 04.10): the seed carries
NO file path, so a reference to a record living in ANOTHER file is computed from
the reference's own name hint. That is the property the whole table below rests
on, and the reason the converter can run inside every single-file reader.

Rule 35 table — every axis is a parametrized row, so a new section or a new
reference form cannot enter the product unnoticed and a red row never hides its
neighbours:

| axis | rows | what the row checks |
|---|---|---|
| §0 record | every `_F3_RECORD_SECTIONS` entry (dict / free / list) | the record's UUID = `migration_uuid(section, full_name)` |
| reference form | every `_F3_REF_TARGET` field AND every `_F3_NODE_KIND_TARGET` kind | the reference's UUID = `migration_uuid(target, hint)` |
| §0 record, seed | `_SECTION_SINGULAR` == `_F3_LIST_SECTIONS` | a list section always has a singular to mint a name from |
| В36 | unnamed / two unnamed / name already taken | `entity_001`, `entity_002`, `..._2` + a WARNING with file and place |
| Р-У3.3 | a tree node with a ref but no kind | `refuse_step`, NAMING the node and the file |
| idempotence | a record / a reference already carrying a UUID | untouched; a second on-disk lift writes 0 bytes |
| file path | the same file lifted in TWO directories; a ref to another file | identical UUIDs; the graph loads after both are lifted |
| В39 folders | a path prefix of a name; one path in two files | `migration_folder_uuid(section, path)`; ONE UUID per path |
| JSON | a format-2 `.json` file | lifted too — the dict is format-agnostic |
| gate | the product at `CURRENT_FORMAT = 2` | no step runs; `STEPS[2]` IS the step |

Only the cells that exercise a PRODUCT path (`upgrade_graph_on_disk`,
`load_config`) use the `format3` fixture; the step's own cells call it directly,
because the step itself is format-agnostic. The constant is NOT pinned back to 2
anywhere — that would move the whole module onto the format-2 path (plan §7,
У3.0.3).
"""
import copy
import json
import logging

import pytest

from kicadstamp.config import format_version as fv
from kicadstamp.config.format3 import (
    _F3_LIST_SECTIONS,
    _F3_NODE_KIND_TARGET,
    _F3_RECORD_SECTIONS,
    _F3_REF_TARGET,
    _f3_refs,
)
from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config.uuids import NS_MIGRATION, migration_folder_uuid, migration_uuid
from kicadstamp.config.upgrade_on_disk import upgrade_graph_on_disk
from kicadstamp.exceptions import ValidationError
from tests.fakes.format3 import format3  # noqa: F401 (fixture import)


def _ctx(path="profiles/p/r.sexp"):
    return fv.UpgradeContext(path=path, at_parse_time=True)


def _step(data, path="profiles/p/r.sexp"):
    return fv._step_2_to_3(data, _ctx(path))


def _write_sexp(path, data, number=2) -> None:
    path.write_text(dict_to_sexp(data, format_number=number), encoding="utf-8")


# ── the Р-1 seed is FIXED forever: pinned as LITERALS, not against itself ──

def test_the_migration_seed_is_pinned_by_literal_values():
    """The seed must NEVER drift: a profile lifted by one build and a reference
    lifted by another would otherwise disagree, and the reference would dangle.

    Every other cell of this module compares a PRODUCT call with ANOTHER product
    call (`migration_uuid(...) == migration_uuid(...)`), so a change to the
    separator, the `folder:` marker or the namespace would keep them all green.
    These three values are the identity of LIVE profiles — DO NOT CHANGE THEM
    (the acceptance rows Y11/Y12/Y13 exist to keep this cell honest)."""
    assert str(NS_MIGRATION) == "8f0c1e6a-9b3d-4a72-8c5e-1d4f7a2b9e30"
    assert migration_uuid("cells", "cap") == "49bf967e-9e3e-50e5-ba7f-99aa390b1822"
    assert (migration_folder_uuid("cells", "Power")
            == "4fd95765-d295-572a-8f90-0077ebf2146a")


# ── the test stub mints on the SAME product seed (Р-У3.4, У3.2) ────────────

def test_the_format3_stub_seed_is_the_product_seed():
    """The stub of tests/fakes/format3.py must mint in the PRODUCT namespace
    (У3.2 / Р-У3.4), not in a private one: a fixture-built format-3 graph and
    the real converter have to compute the SAME uuid for the same record, or a
    comparison of the two would compare different identities. The product never
    imports the stub (that stays true)."""
    from tests.fakes.format3 import det_uuid, mint_format3

    assert det_uuid("cells:cap") == migration_uuid("cells", "cap")
    assert det_uuid("entities:E") == migration_uuid("entities", "E")

    minted = mint_format3({"cells": {"Power/cap": {}}})
    assert minted["cells"]["Power/cap"]["uuid"] == migration_uuid(
        "cells", "Power/cap")
    assert (minted["folders"]["cells"]["Power"]
            == migration_folder_uuid("cells", "Power"))


# ── §0 records: one row per product section ───────────────────────────────

def _record_data(section):
    """A minimal format-2 dict carrying ONE record of `section` named 'n'."""
    if section in _F3_LIST_SECTIONS:
        return {section: [{"name": "n"}]}
    return {section: {"n": {}}}


def _record_uuid(data, section):
    if section in _F3_LIST_SECTIONS:
        return data[section][0].get("uuid")
    return data[section]["n"].get("uuid")


@pytest.mark.parametrize("section", sorted(_F3_RECORD_SECTIONS))
def test_every_record_section_gets_the_migration_seed(section):
    out = _step(_record_data(section))
    assert _record_uuid(out, section) == migration_uuid(section, "n")


def test_a_record_that_already_has_a_uuid_is_never_touched():
    data = {"cells": {"c": {"uuid": "cell-uuid"}},
            "entities": [{"name": "E", "cell": "WRONG", "cell_uuid": "cell-uuid",
                          "uuid": "ent-uuid"}]}
    out = _step(data)
    assert out["cells"]["c"]["uuid"] == "cell-uuid"
    assert out["entities"][0]["uuid"] == "ent-uuid"
    assert out["entities"][0]["cell_uuid"] == "cell-uuid"
    # the step never rewrites a hint — that is the loader's normalization (У2)
    assert out["entities"][0]["cell"] == "WRONG"


# ── reference forms: one builder per shape `_f3_refs` knows ────────────────

def _cell(name="c"):
    return {name: {"layer": "B.Cu"}}


_FORMS = {
    "entity_cell":
        lambda: {"cells": _cell(),
                 "entities": [{"name": "E", "cell": "c"}]},
    "entity_imprint":
        lambda: {"imprints": [{"name": "imp"}],
                 "entities": [{"name": "E", "imprint": "imp"}]},
    "coordinate_anchor_point":
        lambda: {"points": {"P": {}},
                 "coordinate_placements": [{"name": "C", "anchor_point": "P"}]},
    "chain_spoke_cell":
        lambda: {"cells": _cell(),
                 "chains": [{"name": "ch", "spokes": [{"pad": "1", "cell": "c"}]}]},
    "point_anchor_point":
        lambda: {"points": {"P": {"anchor_point": "Q"}, "Q": {}}},
    "cell_nested_clone_cell":
        lambda: {"cells": {"c": {"clone_placements": [{"cell": "d"}]}, "d": {}}},
    "sheet_clone_cell":
        lambda: {"cells": _cell(),
                 "sheet_templates": {"t": {"clone_placements": [{"cell": "c"}]}}},
    "sheet_clone_anchor_point":
        lambda: {"points": {"P": {}},
                 "sheet_templates": {"t": {"clone_placements": [{"anchor_point": "P"}]}}},
    "sheet_coordinate_anchor_point":
        lambda: {"points": {"P": {}},
                 "sheet_templates": {"t": {"coordinate_placements": [{"anchor_point": "P"}]}}},
    "tree_instance_anchor_point":
        lambda: {"points": {"P": {}},
                 "tree_instances": [{"name": "I", "anchor": {"point": "P"}}]},
    "tree_anchor_point":
        lambda: {"points": {"P": {}},
                 "trees": [{"name": "T", "anchor": {"point": "P"}}]},
    "node_placement":
        lambda: {"entities": [{"name": "E", "cell": "c"}], "cells": _cell(),
                 "trees": [{"name": "T", "nodes": [{"kind": "placement", "ref": "E"}]}]},
    "node_clone":
        lambda: {"clone_placements": [{"name": "C"}],
                 "trees": [{"name": "T", "nodes": [{"kind": "clone", "ref": "C"}]}]},
    "node_chain":
        lambda: {"chains": [{"name": "CH"}],
                 "trees": [{"name": "T", "nodes": [{"kind": "chain", "ref": "CH"}]}]},
    "node_rule":
        lambda: {"chains": [{"name": "CH"}],
                 "trees": [{"name": "T", "nodes": [{"kind": "rule", "ref": "CH"}]}]},
    "node_coordinate":
        lambda: {"coordinate_placements": [{"name": "C"}],
                 "trees": [{"name": "T", "nodes": [{"kind": "coordinate", "ref": "C"}]}]},
    "node_net_trace":
        lambda: {"net_traces": [{"name": "NT"}],
                 "trees": [{"name": "T", "nodes": [{"kind": "net_trace", "ref": "NT"}]}]},
    "node_point":
        lambda: {"points": {"P": {}},
                 "trees": [{"name": "T", "nodes": [{"kind": "point", "ref": "P"}]}]},
}

# product (anchor point of the "new form reds" guard) -> the _FORMS row that
# exercises it. Exact equality with the product table is asserted below, so a
# new field or a new node kind turns THIS test red until a cell is added.
_REF_FIELD_FORMS = {
    "cell": "entity_cell",
    "imprint": "entity_imprint",
    "anchor_point": "coordinate_anchor_point",
}
_NODE_KIND_FORMS = {
    "placement": "node_placement",
    "clone": "node_clone",
    "chain": "node_chain",
    "rule": "node_rule",
    "coordinate": "node_coordinate",
    "net_trace": "node_net_trace",
    "point": "node_point",
}


@pytest.mark.parametrize("form", sorted(_FORMS))
def test_every_reference_form_gets_the_target_seed(form):
    out = _step(_FORMS[form]())
    refs = list(_f3_refs(out))
    assert refs, "the form's data must contain at least one reference"
    for ref in refs:
        hint = ref.holder[ref.name_field]
        assert ref.uuid == migration_uuid(ref.target, hint), ref.label


def test_the_reference_form_cells_are_exactly_the_product_tables():
    assert set(_REF_FIELD_FORMS) == set(_F3_REF_TARGET)
    assert set(_NODE_KIND_FORMS) == set(_F3_NODE_KIND_TARGET)


@pytest.mark.parametrize("field", sorted(_F3_REF_TARGET))
def test_every_reference_field_has_a_form_cell(field):
    assert _REF_FIELD_FORMS[field] in _FORMS


@pytest.mark.parametrize("kind", sorted(_F3_NODE_KIND_TARGET))
def test_every_tree_node_kind_has_a_form_cell(kind):
    assert _NODE_KIND_FORMS[kind] in _FORMS


def test_singulars_cover_every_list_section():
    assert set(fv._SECTION_SINGULAR) == set(_F3_LIST_SECTIONS)


# ── В36: an unnamed record is named, loudly ───────────────────────────────

def test_an_unnamed_record_is_minted_and_warned(caplog, tmp_path):
    caplog.set_level(logging.WARNING, logger="kicadstamp.config.format_version")
    path = str(tmp_path / "r.sexp")
    out = _step({"entities": [{"cell": "c"}]}, path=path)
    rec = out["entities"][0]
    assert rec["name"] == "entity_001"
    assert rec["uuid"] == migration_uuid("entities", "entity_001")
    assert "entity_001" in caplog.text
    assert path in caplog.text           # the WARNING names the file


def test_two_unnamed_records_are_numbered_001_and_002():
    out = _step({"entities": [{"cell": "c"}, {"cell": "d"}]})
    assert [r["name"] for r in out["entities"]] == ["entity_001", "entity_002"]
    assert out["entities"][1]["uuid"] == migration_uuid("entities", "entity_002")


def test_a_taken_minted_name_appends_2():
    out = _step({"entities": [{"name": "entity_002"}, {"cell": "d"}]})
    assert [r["name"] for r in out["entities"]] == ["entity_002", "entity_002_2"]
    assert out["entities"][1]["uuid"] == migration_uuid("entities", "entity_002_2")


# ── Р-У3.3: a node without a kind cannot name its target section ──────────

def test_a_tree_node_without_a_kind_is_refused_with_its_place(tmp_path):
    path = str(tmp_path / "r.sexp")
    with pytest.raises(ValidationError) as e:
        _step({"trees": [{"name": "T", "nodes": [{"ref": "E"}]}]}, path=path)
    assert "kind of tree node" in str(e.value)
    assert path in str(e.value)


# ── В39: folder rows ──────────────────────────────────────────────────────

def test_a_folder_path_gets_the_migration_folder_seed():
    out = _step({"cells": {"Power/LDO/out": {"layer": "B.Cu"}}})
    folders = out["folders"]["cells"]
    assert folders["Power"] == migration_folder_uuid("cells", "Power")
    assert folders["Power/LDO"] == migration_folder_uuid("cells", "Power/LDO")
    assert out["cells"]["Power/LDO/out"]["uuid"] == migration_uuid(
        "cells", "Power/LDO/out")


def test_a_name_without_a_slash_adds_no_folder_table():
    out = _step({"cells": {"c": {"layer": "B.Cu"}}})
    assert "folders" not in out


def test_an_existing_folder_row_is_never_overwritten():
    """В39 idempotence one level down: a folder row that already carries a UUID
    keeps it — only MISSING rows are added. A format-2 file has no folder table,
    so this path is nearly unreachable today, but the promise is 'never
    overwritten' and the cell is as cheap as the record one."""
    data = {"cells": {"Power/c": {"layer": "B.Cu"}},
            "folders": {"cells": {"Power": "keep-me"}}}
    out = _step(data)
    assert out["folders"]["cells"]["Power"] == "keep-me"
    assert out["cells"]["Power/c"]["uuid"] == migration_uuid("cells", "Power/c")


# ── the file path lives in NO seed: cross-file and cross-directory ─────────

def test_a_reference_across_files_resolves_after_both_are_lifted(format3, tmp_path):
    """The cell the reopened Р-1 exists for: each file is lifted ALONE, and the
    reference is computed from its own hint — no graph is consulted."""
    sub = tmp_path / "sub.sexp"
    root = tmp_path / "root.sexp"
    _write_sexp(sub, {"cells": {"c": {"layer": "B.Cu"}}})
    _write_sexp(root, {"include": ["sub.sexp"],
                       "entities": [{"name": "E", "cell": "c"}]})

    lifted = {p.name for p in upgrade_graph_on_disk(root)}
    assert lifted == {"root.sexp", "sub.sexp"}

    cfg, _ = load_config(str(root))
    assert cfg.entities[0].cell == "c"
    assert cfg.entities[0].cell_uuid == migration_uuid("cells", "c")
    assert cfg.cells["c"].uuid == migration_uuid("cells", "c")


def test_the_same_file_lifted_in_two_directories_gets_the_same_uuids(format3, tmp_path):
    body = {"cells": {"c": {"layer": "B.Cu"}}}
    a = tmp_path / "a" / "c.sexp"
    b = tmp_path / "b" / "c.sexp"
    a.parent.mkdir()
    b.parent.mkdir()
    uuids = []
    for path in (a, b):
        _write_sexp(path, body)
        upgrade_graph_on_disk(path)
        uuids.append(sexp_to_dict(path.read_text(encoding="utf-8"),
                                  path=str(path))["cells"]["c"]["uuid"])
    assert uuids[0] == uuids[1] == migration_uuid("cells", "c")


def test_one_folder_path_in_two_files_gets_one_uuid(format3, tmp_path):
    sub = tmp_path / "sub.sexp"
    root = tmp_path / "root.sexp"
    _write_sexp(sub, {"cells": {"Power/a": {"layer": "B.Cu"}}})
    _write_sexp(root, {"include": ["sub.sexp"],
                       "cells": {"Power/b": {"layer": "B.Cu"}}})
    upgrade_graph_on_disk(root)

    r = sexp_to_dict(root.read_text(encoding="utf-8"), path=str(root))
    s = sexp_to_dict(sub.read_text(encoding="utf-8"), path=str(sub))
    assert (r["folders"]["cells"]["Power"] == s["folders"]["cells"]["Power"]
            == migration_folder_uuid("cells", "Power"))
    load_config(str(root))  # В39: one UUID per path -> the graph loads


# ── the step is pure: it works on a deep copy of its input ────────────────

def test_the_step_leaves_the_input_dict_untouched():
    """`_step_2_to_3` promises to run on a deep copy. Without it the step would
    mutate the dict the caller handed in — and a file read through
    `cached_file_read` hands out a SHARED object, so the mutation would leak into
    the cache (the К1 class of У2)."""
    data = {"cells": {"Power/c": {"layer": "B.Cu"}},
            "entities": [{"name": "E", "cell": "Power/c"}]}
    before = copy.deepcopy(data)
    _step(data)
    assert data == before


# ── idempotence on disk ────────────────────────────────────────────────────

def test_a_second_lift_writes_nothing(format3, tmp_path):
    p = tmp_path / "c.sexp"
    _write_sexp(p, {"cells": {"c": {"layer": "B.Cu"}}})
    assert len(upgrade_graph_on_disk(p)) == 1
    assert fv.read_version(p) == 3
    first = p.read_bytes()

    assert upgrade_graph_on_disk(p) == []
    assert p.read_bytes() == first


# ── the same step for JSON ─────────────────────────────────────────────────

def test_a_format2_json_file_is_lifted_too(format3, tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"version": 2,
                             "cells": {"c": {"layer": "B.Cu"}}}),
                 encoding="utf-8")
    assert len(upgrade_graph_on_disk(p)) == 1
    assert fv.read_version(p) == 3
    cfg, _ = load_config(str(p))
    assert cfg.cells["c"].uuid == migration_uuid("cells", "c")


# ── the gate: the product stays at format 2 ────────────────────────────────

def test_the_step_is_registered_as_the_2_to_3_converter():
    assert fv.STEPS[2] is fv._step_2_to_3


def test_the_product_lifts_nothing_while_current_format_is_2():
    assert fv.CURRENT_FORMAT == 2
    data = {"entities": [{"name": "E", "cell": "c"}]}
    before = copy.deepcopy(data)
    assert fv.upgrade_data(data, 2) == before
    assert "uuid" not in data["entities"][0]
    assert "cell_uuid" not in data["entities"][0]
