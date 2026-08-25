# kicadstamp/template_selection.py
"""
template_selection.py — pure selection-geometry helpers for template
extraction. Split out of template_extraction.py during the T3.1 god-file
decomposition (behavior-preserving code move — see
handoff_2026_08_05_architecture_fixes_roadmap.md).

Contains only selection logic with no YAML/serialization concern:
  * point matching within POSITION_TOLERANCE_MM (KiCad does not require
    exact coordinate coincidence for electrical connectivity);
  * inflating the real bounding boxes of pads/vias with a small epsilon;
  * filtering selected tracks/vias to those whose connected component (via
    coincident endpoints, track-to-track joints, or touching a via) reaches
    at least one REAL anchor — a pad of a KEPT footprint (a connected-
    components closure, see _filter_tracks_and_vias_within_selection);
  * resolving the extraction origin (bbox lower-left corner, or an explicit
    via/component).
"""
import logging
from typing import Any

from .domain.board import Footprint, Via, Track
from kipy.geometry import Vector2

from .constants import POSITION_TOLERANCE_MM, ROLE_FIELD_NAME
from .exceptions import ValidationError, format_fatal_error
from .kicad.adapter import KiCadBoardAdapter
from .utils.units import MM
from .i18n import _

logger = logging.getLogger(__name__)


def _points_match(p1: Vector2, p2: Vector2, tol_mm: float = POSITION_TOLERANCE_MM) -> bool:
    return abs(p1.x - p2.x) / MM <= tol_mm and abs(p1.y - p2.y) / MM <= tol_mm


def _point_matches_any(point: Vector2, anchors: list[Vector2]) -> bool:
    return any(_points_match(point, a) for a in anchors)


_BBOX_EPSILON_MM = 0.001  # NOT a routing tolerance (the real bbox of via/pad
                          # already provides all the needed margin — via radius
                          # is usually an order of magnitude larger than any
                          # manual routing error). This is purely a defence
                          # against coordinate quantisation/float rounding when
                          # converting to nm, not a "how crookedly the track is
                          # attached" tolerance.


def _inflated_boxes(adapter: KiCadBoardAdapter, items: list[Any]) -> list[Any]:
    boxes = adapter.get_bounding_boxes(items)
    for b in boxes:
        if b is not None:
            b.inflate(int(_BBOX_EPSILON_MM * MM))
    return boxes


def _point_in_box(point: Vector2, box) -> bool:
    if box is None:
        return False
    return (box.pos.x <= point.x <= box.pos.x + box.size.x
            and box.pos.y <= point.y <= box.pos.y + box.size.y)


