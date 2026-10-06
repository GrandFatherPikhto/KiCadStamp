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
from .constants import SPOKE_LEVEL_ROLE_PLACEHOLDER
from .i18n import _
from .registry import record_key_part

__all__ = [
    "CopperReadContext",
    "CopperSubtraction",
    "FootprintInfo",
    "InstanceChoice",
    "apply_live_copper_rule",
    "cell_clusters",
    "cell_record_addresses",
    "choose_instance",
    "divide_unpaired_records",
    "group_selection",
    "is_own_key",
    "journal_is_the_read_instance",
    "own_record_registry_key",
    "subtract_foreign_copper",
    "subtract_net_trace_copper",
]

# NOTE (N1, acceptance of b209c58, 2026-10-05). The first version treated every
# key carrying a physical prefix (pad:/anchor:/role:) as foreign. That is WRONG:
# a cell placed by a ClonePlacement anchored on a component/role records its OWN
# copper under `anchor:`/`role:` (see clone_position_calculator.clone_anchor_id).
# Counting those foreign subtracted the cell's own copper and — with the mixed
# path's old remove_missing=True — DELETED its records. See `is_own_key`.


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
    # True when the cell's cluster is KNOWN but the selection holds NONE of its
    # components — the caller then falls back to the remembered sheet (or
    # refuses), instead of running the role-only rule on foreign groups (Н4.1).
    no_own_cluster: bool = False

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
    # Ф3 (acceptance of cbc8bdc): honest notes about a `net_traces:` record whose
    # anchor could not be resolved (or whose match raised) — a read must SAY it
    # skipped the record, never fail its subtraction silently.
    notes: tuple = ()


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


def _qualifies(key: tuple, members: list[FootprintInfo], cell_roles: set) -> bool:
    """A role-only candidate group: its role set is EXACTLY the cell's role set.
    Used only when the cell's cluster is UNKNOWN (today's rule), or when several
    labels of the cell's cluster stand on ONE sheet (Н4.1, the plan's "old rule
    stays alive")."""
    roles = {m.role for m in members if m.role}
    return roles == set(cell_roles)


def _matches_cell_cluster(cluster: str, cell_clusters_set: Iterable[str]) -> bool:
    return any(cluster_prefix_match(cluster, str(cc)) for cc in cell_clusters_set)


def _pick(candidates: list, others: list) -> InstanceChoice:
    if len(candidates) == 1:
        key, members = candidates[0]
        return InstanceChoice(chosen_key=key, members=tuple(members),
                              candidate_groups=tuple(candidates),
                              others=tuple(others))
    return InstanceChoice(chosen_key=None, candidate_groups=tuple(candidates),
                          others=tuple(others))


