# gui/docks/reead.py
"""
Pure (Qt-free) logic for "Tools -> Re-read selected..." (2026-08-31, plan
reead_selected_dialog.md).

The Extract dock's old "Re-read" was anchored on an extract profile and failed
live for two reasons: the profile stores neither the sheet nor the Cluster, and
a cluster like PIF_AVDD is placed once per hierarchical sheet (Channel_0/1/2),
so no automatic name-matching can tell the instances apart. The selection IS the
unambiguous truth, so re-read works from it: this module finds which Clusters of
the current selection are FULLY selected and maps each to its Entity.

Definitions:
- A footprint belongs to a sheet instance when the Entity's `sheet` value
  appears in the footprint's resolved sheet chain (the same
  'sheet in fp_sheet' convention Board.select(sheet=) uses — see explore.py).
- A Cluster instance is FULLY selected when EVERY board component of that
  (Cluster tag, sheet) instance is in the selection.
- The mapping is Entity (entities: with the matching cluster+sheet) -> the
  Entity's cell -> the extract_profiles recipe keyed by that cell (if any).
  Footprints that match no Entity fall back to a best-effort sheet identity
  (first non-None chain segment).

Testable without Qt.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from kicadstamp.explore import Selected
from kicadstamp.sheet_names import resolve_sheet_path_names


@dataclass
class ReReadCluster:
    """One fully-selected Cluster instance, ready for a batch re-read job."""
    cluster: str
    sheet: Optional[str]
    entity_name: Optional[str]
    cell: str
    profile_key: Optional[str]
    refs: list[str] = field(default_factory=list)


def _slugify(text: str) -> str:
    """Same slug as the retired Extract dock's _slugify — a Cluster tag ->
    default cell name when no Entity provides one."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", text.strip().lower()).strip("_")


def _sheet_chain(s: Selected, sheet_names: Optional[Mapping[str, str]] = None) -> list:
    """The footprint's resolved sheet chain. The GUI's BoardConnection connects
    WITHOUT schematic_dir, so its snapshot's `Selected.sheet` chains are all
    None — but each Selected carries the raw footprint, so when `sheet_names`
    (from the config's RuntimeContext) is available we re-resolve the real
    names (Channel_0/1/2...) on the fly; otherwise fall back to the snapshot's
    own chain (exact in CLI/tests)."""
    if sheet_names:
        fp = getattr(s, "fp", None)
        if fp is not None:
            try:
                return resolve_sheet_path_names(fp, sheet_names)
            except Exception:
                pass
    return list(s.sheet or ())


def sheet_of(fp_sheet) -> Optional[str]:
    """Canonical sheet identity of a footprint's sheet chain: the first non-None
    segment. Used only as a best-effort fallback when no Entity matches."""
    for seg in fp_sheet or ():
        if seg:
            return seg
    return None


def match_entity(entities, cluster: str, fp_sheet) -> Optional[Any]:
    """The Entity (entities:) that places this footprint: same Cluster tag AND
    entity.sheet appears in the footprint's sheet chain (the same
    'sheet in fp_sheet' convention Board.select(sheet=) uses)."""
    for e in entities:
        if e.cluster == cluster and e.sheet and e.sheet in (fp_sheet or ()):
            return e
    return None


def instance_sheet(fp: Selected, entities, sheet_names: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Sheet identity of a footprint for grouping: its matched Entity's sheet
    (exact for hierarchical channels like Channel_0/1/2), else the first
    non-None chain segment as a best-effort fallback."""
    chain = _sheet_chain(fp, sheet_names)
    e = match_entity(entities, fp.cluster or "", chain)
    if e is not None:
        return e.sheet
    return sheet_of(chain)


def group_selected(selected: Iterable[Selected], entities,
                   sheet_names: Optional[Mapping[str, str]] = None) -> dict:
    """{(cluster, sheet): [Selected...]} — selected footprints grouped by
    (Cluster tag, instance sheet). Footprints without a Cluster tag are ignored
    (vias/tracks are handled separately, from the raw items)."""
    groups: dict = {}
    for s in selected:
        if not s.cluster:
            continue
        key = (s.cluster, instance_sheet(s, entities, sheet_names))
        groups.setdefault(key, []).append(s)
    return groups


def fully_selected_clusters(selected: Iterable[Selected], snapshot: Iterable[Selected],
                            entities, profile_keys: Iterable[str],
                            sheet_names: Optional[Mapping[str, str]] = None) -> list[ReReadCluster]:
    """Clusters of the selection that are FULLY selected: every board component
    of the (Cluster tag, sheet) instance (snapshot footprints with the same
    Cluster whose sheet chain contains the group's sheet) is in the selection.
    Each maps to its Entity -> cell -> extract_profiles key (if any).
    `sheet_names` (RuntimeContext) re-resolves the GUI snapshot's otherwise-None
    sheet chains from the raw footprints — see _sheet_chain."""
    selected_by_ref = {s.ref for s in selected}
    groups = group_selected(selected, entities, sheet_names)
    profile_keys = set(profile_keys)
    clusters: list[ReReadCluster] = []
    for (cluster, sheet), members in groups.items():
        if not sheet:
            continue
        snapshot_members = [s for s in snapshot
                            if s.cluster == cluster and sheet in _sheet_chain(s, sheet_names)]
        if not snapshot_members:
            continue
        if not all(s.ref in selected_by_ref for s in snapshot_members):
            continue  # not fully selected
        entity = next((e for e in entities
                       if e.cluster == cluster and e.sheet == sheet), None)
        cell = entity.cell if entity is not None else _slugify(cluster)
        clusters.append(ReReadCluster(
            cluster=cluster,
            sheet=sheet,
            entity_name=entity.name if entity is not None else None,
            cell=cell,
            profile_key=cell if cell in profile_keys else None,
            refs=[s.ref for s in members],
        ))
    clusters.sort(key=lambda c: (c.cluster, c.sheet or ""))
    return clusters
