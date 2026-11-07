# kicadstamp/config/tree_instance_checks.py
"""Read-only QUESTIONS about a `tree_instances:` TEMPLATE — the raw-dict face of
the expansion, kept out of the giant `config/tree_instances.py` (Н8/Н4 of
plan_2026_11_07_tree_instances_dialog_and_subtree).

Moved here verbatim from `tree_instances._template_generated_clusters`
(composite-guard, 2026-09-08, plan tree_instances_cluster_composite_guard): the
question "which clusters would this template's placement nodes produce" is now
also asked by the Instances dialog BEFORE a row is written (Н4), and by the
cluster-axis converter step — one host, no second walk.
"""
from __future__ import annotations

__all__ = ["template_generated_clusters"]


def template_generated_clusters(nodes: list, entities_by_name: dict) -> set[str]:
    """The set of distinct non-empty `cluster` values the given template
    placement nodes (recursively through children) would generate BEFORE any
    declaration-level cluster: override — used to detect whether a template is
    homogeneous (<=1 distinct value, override safe to apply everywhere, today's
    behaviour) or composite (>1 distinct value — e.g. ch0_dac_buf's DAC_BUF main
    entity + PIF_AVDD/PIF_CLKVDD/PIF_DVDD sub-blocks — where a blanket override
    would erase the very distinction role_narrowing.py's Cluster step depends
    on, confirmed live 2026-09-08, done_2026_09_08_role_narrowing_live_probe.md).

    net_trace nodes are skipped (their ref names a net, not an entity, and the
    override never applies to them anyway — see the module docstring's v1.2
    note); a placement node whose ref has no entities: entry is skipped too (it
    would be a load-time fatal elsewhere, here it must not crash the probe)."""
    clusters: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get('kind') == 'net_trace':
            continue
        ref = node.get('ref')
        entity = entities_by_name.get(ref) if ref is not None else None
        if entity is not None:
            c = entity.get('cluster')
            if c:
                clusters.add(c)
        children = node.get('children') or []
        if children:
            clusters |= template_generated_clusters(children, entities_by_name)
    return clusters
