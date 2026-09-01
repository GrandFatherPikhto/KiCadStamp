#!/usr/bin/env python3
"""
Task B regression: ViaPlanner keepout must see sibling regular vias planned in
the same plan_vias() run.

Bug (found 2026-07-31): _build_keepout() only considered pad/component bounding
boxes, not the planned_vias list, so a thermal via could land exactly ON TOP of
a regular spoke/component via planned earlier in the same run. The fix adds the
planned vias to the keepout as circular obstacles
(kicadstamp/placement/services/via_planner.py:_build_keepout).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2, Angle
from kipy.board_types import Pad

from kicadstamp.config import Config, ThermalViaArrayConfig
from kicadstamp.placement.commands import ViaCommand
from kicadstamp.placement.services.via_planner import ViaPlanner

MM = 1_000_000


def _make_thermal_pad(number="1", x_mm=0.0, y_mm=0.0, size_mm=4.0):
    """Pad with a size_mm x size_mm copper layer centred at (x_mm, y_mm),
    angle 0. A 1x1 grid puts the single ideal point at the pad centre; the
    copper is large enough for the free-point search around a blocked point."""
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.size = Vector2.from_xy(int(size_mm * MM), int(size_mm * MM))
    pad.angle_rad = 0.0
    return pad


def _make_anchor_fp(pad):
    fp = MagicMock()
    fp.ref = "Q1"
    return fp


def _make_adapter(fp, pad):
    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: fp if ref == "Q1" else None
    adapter.get_footprint_pads.return_value = [pad]
    adapter.get_pad_by_number.side_effect = lambda _fp, num: pad if num == "1" else None
    adapter.get_bounding_boxes.return_value = []
    return adapter


def _make_cfg():
    tva = ThermalViaArrayConfig(
        name="q1_thermal",
        pad="1",
        anchor_ref="Q1",
        net="GND",
        rows=1,
        cols=1,
        margin_mm=0.0,
        pattern="grid",
        drill_mm=0.3,
        diameter_mm=0.6,
    )
    return Config(
        layer='B.Cu',
        cells={},
        thermal_via_arrays=[tva],
        chains=[],
        clone_placements=[],
        skip_existing_components=False,
        via_keepout_clearance_mm=0.2,
        via_search_step_mm=0.1,
        via_search_max_radius_mm=5.0,
        via_search_n_directions=8,
    )


def _thermal_vias(vias):
    """Thermal vias are the ones with a registry_key; planned_vias passed in by
    the caller have registry_key=None and are filtered out here."""
    return [v for v in vias if v.registry_key is not None]


class TestViaPlannerPlannedViaKeepout:
    def test_thermal_via_placed_at_ideal_point_when_no_planned_via_blocks(self):
        """Control: with no sibling via, the 1x1 grid thermal via lands exactly
        on the ideal grid point (the pad centre)."""
        pad = _make_thermal_pad()
        fp = _make_anchor_fp(pad)
        adapter = _make_adapter(fp, pad)
        planner = ViaPlanner(adapter, _make_cfg())

        vias = planner.plan_vias(planned_components=[], planned_vias=[])
        thermal = _thermal_vias(vias)

        assert len(thermal) == 1
        # ideal grid point for rows=1, cols=1 is the pad centre (0, 0)
        assert abs(thermal[0].position.x) <= ViaPlanner._VIA_POSITION_TOLERANCE_NM
        assert abs(thermal[0].position.y) <= ViaPlanner._VIA_POSITION_TOLERANCE_NM

    def test_thermal_via_not_placed_on_planned_sibling_via(self):
        """Task B regression: a regular via planned earlier in the same run sits
        exactly at the thermal-grid ideal point. The thermal via must NOT be
        placed on top of it — the keepout built from planned_vias pushes it away
        to a free spot on the same pad."""
        pad = _make_thermal_pad()
        fp = _make_anchor_fp(pad)
        adapter = _make_adapter(fp, pad)
        planner = ViaPlanner(adapter, _make_cfg())

        sibling = ViaCommand(
            position=Vector2.from_xy(0, 0),  # exactly the thermal-grid ideal point
            drill_mm=0.3,
            diameter_mm=0.6,
            net_name="GND",
            owner_ref="Q1",
        )
        vias = planner.plan_vias(planned_components=[], planned_vias=[sibling])
        thermal = _thermal_vias(vias)

        # A free spot exists nearby on the 4x4 mm pad, so the thermal via is
        # still planned — but it must not sit on top of the sibling via.
        assert len(thermal) == 1
        assert abs(thermal[0].position.x) > ViaPlanner._VIA_POSITION_TOLERANCE_NM or \
               abs(thermal[0].position.y) > ViaPlanner._VIA_POSITION_TOLERANCE_NM


class TestMultipleThermalViaArrays:
    """2026-08-02: thermal_via_arrays generalized from a single field to a
    real list (the AD9707-per-channel motivating case) — plan_vias() must
    plan every active entry, not just the first/only one, and each entry's
    keepout must see vias already planned by an EARLIER entry in the same
    call (same discipline test_thermal_via_not_placed_on_planned_sibling_via
    above already proves for a plain sibling via)."""

    def _cfg_two_targets(self, tva1, tva2):
        return Config(
            layer='B.Cu', cells={}, thermal_via_arrays=[tva1, tva2],
            chains=[], clone_placements=[], skip_existing_components=False,
            via_keepout_clearance_mm=0.2, via_search_step_mm=0.1,
            via_search_max_radius_mm=5.0, via_search_n_directions=8,
        )

    def test_both_active_arrays_get_a_thermal_via_each(self):
        pad1 = _make_thermal_pad(x_mm=0.0)
        pad2 = _make_thermal_pad(x_mm=20.0)
        fp1, fp2 = MagicMock(), MagicMock()
        fp1.ref = "Q1"
        fp2.ref = "Q2"
        adapter = MagicMock()
        adapter.get_footprint.side_effect = lambda ref: {"Q1": fp1, "Q2": fp2}.get(ref)
        adapter.get_footprint_pads.side_effect = lambda fp: (
            [pad1] if fp is fp1 else [pad2])
        adapter.get_pad_by_number.side_effect = lambda fp, num: (
            pad1 if fp is fp1 and num == "1" else pad2 if fp is fp2 and num == "1" else None)
        adapter.get_bounding_boxes.return_value = []

        tva1 = ThermalViaArrayConfig(name="q1_thermal", pad="1", anchor_ref="Q1", net="GND",
                                     rows=1, cols=1, margin_mm=0.0, pattern="grid",
                                     drill_mm=0.3, diameter_mm=0.6)
        tva2 = ThermalViaArrayConfig(name="q2_thermal", pad="1", anchor_ref="Q2", net="GND",
                                     rows=1, cols=1, margin_mm=0.0, pattern="grid",
                                     drill_mm=0.3, diameter_mm=0.6)
        planner = ViaPlanner(adapter, self._cfg_two_targets(tva1, tva2))

        vias = planner.plan_vias(planned_components=[], planned_vias=[])
        thermal = _thermal_vias(vias)

        assert len(thermal) == 2
        registry_names = {v.registry_key.split("|")[0] for v in thermal}
        assert registry_names == {"thermal:q1_thermal", "thermal:q2_thermal"}

    def test_retired_array_contributes_no_via_the_other_still_does(self):
        pad1 = _make_thermal_pad(x_mm=0.0)
        pad2 = _make_thermal_pad(x_mm=20.0)
        fp1, fp2 = MagicMock(), MagicMock()
        fp1.ref = "Q1"
        fp2.ref = "Q2"
        adapter = MagicMock()
        adapter.get_footprint.side_effect = lambda ref: {"Q1": fp1, "Q2": fp2}.get(ref)
        adapter.get_footprint_pads.side_effect = lambda fp: (
            [pad1] if fp is fp1 else [pad2])
        adapter.get_pad_by_number.side_effect = lambda fp, num: (
            pad1 if fp is fp1 and num == "1" else pad2 if fp is fp2 and num == "1" else None)
        adapter.get_bounding_boxes.return_value = []

        tva1 = ThermalViaArrayConfig(name="q1_thermal", pad="1", anchor_ref="Q1", net="GND",
                                     rows=1, cols=1, margin_mm=0.0, pattern="grid",
                                     drill_mm=0.3, diameter_mm=0.6, retired=True)
        tva2 = ThermalViaArrayConfig(name="q2_thermal", pad="1", anchor_ref="Q2", net="GND",
                                     rows=1, cols=1, margin_mm=0.0, pattern="grid",
                                     drill_mm=0.3, diameter_mm=0.6)
        planner = ViaPlanner(adapter, self._cfg_two_targets(tva1, tva2))

        vias = planner.plan_vias(planned_components=[], planned_vias=[])
        thermal = _thermal_vias(vias)

        assert len(thermal) == 1
        assert thermal[0].registry_key.startswith("thermal:q2_thermal")
