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
      - anchor_point set        -> the corresponding points: entry (a leaf: the
                                   point is NOT expanded further, see below)
      - anchor_role set         -> list of parent records (1 = single parent,
                                   2+ = multiple parents), or fatal if zero
      - none of the above       -> None (no anchor at all: absolute placement,
                                   a root by construction)

    `points` maps a point name to its Record (built once by the caller). A
    point referenced by anchor_point is returned as a parent but its OWN anchor
    (points CAN chain via anchor_point/anchor_ref/anchor_role) is deliberately
    NOT resolved here — per the plan decision, points are leaves in the tree.
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
    placements, external targets, and points — which are leaves by decision).
    """
    records = build_records(cfg)
    by_key = {record_key(r): r for r in records}
    index = build_producer_index(cfg, records)
    points = {r.name: r for r in records if r.kind == "point"}

    parents: dict[str, list[str]] = {record_key(r): [] for r in records}
    external: dict[str, ExternalLeaf] = {}
    external_order: list[str] = []

    for rec in records:
        if rec.kind == "point":
            # Points are leaves by construction (plan decision): their own
            # anchor is never expanded, so they never gain a parent here.
            continue
        edge = resolve_anchor_edge(rec, cfg, index, points)
        key = record_key(rec)
        if edge is None:
            continue  # no anchor -> root
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

    return AnchorGraph(
        records=records,
        by_key=by_key,
        external=external,
        parents=parents,
        children=children,
        roots=roots,
    )
