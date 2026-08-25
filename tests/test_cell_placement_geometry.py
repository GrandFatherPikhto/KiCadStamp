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
from kicadstamp.placement.services import role_narrowing
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

    def test_owner_ref_is_nested_level_not_top(self):
        """2026-08-26 (handoff tag_cluster_overtag): a component resolved by a
        nested CellPlacement must carry THAT nested placement's name as
        owner_ref (not the top-level clone's) — placer.py::_tag_cluster uses
        this to avoid re-tagging nested sub-cell components with the top
        placement's Cluster."""
        top, cfg = self._build()
        c1 = _make_fp("C1", role="R1", nets=["NET_A"])
        adapter = _adapter_for([c1])
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        assert len(placed) == 1
        assert placed[0].owner_ref == "inner"  # NOT "top"

    def test_owner_ref_is_top_level_for_own_direct_component(self):
        """The mirror case: a component owned DIRECTLY by the top-level
        placement's cell (mid) is resolved at the TOP level, so its owner_ref
        is the top clone's effective name — while the nested one still carries
        the nested name."""
        leaf = Cell(name="leaf", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=5.0, offset_across_mm=0.0, angle_deg=0.0),
        ])
        inner = CellPlacement(name="inner", cell="leaf", xy=(10.0, 0.0), nets={"R1": "NET_A"})
        mid = Cell(name="mid", components=[
            TemplateComponentSlot(role="R_MID", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=0.0),
        ], clone_placements=[inner])
        top = ClonePlacement(cluster="top", cell="mid", xy=(0.0, 0.0),
                             nets={"R_MID": "NET_B"})
        cfg = Config(layer="F.Cu", cells={"leaf": leaf, "mid": mid}, clone_placements=[top])
        adapter = _adapter_for([
            _make_fp("C1", role="R1", nets=["NET_A"]),
            _make_fp("RM1", role="R_MID", nets=["NET_B"]),
        ])
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        by_ref = {p.ref: p.owner_ref for p in placed}
        assert by_ref["C1"] == "inner"
        assert by_ref["RM1"] == "top"


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


class TestCellPlacementSheetInheritance:
    """2026-08-26 (handoff cell_placement_sheet_inherit): a nested
    CellPlacement with no own `sheet` inherits the RESOLVED sheet of the
    enclosing placement (chained through arbitrarily deep nesting), so a
    reusable composite cell resolves per-channel without hardcoding the
    channel into the nested entries. Verified by spying on
    role_narrowing.narrow_candidates_by_sheet — the only consumer of the
    effective sheet (it filters ambiguity candidates by it)."""

    def _leaf(self):
        return Cell(name="leaf", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=0.0),
        ])

    def _spy_narrow(self, monkeypatch, calls):
        def _spy(candidates, sheet, sheet_names):
            calls.append(sheet)
            # collapse to the first candidate so the cascade resolves uniquely
            return [candidates[0]]

        monkeypatch.setattr(role_narrowing, "narrow_candidates_by_sheet", _spy)

    def _two_candidates(self):
        # 2 same-role/same-net candidates -> ambiguity narrowing actually runs
        return [
            _make_fp("C1", role="R1", nets=["NET_A"]),
            _make_fp("C2", role="R1", nets=["NET_A"]),
        ]

    def _two_level(self, top_sheet, inner_sheet=None):
        leaf = self._leaf()
        inner = CellPlacement(name="inner", cell="leaf", xy=(1.0, 0.0),
                              nets={"R1": "NET_A"}, sheet=inner_sheet)
        mid = Cell(name="mid", clone_placements=[inner])
        top = ClonePlacement(cluster="top", cell="mid", xy=(0.0, 0.0),
                             nets={"R1": "NET_A"}, sheet=top_sheet)
        cfg = Config(layer="F.Cu", cells={"leaf": leaf, "mid": mid}, clone_placements=[top])
        return top, cfg

    def test_nested_without_sheet_inherits_parent_sheet(self, monkeypatch):
        top, cfg = self._two_level(top_sheet="SheetA")
        calls = []
        self._spy_narrow(monkeypatch, calls)
        adapter = _adapter_for(self._two_candidates())
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        assert len(placed) == 1
        # the nested level narrowed with the INHERITED top sheet, not None
        assert calls == ["SheetA"]

    def test_explicit_nested_sheet_overrides_inheritance(self, monkeypatch):
        top, cfg = self._two_level(top_sheet="SheetA", inner_sheet="SheetB")
        calls = []
        self._spy_narrow(monkeypatch, calls)
        adapter = _adapter_for(self._two_candidates())
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        assert len(placed) == 1
        assert calls == ["SheetB"]  # explicit value wins, not inherited "SheetA"

    def test_three_level_chain_inherits_through_middle_level(self, monkeypatch):
        """The top sheet must chain through an intermediate level that has no
        sheet of its own — not just one hop down."""
        leaf = self._leaf()
        level2 = CellPlacement(name="lvl2", cell="leaf", xy=(1.0, 0.0), nets={"R1": "NET_A"})
        mid2 = Cell(name="mid2", clone_placements=[level2])
        level1 = CellPlacement(name="lvl1", cell="mid2", xy=(1.0, 0.0))
        mid = Cell(name="mid", clone_placements=[level1])
        top = ClonePlacement(cluster="top", cell="mid", xy=(0.0, 0.0),
                             nets={"R1": "NET_A"}, sheet="SheetA")
        cfg = Config(layer="F.Cu", cells={"leaf": leaf, "mid2": mid2, "mid": mid},
                     clone_placements=[top])
        calls = []
        self._spy_narrow(monkeypatch, calls)
        adapter = _adapter_for(self._two_candidates())
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        assert len(placed) == 1
        assert calls == ["SheetA"]  # reached the leaf via lvl1 -> lvl2 chain

    def test_shared_cell_not_mutated_between_branches(self, monkeypatch):
        """Regression: `nested` is a SHARED object between recursion branches
        (one `mid` Cell entry reused by two different top-level ClonePlacements
        with different sheets). Inheritance must build a local copy — the
        original cfg.cells[...] entry must stay sheet=None, and the second
        branch must see its OWN sheet, not the first branch's."""
        leaf = self._leaf()
        inner = CellPlacement(name="inner", cell="leaf", xy=(1.0, 0.0), nets={"R1": "NET_A"})
        mid = Cell(name="mid", clone_placements=[inner])
        top0 = ClonePlacement(cluster="top0", cell="mid", xy=(0.0, 0.0),
                              nets={"R1": "NET_A"}, sheet="Channel_0")
        top1 = ClonePlacement(cluster="top1", cell="mid", xy=(0.0, 0.0),
                              nets={"R1": "NET_A"}, sheet="Channel_1")
        cfg = Config(layer="F.Cu", cells={"leaf": leaf, "mid": mid},
                     clone_placements=[top0, top1])
        calls = []
        self._spy_narrow(monkeypatch, calls)
        adapter = _adapter_for(self._two_candidates())
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top0, top1])

        assert len(placed) == 2
        assert inner.sheet is None  # original shared object untouched
        assert inner.params == {}  # ... and params not polluted with "sheet" either
        assert calls == ["Channel_0", "Channel_1"]  # each branch its own sheet


