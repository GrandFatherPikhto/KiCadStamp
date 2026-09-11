# kicadstamp/trees.py
"""Pure syntactic loader for the optional s-expr "trees" layer.

Loads `*.trees` files (see techdocs/handoff/deepseek/design_2026_08_26_
sexp_trees_grammar.md) into plain dataclasses. This module knows NOTHING
about YAML config, Cells, ClonePlacements or any record kind — linking tree
`ref`s to real Config records is `kicadstamp/link_trees.py` (Phase 3 of the
implementation handoff), NOT here.

Grammar (v1), one `node` type, tree-level anchor:
    (kicadstamp-trees
      (version 1)
      (tree
        (name "power_tree")
        (anchor (ref "CONN_PM5V"))     ; or (anchor (origin))
        (node (ref "AMS1117_REG") (xy 5.0 2.0) (node (ref "C_OUT") (xy 1.0 0)))
        (node (ref "R_AROUND") (polar 3.0 45.0))))

Syntactic rules enforced here (fatal via ValidationError):
  1. tree (name ...) values are unique within one file
  2. a flat record (ref ...) may appear in AT MOST ONE node across the whole
     file (a record's position source is exactly one); the same ref MAY be
     reused as a tree `anchor` (an anchor is a base, not something the tree
     "places"); kind "module" and kind "mount" refs are NAMES, not records, so
     they are exempt from this file-wide rule — mount refs must still be unique
     WITHIN their tree and must not collide with a positioned node's ref there
     (see _validate_mount_refs)
  3. xy / polar are mutually exclusive, each exactly 2 numbers
  4. kind, if present, is one of clone/placement/chain/coordinate/net_trace/
     point/external/module/mount (see KINDS)
  5. cycles are impossible by construction (nested s-expr structure)
  6. a nested (anchor (role ...)) is valid ONLY on a kind "mount" node; on any
     other kind it is the removed own_anchor grammar and is a load-time fatal
     pointing at the tree converter
"""
from dataclasses import dataclass, field

from .cloner.sexp import atom, child, children, is_node, load_file, save_file, sval, sym
from .exceptions import ValidationError
from .i18n import _

# Valid node kinds (syntactic whitelist; cross-referencing against Config
# records is link_trees' job).
# "placement" — the Entity/Placement split's kind for Entity-nodes (2026-08-30,
# design_2026_08_30_entity_placement_grammar.md §2.2). "clone" is KEPT alongside
# during the migration so legacy clone_placement-referencing trees keep working;
# the release cutover (Phase 6 converter) rewrites "clone" -> "placement".
# "net_trace" — 2026-09-01 rework, phase D: inter-cluster copper becomes a tree
# node (ref = the net name, resolved to a net_traces: record by link_trees via
# by_key "net_trace:<net>"). Deliberately NOT auto-searched (requires an explicit
# kind) — its ref is a net name that could collide with another section's name;
# see link_trees._PLACEABLE_KINDS.
# "module" — 2026-09-02 (plan tree_module_embedding): the node lives in the PARENT
# tree; its ref is another Tree's NAME (not a record). It does not place a record —
# at redraw it temporarily substitutes the referenced tree's base (pivot mechanism,
# pivot_xy/pivot_polar fields). Deliberately NOT auto-searched (like net_trace):
# see link_trees._PLACEABLE_KINDS.
# "mount" — 2026-09-11 (plan_2026_09_11_tree_mount_nodes, task Y.1): a POINT OF
# REFERENCE node. It carries the live (role ...) anchor the removed per-node
# own_anchor used to carry and places NOTHING itself; its children are laid from
# that anchor's live frame (the one base rule: a node's base is its PARENT, no
# exceptions). Its ref is a NAME unique within the tree (like module), never a
# config record — deliberately NOT auto-searched: see link_trees._PLACEABLE_KINDS.
KINDS = ("clone", "placement", "chain", "coordinate", "net_trace", "point",
         "external", "module", "mount")

# Legacy kind alias for the 2026-09-01 Rule -> Chain rename: tree nodes written
# with kind "rule" (the old record kind) are still accepted at parse time (a
# profile saved before the rename may carry them); link_trees maps them to the
# canonical "chain:" record key prefix (see link_trees._resolve_node_ref).
LEGACY_KINDS = ("rule",)

_OFFSET_KEYS = ("xy", "polar")


@dataclass
class TreeAnchor:
    """A tree's position base — EXACTLY ONE of:
      - is_origin=True  -> (anchor (origin)): the absolute board origin (0,0)
      - ref set         -> (anchor (ref "...")): a config record name (an
                           Entity for kind "placement") or a live refdes
      - role set        -> (anchor (role "...") [(sheet ...) (cluster ...)
                           (pad ...)]): anchor by the Role custom field on a
                           live component (subsumes ClonePlacement's role-based
                           anchor); sheet/cluster narrow ambiguity, pad moves
                           the anchor point to a specific pad
      - point set       -> (anchor (point "...")): a points: entry name
      - is_self=True    -> (anchor (self [(ref "...") (pad "...")])): the tree
                           hangs on a component the tree ITSELF places; the
                           base is read LIVE from that component (plan
                           2026-09-11 tree_self_anchor, task Д — replaces the
                           implicit is_auto flag). Optional ref names a
                           kind "placement" node OF THIS TREE (unset = the
                           single top-level placement node, today's rule);
                           optional pad moves the base onto that pad.
    is_external=True marks a ref anchor as live-board-only (never resolved
    against config) — symmetric to kind="external" on TreeNode (the anchor's
    own collision shield).

    An ABSENT (anchor ...) is read as (self) FOREVER (plan Д.2 rule 1): the
    tree's anchor is derived at materialization time from its own root Entity
    placement's cell mount point, live-resolved like a (role ...) anchor.
    Explicit anchors ALWAYS win. No migration and no converter is involved.

    shift_xy (2026-09-11, plan_2026_09_11_external_point_materialization §X.2):
    the anchor's OWN ``(shift x y)`` — an offset in LOCAL millimetres of the
    anchor's base frame, rotated by the anchor's angle (rotate_local_offset),
    NOT board-absolute. Combines with ANY base mode: a (point ...) is resolved
    by its own rules FIRST (including the point's OWN absolute shift), then
    this shift is added on top. Deliberately DIFFERENT from Point.shift_x_mm/
    shift_y_mm, which stay board-absolute (design §3.8).
    """
    ref: str | None = None        # None unless a ref anchor
    is_origin: bool = False
    is_external: bool = False   # only meaningful for a ref anchor
    role: str | None = None
    anchor_sheet: str | None = None
    anchor_cluster: str | None = None
    anchor_pad: str | None = None
    point: str | None = None
    is_self: bool = False         # (anchor (self ...)) — was the is_auto flag
    self_ref: str | None = None   # optional node ref OF THIS TREE (placement)
    self_pad: str | None = None   # optional pad of that node's component
    shift_xy: tuple[float, float] | None = None

    @property
    def mode(self) -> str:
        """THE single predicate answering "what kind of anchor is this" — one
        of "origin" / "record" / "external" / "self" / "role" / "point".
        Centralised so no caller has to re-derive the kind by hand (plan Д.3).
        An ABSENT anchor (all base fields unset) IS the self mode."""
        if self.is_origin:
            return "origin"
        if self.is_self:
            return "self"
        if self.role is not None:
            return "role"
        if self.point is not None:
            return "point"
        if self.ref is not None:
            return "external" if self.is_external else "record"
        return "self"


@dataclass
class TreeNode:
    ref: str
    kind: str | None       # "clone"/"chain"/"coordinate"/"point"/"external"/"module"/"mount", or None (auto)
    xy: tuple[float, float] | None
    polar: tuple[float, float] | None   # (radius_mm, angle_deg)
    rotation: float
    name: str | None       # display label, default = ref
    group: str | None      # pure UI tag, does not participate in geometry
    children: list["TreeNode"] = field(default_factory=list)
    # kind "mount" ONLY (2026-09-11, plan_2026_09_11_tree_mount_nodes §Y.1): the
    # node's own live (role ...) anchor — the point this node (and therefore its
    # whole subtree) hangs from. Only the role-anchor shape is meaningful
    # (origin/ref/point/self are tree-anchor-only and load-time fatals, see
    # _parse_mount_anchor/_dict_mount_anchor). A nested (anchor ...) on ANY other
    # kind is a load-time fatal pointing at the tree converter — that was the
    # removed TreeNode.own_anchor grammar (2026-09-03 .. 2026-09-11). None
    # (default) = an ordinary node, measured from its parent.
    #
    # NOTE (2026-09-11, plan_2026_09_11_tree_inner_point_and_rotation §V.3): the
    # pivot_xy/pivot_polar/pivot_ref fields that used to live HERE moved to the
    # TREE (see Tree below) — the inner point is a property of the TREE, not of
    # one embedding of it (design Р3: one inner point per tree, no per-node
    # override). A node carrying them is now a load-time fatal pointing at the
    # converter.
    anchor: TreeAnchor | None = None


