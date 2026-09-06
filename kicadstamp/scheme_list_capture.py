# kicadstamp/scheme_list_capture.py
"""Scheme List capture — record a real, already-routed region of the live
board as a SchemeListConfig (plan_2026_09_05_scheme_list.md P2).

A Scheme List is a named snapshot identified by an explicit list of literal
refdes (NOT a Role): resolve the refs directly on the live board, run the SAME
connectivity-closure copper filter Cell extraction uses
(template_selection._filter_tracks_and_vias_within_selection), keep only the
copper that reaches a pad of a recorded ref, and report whatever is dropped as
boundary_nets diagnostics. Networks are literal (no net_from_role/
classify_net); offsets are in the anchor_ref (+ anchor_pad) frame using the
exact formulas cell_geometry_refresh._import_via_record/_import_track_record
use. Nothing is applied to the board — this is pure capture.
"""
import logging

from .config.models import (
    SchemeListBoundaryNet,
    SchemeListComponentRecord,
    SchemeListConfig,
    SchemeListTrackRecord,
    SchemeListViaRecord,
)
from .domain.board import Footprint, Track, Via
from .domain.geometry import Box2, Vector2
from .exceptions import ValidationError, format_fatal_error
from .template_selection import _filter_tracks_and_vias_within_selection
from .channel_copy import _FOREIGN_BBOX_MARGIN_MM, sheet_name_of_fp
from .utils.layers import layer_to_str
from .utils.units import MM
from .i18n import _

logger = logging.getLogger(__name__)

# Bbox pre-filter margin around the recorded refs — the SAME value channel_copy
# uses for its foreign-copper scan (channel_copy._FOREIGN_BBOX_MARGIN_MM = 1.0).
_BBOX_MARGIN_MM = _FOREIGN_BBOX_MARGIN_MM


def _mm(delta_nm: int) -> float:
    """nm delta -> mm, rounded to 4 dp (the exact rounding the Cell geometry
    refresh formulas use — cell_geometry_refresh._mm)."""
    return round(delta_nm / MM, 4)


def _component_record(fp: Footprint, origin: Vector2) -> SchemeListComponentRecord:
    """One recorded component — same offset formula as Cell's component slot
    (cell_geometry_refresh._component_new_geo), keyed by literal ref."""
    return SchemeListComponentRecord(
        ref=fp.ref,
        offset_along_mm=_mm(fp.position.x - origin.x),
        offset_across_mm=_mm(fp.position.y - origin.y),
        rotation_deg=fp.angle_deg,
    )


def _via_record(via: Via, origin: Vector2) -> SchemeListViaRecord:
    """One recorded via — reuse cell_geometry_refresh._import_via_record's exact
    geometry (offset + drill/diameter); the net is LITERAL (never classified)."""
    record = SchemeListViaRecord(
        offset_along_mm=_mm(via.position.x - origin.x),
        offset_across_mm=_mm(via.position.y - origin.y),
        drill_mm=round(via.drill_mm, 4),
        diameter_mm=round(via.diameter_mm, 4),
        net=via.net_name,
    )
    return record


def _track_record(track: Track, origin: Vector2) -> SchemeListTrackRecord:
    """One recorded track — reuse cell_geometry_refresh._import_track_record's
    exact geometry; net LITERAL and copper layer as a STRING (full copper
    stack, utils.layers.layer_to_str)."""
    return SchemeListTrackRecord(
        start_along_mm=_mm(track.start.x - origin.x),
        start_across_mm=_mm(track.start.y - origin.y),
        end_along_mm=_mm(track.end.x - origin.x),
        end_across_mm=_mm(track.end.y - origin.y),
        width_mm=round(track.width_mm, 4),
        layer=layer_to_str(track.layer),
        net=track.net_name,
    )


# --- bbox pre-filter (avoids the closure filter's O(N²) on a dense board) ---

def _union_box(boxes: list[Box2 | None]) -> Box2 | None:
    real = [b for b in boxes if b is not None]
    if not real:
        return None
    min_x = min(b.pos.x for b in real)
    min_y = min(b.pos.y for b in real)
    max_x = max(b.pos.x + b.size.x for b in real)
    max_y = max(b.pos.y + b.size.y for b in real)
    box = Box2()
    box.pos = Vector2.from_xy(min_x, min_y)
    box.size = Vector2.from_xy(max_x - min_x, max_y - min_y)
    return box


