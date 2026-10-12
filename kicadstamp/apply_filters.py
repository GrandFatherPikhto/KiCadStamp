# kicadstamp/apply_filters.py
"""Pure helpers of the apply pipeline — split out of ``apply_pipeline.py``.

Why they live here (rule 45, 2026-10-12)
---------------------------------------
``apply_pipeline.py`` is a giant (1200+ lines) and giants only SHRINK. The guard
for a duplicated refdes (``apply_duplicate_guard``) had to be wired into the
pipeline's one door, so the growth was paid for by moving the pipeline's PURE
helpers out: they need no adapter, no registry and no board — a Config, a list
of values, or a list of live items in, a value out.

``apply_pipeline`` imports these names back (``from .apply_filters import ...``),
so every existing caller and every existing guard keeps importing them from
``kicadstamp.apply_pipeline`` exactly as before — the move is invisible.
"""
from __future__ import annotations

import logging

from .cluster_matching import matches_any_cluster
from .config import clone_placement_effective_name
from .trees import _walk_nodes as _walk_tree_nodes

logger = logging.getLogger(__name__)

__all__ = [
    "_split_comma_values", "_component_node_names",
    "_filter_materialized_entities", "_live_items_after_deletion",
]


def _split_comma_values(raw: list[str] | None) -> list[str]:
    """--only/--cluster accept both repeating the flag and comma‑separated
    values within one occurrence (e.g. --only a,b --only c -> [a, b, c])."""
    if not raw:
        return []
    result = []
    for item in raw:
        result.extend(part.strip() for part in item.split(",") if part.strip())
    return result


def _component_node_names(cfg) -> set[str]:
    """The refs of every kind "component" tree node (plan_2026_09_17 Э3).

    A component node's ref is a LOCAL NAME, so it is not a record in any config
    section — but it IS an --only identity, because its transient
    CoordinatePlacement carries exactly that name. Recognising it here keeps
    `--only <node name>` (the per-node Redraw button's own call) from being
    rejected as "not found" before the record even exists."""
    names: set[str] = set()
    for tree in (getattr(cfg, "trees", None) or []):
        for node in _walk_tree_nodes(tree.nodes):
            if node.kind == "component":
                names.add(node.ref)
    return names


def _filter_materialized_entities(clones, only: list[str] | None,
                                  cluster: list[str] | None) -> list:
    """Apply --only/--cluster to materialized Entity placements (phase 4.1
    fix). Entity placements are materialized from the FULL cfg (link_trees
    runs over ALL trees/entities — see materialize_entity_placements), so
    --only/--cluster must NOT cut cfg.entities in apply_only_filter/
    apply_cluster_filter first (that would make link_trees fatal on a tree
    node whose entity was filtered away). Instead the materialized clones are
    narrowed HERE by the same axes as regular clone_placements:
    --only by the clone's effective name (== Entity.name), --cluster by the
    clone's cluster tag (== Entity.cluster)."""
    if not clones:
        return clones
    if only:
        wanted = set(only)
        clones = [c for c in clones
                  if clone_placement_effective_name(c) in wanted]
    if cluster:
        clones = [c for c in clones if matches_any_cluster(c.cluster, cluster)]
    return clones


# _filter_materialized_coordinates lived here until Э4/Т4.4 of
# plan_2026_09_16_commit_document_and_pending_direction: it narrowed the
# transient component-node records by --only/--cluster, and
# materialize_component_nodes (which CREATES them) already did exactly the same
# with the same two axes — the same predicate, the same effective name. A
# mutation disabling this pass changed nothing observable, which is what "dead
# weight" means here. The materializer's own narrowing is the one kept: it is
# where the records are born (its docstring promises the narrowing there), it is
# what tests/test_component_nodes.py exercises, and it is where the identity map
# of Т4.1 is narrowed together with the records, so the map cannot describe a
# record the run dropped.


def _live_items_after_deletion(live_items, deleted_uuids):
    """The live board items with the just-deleted UUIDs removed.

    Phases 2 (vias) and 3 (tracks) each fetch the live list ONCE and feed it to
    three consumers: reconcile() (whose to_delete also carries pruned keys),
    adapter.remove_by_ids(), and the positional pre-check (filter_existing_vias
    / filter_existing_tracks). After the deletion that pre-deletion list is
    stale: it still counts the just-deleted copper as present on the board, so
    a command whose registry KEY changed while its geometry did not — renaming
    a cell or a thermal via array, reordering vias inside a cell — is skipped
    as "already exists" right after its copper was deleted, and the apply
    leaves the board empty until the next run (the next apply then sees no live
    copper and finally creates it).

    The positional pre-check must see the board AFTER remove_by_ids(); this is
    the ONE place that computes that view, shared by both phases so they cannot
    drift apart.

    2026-10-02, plan_2026_10_02_stale_live_list_after_prune.md.
    """
    if not deleted_uuids:
        return live_items
    deleted = set(deleted_uuids)
    return [item for item in live_items if item.uuid not in deleted]
