#!/usr/bin/env python3
"""Tests for clone_role_resolver.py — role‑to‑ref mapping for TemplatePlacer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kipy.board_types import FootprintInstance

from kicadstamp.config import Cell, TemplateComponentSlot, ClonePlacement
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.placement.services.clone_role_resolver import (
    resolve_roles_by_selection, resolve_roles_by_nets, resolve_anchor_by_role,
    suggest_role_nets_from_cluster,
)
from kicadstamp.exceptions import ValidationError


def _make_fp(ref, role=None, nets=None, cluster=None):
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
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
    pads = []
    for n in fp._nets:
        p = MagicMock()
        p.net.name = n
        pads.append(p)
    return pads


class TestResolveRolesBySelection:
    def _template(self):
        return Cell(name="crystal", components=[
            TemplateComponentSlot(role="XTAL"),
            TemplateComponentSlot(role="LOAD_CAP_1"),
            TemplateComponentSlot(role="LOAD_CAP_2"),
        ])

    def _clone(self, name="crystal2"):
        return ClonePlacement(name=name, cell="crystal", xy=(0.0, 0.0))

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
                name=gpio_name, cell="pi_filter", xy=(0, 0),
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

        clone = ClonePlacement(name="dac_ch2", cell="dac", xy=(0, 0), params={"channel": 2})
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

        clone = ClonePlacement(name="c", cell="t", xy=(0, 0), nets={"X": "REAL_NET"})
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

    def test_role_without_any_net_source_raises(self):
        tpl = Cell(name="t2", components=[TemplateComponentSlot(role="NO_NET_ROLE")])
        clone = ClonePlacement(name="x", cell="t2", xy=(0, 0))
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

        clone = ClonePlacement(name="y", cell="t3", xy=(0, 0), nets={"X": "NET1"})
        with pytest.raises(ValidationError, match="A.*B|B.*A"):
            resolve_roles_by_nets(adapter, tpl, clone)

    def test_no_match_found_raises(self):
        tpl = Cell(name="t4", components=[TemplateComponentSlot(role="X")])
        fps = [_make_fp("A", "X", ["SOME_OTHER_NET"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(name="z", cell="t4", xy=(0, 0), nets={"X": "NO_SUCH_NET"})
        with pytest.raises(ValidationError, match="NO_SUCH_NET"):
            resolve_roles_by_nets(adapter, tpl, clone)

    def test_no_role_at_all_gives_distinct_message(self):
        """No component with that Role on the board at all — the message should
        clearly differ from 'exists but on the wrong net'."""
        tpl = Cell(name="t5", components=[TemplateComponentSlot(role="NONEXISTENT_ROLE")])
        adapter = MagicMock()
        adapter.get_footprints.return_value = []  # nothing at all on board
        clone = ClonePlacement(name="z", cell="t5", xy=(0, 0),
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

        clone = ClonePlacement(name="z", cell="t6", xy=(0, 0), nets={"X": "EXPECTED_NET"})
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

        clone = ClonePlacement(name="power_filter_vccio", cell="pi_filter", xy=(0, 0),
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

        clone = ClonePlacement(name="c", cell="t", xy=(0, 0),
                               nets={"X": "NET1"}, refs={"X": "B"})
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"X": "B"}  # Must pick B despite ambiguity


def _make_fp_with_sheet(ref, role, nets, sheet_uuid):
    """Like _make_fp, but also carries sheet_path (see _make_anchor_fp) — for
    testing anchor_sheet narrowing of TEMPLATE roles (resolve_roles_by_nets),
    as opposed to anchor resolution (resolve_anchor_by_role)."""
    fp = _make_fp(ref, role, nets)
    fp.sheet_path.path = [MagicMock(value=sheet_uuid), MagicMock(value=f"{ref}-own-uuid")]
    return fp


class TestResolveRolesByNetsAnchorSheet:
    """anchor_sheet narrowing for ambiguous TEMPLATE roles (added 2026-07-28):
    a GLOBAL net (e.g. +3V3, shared by every instance of a role board‑wide,
    unlike a per‑channel hierarchical net) leaves candidates=all instances,
    and Cluster can't help when the schematic reuses one physical section per
    channel via a hierarchical sheet (Cluster is then shared across every
    instance too) — anchor_sheet is the only signal left, same mechanism
    already used for anchor resolution itself (see TestResolveAnchorByRole)."""

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

        clone = ClonePlacement(name="c1", cell="pi_filter", xy=(0, 0),
                               nets={"CAP_IN": "+3V3"}, anchor_sheet="Channel_1")
        result = resolve_roles_by_nets(adapter, self._template(), clone, sheet_names=sheet_names)
        assert result == {"CAP_IN": "C20"}

    def test_anchor_sheet_placeholder_substituted_from_params(self):
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

        clone = ClonePlacement(name="c1", cell="pi_filter", xy=(0, 0),
                               nets={"CAP_IN": "+3V3"}, anchor_sheet="Channel_{channel}",
                               params={"channel": 1})
        result = resolve_roles_by_nets(adapter, self._template(), clone, sheet_names=sheet_names)
        assert result == {"CAP_IN": "C20"}

    def test_insufficient_narrowing_raises_mentioning_anchor_sheet(self):
        """Two candidates share the same sheet — anchor_sheet narrows 3 -> 2,
        not enough; the fatal message must say so instead of the old
        Cluster-only wording."""
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

        clone = ClonePlacement(name="c1", cell="pi_filter", xy=(0, 0),
                               nets={"CAP_IN": "+3V3"}, anchor_sheet="Channel_0")
        with pytest.raises(ValidationError, match="anchor_sheet") as exc_info:
            resolve_roles_by_nets(adapter, self._template(), clone, sheet_names=sheet_names)
        msg = str(exc_info.value)
        assert "C10" in msg and "C11" in msg and "C20" not in msg

    def test_no_anchor_sheet_or_cluster_falls_back_to_old_hint(self):
        """Regression: neither anchor_sheet nor anchor_cluster set — hint text
        must still make sense (updated wording, not just 'Cluster not set')."""
        fps = [
            _make_fp("A", "CAP_IN", ["+3V3"]),
            _make_fp("B", "CAP_IN", ["+3V3"]),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_selected_items.return_value = []

        clone = ClonePlacement(name="c1", cell="pi_filter", xy=(0, 0),
                               nets={"CAP_IN": "+3V3"})
        with pytest.raises(ValidationError, match="anchor_sheet") as exc_info:
            resolve_roles_by_nets(adapter, self._template(), clone)
        msg = str(exc_info.value)
        assert "neither anchor_sheet nor Cluster set" in msg or "не задан" in msg


def _make_anchor_fp(ref, role, sheet_uuid):
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    fp._role = role
    # resolve_sheet_path_names reads fp.sheet_path.path[:-1] (last entry is
    # the component's own uuid, excluded) — see kicadstamp/sheet_names.py.
    fp.sheet_path.path = [MagicMock(value=sheet_uuid), MagicMock(value=f"{ref}-own-uuid")]
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
        clone = ClonePlacement(name="c0", cell="t", xy=(0, 0),
                               anchor_role="AD_DAC", anchor_sheet="Channel_{channel}",
                               params={"channel": 0})
        result = resolve_anchor_by_role(adapter, clone, sheet_names)
        assert result.reference_field.text.value == "IC2"

    def test_anchor_sheet_missing_param_raises(self):
        fps = [_make_anchor_fp("IC2", "AD_DAC", "sheet-uuid-0")]
        adapter = self._adapter(fps)
        clone = ClonePlacement(name="c0", cell="t", xy=(0, 0),
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
        clone = ClonePlacement(name="c0", cell="t", xy=(0, 0),
                               anchor_role="AD_DAC", anchor_sheet="Channel_0")
        result = resolve_anchor_by_role(adapter, clone, sheet_names)
        assert result.reference_field.text.value == "IC2"


class TestSuggestRoleNetsFromCluster:
    """PlacerDock's Nets-tab "Auto-fill from board" button (2026-08-12) —
    read-only, never raises: unresolvable roles are simply absent from the
    returned dict, left for the user to fill by hand."""

    def test_unique_candidate_with_one_non_rule_net_is_suggested(self):
        adapter = MagicMock()
        adapter.get_footprints.return_value = [
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2", "GND"]),
        ]
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads

        result = suggest_role_nets_from_cluster(adapter, ["C_IN_BULK"], "Out_Pi_Filter_N2V5")

        assert result == {"C_IN_BULK": "+1V2"}

    def test_cluster_prefix_match_narrows_like_the_real_resolver(self):
        """Same cluster_prefix_match signal resolve_roles_by_nets's own step
        3 narrowing uses — a sub-segment tag still matches the parent."""
        adapter = MagicMock()
        adapter.get_footprints.return_value = [
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5/sub", nets=["+1V2"]),
        ]
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads

        result = suggest_role_nets_from_cluster(adapter, ["C_IN_BULK"], "Out_Pi_Filter_N2V5")

        assert result == {"C_IN_BULK": "+1V2"}

    def test_role_absent_from_result_when_no_candidate(self):
        adapter = MagicMock()
        adapter.get_footprints.return_value = [
            _make_fp("C22", role="C_IN_BULK", cluster="Some_Other_Cluster", nets=["+1V2"]),
        ]
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads

        result = suggest_role_nets_from_cluster(adapter, ["C_IN_BULK"], "Out_Pi_Filter_N2V5")

        assert result == {}

    def test_role_absent_from_result_when_ambiguous(self):
        adapter = MagicMock()
        adapter.get_footprints.return_value = [
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2"]),
            _make_fp("C23", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2"]),
        ]
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads

        result = suggest_role_nets_from_cluster(adapter, ["C_IN_BULK"], "Out_Pi_Filter_N2V5")

        assert result == {}

    def test_role_absent_from_result_when_candidate_has_multiple_non_rule_nets(self):
        """A bridging component (e.g. PI_FB) can't be reduced to one
        identifying net — same "don't guess" stance as net_from_role's
        lemma 2 ambiguity."""
        adapter = MagicMock()
        adapter.get_footprints.return_value = [
            _make_fp("FB6", role="PI_FB", cluster="Out_Pi_Filter_N2V5", nets=["+1V2", "+1V2_VCCINT"]),
        ]
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads

        result = suggest_role_nets_from_cluster(adapter, ["PI_FB"], "Out_Pi_Filter_N2V5")

        assert result == {}

    def test_role_absent_from_result_when_only_net_is_a_rule_net(self):
        adapter = MagicMock()
        adapter.get_footprints.return_value = [
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["GND"]),
        ]
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads

        result = suggest_role_nets_from_cluster(adapter, ["C_IN_BULK"], "Out_Pi_Filter_N2V5")

        assert result == {}

    def test_custom_rule_nets_respected(self):
        adapter = MagicMock()
        adapter.get_footprints.return_value = [
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2", "+5V"]),
        ]
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads

        result = suggest_role_nets_from_cluster(adapter, ["C_IN_BULK"], "Out_Pi_Filter_N2V5",
                                                 rule_nets={"+5V"})

        assert result == {"C_IN_BULK": "+1V2"}

    def test_multiple_roles_mixed_success_and_failure(self):
        adapter = MagicMock()
        adapter.get_footprints.return_value = [
            _make_fp("C22", role="C_IN_BULK", cluster="Out_Pi_Filter_N2V5", nets=["+1V2"]),
            _make_fp("FB6", role="PI_FB", cluster="Out_Pi_Filter_N2V5", nets=["+1V2", "+1V2_VCCINT"]),
        ]
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads

        result = suggest_role_nets_from_cluster(adapter, ["C_IN_BULK", "PI_FB"], "Out_Pi_Filter_N2V5")

        assert result == {"C_IN_BULK": "+1V2"}