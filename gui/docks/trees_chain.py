# gui/docks/trees_chain.py
"""The frame a tree's content lives in — ONE composition of the marker chain, Qt-free.

Two callers need the same answer and must not grow two formulas (Ф1 and Ф2 of the 19.11
acceptance of plan_2026_11_15_embedded_tree_redraw_through_module):

* `trees_keep_place` — a tree that loses its LAST marker has to keep the place that marker gave it,
  and when the OWNER is itself embedded that place lives in the OUTER owner's frame, not in the
  owner's own anchor (Ф1: measured up to (−60.000, +35.000) mm on a turned host);
* `trees_base_pose` — a mount node inside an embedded tree hangs from a role the tree itself
  places, so its base is the tree's CONTENT base, and that base has to be the MODULE's (Ф2: the
  live HP-Channel defect moved `spoke_p15v_switcha` by 68.007 mm, exactly the marker).

`chain_frame` is the PURE half: it composes the shifts and rotations from the topmost free tree
down the chain of markers (outermost first), and answers None the moment the chain cannot be
expressed without the board — a second marker at any level (which place to take is unknown), an
anchor that is not a plain point/origin, or two anchors that do not share their point. None always
means "do not guess", never "fall back to the tree's own anchor".

`placed_content_base` (added with Ф2) is the LIVE half: the base a layout of the tree must start
from, taken from the very `module_bases` walk the Redraw uses.
"""
from __future__ import annotations

import logging
from typing import Optional

from kicadstamp.domain.geometry import Vector2
from kicadstamp.tree_position import (node_position, tree_layout_base,
                                      layout_tree_from_base)
from kicadstamp.utils.units import MM

from .trees_embedding import Placement, module_nodes, placement_chain, placements

logger = logging.getLogger(__name__)

__all__ = [
    "chain_frame",
    "chain_root",
    "placed_content_base",
]


def _by_name(trees, name):
    return next((t for t in (trees or ()) if t.name == name), None)


def _point_like(anchor) -> bool:
    """True for the two anchor kinds whose own shift is a plain BOARD-axis vector: an origin and a
    named point. Kind `self`/`role`/`ref` resolve against the live board, so nothing about them can
    be composed without reading it."""
    if anchor is None:
        return False
    if getattr(anchor, "is_self", False) or getattr(anchor, "role", None):
        return False
    if getattr(anchor, "ref", None):
        return False
    return bool(getattr(anchor, "is_origin", False) or getattr(anchor, "point", None))


def _same_point(one, other) -> bool:
    """Both anchors resolve to the SAME point, so the point itself cancels out of every
    difference. Two different named points may of course coincide on the board, but proving it
    needs a read — this function refuses to assume it."""
    if not (_point_like(one) and _point_like(other)):
        return False
    if getattr(one, "is_origin", False) and getattr(other, "is_origin", False):
        return True
    return bool(getattr(one, "point", None)) and one.point == getattr(other, "point", None)


def _marker_node(trees, step: Placement):
    owner = _by_name(trees, step.owner)
    if owner is None:
        return None
    return next((n for n in module_nodes(owner) if n.ref == step.marker), None)


def chain_root(trees, tree):
    """The topmost tree of `tree`'s chain — the one nobody places (or `tree` itself when nobody
    places it, or None when the chain runs into a buffer cycle)."""
    chain = placement_chain(trees, tree)
    return _by_name(trees, chain[-1].owner) if chain else tree


