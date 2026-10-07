# kicadstamp/absent_copper_prune.py
"""The record -> LIVE COPPER map of ONE cell instance — the basis of С-2
«Subtract selected copper» (plan_2026_10_06_prune_absent_cell_copper) AND the
registry-only fallback of the same map when the drift guard refused the tree
that places the instance (plan_2026_10_07_refused_tree_matching).

The pair is EXACT, never "nearest": a dry run of THIS instance's recording
(``ApplyPipeline(only=[record], dry_run=True)`` → ``plan_copper()``) produces the
commands the redraw would place, and ``registry_match.match_planned_copper``
matches each command to the live board — tier 1 by the registry's stored uuid,
tier 2 by exact geometry. That is the SAME matching «Select cell» uses
(plan_2026_10_05_select_cell_split, СЦ-2), so the invariant holds:

    you can subtract exactly what «Select cell» would highlight.

When the DRIFT GUARD refused the tree (``trees.check_tree_self_anchor_drift``),
the tree is NOT materialized, so the dry run plans nothing (``planned == 0``) and
the exact pair cannot be built at all. The guard is NOT bypassed for a dry run (a
leaked "materialize the refused tree" flag would bring the live 4.75 mm ``fpga``
drift back into a real apply), and the refused tree's plan is wrong by POSITION
anyway — so geometry (tier 2) is pointless for it. Instead
:func:`registry_record_copper_map` builds the SAME map from the REGISTRY ALONE:
every key ``selection_narrowing.is_own_key`` accepts for the instance, its
``(kind, role_part, index)`` tail read by the ONE :func:`_record_tail`, matched
to the live copper the registry stores — plus the count of this cell's records
the registry has NO key for (they cannot be checked this way — named, never
silent). «Select cell»'s fallback (``gui/select_cell.no_planned_copper_targets``)
and this map share the ONE filter loop :func:`own_registry_entries`, so the two
can never disagree about "which copper is mine".

The record-identifying TAIL of ``make_registry_key`` ("via"/"track", the role
placeholder for spoke-level copper, the 0-based index in the cell's list) is the
key: every command of one dry run shares the anchor and template, so that tail
tells the records apart — the ONE association the plan allows ("запись ↔ команда
— по индексу в ключе реестра").

A TAIL IS A NUMBER, and the number is the record's place in the cell's list AT
THE LAST REDRAW. Editing that list shifts the numbers while nobody renumbers the
registry, so a key can keep the uuid of ANOTHER record's copper (finding of the
f3e116d0 acceptance, plan_2026_10_07_registry_pair_frame_check). Every pair is
therefore CHECKED BY PLACE, each path in the one place it can trust:

  * a DRY RUN — the live item must lie where the COMMAND plans the copper
    (``registry_match.accept_planned_match``; the tree was redrawn, so the plan's
    own place is exact even for a non-rigid cluster);
  * the REGISTRY-ONLY path — inside the CELL FRAME, and only while that frame is
    RIGID (a non-rigid frame is off by more than the step between neighbouring
    records, so it cannot tell a pair from its neighbour).

A pair that does not sit where the record puts the copper, a key whose index has
NO record at all (an ORPHAN), and EVERY pair of a non-rigid frame are "not
checked": counted and named by the caller, never subtracted and never highlighted
as this cell's own.

History: the first С-1 rule asked only the REGISTRY ("delete a record when the
uuid the registry stored for it is gone") and then tried to answer "is the copper
still on the board" with a ``BoardCopperPresence`` verdict. С-1 made the read
STRICTLY the selection, which removed the need for that verdict AND its callers —
so the presence dataclass and ``instance_copper_presence`` are gone (dead code is
not left behind), and what remains is the MAP С-2 needs.

The pipeline builds its OWN adapter (``pipeline.adapter``) and is closed in a
``finally`` (deepseek.md / door.md: "свой сокет — свой finally"). Nothing here
reads the board from the UI thread, and nothing is written — not the registry,
not the board, not the config. Callers decide what to do with the map.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Optional

from .constants import SPOKE_LEVEL_ROLE_PLACEHOLDER
from .i18n import _
from .registry import (
    PlacementRegistry,
    TrackRegistry,
    load_registry_entries,
    record_key_part,
    registry_paths_for_config,
)
from .registry_match import accept_planned_match, match_planned_copper
from .selection_narrowing import cell_record_addresses, is_own_key
from .utils.units import MM

__all__ = [
    "RecordCopperMap",
    "cell_record_slots",
    "instance_record_copper_map",
    "own_instance_context",
    "own_registry_entries",
    "pair_agrees_with_record",
    "record_copper_map_for",
    "record_of",
    "record_points",
    "registry_record_copper_map",
]


@dataclass(frozen=True)
class RecordCopperMap:
    """``{(kind, role_part, index): live_uuid}`` for ONE instance's copper.

    ``planned`` — how many commands the dry run produced (0 = the record placed
    no copper: a refused tree, an unrealized record, a chain-only placement).
    For a map built by :func:`registry_record_copper_map` ``planned`` is the
    number of registry pairs ACCEPTED and ``source`` is ``"registry"``.

    ``without_registry`` — for a registry-only map, how many of this cell's copper
    records the registry has NO key for. They cannot be checked this way, so the
    caller names the number instead of pretending the cell has no such copper.

    ``disagreed`` — pairs the registry offered whose live copper does NOT sit
    where the record puts it (checked by place: the command's planned place on a
    dry run, the rigid cell frame on the registry-only path). They are NOT in the
    map and must never be subtracted or highlighted as this cell's own.
    ``orphan_keys`` — registry keys of this cell whose ``index`` has NO record in
    the cell today (the numbers shifted after the list was edited): never a pair.
    ``entries_total`` — how many own registry keys this map examined, so a caller
    can tell "its copper is not on the board" from "the pair was refused".
    ``not_rigid`` / ``frame_residual_mm`` — the registry-only path's frame could
    not be trusted (non-rigid, or not built at all): NO registry pair was accepted
    and the number is what the caller prints as the reason.

    The caller must subtract NOTHING when ``empty`` and say why — "we could not
    check", never "the cell has no copper".
    """

    by_record: dict = field(default_factory=dict)
    planned: int = 0
    source: str = "dry_run"
    without_registry: int = 0
    disagreed: int = 0
    orphan_keys: int = 0
    entries_total: int = 0
    not_rigid: bool = False
    frame_residual_mm: Optional[float] = None

    @property
    def empty(self) -> bool:
        """True when neither the dry run nor the registry gave a single match."""
        return self.planned == 0

    def live_uuid(self, kind: str, role_part: str, index: int) -> Optional[str]:
        """The live uuid this record's command matched, or None."""
        return self.by_record.get((kind, role_part, int(index)))


