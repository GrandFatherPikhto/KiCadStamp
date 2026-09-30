# tests/test_net_matching.py
"""Tests for kicadstamp/net_matching.py — the production port of the validated
sandbox `diagnostics/net_matching_sandbox.py` (sessions 2026-08-27/08-28).

What these tests lock in (thesis, previously only in gitignored diagnostics):
  - strict (role, pin) model -> the compatibility graph is a partial bijection
    -> matching, whenever it exists, is UNIQUE (no alternating cycle);
  - genuine ambiguity arises exactly where a role is physically symmetric
    (symmetric_roles) — detected FORMALLY via Tarjan SCC over the swap graph;
  - SAFE DEFAULT: every element of an ambiguous SCC passes validate_mapping
    (Assertions A/B/C), so the matching Kuhn built is a valid automatic answer,
    not a guess;
  - non-isomorphism is a REAL error -> ValidationError (production convention),
    never a silent guess.

Scenarios 1-6 mirror the sandbox exactly (same graphs, same expectations).
"""
import random

import pytest

from kicadstamp.exceptions import ValidationError
from kicadstamp.net_matching import (
    Graph,
    enumerate_scc_alternative_mappings,
    find_perfect_matching,
    match_template_to_target,
    prove_unique_or_report_ambiguity,
    refined_colors,
    validate_mapping,
)


def _assert_ambiguous(ambiguous: list[list[str]], expected_groups: list[list[str]]) -> None:
    got = {frozenset(g) for g in ambiguous}
    want = {frozenset(g) for g in expected_groups}
    assert got == want, f"ambiguous groups mismatch: got {got}, want {want}"


def _verify_scc_safe_default(template: Graph, target: Graph,
                             expected_counts: dict[frozenset, int],
                             label: str) -> None:
    """Assertions A/B/C of plan_2026_08_28_net_matching_ambiguous_scc_safe_default:
    rebuild the exact full matching the pipeline builds (match_template_to_target
    discards it once an ambiguity is found — it only returns the claimable
    part), then for EVERY ambiguous SCC group enumerate all compat-respecting
    bijections and require:
      A — the number of alternatives is the expected one (derived from the
          group's structure — K_{k,k} compat -> k! — not an arbitrary constant);
      B — the MAIN claim: every alternative passes validate_mapping (if any
          fails, the "any SCC member is a valid answer" thesis is broken);
      C — the pipeline's own matching is itself one of the alternatives
          (regression guard: if not, enumerate_scc_alternative_mappings is
          mis-built, not find_perfect_matching)."""
    t_colors = refined_colors(template)
    g_colors = refined_colors(target)
    g_by_color: dict[str, list[str]] = {}
    for n, c in g_colors.items():
        if n not in target.global_nets:
            g_by_color.setdefault(c, []).append(n)
    left_nodes = sorted(n for n in t_colors if n not in template.global_nets)
    compat = {u: set(g_by_color.get(t_colors[u], ())) for u in left_nodes}
    matching = find_perfect_matching(left_nodes, compat)
    assert matching is not None, f"{label}: no perfect matching"
    ambiguous = prove_unique_or_report_ambiguity(left_nodes, compat, matching)
    assert ambiguous, f"{label}: expected at least one ambiguous group"

    counts: dict[frozenset, int] = {}
    for group in ambiguous:
        key = frozenset(group)
        alternatives = enumerate_scc_alternative_mappings(group, compat, matching)
        counts[key] = len(alternatives)
        # A — expected alternative count.
        want = expected_counts[key]
        assert len(alternatives) == want, (
            f"{label}: group {sorted(group)}: expected {want} alternative "
            f"mappings, got {len(alternatives)}")
        # B — every alternative must be a valid mapping.
        for i, alt in enumerate(alternatives):
            if not validate_mapping(template, target, alt):
                raise AssertionError(
                    f"{label}: alternative #{i} for group {sorted(group)} "
                    f"({alt}) FAILS validate_mapping — the 'any SCC member "
                    f"is a valid answer' thesis is broken")
        # C — the pipeline's own matching is among the alternatives.
        restricted = {u: matching[u] for u in group}
        assert restricted in alternatives, (
            f"{label}: pipeline's own matching {restricted} for group "
            f"{sorted(group)} is NOT among the enumerated alternatives — "
            f"enumerate_scc_alternative_mappings is mis-built")


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 1 — trivial asymmetric cluster (unique, provably)
# ═══════════════════════════════════════════════════════════════════════════

def test_scenario_1_trivial_asymmetric_unique():
    template = Graph(roles={"DAC": {1: "N1", 2: "N2"}, "OPAMP": {1: "N2", 2: "GND"}},
                     global_nets={"GND"})
    target = Graph(roles={"DAC": {1: "M7", 2: "M8"}, "OPAMP": {1: "M8", 2: "GND"}},
                   global_nets={"GND"})
    mapping, ambiguous = match_template_to_target(template, target)
    assert mapping == {"N1": "M7", "N2": "M8"}
    assert ambiguous == []


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 2 — symmetric 2-pin role (genuine ambiguity) + safe-default + contrast
# ═══════════════════════════════════════════════════════════════════════════

