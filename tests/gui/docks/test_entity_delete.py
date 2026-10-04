# tests/gui/test_entity_delete.py
"""Tests for gui/docks/entity_delete.py — ConfigTreeDock's context-menu
Delete (2026-08-05). Pure file-operation tests, no PyQt widgets involved,
same shape as tests/gui/test_rename.py. Fixtures are s-expr since
core_yaml_removal (2026-08-28) — the config graph reads/writes .sexp/.json
only."""
import pytest
from gui.docks.entity_delete import backup_file, delete_entry, find_references
from gui.docks.rename import collect_graph_files
from kicadstamp.config import format_version
from kicadstamp.config.format_version import current_format
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from tests.fakes.format3 import without_identity


@pytest.fixture
def pin_format2(monkeypatch):
    """Pin CURRENT_FORMAT to 2 for a cell whose SUBJECT is the format-2
    grammar (a nameless chain / coordinate_placement matched by its net or
    cluster/role fallback — the format-3 lift MINTS a name, so the fallback
    display disappears). A no-op while the product is CURRENT_FORMAT = 2."""
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)


def _write(path, data):
    path.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
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
    assert without_identity(_load(first)) == {"cells": {"a": {}}}  # own snapshot
    assert without_identity(_load(second)) == {"cells": {"a": {}, "b": {}}}


# ── find_references ──────────────────────────────────────────────────────

def test_find_references_finds_a_clone_placement_referencing_a_cell(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "clone_placements": [{"name": "spoke_1", "cell": "target_cell"}]})

    refs = find_references([path], "cell", "target_cell")

    assert refs == {path: ["spoke_1"]}


def test_find_references_finds_a_nested_spoke_cell_without_touching_the_chain(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "chains": [{
            "name": "power_chain", "anchor_role": "MCU",
            "spokes": [{"pad": "17", "cell": "target_cell"},
                       {"pad": "26", "cell": "other_cell"}],
        }]})

    refs = find_references([path], "cell", "target_cell")

    assert path in refs
    assert len(refs[path]) == 1  # only the matching spoke, not the whole chain
    data = _load(path)  # find_references must not have written anything
    assert len(data["chains"][0]["spokes"]) == 2


def test_find_references_finds_a_point_chained_to_another_point(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "points": {
            "base": {"xy": [0, 0]},
            "chained": {"anchor_point": "base", "shift_x_mm": 1.0},
        }})

    refs = find_references([path], "anchor_point", "base")

    assert refs == {path: ["chained"]}


def test_find_references_finds_a_chain_anchored_on_a_point(tmp_path, pin_format2):
    """SUBJECT is the format-2 display identity of a nameless chain (net: as
    its effective name) — the format-3 lift mints a name, so pinned to 2."""
    path = _write(tmp_path / "config.sexp", {
        "chains": [{"net": "+3V3", "anchor_point": "base"}]})

    refs = find_references([path], "anchor_point", "base")

    assert refs == {path: ["+3V3"]}


def test_find_references_empty_when_nothing_references_the_target(tmp_path):
    path = _write(tmp_path / "config.sexp", {"cells": {"a": {}}})

    assert find_references([path], "cell", "a") == {}


# ── delete_entry: primary removal, no cascade ────────────────────────────

def test_delete_entry_removes_a_dict_section_entry_and_backs_up_the_file(tmp_path):
    path = _write(tmp_path / "config.sexp", {"cells": {"keep": {}, "drop": {}}})

    report = delete_entry(None, path, "cells", "drop", cascade=False)

    assert without_identity(_load(path)["cells"]) == {"keep": {}}
    assert report["backups"] == [path]
    assert report["cascade_files"] == []
    backups = list(tmp_path.glob("config.sexp.bak.*"))
    # Under format 3 the writer ALSO takes a `.bak` when it lifts the on-disk
    # format-2 file to format 3 (write_config_file's stale-format rule), so a
    # delete on a format-2 file leaves TWO copies; format 2 leaves one. Both
    # copies hold the same PRE-delete content.
    assert len(backups) == (2 if current_format() >= 3 else 1)
    assert without_identity(_load(backups[0])["cells"]) == {"keep": {}, "drop": {}}  # pre-delete snapshot


