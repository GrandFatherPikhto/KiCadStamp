# kicadstamp/net_matching.py
"""Pure Role<->Net bipartite-graph matching for cross-cluster net correspondence.

Port of the validated sandbox `diagnostics/net_matching_sandbox.py` (sessions
2026-08-27/08-28) into production. It answers ONE question: given a template
cluster's role->{pin->net} map and a target cluster's role->{pin->net} map
under a FIXED role bijection (roles are matched by name — the project's
established Role convention), which template net corresponds to which target
net? This is the NET part of trace transfer between symmetric clusters
(techdocs/me/resume.md step 8); the geometric transfer itself is out of scope.

Algorithm (global, never local greedy):
  1. Weisfeiler-Leman color refinement to a genuine fixed point narrows each
     template net's candidates to same-colored target nets (colors only ever
     split, never merge, so a stabilized unique color is a safe filter).
  2. Kuhn's algorithm (augmenting paths) finds ONE perfect matching over the
     WHOLE compatibility graph at once — no per-color-class islands.
  3. Tarjan SCC over the "swap" digraph (contract matched pairs, add every
     non-matched compatible edge) proves uniqueness: an SCC of size > 1 is an
     alternating cycle = a second perfect matching = resume.md's
     "group of indistinguishable nets", found FORMALLY.
  4. Symmetric roles (electrically symmetric 2-pin R/C where pin 1 vs pin 2 is
     arbitrary KiCad marking) contribute their BARE NAME to the signature, so
     they produce exactly the genuine ambiguity class the project already
     models as net_template_same_as_role vs net_template_pad.

SAFE DEFAULT (proven by test in tests/test_net_matching.py, session 08-28):
ambiguity arises exactly where a role is physically symmetric, and EVERY member
of an ambiguous SCC is a formally correct answer — validate_mapping is True for
each, not just for the one perfect matching Kuhn happened to build. So the
matching find_perfect_matching built IS a valid automatic answer; the SCC
report is a diagnostic layer, not a hard human stop.

Boundaries: PURE module — no adapter, no YAML, no printing, no live board. All
input/output is plain data (Graph dataclass / dicts). Errors are ValidationError
via format_fatal_error (project rule: what breaks must say exactly what to fix);
internal graph logic carries no i18n (only the two boundary messages do).
"""

from __future__ import annotations

import hashlib
import itertools
from collections import defaultdict
from dataclasses import dataclass, field

from .exceptions import ValidationError, format_fatal_error
from .i18n import _


# ── Graph model ──────────────────────────────────────────────────────────

@dataclass
class Graph:
    """Bipartite Role<->Net graph. `roles`: role name -> {pin: net name}.
    `global_nets`: nets pre-matched by name (GND/VCC/...) — anchors, never
    matching candidates (they are excluded from the compatibility graph, but
    still refined as neighbors). `symmetric_roles`: roles whose pin labels are
    NOT physically meaningful (symmetric 2-pin R/C) — pins are treated as an
    unordered set; this is the production split of net_template_pad vs
    net_template_same_as_role, transplanted into the net-matching model."""
    roles: dict[str, dict[int, str]]
    global_nets: set[str] = field(default_factory=set)
    symmetric_roles: set[str] = field(default_factory=set)

    def nets(self) -> set[str]:
        return {net for pins in self.roles.values() for net in pins.values()}

    def net_role_pins(self) -> dict[str, set[tuple[str, int]]]:
        out: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for role, pins in self.roles.items():
            for pin, net in pins.items():
                out[net].add((role, pin))
        return out

    def net_neighbors_via_role(self) -> dict[str, set[str]]:
        """net -> other nets reachable through a shared role — WL adjacency."""
        by_net = self.net_role_pins()
        role_to_nets: dict[str, set[str]] = defaultdict(set)
        for net, rp in by_net.items():
            for role, _pin in rp:
                role_to_nets[role].add(net)
        out: dict[str, set[str]] = defaultdict(set)
        for _role, nets in role_to_nets.items():
            for n in nets:
                out[n] |= (nets - {n})
        return out

    def edges(self) -> set[tuple[str, int, str]]:
        return {(role, pin, net) for role, pins in self.roles.items()
                for pin, net in pins.items()}


# ── Weisfeiler-Leman color refinement (candidate narrowing, not decision) ──

def _hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _initial_signature(net: str, rp: dict[str, set[tuple[str, int]]],
                       symmetric_roles: set[str]) -> str:
    """0-hop signature: sorted (role.pin) items, EXCEPT symmetric roles which
    contribute their bare name (pin labels are arbitrary for those — this is
    what makes a symmetric 2-pin R/C a genuine ambiguity rather than a
    silently-picked pin 1/pin 2 assignment)."""
    parts = [role if role in symmetric_roles else f"{role}.{pin}"
             for role, pin in rp[net]]
    return "0:" + "|".join(sorted(parts))


