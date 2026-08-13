# tests/gui/test_entity_delete.py
"""Tests for gui/docks/entity_delete.py — ConfigTreeDock's context-menu
Delete (2026-08-05). Pure file-operation tests, no PyQt widgets involved,
same shape as tests/gui/test_rename.py."""
import yaml

from gui.docks.entity_delete import backup_file, delete_entry, find_references
from gui.docks.rename import collect_graph_files


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── backup_file ──────────────────────────────────────────────────────────

def test_backup_file_copies_content_and_leaves_the_original_untouched(tmp_path):
    path = tmp_path / "cells.yaml"
    path.write_text("cells:\n  a: {}\n", encoding="utf-8")

    backup_path = backup_file(path)

    assert backup_path.exists()
    assert backup_path.name.startswith("cells.yaml.bak.")
    assert backup_path.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")


def test_backup_file_two_calls_produce_two_distinct_files(tmp_path):
    path = tmp_path / "cells.yaml"
    path.write_text("cells:\n  a: {}\n", encoding="utf-8")

    first = backup_file(path)
    path.write_text("cells:\n  a: {}\n  b: {}\n", encoding="utf-8")
    second = backup_file(path)

    assert first != second
    assert first.exists() and second.exists()
    assert _load(first) == {"cells": {"a": {}}}  # first backup keeps its own snapshot
    assert _load(second) == {"cells": {"a": {}, "b": {}}}


# ── find_references ──────────────────────────────────────────────────────

def test_find_references_finds_a_clone_placement_referencing_a_cell(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "clone_placements:\n  - name: spoke_1\n    cell: target_cell\n", encoding="utf-8")

    refs = find_references([path], "cell", "target_cell")

    assert refs == {path: ["spoke_1"]}


def test_find_references_finds_a_nested_spoke_cell_without_touching_the_rule(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "rules:\n"
        "  - name: power_rule\n"
        "    anchor_role: MCU\n"
        "    spokes:\n"
        "      - pad: '17'\n"
        "        cell: target_cell\n"
        "      - pad: '26'\n"
        "        cell: other_cell\n",
        encoding="utf-8")

    refs = find_references([path], "cell", "target_cell")

    assert path in refs
    assert len(refs[path]) == 1  # only the matching spoke, not the whole rule
    data = _load(path)  # find_references must not have written anything
    assert len(data["rules"][0]["spokes"]) == 2


def test_find_references_finds_a_point_chained_to_another_point(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "points:\n"
        "  base: {xy: [0, 0]}\n"
        "  chained: {anchor_point: base, shift_x_mm: 1.0}\n",
        encoding="utf-8")

    refs = find_references([path], "anchor_point", "base")

    assert refs == {path: ["chained"]}


def test_find_references_finds_a_rule_anchored_on_a_point(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "rules:\n  - net: '+3V3'\n    anchor_point: base\n", encoding="utf-8")

    refs = find_references([path], "anchor_point", "base")

    assert refs == {path: ["+3V3"]}