def _live_items(adapter, getter: str) -> list:
    """The live board items of ONE kind, or [] for an adapter that cannot read
    them (a test double) — "no copper" is then the honest answer, never a crash."""
    fn = getattr(adapter, getter, None)
    return list(fn() or ()) if fn else []


def _record_tail(key, cell_identity: Optional[str]) -> Optional[tuple]:
    """``(role_part, index)`` of a registry key whose TEMPLATE part IS this cell,
    or None (a malformed key / a command of some other record).

    ONE read of the key tail ``anchor|cell|role|index``, shared by the dry-run map
    :func:`_map_for`, the registry-only map :func:`registry_record_copper_map` and
    (through :func:`own_registry_entries`) «Select cell» — never a second copy."""
    parts = str(key or "").split("|")
    if len(parts) != 4 or parts[1] != cell_identity:
        return None
    try:
        return parts[2], int(parts[3])
    except ValueError:
        return None


def record_of(cell, kind: str, role_part: str, index: int):
    """The cell RECORD object a registry tail names, or None when the cell holds
    NO such record today — an ORPHAN key (its ``index`` is past the end of the
    list, the numbers having shifted after the list was edited) or a role the cell
    no longer has.

    THE one lookup of a record by ``(kind, role_part, index)``: :func:`record_points`
    reads the stored points through it, and the at-current-place adoption reads the
    record's own ``net`` / ``net_from_role`` through it for its chain guard
    (plan_2026_10_07_adopt_at_current_place, доделка 1б) — never a second lookup.
    Both levels production resolves are covered: a cell-level via/track under the
    ``__spoke__`` placeholder and a component's via under its role. A component
    carries vias only (``TemplateComponentSlot``), so a track key never names a
    role."""
    if cell is None:
        return None
    if kind == "via":
        if role_part == SPOKE_LEVEL_ROLE_PLACEHOLDER:
            records = list(getattr(cell, "vias", None) or ())
        else:
            slot = next((s for s in (getattr(cell, "components", None) or ())
                         if getattr(s, "role", None) == role_part), None)
            if slot is None:
                return None
            records = list(getattr(slot, "vias", None) or ())
    else:
        if role_part != SPOKE_LEVEL_ROLE_PLACEHOLDER:
            return None
        records = list(getattr(cell, "tracks", None) or ())
    if not 0 <= index < len(records):
        return None
    return records[index]


