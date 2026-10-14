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
from kicadstamp.sheet_names import (
    resolve_sheet_path_names,
    sheet_in_path,
    sheet_of_names,
)


@dataclass
class ReReadCluster:
    """One fully-selected Cluster instance, ready for a batch re-read job."""
    cluster: str
    sheet: Optional[str]
    entity_name: Optional[str]
    cell: str
    profile_key: Optional[str]
    refs: list[str] = field(default_factory=list)


# Why a selected (Cluster, sheet) group was NOT accepted as fully selected.
# Structured, translation-free — the GUI turns these into a concrete message
# (gui/docks/extract_diagnostics.py); the engine never knows about Qt or gettext
# (plan_2026_09_11_extract_selection_diagnostics V.2).
REASON_NO_CLUSTER_TAG = "no_cluster_tag"        # (0) footprint has no Cluster field
REASON_SHEET_UNRESOLVED = "sheet_unresolved"    # (1) sheet did not resolve
REASON_SHEET_MAP_EMPTY = "sheet_map_empty"      # (1) ... because no schematic_dir
REASON_PAIR_NOT_IN_SNAPSHOT = "pair_not_in_snapshot"  # (2) (Cluster, sheet) unknown
REASON_PARTIAL = "partial"                      # (3) not every member selected


@dataclass
class ClusterRejection:
    """One reason a group of selected footprints was dropped from the
    fully-selected cluster list. `reason` is one of the REASON_* constants;
    the remaining fields carry whatever that reason needs to be explained
    (plan V.2). Never text: formatting/translation stays in the GUI."""
    reason: str
    cluster: Optional[str] = None
    sheet: Optional[str] = None
    # (3) "not fully selected": how many of the instance's components are in
    # the selection and which refs are missing.
    selected: int = 0
    total: int = 0
    missing: list[str] = field(default_factory=list)
    # Refs involved, for gates with no single (cluster, sheet): the untagged
    # footprints' refs (0), or a group's selected refs (1)/(2).
    refs: list[str] = field(default_factory=list)


def _slugify(text: str) -> str:
    """Same slug as the retired Extract dock's _slugify — a Cluster tag ->
    default cell name when no Entity provides one."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", text.strip().lower()).strip("_")


def _materialize_sheet_names(sheet_names):
    """A concrete {uuid: name} dict from whatever the caller passed.

    The RuntimeContext carries a lazy LazySheetNameMap (a Mapping that is, by
    design, ALWAYS truthy and parses the *.kicad_sch files only on first use),
    while the GUI flows pass a plain dict (they call `dict(ctx.sheet_names)`).
    Normalising here means the grouping and the diagnostics below inspect the
    SAME materialised map — and `bool()` on it then honestly answers "is the
    map empty?" (which the sheet-gate diagnostic needs, unlike the lazy
    object's always-True __bool__). Accepts None / dict / any Mapping."""
    if sheet_names is None or isinstance(sheet_names, dict):
        return sheet_names
    return {key: sheet_names[key] for key in sheet_names}


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
    """Canonical sheet identity of a footprint's sheet chain — the ONE owner of
    that derivation (kicadstamp.sheet_names.sheet_of_names, which the mixed-read
    narrowing delegates to as well, so the two can never group a selection
    differently):

      * the first named segment (the channel identity, e.g. Channel_0);
      * ROOT_SHEET ("/") for an EMPTY chain — a symbol standing on the ROOT sheet
        of the hierarchy (2026-10-14, plan_2026_10_13_root_sheet_clusters: it
        used to come back None and was then refused as "the sheet did not
        resolve");
      * None only when the chain genuinely did not resolve (entries present, none
        named).

    Used as a best-effort fallback when no Entity matches the footprint."""
    return sheet_of_names(fp_sheet)


def match_entity(entities, cluster: str, fp_sheet) -> Optional[Any]:
    """The Entity (entities:) that places this footprint: same Cluster tag AND
    entity.sheet NAMES the footprint's instance — through the project's ONE
    "does this sheet name this component" rule (sheet_names.sheet_in_path), the
    same one Board.select(sheet=) applies to a chain.

    The membership test (rather than "e.sheet is one of the chain's entries") is
    what makes an Entity carrying ROOT_SHEET ("/") match a symbol on the ROOT
    sheet: its chain is EMPTY, so "/" is in no entry and the old test could never
    find it (2026-10-14, plan_2026_10_13_root_sheet_clusters). A sub-sheet Entity
    still matches its own channel and nothing else."""
    for e in entities:
        if e.cluster == cluster and e.sheet and sheet_in_path(fp_sheet or (), e.sheet):
            return e
    return None


