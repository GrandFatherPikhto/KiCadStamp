#!/usr/bin/env python3

from kicadstamp.schematic_discovery import walk_schematic_hierarchy
from tests.fakes.schematic_text import sch_file, sheet_block, symbol_block


def test_walk_single_file_no_sheets(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"])), encoding="utf-8")
    assert walk_schematic_hierarchy(str(root)) == [str(root.resolve())]


def test_walk_follows_sheet_references(tmp_path):
    child = tmp_path / "child.kicad_sch"
    child.write_text(sch_file(symbol_block(["R1"])), encoding="utf-8")
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(sheet_block("child.kicad_sch")), encoding="utf-8")

    files = walk_schematic_hierarchy(str(root))
    assert files[0] == str(root.resolve())
    assert str(child.resolve()) in files
    assert len(files) == 2


def test_walk_ignores_unrelated_file_in_same_directory(tmp_path):
    """A stray .kicad_sch sitting in the same folder but not referenced by
    any (sheet ...) must NOT be picked up — this is the whole point of
    switching away from a flat directory glob."""
    (tmp_path / "unrelated.kicad_sch").write_text(sch_file(symbol_block(["Z9"])), encoding="utf-8")
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"])), encoding="utf-8")

    files = walk_schematic_hierarchy(str(root))
    assert files == [str(root.resolve())]


def test_walk_diamond_shared_sheet_visited_once(tmp_path):
    shared = tmp_path / "shared.kicad_sch"
    shared.write_text(sch_file(symbol_block(["R1"])), encoding="utf-8")
    a = tmp_path / "a.kicad_sch"
    a.write_text(sch_file(sheet_block("shared.kicad_sch")), encoding="utf-8")
    b = tmp_path / "b.kicad_sch"
    b.write_text(sch_file(sheet_block("shared.kicad_sch")), encoding="utf-8")
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(sheet_block("a.kicad_sch"), sheet_block("b.kicad_sch")), encoding="utf-8")

    files = walk_schematic_hierarchy(str(root))
    assert files.count(str(shared.resolve())) == 1
    assert len(files) == 4  # root, a, b, shared


def test_walk_cycle_does_not_infinite_loop(tmp_path):
    a = tmp_path / "a.kicad_sch"
    b = tmp_path / "b.kicad_sch"
    a.write_text(sch_file(sheet_block("b.kicad_sch")), encoding="utf-8")
    b.write_text(sch_file(sheet_block("a.kicad_sch")), encoding="utf-8")

    files = walk_schematic_hierarchy(str(a))
    assert set(files) == {str(a.resolve()), str(b.resolve())}
