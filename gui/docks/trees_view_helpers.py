# gui/docks/trees_view_helpers.py
"""Pure VIEW-model helpers of the Trees dock: the "Copper" pseudo-node's row
marker, the anchor pseudo-root's label and the two tree walks.

Split out of trees_dock.py on 2026-10-20 (plan_2026_10_15_extract_and_read
_paths_cleanup, dodelka Д-2): the Ч2 work added a `verdict` parameter, two Log
lines and a docstring paragraph to that dock, and the "giants only shrink" rule
of this plan is a ratchet measured AFTER Ч1 — the ceiling for trees_dock.py is
7189 lines, not the older 7275 the task text still quotes. Nothing here touches
a board, a config or a widget's state: every name is a value produced from a
Tree (a marker class, a label string, two ref/net walks), so the move is a
verbatim lift and trees_dock.py re-exports the names, keeping every existing
`trees_dock.collect_tree_refs` / `trees_dock._anchor_label` seam alive.
"""
from kicadstamp.i18n import _
from kicadstamp.trees import TreeAnchor


# ── The "Copper" pseudo-node (2026-09-16, plan_2026_09_16_copper_pseudo_node) ──
#
# A VIEW-ONLY row (Т1.1/Т1.2): it exists in the widget tree and in NOTHING else
# — no TreeNode, no config entry, no record, and it is deliberately absent from
# its OWN anchor pad).
#
# Its UserRole payload is therefore a MARKER OBJECT — not None (that is the
# anchor pseudo-root, Ф1), not a str (that is a tree-navigation pseudo item) and
# not a TreeNode (that is a real node) — and every place that decodes UserRole
# names it explicitly.
class _CopperGroupItem:
    """Sentinel carried in UserRole by the "Copper" pseudo-node's row (Т1.2).

    The CLASS is the identity _is_copper_group_item matches on, so a freshly
    built marker is never mistaken for a TreeNode, a str or the anchor's None."""


def _is_copper_group_item(data) -> bool:
    """True when `data` (a row's UserRole payload) is the "Copper" pseudo-node
    marker — the ONE predicate every UserRole decoder asks, so no decoder can
    forget the marker and fall into the anchor branch (Ф1: "no UserRole" used to
    mean "the anchor pseudo-root")."""
    return isinstance(data, _CopperGroupItem)


# Node kinds that NEVER get a row in their file position any more: they are
# drawn under the "Copper" pseudo-node instead (Т1.1) — every "net_trace" row,
# plus the legacy kind "copper" CONTAINER row, which the pseudo-node replaces as
# the folding mechanism (Т1.4: a container in an existing file still LOADS and
# is still appended to, it is just not drawn — its children are shown under the
# pseudo-node like every other copper node).
_COPPER_ROW_KINDS = frozenset({"net_trace", "copper"})


def _anchor_label(anchor: TreeAnchor) -> str:
    """Human-readable label for a tree's anchor pseudo-root — one branch per
    TreeAnchor mode; never renders "None" (2026-08-31, anchor-dialog GUI gap:
    self/role/point anchors carry ref=None and would otherwise show "⚓ None").
    The exact tag per mode is a display convention only — the underlying
    TreeAnchor is unchanged."""
    if anchor.is_origin:
        return _("⚓ (origin)")
    if anchor.is_self:
        return _("⚓ (self)")
    if anchor.role:
        details = " / ".join(
            part for part in (anchor.anchor_sheet, anchor.anchor_cluster,
                              anchor.anchor_pad) if part)
        base = _("⚓ (role {role})").format(role=anchor.role)
        return f"{base} {details}" if details else base
    if anchor.point:
        return _("⚓ (point {point})").format(point=anchor.point)
    if anchor.ref:
        if anchor.is_external:
            return _("⚓ {ref} (external)").format(ref=anchor.ref)
        return f"⚓ {anchor.ref}"
    return _("⚓ (unknown)")


def collect_tree_refs(tree: "Tree") -> list[str]:
    """ALL node refs of a Tree, DFS parent-before-child, regardless of any
    checkbox state — the selection source for "Redraw whole tree"
    (plan_2026_08_29_fork1_rigid_redraw_override.md §5): the whole operation
    must not depend on the UI checkbox state, only on the tree structure."""
    refs: list[str] = []

    def walk(nodes: list) -> None:
        for node in nodes:
            refs.append(node.ref)
            walk(node.children)

    walk(tree.nodes)
    return refs


def _tree_net_trace_nets(tree: "Tree") -> set[str]:
    """Every net referenced by the tree's kind="net_trace" nodes (phase D/E,
    2026-09-01) — the net_traces: records that belong to this tree's captured
    inter-cluster copper (used by the delete-tree cascade to find orphans).

    Since 2026-09-12 a node's ref is the record's IDENTITY (name:, else a legacy
    record's net:) — the same string net_trace_effective_name() returns and
    link_trees resolves the node through, so the cascade's net-based comparison
    below is kept in step by the caller (see _on_delete_tree)."""
    nets: set[str] = set()

    def walk(nodes: list) -> None:
        for node in nodes:
            if node.kind == "net_trace" and node.ref:
                nets.add(node.ref)
            walk(node.children)

    walk(tree.nodes)
    return nets
