# tests/test_convert_placements.py
"""One-time migration converter (plan §6.2, design §9): legacy
clone_placements: -> Entity + a one-node placement tree. Every former clone
becomes an Entity (NO position) plus a trees: tree whose anchor derives from
the old anchor_* fields (or (origin) when absolute) and whose single node
carries xy/polar + rotation. The old clone list is cleared; every other
section is preserved."""
from pathlib import Path

from kicadstamp.config import entity_effective_name, load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.validation import check_entity_cells_exist

from tools.convert_placements import (
    convert_clone_placements_to_entities, convert_placements_file)


def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def test_absolute_clone_becomes_entity_and_origin_tree():
    out = convert_clone_placements_to_entities({
        "clone_placements": [{"cluster": "PIF_AVDD", "cell": "pi_filter",
                              "xy": [10.0, 20.0], "rotation_deg": 90.0}],
    })
    assert out["clone_placements"] == []
    # cluster is the physical Cluster TAG (1:1 into Entity.cluster); name
    # falls back to it (no explicit name on the old clone).
    assert out["entities"] == [{"name": "PIF_AVDD", "cell": "pi_filter",
                                "cluster": "PIF_AVDD"}]
    assert out["trees"] == [{
        "name": "PIF_AVDD", "anchor": {"origin": True},
        "nodes": [{"ref": "PIF_AVDD", "kind": "placement",
                   "xy": [10.0, 20.0], "rotation": 90.0}],
    }]


def test_entity_never_carries_a_position():
    out = convert_clone_placements_to_entities({
        "clone_placements": [{"name": "E1", "cell": "c",
                              "xy": [10.0, 20.0], "rotation_deg": 90.0,
                              "radius_mm": 1.0, "angle_deg": 2.0,
                              "anchor_ref": "R1", "anchor_role": "R",
                              "anchor_point": "P", "anchor_sheet": "S",
                              "anchor_pad": "1", "anchor_cluster": "C"}],
    })
    entity = out["entities"][0]
    for key in ("xy", "radius_mm", "angle_deg", "rotation_deg", "anchor_ref",
                "anchor_role", "anchor_point", "anchor_sheet", "anchor_pad",
                "anchor_cluster"):
        assert key not in entity


def test_anchor_ref_clone_shift_and_rotation():
    out = convert_clone_placements_to_entities({
        "clone_placements": [{"name": "E1", "cell": "c", "xy": [1.0, 2.0],
                              "anchor_ref": "CONN", "rotation_deg": 45.0}],
    })
    tree = out["trees"][0]
    assert tree["anchor"] == {"ref": "CONN"}
    assert tree["nodes"][0]["xy"] == [1.0, 2.0]
    assert tree["nodes"][0]["rotation"] == 45.0


def test_polar_absolute():
    out = convert_clone_placements_to_entities({
        "clone_placements": [{"cluster": "E1", "cell": "c",
                              "radius_mm": 3.0, "angle_deg": 45.0}],
    })
    tree = out["trees"][0]
    assert tree["anchor"] == {"origin": True}
    assert tree["nodes"][0]["polar"] == [3.0, 45.0]


def test_role_anchor_with_narrowing_fields():
    out = convert_clone_placements_to_entities({
        "clone_placements": [{"cluster": "E1", "cell": "c", "xy": [0.5, -0.5],
                              "anchor_role": "FPGA", "anchor_sheet": "Channel_0",
                              "anchor_cluster": "CH0", "anchor_pad": "A1"}],
    })
    tree = out["trees"][0]
    assert tree["anchor"] == {"role": "FPGA", "sheet": "Channel_0",
                              "cluster": "CH0", "pad": "A1"}
    assert tree["nodes"][0]["xy"] == [0.5, -0.5]


def test_point_anchor():
    out = convert_clone_placements_to_entities({
        "clone_placements": [{"cluster": "E1", "cell": "c", "xy": [0.0, 0.0],
                              "anchor_point": "P1"}],
    })
    assert out["trees"][0]["anchor"] == {"point": "P1"}


def test_electrical_fields_and_flags_copied_to_entity():
    out = convert_clone_placements_to_entities({
        "clone_placements": [{"name": "E1", "cell": "c", "xy": [1.0, 1.0],
                              "nets": {"C_IN": "+3V3"}, "params": {"PWR_IN": "+3V3"},
                              "net_overrides": {"+3V3": "+3V3_DIRTY"},
                              "refs": {"C_IN": "C5"}, "cluster": "CH0",
                              "sheet": "Channel_0", "layer": "B.Cu", "mirror": True,
                              "comment": "note", "retired": True, "skip": True,
                              "ignore_selection": True, "by_selection": True}],
    })
    entity = out["entities"][0]
    assert entity["nets"] == {"C_IN": "+3V3"}
    assert entity["net_overrides"] == {"+3V3": "+3V3_DIRTY"}
    assert entity["refs"] == {"C_IN": "C5"}
    assert entity["cluster"] == "CH0"
    assert entity["sheet"] == "Channel_0"
    assert entity["layer"] == "B.Cu" and entity["mirror"] is True
    assert entity["comment"] == "note"
    assert entity["retired"] is True and entity["skip"] is True
    assert entity["ignore_selection"] is True and entity["by_selection"] is True


def test_preserves_other_sections():
    out = convert_clone_placements_to_entities({
        "cells": {"c": {}}, "points": {"P1": {}},
        "rules": [{"net": "N"}],
        "clone_placements": [{"cluster": "E1", "cell": "c", "xy": [0.0, 0.0]}],
    })
    assert out["cells"] == {"c": {}}
    assert out["points"] == {"P1": {}}
    assert out["rules"] == [{"net": "N"}]


