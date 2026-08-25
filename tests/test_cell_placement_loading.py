#!/usr/bin/env python3
"""Tests for recursive Cell (Phase 4, 2026-07-31): CellPlacement — nested,
closed-boundary references to other cells inside a Cell definition. See
handoff_2026_07_31_blocks.md/handoff_2026_07_31_consolidated.md for the
design (closed boundaries, no param scoping, refdes forbidden, cycle
detection on cell DEFINITIONS)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import Config, Cell, CellPlacement, load_config
from kicadstamp.exceptions import ValidationError
from kicadstamp.validation import check_clone_cells_exist, check_no_cell_definition_cycles

MINIMAL = "layer: B.Cu\nrules: []\n"


def _cfg(cells):
    return Config(layer='B.Cu', cells=cells,
                  rules=[], clone_placements=[])


class TestCellPlacementBasicLoading:
    def test_cell_mode_loads(self, tmp_path):
        text = MINIMAL + """
cells:
  leaf:
    components:
      - role: R1
  composite:
    clone_placements:
      - name: inner
        cell: leaf
        xy: [5.0, 2.0]
        rotation_deg: 90.0
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))

        composite = cfg.cells["composite"]
        assert len(composite.clone_placements) == 1
        nested = composite.clone_placements[0]
        assert nested.name == "inner"
        assert nested.cell == "leaf"
        assert nested.xy == (5.0, 2.0)
        assert nested.rotation_deg == 90.0

    def test_role_mode_loads(self, tmp_path):
        text = MINIMAL + """
cells:
  composite:
    clone_placements:
      - name: inner
        role: SOME_ROLE
        nets: {SOME_ROLE: NET_A}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))

        nested = cfg.cells["composite"].clone_placements[0]
        assert nested.role == "SOME_ROLE"
        assert nested.cell is None
        assert nested.nets == {"SOME_ROLE": "NET_A"}

    def test_xy_defaults_to_zero_zero(self, tmp_path):
        text = MINIMAL + """
cells:
  leaf:
    components: []
  composite:
    clone_placements:
      - name: inner
        cell: leaf
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.cells["composite"].clone_placements[0].xy == (0.0, 0.0)

    def test_leaf_and_composite_content_together_is_allowed(self, tmp_path):
        text = MINIMAL + """
cells:
  leaf:
    components: []
  mixed:
    components:
      - role: OWN_ROLE
    clone_placements:
      - name: inner
        cell: leaf
        xy: [1.0, 1.0]
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        mixed = cfg.cells["mixed"]
        assert len(mixed.components) == 1
        assert len(mixed.clone_placements) == 1

    def test_sheet_cluster_load_and_default(self, tmp_path):
        """2026-08-26 (handoff cell_placement_sheet_cluster): a nested
        clone_placement may carry its own-identity sheet/cluster (used by
        role_narrowing.py for internal role narrowing) — they must NOT be
        rejected as unknown fields, and default to None when absent."""
        text = MINIMAL + """
cells:
  leaf:
    components:
      - role: R1
  composite:
    clone_placements:
      - name: inner
        cell: leaf
        sheet: Channel_1
        cluster: PIF_DVDD
      - name: plain
        cell: leaf
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))

        with_identity = cfg.cells["composite"].clone_placements[0]
        assert with_identity.sheet == "Channel_1"
        assert with_identity.cluster == "PIF_DVDD"

        plain = cfg.cells["composite"].clone_placements[1]
        assert plain.sheet is None
        assert plain.cluster is None


class TestCellPlacementMutualExclusion:
    def test_cell_and_role_together_is_fatal(self, tmp_path):
        text = MINIMAL + """
cells:
  leaf:
    components: []
  composite:
    clone_placements:
      - name: inner
        cell: leaf
        role: SOME_ROLE
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError, match="cell and role together"):
            load_config(str(config_file))

    def test_neither_cell_nor_role_is_fatal(self, tmp_path):
        text = MINIMAL + """
cells:
  composite:
    clone_placements:
      - name: inner
        xy: [0, 0]
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError, match="neither cell nor role"):
            load_config(str(config_file))

    def test_without_name_is_fatal(self, tmp_path):
        text = MINIMAL + """
cells:
  leaf:
    components: []
  composite:
    clone_placements:
      - cell: leaf
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError, match="without name"):
            load_config(str(config_file))

    def test_duplicate_nested_name_is_fatal(self, tmp_path):
        text = MINIMAL + """
