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
     "places")
  3. xy / polar are mutually exclusive, each exactly 2 numbers
  4. kind, if present, is one of clone/rule/coordinate/point/external
  5. cycles are impossible by construction (nested s-expr structure)
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
KINDS = ("clone", "placement", "chain", "coordinate", "point", "external")

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
    is_external=True marks a ref anchor as live-board-only (never resolved
    against config) — symmetric to kind="external" on TreeNode (the anchor's
    own collision shield).

    is_auto=True (2026-08-31, plan tree_self_anchor_from_entity) — NO explicit
    (anchor ...) at all: the tree's anchor is derived at materialization time
    from its own root Entity placement's cell "zero slot" (the single
    component at local offset (0,0)), live-resolved like a (role ...) anchor.
    Explicit anchors ALWAYS win; is_auto is only ever the ABSENT-anchor case.
    """
    ref: str | None = None        # None unless a ref anchor
    is_origin: bool = False
    is_external: bool = False   # only meaningful for a ref anchor
    role: str | None = None
    anchor_sheet: str | None = None
    anchor_cluster: str | None = None
    anchor_pad: str | None = None
    point: str | None = None
    is_auto: bool = False


@dataclass
class TreeNode:
    ref: str
    kind: str | None       # "clone"/"chain"/"coordinate"/"point"/"external", or None (auto)
    xy: tuple[float, float] | None
    polar: tuple[float, float] | None   # (radius_mm, angle_deg)
    rotation: float
    name: str | None       # display label, default = ref
    group: str | None      # pure UI tag, does not participate in geometry
    children: list["TreeNode"] = field(default_factory=list)


@dataclass
class Tree:
    name: str
    anchor: TreeAnchor
    nodes: list[TreeNode]  # top-level nodes


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


def _parse_anchor(anchor_node) -> TreeAnchor:
    """(anchor (origin)) -> origin anchor;
    (anchor (ref "...") [(external)]) -> ref anchor (external = live-board-only
    refdes, NEVER resolved against config);
    (anchor (role "...") [(sheet ...) (cluster ...) (pad ...)]) -> role-based
    anchor by the Role custom field on a live component;
    (anchor (point "...")) -> points: entry anchor.
    Exactly one base kind is required (fatal otherwise). ref/role/point are NOT
    validated for uniqueness against nodes — an anchor is a base, not something
    the tree places (rule 2)."""
    is_origin = child(anchor_node, "origin") is not None
    is_external = child(anchor_node, "external") is not None
    ref = atom(anchor_node, "ref")
    role = atom(anchor_node, "role")
    point = atom(anchor_node, "point")
    mode_count = sum(1 for m in (is_origin, ref is not None,
                                 role is not None, point is not None) if m)
    if mode_count != 1:
        _fatal(_("anchor must specify exactly one of (origin), (ref \"...\"), "
                 "(role \"...\"), (point \"...\")"))
    if is_origin:
        if is_external:
            _fatal(_("anchor: (origin) and (external) are mutually exclusive"))
        return TreeAnchor(ref=None, is_origin=True, is_external=False)
    # (external) is a REF-anchor modifier only: a role/point anchor is never a
    # config record, so "external" on it would be silently meaningless. Checked
    # HERE (before the ref/point/role branches) so a (point ...) (external) or
    # (role ...) (external) combination is a hard fatal on BOTH paths, never a
    # silent drop.
    if is_external and ref is None:
        _fatal(_("anchor: (external) is only valid with a (ref \"...\") anchor"))
    if ref is not None:
        return TreeAnchor(ref=sval(ref), is_origin=False, is_external=is_external)
    if point is not None:
        return TreeAnchor(point=sval(point), is_origin=False)
    return TreeAnchor(
        role=sval(role),
        is_origin=False,
        anchor_sheet=_opt_sval(atom(anchor_node, "sheet")),
        anchor_cluster=_opt_sval(atom(anchor_node, "cluster")),
        anchor_pad=_opt_sval(atom(anchor_node, "pad")),
    )


def _parse_node(node, seen_refs: set[str], location: str) -> TreeNode:
    """Parse one (node ...) subtree, recursing into nested (node ...)
    children. seen_refs enforces rule 2 (a ref appears in at most one node
    across the whole file); location is the s-expr path for error messages."""
    ref = atom(node, "ref")
    if ref is None:
        _fatal(_("{location}: node is missing a (ref ...)").format(location=location))
    ref = sval(ref)
    if ref in seen_refs:
        _fatal(_("{location}: record {ref!r} already has a node elsewhere in this "
                 "file — a record's position source must be exactly one")
               .format(location=location, ref=ref))
    seen_refs.add(ref)

    xy = _parse_offset(node, "xy")
    polar = _parse_offset(node, "polar")
    if xy is not None and polar is not None:
        _fatal(_("node {ref!r}: xy and polar are mutually exclusive "
                 "(use exactly one)").format(ref=ref))

    child_nodes = children(node, "node")
    parsed_children = [
        _parse_node(c, seen_refs, f"{location}.node") for c in child_nodes
    ]

    raw_name = atom(node, "name")
    raw_group = atom(node, "group")
    return TreeNode(
        ref=ref,
        kind=_parse_kind(node),
        xy=xy,
        polar=polar,
        rotation=_parse_rotation(node),
        name=sval(raw_name) if raw_name is not None else None,
        group=sval(raw_group) if raw_group is not None else None,
        children=parsed_children,
    )


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
    # A missing (anchor ...) is now legal — the tree's anchor is AUTO-derived
    # at materialization time from its own root Entity placement's cell zero
    # slot (2026-08-31, plan tree_self_anchor_from_entity).
    anchor = _parse_anchor(anchor_node) if anchor_node is not None else TreeAnchor(is_auto=True)

    top_nodes = children(tree_node, "node")
    return Tree(
        name=name,
        anchor=anchor,
        nodes=[_parse_node(n, seen_refs, f"{location}:tree {name!r}") for n in top_nodes],
    )


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
    for child_node in node.children:
        out.append(_node_to_sexp(child_node))
    return out


def _anchor_to_sexp(anchor: TreeAnchor) -> list | None:
    """Serialize one anchor node: (origin), (ref ...) [(external)],
    (role ...) (+ sheet/cluster/pad), (point ...). None for an AUTO anchor
    (no explicit anchor — the (anchor ...) node is omitted entirely, so the
    round-trip load_trees(save_trees(x)) == x keeps holding)."""
    if anchor.is_auto:
        return None
    if anchor.is_origin:
        return [sym("anchor"), [sym("origin")]]
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
    return out


def _tree_to_sexp(tree: Tree) -> list:
    """Serialize one Tree (name, anchor, top-level nodes). The (anchor ...)
    node is omitted for an AUTO anchor (None from _anchor_to_sexp)."""
    out: list = [sym("tree"), [sym("name"), tree.name]]
    anchor_sexp = _anchor_to_sexp(tree.anchor)
    if anchor_sexp is not None:
        out.append(anchor_sexp)
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

def _anchor_to_dict(anchor: TreeAnchor) -> dict | None:
    """Mirror of _anchor_to_sexp in plain-dict shape (the config inlay). None
    for an AUTO anchor — tree_to_dict then omits the "anchor" key entirely."""
    if anchor.is_auto:
        return None
    if anchor.is_origin:
        return {"origin": True}
    if anchor.ref is not None:
        out: dict = {"ref": anchor.ref}
        if anchor.is_external:
            out["external"] = True
        return out
    if anchor.point is not None:
        return {"point": anchor.point}
    out: dict = {"role": anchor.role}
    if anchor.anchor_sheet is not None:
        out["sheet"] = anchor.anchor_sheet
    if anchor.anchor_cluster is not None:
        out["cluster"] = anchor.anchor_cluster
    if anchor.anchor_pad is not None:
        out["pad"] = anchor.anchor_pad
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
    if node.children:
        out["children"] = [_node_to_dict(c) for c in node.children]
    return out


def tree_to_dict(tree: Tree) -> dict:
    """Tree -> plain dict (the config-dict shape). Default-valued fields are
    omitted (kind None, rotation 0.0, name/group None, no offset, empty
    children) so the dict stays minimal — same principle as _node_to_sexp."""
    out: dict = {"name": tree.name}
    anchor_dict = _anchor_to_dict(tree.anchor)
    if anchor_dict is not None:
        out["anchor"] = anchor_dict
    if tree.nodes:
        out["nodes"] = [_node_to_dict(n) for n in tree.nodes]
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


def _dict_node(data: dict, seen_refs: set[str], location: str) -> TreeNode:
    """Parse one dict node (the config-dict shape), recursing into nested
    children. seen_refs enforces the "a ref appears in at most one node"
    invariant across the WHOLE config (shared set from the caller)."""
    ref = data.get("ref")
    if ref is None:
        _fatal(_("{location}: node is missing a (ref ...)").format(location=location))
    if ref in seen_refs:
        _fatal(_("{location}: record {ref!r} already has a node elsewhere in this "
                 "config — a record's position source must be exactly one")
               .format(location=location, ref=ref))
    seen_refs.add(ref)

    xy = _dict_offset(data, "xy", location)
    polar = _dict_offset(data, "polar", location)
    if xy is not None and polar is not None:
        _fatal(_("node {ref!r}: xy and polar are mutually exclusive "
                 "(use exactly one)").format(ref=ref))

    raw_kind = data.get("kind")
    if raw_kind is not None and raw_kind not in KINDS and raw_kind not in LEGACY_KINDS:
        _fatal(_("node {ref!r}: invalid kind {kind!r} — expected one of {kinds}")
               .format(ref=ref, kind=raw_kind, kinds=", ".join(KINDS)))

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
    anchor_data = data.get("anchor") or {}
    anchor_modes = [k for k in ("origin", "ref", "role", "point")
                    if anchor_data.get(k) is not None]
    if len(anchor_modes) > 1:
        _fatal(_("anchor must specify exactly one of origin/ref/role/point"))
    if not anchor_modes:
        # A tree with no (anchor ...) gets an AUTO anchor, derived at
        # materialization time from its own root Entity placement's cell zero
        # slot (2026-08-31, plan tree_self_anchor_from_entity).
        anchor = TreeAnchor(is_auto=True)
    elif anchor_data.get("origin"):
        if anchor_data.get("external"):
            _fatal(_("anchor: origin and external are mutually exclusive"))
        anchor = TreeAnchor(is_origin=True)
    elif anchor_data.get("ref") is not None:
        anchor = TreeAnchor(ref=anchor_data["ref"],
                            is_external=bool(anchor_data.get("external")))
    else:
        # (external) is a REF-anchor modifier only — a role/point anchor is
        # never a config record, so "external" on it would be silently
        # meaningless. Hard fatal, mirroring the s-expr path (_parse_anchor).
        if anchor_data.get("external"):
            _fatal(_("anchor: external is only valid with a ref anchor"))
        if anchor_data.get("point") is not None:
            anchor = TreeAnchor(point=anchor_data["point"])
        else:
            anchor = TreeAnchor(
                role=anchor_data["role"],
                anchor_sheet=anchor_data.get("sheet"),
                anchor_cluster=anchor_data.get("cluster"),
                anchor_pad=anchor_data.get("pad"),
            )
    return Tree(
        name=name,
        anchor=anchor,
        nodes=[_dict_node(n, seen_refs, f"tree {name!r}") for n in data.get("nodes") or []],
    )
