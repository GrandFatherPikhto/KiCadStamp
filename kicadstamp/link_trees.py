# kicadstamp/link_trees.py
"""Resolve trees-layer `ref`s against a loaded Config.

The pure-syntax loader (kicadstamp/trees.py) does not know YAML; this module
is the linking pass that connects each tree node/anchor `ref` to the actual
config record. Design: techdocs/handoff/deepseek/design_2026_08_26_link_trees.md
(all forks resolved). FORK-1's inline-anchor check is NOT part of linking
anymore — it moved to redraw-select time
(plan_2026_08_28_fork1_move_to_redraw_time.md): a node whose record carries an
inline anchor is perfectly legal at link/Save time; the conflict only matters
when the tree actually redraws a SELECTED node. link_trees only exposes the
inline_anchor_field() helper for that redraw-time consultation.

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
  - NOT checked here: inline anchor on a resolved node record (FORK-1) — see
    the module docstring; the conflict is only consulted at redraw-select time
    via inline_anchor_field() (tree_position.curated_redraw_plan), never at
    link/Save time.
"""
from dataclasses import dataclass

from .anchor_graph import Record, build_records, record_key
from .exceptions import ValidationError
from .i18n import _
from .trees import KINDS, Tree, TreeAnchor, TreeNode

# The sections a node/anchor can auto-search without an explicit kind.
# net_trace IS a valid node kind (2026-09-01, phase D) but is NOT auto-searched:
# a net_trace node's ref is a net name that could collide with another section's
# name, so it requires an explicit kind (resolved via by_key "net_trace:<net>").
_PLACEABLE_KINDS = set(KINDS) - {"external", "net_trace", "module"}

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
    record: Record | None          # None ONLY when node.kind == "external" / "module"
    is_external: bool
    children: list["LinkedNode"]
    # kind=="module": the referenced Tree (resolved by name, never a record).
    module_tree: Tree | None = None


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

    if node.kind == "module":
        # A module node references another TREE (by name), never a config
        # record — the target Tree is resolved by _link_node via the by_tree
        # index (and validated up front by link_trees' module-graph pass).
        return None, False

    if node.kind is not None:
        # Legacy kind "rule" -> canonical "chain:" record key prefix (the
        # 2026-09-01 Rule -> Chain rename; anchor_graph now emits "chain:").
        kind_key = "chain" if node.kind == "rule" else node.kind
        rec = by_key.get(f"{kind_key}:{ref}")
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
    if anchor.is_auto:
        # No explicit anchor at all — the base is AUTO-derived at materialization
        # time from the tree's own root Entity placement's cell zero slot
        # (2026-08-31, plan tree_self_anchor_from_entity); there is no config
        # record to link and it is NOT a live-board external refdes either.
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


def inline_anchor_field(record: Record | None) -> str | None:
    """First inline-anchor field name set on record.obj, or None — the same
    _INLINE_ANCHOR_FIELDS check FORK-1 used to run at link-time. Now consulted
    at redraw-select-time only (curated_redraw_plan), never at link/Save time:
    presence in a tree is not "ownership" — ownership is the act of actually
    redrawing a SELECTED node (plan_2026_08_28_fork1_move_to_redraw_time.md,
    supersedes design_2026_08_28_tree_node_reference_scope.md's is_reference
    approach). Reads from rec.obj via getattr: Record itself does not carry
    anchor_origin (a Point-only field), so a point record with anchor_origin
    would silently pass unless we read the original dataclass."""
    if record is None:
        return None
    for field in _INLINE_ANCHOR_FIELDS:
        if getattr(record.obj, field, None) is not None:
            return field
    return None


def _collect_module_targets(nodes: list[TreeNode], out: list[str] | None = None) -> list[str]:
    """Every module TARGET tree name referenced by kind=="module" nodes in
    `nodes` at any depth (a module node may itself be someone's child) — as a
    LIST preserving duplicates: a within-one-tree duplicate is exactly what
    _module_graph's per-parent guard must catch."""
    out = [] if out is None else out
    for n in nodes:
        if n.kind == "module":
            out.append(n.ref)
        _collect_module_targets(n.children, out)
    return out


