# gui/docks/tree_from_selection.py
"""
Pure (Qt-free) logic for "Tools -> Extract tree..." (2026-09-01, plan
extract_selection_as_tree.md).

The plan's confirmed starting point: there is NO "extract into tree" — the
extract never writes to `trees:` (extract_writer.py: "NO position and NO auto
tree"), so there is nothing to strip from the flat config list. This module
BUILDS a NEW tree from the CURRENT board SELECTION instead: the fully-selected
Clusters (reusing reead.py's fully_selected_clusters / group_selected /
_sheet_chain detection) become top-level kind="placement" nodes on a Tree with
an explicit ROLE anchor.

Autopositioning (Denis's 09-01 decision): every placement node gets
xy = the Entity's live position MINUS the anchor base — offsets relative to the
chosen anchor point, captured at build time like "Reread current position" (the
tree freezes the current geometry of the selection relative to the anchor). At
apply the clusters stand relative to the anchor by the captured offsets. An
explicit role anchor permits N top-level placement nodes (the "exactly one
top-level placement" rule is only for is_auto anchors) — so N clusters = N
top-level nodes.

Inter-cluster copper (tracks/vias between 2+ selected Clusters) is captured
separately as `net_traces:` records — NOT as tree nodes (KINDS has no
"net_trace"; net-trace-as-node is a separate task needing a new kind). See
detect_inter_cluster_nets below.

Testable without Qt.
"""
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.i18n import _
from kicadstamp.net_resolution import RULE_NETS
from kicadstamp.placement.services.component_resolver import (
    ComponentResolver,
    resolve_anchor_pad_position,
)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from kicadstamp.utils.units import MM

from .reead import ReReadCluster


@dataclass
class InterClusterNet:
    """One net whose selected copper connects 2+ fully-selected Clusters —
    a `net_traces:` capture candidate (the dialog's third tab)."""
    net: str
    track_count: int = 0
    via_count: int = 0


# A point-to-point inter-cluster link spans exactly 2 selected Clusters; a net
# on pads of MORE than this many is a ubiquitous rail (+3V3, GND...) — not a
# capture candidate for the third tab. 2026-09-01 review, live-verified on
# 3CH-AWG-TIA: GND on 6 Clusters, +3V3 on 3, real links on exactly 2.
DEFAULT_MAX_CLUSTER_COVERAGE = 2


# ── Anchor construction ───────────────────────────────────────────────────

def build_role_anchor(sheet: Optional[str], cluster: Optional[str],
                      role: str, pad: Optional[str] = None) -> TreeAnchor:
    """Assemble a role-based TreeAnchor from the dialog's narrowing fields:
    role is required, sheet/cluster narrow the role's ambiguity, pad is
    optional and moves the anchor point to that pad (same semantics as
    ClonePlacement.anchor_pad)."""
    return TreeAnchor(
        role=role, is_origin=False,
        anchor_sheet=sheet or None,
        anchor_cluster=cluster or None,
        anchor_pad=pad or None,
    )


def _zero_slot_role(entity: Any, cfg: Any) -> Optional[str]:
    """The role of the Entity's cell's single zero-offset (local (0,0))
    component — the "existing cluster anchor" role. Falls back to the first
    component's role when the cell has no zero-offset slot (a hand-authored
    cell without one), None when the cell has no components at all."""
    cell = cfg.cells.get(entity.cell)
    if cell is None or not cell.components:
        return None
    zero = [c for c in cell.components
            if c.offset_along_mm == 0.0 and c.offset_across_mm == 0.0]
    slots = zero or cell.components
    return slots[0].role


def tree_anchor_from_cluster_entity(entity: Any, cfg: Any) -> TreeAnchor:
    """The "existing cluster anchor" for an Entity: its own sheet/cluster
    narrow the role, and the role is the zero-slot role of the Entity's cell
    (the same base the tree auto-anchor derives). Used to prefill the anchor
    tab when the user picks a root cluster."""
    return TreeAnchor(
        role=_zero_slot_role(entity, cfg),
        anchor_sheet=entity.sheet,
        anchor_cluster=entity.cluster,
    )


# ── Auto Entity/cell derivation (2026-09-01 rework, phase A) ───────────────
# "Extract tree" used to be gated on a `entities:` record for every cluster
# (cluster_errors blocked OK when absent). On real boards (live 3CH-AWG-TIA,
# 2026-09-01) the existing entities carry NO cluster/sheet, so
# fully_selected_clusters returns entity=None for every cluster and the dialog
# could never proceed. Phase A lifts that gate: a cluster without a matching
# Entity is auto-satisfiable — an Entity is DERIVED (name unique per
# cluster+sheet instance) and its cell is the cluster's slug-named cell
# (existing, or GENERATED from the cluster's own selection at save time —
# resolve_cluster_entity / cluster_raw_items).

