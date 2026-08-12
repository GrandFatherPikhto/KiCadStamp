#!/usr/bin/env python3
"""Тесты на фатальные предварительные проверки (validation.py), KiCadStamp 4.0."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock

from kicadstamp.config import (
    Config, ManualSpoke, Cell,
    TemplateComponentSlot, TemplateVia, Rule, ClonePlacement
)
from kicadstamp.exceptions import ValidationError
from kicadstamp.validation import (
    check_cells_and_pads_exist,
    check_role_pool_sufficiency,
    check_no_duplicate_clone_anchors,
    check_clone_nets_exist_on_board,
    check_single_selection_based_clone,
)


def _cfg(rules=None, cells=None, clone_placements=None, layer='B.Cu'):
    return Config(
        layer=layer,
        cells=cells or {"t": Cell(name="t", components=[
            TemplateComponentSlot(role="HEAVY"), TemplateComponentSlot(role="LIGHT")
        ])},
        rules=rules or [],
        clone_placements=clone_placements or [],
    )


def _make_pad(number):
    pad = MagicMock()
    pad.number = number
    return pad


def _adapter_with_pads(pad_numbers):
    ic1 = MagicMock()
    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: ic1 if ref == "IC1" else None
    pads = {n: _make_pad(n) for n in pad_numbers}
    adapter.get_pad_by_number.side_effect = lambda fp, num: pads.get(num)
    return adapter


class TestCellsAndPadsExist:
    def test_valid_config_passes(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", cell="t")])])
        adapter = _adapter_with_pads(["17"])
        check_cells_and_pads_exist(adapter, cfg)

    def test_unknown_cell_raises(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", cell="does_not_exist")])])
        adapter = _adapter_with_pads(["17"])
        with pytest.raises(ValidationError, match="does_not_exist"):
            check_cells_and_pads_exist(adapter, cfg)

    def test_unknown_pad_raises(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="999", cell="t")])])
        adapter = _adapter_with_pads(["17"])
        with pytest.raises(ValidationError, match="999"):
            check_cells_and_pads_exist(adapter, cfg)

    def test_target_ref_not_found_raises(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", cell="t")])])
        adapter = MagicMock()
        adapter.get_footprint.return_value = None
        with pytest.raises(ValidationError, match="IC1"):
            check_cells_and_pads_exist(adapter, cfg)

    def test_retired_spoke_not_checked(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[
            ManualSpoke(pad="999", cell="does_not_exist", retired=True)
        ])])
        adapter = _adapter_with_pads(["17"])
        check_cells_and_pads_exist(adapter, cfg)


class TestRolePoolSufficiency:
    def _adapter_with_pool(self, components):
        fps = []
        for ref, role, net_name in components:
            fp = MagicMock()
            fp.reference_field.text.value = ref
            pad = MagicMock()
            pad.net.name = net_name
            fp._pads = [pad]
            fp._role = role
            fps.append(fp)
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = lambda fp: fp._pads
        return adapter

    def test_sufficient_pool_passes(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[
            ManualSpoke(pad="17", cell="t"),
            ManualSpoke(pad="26", cell="t"),
        ])])
        adapter = self._adapter_with_pool([
            ("C5", "LIGHT", "+3V3"), ("C6", "LIGHT", "+3V3"),
            ("C30", "HEAVY", "+3V3"), ("C31", "HEAVY", "+3V3"),
        ])
        check_role_pool_sufficiency(adapter, cfg)

    def test_insufficient_pool_raises_with_exact_counts(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[
            ManualSpoke(pad="17", cell="t"),
            ManualSpoke(pad="26", cell="t"),
        ])])
        adapter = self._adapter_with_pool([
            ("C5", "LIGHT", "+3V3"), ("C6", "LIGHT", "+3V3"),
            ("C30", "HEAVY", "+3V3"),
        ])
        with pytest.raises(ValidationError, match="HEAVY"):
            check_role_pool_sufficiency(adapter, cfg)

    def test_wrong_net_component_not_counted(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", cell="t")])])
        adapter = self._adapter_with_pool([
            ("C5", "LIGHT", "+3V3"),
            ("C30", "HEAVY", "+1V2_VCCINT"),
        ])
        with pytest.raises(ValidationError, match="HEAVY"):
            check_role_pool_sufficiency(adapter, cfg)

    def test_multiple_rules_checked_independently(self):
        cell = Cell(name="t", components=[TemplateComponentSlot(role="HEAVY")])
        cfg = _cfg(
            [
                Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", cell="t")]),
                Rule(net="+1V2", anchor_ref='IC1', spokes=[ManualSpoke(pad="40", cell="t")]),
            ],
            cells={"t": cell},
        )
        adapter = self._adapter_with_pool([
            ("C30", "HEAVY", "+3V3"), ("C31", "HEAVY", "+3V3"),
        ])
        with pytest.raises(ValidationError, match="\\+1V2"):
            check_role_pool_sufficiency(adapter, cfg)


class TestNoDuplicateCloneAnchors:
    def test_no_duplicates_passes(self):
        clones = [
            ClonePlacement(name="a", cell="t", xy=(0, 0),
                           anchor_ref="IC1", anchor_pad="17"),
            ClonePlacement(name="b", cell="t", xy=(0, 0),
                           anchor_ref="IC1", anchor_pad="18"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        check_no_duplicate_clone_anchors(cfg)

    def test_duplicate_anchor_raises(self):
        clones = [
            ClonePlacement(name="a", cell="t", xy=(0, 0),
                           anchor_ref="IC1", anchor_pad="17"),
            ClonePlacement(name="b", cell="t", xy=(0, 0),
                           anchor_ref="IC1", anchor_pad="17"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        with pytest.raises(ValidationError, match="b.*a"):
            check_no_duplicate_clone_anchors(cfg)

    def test_duplicate_role_anchor_raises(self):
        clones = [
            ClonePlacement(name="a", cell="t", xy=(0, 0),
                           anchor_role="MASTER", anchor_sheet="Sheet1"),
            ClonePlacement(name="b", cell="t", xy=(0, 0),
                           anchor_role="MASTER", anchor_sheet="Sheet1"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        with pytest.raises(ValidationError, match="b.*a"):
            check_no_duplicate_clone_anchors(cfg)

    def test_duplicate_name_raises(self):
        clones = [
            ClonePlacement(name="a", cell="t", xy=(0, 0)),
            ClonePlacement(name="a", cell="t", xy=(0, 0)),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        with pytest.raises(ValidationError, match="a"):
            check_no_duplicate_clone_anchors(cfg)

    def test_same_anchor_different_origin_is_not_a_duplicate(self):
        """Regression (found 2026-07-27): two clones legitimately sharing one
        physical anchor (e.g. a connector pad) but offset to opposite sides
        via xy must NOT be flagged — this must match
        clone_anchor_id's identity exactly, or the registry and this check
        disagree on what counts as a duplicate."""
        clones = [
            ClonePlacement(name="p5v", cell="t", xy=(7.0, -6.0),
                           anchor_role="CONN_PM5V", anchor_pad="1"),
            ClonePlacement(name="n5v", cell="t", xy=(7.0, 6.0),
                           anchor_role="CONN_PM5V", anchor_pad="1"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        check_no_duplicate_clone_anchors(cfg)

    def test_same_anchor_same_origin_role_based_raises(self):
        clones = [
            ClonePlacement(name="a", cell="t", xy=(1.0, 2.0),
                           anchor_role="CONN_PM5V", anchor_pad="1"),
            ClonePlacement(name="b", cell="t", xy=(1.0, 2.0),
                           anchor_role="CONN_PM5V", anchor_pad="1"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        with pytest.raises(ValidationError, match="b.*a"):
            check_no_duplicate_clone_anchors(cfg)

    def test_same_anchor_different_cluster_is_not_a_duplicate(self):
        """Regression (found 2026-07-28): p5v_led_spoke/n5v_led_spoke share
        identical anchor_role/anchor_sheet/anchor_pad/origin and differ ONLY
        by anchor_cluster (Pos vs Neg, the field that actually picks which
        physical component the anchor resolves to) — must NOT be flagged,
        must match clone_anchor_id's identity exactly."""
        clones = [
            ClonePlacement(name="p5v_led", cell="t", xy=(3.0, 0.0),
                           anchor_role="C_OUT_BYPASS", anchor_pad="1",
                           anchor_cluster="In_Pi_Filter_Pos"),
            ClonePlacement(name="n5v_led", cell="t", xy=(3.0, 0.0),
                           anchor_role="C_OUT_BYPASS", anchor_pad="1",
                           anchor_cluster="In_Pi_Filter_Neg"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        check_no_duplicate_clone_anchors(cfg)

    def test_same_anchor_same_cluster_role_based_raises(self):
        clones = [
            ClonePlacement(name="a", cell="t", xy=(3.0, 0.0),
                           anchor_role="C_OUT_BYPASS", anchor_pad="1",
                           anchor_cluster="In_Pi_Filter_Pos"),
            ClonePlacement(name="b", cell="t", xy=(3.0, 0.0),
                           anchor_role="C_OUT_BYPASS", anchor_pad="1",
                           anchor_cluster="In_Pi_Filter_Pos"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        with pytest.raises(ValidationError, match="b.*a"):
            check_no_duplicate_clone_anchors(cfg)

    def test_duplicate_point_anchor_raises(self):
        """Found 2026-08-06: anchor_point had no branch here at all, same gap
        as clone_anchor_id — two clones on the same Point with the same
        offset went completely unnoticed."""
        clones = [
            ClonePlacement(name="a", cell="t", xy=(4.0, -110.0), anchor_point="Origin"),
            ClonePlacement(name="b", cell="t", xy=(4.0, -110.0), anchor_point="Origin"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        with pytest.raises(ValidationError, match="b.*a"):
            check_no_duplicate_clone_anchors(cfg)

    def test_same_point_different_origin_is_not_a_duplicate(self):
        clones = [
            ClonePlacement(name="a", cell="t", xy=(4.0, -110.0), anchor_point="Origin"),
            ClonePlacement(name="b", cell="t", xy=(8.0, -69.0), anchor_point="Origin"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        check_no_duplicate_clone_anchors(cfg)

    def test_same_anchor_different_polar_radius_is_not_a_duplicate(self):
        """Polar clones (radius_mm/angle_deg) on the same anchor with
        different radii are distinct — their xy is the loader default (0,0),
        so this check must use the polar offset, not xy, to match
        clone_anchor_id's identity."""
        clones = [
            ClonePlacement(name="a", cell="t", xy=(0, 0),
                           radius_mm=5.0, angle_deg=0.0,
                           anchor_role="CONN_PM5V", anchor_pad="1"),
            ClonePlacement(name="b", cell="t", xy=(0, 0),
                           radius_mm=7.0, angle_deg=0.0,
                           anchor_role="CONN_PM5V", anchor_pad="1"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        check_no_duplicate_clone_anchors(cfg)

    def test_same_anchor_same_polar_radius_raises(self):
        clones = [
            ClonePlacement(name="a", cell="t", xy=(0, 0),
                           radius_mm=5.0, angle_deg=0.0,
                           anchor_role="CONN_PM5V", anchor_pad="1"),
            ClonePlacement(name="b", cell="t", xy=(0, 0),
                           radius_mm=5.0, angle_deg=0.0,
                           anchor_role="CONN_PM5V", anchor_pad="1"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        with pytest.raises(ValidationError, match="b.*a"):
            check_no_duplicate_clone_anchors(cfg)


class TestCloneNetsExistOnBoard:
    def _make_net_mock(self, name):
        net = MagicMock()
        net.name = name
        return net

    def test_valid_nets_passes(self):
        tpl = Cell(name="t", vias=[TemplateVia(net="GND")])
        clone = ClonePlacement(name="c", cell="t", xy=(0, 0))
        cfg = _cfg(rules=[], cells={"t": tpl}, clone_placements=[clone])
        adapter = MagicMock()
        adapter.get_all_nets.return_value = [self._make_net_mock("GND")]
        check_clone_nets_exist_on_board(adapter, cfg)  # не должно бросить

    def test_missing_net_raises(self):
        tpl = Cell(name="t", vias=[TemplateVia(net="NON_EXISTENT")])
        clone = ClonePlacement(name="c", cell="t", xy=(0, 0))
        cfg = _cfg(rules=[], cells={"t": tpl}, clone_placements=[clone])
        adapter = MagicMock()
        adapter.get_all_nets.return_value = [self._make_net_mock("GND")]
        with pytest.raises(ValidationError, match="NON_EXISTENT"):
            check_clone_nets_exist_on_board(adapter, cfg)

    def test_via_in_component_slot_checked(self):
        tpl = Cell(name="t", components=[
            TemplateComponentSlot(role="X", vias=[TemplateVia(net="VCC")])
        ])
        clone = ClonePlacement(name="c", cell="t", xy=(0, 0))
        cfg = _cfg(rules=[], cells={"t": tpl}, clone_placements=[clone])
        adapter = MagicMock()
        adapter.get_all_nets.return_value = [self._make_net_mock("GND")]
        with pytest.raises(ValidationError, match="VCC"):
            check_clone_nets_exist_on_board(adapter, cfg)


class TestSingleSelectionBasedClone:
    def test_single_selection_passes(self):
        cfg = _cfg(rules=[], clone_placements=[
            ClonePlacement(name="a", cell="t", xy=(0, 0))
        ])
        check_single_selection_based_clone(cfg)

    def test_two_selection_based_raises(self):
        cfg = _cfg(rules=[], clone_placements=[
            ClonePlacement(name="a", cell="t", xy=(0, 0)),
            ClonePlacement(name="b", cell="t", xy=(0, 0)),
        ])
        with pytest.raises(ValidationError, match="a.*b"):
            check_single_selection_based_clone(cfg)

    def test_mixed_modes_passes(self):
        cfg = _cfg(rules=[], clone_placements=[
            ClonePlacement(name="a", cell="t", xy=(0, 0)),
            ClonePlacement(name="b", cell="t", xy=(0, 0),
                           nets={"X": "GND"}),
        ])
        check_single_selection_based_clone(cfg)