def test_scenario_2_symmetric_role_ambiguity_and_safe_default():
    # A non-polar resistor: pin 1 vs pin 2 is arbitrary KiCad marking, so the
    # two nets on it are interchangeable.
    template = Graph(roles={"R": {1: "N1", 2: "N2"}}, symmetric_roles={"R"})
    target = Graph(roles={"R": {1: "M1", 2: "M2"}}, symmetric_roles={"R"})
    mapping, ambiguous = match_template_to_target(template, target)
    assert mapping == {}                       # nothing resolvable on its own
    _assert_ambiguous(ambiguous, [["N1", "N2"]])  # the pair stays for the human

    # Safe default: complete K_{2,2} -> 2! = 2 alternatives, BOTH valid.
    _verify_scc_safe_default(
        template, target,
        {frozenset(["N1", "N2"]): 2},
        "Scenario 2")


def test_scenario_2_contrast_without_symmetric_marking_is_spuriously_unique():
    """The SAME graph WITHOUT the symmetric marking is (mis)reported as unique —
    pin 1->pin 1 / pin 2->pin 2. This is exactly why the marking (production:
    net_template_same_as_role vs net_template_pad) matters."""
    strict_template = Graph(roles={"R": {1: "N1", 2: "N2"}})
    strict_target = Graph(roles={"R": {1: "M1", 2: "M2"}})
    m2, amb2 = match_template_to_target(strict_template, strict_target)
    assert m2 == {"N1": "M1", "N2": "M2"} and amb2 == []


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 3 — partial symmetry, no infection
# ═══════════════════════════════════════════════════════════════════════════

def test_scenario_3_partial_symmetry_no_infection():
    """One graph, two disconnected parts: OPAMP resolves uniquely, RA (symmetric
    resistor) is genuinely ambiguous. The global alternating-cycle check must
    NOT drag the resolved N1/N2 into the ambiguous group (SCCs are disjoint)."""
    template = Graph(roles={"OPAMP": {1: "N1", 2: "N2"}, "RA": {1: "N3", 2: "N4"}},
                     symmetric_roles={"RA"})
    target = Graph(roles={"OPAMP": {1: "M1", 2: "M2"}, "RA": {1: "M3", 2: "M4"}},
                   symmetric_roles={"RA"})
    mapping, ambiguous = match_template_to_target(template, target)
    assert mapping == {"N1": "M1", "N2": "M2"}
    _assert_ambiguous(ambiguous, [["N3", "N4"]])


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 4 — symmetric-role cycle (connected, still ambiguous) + safe-default
# ═══════════════════════════════════════════════════════════════════════════

def test_scenario_4_symmetric_cycle_and_safe_default():
    """R1 and R2 are both symmetric; their four nets form a SINGLE connected
    component (N1-R1-N2-R2-N1), yet the whole component is genuinely ambiguous:
    reflecting the cycle swaps N1<->N2 and N3<->N4, a second perfect matching.
    Shows ambiguity is not just about disconnected islands."""
    template = Graph(roles={"R1": {1: "N1", 2: "N2"}, "R2": {1: "N2", 2: "N1"}},
                     symmetric_roles={"R1", "R2"})
    target = Graph(roles={"R1": {1: "M1", 2: "M2"}, "R2": {1: "M2", 2: "M1"}},
                   symmetric_roles={"R1", "R2"})
    mapping, ambiguous = match_template_to_target(template, target)
    assert mapping == {}
    _assert_ambiguous(ambiguous, [["N1", "N2"]])

    _verify_scc_safe_default(
        template, target,
        {frozenset(["N1", "N2"]): 2},  # both nets share one role set -> K_{2,2}
        "Scenario 4")


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 5 — strict model is ALWAYS unique; non-isomorphism is a real error
# ═══════════════════════════════════════════════════════════════════════════

def _random_graph(rng: random.Random) -> Graph:
    """Random strict (all-asymmetric) graph: 2-4 roles, 2-3 pins each; nets
    drawn from a small shared pool (plus GND) so roles genuinely share nets."""
    n_roles = rng.randint(2, 4)
    pool = [f"S{j}" for j in range(rng.randint(3, 6))] + ["GND"]
    roles: dict[str, dict[int, str]] = {}
    for i in range(n_roles):
        roles[f"R{i}"] = {p: rng.choice(pool) for p in range(1, rng.randint(2, 4))}
    return Graph(roles=roles, global_nets={"GND"})