@dataclass
class Tree:
    name: str
    anchor: TreeAnchor
    nodes: list[TreeNode]  # top-level nodes
    # The tree's INNER point (the "handle"): the ONE point of THIS tree that
    # must land on the tree's OUTER anchor, and the centre the tree's own
    # `rotation` turns around (2026-09-11, plan_2026_09_11_tree_inner_point_and_
    # rotation §V.1). EXACTLY one of the three forms, or none = (0,0) = the
    # tree's own origin. Moved here from the module NODE (design Р3): described
    # once per tree, so embedding one tree in three places cannot describe its
    # handle three ways.
    #   pivot_ref: the `ref` of a node OF THIS TREE, resolved by laying the
    #              tree out from a bare (0,0)/0 base — pure geometry, no live
    #              board (unless the tree has mount nodes, whose bases are
    #              live by nature). NOT a mount node itself, and NOT any node
    #              hanging under one at any depth: such a node is pinned to a
    #              live component and does not follow the tree, so it cannot
    #              be a handle (validated at load — _validate_tree_pivot_ref).
    #   pivot_xy / pivot_polar: a raw coordinate in the tree's OWN frame.
    pivot_xy: tuple[float, float] | None = None
    pivot_polar: tuple[float, float] | None = None   # (radius_mm, angle_deg)
    pivot_ref: str | None = None
    # The tree's OWN angle (plan §V.2): a DОВОРОТ (increment) added ON TOP of
    # the outer anchor's angle, NOT a replacement — a role anchor still supplies
    # the channel rotation, and this turns the tree a bit further AROUND ITS
    # INNER POINT. Default 0.0 keeps every pre-existing tree bit-identical.
    rotation: float = 0.0


def _fatal(message: str) -> None:
    raise ValidationError(message)


def _parse_kind(node) -> str | None:
    """node's (kind ...) value, validated against the whitelist. None when
    the node carries no kind (auto-resolve by name in link_trees)."""
    raw = atom(node, "kind")
    if raw is None:
        return None
    if raw not in KINDS and raw not in LEGACY_KINDS:
        _fatal(_("node {ref!r}: invalid kind {kind!r} — expected one of {kinds}")
               .format(ref=atom(node, "ref"), kind=raw, kinds=", ".join(KINDS)))
    return raw


def _parse_offset(node, key: str) -> tuple[float, float] | None:
    """Node's (key x y) as a pair of floats, or None if the key is absent.
    Enforces "exactly 2 numbers" — both values must be numeric (a Symbol,
    e.g. an unquoted name, is not a number)."""
    c = child(node, key)
    if c is None:
        return None
    if len(c) != 3 or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                              for v in c[1:]):
        _fatal(_("node {ref!r}: {key} must be exactly two numbers")
               .format(ref=atom(node, "ref"), key=key))
    return float(c[1]), float(c[2])


def _parse_rotation(node) -> float:
    """Node's (rotation ...) as a float, default 0.0. A non-numeric value is
    fatal (rotation is a number, not a Symbol)."""
    raw = atom(node, "rotation")
    if raw is None:
        return 0.0
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        _fatal(_("node {ref!r}: rotation must be a number")
               .format(ref=atom(node, "ref")))
    return float(raw)


def _opt_sval(value) -> str | None:
    """sval() that tolerates None — for optional quoted-string anchor fields
    (sheet/cluster/pad)."""
    return sval(value) if value is not None else None


def _parse_anchor_shift(anchor_node) -> tuple[float, float] | None:
    """An (anchor ...)'s optional (shift x y) child — the anchor's OWN offset
    in LOCAL mm of its base frame (plan_2026_09_11_external_point_materialization
    §X.2), or None when absent. Exactly two numbers, or a fatal (same "exactly
    two numbers" discipline as a node's xy)."""
    c = child(anchor_node, "shift")
    if c is None:
        return None
    if len(c) != 3 or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                              for v in c[1:]):
        _fatal(_("anchor: shift must be exactly two numbers"))
    return float(c[1]), float(c[2])


def _parse_anchor(anchor_node) -> TreeAnchor:
    """(anchor (origin)) -> origin anchor;
    (anchor (ref "...") [(external)]) -> ref anchor (external = live-board-only
    refdes, NEVER resolved against config);
    (anchor (role "...") [(sheet ...) (cluster ...) (pad ...)]) -> role-based
    anchor by the Role custom field on a live component;
    (anchor (point "...")) -> points: entry anchor;
    (anchor (self [(ref "...") (pad "...")])) -> SELF anchor (2026-09-11, plan
    tree_self_anchor, task Д): the tree hangs on a component it places itself,
    read LIVE. The optional (ref ...) names a kind "placement" node OF THIS
    TREE — deliberately NOT the tree-level (ref "...") record anchor it nests
    beside; the optional (pad ...) moves the base onto that specific pad. Both
    are validated against the tree's own nodes later (_validate_self_ref, which
    needs the parsed node list).
    Exactly one base kind is required (fatal otherwise). ref/role/point are NOT
    validated for uniqueness against nodes — an anchor is a base, not something
    the tree places (rule 2).
    An optional (shift x y) child is the anchor's own LOCAL-mm offset (§X.2),
    orthogonal to the base kind (combines with any of them)."""
    shift_xy = _parse_anchor_shift(anchor_node)
    is_origin = child(anchor_node, "origin") is not None
    is_external = child(anchor_node, "external") is not None
    self_node = child(anchor_node, "self")
    ref = atom(anchor_node, "ref")
    role = atom(anchor_node, "role")
    point = atom(anchor_node, "point")
    mode_count = sum(1 for m in (is_origin, ref is not None,
                                 role is not None, point is not None,
                                 self_node is not None) if m)
    if mode_count != 1:
        _fatal(_("anchor must specify exactly one of (origin), (ref \"...\"), "
                 "(role \"...\"), (point \"...\"), (self ...)"))
    if is_origin:
        if is_external:
            _fatal(_("anchor: (origin) and (external) are mutually exclusive"))
        return TreeAnchor(ref=None, is_origin=True, is_external=False,
                          shift_xy=shift_xy)
    # (external) is a REF-anchor modifier only: a role/point/self anchor is
    # never a config record, so "external" on it would be silently meaningless.
    # Checked HERE (before the ref/point/role/self branches) so a (point ...)
    # (external) or (role ...) (external) combination is a hard fatal on BOTH
    # paths, never a silent drop.
    if is_external and ref is None:
        _fatal(_("anchor: (external) is only valid with a (ref \"...\") anchor"))
    if ref is not None:
        return TreeAnchor(ref=sval(ref), is_origin=False, is_external=is_external,
                          shift_xy=shift_xy)
    if point is not None:
        return TreeAnchor(point=sval(point), is_origin=False, shift_xy=shift_xy)
    if self_node is not None:
        # (self (ref "...") (pad "...")) — ref/pad NEST inside the (self ...)
        # node; the top-level (ref "...") above is the record anchor.
        return TreeAnchor(
            is_self=True,
            self_ref=atom(self_node, "ref"),
            self_pad=atom(self_node, "pad"),
            shift_xy=shift_xy,
        )
    return TreeAnchor(
        role=sval(role),
        is_origin=False,
        anchor_sheet=_opt_sval(atom(anchor_node, "sheet")),
        anchor_cluster=_opt_sval(atom(anchor_node, "cluster")),
        anchor_pad=_opt_sval(atom(anchor_node, "pad")),
        shift_xy=shift_xy,
    )


def _parse_mount_anchor(ref: str, anchor_node) -> TreeAnchor:
    """A kind "mount" node's nested (anchor ...) child. Only the ROLE shape is
    meaningful (plan §Y.1.2): the mount node's children are measured from this
    anchor's LIVE role position instead of the parent. (origin)/(ref ...)/
    (point ...)/(external)/(self ...) are tree-anchor-only concepts — hard
    fatal, mirroring the tree-level _parse_anchor discipline; sheet/cluster
    narrow an ambiguous Role, pad moves the base onto a specific pad (all
    optional). A (shift x y) is likewise tree-anchor-only for now (the shift
    belongs to the tree's OUTER point, plan §X.2): fatal rather than silently
    dropped."""
    if (child(anchor_node, "origin") is not None
            or atom(anchor_node, "ref") is not None
            or atom(anchor_node, "point") is not None
            or child(anchor_node, "external") is not None
            or child(anchor_node, "self") is not None
            or child(anchor_node, "shift") is not None):
        _fatal(_("mount node {ref!r}: anchor supports only (role ...) — "
                 "origin/ref/point/external/self/shift are tree-anchor-only")
               .format(ref=ref))
    role = atom(anchor_node, "role")
    if not role:
        _fatal(_("mount node {ref!r}: anchor needs a (role ...)").format(ref=ref))
    return TreeAnchor(
        role=sval(role),
        is_origin=False,
        anchor_sheet=_opt_sval(atom(anchor_node, "sheet")),
        anchor_cluster=_opt_sval(atom(anchor_node, "cluster")),
        anchor_pad=_opt_sval(atom(anchor_node, "pad")),
    )