def record_points(cell, kind: str, role_part: str, index: int):
    """The STORED local points of the cell record a registry tail names, or None
    when the cell holds NO such record today — an ORPHAN key (its ``index`` is
    past the end of the list, the numbers having shifted after the list was
    edited) or a role the cell no longer has. The record itself is found by
    :func:`record_of` (ONE lookup, shared with the chain guard).

    One ``(along_mm, across_mm)`` for a via, two (start, end) for a track."""
    rec = record_of(cell, kind, role_part, index)
    if rec is None:
        return None
    if kind == "via":
        return ((float(rec.offset_along_mm), float(rec.offset_across_mm)),)
    return ((float(rec.start_along_mm), float(rec.start_across_mm)),
            (float(rec.end_along_mm), float(rec.end_across_mm)))


def _live_points(live_item) -> tuple:
    """``(x_mm, y_mm)`` of a live via's centre / a live track's two ends."""
    if hasattr(live_item, "position"):
        return ((live_item.position.x / MM, live_item.position.y / MM),)
    return ((live_item.start.x / MM, live_item.start.y / MM),
            (live_item.end.x / MM, live_item.end.y / MM))


def _max_point_distance(a: tuple, b: tuple) -> float:
    """max endpoint distance between two same-length point tuples. A track
    segment is UNORIENTED, so the orientation that fits better is taken — the
    SAME convention the refresh's `_greedy_nearest` uses."""
    if len(a) == 1:
        return math.dist(a[0], b[0])
    straight = max(math.dist(a[0], b[0]), math.dist(a[1], b[1]))
    flipped = max(math.dist(a[0], b[1]), math.dist(a[1], b[0]))
    return min(straight, flipped)


def pair_agrees_with_record(points, live_item, frame, tol_mm) -> bool:
    """True ⇔ the live copper lies WHERE THE RECORD PUTS IT, measured in the cell
    frame of the instance — the ONE place of comparison of the registry-only path
    (the same translation "Refresh geometry from selection" writes the cell
    through).

    ``points`` — what :func:`record_points` returned. None means the cell holds NO
    such record (an ORPHAN key, or a role it no longer has), and such a key is
    NEVER a pair: a shifted number must not be checked against the wrong record.
    ``frame`` — None means the place cannot be computed (no origin / the frame was
    not built): the pair is not accepted either. A NON-RIGID frame must not be
    passed here at all — the caller refuses every pair then, with the residual as
    the reason (``RIGID_TOLERANCE_MM`` is the tolerance of a rigid one).
    ``tol_mm`` — the caller's tolerance (``RIGID_TOLERANCE_MM``).

    Pure: mm floats in, a verdict out. READ-ONLY, like everything in this map."""
    if points is None or frame is None or live_item is None:
        return False
    expect = tuple(frame.point_to_world_mm(along, across)
                   for along, across in points)
    return _max_point_distance(expect, _live_points(live_item)) <= tol_mm


