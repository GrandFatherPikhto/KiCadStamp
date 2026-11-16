# kicadstamp/trees_local_refs.py
"""THE local-name rule of ONE tree — one owner of the rule AND of its wording.

A `mount` / `copper` / `component` node's ref is a NAME local to its tree, not a
config record (plan §Y.1.3 for a mount, plan_2026_09_16_copper_node_order_and_
container P.2.4 for a copper container, plan Э3/Т3.2 for a component node). The
loader therefore checks it per tree, and the checks are load-time fatals:

  * the name is unique among the refs of ITS OWN kind in the tree — for EVERY
    local kind;
  * it does not collide with ANY positioned node's ref of the same tree — only
    for `LOCAL_COLLISION_KINDS`. This second check was LIFTED for a `mount` on
    16.11.2026 (plan_2026_11_14_mount_and_record_may_share_a_name): a mount is a
    point of reference, a positioned node PLACES a record, and the two are told
    apart by kind and uuid rather than by name — Denis's word, and the reason 24
    mounts had to be renamed for a rule that no longer exists. `copper` and
    `component` keep the check until their own answer is given.

`validate_local_refs` is the load-time form, called by BOTH node parsers; the
three `local_*` helpers are the SAME rule for ONE ref and answer with the very
same text instead of raising, so the node form can refuse exactly what the loader
would refuse (plan_2026_11_14_used_refs_by_uuid §1 — before it, the GUI let a
mount and an entity of one name into one tree and the next `load_config` died on
"collide with a positioned node"; since the mount half of that ban is gone, the
same door now refuses a COPPER container doing it).

Moved out of `kicadstamp/trees.py` (which may only shrink) with thin aliases left
behind: `trees._LOCAL_REF_MESSAGES` and `trees._validate_local_refs` still exist,
so every caller and docstring keeps working.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .exceptions import ValidationError
from .i18n import _

#: {local kind: (not_unique, collides)} — the texts of the rule. The KEYS are the
#: complete list of local-name kinds the rule applies to, and a `None` SECOND
#: element means "this kind has no collision ban": the table IS the rule, so
#: lifting one is a one-word edit here and there is never a second list to drift.
#: The "mount" uniqueness text is VERBATIM what it has always been (user-facing,
#: must not drift); its collision text is gone with the ban (16.11.2026).
LOCAL_REF_MESSAGES: dict[str, tuple[str, Optional[str]]] = {
    "mount": (
        _("tree {tree!r}: mount node ref(s) {refs} are not unique — a "
          "mount node's ref must identify exactly one node in the tree"),
        None,   # a mount MAY share its name with a positioned node (16.11.2026)
    ),
    "copper": (
        _("tree {tree!r}: copper node ref(s) {refs} are not unique — a "
          "copper container's ref must identify exactly one node in the tree"),
        _("tree {tree!r}: copper node ref(s) {refs} collide with a "
          "positioned node of the same tree — copper refs must be "
          "distinct so a node can be named unambiguously"),
    ),
    "component": (
        _("tree {tree!r}: component node ref(s) {refs} are not unique — a "
          "component node's ref is the name the redraw applies it by and must "
          "identify exactly one node in the tree"),
        _("tree {tree!r}: component node ref(s) {refs} collide with a "
          "positioned node of the same tree — component node refs must be "
          "distinct so a node can be named unambiguously"),
    ),
}

#: The kinds whose local name may not equal a POSITIONED node's ref — read off the
#: table above, so a lifted ban cannot leave a stale second list behind.
LOCAL_COLLISION_KINDS = frozenset(kind for kind, (_u, c)
                                  in LOCAL_REF_MESSAGES.items() if c)

#: The kinds whose ref is a local NAME and never a record — exactly the names the
#: table above rules on. Read by the redraw's selection sets, which must collect
#: RECORDS: since a name may equal a record's (16.11.2026) a local node's ref
#: would otherwise pull in somebody else's record. A `module` is deliberately NOT
#: here: its ref is another tree's NAME and is the marker that ACTIVATES that
#: tree's content in a redraw.
NAME_ONLY_KINDS = frozenset(LOCAL_REF_MESSAGES)


def _walk(nodes):
    """Every node of a tree, depth-first. Deliberately local (3 lines): importing
    `trees._walk_nodes` would make this module and `trees` import each other, and
    only a RULE belongs in one place, not a generator."""
    for node in nodes:
        yield node
        yield from _walk(node.children)


def node_named_for_tree_ref(nodes, ref: str):
    """The node a TREE-LEVEL ref (a `pivot-ref`, a self anchor's `(ref ...)`)
    names: the RECORD of that name, or — only when no record carries it — the
    LOCAL node. Both refs mean the RECORD, and a `mount` may share a record's
    name (16.11.2026), so "the last one in the file wins" would make a tree's
    readability depend on node ORDER (plan_2026_11_14_mount_and_record_may_share_
    a_name §1). Lives HERE, next to the rule that says which kinds are names
    rather than records — and `kicadstamp/trees.py` (which may not grow) calls it."""
    fallback = None
    for node in _walk(nodes):
        if node.ref != ref:
            continue
        if node.kind not in NAME_ONLY_KINDS:
            return node
        fallback = fallback or node
    return fallback


def local_ref_kind(nodes, ref: str) -> Optional[str]:
    """The LOCAL kind of the first node carrying `ref` as a local name, or None.
    Used for the FORWARD direction: a record whose name a local node already
    carries of this tree."""
    if not ref:
        return None
    for node in _walk(nodes):
        if node.ref == ref and node.kind in LOCAL_REF_MESSAGES:
            return node.kind
    return None


def free_node_ref(kind: Optional[str], tree_name: str, ref: str, nodes,
                  taken: Iterable[str] = (), start: int = 2) -> str:
    """`ref` if it may stand in `tree_name`, else the first `ref_<n>` (from
    `start`) that may.

    THE answer every door needs before it puts a node into a tree's buffer
    (plan_2026_11_10_extra_copper_by_selection §0 п.6): a generated record name
    must not collide with a LOCAL name of that tree (a mount, a copper container),
    or the next `load_config` refuses the file. A door calls THIS and never
    compares names itself — when the rule changes (a kind's collision check is
    lifted, plan_2026_11_14_mount_and_record_may_share_a_name), every door follows
    without a line of its own.

    `taken` — names held OUTSIDE the tree's own buffer and just as unusable: the
    config's record names for the re-read (§0.1), the "used" set for the node
    dialog. Without it this helper picked `n__a__b_2` for a record that a
    config ALREADY had under that name, and the write dropped the copper while
    the report said "added". Both bans are ONE decision, so both live here.

    `start` keeps each door's historical numbering: a record's name counts from
    `_2` (`internode_copper.generate_trace_name`, the suffix a capture has always
    used) and a node's auto-number from `_1` (phase 5.5, `TreesDock._unique_ref`).
    """
    held = set(taken)

    def clashes(name: str) -> bool:
        if name in held:
            return True
        if kind in LOCAL_REF_MESSAGES:
            return local_ref_refusal(kind, tree_name, name, nodes) is not None
        return local_name_refusal(tree_name, name, nodes) is not None

    if not clashes(ref):
        return ref
    suffix = start
    while clashes(f"{ref}_{suffix}"):
        suffix += 1
    return f"{ref}_{suffix}"


def local_name_refusal(tree_name: str, ref: str, nodes) -> Optional[str]:
    """The loader's refusal for a node of ANY OTHER kind being named `ref` in this
    tree — None when no local node carries that name, or when that kind has no
    collision ban left (`mount` since 16.11.2026). The text is the SAME one
    `validate_local_refs` would raise."""
    kind = local_ref_kind(nodes, ref)
    if kind is None:
        return None
    collides = LOCAL_REF_MESSAGES[kind][1]
    if collides is None:
        return None
    return collides.format(tree=tree_name, refs=ref)


def local_ref_refusal(kind: Optional[str], tree_name: str, ref: str, nodes, *,
                      own=None) -> Optional[str]:
    """The loader's refusal for a node OF A LOCAL KIND `kind` being named `ref` in
    this tree: ALWAYS the uniqueness of its own kind, plus the REVERSE collision
    check where that kind still has one (a copper container named like a
    positioned node). None for a non-local kind, an empty ref, or a name that
    breaks nothing.

    `own` is the node being EDITED: it is not "another node", so editing a mount
    without renaming it never trips the uniqueness check."""
    pair = LOCAL_REF_MESSAGES.get(kind) if kind else None
    if pair is None or not ref:
        return None
    not_unique, collides = pair
    same = [n for n in _walk(nodes)
            if n is not own and n.kind == kind and n.ref == ref]
    if same:
        return not_unique.format(tree=tree_name, refs=ref)
    if collides is None:
        return None
    if any(n is not own and n.kind != kind and n.ref == ref for n in _walk(nodes)):
        return collides.format(tree=tree_name, refs=ref)
    return None


def validate_local_refs(nodes: list, tree_name: str) -> None:
    """Per-tree ref checks for the LOCAL-NAME kinds — the load-time form of the
    rule, run by both node parsers. The uniqueness check ALWAYS runs; the
    collision check only where the table still carries its text
    (`LOCAL_COLLISION_KINDS`). Both are reported separately (they mean different
    things) and each is a fatal.

    ONE walk per kind, with per-kind texts, so the mount message stays verbatim
    while copper gets wording that says "container". "module" is deliberately
    NOT here: a module's ref is another tree's NAME and the "same tree embedded
    twice under one parent" case is guarded by link_trees' own duplicate rule.
    """
    for kind, (not_unique, collides) in LOCAL_REF_MESSAGES.items():
        counts: dict[str, int] = {}
        local_refs: set[str] = set()
        placed_refs: set[str] = set()
        for n in _walk(nodes):
            if n.kind == kind:
                counts[n.ref] = counts.get(n.ref, 0) + 1
                local_refs.add(n.ref)
            else:
                placed_refs.add(n.ref)
        duplicates = sorted(ref for ref, count in counts.items() if count > 1)
        if duplicates:
            raise ValidationError(not_unique.format(tree=tree_name,
                                                    refs=", ".join(duplicates)))
        if collides is None:
            continue
        collisions = sorted(local_refs & placed_refs)
        if collisions:
            raise ValidationError(collides.format(tree=tree_name,
                                                  refs=", ".join(collisions)))
