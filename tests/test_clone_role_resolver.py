#!/usr/bin/env python3
"""Tests for clone_role_resolver.py — role‑to‑ref mapping for TemplatePlacer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kicadstamp.domain.geometry import BoardLayer
from kicadstamp.domain.geometry import Vector2

from kicadstamp.domain.board import Footprint

from kicadstamp.config import Cell, TemplateComponentSlot, ClonePlacement
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.placement.services.clone_role_resolver import (
    resolve_roles_by_selection, resolve_roles_by_nets, resolve_anchor_by_role,
    candidate_nets_by_role, resolve_single_role_candidate,
    suggest_role_nets_from_cluster,
    _prefix_remap_local_net, _target_channel,
)
from kicadstamp.exceptions import ValidationError


def _make_fp(ref, role=None, nets=None, cluster=None):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._nets = nets or []
    fp._cluster = cluster
    return fp


def _role_or_cluster(fp, field_name):
    """adapter.get_field_value side_effect for tests exercising BOTH fields
    on the same fake footprint (resolve_by_cluster_tag only reads Cluster,
    but a realistic fp usually carries both)."""
    if field_name == ROLE_FIELD_NAME:
        return fp._role
    if field_name == CLUSTER_FIELD_NAME:
        return fp._cluster
    return None


def _get_pads(fp):
    """Pads get sequential numbers 1..N (2026-08-16, net_template_pad): the
    new pad-aware tests address pads by number — a candidate with nets
    [a, b, c] has pad '1' -> a, pad '2' -> b, pad '3' -> c."""
    pads = []
    for i, n in enumerate(fp._nets, start=1):
        p = MagicMock()
        p.number = str(i)
        p.net_name = n
        pads.append(p)
    return pads


def _get_pad_by_number(fp, num):
    return next((p for p in _get_pads(fp) if p.number == str(num)), None)


class TestResolveRolesBySelection:
    def _template(self):
        return Cell(name="crystal", components=[
            TemplateComponentSlot(role="XTAL"),
            TemplateComponentSlot(role="LOAD_CAP_1"),
            TemplateComponentSlot(role="LOAD_CAP_2"),
        ])

    def _clone(self, name="crystal2"):
        return ClonePlacement(cluster=name, cell="crystal", xy=(0.0, 0.0))

    def test_exact_match_resolves(self):
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [
            _make_fp("Y3", "XTAL"), _make_fp("C20", "LOAD_CAP_1"), _make_fp("C21", "LOAD_CAP_2"),
        ]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        result = resolve_roles_by_selection(adapter, self._template(), self._clone())
        assert result == {"XTAL": "Y3", "LOAD_CAP_1": "C20", "LOAD_CAP_2": "C21"}

    def test_missing_role_raises(self):
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [_make_fp("Y3", "XTAL"), _make_fp("C20", "LOAD_CAP_1")]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprints.return_value = []
        with pytest.raises(ValidationError, match="LOAD_CAP_2"):
            resolve_roles_by_selection(adapter, self._template(), self._clone())

    def test_extra_role_not_in_template_raises(self):
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [
            _make_fp("Y3", "XTAL"), _make_fp("C20", "LOAD_CAP_1"),
            _make_fp("C21", "LOAD_CAP_2"), _make_fp("R5", "EXTRA_ROLE"),
        ]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        with pytest.raises(ValidationError, match="EXTRA_ROLE"):
            resolve_roles_by_selection(adapter, self._template(), self._clone())

    def test_duplicate_role_in_selection_raises(self):
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [
            _make_fp("Y3", "XTAL"), _make_fp("Y4", "XTAL"),
            _make_fp("C20", "LOAD_CAP_1"), _make_fp("C21", "LOAD_CAP_2"),
        ]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprints.return_value = adapter.get_selected_items.return_value
        with pytest.raises(ValidationError, match="XTAL"):
            resolve_roles_by_selection(adapter, self._template(), self._clone())

    def test_ambiguous_role_narrowed_by_placement_name(self):
        """Split 2026-08-14: in by-selection mode too, an ambiguous role NOT
        in the selection is narrowed by the placement's OWN Cluster (`name`,
        not `anchor_cluster`) — same shared _narrow_ambiguous_candidates as
        the by-nets path."""
        tpl = self._template()  # XTAL / LOAD_CAP_1 / LOAD_CAP_2
        fps = [
            _make_fp("Y3", "XTAL", cluster="Out_Cluster"),
            _make_fp("Y4", "XTAL", cluster="Other_Cluster"),
        ]
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [_make_fp("C20", "LOAD_CAP_1"),
                                                   _make_fp("C21", "LOAD_CAP_2")]
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprints.return_value = fps + adapter.get_selected_items.return_value

        clone = ClonePlacement(cluster="Out_Cluster", cell="crystal", xy=(0, 0))
        result = resolve_roles_by_selection(adapter, tpl, clone)
        assert result == {"XTAL": "Y3", "LOAD_CAP_1": "C20", "LOAD_CAP_2": "C21"}


class TestResolveRolesByNets:
    def _pi_filter_template(self):
        return Cell(name="pi_filter", components=[
            TemplateComponentSlot(role="CAP_IN"),
            TemplateComponentSlot(role="CAP_OUT"),
            TemplateComponentSlot(role="FERRITE"),
        ])

    def test_three_identical_filters_not_confused(self):
        """
        Key scenario: 3 physically indistinguishable PI‑filters — each must
        resolve to its own components, not someone else's.
        """
        fps = [
            _make_fp("C10", "CAP_IN", ["GPIO12"]), _make_fp("C11", "CAP_OUT", ["GPIO12_FILTERED"]),
            _make_fp("L1", "FERRITE", ["GPIO12", "GPIO12_FILTERED"]),
            _make_fp("C12", "CAP_IN", ["GPIO13"]), _make_fp("C13", "CAP_OUT", ["GPIO13_FILTERED"]),
            _make_fp("L2", "FERRITE", ["GPIO13", "GPIO13_FILTERED"]),
            _make_fp("C14", "CAP_IN", ["GPIO14"]), _make_fp("C15", "CAP_OUT", ["GPIO14_FILTERED"]),
            _make_fp("L3", "FERRITE", ["GPIO14", "GPIO14_FILTERED"]),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        tpl = self._pi_filter_template()
        results = {}
        for gpio_num, gpio_name in [(12, "filter_gpio12"), (13, "filter_gpio13"), (14, "filter_gpio14")]:
            clone = ClonePlacement(
                cluster=gpio_name, cell="pi_filter", xy=(0, 0),
                nets={"CAP_IN": f"GPIO{gpio_num}", "CAP_OUT": f"GPIO{gpio_num}_FILTERED",
                     "FERRITE": f"GPIO{gpio_num}"},
            )
            results[gpio_name] = resolve_roles_by_nets(adapter, tpl, clone)

        assert results["filter_gpio12"] == {"CAP_IN": "C10", "CAP_OUT": "C11", "FERRITE": "L1"}
        assert results["filter_gpio13"] == {"CAP_IN": "C12", "CAP_OUT": "C13", "FERRITE": "L2"}
        assert results["filter_gpio14"] == {"CAP_IN": "C14", "CAP_OUT": "C15", "FERRITE": "L3"}
        # No ref should repeat across instances
        all_refs = [ref for r in results.values() for ref in r.values()]
        assert len(all_refs) == len(set(all_refs))

    def test_net_template_with_params_resolves(self):
        tpl = Cell(name="dac", components=[
            TemplateComponentSlot(role="DAC_DB1_CAP", net_template="DAC{channel}_DB1"),
        ])
        fps = [_make_fp("C50", "DAC_DB1_CAP", ["DAC2_DB1"]), _make_fp("C51", "DAC_DB1_CAP", ["DAC3_DB1"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(cluster="dac_ch2", cell="dac", xy=(0, 0), params={"channel": 2})
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"DAC_DB1_CAP": "C50"}

    def test_explicit_nets_take_priority_over_net_template(self):
        """ClonePlacement.nets must override cell net_template when both are set."""
        tpl = Cell(name="t", components=[
            TemplateComponentSlot(role="X", net_template="SHOULD_NOT_BE_USED"),
        ])
        fps = [_make_fp("A", "X", ["REAL_NET"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0), nets={"X": "REAL_NET"})
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"X": "A"}

    def test_works_with_a_cell_placement_lacking_anchor_sheet_and_cluster(self):
        """Phase 4 regression: resolve_roles_by_nets/_narrow_ambiguous_candidates
        also serve CellPlacement (nested, closed-boundary references inside a
        composite cell — see config/models.py) — that type has NO
        anchor_sheet/anchor_cluster attribute at all (not just None), unlike
        ClonePlacement. Must not raise AttributeError; getattr(..., None)
        makes this a no-op narrowing step, same as ClonePlacement with both
        explicitly unset."""
        from kicadstamp.config import CellPlacement

        tpl = Cell(name="t", components=[TemplateComponentSlot(role="X")])
        fps = [_make_fp("A", "X", ["REAL_NET"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        nested = CellPlacement(name="inner", cell="t", xy=(0, 0), nets={"X": "REAL_NET"})
        assert not hasattr(nested, "anchor_sheet")
        assert not hasattr(nested, "anchor_cluster")

        result = resolve_roles_by_nets(adapter, tpl, nested)
        assert result == {"X": "A"}

    def test_cell_placement_own_sheet_cluster_narrows_shared_net_role(self):
        """Live bug 2026-08-25/26 (handoff cell_placement_sheet_cluster): a
        nested CellPlacement had NO own sheet/cluster, so the narrowing
        cascade read None (getattr) and a shared-rail role like C_IN_BULK on
        +3V3 stayed ambiguous among identical physical instances (14
        candidates board-wide, <2x anchor gap, honest fatal). With
        sheet/cluster carried over from the source ClonePlacement, the cascade
        narrows 14 -> 3 (by sheet Channel_1) -> 1 (by cluster PIF_DVDD)
        without a fatal."""
        from kicadstamp.config import CellPlacement

        tpl = Cell(name="dac_pif_dvdd", components=[
            TemplateComponentSlot(role="C_IN_BULK"),
        ])
        # 14 identical C_IN_BULK instances on the shared +3V3 rail, spread
        # over 3 reused hierarchical sheets (Channel_0/1/2) with PIF_* clusters.
        fps = [
            # Channel_0 (5)
            _make_fp_with_sheet("C100", "C_IN_BULK", ["+3V3"], "sheet-uuid-0", cluster="PIF_DVDD"),
            _make_fp_with_sheet("C101", "C_IN_BULK", ["+3V3"], "sheet-uuid-0", cluster="PIF_AVDD"),
            _make_fp_with_sheet("C102", "C_IN_BULK", ["+3V3"], "sheet-uuid-0", cluster="PIF_CLKVDD"),
            _make_fp_with_sheet("C103", "C_IN_BULK", ["+3V3"], "sheet-uuid-0", cluster="PIF_DVDD"),
            _make_fp_with_sheet("C104", "C_IN_BULK", ["+3V3"], "sheet-uuid-0", cluster="PIF_DVDD"),
            # Channel_1 (3)
            _make_fp_with_sheet("C147", "C_IN_BULK", ["+3V3"], "sheet-uuid-1", cluster="PIF_DVDD"),
            _make_fp_with_sheet("C148", "C_IN_BULK", ["+3V3"], "sheet-uuid-1", cluster="PIF_AVDD"),
            _make_fp_with_sheet("C149", "C_IN_BULK", ["+3V3"], "sheet-uuid-1", cluster="PIF_CLKVDD"),
            # Channel_2 (6)
            _make_fp_with_sheet("C200", "C_IN_BULK", ["+3V3"], "sheet-uuid-2", cluster="PIF_DVDD"),
            _make_fp_with_sheet("C201", "C_IN_BULK", ["+3V3"], "sheet-uuid-2", cluster="PIF_AVDD"),
            _make_fp_with_sheet("C202", "C_IN_BULK", ["+3V3"], "sheet-uuid-2", cluster="PIF_CLKVDD"),
            _make_fp_with_sheet("C203", "C_IN_BULK", ["+3V3"], "sheet-uuid-2", cluster="PIF_DVDD"),
            _make_fp_with_sheet("C204", "C_IN_BULK", ["+3V3"], "sheet-uuid-2", cluster="PIF_DVDD"),
            _make_fp_with_sheet("C205", "C_IN_BULK", ["+3V3"], "sheet-uuid-2", cluster="PIF_AVDD"),
        ]
        assert len(fps) == 14
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1",
                       "sheet-uuid-2": "Channel_2"}
        nested = CellPlacement(
            name="ch1_pif_dvdd", cell="dac_pif_dvdd", xy=(0, 0),
            nets={"C_IN_BULK": "+3V3"}, sheet="Channel_1", cluster="PIF_DVDD")
        # Sheet narrows 14 -> 3 (Channel_1's instances), Cluster narrows 3 -> 1.
        result = resolve_roles_by_nets(adapter, tpl, nested, sheet_names=sheet_names)
        assert result == {"C_IN_BULK": "C147"}

        # Negative control — the OLD broken shape (no own sheet/cluster) must
        # stay ambiguous: proves the fix is what unblocked this, not some
        # other narrowing step.
        stripped = CellPlacement(
            name="ch1_pif_dvdd", cell="dac_pif_dvdd", xy=(0, 0),
            nets={"C_IN_BULK": "+3V3"})
        with pytest.raises(ValidationError, match="C_IN_BULK"):
            resolve_roles_by_nets(adapter, tpl, stripped, sheet_names=sheet_names)

    def test_internal_role_narrowing_uses_placement_name(self):
        """Split 2026-08-14: ambiguous roles INSIDE the cell are narrowed by
        the placement's OWN Cluster — by convention clone.name (the GUI's
        "Cluster:" field on the Source tab), NOT by anchor_cluster (which
        narrows only the external anchor). A ClonePlacement with
        name="Out_Cluster" and NO anchor_cluster at all must still narrow two
        same-role/same-net candidates that differ by their board Cluster field
        — the live PI_FB/FB10/FB4 case, now via name."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="PI_FB")])
        fps = [
            _make_fp("FB10", "PI_FB", ["NET1"], cluster="Out_Cluster"),
            _make_fp("FB4", "PI_FB", ["NET1"], cluster="Other_Cluster"),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        clone = ClonePlacement(cluster="Out_Cluster", cell="t", xy=(0, 0),
                               nets={"PI_FB": "NET1"})
        assert clone.anchor_cluster is None  # narrowing must NOT depend on it
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"PI_FB": "FB10"}

    def test_anchor_cluster_does_not_narrow_internal_roles(self):
        """Split regression: anchor_cluster is set to a DIFFERENT value than
        name — internal-role narrowing must still follow name; anchor_cluster
        must have no effect on it (it narrows only the anchor, see
        resolve_footprint_by_role)."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="PI_FB")])
        fps = [
            _make_fp("FB10", "PI_FB", ["NET1"], cluster="Out_Cluster"),
            _make_fp("FB4", "PI_FB", ["NET1"], cluster="Other_Cluster"),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        clone = ClonePlacement(cluster="Out_Cluster", cell="t", xy=(0, 0),
                               nets={"PI_FB": "NET1"},
                               anchor_cluster="Unrelated_Anchor_Cluster")
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"PI_FB": "FB10"}

    def test_bridging_role_matches_by_auto_derived_net_template(self):
        """Phase 1 step 1.2 round-trip: extract auto-derives a designated
        net_template (+ net_template_pad) for a bridging role — 2 DIFFERENT
        nets on its pads — WITHOUT --net-template-role. resolve_roles_by_nets
        must then resolve that role from the auto-derived net_template alone
        (no manual clone.nets), picking the instance on the designated net."""
        tpl = Cell(name="t", components=[
            TemplateComponentSlot(role="PI_FILTER_FB", net_template="{PWR_OUT}",
                                  net_template_pad="1"),
        ])
        fps = [
            _make_fp("FB1", "PI_FILTER_FB", ["+5V", "+5V_DIRTY"]),
            _make_fp("FB2", "PI_FILTER_FB", ["+3V3", "+3V3_DIRTY"]),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        # params supply the value the auto-derived {PWR_OUT} designates; no
        # clone.nets at all — the whole manual net definition is gone.
        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0),
                               params={"PWR_OUT": "+5V"})
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"PI_FILTER_FB": "FB1"}

    def test_bridging_role_narrowed_by_designated_pad_when_rail_shared(self):
        """Review 2026-08-28 resolver change: BOTH candidates carry the
        designated net (+5V) but on DIFFERENT pad numbers — matching by the
        single expected net alone would be ambiguous/fatal. With
        net_template_pad (auto-derived by extract for a bridging role), the
        resolver narrows to the candidate whose pad with that number carries
        the designated net."""
        tpl = Cell(name="t", components=[
            TemplateComponentSlot(role="PI_FILTER_FB", net_template="{PWR_OUT}",
                                  net_template_pad="1"),
        ])
        fps = [
            _make_fp("FB1", "PI_FILTER_FB", ["+5V", "+5V_DIRTY"]),    # pad1 = +5V
            _make_fp("FB2", "PI_FILTER_FB", ["+5V_CLEAN", "+5V"]),    # pad2 = +5V
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0),
                               params={"PWR_OUT": "+5V"})
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"PI_FILTER_FB": "FB1"}

    def test_bridging_role_unreliable_pad_never_picks_wrong_single(self):
        """Guard: pad numbering is unreliable for electrically symmetric
        parts, so the designated-pad check must only ever NARROW. When it
        yields empty (net_template_pad points to a pad that carries the
        designated net nowhere), the primary net-value match is kept — both
        candidates stay ambiguous and it is FATAL, never a wrong single pick."""
        tpl = Cell(name="t", components=[
            TemplateComponentSlot(role="PI_FILTER_FB", net_template="{PWR_OUT}",
                                  net_template_pad="9"),  # exists nowhere
        ])
        fps = [
            _make_fp("FB1", "PI_FILTER_FB", ["+5V", "+5V_DIRTY"]),
            _make_fp("FB2", "PI_FILTER_FB", ["+5V_CLEAN", "+5V"]),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0),
                               params={"PWR_OUT": "+5V"})
        with pytest.raises(ValidationError, match="PI_FILTER_FB"):
            resolve_roles_by_nets(adapter, tpl, clone)

    def test_internal_role_narrowing_uses_placement_sheet(self):
        """Split 2026-08-15: ambiguous roles INSIDE the cell are narrowed by
        the placement's OWN sheet (clone.sheet), NOT by anchor_sheet (which
        narrows only the external anchor). A reused hierarchical sheet clones
        IDENTICAL Cluster/Role fields onto every instance (Denis, live:
        AD_DAC/IC2 exists identically on every channel's cloned sheet) — only
        the sheet can tell two physical copies apart."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="PI_FB")])
        fps = [
            _make_fp_with_sheet("FB10", "PI_FB", ["NET1"], "sheet-uuid-0"),
            _make_fp_with_sheet("FB4", "PI_FB", ["NET1"], "sheet-uuid-1"),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0),
                               nets={"PI_FB": "NET1"}, sheet="Channel_0")
        assert clone.anchor_sheet is None  # narrowing must NOT depend on it
        result = resolve_roles_by_nets(
            adapter, tpl, clone,
            sheet_names={"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1"})
        assert result == {"PI_FB": "FB10"}

    def test_anchor_sheet_does_not_narrow_internal_roles(self):
        """Split regression (same class as the 08-14 anchor_cluster one):
        anchor_sheet is set to a DIFFERENT value than sheet — internal-role
        narrowing must still follow sheet; anchor_sheet must have no effect on
        it (it narrows only the external anchor, see resolve_footprint_by_role)."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="PI_FB")])
        fps = [
            _make_fp_with_sheet("FB10", "PI_FB", ["NET1"], "sheet-uuid-0"),
            _make_fp_with_sheet("FB4", "PI_FB", ["NET1"], "sheet-uuid-1"),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0),
                               nets={"PI_FB": "NET1"}, sheet="Channel_0",
                               anchor_sheet="Unrelated_Sheet_99")
        result = resolve_roles_by_nets(
            adapter, tpl, clone,
            sheet_names={"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1"})
        assert result == {"PI_FB": "FB10"}

    def test_role_without_any_net_source_raises(self):
        tpl = Cell(name="t2", components=[TemplateComponentSlot(role="NO_NET_ROLE")])
        clone = ClonePlacement(cluster="x", cell="t2", xy=(0, 0))
        adapter = MagicMock()
        adapter.get_footprints.return_value = []
        with pytest.raises(ValidationError, match="NO_NET_ROLE"):
            resolve_roles_by_nets(adapter, tpl, clone)

    def test_ambiguous_match_raises_with_both_refs(self):
        tpl = Cell(name="t3", components=[TemplateComponentSlot(role="X")])
        fps = [_make_fp("A", "X", ["NET1"]), _make_fp("B", "X", ["NET1"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(cluster="y", cell="t3", xy=(0, 0), nets={"X": "NET1"})
        with pytest.raises(ValidationError, match="A.*B|B.*A"):
            resolve_roles_by_nets(adapter, tpl, clone)

    def test_no_match_found_raises(self):
        tpl = Cell(name="t4", components=[TemplateComponentSlot(role="X")])
        fps = [_make_fp("A", "X", ["SOME_OTHER_NET"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(cluster="z", cell="t4", xy=(0, 0), nets={"X": "NO_SUCH_NET"})
        with pytest.raises(ValidationError, match="NO_SUCH_NET"):
            resolve_roles_by_nets(adapter, tpl, clone)

    def test_no_role_at_all_gives_distinct_message(self):
        """No component with that Role on the board at all — the message should
        clearly differ from 'exists but on the wrong net'."""
        tpl = Cell(name="t5", components=[TemplateComponentSlot(role="NONEXISTENT_ROLE")])
        adapter = MagicMock()
        adapter.get_footprints.return_value = []  # nothing at all on board
        clone = ClonePlacement(cluster="z", cell="t5", xy=(0, 0),
                              nets={"NONEXISTENT_ROLE": "GND"})
        # Message text is translated (see kicadstamp/i18n.py) — match either
        # locale the project ships (en/ru), not just the raw English msgid.
        with pytest.raises(ValidationError, match="NO component with this role|НЕТ ни одного компонента с этой ролью"):
            resolve_roles_by_nets(adapter, tpl, clone)

    def test_role_exists_wrong_net_gives_distinct_message_with_real_nets(self):
        """Component(s) with the role exist, but not on the expected net — the
        message must name the nets they actually sit on."""
        tpl = Cell(name="t6", components=[TemplateComponentSlot(role="X")])
        fps = [_make_fp("A", "X", ["ACTUAL_NET"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(cluster="z", cell="t6", xy=(0, 0), nets={"X": "EXPECTED_NET"})
        with pytest.raises(ValidationError, match="ACTUAL_NET") as exc_info:
            resolve_roles_by_nets(adapter, tpl, clone)
        msg = str(exc_info.value)
        assert "A" in msg
        assert "exist" in msg or "есть на плате" in msg  # different from "NO component"

    def test_realistic_scenario_some_roles_ok_some_missing_some_wrong_net(self):
        """
        Realistic scenario: 4 filter roles, some components missing,
        some on the wrong net — the final message should clearly separate
        each role by its reason for failure.
        """
        tpl = Cell(name="pi_filter", components=[
            TemplateComponentSlot(role="PI_FILTER_C1", net_template="{NET_IN}"),
            TemplateComponentSlot(role="PI_FILTER_C2", net_template="{NET_IN}"),
            TemplateComponentSlot(role="PI_FILTER_FB", net_template="{NET_OUT}"),
            TemplateComponentSlot(role="FB_FILTER_C3", net_template="{NET_OUT}"),
        ])
        # PI_FILTER_C1/C2 — no components with that role on the board at all.
        # PI_FILTER_FB — exists but on a different net.
        # FB_FILTER_C3 — exists and correctly connected.
        fps = [
            _make_fp("C99", "PI_FILTER_FB", ["+3V3"]),       # wrong net (expected +3V3_VCCIO)
            _make_fp("C100", "FB_FILTER_C3", ["+3V3_VCCIO"]),  # correct
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(cluster="power_filter_vccio", cell="pi_filter", xy=(0, 0),
                              params={"NET_IN": "+3V3", "NET_OUT": "+3V3_VCCIO"})
        with pytest.raises(ValidationError) as exc_info:
            resolve_roles_by_nets(adapter, tpl, clone)
        msg = str(exc_info.value)
        assert "PI_FILTER_C1" in msg and ("NO component" in msg or "НЕТ ни одного компонента" in msg)
        assert "PI_FILTER_C2" in msg
        assert "PI_FILTER_FB" in msg and "C99" in msg and "+3V3" in msg
        assert "FB_FILTER_C3" not in msg  # this role resolved successfully, so no problem

    def test_explicit_refs_override_all_other_resolution(self):
        """refs in ClonePlacement must have highest priority, bypassing net‑based search."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="X")])
        # Two candidates on the same net, but refs points to a specific one
        fps = [_make_fp("A", "X", ["NET1"]), _make_fp("B", "X", ["NET1"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0),
                               nets={"X": "NET1"}, refs={"X": "B"})
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"X": "B"}  # Must pick B despite ambiguity


