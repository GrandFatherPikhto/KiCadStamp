# kicadstamp/adopt_at_current_place.py
"""«Adopt the copper at its CURRENT place, before the redraw» — the second half
of the Bug 3 decision (Denis, 2026-09-05: "adopt current copper BEFORE the move").

WHY (plan_2026_10_07_adopt_at_current_place): ``registry.adopt_matching_unowned``
runs AFTER the component moves and claims copper that already sits at the
PLANNED (new) place. An anchor that moved leaves its copper at the OLD place, so
that adoption cannot see it, ``reconcile`` draws new copper and the old copper
stays orphaned — the "trail". This pass runs BEFORE any component moves and binds
each cell record to the live copper that lies where the record puts it RIGHT NOW.

HOW it stays a SUBSET of the plan's keys (the one fatal risk): the key is built
by the SAME builders the planner uses — ``clone_registry_identity`` (anchor_id +
cell_key_part) then ``make_registry_key`` (registry.py:102). A key the run's plan
does not produce is treated by ``BaseRegistry.reconcile`` as genuinely stale and
its copper is DELETED, not left as a trail. ``clone_registry_identity`` and the
planner's ``_resolve_content`` share ``cell_key_part_for`` + ``clone_anchor_id``,
so the two cannot drift.

The place is read from the RECORD + the live cell frame — never from the planned
commands (those do not exist yet at this point; they are planned after the move).
That is why no reordering of the pipeline is needed.

READ/decide here is pure; the ONLY write is ``reg.adopt_live`` (the registry's own
schema owner), and only when ``write=True`` (a dry run passes False and counts
"would adopt").

Scope v1 (deliberately NOT touched): net_traces, chains, thermal_via_arrays,
imprints, coordinate_placements / component nodes, points — each has its own path
and its own key scheme. LIMITATION: a NON-RIGID live cluster (e.g. the live `fpga`
at 2.56 mm) cannot tell a record from its neighbour, so the whole instance is
skipped and its trail — if any — remains (v2 would use a per-component local frame;
a separate task)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .i18n import _

logger = logging.getLogger(__name__)

__all__ = ["AdoptionReport", "adopt_cell_copper_at_current_place"]


@dataclass
class AdoptionReport:
    """What the pass decided, in counts (no writes reported here).

    ``adopted`` counts records bound to a live item (a NEW key or a rebind of a
    stale one) — the number the caller prints as "adopted" / "would adopt".
    ``instances`` — how many rigid instances were actually examined;
    ``skipped_not_rigid`` / ``no_frame`` — instances the pass could not measure
    (the behaviour is unchanged for them); ``ambiguous`` — a live item two records
    (or two live items one record) claimed: taken by no one."""

    adopted: int = 0
    vias: int = 0
    tracks: int = 0
    instances: int = 0
    skipped_not_rigid: int = 0
    no_frame: int = 0
    ambiguous: int = 0

    @property
    def empty(self) -> bool:
        return self.adopted == 0


def _live(adapter, getter: str) -> list:
    """The live board items of one kind, or [] for an adapter that cannot read
    them (a test double) — "no copper" is then the honest answer, never a crash."""
    fn = getattr(adapter, getter, None)
    return list(fn() or ()) if fn else []


def _entry_from_live(kind: str, item):
    """The registry entry for a LIVE board item, in the SAME schema the registry
    persists (``RegistryEntry`` / ``TrackRegistryEntry`` in registry.py — their
    owner). This is the at-current-place adoption's counterpart of
    ``registry._build_entry`` (which reads a planned command); the geometry is the
    item's ACTUAL place, exactly what ``record_created`` stores for it. Kept here,
    not in registry.py, so that file stays under the 800-line rule (§45)."""
    from .registry import RegistryEntry, TrackRegistryEntry
    from .utils.layers import layer_to_str
    from .utils.units import MM
    if kind == "via":
        return RegistryEntry(uuid=item.uuid, x_mm=item.position.x / MM,
                             y_mm=item.position.y / MM, net=item.net_name,
                             drill_mm=item.drill_mm, diameter_mm=item.diameter_mm)
    return TrackRegistryEntry(uuid=item.uuid, start_x_mm=item.start.x / MM,
                              start_y_mm=item.start.y / MM, end_x_mm=item.end.x / MM,
                              end_y_mm=item.end.y / MM, width_mm=item.width_mm,
                              net=item.net_name, layer=layer_to_str(item.layer))


def adopt_cell_copper_at_current_place(adapter, cfg, items, via_reg, track_reg,
                                       *, write: bool) -> AdoptionReport:
    """Run the at-current-place adoption over the RUN'S OWN cell instances.

    ``items`` — the resolved execution order AFTER --only/--cluster narrowing
    (``ApplyPipeline.items``): only its ``kind == "clone"`` instances are
    examined, so a foreign instance of the same cell is never touched. ``adapter``
    must be read at the PRE-move board. ``write`` — False on a dry run: everything
    is decided and counted, nothing is saved.

    SAFE BY CONSTRUCTION, like ``adopt_matching_unowned``: never deletes; a live
    item owned by ANY registry entry is never taken; a key whose stored uuid is
    STILL live is left alone (reconcile will move its copper); a key whose uuid is
    gone from the board may be rebound; an ambiguous claim is taken by no one."""
    from .absent_copper_prune import (
        cell_record_slots,
        own_instance_context,
        pair_agrees_with_record,
        record_points,
    )
    from .cell_frame import RIGID_TOLERANCE_MM
    from .cell_geometry_refresh import cell_frame_from_live, cell_slot_dicts
    from .constants import SPOKE_LEVEL_ROLE_PLACEHOLDER
    from .placement.services.clone_position_calculator import clone_registry_identity
    from .registry import make_registry_key

    clones = [it.obj for it in (items or ()) if getattr(it, "kind", None) == "clone"]
    report = AdoptionReport()
    if not clones:
        return report

    live = {"via": _live(adapter, "get_vias"),
            "track": _live(adapter, "get_tracks")}
    live_uuid = {kind: {getattr(i, "uuid", None) for i in ix}
                 for kind, ix in live.items()}
    regs = {"via": via_reg, "track": track_reg}
    # Every uuid the registry files already own (either kind) is off limits: an
    # item another record claims must never be adopted by geometry.
    owned = {getattr(e, "uuid", None)
             for reg in (via_reg, track_reg) for e in reg.entries.values()}

    # The decision is filed by (kind, key): a via and a track of the SAME record
    # carry the SAME key string (they live in different registry files), so a
    # key-only map would let one overwrite the other.
    key_to_item: dict = {}          # (kind, key) -> live_item
    uuid_to_keys: dict = {}         # live uuid -> {(kind, key) that claimed it}

    for clone in clones:
        anchor_id, cell_name, cell_key_part = clone_registry_identity(clone, cfg.cells)
        cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
        if cell is None or cell_key_part is None:
            continue
        cluster, sheet = getattr(clone, "cluster", None), getattr(clone, "sheet", None)
        footprints = own_instance_context(adapter, cfg, cell_name, cluster, sheet)[0]
        frame = cell_frame_from_live(cell_slot_dicts(cell), footprints, adapter,
                                     origin_role=getattr(cell, "anchor_role", None))
        if frame is None:
            report.no_frame += 1
            logger.warning(_("Adopt at current place: no live cell frame for cell "
                             "{cell} on {cluster} — its copper is not adopted")
                           .format(cell=cell_name, cluster=cluster))
            continue
        if frame.residual_mm > RIGID_TOLERANCE_MM:
            report.skipped_not_rigid += 1
            logger.warning(_("Adopt at current place: cell {cell} on {cluster} is not a "
                             "rigid copy of the cell (worst deviation {deviation} mm) — "
                             "its copper is not adopted, a trail may remain")
                           .format(cell=cell_name, cluster=cluster,
                                   deviation=f"{frame.residual_mm:.3f}"))
            continue
        report.instances += 1

        for kind, role_part, index in sorted(cell_record_slots(cell)):
            reg = regs[kind]
            role = None if role_part == SPOKE_LEVEL_ROLE_PLACEHOLDER else role_part
            key = make_registry_key(anchor_id, cell_key_part, role, index)
            entry = reg.entries.get(key)
            if entry is not None and getattr(entry, "uuid", None) in live_uuid[kind]:
                # The record's own copper is on the board — reconcile will move it
                # by uuid. Never touch a live entry, or the move would be lost.
                continue
            points = record_points(cell, kind, role_part, index)
            if points is None:
                continue
            matches = [item for item in live[kind]
                       if getattr(item, "uuid", None) not in owned
                       and pair_agrees_with_record(points, item, frame, RIGID_TOLERANCE_MM)]
            if not matches:
                continue
            if len(matches) > 1:
                report.ambiguous += 1
                logger.warning(_("Adopt at current place: {count} live {kind}(s) lie where "
                                 "record {key} puts its copper — ambiguous, none taken")
                               .format(count=len(matches), kind=kind, key=key))
                continue
            item = matches[0]
            uuid = getattr(item, "uuid", None)
            key_to_item[(kind, key)] = item
            uuid_to_keys.setdefault(uuid, set()).add((kind, key))

    # One live object claimed by two records: nobody takes it (the plan's rule).
    for uuid, claims in uuid_to_keys.items():
        if len(claims) > 1:
            report.ambiguous += 1
            logger.warning(_("Adopt at current place: live {uuid} is claimed by {count} "
                             "records ({keys}) — none takes it")
                           .format(uuid=uuid, count=len(claims),
                                   keys=", ".join(sorted(key for _kind, key in claims))))

    for (kind, key), item in key_to_item.items():
        if len(uuid_to_keys.get(getattr(item, "uuid", None), ())) > 1:
            continue
        if write:
            regs[kind].adopt_live(key, _entry_from_live(kind, item))
        report.adopted += 1
        if kind == "via":
            report.vias += 1
        else:
            report.tracks += 1

    if report.adopted:
        logger.info(_("Adopt at current place: {count} record(s) bound to their live "
                      "copper ({vias} via(s), {tracks} track(s))")
                    .format(count=report.adopted, vias=report.vias, tracks=report.tracks))
    return report
