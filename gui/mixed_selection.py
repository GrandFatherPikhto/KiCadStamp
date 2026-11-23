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
Н4 п.5а). ONE exception, and it comes first (Р4 of plan_2026_11_20_cell_read_
takes_inter_node_copper, Denis 20.11): a piece the registry already knows as a
``net_traces`` piece (a ``net:`` key) is NOT subtracted — it goes to the cell by
``_keyed_decisions``, which is also where the key's guards live. The record with
no live pair is no longer decided here at all: С-1
(plan_2026_10_06_prune_absent_cell_copper, Denis 2026-10-06) made the read
STRICTLY the selection, so the worker itself passes ``remove_missing=True`` and
this prelude only narrows and subtracts. The chosen instance's ``(cluster,
sheet)`` address rides out in ``chosen_address`` for that plan.

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

from kicadstamp.anchor_ids import config_anchor_ids, orphan_keys
from kicadstamp.anchor_names import name_for, named_anchors
from kicadstamp.near_miss import near_miss
from kicadstamp.cell_instance import resolve_context_footprints
from kicadstamp.config import net_trace_effective_name
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.i18n import _
from kicadstamp.net_trace_planner import (
    NET_TRACE_KEY_PREFIX,
    net_trace_piece_index,
    net_trace_piece_net,
    net_trace_record_for_key,
    net_trace_refs,
)
from kicadstamp.registry import (
    load_registry_entries,
    record_key_part,
)
from kicadstamp.selection_narrowing import (
    FootprintInfo,
    NetTraceTransfer,
    cell_clusters,
    cell_entity_naming_address,
    cell_record_addresses,
    choose_instance,
    group_selection,
    net_trace_transfers,
    read_net_trace_owned,
    record_address_matches,
    subtract_foreign_copper,
    subtract_net_trace_copper,
)
from kicadstamp.sheet_names import resolve_sheet_path_names

