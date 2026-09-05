#!/usr/bin/env python3
"""Tests for manual_position_calculator.py — ManualSpoke component layer
resolution must match ClonePlacement's convention (own slot layer, else
cell layer), same as tracks already did (spoke_layout._resolve_track).
Found live: cells/fpga_cap_pair_spoke.yaml's layer: B.Cu was honoured
for its tracks but silently ignored for its components — PlacedComponentInfo
was always built with layer=None (inherit PlacementPlanner's single global
target_layer), regardless of what the cell declared."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2
from kipy.board_types import Pad, FootprintInstance
from kicadstamp.domain.geometry import BoardLayer

from kicadstamp.config import (Config, Rule, ManualSpoke, Cell, TemplateComponentSlot,
                               TemplateVia, TemplateTrack)
from kicadstamp.placement.services.manual_position_calculator import ManualPositionCalculator

MM = 1_000_000


def _make_pad(number, net_name, x_mm=0.0, y_mm=0.0):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.net_name = net_name
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    return pad


def _make_fp(ref, role=None, nets=()):
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = ref
    fp._role = role
    fp._pads = [_make_pad("1", n) for n in nets]
    return fp


def _adapter_for(anchor_fp, comp_fp, anchor_pad):
    all_fps = [anchor_fp, comp_fp]
    adapter = MagicMock()
    adapter.get_footprints.return_value = all_fps
    adapter.get_footprint.side_effect = lambda ref: next(
        (fp for fp in all_fps if fp.ref == ref), None)
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None
    adapter.get_footprint_pads.side_effect = lambda fp: list(getattr(fp, "_pads", []))
    adapter.get_pad_by_number.return_value = anchor_pad
    adapter.get_selected_items.return_value = []
    return adapter


def _run(cell, cfg_layer="F.Cu"):
    anchor_fp = _make_fp("IC1")
    comp_fp = _make_fp("C1", role="R1", nets=["NET1"])
    anchor_pad = _make_pad("1", "NET1")
    adapter = _adapter_for(anchor_fp, comp_fp, anchor_pad)

    rule = Rule(net="NET1", anchor_ref="IC1", spokes=[ManualSpoke(pad="1", cell="tpl")])
    cfg = Config(layer=cfg_layer, cells={"tpl": cell}, chains=[rule])

    calc = ManualPositionCalculator(adapter, cfg)
    placed, _vias, _tracks = calc.compute_raw_positions([rule])
    assert len(placed) == 1
    return placed[0]


def test_component_layer_inherits_template_layer_when_slot_has_no_override():
    cell = Cell(
        name="tpl", layer="B.Cu",
        components=[TemplateComponentSlot(role="R1", offset_along_mm=0.0,
                                          offset_across_mm=0.0, angle_deg=0.0)],
    )
    placed = _run(cell, cfg_layer="F.Cu")
    assert placed.layer == BoardLayer.BL_B_Cu


def test_component_slot_layer_overrides_template_layer():
    cell = Cell(
        name="tpl", layer="B.Cu",
        components=[TemplateComponentSlot(role="R1", offset_along_mm=0.0,
                                          offset_across_mm=0.0, angle_deg=0.0,
                                          layer="F.Cu")],
    )
    placed = _run(cell, cfg_layer="B.Cu")
    assert placed.layer == BoardLayer.BL_F_Cu


def test_component_layer_is_template_default_front(tmp_path=None):
    cell = Cell(
        name="tpl", layer="F.Cu",
        components=[TemplateComponentSlot(role="R1", offset_along_mm=0.0,
                                          offset_across_mm=0.0, angle_deg=0.0)],
    )
    placed = _run(cell, cfg_layer="B.Cu")
    assert placed.layer == BoardLayer.BL_F_Cu


def test_rule_override_replaces_anchor_position():
    """Bug #5 unit gate: a tree rigid-redraw PositionOverride REPLACES a
    rule's anchor entirely — the spoke geometry lands on the override
    position, NOT on the anchor footprint's own live pad position. Previously
    compute_raw_positions() had no position_overrides parameter at all, so the
    override was silently dropped and the rule resolved through its own anchor
    (here: IC1 at (10, 20) with its pad at (11, 20))."""
    from kicadstamp.tree_position import PositionOverride

    cell = Cell(
        name="tpl", layer="F.Cu",
        components=[TemplateComponentSlot(role="R1", offset_along_mm=0.0,
                                          offset_across_mm=0.0, angle_deg=0.0)],
    )
    anchor_fp = _make_fp("IC1")
    anchor_fp.position = Vector2.from_xy(int(10.0 * MM), int(20.0 * MM))
    anchor_fp.angle_deg = 0.0
    comp_fp = _make_fp("C1", role="R1", nets=["NET1"])
    # Anchor pad 1mm to the +x of IC1's origin -> live at (11, 20).
    anchor_pad = _make_pad("1", "NET1", x_mm=11.0, y_mm=20.0)
    adapter = _adapter_for(anchor_fp, comp_fp, anchor_pad)

    rule = Rule(net="NET1", anchor_ref="IC1",
                spokes=[ManualSpoke(pad="1", cell="tpl")])
    cfg = Config(layer="F.Cu", cells={"tpl": cell}, chains=[rule])
    calc = ManualPositionCalculator(adapter, cfg)

    # Without an override the rule lands on its own anchor's live pad (11, 20).
    placed, _v, _t = calc.compute_raw_positions([rule])
    assert placed[0].dest.x == int(11.0 * MM)
    assert placed[0].dest.y == int(20.0 * MM)

    # With the override (rule effective name == net "NET1") the anchor
    # footprint's ORIGIN is treated as sitting at (50, 60) — the spoke pad,
    # at its +x 1mm LOCAL offset from that origin, lands at (51, 60), NOT on
    # the anchor's own live pad (11, 20). (51, 60) proves the override origin
    # (not the anchor's own (10, 20)) was used.
    override = PositionOverride(position=Vector2.from_xy(int(50.0 * MM), int(60.0 * MM)),
                                rotation_deg=0.0)
    placed, _v, _t = calc.compute_raw_positions([rule], position_overrides={"NET1": override})
    assert len(placed) == 1
    assert placed[0].dest.x == int(51.0 * MM)
    assert placed[0].dest.y == int(60.0 * MM)


def test_rule_override_reprojects_pad_rotation():
    """The override's rotation_deg rotates the spoke geometry with it: the
    anchor pad's local +x offset (1, 0) re-projects to (0, -1) under rotation
    90 (KiCad Y-down) — the spoke lands 1mm to -y of the override origin,
    not at the footprint's live pad."""
    from kicadstamp.tree_position import PositionOverride

    cell = Cell(
        name="tpl", layer="F.Cu",
        components=[TemplateComponentSlot(role="R1", offset_along_mm=0.0,
                                          offset_across_mm=0.0, angle_deg=0.0)],
    )
    anchor_fp = _make_fp("IC1")
    anchor_fp.position = Vector2.from_xy(int(10.0 * MM), int(20.0 * MM))
    anchor_fp.angle_deg = 0.0
    comp_fp = _make_fp("C1", role="R1", nets=["NET1"])
    anchor_pad = _make_pad("1", "NET1", x_mm=11.0, y_mm=20.0)
    adapter = _adapter_for(anchor_fp, comp_fp, anchor_pad)

    rule = Rule(net="NET1", anchor_ref="IC1",
                spokes=[ManualSpoke(pad="1", cell="tpl")])
    cfg = Config(layer="F.Cu", cells={"tpl": cell}, chains=[rule])
    calc = ManualPositionCalculator(adapter, cfg)

    override = PositionOverride(position=Vector2.from_xy(int(50.0 * MM), int(60.0 * MM)),
                                rotation_deg=90.0)
    placed, _v, _t = calc.compute_raw_positions([rule], position_overrides={"NET1": override})
    assert len(placed) == 1
    # local (1, 0) rotated by 90 -> (0, -1); origin (50, 60) -> (50, 59).
    assert placed[0].dest.x == int(50.0 * MM)
    assert placed[0].dest.y == int(59.0 * MM)


