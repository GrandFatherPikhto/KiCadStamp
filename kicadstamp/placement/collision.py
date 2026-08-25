# kicadstamp/placement/collision.py

"""
* _radius_from_bbox – computes radius as half the diagonal.
* compute_radii – batch-requests radii for a list of footprints.
* footprints_overlap – checks overlap of two circles.
* check_collisions – main function, checks collisions between moving and non-moving components.
"""

import logging
import math

from ..domain.board import Footprint
from kipy.geometry import Vector2

from .commands import MoveCommand
from ..utils.units import MM
from ..i18n import _

logger = logging.getLogger(__name__)

DEFAULT_RADIUS_MM = 2.0  # fallback when bounding box is unavailable


def _radius_from_bbox(bbox) -> float:
    """Half the diagonal of a bounding box, in nanometres. None -> fallback radius."""
    if bbox is None:
        return DEFAULT_RADIUS_MM * MM
    return 0.5 * math.hypot(bbox.size.x, bbox.size.y)


def compute_radii(footprints: list[Footprint], adapter) -> dict[str, float]:
    """
    Computes radii (nm) for a list of footprints with ONE batch request through
    adapter.get_bounding_boxes(), instead of calling the non-existent
    fp.getBoundingBox()/fp.size (see below — this was the reason why
    previously a HARDCODED fallback radius of 2mm was ALWAYS used for
    absolutely all components, including large 4.7uF and IC1 itself).

    FIXED (2026-07-12): in the real kicad-python 0.7.1 API,
    FootprintInstance has neither .getBoundingBox() nor .size — only
    attributes, datasheet_field, definition, description_field, id,
    layer, locked, orientation, position, proto, reference_field,
    sheet_path, texts_and_fields, value_field (verified via dir()).
    The real size is only given by Board.get_item_bounding_box().
    """
    if not footprints:
        return {}
    bboxes = adapter.get_bounding_boxes(footprints)
    radii = {}
    for fp, bbox in zip(footprints, bboxes):
        ref = fp.ref
        radii[ref] = _radius_from_bbox(bbox)
        if bbox is None:
            logger.debug(_("  {ref}: bounding box unavailable, using fallback radius {radius}mm").format(
                ref=ref, radius=DEFAULT_RADIUS_MM))
    return radii


def footprints_overlap(pos1: Vector2, r1: float, pos2: Vector2, r2: float,
                       margin_mm: float = 0.2) -> bool:
    """Checks whether two circle-approximations overlap at given positions/radii."""
    dist = (pos1 - pos2).length()
    return dist < (r1 + r2 + margin_mm * MM)


def check_collisions(moves: list[MoveCommand],
                     all_footprints: list[Footprint],
                     adapter,
                     ignore_refs: set[str] = None,
                     margin_mm: float = 0.2) -> list[tuple[str, str, float]]:
    """
    Checks collisions between moving capacitors and other
    components, using REAL sizes (via adapter.get_bounding_boxes),
    rather than a fixed radius for all.

    Returns a list of tuples (ref1, ref2, distance_mm) for all
    conflicting pairs.
    """
    if ignore_refs is None:
        ignore_refs = set()

    conflicts = []
    move_positions = {m.ref: m.position for m in moves}
    move_refs = set(move_positions.keys())

    relevant_footprints = [fp for fp in all_footprints
                            if fp.ref not in ignore_refs]
    radii = compute_radii(relevant_footprints, adapter)

    fp_by_ref = {fp.ref: fp for fp in relevant_footprints}

    checked_pairs = set()

    for move in moves:
        ref = move.ref
        new_pos = move.position
        r_move = radii.get(ref, DEFAULT_RADIUS_MM * MM)

        # With non-moving components
        for other_ref, other_fp in fp_by_ref.items():
            if other_ref == ref or other_ref in move_refs:
                continue
            other_pos = other_fp.position
            other_r = radii.get(other_ref, DEFAULT_RADIUS_MM * MM)
            if footprints_overlap(new_pos, r_move, other_pos, other_r, margin_mm):
                dist_mm = (new_pos - other_pos).length() / MM
                conflicts.append((ref, other_ref, dist_mm))

        # With other moving components (check each pair once)
        for other_move in moves:
            other_ref = other_move.ref
            if other_ref == ref:
                continue
            pair = tuple(sorted((ref, other_ref)))
            if pair in checked_pairs:
                continue
            checked_pairs.add(pair)
            other_r = radii.get(other_ref, DEFAULT_RADIUS_MM * MM)
            if footprints_overlap(new_pos, r_move, other_move.position, other_r, margin_mm):
                dist_mm = (new_pos - other_move.position).length() / MM
                conflicts.append((ref, other_ref, dist_mm))

    return conflicts