def _point_in_box(point: Vector2, box: Box2) -> bool:
    return (box.pos.x <= point.x <= box.pos.x + box.size.x
            and box.pos.y <= point.y <= box.pos.y + box.size.y)


def _segment_intersects_box(a: Vector2, b: Vector2, box: Box2) -> bool:
    """True when the segment [a,b] intersects the axis-aligned box (endpoint
    inside OR crossing one of the four edges) — the plan's pre-filter criterion
    keeps a LONG track that passes through the capture region but has both ends
    outside it."""
    x1, y1 = float(a.x), float(a.y)
    x2, y2 = float(b.x), float(b.y)
    min_x, min_y = float(box.pos.x), float(box.pos.y)
    max_x, max_y = float(box.pos.x + box.size.x), float(box.pos.y + box.size.y)

    # Liang–Barsky slab test.
    dx = x2 - x1
    dy = y2 - y1
    p = (-dx, dx, -dy, dy)
    q = (x1 - min_x, max_x - x1, y1 - min_y, max_y - y1)
    t0, t1 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if pi == 0.0:
            if qi < 0.0:
                return False  # parallel and outside
        else:
            r = qi / pi
            if pi < 0.0:
                if r > t1:
                    return False
                if r > t0:
                    t0 = r
            else:
                if r < t0:
                    return False
                if r < t1:
                    t1 = r
    return True


def _prefilter_copper(tracks: list[Track], vias: list[Via], region: Box2 | None
                      ) -> tuple[list[Track], list[Via]]:
    """Keep only copper near the recorded refs (region = refs' union bbox +
    1 mm margin). No region (e.g. a mock adapter without boxes) -> everything is
    passed through; the closure filter is then the only gate (correct, just
    O(N²)). Criterion: via position inside, OR a track endpoint inside, OR the
    track SEGMENT crosses the region."""
    if region is None:
        return tracks, vias
    kept_tracks = [t for t in tracks
                   if _point_in_box(t.start, region) or _point_in_box(t.end, region)
                   or _segment_intersects_box(t.start, t.end, region)]
    kept_vias = [v for v in vias if _point_in_box(v.position, region)]
    return kept_tracks, kept_vias


def _boundary_net_external_ref(dropped: list[Track] | list[Via],
                               external_fps: list[Footprint],
                               adapter) -> str | None:
    """Diagnostics: which footprint OUTSIDE the recorded refs the dropped
    copper belongs to (first external footprint whose bounding box contains a
    dropped point). Returns None when nothing matches — external_ref is
    diagnostics only, never a decision key."""
    if not dropped or not external_fps:
        return None
    boxes = adapter.get_bounding_boxes(external_fps)
    for item in dropped:
        points: list[Vector2] = []
        if isinstance(item, Track):
            points = [item.start, item.end]
        else:
            points = [item.position]
        for pt in points:
            for fp, box in zip(external_fps, boxes):
                if box is not None and _point_in_box(pt, box):
                    return fp.ref
    return None


