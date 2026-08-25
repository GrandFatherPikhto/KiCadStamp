#!/usr/bin/env python3
"""Cycle guard in the recursive Cell resolver (2026-08-25, handoff
composite_cell_autodetect_and_cycle_guard.py, Задание 2).

The load-time check (check_no_cell_definition_cycles, config/loader.py)
catches a cyclic clone_placements graph in configs that go through
load_config() — but a cfg assembled in memory (GUI single-file edit flows,
ExtractDock auto-generating clone_placements, programmatic construction) can
still reach ClonePositionCalculator.compute_raw_positions() directly. That
path had NO cycle protection at all and died with Python's own RecursionError
instead of a config error. These tests pin the resolver-level guard: a cell
referencing itself (directly or via a longer chain) must raise a clean
ValidationError that names the full path (A -> B -> A), while a diamond (the
same leaf referenced from two SIBLING branches) is legitimately NOT a cycle.

Test style mirrors test_cell_placement_geometry.py (same MagicMock adapter
helpers) — the recursive-Cell composition machinery is covered there; here we
only exercise the new cycle guard on top of the unchanged resolver.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2
from kipy.board_types import Pad, FootprintInstance

from kicadstamp.config import Config, Cell, CellPlacement, ClonePlacement, TemplateComponentSlot
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.clone_position_calculator import ClonePositionCalculator

MM = 1_000_000


def _make_pad(number, x_mm, y_mm, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net_name = net_name
    return pad


def _make_fp(ref, role=None, nets=None, cluster=None):
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = ref
    fp._role = role
    fp._cluster = cluster
    fp._pads = [_make_pad("1", 0, 0, n) for n in (nets or [])]
    return fp


def _adapter_for(fps):
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps

    def _field(fp, name):
        if name == "Role":
            return getattr(fp, "_role", None)
        if name == "Cluster":
            return getattr(fp, "_cluster", None)
        return None

    adapter.get_footprint_pads.side_effect = lambda fp: list(getattr(fp, "_pads", []))
    adapter.get_field_value.side_effect = _field
    adapter.get_selected_items.return_value = []
    return adapter


def _leaf(name="leaf", role="R1", net="NET_A"):
    """A single-component leaf cell — the resolution terminus of every chain
    below (the recursion only needs a cell that EXISTS in cfg.cells to proceed;
    a component makes the acyclic path produce something to assert on)."""
    return Cell(name=name, components=[
        TemplateComponentSlot(role=role, offset_along_mm=0.0, offset_across_mm=0.0,
                              angle_deg=0.0),
    ])


def _cfg_for(cells, top_cell):
    top = ClonePlacement(cluster="top", cell=top_cell, xy=(0.0, 0.0))
    return Config(layer="F.Cu", cells=cells, clone_placements=[top])


class TestCycleGuard:
    def test_direct_self_reference_raises(self):
        """cell a -> a (one level): the top-level cell directly nests itself."""
        cells = {"a": Cell(name="a", clone_placements=[
            CellPlacement(name="x", cell="a")])}
        cfg = _cfg_for(cells, "a")
        calc = ClonePositionCalculator(_adapter_for([]), cfg)

        with pytest.raises(ValidationError, match="a -> a"):
            calc.compute_raw_positions(cfg.clone_placements)

    def test_two_level_direct_cycle_raises_with_path(self):
        """cell a -> b -> a (2 levels): the handoff's headline case. The error
        must name BOTH cells and the whole path, not just one name."""
        cells = {
            "a": Cell(name="a", clone_placements=[CellPlacement(name="x", cell="b")]),
            "b": Cell(name="b", clone_placements=[CellPlacement(name="y", cell="a")]),
        }
        cfg = _cfg_for(cells, "a")
        calc = ClonePositionCalculator(_adapter_for([]), cfg)

        with pytest.raises(ValidationError, match="a -> b -> a") as exc:
            calc.compute_raw_positions(cfg.clone_placements)
        text = str(exc.value)
        assert "a" in text and "b" in text

    def test_three_level_cycle_raises_with_full_path(self):
        """cell a -> b -> c -> a (3 levels): the cycle closes only deeper in."""
        cells = {
            "a": Cell(name="a", clone_placements=[CellPlacement(name="x", cell="b")]),
            "b": Cell(name="b", clone_placements=[CellPlacement(name="y", cell="c")]),
            "c": Cell(name="c", clone_placements=[CellPlacement(name="z", cell="a")]),
        }
        cfg = _cfg_for(cells, "a")
        calc = ClonePositionCalculator(_adapter_for([]), cfg)

        with pytest.raises(ValidationError, match="a -> b -> c -> a"):
            calc.compute_raw_positions(cfg.clone_placements)

    def test_diamond_shared_leaf_is_not_a_cycle(self):
        """A -> (B, C), both B and C referencing the same leaf D — the SAME
        leaf appears in two DIFFERENT, non-overlapping branches of one tree.
        Per the handoff this is a legitimate diamond, NOT a cycle: each branch
        carries its own chain, so D never sees itself twice on one path."""
        leaf = _leaf()
        cells = {
            "leaf": leaf,
            "b": Cell(name="b", clone_placements=[
                CellPlacement(name="x", cell="leaf", nets={"R1": "NET_A"})]),
            "c": Cell(name="c", clone_placements=[
                CellPlacement(name="y", cell="leaf", nets={"R1": "NET_A"})]),
            "a": Cell(name="a", clone_placements=[
                CellPlacement(name="p", cell="b"),
                CellPlacement(name="q", cell="c"),
            ]),
        }
        cfg = _cfg_for(cells, "a")
        c1 = _make_fp("C1", role="R1", nets=["NET_A"])
        calc = ClonePositionCalculator(_adapter_for([c1]), cfg)

        # Must not raise — and both branches must resolve their leaf component.
        placed, _vias, _tracks = calc.compute_raw_positions(cfg.clone_placements)
        assert len(placed) == 2

    def test_acyclic_nested_chain_still_resolves(self):
        """Regression: a normal (acyclic) nested chain keeps resolving exactly
        as before the guard — the new chain parameter must not disturb the
        existing recursive composition."""
        leaf = _leaf()
        mid = Cell(name="mid", clone_placements=[
            CellPlacement(name="inner", cell="leaf", xy=(1.0, 0.0),
                          nets={"R1": "NET_A"})])
        cells = {"leaf": leaf, "mid": mid}
        cfg = _cfg_for(cells, "mid")
        c1 = _make_fp("C1", role="R1", nets=["NET_A"])
        calc = ClonePositionCalculator(_adapter_for([c1]), cfg)

        placed, _vias, _tracks = calc.compute_raw_positions(cfg.clone_placements)
        assert len(placed) == 1
