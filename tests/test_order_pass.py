# tests/test_order_pass.py
"""Tests for kicadstamp/order_pass.py — the project's ONE order machine.

plan_2026_09_17_order_pass_and_component_node, Э1 + guards С2/С3/С5.

The machine is deliberately free of tree/config knowledge: vertices are opaque
hashables, every dependency source is a named provider, and the only remaining
degree of freedom is the tie-breaker (group + document index). These tests pin
exactly that: the edges decide the ORDER, the document index decides the TIES,
the groups keep the queue type-homogeneous, and a cycle names the provider that
gave each of its edges.
"""
import pytest

from kicadstamp.exceptions import ValidationError
from kicadstamp.order_pass import EdgeProvider, run_order_pass


def _doc(*vertices) -> dict:
    """Document index in the given (declaration) order."""
    return {v: i for i, v in enumerate(vertices)}


def _pairs(name: str, *pairs) -> EdgeProvider:
    """A provider that yields exactly the given pairs, restricted (like a real
    one) to the vertices the run actually declares."""
    return EdgeProvider(name, lambda vertices: [p for p in pairs
                                                if p[0] in vertices and p[1] in vertices])


def _groups(by_group: dict[int, set]) -> dict:
    out: dict[object, int] = {}
    for group, members in by_group.items():
        for member in members:
            out[member] = group
    return out


def _run(vertices, providers, groups, doc, title="cycle"):
    return run_order_pass(vertices, providers,
                          group_of=lambda v: groups.get(v, 0),
                          doc_index=doc, cycle_title=title)


# ── edges decide the order ───────────────────────────────────────────────────

def test_a_provider_edge_puts_the_parent_first():
    """One provider, one edge: the child goes after the parent even though the
    document order (and the alphabet) says otherwise."""
    order = _run(["B", "A"], [_pairs("structure", ("A", "B"))],
                 _groups({0: {"A", "B"}}), _doc("B", "A"))
    assert order == ["A", "B"]


def test_two_providers_are_unioned_not_ranked():
    """Providers are a SET of edge sources, not a priority list: the order
    machine sees one graph. A→B from one provider and B→C from another gives
    A, B, C."""
    order = _run(["C", "B", "A"],
                 [_pairs("structure", ("A", "B")),
                  _pairs("module", ("B", "C"))],
                 _groups({0: {"A", "B", "C"}}), _doc("C", "B", "A"))
    assert order == ["A", "B", "C"]


def test_a_repeated_edge_is_counted_once_per_yield():
    """Two independent reasons for the same precedence must not corrupt the
    indegree bookkeeping: every yielded pair is decremented exactly once."""
    order = _run(["A", "B"],
                 [_pairs("structure", ("A", "B")),
                  _pairs("anchor", ("A", "B"))],
                 _groups({0: {"A", "B"}}), _doc("A", "B"))
    assert order == ["A", "B"]


def test_an_edge_outside_the_vertex_set_is_dropped():
    """A provider naturally produces "parent not in this run" edges (the
    planner's own warning case). Such an edge must neither crash the pass nor
    silently ADD a vertex that nobody planned."""
    order = _run(["A"], [_pairs("structure", ("OUTSIDE", "A"))],
                 _groups({0: {"A"}}), _doc("A"))
    assert order == ["A"]


def test_document_order_is_the_tie_breaker_not_the_alphabet():
    """C1/C2 (Т1.1): the alphabet must NOT decide anything. `alpha`/`beta`
    sort B before A — the document order says A first, and A wins."""
    order = _run(["beta", "alpha"], [],
                 _groups({0: {"alpha", "beta"}}), _doc("alpha", "beta"))
    assert order == ["alpha", "beta"]


def test_document_order_also_decides_among_newly_ready_vertices():
    """The queue is re-sorted at every step: a child that becomes ready
    mid-run competes with the vertices already waiting, and the document index
    decides — not who happened to be appended first."""
    # B waits for A; X is independent. Document order: X(0), A(1), B(2).
    # After A the queue is [B], which is emitted right away — the point is that
    # B's readiness (not its doc index vs X, they never coexist) is what frees
    # it, while X must still come by doc order when they do coexist.
    order = _run(["X", "B", "A"], [_pairs("structure", ("A", "B"))],
                 _groups({0: {"A", "B", "X"}}), _doc("X", "A", "B"))
    # A and X are both roots: X (doc 0) first, then A, then B.
    assert order == ["X", "A", "B"]