# ---- Bug 3 spoke-path fix (2026-09-05): net_from_role / net_from_role_pad ----
def _make_fp_with_pads(ref, pad_specs, role=None):
    """Footprint with connected pads, each spec (number, net_name) — mirrors
    the bypass-cap topology '1' on the rail net, '2' on GND."""
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = ref
    fp._role = role
    fp._pads = [_make_pad(num, net) for num, net in pad_specs]
    fp._pads_by_num = {p.number: p for p in fp._pads}
    return fp


def _adapter_with_pads(anchor_fp, comp_fp):
    all_fps = [anchor_fp, comp_fp]
    adapter = MagicMock()
    adapter.get_footprints.return_value = all_fps
    adapter.get_footprint.side_effect = lambda ref: next(
        (fp for fp in all_fps if fp.ref == ref), None)
    adapter.get_field_value.side_effect = (
        lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None)
    adapter.get_footprint_pads.side_effect = lambda fp: list(getattr(fp, "_pads", []))
    adapter.get_pad_by_number.side_effect = (
        lambda fp, num: getattr(fp, "_pads_by_num", {}).get(str(num)))
    adapter.get_selected_items.return_value = []
    return adapter


def _bypass_cell():
    """Bypass-cap spoke cell — the exact fpga_pwr_bank-style topology that
    triggered the Bug 3 GND-duplication: cell-level rail via (pad 1), cell-level
    GND via (pad 2), a GND track and a slot GND via, all via net_from_role."""
    return Cell(
        name="bypass_spoke",
        vias=[
            TemplateVia(offset_along_mm=0.0, offset_across_mm=-1.5,
                        net_from_role="BYPASS", net_from_role_pad="1"),
            TemplateVia(offset_along_mm=0.0, offset_across_mm=-2.0,
                        net_from_role="BYPASS", net_from_role_pad="2"),
        ],
        tracks=[
            TemplateTrack(start_along_mm=0.0, start_across_mm=-2.0,
                          end_along_mm=1.0, end_across_mm=-2.0,
                          net_from_role="BYPASS", net_from_role_pad="2"),
        ],
        components=[
            TemplateComponentSlot(role="BYPASS", offset_along_mm=1.0, offset_across_mm=-1.0,
                                  angle_deg=0.0,
                                  vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=-0.5,
                                                    net_from_role="BYPASS", net_from_role_pad="2")]),
        ],
    )


