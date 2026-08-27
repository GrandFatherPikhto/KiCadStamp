# kicadstamp/link_trees.py
"""Resolve trees-layer `ref`s against a loaded Config.

The pure-syntax loader (kicadstamp/trees.py) does not know YAML; this module
is the linking pass that connects each tree node/anchor `ref` to the actual
config record. Design: techdocs/handoff/deepseek/design_2026_08_26_link_trees.md
(all forks resolved, only FORK-1's inline-anchor half is in scope here).

Output is WRAPPERS (LinkedTree/LinkedNode/LinkedAnchor), not mutations of the
Tree/TreeNode/TreeAnchor dataclasses — same normalization pattern as
anchor_graph.build_records(). Trees stay NESTED; flattening into an ordered
name list for run_cascade is the position-resolution phase, not here.

Resolution rules (all fatal via ValidationError, formatted AFTER _()):
  - node with explicit kind: by_key[`{kind}:{ref}`], fatal if absent
  - node with no kind: auto-search the 4 placeable sections
    (clone/rule/coordinate/point), fatal on 0 or 2+ matches
  - node with kind "external": record = None, never touches config
  - anchor with is_origin: record = None, not "external"
  - anchor otherwise: auto-search; 1 match -> record; 0 -> SILENT external
    (anchors may legally point at live-board components, mirroring the
    never-statically-validated anchor_ref); 2+ -> fatal
  - FORK-1: fatal if a resolved node record carries its own inline
    anchor_ref/anchor_role/anchor_point/anchor_origin (read from rec.obj —
    anchor_origin is a Point-only field not copied onto Record)
"""
from dataclasses import dataclass

from .anchor_graph import Record, build_records, record_key
from .exceptions import ValidationError
from .i18n import _
from .trees import KINDS, Tree, TreeAnchor, TreeNode

# The 4 sections a node/anchor can auto-search without an explicit kind —
# net_trace/thermal_via are not valid node kinds, so the index never scans
# them (symmetry between the kind whitelist and the search index).
_PLACEABLE_KINDS = set(KINDS) - {"external"}

# inline position-source fields FORK-1 checks on the resolved record's obj —
# a tree-placed record must not ALSO carry its own anchor (two sources of truth).
_INLINE_ANCHOR_FIELDS = ("anchor_ref", "anchor_role", "anchor_point", "anchor_origin")


@dataclass
class LinkedAnchor:
    anchor: TreeAnchor            # original node, as-is
    record: Record | None         # resolved config record, or None
    is_origin: bool
    is_external: bool             # True if ref not found in config (external refdes)


@dataclass
class LinkedNode:
    node: TreeNode                 # original node, as-is
    record: Record | None          # None ONLY when node.kind == "external"
    is_external: bool
    children: list["LinkedNode"]


@dataclass
class LinkedTree:
    name: str
    anchor: LinkedAnchor
    nodes: list[LinkedNode]


def _fatal(message: str) -> None:
    raise ValidationError(message)


def _build_by_key_index(records: list[Record]) -> dict[str, Record]:
    """{record_key: Record} for exact lookup with an explicit kind. Detects
    collisions (two records yielding one key, e.g. two clone_placements with
    the same effective name) as fatal — one key must map to exactly one record
    (clone-name uniqueness is NOT guaranteed by load_config, see design §2.1
    fact-check A)."""
    by_key: dict[str, Record] = {}
    for rec in records:
        key = record_key(rec)
        if key in by_key:
            _fatal(_("config has multiple records with key {key!r} ({a!r} and {b!r}) — "
                     "names must be unique").format(key=key, a=by_key[key].name, b=rec.name))
        by_key[key] = rec
    return by_key


def _build_by_name_index(records: list[Record]) -> dict[str, list[Record]]:
    """{name: [records]} grouped by name, filtered to the 4 placeable kinds —
    for auto-search without an explicit kind. Order preserved per name."""
    by_name: dict[str, list[Record]] = {}
    for rec in records:
        if rec.kind not in _PLACEABLE_KINDS:
            continue
        by_name.setdefault(rec.name, []).append(rec)
    return by_name


