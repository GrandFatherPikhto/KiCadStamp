# tests/gui/test_entity_export.py
"""Tests for gui/docks/entity_export.py — ConfigTreeDock's context-menu
Export (2026-08-05). Pure file-operation tests, same shape as
tests/gui/test_rename.py / tests/gui/test_entity_delete.py."""
import yaml

from gui.docks.entity_export import ExportItem, export_entries


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_export_dict_section_entry_reads_fresh_from_source(tmp_path):
    """Payload for a DICT-section leaf is just the name (see config_tree.py's
    _entries() docstring) — export must re-read the actual entry from
    source_path, not trust whatever (possibly stale) payload the tree item
    happened to carry."""
    source = tmp_path / "cells.yaml"
    source.write_text("cells:\n  my_cell: {layer: 'F.Cu'}\n", encoding="utf-8")
    target = tmp_path / "out.yaml"
    target.write_text("{}\n", encoding="utf-8")
    item = ExportItem(source_path=source, section="cells", name="my_cell", payload="stale")

    export_entries(target, [item], overwrite=False)

    assert _load(target) == {"cells": {"my_cell": {"layer": "F.Cu"}}}


def test_export_list_section_entry_uses_the_payload_directly(tmp_path):
    source = tmp_path / "config.yaml"
    source.write_text("clone_placements:\n  - name: spoke_1\n    cell: ldo\n", encoding="utf-8")
    target = tmp_path / "out.yaml"
    target.write_text("{}\n", encoding="utf-8")
    item = ExportItem(source_path=source, section="clone_placements", name="spoke_1",
                      payload={"name": "spoke_1", "cell": "ldo"})

    export_entries(target, [item], overwrite=False)

    assert _load(target) == {"clone_placements": [{"name": "spoke_1", "cell": "ldo"}]}


def test_export_merge_preserves_the_target_files_other_content(tmp_path):
    source = tmp_path / "cells.yaml"
    source.write_text("cells:\n  my_cell: {}\n", encoding="utf-8")
    target = tmp_path / "out.yaml"
    target.write_text("cells:\n  existing: {}\ninclude:\n  - somewhere.yaml\n", encoding="utf-8")
    item = ExportItem(source_path=source, section="cells", name="my_cell", payload=None)

    export_entries(target, [item], overwrite=False)

    data = _load(target)
    assert data["cells"] == {"existing": {}, "my_cell": {}}
    assert data["include"] == ["somewhere.yaml"]


def test_export_merge_matches_a_nameless_rule_by_net_fallback(tmp_path):
    """rules: falls back to net: as identity when name: is absent (config/
    models.py's rule_effective_name()) — exporting into a target that
    already has a rule with the same net must REPLACE it, not duplicate."""
    source = tmp_path / "config.yaml"
    source.write_text("rules:\n  - net: '+3V3'\n    anchor_role: NEW_MCU\n", encoding="utf-8")
    target = tmp_path / "out.yaml"
    target.write_text("rules:\n  - net: '+3V3'\n    anchor_role: OLD_MCU\n", encoding="utf-8")
    item = ExportItem(source_path=source, section="rules", name="+3V3",
                      payload={"net": "+3V3", "anchor_role": "NEW_MCU"})

    export_entries(target, [item], overwrite=False)

    rules = _load(target)["rules"]
    assert len(rules) == 1
    assert rules[0]["anchor_role"] == "NEW_MCU"


def test_export_overwrite_replaces_the_targets_whole_content(tmp_path):
    source = tmp_path / "cells.yaml"
    source.write_text("cells:\n  my_cell: {}\n", encoding="utf-8")
    target = tmp_path / "out.yaml"
    target.write_text("cells:\n  unrelated: {}\ninclude:\n  - somewhere.yaml\n", encoding="utf-8")
    item = ExportItem(source_path=source, section="cells", name="my_cell", payload=None)

    export_entries(target, [item], overwrite=True)

    assert _load(target) == {"cells": {"my_cell": {}}}


def test_export_overwrite_combines_multiple_sections(tmp_path):
    source = tmp_path / "config.yaml"
    source.write_text(
        "cells:\n  my_cell: {}\n"
        "clone_placements:\n  - name: spoke_1\n    cell: my_cell\n",
        encoding="utf-8")
    target = tmp_path / "out.yaml"
    target.write_text("{}\n", encoding="utf-8")
    items = [
        ExportItem(source_path=source, section="cells", name="my_cell", payload=None),
        ExportItem(source_path=source, section="clone_placements", name="spoke_1",
                  payload={"name": "spoke_1", "cell": "my_cell"}),
    ]

    export_entries(target, items, overwrite=True)

    data = _load(target)
    assert data["cells"] == {"my_cell": {}}
    assert data["clone_placements"] == [{"name": "spoke_1", "cell": "my_cell"}]


def test_export_skips_a_dict_entry_that_no_longer_exists_in_the_source(tmp_path):
    """The tree can be stale (same tolerance ConfigTreeDock's click routing
    already has elsewhere) — a DICT-section entry deleted between selecting
    and exporting is silently skipped, not a crash."""
    source = tmp_path / "cells.yaml"
    source.write_text("cells: {}\n", encoding="utf-8")
    target = tmp_path / "out.yaml"
    target.write_text("{}\n", encoding="utf-8")
    item = ExportItem(source_path=source, section="cells", name="gone", payload=None)

    export_entries(target, [item], overwrite=False)

    assert _load(target) in ({}, None)


def test_export_merge_does_not_collapse_two_nameless_coordinate_placements(tmp_path):
    """2026-08-13 review, bug 3: coordinate_placements entries fall back to
    cluster/role as identity when name: is absent — exporting two different
    nameless records into one file must keep BOTH, not collapse them into one
    (the old rules-only key_fn left the identity None for every nameless
    entry, so the second merge-write replaced the first)."""
    target = tmp_path / "out.yaml"
    target.write_text("{}\n", encoding="utf-8")
    items = [
        ExportItem(source_path=tmp_path / "a.yaml", section="coordinate_placements",
                   name="X/R1", payload={"cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0}),
        ExportItem(source_path=tmp_path / "a.yaml", section="coordinate_placements",
                   name="X/R2", payload={"cluster": "X", "role": "R2", "x_mm": 3.0, "y_mm": 4.0}),
    ]

    export_entries(target, items, overwrite=False)

    entries = _load(target)["coordinate_placements"]
    assert [e["role"] for e in entries] == ["R1", "R2"]
