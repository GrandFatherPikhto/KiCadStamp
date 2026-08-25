#!/usr/bin/env python3
"""
Интеграционный тест PlacementPlanner с ClonePlacement (TemplatePlacer)
целиком — включая совместную работу с обычными rules (ManualSpoke) в
одном прогоне.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2, Angle
from kipy.board_types import BoardLayer, Pad, Net

from kicadstamp.domain.board import Footprint

from kicadstamp.config import (
    Config, ClonePlacement, Cell,
    TemplateVia, TemplateComponentSlot, ManualSpoke, Rule
)
from kicadstamp.placement.planner import PlacementPlanner
from kicadstamp.constants import SPOKE_LEVEL_ROLE_PLACEHOLDER

MM = 1_000_000


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
        rules=[],  # НЕТ rules вовсе
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
    assert SPOKE_LEVEL_ROLE_PLACEHOLDER in clone_vias[0].registry_key


def test_rules_and_clone_placements_together():
    """Одновременно и rules (ManualSpoke), и clone_placements — оба потока
    должны отработать в одном plan_moves()/plan_vias(), не мешая друг другу."""
    ic1_pads = [_make_pad("17", 50.0, 50.0, "+3V3")]
    ic1 = _make_fp("IC1")
    ic1.definition.items = ic1_pads

    spoke_tpl = Cell(
        name="cap_single",
        components=[TemplateComponentSlot(role="SOLO", offset_along_mm=1.0, offset_across_mm=0.0)],
    )
    clone_tpl = Cell(
        name="crystal",
        components=[TemplateComponentSlot(role="XTAL", offset_along_mm=0.0, offset_across_mm=0.0)],
    )

    cfg = Config(
        layer='B.Cu',
        cells={"cap_single": spoke_tpl, "crystal": clone_tpl},
        rules=[Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", cell="cap_single")])],
        clone_placements=[ClonePlacement(cluster="xtal1", cell="crystal", xy=(200.0, 0.0))],
    )

    c5 = _make_fp("C5", role="SOLO", nets=["+3V3"])
    y1 = _make_fp("Y1", role="XTAL")

    def get_pad_by_number(fp, num):
        return next((p for p in fp.definition.items if p.number == num), None)

    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: ic1 if ref == "IC1" else None
    adapter.get_pad_by_number.side_effect = get_pad_by_number
    adapter.get_footprints.return_value = [c5]  # для ComponentPool (rules-сторона)
    adapter.get_selected_items.return_value = [y1]  # для clone-стороны (по выделению)
    adapter.get_field_value.side_effect = lambda fp, name: fp._role
    adapter.get_footprint_pads.side_effect = lambda fp: fp.definition.items
    adapter.get_net_by_name.return_value = MagicMock()
    adapter.get_bounding_boxes.return_value = []

    planner = PlacementPlanner(adapter, cfg)
    moves = planner.plan_moves()

    refs = {m.ref for m in moves}
    assert refs == {"C5", "Y1"}, f"оба потока должны дать свои перемещения, получили {refs}"

    # Дополнительно проверим, что via для rules (ManualSpoke) тоже созданы
    # (в этом тесте via нет, но если бы были, их бы проверили)
    vias = planner.plan_vias()
    # Здесь может быть via уровня спицы для cap_single, но мы не добавляли via в шаблон,
    # так что via будут только от clone_placements
    clone_vias = [v for v in vias if v.owner_ref == "xtal1"]
    # Шаблон crystal не содержит via, значит clone_vias должно быть пусто
    assert len(clone_vias) == 0