def _refine_to_fixed_point(colors: dict[str, str], neighbors: dict[str, set[str]],
                           max_iterations: int = 20) -> dict[str, str]:
    """Runs to a genuine fixed point (colors stop changing) — only full
    stabilization makes "same color" a safe candidate filter (WL colors only
    ever split, never merge back, so a class that's unique stays unique —
    but only once stabilized; an early-stopped pass isn't yet a true
    invariant). Capped at max_iterations as a sanity bound, not a tuning
    knob."""
    cur = dict(colors)
    for _iter in range(max_iterations):
        nxt = {n: _hash(cur[n] + "|" + ",".join(sorted(cur[m] for m in neighbors.get(n, ()))))
              for n in cur}
        if nxt == cur:
            return cur
        cur = nxt
    return cur  # extremely deep graphs only; not expected in practice


def refined_colors(g: Graph) -> dict[str, str]:
    """Global nets get a fixed synthetic color (they act as anchors other
    nets' signatures can reference) but are refined and returned like any
    other net — the caller filters them out of the MATCHING candidate pool,
    not out of the neighbor computation, or refinement crashes on a dangling
    reference the moment a non-global net touches a role that also touches
    a global net (e.g. OPAMP: {1: N2, 2: GND})."""
    rp = g.net_role_pins()
    colors = {n: (f"GLOBAL:{n}" if n in g.global_nets
                  else _initial_signature(n, rp, g.symmetric_roles))
              for n in g.nets()}
    neigh = g.net_neighbors_via_role()
    return _refine_to_fixed_point(colors, neigh)


# ── Global perfect matching (Kuhn's algorithm) ──────────────────────────

def find_perfect_matching(left_nodes: list[str], compat: dict[str, set[str]]) -> dict[str, str] | None:
    """Kuhn's algorithm — augmenting paths over the WHOLE compatibility graph
    at once (no per-class islands). Returns left->right, or None if no
    perfect matching covering every left node exists."""
    match_right: dict[str, str] = {}  # right -> left

    def try_augment(u: str, visited: set[str]) -> bool:
        for v in compat.get(u, ()):
            if v in visited:
                continue
            visited.add(v)
            if v not in match_right or try_augment(match_right[v], visited):
                match_right[v] = u
                return True
        return False

    for u in left_nodes:
        if not try_augment(u, set()):
            return None
    return {u: v for v, u in match_right.items()}


# ── Uniqueness proof via alternating-cycle detection (Tarjan SCC) ──────

