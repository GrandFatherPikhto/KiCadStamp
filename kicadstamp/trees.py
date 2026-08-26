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
KINDS = ("clone", "rule", "coordinate", "point", "external")

_OFFSET_KEYS = ("xy", "polar")


@dataclass
class TreeAnchor:
    ref: str | None        # None only if is_origin
    is_origin: bool


@dataclass
class TreeNode:
    ref: str
    kind: str | None       # "clone"/"rule"/"coordinate"/"point"/"external", or None (auto)
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
    if raw not in KINDS:
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


def _parse_anchor(anchor_node) -> TreeAnchor:
    """(anchor (ref "...")) -> ref anchor; (anchor (origin)) -> origin anchor.
    Anything else is fatal. ref is NOT validated for uniqueness against
    nodes — an anchor is a base, not something the tree places (rule 2)."""
    if child(anchor_node, "origin") is not None:
        return TreeAnchor(ref=None, is_origin=True)
    ref = atom(anchor_node, "ref")
    if ref is None:
        _fatal(_("anchor must be either (ref \"...\") or (origin)"))
    return TreeAnchor(ref=ref, is_origin=False)


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
    trees: list[Tree] = []

    for tree_node in tree_nodes:
        name = atom(tree_node, "name")
        if name is None:
            _fatal(_("{path}: a tree is missing a (name ...)")
                   .format(path=path))
        name = sval(name)
        if name in seen_names:
            _fatal(_("{path}: duplicate tree name {name!r} — tree names must be "
                     "unique within one file").format(path=path, name=name))
        seen_names.add(name)

        anchor_node = child(tree_node, "anchor")
        if anchor_node is None:
            _fatal(_("{path}: tree {name!r} is missing an (anchor ...)")
                   .format(path=path, name=name))

        top_nodes = children(tree_node, "node")
        trees.append(Tree(
            name=name,
            anchor=_parse_anchor(anchor_node),
            nodes=[_parse_node(n, seen_refs, f"{path}:tree {name!r}") for n in top_nodes],
        ))

    return trees


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


def _anchor_to_sexp(anchor: TreeAnchor) -> list:
    """(ref "...") for a ref anchor, (origin) for an origin anchor."""
    if anchor.is_origin:
        return [sym("anchor"), [sym("origin")]]
    return [sym("anchor"), [sym("ref"), anchor.ref]]


def _tree_to_sexp(tree: Tree) -> list:
    """Serialize one Tree (name, anchor, top-level nodes)."""
    out: list = [sym("tree"), [sym("name"), tree.name]]
    out.append(_anchor_to_sexp(tree.anchor))
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
