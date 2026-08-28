# tests/gui/test_entity_delete.py
"""Tests for gui/docks/entity_delete.py — ConfigTreeDock's context-menu
Delete (2026-08-05). Pure file-operation tests, no PyQt widgets involved,
same shape as tests/gui/test_rename.py. Fixtures are s-expr since
core_yaml_removal (2026-08-28) — the config graph reads/writes .sexp/.json
only."""
from gui.docks.entity_delete import backup_file, delete_entry, find_references
from gui.docks.rename import collect_graph_files
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict


def _write(path, data):
    path.write_text(dict_to_sexp(data), encoding="utf-8")
    return path


def _load(path):
    return sexp_to_dict(path.read_text(encoding="utf-8"))


# ── backup_file ──────────────────────────────────────────────────────────

def test_backup_file_copies_content_and_leaves_the_original_untouched(tmp_path):
    path = _write(tmp_path / "cells.sexp", {"cells": {"a": {}}})

    backup_path = backup_file(path)

    assert backup_path.exists()
    assert backup_path.name.startswith("cells.sexp.bak.")
    assert backup_path.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")


def test_backup_file_two_calls_produce_two_distinct_files(tmp_path):
    path = _write(tmp_path / "cells.sexp", {"cells": {"a": {}}})

    first = backup_file(path)
    _write(path, {"cells": {"a": {}, "b": {}}})
    second = backup_file(path)

    assert first != second
    assert first.exists() and second.exists()
    assert _load(first) == {"cells": {"a": {}}}  # first backup keeps its own snapshot
    assert _load(second) == {"cells": {"a": {}, "b": {}}}


# ── find_references ──────────────────────────────────────────────────────

def test_find_references_finds_a_clone_placement_referencing_a_cell(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "clone_placements": [{"name": "spoke_1", "cell": "target_cell"}]})

    refs = find_references([path], "cell", "target_cell")

    assert refs == {path: ["spoke_1"]}


def test_find_references_finds_a_nested_spoke_cell_without_touching_the_rule(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "rules": [{
            "name": "power_rule", "anchor_role": "MCU",
            "spokes": [{"pad": "17", "cell": "target_cell"},
                       {"pad": "26", "cell": "other_cell"}],
        }]})

    refs = find_references([path], "cell", "target_cell")

    assert path in refs
    assert len(refs[path]) == 1  # only the matching spoke, not the whole rule
    data = _load(path)  # find_references must not have written anything
    assert len(data["rules"][0]["spokes"]) == 2


def test_find_references_finds_a_point_chained_to_another_point(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "points": {
            "base": {"xy": [0, 0]},
            "chained": {"anchor_point": "base", "shift_x_mm": 1.0},
        }})

    refs = find_references([path], "anchor_point", "base")

    assert refs == {path: ["chained"]}


def test_find_references_finds_a_rule_anchored_on_a_point(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "rules": [{"net": "+3V3", "anchor_point": "base"}]})

    refs = find_references([path], "anchor_point", "base")

    assert refs == {path: ["+3V3"]}


def test_find_references_empty_when_nothing_references_the_target(tmp_path):
    path = _write(tmp_path / "config.sexp", {"cells": {"a": {}}})

    assert find_references([path], "cell", "a") == {}


# ── delete_entry: primary removal, no cascade ────────────────────────────

def test_delete_entry_removes_a_dict_section_entry_and_backs_up_the_file(tmp_path):
    path = _write(tmp_path / "config.sexp", {"cells": {"keep": {}, "drop": {}}})

    report = delete_entry(None, path, "cells", "drop", cascade=False)

    assert _load(path)["cells"] == {"keep": {}}
    assert report["backups"] == [path]
    assert report["cascade_files"] == []
    backups = list(tmp_path.glob("config.sexp.bak.*"))
    assert len(backups) == 1
    assert _load(backups[0])["cells"] == {"keep": {}, "drop": {}}  # pre-delete snapshot


def test_delete_entry_removes_a_list_section_entry_by_net_fallback(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "rules": [{"net": "+3V3", "anchor_role": "MCU"},
                  {"net": "GND", "anchor_role": "MCU"}]})

    delete_entry(None, path, "rules", "+3V3", cascade=False)

    nets = [r["net"] for r in _load(path)["rules"]]
    assert nets == ["GND"]


def test_delete_entry_removes_a_nameless_coordinate_placement_by_effective_name(tmp_path):
    """2026-08-12, Group 1: coordinate_placements is a normal named-records
    section — a nameless entry is matched in the tree by its cluster/role
    display name, and delete must recognize that same identity, exactly like
    rules:' net: fallback."""
    path = _write(tmp_path / "config.sexp", {
        "coordinate_placements": [
            {"cluster": "X", "role": "R1"},
            {"cluster": "X", "role": "R2"},
        ]})

    delete_entry(None, path, "coordinate_placements", "X/R1", cascade=False)

    roles = [e["role"] for e in _load(path)["coordinate_placements"]]
    assert roles == ["R2"]


