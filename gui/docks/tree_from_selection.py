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
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from kicadstamp.domain.board import Track, Via
from kicadstamp.i18n import _
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


# ── Validation ────────────────────────────────────────────────────────────

def cluster_errors(clusters: Iterable[ReReadCluster], entities,
                   cfg) -> list[str]:
    """One error message per cluster ('' when valid), aligned by index with
    `clusters`: a cluster with no Entity record, or an Entity whose cell is
    missing from cfg.cells, BLOCKS the tree build (plan: "no cell" rows are
    marked in the dialog, OK is disabled)."""
    errors: list[str] = []
    for c in clusters:
        if not c.entity_name:
            errors.append(_("cluster {cluster!r}: no Entity").format(cluster=c.cluster))
            continue
        entity = next((e for e in entities if e.name == c.entity_name), None)
        if entity is None:
            errors.append(_("cluster {cluster!r}: Entity {name!r} not found in "
                            "the config").format(cluster=c.cluster, name=c.entity_name))
        elif cfg.cells.get(entity.cell) is None:
            errors.append(_("cluster {cluster!r}: Entity {name!r} references "
                            "missing cell {cell!r}").format(
                                cluster=c.cluster, name=c.entity_name, cell=entity.cell))
        else:
            errors.append("")
    return errors


def _name_errors(tree_name: str, cfg: Any) -> list[str]:
    """Tree-name validation (empty / duplicate in cfg.trees) — an error here
    blocks the build exactly like a cluster error."""
    if not tree_name or not tree_name.strip():
        return [_("Tree name must not be empty.")]
    if any(t.name == tree_name for t in cfg.trees):
        return [_("A tree named {name!r} already exists.").format(name=tree_name)]
    return []


# ── Tree construction ─────────────────────────────────────────────────────

def build_tree_from_clusters(
    clusters: Iterable[ReReadCluster], tree_name: str, anchor: TreeAnchor,
    entities, cfg,
    *, entity_positions: Optional[dict] = None,
    anchor_base: Optional[tuple[float, float]] = None,
) -> tuple[Optional[Tree], list[str]]:
    """Build the Tree from the checked clusters. Every cluster becomes a
    top-level kind="placement" TreeNode with ref = its Entity's name; xy (the
    offset from the anchor) is entity_positions[entity] - anchor_base when
    both are available (autopositioning), else None (live-position rule at
    apply). The anchor is preserved exactly as passed.

    Returns (None, errors) when the tree name is empty/duplicate or any
    cluster is invalid (no Entity / missing cell) — the dialog blocks OK on
    those before ever calling this, so this is the defensive second line.
    """
    errors = _name_errors(tree_name, cfg)
    if errors:
        return None, errors
    errors = cluster_errors(clusters, entities, cfg)
    if any(errors):
        return None, errors

    nodes: list[TreeNode] = []
    for c in clusters:
        entity = next((e for e in entities if e.name == c.entity_name), None)
        xy = None
        if entity is not None and entity_positions and anchor_base is not None:
            pos = entity_positions.get(entity.name)
            if pos is not None:
                xy = (pos[0] - anchor_base[0], pos[1] - anchor_base[1])
        nodes.append(TreeNode(
            ref=entity.name,
            kind="placement",
            xy=xy,
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


def detect_inter_cluster_nets(raw_items: Iterable[Any],
                              clusters: Iterable[ReReadCluster],
                              snapshot: Iterable[Any],
                              rule_nets: Iterable[str] = ()) -> list[InterClusterNet]:
    """Nets of the raw SELECTED copper that connect 2+ fully-selected Clusters
    (i.e. do not belong to one cluster-cell alone) — the `net_traces:` capture
    candidates shown in the dialog's third tab.

    A net is inter-cluster when its name appears on the footprints of at
    least two clusters (from the snapshot's Selected.nets). Rule nets
    (rule_nets — e.g. a power net a Rule already plans) are excluded. Only
    nets that ALSO have selected tracks/vias in `raw_items` are offered —
    a net with no selected copper is nothing to capture, so the tab stays
    empty (the dialog then has no nets tab content)."""
    cluster_nets = _cluster_nets(clusters, snapshot)
    inter: set[str] = set()
    for i in range(len(cluster_nets)):
        for j in range(i + 1, len(cluster_nets)):
            inter.update(cluster_nets[i] & cluster_nets[j])
    inter -= set(rule_nets)
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
