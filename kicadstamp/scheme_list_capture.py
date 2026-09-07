# kicadstamp/scheme_list_capture.py
"""Scheme List capture — record a real, already-routed region of the live
board as a SchemeListConfig (plan_2026_09_05_scheme_list.md P2;
design_2026_09_07_scheme_list_pivot.md — CENTRE-frame, no anchor).

A Scheme List is a named snapshot identified by an explicit list of literal
refdes (NOT a Role): resolve the refs directly on the live board, run the SAME
connectivity-closure copper filter Cell extraction uses
(template_selection._filter_tracks_and_vias_within_selection), keep only the
copper that reaches a pad of a recorded ref, and report whatever is dropped as
boundary_nets diagnostics. Networks are literal (no net_from_role/
classify_net).

The frame of the record is the CENTRE of the recorded region's bbox (the
midpoint of the recorded footprints' position extents): every component/via/
track offset is measured from that centre, and the record's `pivot` (a point
in the same centre-frame, default (0,0) = the centre) is the point that lands
on a placement node at Redraw. Each element keeps its REAL absolute angle at
capture — no `anchor_rotation_deg` compensation exists (the d3326e4
double-rotation class is gone by construction). Nothing is applied to the
board — this is pure capture.
"""
import logging
from dataclasses import dataclass, field

from .config.models import (
    SchemeListBoundaryNet,
    SchemeListComponentRecord,
    SchemeListConfig,
    SchemeListScopePreset,
    SchemeListTrackRecord,
    SchemeListViaRecord,
)
from .domain.board import Footprint, Track, Via
from .domain.geometry import Box2, Vector2, clip_segment_to_box
from .exceptions import ValidationError, format_fatal_error
from .template_selection import _filter_tracks_and_vias_within_selection
from .channel_copy import _FOREIGN_BBOX_MARGIN_MM
from .constants import ANGLE_TOLERANCE_DEG, POSITION_TOLERANCE_MM
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