def cluster_cell_name(cluster: str) -> str:
    """Default cell name for a cluster — the same Cluster-tag slug reead.py's
    _slugify produces (dac_buf <- DAC_BUF, pif_avdd <- PIF_AVDD)."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", (cluster or "").strip().lower()).strip("_")


def _unique_entity_name(cluster: str, sheet: Optional[str],
                        entities: Iterable[Any]) -> str:
    """A unique Entity name for an auto-derived entity — the cluster slug plus
    the sheet instance (Channel_0/1/2 share the cluster tag, so the instance
    must disambiguate), suffixed until unique against cfg.entities."""
    base = cluster_cell_name(cluster)
    if sheet:
        base += "_" + re.sub(r"[^0-9a-zA-Z]+", "_", sheet.strip().lower()).strip("_")
    taken = {e.name for e in entities}
    name, i = base, 1
    while name in taken:
        i += 1
        name = f"{base}_{i}"
    return name


def resolve_cluster_entity(c: ReReadCluster, cfg: Any) -> tuple[str, str, bool]:
    """(entity_name, cell_name, is_new) for one fully-selected cluster.

    An existing Entity matched by (cluster, sheet) wins (c.entity_name is
    already that Entity, set by fully_selected_clusters). Otherwise an
    auto-derived Entity name (unique per cluster+sheet instance) pointing at
    the cluster's slug-named cell — which may not exist yet (generated at save
    time from the cluster's selection, see cluster_raw_items).
    """
    if c.entity_name:
        entity = next((e for e in cfg.entities if e.name == c.entity_name), None)
        if entity is not None:
            return entity.name, entity.cell, False
    return _unique_entity_name(c.cluster, c.sheet, cfg.entities), \
        cluster_cell_name(c.cluster), True


def cluster_raw_items(c: ReReadCluster, raw_items: Iterable[Any]) -> list:
    """The raw selection narrowed to one fully-selected cluster: its footprints
    (by ref) plus ALL selected vias/tracks. Foreign copper is left in on
    purpose — the extractor's connectivity filter drops whatever doesn't reach
    a kept pad (2026-09-01, same rationale as ExtractDock._reead_items_for_
    cluster, which phase F folds into this flow)."""
    kept = set(c.refs)
    return [i for i in raw_items
            if not (isinstance(i, Footprint) and i.ref not in kept)]


def cluster_origin_role(c: ReReadCluster, selected_footprints: Iterable[Any]) -> str | None:
    """A role appearing EXACTLY ONCE among the cluster's selected footprints — a
    stable anchor role for the auto-generated cell (phase A). The extractor uses
    it as origin_component_role, so that component lands at the cell's local
    (0,0) — giving the cell a zero-slot, which is what the Entity's live
    position read requires at apply. None when no role is unique (falls back to
    a bbox origin — the cell then has no zero-slot and the node saves without
    autopositioning)."""
    refs = set(c.refs)
    counts: dict[str, int] = {}
    for s in selected_footprints:
        if s.ref in refs and s.role:
            counts[s.role] = counts.get(s.role, 0) + 1
    uniq = sorted(role for role, n in counts.items() if n == 1)
    return uniq[0] if uniq else None


def resolve_cluster_live_position_mm(adapter, cfg, c: ReReadCluster, sheet_names,
                                     role: str, label: Optional[str] = None
                                     ) -> tuple[float, float]:
    """(x_mm, y_mm) of a cluster's role, live-resolved over the whole board with
    the sheet/cluster narrowing — the phase-A autopositioning twin of
    resolve_entity_live_position_mm for a cluster whose cell is generated only
    at save time. `role` is the cell's future zero-slot (cluster_origin_role),
    so this reads the same point the Entity's own zero-slot live read would."""
    resolver = ComponentResolver(adapter, cfg, sheet_names)
    fp = resolver.resolve_anchor_fp(
        None, role, c.sheet, c.cluster,
        label=label or _("cluster {cluster!r} live position").format(cluster=c.cluster))
    return fp.position.x / MM, fp.position.y / MM


# ── Validation ────────────────────────────────────────────────────────────