def test_delete_entry_removes_a_list_section_entry_by_net_fallback(tmp_path, pin_format2):
    """SUBJECT is the format-2 net: fallback identity of a nameless chain;
    the format-3 lift mints a name — pinned to 2."""
    path = _write(tmp_path / "config.sexp", {
        "chains": [{"net": "+3V3", "anchor_role": "MCU"},
                   {"net": "GND", "anchor_role": "MCU"}]})

    delete_entry(None, path, "chains", "+3V3", cascade=False)

    nets = [r["net"] for r in _load(path)["chains"]]
    assert nets == ["GND"]


def test_delete_entry_removes_a_nameless_coordinate_placement_by_effective_name(
        tmp_path, pin_format2):
    """2026-08-12, Group 1: coordinate_placements is a normal named-records
    section — a nameless entry is matched in the tree by its cluster/role
    display name, and delete must recognize that same identity, exactly like
    rules:' net: fallback. SUBJECT is the format-2 nameless grammar (the
    format-3 lift mints a name) — pinned to 2."""
    path = _write(tmp_path / "config.sexp", {
        "coordinate_placements": [
            {"cluster": "X", "role": "R1"},
            {"cluster": "X", "role": "R2"},
        ]})

    delete_entry(None, path, "coordinate_placements", "X/R1", cascade=False)

    roles = [e["role"] for e in _load(path)["coordinate_placements"]]
    assert roles == ["R2"]


def test_delete_entry_without_cascade_is_refused_under_format3(tmp_path):
    """Formatted for BOTH formats, because the outcome differs by design.

    Format 2: deleting without a cascade removes the record and leaves the
    reference dangling (no validation). Format 3 (Р-У4.3): the writer REFUSES
    to leave a dangling reference — the delete is refused, the file on disk is
    untouched, and the refusal NAMES the referring record (so the user is told
    which entries to cascade). The old cell asserted the format-2 outcome
    unconditionally; that premise is unreachable under format 3."""
    path = _write(tmp_path / "config.sexp", {
        "cells": {"target_cell": {}},
        "clone_placements": [{"name": "spoke_1", "cell": "target_cell"}],
    })

    if current_format() >= 3:
        with pytest.raises(OSError, match="spoke_1"):
            delete_entry(path, path, "cells", "target_cell", cascade=False)
        data = _load(path)
        assert "target_cell" in data["cells"]                       # file untouched
        assert data["clone_placements"][0]["cell"] == "target_cell"  # reference intact
    else:
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


def test_delete_entry_cascade_removes_only_the_referencing_spoke_not_the_whole_chain(tmp_path):
    path = _write(tmp_path / "config.sexp", {
        "cells": {"target_cell": {}, "keep_cell": {}},
        "chains": [{
            "name": "power_chain", "anchor_role": "MCU",
            "spokes": [{"pad": "17", "cell": "target_cell"},
                       {"pad": "26", "cell": "keep_cell"}],
        }],
    })

    delete_entry(path, path, "cells", "target_cell", cascade=True)

    data = _load(path)
    pads = [s["pad"] for s in data["chains"][0]["spokes"]]
    assert pads == ["26"]  # the chain itself survives with one spoke left


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


# ── cascade: end state built in memory, then ONE ordered write per file ────
#
# У3.5 delete-cascade finding. Rule 35 table for delete_entry(cascade=True):
#   end state        | cell
#   one file         | the record AND the reference to it in the SAME file
#   two files        | the reference in the OTHER file
# Every row is checked under BOTH formats: format 2 (no reference check at all)
# and format 3 (the writer REFUSES a file whose reference names a missing
# record — the shape the old "primary removal first" order could not survive).

def _reload(path):
    """Load the graph through the PRODUCT loader: proves what was written is a
    legal graph, not merely parseable text."""
    from kicadstamp.config import load_config
    return load_config(str(path))[0]