from .entity.address import (
    address_matches_selection,
    cannot_verify_line,
    not_the_entity_line,
)

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
    chosen_address — the chosen instance's (cluster, sheet) the narrowing
        resolved (None only for a refusal result). The worker hands it to
        ``build_refresh_plan`` as ``chosen_cluster``/``chosen_sheet``.
    log_lines — ((text, level), ...) the finish handler prints in the Log.
    refusal — a non-empty text means the caller must build nothing and print it
        red (the ambiguity / no-instance cases)."""

    footprints: list
    vias: list
    tracks: list
    instance_footprints: list
    kept_copper: list
    chosen_address: Optional[tuple] = None
    log_lines: list = field(default_factory=list)
    refusal: Optional[str] = None
    # Р1: the pieces handed from a net_traces record to the cell — plain data for
    # the apply step, filled on EVERY read: since Т1 a piece the REGISTRY already
    # knows (`net:` key) goes over, since Т3 a piece found by GEOMETRY goes over
    # too. Nothing here depends on the explode door any more; the only line is Р2
    # (the record must TOUCH the instance being read).
    transfers: tuple = ()
    # Г (plan_2026_10_08_narrowing_net_traces_cost): WHAT this read subtracted and
    # WHY, split by kind — ((record identity, count), ...). The read-outcome line
    # names them ("another record's copper" / "inter-node copper"), so the skip
    # reasons are data, not a text the caller would have to parse.
    subtracted_records: tuple = ()
    subtracted_net_traces: tuple = ()
    # С3: the TWO halves of «another cluster», decided by the narrow itself — it
    # read every selected component's Cluster field, so «has a different cluster»
    # and «has no Cluster at all» are told apart here and the refs travel as plain
    # data for `gui/read_outcome` to print.
    no_cluster_refs: tuple = ()
    other_cluster_refs: tuple = ()


def _component_roles(components) -> set:
    return {c.get("role") for c in (components or ()) if c.get("role")}


def _instance_line(cluster, sheet, others) -> str:
    skipped = sum(count for _key, count in others)
    names = ", ".join(sorted({str(key[0]) for key, _count in others})) or "-"
    return _("read instance {cluster} on {sheet}; skipped {count} component(s) "
             "of other clusters: {clusters}").format(
        cluster=cluster, sheet=sheet if sheet is not None else _("(no sheet)"),
        count=skipped, clusters=names)


def _transfer_lines(transfers) -> list:
    """Р3: one yellow line per net_traces record whose copper moves into the cell
    (`перейдёт из net_traces <имя> в ячейку: N дорожек, M переходных`)."""
    per: dict[str, list] = {}
    for tr in transfers or ():
        counts = per.setdefault(tr.identity, [0, 0])
        counts[0 if "via" in str(tr.kind).lower() else 1] += 1
    out = []
    for identity, (vias, tracks) in sorted(per.items()):
        out.append(_("transferred from net_traces {name} into the cell: "
                     "{tracks} track(s), {vias} via(s)").format(
            name=identity, tracks=tracks, vias=vias))
    return out


def _registry_kinds(via_entries, track_entries) -> dict:
    """{copper uuid -> "via"/"track"} over both registry files.

    WHICH FILE holds a uuid is the only place a piece's kind is written down: the
    registry key does not carry it (via #0 and track #0 of one record share the
    key byte for byte), so the kind can be read from the file — and nothing else."""
    kinds: dict[str, str] = {}
    for kind, entries in (("via", via_entries), ("track", track_entries)):
        for entry in (entries or {}).values():
            uuid = getattr(entry, "uuid", None)
            if uuid:
                kinds[uuid] = kind
    return kinds


def _split_net_keyed(items, kind: str, owner: dict, kinds: dict, orphans) -> tuple:
    """(plain, keyed) — the selected copper split by WHAT ITS KEY SAYS.

    Р4 of plan_2026_11_20_cell_read_takes_inter_node_copper (Denis, 20.11): a
    `net:` key no longer means "subtract". Such a piece is taken OUT of the
    subtraction and decided by :func:`_keyed_decisions`. The extraction lives
    HERE, not inside `subtract_foreign_copper`: `is_own_key` is the ONE ownership
    rule of five callers (explode, «Select cell», `absent_copper_prune`,
    `change_cell_flow` and this read), and its meaning must not change for them.

    An ORPHAN key stays in `plain`: its record is GONE from the config, so there
    is nobody to hand the piece to and the existing read-as-orphan path keeps its
    behaviour byte for byte."""
    plain: list = []
    keyed: list = []
    for item in items or ():
        key = owner.get(getattr(item, "uuid", None))
        if key and key.startswith(NET_TRACE_KEY_PREFIX) and key not in orphans:
            keyed.append((item, kind, kinds.get(getattr(item, "uuid", None)), key))
            continue
        plain.append(item)
    return plain, keyed


def _without(items, drop) -> list:
    """Every item whose uuid ``drop`` does not name — the ONE way a list loses the
    pieces Р2 refuses to hand over."""
    return [item for item in items or ()
            if getattr(item, "uuid", None) not in drop]


def _boundary_lines(boundary: dict) -> list:
    """ONE yellow line per record Р2 refused: it sits between other nodes.

    Not an error and not a subtraction line of its own — the copper is not this
    instance's, so the read leaves it alone and the Log says whose it was."""
    return [_("net_traces {name}: the record does not touch this instance — its "
              "copper was subtracted, not transferred").format(name=name)
            for name in sorted(boundary)]


def _near_miss_lines(adapter, infos, instance_uuids, instance_fps) -> list:
    """``[(ref, field, target)]`` — a look-alike field name behind a MISSING tag.

    С4 of plan_2026_11_20_cell_read_takes_inter_node_copper: the live J5 (19.11)
    carried `Claster` and was read as an untagged component without a word. A line is
    earned only when the EFFECTIVE value is empty (an override may have supplied
    the Cluster — then the component is read fine and there is nothing to say) AND
    the footprint really carries a name one or two edits away.

    ``field_names`` is a NEW adapter method with a default of «no names known», so a
    test double that never heard of it simply yields nothing."""
    names_of = getattr(adapter, "field_names", None)
    if names_of is None:
        return []
    out: list = []
    for info in infos:
        item = info.item
        uuid = getattr(item, "uuid", None)
        if (uuid in instance_uuids) if uuid else any(item is fp for fp in instance_fps):
            continue
        try:
            names = names_of(item) or ()
        except Exception:  # noqa: BLE001 — a name question must never break a read
            continue
        for target, value in ((CLUSTER_FIELD_NAME, info.cluster),
                              (ROLE_FIELD_NAME, info.role)):
            if value:
                continue
            look_alike = near_miss(names, target)
            if look_alike:
                out.append((str(getattr(item, "ref", None) or "?"), look_alike,
                            target))
    return out


