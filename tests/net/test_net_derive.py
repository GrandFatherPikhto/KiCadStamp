# tests/test_net_derive.py
"""Tests for kicadstamp/net_derive.py — the Phase 0 contract
`derive_role_nets`: {role: NetDerivation(net, source)} with the three-priority
rule (live_pad -> prefix_remap -> kuhn/kuhn_scc_group). The function is pure
(callers gather adapter data); here it is exercised with plain dict data (the
"mocked adapter" evidence), per plan_2026_08_28_auto_nets_full_automation.md
Phase 0 step 0.2.
"""
from kicadstamp.net_derive import (
    KUHN,
    KUHN_SCC_GROUP,
    LIVE_PAD,
    PREFIX_REMAP,
    derive_role_nets,
)


def test_priority_1_live_pad_wins_over_remap_and_kuhn():
    """A net already known ON the target (live read) beats both the prefix
    remap and the Kuhn correspondence — it is the strongest evidence."""
    result = derive_role_nets(
        roles=["A", "B", "C"],
        role_source_nets={"A": "/Channel_0/X", "B": "/Channel_0/Y",
                          "C": "/FPGA/Z"},  # not prefix-remappable -> kuhn
        live_pad_nets={"A": "/Channel_1/X"},
        source_prefix="/Channel_0/", target_prefix="/Channel_1/",
        kuhn_mapping={"/FPGA/Z": "/Channel_1/ZZ"},
    )
    assert result["A"].net == "/Channel_1/X"
    assert result["A"].source == LIVE_PAD
    # B fell through to prefix remap; C (non-prefix source) to kuhn.
    assert result["B"].source == PREFIX_REMAP
    assert result["C"].source == KUHN


def test_priority_2_prefix_remap_of_source_net():
    """Hierarchical prefix remap (/Channel_0/X -> /Channel_1/X) — the
    TwinMap.twin_net semantics for symmetric twin clusters."""
    result = derive_role_nets(
        roles=["A", "B"],
        role_source_nets={"A": "/Channel_0/DAC/DB0", "B": "GND"},
        source_prefix="/Channel_0/", target_prefix="/Channel_1/",
    )
    assert result["A"].net == "/Channel_1/DAC/DB0"
    assert result["A"].source == PREFIX_REMAP
    # B's net does not start with the source prefix -> no remap -> absent
    # (global nets are deliberately out of scope for Phase 0; Phase 2 design).
    assert "B" not in result


def test_priority_3_kuhn_plain():
    """A source net mapped to the target via Kuhn, not in any ambiguous SCC —
    provenance is plain kuhn."""
    result = derive_role_nets(
        roles=["A"],
        role_source_nets={"A": "/Channel_0/N1"},
        kuhn_mapping={"/Channel_0/N1": "/Channel_1/M1"},
    )
    assert result["A"].net == "/Channel_1/M1"
    assert result["A"].source == KUHN
    assert result["A"].ambiguous_group is None


def test_priority_3_kuhn_scc_group_provenance():
    """A source net inside an ambiguous SCC maps via Kuhn but carries the
    kuhn_scc_group provenance (any SCC member is a valid answer — safe
    default) plus the group itself, for the diagnostic layer."""
    group = frozenset(["/Channel_0/N1", "/Channel_0/N2"])
    result = derive_role_nets(
        roles=["A"],
        role_source_nets={"A": "/Channel_0/N1"},
        kuhn_mapping={"/Channel_0/N1": "/Channel_1/M1",
                      "/Channel_0/N2": "/Channel_1/M2"},
        kuhn_scc_groups=[group],
    )
    assert result["A"].net == "/Channel_1/M1"
    assert result["A"].source == KUHN_SCC_GROUP
    assert result["A"].ambiguous_group == group


def test_role_with_no_priority_is_absent():
    """A role with no live read, no matching prefix, and no Kuhn entry is
    simply absent — the caller decides the fallback, never a silent guess."""
    result = derive_role_nets(
        roles=["A", "B"],
        role_source_nets={"A": "/Channel_0/X", "B": "/Other/Y"},
        source_prefix="/Channel_0/", target_prefix="/Channel_1/",
    )
    assert "A" in result  # remapped
    assert "B" not in result  # no priority applies


def test_empty_inputs_produce_empty_result():
    assert derive_role_nets(roles=[], role_source_nets={}) == {}


def test_live_pad_role_without_source_net_still_derived():
    """Priority 1 does not need the source net at all — the live read is
    self-sufficient evidence."""
    result = derive_role_nets(
        roles=["A"],
        role_source_nets={},
        live_pad_nets={"A": "+3V3"},
    )
    assert result["A"].net == "+3V3"
    assert result["A"].source == LIVE_PAD