cells:
  leaf:
    components: []
  composite:
    clone_placements:
      - name: dup
        cell: leaf
        xy: [0, 0]
      - name: dup
        cell: leaf
        xy: [1, 1]
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError, match="appears twice"):
            load_config(str(config_file))


class TestCellPlacementClosedBoundary:
    @pytest.mark.parametrize("forbidden_field", [
        "anchor_ref: U5",
        "anchor_role: FPGA",
        "anchor_sheet: Channel_0",
        "anchor_pad: '1'",
        "by_selection: true",
        "ignore_selection: true",
    ])
    def test_forbidden_field_is_fatal_with_closed_boundary_hint(self, tmp_path, forbidden_field):
        text = MINIMAL + f"""
cells:
  leaf:
    components: []
  composite:
    clone_placements:
      - name: inner
        cell: leaf
        {forbidden_field}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError, match="closed-boundary"):
            load_config(str(config_file))


class TestCheckCloneCellsExistNested:
    def test_nested_reference_to_existing_cell_passes(self):
        cfg = _cfg({
            "leaf": Cell(name="leaf"),
            "composite": Cell(name="composite", clone_placements=[
                CellPlacement(name="inner", cell="leaf"),
            ]),
        })
        check_clone_cells_exist(cfg)  # should not raise

    def test_nested_reference_to_missing_cell_raises(self):
        cfg = _cfg({
            "composite": Cell(name="composite", clone_placements=[
                CellPlacement(name="inner", cell="does_not_exist"),
            ]),
        })
        with pytest.raises(ValidationError, match="does_not_exist"):
            check_clone_cells_exist(cfg)


class TestCheckNoCellDefinitionCycles:
    def test_no_cycle_passes(self):
        cfg = _cfg({
            "leaf": Cell(name="leaf"),
            "mid": Cell(name="mid", clone_placements=[CellPlacement(name="a", cell="leaf")]),
            "top": Cell(name="top", clone_placements=[CellPlacement(name="b", cell="mid")]),
        })
        check_no_cell_definition_cycles(cfg)  # should not raise

    def test_direct_self_cycle_raises(self):
        cfg = _cfg({
            "a": Cell(name="a", clone_placements=[CellPlacement(name="x", cell="a")]),
        })
        with pytest.raises(ValidationError, match="cycle among cell definitions"):
            check_no_cell_definition_cycles(cfg)

    def test_indirect_cycle_raises(self):
        cfg = _cfg({
            "a": Cell(name="a", clone_placements=[CellPlacement(name="x", cell="b")]),
            "b": Cell(name="b", clone_placements=[CellPlacement(name="y", cell="a")]),
        })
        with pytest.raises(ValidationError, match="cycle among cell definitions"):
            check_no_cell_definition_cycles(cfg)

    def test_three_level_indirect_cycle_raises_cleanly_not_recursion_error(self):
        cfg = _cfg({
            "a": Cell(name="a", clone_placements=[CellPlacement(name="x", cell="b")]),
            "b": Cell(name="b", clone_placements=[CellPlacement(name="y", cell="c")]),
            "c": Cell(name="c", clone_placements=[CellPlacement(name="z", cell="a")]),
        })
        with pytest.raises(ValidationError, match="cycle among cell definitions"):
            check_no_cell_definition_cycles(cfg)

    def test_diamond_shared_dependency_is_not_a_cycle(self):
        """Two different cells both nesting the SAME leaf — a DAG, not a
        cycle; must not be flagged (each cell has ONE parent per nesting
        edge, but a leaf can be referenced from multiple different parents)."""
        cfg = _cfg({
            "leaf": Cell(name="leaf"),
            "mid_a": Cell(name="mid_a", clone_placements=[CellPlacement(name="x", cell="leaf")]),
            "mid_b": Cell(name="mid_b", clone_placements=[CellPlacement(name="y", cell="leaf")]),
            "top": Cell(name="top", clone_placements=[
                CellPlacement(name="p", cell="mid_a"),
                CellPlacement(name="q", cell="mid_b"),
            ]),
        })
        check_no_cell_definition_cycles(cfg)  # should not raise
