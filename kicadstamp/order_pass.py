# kicadstamp/order_pass.py
"""order_pass.py — the ONE order machine ("первопроход") of the project.

plan_2026_09_17_order_pass_and_component_node, Э1: before this module the
project had two planners, each with its OWN Kahn loop, its own queue sort key
and its own idea of what a vertex is —

  * tree_position._plan_forest_plain()  — the no-module forest order;
  * tree_position.curated_redraw_plan_forest() — the module-aware one;

— and the two had already drifted once (2026-09-16: the copper-last rule, and
the "module vertices must sit BEFORE copper" ordering that only the module
branch could express). A third dependency source (Э2's "copper after the pads
it connects") was about to be needed by BOTH, which is exactly how a third
sort key gets invented.

This module is that third thing done right: ONE Kahn implementation over an
arbitrary vertex set, where every source of "parent before child" is a NAMED
provider (EdgeProvider) plugged into a list, and the only remaining degree of
freedom is the tie-breaker — the order of vertices that are ALL ready at the
same moment (the document order of the tree, see tree_position's
_document_index).

Design rules this module exists to enforce (Т1.2/Т1.4 of the plan):

  * adding a new dependency source means writing a provider and appending it
    to the list — never touching the machine, never touching a sort key;
  * an edge knows WHICH provider gave it, so a cycle can name the culprit:
    "these nodes form a cycle ... cycle edges by provider: A -> B (structure)".

The machine is deliberately free of tree/config knowledge: it takes opaque
vertex objects (record refs `str`, module markers `id(node)` `int` — anything
hashable), named providers and two callables.
"""
import dataclasses
import logging
from typing import Callable, Iterable

from .exceptions import ValidationError, format_fatal_error
from .i18n import _

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class EdgeProvider:
    """One NAMED source of precedence edges for the order pass.

    `edges(vertices)` is a pure function of the run's vertex set: it yields
    `(parent, child)` pairs meaning "parent must be emitted strictly before
    child" (the direction every existing planner already uses). `name` is the
    short technical identifier a cycle report names — deliberately an
    identifier, not prose, so the provider names are not user-facing strings
    of their own.

    A provider MAY yield an edge whose endpoints are not in `vertices` (a
    natural consequence of writing "for each selected node, look up its
    parent": the parent can be outside the run). Such an edge is meaningless
    for this pass and is dropped by the machine with a debug line — dropping
    is deliberate, because "the parent is not applied this run" is EXACTLY the
    case the planners warn about instead of fataling.

    A provider MAY yield the same edge twice (two independent reasons for one
    precedence). The indegree/child lists stay consistent because every
    yielded pair is counted once and decremented once.
    """
    name: str
    edges: Callable[[frozenset], Iterable[tuple]]


def _cycle_report(remaining: set, children: dict, givers: dict) -> str:
    """The "cycle edges by provider" line: every edge BETWEEN the vertices
    still holding an indegree > 0, tagged with the provider(s) that gave it.

    This is Т1.4 of the plan: with new providers, a cycle can appear where the
    old planner silently produced a WRONG order instead — the error must say
    which dependency source to go and fix, or the reader cannot act on it.
    Sorted by (parent, child) so the same broken config reports the same text
    every run.
    """
    parts: list[str] = []
    for parent in sorted(remaining, key=str):
        for child in children.get(parent, []):
            if child not in remaining:
                continue
            names = ", ".join(sorted(givers.get((parent, child), ())))
            parts.append("{parent} -> {child} ({names})".format(
                parent=parent, child=child, names=names or "?"))
    return "; ".join(parts) or "?"


def run_order_pass(vertices, providers: list[EdgeProvider], *,
                   group_of: Callable[[object], int],
                   doc_index: dict,
                   cycle_title: str) -> list:
    """Kahn's algorithm over `vertices` with pluggable providers.

    `group_of(vertex)` -> int is the FIRST sort component: the queue is split
    into type-homogenous groups (0 = ordinary records, 1 = module pass-through
    vertices, 2 = copper — the order the 2026-09-16 work settled on, and the
    reason a bare `str` never gets compared with an `int`). Within a group the
    tie-breaker is `doc_index[vertex]` — the vertex's position in the top-down
    document walk — so two independent nodes are applied in the order they are
    written, which is the order the GUI shows (P.4/Т1.1).

    Vertices the caller did not index (a defensive case: the planner indexes
    everything it plans) sort after every indexed one, by `str(vertex)` — with
    `str()` as the LAST component of the key the whole tuple stays comparable
    whatever the vertex types are.

    Raises ValidationError (via format_fatal_error) naming the cycle's
    participants AND the provider of every edge among them.
    """
    vertex_set = set(vertices)
    children: dict[object, list[object]] = {}
    indeg: dict[object, int] = {v: 0 for v in vertex_set}
    givers: dict[tuple, set[str]] = {}

    for provider in providers:
        for parent, child in provider.edges(frozenset(vertex_set)):
            if parent not in vertex_set or child not in vertex_set:
                logger.debug("order pass: provider %r gave an edge outside the "
                             "vertex set (%r -> %r) - ignored",
                             provider.name, parent, child)
                continue
            children.setdefault(parent, []).append(child)
            indeg[child] += 1
            givers.setdefault((parent, child), set()).add(provider.name)

    fallback = len(doc_index) + 1

    def sort_key(vertex):
        return (group_of(vertex), doc_index.get(vertex, fallback), str(vertex))

    queue = sorted((v for v in vertex_set if indeg[v] == 0), key=sort_key)
    order: list = []
    while queue:
        vertex = queue.pop(0)
        order.append(vertex)
        for child in children.get(vertex, []):
            indeg[child] -= 1
            if indeg[child] == 0:
                queue.append(child)
        # Re-sorted at every step (same discipline as both pre-2026-09-17
        # planners): a child that just became ready competes with the vertices
        # already waiting, and the tie-breaker decides — not the insertion
        # order of some internal list.
        queue.sort(key=sort_key)

    if len(order) != len(vertex_set):
        remaining = vertex_set - set(order)
        raise ValidationError(format_fatal_error(
            cycle_title,
            [_("these nodes form a cycle through tree anchors: {items}")
             .format(items=", ".join(sorted(str(v) for v in remaining))),
             _("cycle edges by provider: {edges}")
             .format(edges=_cycle_report(remaining, children, givers))]))
    return order
