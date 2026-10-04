# tests/config/test_uuid_derivation.py
"""У5.2 (plan §6, Р-У5.3/Р-У5.4): the COMPUTED UUIDs the template expansions
hand to their generated copies, plus the post-expansion uniqueness check.

Rule 35 table (property x branch):

| # | property | tree_instances | sheet_templates |
|---|---|---|---|
| 1 | copy uuid is deterministic | direct + across two loads | direct |
| 2 | copies of DIFFERENT instances/sheets differ | two declarations | two sheets |
| 3 | copy uuid != original/template uuid | yes | yes |
| 4 | node `ref_uuid` leads to the GENERATED record | placement + net_trace | n/a (no nodes) |
| 5 | renaming the declaration changes the copy uuid | yes | sheet rename -> changes |
| 6 | format < 3: no derivation (byte-identical product) | copy keeps original uuid | no uuid added |
| 7 | post-expansion duplicate uuid -> fatal with BOTH sources | yes (loader) | — |

Cells 1-5 and 7 pin the build to format 3 with the `format3` fixture; cell 6
runs WITHOUT it (the product's own format 2). The product path is unchanged.
"""
import copy

import pytest

from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config.sheet_templates import expand_sheet_templates
from kicadstamp.config.tree_instances import expand_tree_instances
from kicadstamp.config.uuids import NS_DERIVED, NS_MIGRATION, derived_uuid
from kicadstamp.exceptions import ValidationError
from tests.fakes.format3 import format3  # noqa: F401  (fixture)

ENTITY_UUID = "11111111-1111-4111-8111-111111111111"
NT_UUID = "22222222-2222-4222-8222-222222222222"
TPL_UUID = "33333333-3333-4333-8333-333333333333"
CELL_UUID = "44444444-4444-4444-8444-444444444444"


def _entity(name, uuid=ENTITY_UUID):
    return {"name": name, "cell": "c", "cell_uuid": CELL_UUID, "uuid": uuid}


def _named_nt(name, uuid=NT_UUID):
    return {"name": name, "net": "/S0/G/N", "anchor_sheet": "S0",
            "anchor_role": "R", "uuid": uuid, "tracks": [], "vias": []}


def _placement_template(nodes=None):
    return {"name": "tpl", "anchor": {"role": "R", "sheet": "S0"},
            "nodes": nodes or [{"ref": "E", "kind": "placement",
                                "ref_uuid": ENTITY_UUID, "xy": [0.0, 0.0]}]}


def _ti_data(instances, nodes=None, entities=None, net_traces=None):
    data = {
        "cells": {"c": {"layer": "B.Cu", "uuid": CELL_UUID}},
        "entities": entities if entities is not None else [_entity("E")],
        "trees": [_placement_template(nodes)],
        "tree_instances": instances,
    }
    if net_traces is not None:
        data["net_traces"] = net_traces
    return data


def _tree(out, name):
    return next(t for t in out["trees"] if t["name"] == name)


def _name(out, name):
    return next(e for e in out["entities"] if e["name"] == name)


# ── 0. the two namespaces must differ (mutation row "NS_DERIVED = NS") ──────

def test_derived_namespace_differs_from_the_migration_seed():
    """NS_DERIVED is deliberately its OWN fixed namespace (Р-У5.3): a generated
    copy must not share the migration's seed space, so a collision with a lifted
    record is impossible by construction. The mutation "NS_DERIVED = NS_MIGRATION"
    must die HERE."""
    assert NS_DERIVED != NS_MIGRATION


def test_derived_uuid_is_deterministic():
    assert derived_uuid("seed") == derived_uuid("seed")
    assert derived_uuid("seed") != derived_uuid("other")


# ── 1/2/3/5. tree_instances copies ──────────────────────────────────────────

