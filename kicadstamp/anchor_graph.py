# kicadstamp/anchor_graph.py
"""
anchor_graph.py — the STATIC anchor-dependency graph over a loaded Config.

This is §0 of plan_2026_08_21_anchor_dependency_tree_cascade_redraw.md: the
tree of "which config record anchors on which other config record" is built
PURELY from the YAML-derived Config, with NO live board access. A graph node is
ONE CONFIG RECORD (not a live resolved component) — the principle confirmed
with Denis on 2026-08-21: `Channel_0_DAC_BUF` / `Channel_1_DAC_BUF` /
`Channel_2_DAC_BUF` are already three separate records (materialised by
`sheet_templates:` expansion before load), so the "node = record" principle
needs no per-instance live resolution.

Because there is no live board, the graph edges ("record Y anchors on record X")
must be derived statically: the anchor fields (anchor_ref / anchor_role
(+anchor_sheet+anchor_cluster) / anchor_point) name a ROLE/REFDES/POINT, not
another record directly. resolve_anchor_edge() therefore re-does, offline, the
same narrowing work the runtime does live through role_narrowing.py /
resolve_footprint_by_role — but against the producer index built from the
Config itself (build_producer_index), not against footprints on a board.

This module is pure: it imports config models, cluster_matching, exceptions,
i18n and net_resolution.resolve_placeholder — no kicad adapter, no geometry,
no placement. It is the foundation for the GUI dependency tree (§1 of the
plan) and for cascade "Redraw dependents" ordering (§2), which both consume
AnchorGraph. Those consumers live elsewhere; this module only builds the graph.

Node identity: every node has a namespaced string key — record_key(rec) for
config records ("clone:<name>", "rule:<name>", ...), external_key(ref) for
anchor_ref targets that are NOT produced by any config record (the FPGA-like
external case), so a record name can never collide with an external ref or
with a record of another kind. The human-readable name is Record.name (the
same --only identity apply_only_filter uses).
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Union

from .cluster_matching import cluster_prefix_match
from .config import (
    Config,
    clone_placement_effective_name,
    coordinate_placement_effective_name,
    net_trace_effective_name,
    rule_effective_name,
    thermal_via_array_effective_name,
)
from .exceptions import ValidationError, format_fatal_error
from .i18n import _
from .net_resolution import resolve_placeholder

logger = logging.getLogger(__name__)


@dataclass
class Record:
    """One config record, normalized for graph building — the same "one node =
    one config record" shape for every concrete type. `obj` is the original
    dataclass; the anchor fields are flattened onto the Record so the graph
    builder does not branch on type except where a type genuinely differs."""
    kind: str                     # 'clone' | 'rule' | 'coordinate' | 'net_trace' | 'thermal_via' | 'point'
    obj: Any
    name: str                     # human-readable --only identity (see below)
    sheet: str | None             # own-identity sheet (None where absent)
    anchor_ref: str | None
    anchor_role: str | None
    anchor_sheet: str | None
    anchor_cluster: str | None
    anchor_point: str | None
    params: dict[str, Any]        # for {placeholder} in anchor_sheet (ClonePlacement only)


@dataclass
class ExternalLeaf:
    """An anchor_ref target that is NOT produced by any config record — a
    legal, non-fatal case (the FPGA-like external component): the dependent
    record is anchored on a component this tool does not manage, so the edge
    leads OUT of the config graph to a synthetic leaf node."""
    ref: str


@dataclass
class AnchorGraph:
    """The resolved static dependency graph. `parents`/`children`/`roots` use
    the namespaced node keys from record_key()/external_key(); `by_key` maps a
    record key back to its Record."""
    records: list[Record]
    by_key: dict[str, Record]
    external: dict[str, ExternalLeaf]
    parents: dict[str, list[str]]
    children: dict[str, list[str]]
    roots: list[str]


def record_key(rec: Record) -> str:
    """Namespaced node key for a config record — kind-prefixed so a clone and
    a rule can never collide on the same --only identity."""
    return f"{rec.kind}:{rec.name}"


def external_key(ref: str) -> str:
    """Namespaced node key for an external anchor_ref target."""
    return f"ref:{ref}"


def _record(kind: str, obj: Any, name: str, sheet: str | None,
            params: dict[str, Any] | None = None) -> Record:
    """Build a Record from a concrete config dataclass, reading the anchor
    fields generically (all anchor-bearing types carry this same field set,
    except NetTrace which has no anchor_ref/anchor_point — getattr defaults
    keep that harmless)."""
    return Record(
        kind=kind,
        obj=obj,
        name=name,
        sheet=sheet,
        anchor_ref=getattr(obj, "anchor_ref", None),
        anchor_role=getattr(obj, "anchor_role", None),
        anchor_sheet=getattr(obj, "anchor_sheet", None),
        anchor_cluster=getattr(obj, "anchor_cluster", None),
        anchor_point=getattr(obj, "anchor_point", None),
        params=params or {},
    )


def build_records(cfg: Config) -> list[Record]:
    """Normalize every config record into a list of Records, in a stable order
    (clone_placements, rules, coordinate_placements, net_traces,
    thermal_via_arrays, points — mirroring the Config's own section order).

    retired: true records are dropped entirely — they "do not exist on the
    board right now" (same convention as apply_pipeline.drop_disabled_rules),
    so they neither produce nor anchor nor appear. skip: true records are kept:
    skip only excludes them from THIS run, the board entity still exists.
    """
    records: list[Record] = []
    for c in cfg.clone_placements:
        if c.retired:
            continue
        records.append(_record(
            "clone", c, clone_placement_effective_name(c), c.sheet, c.params))
    for r in cfg.rules:
        if r.retired:
            continue
        # Rule has no own-identity sheet field yet (plan §1.0 adds it) —
        # getattr keeps this forward-compatible; None until then.
        records.append(_record(
            "rule", r, rule_effective_name(r), getattr(r, "sheet", None)))
    for cp in cfg.coordinate_placements:
        if cp.retired:
            continue
        records.append(_record(
            "coordinate", cp, coordinate_placement_effective_name(cp), cp.sheet))
    for nt in cfg.net_traces:
        if nt.retired:
            continue
        records.append(_record(
            "net_trace", nt, net_trace_effective_name(nt),
            getattr(nt, "sheet", None)))
    for tva in cfg.thermal_via_arrays:
        if tva.retired:
            continue
        records.append(_record(
            "thermal_via", tva, thermal_via_array_effective_name(tva),
            getattr(tva, "sheet", None)))
    for p in cfg.points.values():
        records.append(_record("point", p, p.name, None))
    return records


def _record_produces(cfg: Config, rec: Record) -> list[tuple[str | None, str, str | None]]:
    """The (cluster, role, sheet) identities this record PRODUCES on the board
    (the inverse of anchoring). Only clone_placements, rules and
    coordinate_placements produce components; net_traces/thermal_via_arrays/
    points produce no component (copper/vias only, or a bare coordinate), so
    they can never be a parent in the graph.

    Cluster tag conventions (mirroring role_narrowing.py's live cascade):
      - clone_placement -> its own `name` (the Cluster TAG, not placer_name)
      - rule -> each spoke's own `cluster` (a rule can produce the same role
        under several clusters, one entry per spoke)
      - coordinate_placement -> its own cluster/role/sheet fields
    """
    if rec.kind == "clone":
        cell = cfg.cells.get(rec.obj.cell)
        if cell is None:
            logger.debug(_("clone_placement {name!r}: cell {cell!r} not found, produces nothing")
                         .format(name=rec.name, cell=rec.obj.cell))
            return []
        return [(rec.obj.name, slot.role, rec.sheet) for slot in cell.components]

    if rec.kind == "rule":
        out: list[tuple[str | None, str, str | None]] = []
        for spoke in rec.obj.spokes:
            if spoke.retired:
                continue
            cell = cfg.cells.get(spoke.cell)
            if cell is None:
                logger.debug(_("rule {name!r}: spoke cell {cell!r} not found, produces nothing")
                             .format(name=rec.name, cell=spoke.cell))
                continue
            out.extend((spoke.cluster, slot.role, rec.sheet) for slot in cell.components)
        return out

    if rec.kind == "coordinate":
        return [(rec.obj.cluster, rec.obj.role, rec.sheet)]

    return []


def build_producer_index(cfg: Config,
                         records: list[Record]) -> dict[tuple[str | None, str, str | None], list[Record]]:
    """§0.2: {(cluster, role, sheet_or_None): [producer records]} over the whole
    loaded Config (already resolve_includes()/expand_sheet_templates()-ed by
    load_config before it reaches us). A record appears under one key per
    (cluster, role, sheet) it produces; a rule producing the same role under
    two clusters appears under two keys. Values preserve config order."""
    index: dict[tuple[str | None, str, str | None], list[Record]] = {}
    for rec in records:
        for key in _record_produces(cfg, rec):
            index.setdefault(key, []).append(rec)
    return index


def _resolve_role_edge(rec: Record, cfg: Config,
                       index: dict[tuple[str | None, str, str | None], list[Record]]
                       ) -> list[Record]:
    """Resolve an anchor_role edge statically: search the producer index by
    (Role, Sheet-prefix, Cluster-prefix), reusing the SAME narrowing cascade
    as role_narrowing._narrow_by_sheet_cluster_selection (minus the live-only
    current-selection step): sheet first, then cluster, each step applied only
    if it reduces the set to a non-empty subset.

    Returns the list of parent records — one (single parent), or several
    (genuine static ambiguity -> the node has multiple parents; the tree at
    that point is technically a DAG, which is expected and fine). Raises
    ValidationError when NO config record produces the role at all — the
    anchor points into nowhere (a real config error, not silently ignored).
    """
    role = rec.anchor_role
    anchor_sheet = rec.anchor_sheet
    if anchor_sheet is not None:
        # Same {placeholder} substitution as the live resolve_anchor_by_role
        # (clone_role_resolver.py) — only ClonePlacement carries params, so the
        # substitution is a no-op for every other kind.
        anchor_sheet = resolve_placeholder(anchor_sheet, rec.params, what="anchor_sheet")

    entries: list[tuple[str | None, str | None, Record]] = [
        (cluster, sheet, producer)
        for (cluster, r, sheet), producers in index.items()
        if r == role
        for producer in producers
    ]

    if not entries:
        raise ValidationError(format_fatal_error(
            _("anchor_role {role!r} of {name!r} is not produced by any config record")
            .format(role=role, name=rec.name),
            [_("no clone_placements:/rules:/coordinate_placements: entry produces a "
               "component with role {role!r} — this anchor points outside the config. "
               "For an external (non-kicadstamp-managed) component use anchor_ref: "
               "instead of anchor_role:").format(role=role)]
        ))

    # Sheet narrowing — prefix-segment match (same semantics as
    # cluster_prefix_match, see its docstring): a producer deeper in the
    # hierarchy ("Channel_0/SubA") matches a less-specific anchor_sheet
    # ("Channel_0"), mirroring _fp_on_sheet's "anchor_sheet is one of the path
    # segments". A producer with sheet=None is unknown and therefore excluded
    # only if SOME candidate matches; if the sheet filter yields empty we keep
    # the full set (never narrow to empty — same "never choose for the user"
    # convention as the live cascade).
    if anchor_sheet:
        by_sheet = [(c, s, p) for (c, s, p) in entries
                    if s is not None and cluster_prefix_match(s, anchor_sheet)]
        if by_sheet and len(by_sheet) < len(entries):
            entries = by_sheet

    # Cluster narrowing — exact reuse of cluster_prefix_match on the produced
    # cluster (clone.name / spoke.cluster / coordinate.cluster).
    if rec.anchor_cluster:
        by_cluster = [(c, s, p) for (c, s, p) in entries
                      if c is not None and cluster_prefix_match(c, rec.anchor_cluster)]
        if by_cluster and len(by_cluster) < len(entries):
            entries = by_cluster

    # Deduplicate by record identity (a rule producing the same role under two
    # clusters, both surviving narrowing, must yield ONE parent, not two).
    seen: set[str] = set()
    parents: list[Record] = []
    for _cluster, _sheet, producer in entries:
        key = record_key(producer)
        if key not in seen:
            seen.add(key)
            parents.append(producer)
    return parents


def resolve_anchor_edge(rec: Record, cfg: Config,
                        index: dict[tuple[str | None, str, str | None], list[Record]],
                        points: dict[str, Record]
                        ) -> Union[list[Record], ExternalLeaf, None]:
    """§0.3: resolve ONE record's anchor into its parent(s):

      - anchor_ref set          -> ExternalLeaf (edge leads OUT of the graph —
                                   a legal non-fatal case, no parent RECORD)
      - anchor_point set        -> the corresponding points: entry (which may
                                   itself chain further — points are normal
                                   records here, NOT leaves: a Point really
                                   does carry its own anchor fields, see
                                   config/points.py)
      - anchor_role set         -> list of parent records (1 = single parent,
                                   2+ = multiple parents), or fatal if zero
      - none of the above       -> None (no anchor at all: absolute placement,
                                   xy/anchor_origin, a root by construction)

    `points` maps a point name to its Record (built once by the caller). A
    point referenced by anchor_point is returned as a parent; the point's OWN
    anchor is resolved by the SAME function when the graph walks it, so
    point->point chains and point->role edges are part of the graph.
    """
    if rec.anchor_ref:
        return ExternalLeaf(rec.anchor_ref)

    if rec.anchor_point:
        point_rec = points.get(rec.anchor_point)
        if point_rec is None:
            raise ValidationError(format_fatal_error(
                _("anchor_point {point!r} of {name!r} not found in points:")
                .format(point=rec.anchor_point, name=rec.name),
                [_("known points: {names}").format(names=sorted(points.keys()))]
            ))
        return [point_rec]

    if rec.anchor_role:
        return _resolve_role_edge(rec, cfg, index)

    return None


def build_anchor_graph(cfg: Config) -> AnchorGraph:
    """Build the full static anchor-dependency graph for a loaded Config.

    The graph has one node per config record (retired records dropped, skip
    kept) plus one synthetic node per external anchor_ref target. Edges run
    child -> parent(s); roots are nodes with no parent at all (absolute
    placements, xy/anchor_origin points, and external targets). Points are
    normal records: a point can chain to another point (anchor_point), anchor
    on a produced role (anchor_role), or an external ref (anchor_ref).
    """
    records = build_records(cfg)
    by_key = {record_key(r): r for r in records}
    index = build_producer_index(cfg, records)
    points = {r.name: r for r in records if r.kind == "point"}

    parents: dict[str, list[str]] = {record_key(r): [] for r in records}
    external: dict[str, ExternalLeaf] = {}
    external_order: list[str] = []

    for rec in records:
        edge = resolve_anchor_edge(rec, cfg, index, points)
        key = record_key(rec)
        if edge is None:
            continue  # no anchor -> root (absolute placement / xy / anchor_origin)
        if isinstance(edge, ExternalLeaf):
            ekey = external_key(edge.ref)
            if ekey not in external:
                external[ekey] = edge
                external_order.append(ekey)
            parents[key] = [ekey]
        else:
            parents[key] = [record_key(p) for p in edge]

    children: dict[str, list[str]] = {}
    for child_key, parent_keys in parents.items():
        for pkey in parent_keys:
            children.setdefault(pkey, []).append(child_key)

    roots = [record_key(r) for r in records if not parents[record_key(r)]]
    roots += external_order

    # Cycle guard: every record must be reachable from some root. A set of
    # records whose anchors loop back onto each other, with no root reaching
    # them from outside, would otherwise silently disappear from the tree (the
    # UI renders only graph.roots) — detect it here and fail loudly instead.
    reachable: set[str] = set()
    stack = list(roots)
    while stack:
        key = stack.pop()
        if key in reachable:
            continue
        reachable.add(key)
        stack.extend(children.get(key, []))

    unreachable = [key for key in parents if key not in reachable]
    if unreachable:
        raise ValidationError(format_fatal_error(
            _("dependency cycle in anchor graph"),
            [_("these records form a cycle through their anchors (no root reaches them): {items}")
             .format(items=", ".join(sorted(unreachable)))]
        ))

    return AnchorGraph(
        records=records,
        by_key=by_key,
        external=external,
        parents=parents,
        children=children,
        roots=roots,
    )


# ── §2: cascade traversal and topological order ───────────────────────────────


def collect_dependents(graph: AnchorGraph, start_key: str) -> list[str]:
    """§2.1: all node keys TRANSITIVELY anchored on `start_key` — DFS over the
    children map, excluding start_key itself. Deterministic order: children
    lists are built in config order and the stack is processed LIFO, so the
    result is a stable depth-first discovery order. External leaves and
    points are returned too when they are descendants (a record anchored on
    a point that is itself anchored further up is reached transitively)."""
    out: list[str] = []
    seen: set[str] = {start_key}
    stack: list[str] = list(graph.children.get(start_key, []))
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
        stack.extend(graph.children.get(key, []))
    return out


def topological_order(graph: AnchorGraph, start_key: str) -> list[str]:
    """§2.2: topological order of {start_key} ∪ its transitive dependents —
    a parent ALWAYS appears before any node anchored on it, respecting ALL
    incoming edges (a DAG point with several parents is ordered after every
    one of them). Kahn's algorithm over the induced subgraph; the start node
    is guaranteed first (its parents lie outside the subgraph). Raises
    ValidationError on a cycle (anchors looping back onto each other)."""
    dependents = collect_dependents(graph, start_key)
    nodes = [start_key] + dependents
    node_set = set(nodes)

    in_degree: dict[str, int] = {k: 0 for k in nodes}
    for key in nodes:
        for parent in graph.parents.get(key, []):
            if parent in node_set:
                in_degree[key] += 1

    queue = deque(k for k in nodes if in_degree[k] == 0)
    order: list[str] = []
    while queue:
        key = queue.popleft()
        order.append(key)
        for child in graph.children.get(key, []):
            if child in in_degree and in_degree[child] > 0:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

    if len(order) != len(nodes):
        remaining = sorted(k for k in nodes if k not in order)
        raise ValidationError(format_fatal_error(
            _("dependency cycle in anchor graph"),
            [_("these records form a cycle through their anchors: {items}")
             .format(items=", ".join(remaining))]
        ))
    return order


def redraw_records_in_order(graph: AnchorGraph, start_key: str) -> list[Record]:
    """§2.3 input: the records to redraw for a "Redraw dependents" cascade —
    the start node and all transitive dependents, in topological order,
    FILTERED to records actually appliable via --only. Points (anchor-only
    bookkeeping) and external leaves (not config records) are skipped, but
    the traversal still passes through them so their record descendants are
    included."""
    order = topological_order(graph, start_key)
    result: list[Record] = []
    for key in order:
        rec = graph.by_key.get(key)
        if rec is None:
            continue  # external leaf — not a --only-appliable record
        if rec.kind == "point":
            continue  # anchor-only bookkeeping — not appliable via --only
        result.append(rec)
    return result