def test_find_references_empty_when_nothing_references_the_target(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("cells:\n  a: {}\n", encoding="utf-8")

    assert find_references([path], "cell", "a") == {}


# ── delete_entry: primary removal, no cascade ────────────────────────────

def test_delete_entry_removes_a_dict_section_entry_and_backs_up_the_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("cells:\n  keep: {}\n  drop: {}\n", encoding="utf-8")

    report = delete_entry(None, path, "cells", "drop", cascade=False)

    assert _load(path)["cells"] == {"keep": {}}
    assert report["backups"] == [path]
    assert report["cascade_files"] == []
    backups = list(tmp_path.glob("config.yaml.bak.*"))
    assert len(backups) == 1
    assert _load(backups[0])["cells"] == {"keep": {}, "drop": {}}  # pre-delete snapshot


def test_delete_entry_removes_a_list_section_entry_by_net_fallback(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "rules:\n  - net: '+3V3'\n    anchor_role: MCU\n  - net: GND\n    anchor_role: MCU\n",
        encoding="utf-8")

    delete_entry(None, path, "rules", "+3V3", cascade=False)

    nets = [r["net"] for r in _load(path)["rules"]]
    assert nets == ["GND"]


def test_delete_entry_removes_a_nameless_coordinate_placement_by_effective_name(tmp_path):
    """2026-08-12, Group 1: coordinate_placements is a normal named-records
    section — a nameless entry is matched in the tree by its cluster/role
    display name, and delete must recognize that same identity, exactly like
    rules:' net: fallback."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "coordinate_placements:\n"
        "  - cluster: X\n    role: R1\n"
        "  - cluster: X\n    role: R2\n", encoding="utf-8")

    delete_entry(None, path, "coordinate_placements", "X/R1", cascade=False)

    roles = [e["role"] for e in _load(path)["coordinate_placements"]]
    assert roles == ["R2"]


def test_delete_entry_without_cascade_leaves_references_dangling(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "cells:\n  target_cell: {}\n"
        "clone_placements:\n  - name: spoke_1\n    cell: target_cell\n",
        encoding="utf-8")

    delete_entry(path, path, "cells", "target_cell", cascade=False)

    data = _load(path)
    assert "target_cell" not in data["cells"]
    assert data["clone_placements"][0]["cell"] == "target_cell"  # left as-is


# ── delete_entry: cascade ────────────────────────────────────────────────

def test_delete_entry_cascade_removes_referencing_clone_placement(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "cells:\n  target_cell: {}\n  other_cell: {}\n"
        "clone_placements:\n"
        "  - name: spoke_1\n    cell: target_cell\n"
        "  - name: spoke_2\n    cell: other_cell\n",
        encoding="utf-8")

    report = delete_entry(path, path, "cells", "target_cell", cascade=True)

    data = _load(path)
    assert "target_cell" not in data["cells"]
    names = [e["name"] for e in data["clone_placements"]]
    assert names == ["spoke_2"]
    assert report["cascade_files"] == [path]


def test_delete_entry_cascade_removes_only_the_referencing_spoke_not_the_whole_rule(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "cells:\n  target_cell: {}\n"
        "rules:\n"
        "  - name: power_rule\n"
        "    anchor_role: MCU\n"
        "    spokes:\n"
        "      - pad: '17'\n        cell: target_cell\n"
        "      - pad: '26'\n        cell: keep_cell\n",
        encoding="utf-8")

    delete_entry(path, path, "cells", "target_cell", cascade=True)

    data = _load(path)
    pads = [s["pad"] for s in data["rules"][0]["spokes"]]
    assert pads == ["26"]  # the rule itself survives with one spoke left


def test_delete_entry_cascade_across_the_include_graph(tmp_path):
    (tmp_path / "cells.yaml").write_text("cells:\n  target_cell: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text(
        "include:\n  - cells.yaml\n"
        "clone_placements:\n  - name: spoke_1\n    cell: target_cell\n",
        encoding="utf-8")

    report = delete_entry(root, tmp_path / "cells.yaml", "cells", "target_cell", cascade=True)

    assert "target_cell" not in _load(tmp_path / "cells.yaml")["cells"]
    assert _load(root)["clone_placements"] == []
    assert {p.name for p in report["backups"]} == {"cells.yaml", "root.yaml"}
    assert {p.name for p in report["cascade_files"]} == {"root.yaml"}


def test_delete_entry_cascade_removes_a_point_chained_to_the_deleted_point(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "points:\n"
        "  base: {xy: [0, 0]}\n"
        "  chained: {anchor_point: base}\n",
        encoding="utf-8")

    delete_entry(path, path, "points", "base", cascade=True)

    assert _load(path)["points"] == {}


def test_delete_entry_never_backs_up_the_same_file_twice(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "cells:\n  target_cell: {}\n"
        "clone_placements:\n  - name: spoke_1\n    cell: target_cell\n",
        encoding="utf-8")

    report = delete_entry(path, path, "cells", "target_cell", cascade=True)

    assert report["backups"] == [path]  # entry_path == the only cascade file, listed once


# ── collect_graph_files reuse sanity check ───────────────────────────────

def test_collect_graph_files_still_used_the_same_way_delete_entry_expects(tmp_path):
    (tmp_path / "sub.yaml").write_text("cells: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    assert {p.name for p in collect_graph_files(root)} == {"root.yaml", "sub.yaml"}