def test_tree_instance_entity_copy_uuid_is_derived_and_distinct(format3):
    out = expand_tree_instances(_ti_data([
        {"template": "tpl", "name": "a", "sheet": "S1"},
        {"template": "tpl", "name": "b", "sheet": "S2"},
    ]))
    a, b = _name(out, "E__a"), _name(out, "E__b")
    assert a["uuid"] == derived_uuid(f"{ENTITY_UUID}|tree_instance:a")
    assert b["uuid"] == derived_uuid(f"{ENTITY_UUID}|tree_instance:b")
    assert a["uuid"] != b["uuid"]              # different instances differ
    assert a["uuid"] != ENTITY_UUID            # not the original's uuid
    assert b["uuid"] != ENTITY_UUID
    assert _name(out, "E")["uuid"] == ENTITY_UUID   # original keeps its uuid


def test_tree_instance_entity_copy_uuid_is_deterministic(format3):
    rows = [{"template": "tpl", "name": "a", "sheet": "S1"}]
    one = expand_tree_instances(_ti_data(copy.deepcopy(rows)))
    two = expand_tree_instances(_ti_data(copy.deepcopy(rows)))
    assert _name(one, "E__a")["uuid"] == _name(two, "E__a")["uuid"]


def test_tree_instance_node_ref_uuid_leads_to_the_generated_entity(format3):
    """Р-У5.4: the generated node references the COPY, not the template Entity."""
    out = expand_tree_instances(_ti_data(
        [{"template": "tpl", "name": "a", "sheet": "S1"}]))
    copy_uuid = _name(out, "E__a")["uuid"]
    node = _tree(out, "a")["nodes"][0]
    assert node["ref"] == "E__a"
    assert node["ref_uuid"] == copy_uuid
    assert node["ref_uuid"] != ENTITY_UUID


def test_renaming_a_tree_instance_changes_the_copy_uuid(format3):
    before = expand_tree_instances(_ti_data(
        [{"template": "tpl", "name": "a", "sheet": "S1"}]))
    after = expand_tree_instances(_ti_data(
        [{"template": "tpl", "name": "renamed", "sheet": "S1"}]))
    assert (_name(before, "E__a")["uuid"]
            != _name(after, "E__renamed")["uuid"])


def test_tree_instance_net_trace_copy_uuid_and_node_ref(format3):
    data = _ti_data(
        [{"template": "tpl", "name": "a", "sheet": "S1"}],
        nodes=[{"ref": "nt", "kind": "net_trace", "ref_uuid": NT_UUID,
                "xy": [1.0, 0.0]}],
        entities=[_entity("E")],
        net_traces=[_named_nt("nt")])
    out = expand_tree_instances(data)
    copy = next(nt for nt in out["net_traces"] if nt["name"] == "nt__a")
    assert copy["uuid"] == derived_uuid(f"{NT_UUID}|tree_instance:a")
    node = _tree(out, "a")["nodes"][0]
    assert node["ref_uuid"] == copy["uuid"]


# ── 6. format < 3: the product path is untouched ────────────────────────────

def test_tree_instance_copy_keeps_the_original_uuid_without_the_gate():
    # A format-2 template node carries no ref_uuid at all (trees get no UUIDs in
    # this step), so the no-gate branch is exercised on a node WITHOUT one.
    data = _ti_data([{"template": "tpl", "name": "a", "sheet": "S1"}],
                    nodes=[{"ref": "E", "kind": "placement", "xy": [0.0, 0.0]}])
    out = expand_tree_instances(data)
    # format 2 (no fixture): the deep copy carries the template Entity's uuid.
    assert _name(out, "E__a")["uuid"] == ENTITY_UUID
    assert "ref_uuid" not in _tree(out, "a")["nodes"][0]


# ── sheet_templates copies ──────────────────────────────────────────────────

def _st_data(sheets, uuid=TPL_UUID, cluster="K"):
    return {"sheet_templates": {"tpl": {
        "uuid": uuid,
        "sheets": sheets,
        "clone_placements": [{"cluster": cluster, "cell": "c",
                              "cell_uuid": CELL_UUID, "xy": [0.0, 0.0]}]}}}