def cluster_errors(clusters: Iterable[ReReadCluster], entities,
                   cfg) -> list[str]:
    """One message per cluster ('' when OK), aligned by index with `clusters`.

    2026-09-01 rework (phase A): a cluster WITHOUT an Entity record (or whose
    Entity's cell is missing) is NO LONGER a blocking error — the Entity is
    derived and the cell generated from the cluster's own selection at save
    time (resolve_cluster_entity / cluster_raw_items / A1). The function
    returns no blocking errors (the dialog no longer disables OK for these
    rows; rows may still be flagged as informational in the UI)."""
    return ["" for _ in clusters]


def _name_errors(tree_name: str, cfg: Any, allow_existing: bool = False) -> list[str]:
    """Tree-name validation (empty / duplicate in cfg.trees) — an error here
    blocks the build exactly like a cluster error. allow_existing=True (phase E,
    re-extract): the name is expected to match an existing tree that is being
    UPDATED from the current selection, so the duplicate check is skipped."""
    if not tree_name or not tree_name.strip():
        return [_("Tree name must not be empty.")]
    if not allow_existing and any(t.name == tree_name for t in cfg.trees):
        return [_("A tree named {name!r} already exists.").format(name=tree_name)]
    return []


# ── Tree construction ─────────────────────────────────────────────────────

def build_tree_from_clusters(
    clusters: Iterable[ReReadCluster], tree_name: str, anchor: TreeAnchor,
    entities, cfg,
    *, entity_positions: Optional[dict] = None,
    anchor_base: Optional[tuple[float, float]] = None,
    net_nodes: Iterable[str] = (),
    allow_existing: bool = False,
) -> tuple[Optional[Tree], list[str]]:
    """Build the Tree from the checked clusters. Every cluster becomes a
    top-level kind="placement" TreeNode with ref = the Entity that will place
    it — an existing (cluster, sheet)-matched Entity when there is one, else
    the auto-derived Entity name persisted at save time (phase A,
    resolve_cluster_entity). xy (the offset from the anchor) is
    entity_positions[entity] - anchor_base when both are available
    (autopositioning), else None (live-position rule at apply). The anchor is
    preserved exactly as passed.

    Returns (None, errors) when the tree name is empty/duplicate — the only
    remaining hard error. A cluster without an Entity/cell is auto-satisfiable
    (phase A) and no longer blocks the build.
    """
    errors = _name_errors(tree_name, cfg, allow_existing=allow_existing)
    if errors:
        return None, errors
    errors = cluster_errors(clusters, entities, cfg)
    if any(errors):
        return None, errors

    nodes: list[TreeNode] = []
    for c in clusters:
        # ref = the Entity that WILL place this cluster: an existing
        # (cluster, sheet)-matched Entity when there is one, else the
        # auto-derived Entity name persisted at save time (phase A).
        entity_name, _cell, _is_new = resolve_cluster_entity(c, cfg)
        xy = None
        if entity_positions and anchor_base is not None:
            pos = entity_positions.get(entity_name)
            if pos is not None:
                xy = (pos[0] - anchor_base[0], pos[1] - anchor_base[1])
        nodes.append(TreeNode(
            ref=entity_name,
            kind="placement",
            xy=xy,
            polar=None,
            rotation=0.0,
            name=None,
            group=None,
            children=[],
        ))
    # Phase D (2026-09-01): the checked inter-cluster nets become top-level
    # kind="net_trace" nodes (ref = the net name, resolved to a net_traces:
    # record by link_trees). No xy — a net trace is stored as local offsets
    # from its own anchor; live-position at apply.
    for net in net_nodes:
        nodes.append(TreeNode(
            ref=net,
            kind="net_trace",
            xy=None,
            polar=None,
            rotation=0.0,
            name=None,
            group=None,
            children=[],
        ))
    return Tree(name=tree_name.strip(), anchor=anchor, nodes=nodes), []


# ── Inter-cluster copper detection ────────────────────────────────────────

def _cluster_nets(clusters: Iterable[ReReadCluster],
                  snapshot: Iterable[Any]) -> list[set[str]]:
    """One set of net names per cluster (aligned by index with `clusters`) —
    the union of every pad net on the cluster's footprints (from the
    snapshot's Selected.nets dicts, the same Board.select source the rest of
    the GUI uses)."""
    by_ref = {s.ref: s for s in snapshot}
    out: list[set[str]] = []
    for c in clusters:
        nets: set[str] = set()
        for ref in c.refs:
            s = by_ref.get(ref)
            if s is not None:
                nets_data = getattr(s, "nets", {}) or {}
                nets.update(nets_data.values())
        out.append(nets)
    return out