def _walk_nodes(nodes: list[TreeNode]):
    """Every node of a tree, depth-first (the mount-ref uniqueness helper)."""
    for n in nodes:
        yield n
        yield from _walk_nodes(n.children)


def _validate_mount_refs(nodes: list[TreeNode], tree_name: str) -> None:
    """Per-tree ref uniqueness for kind "mount" (plan §Y.1.3). A mount node's
    ref is a local NAME: it must be unique among the tree's mount nodes AND must
    not collide with any positioned node's ref of the same tree, or the design's
    later stages (the tree's own inner point) could not say which node is meant.
    Both checks are load-time fatals (reported separately — they mean different
    things)."""
    counts: dict[str, int] = {}
    mount_refs: set[str] = set()
    placed_refs: set[str] = set()
    for n in _walk_nodes(nodes):
        if n.kind == "mount":
            counts[n.ref] = counts.get(n.ref, 0) + 1
            mount_refs.add(n.ref)
        else:
            placed_refs.add(n.ref)
    duplicates = sorted(ref for ref, count in counts.items() if count > 1)
    if duplicates:
        _fatal(_("tree {tree!r}: mount node ref(s) {refs} are not unique — a "
                 "mount node's ref must identify exactly one node in the tree")
               .format(tree=tree_name, refs=", ".join(duplicates)))
    collisions = sorted(mount_refs & placed_refs)
    if collisions:
        _fatal(_("tree {tree!r}: mount node ref(s) {refs} collide with a "
                 "positioned node of the same tree — mount refs must be "
                 "distinct so a node can be named unambiguously")
               .format(tree=tree_name, refs=", ".join(collisions)))


_PLACEMENT_KINDS = ("placement", "clone")


def _validate_self_ref(tree_name: str, nodes: list[TreeNode],
                       anchor: TreeAnchor) -> None:
    """A self anchor's optional (ref ...) must name a kind "placement" node OF
    THIS TREE (plan 2026-09-11 tree_self_anchor, task Д.4). A NAMED node makes
    the anchor unambiguous, so the EXACTLY-ONE top-level rule is dropped for it
    (that rule stays only for a bare (self) — see the resolvers). Both
    violations are load-time fatals, mirroring _validate_tree_pivot_ref."""
    if anchor is None or not anchor.is_self or anchor.self_ref is None:
        return
    by_ref = {n.ref: n for n in _walk_nodes(nodes)}
    target = by_ref.get(anchor.self_ref)
    if target is None:
        _fatal(_("tree {name!r}: self anchor ref {ref!r} names no node of this "
                 "tree").format(name=tree_name, ref=anchor.self_ref))
    if target.kind not in _PLACEMENT_KINDS:
        _fatal(_("tree {name!r}: self anchor ref {ref!r} is a kind {kind!r} node "
                 "— a self anchor must name a kind \"placement\" node of this "
                 "tree").format(name=tree_name, ref=anchor.self_ref,
                                kind=target.kind))


def _find_entity(cfg, name: str):
    """cfg.entities record by name, or None (duck-typed: trees.py must not
    depend on the config package — the loader calls the guard below after
    loading, with a fully built Config)."""
    for entity in getattr(cfg, "entities", []) or []:
        if entity.name == name:
            return entity
    return None


def _find_tree(cfg, name: str):
    for tree in getattr(cfg, "trees", []) or []:
        if tree.name == name:
            return tree
    return None


@dataclass
class RolePlacementMatch:
    """ONE node of a tree that places a cell carrying a queried role — the
    return element of `find_role_placement_matches`, shared by the mount drift
    guard and the internal-mount resolver (plan_2026_09_11_internal_mount §Г.2)
    so the two can never disagree about WHERE a role lives.

    tree   — the tree that DIRECTLY contains `node` (== the queried tree, unless
             the role comes from a tree embedded through a module node);
    node   — the kind "placement"/"clone" node placing the cell;
    path   — the node chain from `tree`'s top level down to `node` (inclusive);
    entity — the cfg.entities record `node.ref` resolves to;
    cell   — cfg.cells[entity.cell];
    slot   — the TemplateComponentSlot carrying the queried role."""
    tree: "Tree"
    node: TreeNode
    path: list[TreeNode]
    entity: object
    cell: object
    slot: object


def find_role_placement_matches(cfg, tree: "Tree", role: str, *,
                                sheet: str | None = None,
                                cluster: str | None = None,
                                _seen: frozenset = frozenset()
                                ) -> list[RolePlacementMatch]:
    """Every node of `tree` (module content included, recursively) that places a
    cell carrying `role`, optionally narrowed to the placing Entity's own
    `sheet`/`cluster` (None = any). THE single predicate behind both the mount
    drift guard and the internal-mount resolver (plan_2026_09_11_internal_mount
    §Г.2): the guard asks "does this role live inside the tree?", the resolver
    asks "which node poses the frame the role's pad is measured in?".

    Module content is searched the way the removed `_tree_placed_roles` did (a
    role placed by an embedded tree counts) — a match whose `.tree` is NOT the
    queried `tree` is that embedded case. Multiple matches are legal HERE; the
    caller decides (guard and resolver both treat 2+ as an ambiguity fatal)."""
    out: list[RolePlacementMatch] = []
    cells = getattr(cfg, "cells", {}) or {}

    def walk(nodes: list[TreeNode], path: list[TreeNode]) -> None:
        for n in nodes:
            node_path = path + [n]
            if n.kind == "module":
                if n.ref not in _seen:
                    nested = _find_tree(cfg, n.ref)
                    if nested is not None:
                        out.extend(find_role_placement_matches(
                            cfg, nested, role, sheet=sheet, cluster=cluster,
                            _seen=_seen | {tree.name}))
                # A module node's OWN children are ordinary nodes of THIS tree.
                walk(n.children, node_path)
                continue
            if n.kind in ("placement", "clone"):
                entity = _find_entity(cfg, n.ref)
                if entity is None:
                    continue
                cell = cells.get(entity.cell)
                if cell is None:
                    continue
                if sheet is not None and sheet != getattr(entity, "sheet", None):
                    continue
                if cluster is not None and cluster != getattr(entity, "cluster", None):
                    continue
                for slot in cell.components:
                    if slot.role == role:
                        out.append(RolePlacementMatch(
                            tree=tree, node=n, path=node_path, entity=entity,
                            cell=cell, slot=slot))
            walk(n.children, node_path)

    walk(tree.nodes, [])
    return out


def _is_under(target: TreeNode, ancestor: TreeNode, nodes: list[TreeNode]) -> bool:
    """True when `target` IS `ancestor` or hangs anywhere under it (identity
    comparison; TreeNode is an unhashable dataclass, so id() is used). The
    mount-drift guard's static cycle test: a mount node whose role is placed by
    a node inside its own subtree would depend on its own base."""
    parent_of: dict[int, TreeNode] = {}
    stack = list(nodes)
    while stack:
        node = stack.pop()
        for child in node.children:
            parent_of[id(child)] = node
            stack.append(child)
    current: TreeNode | None = target
    while current is not None:
        if current is ancestor:
            return True
        current = parent_of.get(id(current))
    return False


def _candidate_label(match: RolePlacementMatch) -> str:
    return f"{match.tree.name}/{match.node.ref}"


def resolve_internal_mount_match(cfg, tree: "Tree", node: TreeNode
                                 ) -> RolePlacementMatch | None:
    """The ONE place a mount node's anchor is classified (plan_2026_09_11_
    internal_mount §Г.2/§Г.4), shared by the load-time guard and the resolver so
    they can never disagree:

      - None — the role is OUTSIDE this tree: the LIVE method applies, always
        legal (the component really does live outside the tree);
      - a single RolePlacementMatch — the role belongs to a cell THIS tree
        places: the INTERNAL method computes the base from the tree's own
        layout, so it can never depend on a previous Apply.

    Raises the genuinely unresolvable cases — the SAME fatals the guard used to
    raise, redirected:

      * ambiguity — 2+ nodes place cells carrying the role and the anchor's
        (sheet ...)/(cluster ...) do not narrow it to one;
      * module crossing — the only internal match sits in a tree EMBEDDED
        through a module node: the resolver poses the placing node along its
        node path within THIS tree, so that pose has no defined value yet;
      * a statically visible cycle — the placing node sits under the mount node
        itself, so the base would depend on its own result."""
    anchor = node.anchor
    if anchor is None:
        return None
    matches = find_role_placement_matches(
        cfg, tree, anchor.role, sheet=anchor.anchor_sheet,
        cluster=anchor.anchor_cluster)
    if not matches:
        return None
    if len(matches) > 1:
        _fatal(_(
            "tree {tree!r}: mount node {ref!r} is anchored to role "
            "{role!r}, which this tree places in more than one cell "
            "({candidates}) — narrow the anchor with (sheet ...)/"
            "(cluster ...), or anchor to a role from outside the tree")
            .format(tree=tree.name, ref=node.ref, role=anchor.role,
                    candidates=", ".join(_candidate_label(m) for m in matches)))
    match = matches[0]
    if match.tree is not tree:
        _fatal(_(
            "tree {tree!r}: mount node {ref!r} is anchored to role "
            "{role!r} placed by an EMBEDDED tree {child!r} — an internal "
            "mount across a module embedding is not supported yet; anchor "
            "it to a role of a cell this tree places directly, or to a "
            "role from outside the tree")
            .format(tree=tree.name, ref=node.ref, role=anchor.role,
                    child=match.tree.name))
    if _is_under(match.node, node, tree.nodes):
        _fatal(_(
            "tree {tree!r}: mount node {ref!r} is anchored to role "
            "{role!r} placed by node {node_ref!r}, which sits under this "
            "very mount node — the base would depend on itself (cycle); "
            "move the mount node above the placing node, or anchor to a "
            "role from outside the tree")
            .format(tree=tree.name, ref=node.ref, role=anchor.role,
                    node_ref=match.node.ref))
    return match