def test_spoke_net_from_role_pad2_vias_planned_as_gnd_not_rail():
    """Bug 3 spoke-path fix end-to-end through the ManualSpoke planner: the
    bypass-cell net_from_role vias/tracks are resolved from the role's REAL pad
    nets BEFORE geometry, so the pad-2 (GND) items are planned as GND, NOT as
    the chain rail. Before the fix every via/track fell back to `net or
    chain.net` (the rail) — the registry stored the rail net, live copper was
    GND, adopt/pre-check honestly refused and a duplicate GND copy appeared on
    a first/empty-registry redraw.

    Chain net = rail +2V5_VCCA; anchor IC1 pad '1'; pool component C1
    (Role=BYPASS) with pad '1'=+2V5_VCCA and pad '2'=GND."""
    rail = "+2V5_VCCA"
    anchor_fp = _make_fp_with_pads("IC1", [("1", rail)])
    comp_fp = _make_fp_with_pads("C1", [("1", rail), ("2", "GND")], role="BYPASS")
    adapter = _adapter_with_pads(anchor_fp, comp_fp)

    rule = Rule(net=rail, anchor_ref="IC1",
                spokes=[ManualSpoke(pad="1", cell="bypass_spoke")])
    cfg = Config(layer="F.Cu", cells={"bypass_spoke": _bypass_cell()}, chains=[rule])
    calc = ManualPositionCalculator(adapter, cfg)

    placed, vias, tracks = calc.compute_raw_positions([rule])
    assert [p.ref for p in placed] == ["C1"]
    # cell-level vias: pad1 -> rail, pad2 -> GND; then the slot's pad2 -> GND.
    assert [v.net_name for v in vias] == [rail, "GND", "GND"]
    # cell-level track with net_from_role pad2 -> GND (was the rail before the fix).
    assert [t.net_name for t in tracks] == ["GND"]
