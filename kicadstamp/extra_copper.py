# kicadstamp/extra_copper.py
"""Extra copper of a tree — the THIRD owner of copper, written ONLY by selection
(plan_2026_11_10_extra_copper_by_selection, п.4–п.7).

The design's table (п.1 of the plan) gives every piece of copper exactly one owner:

  * a CELL's copper — inside one node, refreshed by the cell's own record;
  * INTER-NODE copper — pad of one node to pad of another, written by
    «Reread inter-node copper» through the classifier;
  * EXTRA copper — what the classifier does NOT take as INTERNODE: a dead end
    from a node's pad into a pour (a track plus a via), the stitching nobody
    records. Nobody writes it automatically, because "a dead end" and "a bridge
    still being routed" look the same on the board.

WHAT THIS MODULE TAKES. The selection's tracks and vias are split by the SAME
`find_copper_units` the re-read uses, with the WHOLE BOARD's pads as boundaries
(`reread_area.read_reread_area` is the owner of that rule), then classified by
the SAME `classify_unit` against the SAME node maps the classification built
(`internode_capture._classify_area`). The one verdict this door accepts is
`STUB`: exactly one pad, and that pad belongs to a node of the tree. Everything
else is REFUSED with one Log line naming the net and the pads — the other
pieces of the selection still go through (п.5):

  * INTERNODE — «that is inter-node copper, Reread inter-node copper writes it»;
  * a CELL's own copper — the same ownership proof (`CellCopperMap.uuids`) the
    re-read subtracts by, so a cell's piece is never claimed twice;
  * FOREIGN — a pad outside the tree: there is no node to anchor on;
  * UNMOORED — no pad at all: nothing to anchor on;
  * CLUSTER / MODULE — one node's, or one module's, own copper.

THE RECORD is the ordinary `NetTrace` built by the ordinary builder: `capture_unit`
is called and NOTHING here builds a second record (п.4), so the anchor, the
(role, pad) references and the `pads:` signature cannot drift from the inter-node
one. Two fields are added on top: `category = "extra"`
(`kicadstamp/net_trace_category.py`) and — on a repeat — the stored `uuid`.

REPEAT (п.6). A piece whose PAD SET and NET equal those of an existing extra
record of THIS tree refreshes that record's geometry; its name and uuid stay
(the tree node references the name — a re-read never renames). Extra records the
selection does not touch are not looked at, let alone touched.

THE NAME (п.7) comes from `generate_trace_name` (inside `capture_unit`) and then
goes through `free_node_ref` with the config's record names as `taken` — the same
two bans the re-read answers in one call (§0 п.6/§0.1 of the plan).

WHAT THIS MODULE DOES NOT DO: no board write (the door stages the working set
only), no clone expansion change (`tree_instances` deep-copies the record, so
`category` travels with it — п.9), and no classifier change (п.8 lives in
`plan_internode_reread`, which now skips extra records by
`net_trace_category.inter_node_records`).
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Iterable

from .config import Config, NetTrace, net_trace_effective_name, net_trace_pad_signature
from .domain.board import Via
from .i18n import _
from .internode_capture import (
    CapturedTrace,
    RereadPlan,
    _classify_area,
    _pad_label,
    capture_unit,
    tree_net_trace_identities,
)
from .internode_cell_copper import _cell_by_node_key
from .internode_copper import CopperVerdict, classify_unit, find_copper_units
from .internode_nodes import same_geometry, tree_node_keys
from .net_trace_category import CATEGORY_EXTRA, is_extra
from .trees_local_refs import free_node_ref

logger = logging.getLogger(__name__)

__all__ = [
    "extra_report_lines",
    "plan_extra_copper",
]

#: The order the extra report lists what the door did NOT take in: the verdicts it
#: refuses (INTERNODE first — it is the one that has another owner waiting) plus
#: the cell proof, which is not a `CopperVerdict` at all.
_NOT_TAKEN_ORDER = (CopperVerdict.INTERNODE.value, "cell",
                    CopperVerdict.CLUSTER.value, CopperVerdict.MODULE.value,
                    CopperVerdict.FOREIGN.value, CopperVerdict.UNMOORED.value)


def _pads_text(pads: Iterable[Any]) -> str:
    """The pads of a piece as the Log names them ("R1.2, R1.3"), "-" for none."""
    return ", ".join(f"{p.ref}.{p.pad}" for p in pads) or "-"


def _refuse(plan: RereadPlan, reason: str) -> None:
    """Count one refused piece and keep its one line for the report (п.5)."""
    plan.discarded[reason] = plan.discarded.get(reason, 0) + 1


def _cell_owned(cell_map, unit) -> bool:
    """True ⇔ a cell's record owns any piece of this unit's copper.

    THE same proof `internode_cell_copper.subtract_units` subtracts by (the
    registry key or the place pass, both already resolved into `CellCopperMap.uuids`
    by `_classify_area`), so the extra door can never claim copper the re-read
    gives back to a cell — and the cell's name in the refusal comes from the very
    node key the classifier matched."""
    owned = getattr(cell_map, "uuids", None)
    if not owned:
        return False
    return any(getattr(item, "uuid", None) in owned
               for item in (*unit.tracks, *unit.vias))


def _cell_name(unit, node_key_by_ref, cells: dict) -> str | None:
    """The cell whose node a pad of this piece belongs to, if the tree names one."""
    for pad in unit.pads:
        key = node_key_by_ref.get(pad.ref)
        if key is not None and cells.get(key):
            return cells[key]
    return None


def plan_extra_copper(adapter, cfg: Config, tree, *,
                      config_path,
                      area_items: Iterable[Any],
                      area_footprints: Iterable[Any],
                      sheet_names: dict[str, str] | None = None,
                      narrowed: bool = True) -> RereadPlan:
    """Plan the EXTRA copper of `tree` from the selected pieces (п.4–п.7).

    `area_items`/`area_footprints` come from `reread_area.read_reread_area`
    (selection copper, whole-board pads) — the door never widens or narrows them.
    The result is a `RereadPlan` so the ONE writer (`apply_reread_plan`) and the
    ONE staging path (`TreesDock._stage_net_traces`) are reused unchanged; the
    refusals ride in `warnings`, the counts in `discarded`.

    The classification is `_classify_area` — the SAME step the re-read runs, so a
    pad's node, sheet and Role are resolved exactly as they are there. It keeps
    only INTERNODE units, so the copper is split a SECOND time here (the same
    pure function on the same items) to reach the pieces the classifier threw
    away; its split warnings are the classification's own and are not repeated."""
    _sn = dict(sheet_names or {})
    items = list(area_items)
    plan = RereadPlan(narrowed=narrowed,
                      area_tracks=sum(1 for item in items
                                      if not isinstance(item, Via)),
                      area_vias=sum(1 for item in items
                                    if isinstance(item, Via)))
    # Н2 of the acceptance: this door writes ONLY by selection (п.3 of the plan).
    # An empty selection arrives here as the WHOLE-board area (`read_reread_area`
    # opens it for the re-read, narrowed=False) — taking it would write every dead
    # end of the board from a click with nothing selected. One refusal line.
    if not narrowed or not items:
        plan.warnings.append(_(
            "extra copper: the selection holds no track and no via — nothing "
            "was read"))
        return plan

    classification = _classify_area(
        adapter, cfg, tree, config_path=config_path, area_items=items,
        area_footprints=area_footprints, sheet_names=_sn)
    plan.warnings.extend(classification.warnings)
    plan.matched_components = classification.matched_components
    plan.unmatched_components = classification.unmatched_components
    plan.node_keys = classification.node_keys
    components = classification.components

    # The module owners the classifier itself used: rebuilt from the SAME node
    # keys and the SAME walk, so a piece inside one module is MODULE here too
    # (and is refused as such) rather than being claimed by the parent tree.
    owners = tree_node_keys(tree, cfg).owners
    module_owner_by_ref = {ref: owners.get(key)
                           for ref, key in classification.node_key_by_ref.items()}
    cells = _cell_by_node_key(cfg)

    # The split's own warnings are the classification's (the SAME function on the
    # SAME items, already in `plan.warnings`), so they are not repeated here.
    units, _same_split = find_copper_units(
        adapter, items, footprints=list(area_footprints))

    identities = set(tree_net_trace_identities(tree))
    tree_records = [nt for nt in cfg.net_traces
                    if net_trace_effective_name(nt) in identities]
    # The REPEAT key (п.6): the pad set AND the net of an EXISTING extra record of
    # this tree. An inter-node record of the same pads is not a match — its owner
    # is the other door, and the inter-node re-read already says so.
    by_key: dict[tuple[frozenset[str], str], NetTrace] = {}
    for nt in tree_records:
        signature = net_trace_pad_signature(nt)
        if signature and is_extra(nt):
            by_key.setdefault((signature, nt.net), nt)
    existing_names = [net_trace_effective_name(nt) for nt in cfg.net_traces]

    for unit in units:
        verdict = classify_unit(unit, classification.node_key_by_ref,
                                module_owner_by_ref=module_owner_by_ref)
        pads = _pads_text(unit.pads)
        # The CELL proof outranks the verdict (п.5): a piece a cell's record owns
        # belongs to the cell whatever it touches on the other end, and the
        # inter-node re-read names it the same way (`duplicate_line`).
        if _cell_owned(classification.cell_copper, unit):
            _refuse(plan, "cell")
            plan.warnings.append(_(
                "skipped copper {net!r} (pads {pads}): it is the copper of cell "
                "{cell!r} — the cell's record owns it, the extra door leaves it "
                "alone").format(net=unit.net_name, pads=pads,
                                cell=_cell_name(unit, classification.node_key_by_ref,
                                                cells) or "-"))
            continue
        if verdict is CopperVerdict.INTERNODE:
            _refuse(plan, verdict.value)
            plan.warnings.append(_(
                "skipped copper {net!r} (pads {pads}): it joins two or more "
                "nodes of tree {tree!r} — that is inter-node copper, «Reread "
                "inter-node copper» writes it").format(
                    net=unit.net_name, pads=pads, tree=tree.name))
            continue
        if verdict is CopperVerdict.FOREIGN:
            _refuse(plan, verdict.value)
            outside = ", ".join(f"{p.ref}.{p.pad}" for p in unit.pads
                                if p.ref not in classification.node_key_by_ref)
            plan.warnings.append(_(
                "skipped copper {net!r} (pads {pads}): the pad(s) {outside} "
                "belong to no node of tree {tree!r} — there is no node to anchor "
                "a record on").format(net=unit.net_name, pads=pads,
                                      outside=outside or "-", tree=tree.name))
            continue
        if verdict is CopperVerdict.UNMOORED:
            _refuse(plan, verdict.value)
            plan.warnings.append(_(
                "skipped copper {net!r}: it touches no pad at all — there is "
                "nothing to anchor a record on").format(net=unit.net_name))
            continue
        if verdict is not CopperVerdict.STUB:
            # CLUSTER (one node's own copper) and MODULE (one module's, re-read
            # on its own tab): refused, but not with the inter-node sentence.
            _refuse(plan, verdict.value)
            plan.warnings.append(_(
                "skipped copper {net!r} (pads {pads}): its pads belong to one "
                "node of tree {tree!r} — that is the node's own copper, not a "
                "dead end").format(net=unit.net_name, pads=pads, tree=tree.name))
            continue

        # ONE builder, shared with the re-read and the dialog (п.4): the anchor is
        # the node's own pad, the sheet is the node's sheet, the name is
        # `generate_trace_name` — nothing about the record is rebuilt here.
        signature = frozenset(_pad_label(p, components) for p in unit.pads)
        old = by_key.pop((signature, unit.net_name), None)
        record, warning = capture_unit(
            adapter, unit, components=components,
            node_by_ref=classification.node_name_label_by_ref,
            node_sheet_by_ref=classification.anchor_sheet_by_ref,
            sheet_names=_sn, existing=old, existing_names=existing_names,
            resolver=classification.resolver)
        if warning:
            plan.warnings.append(warning)
        if record is None:
            continue
        record = dataclasses.replace(record, category=CATEGORY_EXTRA)
        if old is None and record.name:
            free = free_node_ref("net_trace", tree.name, record.name, tree.nodes,
                                 taken=existing_names)
            if free != record.name:
                record = dataclasses.replace(record, name=free)
        elif old is not None:
            # п.6: the repeat keeps the record's IDENTITY — its name (already kept
            # by `capture_unit`) and its uuid.
            record = dataclasses.replace(record, uuid=old.uuid)

        identity = net_trace_effective_name(record)
        fresh = CapturedTrace(record=record, identity=identity,
                              signature=signature,
                              track_count=len(record.tracks),
                              via_count=len(record.vias))
        if old is None:
            existing_names.append(identity)
            plan.added.append(fresh)
        elif same_geometry(old, record):
            plan.unchanged.append(identity)
        else:
            plan.updated.append((fresh, (len(old.tracks), len(old.vias),
                                         len(record.tracks), len(record.vias))))
    return plan