def check_mount_anchor_drift(cfg) -> None:
    """Load-time guard for mount-node anchors (plan_2026_09_11_internal_mount
    §Г.4; design 2026-09-11 §3.11 / §7.2 — REDIRECTED 2026-09-11).

    A mount node anchored to a role of a cell THIS tree places is NO LONGER a
    fatal: that shape is the legitimate INTERNAL method (§Г.1) — the base is
    computed from the tree's OWN layout, not read back from the board, so it
    cannot drift. Every mount node whose anchor is internal is validated
    (ambiguity / module crossing / cycle) by `resolve_internal_mount_match`; a
    role from OUTSIDE the tree stays the LIVE method and is always legal.

    Deliberately NOT applied to the tree's OWN (role ...) anchor (the empirical
    `dac_buf_tpl` exception documented before 2026-09-11 — that component is the
    tree's REFERENCE, not something the tree moves). Pure config check, no board
    needed."""
    for tree in getattr(cfg, "trees", []) or []:
        for node in _walk_nodes(tree.nodes):
            if node.kind == "mount":
                resolve_internal_mount_match(cfg, tree, node)


def _parse_node(node, seen_refs: set[str], location: str) -> TreeNode:
    """Parse one (node ...) subtree, recursing into nested (node ...)
    children. seen_refs enforces rule 2 (a ref appears in at most one node
    across the whole file); location is the s-expr path for error messages."""
    ref = atom(node, "ref")
    if ref is None:
        _fatal(_("{location}: node is missing a (ref ...)").format(location=location))
    ref = sval(ref)

    # kind read BEFORE the seen_refs check: a module node's ref is another
    # TREE's name and a mount node's ref is a local NAME — neither is a record,
    # so rule 2 (a record ref appears in at most one node of the file) does not
    # apply to them (the same tree may be embedded by several different
    # parents; per-parent duplicates are guarded in link_trees; mount ref
    # uniqueness is per-TREE, see _validate_mount_refs).
    kind = _parse_kind(node)
    if kind not in ("module", "mount"):
        if ref in seen_refs:
            _fatal(_("{location}: record {ref!r} already has a node elsewhere in this "
                     "file — a record's position source must be exactly one")
                   .format(location=location, ref=ref))
        seen_refs.add(ref)

    # A nested (anchor ...) belongs to a kind "mount" node ONLY (2026-09-11,
    # plan Y.1). On any other kind it IS the removed own_anchor grammar: fatal
    # with a pointer to the converter, never an AttributeError (plan Y.9.1.7).
    anchor_node = child(node, "anchor")
    if kind == "mount":
        if anchor_node is None:
            _fatal(_("mount node {ref!r}: needs a (anchor (role ...)) — a mount "
                     "node is a point of reference and places nothing itself")
                   .format(ref=ref))
        node_anchor = _parse_mount_anchor(ref, anchor_node)
    else:
        if anchor_node is not None:
            _fatal(_("node {ref!r}: a nested (anchor ...) is only valid on a "
                     "(kind mount) node — the old own_anchor grammar was removed "
                     "2026-09-11; run the tree converter (kicadstamp "
                     "convert-trees) on this config").format(ref=ref))
        node_anchor = None

    xy = _parse_offset(node, "xy")
    polar = _parse_offset(node, "polar")
    if xy is not None and polar is not None:
        _fatal(_("node {ref!r}: xy and polar are mutually exclusive "
                 "(use exactly one)").format(ref=ref))

    # pivot-* used to live HERE, on a module node. It moved to the TREE level
    # (2026-09-11, plan_2026_09_11_tree_inner_point_and_rotation §V.3): the inner
    # point is one per TREE, never per embedding (design Р3). A config still
    # carrying it on a node is the old grammar — fatal with a pointer to the
    # converter, the SAME discipline the removed own_anchor gets, never a silent
    # drop.
    for leftover in ("pivot-xy", "pivot-polar", "pivot-ref"):
        if child(node, leftover) is not None:
            _fatal(_("node {ref!r}: {key} is no longer valid on a node — the "
                     "tree's inner point moved to the (tree ...) level; run the "
                     "tree converter (kicadstamp convert-trees) on this config")
                   .format(ref=ref, key=leftover))

    child_nodes = children(node, "node")
    parsed_children = [
        _parse_node(c, seen_refs, f"{location}.node") for c in child_nodes
    ]

    raw_name = atom(node, "name")
    raw_group = atom(node, "group")
    return TreeNode(
        ref=ref,
        kind=kind,
        xy=xy,
        polar=polar,
        rotation=_parse_rotation(node),
        name=sval(raw_name) if raw_name is not None else None,
        group=sval(raw_group) if raw_group is not None else None,
        children=parsed_children,
        anchor=node_anchor,
    )


def _parse_tree_offset(tree_node, key: str, tree_name: str
                       ) -> tuple[float, float] | None:
    """A TREE-level (key x y) offset as a pair of floats, or None. Mirror of
    _parse_offset with a tree-shaped error message (the node-level helper
    formats "node {ref!r}", which would read "node None" here)."""
    c = child(tree_node, key)
    if c is None:
        return None
    if len(c) != 3 or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                              for v in c[1:]):
        _fatal(_("tree {name!r}: {key} must be exactly two numbers")
               .format(name=tree_name, key=key))
    return float(c[1]), float(c[2])


def _parse_tree_rotation(tree_node, tree_name: str) -> float:
    """A TREE-level (rotation ...) as a float, default 0.0 (plan §V.2.1)."""
    raw = atom(tree_node, "rotation")
    if raw is None:
        return 0.0
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        _fatal(_("tree {name!r}: rotation must be a number").format(name=tree_name))
    return float(raw)


def _mount_ancestor_of(target: TreeNode, nodes: list[TreeNode]) -> TreeNode | None:
    """The nearest kind "mount" ancestor of `target` within `nodes`, or None.

    Built from the tree's TOP-LEVEL list by an explicit parent map. TreeNode is
    a plain (unhashable) dataclass, so relationships are tracked by id(). Walks
    the WHOLE parent chain, so a mount node at ANY depth counts, not just a
    direct parent (plan_2026_09_11_pivot_ref_mount_ancestor §P.1.1/§P.1.3)."""
    parent_of: dict[int, TreeNode] = {}
    stack = list(nodes)
    while stack:
        node = stack.pop()
        for child in node.children:
            parent_of[id(child)] = node
            stack.append(child)
    current = parent_of.get(id(target))
    while current is not None:
        if current.kind == "mount":
            return current
        current = parent_of.get(id(current))
    return None


def _pivot_ref_rejection(target: TreeNode, nodes: list[TreeNode]) -> str | None:
    """Why `target` may NOT serve as a tree's pivot-ref, or None when it may.

    THE single source of truth for pivot-ref eligibility, shared by the
    load-time validator below AND the GUI picker (tree_pivot_ref_candidates):
    both must agree on exactly the same set, or the form would offer a name the
    grammar refuses (or hide one it accepts). Three barred shapes:

    * "external" — a bare live refdes with no config record, so hanging the
      handle on it would cost the WHOLE tree its portability (plan §V.1.3);
    * "module" / "mount" — absent from layout_tree_from_base's returned map (a
      module node places no record of its own; a mount node's base is LIVE), so
      tree_pivot_offset could not resolve them;
    * a node hanging under a mount node at ANY depth — its base is pinned to a
      LIVE component (mount_node_base), so it does NOT move when the tree moves
      — physically not a handle (plan_2026_09_11_pivot_ref_mount_ancestor
      §P.1.3)."""
    if target.kind == "external":
        return "external"
    if target.kind in ("module", "mount"):
        return target.kind
    if _mount_ancestor_of(target, nodes) is not None:
        return "mount-ancestor"
    return None