def test_scenario_5_strict_model_signatures_are_globally_unique():
    """Lemma: with strict (role, pin) signatures each (role, pin) belongs to
    exactly one net, so the incident-(role, pin) sets of distinct nets are
    pairwise disjoint -> every non-global 0-hop signature is globally unique ->
    the compatibility graph is a partial bijection, no alternating cycle can
    exist, matching is unique whenever it exists."""
    rng = random.Random(20260827)
    for trial in range(50):
        g = _random_graph(rng)
        colors = refined_colors(g)
        sigs = [c for n, c in colors.items() if n not in g.global_nets]
        assert len(sigs) == len(set(sigs)), \
            f"strict signatures must be distinct (trial {trial}): {g.roles}"


def test_scenario_5_non_isomorphic_pair_is_validation_error_not_guess():
    """Non-isomorphic structures are a REAL error (no perfect matching), not an
    ambiguity: the target's OPAMP pin 1 sits on a different net than the
    template's, so N2 loses its candidate. Production convention: ValidationError
    (never the sandbox's bare RuntimeError, never a silent guess)."""
    template = Graph(roles={"DAC": {1: "N1", 2: "N2"}, "OPAMP": {1: "N2", 2: "GND"}},
                     global_nets={"GND"})
    target = Graph(roles={"DAC": {1: "M1", 2: "M2"}, "OPAMP": {1: "M3", 2: "GND"}},
                   global_nets={"GND"})
    with pytest.raises(ValidationError, match="not isomorphic"):
        match_template_to_target(template, target)


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 6 — ambiguous SCC group of SIZE 3 (safe-default thesis, k>2)
# ═══════════════════════════════════════════════════════════════════════════

def test_scenario_6_scc_group_of_size_three_and_safe_default():
    """Production rationale is a symmetric 2-pin R/C, and with 2-pin roles every
    ambiguous group is exactly a PAIR (a k-pin symmetric role is the only way a
    group can exceed 2). The Graph model does not enforce 2 pins, so we exercise
    the k>2 machinery with a 3-pin symmetric role: complete K_{3,3} -> 3! = 6
    perfect matchings, every one a valid answer."""
    template = Graph(roles={"R3": {1: "N1", 2: "N2", 3: "N3"}},
                     symmetric_roles={"R3"})
    target = Graph(roles={"R3": {1: "M1", 2: "M2", 3: "M3"}},
                   symmetric_roles={"R3"})
    mapping, ambiguous = match_template_to_target(template, target)
    assert mapping == {}
    _assert_ambiguous(ambiguous, [["N1", "N2", "N3"]])
    _verify_scc_safe_default(
        template, target,
        {frozenset(["N1", "N2", "N3"]): 6},  # complete K_{3,3} -> 3! = 6
        "Scenario 6")


def test_scenario_6_contrast_three_cycle_of_two_pin_roles_is_unique():
    """A 3-CYCLE of 2-pin symmetric roles is NOT ambiguous — each net touches a
    distinct role set (N1:{R1,R3}, N2:{R1,R2}, N3:{R2,R3}), so the signatures
    are globally unique. A "cycle of roles" alone never creates an ambiguous
    group; only nets that share the SAME role set do."""
    cycle_template = Graph(
        roles={"R1": {1: "N1", 2: "N2"}, "R2": {1: "N2", 2: "N3"},
               "R3": {1: "N3", 2: "N1"}},
        symmetric_roles={"R1", "R2", "R3"})
    cycle_target = Graph(
        roles={"R1": {1: "M1", 2: "M2"}, "R2": {1: "M2", 2: "M3"},
               "R3": {1: "M3", 2: "M1"}},
        symmetric_roles={"R1", "R2", "R3"})
    m2, amb2 = match_template_to_target(cycle_template, cycle_target)
    assert m2 == {"N1": "M1", "N2": "M2", "N3": "M3"} and amb2 == []


# ═══════════════════════════════════════════════════════════════════════════
# Boundary/guard tests (port-only, not in the sandbox's scenario list)
# ═══════════════════════════════════════════════════════════════════════════

def test_global_nets_must_match_before_matching():
    template = Graph(roles={"R": {1: "N1", 2: "GND"}}, global_nets={"GND"})
    target = Graph(roles={"R": {1: "M1", 2: "VCC"}}, global_nets={"VCC"})
    with pytest.raises(ValidationError, match="pre-matched"):
        match_template_to_target(template, target)


def test_symmetric_roles_must_match_across_sides():
    template = Graph(roles={"R": {1: "N1", 2: "N2"}}, symmetric_roles={"R"})
    target = Graph(roles={"R": {1: "M1", 2: "M2"}}, symmetric_roles=set())
    with pytest.raises(ValidationError, match="symmetric-role marking"):
        match_template_to_target(template, target)


def test_enumerate_scc_alternative_mappings_rejects_mismatched_candidates():
    """A perfect-matching SCC must have exactly one candidate per left node — a
    mismatch is a caller bug, surfaced as ValidationError, never a quiet skip."""
    with pytest.raises(ValidationError, match="candidate count"):
        enumerate_scc_alternative_mappings(
            ["N1", "N2"], {"N1": {"M1", "M2", "M3"}, "N2": {"M1", "M2", "M3"}},
            {"N1": "M1", "N2": "M2"})
