#!/usr/bin/env python3
"""Tests for the core net-from-role resolver (plan step 2): port of the
synthetic cases validated live in this session (see handoff_2026_08_11_net_
from_role_audit_validation.md) — lemma 2, explicit pad for multi-net roles
(LDO VIN/VOUT counterexample to autoweight), and the geometric tiebreak for
|R(n)| > 1 (nearest component wins)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.net_from_role_resolver import (
    classify_net, canonical_role, local_points,
)


def _rn(pad_nets: dict[str, str]) -> dict[str, dict[str, set[str]]]:
    """Turn {pad: net} into the role_nets shape {role: {pad: {net}}}."""
    return {p: {n} for p, n in pad_nets.items()}


# --------------------------------------------------------------------------
# lemma 2: two-pin role with GND as the rule net
# --------------------------------------------------------------------------

class TestLemma2:
    def test_single_non_rule_net_needs_no_pad(self):
        # C_OUT_BULK: one pad on +3V3 (non-rule), the other on GND (rule).
        role_nets = {"C_OUT_BULK": {"1": {"+3V3"}, "2": {"GND"}}}
        role, pad = classify_net(role_nets, "+3V3", {"C_OUT_BULK"})
        assert role == "C_OUT_BULK"
        assert pad is None

    def test_rule_net_needs_no_role(self):
        role_nets = {"C_OUT_BULK": {"1": {"+3V3"}, "2": {"GND"}}}
        role, pad = classify_net(role_nets, "GND", {"C_OUT_BULK"})
        assert role is None
        assert pad is None

    def test_case_and_synonym_tolerant(self):
        # Cell writes FB_PI_FLT, the graph knows PI_FB — same canonical role.
        role_nets = {"PI_FB": {"1": {"+3V3"}, "2": {"GND"}}}
        role, pad = classify_net(role_nets, "+3V3", {"FB_PI_FLT"})
        assert role == "PI_FB"
        assert canonical_role("FB_PI_FLT") == canonical_role("PI_FB")


# --------------------------------------------------------------------------
# pad: multi-net role (LDO VIN/VOUT — the autoweight counterexample)
# --------------------------------------------------------------------------

class TestPad:
    def test_ldo_vout_requires_explicit_pad(self):
        # LDO has VIN on +5V, VOUT on +3V3, GND — a multi-net role. A via on
        # VOUT cannot be attributed by lemma 2 (the role has several non-rule
        # nets); it needs net_from_role: LDO, pad: <vout pad>.
        role_nets = {
            "LDO": {"1": {"+5V"}, "2": {"+3V3"}, "3": {"GND"}},
        }
        role, pad = classify_net(role_nets, "+3V3", {"LDO"})
        assert role == "LDO"
        assert pad == "2"  # the pad whose net is +3V3

    def test_ldo_vin_requires_explicit_pad(self):
        role_nets = {
            "LDO": {"1": {"+5V"}, "2": {"+3V3"}, "3": {"GND"}},
        }
        role, pad = classify_net(role_nets, "+5V", {"LDO"})
        assert role == "LDO"
        assert pad == "1"


# --------------------------------------------------------------------------
# geometric tiebreak: |R(n)| > 1, several roles on the same net
# --------------------------------------------------------------------------

def _slot(role, along, across):
    return {"role": role, "offset_along_mm": along, "offset_across_mm": across}


class TestGeometricTiebreak:
    def test_chooses_nearest_component(self):
        # Two 2-pin caps share net +5V (common bus). Via at (1, 0) is near C1
        # at (0, 0); via at (9, 0) is near C2 at (10, 0).
        role_nets = {
            "C1": {"1": {"+5V"}, "2": {"GND"}},
            "C2": {"1": {"+5V"}, "2": {"GND"}},
        }
        comps = [_slot("C1", 0.0, 0.0), _slot("C2", 10.0, 0.0)]

        role, pad = classify_net(
            role_nets, "+5V", {"C1", "C2"},
            points=[(1.0, 0.0)], components=comps)
        assert role == "C1"
        assert pad is None

        role, pad = classify_net(
            role_nets, "+5V", {"C1", "C2"},
            points=[(9.0, 0.0)], components=comps)
        assert role == "C2"
        assert pad is None

    def test_track_uses_min_endpoint_distance(self):
        # Track from (0.0, 2.0) to (9.0, 0.0): nearest C1 endpoint is (0,2) at
        # 2.0mm, nearest C2 endpoint is (9,0) at 1.0mm -> distance is the MIN
        # over both endpoints, so C2 wins.
        role_nets = {
            "C1": {"1": {"+5V"}, "2": {"GND"}},
            "C2": {"1": {"+5V"}, "2": {"GND"}},
        }
        comps = [_slot("C1", 0.0, 0.0), _slot("C2", 10.0, 0.0)]
        pts = [(0.0, 2.0), (9.0, 0.0)]
        role, _ = classify_net(role_nets, "+5V", {"C1", "C2"},
                               points=pts, components=comps)
        assert role == "C2"  # d(C1)=2.0, d(C2)=1.0

    def test_ambiguity_without_geometry_is_fatal(self):
        role_nets = {
            "C1": {"1": {"+5V"}, "2": {"GND"}},
            "C2": {"1": {"+5V"}, "2": {"GND"}},
        }
        with pytest.raises(ValidationError, match="shared by several"):
            classify_net(role_nets, "+5V", {"C1", "C2"})


# --------------------------------------------------------------------------
# fallback: M(n) empty
# --------------------------------------------------------------------------

class TestFallback:
    def test_no_candidate_on_net_is_fatal(self):
        role_nets = {"C_OUT_BULK": {"1": {"+3V3"}, "2": {"GND"}}}
        with pytest.raises(ValidationError, match="no role pad on net"):
            classify_net(role_nets, "+1V8", {"C_OUT_BULK"})

    def test_rule_net_even_without_candidate_is_fine(self):
        # A rule net (GND) needs no role at all — even if the role set is empty.
        role, pad = classify_net({}, "GND", set())
        assert role is None
        assert pad is None


# --------------------------------------------------------------------------
# local_points helper (dict + dataclass forms)
# --------------------------------------------------------------------------

class TestLocalPoints:
    def test_via_dict(self):
        assert local_points({"offset_along_mm": 1.0, "offset_across_mm": 2.0}) == [(1.0, 2.0)]

    def test_track_dict(self):
        assert local_points({"start_along_mm": 0.0, "start_across_mm": 1.0,
                             "end_along_mm": 2.0, "end_across_mm": 3.0}) == [
            (0.0, 1.0), (2.0, 3.0)]