def tree_pivot_ref_candidates(tree: Tree) -> list[str]:
    """Refs of THIS tree's nodes that may legally be its pivot-ref, in
    depth-first traversal order — the picker list for the tree settings form.

    Derived from the SAME predicate the load-time validator enforces, so the
    list can never offer a name that `_validate_tree_pivot_ref` would reject
    (plan_2026_09_11_tree_settings_form §W.3). May legitimately be EMPTY: a
    tree whose every record-backed node hangs under a mount node (e.g. the
    working `ch0_dac_buf`) has no node that follows the tree, so `pivot-xy`
    is the only usable inner point there (§W.3.1)."""
    return [n.ref for n in _walk_nodes(tree.nodes)
            if _pivot_ref_rejection(n, tree.nodes) is None]


def tree_self_ref_candidates(tree: Tree | None) -> list[str]:
    """Refs of THIS tree's kind "placement" nodes, in depth-first traversal
    order — the picker list for a (self (ref "...")) anchor (plan 2026-09-11
    tree_self_anchor, task Д.7). The SAME set the load-time validator
    (_validate_self_ref) accepts. Legitimately EMPTY (a create-tree dialog with
    no tree yet, or a tree with no placement nodes) — the (self) form then just
    offers the bare form."""
    if tree is None:
        return []
    return [n.ref for n in _walk_nodes(tree.nodes)
            if n.kind in _PLACEMENT_KINDS]


def _validate_tree_pivot_ref(tree_name: str, nodes: list[TreeNode],
                             pivot_ref: str | None) -> None:
    """A tree's pivot-ref must name a node OF THIS TREE whose base FOLLOWS the
    tree — which rules out the three shapes `_pivot_ref_rejection` names. All
    three are load-time fatals on BOTH paths (s-expr and dict bridge)."""
    if pivot_ref is None:
        return
    by_ref = {n.ref: n for n in _walk_nodes(nodes)}
    target = by_ref.get(pivot_ref)
    if target is None:
        _fatal(_("tree {name!r}: pivot-ref {ref!r} names no node of this tree")
               .format(name=tree_name, ref=pivot_ref))
    reason = _pivot_ref_rejection(target, nodes)
    if reason == "external":
        _fatal(_("tree {name!r}: pivot-ref {ref!r} is a kind \"external\" node — a "
                 "live refdes is not portable, so it cannot be the tree's inner "
                 "point").format(name=tree_name, ref=pivot_ref))
    # kind "module" / "mount" themselves: absent from layout_tree_from_base's map,
    # so tree_pivot_offset could not resolve them — it would raise at apply time
    # instead of here. Rejecting at LOAD keeps the failure early and explicit.
    # Opening a mount node up needs a real decision about the FRAME its live base
    # is expressed in.
    if reason in ("module", "mount"):
        _fatal(_("tree {name!r}: pivot-ref {ref!r} is a kind {kind!r} node, which "
                 "the layout cannot resolve yet (it places no record of its own) — "
                 "use a record-backed node of this tree as the inner point")
               .format(name=tree_name, ref=pivot_ref, kind=reason))
    if reason == "mount-ancestor":
        mount_ancestor = _mount_ancestor_of(target, nodes)
        _fatal(_("tree {name!r}: pivot-ref {ref!r} hangs under mount node "
                 "{mount!r} — a node under a mount node is pinned to that live "
                 "component's position and does not follow the tree, so it "
                 "cannot be the tree's inner point (use a node outside the "
                 "mount subtree, or pivot-xy)")
               .format(name=tree_name, ref=pivot_ref, mount=mount_ancestor.ref))


def _parse_tree_pivot(tree_node, tree_name: str, nodes: list[TreeNode]
                      ) -> tuple[tuple[float, float] | None,
                                 tuple[float, float] | None, str | None]:
    """The tree's INNER point (plan §V.1): pivot-xy / pivot-polar / pivot-ref,
    mutually exclusive, absent = (0,0) = the tree's own origin. Same three
    keywords the module node used to carry — the converter then just lifts the
    s-expr one level up, and the user has no second name to learn."""
    pivot_xy = _parse_tree_offset(tree_node, "pivot-xy", tree_name)
    pivot_polar = _parse_tree_offset(tree_node, "pivot-polar", tree_name)
    raw_pivot_ref = atom(tree_node, "pivot-ref")
    pivot_ref = sval(raw_pivot_ref) if raw_pivot_ref is not None else None
    if sum(v is not None for v in (pivot_xy, pivot_polar, pivot_ref)) > 1:
        _fatal(_("tree {name!r}: pivot-xy, pivot-polar and pivot-ref are mutually "
                 "exclusive (use at most one)").format(name=tree_name))
    _validate_tree_pivot_ref(tree_name, nodes, pivot_ref)
    return pivot_xy, pivot_polar, pivot_ref


def tree_from_sexp(tree_node, seen_names: set[str], seen_refs: set[str],
                   location: str) -> Tree:
    """Parse ONE (tree ...) node (no (kicadstamp-trees ...) wrapper) into a
    Tree. seen_names/seen_refs seed from the CALLER so name/ref uniqueness
    spans the whole include graph, not just one file (the config inlay calls
    this once per tree with shared sets; load_trees seeds them empty)."""
    name = atom(tree_node, "name")
    if name is None:
        _fatal(_("{location}: a tree is missing a (name ...)").format(location=location))
    name = sval(name)
    if name in seen_names:
        _fatal(_("{location}: duplicate tree name {name!r} — tree names must be "
                 "unique within one config").format(location=location, name=name))
    seen_names.add(name)

    anchor_node = child(tree_node, "anchor")
    # A missing (anchor ...) is read as (self) FOREVER (2026-09-11, plan
    # tree_self_anchor, task Д.2 rule 1): the tree's anchor is derived at
    # materialization time from its own root Entity placement's cell mount
    # point. No migration — every existing config keeps loading unchanged.
    anchor = (_parse_anchor(anchor_node) if anchor_node is not None
              else TreeAnchor(is_self=True))

    top_nodes = children(tree_node, "node")
    parsed_nodes = [_parse_node(n, seen_refs, f"{location}:tree {name!r}")
                    for n in top_nodes]
    _validate_mount_refs(parsed_nodes, name)
    # A self (ref ...) names a node of THIS tree — validated with the nodes in
    # hand (the anchor is parsed before them).
    _validate_self_ref(name, parsed_nodes, anchor)
    # The tree's OWN inner point + angle (plan §V.1/§V.2) — parsed here, so a
    # pivot-ref is validated against THIS tree's nodes without needing a Config.
    pivot_xy, pivot_polar, pivot_ref = _parse_tree_pivot(tree_node, name, parsed_nodes)
    rotation = _parse_tree_rotation(tree_node, name)
    return Tree(name=name, anchor=anchor, nodes=parsed_nodes,
                pivot_xy=pivot_xy, pivot_polar=pivot_polar, pivot_ref=pivot_ref,
                rotation=rotation)


def tree_to_sexp(tree: Tree) -> list:
    """Serialize one Tree into the (tree ...) s-expr node shape — the public
    alias of _tree_to_sexp, used by sexp_format.py's trees inlay."""
    return _tree_to_sexp(tree)


def load_trees(path: str) -> list[Tree]:
    """Parse one `*.trees` file into a list of Tree dataclasses. Pure
    syntax: no YAML, no Config, no record lookup — see the module docstring.

    Raises ValidationError (not OSError) on any structural violation, so a
    hand-authored tree file fails loudly at load time, same discipline as
    config/loader.py's duplicate-name fatal."""
    obj = load_file(path)
    if not is_node(obj, "kicadstamp-trees"):
        _fatal(_("{path}: top level must be (kicadstamp-trees ...)")
               .format(path=path))

    tree_nodes = children(obj, "tree")
    seen_names: set[str] = set()
    seen_refs: set[str] = set()
    return [tree_from_sexp(n, seen_names, seen_refs, path) for n in tree_nodes]


