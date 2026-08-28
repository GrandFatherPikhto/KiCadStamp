#!/usr/bin/env python3
"""Tests for net_resolution.py — three‑layer net name resolution for TemplatePlacer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.net_resolution import (
    discover_net_template_pattern, resolve_net, resolve_placeholder, resolve_net_from_role,
)
from kicadstamp.exceptions import ValidationError


class TestResolveNet:
    def test_literal_unchanged(self):
        assert resolve_net("GND", {}, {}) == "GND"

    def test_placeholder_substituted_from_params(self):
        assert resolve_net("DAC{channel}_DB1", {"channel": 2}, {}) == "DAC2_DB1"

    def test_multiple_placeholders(self):
        assert resolve_net("DAC{channel}_CLK_{polarity}", {"channel": 3, "polarity": "N"}, {}) == "DAC3_CLK_N"

    def test_net_overrides_applied_to_literal(self):
        result = resolve_net("/STM32F4xx/BOOT0", {}, {"/STM32F4xx/BOOT0": "/STM32F4xx_2/BOOT0"})
        assert result == "/STM32F4xx_2/BOOT0"

    def test_net_overrides_applied_to_resolved_not_template(self):
        """Override must match the ALREADY substituted name, not the template with placeholders."""
        result = resolve_net("DAC{channel}_DB1", {"channel": 2}, {"DAC2_DB1": "DAC2_DB1_SPECIAL"})
        assert result == "DAC2_DB1_SPECIAL"

    def test_override_for_different_channel_does_not_apply(self):
        """Override for DAC2_DB1 must not affect DAC3_DB1."""
        result = resolve_net("DAC{channel}_DB1", {"channel": 3}, {"DAC2_DB1": "SHOULD_NOT_APPLY"})
        assert result == "DAC3_DB1"

    def test_missing_param_raises_fatal_error(self):
        """
        Regression for a real bug: `if params:` skipped `.format()` entirely when
        params was EMPTY (empty dict is falsy!), so placeholders silently remained
        unchanged instead of raising a proper error.
        """
        with pytest.raises(ValidationError, match="channel"):
            resolve_net("DAC{channel}_DB1", {}, {})

    def test_missing_one_of_several_params_raises(self):
        with pytest.raises(ValidationError, match="polarity"):
            resolve_net("DAC{channel}_CLK_{polarity}", {"channel": 1}, {})

    def test_extra_unused_params_are_harmless(self):
        """Extra params not used in the template must not interfere."""
        assert resolve_net("GND", {"channel": 5, "unused": "x"}, {}) == "GND"


class TestResolvePlaceholder:
    """resolve_net's substitution engine, extracted so ClonePlacement.anchor_sheet
    can reuse it too (see clone_role_resolver.resolve_anchor_by_role) — same
    behaviour, no net_overrides step."""

    def test_literal_unchanged(self):
        assert resolve_placeholder("Channel_0", {}) == "Channel_0"

    def test_placeholder_substituted(self):
        assert resolve_placeholder("Channel_{channel}", {"channel": 2}) == "Channel_2"

    def test_missing_param_raises_fatal_error(self):
        with pytest.raises(ValidationError, match="channel"):
            resolve_placeholder("Channel_{channel}", {})

    def test_error_message_uses_what_label(self):
        with pytest.raises(ValidationError, match="anchor_sheet"):
            resolve_placeholder("Channel_{channel}", {}, what="anchor_sheet")


class _Net:
    def __init__(self, name: str):
        self.name = name


class _Pad:
    def __init__(self, number: str, net_name: str):
        self.number = number
        self.net_name = net_name


class _Fp:
    """Fake footprint: {pad_number: net_name}."""

    def __init__(self, pad_nets: dict[str, str]):
        self._pads = pad_nets

    @property
    def pads(self):
        return [_Pad(n, net) for n, net in self._pads.items()]


class _FakeAdapter:
    """Minimal adapter duck-type: get_footprint / get_footprint_pads /
    get_pad_by_number, as used by resolve_net_from_role."""

    def __init__(self, footprints: dict[str, _Fp]):
        self._fps = footprints

    def get_footprint(self, ref):
        return self._fps.get(ref)

    def get_footprint_pads(self, fp):
        return fp.pads

    def get_pad_by_number(self, fp, pad_number):
        return next((p for p in fp.pads if p.number == str(pad_number)), None)


class TestResolveNetFromRole:
    """Live net resolution of a via/track from a role's real pad (plan step 3)."""

    def _adapter(self):
        return _FakeAdapter({
            "C1": _Fp({"1": "+3V3", "2": "GND"}),       # lemma-2 cap
            "U1": _Fp({"1": "+5V", "2": "+3V3", "3": "GND"}),  # multi-net LDO
        })

    def test_explicit_pad_returns_that_pad_net(self):
        adapter = self._adapter()
        net = resolve_net_from_role("LDO", "2", {"LDO": "U1"}, adapter)
        assert net == "+3V3"

    def test_explicit_pad_vin(self):
        adapter = self._adapter()
        net = resolve_net_from_role("LDO", "1", {"LDO": "U1"}, adapter)
        assert net == "+5V"

    def test_no_pad_lemma2_single_non_rule_net(self):
        adapter = self._adapter()
        # C1: +3V3 (non-rule) + GND (rule) -> exactly one non-rule net.
        net = resolve_net_from_role("CAP", None, {"CAP": "C1"}, adapter)
        assert net == "+3V3"

    def test_no_pad_multi_net_role_is_fatal(self):
        adapter = self._adapter()
        with pytest.raises(ValidationError, match="not exactly one"):
            resolve_net_from_role("LDO", None, {"LDO": "U1"}, adapter)

    def test_role_not_in_role_to_ref_is_fatal(self):
        adapter = self._adapter()
        with pytest.raises(ValidationError, match="not resolved in this clone"):
            resolve_net_from_role("NOPE", None, {}, adapter)

    def test_ref_not_on_board_is_fatal(self):
        adapter = self._adapter()
        with pytest.raises(ValidationError, match="not found on the board"):
            resolve_net_from_role("CAP", None, {"CAP": "Z99"}, adapter)

    def test_pad_not_found_is_fatal(self):
        adapter = self._adapter()
        with pytest.raises(ValidationError, match="pad '9' of 'C1'"):
            resolve_net_from_role("CAP", "9", {"CAP": "C1"}, adapter)

    def test_custom_rule_nets_respected(self):
        # If +3V3 is declared a rule net, C1's only non-rule net is none -> fatal.
        adapter = self._adapter()
        with pytest.raises(ValidationError, match="not exactly one"):
            resolve_net_from_role("CAP", None, {"CAP": "C1"}, adapter,
                                  rule_nets={"GND", "+3V3"})


