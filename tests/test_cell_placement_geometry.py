#!/usr/bin/env python3
"""Tests for recursive Cell geometry (Phase 4 step 2, 2026-07-31) —
ClonePositionCalculator resolving a composite cell's nested clone_placements,
composing transforms across multiple levels. Expected positions are
independently re-derived here via the same trusted primitives
(rotate_local_offset/local_to_absolute from spoke_layout.py) rather than
hand-derived trig constants — this still catches real composition bugs
(wrong rotation summing order, rotating the wrong thing) since the
composition itself is written independently of the implementation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2
from kipy.board_types import Pad, FootprintInstance

from kicadstamp.config import Config, Cell, CellPlacement, ClonePlacement, TemplateComponentSlot, TemplateVia
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.spoke_layout import rotate_local_offset, local_to_absolute
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


class TestTwoLevelComposition:
    def _build(self):
        leaf = Cell(name="leaf", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=5.0, offset_across_mm=0.0, angle_deg=0.0),
        ])
        inner = CellPlacement(name="inner", cell="leaf", xy=(10.0, 0.0), rotation_deg=90.0,
                              nets={"R1": "NET_A"})
        mid = Cell(name="mid", clone_placements=[inner])
        top = ClonePlacement(cluster="top", cell="mid", xy=(100.0, 100.0))
        cfg = Config(layer="F.Cu", cells={"leaf": leaf, "mid": mid}, clone_placements=[top])
        return top, cfg

    def test_nested_component_position_and_angle_composed_correctly(self):
        top, cfg = self._build()
        c1 = _make_fp("C1", role="R1", nets=["NET_A"])
        adapter = _adapter_for([c1])
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        assert len(placed) == 1
        p = placed[0]
        assert p.ref == "C1"

        # Independently re-derive: top has no anchor (absolute xy=(100,100),
        # rotation=0); "inner" is nested one level in with its own xy=(10,0),
        # rotation_deg=90 relative to "mid"'s local (0,0).
        top_origin = Vector2.from_xy(int(100.0 * MM), int(100.0 * MM))
        inner_shift = rotate_local_offset(10.0, 0.0, 0.0)  # parent (top) rotation = 0
        inner_origin = Vector2.from_xy(top_origin.x + inner_shift.x, top_origin.y + inner_shift.y)
        inner_world_rotation = 0.0 + 90.0  # parent's 0 + inner's own 90
        expected_pos = local_to_absolute(inner_origin, 5.0, 0.0, inner_world_rotation)

        assert p.dest.x == expected_pos.x
        assert p.dest.y == expected_pos.y
        assert p.angle_deg == 0.0 + inner_world_rotation

    def test_nested_via_registry_key_contains_both_names(self):
        leaf = Cell(name="leaf", vias=[
            TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net="GND"),
        ])
        inner = CellPlacement(name="inner", cell="leaf", xy=(0.0, 0.0))
        mid = Cell(name="mid", clone_placements=[inner])
        top = ClonePlacement(cluster="top", cell="mid", xy=(0.0, 0.0))
        cfg = Config(layer="F.Cu", cells={"leaf": leaf, "mid": mid}, clone_placements=[top])
        adapter = _adapter_for([])
        calc = ClonePositionCalculator(adapter, cfg)

        _placed, vias, _tracks = calc.compute_raw_positions([top])
        assert len(vias) == 1
        assert "inner" in vias[0].registry_key


class TestThreeLevelComposition:
    def test_three_level_chain_resolves(self):
        """A composite cell nesting ANOTHER composite cell nesting a leaf —
        the one genuinely new piece of math (multi-level, not just one hop)."""
        leaf = Cell(name="leaf", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=0.0),
        ])
        level2 = CellPlacement(name="lvl2", cell="leaf", xy=(1.0, 0.0), rotation_deg=90.0,
                               nets={"R1": "NET_A"})
        mid = Cell(name="mid", clone_placements=[level2])
        level1 = CellPlacement(name="lvl1", cell="mid", xy=(1.0, 0.0), rotation_deg=90.0)
        top_cell = Cell(name="top_cell", clone_placements=[level1])
        top = ClonePlacement(cluster="top", cell="top_cell", xy=(0.0, 0.0))
        cfg = Config(layer="F.Cu", cells={"leaf": leaf, "mid": mid, "top_cell": top_cell},
                    clone_placements=[top])
        c1 = _make_fp("C1", role="R1", nets=["NET_A"])
        adapter = _adapter_for([c1])
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        assert len(placed) == 1
        p = placed[0]

        top_origin = Vector2.from_xy(0, 0)
        lvl1_shift = rotate_local_offset(1.0, 0.0, 0.0)  # parent (top) rotation = 0
        lvl1_origin = Vector2.from_xy(top_origin.x + lvl1_shift.x, top_origin.y + lvl1_shift.y)
        lvl1_world_rotation = 0.0 + 90.0
        lvl2_shift = rotate_local_offset(1.0, 0.0, lvl1_world_rotation)
        lvl2_origin = Vector2.from_xy(lvl1_origin.x + lvl2_shift.x, lvl1_origin.y + lvl2_shift.y)
        lvl2_world_rotation = lvl1_world_rotation + 90.0
        expected_pos = local_to_absolute(lvl2_origin, 1.0, 0.0, lvl2_world_rotation)

        assert p.dest.x == expected_pos.x
        assert p.dest.y == expected_pos.y
        assert p.angle_deg == 0.0 + lvl2_world_rotation


class TestNestedRoleMode:
    def test_nested_placement_by_role_without_cell(self):
        inner = CellPlacement(name="inner", role="R1", xy=(3.0, 4.0), nets={"R1": "NET_A"})
        mid = Cell(name="mid", clone_placements=[inner])
        top = ClonePlacement(cluster="top", cell="mid", xy=(0.0, 0.0))
        cfg = Config(layer="F.Cu", cells={"mid": mid}, clone_placements=[top])
        c1 = _make_fp("C1", role="R1", nets=["NET_A"])
        adapter = _adapter_for([c1])
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        assert len(placed) == 1
        assert placed[0].ref == "C1"
        assert placed[0].dest.x == int(3.0 * MM)
        assert placed[0].dest.y == int(4.0 * MM)


class TestMirrorOfCompositeCellIsRejected:
    def test_mirror_on_composite_cell_raises(self):
        leaf = Cell(name="leaf", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=0.0, offset_across_mm=0.0, angle_deg=0.0),
        ])
        inner = CellPlacement(name="inner", cell="leaf", xy=(0.0, 0.0), nets={"R1": "NET_A"})
        mid = Cell(name="mid", clone_placements=[inner])
        top = ClonePlacement(cluster="top", cell="mid", xy=(0.0, 0.0), mirror=True, layer="B.Cu")
        cfg = Config(layer="F.Cu", cells={"leaf": leaf, "mid": mid}, clone_placements=[top])
        adapter = _adapter_for([])
        calc = ClonePositionCalculator(adapter, cfg)

        with pytest.raises(ValidationError, match="not supported yet"):
            calc.compute_raw_positions([top])


class TestNoParamScoping:
    def test_nested_placement_does_not_inherit_parent_params(self):
        """The top-level clone's params must NOT leak into the nested
        placement's own net_template resolution — explicit passing only."""
        leaf = Cell(name="leaf", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=0.0, offset_across_mm=0.0,
                                  angle_deg=0.0, net_template="NET_{channel}"),
        ])
        # Top has params={"channel": 1}, but "inner" does NOT pass params
        # through — it should NOT resolve NET_1, it has no net source at all
        # for R1 (no nets[], no params to fill net_template's placeholder).
        inner = CellPlacement(name="inner", cell="leaf", xy=(0.0, 0.0))
        mid = Cell(name="mid", clone_placements=[inner])
        top = ClonePlacement(cluster="top", cell="mid", xy=(0.0, 0.0), params={"channel": 1})
        cfg = Config(layer="F.Cu", cells={"leaf": leaf, "mid": mid}, clone_placements=[top])
        c1 = _make_fp("C1", role="R1", nets=["NET_1"])
        adapter = _adapter_for([c1])
        calc = ClonePositionCalculator(adapter, cfg)

        with pytest.raises(ValidationError):
            calc.compute_raw_positions([top])
