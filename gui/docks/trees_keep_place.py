# gui/docks/trees_keep_place.py
"""A tree that loses its LAST module marker keeps the place that marker gave it.

plan_2026_11_15_embedded_tree_redraw_through_module, п. 8 as DECIDED on 19.11 (Р4): no question
anywhere — the place is written into the tree's OWN anchor silently, in the dock's buffer, and ONE
Log line says what changed. Denis, 17.11: «таких предупреждений нигде, кроме как в логе быть не
должно». Without this a tree that nobody embeds any more jumps back to its old anchor (measured on
the STM32 profile: `power` would move from main's (6, −64) back to `Origin` + (5, −60), i.e.
(−1, +4) mm).

Why the anchor CAN express it: the anchor's kind never changes (point / ref / role stay), only its
shift and the tree's own rotation. Both sides are reduced to the SAME stage — the marker's pose
BEFORE the embedded tree's inner point is inverted — so the tree's pivot cancels and the shift is
one vector: the owner's own shift plus the marker's xy turned by the owner's angle.

Pure arithmetic when both anchors share one point (on the STM32 profile both are `Origin`). When
they do not (a ref anchor against a role anchor, …) the place needs the LIVE board to be read, and
this path does not read it: the anchor is left untouched and ONE yellow line says so, exactly as
the plan requires — no board, no guess, and never the silent jump.

Nesting (п. 8 last bullet): the owner may itself be embedded. The marker's place is then expressed
in the frame it had BEFORE the deletion, which is what `placement_chain` gives while the marker is
still in the buffer — the callers collect the lost markers BEFORE they detach anything.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.i18n import _
from kicadstamp.utils.units import MM

from .trees_embedding import placements

logger = logging.getLogger(__name__)

__all__ = [
    "keep_places_after_ref_change",
    "keep_places_after_removal",
    "marker_place",
    "module_markers_in",
]


def module_markers_in(nodes) -> list:
    """Every kind=="module" node of `nodes` and their subtrees — what a deletion takes away."""
    found: list = []
    stack = list(nodes or ())
    while stack:
        node = stack.pop()
        if node.kind == "module":
            found.append(node)
        stack.extend(node.children or ())
    return found


def _same_place_point(own, host) -> bool:
    """True when the tree's own anchor and the owner's resolve to the SAME point, so the point
    itself cancels and the place can be computed without the board."""
    if own is None or host is None:
        return False
    if own.is_self or host.is_self or own.role or host.role or own.ref or host.ref:
        return False                     # three live kinds — the board is the only answer
    if own.is_origin and host.is_origin:
        return True
    return bool(own.point) and own.point == host.point


def marker_place(tree, marker, owner) -> Optional[tuple[tuple[float, float], float]]:
    """`((shift_x_mm, shift_y_mm), rotation)` that makes `tree`'s OWN anchor land exactly where
    `marker` in `owner` put it — or None when the two anchors do not share a point.

    The shift is stored the way a point/origin anchor reads it: BOARD-axis mm (its own angle is
    None, so `anchor_shift_offset_nm` leaves it in board axes). The rotation is the tree's own,
    i.e. `owner.rotation + marker.rotation + tree.rotation` — the frame the marker gave, with the
    tree's inner angle kept on top."""
    own, host = getattr(tree, "anchor", None), getattr(owner, "anchor", None)
    if not _same_place_point(own, host):
        return None
    owner_shift = getattr(host, "shift_xy", None) or (0.0, 0.0)
    xy = getattr(marker, "xy", None) or (0.0, 0.0)
    turned = rotate_local_offset(xy[0], xy[1], owner.rotation)
    dx, dy = turned.x / MM, turned.y / MM
    shift = (round(owner_shift[0] + dx, 6) + 0.0, round(owner_shift[1] + dy, 6) + 0.0)
    rotation = round(owner.rotation + marker.rotation + tree.rotation, 6) + 0.0
    return shift, rotation


def _anchor_kind(anchor) -> str:
    """How the line names the anchor's kind — the SAME words the anchor form's modes use."""
    if anchor is None:
        return "?"
    if getattr(anchor, "is_origin", False):
        return "origin"
    if getattr(anchor, "point", None):
        return "point '{name}'".format(name=anchor.point)
    if getattr(anchor, "role", None):
        return "role '{role}'".format(role=anchor.role)
    if getattr(anchor, "is_self", False):
        return "self"
    return "ref '{ref}'".format(ref=getattr(anchor, "ref", None))


def keep_place(dock, tree, marker, owner) -> bool:
    """Write the marker's place into `tree`'s own anchor (BUFFER only) and say it in ONE line.

    False (and one yellow line) when the place cannot be computed without the board: the anchor is
    then NOT touched, and the line says the tree will stand from its own anchor."""
    values = marker_place(tree, marker, owner)
    if values is None:
        logger.warning(_(
            "Tree {tree!r} is no longer placed by module {marker!r} in {owner!r} — its own anchor "
            "and the owner's do not share a point, so the place was NOT moved; the tree now "
            "stands from its own anchor").format(
                tree=getattr(tree, "name", tree), marker=getattr(marker, "ref", marker),
                owner=getattr(owner, "name", owner)))
        return False
    shift, rotation = values
    old = getattr(getattr(tree, "anchor", None), "shift_xy", None)
    old_shift = "{:.3f}, {:.3f}".format(old[0], old[1]) if old else _("none")
    old_rotation = tree.rotation
    kind = _anchor_kind(tree.anchor)
    tree.anchor = dataclasses.replace(tree.anchor, shift_xy=shift)
    tree.rotation = rotation
    dock._mark_dirty()
    logger.info(_(
        "Tree {tree!r} is no longer placed by module {marker!r} in {owner!r}: its anchor now keeps "
        "that place ({kind} + shift ({x:.3f}, {y:.3f}), rotation {rot:.1f}°; was shift ({was}), "
        "rotation {old_rot:.1f}°)").format(
            tree=tree.name, marker=marker.ref, owner=owner.name, kind=kind,
            x=shift[0], y=shift[1], rot=rotation, was=old_shift, old_rot=old_rotation))
    return True


def keep_places_after_removal(dock, lost) -> int:
    """`lost` = [(marker node, owner tree)] collected BEFORE the detach; writes the place for
    every tree whose LAST marker went away and returns how many anchors were written.

    «Пока остаётся другой маркер — ничего не предлагать» (п. 8): a tree that some other marker
    still places is skipped, and so is a marker whose tree no longer exists."""
    written = 0
    for marker, owner in lost:
        target = next((t for t in dock._trees if t.name == marker.ref), None)
        if target is None:
            continue
        if placements(dock._trees, target):
            continue                     # still placed somewhere else — nothing to keep
        written += 1 if keep_place(dock, target, marker, owner) else 0
    return written


def keep_places_after_ref_change(dock, node, before, owner) -> int:
    """A marker whose Ref was edited onto ANOTHER name stops placing its old tree (п. 8: «правка
    Ref маркера на другое имя — дерево перестаёт быть встроенным»).

    `before` is the node's `(kind, ref)` read BEFORE the form applied; `owner` is the tree the
    node lives in (the form's owner, which the node itself does not name)."""
    if before is None or before[0] != "module" or owner is None:
        return 0                         # nothing to compute the place in
    if node.kind == "module" and node.ref == before[1]:
        return 0                         # the ref did not change
    target = next((t for t in dock._trees if t.name == before[1]), None)
    if target is None or placements(dock._trees, target):
        return 0
    return 1 if keep_place(dock, target, node, owner) else 0