def _connected_cluster_labels(adapter, clusters: Iterable[ReReadCluster],
                              raw_items: Iterable[Any], net: str) -> set[int]:
    """Cluster indices whose pads the net's SELECTED copper reaches — via a
    connected-component closure over the net's selected tracks/vias anchored at
    the clusters' pads (the same union-find pattern as template_selection's
    _filter_tracks_and_vias_within_selection, but per-cluster-LABELLED so we
    know WHICH clusters a component touches). Phase C (2026-09-01) — the "по
    выделенному" strengthener: a net is inter-cluster only when its selected
    copper genuinely reaches pads of 2+ clusters, not merely shares a name."""
    # Local import: template_selection pulls placement.services.role_narrowing;
    # a module-level import would widen tree_from_selection's load graph for no
    # benefit (the import itself is acyclic — verified 2026-09-01).
    from kicadstamp.template_selection import _inflated_boxes, _point_in_box, _points_match

    tracks = [t for t in raw_items if isinstance(t, Track) and t.net_name == net]
    vias = [v for v in raw_items if isinstance(v, Via) and v.net_name == net]
    if not tracks and not vias:
        return set()

    # Inflated pad boxes per cluster (the same anchors the extractor roots its
    # connectivity closure at).
    pad_boxes: list[list] = []
    for c in clusters:
        refs = set(c.refs)
        pads = []
        for fp in raw_items:
            if isinstance(fp, Footprint) and fp.ref in refs:
                pads.extend(adapter.get_footprint_pads(fp))
        pad_boxes.append(_inflated_boxes(adapter, pads))

    parent: dict = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(tracks)):
        parent[("t", i)] = ("t", i)
    for i in range(len(vias)):
        parent[("v", i)] = ("v", i)
    for k in range(len(clusters)):
        parent[("c", k)] = ("c", k)

    def _touches(k, i, is_track):
        boxes = pad_boxes[k]
        if not boxes:
            return False
        if is_track:
            t = tracks[i]
            return any(_point_in_box(t.start, b) or _point_in_box(t.end, b) for b in boxes)
        v = vias[i]
        return any(_point_in_box(v.position, b) for b in boxes)

    # Anchor each track/via to every cluster whose pad box it touches.
    for k in range(len(clusters)):
        for i in range(len(tracks)):
            if _touches(k, i, True):
                union(("t", i), ("c", k))
        for i in range(len(vias)):
            if _touches(k, i, False):
                union(("v", i), ("c", k))
    # Track-to-track / track-to-via joints (copper chains across clusters).
    for i, t in enumerate(tracks):
        for j, v in enumerate(vias):
            if _points_match(t.start, v.position) or _points_match(t.end, v.position):
                union(("t", i), ("v", j))
    for i, t in enumerate(tracks):
        for j in range(i + 1, len(tracks)):
            o = tracks[j]
            if (_points_match(t.start, o.start) or _points_match(t.start, o.end)
                    or _points_match(t.end, o.start) or _points_match(t.end, o.end)):
                union(("t", i), ("t", j))

    labels: set[int] = set()
    for k in range(len(clusters)):
        croot = find(("c", k))
        if any(find(("t", i)) == croot for i in range(len(tracks))) or \
           any(find(("v", i)) == croot for i in range(len(vias))):
            labels.add(k)
    return labels


