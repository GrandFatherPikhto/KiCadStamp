#!/usr/bin/env python3
"""
Regression test for two‑phase execution (execute_moves -> adapter.refresh_board() ->
plan_vias -> execute_vias), as in kicadstamp_cli.py:cmd_apply.

REVISED (KiCadStamp, generalised vias, 2026-07-15): previously this test
checked that plan_vias() sees the REAL (re‑read) pad of the component after
the move commit — that was protection against a bug where GND vias were
computed from the old, not‑yet‑moved position. Now vias (at both levels) are
pure geometry computed AT plan_moves() time; no reading of live component pads
for vias is required at all — the problem this test protected against can no
longer arise structurally.

The test now verifies that the two‑phase flow still runs entirely without
errors and produces geometrically correct positions (verified against an
independent calculation).
"""
import sys
import math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2, Angle
from kipy.board_types import Pad, Net
from kicadstamp.domain.geometry import BoardLayer

from kicadstamp.config import (
    Config, ManualSpoke, Cell,
    TemplateVia, TemplateComponentSlot, Rule
)
from kicadstamp.placement.planner import PlacementPlanner
from kicadstamp.placement.executor import BatchExecutor
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.constants import SPOKE_LEVEL_ROLE_PLACEHOLDER

MM = 1_000_000


def _make_pad(number, x_mm, y_mm, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net_name = net_name
    return pad


def test_two_phase_flow_completes_and_via_geometry_is_correct():
    cell = Cell(
        name="t",
        components=[TemplateComponentSlot(
            role="LIGHT",
            offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=0.0,
            vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=0.5, net="GND")],
        )],
    )
    spoke = ManualSpoke(pad="17", cell="t", rotation_deg=0.0)
    cfg = Config(
        layer='B.Cu',
        cells={"t": cell},
        rules=[Rule(net="+3V3", anchor_ref='IC1', spokes=[spoke])],
    )

    ic1 = MagicMock()
    ic1.ref = "IC1"
    pad_pos = Vector2.from_xy(int(50.0 * MM), int(50.0 * MM))
    ic1.definition.items = [_make_pad("17", 50.0, 50.0, "+3V3")]

    c5 = MagicMock()
    c5.ref = "C5"
    c5.position = Vector2.from_xy(0, 0)
    c5.angle_deg = 0.0
    c5.layer = BoardLayer.BL_F_Cu
    c5.definition.items = [_make_pad("1", 0.0, 0.0, "+3V3"), _make_pad("2", 0.0, 0.0, "GND")]
    c5._role = "LIGHT"

    net_gnd = Net(name="GND")
    net_power = Net(name="+3V3")

    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: ic1 if ref == "IC1" else (c5 if ref == "C5" else None)
    adapter.get_footprints.return_value = [ic1, c5]
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in fp.definition.items if p.number == num), None
    )
    adapter.get_footprint_pads.side_effect = lambda fp: list(fp.definition.items)
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    adapter.get_net_by_name.side_effect = lambda name: net_gnd if name == "GND" else (
        net_power if name == "+3V3" else None
    )
    adapter.get_bounding_boxes.return_value = []
    adapter.commit_with_retry.return_value = True
    adapter.get_vias.return_value = []

    planner = PlacementPlanner(adapter, cfg)
    executor = BatchExecutor(adapter, cfg, batch_size=10)

    # The exact order from kicadstamp_cli.py:cmd_apply
    moves = planner.plan_moves()
    assert len(moves) == 1
    executor.execute_moves(moves, check_collisions=False)
    adapter.refresh_board()
    vias = planner.plan_vias()
    executor.execute_vias(vias)

    gnd_vias = [v for v in vias if v.owner_ref == "C5"]
    assert len(gnd_vias) == 1
    via = gnd_vias[0]

    # via — pure geometry from the spoke origin (pad_pos), verified against independent calculation
    expected_offset = rotate_local_offset(0.0, 0.5, 0.0)
    expected_x = pad_pos.x + expected_offset.x
    expected_y = pad_pos.y + expected_offset.y
    assert via.position.x == expected_x
    assert via.position.y == expected_y
    assert via.net_name == "GND"

    # Check that registry_key is filled (important for idempotency)
    assert via.registry_key is not None
    # For component‑level vias the role is LIGHT, not SPOKE_LEVEL
    assert "LIGHT" in via.registry_key
    assert SPOKE_LEVEL_ROLE_PLACEHOLDER not in via.registry_key
