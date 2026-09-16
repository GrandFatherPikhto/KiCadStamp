# kicadstamp/copper_order.py
"""copper_order.py — "inter-node copper goes AFTER the pads it connects".

plan_2026_09_17_order_pass_and_component_node, Э2.

A `net_traces:` record has always carried the identity of both of its ends —
`pads: ["AD_DAC.11", "C_OUT_BULK.1"]` — and the redraw order has never used
them. The planner only knew the coarse rule of 2026-09-16 ("copper last"), which
is right but cannot say WHICH components a given piece of copper waits for: it
defers every net_trace behind every record, including records that have nothing
to do with it.

This module turns the stored ends into dependency EDGES for the shared order
machine (kicadstamp/order_pass.py via tree_position._copper_provider):
for every end, find the tree node that places the cell carrying that Role and
yield "node -> copper".

WHOSE rule says "this node places this role" — the project's own, not a second
one (Т2.3): `trees.find_role_placement_matches` is THE single predicate behind
both the mount drift guard and the internal-mount resolver, and it is what
already answers "does this role live inside this tree, and where". Reusing it
means the order and the placement layer cannot disagree about where a role
lives.

WHY THE TREE SCOPES THE ANSWER (measured live, 2026-09-17, profile
3ch-awg-tia-v103): a Role alone is NOT a board-wide identity — the same Role
lives in several Clusters and on several sheets ("C_OUT_BULK" is on 33
components, "AD_DAC" on 3), so a role resolved against the whole board produced
"every copper waits for all 30 nodes", i.e. no information at all. Scoped to the
tree that hosts the copper node, the same role resolves to the one node that
actually places it (each instance tree carries its own sheet), and the anchor end
is nailed down further by the record's OWN anchor_cluster/anchor_sheet.

NOT GUESSING (Т2.1 and the no-silent-choice rule of Т3.6): an end that resolves
to no node of its tree contributes no edge (the component lives outside the
tree — legal, not an error), and an end that resolves to SEVERAL nodes of the
tree contributes no edge either: with a role ambiguous inside one tree the
format carries nothing that could tell the candidates apart, and picking one
would silently order the copper after the wrong component. The coarse "copper
last" slot stays underneath for every such case (Т2.2).

Pure by construction: no board, no adapter, nothing written — the whole answer
comes from the config and the trees.
"""
import logging

from .config import net_trace_effective_name
from .trees import _walk_nodes, find_role_placement_matches

logger = logging.getLogger(__name__)

__all__ = ["copper_node_dependencies"]


def _end_role_and_pad(label: str) -> tuple[str, str]:
    """("AD_DAC.11") -> ("AD_DAC", "11"); ("junk") -> ("", ""). The stored ends
    are "ROLE.pad" strings (internode_capture._pad_label), the pad being dropped
    here: the ORDER only cares about the component, never about the pin."""
    role, _dot, pad = label.rpartition(".")
    return role, pad


def _owners_of(cfg, tree, trace) -> set[str]:
    """The refs of the tree's nodes this copper must be applied AFTER.

    Every end is looked up by the project's own role predicate
    (find_role_placement_matches, module content included). The end that IS the
    record's anchor (same role AND same pad as anchor_role/anchor_pad) is
    narrowed by the record's own anchor_cluster/anchor_sheet — that is the same
    addressing the `NetTrace` anchor itself is resolved with, and it is what
    makes an ambiguous role (the anchor of a PI-filter bridge lives in a
    Cluster-wide role) name exactly one node."""
    owners: set[str] = set()
    for label in trace.pads or ():
        role, pad = _end_role_and_pad(label)
        if not role:
            continue
        is_anchor_end = (role == trace.anchor_role
                         and trace.anchor_pad is not None
                         and pad == trace.anchor_pad)
        matches = find_role_placement_matches(
            cfg, tree, role,
            sheet=trace.anchor_sheet if is_anchor_end else None,
            cluster=trace.anchor_cluster if is_anchor_end else None)
        nodes = {m.node.ref for m in matches}
        if len(nodes) == 1:
            owners |= nodes
        elif len(nodes) > 1:
            logger.debug("copper order: end %r of %s matches %d nodes of tree %r "
                         "(%s) — no edge is invented for an ambiguous role",
                         label, net_trace_effective_name(trace), len(nodes),
                         tree.name, ", ".join(sorted(nodes)))
        # 0 matches: the component of this end is not placed by this tree —
        # legal (Т2.1), and the safety net keeps the copper last anyway.
    return owners


def copper_node_dependencies(cfg) -> dict[str, set[str]]:
    """{copper record ref: {tree node refs that must be applied BEFORE it}}.

    Empty when no tree carries a `net_trace` node, or when no record stores a
    `pads:` list at all (a legacy record cannot say what it connects — see
    internode_capture's docstring). In every such case the caller's order is
    exactly the pre-Э2 one: the "copper last" slot alone.

    A copper NODE is found by its own tree walk (the plain `cfg.trees`), so a
    node's ends are resolved against the tree that hosts it — including a copper
    node that lives in the content of a tree embedded through a module."""
    plain_trees = {t.name: t for t in (getattr(cfg, "trees", None) or [])}
    if not plain_trees:
        return {}
    traces = {net_trace_effective_name(nt): nt
              for nt in (getattr(cfg, "net_traces", None) or [])}
    if not traces:
        return {}

    out: dict[str, set[str]] = {}
    for tree in plain_trees.values():
        for node in _walk_nodes(tree.nodes):
            if node.kind != "net_trace":
                continue
            trace = traces.get(node.ref)
            if trace is None or not trace.pads:
                continue
            owners = _owners_of(cfg, tree, trace)
            if owners:
                out.setdefault(node.ref, set()).update(owners)
                logger.debug("copper order: %s waits for %s", node.ref,
                             ", ".join(sorted(owners)))
    return out