def _filter_tracks_and_vias_within_selection(
    tracks: list[Track], vias: list[Via], footprints: list[Footprint],
    adapter: KiCadBoardAdapter,
) -> tuple[list[Track], list[Via]]:
    """
    Keeps only tracks/vias whose connected component (via coincident
    endpoints, track-to-track joints, or touching a via) reaches at least
    one REAL anchor — a pad of a KEPT footprint (`footprints`, already
    Cluster-filtered upstream by the caller if "Keep only one Cluster" is
    active). A track-to-track (or track-to-via) chain that only ever
    touches OTHER excluded-cluster material, however long, is dropped as a
    whole connected component — fixes the "two tracks mutually validate
    each other at a shared endpoint on an EXCLUDED footprint's pad, neither
    ever reaching a real anchor" loophole the old per-endpoint local check
    had (found live 2026-08-16, dac_buf/DAC_BUF: 6 literal-net tracks
    belonging to a completely separate protection network survived "Keep
    only one Cluster" this way, hardcoded to /Channel_0/..., breaking reuse
    on Channel_1/2).

    "Connected" is not exact coordinate equality (KiCad does not require
    exact coincidence for electrical connectivity — connectivity is about
    copper overlap within the real via/pad footprint, not coordinate to the
    micron; manual routing almost never lands exactly at the centre), but
    rather that the endpoint falls within the REAL bounding box of the
    corresponding pad/via (+ a small technological margin), or coincides
    with another track's endpoint (track-to-track butt-joint). The anchor
    set is ONLY the kept footprints' pads — a via is never an anchor by
    itself (it is kept only when its component reaches a kept pad), so a
    track-to-track island that only ever touches other excluded material is
    dropped as a WHOLE component instead of being locally "rescued" hop by
    hop.

    Vias are filtered by the same closure — they used to pass through
    completely unfiltered (no gate existed at all).

    Fallback: when the selection has NO usable kept-pad geometry (no
    footprints, or the adapter cannot produce real pad bounding boxes — e.g.
    via-only extractions and mock adapters), there is no anchor set to root
    the closure at, so the historical behavior is preserved exactly: vias
    pass through unchanged, tracks are filtered by the old both-ends-match
    rule (a track whose end goes nowhere is dropped with a warning).
    """
    all_pads = [p for fp in footprints for p in adapter.get_footprint_pads(fp)]
    pad_boxes = _inflated_boxes(adapter, all_pads)
    logger.debug(_("Kept footprints: {refs}; pad boxes: {ok} real / {total} total").format(
        refs=[fp.ref if fp.ref else "?" for fp in footprints],
        ok=sum(1 for b in pad_boxes if b is not None), total=len(pad_boxes)))

    if not any(b is not None for b in pad_boxes):
        # No real kept-pad anchors available — via-only extraction / mock
        # adapters without pad geometry. Fall back to the historical
        # behavior: vias pass through unchanged, tracks both-ends-match.
        via_boxes = _inflated_boxes(adapter, vias)

        def endpoint_ok(point: Vector2, this_track: Track) -> bool:
            if any(_point_in_box(point, box) for box in via_boxes):
                return True
            if any(_point_in_box(point, box) for box in pad_boxes):
                return True
            for other in tracks:
                if other is this_track:
                    continue
                if _points_match(point, other.start) or _points_match(point, other.end):
                    return True
            return False

        kept_tracks = []
        for t in tracks:
            start_ok = endpoint_ok(t.start, t)
            end_ok = endpoint_ok(t.end, t)
            if start_ok and end_ok:
                kept_tracks.append(t)
            else:
                missing = (_("both ends") if not start_ok and not end_ok
                           else _("start") if not start_ok else _("end"))
                logger.warning(_("  track ({sx:.3f},{sy:.3f}) -> ({ex:.3f},{ey:.3f}) mm, net={net}: "
                                 "{missing} does not match anything else in the selection — "
                                 "probably extends beyond the intended area, skipped")
                               .format(sx=t.start.x/MM, sy=t.start.y/MM,
                                       ex=t.end.x/MM, ey=t.end.y/MM,
                                       net=t.net_name,
                                       missing=missing))
        return kept_tracks, list(vias)

    ANCHOR = object()
    parent: dict[Any, Any] = {ANCHOR: ANCHOR}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, _t in enumerate(tracks):
        parent[("t", i)] = ("t", i)
    for i, _v in enumerate(vias):
        parent[("v", i)] = ("v", i)

    def anchored_by_pad(point: Vector2) -> bool:
        return any(_point_in_box(point, box) for box in pad_boxes)

    for i, t in enumerate(tracks):
        if anchored_by_pad(t.start) or anchored_by_pad(t.end):
            union(("t", i), ANCHOR)
    for i, v in enumerate(vias):
        if anchored_by_pad(v.position):
            union(("v", i), ANCHOR)

    for i, t in enumerate(tracks):
        for j, v in enumerate(vias):
            if _points_match(t.start, v.position) or _points_match(t.end, v.position):
                union(("t", i), ("v", j))
    for i, t in enumerate(tracks):
        for j, other in enumerate(tracks):
            if j <= i:
                continue
            if (_points_match(t.start, other.start) or _points_match(t.start, other.end)
                    or _points_match(t.end, other.start) or _points_match(t.end, other.end)):
                union(("t", i), ("t", j))

    root = find(ANCHOR)
    kept_tracks = [t for i, t in enumerate(tracks) if find(("t", i)) == root]
    kept_vias = [v for i, v in enumerate(vias) if find(("v", i)) == root]
    dropped_tracks = [t for i, t in enumerate(tracks) if find(("t", i)) != root]
    dropped_vias = [v for i, v in enumerate(vias) if find(("v", i)) != root]
    for t in dropped_tracks:
        logger.warning(_("  track ({sx:.3f},{sy:.3f}) -> ({ex:.3f},{ey:.3f}) mm, net={net}: "
                         "not connected to any kept footprint's pad or via — "
                         "belongs to excluded material, skipped")
                       .format(sx=t.start.x/MM, sy=t.start.y/MM, ex=t.end.x/MM, ey=t.end.y/MM,
                               net=t.net_name))
    for v in dropped_vias:
        logger.warning(_("  via at ({x:.3f},{y:.3f}) mm, net={net}: "
                         "not connected to any kept footprint's pad — "
                         "belongs to excluded material, skipped")
                       .format(x=v.position.x/MM, y=v.position.y/MM,
                               net=v.net_name))
    return kept_tracks, kept_vias