def _record_touches(adapter, nt, chosen_address, chosen_refs, sheet_names) -> bool:
    """Р2: does this record TOUCH the instance being read?

    The ADDRESS half first — (cluster, sheet) naming the instance needs no board
    read — then the REFS half: the record's anchor role and its pieces' far ends,
    resolved LIVE by ``net_trace_refs`` (the ONE "role → footprint" step
    ``record_nets`` also stands on). A record between two OTHER nodes answers
    False, and its copper is subtracted exactly as before."""
    if record_address_matches((getattr(nt, "anchor_cluster", None),
                               getattr(nt, "anchor_sheet", None)),
                              chosen_address):
        return True
    refs = net_trace_refs(adapter, nt, sheet_names)
    return bool(refs & frozenset(chosen_refs or ()))


def _touch_table(net_traces, adapter, chosen_address, chosen_refs, sheet_names):
    """``touches(identity) -> bool`` — answered ONCE per record per read.

    The boundary is asked only about the records that actually own selected
    copper, and the role search behind it is the same per-record question the walk
    already asks, so caching keeps the read's cost where it was."""
    by_name = {net_trace_effective_name(nt): nt for nt in net_traces or ()}
    cache: dict[str, bool] = {}

    def touches(identity: str) -> bool:
        if identity not in cache:
            nt = by_name.get(identity)
            # An identity the walk reported but the config list does not hold
            # cannot be checked: stay permissive instead of subtracting on a
            # lookup failure.
            cache[identity] = (True if nt is None else
                               _record_touches(adapter, nt, chosen_address,
                                               chosen_refs, sheet_names))
        return cache[identity]

    return touches


def _keyed_decisions(net_keyed, net_traces, adapter, sheet_names,
                     touches=None, names=None) -> tuple:
    """(transfers, kept, refused_report, boundary, lines) — the registry-keyed
    pieces of ONE read.

    Р1/Р4: a piece the registry already knows as a `net:` key is TRANSFERRED to
    the cell instead of subtracted — the registry claim is the strongest evidence
    there is, and it survives a geometry the user moved by hand.

    Denis's guards (20.11) subtract instead, with a yellow line, whenever the key
    cannot be trusted: the kind the registry FILE states disagrees with the live
    item's type (т3г), the record no longer has that piece — it was shortened
    after the keys were issued (т3б), or the piece stands on another net than the
    record's own piece (т3б). Without them a stale key would hand the cell copper
    that is not the record's any more.

    ``boundary`` is SEPARATE: a piece of a record that does not touch this
    instance is the ordinary Р2 case, one line per record (not per piece), and the
    caller counts it next to the geometry path's own refusals."""
    transfers: list = []
    kept: list = []
    removed: dict[str, int] = {}
    boundary: dict[str, int] = {}
    lines: list = []
    for item, run_kind, registry_kind, key in net_keyed:
        # С2: the KEY's anchor id as the label, then the record's NAME for it.
        label = name_for(names or {}, key.split("|")[0] or key)
        if registry_kind != run_kind:
            removed[label] = removed.get(label, 0) + 1
            lines.append(_("net_traces {name}: the registry says it is a {file_kind} "
                           "while the live item is a {live_kind} — the piece was "
                           "subtracted, not transferred").format(
                name=label, file_kind=registry_kind or _("(unknown kind)"),
                live_kind=run_kind))
            continue
        index = net_trace_piece_index(key)
        nt = net_trace_record_for_key(net_traces, run_kind, key)
        if nt is None or index is None:
            removed[label] = removed.get(label, 0) + 1
            lines.append(_("net_traces {name}: the record no longer has that piece "
                           "— the piece was subtracted, not transferred").format(
                name=label))
            continue
        if touches is not None and not touches(net_trace_effective_name(nt)):
            name = net_trace_effective_name(nt)
            boundary[name] = boundary.get(name, 0) + 1
            continue
        piece_net = net_trace_piece_net(adapter, nt, run_kind, index, sheet_names)
        live_net = str(getattr(item, "net_name", "") or "")
        if piece_net and live_net and piece_net != live_net:
            removed[label] = removed.get(label, 0) + 1
            lines.append(_("net_traces {name}: its piece #{index} stands on net "
                           "{live!r} while the record says {own!r} — the piece was "
                           "subtracted, not transferred").format(
                name=net_trace_effective_name(nt), index=index, live=live_net,
                own=piece_net))
            continue
        transfers.append(NetTraceTransfer(net_trace_effective_name(nt), run_kind,
                                          index))
        kept.append((item, run_kind))
    return tuple(transfers), kept, removed, boundary, lines