def chain_frame(trees, tree) -> Optional[tuple[tuple[float, float], float]]:
    """((shift_x_mm, shift_y_mm), rotation_deg) of `tree`'s CONTENT frame, in the axes of the point
    its own anchor and the chain's root share — or None when that cannot be said without the board.

    Composition, outermost first (the same order the multi-level Redraw activates):

        pos = root.shift; rot = root.rotation
        for each marker on the way down:  pos += rotate(marker.xy, rot); rot += marker.rotation

    For a tree nobody places this is simply its own anchor (`shift`, `rotation`) — the historic
    answer, so every existing caller keeps its numbers.
    """
    root = chain_root(trees, tree)
    if root is None:
        return None
    if len(placements(trees, tree)) > 1:
        return None                      # this very tree has two markers: which place is unknown
    if not _same_point(getattr(tree, "anchor", None), getattr(root, "anchor", None)):
        return None
    chain = placement_chain(trees, tree)
    pos = tuple(getattr(root.anchor, "shift_xy", None) or (0.0, 0.0))
    rot = float(getattr(root, "rotation", 0.0) or 0.0)
    for step in reversed(chain):
        owner = _by_name(trees, step.owner)
        if owner is None:
            return None
        if len(placements(trees, owner)) > 1:
            return None                  # two markers on a host: which place is unknown
        marker = _marker_node(trees, step)
        if marker is None:
            return None
        base = Vector2.from_xy(int(round(pos[0] * MM)), int(round(pos[1] * MM)))
        landed = node_position(marker, base, rot)
        pos = (landed.x / MM, landed.y / MM)
        rot += float(getattr(marker, "rotation", 0.0) or 0.0)
    return (round(pos[0], 6) + 0.0, round(pos[1], 6) + 0.0), round(rot, 6) + 0.0


def placed_content_base(trees, cfg, adapter, sheet_names, tree, *, snapshot=None):
    """(position_nm, rotation_deg) the tree's content is laid out from — the LIVE half.

    Nobody places the tree → `tree_layout_base` (its own anchor, exactly as before). Otherwise the
    EFFECTIVE base of the marker, taken from `module_bases` of ONE layout walk from the chain's
    root: `layout_tree_from_base` hands out both poses at the one place where both are known, so
    the numbers here are what the Redraw uses BY CONSTRUCTION, not a second composition.

    Raises ValidationError when the chain cannot be laid out or a level has two markers — callers
    turn that into their own named refusal (a Read warning, a Log line and no write). There is
    never a fallback to the tree's own anchor: that fallback IS the defect Ф2 removed."""
    from kicadstamp.exceptions import ValidationError
    from kicadstamp.i18n import _

    forest = {t.name: t for t in (trees or ())}
    chain = placement_chain(trees, tree)
    if not chain:
        return tree_layout_base(adapter, cfg, tree, sheet_names, forest, snapshot=snapshot)
    first = placements(trees, tree)
    if len(first) > 1:
        from .trees_embedding import several_markers_line
        raise ValidationError(several_markers_line(tree.name, first))
    root = _by_name(trees, chain[-1].owner)
    if root is None:
        raise ValidationError(_(
            "tree {tree!r}: its module chain starts at {name!r}, which is not in this working "
            "set — nothing is measured against the tree's own anchor").format(
                tree=tree.name, name=chain[-1].owner))
    for step in chain:
        owner = _by_name(trees, step.owner)
        if owner is not None and len(placements(trees, owner)) > 1:
            from .trees_embedding import several_markers_line
            raise ValidationError(several_markers_line(owner.name, placements(trees, owner)))
    base_pos, base_rot = tree_layout_base(adapter, cfg, root, sheet_names, forest,
                                          snapshot=snapshot)
    bases: dict = {}
    layout_tree_from_base(root, base_pos, base_rot, forest, adapter=adapter, cfg=cfg,
                          sheet_names=sheet_names, snapshot=snapshot, module_bases=bases)
    entry = bases.get(chain[0].marker)
    if entry is None:
        raise ValidationError(_(
            "tree {tree!r}: the layout of {root!r} did not reach the marker {marker!r} — nothing "
            "is measured against the tree's own anchor").format(
                tree=tree.name, root=root.name, marker=chain[0].marker))
    _name, _marker_pos, _marker_rot, eff_pos, eff_rot = entry
    return eff_pos, eff_rot