def extra_report_lines(tree_name: str, plan: RereadPlan) -> list[str]:
    """The extra door's LOG LINES: the same shape as the re-read's report (header,
    area, added, updated, unchanged, not taken, then one line per refusal), with
    its own header so the two doors can be told apart in one Log."""
    lines = [_("Tree {name!r}: extra copper read.").format(name=tree_name)]
    if plan.narrowed:
        lines.append(_("  area: selection — {tracks} track(s), {vias} via(s)")
                     .format(tracks=plan.area_tracks, vias=plan.area_vias))
    else:
        lines.append(_("  area: whole board"))
    if plan.added:
        lines.append(_("  added:      {names}").format(
            names=", ".join(c.identity for c in plan.added)))
    if plan.updated:
        described = "; ".join(
            _("{name} (was {old_tracks} tracks / {old_vias} via, now {new_tracks} / {new_vias})")
            .format(name=c.identity, old_tracks=counts[0], old_vias=counts[1],
                    new_tracks=counts[2], new_vias=counts[3])
            for c, counts in plan.updated)
        lines.append(_("  updated:    {names}").format(names=described))
    lines.append(_("  unchanged:  {count}").format(count=len(plan.unchanged)))
    if plan.discarded:
        counts = ", ".join(
            "{verdict} {count}".format(verdict=verdict,
                                       count=plan.discarded[verdict])
            for verdict in _NOT_TAKEN_ORDER if plan.discarded.get(verdict))
        lines.append(_("  not taken:  {counts} — copper another owner keeps")
                     .format(counts=counts))
    for warning in plan.warnings:
        lines.append("  " + warning)
    return lines
