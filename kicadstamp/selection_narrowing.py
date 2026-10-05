# kicadstamp/selection_narrowing.py
"""Narrow a MIXED board selection to ONE cell instance (2026-10-04/05).

"Update from selection" / "Import vias/tracks from selection" used to refuse on
any foreign component: the extra roles would become new records
(``add_new_copper=True``) and the records whose live pair is missing would be
deleted (``remove_missing=True``). Denis (2026-10-04): DAC_BUF cannot be
selected without the PIF clusters standing next to it, and known copper may
simply be SUBTRACTED from the selection (inter-cluster copper included), then
the read copper selected back onto the board so the user can fix it by hand.

This module is that rule, pure and Qt-free. The workers read the board (adapter)
and the two registries; THIS decides:

  * GROUP the selected components by (Cluster tag, instance sheet) — the
    project's own (Sheet, Cluster) addressing: the sheet comes from the
    footprint's resolved sheet chain and, when an Entity names it, from that
    Entity's ``sheet`` (the exact rule ``gui/docks/reead.instance_sheet`` uses).
    No second terminology is invented.
  * CHOOSE the group whose ``Cluster`` is THIS cell's cluster AND whose role set
    is EXACTLY the cell's role set. ROLES ALONE cannot tell PIF instances apart
    (all PIFs carry the same roles), so the cluster of the cell is the
    discriminator. One candidate -> it is taken; zero -> "no candidate": the
    caller keeps its TODAY behaviour (the role mismatch fatal); more than one ->
    an ambiguity the caller reports as a red Log line with the candidate list,
    building nothing.
  * SUBTRACT from the selected copper everything the registries recorded for
    OTHER records — other cells, ``net_traces`` (inter-cluster copper), chains,
    thermal via arrays, and this same cell on ANOTHER instance. Copper the
    registries do not know is KEPT and goes the ordinary way (paired with a
    record or imported as a new one), exactly like today.

The cell's cluster and the record ADDRESSES come from the LOADED config
(``entities`` / ``clone_placements`` that place this cell, materialized
``tree_instances`` / ``sheet_templates`` included) — one function for every
door. Registry keys are read through the product's own format gate
(``record_key_part``), NEVER by parsing a record name: after the 2->3 switch the
record-identifying part is a UUID (plan_2026_10_02_uuid_format_2_to_3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .cluster_matching import cluster_prefix_match
from .registry import record_key_part

__all__ = [
    "CopperSubtraction",
    "FootprintInfo",
    "InstanceChoice",
    "cell_clusters",
    "cell_record_addresses",
    "choose_instance",
    "group_selection",
    "subtract_foreign_copper",
]

# The anchor_id prefixes that name PHYSICS, not a record — a key carrying one
# can never be "this cell's copper at the chosen instance" (the plan: a key with
# a physical prefix pad:/anchor:/role: is foreign).
_PHYSICAL_PREFIXES = ("pad:", "anchor:", "role:")


@dataclass(frozen=True)
class FootprintInfo:
    """One selected footprint as the narrowing reads it: the live item it came
    from (so the caller can select/plan on the very same object), its Role and
    Cluster fields, and its RESOLVED sheet chain (a tuple of names — the caller
    resolves it with the project's own ``resolve_sheet_path_names``)."""

    item: Any
    role: str | None
    cluster: str | None
    sheet: tuple = ()


@dataclass(frozen=True)
class InstanceChoice:
    """The result of :func:`choose_instance`.

    chosen_key — the (cluster, sheet) group the cell's instance resolves to, or
        None when nothing (or too many) qualified.
    members — the FootprintInfo of the chosen group (empty when not chosen).
    candidate_groups — ((key, members), ...) of every group that qualified (the
        cell's cluster + the exact role set); its LENGTH is the decision:
        1 -> chosen, 0 -> today's refusal, >1 -> ambiguity.
    others — ((group_key, member_count), ...) of the groups that did NOT
        qualify (for the "skipped N components" Log line)."""

    chosen_key: tuple | None
    members: tuple = ()
    candidate_groups: tuple = ()
    others: tuple = ()

    @property
    def candidate_keys(self) -> tuple:
        return tuple(key for key, _members in self.candidate_groups)


@dataclass(frozen=True)
class CopperSubtraction:
    """The outcome of :func:`subtract_foreign_copper`.

    kept — the copper items left in the selection (unregistered, or recorded
        for the chosen instance) — they go the ordinary way.
    removed — the items recorded for some OTHER record.
    report — ((label, count), ...): the anchor part of every foreign key and how
        many items it owns, for the "subtracted: M vias, K tracks — ..." Log
        line."""

    kept: tuple
    removed: tuple
    report: tuple = ()


# ── the cell's cluster and record addresses (from the LOADED config) ────────

def cell_clusters(cfg, cell_name: str | None) -> set[str]:
    """The Cluster tags of every record that PLACES ``cell_name`` — the
    cell's own cluster(s), the discriminator roles cannot give.

    Read from the LOADED config, so materialized ``tree_instances`` /
    ``sheet_templates`` copies are included (``load_config`` expands them into
    ``entities`` / ``clone_placements``). One function for every door."""
    if not cell_name:
        return set()
    out: set[str] = set()
    for e in getattr(cfg, "entities", None) or ():
        if getattr(e, "cell", None) == cell_name and getattr(e, "cluster", None):
            out.add(str(e.cluster))
    for c in getattr(cfg, "clone_placements", None) or ():
        if getattr(c, "cell", None) == cell_name and getattr(c, "cluster", None):
            out.add(str(c.cluster))
    return out


def cell_record_addresses(cfg, cell_name: str | None
                          ) -> dict[str, tuple[str | None, str | None]]:
    """{record-identity -> (cluster, sheet)} for every record of the LOADED
    config that places ``cell_name`` — the ANCHOR side of a registry key.

    The identity is the product's own format-gated value
    (:func:`kicadstamp.registry.record_key_part`): the record's UUID under
    format 3, its name under format 2. Comparing a registry key's anchor part
    against these keys is what the plan means by reading a key ONLY through
    product functions — never by splitting the name out of the key."""
    out: dict[str, tuple[str | None, str | None]] = {}
    if not cell_name:
        return out
    for e in getattr(cfg, "entities", None) or ():
        if getattr(e, "cell", None) != cell_name:
            continue
        identity = record_key_part(getattr(e, "name", "") or "",
                                   getattr(e, "uuid", None))
        out[identity] = (getattr(e, "cluster", None), getattr(e, "sheet", None))
    for c in getattr(cfg, "clone_placements", None) or ():
        if getattr(c, "cell", None) != cell_name:
            continue
        name = getattr(c, "name", None) or getattr(c, "cluster", None) or ""
        identity = record_key_part(name, getattr(c, "uuid", None))
        out[identity] = (getattr(c, "cluster", None), getattr(c, "sheet", None))
    return out


# ── grouping ────────────────────────────────────────────────────────────────

def _sheet_identity(chain: Iterable[str | None], cluster: str | None,
                    entities) -> str | None:
    """The instance-sheet identity of a footprint: the Entity's ``sheet`` when an
    Entity with the same Cluster names it (exact for hierarchical channels like
    Channel_0/1/2), else the first non-None segment of the resolved chain — the
    SAME rule ``gui/docks/reead.instance_sheet`` uses, so the two never group a
    selection differently."""
    chain = tuple(chain or ())
    if cluster:
        for e in entities or ():
            e_cluster = getattr(e, "cluster", None)
            e_sheet = getattr(e, "sheet", None)
            if (e_cluster and e_sheet
                    and cluster_prefix_match(str(cluster), str(e_cluster))
                    and e_sheet in chain):
                return e_sheet
    for seg in chain:
        if seg:
            return seg
    return None


def group_selection(infos: Iterable[FootprintInfo], entities=()
                    ) -> dict[tuple, list[FootprintInfo]]:
    """{(Cluster tag, instance sheet): [FootprintInfo, ...]}.

    Footprints without a Cluster tag are ignored (they belong to no instance —
    the same convention ``gui/docks/reead.group_selected`` has)."""
    groups: dict[tuple, list[FootprintInfo]] = {}
    for info in infos or ():
        if not info.cluster:
            continue
        key = (str(info.cluster),
               _sheet_identity(info.sheet, info.cluster, entities))
        groups.setdefault(key, []).append(info)
    return groups


def _qualifies(key: tuple, members: list[FootprintInfo], cell_roles: set,
               cell_clusters_set: set) -> bool:
    """A candidate group: its Cluster is the cell's cluster (when the cell's
    cluster is known) AND its role set is EXACTLY the cell's role set."""
    cluster, _sheet = key
    if cell_clusters_set and not any(
            cluster_prefix_match(str(cluster), cc) for cc in cell_clusters_set):
        return False
    roles = {m.role for m in members if m.role}
    return roles == set(cell_roles)


def choose_instance(groups: dict, cell_roles: Iterable[str],
                    cell_clusters_set: Iterable[str] = ()) -> InstanceChoice:
    """The instance of the cell the (mixed) selection pins down.

    cell_clusters_set — the cell's clusters from the config; when EMPTY (no
    record places this cell, no remembered context) the cluster step is dropped
    and the choice is by roles only — the plan's "selection by roles alone".

    Never raises: zero/one/many candidates are all returned; the CALLER owns the
    reaction (today's refusal / take it / the ambiguity Log line)."""
    roles = {r for r in (cell_roles or ()) if r}
    clusters = {str(c) for c in (cell_clusters_set or ()) if c}
    candidates: list[tuple] = []
    others: list[tuple] = []
    for key, members in groups.items():
        if _qualifies(key, members, roles, clusters):
            candidates.append((key, members))
        else:
            others.append((key, len(members)))
    if len(candidates) == 1:
        key, members = candidates[0]
        return InstanceChoice(chosen_key=key, members=tuple(members),
                              candidate_groups=tuple(candidates),
                              others=tuple(others))
    return InstanceChoice(chosen_key=None, candidate_groups=tuple(candidates),
                          others=tuple(others))


# ── copper subtraction (registry, both files) ───────────────────────────────

def _match_own_identity(value: str, own_addresses: dict) -> str | None:
    """The longest own-record identity that ``value`` names — the value itself,
    or a ``<identity>:<offsets>`` (a ``point:`` anchor) or ``<identity>/<nested>``
    (a nested placement suffix). None when the anchor names no record of THIS
    cell (a foreign cell, a net_trace, an orphan)."""
    best: str | None = None
    for identity in own_addresses:
        if not identity:
            continue
        if (value == identity
                or value.startswith(identity + ":")
                or value.startswith(identity + "/")):
            if best is None or len(identity) > len(best):
                best = identity
    return best


def _address_matches(record_address: tuple, chosen_address: tuple) -> bool:
    """Does a record's (cluster, sheet) address name the chosen instance?

    Cluster by the project's ``cluster_prefix_match`` (a board tag may refine the
    config's), sheet by equality — and only when BOTH sides carry one: a missing
    sheet (a single-instance cell that never stored one) narrows nothing, the
    same best-effort rule the rest of the (Sheet, Cluster) cascade follows."""
    r_cluster, r_sheet = record_address
    c_cluster, c_sheet = chosen_address
    if r_cluster and c_cluster and not cluster_prefix_match(str(c_cluster),
                                                            str(r_cluster)):
        return False
    if r_sheet and c_sheet and r_sheet != c_sheet:
        return False
    return True


def _anchor_label(key: str) -> str:
    """A human label for a foreign key: its anchor part (the record identity or
    the physical prefix), so the Log can say WHICH record owns the copper."""
    parts = key.split("|")
    return parts[0] if parts else key


def _is_own_key(key: str, cell_identity: str | None,
                own_addresses: dict, chosen_address: tuple) -> bool:
    """The plan's "own record": the key's template part is THIS cell AND its
    anchor part points at a record of this cell whose address IS the chosen
    instance. Everything else is foreign."""
    if cell_identity is None:
        return False
    parts = key.split("|")
    if len(parts) != 4:
        return False
    anchor_id, template_name, _role, _index = parts
    if template_name != cell_identity:
        return False
    if anchor_id.startswith(_PHYSICAL_PREFIXES):
        return False
    if anchor_id.startswith("name:"):
        value = anchor_id[len("name:"):]
    elif anchor_id.startswith("point:"):
        value = anchor_id[len("point:"):]
    else:
        # thermal: / net: / anything we do not own -> foreign (inter-cluster
        # net_traces, thermal via arrays, chains).
        return False
    identity = _match_own_identity(value, own_addresses)
    if identity is None:
        return False
    return _address_matches(own_addresses[identity], chosen_address)


def subtract_foreign_copper(items: Iterable[Any], owner: dict,
                            cell_identity: str | None,
                            own_addresses: dict,
                            chosen_address: tuple) -> CopperSubtraction:
    """Split selected copper into KEPT (own / unregistered) and REMOVED
    (recorded for some other record).

    owner — {copper uuid -> registry key} over BOTH registry files (the worker
    builds it with the product's ``load_registry`` / ``load_track_registry``).
    An item whose uuid is not in ``owner`` is NOT registered and is KEPT — a
    hand-drawn track then goes the ordinary way, exactly as today."""
    kept: list[Any] = []
    removed: list[Any] = []
    report: dict[str, int] = {}
    for item in items or ():
        key = owner.get(getattr(item, "uuid", None))
        if key is None:
            kept.append(item)
            continue
        if _is_own_key(key, cell_identity, own_addresses, chosen_address):
            kept.append(item)
            continue
        removed.append(item)
        label = _anchor_label(key)
        report[label] = report.get(label, 0) + 1
    return CopperSubtraction(kept=tuple(kept), removed=tuple(removed),
                             report=tuple(sorted(report.items())))
