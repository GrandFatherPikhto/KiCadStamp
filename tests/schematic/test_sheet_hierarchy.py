#!/usr/bin/env python3
# tests/schematic/test_sheet_hierarchy.py
"""The instance HIERARCHY of kicadstamp/sheet_names.py — `экз(X, F)` of Р1
(plan_2026_11_23_nested_clone_sheet_instances, Т1).

The schematic built here is the live HP-ISO-Channel-v099 one: `ISO In` is a
sheet that occurs ONCE, and its two blocks `Signal In A` / `Signal In B` point
at the SAME file. The old first-segment rule merged those two instances into
one (12 components instead of 6); `экз` keeps them apart because it counts a
PLACE, not a file — `walk_schematic_hierarchy` (schematic_discovery) dedups
diamonds on purpose and cannot answer this.

Т1 is ONLY the walk: which file sits under which file, and how many times.
Turning `экз` into the shortest-prefix instance name is Т2
(`instance_sheet_of`), and the `Sheetfile` of each block comes from the same
one sexpdata pass as the name dictionary (`uuid -> (name, Sheetfile)`).

The last cells pin the ROOT as a COMPLETENESS requirement (Denis, 23.11): `экз`
is counted only over the files the root walk reaches, never over
`schematic_files`; a missing or unparsable file of the tree steps the WHOLE
map back to Р4 with ONE Log line, so a partial tree can never undercount an
instance silently.
"""
import logging
from pathlib import Path

import pytest

import kicadstamp.sheet_names as sheet_names_module
from kicadstamp.config.loader import LazySheetNameMap, load_config
from kicadstamp.sheet_names import build_sheet_hierarchy


# ── building a schematic tree the way KiCad writes it ───────────────────────

def _sheet(uuid, name=None, sheetfile=None):
    """ONE `(sheet ...)` block: its own uuid, then the two properties."""
    lines = [f'(sheet (uuid "{uuid}")']
    if name is not None:
        lines.append(f'  (property "Sheetname" "{name}")')
    if sheetfile is not None:
        lines.append(f'  (property "Sheetfile" "{sheetfile}")')
    lines.append(')')
    return '\n'.join(lines)


