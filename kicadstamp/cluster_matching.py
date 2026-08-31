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

    Case-insensitive since 2026-08-31 (plan_2026_08_31_fpga_flash_rigid_redraw_
    not_following.md): a Cluster tag is a user-visible label, its case is not
    semantically significant. Live repro: an Entity materialized from a tree
    whose cell was extracted with Origin "By component role" may fall back its
    cluster to entity.name (e.g. "fpga_flash", lower-case) while the physical
    Cluster field on the board is upper-case ("FPGA_FLASH") — a case-sensitive
    segment match then empties the candidate set and the live net auto-derivation
    (_auto_derive_live_net) cannot find the unique instance, failing the whole
    apply/redraw of that placement. Both sides are lower-cased before the
    segment-prefix comparison.
    """
    candidate = candidate_cluster.lower()
    wanted_l = wanted.lower()
    return candidate == wanted_l or candidate.startswith(wanted_l + '/')