def test_round_trip_file_loads_and_passes_entity_cell_check(tmp_path):
    """Plan §6.2's gate: the converted file must LOAD (load_config), pass the
    Entity-cell check, AND link_trees (the step Apply/Redraw actually runs —
    a load-only check missed the "clone"->"placement" rewrite gap)."""
    path = tmp_path / "root.sexp"
    _write(path, {
        "cells": {"pi_filter": {"components": [], "vias": [], "tracks": [],
                                "layer": "F.Cu"}},
        "clone_placements": [{"cluster": "PIF_AVDD", "cell": "pi_filter",
                              "xy": [10.0, 20.0]}],
        "trees": [{"name": "pre", "anchor": {"origin": True},
                   "nodes": [{"ref": "PIF_AVDD", "kind": "clone", "xy": [0.0, 0.0]}]}],
    })
    convert_placements_file(path)
    cfg, _ctx = load_config(str(path))
    assert [entity_effective_name(e) for e in cfg.entities] == ["PIF_AVDD"]
    assert cfg.trees[0].anchor.is_origin
    assert cfg.clone_placements == []
    check_entity_cells_exist(cfg)
    # the real Apply/Redraw gate: link_trees must resolve cleanly (the
    # pre-existing "clone" node was rewritten to "placement").
    from kicadstamp.link_trees import link_trees
    linked = link_trees(cfg, cfg.trees)
    assert linked, "link_trees must resolve the converted trees"
    assert linked[0].nodes[0].node.kind == "placement"


def test_convert_placements_file_creates_a_timestamped_backup(tmp_path):
    """A real conversion rewrites the input — the original must survive as a
    timestamped .bak next to it."""
    path = tmp_path / "root.sexp"
    _write(path, {
        "clone_placements": [{"cluster": "E1", "cell": "c", "xy": [0.0, 0.0]}],
    })
    original = path.read_text(encoding="utf-8")
    convert_placements_file(path)
    backups = list(tmp_path.glob("root.sexp.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    assert sexp_to_dict(path.read_text(encoding="utf-8"))["clone_placements"] == []


def test_second_run_is_idempotent():
    data = {"clone_placements": [{"cluster": "E1", "cell": "c", "xy": [0.0, 0.0]}]}
    once = convert_clone_placements_to_entities(data)
    twice = convert_clone_placements_to_entities(once)
    assert twice["entities"] == once["entities"]
    assert twice["trees"] == once["trees"]
    assert sexp_to_dict(dict_to_sexp(twice)) == sexp_to_dict(dict_to_sexp(once))


def test_partially_converted_profile_no_duplicate_refs():
    """A transitional profile that already has SOME trees: nodes (kind "clone",
    as every pre-migration node is) must not get duplicate node refs (the
    link_trees "one node per ref" invariant): an Entity whose name already
    exists is skipped, a tree is only created for a ref that is not already
    placed, and the pre-existing "clone" node is rewritten to "placement"."""
    out = convert_clone_placements_to_entities({
        "entities": [{"name": "ALREADY", "cell": "c"}],
        "trees": [{"name": "old", "anchor": {"origin": True},
                   "nodes": [{"ref": "PLACED", "kind": "clone",
                              "xy": [1.0, 1.0]}]}],
        "clone_placements": [
            {"cluster": "ALREADY", "cell": "c", "xy": [0.0, 0.0]},   # entity exists -> skip
            {"cluster": "PLACED", "cell": "c", "xy": [2.0, 2.0]},    # already placed -> entity only
            {"cluster": "FRESH", "cell": "c", "xy": [3.0, 3.0]},     # fully new -> entity + tree
        ],
    })
    assert [e["name"] for e in out["entities"]] == ["ALREADY", "PLACED", "FRESH"]
    assert len(out["trees"]) == 2  # old + FRESH
    refs = {n["ref"] for t in out["trees"] for n in t.get("nodes", [])}
    assert refs == {"PLACED", "FRESH"}
    # the pre-existing "clone" node now points at an Entity -> rewritten
    old_tree = next(t for t in out["trees"] if t["name"] == "old")
    assert old_tree["nodes"][0]["kind"] == "placement"


def test_existing_clone_nodes_rewritten_to_placement():
    """Phase 6.2 cutover: pre-migration tree nodes with kind "clone" whose ref
    is now an Entity are rewritten to "placement" — otherwise Apply/Redraw's
    link_trees would fail to resolve them after clone_placements is cleared."""
    out = convert_clone_placements_to_entities({
        "clone_placements": [
            {"cluster": "E1", "cell": "c", "xy": [1.0, 1.0]},
            {"cluster": "E2", "cell": "c", "xy": [2.0, 2.0]},
        ],
        "trees": [
            {"name": "pre", "anchor": {"origin": True},
             "nodes": [{"ref": "E1", "kind": "clone", "xy": [0.0, 0.0]}]},
            # a non-entity ref (no matching clone) keeps its kind
            {"name": "legacy", "anchor": {"origin": True},
             "nodes": [{"ref": "OTHER", "kind": "clone", "xy": [0.0, 0.0]}]},
        ],
    })
    pre = next(t for t in out["trees"] if t["name"] == "pre")
    legacy = next(t for t in out["trees"] if t["name"] == "legacy")
    assert pre["nodes"][0]["kind"] == "placement"   # ref is now an Entity
    assert legacy["nodes"][0]["kind"] == "clone"     # not an Entity -> untouched