def _node_to_sexp(node: TreeNode) -> list:
    """Serialize one TreeNode into the nested s-expr node shape. Fields with
    default values are OMITTED (kind None, rotation 0.0, name/group None) —
    load_trees would re-default them on read anyway, so writing them is pure
    noise (same "no `sheet: null`" principle the YAML config uses)."""
    out: list = [sym("node"), [sym("ref"), node.ref]]
    if node.kind is not None:
        # kind is a Symbol in the grammar ((kind clone)), not a quoted string —
        # load_trees's sval() reads it back as str either way, so the
        # round-trip dataclass equality is unaffected.
        out.append([sym("kind"), sym(node.kind)])
    if node.xy is not None:
        out.append([sym("xy"), node.xy[0], node.xy[1]])
    elif node.polar is not None:
        out.append([sym("polar"), node.polar[0], node.polar[1]])
    if node.rotation != 0.0:
        out.append([sym("rotation"), node.rotation])
    if node.name is not None:
        out.append([sym("name"), node.name])
    if node.group is not None:
        out.append([sym("group"), node.group])
    if node.anchor is not None:
        # A kind "mount" node's anchor serializes as a nested (anchor ...) child
        # with the SAME role shape as a tree-level role anchor (plan §Y.1.1) —
        # written explicitly (not via _anchor_to_sexp) so a hand-built non-role
        # anchor can never leak an origin/ref/point/external shape into a node
        # (the parser fatals on it).
        a = node.anchor
        anchor_sexp = [sym("anchor"), [sym("role"), a.role]]
        if a.anchor_sheet is not None:
            anchor_sexp.append([sym("sheet"), a.anchor_sheet])
        if a.anchor_cluster is not None:
            anchor_sexp.append([sym("cluster"), a.anchor_cluster])
        if a.anchor_pad is not None:
            anchor_sexp.append([sym("pad"), a.anchor_pad])
        out.append(anchor_sexp)
    for child_node in node.children:
        out.append(_node_to_sexp(child_node))
    return out


def anchor_to_sexp(anchor: TreeAnchor) -> list:
    """Public entry point to the anchor s-expr grammar (_anchor_to_sexp): the
    tree_instances: DECLARATION's own (anchor ...) is written with THIS very
    serializer (2026-09-12, plan_2026_09_12_tree_instance_own_place §И.2) so a
    declaration never grows a second anchor grammar."""
    return _anchor_to_sexp(anchor)


def parse_anchor(anchor_node) -> TreeAnchor:
    """Public entry point to the anchor s-expr PARSER (_parse_anchor) — the
    symmetric half of anchor_to_sexp(), used for a tree_instances: declaration's
    own (anchor ...) (§И.2). Validating: a malformed anchor is a fatal here,
    with the s-expr grammar's own messages."""
    return _parse_anchor(anchor_node)


def anchor_to_dict(anchor: TreeAnchor) -> dict:
    """Public entry point to the anchor dict bridge (_anchor_to_dict) — what a
    tree_instances: declaration stores for its own anchor (the SAME dict shape a
    tree's `anchor:` uses, §И.2)."""
    return _anchor_to_dict(anchor)


def _anchor_to_sexp(anchor: TreeAnchor) -> list:
    """Serialize one anchor node: (origin), (ref ...) [(external)],
    (role ...) (+ sheet/cluster/pad), (point ...), (self ...), and the optional
    own (shift x y). An ABSENT anchor is written EXPLICITLY as (anchor (self))
    (plan Д.2 rule 2): the self mode is the canonical form of "no anchor", so
    the result is never empty and the (anchor ...) node is never omitted."""
    if anchor.is_self:
        self_node: list = [sym("self")]
        if anchor.self_ref is not None:
            self_node.append([sym("ref"), anchor.self_ref])
        if anchor.self_pad is not None:
            self_node.append([sym("pad"), anchor.self_pad])
        out = [sym("anchor"), self_node]
    elif anchor.is_origin:
        out = [sym("anchor"), [sym("origin")]]
    else:
        out = [sym("anchor")]
        if anchor.ref is not None:
            out.append([sym("ref"), anchor.ref])
            if anchor.is_external:
                out.append([sym("external")])
        elif anchor.point is not None:
            out.append([sym("point"), anchor.point])
        else:
            out.append([sym("role"), anchor.role])
            if anchor.anchor_sheet is not None:
                out.append([sym("sheet"), anchor.anchor_sheet])
            if anchor.anchor_cluster is not None:
                out.append([sym("cluster"), anchor.anchor_cluster])
            if anchor.anchor_pad is not None:
                out.append([sym("pad"), anchor.anchor_pad])
    if anchor.shift_xy is not None:
        out.append([sym("shift"), anchor.shift_xy[0], anchor.shift_xy[1]])
    return out


def _tree_to_sexp(tree: Tree) -> list:
    """Serialize one Tree (name, anchor, inner point, own angle, top-level
    nodes). The (anchor ...) node is ALWAYS written (a self anchor serializes
    as (anchor (self)), plan Д.2 rule 2); the pivot-*/rotation keys are written
    only when set, the same no-noise discipline every other optional field
    follows."""
    out: list = [sym("tree"), [sym("name"), tree.name]]
    out.append(_anchor_to_sexp(tree.anchor))
    if tree.pivot_xy is not None:
        out.append([sym("pivot-xy"), tree.pivot_xy[0], tree.pivot_xy[1]])
    elif tree.pivot_polar is not None:
        out.append([sym("pivot-polar"), tree.pivot_polar[0], tree.pivot_polar[1]])
    elif tree.pivot_ref is not None:
        out.append([sym("pivot-ref"), tree.pivot_ref])
    if tree.rotation != 0.0:
        out.append([sym("rotation"), tree.rotation])
    for node in tree.nodes:
        out.append(_node_to_sexp(node))
    return out


def save_trees(path: str, trees: list[Tree]) -> None:
    """Inverse of load_trees(): serializes trees back into the v1 s-expr
    grammar and writes them via cloner.sexp.save_file(). Round-trip
    contract: load_trees(path) after save_trees(path, trees) must equal
    trees structurally (== on the dataclasses, which are plain @dataclass,
    not @dataclass(eq=False) — see Tree/TreeNode/TreeAnchor definitions)."""
    obj: list = [sym("kicadstamp-trees"), [sym("version"), 1]]
    for tree in trees:
        obj.append(_tree_to_sexp(tree))
    save_file(path, obj)


# ── dict bridges (for the config inlay: trees as a section of Config) ──────
# The plain-dict shape mirrors the s-expr node shape 1:1 (design_2026_08_27_
# trees_in_config_file.md FORK-2, Variant B): sexp_format.py delegates the
# (trees ...) node to tree_to_sexp / tree_from_sexp, and config/entries.py's
# _load_tree wraps tree_from_dict for the dict pipeline. Bijective, with
# default-valued fields omitted on serialization (same no-noise principle as
# _node_to_sexp) — tree_to_dict(tree_from_dict(d)) is the canonical form.

def _anchor_to_dict(anchor: TreeAnchor) -> dict:
    """Mirror of _anchor_to_sexp in plain-dict shape (the config inlay). A self
    anchor serializes as {"self": {}} (with optional "ref"/"pad" inside), NEVER
    by omitting the key (plan Д.2 rule 2). The anchor's own "shift" (local-mm,
    §X.2) is added on ANY base kind."""
    if anchor.is_self:
        self_data: dict = {}
        if anchor.self_ref is not None:
            self_data["ref"] = anchor.self_ref
        if anchor.self_pad is not None:
            self_data["pad"] = anchor.self_pad
        out: dict = {"self": self_data}
    elif anchor.is_origin:
        out = {"origin": True}
    elif anchor.ref is not None:
        out = {"ref": anchor.ref}
        if anchor.is_external:
            out["external"] = True
    elif anchor.point is not None:
        out = {"point": anchor.point}
    else:
        out = {"role": anchor.role}
        if anchor.anchor_sheet is not None:
            out["sheet"] = anchor.anchor_sheet
        if anchor.anchor_cluster is not None:
            out["cluster"] = anchor.anchor_cluster
        if anchor.anchor_pad is not None:
            out["pad"] = anchor.anchor_pad
    if anchor.shift_xy is not None:
        out["shift"] = [anchor.shift_xy[0], anchor.shift_xy[1]]
    return out


def _node_to_dict(node: TreeNode) -> dict:
    out: dict = {"ref": node.ref}
    if node.kind is not None:
        out["kind"] = node.kind
    if node.xy is not None:
        out["xy"] = [node.xy[0], node.xy[1]]
    elif node.polar is not None:
        out["polar"] = [node.polar[0], node.polar[1]]
    if node.rotation != 0.0:
        out["rotation"] = node.rotation
    if node.name is not None:
        out["name"] = node.name
    if node.group is not None:
        out["group"] = node.group
    if node.anchor is not None:
        # A kind "mount" node's anchor in the dict node shape — role-only
        # (mirror of the s-expr (anchor ...) child of a node), written
        # explicitly so a hand-built non-role anchor can never leak a
        # ref/origin/point shape into the config dict (the parser fatals on it).
        a = node.anchor
        anchor_dict: dict = {"role": a.role}
        if a.anchor_sheet is not None:
            anchor_dict["sheet"] = a.anchor_sheet
        if a.anchor_cluster is not None:
            anchor_dict["cluster"] = a.anchor_cluster
        if a.anchor_pad is not None:
            anchor_dict["pad"] = a.anchor_pad
        out["anchor"] = anchor_dict
    if node.children:
        out["children"] = [_node_to_dict(c) for c in node.children]
    return out