def _merged_report(parts, names: dict | None = None) -> dict:
    """{label -> count} over the subtraction reports of `parts` — ONE merge, used
    by both the Log line and the read-outcome's reason counts.

    ``names`` (:func:`kicadstamp.anchor_names.named_anchors`) turns each anchor id
    into the name the user knows the record by (С2): the report carries the
    FORMATTED label, so the line and the data can never disagree."""
    merged: dict[str, int] = {}
    for sub in parts:
        for label, count in sub.report:
            label = name_for(names or {}, label)
            merged[label] = merged.get(label, 0) + count
    return merged


def _orphan_line(parts, names: dict | None = None) -> Optional[str]:
    """One INFO line naming the copper READ although its key's record is gone.

    Not a subtraction and not a warning (Denis, 27.10): the read did what it was
    asked. The anchors are named exactly as the subtraction line names its own —
    «name:e8b2c497-…: 18» is what makes the line useful, and the same label helper
    builds both."""
    merged: dict[str, int] = {}
    for sub in parts:
        for label, count in getattr(sub, "orphan", ()) or ():
            label = name_for(names or {}, label)
            merged[label] = merged.get(label, 0) + count
    if not merged:
        return None
    total = sum(merged.values())
    anchors = ", ".join(f"{label}: {count}"
                        for label, count in sorted(merged.items()))
    return _("read {count} item(s) held by key(s) of a record that is no longer in "
             "the config — {anchors}").format(count=total, anchors=anchors)


