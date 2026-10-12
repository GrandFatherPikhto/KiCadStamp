#!/usr/bin/env python3
"""
Интеграционный тест PlacementPlanner с ClonePlacement (TemplatePlacer) целиком.
"""

import pytest
from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2, Angle
from kipy.board_types import Pad, Net
from kicadstamp.domain.geometry import BoardLayer

from kicadstamp.domain.board import Footprint

from kicadstamp.config import (
    Config, ClonePlacement, Cell,
    TemplateVia, TemplateComponentSlot
)
from kicadstamp.placement.planner import PlacementPlanner
from kicadstamp.constants import CELL_LEVEL_ROLE_PLACEHOLDER
from tests.fakes.format3 import stamp_config

MM = 1_000_000


@pytest.fixture(autouse=True)
def _stamp_configs(monkeypatch):
    """У3.5 К3: the hand-built Configs of this module carry no uuid; stamp them
    at the ONE entry point — the planner (uuid-помощник, not pin). No effect
    under CURRENT_FORMAT = 2."""
    original = PlacementPlanner.__init__

    def _init(self, adapter, cfg, target_ref=None, *args, **kwargs):
        stamp_config(cfg)
        original(self, adapter, cfg, target_ref, *args, **kwargs)

    monkeypatch.setattr(PlacementPlanner, "__init__", _init)


def _make_pad(number, x_mm, y_mm, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net_name = net_name
    return pad


def _make_fp(ref, role=None, nets=None):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp.definition = MagicMock()
    fp.definition.items = [_make_pad("1", 0, 0, n) for n in (nets or [])]
    return fp


def test_clone_placements_only_via_selection():
    """Конфиг вообще без rules — только clone_placements, сопоставление по выделению."""
    ic1 = _make_fp("IC1")  # нужен только для target_ref в конструкторе PlacementPlanner

    tpl = Cell(
        name="crystal",
        vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=-1.0, net="GND")],
        components=[
            TemplateComponentSlot(role="XTAL", offset_along_mm=0.0, offset_across_mm=0.0, angle_deg=0.0),
            TemplateComponentSlot(role="LOAD_CAP", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=90.0),
        ],
    )
    clone = ClonePlacement(cluster="crystal2", cell="crystal", xy=(100.0, 50.0),
                          rotation_deg=0.0)
    cfg = Config(
        layer='B.Cu',
        cells={"crystal": tpl},
        clone_placements=[clone],
    )

    y3 = _make_fp("Y3", role="XTAL")
    c20 = _make_fp("C20", role="LOAD_CAP")

    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: ic1 if ref == "IC1" else None
    adapter.get_selected_items.return_value = [y3, c20]
    adapter.get_field_value.side_effect = lambda fp, name: fp._role
    adapter.get_net_by_name.return_value = MagicMock()
    adapter.get_bounding_boxes.return_value = []
    adapter.get_footprint_pads.return_value = []

    planner = PlacementPlanner(adapter, cfg)
    moves = planner.plan_moves()

    assert len(moves) == 2
    refs = {m.ref for m in moves}
    assert refs == {"Y3", "C20"}

    y3_move = next(m for m in moves if m.ref == "Y3")
    assert y3_move.position.x == int(100.0 * MM)
    assert y3_move.position.y == int(50.0 * MM)

    vias = planner.plan_vias()
    clone_vias = [v for v in vias if v.owner_ref == "crystal2"]
    assert len(clone_vias) == 1
    assert clone_vias[0].net_name == "GND"
    # Проверяем, что registry_key заполнен (важно для идемпотентности)
    assert clone_vias[0].registry_key is not None
    assert CELL_LEVEL_ROLE_PLACEHOLDER in clone_vias[0].registry_key