def not_checked_reasons(*, not_rigid: bool, frame_residual_mm,
                        disagreed: int, orphan_keys: int) -> list:
    """The honest reasons a pair the registry OFFERED was not accepted, as
    translated fragments — ONE wording for the doors that report them («Subtract
    selected copper»'s Log lines and «Select cell»'s own line), so the same
    refusal can never be described two different ways.

    Empty when everything the registry offered was either accepted or simply not
    on the board (the latter is a count of its own, not a refusal)."""
    out: list = []
    if not_rigid:
        if frame_residual_mm is None:
            out.append(_("the live cluster has no usable cell frame — registry "
                         "pairs are not checked"))
        else:
            out.append(_("the live cluster is not a rigid copy of the cell "
                         "(worst deviation {deviation} mm) — registry pairs are "
                         "not checked")
                       .format(deviation=f"{frame_residual_mm:.3f}"))
    if disagreed:
        out.append(_("{count} registry pair(s) do not sit where the record puts "
                     "them — not checked").format(count=disagreed))
    if orphan_keys:
        out.append(_("{count} registry key(s) point past the end of the cell's "
                     "record list — not checked").format(count=orphan_keys))
    return out


def cell_record_slots(cell) -> set:
    """``{(kind, role_part, index)}`` — every copper record the cell's OWN lists
    can hold: cell-level vias/tracks under the spoke placeholder and each
    component's vias under its role (the SAME two levels ``plan_subtraction``
    resolves). Shared by the registry-only map (to count the records the registry
    has no key for) and by the at-current-place adoption (to walk the records)."""
    slots: set = set()
    if cell is None:
        return slots
    for index in range(len(getattr(cell, "vias", None) or ())):
        slots.add(("via", SPOKE_LEVEL_ROLE_PLACEHOLDER, index))
    for index in range(len(getattr(cell, "tracks", None) or ())):
        slots.add(("track", SPOKE_LEVEL_ROLE_PLACEHOLDER, index))
    for component in (getattr(cell, "components", None) or ()):
        role = getattr(component, "role", None)
        if not role:
            continue
        for index in range(len(getattr(component, "vias", None) or ())):
            slots.add(("via", str(role), index))
    return slots


def own_instance_context(adapter, cfg, cell_name: str, cluster, sheet,
                         sheet_names=None, own_refs=None) -> tuple:
    """``(footprints, cell_identity, own_addresses, chosen_address, refs)`` for
    ONE instance — the ONE resolution of the inputs ``is_own_key`` needs.

    Moved here from ``gui/select_cell.py`` (plan_2026_10_07_refused_tree_matching):
    the dry-run map, the registry-only map and «Select cell» all resolve the
    instance the same way, so they can never disagree about "which registry keys
    are this cell's".

    ``refs`` — the chosen instance's component refs. ``own_refs`` may be given as
    EITHER a mapping ``{role: ref}`` (the shape the ``elif own_refs:`` branch reads
    its footprints from — an already-resolved instance, e.g. the at-current-place
    adoption's planner-identical role resolution) OR a plain iterable of refs (the
    «Select cell» shape, where the component set still comes from the cluster and
    ``own_refs`` only decides which ``anchor:<ref>`` keys are this cell's own).
    They are what makes an ``anchor:<ref>`` key this cell's own. A board that
    cannot be read yields no refs — an ``anchor:`` key is then simply not claimed,
    and the ``name:``/``role:`` branches still work."""
    from .cell_instance import resolve_context_footprints

    if cluster:
        footprints = list(resolve_context_footprints(
            adapter, _live_items(adapter, "get_footprints"), cluster, sheet,
            sheet_names or {}))
    elif own_refs:
        get_footprint = getattr(adapter, "get_footprint", None)
        footprints = ([fp for ref in own_refs.values()
                       if (fp := get_footprint(ref)) is not None]
                      if get_footprint else [])
    else:
        footprints = []
    # A mapping's refs are its VALUES — a role->ref map iterated directly would
    # give ROLES here and quietly break the anchor:<ref> test in is_own_key;
    # a plain iterable of refs is taken as-is («Select cell»'s shape).
    refs = frozenset(
        (own_refs.values() if isinstance(own_refs, Mapping) else own_refs)
        if own_refs is not None
        else [getattr(f, "ref", None) for f in footprints
              if getattr(f, "ref", None)])
    cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
    cell_uuid = getattr(cell, "uuid", None) if cell is not None else None
    cell_identity = record_key_part(cell_name, cell_uuid)
    own_addresses = cell_record_addresses(cfg, cell_name)
    chosen_address = (cluster, sheet)
    return footprints, cell_identity, own_addresses, chosen_address, refs