def _bbox_origin(footprints: list[Footprint], vias: list[Via]) -> Vector2:
    """(min_x, max_y) — lower‑left corner of the selection bounding box."""
    xs = [fp.position.x for fp in footprints] + [v.position.x for v in vias]
    ys = [fp.position.y for fp in footprints] + [v.position.y for v in vias]
    return Vector2.from_xy(min(xs), max(ys))


def _find_origin(footprints: list[Footprint], vias: list[Via],
                 origin_via_net: str | None, origin_component_role: str | None,
                 origin_component_pad: str | None,
                 adapter: KiCadBoardAdapter) -> Vector2:
    """
    Default origin is bbox (see _bbox_origin). If origin_via_net or
    origin_component_role is set, origin is taken from the specific element
    in the selection (its current position on the board) rather than the bbox.
    origin_via_net and origin_component_role are mutually exclusive (checked in
    kicadstamp_cli.py). origin_component_pad is ONLY a refinement of
    origin_component_role (without it it is meaningless — fatal in CLI):
    without it origin is the component centre, with it the position of the
    specific pad (same principle as anchor_pad in ClonePlacement).
    Fatal if the element is not found or (for via_net) ambiguous — no guessing.
    """
    if origin_via_net is not None:
        candidates = [v for v in vias if v.net_name == origin_via_net]
        if not candidates:
            raise ValidationError(format_fatal_error(
                _("--origin-by-via-net {net!r} not found in selection").format(net=origin_via_net),
                [_("among {count} selected vias, none is on net {net!r}").format(
                    count=len(vias), net=origin_via_net)]
            ))
        if len(candidates) > 1:
            positions = [f"({v.position.x/MM:.3f}, {v.position.y/MM:.3f})" for v in candidates]
            raise ValidationError(format_fatal_error(
                _("--origin-by-via-net {net!r} is ambiguous").format(net=origin_via_net),
                [_("selection contains {count} vias on this net: {pos} — "
                   "refine the selection (keep only one such via) or use "
                   "--origin-by-component-role instead").format(
                       count=len(candidates), pos=positions)]
            ))
        return candidates[0].position

    if origin_component_role is not None:
        for fp in footprints:
            if adapter.get_field_value(fp, ROLE_FIELD_NAME) == origin_component_role:
                if origin_component_pad is None:
                    return fp.position
                pad = adapter.get_pad_by_number(fp, origin_component_pad)
                if pad is None:
                    raise ValidationError(format_fatal_error(
                        _("--origin-by-component-pad {pad!r} not found").format(pad=origin_component_pad),
                        [_("component with role {role!r} ({ref}) has no pad {pad!r} — "
                           "pad numbers are strings as in KiCad").format(
                               role=origin_component_role, ref=fp.ref,
                               pad=origin_component_pad)]
                    ))
                return pad.position
        raise ValidationError(format_fatal_error(
            _("--origin-by-component-role {role!r} not found in selection").format(role=origin_component_role),
            [_("among {count} selected components, none has role {role!r}").format(
                count=len(footprints), role=origin_component_role)]
        ))

    return _bbox_origin(footprints, vias)
