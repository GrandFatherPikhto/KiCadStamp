# gui/mixed_selection.py
"""The bridge between the LIVE board + the two registries and the pure
narrowing rule in `kicadstamp/selection_narrowing.py`
(plan_2026_10_04_refresh_mixed_cluster_selection, Denis 2026-10-04; reworked for
Н4, Denis 2026-10-05).

ONE function serves BOTH doors — "Update from selection" and "Import
vias/tracks from selection" — so the two can never narrow differently (plan
item 4: one function for every door, not a copy). It reads the adapter fields
and resolves each selected footprint's sheet chain, then hands plain records to
the pure rule; the registries are read HERE, on the worker thread (the plan:
reading the registry/config is the worker's job, not the UI thread's).

Н4 (Denis 2026-10-05, after the live DAC_BUF read): the instance is
``(cell cluster, sheet)`` and its COMPONENTS ARE TAKEN FROM THE BOARD, not from
the selection — a component of the instance outside the selection frame is
still read, and a role the cell does not have yet (D23/R70) is a NEW record,
not a refusal. The rule runs on ANY selection, clean or mixed; only an UNKNOWN
cell cluster keeps today's whole-selection path.

The copper is the SELECTED copper minus everything the registries recorded for
OTHER records (``subtract_foreign_copper``) minus every live ``net_traces:``
record's planned copper even when unregistered (``subtract_net_trace_copper``,
Н4 п.5а). The prelude also returns a ``CopperReadContext`` so the worker can
apply Н4 п.5 (delete a record only when the registry's uuid is ABSENT from the
board) — the decision itself lives in `kicadstamp/`.

Returns None when the cell's cluster is UNKNOWN (nothing to narrow by) — the
caller then keeps today's behaviour byte for byte. A truthy result always
carries the selection to plan on; a non-empty `refusal` means "build NOTHING,
print this red line" (the ambiguity / no-instance cases — plan item 33, no
dialog).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from kicadstamp.cell_instance import resolve_context_footprints
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.i18n import _
from kicadstamp.registry import (
    load_registry_entries,
    record_key_part,
)
from kicadstamp.selection_narrowing import (
    CopperReadContext,
    FootprintInfo,
    cell_clusters,
    cell_record_addresses,
    choose_instance,
    group_selection,
    subtract_foreign_copper,
    subtract_net_trace_copper,
)
from kicadstamp.sheet_names import resolve_sheet_path_names

logger = logging.getLogger(__name__)

SUCCESS = "success"
WARN = "warn"
ERROR = "error"


@dataclass
class MixedPrelude:
    """What a BY-CLUSTER read narrows to.

    footprints/vias/tracks — the live items to hand to the planner: the chosen
        instance's components (READ FROM THE BOARD, not the selection) + the
        KEPT copper.
    instance_footprints — the chosen instance's board components (the same
        objects), the component half of the selection-after-read.
    kept_copper — every live via/track that stayed in the read.
    copper_ctx — the data Н4 п.5 needs to split the unpaired records (None only
        for a refusal result, where nothing is planned anyway).
    log_lines — ((text, level), ...) the finish handler prints in the Log.
    refusal — a non-empty text means the caller must build nothing and print it
        red (the ambiguity / no-instance cases)."""

    footprints: list
    vias: list
    tracks: list
    instance_footprints: list
    kept_copper: list
    copper_ctx: Optional[CopperReadContext] = None
    log_lines: list = field(default_factory=list)
    refusal: Optional[str] = None


def _component_roles(components) -> set:
    return {c.get("role") for c in (components or ()) if c.get("role")}


def _instance_line(cluster, sheet, others) -> str:
    skipped = sum(count for _key, count in others)
    names = ", ".join(sorted({str(key[0]) for key, _count in others})) or "-"
    return _("read instance {cluster} on {sheet}; skipped {count} component(s) "
             "of other clusters: {clusters}").format(
        cluster=cluster, sheet=sheet if sheet is not None else _("(no sheet)"),
        count=skipped, clusters=names)


def _subtraction_line(parts) -> Optional[str]:
    """One Log line naming every unit of foreign copper subtracted (registry
    records + unregistered net_traces records)."""
    merged: dict[str, int] = {}
    for sub in parts:
        for label, count in sub.report:
            merged[label] = merged.get(label, 0) + count
    total = sum(len(sub.removed) for sub in parts)
    if not merged:
        return None
    records = ", ".join(f"{label}: {count}"
                        for label, count in sorted(merged.items()))
    return _("subtracted from selection: {count} item(s) — {records}").format(
        count=total, records=records)


def _ambiguity_refusal(cell_name, choice) -> str:
    parts = []
    for key, members in choice.candidate_groups:
        cluster, sheet = key
        refs = ", ".join(sorted(str(getattr(m.item, "ref", "?")) for m in members))
        parts.append(_("{cluster} on {sheet} ({refs})").format(
            cluster=cluster,
            sheet=sheet if sheet is not None else _("(no sheet)"), refs=refs))
    return _("the selection mixes {count} instances of cell {cell!r}: "
             "{candidates} — select ONE instance and read again").format(
        count=len(choice.candidate_groups), cell=cell_name,
        candidates="; ".join(parts))


def _refusal_preamble(text: str, footprints, vias, tracks) -> MixedPrelude:
    return MixedPrelude(list(footprints), list(vias), list(tracks), [], [],
                        refusal=text)


def narrow_mixed_selection(*, config_path: str, adapter: Any, footprints: list,
                           vias: list, tracks: list, cfg, sheet_names,
                           cell_name: str, cell_roles,
                           remembered_cluster=None, remembered_sheet=None
                           ) -> Optional[MixedPrelude]:
    """Narrow ANY selection (clean or mixed) to ONE cell instance, or return
    None when the cell's cluster is UNKNOWN (the caller keeps today's path)."""
    if not cell_name:
        return None
    entities = getattr(cfg, "entities", ()) or ()
    clusters = cell_clusters(cfg, cell_name)
    if not clusters and remembered_cluster:
        clusters = {str(remembered_cluster)}
    if not clusters:
        return None  # unknown cell cluster -> today's whole-selection path

    infos = [FootprintInfo(
        item=fp,
        role=adapter.get_field_value(fp, ROLE_FIELD_NAME),
        cluster=adapter.get_field_value(fp, CLUSTER_FIELD_NAME),
        sheet=tuple(resolve_sheet_path_names(fp, sheet_names) or ()))
        for fp in footprints]
    groups = group_selection(infos, entities)
    choice = choose_instance(groups, cell_roles, clusters)

    if choice.chosen_key is not None:
        chosen_cluster, chosen_sheet = choice.chosen_key
        others = choice.others
    elif choice.no_own_cluster:
        if not (remembered_cluster and remembered_sheet):
            return _refusal_preamble(
                _("no component of cluster {cluster!r} is in the selection — "
                  "select the instance of cell {cell!r}, or remember one from "
                  "the cell-anchor page").format(
                      cluster=", ".join(sorted(clusters)), cell=cell_name),
                footprints, vias, tracks)
        chosen_cluster, chosen_sheet = str(remembered_cluster), remembered_sheet
        others = choice.others
    elif len(choice.candidate_groups) > 1:
        return _refusal_preamble(_ambiguity_refusal(cell_name, choice),
                                 footprints, vias, tracks)
    else:
        return None  # zero role-only candidates -> today's (role) refusal

    # Н4.2: the instance's components come from the BOARD, never the selection.
    try:
        board_footprints = adapter.get_footprints()
    except Exception:  # noqa: BLE001 — a board read must not crash the narrow
        logger.exception("could not read the board footprints for the instance")
        return None
    instance_fps = resolve_context_footprints(
        adapter, board_footprints, chosen_cluster, chosen_sheet, sheet_names)
    if not instance_fps:
        return _refusal_preamble(
            _("cluster {cluster!r} on {sheet} is not on the current board — "
              "select the instance by hand").format(
                  cluster=chosen_cluster,
                  sheet=chosen_sheet if chosen_sheet is not None else _("(no sheet)")),
            footprints, vias, tracks)

    chosen_refs = [r for r in (getattr(fp, "ref", None) for fp in instance_fps) if r]

    cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
    cell_uuid = getattr(cell, "uuid", None) if cell is not None else None
    cell_identity = record_key_part(cell_name, cell_uuid)
    own_addresses = cell_record_addresses(cfg, cell_name)
    via_entries, track_entries, owner = load_registry_entries(config_path, cfg)
    chosen_address = (chosen_cluster, chosen_sheet)

    sub_v = subtract_foreign_copper(vias, owner, cell_identity, own_addresses,
                                    chosen_address, chosen_refs)
    sub_t = subtract_foreign_copper(tracks, owner, cell_identity, own_addresses,
                                    chosen_address, chosen_refs)
    # Н4 п.5а: inter-cluster copper recorded in `net_traces:` but NOT yet in the
    # registry is subtracted too (the hole: extract writes the record, the
    # registry learns the uuids only at redraw).
    net_v = subtract_net_trace_copper(
        list(sub_v.kept), getattr(cfg, "net_traces", None), adapter,
        via_entries=via_entries, track_entries=track_entries,
        sheet_names=sheet_names)
    net_t = subtract_net_trace_copper(
        list(sub_t.kept), getattr(cfg, "net_traces", None), adapter,
        via_entries=via_entries, track_entries=track_entries,
        sheet_names=sheet_names)

    # Ф1: a failed board-copper read must NOT look like "the board is empty" —
    # the deletion rule then deletes nothing (board_read_ok=False).
    board_read_ok = True
    try:
        board_via_uuids = frozenset(getattr(v, "uuid", None)
                                    for v in adapter.get_vias())
        board_track_uuids = frozenset(getattr(t, "uuid", None)
                                      for t in adapter.get_tracks())
    except Exception:  # noqa: BLE001 — a read must not crash the narrow
        logger.exception("could not read the board copper for the live-UUID rule")
        board_read_ok = False
        board_via_uuids = frozenset()
        board_track_uuids = frozenset()

    ctx = CopperReadContext(
        cell_identity=cell_identity, own_addresses=own_addresses,
        chosen_address=chosen_address, chosen_refs=frozenset(chosen_refs),
        via_entries=via_entries, track_entries=track_entries,
        board_via_uuids=board_via_uuids, board_track_uuids=board_track_uuids,
        vias=list(vias), tracks=list(tracks), board_read_ok=board_read_ok)

    lines = [(_instance_line(chosen_cluster, chosen_sheet, others), SUCCESS)]
    # Ф3: a net_traces record whose anchor could not be resolved says so.
    for note in list(net_v.notes) + list(net_t.notes):
        lines.append((note, WARN))
    # м1: the "could not read the board copper" line is NOT added here — the
    # worker gets it from ONE place only (apply_live_copper_rule), so refresh and
    # import each print it exactly once.
    subtraction = _subtraction_line([sub_v, sub_t, net_v, net_t])
    if subtraction:
        lines.append((subtraction, SUCCESS))

    return MixedPrelude(
        footprints=list(instance_fps),
        vias=list(net_v.kept),
        tracks=list(net_t.kept),
        instance_footprints=list(instance_fps),
        kept_copper=list(net_v.kept) + list(net_t.kept),
        copper_ctx=ctx,
        log_lines=lines)