def own_registry_entries(via_entries, track_entries, cell_identity,
                         own_addresses, chosen_address, refs) -> list:
    """``[(kind, role_part, index, uuid), ...]`` — the OWN registry entries of
    ONE instance across BOTH registry files.

    ONE place for the loop «Select cell» used to carry inline: the filter is the
    SAME ``selection_narrowing.is_own_key`` and the tail is read by the SAME
    :func:`_record_tail`. ``kind`` comes from WHICH file the key came from, so a
    via key can never be filed as a track (and vice versa). A uuid present in both
    files is seen once (the last writer wins), exactly like
    ``registry.load_registry_entries``'s ``owner`` map."""
    owner: dict = {}
    for kind, entries in (("via", via_entries), ("track", track_entries)):
        for key, entry in (entries or {}).items():
            uuid = getattr(entry, "uuid", None)
            if uuid:
                owner[uuid] = (kind, key)
    out: list = []
    for uuid, (kind, key) in owner.items():
        if not is_own_key(key, cell_identity, own_addresses, chosen_address, refs):
            continue
        tail = _record_tail(key, cell_identity)
        if tail is None:
            continue
        out.append((kind, tail[0], tail[1], uuid))
    return out


def instance_record_copper_map(config_path: str, record_name: str,
                               timeout_ms: Optional[int],
                               cell_identity: Optional[str]) -> RecordCopperMap:
    """Dry-run ``record_name`` on the live board and report, per cell record, the
    live copper its command matched (registry uuid first, then exact geometry).

    A pipeline that cannot be prepared or planned raises — the CALLER turns that
    into "could not check" (an empty ``RecordCopperMap()``), never a subtraction.
    A REFUSED tree plans nothing: the caller then asks
    :func:`registry_record_copper_map` for the SAME map by the registry alone."""
    from .apply_pipeline import ApplyPipeline

    pipeline = None
    try:
        pipeline = ApplyPipeline(str(config_path), only=[record_name],
                                 dry_run=True, timeout_ms=timeout_ms)
        pipeline.run()
        vias, tracks = pipeline.plan_copper()
        return record_copper_map_for(pipeline.adapter, str(config_path),
                                     cell_identity, vias, tracks)
    finally:
        if pipeline is not None:
            pipeline.close()


def record_copper_map_for(adapter, config_path: str,
                          cell_identity: Optional[str],
                          planned_vias, planned_tracks) -> RecordCopperMap:
    """The SAME map for a caller that ALREADY holds an adapter (and the dry run's
    commands) — one rule, one place: the read-only match is ``_map_for``."""
    return _map_for(adapter, config_path, cell_identity, planned_vias,
                    planned_tracks)