def tree_to_dict(tree: Tree) -> dict:
    """Tree -> plain dict (the config-dict shape). Default-valued fields are
    omitted (kind None, rotation 0.0, name/group None, no offset, empty
    children) so the dict stays minimal — same principle as _node_to_sexp.
    The tree's own inner point + angle (§V.1/§V.2) are written only when set."""
    out: dict = {"name": tree.name}
    out["anchor"] = _anchor_to_dict(tree.anchor)
    if tree.pivot_xy is not None:
        out["pivot_xy"] = [tree.pivot_xy[0], tree.pivot_xy[1]]
    elif tree.pivot_polar is not None:
        out["pivot_polar"] = [tree.pivot_polar[0], tree.pivot_polar[1]]
    elif tree.pivot_ref is not None:
        out["pivot_ref"] = tree.pivot_ref
    if tree.rotation != 0.0:
        out["rotation"] = tree.rotation
    if tree.nodes:
        out["nodes"] = [_node_to_dict(n) for n in tree.nodes]
    return out


# ── raw (UNVALIDATED) s-expr -> config-dict, for the one-way converter only ──

def _raw_anchor(anchor_node) -> dict:
    """(anchor ...) -> the config-dict anchor shape, WITHOUT validation — the
    converter must be able to read whatever a pre-2026-09-11 config holds.
    A (shift x y) is passed through verbatim (it is the NEW grammar, but the
    converter must round-trip it unchanged — idempotency). A (self ...) anchor
    (the NEW grammar, plan Д) is passed through too — WITHOUT this the pass
    would fall through to the role branch and rewrite (anchor (self)) as
    (anchor (role None)), corrupting an already-converted config."""
    if child(anchor_node, "self") is not None:
        self_node = child(anchor_node, "self")
        self_data: dict = {}
        s_ref = atom(self_node, "ref")
        if s_ref is not None:
            self_data["ref"] = s_ref
        s_pad = atom(self_node, "pad")
        if s_pad is not None:
            self_data["pad"] = s_pad
        out: dict = {"self": self_data}
    elif child(anchor_node, "origin") is not None:
        out = {"origin": True}
    else:
        ref = atom(anchor_node, "ref")
        point = atom(anchor_node, "point")
        if ref is not None:
            out = {"ref": sval(ref)}
            if child(anchor_node, "external") is not None:
                out["external"] = True
        elif point is not None:
            out = {"point": sval(point)}
        else:
            out = {"role": sval(atom(anchor_node, "role"))}
            for key in ("sheet", "cluster", "pad"):
                value = atom(anchor_node, key)
                if value is not None:
                    out[key] = sval(value)
    shift = _raw_offset(anchor_node, "shift")
    if shift is not None:
        out["shift"] = shift
    return out


def _raw_offset(node, key: str):
    c = child(node, key)
    if c is None or len(c) != 3:
        return None
    return [c[1], c[2]]


def _raw_node(node) -> dict:
    """(node ...) -> the config-dict node shape, WITHOUT validation. Keeps a
    nested (anchor ...) verbatim (the removed own_anchor grammar included) so
    the converter can see it and rewrite it."""
    out: dict = {"ref": sval(atom(node, "ref"))}
    kind = atom(node, "kind")
    if kind is not None:
        out["kind"] = sval(kind)
    xy = _raw_offset(node, "xy")
    if xy is not None:
        out["xy"] = xy
    polar = _raw_offset(node, "polar")
    if polar is not None:
        out["polar"] = polar
    rotation = atom(node, "rotation")
    if rotation is not None:
        out["rotation"] = rotation
    name = atom(node, "name")
    if name is not None:
        out["name"] = sval(name)
    group = atom(node, "group")
    if group is not None:
        out["group"] = sval(group)
    for sexp_key, dict_key in (("pivot-xy", "pivot_xy"),
                               ("pivot-polar", "pivot_polar")):
        value = _raw_offset(node, sexp_key)
        if value is not None:
            out[dict_key] = value
    pivot_ref = atom(node, "pivot-ref")
    if pivot_ref is not None:
        out["pivot_ref"] = sval(pivot_ref)
    anchor_node = child(node, "anchor")
    if anchor_node is not None:
        out["anchor"] = _raw_anchor(anchor_node)
    child_nodes = children(node, "node")
    if child_nodes:
        out["children"] = [_raw_node(c) for c in child_nodes]
    return out


def raw_tree_from_sexp(tree_node) -> dict:
    """(tree ...) -> the config-dict tree shape, WITHOUT grammar validation.

    Used ONLY by the one-way old-grammar converter (kicadstamp/
    tree_mount_convert.py, plan §Y.6): unlike tree_from_sexp this ACCEPTS the
    removed per-node own_anchor grammar, so the converter can read a
    pre-2026-09-11 config and rewrite it. Every normal reader must keep using
    tree_from_sexp, which fatals on the removed grammar with a pointer to the
    converter.

    BOTH pivot locations are read here (2026-09-11, plan_2026_09_11_tree_inner_
    point_and_rotation §V.4): the tree-level one (the NEW grammar) and the
    node-level one (_raw_node keeps it verbatim — the OLD grammar the converter
    has to lift). The converter needs to see both to decide whether they
    conflict (V.4.2) and to be idempotent."""
    out: dict = {"name": sval(atom(tree_node, "name"))}
    anchor_node = child(tree_node, "anchor")
    if anchor_node is not None:
        out["anchor"] = _raw_anchor(anchor_node)
    for sexp_key, dict_key in (("pivot-xy", "pivot_xy"),
                               ("pivot-polar", "pivot_polar")):
        value = _raw_offset(tree_node, sexp_key)
        if value is not None:
            out[dict_key] = value
    pivot_ref = atom(tree_node, "pivot-ref")
    if pivot_ref is not None:
        out["pivot_ref"] = sval(pivot_ref)
    rotation = atom(tree_node, "rotation")
    if rotation is not None:
        out["rotation"] = rotation
    nodes = children(tree_node, "node")
    if nodes:
        out["nodes"] = [_raw_node(n) for n in nodes]
    return out


def _dict_offset(data: dict, key: str, location: str) -> tuple[float, float] | None:
    """Node dict's (key, [x, y]) as a pair of floats, or None. Enforces
    "exactly 2 numbers" — a non-numeric value is fatal."""
    raw = data.get(key)
    if raw is None:
        return None
    if not (isinstance(raw, (list, tuple)) and len(raw) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in raw)):
        _fatal(_("node {ref!r}: {key} must be exactly two numbers")
               .format(ref=data.get("ref"), key=key))
    return float(raw[0]), float(raw[1])


def _dict_mount_anchor(ref: str, anchor_data: dict) -> TreeAnchor:
    """A kind "mount" node dict's nested "anchor" mapping (mirror of the s-expr
    (anchor (role ...)) child). Only the role shape is valid — origin/ref/point/
    external/shift on a mount node's anchor are tree-anchor-only concepts and
    are load-time fatal (mirrors _parse_mount_anchor)."""
    if not isinstance(anchor_data, dict):
        _fatal(_("mount node {ref!r}: anchor must be a mapping").format(ref=ref))
    forbidden = [k for k in ("origin", "ref", "point", "external", "self",
                             "shift")
                 if anchor_data.get(k) is not None]
    if forbidden:
        _fatal(_("mount node {ref!r}: anchor supports only role — {keys} are "
                 "tree-anchor-only").format(ref=ref, keys=", ".join(forbidden)))
    role = anchor_data.get("role")
    if not role:
        _fatal(_("mount node {ref!r}: anchor needs a role").format(ref=ref))
    return TreeAnchor(
        role=role,
        is_origin=False,
        anchor_sheet=anchor_data.get("sheet"),
        anchor_cluster=anchor_data.get("cluster"),
        anchor_pad=anchor_data.get("pad"),
    )