def _write(path, *blocks):
    """Write one .kicad_sch and return its RESOLVED path (the walk compares
    resolved paths, never file names)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('(kicad_sch\n' + '\n'.join(blocks) + '\n)\n', encoding='utf-8')
    return str(path.resolve())


@pytest.fixture
def hp_iso(tmp_path):
    """The live shape: root -> ISO In (once) -> Signal In A|B (one file), and
    ISO Out -> four blocks over one file (Denis's SIG_OUT = four instances)."""
    signal_in = _write(tmp_path / "signal_in.kicad_sch")
    signal_out = _write(tmp_path / "signal_out.kicad_sch")
    iso_in = _write(
        tmp_path / "iso_in.kicad_sch",
        _sheet("b-sia", "Signal In A", "signal_in.kicad_sch"),
        _sheet("b-sib", "Signal In B", "signal_in.kicad_sch"))
    iso_out = _write(
        tmp_path / "iso_out.kicad_sch",
        *[_sheet(f"b-so{letter}", f"Signal Out {letter}", "signal_out.kicad_sch")
          for letter in "ABCD"])
    root = _write(
        tmp_path / "root.kicad_sch",
        _sheet("b-iso-in", "ISO In", "iso_in.kicad_sch"),
        _sheet("b-iso-out", "ISO Out", "iso_out.kicad_sch"))
    return {"root": root, "iso_in": iso_in, "iso_out": iso_out,
            "signal_in": signal_in, "signal_out": signal_out}


# ── экз(X, F): the multiplicities of ONE file ───────────────────────────────

def test_the_iso_in_diamond_counts_two_instances_of_one_file(hp_iso):
    """The whole live case in one assertion: the file under `Signal In A` and
    `Signal In B` is ONE file with TWO places under `ISO In`."""
    h = build_sheet_hierarchy(hp_iso["root"])
    assert h is not None
    assert h.instances(hp_iso["iso_in"], hp_iso["signal_in"]) == 2
    assert h.instances(hp_iso["root"], hp_iso["signal_in"]) == 2
    # `ISO In` itself occurs once, so a component standing ON it keeps one name.
    assert h.instances(hp_iso["root"], hp_iso["iso_in"]) == 1


def test_the_iso_out_fan_out_counts_four_instances(hp_iso):
    h = build_sheet_hierarchy(hp_iso["root"])
    assert h.instances(hp_iso["iso_out"], hp_iso["signal_out"]) == 4
    assert h.instances(hp_iso["root"], hp_iso["signal_out"]) == 4
    assert h.instances(hp_iso["root"], hp_iso["iso_out"]) == 1


def test_a_file_counts_itself_exactly_once(hp_iso):
    """Р1's base case — why the shortest-prefix search always terminates:
    `экз(F, F) == 1`, a sheet does not contain itself."""
    h = build_sheet_hierarchy(hp_iso["root"])
    for key in ("root", "iso_in", "iso_out", "signal_in", "signal_out"):
        assert h.instances(hp_iso[key], hp_iso[key]) == 1


def test_an_unrelated_file_counts_zero(hp_iso):
    h = build_sheet_hierarchy(hp_iso["root"])
    assert h.instances(hp_iso["signal_in"], hp_iso["signal_out"]) == 0
    assert h.instances(hp_iso["iso_out"], hp_iso["iso_in"]) == 0


def test_an_unknown_file_answers_zero_never_a_guess(hp_iso):
    """No file, no target, a file outside the tree — 0. A guessed 1 would turn
    a broken chain into a confident instance name."""
    h = build_sheet_hierarchy(hp_iso["root"])
    assert h.instances(None, hp_iso["signal_in"]) == 0
    assert h.instances("", hp_iso["signal_in"]) == 0
    assert h.instances(hp_iso["iso_in"], "") == 0
    assert h.instances(str(Path("/nowhere.kicad_sch")), hp_iso["signal_in"]) == 0


def test_children_keep_repeats_in_file_order(hp_iso):
    """Multiplicity is the POINT: the same child file must stay listed twice."""
    h = build_sheet_hierarchy(hp_iso["root"])
    assert h.children[hp_iso["root"]] == (hp_iso["iso_in"], hp_iso["iso_out"])
    assert h.children[hp_iso["iso_in"]] == (hp_iso["signal_in"], hp_iso["signal_in"])
    assert h.children[hp_iso["signal_in"]] == ()


def test_every_block_knows_the_file_it_lives_in(hp_iso):
    """`F` of Р1 comes from here, never from a file-name guess."""
    h = build_sheet_hierarchy(hp_iso["root"])
    assert h.block_file["b-iso-in"] == hp_iso["root"]
    assert h.block_file["b-sia"] == hp_iso["iso_in"]
    assert h.block_file["b-sib"] == hp_iso["iso_in"]
    assert len(h.block_file) == 2 + 2 + 4      # root, ISO In, ISO Out


def test_a_clone_of_a_clone_counts_four_places(tmp_path):
    """ш4's primitive: two clones of a sheet that itself carries two clones —
    four DISTINCT places for one file, and the inner count stays local."""
    signal = _write(tmp_path / "signal.kicad_sch")
    iso = _write(
        tmp_path / "iso.kicad_sch",
        _sheet("a1", "Signal A", "signal.kicad_sch"),
        _sheet("b1", "Signal B", "signal.kicad_sch"))
    root = _write(
        tmp_path / "root.kicad_sch",
        _sheet("i1", "ISO In 1", "iso.kicad_sch"),
        _sheet("i2", "ISO In 2", "iso.kicad_sch"))
    h = build_sheet_hierarchy(root)
    assert h.instances(root, signal) == 4
    assert h.instances(iso, signal) == 2     # the LOCAL submount, not the total
    assert h.instances(root, iso) == 2


def test_clones_deep_under_plain_sheets_keep_every_hop(tmp_path):
    """ш4б's primitive: root -> Wrap -> Mid (both plain, single) -> A|B."""
    signal = _write(tmp_path / "signal.kicad_sch")
    mid = _write(
        tmp_path / "mid.kicad_sch",
        _sheet("ma", "Signal In A", "signal.kicad_sch"),
        _sheet("mb", "Signal In B", "signal.kicad_sch"))
    wrap = _write(tmp_path / "wrap.kicad_sch",
                  _sheet("wm", "Mid", "mid.kicad_sch"))
    root = _write(tmp_path / "root.kicad_sch",
                  _sheet("rw", "Wrap", "wrap.kicad_sch"))
    h = build_sheet_hierarchy(root)
    assert h.instances(root, signal) == 2
    assert h.instances(wrap, signal) == 2
    assert h.instances(mid, signal) == 2
    assert h.instances(root, mid) == 1


def test_a_sheetfile_with_a_subdirectory_resolves_against_its_own_file(tmp_path):
    """Ч0 п.3: `Sheetfile` is relative to the CURRENT file's directory, so a
    subdirectory hop is legal and must not split one file into two."""
    signal = _write(tmp_path / "sub" / "signal.kicad_sch")
    root = _write(
        tmp_path / "root.kicad_sch",
        _sheet("sa", "Signal A", "sub/signal.kicad_sch"),
        _sheet("sb", "Signal B", "sub/signal.kicad_sch"))
    h = build_sheet_hierarchy(root)
    assert h.instances(root, signal) == 2
    assert h.instances(signal, signal) == 1


# ── Р4: no hierarchy, or an INCOMPLETE one — the old rule, and one Log line ──

def test_without_a_root_sheet_there_is_no_hierarchy(caplog):
    with caplog.at_level(logging.WARNING, logger="kicadstamp.sheet_names"):
        assert build_sheet_hierarchy(None) is None
        assert build_sheet_hierarchy("") is None
    assert caplog.records == []


def test_a_missing_root_file_steps_back_with_one_line(tmp_path, caplog):
    missing = str(tmp_path / "absent.kicad_sch")
    with caplog.at_level(logging.WARNING, logger="kicadstamp.sheet_names"):
        assert build_sheet_hierarchy(missing) is None
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(lines) == 1
    assert "sheet hierarchy:" in lines[0] and "absent.kicad_sch" in lines[0]


def test_a_missing_sheetfile_steps_the_whole_map_back_with_one_line(tmp_path, caplog):
    """Denis's rule: a PARTIAL tree could undercount an instance and hand out a
    wrong address silently — so ONE missing file cancels the WHOLE hierarchy."""
    root = _write(tmp_path / "root.kicad_sch",
                  _sheet("a", "Signal A", "gone.kicad_sch"),
                  _sheet("b", "Signal B", "gone.kicad_sch"))
    with caplog.at_level(logging.WARNING, logger="kicadstamp.sheet_names"):
        assert build_sheet_hierarchy(root) is None
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(lines) == 1
    assert "sheet hierarchy:" in lines[0] and "gone.kicad_sch" in lines[0]


def test_an_unparsable_file_of_the_tree_also_steps_back(tmp_path, caplog):
    """A file that cannot be parsed is «not found» too — the walk must not read
    it as «no child sheets» and undercount quietly."""
    (tmp_path / "broken.kicad_sch").write_text("(kicad_sch (sheet ",
                                               encoding='utf-8')
    root = _write(tmp_path / "root.kicad_sch",
                  _sheet("a", "Signal A", "broken.kicad_sch"))
    with caplog.at_level(logging.WARNING, logger="kicadstamp.sheet_names"):
        assert build_sheet_hierarchy(root) is None
    hierarchy_lines = [m for m in (r.getMessage() for r in caplog.records)
                       if "sheet hierarchy:" in m]
    assert len(hierarchy_lines) == 1
    assert "broken.kicad_sch" in hierarchy_lines[0]


def test_an_unparsable_file_leaves_the_name_dictionary_as_before(tmp_path):
    """The NEW failure mode is the INSTANCE walk only: the names map keeps its
    old behaviour (log the parse, skip the file, carry on) — Р4's promise."""
    broken = tmp_path / "broken.kicad_sch"
    broken.write_text("(kicad_sch (sheet ", encoding='utf-8')
    assert sheet_names_module._parse_sheet_uuids(str(broken)) == {}
    assert sheet_names_module._sheet_blocks(str(broken)) is None


# ── one parse, two projections ─────────────────────────────────────────────

def test_the_names_are_a_projection_of_the_one_parse(tmp_path):
    iso = _write(
        tmp_path / "iso.kicad_sch",
        _sheet("a", "Signal In A", "signal_in.kicad_sch"),
        _sheet("b", "Signal In B", "signal_in.kicad_sch"))
    assert sheet_names_module._parse_sheet_uuids(iso) == {
        "a": "Signal In A", "b": "Signal In B"}
    assert sheet_names_module._sheet_blocks(iso) == {
        "a": ("Signal In A", "signal_in.kicad_sch"),
        "b": ("Signal In B", "signal_in.kicad_sch")}


# ── the map: lazy, and its hierarchy never comes from schematic_files ───────

def test_the_hierarchy_is_built_on_first_access_only(monkeypatch):
    calls = []
    monkeypatch.setattr(sheet_names_module, "build_sheet_hierarchy",
                        lambda root: calls.append(root) or None)
    m = LazySheetNameMap("config.sexp", None, [], root_sheet="/root.kicad_sch")
    assert m.hierarchy is None and calls == ["/root.kicad_sch"]
    assert m.hierarchy is None and calls == ["/root.kicad_sch"]


def test_a_map_without_a_root_has_no_hierarchy():
    assert LazySheetNameMap("config.sexp", None, []).hierarchy is None


def test_the_hierarchy_does_not_need_schematic_files(hp_iso):
    """The killing cell for «экз по списку schematic_files»: no
    schematic_dir, an EMPTY schematic_files — the tree is still complete, and
    the name dictionary is still empty, so the two sources cannot be confused."""
    m = LazySheetNameMap("config.sexp", None, [], root_sheet=hp_iso["root"])
    assert dict(m) == {}
    assert m.hierarchy.instances(hp_iso["iso_in"], hp_iso["signal_in"]) == 2


def test_a_partial_schematic_files_list_does_not_limit_the_tree(hp_iso):
    """ш1's second half: only the root is listed for names, yet every file of
    the tree still takes part in the count."""
    m = LazySheetNameMap("config.sexp", None, [hp_iso["root"]],
                         root_sheet=hp_iso["root"])
    assert len(dict(m)) == 2                      # only the root's own blocks
    assert m.hierarchy.instances(m.hierarchy.root, hp_iso["signal_in"]) == 2


def test_load_config_hands_the_resolved_root_sheet_to_the_map(tmp_path, monkeypatch):
    """The plumbing that made the whole rule impossible before: `root_sheet`
    was READ but never passed (kicadstamp/config/loader.py)."""
    from kicadstamp.config.sexp_format import dict_to_sexp

    seen = {}
    real = LazySheetNameMap

    class _Spy(real):
        def __init__(self, config_path, schematic_dir, schematic_files,
                     root_sheet=None):
            seen["root_sheet"] = root_sheet
            super().__init__(config_path, schematic_dir, schematic_files,
                             root_sheet=root_sheet)

    monkeypatch.setattr("kicadstamp.config.loader.LazySheetNameMap", _Spy)
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"root_sheet": "board/root.kicad_sch",
                                  "cells": {}}), encoding='utf-8')
    load_config(str(root))
    assert seen["root_sheet"] == str((tmp_path / "board" / "root.kicad_sch").resolve())
