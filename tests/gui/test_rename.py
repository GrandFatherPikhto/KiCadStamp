# tests/gui/test_rename.py
"""Tests for gui/docks/rename.py — ConfigTreeDock's context-menu Rename
(2026-08-04). Pure file-operation tests, no PyQt widgets involved (see
gui/docks/rename.py's module docstring for the cross-reference audit this
is built against)."""
import yaml

from gui.docks.rename import (collect_all_cell_names, collect_all_point_names, collect_all_sheet_names,
                              collect_graph_files, entry_effective_name, name_exists_in_graph,
                              rename_dict_entry, rename_entry, rename_list_entry, rename_references)


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── collect_graph_files ──────────────────────────────────────────────────

def test_collect_graph_files_walks_includes(tmp_path):
    (tmp_path / "sub.yaml").write_text("cells: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    files = collect_graph_files(root)

    assert {p.name for p in files} == {"root.yaml", "sub.yaml"}


def test_collect_graph_files_dedupes_a_diamond_include(tmp_path):
    """walk_include_tree() itself walks a diamond (same file reachable from
    two branches) twice — see its own docstring — collect_graph_files()
    must dedupe by resolved path or a rename would rewrite/report that file
    twice."""
    (tmp_path / "shared.yaml").write_text("cells: {}\n", encoding="utf-8")
    (tmp_path / "a.yaml").write_text("include:\n  - shared.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("include:\n  - shared.yaml\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - a.yaml\n  - b.yaml\n", encoding="utf-8")

    files = collect_graph_files(root)

    assert sorted(p.name for p in files) == ["a.yaml", "b.yaml", "root.yaml", "shared.yaml"]


# ── name_exists_in_graph ─────────────────────────────────────────────────

def test_name_exists_in_graph_finds_a_match_in_an_included_file(tmp_path):
    (tmp_path / "sub.yaml").write_text("cells:\n  existing_cell: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")
    files = collect_graph_files(root)

    assert name_exists_in_graph(files, "cells", "existing_cell") is True
    assert name_exists_in_graph(files, "cells", "no_such_cell") is False


# ── collect_all_cell_names ───────────────────────────────────────────────

def test_collect_all_cell_names_unions_across_the_whole_graph(tmp_path):
    (tmp_path / "sub.yaml").write_text(
        "cells:\n  b_cell: {}\n  a_cell: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text(
        "cells:\n  root_cell: {}\ninclude:\n  - sub.yaml\n", encoding="utf-8")

    assert collect_all_cell_names(root) == ["a_cell", "b_cell", "root_cell"]


def test_collect_all_cell_names_empty_when_no_cells_anywhere(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("rules: []\n", encoding="utf-8")

    assert collect_all_cell_names(root) == []


# ── collect_all_point_names ──────────────────────────────────────────────

def test_collect_all_point_names_unions_across_the_whole_graph(tmp_path):
    (tmp_path / "sub.yaml").write_text(
        "points:\n  b_point: {xy: [0, 0]}\n  a_point: {xy: [1, 1]}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text(
        "points:\n  root_point: {xy: [2, 2]}\ninclude:\n  - sub.yaml\n", encoding="utf-8")

    assert collect_all_point_names(root) == ["a_point", "b_point", "root_point"]


# ── collect_all_sheet_names ──────────────────────────────────────────────

def test_collect_all_sheet_names_reads_schematic_dir(tmp_path):
    """The Sheet-autocomplete list comes from the project's *.kicad_sch
    files (RuntimeContext.sheet_names, built inside config/loader.py's
    load_config), NOT a YAML section — a real root with schematic_dir
    pointing at a directory of .kicad_sch files."""
    sch = tmp_path / "sch"
    sch.mkdir()
    (sch / "root.kicad_sch").write_text(
        '(kicad_sch\n'
        '  (sheet\n'
        '    (uuid "11111111-1111-1111-1111-111111111111")\n'
        '    (property "Sheetname" "Channel_0"))\n'
        '  (sheet\n'
        '    (uuid "22222222-2222-2222-2222-222222222222")\n'
        '    (property "Sheetname" "DAC Sheet"))\n'
        ')\n',
        encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("schematic_dir: sch\n", encoding="utf-8")

    assert collect_all_sheet_names(root) == ["Channel_0", "DAC Sheet"]


def test_collect_all_sheet_names_dedupes_and_sorts(tmp_path):
    """Two sheet instances sharing one name must collapse to one list
    entry, sorted alphabetically (set() dedupe before sorted)."""
    sch = tmp_path / "sch"
    sch.mkdir()
    (sch / "root.kicad_sch").write_text(
        '(kicad_sch\n'
        '  (sheet (uuid "a") (property "Sheetname" "Channel_1"))\n'
        '  (sheet (uuid "b") (property "Sheetname" "Channel_1"))\n'
        '  (sheet (uuid "c") (property "Sheetname" "Channel_0"))\n'
        ')\n',
        encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("schematic_dir: sch\n", encoding="utf-8")

    assert collect_all_sheet_names(root) == ["Channel_0", "Channel_1"]


def test_collect_all_sheet_names_empty_when_no_schematic_dir(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("rules: []\n", encoding="utf-8")

    assert collect_all_sheet_names(root) == []


def test_collect_all_sheet_names_empty_on_broken_root(tmp_path):
    """A broken root config must yield an empty list, never raise — the
    Sheet fields stay free-text-editable either way, this is autocomplete,
    not validation."""
    root = tmp_path / "root.yaml"
    root.write_text("not: [valid yaml\n", encoding="utf-8")

    assert collect_all_sheet_names(root) == []


# ── rename_dict_entry ─────────────────────────────────────────────────────

def test_rename_dict_entry_renames_the_key_in_place(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "cells:\n  first: {a: 1}\n  target: {b: 2}\n  last: {c: 3}\n", encoding="utf-8")

    rename_dict_entry(path, "cells", "target", "renamed")

    data = _load(path)
    assert list(data["cells"].keys()) == ["first", "renamed", "last"]  # position preserved
    assert data["cells"]["renamed"] == {"b": 2}  # value untouched


def test_rename_dict_entry_raises_when_old_name_missing(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("cells:\n  a: {}\n", encoding="utf-8")

    try:
        rename_dict_entry(path, "cells", "does_not_exist", "new")
        assert False, "expected OSError"
    except OSError:
        pass


def test_rename_dict_entry_raises_on_collision(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("cells:\n  a: {}\n  b: {}\n", encoding="utf-8")

    try:
        rename_dict_entry(path, "cells", "a", "b")
        assert False, "expected OSError"
    except OSError:
        pass
    assert _load(path)["cells"] == {"a": {}, "b": {}}  # untouched on rejection


# ── rename_list_entry ─────────────────────────────────────────────────────

def test_rename_list_entry_renames_clone_placement_by_name(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "clone_placements:\n  - name: spoke_1\n    cell: ldo\n  - name: spoke_2\n    cell: ldo\n",
        encoding="utf-8")

    rename_list_entry(path, "clone_placements", "spoke_1", "spoke_1_renamed")

    data = _load(path)
    names = [e["name"] for e in data["clone_placements"]]
    assert names == ["spoke_1_renamed", "spoke_2"]
    assert data["clone_placements"][0]["cell"] == "ldo"  # other fields untouched


def test_rename_list_entry_gives_a_nameless_rule_an_explicit_name(tmp_path):
    """rules: entries may have no name: at all, falling back to net: as
    their effective display name (config/models.py's rule_effective_name())
    — renaming one is what GIVES it an explicit name: for the first time."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "rules:\n  - net: '+3V3'\n    anchor_role: MCU\n", encoding="utf-8")

    rename_list_entry(path, "rules", "+3V3", "power_rule")

    data = _load(path)
    assert data["rules"][0]["name"] == "power_rule"
    assert data["rules"][0]["net"] == "+3V3"  # net: itself is never touched


def test_rename_list_entry_gives_a_nameless_coordinate_placement_an_explicit_name(tmp_path):
    """coordinate_placements: entries may have no name: at all, falling back
    to cluster/role as their effective display name (config/models.py's
    coordinate_placement_effective_name()) — renaming one by that display
    name is what GIVES it an explicit name: for the first time (2026-08-12,
    Group 1: coordinate_placements is a normal named-records section now,
    addressable in the tree exactly like rules:' net: fallback)."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "coordinate_placements:\n  - cluster: FPGA_PERIPH\n    role: R18\n"
        "    x_mm: 10.0\n    y_mm: 20.0\n", encoding="utf-8")

    rename_list_entry(path, "coordinate_placements", "FPGA_PERIPH/R18", "my_cap")

    data = _load(path)
    assert data["coordinate_placements"][0]["name"] == "my_cap"
    assert data["coordinate_placements"][0]["cluster"] == "FPGA_PERIPH"  # identity fields untouched
    assert data["coordinate_placements"][0]["role"] == "R18"


def test_rename_list_entry_raises_on_collision(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "clone_placements:\n  - name: a\n  - name: b\n", encoding="utf-8")

    try:
        rename_list_entry(path, "clone_placements", "a", "b")
        assert False, "expected OSError"
    except OSError:
        pass


# ── rename_references ────────────────────────────────────────────────────

def test_rename_references_rewrites_every_matching_field_recursively(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "clone_placements:\n"
        "  - name: spoke_1\n"
        "    cell: old_cell\n"
        "cells:\n"
        "  outer_cell:\n"
        "    components:\n"
        "      - cell: old_cell\n"  # nested CellPlacement — recursive-Cell case
        "        offset_along_mm: 0\n"
        "  unrelated_cell:\n"
        "    components:\n"
        "      - role: SOME_ROLE\n",  # a plain role slot — must NOT be touched
        encoding="utf-8")

    changed = rename_references([path], "cell", "old_cell", "new_cell")

    assert changed == [path]
    data = _load(path)
    assert data["clone_placements"][0]["cell"] == "new_cell"
    assert data["cells"]["outer_cell"]["components"][0]["cell"] == "new_cell"
    assert data["cells"]["unrelated_cell"]["components"][0] == {"role": "SOME_ROLE"}


def test_rename_references_leaves_unaffected_files_unwritten(tmp_path):
    matching = tmp_path / "matching.yaml"
    matching.write_text("clone_placements:\n  - name: a\n    cell: old\n", encoding="utf-8")
    unrelated = tmp_path / "unrelated.yaml"
    unrelated.write_text("clone_placements:\n  - name: b\n    cell: something_else\n",
                         encoding="utf-8")

    changed = rename_references([matching, unrelated], "cell", "old", "new")

    assert changed == [matching]


# ── rename_entry (end-to-end) ─────────────────────────────────────────────

def test_rename_entry_cascades_a_cell_rename_across_the_graph(tmp_path):
    (tmp_path / "cells.yaml").write_text("cells:\n  old_cell: {components: []}\n",
                                         encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text(
        "include:\n  - cells.yaml\n"
        "clone_placements:\n  - name: spoke_1\n    cell: old_cell\n",
        encoding="utf-8")

    changed = rename_entry(root, tmp_path / "cells.yaml", "cells", "old_cell", "new_cell")

    assert {p.name for p in changed} == {"cells.yaml", "root.yaml"}
    assert "new_cell" in _load(tmp_path / "cells.yaml")["cells"]
    assert _load(root)["clone_placements"][0]["cell"] == "new_cell"


def test_rename_entry_does_not_cascade_for_a_non_referenced_section(tmp_path):
    """clone_placements:/thermal_via_arrays:/extract_profiles:/rules:/
    clone_profiles: are never referenced by name from elsewhere in the YAML
    graph (see gui/docks/rename.py's module docstring) — only the one file
    the entry itself lives in should ever be touched."""
    root = tmp_path / "root.yaml"
    root.write_text("clone_placements:\n  - name: spoke_1\n    cell: ldo\n", encoding="utf-8")

    changed = rename_entry(root, root, "clone_placements", "spoke_1", "spoke_1_renamed")

    assert changed == [root]


def test_rename_entry_refuses_a_graph_wide_collision_before_writing_anything(tmp_path):
    (tmp_path / "other.yaml").write_text("cells:\n  taken_name: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text(
        "include:\n  - other.yaml\n"
        "cells:\n  old_cell: {}\n",
        encoding="utf-8")

    try:
        rename_entry(root, root, "cells", "old_cell", "taken_name")
        assert False, "expected OSError"
    except OSError:
        pass

    # Nothing written — the collision is in a DIFFERENT file than the entry
    # itself, so this only fails if the graph-wide check ran before any write.
    assert _load(root)["cells"] == {"old_cell": {}}


# ── placer_name-aware identity for clone_placements (2026-08-15, plan
# config_tree_rename_placer_name_aware) — same split as config/models.py's
# clone_placement_effective_name(): display/match/rename must use placer_name
# (the save/--only identity), not the raw Cluster tag `name`. ─────────────

def test_entry_effective_name_uses_name_for_clone_placements():
    """clone_placements identity is name when set, else cluster (the Cluster
    tag) — the tree/Rename/Delete must show/match the save/--only identity,
    not the physical tag."""
    assert entry_effective_name("clone_placements",
                                {"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD"}) == "CH0_PIF_AVDD"
    assert entry_effective_name("clone_placements", {"cluster": "PIF_AVDD"}) == "PIF_AVDD"


def test_rename_list_entry_finds_entry_by_name(tmp_path):
    """Rename must locate a clone_placement by its name (the identity the tree
    shows) — and write the new value to name, leaving the Cluster tag
    untouched."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "clone_placements:\n"
        "  - cluster: PIF_AVDD\n    name: CH0_PIF_AVDD\n    cell: ldo\n",
        encoding="utf-8")

    rename_list_entry(path, "clone_placements", "CH0_PIF_AVDD", "CH1_PIF_AVDD")

    data = _load(path)["clone_placements"][0]
    assert data["name"] == "CH1_PIF_AVDD"
    assert data["cluster"] == "PIF_AVDD"  # Cluster tag untouched


def test_rename_list_entry_writes_cluster_when_no_name_yet(tmp_path):
    """Regression guard: an entry that never diverged still renames its
    single `cluster` field, exactly as before the split."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "clone_placements:\n  - cluster: spoke_1\n    cell: ldo\n", encoding="utf-8")

    rename_list_entry(path, "clone_placements", "spoke_1", "spoke_1_renamed")

    data = _load(path)["clone_placements"][0]
    assert data["cluster"] == "spoke_1_renamed"
    assert "name" not in data


def test_rename_list_entry_collision_checks_by_name(tmp_path):
    """The new_name collision check must also use the effective identity —
    renaming to a name another entry already has must be refused."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "clone_placements:\n"
        "  - cluster: A\n    name: CH0_PIF_AVDD\n    cell: ldo\n"
        "  - cluster: B\n    name: CH1_PIF_AVDD\n    cell: ldo\n",
        encoding="utf-8")

    try:
        rename_list_entry(path, "clone_placements", "CH0_PIF_AVDD", "CH1_PIF_AVDD")
        assert False, "expected OSError"
    except OSError:
        pass


# ── shared-cache regression (2026-08-26 code-review, item 3) ───────────────
#
# cached_file_read() returns the SHARED cached object on a hit (no defensive
# copy), so a rename that mutates read_data()'s return in place and then
# fails at write_data() would leave the in-process cache diverged from disk
# (write_data() only invalidates the cache AFTER a successful write). The
# rename helpers must deepcopy before mutating.

def test_rename_dict_entry_does_not_corrupt_the_cache_on_write_failure(tmp_path, monkeypatch):
    """Regression guard: a write_data() failure after mutation must NOT leave
    the shared cached object containing the renamed key — otherwise every
    later read_data() in this process reads a desynced value until restart."""
    import gui.docks.rename as rename_mod

    path = tmp_path / "config.yaml"
    path.write_text("cells:\n  first: {a: 1}\n  target: {b: 2}\n", encoding="utf-8")

    # Warm the cache so the read_data() inside rename_dict_entry is a HIT that
    # returns the SHARED cached object (the mutation hazard this guards).
    rename_mod.read_data(path)

    def _boom(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(rename_mod, "write_data", _boom)

    try:
        rename_mod.rename_dict_entry(path, "cells", "target", "renamed")
        assert False, "expected OSError"
    except OSError:
        pass

    # Cache hit (mtime unchanged — nothing was written) must still show the
    # on-disk state, not the mutated-in-memory rename.
    cached = rename_mod.read_data(path)
    assert list(cached["cells"].keys()) == ["first", "target"]
    assert cached["cells"]["target"] == {"b": 2}


def test_rename_list_entry_does_not_corrupt_the_cache_on_write_failure(tmp_path, monkeypatch):
    """Same regression guard for the list-section rename: a failed write must
    not leave the entry renamed inside the shared cached object."""
    import gui.docks.rename as rename_mod

    path = tmp_path / "config.yaml"
    path.write_text(
        "clone_placements:\n  - name: spoke_1\n    cell: ldo\n", encoding="utf-8")

    rename_mod.read_data(path)  # warm the cache (HIT inside rename_list_entry)

    def _boom(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(rename_mod, "write_data", _boom)

    try:
        rename_mod.rename_list_entry(path, "clone_placements", "spoke_1", "spoke_1_renamed")
        assert False, "expected OSError"
    except OSError:
        pass

    cached = rename_mod.read_data(path)
    assert cached["clone_placements"][0]["name"] == "spoke_1"
    assert cached["clone_placements"][0]["cell"] == "ldo"
