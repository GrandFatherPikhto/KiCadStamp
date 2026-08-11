# kicadstamp/cluster_matching.py
"""Pure string matching of cluster names — no kipy, no placement types.

cluster_prefix_match() used to live in placement/services/component_pool.py,
but its consumers are top-level modules too (apply_pipeline, explore) and
diagnostics — a placement-owned location forced those placement-independent
callers to depend on the placement package just for one string helper. This
neutral module is the single home; placement imports it like everyone else.
"""


def cluster_prefix_match(candidate_cluster: str, wanted: str) -> bool:
    """
    candidate_cluster == wanted, OR candidate_cluster starts with 'wanted/' —
    segment‑prefix comparison, not substring (so 'Channel_1' does not match
    'Channel_10'). Flat names without '/' reduce to exact match — hierarchy is
    optional, works with the same code. Single implementation shared across the
    whole project — previously duplicated with exact match here and prefix match
    in clone_role_resolver.py, causing SILENTLY DIFFERENT behaviour of Cluster
    in two places; now one function, defined here (not in clone_role_resolver.py —
    that already depends on component_pool via ROLE_FIELD_NAME, and the reverse
    dependency would create an import cycle).
    """
    return candidate_cluster == wanted or candidate_cluster.startswith(wanted + '/')
