# gui/mixed_selection.py
"""The bridge between the LIVE board + the two registries and the pure
narrowing rule in `kicadstamp/selection_narrowing.py`
(plan_2026_10_04_refresh_mixed_cluster_selection, Denis 2026-10-04).

ONE function serves BOTH doors — "Update from selection" and "Import
vias/tracks from selection" — so the two can never narrow differently (plan
item 4: one function for every door, not a copy). It reads the adapter fields
and resolves each selected footprint's sheet chain, then hands plain records to
the pure rule; the registries are read HERE, on the worker thread (the plan:
reading the registry/config is the worker's job, not the UI thread's).

Returns None when the selection is NOT mixed (zero or one cluster instance) —
the caller then keeps today's behaviour byte for byte. A truthy result always
carries the selection to plan on; a non-empty `refusal` means "build NOTHING,
print this red line" (the more-than-one-candidate ambiguity — plan item 33, no
dialog).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.i18n import _
from kicadstamp.registry import (
    load_registry,
    load_track_registry,
    record_key_part,
    registry_paths_for_config,
)
from kicadstamp.selection_narrowing import (
    FootprintInfo,
    cell_clusters,
    cell_record_addresses,
    choose_instance,
    group_selection,
    subtract_foreign_copper,
)
from kicadstamp.sheet_names import resolve_sheet_path_names

logger = logging.getLogger(__name__)

SUCCESS = "success"
WARN = "warn"
ERROR = "error"


@dataclass
class MixedPrelude:
    """What a MIXED selection narrows to.

    footprints/vias/tracks — the live items to hand to the planner (the chosen
        instance's components + the KEPT copper).
    instance_footprints — the chosen instance's components (the same Footprint
        objects), the component half of the selection-after-read.
    kept_copper — every live via/track that stayed in the read (plan item 3).
    log_lines — ((text, level), ...) the finish handler prints in the Log.
    refusal — a non-empty text means the caller must build nothing and print it
        red (the more-than-one-candidate ambiguity)."""

    footprints: list
    vias: list
    tracks: list
    instance_footprints: list
    kept_copper: list
    log_lines: list = field(default_factory=list)
    refusal: Optional[str] = None


def _component_roles(components) -> set:
    return {c.get("role") for c in (components or ()) if c.get("role")}


def _registry_owner(config_path: str, cfg) -> dict:
    """{copper uuid -> registry key} over BOTH registry files, read with the
    product's own loaders (schema/refusal checks included). The two paths come
    from the product's ONE decision (registry_paths_for_config) with the
    config's explicit registry_path:/track_registry_path: values."""
    via_path, trk_path = registry_paths_for_config(
        str(config_path), getattr(cfg, "registry_path", None),
        getattr(cfg, "track_registry_path", None))
    owner: dict[str, str] = {}
    for key, entry in load_registry(via_path).items():
        uuid = getattr(entry, "uuid", None)
        if uuid:
            owner[uuid] = key
    for key, entry in load_track_registry(trk_path).items():
        uuid = getattr(entry, "uuid", None)
        if uuid:
            owner[uuid] = key
    return owner


def _instance_line(cluster, sheet, others) -> str:
    skipped = sum(count for _key, count in others)
    names = ", ".join(sorted({str(key[0]) for key, _count in others})) or "-"
    return _("read instance {cluster} on {sheet}; skipped {count} component(s) "
             "of other clusters: {clusters}").format(
        cluster=cluster, sheet=sheet if sheet is not None else _("(no sheet)"),
        count=skipped, clusters=names)


def _subtraction_line(sub_v, sub_t) -> Optional[str]:
    if not sub_v.report and not sub_t.report:
        return None
    merged: dict[str, int] = {}
    for label, count in list(sub_v.report) + list(sub_t.report):
        merged[label] = merged.get(label, 0) + count
    records = ", ".join(f"{label}: {count}"
                        for label, count in sorted(merged.items()))
    return _("subtracted from selection: {vias} via(s), {tracks} track(s) — "
             "{records}").format(vias=len(sub_v.removed),
                                 tracks=len(sub_t.removed), records=records)


def _ambiguity_refusal(cell_name, choice) -> str:
    parts = []
    for key, members in choice.candidate_groups:
        cluster, sheet = key
        refs = ", ".join(sorted(str(m.item.ref) for m in members))
        parts.append(_("{cluster} on {sheet} ({refs})").format(
            cluster=cluster,
            sheet=sheet if sheet is not None else _("(no sheet)"), refs=refs))
    return _("the selection mixes {count} instances of cell {cell!r}: "
             "{candidates} — select ONE instance and read again").format(
        count=len(choice.candidate_groups), cell=cell_name,
        candidates="; ".join(parts))


def narrow_mixed_selection(*, config_path: str, adapter: Any, footprints: list,
                           vias: list, tracks: list, cfg, sheet_names,
                           cell_name: str, cell_roles, remembered_cluster=None
                           ) -> Optional[MixedPrelude]:
    """Narrow a MIXED selection to ONE cell instance, or return None when the
    selection is NOT mixed (the caller keeps today's behaviour)."""
    if not cell_name or not footprints:
        return None
    entities = getattr(cfg, "entities", ()) or ()
    infos = []
    for fp in footprints:
        infos.append(FootprintInfo(
            item=fp,
            role=adapter.get_field_value(fp, ROLE_FIELD_NAME),
            cluster=adapter.get_field_value(fp, CLUSTER_FIELD_NAME),
            sheet=tuple(resolve_sheet_path_names(fp, sheet_names) or ())))
    groups = group_selection(infos, entities)
    if len(groups) <= 1:
        return None  # clean selection (or none has a Cluster) — today's path

    clusters = cell_clusters(cfg, cell_name)
    if not clusters and remembered_cluster:
        clusters = {str(remembered_cluster)}
    choice = choose_instance(groups, cell_roles, clusters)
    if choice.chosen_key is None:
        if len(choice.candidate_groups) > 1:
            return MixedPrelude(footprints, vias, tracks, [], [],
                                refusal=_ambiguity_refusal(cell_name, choice))
        return None  # zero candidates -> today's (role) refusal

    chosen_cluster, chosen_sheet = choice.chosen_key
    instance_fps = [m.item for m in choice.members]

    cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
    cell_uuid = getattr(cell, "uuid", None) if cell is not None else None
    cell_identity = record_key_part(cell_name, cell_uuid)
    own_addresses = cell_record_addresses(cfg, cell_name)
    owner = _registry_owner(config_path, cfg)
    chosen_address = (chosen_cluster, chosen_sheet)
    sub_v = subtract_foreign_copper(vias, owner, cell_identity, own_addresses,
                                    chosen_address)
    sub_t = subtract_foreign_copper(tracks, owner, cell_identity, own_addresses,
                                    chosen_address)

    lines = [(_instance_line(chosen_cluster, chosen_sheet, choice.others),
              SUCCESS)]
    subtraction = _subtraction_line(sub_v, sub_t)
    if subtraction:
        lines.append((subtraction, SUCCESS))

    return MixedPrelude(
        footprints=instance_fps,
        vias=list(sub_v.kept),
        tracks=list(sub_t.kept),
        instance_footprints=instance_fps,
        kept_copper=list(sub_v.kept) + list(sub_t.kept),
        log_lines=lines)