def _dict_node(data: dict, seen_refs: set[str], location: str) -> TreeNode:
    """Parse one dict node (the config-dict shape), recursing into nested
    children. seen_refs enforces the "a ref appears in at most one node"
    invariant across the WHOLE config (shared set from the caller)."""
    ref = data.get("ref")
    if ref is None:
        _fatal(_("{location}: node is missing a (ref ...)").format(location=location))

    raw_kind = data.get("kind")
    if raw_kind is not None and raw_kind not in KINDS and raw_kind not in LEGACY_KINDS:
        _fatal(_("node {ref!r}: invalid kind {kind!r} — expected one of {kinds}")
               .format(ref=ref, kind=raw_kind, kinds=", ".join(KINDS)))
    # Mirror of the s-expr _parse_node: a module node's ref is a TREE name and a
    # mount node's ref is a local NAME — neither is a record, so exempt them
    # from the file-wide seen_refs (rule 2) check here too (the same tree may be
    # embedded by several different parents; mount ref uniqueness is per-TREE,
    # see _validate_mount_refs).
    if raw_kind not in ("module", "mount"):
        if ref in seen_refs:
            _fatal(_("{location}: record {ref!r} already has a node elsewhere in this "
                     "config — a record's position source must be exactly one")
                   .format(location=location, ref=ref))
        seen_refs.add(ref)

    anchor_data = data.get("anchor")
    if raw_kind == "mount":
        if anchor_data is None:
            _fatal(_("mount node {ref!r}: needs an anchor mapping with role — a "
                     "mount node is a point of reference and places nothing "
                     "itself").format(ref=ref))
        node_anchor = _dict_mount_anchor(ref, anchor_data)
    else:
        if anchor_data is not None:
            _fatal(_("node {ref!r}: a nested anchor mapping is only valid on a "
                     "kind mount node — the old own_anchor grammar was removed "
                     "2026-09-11; run the tree converter (kicadstamp "
                     "convert-trees) on this config").format(ref=ref))
        node_anchor = None

    xy = _dict_offset(data, "xy", location)
    polar = _dict_offset(data, "polar", location)
    if xy is not None and polar is not None:
        _fatal(_("node {ref!r}: xy and polar are mutually exclusive "
                 "(use exactly one)").format(ref=ref))

    # pivot-* on a NODE is the old grammar (plan §V.3) — the inner point moved
    # to the TREE. Mirror of the s-expr path's leftover fatal, so the dict
    # pipeline (config/entries.py -> tree_from_dict) fails the same way.
    for leftover in ("pivot_xy", "pivot_polar", "pivot_ref"):
        if data.get(leftover) is not None:
            _fatal(_("node {ref!r}: {key} is no longer valid on a node — the "
                     "tree's inner point moved to the TREES level; run the tree "
                     "converter (kicadstamp convert-trees) on this config")
                   .format(ref=ref, key=leftover))

    raw_rotation = data.get("rotation")
    if raw_rotation is not None and not isinstance(raw_rotation, (int, float)):
        _fatal(_("node {ref!r}: rotation must be a number").format(ref=ref))

    return TreeNode(
        ref=ref,
        kind=raw_kind,
        xy=xy,
        polar=polar,
        rotation=float(raw_rotation) if raw_rotation is not None else 0.0,
        name=data.get("name"),
        group=data.get("group"),
        children=[_dict_node(c, seen_refs, f"{location}.node") for c in data.get("children") or []],
        anchor=node_anchor,
    )


def _dict_tree_offset(data: dict, key: str, tree_name: str
                      ) -> tuple[float, float] | None:
    """Tree dict's (key, [x, y]) as a pair of floats, or None — the tree-level
    mirror of _dict_offset (which formats a node-shaped error message)."""
    raw = data.get(key)
    if raw is None:
        return None
    if not (isinstance(raw, (list, tuple)) and len(raw) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in raw)):
        _fatal(_("tree {name!r}: {key} must be exactly two numbers")
               .format(name=tree_name, key=key))
    return float(raw[0]), float(raw[1])


def _dict_tree_pivot(data: dict, tree_name: str, nodes: list[TreeNode]
                     ) -> tuple[tuple[float, float] | None,
                                tuple[float, float] | None, str | None]:
    """The dict-bridge mirror of _parse_tree_pivot (config-dict tree shape):
    pivot_xy / pivot_polar / pivot_ref, mutually exclusive, validated against
    THIS tree's own nodes."""
    pivot_xy = _dict_tree_offset(data, "pivot_xy", tree_name)
    pivot_polar = _dict_tree_offset(data, "pivot_polar", tree_name)
    pivot_ref = data.get("pivot_ref")
    if pivot_ref is not None and not isinstance(pivot_ref, str):
        _fatal(_("tree {name!r}: pivot_ref must be a string").format(name=tree_name))
    if sum(v is not None for v in (pivot_xy, pivot_polar, pivot_ref)) > 1:
        _fatal(_("tree {name!r}: pivot_xy, pivot_polar and pivot_ref are mutually "
                 "exclusive (use at most one)").format(name=tree_name))
    _validate_tree_pivot_ref(tree_name, nodes, pivot_ref)
    return pivot_xy, pivot_polar, pivot_ref


def _dict_anchor_shift(anchor_data: dict, tree_name: str
                       ) -> tuple[float, float] | None:
    """The anchor dict's optional "shift" [x, y] — the anchor's OWN LOCAL-mm
    offset (plan_2026_09_11_external_point_materialization §X.2), or None.
    Exactly two numbers, or a fatal (mirror of _parse_anchor_shift)."""
    raw = anchor_data.get("shift")
    if raw is None:
        return None
    if not (isinstance(raw, (list, tuple)) and len(raw) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in raw)):
        _fatal(_("tree {name!r}: anchor shift must be exactly two numbers")
               .format(name=tree_name))
    return float(raw[0]), float(raw[1])


def anchor_from_dict(anchor_data: dict, owner: str) -> TreeAnchor:
    """Plain-dict anchor shape -> TreeAnchor, the ONE dict-bridge anchor parser:
    shared by tree_from_dict and by a tree_instances: DECLARATION's own `anchor:`
    (2026-09-12, plan_2026_09_12_tree_instance_own_place §И.2 — a declaration's
    anchor is the TREE anchor grammar, verbatim; no second grammar).

    `owner` is the name the anchor belongs to (a real tree's name, or a
    declaration's instance name) and appears in every fatal message.

    A missing/empty mapping reads as a bare (self) FOREVER (plan Д.2 rule 1);
    a shift with no base is a fatal (mirrors the s-expr mode-count fatal)."""
    anchor_data = anchor_data or {}
    shift_xy = _dict_anchor_shift(anchor_data, owner)
    anchor_modes = [k for k in ("origin", "ref", "role", "point", "self")
                    if anchor_data.get(k) is not None]
    if len(anchor_modes) > 1:
        _fatal(_("anchor must specify exactly one of origin/ref/role/point/self"))
    if not anchor_modes:
        # (the self default also covers a shift-less {} — see the docstring)
        if shift_xy is not None:
            _fatal(_("anchor: shift needs a base — set one of "
                     "origin/ref/role/point/self"))
        return TreeAnchor(is_self=True)
    if anchor_data.get("origin"):
        if anchor_data.get("external"):
            _fatal(_("anchor: origin and external are mutually exclusive"))
        return TreeAnchor(is_origin=True, shift_xy=shift_xy)
    if anchor_data.get("self") is not None:
        self_data = anchor_data["self"]
        if self_data is not None and not isinstance(self_data, dict):
            _fatal(_("anchor: self must be a mapping with an optional ref/pad"))
        self_data = self_data or {}
        return TreeAnchor(
            is_self=True,
            self_ref=self_data.get("ref"),
            self_pad=self_data.get("pad"),
            shift_xy=shift_xy,
        )
    if anchor_data.get("ref") is not None:
        return TreeAnchor(ref=anchor_data["ref"],
                          is_external=bool(anchor_data.get("external")),
                          shift_xy=shift_xy)
    # (external) is a REF-anchor modifier only — a role/point anchor is never a
    # config record, so "external" on it would be silently meaningless. Hard
    # fatal, mirroring the s-expr path (_parse_anchor).
    if anchor_data.get("external"):
        _fatal(_("anchor: external is only valid with a ref anchor"))
    if anchor_data.get("point") is not None:
        return TreeAnchor(point=anchor_data["point"], shift_xy=shift_xy)
    role = anchor_data.get("role")
    if not role:
        _fatal(_("anchor must specify exactly one of origin/ref/role/point/self"))
    return TreeAnchor(
        role=role,
        anchor_sheet=anchor_data.get("sheet"),
        anchor_cluster=anchor_data.get("cluster"),
        anchor_pad=anchor_data.get("pad"),
        shift_xy=shift_xy,
    )


def tree_from_dict(data: dict, seen_refs: set[str] | None = None) -> Tree:
    """Plain dict -> Tree, the inverse of tree_to_dict. seen_refs (optional,
    shared across the whole config) enforces node-ref uniqueness across the
    include graph; when None a fresh set is used (single-tree call)."""
    if not isinstance(data, dict):
        _fatal(_("tree must be a mapping"))
    if seen_refs is None:
        seen_refs = set()

    name = data.get("name")
    if name is None:
        _fatal(_("a tree is missing a (name ...)"))
    anchor = anchor_from_dict(data.get("anchor") or {}, name)
    parsed_nodes = [_dict_node(n, seen_refs, f"tree {name!r}")
                    for n in data.get("nodes") or []]
    _validate_mount_refs(parsed_nodes, name)
    _validate_self_ref(name, parsed_nodes, anchor)
    # The tree's OWN inner point + angle (plan §V.1/§V.2) — the dict mirror of
    # tree_from_sexp's tail.
    pivot_xy, pivot_polar, pivot_ref = _dict_tree_pivot(data, name, parsed_nodes)
    raw_rotation = data.get("rotation")
    if raw_rotation is not None and not isinstance(raw_rotation, (int, float)):
        _fatal(_("tree {name!r}: rotation must be a number").format(name=name))
    return Tree(name=name, anchor=anchor, nodes=parsed_nodes,
                pivot_xy=pivot_xy, pivot_polar=pivot_polar, pivot_ref=pivot_ref,
                rotation=float(raw_rotation) if raw_rotation is not None else 0.0)