def test_sheet_template_copy_uuid_is_derived_and_per_sheet(format3):
    out = expand_sheet_templates(_st_data(["S1", "S2"]))
    got = [cp["uuid"] for cp in out["clone_placements"]]
    assert got == [derived_uuid(f"{TPL_UUID}|clone_placements|K|sheet:S1"),
                   derived_uuid(f"{TPL_UUID}|clone_placements|K|sheet:S2")]
    assert got[0] != got[1]
    assert TPL_UUID not in got
    assert out["clone_placements"][0]["name"] == "S1_K"
    assert out["clone_placements"][0]["cell_uuid"] == CELL_UUID  # shared target kept


def test_sheet_template_single_sheet_also_gets_a_uuid(format3):
    out = expand_sheet_templates(_st_data(["Only"]))
    assert out["clone_placements"][0]["uuid"] == derived_uuid(
        f"{TPL_UUID}|clone_placements|K|sheet:Only")


def test_sheet_template_uuid_is_deterministic(format3):
    one = expand_sheet_templates(_st_data(["S1"]))
    two = expand_sheet_templates(_st_data(["S1"]))
    assert one["clone_placements"][0]["uuid"] == two["clone_placements"][0]["uuid"]


def test_sheet_template_copy_has_no_uuid_without_the_gate():
    out = expand_sheet_templates(_st_data(["S1", "S2"]))
    assert all("uuid" not in cp for cp in out["clone_placements"])


# ── 4/1 across LOADS (the real loader, format 3) ────────────────────────────

def _write_graph(root, data):
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text(dict_to_sexp(data, format_number=3), encoding="utf-8")


def _format3_graph():
    return {
        "cells": {"c": {"layer": "B.Cu", "uuid": CELL_UUID}},
        "entities": [_entity("E")],
        "trees": [_placement_template()],
        "tree_instances": [{"template": "tpl", "name": "inst1", "sheet": "S1"}],
    }


def test_loader_derives_and_persists_ref_uuid_to_the_copy(tmp_path, format3):
    a = tmp_path / "a" / "root.sexp"
    b = tmp_path / "b" / "root.sexp"
    _write_graph(a, _format3_graph())
    _write_graph(b, _format3_graph())
    cfg_a, _ = load_config(str(a))
    cfg_b, _ = load_config(str(b))
    copy_a = next(e for e in cfg_a.entities if e.name == "E__inst1")
    copy_b = next(e for e in cfg_b.entities if e.name == "E__inst1")
    assert copy_a.uuid == copy_b.uuid                       # deterministic loads
    assert copy_a.uuid == derived_uuid(f"{ENTITY_UUID}|tree_instance:inst1")
    tree = next(t for t in cfg_a.trees if t.name == "inst1")
    assert tree.nodes[0].ref_uuid == copy_a.uuid            # Р-У5.4
    assert copy_a.uuid != ENTITY_UUID


# ── 7. post-expansion uniqueness check ──────────────────────────────────────

def test_duplicate_uuid_after_expansion_is_a_fatal_naming_both(tmp_path, format3):
    """A copy's derived uuid colliding with an existing record must fatal with
    BOTH sources. The raw-file check cannot see the copy (it runs first), so
    this is exactly the check У5.2 adds."""
    derived = derived_uuid(f"{ENTITY_UUID}|tree_instance:inst1")
    data = {
        "cells": {"c": {"layer": "B.Cu", "uuid": CELL_UUID}},
        "entities": [_entity("E"), _entity("DUP", uuid=derived)],
        "trees": [_placement_template()],
        "tree_instances": [{"template": "tpl", "name": "inst1", "sheet": "S1"}],
    }
    root = tmp_path / "root.sexp"
    _write_graph(root, data)
    with pytest.raises(ValidationError) as exc:
        load_config(str(root))
    text = str(exc.value)
    assert "E__inst1" in text and "DUP" in text
    assert derived in text