def test_groups_keep_a_mixed_str_int_queue_comparable():
    """C3 (Т1.1): the precondition of a mixed run is that no `str` is ever
    compared with an `int`. Records are group 0 as strings, module pass-through
    vertices are group 1 as ints — and the pass walks through both."""
    # The group is the FIRST key, so group 1 waits for every group 0 vertex —
    # the same "records first, then the module pass-through vertices" order the
    # pre-2026-09-17 module branch had.
    order = _run(["PA", 7, "D0"], [],
                 _groups({0: {"PA", "D0"}, 1: {7}}),
                 _doc("PA", 7, "D0"))
    assert order == ["PA", "D0", 7]


def test_copper_group_sinks_behind_everything_even_when_its_edges_are_missing():
    """Т2.2 — the safety net: a copper vertex with NO edge of its own still
    lands last, while its own edges (when the board supplied them) only refine
    that. Group 2 is the net, not a replacement for it."""
    groups = _groups({0: {"dac_buf", "pif"}, 2: {"2v5_oa__x"}})
    order = _run(["2v5_oa__x", "dac_buf", "pif"], [], groups,
                 _doc("2v5_oa__x", "dac_buf", "pif"))
    assert order == ["dac_buf", "pif", "2v5_oa__x"]


# ── cycles ───────────────────────────────────────────────────────────────────

def test_a_cycle_is_fatal_and_names_its_participants():
    with pytest.raises(ValidationError) as exc:
        _run(["A", "B"], [_pairs("structure", ("A", "B"), ("B", "A"))],
             _groups({0: {"A", "B"}}), _doc("A", "B"))
    assert "A, B" in str(exc.value)


def test_a_cycle_names_the_provider_of_every_edge_in_it():
    """С5 (Т1.4): with several providers the reader must know WHICH source to
    go and fix — the report tags each cycle edge with its provider."""
    with pytest.raises(ValidationError) as exc:
        _run(["A", "B"],
             [_pairs("structure", ("A", "B")),
              _pairs("module", ("B", "A"))],
             _groups({0: {"A", "B"}}), _doc("A", "B"))
    text = str(exc.value)
    assert "A -> B (structure)" in text
    assert "B -> A (module)" in text


def test_an_innocent_provider_is_not_blamed_for_the_cycle():
    """Only edges BETWEEN the vertices holding an indegree > 0 are the cycle —
    a provider whose edge feeds into the cycle from outside stays unnamed."""
    with pytest.raises(ValidationError) as exc:
        _run(["A", "B", "C"],
             [_pairs("structure", ("A", "B"), ("B", "A")),
              _pairs("anchor", ("C", "A"))],
             _groups({0: {"A", "B", "C"}}), _doc("A", "B", "C"))
    text = str(exc.value)
    assert "A -> B (structure)" in text
    assert "B -> A (structure)" in text
    # The innocent provider's edge is nowhere in the report — "anchor" appears
    # only as prose ("cycle through tree anchors"), never as a provider tag.
    assert "(anchor)" not in text
    # C is emitted (its edge into the cycle is fine); only A and B remain.
    assert "A, B" in text


def test_a_provider_that_breaks_nothing_leaves_the_order_intact():
    """A provider whose edges are all satisfied by the document order is a
    no-op: this is what makes the passage from the old planners safe — where
    the tree already ordered itself, the new edges change nothing."""
    with_edges = _run(["A", "B", "C"],
                      [_pairs("structure", ("A", "B"), ("B", "C"))],
                      _groups({0: {"A", "B", "C"}}), _doc("A", "B", "C"))
    without = _run(["A", "B", "C"], [], _groups({0: {"A", "B", "C"}}),
                   _doc("A", "B", "C"))
    assert with_edges == without == ["A", "B", "C"]