def registry_record_copper_map(adapter, config_path: str, cfg, cell_name: str,
                               cluster, sheet, own_refs=None,
                               sheet_names=None) -> RecordCopperMap:
    """The «registry only» map of ONE instance — the fallback for a REFUSED tree.

    For every registry key ``is_own_key`` accepts for this instance, the record
    ``(kind, role_part, index)`` maps to the live copper the registry's stored
    uuid points at — AND the pair must sit where the record puts the copper, in
    the cell frame built from the instance's LIVE components. Two guards belong
    here because this path exists exactly where there is no current redraw to
    trust (plan_2026_10_07_registry_pair_frame_check, rules 2 and 3):

      * the place is checked only while the frame is RIGID
        (``residual_mm <= RIGID_TOLERANCE_MM``): a non-rigid cluster's frame is
        off by more than the step between neighbouring records, so it cannot tell
        a pair from its neighbour — then NO registry pair is accepted at all, and
        ``not_rigid``/``frame_residual_mm`` carry the reason to the caller's line;
      * a key whose ``index`` has NO record in the cell today (an ORPHAN — the
        numbers shifted after the list was edited) is never a pair and is counted
        in ``orphan_keys``.

    A key whose uuid is NOT on the board (the copper was removed or re-routed)
    maps to nothing — it must never be claimed, or the subtraction would "remove"
    a record whose copper is gone. ``source`` is ``"registry"``;
    ``without_registry`` counts this cell's records the registry has no key for,
    ``entries_total`` how many own keys were examined, ``disagreed`` the pairs the
    registry offered that do not sit where the record puts them.

    READ-ONLY: reads the two registry files and the board, writes nothing. The
    drift guard, the redraw plan and the config are not touched."""
    from .cell_frame import RIGID_TOLERANCE_MM
    from .cell_geometry_refresh import cell_frame_from_live, cell_slot_dicts

    cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
    footprints, cell_identity, own_addresses, chosen_address, refs = \
        own_instance_context(adapter, cfg, cell_name, cluster, sheet,
                             sheet_names=sheet_names, own_refs=own_refs)
    via_entries, track_entries, _owner = load_registry_entries(config_path, cfg)
    live = {"via": _live_items(adapter, "get_vias"),
            "track": _live_items(adapter, "get_tracks")}
    by_uuid = {kind: {getattr(item, "uuid", None): item for item in items}
               for kind, items in live.items()}
    entries = own_registry_entries(via_entries, track_entries, cell_identity,
                                   own_addresses, chosen_address, refs)
    without_registry = len(cell_record_slots(cell) - {
        (kind, role_part, index)
        for kind, role_part, index, _uuid in entries})

    frame = cell_frame_from_live(
        cell_slot_dicts(cell), footprints, adapter,
        origin_role=getattr(cell, "anchor_role", None))
    residual = None if frame is None else frame.residual_mm
    if frame is None or frame.residual_mm > RIGID_TOLERANCE_MM:
        # No frame / no rigid frame: not one pair can be checked by place, so
        # none is claimed — the caller prints the reason, never a lie.
        return RecordCopperMap(by_record={}, planned=0, source="registry",
                               without_registry=without_registry,
                               entries_total=len(entries), not_rigid=True,
                               frame_residual_mm=residual)

    by_record: dict = {}
    disagreed = orphan = 0
    for kind, role_part, index, uuid in entries:
        live_item = by_uuid[kind].get(uuid)
        if live_item is None:
            continue                     # the copper is gone — never claimed
        points = record_points(cell, kind, role_part, index)
        if points is None:
            orphan += 1                  # an ORPHAN key is never a pair
            continue
        if not pair_agrees_with_record(points, live_item, frame,
                                       RIGID_TOLERANCE_MM):
            disagreed += 1               # not where the record puts the copper
            continue
        by_record[(kind, role_part, index)] = uuid
    return RecordCopperMap(by_record=by_record, planned=len(by_record),
                           source="registry", without_registry=without_registry,
                           entries_total=len(entries), disagreed=disagreed,
                           orphan_keys=orphan, frame_residual_mm=residual)


def _map_for(adapter, config_path: str, cell_identity: Optional[str],
             planned_vias, planned_tracks) -> RecordCopperMap:
    """The read-only match of one dry run's commands against the live board.

    A tier-1 (registry uuid) match is taken only when the live item really lies
    where the COMMAND plans the copper (`accept_planned_match`, the registry's own
    predicate): the key stores the record's number at the LAST REDRAW, so a
    shifted number hands over the copper of ANOTHER record
    (plan_2026_10_07_registry_pair_frame_check, rule 1). Tier 2 is geometric by
    construction and needs no second check. A rejected pair is NOT in the map and
    is counted in ``disagreed`` — the caller names it as "not checked"."""
    via_path, trk_path = registry_paths_for_config(config_path)
    via_reg = PlacementRegistry(adapter, via_path)
    trk_reg = TrackRegistry(adapter, trk_path)

    by_record: dict = {}
    disagreed = 0
    for kind, reg, cmds, live in (
            ("via", via_reg, planned_vias, _live_items(adapter, "get_vias")),
            ("track", trk_reg, planned_tracks,
             _live_items(adapter, "get_tracks"))):
        for m in match_planned_copper(reg, list(cmds or ()), live_items=live):
            if m.live is None:
                continue
            if not accept_planned_match(reg, m):
                disagreed += 1
                continue
            tail = _record_tail(getattr(m.command, "registry_key", None),
                                cell_identity)
            if tail is None:
                # A command of some OTHER record in the same run (net_traces,
                # a chain, a thermal array): not this cell's record.
                continue
            by_record[(kind, tail[0], tail[1])] = getattr(m.live, "uuid", None)
    planned = len(planned_vias or ()) + len(planned_tracks or ())
    return RecordCopperMap(by_record=by_record, planned=planned,
                           disagreed=disagreed)