def _tarjan_scc(nodes: list[int], adj: dict[int, list[int]]) -> list[list[int]]:
    index_counter = [0]
    stack: list[int] = []
    on_stack: set[int] = set()
    indices: dict[int, int] = {}
    low: dict[int, int] = {}
    sccs: list[list[int]] = []

    import sys
    sys.setrecursionlimit(max(10000, len(nodes) * 4 + 100))

    def strongconnect(v: int) -> None:
        indices[v] = low[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj.get(v, ()):
            if w not in indices:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], indices[w])
        if low[v] == indices[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(comp)

    for v in nodes:
        if v not in indices:
            strongconnect(v)
    return sccs


def _owner(right_node: str, matching: dict[str, str]) -> str:
    for u, v in matching.items():
        if v == right_node:
            return u
    raise KeyError(right_node)  # sandbox-only lookup; index this if perf matters


def prove_unique_or_report_ambiguity(
        left_nodes: list[str], compat: dict[str, set[str]], matching: dict[str, str],
) -> list[list[str]]:
    """Returns [] if `matching` is the ONLY perfect matching of the whole
    compatibility graph (formally proven, not sampled). Otherwise returns
    one list of left-node names per ambiguous group (an SCC of size > 1 in
    the "swap" digraph — resume.md's Step 5 payload)."""
    idx = {u: i for i, u in enumerate(left_nodes)}
    adj: dict[int, list[int]] = defaultdict(list)
    for u in left_nodes:
        mu = matching[u]
        for v in compat.get(u, ()):
            if v == mu:
                continue  # the matched edge itself, not an alternative
            j = idx[_owner(v, matching)]
            adj[idx[u]].append(j)

    sccs = _tarjan_scc(list(range(len(left_nodes))), adj)
    ambiguous = [[left_nodes[i] for i in comp] for comp in sccs if len(comp) > 1]
    return ambiguous


# ── Enumerate every alternative matching of an ambiguous SCC group ────────

def enumerate_scc_alternative_mappings(
        scc_group: list[str], compat: dict[str, set[str]], mapping: dict[str, str],
) -> list[dict[str, str]]:
    """For one ambiguous group (one SCC from prove_unique_or_report_ambiguity),
    brute-force every compat-respecting bijection between the group's left
    nodes and the union of their compatible right nodes, then splice each
    onto a copy of the FULL `mapping` (replacing only this group's entries).
    Groups here are small (a handful of symmetric-role nets) — brute force
    over itertools.permutations is fine, no need for cycle-rotation math."""
    group = sorted(scc_group)
    right_candidates = sorted({r for u in group for r in compat.get(u, ())})
    if len(right_candidates) != len(group):
        # A perfect-matching SCC must have exactly one candidate per left
        # node — a mismatch is a caller bug, not a quiet skip.
        raise ValidationError(format_fatal_error(
            _("ambiguous SCC group has mismatched candidate count"),
            [_("group {group} has {n} left nodes but {m} candidate right nodes "
               "({cands}) — a perfect-matching SCC must have exactly one "
               "candidate per left node; this is a caller bug")
             .format(group=group, n=len(group), m=len(right_candidates),
                     cands=right_candidates)]))

    alternatives: list[dict[str, str]] = []
    for perm in itertools.permutations(right_candidates):
        if all(perm[i] in compat[u] for i, u in enumerate(group)):
            alt = dict(mapping)
            for u, v in zip(group, perm):
                alt[u] = v
            alternatives.append(alt)
    return alternatives


# ── End-to-end: narrow, match, prove ────────────────────────────────────

def _role_nets(g: Graph, role: str) -> set[str]:
    return set(g.roles[role].values())


def validate_mapping(template: Graph, target: Graph, mapping: dict[str, str]) -> bool:
    """Relaxed edge-preservation: asymmetric roles must map (role, pin, net)
    exactly onto the target's (role, pin, net). Symmetric roles only need
    their incident net SET to map onto the target role's incident net set —
    pin labels are not physically meaningful there."""
    target_edges = target.edges()
    full = {**mapping, **{g: g for g in template.global_nets}}
    for role, pins in template.roles.items():
        if role in template.symmetric_roles:
            mapped = {full[n] for n in pins.values() if n in full}
            if mapped != _role_nets(target, role):
                return False
        else:
            for pin, net in pins.items():
                if net not in full or (role, pin, full[net]) not in target_edges:
                    return False
    return True


def match_template_to_target(template: Graph, target: Graph) -> tuple[dict[str, str], list[list[str]]]:
    """Returns (mapping, ambiguous_groups). mapping covers every non-global
    net iff ambiguous_groups is empty. For ambiguous groups the reported
    mapping only includes the nets NOT in any ambiguous group (the claimable
    part); the ambiguous nets are left as a diagnostic (resume.md Step 5).

    Raises ValidationError (never RuntimeError — production error convention)
    when the graphs are not isomorphic under the fixed role bijection (no
    perfect matching), or when a provably-unique matching fails full edge
    validation (a narrowing-rule bug, not a real symmetry)."""
    if template.global_nets != target.global_nets:
        raise ValidationError(format_fatal_error(
            _("global nets must be pre-matched by name before net matching"),
            [_("template global nets {t} and target global nets {g} differ — "
               "match anchors by name first")
             .format(t=sorted(template.global_nets), g=sorted(target.global_nets))]))
    if template.symmetric_roles != target.symmetric_roles:
        raise ValidationError(format_fatal_error(
            _("symmetric-role marking must match across template and target"),
            [_("template {t} vs target {g} — the same role must be marked "
               "symmetric (or not) on both sides")
             .format(t=sorted(template.symmetric_roles),
                     g=sorted(target.symmetric_roles))]))

    t_colors = refined_colors(template)
    g_colors = refined_colors(target)
    g_by_color: dict[str, list[str]] = defaultdict(list)
    for n, c in g_colors.items():
        if n in target.global_nets:
            continue  # anchors, not matching candidates
        g_by_color[c].append(n)

    left_nodes = sorted(n for n in t_colors if n not in template.global_nets)
    compat = {u: set(g_by_color.get(t_colors[u], ())) for u in left_nodes}

    matching = find_perfect_matching(left_nodes, compat)
    if matching is None:
        raise ValidationError(format_fatal_error(
            _("template and target are not isomorphic under the given role bijection"),
            [_("no perfect matching exists — some template net has no "
               "compatible candidate in the target cluster; check the "
               "role/pin topology")]))

    ambiguous = prove_unique_or_report_ambiguity(left_nodes, compat, matching)
    if ambiguous:
        # matching is A solution but not THE solution — don't report it as fact.
        claimed = {u: v for u, v in matching.items()
                  if u not in {n for g in ambiguous for n in g}}
        return claimed, ambiguous

    if not validate_mapping(template, target, matching):
        raise ValidationError(format_fatal_error(
            _("unique matching fails full edge validation"),
            [_("the matching is unique by the color/structure proof but does "
               "not preserve edges — the narrowing rule is too coarse, not a "
               "real symmetry (bug in the signature, not the graph)")]))
    return matching, []


__all__ = [
    "Graph",
    "find_perfect_matching",
    "prove_unique_or_report_ambiguity",
    "enumerate_scc_alternative_mappings",
    "validate_mapping",
    "match_template_to_target",
    "refined_colors",
]