def test_delete_entry_without_cascade_leaves_references_dangling(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "cells": {"target_cell": {}},
        "clone_placements": [{"name": "spoke_1", "cell": "target_cell"}],
    })

    delete_entry(path, path, "cells", "target_cell", cascade=False)

    data = _load(path)
    assert "target_cell" not in data["cells"]
    assert data["clone_placements"][0]["cell"] == "target_cell"  # left as-is


# ── delete_entry: cascade ────────────────────────────────────────────────

def test_delete_entry_cascade_removes_referencing_clone_placement(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "cells": {"target_cell": {}, "other_cell": {}},
        "clone_placements": [
            {"name": "spoke_1", "cell": "target_cell"},
            {"name": "spoke_2", "cell": "other_cell"},
        ],
    })

    report = delete_entry(path, path, "cells", "target_cell", cascade=True)

    data = _load(path)
    assert "target_cell" not in data["cells"]
    names = [e["name"] for e in data["clone_placements"]]
    assert names == ["spoke_2"]
    assert report["cascade_files"] == [path]


def test_delete_entry_cascade_removes_only_the_referencing_spoke_not_the_whole_rule(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "cells": {"target_cell": {}},
        "rules": [{
            "name": "power_rule", "anchor_role": "MCU",
            "spokes": [{"pad": "17", "cell": "target_cell"},
                       {"pad": "26", "cell": "keep_cell"}],
        }],
    })

    delete_entry(path, path, "cells", "target_cell", cascade=True)

    data = _load(path)
    pads = [s["pad"] for s in data["rules"][0]["spokes"]]
    assert pads == ["26"]  # the rule itself survives with one spoke left


def test_delete_entry_cascade_across_the_include_graph(tmp_path):
    _write(tmp_path / "cells.sexp", {"cells": {"target_cell": {}}})
    root = _write(tmp_path / "root.sexp", {
        "include": ["cells.sexp"],
        "clone_placements": [{"name": "spoke_1", "cell": "target_cell"}],
    })

    report = delete_entry(root, tmp_path / "cells.sexp", "cells", "target_cell", cascade=True)

    assert "target_cell" not in _load(tmp_path / "cells.sexp")["cells"]
    assert _load(root)["clone_placements"] == []
    assert {p.name for p in report["backups"]} == {"cells.sexp", "root.sexp"}
    assert {p.name for p in report["cascade_files"]} == {"root.sexp"}


def test_delete_entry_cascade_removes_a_point_chained_to_the_deleted_point(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "points": {
            "base": {"xy": [0, 0]},
            "chained": {"anchor_point": "base"},
        }})

    delete_entry(path, path, "points", "base", cascade=True)

    assert _load(path)["points"] == {}


def test_delete_entry_never_backs_up_the_same_file_twice(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "cells": {"target_cell": {}},
        "clone_placements": [{"name": "spoke_1", "cell": "target_cell"}],
    })

    report = delete_entry(path, path, "cells", "target_cell", cascade=True)

    assert report["backups"] == [path]  # entry_path == the only cascade file, listed once


# ── collect_graph_files reuse sanity check ───────────────────────────────

def test_collect_graph_files_still_used_the_same_way_delete_entry_expects(tmp_path):
    _write(tmp_path / "sub.sexp", {"cells": {}})
    root = _write(tmp_path / "root.sexp", {"include": ["sub.sexp"]})

    assert {p.name for p in collect_graph_files(root)} == {"root.sexp", "sub.sexp"}


def test_delete_entry_removes_clone_placement_by_name(tmp_path):
    """Delete goes through the same entry_effective_name as the tree shows —
    a clone_placement with name must be removed by that identity, not by its
    raw Cluster tag."""
    path = _write(tmp_path / "config.sexp", {
        "clone_placements": [
            {"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD", "cell": "ldo"},
        ]})

    # Matched by name -> removed.
    delete_entry(None, path, "clone_placements", "CH0_PIF_AVDD", cascade=False)
    assert _load(path)["clone_placements"] == []

    # Matched by raw Cluster tag -> NOT found (identity is name now).
    _write(path, {"clone_placements": [
        {"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD", "cell": "ldo"},
    ]})
    delete_entry(None, path, "clone_placements", "PIF_AVDD", cascade=False)
    assert len(_load(path)["clone_placements"]) == 1