def detect_inter_cluster_nets(raw_items: Iterable[Any],
                              clusters: Iterable[ReReadCluster],
                              snapshot: Iterable[Any],
                              rule_nets: Iterable[str] = (),
                              max_cluster_coverage: int = DEFAULT_MAX_CLUSTER_COVERAGE,
                              adapter=None,
                              ) -> list[InterClusterNet]:
    """Nets of the raw SELECTED copper that connect 2+ fully-selected Clusters
    (i.e. do not belong to one cluster-cell alone) — the `net_traces:` capture
    candidates shown in the dialog's third tab.

    A net is inter-cluster when its name appears on the footprints of at
    least two clusters (from the snapshot's Selected.nets). Excluded:

      - rule nets — rule_nets (a power net a Rule/Chain already plans) AND the
        default RULE_NETS (kicadstamp.net_resolution.RULE_NETS, {"GND"}), the
        same always-excluded set the Cells/Extract dock uses — so a global GND
        is never offered even when no Chain registers it (2026-09-01 review,
        live 3CH-AWG-TIA: GND leaked with 32 tracks / 25 vias);
      - ubiquitous rails — a net that sits on pads of MORE than
        `max_cluster_coverage` of the SELECTED clusters. A point-to-point
        inter-cluster link spans exactly 2 clusters; a global rail (+3V3, GND)
        spans most/all of them (live: GND on 6, +3V3 on 3, real links on
        exactly 2). Configurable, default 2 — i.e. coverage > 2 is a rail.

    Only nets that ALSO have selected tracks/vias in `raw_items` are offered —
    a net with no selected copper is nothing to capture, so the tab stays
    empty (the dialog then has no nets tab content).

    adapter — OPTIONAL (phase C): when given, the name-based candidates are
    additionally filtered by CONNECTIVITY — only nets whose selected copper
    actually reaches pads of 2+ clusters (via _connected_cluster_labels) are
    offered, so a net that merely shares a name (or a stitching via that touches
    no cluster pad) is dropped. None (default) keeps the pure name-based
    detection (used by tests and callers without a board adapter)."""
    cluster_nets = _cluster_nets(clusters, snapshot)
    # coverage[net] = how many SELECTED Clusters carry the net on a pad —
    # the signal that separates a point-to-point link (2) from a ubiquitous
    # rail (+3V3, GND — 3+).
    coverage: dict[str, int] = {}
    for nets in cluster_nets:
        for net in nets:
            coverage[net] = coverage.get(net, 0) + 1

    inter: set[str] = set()
    for i in range(len(cluster_nets)):
        for j in range(i + 1, len(cluster_nets)):
            inter.update(cluster_nets[i] & cluster_nets[j])
    inter -= set(rule_nets)
    inter -= RULE_NETS  # default rule nets — GND is always a rule net
    inter = {n for n in inter if coverage.get(n, 0) <= max_cluster_coverage}
    # Phase C connectivity filter — only when the adapter actually provides the
    # geometry the union-find closure needs (a limited/`object()` adapter in
    # tests falls back to the pure name-based detection).
    if adapter is not None and hasattr(adapter, "get_bounding_boxes") \
            and hasattr(adapter, "get_footprint_pads"):
        raw = list(raw_items)
        inter = {n for n in inter
                 if len(_connected_cluster_labels(adapter, clusters, raw, n)) >= 2}
    if not inter:
        return []

    counts: dict[str, list[int]] = {}
    for item in raw_items:
        if isinstance(item, Track):
            net = item.net_name
            if net and net in inter:
                counts.setdefault(net, [0, 0])[0] += 1
        elif isinstance(item, Via):
            net = item.net_name
            if net and net in inter:
                counts.setdefault(net, [0, 0])[1] += 1
    return [InterClusterNet(net=net, track_count=c[0], via_count=c[1])
            for net, c in sorted(counts.items())]


# ── Live-position bridges (Qt-free, but need a live board adapter) ────────

def resolve_entity_live_position_mm(adapter, cfg, entity: Any, sheet_names,
                                    label: Optional[str] = None
                                    ) -> tuple[float, float]:
    """(x_mm, y_mm) of a cluster's Entity — its cell's zero-offset (local
    (0,0)) component's role, live-resolved over the whole board (the SAME
    derivation the tree auto-anchor and tree_position's "placement" branch
    use — an Entity carries no position by design, so its current board
    position IS its zero-slot component). Local import: entity_placement
    imports tree_position at module level, so a module-level import here
    would be circular on the entity_placement side."""
    from kicadstamp.placement.entity_placement import _entity_own_zero_slot_live_position
    pos, _rot = _entity_own_zero_slot_live_position(
        adapter, cfg, entity, sheet_names, label=label)
    return pos.x / MM, pos.y / MM


def resolve_role_anchor_base_mm(adapter, cfg, anchor: TreeAnchor, sheet_names,
                                label: Optional[str] = None
                                ) -> tuple[float, float]:
    """(x_mm, y_mm) of a role anchor's base point: resolve the role footprint
    over the whole board (narrowed by sheet/cluster), then the pad centre
    when anchor_pad is set, else the footprint centre — the same resolution
    _anchor_base uses at materialization. Only role/origin anchors are
    supported here (the dialog produces role anchors only; origin is trivial)."""
    if anchor.is_origin:
        return 0.0, 0.0
    label = label or _("tree anchor (from selection)")
    resolver = ComponentResolver(adapter, cfg, sheet_names)
    fp = resolver.resolve_anchor_fp(
        None, anchor.role, anchor.anchor_sheet, anchor.anchor_cluster, label=label)
    if anchor.anchor_pad:
        pos = resolve_anchor_pad_position(adapter, fp, anchor.anchor_pad, label)
    else:
        pos = fp.position
    return pos.x / MM, pos.y / MM
