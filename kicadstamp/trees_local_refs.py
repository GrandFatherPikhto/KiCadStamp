# kicadstamp/trees_local_refs.py
"""THE local-name rule of ONE tree — one owner of the rule AND of its wording.

A `mount` / `copper` / `component` node's ref is a NAME local to its tree, not a
config record (plan §Y.1.3 for a mount, plan_2026_09_16_copper_node_order_and_
container P.2.4 for a copper container, plan Э3/Т3.2 for a component node). The
loader therefore checks it per tree, and BOTH checks are load-time fatals:

  * the name is unique among the refs of ITS OWN kind in the tree;
  * it does not collide with ANY other node's ref of the same tree — in EITHER
    direction: a positioned node may not take a local name, and a local node may
    not take a positioned node's name. Otherwise the tree's later stages (its own
    inner point, the copper container lookup) could not say which node is meant.

`validate_local_refs` is the load-time form, called by BOTH node parsers; the
three `local_*` helpers are the SAME rule for ONE ref and answer with the very
same text instead of raising, so the node form can refuse exactly what the loader
would refuse (plan_2026_11_14_used_refs_by_uuid §1 — before it, the GUI let a
mount and an entity of one name into one tree and the next `load_config` died on
"collide with a positioned node").

Moved out of `kicadstamp/trees.py` (which may only shrink) with thin aliases left
behind: `trees._LOCAL_REF_MESSAGES` and `trees._validate_local_refs` still exist,
so every caller and docstring keeps working.
"""
from __future__ import annotations

from typing import Optional

from .exceptions import ValidationError
from .i18n import _

#: {local kind: (not_unique, collides)} — the two texts of the rule.
#: The "mount" pair is VERBATIM what these messages have always been (they are
#: user-facing and must not drift); "copper" gets the same two checks worded for
#: a container, "component" for a node that places a live component. The KEYS are
#: the complete list of local-name kinds the rule applies to.
LOCAL_REF_MESSAGES: dict[str, tuple[str, str]] = {
    "mount": (
        _("tree {tree!r}: mount node ref(s) {refs} are not unique — a "
          "mount node's ref must identify exactly one node in the tree"),
        _("tree {tree!r}: mount node ref(s) {refs} collide with a "
          "positioned node of the same tree — mount refs must be "
          "distinct so a node can be named unambiguously"),
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


def _walk(nodes):
    """Every node of a tree, depth-first. Deliberately local (3 lines): importing
    `trees._walk_nodes` would make this module and `trees` import each other, and
    only a RULE belongs in one place, not a generator."""
    for node in nodes:
        yield node
        yield from _walk(node.children)


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


def local_name_refusal(tree_name: str, ref: str, nodes) -> Optional[str]:
    """The loader's refusal for a node of ANY OTHER kind being named `ref` in this
    tree — None when no local node carries that name. The text is the SAME one
    `validate_local_refs` would raise."""
    kind = local_ref_kind(nodes, ref)
    if kind is None:
        return None
    return LOCAL_REF_MESSAGES[kind][1].format(tree=tree_name, refs=ref)


def local_ref_refusal(kind: Optional[str], tree_name: str, ref: str, nodes, *,
                      own=None) -> Optional[str]:
    """The loader's refusal for a node OF A LOCAL KIND `kind` being named `ref` in
    this tree — the REVERSE direction (a mount renamed onto a positioned node, or
    onto another mount of its own kind). None for a non-local kind, an empty ref,
    or a name that breaks nothing.

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
    if any(n is not own and n.kind != kind and n.ref == ref for n in _walk(nodes)):
        return collides.format(tree=tree_name, refs=ref)
    return None


def validate_local_refs(nodes: list, tree_name: str) -> None:
    """Per-tree ref uniqueness for the LOCAL-NAME kinds — the load-time form of
    the rule, run by both node parsers. Both checks are reported separately (they
    mean different things) and each is a fatal.

    ONE walk per kind, with per-kind texts, so the mount messages stay verbatim
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
        collisions = sorted(local_refs & placed_refs)
        if collisions:
            raise ValidationError(collides.format(tree=tree_name,
                                                  refs=", ".join(collisions)))