def _make_fp_with_sheet(ref, role, nets, sheet_uuid, cluster=None):
    """Like _make_fp, but also carries sheet_path (see _make_anchor_fp) — for
    testing anchor_sheet narrowing of TEMPLATE roles (resolve_roles_by_nets),
    as opposed to anchor resolution (resolve_anchor_by_role). cluster: optional
    (2026-08-16, Auto-fill Sheet narrowing tests) — combined with sheet_path so
    resolve_single_role_candidate can be exercised on same-Role/same-Cluster
    candidates spread across reused hierarchical sheets."""
    fp = _make_fp(ref, role, nets, cluster)
    fp.sheet_path_uuids = (sheet_uuid, f"{ref}-own-uuid")
    return fp


class TestResolveRolesByNetsPlacementSheet:
    """The placement's OWN sheet narrowing for ambiguous TEMPLATE roles (added
    2026-07-28 as anchor_sheet; split 2026-08-15 into the placement's own
    `sheet` field — anchor_sheet now narrows only the EXTERNAL anchor, see
    TestResolveAnchorByRole): a GLOBAL net (e.g. +3V3, shared by every
    instance of a role board‑wide, unlike a per‑channel hierarchical net)
    leaves candidates=all instances, and Cluster can't help when the
    schematic reuses one physical section per channel via a hierarchical
    sheet (Cluster is then shared across every instance too) — the
    placement's own sheet is the only signal left."""

    def _template(self):
        return Cell(name="pi_filter", components=[TemplateComponentSlot(role="CAP_IN")])

    def test_narrows_ambiguous_global_net_to_one(self):
        fps = [
            _make_fp_with_sheet("C10", "CAP_IN", ["+3V3"], "sheet-uuid-0"),
            _make_fp_with_sheet("C20", "CAP_IN", ["+3V3"], "sheet-uuid-1"),
            _make_fp_with_sheet("C30", "CAP_IN", ["+3V3"], "sheet-uuid-2"),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []
        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1", "sheet-uuid-2": "Channel_2"}

        clone = ClonePlacement(cluster="c1", cell="pi_filter", xy=(0, 0),
                               nets={"CAP_IN": "+3V3"}, sheet="Channel_1")
        assert clone.anchor_sheet is None  # narrowing must NOT depend on it
        result = resolve_roles_by_nets(adapter, self._template(), clone, sheet_names=sheet_names)
        assert result == {"CAP_IN": "C20"}

    def test_own_sheet_used_literally_not_resolved_from_params(self):
        """Split 2026-08-15: internal-role narrowing reads the placement's OWN
        sheet LITERALLY — `sheet` is an "own identity" field, NOT run through
        resolve_placeholder (only the EXTERNAL anchor_sheet keeps that
        treatment, see TestResolveAnchorByRole). A {placeholder} in sheet must
        NOT be substituted: the literal string matches no sheet, so the
        ambiguity is reported rather than silently narrowing."""
        fps = [
            _make_fp_with_sheet("C10", "CAP_IN", ["+3V3"], "sheet-uuid-0"),
            _make_fp_with_sheet("C20", "CAP_IN", ["+3V3"], "sheet-uuid-1"),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []
        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1"}

        clone = ClonePlacement(cluster="c1", cell="pi_filter", xy=(0, 0),
                               nets={"CAP_IN": "+3V3"}, sheet="Channel_{channel}",
                               params={"channel": 1})
        with pytest.raises(ValidationError, match="CAP_IN"):
            resolve_roles_by_nets(adapter, self._template(), clone, sheet_names=sheet_names)

    def test_insufficient_narrowing_raises_mentioning_placement_sheet(self):
        """Two candidates share the same sheet — this placement's sheet narrows
        3 -> 2, not enough; the fatal message must say so (naming the sheet
        value) instead of the old Cluster-only wording."""
        fps = [
            _make_fp_with_sheet("C10", "CAP_IN", ["+3V3"], "sheet-uuid-0"),
            _make_fp_with_sheet("C11", "CAP_IN", ["+3V3"], "sheet-uuid-0"),
            _make_fp_with_sheet("C20", "CAP_IN", ["+3V3"], "sheet-uuid-1"),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []
        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1"}

        clone = ClonePlacement(cluster="c1", cell="pi_filter", xy=(0, 0),
                               nets={"CAP_IN": "+3V3"}, sheet="Channel_0")
        with pytest.raises(ValidationError, match="Channel_0") as exc_info:
            resolve_roles_by_nets(adapter, self._template(), clone, sheet_names=sheet_names)
        msg = str(exc_info.value)
        assert "C10" in msg and "C11" in msg and "C20" not in msg

    def test_no_sheet_mentions_placement_cluster_hint(self):
        """Regression (splits 2026-08-14/08-15): no placement sheet set — the
        hint must name the placement's OWN Cluster (`name` — required and
        non-empty for ClonePlacement/CellPlacement, see config/entries.py, so
        the "neither sheet nor Cluster set" branch is unreachable) instead of
        the old anchor_cluster/anchor_sheet wording."""
        fps = [
            _make_fp("A", "CAP_IN", ["+3V3"]),
            _make_fp("B", "CAP_IN", ["+3V3"]),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        clone = ClonePlacement(cluster="c1", cell="pi_filter", xy=(0, 0),
                               nets={"CAP_IN": "+3V3"})
        with pytest.raises(ValidationError, match="CAP_IN") as exc_info:
            resolve_roles_by_nets(adapter, self._template(), clone)
        msg = str(exc_info.value)
        assert "this placement's Cluster 'c1'" in msg or "собственного Cluster 'c1'" in msg


def _make_anchor_fp(ref, role, sheet_uuid):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    # resolve_sheet_path_names reads fp.sheet_path_uuids[:-1] (last entry is
    # the component's own uuid, excluded) — see kicadstamp/sheet_names.py.
    fp.sheet_path_uuids = (sheet_uuid, f"{ref}-own-uuid")
    return fp


class TestResolveAnchorByRole:
    """anchor_sheet supports {placeholder} substitution from clone.params
    (real bug hit live 2026-07-28: IC2/IC3/IC4 — same Role field shared
    across 3 instances of a reused sheet, channel.kicad_sch — a clone
    parametrized with params: {channel: N} needs anchor_sheet:
    'Channel_{channel}' to actually narrow per‑instance)."""

    def _adapter(self, fps):
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_selected_items.return_value = []
        return adapter

    def test_anchor_sheet_placeholder_substituted_from_params(self):
        fps = [
            _make_anchor_fp("IC2", "AD_DAC", "sheet-uuid-0"),
            _make_anchor_fp("IC3", "AD_DAC", "sheet-uuid-1"),
        ]
        adapter = self._adapter(fps)
        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1"}
        clone = ClonePlacement(cluster="c0", cell="t", xy=(0, 0),
                               anchor_role="AD_DAC", anchor_sheet="Channel_{channel}",
                               params={"channel": 0})
        result = resolve_anchor_by_role(adapter, clone, sheet_names)
        assert result.ref == "IC2"

    def test_anchor_sheet_missing_param_raises(self):
        fps = [_make_anchor_fp("IC2", "AD_DAC", "sheet-uuid-0")]
        adapter = self._adapter(fps)
        clone = ClonePlacement(cluster="c0", cell="t", xy=(0, 0),
                               anchor_role="AD_DAC", anchor_sheet="Channel_{channel}", params={})
        with pytest.raises(ValidationError, match="channel"):
            resolve_anchor_by_role(adapter, clone, {"sheet-uuid-0": "Channel_0"})

    def test_anchor_sheet_literal_without_placeholder_unaffected(self):
        """Regression: a plain, unparametrized anchor_sheet (no {placeholder})
        must keep working exactly as before."""
        fps = [
            _make_anchor_fp("IC2", "AD_DAC", "sheet-uuid-0"),
            _make_anchor_fp("IC3", "AD_DAC", "sheet-uuid-1"),
        ]
        adapter = self._adapter(fps)
        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1"}
        clone = ClonePlacement(cluster="c0", cell="t", xy=(0, 0),
                               anchor_role="AD_DAC", anchor_sheet="Channel_0")
        result = resolve_anchor_by_role(adapter, clone, sheet_names)
        assert result.ref == "IC2"


class TestResolveSingleRoleCandidate:
    """2026-08-16 (net_template_pad): the Cluster+Role narrowing, previously
    inline in suggest_role_nets_from_cluster, pulled out as a reusable piece —
    exactly one candidate, or None (0 or 2+)."""

    def _adapter(self, fps):
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        return adapter

    def test_single_candidate_resolves(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5"),
        ])
        fp = resolve_single_role_candidate(adapter.get_footprints(), adapter,
                                           "C_IN_BULK", "Out_Pi_Filter_N2V5")
        assert fp.ref == "C22"

    def test_no_candidate_returns_none(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Some_Other_Cluster"),
        ])
        assert resolve_single_role_candidate(adapter.get_footprints(), adapter,
                                             "C_IN_BULK", "Out_Pi_Filter_N2V5") is None

    def test_two_candidates_return_none(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5"),
            _make_fp("C23", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5"),
        ])
        assert resolve_single_role_candidate(adapter.get_footprints(), adapter,
                                             "C_IN_BULK", "Out_Pi_Filter_N2V5") is None

    def test_sheet_narrows_ambiguous_cluster_role_to_one(self):
        """2026-08-16 (Auto-fill Sheet narrowing): the live DAC_BUF repro —
        three AD_DAC+DAC_BUF candidates across three reused sheets (IC2/IC3/IC4),
        narrowed by the placement's own Sheet to exactly the right one."""
        adapter = self._adapter([
            _make_fp_with_sheet("IC2", "AD_DAC", [], "sheet-uuid-0", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC3", "AD_DAC", [], "sheet-uuid-1", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC4", "AD_DAC", [], "sheet-uuid-2", cluster="DAC_BUF"),
        ])
        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1", "sheet-uuid-2": "Channel_2"}
        fp = resolve_single_role_candidate(adapter.get_footprints(), adapter,
                                           "AD_DAC", "DAC_BUF", sheet="Channel_0", sheet_names=sheet_names)
        assert fp.ref == "IC2"

    def test_sheet_none_stays_ambiguous(self):
        """Regression guard: no sheet passed — the same Cluster+Role ambiguity
        returns None exactly as before (nothing to narrow to)."""
        adapter = self._adapter([
            _make_fp_with_sheet("IC2", "AD_DAC", [], "sheet-uuid-0", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC3", "AD_DAC", [], "sheet-uuid-1", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC4", "AD_DAC", [], "sheet-uuid-2", cluster="DAC_BUF"),
        ])
        assert resolve_single_role_candidate(adapter.get_footprints(), adapter,
                                             "AD_DAC", "DAC_BUF") is None

    def test_sheet_names_that_resolve_nothing_stay_ambiguous(self):
        """A sheet_names map that can't resolve these footprints' UUID chain
        (empty dict, or an unknown sheet name) must NOT produce a wrong guess —
        same ambiguous None as today."""
        adapter = self._adapter([
            _make_fp_with_sheet("IC2", "AD_DAC", [], "sheet-uuid-0", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC3", "AD_DAC", [], "sheet-uuid-1", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC4", "AD_DAC", [], "sheet-uuid-2", cluster="DAC_BUF"),
        ])
        # empty dict -> nothing resolves, still ambiguous
        assert resolve_single_role_candidate(adapter.get_footprints(), adapter,
                                             "AD_DAC", "DAC_BUF", sheet="Channel_0", sheet_names={}) is None
        # unknown sheet name in an otherwise-resolvable map -> still ambiguous
        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1", "sheet-uuid-2": "Channel_2"}
        assert resolve_single_role_candidate(adapter.get_footprints(), adapter,
                                             "AD_DAC", "DAC_BUF", sheet="Bogus", sheet_names=sheet_names) is None


class TestSuggestRoleNetsFromCluster:
    """PlacerDock's Nets-tab "Auto-fill from board" button (2026-08-12) —
    read-only, never raises: unresolvable roles are simply absent from the
    returned dict, left for the user to fill by hand. SIGNATURE (2026-08-16):
    first `roles: list[str]` -> `role_pads: dict[str, str|None]` (morning,
    net_template_pad), then -> `role_hints: dict[str, tuple[str|None, str|None]]`
    (afternoon, net_template_same_as_role): each role's (net_template_pad,
    net_template_same_as_role) pair — exactly one non-None, or both None."""

    def _adapter(self, fps):
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_pad_by_number.side_effect = _get_pad_by_number
        return adapter

    def test_unique_candidate_with_one_non_rule_net_is_suggested(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2", "GND"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"C_IN_BULK": (None, None)}, "Out_Pi_Filter_N2V5")

        assert result == {"C_IN_BULK": "+1V2"}

    def test_cluster_prefix_match_narrows_like_the_real_resolver(self):
        """Same cluster_prefix_match signal resolve_roles_by_nets's own step
        3 narrowing uses — a sub-segment tag still matches the parent."""
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5/sub", nets=["+1V2"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"C_IN_BULK": (None, None)}, "Out_Pi_Filter_N2V5")

        assert result == {"C_IN_BULK": "+1V2"}

    def test_role_absent_from_result_when_no_candidate(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Some_Other_Cluster", nets=["+1V2"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"C_IN_BULK": (None, None)}, "Out_Pi_Filter_N2V5")

        assert result == {}

    def test_role_absent_from_result_when_ambiguous(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2"]),
            _make_fp("C23", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"C_IN_BULK": (None, None)}, "Out_Pi_Filter_N2V5")

        assert result == {}

    def test_role_absent_from_result_when_candidate_has_multiple_non_rule_nets(self):
        """A bridging component (e.g. PI_FB) without net_template_pad can't be
        reduced to one identifying net — same "don't guess" stance as
        net_from_role's lemma 2 ambiguity (unchanged regression)."""
        adapter = self._adapter([
            _make_fp("FB6", role="PI_FB", cluster="Out_Pi_Filter_N2V5", nets=["+1V2", "+1V2_VCCINT"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"PI_FB": (None, None)}, "Out_Pi_Filter_N2V5")

        assert result == {}

    def test_role_absent_from_result_when_only_net_is_a_rule_net(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["GND"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"C_IN_BULK": (None, None)}, "Out_Pi_Filter_N2V5")

        assert result == {}

    def test_custom_rule_nets_respected(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2", "+5V"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"C_IN_BULK": (None, None)}, "Out_Pi_Filter_N2V5",
                                                 rule_nets={"+5V"})

        assert result == {"C_IN_BULK": "+1V2"}

    def test_multiple_roles_mixed_success_and_failure(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2"]),
            _make_fp("FB6", role="PI_FB", cluster="Out_Pi_Filter_N2V5", nets=["+1V2", "+1V2_VCCINT"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"C_IN_BULK": (None, None), "PI_FB": (None, None)},
                                                "Out_Pi_Filter_N2V5")

        assert result == {"C_IN_BULK": "+1V2"}

    def test_multi_pad_role_with_net_template_pad_reads_that_pad_directly(self):
        """THE fix (2026-08-16, net_template_pad): a regulator/diode/inductor
        candidate carries several real nets on different pads — with the role's
        net_template_pad set, the SPECIFIC pad's net is read directly, no
        "exactly one net" requirement. This is the 7/13 -> 13/13 difference."""
        adapter = self._adapter([
            _make_fp("U2", role="LDO_ADJ", cluster="LDO_ADJ_P2V5",
                     nets=["+2V5_ADJ", "+2V5_DIRTY", "+5V"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"LDO_ADJ": ("3", None)}, "LDO_ADJ_P2V5")

        assert result == {"LDO_ADJ": "+5V"}

    def test_net_template_pad_for_other_pad_number(self):
        """Pad numbers are just strings — pad '1' -> the first net."""
        adapter = self._adapter([
            _make_fp("U2", role="LDO_ADJ", cluster="LDO_ADJ_P2V5",
                     nets=["+2V5_ADJ", "+2V5_DIRTY", "+5V"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"LDO_ADJ": ("1", None)}, "LDO_ADJ_P2V5")

        assert result == {"LDO_ADJ": "+2V5_ADJ"}

    def test_net_template_pad_with_missing_pad_leaves_role_out(self):
        """An unset/missing pad on the resolved candidate is simply left out
        (unblocked, same as before) — never guessed."""
        adapter = self._adapter([
            _make_fp("U2", role="LDO_ADJ", cluster="LDO_ADJ_P2V5",
                     nets=["+2V5_ADJ", "+2V5_DIRTY", "+5V"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"LDO_ADJ": ("9", None)}, "LDO_ADJ_P2V5")

        assert result == {}

    def test_same_as_role_resolves_via_lemma2_sibling(self):
        """net_template_same_as_role (2026-08-16 afternoon): the role's net is
        taken live from the named sibling role, resolved on the SAME cluster
        and RE-VERIFIED to be lemma-2-safe on the current board (exactly one
        non-rule net) — the cross-instance-safe alternative to a pad number
        for electrically symmetric 2-pin parts (the R_FB_TOP/R_FB_BOT case)."""
        adapter = self._adapter([
            _make_fp("R10", role="R_FB_TOP", cluster="LDO_ADJ_P2V5", nets=["+2V5_ADJ", "-2V5_DIRTY"]),
            _make_fp("R11", role="R_FB_BOT", cluster="LDO_ADJ_P2V5", nets=["+2V5_ADJ", "GND"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"R_FB_TOP": (None, "R_FB_BOT")}, "LDO_ADJ_P2V5")

        # R_FB_BOT is lemma-2-safe on "+2V5_ADJ" -> R_FB_TOP shares that net.
        assert result == {"R_FB_TOP": "+2V5_ADJ"}

    def test_same_as_role_sibling_absent_leaves_role_out(self):
        """Same-as-role names a sibling that is NOT on this live board (0 or
        2+ candidates) — role left out, never a stale guess."""
        adapter = self._adapter([
            _make_fp("R10", role="R_FB_TOP", cluster="LDO_ADJ_P2V5", nets=["+2V5_ADJ", "-2V5_DIRTY"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"R_FB_TOP": (None, "R_FB_BOT")}, "LDO_ADJ_P2V5")

        assert result == {}

    def test_same_as_role_sibling_no_longer_lemma2_safe_leaves_role_out(self):
        """Sibling exists on the live board but is no longer lemma-2-safe there
        (board changed since extraction — now multi-net) — left out, NOT a
        stale/wrong guess: the sibling's own net is re-verified live."""
        adapter = self._adapter([
            _make_fp("R10", role="R_FB_TOP", cluster="LDO_ADJ_P2V5", nets=["+2V5_ADJ", "-2V5_DIRTY"]),
            _make_fp("R11", role="R_FB_BOT", cluster="LDO_ADJ_P2V5", nets=["+2V5_ADJ", "-2V5_DIRTY"]),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"R_FB_TOP": (None, "R_FB_BOT")}, "LDO_ADJ_P2V5")

        assert result == {}

    def test_sheet_narrows_ambiguous_same_cluster_role(self):
        """2026-08-16 (Auto-fill Sheet narrowing): the live DAC_BUF repro — three
        AD_DAC+DAC_BUF instances across three reused sheets are ambiguous by
        Cluster+Role alone; the placement's own Sheet resolves to exactly one,
        so the net IS suggested instead of the full-board fallback."""
        adapter = self._adapter([
            _make_fp_with_sheet("IC2", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-0", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC3", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-1", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC4", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-2", cluster="DAC_BUF"),
        ])
        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1", "sheet-uuid-2": "Channel_2"}

        result = suggest_role_nets_from_cluster(adapter, {"AD_DAC": (None, None)}, "DAC_BUF",
                                                sheet="Channel_0", sheet_names=sheet_names)

        assert result == {"AD_DAC": "DAC_OUT_P"}

    def test_ambiguous_without_sheet_stays_out(self):
        """Regression guard: no sheet passed — the same 3-way Cluster+Role
        ambiguity leaves the role out of the suggestions (identical to today's
        behavior, never a guess)."""
        adapter = self._adapter([
            _make_fp_with_sheet("IC2", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-0", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC3", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-1", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC4", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-2", cluster="DAC_BUF"),
        ])

        result = suggest_role_nets_from_cluster(adapter, {"AD_DAC": (None, None)}, "DAC_BUF")

        assert result == {}

    def test_same_as_role_sibling_narrowed_by_sheet(self):
        """2026-08-16 (Auto-fill Sheet narrowing): the net_template_same_as_role
        sibling lookup ALSO narrows by the placement's sheet — a reused-sheet
        sibling is just as ambiguous by Cluster+Role alone (three R_FB_BOT
        instances board-wide), so it must be resolved on the SAME sheet as the
        main role or the sibling lookup stays blind."""
        adapter = self._adapter([
            _make_fp_with_sheet("R10", "R_FB_TOP", ["+2V5_ADJ", "-2V5_DIRTY"], "sheet-uuid-0", cluster="DAC_BUF"),
            _make_fp_with_sheet("R11", "R_FB_BOT", ["+2V5_ADJ", "GND"], "sheet-uuid-0", cluster="DAC_BUF"),
            _make_fp_with_sheet("R20", "R_FB_TOP", ["+2V5_ADJ", "-2V5_DIRTY"], "sheet-uuid-1", cluster="DAC_BUF"),
            _make_fp_with_sheet("R21", "R_FB_BOT", ["+2V5_ADJ", "GND"], "sheet-uuid-1", cluster="DAC_BUF"),
            _make_fp_with_sheet("R30", "R_FB_TOP", ["+2V5_ADJ", "-2V5_DIRTY"], "sheet-uuid-2", cluster="DAC_BUF"),
            _make_fp_with_sheet("R31", "R_FB_BOT", ["+2V5_ADJ", "GND"], "sheet-uuid-2", cluster="DAC_BUF"),
        ])
        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1", "sheet-uuid-2": "Channel_2"}

        result = suggest_role_nets_from_cluster(adapter, {"R_FB_TOP": (None, "R_FB_BOT")}, "DAC_BUF",
                                                sheet="Channel_0", sheet_names=sheet_names)

        # R11 (Channel_0 sibling) is lemma-2-safe on "+2V5_ADJ" -> R_FB_TOP shares it.
        assert result == {"R_FB_TOP": "+2V5_ADJ"}


class TestCandidateNetsByRole:
    """2026-08-16 (net_template_pad) — for GUI Net-combobox narrowing, NOT
    auto-fill: {role: [nets]} for every role with exactly one Cluster+Role
    candidate, regardless of net count."""

    def _adapter(self, fps):
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        return adapter

    def test_single_candidate_with_multiple_nets_returns_all(self):
        adapter = self._adapter([
            _make_fp("U2", role="LDO_ADJ", cluster="LDO_ADJ_P2V5",
                     nets=["+2V5_ADJ", "+2V5_DIRTY", "+5V"]),
        ])

        result = candidate_nets_by_role(adapter, ["LDO_ADJ"], "LDO_ADJ_P2V5")

        assert result == {"LDO_ADJ": ["+2V5_ADJ", "+2V5_DIRTY", "+5V"]}

    def test_rule_nets_filtered_out(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2", "GND"]),
        ])

        result = candidate_nets_by_role(adapter, ["C_IN_BULK"], "Out_Pi_Filter_N2V5")

        assert result == {"C_IN_BULK": ["+1V2"]}

    def test_no_candidate_leaves_role_out(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Some_Other_Cluster", nets=["+1V2"]),
        ])

        result = candidate_nets_by_role(adapter, ["C_IN_BULK"], "Out_Pi_Filter_N2V5")

        assert result == {}

    def test_two_candidates_leaves_role_out(self):
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2"]),
            _make_fp("C23", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2"]),
        ])

        result = candidate_nets_by_role(adapter, ["C_IN_BULK"], "Out_Pi_Filter_N2V5")

        assert result == {}

    def test_zero_non_rule_nets_surfaces_empty_list(self):
        """A candidate with only rule nets is unusual but must NOT be hidden as
        'nothing to narrow' — the empty list value is surfaced as-is."""
        adapter = self._adapter([
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["GND"]),
        ])

        result = candidate_nets_by_role(adapter, ["C_IN_BULK"], "Out_Pi_Filter_N2V5")

        assert result == {"C_IN_BULK": []}

    def test_sheet_narrows_ambiguous_cluster_role(self):
        """2026-08-16 (Auto-fill Sheet narrowing): 3 same-Role/same-Cluster
        candidates on 3 reused sheets narrowed by the placement's Sheet — the
        Net-combobox gets the right instance's nets instead of falling back to
        the full board list."""
        adapter = self._adapter([
            _make_fp_with_sheet("IC2", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-0", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC3", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-1", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC4", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-2", cluster="DAC_BUF"),
        ])
        sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1", "sheet-uuid-2": "Channel_2"}

        result = candidate_nets_by_role(adapter, ["AD_DAC"], "DAC_BUF",
                                        sheet="Channel_0", sheet_names=sheet_names)

        assert result == {"AD_DAC": ["DAC_OUT_P"]}

    def test_no_sheet_leaves_role_out(self):
        """Regression guard: no sheet passed — the 3-way Cluster+Role ambiguity
        leaves the role out of the narrowing map (full board list fallback),
        exactly as before."""
        adapter = self._adapter([
            _make_fp_with_sheet("IC2", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-0", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC3", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-1", cluster="DAC_BUF"),
            _make_fp_with_sheet("IC4", "AD_DAC", ["DAC_OUT_P"], "sheet-uuid-2", cluster="DAC_BUF"),
        ])

        result = candidate_nets_by_role(adapter, ["AD_DAC"], "DAC_BUF")

        assert result == {}


class TestResolveRolesByNetsAutoDerive:
    """Phase 2 step 2.1 — roles with NO explicit net source (no clone.nets, no
    cell net_template) auto-derive their expected net from the live board via
    derive_role_nets (live_pad / prefix_remap). nets:/params:/net_overrides are
    optional overrides; the fatal "a net is required for every role" is gone
    when the net can be derived automatically
    (design_2026_08_28_phase2_step2_1_mini.md)."""

    def _adapter(self, fps):
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_pad_by_number.side_effect = _get_pad_by_number
        adapter.get_selected_items.return_value = []
        return adapter

    def test_unique_instance_single_net_live_pad(self):
        """A role with no net source at all resolves via its unique instance's
        single net (derive_role_nets live_pad, priority 1)."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="X")])
        fps = [_make_fp("A", "X", ["NET1"], cluster="c")]
        adapter = self._adapter(fps)
        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0))  # no nets/params
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"X": "A"}

    def test_shared_single_net_across_identical_candidates(self):
        """N identical instances on one net (e.g. +3V3 PI-filters): the single
        shared non-rule net is the expected net (live_pad), then the normal
        ambiguity cascade (the placement's own Cluster) disambiguates the
        instance — Kuhn is NOT used for instance selection."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="CAP_IN")])
        fps = [
            _make_fp("C1", "CAP_IN", ["+3V3"], cluster="ch1"),
            _make_fp("C2", "CAP_IN", ["+3V3"], cluster="ch2"),
            _make_fp("C3", "CAP_IN", ["+3V3"], cluster="ch2"),
        ]
        adapter = self._adapter(fps)
        clone = ClonePlacement(cluster="ch1", cell="t", xy=(0, 0))
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"CAP_IN": "C1"}

    def test_literal_local_net_template_prefix_remapped(self):
        """prefix_remap (derive_role_nets priority 2, TwinMap.twin_net
        semantics): a LITERAL local net '/Channel_0/...' in the cell auto-remaps
        to '/Channel_1/...' when the placement's Cluster names Channel_1 — no
        {channel} param, no nets:."""
        tpl = Cell(name="dac", components=[
            TemplateComponentSlot(role="DAC_DB1_CAP", net_template="/Channel_0/DAC/DB1"),
        ])
        fps = [_make_fp("C50", "DAC_DB1_CAP", ["/Channel_1/DAC/DB1"], cluster="Channel_1")]
        adapter = self._adapter(fps)
        clone = ClonePlacement(cluster="Channel_1", cell="dac", xy=(0, 0))
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"DAC_DB1_CAP": "C50"}

    def test_parametrized_net_template_not_remapped(self):
        """A parametrized net_template ({channel}) is the user's explicit
        choice — prefix_remap must NOT silently override it."""
        tpl = Cell(name="dac", components=[
            TemplateComponentSlot(role="DAC_DB1_CAP", net_template="/Channel_{channel}/DAC/DB1"),
        ])
        fps = [_make_fp("C50", "DAC_DB1_CAP", ["/Channel_0/DAC/DB1"], cluster="Channel_0")]
        adapter = self._adapter(fps)
        # params explicitly say channel 0; the placement Cluster is Channel_1 —
        # the parametrized net is respected as written (no remap).
        clone = ClonePlacement(cluster="Channel_1", cell="dac", xy=(0, 0), params={"channel": 0})
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"DAC_DB1_CAP": "C50"}

    def test_bridging_unique_instance_without_designated_pad_maps_directly(self):
        """A unique instance whose net cannot be reduced to one (bridging role,
        no net_template_pad) is mapped DIRECTLY — no guessing which net is
        'the' net (mini-design §3)."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="FB")])
        fps = [_make_fp("FB1", "FB", ["+5V", "+5V_DIRTY"], cluster="c")]
        adapter = self._adapter(fps)
        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0))
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"FB": "FB1"}

    def test_bridging_unique_instance_with_designated_pad_live_pad(self):
        """A bridging role with net_template_pad: the designated pad's net is
        the expected net (live_pad)."""
        tpl = Cell(name="t", components=[
            TemplateComponentSlot(role="FB", net_template_pad="1")])
        fps = [_make_fp("FB1", "FB", ["+5V", "+5V_DIRTY"], cluster="c")]
        adapter = self._adapter(fps)
        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0))
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"FB": "FB1"}

    def test_no_derivable_net_raises_improved_error(self):
        """Candidates on DIFFERENT nets, no unique instance, no cluster/sheet to
        narrow — honest error (never a silent guess)."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="X")])
        fps = [_make_fp("A", "X", ["NET_A"], cluster="c"),
               _make_fp("B", "X", ["NET_B"], cluster="c")]
        adapter = self._adapter(fps)
        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0))
        with pytest.raises(ValidationError, match="X"):
            resolve_roles_by_nets(adapter, tpl, clone)

    def test_identical_global_net_cascade_resolves_without_kuhn(self):
        """Phase 2 step 2.2 — the two-matchings separation (mini-design §1):
        N identical instances on a shared GLOBAL net (+3V3) auto-derive the one
        shared non-rule net (live_pad, shared-net path), then the INSTANCE is
        disambiguated by the EXISTING cascade (here: current selection) —
        Kuhn/net_matching is NEVER applied to instance selection."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="CAP_IN")])
        fps = [
            _make_fp("C1", "CAP_IN", ["+3V3"], cluster="ch1"),
            _make_fp("C2", "CAP_IN", ["+3V3"], cluster="ch1"),
            _make_fp("C3", "CAP_IN", ["+3V3"], cluster="ch1"),
        ]
        adapter = self._adapter(fps)
        adapter.get_selected_items.return_value = [fps[1]]  # C2 is selected
        clone = ClonePlacement(cluster="ch1", cell="t", xy=(0, 0))
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"CAP_IN": "C2"}

    def test_indistinguishable_global_net_is_honest_fatal_not_kuhn_guess(self):
        """Phase 2 step 2.2 — the two-matchings separation (negative): N
        identical instances on one shared global net with NOTHING to
        disambiguate (same cluster, no sheet, no selection, no proximity gap)
        is an HONEST FATAL naming the ambiguity — Kuhn/net_matching is never
        silently applied to instance selection on a global net."""
        tpl = Cell(name="t", components=[TemplateComponentSlot(role="CAP_IN")])
        fps = [
            _make_fp("C1", "CAP_IN", ["+3V3"], cluster="ch1"),
            _make_fp("C2", "CAP_IN", ["+3V3"], cluster="ch1"),
            _make_fp("C3", "CAP_IN", ["+3V3"], cluster="ch1"),
        ]
        adapter = self._adapter(fps)  # no selection, no sheet, no anchor
        clone = ClonePlacement(cluster="ch1", cell="t", xy=(0, 0))
        with pytest.raises(ValidationError, match="CAP_IN"):
            resolve_roles_by_nets(adapter, tpl, clone)


class TestPrefixRemapHelpers:
    """Unit tests for the Step 2.1 prefix_remap helpers (mini-design §2)."""

    def test_target_channel(self):
        assert _target_channel(ClonePlacement(cluster="Channel_1", cell="c", xy=(0, 0))) == "Channel_1"
        assert _target_channel(ClonePlacement(cluster="PIF_DVDD", cell="c", xy=(0, 0))) is None
        assert _target_channel(ClonePlacement(cluster="/Channel_2/", cell="c", xy=(0, 0))) == "Channel_2"

    def test_prefix_remap_local_net(self):
        clone = ClonePlacement(cluster="Channel_1", cell="c", xy=(0, 0))
        assert _prefix_remap_local_net("/Channel_0/DAC/DB1", clone) == "/Channel_1/DAC/DB1"
        # same channel — no remap
        same = ClonePlacement(cluster="Channel_0", cell="c", xy=(0, 0))
        assert _prefix_remap_local_net("/Channel_0/DAC/DB1", same) is None
        # non-channel cluster — no remap
        no_ch = ClonePlacement(cluster="PIF_DVDD", cell="c", xy=(0, 0))
        assert _prefix_remap_local_net("/Channel_0/DAC/DB1", no_ch) is None
        # flat net — no remap
        assert _prefix_remap_local_net("DAC0_DB1", clone) is None