def capture_scheme_list(
    name: str,
    refs: list[str],
    anchor_ref: str,
    anchor_pad: str | None = None,
    adapter=None,
) -> SchemeListConfig:
    """Capture the live board region identified by `refs` as a Scheme List.

    Resolves every ref by direct refdes lookup (missing refs -> one fatal
    listing ALL of them), runs the shared connectivity-closure filter over the
    copper near the refs' bbox (+1 mm), records components/vias/tracks with
    literal nets and literal copper-layer strings in the anchor_ref frame, and
    reports dropped (excluded-material) copper as boundary_nets (v1 action
    "exclude"). Pure capture — writes nothing to the board.
    """
    anchor_ref = anchor_ref or (refs[0] if refs else "")
    if not refs:
        raise ValidationError(format_fatal_error(
            _("cannot record scheme list {name!r}: no refs given").format(name=name),
            [_("a Scheme List needs at least one component ref to capture")]))

    all_footprints = adapter.get_footprints()  # one IPC read, shared below
    fp_by_ref = {fp.ref: fp for fp in all_footprints}
    missing = sorted(set(refs) - set(fp_by_ref))
    if missing:
        raise ValidationError(format_fatal_error(
            _("cannot record scheme list {name!r}: refs not found on the board: {refs}").format(
                name=name, refs=", ".join(missing)),
            [_("Scheme List capture is a snapshot of real footprints — every "
               "ref in the list must resolve on the live board (use the board "
               "selection / direct refdes lookup)")]))
    if anchor_ref not in refs:
        raise ValidationError(format_fatal_error(
            _("cannot record scheme list {name!r}: anchor_ref {ref!r} is not among the refs").format(
                name=name, ref=anchor_ref),
            [_("the anchor_ref must be one of the captured components (it is "
               "the offset origin and the clone anchor point)")]))
    footprints = [fp_by_ref[r] for r in refs]
    anchor_fp = fp_by_ref[anchor_ref]

    # Origin: anchor_ref centre, or the anchor_pad centre when given.
    origin = anchor_fp.position
    if anchor_pad:
        pads = {str(pad.number): pad for pad in adapter.get_footprint_pads(anchor_fp)}
        pad = pads.get(anchor_pad) or pads.get(str(anchor_pad))
        if pad is None:
            raise ValidationError(format_fatal_error(
                _("cannot record scheme list {name!r}: pad {pad!r} not found on {ref}").format(
                    name=name, pad=anchor_pad, ref=anchor_ref),
                [_("anchor_pad must be a pad number of the anchor footprint")]))
        origin = pad.position

    components = [_component_record(fp, origin) for fp in footprints]

    # Region + bbox pre-filter (perf), then the shared closure filter with the
    # dropped copper collected for boundary_nets.
    fp_boxes = adapter.get_bounding_boxes(footprints)
    region = _union_box(fp_boxes)
    if region is not None:
        region.inflate(int(_BBOX_MARGIN_MM * MM))

    all_tracks = adapter.get_tracks()
    all_vias = adapter.get_vias()
    pre_tracks, pre_vias = _prefilter_copper(all_tracks, all_vias, region)

    kept_tracks, kept_vias, dropped_tracks, dropped_vias = (
        _filter_tracks_and_vias_within_selection(
            pre_tracks, pre_vias, footprints, adapter, collect_dropped=True))

    vias = [_via_record(v, origin) for v in kept_vias]
    tracks = [_track_record(t, origin) for t in kept_tracks]

    # Boundary nets: group the dropped copper by its literal net; one decision
    # (exclude) per NET, external_ref = first external footprint that contains
    # a dropped stub (diagnostics only).
    external_refs = {ref for ref in fp_by_ref if ref not in set(refs)}
    external_fps = [fp_by_ref[r] for r in sorted(external_refs)]
    dropped_items: list = list(dropped_tracks) + list(dropped_vias)
    boundary_by_net: dict[str, str | None] = {}
    for item in dropped_items:
        if not item.net_name:
            continue
        if item.net_name in boundary_by_net:
            continue
        boundary_by_net[item.net_name] = _boundary_net_external_ref(
            [item], external_fps, adapter)
    boundary_nets = [
        SchemeListBoundaryNet(net=net, action="exclude", external_ref=ref)
        for net, ref in sorted(boundary_by_net.items())
    ]

    # source_sheet: the sheet the anchor (or, failing that, another recorded
    # ref) currently sits on — the top-level sheet name from its local-net
    # prefix (channel_copy.sheet_name_of_fp). None when every recorded ref is
    # global-only (root sheet) — such a record is inherently "in place only".
    source_sheet = None
    for fp in [anchor_fp] + [f for f in footprints if f is not anchor_fp]:
        sheet = sheet_name_of_fp(adapter, fp)
        if sheet:
            source_sheet = sheet
            break

    return SchemeListConfig(
        name=name,
        anchor_ref=anchor_ref,
        anchor_pad=anchor_pad,
        source_sheet=source_sheet,
        components=components,
        vias=vias,
        tracks=tracks,
        boundary_nets=boundary_nets,
    )
