# kicadstamp/tree_plan_warnings.py
"""The facts a curated-redraw WARNING needs before it can be honest, kept out of
tree_position.py so that giant does not grow (plan_2026_10_20_cells_save_keeps_
trees_cfg_fresh, Ч1-3: the file was 1930 lines and must not get longer).

`anchor_base_label` — how a warning NAMES the tree's own anchor (moved here
verbatim from tree_position, 2026-10-20; tree_position re-exports it, so the
existing import path and its test keep working).

`plan_pose_subject` — THE ONE HOST of "does the redraw take THIS node's pose
from the tree plan instead of from the board". That distinction is what makes
the parent warning honest: for a kind "placement" record (an Entity) and for an
INTERNAL mount the pose IS the plan (tree_position._internal_mount_base reads
only the pad's shape), so the old wording promised a live position that does not
exist (found live 20.10). Both planners (single tree AND forest) and
gui/docks/tree_place_drift ask HERE, so they cannot disagree again — they DID
(acceptance 21.10), and the forest planner even spelled the old wording out by
hand, so it promised "current position" for a pose that comes from the tree.

`plain_tree_by_name` — the one {cfg.trees} lookup this module and
gui/docks/tree_place_drift use: the mount resolver wants the PLAIN Tree, never
the linked wrapper.

`top_level_anchor_labels` — {top-level ref: label} for the warning of a node
whose base is its tree's own anchor (Ф10); moved here from tree_position so that
giant did not grow (≤1927, acceptance ratchet).
"""
from __future__ import annotations

from .i18n import _
from .trees import resolve_internal_mount_match

__all__ = ["anchor_base_label", "inline_anchor_override_warning",
           "parent_outside_selection_warning", "plan_pose_subject",
           "plain_tree_by_name", "top_level_anchor_labels"]


def parent_outside_selection_warning(ref: str, parent_label: str,
                                     from_plan: bool) -> str:
    """The ONE wording of "this node's parent/base is not in the selection".

    `from_plan` decides the promise: for a plan-borne base (an Entity, an
    internal mount) the child is laid from the TREE, so saying "current position"
    was false; everything else IS read from the board and keeps the original
    text byte for byte (the historical warning and its cells)."""
    if from_plan:
        return _("Node {ref!r} will be redrawn from {parent!r}'s place in the "
                 "tree (not in selection) — that pose comes from the tree, not "
                 "from the board, so if {parent!r} really stands elsewhere, "
                 "{ref!r} will land off by that difference").format(
                     ref=ref, parent=parent_label)
    return _("Node {ref!r} will be redrawn from the current position of "
             "{parent!r} (not in selection); if {parent!r} moved, {ref!r} will "
             "land from the old point").format(ref=ref, parent=parent_label)


def inline_anchor_override_warning(ref: str, field: str) -> str:
    """The informational line for a selected node whose record carries its own
    position mechanism while this tree redraw overrides it TEMPORARILY."""
    return _("Node {ref!r} also has its own {field} — the regular (non-tree) "
             "Apply/Redraw keeps using it; this tree redraw moves it "
             "TEMPORARILY, without touching the record").format(
                 ref=ref, field=field)


def plain_tree_by_name(cfg, name):
    """The plain `Tree` of `cfg.trees` called `name`, or None. Never the linked
    wrapper: a mount's base is resolved from the tree's OWN layout + the forest
    by name, exactly as the redraw resolves it."""
    for tree in getattr(cfg, "trees", None) or []:
        if tree.name == name:
            return tree
    return None


def plan_pose_subject(cfg, linked_node, tree_name):
    """(ref, record) of the node whose PLAN pose lays the children of
    `linked_node`, or None when that pose is read from the board.

    kind "placement" (an Entity) — its "live" resolution IS the tree
    (`resolve_entity_live_position`: an Entity carries no position of its own).
    BOTH halves must say "placement": the node's kind AND the record's kind
    (link_trees derives one from the other, so a disagreement is a bug somewhere
    else and must not become a wrong warning here).

    kind "mount" — an INTERNAL mount (`_internal_mount_base` reads only the pad's
    SHAPE, the pose comes from the tree's own layout) yields the node that
    PLACES the anchored role: that placing node is the SUBJECT whose deviation
    matters, never the mount node itself. The record is not resolved here — this
    module only has the PLAIN tree, so a caller holding a forest index looks it
    up there. Whether that record's plan pose is READABLE is the caller's
    question, not this rule's. A LIVE mount (a role outside the tree) is
    resolved from the board and is NOT plan-borne.
    Everything else (clone / coordinate / external) is read live: the nodes below
    follow it, so there is nothing to warn about.

    `cfg` may be None (a caller with no config in hand — the forest planner
    before 21.10): the mount case then answers None, i.e. the historical wording,
    never a wrong claim.

    Raises whatever the mount classifier raises (a config-level fatal: ambiguity
    / module crossing / cycle). The caller decides how to report it: a warning
    check must neither swallow a config error silently nor break the redraw."""
    node = getattr(linked_node, "node", None)
    if node is None:
        return None
    kind = getattr(node, "kind", None)
    if kind == "placement":
        record = getattr(linked_node, "record", None)
        if getattr(record, "kind", None) != "placement":
            return None
        return (node.ref, record)
    if kind != "mount" or cfg is None:
        return None
    plain = plain_tree_by_name(cfg, tree_name)
    if plain is None:
        return None
    match = resolve_internal_mount_match(cfg, plain, node)
    if match is None:
        return None
    return (match.node.ref, None)


def anchor_base_label(linked_anchor) -> str:
    """How a warning NAMES a node's base when that base is the tree's own ANCHOR
    (plan_2026_09_18 Часть A, Ф10).

    A ref/external anchor is named by its ref — what the user typed. Every OTHER
    anchor used to be printed as the hard-coded "(origin)", whatever it really
    was, so a ROLE-anchored tree's top-level nodes announced "will be redrawn
    from the current position of '(origin)'" — a warning that read as "this tree
    is origin-anchored" and cost a wrong diagnosis (it is what made the plan
    assume (origin)). The label is a NAME, like a refdes, so it stays
    untranslated — exactly like the ref it replaces."""
    ref = linked_anchor.anchor.ref
    if ref:
        return ref
    if linked_anchor.is_origin:
        return "(origin)"
    anchor = linked_anchor.anchor
    if anchor.role:
        return f"(role {anchor.role!r})"
    if getattr(anchor, "point", None):
        return f"(point {anchor.point!r})"
    if getattr(anchor, "is_self", False):
        return "(self)"
    return "(origin)"


def top_level_anchor_labels(linked_trees) -> dict:
    """{top-level node ref: how its tree's ANCHOR is named} — the label the
    parent warning must print for a base that is the tree's own anchor (Ф10),
    since a role/point/self anchor is a live, floating base and printing
    "(origin)" for it read as "this tree is origin-anchored".

    Lived in tree_position._plan_forest until 21.10; moved here so the giant did
    not grow for the forest wording fix (acceptance: ≤1927 lines)."""
    labels: dict = {}
    for linked_tree in linked_trees:
        label = anchor_base_label(linked_tree.anchor)
        for linked_node in linked_tree.nodes:
            labels[linked_node.node.ref] = label
    return labels