def _subtraction_line(registry: dict, net_traces: dict) -> Optional[str]:
    """One Log line naming every unit of foreign copper subtracted (registry
    records + unregistered net_traces records)."""
    merged = dict(registry)
    for label, count in (net_traces or {}).items():
        merged[label] = merged.get(label, 0) + count
    total = sum(merged.values())
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
                           remembered_cluster=None, remembered_sheet=None,
                           expected_address=None,
                           explode_transfer: bool = False, explode_journal=None
                           ) -> Optional[MixedPrelude]:
    """Narrow ANY selection (clean or mixed) to ONE cell instance, or return
    None when the cell's cluster is UNKNOWN (the caller keeps today's path).

    ``explode_transfer``/``explode_journal`` — NO LONGER A GATE (Р1/Р5 of
    plan_2026_11_20_cell_read_takes_inter_node_copper, Denis 20.11). Until Т3 they
    were what let the transfer run at all, and Р3а-3 narrowed it to the journal's
    instance. Now EVERY read hands over the copper of a ``net_traces:`` record that
    TOUCHES this instance (Р2), and the door's journal decides nothing: the same
    selection reads the same way from the cell window, from «Import vias/tracks»
    and from the Explode tab. The two arguments stay in the signature because the
    four door payloads still carry them (``gui/docks/cell_editor`` builds them) and
    the Explode tab is still where a re-read is ASKED for — a cleanup of that dead
    wiring is its own plan, named in the report of this one.

    ``expected_address`` (part 2, п.5): the instance EXPLICITLY chosen on the cell
    page's "Entity" dropdown (gui/cell_entity_choice.InstanceAddress), read from
    the ONE working-instance store. When given, its own (cluster, sheet) IS the
    instance and the selection may only CONFIRM it: a selection naming ANOTHER
    instance is refused with a red line, and a selection with no Cluster at all
    cannot be checked — it passes with a YELLOW line, never silently (tagging a
    fresh pair is what these doors are for). The address comes from the page; this
    function never derives one from a cluster/sheet guesswork."""
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

    unverified_line = ""
    own_key = choice.chosen_key
    if expected_address is not None and expected_address.cluster:
        own_cluster = own_key[0] if own_key else None
        own_sheet = own_key[1] if own_key else None
        if own_cluster and not address_matches_selection(
                expected_address, own_cluster, own_sheet):
            # The selection names ANOTHER instance — nothing is read from it.
            # часть 3, п.6: the refusal names BOTH addresses — what the selection
            # IS (cluster/sheet, as read here) and which ENTITY was expected.
            # доделка 3а, п.1: and when the selection's own address names an
            # entity of the SAME cell, that entity is named too (found through the
            # part-1 entities-of-the-cell relation, by the ONE address rule) — the
            # user is told which leaf to use instead of the wrong pair.
            return _refusal_preamble(
                not_the_entity_line(
                    expected_address.entity_name, own_cluster, own_sheet,
                    cell_entity_naming_address(cfg, cell_name, own_cluster,
                                               own_sheet)),
                footprints, vias, tracks)
        if not own_cluster:
            unverified_line = cannot_verify_line(expected_address)
        chosen_cluster, chosen_sheet = (expected_address.cluster,
                                        expected_address.sheet)
        others = ()
    elif choice.chosen_key is not None:
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

    # С3: split the components the read will NOT take into «another cluster» and
    # «no Cluster field». The instance's own components are recognised by uuid (the
    # board's identity) and, for an object WITHOUT one (a test double), by identity.
    instance_uuids = {getattr(fp, "uuid", None) for fp in instance_fps} - {None}
    no_cluster_refs: list[str] = []
    other_cluster_refs: list[str] = []
    for info in infos:
        item = info.item
        uuid = getattr(item, "uuid", None)
        if (uuid in instance_uuids) if uuid else any(item is fp for fp in instance_fps):
            continue
        (other_cluster_refs if info.cluster else no_cluster_refs).append(
            str(getattr(item, "ref", None) or "?"))
    # С4: a look-alike field name behind a missing tag (see `_near_miss_lines`).
    near_misses = _near_miss_lines(adapter, infos, instance_uuids, instance_fps)

    cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
    cell_uuid = getattr(cell, "uuid", None) if cell is not None else None
    cell_identity = record_key_part(cell_name, cell_uuid)
    own_addresses = cell_record_addresses(cfg, cell_name)
    via_entries, track_entries, owner = load_registry_entries(config_path, cfg)
    # The keys whose RECORD is gone from the config (Ч1 of
    # plan_2026_10_27_orphan_anchor_keys_own_nothing): their copper is READ, not
    # subtracted — a claim of «another record» needs a record that exists. ONE set
    # from the ONE builder `reconcile` is protected by.
    orphans = orphan_keys(via_entries, track_entries, config_anchor_ids(cfg))
    chosen_address = (chosen_cluster, chosen_sheet)
    # Р5: `explode_transfer`/`explode_journal` are NO LONGER A GATE (see the
    # docstring) — the transfer runs on every read and the boundary is the record
    # touching this instance (Р2). The old check «is this the journal's instance?»
    # is gone with the gate it served.

    # Р4 (plan_2026_11_20_cell_read_takes_inter_node_copper, Denis 20.11): a piece
    # the REGISTRY already knows as a net_traces piece (`net:` key) is NOT
    # subtracted — it leaves the subtraction here and is decided by the rule
    # below. The live case: «subtracted from selection: … net:b166cc84-…: 4» —
    # the four pieces of one record were thrown away before the transfer could
    # ever see them.
    kinds = _registry_kinds(via_entries, track_entries)
    plain_vias, keyed_v = _split_net_keyed(vias, "via", owner, kinds, orphans)
    plain_tracks, keyed_t = _split_net_keyed(tracks, "track", owner, kinds, orphans)
    net_keyed = keyed_v + keyed_t

    sub_v = subtract_foreign_copper(plain_vias, owner, cell_identity,
                                    own_addresses, chosen_address, chosen_refs,
                                    orphan_keys=orphans)
    sub_t = subtract_foreign_copper(plain_tracks, owner, cell_identity,
                                    own_addresses, chosen_address, chosen_refs,
                                    orphan_keys=orphans)
    net_traces = getattr(cfg, "net_traces", None)
    # Р2: WHICH records may hand their copper to this read — the boundary is the
    # instance itself, asked once per record (`_touch_table`).
    touches = _touch_table(net_traces, adapter, chosen_address, chosen_refs,
                           sheet_names)
    # С2: the ONE map «anchor id → record name», built once per read; every Log
    # line below goes through it, so a uuid never reaches the user's eyes alone.
    names = named_anchors(cfg)
    # А1 (plan_2026_10_08_narrowing_net_traces_cost): the board's copper is read
    # ONCE for the whole narrowing and the ownership map is computed ONCE, then
    # handed to BOTH lists. It used to be read (and the whole net_traces list
    # walked) twice per kind — once for the vias, once for the tracks — i.e. 292
    # full board reads for ONE click in the live profile. The walk does not
    # depend on which list is being filtered (same records, same two registry
    # files), so one map serves both.
    from kicadstamp.net_trace_planner import read_live_copper

    # The KEYED pieces join the walk as well: it is the SAME board read and the
    # SAME ownership map the transfer and the subtraction share, so no piece is
    # ever matched twice.
    kept_all = (list(sub_v.kept) + list(sub_t.kept)
                + [item for item, _kind, _reg_kind, _key in net_keyed])
    if net_traces and kept_all:
        owned, net_notes = read_net_trace_owned(
            kept_all, net_traces, adapter, via_entries=via_entries,
            track_entries=track_entries, sheet_names=sheet_names,
            live=read_live_copper(adapter))
    else:
        # А2: a selection with NO copper cannot be owned by any record — the walk
        # (and the board read) is skipped entirely instead of planning every
        # record of the project.
        owned, net_notes = {}, ()
    # Р1 (plan_2026_11_20_cell_read_takes_inter_node_copper, Denis 20.11): EVERY
    # read transfers the copper of a record that TOUCHES this instance — the
    # explode door's journal no longer decides it. Р2 draws the line: the pieces of
    # a record between two OTHER nodes are subtracted exactly as Н4 п.5а always
    # did (that IS the «inter-cluster copper» the subtraction was for). ONE walk,
    # ONE board read (А1): the ownership map is SPLIT, never recomputed.
    owned_touch = {uuid: tr for uuid, tr in (owned or {}).items()
                   if touches(tr.identity)}
    owned_other = {uuid: tr for uuid, tr in (owned or {}).items()
                   if not touches(tr.identity)}
    # NOTE: never name the discarded notes `_` here — the i18n helper IS `_` in
    # this module, and a local binding of that name shadows it for the WHOLE
    # function (measured: UnboundLocalError three lines below). The notes travel
    # in `net_notes`, computed once above.
    kept_v, tr_v, notes_v = net_trace_transfers(
        _without(sub_v.kept, owned_other), net_traces, adapter,
        via_entries=via_entries, track_entries=track_entries,
        sheet_names=sheet_names, owned=owned_touch, notes=net_notes)
    kept_t, tr_t, notes_t = net_trace_transfers(
        _without(sub_t.kept, owned_other), net_traces, adapter,
        via_entries=via_entries, track_entries=track_entries,
        sheet_names=sheet_names, owned=owned_touch, notes=net_notes)
    net_v = subtract_net_trace_copper(
        _without(sub_v.kept, owned_touch), net_traces, adapter,
        via_entries=via_entries, track_entries=track_entries,
        sheet_names=sheet_names, owned=owned_other, notes=net_notes)
    net_t = subtract_net_trace_copper(
        _without(sub_t.kept, owned_touch), net_traces, adapter,
        via_entries=via_entries, track_entries=track_entries,
        sheet_names=sheet_names, owned=owned_other, notes=net_notes)
    transfers = tuple(tr_v) + tuple(tr_t)
    boundary: dict[str, int] = {}
    for tr in owned_other.values():
        boundary[tr.identity] = boundary.get(tr.identity, 0) + 1

    # Р1/Р4: the KEYED pieces are decided by the KEY, by the same rule and with the
    # same boundary. Denis's guards subtract, with a yellow line, whatever key
    # cannot be trusted (`_keyed_decisions`).
    (keyed_transfers, keyed_kept, keyed_removed, keyed_boundary,
     keyed_lines) = _keyed_decisions(net_keyed, net_traces, adapter, sheet_names,
                                     touches=touches, names=names)
    transfers = tuple(transfers) + keyed_transfers
    for item, run_kind in keyed_kept:
        (kept_v if run_kind == "via" else kept_t).append(item)
    for name, count in keyed_boundary.items():
        boundary[name] = boundary.get(name, 0) + count

    # С-1 (plan_2026_10_06_prune_absent_cell_copper, Denis 2026-10-06): the read
    # is STRICTLY the selection, decided by ``build_refresh_plan(remove_missing=
    # True)`` in the worker. The soft Н4 п.5 verdict (registry uuid / geometry
    # dry run) is gone from the product — nothing here reads the board's copper
    # uuids any more, and no CopperReadContext crosses this boundary.

    lines = [(_instance_line(chosen_cluster, chosen_sheet, others), SUCCESS)]
    if unverified_line:
        # п.5: the read is pinned to an entity, but the selection carried no
        # Cluster — nothing CHECKED it, so it is said out loud, not passed on.
        lines.append((unverified_line, WARN))
    # Ф3: a net_traces record whose anchor could not be resolved says so.
    for note in net_notes:
        lines.append((note, WARN))
    # С4: ONE line per component whose tag is missing because of a look-alike name.
    for ref, look_alike, target in near_misses:
        lines.append((_("{ref}: field {field!r} looks like {target!r} — the component "
                        "is read as having no {target}").format(
            ref=ref, field=look_alike, target=target), WARN))
    # Т1: every keyed piece that was NOT transferred says WHY it was subtracted.
    for line in keyed_lines:
        lines.append((line, WARN))
    # Р3: one yellow line per record whose copper moves into the cell.
    for line in _transfer_lines(transfers):
        lines.append((line, WARN))
    # Р2: one yellow line per record whose copper this read left alone because it
    # does not touch the instance (it lies between other nodes).
    for line in _boundary_lines(boundary):
        lines.append((line, WARN))
    # Г: the subtracted copper, split by WHY (the read-outcome line counts each
    # kind separately). A transferred piece is NOT subtracted, so the net_traces
    # report is empty on that path.
    registry_report = _merged_report([sub_v, sub_t], names)
    net_report = _merged_report([net_v, net_t], names)
    # Т1: a keyed piece a guard refused WAS subtracted — it belongs in the итог
    # (and in the read-outcome reasons), never in a silent void. The boundary
    # refusals of the keyed path are counted here as well (the geometry path's own
    # are already in `net_v`/`net_t`'s reports).
    for label, count in keyed_removed.items():
        net_report[label] = net_report.get(label, 0) + count
    for label, count in keyed_boundary.items():
        net_report[label] = net_report.get(label, 0) + count
    subtraction = _subtraction_line(registry_report, net_report)
    if subtraction:
        lines.append((subtraction, SUCCESS))
    # INFO: the copper read although its key's record is no longer in the config.
    # Deliberately NOT in `subtraction`/`subtracted_records` — nothing was removed.
    orphan_line = _orphan_line([sub_v, sub_t], names)
    if orphan_line:
        lines.append((orphan_line, SUCCESS))

    return MixedPrelude(
        footprints=list(instance_fps),
        vias=list(kept_v),
        tracks=list(kept_t),
        instance_footprints=list(instance_fps),
        kept_copper=list(kept_v) + list(kept_t),
        chosen_address=(chosen_cluster, chosen_sheet),
        log_lines=lines,
        transfers=transfers,
        subtracted_records=tuple(sorted(registry_report.items())),
        subtracted_net_traces=tuple(sorted(net_report.items())),
        no_cluster_refs=tuple(no_cluster_refs),
        other_cluster_refs=tuple(other_cluster_refs))