def _region_centre(footprints: list[Footprint]) -> Vector2:
    """The centre of the recorded region — the midpoint of the recorded
    footprints' POSITION extents (min/max over their origins). Deterministic
    from the refs alone (no per-footprint bbox geometry), the frame origin of
    the record (design_2026_09_07_scheme_list_pivot.md). Empty -> (0,0)."""
    if not footprints:
        return Vector2.from_xy(0, 0)
    xs = [fp.position.x for fp in footprints]
    ys = [fp.position.y for fp in footprints]
    return Vector2.from_xy((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2)


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
    outside it. Delegates to the shared Liang-Barsky clip math in
    domain.geometry (the SAME core Part A truncate uses for real clipping),
    so the boolean pre-filter and the geometric clip cannot drift."""
    return clip_segment_to_box(a, b, box) is not None


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
    adapter=None,
    pivot: tuple[float, float] | None = None,
    source_sheet: str | None = None,   # explicit override (Reread)
    sheet_names: dict[str, str] | None = None,  # for derivation (Record/Re-source)
    scope_sheet_paths: list[list[str]] | None = None,  # 5c.1 — "By sheet" scope
    scope_presets: list[SchemeListScopePreset] | None = None,  # named presets
    boundary_net_actions: dict[str, str] | None = None,  # net -> "exclude"|"truncate"
) -> SchemeListConfig:
    """Capture the live board region identified by `refs` as a Scheme List.

    Resolves every ref by direct refdes lookup (missing refs -> one fatal
    listing ALL of them), runs the shared connectivity-closure filter over the
    copper near the refs' bbox (+1 mm), records components/vias/tracks with
    literal nets and literal copper-layer strings in the record's CENTRE frame,
    and reports dropped (excluded-material) copper as boundary_nets. Pure
    capture — writes nothing to the board.

    The record's frame origin is the CENTRE of the recorded region (the
    midpoint of the recorded footprints' position extents, design_2026_09_07_
    scheme_list_pivot.md): every recorded element's offset is measured from
    that centre, NOT from any single anchor component. `pivot` (a point in the
    same centre-frame, default (0,0) = the centre) is stored on the record —
    the point that lands on a placement node at Redraw. Reread passes the
    STORED pivot so a re-capture does not reset a user-chosen pivot.

    ``boundary_net_actions`` (Part A truncate): per-NET decision
    ``{net: "exclude" | "truncate"}`` for copper the closure dropped (reaches
    only EXCLUDED footprints). Default for any net not in the dict — and when
    the parameter is None entirely — is ``"exclude"`` (drop the whole
    connected component, v1 behavior, byte-for-byte backwards compatible).
    ``"truncate"`` instead clips EVERY dropped stub of that net at the HONEST
    capture boundary (the union bbox of the captured footprints, no
    pre-filter margin) and keeps its in-region part; a dropped via is a point,
    kept only when it lies inside the boundary, dropped otherwise. The net
    STILL appears in boundary_nets with action="truncate" so Reread knows the
    decision and re-clips deterministically.

    ``source_sheet`` — the sheet the record was captured from. An explicit
    value (Reread's override, keeping the STORED sheet) always wins; otherwise,
    when ``sheet_names`` (the {uuid: Sheetname} map a Config/ctx carries) is
    given, it is DERIVED from the FIRST sorted recorded footprint's OWN full
    resolved sheet path (resolve_sheet_path_names over its sheet_path_uuids) —
    the same derivation for both Record tabs, never a network-prefix guess
    (channel_copy.sheet_name_of_fp). Without either the record is "in place
    only" (source_sheet None).
    """
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
    footprints = [fp_by_ref[r] for r in refs]

    # Frame origin = centre of the recorded footprints' POSITION extents
    # (deterministic, needs no per-footprint bbox geometry — mirror of the
    # design's "centre of the recorded region's bbox").
    origin = _region_centre(footprints)
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

    # External footprints (refs NOT in this capture) — feed the boundary-net
    # diagnostics' external_ref ("which outside component dragged this net").
    external_refs = {ref for ref in fp_by_ref if ref not in set(refs)}
    external_fps = [fp_by_ref[r] for r in sorted(external_refs)]

    # Part A truncate (plan_2026_09_06_boundary_truncate.md §4): for a net with
    # action="truncate" clip EVERY dropped stub at the HONEST capture boundary —
    # the union bbox of the captured footprints WITHOUT the pre-filter margin
    # (region above is inflated by _BBOX_MARGIN_MM only as a perf pre-filter,
    # NOT a decision boundary) — and KEEP its in-region part. A dropped via is a
    # point: kept only when inside the boundary. The net STILL lands in
    # boundary_nets with action="truncate" (the decision is persisted, so Reread
    # re-applies the same clip deterministically).
    truncate_nets = {net for net, act in (boundary_net_actions or {}).items()
                     if act == "truncate"}
    clip_box = _union_box(fp_boxes) if truncate_nets else None

    dropped_by_net: dict[str, list] = {}
    for item in list(dropped_tracks) + list(dropped_vias):
        if item.net_name:
            dropped_by_net.setdefault(item.net_name, []).append(item)

    boundary_nets: list[SchemeListBoundaryNet] = []
    for net, items in sorted(dropped_by_net.items()):
        if (boundary_net_actions or {}).get(net) == "truncate":
            if clip_box is None:
                # No real footprint bbox geometry to clip against (e.g. a mock
                # adapter without boxes) — degrade to exclude, never crash.
                logger.warning(
                    "scheme list %r: boundary net %r requested 'truncate' but no "
                    "footprint bbox is available — falling back to 'exclude'",
                    name, net)
                action = "exclude"
            else:
                action = "truncate"
                for item in items:
                    if isinstance(item, Track):
                        clipped = clip_segment_to_box(item.start, item.end, clip_box)
                        if clipped is not None:
                            kept_tracks.append(Track(
                                uuid=f"clipped-{item.uuid}",
                                start=clipped[0], end=clipped[1],
                                net_name=item.net_name,
                                width_mm=item.width_mm,
                                layer=item.layer,
                                _kipy=None))
                    elif _point_in_box(item.position, clip_box):
                        # Via — a point: nothing to clip; keep when in-region.
                        kept_vias.append(item)
        else:
            action = "exclude"
        boundary_nets.append(SchemeListBoundaryNet(
            net=net,
            action=action,
            external_ref=_boundary_net_external_ref(items, external_fps, adapter)))

    vias = [_via_record(v, origin) for v in kept_vias]
    tracks = [_track_record(t, origin) for t in kept_tracks]

    # source_sheet: an explicit value (Reread's override) wins; otherwise it is
    # DERIVED from the FIRST SORTED recorded footprint's OWN full resolved
    # sheet path (deterministic — the same derivation for both Record tabs).
    # Not a network-prefix guess: channel_copy.sheet_name_of_fp only sees one
    # hierarchy level and fails on global-only footprints. None when no
    # sheet_names are given or the path is unresolved — such a record is
    # inherently "in place only".
    if source_sheet is None and sheet_names:
        from .sheet_names import resolve_sheet_path_names
        for fp in sorted(footprints, key=lambda f: f.ref):
            path = resolve_sheet_path_names(fp, sheet_names)
            if path and all(path):
                source_sheet = "/".join(path)
                break

    return SchemeListConfig(
        name=name,
        pivot=pivot if pivot is not None else (0.0, 0.0),
        source_sheet=source_sheet,
        # 5c.1 — persisted verbatim, never interpreted here: for a "By sheet"
        # capture it is the CHECKED leaf paths (so a later Reread recomputes
        # the same scope); for a "By selection" capture it stays None.
        scope_sheet_paths=scope_sheet_paths,
        # Named presets library — persisted VERBATIM, never interpreted here
        # either (capture does not decide what is inside; the caller does —
        # DockHub merges the save-as-preset into the payload, plan
        # 2026_09_06_scheme_list_named_presets.md §4/§7).
        scope_presets=scope_presets or [],
        components=components,
        vias=vias,
        tracks=tracks,
        boundary_nets=boundary_nets,
    )


# --- Reread diff (P3, pure computation — never applies anything) -------------


@dataclass
class SchemeListComponentChange:
    """A recorded component whose live position/rotation moved beyond the Reread
    tolerances (POSITION_TOLERANCE_MM / ANGLE_TOLERANCE_DEG)."""

    ref: str
    old_offset_along_mm: float
    old_offset_across_mm: float
    old_rotation_deg: float
    new_offset_along_mm: float
    new_offset_across_mm: float
    new_rotation_deg: float


@dataclass
class SchemeListDiff:
    """Reread result — what changed between a stored SchemeListConfig and the
    live board. Pure calculation; the caller (GUI) decides whether to apply
    (rewrite the stored record) after explicit confirmation.

    5c (plan_2026_09_06_scheme_list_sheet_capture.md 5c.2): two categories
    for a changeable REF SET, distinct from ``refs_not_found`` — that one means
    "recorded but PHYSICALLY ABSENT from the board"; ``refs_removed_from_scope``
    means "physically present, just no longer inside the CURRENT scope" (the
    user un-selected / a sub-sheet was excluded), and ``components_added`` are
    refs in the current scope but not in the stored record (their fresh
    geometry comes from the same capture the diff builds).

    Centre-frame semantics (design_2026_09_07_scheme_list_pivot.md): the
    record's offsets live relative to the recorded region's CENTRE, which is a
    function of the ref SET. The geometry diff (component moved / via/track
    added-removed) is therefore only computed when the compared set is
    UNCHANGED (no missing refs, no scope add/remove), and is TRANSLATION-
    INVARIANT there: both sides are aligned through a transient reference
    recorded component (see build_scheme_list_diff), so moving one part never
    reports the whole frame drifting. On a set change the diff reports the
    membership change (refs_not_found / components_added / refs_removed_from_
    scope — added components WITH their fresh geometry) and skips the per-
    element moved/via/track comparison (the record gets rewritten by Apply
    anyway)."""

    refs_not_found: list[str] = field(default_factory=list)
    components_moved: list[SchemeListComponentChange] = field(default_factory=list)
    components_added: list[SchemeListComponentRecord] = field(default_factory=list)
    refs_removed_from_scope: list[str] = field(default_factory=list)
    vias_added: list[SchemeListViaRecord] = field(default_factory=list)
    vias_removed: list[SchemeListViaRecord] = field(default_factory=list)
    tracks_added: list[SchemeListTrackRecord] = field(default_factory=list)
    tracks_removed: list[SchemeListTrackRecord] = field(default_factory=list)
    boundary_nets_added: list[str] = field(default_factory=list)
    boundary_nets_gone: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.refs_not_found or self.components_moved
                    or self.components_added or self.refs_removed_from_scope
                    or self.vias_added or self.vias_removed
                    or self.tracks_added or self.tracks_removed
                    or self.boundary_nets_added or self.boundary_nets_gone)


def _pos_equal(a: float, b: float) -> bool:
    return abs(a - b) <= POSITION_TOLERANCE_MM


def _angle_delta(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _via_matches(a: SchemeListViaRecord, b: SchemeListViaRecord) -> bool:
    return (a.net == b.net and _pos_equal(a.offset_along_mm, b.offset_along_mm)
            and _pos_equal(a.offset_across_mm, b.offset_across_mm))


def _track_matches(a: SchemeListTrackRecord, b: SchemeListTrackRecord) -> bool:
    if a.net != b.net or a.layer != b.layer:
        return False
    if not _pos_equal(a.width_mm, b.width_mm):
        return False
    # Endpoints must coincide within tolerance; a track may be drawn either way.
    if (_pos_equal(a.start_along_mm, b.start_along_mm)
            and _pos_equal(a.start_across_mm, b.start_across_mm)
            and _pos_equal(a.end_along_mm, b.end_along_mm)
            and _pos_equal(a.end_across_mm, b.end_across_mm)):
        return True
    if (_pos_equal(a.start_along_mm, b.end_along_mm)
            and _pos_equal(a.start_across_mm, b.end_across_mm)
            and _pos_equal(a.end_along_mm, b.start_along_mm)
            and _pos_equal(a.end_across_mm, b.start_across_mm)):
        return True
    return False


def _split_changes(old: list, new: list, matches) -> tuple[list, list]:
    """Greedy 1:1 matching over same-kind records -> (added, removed)."""
    unmatched_new = list(new)
    removed: list = []
    for old_item in old:
        for i, new_item in enumerate(unmatched_new):
            if matches(old_item, new_item):
                del unmatched_new[i]
                break
        else:
            removed.append(old_item)
    return unmatched_new, removed


def build_scheme_list_diff(stored: SchemeListConfig, adapter,
                           scope_refs: list[str] | None = None) -> SchemeListDiff:
    """Re-read the region a stored Scheme List was recorded from and report what
    differs, within the Reread tolerances. Pure computation — applies nothing.

    Centre-frame semantics (design_2026_09_07_scheme_list_pivot.md): the
    stored offsets are relative to the recorded region's centre, which is a
    function of the recorded ref SET. The per-element geometry diff
    (components_moved / vias / tracks / boundary nets) is therefore only
    computed when the compared set is UNCHANGED — no missing refs AND no scope
    add/remove — because only then do the stored and the freshly-captured
    frames coincide. On a set change the diff reports the membership change
    (refs_not_found / components_added / refs_removed_from_scope) and skips the
    geometry comparison (the record is rewritten by Apply anyway).

    ``scope_refs`` (5c) is the CURRENT scope (recomputed by the caller — for a
    "By sheet" record from its stored ``scope_sheet_paths`` over the live
    snapshot, for a "By selection" record from a fresh board selection). When
    None (no scope change) only the stored refs are re-read and nothing is
    added/removed from the set.
    """
    present = {fp.ref for fp in adapter.get_footprints()}
    stored_refs = [c.ref for c in stored.components]
    refs_not_found = [r for r in stored_refs if r not in present]
    found = [r for r in stored_refs if r in present]

    if scope_refs is not None:
        # Refs in the CURRENT scope but never recorded -> added. Refs recorded
        # AND physically present but outside the current scope -> removed-from-
        # scope (NOT refs_not_found — those stay the "must be, but absent"
        # category).
        added_refs = sorted(set(scope_refs) - set(stored_refs))
        removed_from_scope = sorted(set(found) - set(scope_refs))
        refs_for_fresh = sorted((set(found) - set(removed_from_scope)) | set(added_refs))
    else:
        added_refs = []
        removed_from_scope = []
        refs_for_fresh = found

    # A changed SET (missing / scope add-remove) means the region's centre —
    # a function of the ref set — differs between stored and fresh, so a
    # per-element offset comparison (components_moved / vias / tracks /
    # boundary nets) is meaningless: the two centre-frames no longer coincide.
    # Report ONLY the membership change:
    #   refs_not_found           recorded but physically absent
    #   refs_removed_from_scope  present but outside the current scope
    #   components_added         in scope but never recorded — WITH their FRESH
    #                            geometry from a capture over the widened scope
    #                            (the frame the record is rewritten in by Apply;
    #                            that capture skips the absent refs already
    #                            reported above).
    set_changed = bool(refs_not_found or added_refs or removed_from_scope)
    if set_changed or not refs_for_fresh:
        added_records: list = []
        if added_refs and refs_for_fresh:
            fresh = capture_scheme_list(
                name=stored.name, refs=refs_for_fresh, adapter=adapter,
                # Keep the STORED pivot and source_sheet as explicit overrides —
                # Reread's job is re-reading the same source, never resetting a
                # user-chosen pivot or re-deriving the sheet.
                pivot=stored.pivot, source_sheet=stored.source_sheet)
            fresh_by_ref = {c.ref: c for c in fresh.components}
            added_records = [fresh_by_ref[r] for r in sorted(added_refs)
                             if r in fresh_by_ref]
        return SchemeListDiff(
            refs_not_found=refs_not_found,
            components_added=added_records,
            refs_removed_from_scope=removed_from_scope,
        )

    fresh = capture_scheme_list(
        name=stored.name, refs=refs_for_fresh, adapter=adapter,
        # Keep the STORED pivot and source_sheet as explicit overrides —
        # Reread's job is re-reading the same source, never resetting a
        # user-chosen pivot or re-deriving the sheet.
        pivot=stored.pivot, source_sheet=stored.source_sheet)
    fresh_by_ref = {c.ref: c for c in fresh.components}

    # Centre-frame diff is TRANSLATION-INVARIANT ("центр сокращается",
    # design_2026_09_07 p.6.2): both sides' offsets are relative to the region's
    # CURRENT centre, and that centre moves whenever a component physically
    # moves — comparing the raw offsets would report the WHOLE frame drifting
    # instead of the true relative movement. So both sides are aligned through a
    # deterministic REFERENCE recorded component (the first sorted ref present
    # in both; this is a transient diff-comparison anchor, never stored — the
    # record itself has no anchor component). Adding the reference component's
    # own fresh-vs-stored shift to the stored side before comparing cancels the
    # centre exactly: relative positions p_i - p_ref0 are what is compared, and
    # reported new positions are expressed in the STORED frame.
    stored_by_ref = {c.ref: c for c in stored.components}
    ref0 = sorted(stored_by_ref)[0]
    ref0_stored = stored_by_ref[ref0]
    ref0_fresh = fresh_by_ref[ref0]
    shift_along = ref0_fresh.offset_along_mm - ref0_stored.offset_along_mm
    shift_across = ref0_fresh.offset_across_mm - ref0_stored.offset_across_mm

    # Components — report when position/rotation moved beyond the tolerance.
    components_moved: list[SchemeListComponentChange] = []
    for stored_comp in stored.components:
        new_comp = fresh_by_ref.get(stored_comp.ref)
        if new_comp is None:
            continue  # ref already reported in refs_not_found
        aligned_x = stored_comp.offset_along_mm + shift_along
        aligned_y = stored_comp.offset_across_mm + shift_across
        moved = (not (_pos_equal(aligned_x, new_comp.offset_along_mm)
                      and _pos_equal(aligned_y, new_comp.offset_across_mm))
                 or _angle_delta(stored_comp.rotation_deg, new_comp.rotation_deg)
                 > ANGLE_TOLERANCE_DEG)
        if moved:
            components_moved.append(SchemeListComponentChange(
                ref=stored_comp.ref,
                old_offset_along_mm=stored_comp.offset_along_mm,
                old_offset_across_mm=stored_comp.offset_across_mm,
                old_rotation_deg=stored_comp.rotation_deg,
                # expressed in the STORED frame (fresh minus the ref0 shift)
                new_offset_along_mm=round(new_comp.offset_along_mm - shift_along, 6),
                new_offset_across_mm=round(new_comp.offset_across_mm - shift_across, 6),
                new_rotation_deg=new_comp.rotation_deg))

    # Vias/tracks: align the STORED side by the same ref0 shift, then match.
    def _align_via(v: SchemeListViaRecord) -> SchemeListViaRecord:
        return SchemeListViaRecord(
            offset_along_mm=round(v.offset_along_mm + shift_along, 6),
            offset_across_mm=round(v.offset_across_mm + shift_across, 6),
            drill_mm=v.drill_mm, diameter_mm=v.diameter_mm, net=v.net)

    def _align_track(t: SchemeListTrackRecord) -> SchemeListTrackRecord:
        return SchemeListTrackRecord(
            start_along_mm=round(t.start_along_mm + shift_along, 6),
            start_across_mm=round(t.start_across_mm + shift_across, 6),
            end_along_mm=round(t.end_along_mm + shift_along, 6),
            end_across_mm=round(t.end_across_mm + shift_across, 6),
            width_mm=t.width_mm, layer=t.layer, net=t.net)

    aligned_stored_vias = [_align_via(v) for v in stored.vias]
    aligned_stored_tracks = [_align_track(t) for t in stored.tracks]
    vias_added, vias_removed = _split_changes(
        aligned_stored_vias, fresh.vias, _via_matches)
    tracks_added, tracks_removed = _split_changes(
        aligned_stored_tracks, fresh.tracks, _track_matches)

    stored_boundary = {bn.net for bn in stored.boundary_nets}
    fresh_boundary = {bn.net for bn in fresh.boundary_nets}
    boundary_nets_added = sorted(fresh_boundary - stored_boundary)
    boundary_nets_gone = sorted(stored_boundary - fresh_boundary)

    return SchemeListDiff(
        refs_not_found=refs_not_found,
        components_moved=components_moved,
        components_added=[],
        refs_removed_from_scope=[],
        vias_added=vias_added,
        vias_removed=vias_removed,
        tracks_added=tracks_added,
        tracks_removed=tracks_removed,
        boundary_nets_added=boundary_nets_added,
        boundary_nets_gone=boundary_nets_gone,
    )