def _resolve_node_ref(node: TreeNode, by_key: dict[str, Record],
                      by_name: dict[str, list[Record]]) -> tuple[Record | None, bool]:
    """Resolve one node's ref -> (record, is_external). Follows the node
    resolution rules from the module docstring (explicit kind, auto-search,
    external marker)."""
    ref = node.ref
    if node.kind == "external":
        # Live-board-only refdes: never touch the config (symmetrically to
        # anchor_ref, which is never statically validated).
        return None, True

    if node.kind is not None:
        rec = by_key.get(f"{node.kind}:{ref}")
        if rec is None:
            _fatal(_("Node {ref!r} (kind {kind!r}) not found in config")
                   .format(ref=ref, kind=node.kind))
        return rec, False

    candidates = by_name.get(ref, [])
    if not candidates:
        _fatal(_("Node {ref!r} not found in config").format(ref=ref) + " " +
               _("If this is an external (live-board-only) refdes, add (kind external)"))
    if len(candidates) > 1:
        kinds = ", ".join(sorted({c.kind for c in candidates}))
        _fatal(_("Node {ref!r} is ambiguous across sections: {kinds}")
               .format(ref=ref, kinds=kinds) + " " +
               _("Add an explicit (kind ...) to disambiguate"))
    return candidates[0], False


def _resolve_anchor_ref(anchor: TreeAnchor,
                        by_name: dict[str, list[Record]]) -> tuple[Record | None, bool]:
    """Resolve a tree anchor's ref -> (record, is_external). is_origin never
    resolves (no base, not "external"). An explicit external marker (the
    anchor's (external) child) is ALWAYS external — never touches config, so
    a name collision with a config record is impossible (the fix for
    note_2026_08_28_tree_anchor_name_collision). Zero matches is SILENTLY
    external — a legacy fallback for anchors pointing at a live component
    outside the config (kept for backward compatibility)."""
    if anchor.is_origin:
        return None, False
    if anchor.is_external:
        return None, True
    candidates = by_name.get(anchor.ref, [])
    if not candidates:
        return None, True
    if len(candidates) > 1:
        kinds = ", ".join(sorted({c.kind for c in candidates}))
        _fatal(_("Anchor {ref!r} is ambiguous across sections: {kinds}")
               .format(ref=anchor.ref, kinds=kinds))
    return candidates[0], False


def _check_fork1_inline_conflict(node: TreeNode, record: Record | None) -> None:
    """FORK-1 (inline-anchor half): a tree-placed record must not ALSO carry
    its own inline anchor — two sources of truth on one record's position.
    Reads from rec.obj via getattr: Record itself does not carry anchor_origin
    (a Point-only field), so a point node with anchor_origin would silently
    pass unless we read the original dataclass (design §4 fact-check B).
    Nodes only — a tree anchor is a base, not something the tree "places"."""
    if record is None:
        return
    for field in _INLINE_ANCHOR_FIELDS:
        if getattr(record.obj, field, None) is not None:
            _fatal(_("Node {ref!r} is placed by a tree but its own config record "
                     "already has an inline anchor ({field})")
                   .format(ref=node.ref, field=field))


def _link_node(node: TreeNode, by_key: dict[str, Record],
               by_name: dict[str, list[Record]]) -> LinkedNode:
    """Recursively wrap one TreeNode (and its children) into a LinkedNode."""
    record, is_external = _resolve_node_ref(node, by_key, by_name)
    _check_fork1_inline_conflict(node, record)
    return LinkedNode(
        node=node,
        record=record,
        is_external=is_external,
        children=[_link_node(c, by_key, by_name) for c in node.children],
    )


def _link_tree(tree: Tree, by_key: dict[str, Record],
               by_name: dict[str, list[Record]]) -> LinkedTree:
    record, is_external = _resolve_anchor_ref(tree.anchor, by_name)
    return LinkedTree(
        name=tree.name,
        anchor=LinkedAnchor(
            anchor=tree.anchor,
            record=record,
            is_origin=tree.anchor.is_origin,
            is_external=is_external,
        ),
        nodes=[_link_node(n, by_key, by_name) for n in tree.nodes],
    )


def link_trees(cfg, trees: list[Tree]) -> list[LinkedTree]:
    """Resolve every tree node/anchor ref against `cfg` (a loaded Config).

    Indexes are built ONCE here (before any tree loop): by_key for explicit-
    kind lookup (with collision detection), by_name for auto-search over the
    4 placeable sections. retired: true records are already dropped by
    build_records(), so a node/anchor referring to one gets "not found"
    (fatal for nodes, silent-external for anchors) — deliberate behavior."""
    records = build_records(cfg)
    by_key = _build_by_key_index(records)
    by_name = _build_by_name_index(records)
    return [_link_tree(t, by_key, by_name) for t in trees]