def instance_sheet(fp: Selected, entities, sheet_names: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Sheet identity of a footprint for grouping: its matched Entity's sheet
    (exact for hierarchical channels like Channel_0/1/2), else the chain's own
    identity (sheet_of: the first named segment, or ROOT_SHEET for an empty
    chain — a symbol on the root sheet)."""
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
                            sheet_names: Optional[Mapping[str, str]] = None,
                            rejections: Optional[list] = None) -> list[ReReadCluster]:
    """Clusters of the selection that are FULLY selected: every board component
    of the (Cluster tag, sheet) instance (snapshot footprints with the same
    Cluster whose sheet chain NAMES the group's sheet — sheet_names.sheet_in_path,
    the project's ONE membership rule) is in the selection.
    Each maps to its Entity -> cell -> extract_profiles key (if any).
    `sheet_names` (RuntimeContext) re-resolves the GUI snapshot's otherwise-None
    sheet chains from the raw footprints — see _sheet_chain.

    ROOT clusters (2026-10-14, plan_2026_10_13_root_sheet_clusters Ч1): a symbol
    on the ROOT sheet of the hierarchy has an EMPTY chain — there is no sheet
    above it, so there is nothing to "resolve" and `schematic_dir` is not needed
    at all. Its instance identity is ROOT_SHEET ("/") (sheet_of_names), so the
    group is neither refused as "the sheet did not resolve" nor confused with a
    same-tagged cluster standing on a sub-sheet: the two become two groups
    ("/" and the sub-sheet's name) with disjoint members, and the extraction
    writes `sheet "/"` onto the root Entity.

    `rejections` (plan_2026_09_11_extract_selection_diagnostics V.2): an
    OPTIONAL out-list the caller passes in; every dropped group appends one
    structured ClusterRejection explaining WHICH gate rejected it (untagged
    footprint, unresolved sheet, unknown cluster/sheet pair, partial
    selection). The return value is unchanged (the accepted clusters only), so
    every existing caller/test keeps working; the diagnostics are additive.
    With `sheet_names` empty the sheet gate cannot work at all — that is
    reported as REASON_SHEET_MAP_EMPTY, distinct from a single sheet that
    failed to resolve (REASON_SHEET_UNRESOLVED). A ROOT cluster is unaffected by
    that: the GUI's stale all-None chain is the only thing an empty map hides,
    and even then a chain built by resolve_sheet_path_names is [] for a root
    symbol regardless of the dictionary."""
    sheet_names = _materialize_sheet_names(sheet_names)
    selected_list = list(selected)
    selected_by_ref = {s.ref for s in selected_list}
    if rejections is not None:
        untagged = [s.ref for s in selected_list if not s.cluster]
        if untagged:
            rejections.append(ClusterRejection(
                reason=REASON_NO_CLUSTER_TAG, refs=untagged))
    groups = group_selected(selected_list, entities, sheet_names)
    profile_keys = set(profile_keys)
    sheet_map_empty = not sheet_names
    clusters: list[ReReadCluster] = []
    for (cluster, sheet), members in groups.items():
        member_refs = [s.ref for s in members]
        if not sheet:
            # No identity at all: either the sheet map is EMPTY (nothing can be
            # resolved — REASON_SHEET_MAP_EMPTY) or the chain has entries the
            # dictionary cannot name (REASON_SHEET_UNRESOLVED, the honest
            # "did not resolve" of the plan). A symbol on the ROOT sheet is NOT
            # this case any more: its identity is ROOT_SHEET ("/") — truthy — and
            # it proceeds (2026-10-14, plan_2026_10_13_root_sheet_clusters).
            if rejections is not None:
                rejections.append(ClusterRejection(
                    reason=(REASON_SHEET_MAP_EMPTY if sheet_map_empty
                            else REASON_SHEET_UNRESOLVED),
                    cluster=cluster, refs=member_refs))
            continue
        # Membership by the project's ONE rule, not by "the name is one of the
        # chain's entries": the root group's sheet is ROOT_SHEET ("/") and its
        # footprints' chain is EMPTY, which no entry ever equals — the old test
        # turned the (then refused) root group into a REASON_PAIR_NOT_IN_SNAPSHOT
        # once the gate above let it through.
        snapshot_members = [s for s in snapshot
                            if s.cluster == cluster
                            and sheet_in_path(_sheet_chain(s, sheet_names), sheet)]
        if not snapshot_members:
            if rejections is not None:
                rejections.append(ClusterRejection(
                    reason=REASON_PAIR_NOT_IN_SNAPSHOT, cluster=cluster,
                    sheet=sheet, refs=member_refs))
            continue
        missing = sorted(s.ref for s in snapshot_members
                         if s.ref not in selected_by_ref)
        if missing:
            if rejections is not None:
                rejections.append(ClusterRejection(
                    reason=REASON_PARTIAL, cluster=cluster, sheet=sheet,
                    selected=len(snapshot_members) - len(missing),
                    total=len(snapshot_members), missing=missing,
                    refs=member_refs))
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
            refs=member_refs,
        ))
    clusters.sort(key=lambda c: (c.cluster, c.sheet or ""))
    return clusters
