# kicadstamp/tree_plan_warnings.py
"""The two facts a curated-redraw WARNING needs before it can be honest, kept out
of tree_position.py so that giant does not grow (plan_2026_10_20_cells_save_keeps_
trees_cfg_fresh, Ч1-3: the file was 1930 lines and must not get longer).

`anchor_base_label` — how a warning NAMES the tree's own anchor (moved here
verbatim from tree_position, 2026-10-20; tree_position re-exports it, so the
existing import path and its test keep working).

`parent_pose_comes_from_plan` — whether the redraw takes THIS node's pose from
the tree plan instead of from the board. That distinction is what makes
`curated_redraw_plan`'s "will be redrawn from the current position of X" honest:
for a kind "placement" record (an Entity) and for an INTERNAL mount the pose IS
the plan (tree_position._internal_mount_base reads only the pad's shape), so the
old wording promised a live position that does not exist (found live 20.10).

`plain_tree_by_name` — the one {cfg.trees} lookup both this module and
gui/docks/tree_place_drift use: the mount resolver wants the PLAIN Tree, never
the linked wrapper.
"""
from __future__ import annotations

from .i18n import _
from .trees import resolve_internal_mount_match

__all__ = ["anchor_base_label", "inline_anchor_override_warning",
           "parent_outside_selection_warning", "parent_pose_comes_from_plan",
           "plain_tree_by_name"]


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


def parent_pose_comes_from_plan(linked_node, tree_name, cfg) -> bool:
    """True when the curated redraw lays the nodes under THIS node from the tree
    PLAN rather than from where the board says this node stands.

    kind "placement" (an Entity) — its "live" resolution IS the tree
    (`resolve_entity_live_position`: an Entity carries no position of its own).
    kind "mount" — an INTERNAL mount (the role belongs to a cell THIS tree
    places) computes its base from the tree's layout and the cell's stored
    geometry, reading no live pose of the placing component; a LIVE mount (a role
    outside the tree) is resolved from the board and is NOT plan-borne.
    Everything else (clone / coordinate / external) is read live.

    `cfg` may be None (a caller that has no config in hand): the mount case then
    answers False, i.e. the historical wording, never a wrong claim."""
    node = getattr(linked_node, "node", None)
    if node is None:
        return False
    if getattr(node, "kind", None) == "mount":
        if cfg is None:
            return False
        plain = plain_tree_by_name(cfg, tree_name)
        if plain is None:
            return False
        return resolve_internal_mount_match(cfg, plain, node) is not None
    return getattr(getattr(linked_node, "record", None), "kind", None) == "placement"


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