class TestDiscoverNetTemplatePattern:
    """Phase 1 step 1.3 — single-token {param} pattern discovery across
    cluster instances, with the (a)/(b) limiter (never guess silently)."""

    def test_hierarchical_channel_pattern(self):
        found = discover_net_template_pattern(
            ["/Channel_0/DAC/DB0", "/Channel_1/DAC/DB0"])
        assert found is not None
        pattern, param_name, value = found
        assert pattern == "/Channel_{channel}/DAC/DB0"
        assert param_name == "channel"
        # Sub-token refinement: only the differing CORE is the value; the
        # common "Channel_" prefix stays in the pattern, not the value.
        assert value == "0"
        # Round-trip: applying the discovered value yields the first literal.
        assert resolve_net(pattern, {param_name: value}, {}) == "/Channel_0/DAC/DB0"
        # The other instance resolves with its own core value.
        assert resolve_net(pattern, {param_name: "1"}, {}) == "/Channel_1/DAC/DB0"

    def test_three_instances_still_one_token(self):
        found = discover_net_template_pattern(
            ["/Channel_0/OpAmp/X", "/Channel_1/OpAmp/X", "/Channel_2/OpAmp/X"])
        assert found is not None
        pattern, _, _ = found
        assert pattern == "/Channel_{channel}/OpAmp/X"

    def test_less_than_two_literals_returns_none(self):
        assert discover_net_template_pattern(["/Channel_0/DAC/DB0"]) is None
        assert discover_net_template_pattern([]) is None

    def test_multiple_differing_segments_returns_none(self):
        """Two logical nets mixed in one set -> more than one varying segment
        -> no pattern (a bridging role's two nets must not fake a pattern)."""
        assert discover_net_template_pattern(
            ["/Channel_0/A", "/Channel_0/B", "/Channel_1/A", "/Channel_1/B"]) is None

    def test_differing_segment_count_returns_none(self):
        assert discover_net_template_pattern(
            ["/Channel_0/X", "/Channel_0/DAC/X"]) is None

    def test_flat_non_hierarchical_single_token(self):
        found = discover_net_template_pattern(["DAC0_DB1", "DAC1_DB1"])
        assert found is not None
        pattern, param_name, value = found
        # "DAC0_DB1".split('/') == ["DAC0_DB1"]; one differing segment. The
        # common "DAC" prefix and "_DB1" suffix (both instances share the
        # underscore) stay in the pattern; the value is only the differing
        # core ("0" / "1").
        assert pattern == "DAC{dac}_DB1"
        assert param_name == "dac"
        assert value == "0"
        assert resolve_net(pattern, {param_name: value}, {}) == "DAC0_DB1"
        assert resolve_net(pattern, {param_name: "1"}, {}) == "DAC1_DB1"