class TestCellPlacementSheetPlaceholder:
    """2026-08-26 (handoff cell_placement_net_sheet_template): nets:/params:
    of a nested CellPlacement may use the `{sheet}` placeholder — the EFFECTIVE
    sheet (own or inherited) is injected into the nested placement's OWN params
    under "sheet" at resolve time, so per-instance hierarchical nets like
    /Channel_1/DAC/+3V3_DVDD resolve per-channel instead of dragging Channel_1
    parts onto every other channel. Real net-based resolution, no mocks."""

    def _build(self, top_sheet, inner_sheet=None, inner_params=None, net_template="{sheet}_NET"):
        leaf = Cell(name="leaf", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=0.0),
        ])
        inner = CellPlacement(name="inner", cell="leaf", xy=(1.0, 0.0),
                              nets={"R1": net_template}, sheet=inner_sheet, params=inner_params or {})
        mid = Cell(name="mid", clone_placements=[inner])
        top = ClonePlacement(cluster="top", cell="mid", xy=(0.0, 0.0), sheet=top_sheet)
        cfg = Config(layer="F.Cu", cells={"leaf": leaf, "mid": mid}, clone_placements=[top])
        return top, cfg

    def test_inherited_sheet_resolves_sheet_placeholder(self):
        """top.sheet='Channel_0', nested nets {R1: '{sheet}_NET'} -> matches a
        component on 'Channel_0_NET' (NOT the literal '{sheet}_NET' — which no
        real net has)."""
        top, cfg = self._build(top_sheet="Channel_0")
        adapter = _adapter_for([_make_fp("C1", role="R1", nets=["Channel_0_NET"])])
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        assert len(placed) == 1
        assert placed[0].ref == "C1"

    def test_explicit_nested_sheet_wins_for_placeholder(self):
        """Explicit nested.sheet ('SheetB') overrides the inherited one
        ('SheetA') — {sheet} resolves to the explicit value."""
        top, cfg = self._build(top_sheet="SheetA", inner_sheet="SheetB")
        adapter = _adapter_for([_make_fp("C1", role="R1", nets=["SheetB_NET"])])
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        assert len(placed) == 1
        assert placed[0].ref == "C1"

    def test_explicit_params_sheet_overrides_injected(self):
        """A user-authored params['sheet'] ('Custom') wins over the injected
        effective sheet — {sheet} resolves to 'Custom', not to the inherited
        'Channel_0'."""
        top, cfg = self._build(top_sheet="Channel_0", inner_params={"sheet": "Custom"})
        adapter = _adapter_for([_make_fp("C1", role="R1", nets=["Custom_NET"])])
        calc = ClonePositionCalculator(adapter, cfg)

        placed, _vias, _tracks = calc.compute_raw_positions([top])

        assert len(placed) == 1
        assert placed[0].ref == "C1"
