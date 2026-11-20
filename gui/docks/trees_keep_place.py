# gui/docks/trees_keep_place.py
"""A tree that loses its LAST module marker keeps the place that marker gave it.

plan_2026_11_15_embedded_tree_redraw_through_module, п. 8 as DECIDED on 19.11 (Р4): no question
anywhere — the place is written into the tree's OWN anchor silently, in the dock's buffer, and ONE
Log line says what changed. Denis, 17.11: «таких предупреждений нигде, кроме как в логе быть не
должно». Without this a tree that nobody embeds any more jumps back to its old anchor (measured on
the STM32 profile: `power` would move from main's (6, −64) back to `Origin` + (5, −60), i.e.
(−1, +4) mm).

WHY THE OWNER IS NOT ENOUGH (Ф1 of the 19.11 acceptance). The first version composed the place from
the OWNER's own anchor — `owner.anchor.shift_xy` and `owner.rotation`. That is right only while the
owner is a free tree: an owner that is itself placed stands where ITS marker puts it, and the shift
missed that entirely (measured on a turned host: (−60.000, +35.000) mm). The frame is therefore taken
from `trees_chain.chain_frame`, which composes the shifts and rotations down the whole chain of
markers, outermost first — the same composition the multi-level Redraw activates. It answers None
when the chain cannot be expressed purely (a second marker on some level, an anchor that is not a
plain point, two anchors that do not share their point): then the anchor is left alone and ONE yellow
line says so. Never a guess, never the silent jump.

TWO PHASES, because the chain has to be read BEFORE anything is detached: `lost_places()` computes
(the caller collects the markers before it detaches them, as the delete doors already did) and
`apply_lost_places()` writes afterwards — that is also where «пока остаётся другой маркер — ничего не
писать» can be answered honestly, because by then the buffer shows the truth.

Why the anchor CAN express the place: the anchor's kind never changes (point / ref / role stay), only
its shift and the tree's own rotation. Both sides are reduced to the SAME stage — the marker's pose
BEFORE the embedded tree's inner point is inverted — so the tree's pivot cancels and the shift is one
vector.

A GENERATED `tree_instances` copy keeps NOTHING (Ф4 of plan_2026_11_19_forest_index_keys_and_instances,
decision of 19.11 «вариант в»): its place lives in the DECLARATION, the dock never writes that
section (`_do_save` drops every generated tree), and a silent write into the copy would be lost by
the next expansion while the Log claimed success. So for an instance nothing is written and
`_mark_dirty` is not called: ONE yellow line carries the numbers to type into
Tools → Trees → Instances… instead. The sentence «its anchor now keeps that place» is never printed
for an instance.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from kicadstamp.domain.geometry import Vector2
from kicadstamp.i18n import _
from kicadstamp.tree_position import node_position
from kicadstamp.trees import anchor_from_dict
from kicadstamp.utils.units import MM

from .trees_chain import chain_frame, chain_root, _same_point
from .trees_embedding import placements

logger = logging.getLogger(__name__)

__all__ = [
    "apply_lost_places",
    "instance_line",
    "instance_of",
    "is_generated",
    "keep_places_after_ref_change",
    "keep_places_after_removal",
    "lost_places",
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


def marker_place(trees, tree, marker, owner) -> Optional[tuple[tuple[float, float], float]]:
    """`((shift_x_mm, shift_y_mm), rotation)` that makes `tree`'s OWN anchor land exactly where
    `marker` in `owner` put it — or None when that cannot be said without the board.

    The owner's frame comes from the CHAIN (`trees_chain.chain_frame`), so an owner that is itself
    embedded is placed by its own marker and not by its own anchor (Ф1). The tree's own anchor must
    share the chain's root point, otherwise the point itself would have to be read.

    The shift is stored the way a point/origin anchor reads it: BOARD-axis mm (its own angle is
    None, so `anchor_shift_offset_nm` leaves it in board axes). The rotation is the tree's own,
    i.e. the chain's angle + the marker's + the tree's, with the tree's inner angle kept on top."""
    own = getattr(tree, "anchor", None)
    root = chain_root(trees, owner)
    if root is None or not _same_point(own, getattr(root, "anchor", None)):
        return None
    frame = chain_frame(trees, owner)
    if frame is None:
        return None
    pos, rot = frame
    base = Vector2.from_xy(int(round(pos[0] * MM)), int(round(pos[1] * MM)))
    landed = node_position(marker, base, rot)
    shift = (round(landed.x / MM, 6) + 0.0, round(landed.y / MM, 6) + 0.0)
    rotation = round(rot + float(getattr(marker, "rotation", 0.0) or 0.0)
                     + float(getattr(tree, "rotation", 0.0) or 0.0), 6) + 0.0
    return shift, rotation


def instance_of(dock, tree):
    """The `TreeInstance` DECLARATION behind `tree`, or None.

    The dock's own rule when there is a dock (`TreesDock._instance_of`, the read-only index
    built from `cfg.tree_instances`), else that very index (`_instances`). A tree that came
    out of no expansion answers None and takes the ordinary write path."""
    seam = getattr(dock, "_instance_of", None)
    if callable(seam):
        try:
            return seam(tree)
        except Exception:      # noqa: BLE001 — a line never breaks a deletion
            return None
    return (getattr(dock, "_instances", None) or {}).get(getattr(tree, "name", None))


def is_generated(dock, tree) -> bool:
    """True for a buffer tree an expansion produced — `_generated` is STICKY, so it also
    answers for a copy whose declaration has already been deleted."""
    return getattr(tree, "name", None) in (getattr(dock, "_generated", None) or set())


def _declaration_place(inst) -> tuple[str, str, float]:
    """What the DECLARATION says now: (kind, «x, y» or the word «none», rotation).

    `TreeInstance.anchor` is the config's own anchor grammar, so it is parsed by the ONE
    parser (`trees.anchor_from_dict`) and named by the same words the anchor form uses. A
    declaration without an `anchor:` has no numbers at all — and the line says exactly
    that instead of inventing zeros."""
    raw = getattr(inst, "anchor", None)
    anchor = None
    if raw:
        try:
            anchor = anchor_from_dict(raw, getattr(inst, "name", "") or "")
        except Exception:      # noqa: BLE001 — a line never breaks a deletion
            anchor = None
    rotation = float(getattr(inst, "rotation", None) or 0.0)
    if anchor is None:
        return _("the template's own anchor"), _("none"), rotation
    shift = getattr(anchor, "shift_xy", None)
    shown = "{:.3f}, {:.3f}".format(shift[0], shift[1]) if shift else _("none")
    return _anchor_kind(anchor), shown, rotation


def instance_line(plan: "_LostPlace", inst) -> str:
    """The ONE yellow line an INSTANCE gets instead of a write (Р4, Ф4).

    It carries the place the marker gave the copy — the numbers a human types into
    Tools → Trees → Instances… — and what the declaration says today, so the sentence is
    actionable without a second look at the dialog. When the chain cannot be reduced
    without the board there are no numbers to carry, and the line says that instead."""
    tree, marker, owner = plan.tree, plan.marker, plan.owner
    where = dict(tree=tree.name, template=getattr(inst, "template", "?"),
                 marker=marker.ref, owner=owner.name)
    if plan.values is None:
        return _(
            "Tree {tree!r} is an instance of {template!r} and is no longer placed by module "
            "{marker!r} in {owner!r}: its place was NOT moved (the anchor chain does not reduce "
            "to one point) — and an instance keeps nothing by itself, so set its anchor in "
            "Tools → Trees → Instances… if the place matters").format(**where)
    shift, rotation = plan.values
    kind = _anchor_kind(tree.anchor)
    now_kind, now_shift, now_rotation = _declaration_place(inst)
    return _(
        "Tree {tree!r} is an instance of {template!r} and is no longer placed by module "
        "{marker!r} in {owner!r}: its place is NOT kept — to keep it, set the instance's anchor "
        "in Tools → Trees → Instances… to {kind} + shift ({x:.3f}, {y:.3f}), rotation "
        "{rotation:.1f}° (now: {now_kind} shift ({now_shift}), rotation {now_rotation:.1f}°)"
    ).format(**where, kind=kind, x=shift[0], y=shift[1], rotation=rotation,
             now_kind=now_kind, now_shift=now_shift, now_rotation=now_rotation)


def _generated_gone_line(plan: "_LostPlace") -> str:
    """The same situation for a copy whose declaration is ALREADY gone: there is nothing
    left to write a place into, and the sticky `_generated` mark is why it is not treated
    as an ordinary tree."""
    return _(
        "Tree {tree!r} came out of a `tree_instances` declaration that is gone and is no longer "
        "placed by module {marker!r} in {owner!r} — nothing is kept: a copy has no place of its "
        "own, and there is no declaration left to write one into").format(
            tree=plan.tree.name, marker=plan.marker.ref, owner=plan.owner.name)


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


@dataclasses.dataclass(frozen=True)
class _LostPlace:
    """ONE marker that went away: the tree it used to place, that marker, and the place (or None
    when the chain cannot be expressed purely)."""

    tree: object
    marker: object
    owner: object
    values: Optional[tuple]


def lost_places(dock, lost) -> list:
    """Compute the place for every (marker node, owner tree) of `lost`, BEFORE anything is detached.

    Nothing is written and nothing is logged here: a tree that some other marker still places is
    skipped by `apply_lost_places`, and a yellow line about it would be a lie. A marker whose tree
    no longer exists is dropped the same way."""
    plans = []
    for marker, owner in lost:
        target = next((t for t in dock._trees if t.name == getattr(marker, "ref", None)), None)
        if target is None:
            continue
        plans.append(_LostPlace(tree=target, marker=marker, owner=owner,
                                values=marker_place(dock._trees, target, marker, owner)))
    return plans


def _write_place(dock, plan: _LostPlace) -> bool:
    """The write half of ONE plan: the buffer, one Log line, nothing else.

    A GENERATED copy is the exception (Ф4): nothing is written, `_mark_dirty` is not called
    and the numbers go into the yellow line for `Tools → Trees → Instances…` — a copy has no
    place of its own, and `_do_save` would drop the write anyway."""
    tree, marker, owner = plan.tree, plan.marker, plan.owner
    inst = instance_of(dock, tree)
    if inst is not None:
        logger.warning(instance_line(plan, inst))
        return False
    if is_generated(dock, tree):
        logger.warning(_generated_gone_line(plan))
        return False
    if plan.values is None:
        logger.warning(_(
            "Tree {tree!r} is no longer placed by module {marker!r} in {owner!r} — its anchor "
            "chain does not reduce to one point (or a level has two markers), so the place was "
            "NOT moved; the tree now stands from its own anchor").format(
                tree=getattr(tree, "name", tree), marker=getattr(marker, "ref", marker),
                owner=getattr(owner, "name", owner)))
        return False
    shift, rotation = plan.values
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


def apply_lost_places(dock, plans) -> int:
    """Write every plan whose tree lost its LAST marker — called AFTER the detach, so the buffer
    already says whether another marker still places it (п. 8: then nothing is written)."""
    written = 0
    for plan in plans:
        if placements(dock._trees, plan.tree):
            continue                     # still placed somewhere else — nothing to keep
        written += 1 if _write_place(dock, plan) else 0
    return written


def keep_places_after_removal(dock, lost) -> int:
    """One-shot form for a caller whose removal has ALREADY happened (no chain to read before it):
    it still computes against the current buffer and then applies. The delete doors use the
    two-phase form instead, because there the chain must be read before the detach."""
    return apply_lost_places(dock, lost_places(dock, lost))


def keep_places_after_ref_change(dock, node, before, owner) -> int:
    """A marker whose Ref was edited onto ANOTHER name stops placing its old tree (п. 8: «правка
    Ref маркера на другое имя — дерево перестаёт быть встроенным»).

    `before` is the node's `(kind, ref)` read BEFORE the form applied; `owner` is the tree the node
    lives in. No two-phase here: the form has already rewritten the ref, so the buffer IS the state
    after the removal."""
    if before is None or before[0] != "module" or owner is None:
        return 0
    if node.kind == "module" and node.ref == before[1]:
        return 0                         # the ref did not change
    target = next((t for t in dock._trees if t.name == before[1]), None)
    if target is None or placements(dock._trees, target):
        return 0
    plan = _LostPlace(tree=target, marker=node, owner=owner,
                      values=marker_place(dock._trees, target, node, owner))
    return 1 if _write_place(dock, plan) else 0