def choose_instance(groups: dict, cell_roles: Iterable[str],
                    cell_clusters_set: Iterable[str] = ()) -> InstanceChoice:
    """The instance of the cell the (mixed) selection pins down.

    Н4.1 (Denis 2026-10-05, live board): when the cell's cluster is KNOWN the
    instance is ``(cluster, sheet)`` and ROLES DO NOT PARTICIPATE in the choice.
    The exact-role candidate rule from 04.10 refused the whole selection as soon
    as the instance gained a role the cell did not have yet (D23/R70 on
    DAC_BUF), which is exactly the read that must happen. Roles decide only:

      * when the cell's cluster is UNKNOWN (no record places this cell, no
        remembered context) — today's role-only rule;
      * when several labels of the cell's cluster stand on ONE sheet — then the
        exact role set picks among them (the plan's "old rule stays alive").

    cell_clusters_set — the cell's clusters from the config (or the remembered
    one). Never raises: zero/one/many candidates are all returned; the CALLER
    owns the reaction (take it / remembered sheet / the ambiguity Log line)."""
    roles = {r for r in (cell_roles or ()) if r}
    clusters = {str(c) for c in (cell_clusters_set or ()) if c}

    # ── cluster unknown: the historical role-only rule ──────────────────────
    if not clusters:
        candidates = [(key, members) for key, members in groups.items()
                      if _qualifies(key, members, roles)]
        chosen_keys = {key for key, _members in candidates}
        others = [(key, len(members)) for key, members in groups.items()
                  if key not in chosen_keys]
        return _pick(candidates, others)

    # ── cluster known: (cluster, sheet), roles out of the choice ─────────────
    own = [(key, members) for key, members in groups.items()
           if _matches_cell_cluster(str(key[0] or ""), clusters)]
    own_keys = {key for key, _members in own}
    others = [(key, len(members)) for key, members in groups.items()
              if key not in own_keys]
    if not own:
        # None of the cell's cluster is selected — the caller uses the
        # remembered sheet (when it resolves) or refuses.
        return InstanceChoice(chosen_key=None, candidate_groups=(),
                              others=tuple(others), no_own_cluster=True)
    sheets = {key[1] for key, _members in own}
    if len(sheets) > 1:
        # Several channels of the cell selected at once -> ambiguity, list them.
        return InstanceChoice(chosen_key=None, candidate_groups=tuple(own),
                              others=tuple(others))
    if len(own) == 1:
        key, members = own[0]
        return InstanceChoice(chosen_key=key, members=tuple(members),
                              candidate_groups=tuple(own), others=tuple(others))
    # Several labels of the cell's cluster on ONE sheet -> exact role set; not
    # exactly one -> refuse WITH the list (the plan's "не один — отказ со списком").
    role_matches = [(key, members) for key, members in own
                    if _qualifies(key, members, roles)]
    return _pick(role_matches, others) if len(role_matches) == 1 else \
        InstanceChoice(chosen_key=None,
                       candidate_groups=tuple(role_matches or own),
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


def record_address_matches(record_address: tuple, chosen_address: tuple) -> bool:
    """Public name of the ONE "(cluster, sheet) names this instance" rule.

    Thin delegate to ``_address_matches`` (kept private for the ownership rule
    that already calls it): cluster by ``cluster_prefix_match``, sheet only when
    BOTH sides carry one — the same best-effort cascade the rest of the project
    follows. The read-only "Select cell" uses it to find the entity /
    clone_placement record that places a cell at a given instance; no second
    address rule is written."""
    return _address_matches(record_address, chosen_address)


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


def journal_is_the_read_instance(journal, cell_name: str | None,
                                 cluster, sheet) -> bool:
    """True ⇔ a live explode JOURNAL describes exactly the instance being read.

    Р3а-3 (plan_2026_10_05_explode_r2_r3_tab_and_reread): while the clusters are
    exploded the ownership TRANSFER may move inter-cluster copper into the cell
    ONLY for the instance the journal was made for. Reading ANY other instance of
    the same cell must subtract that copper as usual (Н4) and say so — handing it
    to a different instance would give it a piece it does not own.

    The journal carries the exploded instance's ``(cell, cluster, sheet)``; the
    READ's address is resolved on the worker (``narrow_mixed_selection``), so this
    comparison is the ONE host of the rule — ``gui.ExplodeGuard.transfer_enabled``
    (the UI-thread gate) and the worker's re-check of the RESOLVED address both
    call it, and the address comparison is the product's own (``_address_matches``:
    cluster by prefix, sheet only when both carry one), so a board cluster tag
    refining the config's still matches."""
    if not journal:
        return False
    if str(journal.get("cell") or "") != str(cell_name or ""):
        return False
    return _address_matches((journal.get("cluster"), journal.get("sheet")),
                            (cluster, sheet))


def _anchor_label(key: str) -> str:
    """A human label for a foreign key: its anchor part (the record identity or
    the physical prefix), so the Log can say WHICH record owns the copper."""
    parts = key.split("|")
    return parts[0] if parts else key


def is_own_key(key: str, cell_identity: str | None,
               own_addresses: dict, chosen_address: tuple,
               chosen_refs: frozenset = frozenset()) -> bool:
    """The plan's "own record": the key's TEMPLATE part is THIS cell, and its
    ANCHOR part points at the CHOSEN instance. Everything else is foreign.

    N1 (acceptance of b209c58, 2026-10-05): a cell placed by a ClonePlacement
    anchored on a component/role records its OWN copper under the physical
    prefixes ``anchor:``/``role:`` (``clone_anchor_id``). The first version
    called those foreign, subtracted the cell's own copper, and — because the
    mixed path then ran with ``remove_missing=True`` — DELETED its records.
    The rule, per the acceptance:

      * ``anchor:<ref>:...``   — own when ``<ref>`` is one of the chosen
        instance's components (``chosen_refs``);
      * ``role:<role>:<sheet>:<cluster>:...`` — own when its (cluster, sheet)
        ADDRESS matches the chosen instance (the same ``_address_matches``);
      * ``point:`` / ``pad:``  — FOREIGN (a point names a Point record, a pad
        names physics — neither is this cell's record at this instance);
      * ``name:<identity>...`` — own when the named record is one of this cell's
        and its stored address is the chosen instance.
    """
    if cell_identity is None:
        return False
    parts = key.split("|")
    if len(parts) != 4:
        return False
    anchor_id, template_name, _role, _index = parts
    if template_name != cell_identity:
        return False
    if anchor_id.startswith("anchor:"):
        ref = anchor_id[len("anchor:"):].split(":", 1)[0]
        return ref in chosen_refs
    if anchor_id.startswith("role:"):
        fields = anchor_id[len("role:"):].split(":")
        if len(fields) < 3:
            return False
        sheet = fields[1] or None
        cluster = fields[2] or None
        return _address_matches((cluster, sheet), chosen_address)
    if anchor_id.startswith("name:"):
        identity = _match_own_identity(anchor_id[len("name:"):], own_addresses)
        if identity is None:
            return False
        return _address_matches(own_addresses[identity], chosen_address)
    # point: / pad: / thermal: / net: / anything else -> foreign (points,
    # physics pads, inter-cluster net_traces, thermal via arrays, chains).
    return False


def subtract_foreign_copper(items: Iterable[Any], owner: dict,
                            cell_identity: str | None,
                            own_addresses: dict,
                            chosen_address: tuple,
                            chosen_refs: Iterable[str] = ()) -> CopperSubtraction:
    """Split selected copper into KEPT (own / unregistered) and REMOVED
    (recorded for some other record).

    owner — {copper uuid -> registry key} over BOTH registry files (the worker
    builds it with the product's ``load_registry`` / ``load_track_registry``).
    An item whose uuid is not in ``owner`` is NOT registered and is KEPT — a
    hand-drawn track then goes the ordinary way, exactly as today.

    chosen_refs — the chosen instance's component refs, used to recognise an
    ``anchor:<ref>`` key as this cell's own (N1)."""
    chosen_ref_set = frozenset(chosen_refs or ())
    kept: list[Any] = []
    removed: list[Any] = []
    report: dict[str, int] = {}
    for item in items or ():
        key = owner.get(getattr(item, "uuid", None))
        if key is None:
            kept.append(item)
            continue
        if is_own_key(key, cell_identity, own_addresses, chosen_address,
                      chosen_ref_set):
            kept.append(item)
            continue
        removed.append(item)
        label = _anchor_label(key)
        report[label] = report.get(label, 0) + 1
    return CopperSubtraction(kept=tuple(kept), removed=tuple(removed),
                             report=tuple(sorted(report.items())))


# ── Н4 п.5: the live-UUID rule for copper with no live pair ─────────────────

@dataclass
class CopperReadContext:
    """Everything the live-UUID deletion rule needs, gathered by the worker
    (Н4 п.5, Denis 2026-10-05): the cell's registry identity/addresses, the two
    registry ENTRY maps and the live board uuids.

    NOTE (plan_2026_10_06_prune_absent_cell_copper, Дефект 1): the cell's OWN
    record lists are NOT held here. The rule needs the SAME dict objects that
    went to ``build_refresh_plan`` (``payload["vias"]``/``payload["tracks"]``),
    to find a record's index — the registry key's index part. Keeping them here
    invited the very bug this plan fixes: ``gui/mixed_selection.py`` filled them
    with the SELECTION's live copper, so the index never matched and every
    unpaired record read as "left as they are". The lists are now an EXPLICIT
    argument of :func:`apply_live_copper_rule`."""

    cell_identity: str | None
    own_addresses: dict
    chosen_address: tuple
    chosen_refs: frozenset
    via_entries: dict
    track_entries: dict
    board_via_uuids: frozenset
    board_track_uuids: frozenset
    # Ф1 (acceptance of cbc8bdc): False when the board copper could NOT be read.
    # The deletion rule must then delete NOTHING — an empty uuid set is NOT "the
    # board is empty", it is "we do not know" (a read error must never erase
    # every unpaired record).
    board_read_ok: bool = True


def own_record_registry_key(entries, cell_identity: str | None,
                            role: str | None, index: int | None,
                            own_addresses: dict, chosen_address: tuple,
                            chosen_refs: Iterable[str] = ()) -> str | None:
    """The registry key naming THIS cell record at the CHOSEN instance, or None.

    Matched through the product's own key grammar (``anchor|template|role|
    index``) and :func:`is_own_key` — never by parsing an anchor out of a
    record name. ``index`` is the record's 0-based position in the cell's
    vias/tracks list (exactly what ``make_registry_key`` wrote)."""
    if cell_identity is None or index is None or not entries:
        return None
    refs = frozenset(chosen_refs or ())
    role_part = role if role is not None else SPOKE_LEVEL_ROLE_PLACEHOLDER
    for key in entries:
        parts = key.split("|")
        if len(parts) != 4:
            continue
        _anchor, template_name, key_role, key_index = parts
        if template_name != cell_identity or key_role != role_part:
            continue
        if str(key_index) != str(index):
            continue
        if is_own_key(key, cell_identity, own_addresses, chosen_address, refs):
            return key
    return None


def _record_label(record: dict, kind: str) -> str:
    """A short human name for one copper record (kind + net), for the yellow
    "left as they are" line."""
    if record.get("net_from_role"):
        pad = record.get("net_from_role_pad")
        net = (f"net_from_role {record['net_from_role']}/{pad}"
               if pad else f"net_from_role {record['net_from_role']}")
    elif record.get("net"):
        net = str(record["net"])
    else:
        net = _("(no net)")
    return _("{kind} on {net}").format(kind=kind, net=net)


def divide_unpaired_records(unpaired, records, kind, entries, board_uuids,
                            ctx: CopperReadContext, presence=None):
    """Н4 п.5: split the records with NO live pair into DELETE and KEEP.

    A record is KEPT when its copper is found on the board by EITHER half of the
    rule:

      * REGISTRY — the uuid the registry stored for the record is live;
      * GEOMETRY (plan_2026_10_06_prune_absent_cell_copper, Дефект 2) — the dry
        run of THIS instance's recording produced a command for the record whose
        copper ``match_planned_copper`` found on the board. ``presence`` is that
        verdict (``BoardCopperPresence``); ``None`` keeps the historical
        registry-ONLY behaviour.

    A record found by NEITHER is DELETED. Everything kept is named for the
    yellow Log line. Returns (to_delete, kept, kept_names).

    ``records`` — the cell's OWN record lists (the SAME dicts that went to
    ``build_refresh_plan``), used to find a record's index. The registry key
    carries that index; feeding the SELECTION's copper here was Дефект 1 — the
    index never matched and nothing was ever deleted."""
    index_by_id = {id(r): i for i, r in enumerate(records or ())}
    live = set(board_uuids or ())
    to_delete: list = []
    kept: list = []
    names: list[str] = []
    for rec in unpaired or ():
        index = index_by_id.get(id(rec))
        key = own_record_registry_key(
            entries, ctx.cell_identity, rec.get("role"), index,
            ctx.own_addresses, ctx.chosen_address, ctx.chosen_refs)
        entry = (entries or {}).get(key) if key else None
        uuid = getattr(entry, "uuid", None)
        on_board = bool(uuid) and uuid in live
        if not on_board and presence is not None:
            on_board = presence.has(kind, rec.get("role"), index)
        if on_board:
            kept.append(rec)
            names.append(_record_label(rec, kind))
        else:
            to_delete.append(rec)
    return to_delete, kept, names


def apply_live_copper_rule(plan, ctx: CopperReadContext, record_vias,
                           record_tracks, presence=None,
                           record_name=None) -> list[str]:
    """Move the records whose copper is ABSENT from the board into the plan's
    removed_* lists; keep and NAME the rest. Returns the yellow Log lines.
    Mutates the plan's removal lists only.

    ``record_vias``/``record_tracks`` — the cell's OWN record lists (the SAME
    dicts ``build_refresh_plan`` got): the index part of a registry key is read
    from them, NEVER from the selection's copper (Дефект 1).

    ``presence`` — ``BoardCopperPresence`` from ``absent_copper_prune`` (the
    geometry half). ``None`` = no geometry information: the historical
    registry-only rule (direct/unit callers). A NOT-checked presence (the dry run
    gave no command — a refused tree, a chain-only placement, an unrealized
    record) deletes NOTHING and says so; a checked presence deletes only what
    NEITHER the registry nor geometry found (Дефект 2)."""
    # Ф1: a failed board-copper read means "we do not know", NOT "the board is
    # empty" — delete nothing and say so.
    if not getattr(ctx, "board_read_ok", True):
        return [_("could not read the board copper — records without a live "
                  "pair were left as they are")]
    unpaired_v = list(getattr(plan, "unpaired_via_records", None) or ())
    unpaired_t = list(getattr(plan, "unpaired_track_records", None) or ())
    lines: list[str] = []
    if presence is not None and not presence.checked:
        # The geometry half could not run at all: KEEP every unpaired record.
        kept_v, kept_t = unpaired_v, unpaired_t
        if unpaired_v or unpaired_t:
            lines.append(_("could not check the board for record {record} — "
                           "unpaired records left as they are").format(
                               record=record_name or "?"))
    else:
        to_del_v, kept_v, _names_v = divide_unpaired_records(
            unpaired_v, record_vias, "via", ctx.via_entries,
            ctx.board_via_uuids, ctx, presence)
        to_del_t, kept_t, _names_t = divide_unpaired_records(
            unpaired_t, record_tracks, "track", ctx.track_entries,
            ctx.board_track_uuids, ctx, presence)
        if to_del_v:
            plan.removed_via_records = list(plan.removed_via_records) + to_del_v
        if to_del_t:
            plan.removed_track_records = list(plan.removed_track_records) + to_del_t
        removed = len(to_del_v) + len(to_del_t)
        # The summary names BOTH halves; only say it when geometry really ran
        # (a registry-only caller keeps the historical, silent behaviour).
        if removed and presence is not None:
            lines.append(_("removed {count} record(s) whose copper is not on the "
                           "board (by registry and by geometry)").format(
                               count=removed))
    names = [_record_label(r, "via") for r in kept_v]
    names += [_record_label(r, "track") for r in kept_t]
    if names:
        lines.append(_("not in the selection, left as they are: {names}").format(
            names=", ".join(names)))
    return lines


@dataclass(frozen=True)
class NetTraceTransfer:
    """Р3: ONE selected copper piece handed from a ``net_traces`` record to the
    cell instead of being subtracted. Plain data, so it crosses the worker/UI
    boundary: `identity` names the record, `kind` is "via"/"track", `index` is the
    piece's 0-based position in the record's vias/tracks list — the SAME
    ``Expectation.index`` ``find_live_copper`` computed (never counted twice)."""
    identity: str
    kind: str
    index: int


def _net_trace_owned(items, net_traces, adapter, *, via_entries, track_entries,
                     sheet_names=None):
    """(owned, notes): for each selected copper uuid a LIVE ``net_traces:`` record
    owns, its ``NetTraceTransfer`` (identity + kind + index); plus the yellow notes
    about records whose copper could not be matched.

    ONE ``find_live_copper`` call per record, SHARED by the subtraction (Н4 п.5а)
    and the transfer (Р3) — a piece is never matched twice. The match reuses
    ``net_trace_planner.find_live_copper`` (the SAME calculation the redraw uses,
    via ``plan_net_traces``), never a second copy; the two registry objects are
    thin ``{key: entry}`` shims, so nothing is written."""
    from .net_trace_planner import find_live_copper

    class _Entries:
        def __init__(self, entries):
            self.entries = entries

    vreg, treg = _Entries(via_entries or {}), _Entries(track_entries or {})
    owned: dict[str, NetTraceTransfer] = {}
    notes: list[str] = []
    for nt in net_traces or ():
        name = str(getattr(nt, "net", "?"))
        try:
            live = find_live_copper(adapter, nt, via_registry=vreg,
                                    track_registry=treg,
                                    sheet_names=sheet_names or {})
        except Exception as e:  # noqa: BLE001 — a read must never crash the narrow
            notes.append(_("net_traces {net!r}: cannot match its copper ({error})")
                         .format(net=name, error=" ".join(str(e).split())))
            continue
        if getattr(live, "reason", None):
            # Ф3 + м2: only an UNRESOLVED anchor is worth a line. A retired/skip
            # record legitimately plans nothing (retired: not placed at all;
            # skip: its registry-known copper is still subtracted below) and
            # must NOT add a line per read.
            if not (getattr(nt, "retired", False) or getattr(nt, "skip", False)):
                notes.append(_("net_traces {net!r}: {reason} — it was not subtracted")
                             .format(net=getattr(live, "identity", None) or name,
                                     reason=live.reason))
        identity = getattr(live, "identity", None) or name
        pieces = getattr(live, "pieces", None)
        if pieces is None:
            # A test double may expose only `found` (the subtraction needs no
            # kind/index); the transfer always gets real pieces from
            # find_live_copper, which is where the piece number comes from.
            for item in getattr(live, "found", ()) or ():
                uuid = getattr(item, "uuid", None)
                if uuid:
                    owned[uuid] = NetTraceTransfer(identity, "", -1)
            continue
        for piece in pieces:
            uuid = getattr(piece.live, "uuid", None)
            if uuid:
                exp = piece.expectation
                owned[uuid] = NetTraceTransfer(
                    identity=identity, kind=str(exp.kind), index=int(exp.index))
    return owned, notes


def subtract_net_trace_copper(items, net_traces, adapter, *,
                              via_entries, track_entries, sheet_names=None
                              ) -> CopperSubtraction:
    """Н4 п.5а: remove the selected copper that matches a LIVE ``net_traces:``
    record's planned copper — even when the registry does not know it yet."""
    owned, notes = _net_trace_owned(items, net_traces, adapter,
                                    via_entries=via_entries,
                                    track_entries=track_entries,
                                    sheet_names=sheet_names)
    kept: list = []
    removed: list = []
    report: dict[str, int] = {}
    for item in items or ():
        tr = owned.get(getattr(item, "uuid", None))
        if tr is None:
            kept.append(item)
        else:
            removed.append(item)
            report[tr.identity] = report.get(tr.identity, 0) + 1
    return CopperSubtraction(kept=tuple(kept), removed=tuple(removed),
                             report=tuple(sorted(report.items())),
                             notes=tuple(notes))


def net_trace_transfers(items, net_traces, adapter, *,
                        via_entries, track_entries, sheet_names=None) -> tuple:
    """Р3: the (kept, transfers, notes) split — the selected copper a LIVE
    ``net_traces:`` record owns STAYS in the read (it becomes the cell's new
    copper) and each piece is named for the ownership transfer; everything else is
    kept exactly as the subtraction would leave it (other cells, chains, thermal
    arrays and the record's own unselected copper never appear here).

    NOTE (Р3а-3, found while writing the address-gate cells): the owned piece is
    BOTH kept AND named. Kept is what reaches ``build_refresh_plan`` — without it
    the cell gains NO record for the piece, ``apply_transfers`` still takes it away
    from the ``net_traces`` record, and the redraw then owns nothing: exactly the
    "two owners / no owner" damage the transfer exists to avoid. The first version
    put the piece ONLY into ``transfers``, so the transfer silently dropped it."""
    owned, notes = _net_trace_owned(items, net_traces, adapter,
                                    via_entries=via_entries,
                                    track_entries=track_entries,
                                    sheet_names=sheet_names)
    kept: list = []
    transfers: list[NetTraceTransfer] = []
    for item in items or ():
        tr = owned.get(getattr(item, "uuid", None))
        kept.append(item)                      # the piece STAYS in the read...
        if tr is not None:
            transfers.append(tr)               # ...and is named for the transfer
    return kept, tuple(transfers), notes
 