def _module_graph(trees: list[Tree]) -> dict[str, set[str]]:
    """tree name -> the set of module TARGET tree names referenced anywhere in
    that tree (any depth). Validates as config-level fatals:
      - a module ref that names no existing tree (0 matches);
      - a tree embedding ITSELF (module ref == its own name);
      - two module nodes INSIDE ONE tree referencing the same tree — a parent
        may embed a child tree only once (multiple parents ARE allowed, that is
        checked across trees, not within one)."""
    names = {t.name for t in trees}
    graph: dict[str, set[str]] = {t.name: set() for t in trees}
    for tree in trees:
        for target in _collect_module_targets(tree.nodes):
            if target == tree.name:
                _fatal(_("tree {name!r} has a module node referencing itself — "
                         "a tree cannot embed itself").format(name=tree.name))
            if target not in names:
                _fatal(_("tree {name!r}: module node references unknown tree "
                         "{ref!r} (no tree with that name exists)")
                       .format(name=tree.name, ref=target))
            if target in graph[tree.name]:
                _fatal(_("tree {name!r} embeds tree {ref!r} more than once — a "
                         "parent may embed a tree by module only once")
                       .format(name=tree.name, ref=target))
            graph[tree.name].add(target)
    return graph


def _check_module_cycles(graph: dict[str, set[str]]) -> None:
    """DFS over the module graph; any cycle (A embeds B, ... , B embeds A) is a
    config-level fatal naming the offending chain. Self-embedding is rejected
    earlier in _module_graph, so only length>=2 cycles reach here."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in graph}

    def visit(start: str, path: list[str]) -> None:
        color[start] = GRAY
        path.append(start)
        for nxt in sorted(graph.get(start, ())):
            if color[nxt] == GRAY:
                idx = path.index(nxt)
                chain = path[idx:] + [nxt]
                _fatal(_("tree module cycle: {chain}").format(chain=" > ".join(chain)))
            if color[nxt] == WHITE:
                visit(nxt, path)
        path.pop()
        color[start] = BLACK

    for name in sorted(graph):
        if color[name] == WHITE:
            visit(name, [])


def _link_node(node: TreeNode, by_key: dict[str, Record],
               by_name: dict[str, list[Record]],
               by_tree: dict[str, Tree]) -> LinkedNode:
    """Recursively wrap one TreeNode (and its children) into a LinkedNode. A
    module node resolves its ref as another TREE (module_tree), never a record;
    its own children (ordinary marker children) link normally."""
    if node.kind == "module":
        module_tree = by_tree.get(node.ref)
        if module_tree is None:
            # Unreachable when link_trees validates the module graph first
            # (existence is a config fatal there) — defensive for a caller that
            # links a SUBSET missing the referenced tree: fail loudly, never
            # silently drop the embed.
            _fatal(_("Module {ref!r} references a tree that is not in this "
                     "set of trees — link the whole config").format(ref=node.ref))
        return LinkedNode(
            node=node,
            record=None,
            is_external=False,
            children=[_link_node(c, by_key, by_name, by_tree) for c in node.children],
            module_tree=module_tree,
        )
    record, is_external = _resolve_node_ref(node, by_key, by_name)
    return LinkedNode(
        node=node,
        record=record,
        is_external=is_external,
        children=[_link_node(c, by_key, by_name, by_tree) for c in node.children],
        module_tree=None,
    )


def _link_tree(tree: Tree, by_key: dict[str, Record],
               by_name: dict[str, list[Record]],
               by_tree: dict[str, Tree]) -> LinkedTree:
    record, is_external = _resolve_anchor_ref(tree.anchor, by_name)
    return LinkedTree(
        name=tree.name,
        anchor=LinkedAnchor(
            anchor=tree.anchor,
            record=record,
            is_origin=tree.anchor.is_origin,
            is_external=is_external,
        ),
        nodes=[_link_node(n, by_key, by_name, by_tree) for n in tree.nodes],
    )


def link_trees(cfg, trees: list[Tree]) -> list[LinkedTree]:
    """Resolve every tree node/anchor ref against `cfg` (a loaded Config).

    Indexes are built ONCE here (before any tree loop): by_key for explicit-
    kind lookup (with collision detection), by_name for auto-search over the
    4 placeable sections, by_tree for module refs (a module node references
    another Tree BY NAME — plan 2026-09-02 tree_module_embedding). Module
    config errors are validated here (unknown target, self-embed, a duplicate
    embed within ONE parent, and module cycles A⊃B⊃A). retired: true records
    are already dropped by build_records(), so a node/anchor referring to one
    gets "not found" (fatal for nodes, silent-external for anchors) — deliberate
    behavior."""
    records = build_records(cfg)
    by_key = _build_by_key_index(records)
    by_name = _build_by_name_index(records)
    by_tree = {t.name: t for t in trees}
    if trees:
        _check_module_cycles(_module_graph(trees))
    return [_link_tree(t, by_key, by_name, by_tree) for t in trees]
