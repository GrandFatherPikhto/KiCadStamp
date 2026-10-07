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

from dataclasses import dataclass, field
from typing import Optional

from .constants import SPOKE_LEVEL_ROLE_PLACEHOLDER
from .registry import (
    PlacementRegistry,
    TrackRegistry,
    load_registry_entries,
    record_key_part,
    registry_paths_for_config,
)
from .registry_match import match_planned_copper
from .selection_narrowing import cell_record_addresses, is_own_key

__all__ = [
    "RecordCopperMap",
    "instance_record_copper_map",
    "own_instance_context",
    "own_registry_entries",
    "record_copper_map_for",
    "registry_record_copper_map",
]


@dataclass(frozen=True)
class RecordCopperMap:
    """``{(kind, role_part, index): live_uuid}`` for ONE instance's dry run.

    ``planned`` — how many commands the dry run produced (0 = the record placed
    no copper: a refused tree, an unrealized record, a chain-only placement).
    For a map built by :func:`registry_record_copper_map` ``planned`` is the
    number of registry-matched entries and ``source`` is ``"registry"``.

    ``without_registry`` — for a registry-only map, how many of this cell's copper
    records the registry has NO key for. They cannot be checked this way, so the
    caller names the number instead of pretending the cell has no such copper.

    The caller must subtract NOTHING when ``empty`` and say why — "we could not
    check", never "the cell has no copper".
    """

    by_record: dict = field(default_factory=dict)
    planned: int = 0
    source: str = "dry_run"
    without_registry: int = 0

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


def _cell_record_slots(cell) -> set:
    """``{(kind, role_part, index)}`` — every copper record the cell's OWN lists
    can hold: cell-level vias/tracks under the spoke placeholder and each
    component's vias under its role (the SAME two levels ``plan_subtraction``
    resolves). Used to count the records the registry has no key for."""
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

    ``refs`` — the chosen instance's component refs (``own_refs`` when given, else
    the cluster's live footprints); they are what makes an ``anchor:<ref>`` key
    this cell's own. A board that cannot be read yields no refs — an ``anchor:``
    key is then simply not claimed, and the ``name:``/``role:`` branches still
    work."""
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
    refs = frozenset(
        own_refs if own_refs is not None
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
    uuid points at. A key whose uuid is NOT on the board (the copper was removed
    or re-routed) maps to nothing — it must never be claimed, or the subtraction
    would "remove" a record whose copper is gone. ``source`` is ``"registry"`` and
    ``without_registry`` counts this cell's records the registry has no key for.

    READ-ONLY: reads the two registry files and the board, writes nothing. The
    drift guard, the redraw plan and the config are not touched."""
    _footprints, cell_identity, own_addresses, chosen_address, refs = \
        own_instance_context(adapter, cfg, cell_name, cluster, sheet,
                             sheet_names=sheet_names, own_refs=own_refs)
    via_entries, track_entries, _owner = load_registry_entries(config_path, cfg)
    live_uuids = {
        "via": {getattr(v, "uuid", None)
                for v in _live_items(adapter, "get_vias")},
        "track": {getattr(t, "uuid", None)
                  for t in _live_items(adapter, "get_tracks")},
    }
    entries = own_registry_entries(via_entries, track_entries, cell_identity,
                                   own_addresses, chosen_address, refs)
    by_record: dict = {}
    checked: set = set()
    for kind, role_part, index, uuid in entries:
        checked.add((kind, role_part, index))
        if uuid in live_uuids[kind]:
            by_record[(kind, role_part, index)] = uuid
    cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
    without_registry = len(_cell_record_slots(cell) - checked)
    return RecordCopperMap(by_record=by_record, planned=len(by_record),
                           source="registry", without_registry=without_registry)


def _map_for(adapter, config_path: str, cell_identity: Optional[str],
             planned_vias, planned_tracks) -> RecordCopperMap:
    """The read-only match of one dry run's commands against the live board."""
    via_path, trk_path = registry_paths_for_config(config_path)
    via_reg = PlacementRegistry(adapter, via_path)
    trk_reg = TrackRegistry(adapter, trk_path)

    by_record: dict = {}
    for kind, reg, cmds, live in (
            ("via", via_reg, planned_vias, _live_items(adapter, "get_vias")),
            ("track", trk_reg, planned_tracks,
             _live_items(adapter, "get_tracks"))):
        for m in match_planned_copper(reg, list(cmds or ()), live_items=live):
            if m.live is None:
                continue
            tail = _record_tail(getattr(m.command, "registry_key", None),
                                cell_identity)
            if tail is None:
                # A command of some OTHER record in the same run (net_traces,
                # a chain, a thermal array): not this cell's record.
                continue
            by_record[(kind, tail[0], tail[1])] = getattr(m.live, "uuid", None)
    planned = len(planned_vias or ()) + len(planned_tracks or ())
    return RecordCopperMap(by_record=by_record, planned=planned)
