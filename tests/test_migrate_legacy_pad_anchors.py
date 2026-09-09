#!/usr/bin/env python3
"""Tests for tools/migrate_legacy_pad_anchors.py (GUARD 2, plan
2026_09_09_cell_anchor_v2 §A.5): a Cell with anchor_role + anchor_pad and NO
anchor_xy is the LEGACY rebase-by-pad form (design 2026-09-04) whose stored
mount is (0,0). Phase A repurposes that field combination as a declarative pad
anchor (resolved live at apply), so without migration such cells would shift on
the board. The migration writes anchor_xy: [0.0, 0.0] — preserving today's
behaviour byte-for-byte and freeing the "no xy" form for the new meaning.

Pure + file-level + include-graph tests; no Qt, no live board.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from tools.migrate_legacy_pad_anchors import (  # noqa: E402
    migrate_cells_data,
    migrate_file,
    migrate_root,
)


def _legacy_cell(name="pif_3v3_vdd", **extra):
    cell = {"anchor_role": "C_OUT_BYPASS", "anchor_pad": "1"}
    cell.update(extra)
    return name, cell


class TestMigrateCellsData:
    def test_legacy_pad_anchor_gets_zero_xy(self):
        data = {"cells": dict([_legacy_cell()])}
        migrated = migrate_cells_data(data)
        assert migrated == ["pif_3v3_vdd"]
        cell = data["cells"]["pif_3v3_vdd"]
        assert cell["anchor_xy"] == [0.0, 0.0]
        assert cell["anchor_role"] == "C_OUT_BYPASS"
        assert cell["anchor_pad"] == "1"

    def test_already_v2_anchor_xy_is_left_alone(self):
        """A cell that already carries anchor_xy (the v2 form — including the
        exact combination the 2026-09-08/09 commits wrote) is untouched."""
        name, cell = _legacy_cell(anchor_xy=[-3.05, -1.295])
        data = {"cells": {name: cell}}
        assert migrate_cells_data(data) == []
        assert cell["anchor_xy"] == [-3.05, -1.295]

    def test_role_only_is_left_alone(self):
        name, cell = _legacy_cell(anchor_pad=None)
        cell.pop("anchor_pad", None)
        data = {"cells": {name: cell}}
        assert migrate_cells_data(data) == []
        assert "anchor_xy" not in cell

    def test_pad_only_without_role_is_left_alone(self):
        cell = {"anchor_pad": "1"}  # pad without role is not a pad anchor
        data = {"cells": {"odd": cell}}
        assert migrate_cells_data(data) == []
        assert "anchor_xy" not in cell

    def test_no_anchor_is_left_alone(self):
        cell = {"components": []}
        data = {"cells": {"plain": cell}}
        assert migrate_cells_data(data) == []
        assert "anchor_xy" not in cell

    def test_only_legacy_cells_are_reported_sorted(self):
        data = {"cells": dict([
            _legacy_cell("zeta"),
            _legacy_cell("alpha"),
            ("v2", {"anchor_role": "R", "anchor_pad": "1",
                    "anchor_xy": [0.0, 0.0]}),
        ])}
        assert migrate_cells_data(data) == ["alpha", "zeta"]
        assert data["cells"]["alpha"]["anchor_xy"] == [0.0, 0.0]
        assert data["cells"]["zeta"]["anchor_xy"] == [0.0, 0.0]
        assert data["cells"]["v2"]["anchor_xy"] == [0.0, 0.0]

    def test_second_run_is_a_noop(self):
        data = {"cells": dict([_legacy_cell()])}
        assert migrate_cells_data(data) == ["pif_3v3_vdd"]
        assert migrate_cells_data(data) == []

    def test_non_cell_entries_are_skipped(self):
        data = {"cells": {"not_a_cell": "scalar"}}
        assert migrate_cells_data(data) == []


class TestMigrateFile:
    def test_roundtrip_writes_zero_xy_and_backup(self, tmp_path):
        p = tmp_path / "profile.json"
        p.write_text(json.dumps({"cells": dict([_legacy_cell()])}),
                     encoding="utf-8")
        assert migrate_file(p) == ["pif_3v3_vdd"]
        # The cell now carries anchor_xy=[0,0].
        reread = json.loads(p.read_text(encoding="utf-8"))
        assert reread["cells"]["pif_3v3_vdd"]["anchor_xy"] == [0.0, 0.0]
        # A timestamped .bak of the ORIGINAL was created next to the file.
        baks = list(tmp_path.glob("profile.json.bak.*"))
        assert len(baks) == 1
        original = json.loads(baks[0].read_text(encoding="utf-8"))
        assert "anchor_xy" not in original["cells"]["pif_3v3_vdd"]

    def test_noop_file_is_not_rewritten(self, tmp_path):
        p = tmp_path / "clean.json"
        p.write_text(json.dumps({"cells": {"v2": {"anchor_role": "R",
                                                  "anchor_pad": "1",
                                                  "anchor_xy": [0.0, 0.0]}}}),
                     encoding="utf-8")
        assert migrate_file(p) == []
        assert list(tmp_path.glob("clean.json.bak.*")) == []


class TestMigrateRoot:
    def test_walks_includes(self, tmp_path):
        """Cells live across the include: graph — the migration must reach an
        included subsystem file, not just the root."""
        sub = tmp_path / "sub.json"
        sub.write_text(json.dumps({"cells": dict([_legacy_cell("sub_cell")])}),
                       encoding="utf-8")
        root = tmp_path / "root.json"
        root.write_text(json.dumps({
            "cells": dict([
                _legacy_cell("root_cell"),
                ("v2", {"anchor_role": "R", "anchor_pad": "1",
                        "anchor_xy": [0.0, 0.0]}),
            ]),
            "include": [str(sub)],
        }), encoding="utf-8")

        result = migrate_root(root)
        migrated_names = [n for names in result.values() for n in names]
        assert sorted(migrated_names) == ["root_cell", "sub_cell"]

        root_data = json.loads(root.read_text(encoding="utf-8"))
        sub_data = json.loads(sub.read_text(encoding="utf-8"))
        assert root_data["cells"]["root_cell"]["anchor_xy"] == [0.0, 0.0]
        assert sub_data["cells"]["sub_cell"]["anchor_xy"] == [0.0, 0.0]
        # The already-v2 root cell stayed untouched.
        assert root_data["cells"]["v2"]["anchor_xy"] == [0.0, 0.0]

    def test_clean_graph_returns_empty(self, tmp_path):
        root = tmp_path / "root.json"
        root.write_text(json.dumps({"cells": {}}), encoding="utf-8")
        assert migrate_root(root) == {}