def test_delete_cascade_in_one_file_leaves_no_reference_and_the_graph_reloads(tmp_path):
    """The SAME file holds the deleted record and the reference to it. After the
    cascading delete the record is gone, no reference remains, and the graph
    LOADS. Under format 3 the writer refuses a write whose reference names a
    missing record, so the old order (primary removal written before the
    reference was pruned) failed on exactly this shape."""
    path = _write(tmp_path / "config.sexp", {
        "cells": {"target_cell": {}, "other_cell": {}},
        "clone_placements": [
            {"name": "spoke_1", "cluster": "CH0", "cell": "target_cell", "xy": [0.0, 0.0]},
            {"name": "spoke_2", "cluster": "CH0", "cell": "other_cell", "xy": [0.0, 0.0]},
        ],
    })

    delete_entry(path, path, "cells", "target_cell", cascade=True)

    data = _load(path)
    assert without_identity(data["cells"]) == {"other_cell": {}}
    assert [e["name"] for e in data["clone_placements"]] == ["spoke_2"]
    assert _reload(path).clone_placements[0].name == "spoke_2"


def test_delete_cascade_across_two_files_leaves_no_reference_and_the_graph_reloads(tmp_path):
    """The record lives in cells.sexp; the reference to it lives in the ROOT
    that includes it. Both files must be updated, and the root must still LOAD
    after the delete."""
    _write(tmp_path / "cells.sexp", {"cells": {"target_cell": {}}})
    root = _write(tmp_path / "root.sexp", {
        "include": ["cells.sexp"],
        "clone_placements": [{"name": "spoke_1", "cluster": "CH0",
                              "cell": "target_cell", "xy": [0.0, 0.0]}],
    })

    report = delete_entry(root, tmp_path / "cells.sexp", "cells", "target_cell", cascade=True)

    assert "target_cell" not in _load(tmp_path / "cells.sexp")["cells"]
    assert _load(root)["clone_placements"] == []
    assert {p.name for p in report["cascade_files"]} == {"root.sexp"}
    assert _reload(root).clone_placements == []


def test_delete_cascade_writes_the_referencing_file_before_the_deleted_records_file(
        tmp_path, monkeypatch):
    """Write ORDER is the invariant that keeps the on-disk graph legal at every
    step: every file that only REFERENCES the record is written first, and the
    file that HELD it is written LAST. Reversed, the record's file would be
    written while the referencing file still named it — an intermediate graph
    the format-3 writer refuses."""
    import gui.docks.entity_delete as entity_delete_mod

    _write(tmp_path / "cells.sexp", {"cells": {"target_cell": {}}})
    root = _write(tmp_path / "root.sexp", {
        "include": ["cells.sexp"],
        "clone_placements": [{"name": "spoke_1", "cluster": "CH0",
                              "cell": "target_cell", "xy": [0.0, 0.0]}],
    })
    entry = tmp_path / "cells.sexp"

    calls = []
    real_write = entity_delete_mod.write_data

    def _spy(path, data):
        calls.append(path)
        return real_write(path, data)

    monkeypatch.setattr(entity_delete_mod, "write_data", _spy)

    delete_entry(root, entry, "cells", "target_cell", cascade=True)

    assert calls.index(root) < calls.index(entry)


def test_delete_cascade_writes_a_file_that_holds_record_and_reference_once(
        tmp_path, monkeypatch):
    """When the deleted record and the reference to it share ONE file, that file
    is written EXACTLY ONCE — the old code wrote it twice (the primary removal,
    then the prune), and the first of those writes was the refused one."""
    import gui.docks.entity_delete as entity_delete_mod

    path = _write(tmp_path / "config.sexp", {
        "cells": {"target_cell": {}},
        "clone_placements": [{"name": "spoke_1", "cluster": "CH0",
                              "cell": "target_cell", "xy": [0.0, 0.0]}],
    })

    calls = []
    real_write = entity_delete_mod.write_data

    def _spy(p, data):
        calls.append(p)
        return real_write(p, data)

    monkeypatch.setattr(entity_delete_mod, "write_data", _spy)

    delete_entry(path, path, "cells", "target_cell", cascade=True)

    assert calls == [path]


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


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """У3.5 A, class (в): the format-3 writer resolves a reference's UUID against
    the ACTIVE GRAPH ROOT. These cells write a self-contained config; the root is
    a path that does NOT exist, so the stamp indexes THIS write's own records
    (config/format3._build_format3_index) — the format-3 product path, no
    on-disk graph walked. Under format 2 (< 3) the root is never consulted."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)
