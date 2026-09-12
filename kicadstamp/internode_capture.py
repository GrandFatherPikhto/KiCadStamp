# kicadstamp/internode_capture.py
"""
internode_capture.py — capture / RE-READ a tree's inter-node copper as
`net_traces:` records (plan_2026_09_12_internode_copper_core, stage Э4; design
§4, §6, §11, §16).

Pure: no Qt, no widget, no board writes. The live board is read through the
adapter interface only, so the whole planning step is testable with a mock.

TWO READINGS OF "ONE UNIT"
  * geometry — find_copper_units (internode_copper.py): the copper between pads;
  * identity — the SET of pads the unit connects, stored as `pads:` on the
    record ("ROLE.pad" strings). A re-read matches a fresh unit to a stored
    record by THAT SET, never by geometry: the geometry is exactly what the
    re-read refreshes (design §11). A changed pad set is a DIFFERENT bridge and
    gets a new record, never a silent overwrite of the old one's contents.

NOTHING IS EVER REMOVED (design §6). A record whose copper is gone from the
board is reported as `missing` and left exactly as it is — deleting is a human
action, and "the copper is gone" cannot be told apart from "I pulled it out for
a minute".

LEGACY RECORDS — the migration bridge. A record with no stored `pads:` (every
profile written before this stage) has only its NET as an identity, so it can
only be matched by net. The first successful re-read stores the signature, and
from then on the match is exact.

WHAT A RE-READ WRITES
  * a NAMED record keeps its name (the identity, and the tree node referencing
    it — a re-read refreshes geometry, it does not rename) and its `(role,
    pad)` representation: every item references a pad of the unit, so no
    literal net is needed and the recording survives cloning (design §15). An
    item that touches no pad of its own (a mid-chain via) takes the unit's
    anchor pad — the whole unit is ONE net, so any of its pads resolves to it;
  * a LEGACY record stays legacy: its items keep literal nets, deliberately, so
    the file it lives in is not silently rewritten into another representation.
    It DOES gain the `pads:` signature, which makes the next re-read exact.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import (
    Config,
    NetTrace,
    load_template_track,
    load_template_via,
    net_trace_effective_name,
    net_trace_pad_signature,
)
from .constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from .domain.board import Footprint
from .i18n import _
from .internode_copper import (
    CopperUnit,
    CopperVerdict,
    PadRef,
    classify_unit,
    find_copper_units,
    generate_trace_name,
)
from .net_trace_planner import resolve_live_anchor
from .sheet_names import resolve_sheet_path_names
from .trees import Tree
from .utils.units import MM

logger = logging.getLogger(__name__)

__all__ = [
    "CapturedTrace",
    "RereadPlan",
    "apply_reread_plan",
    "plan_internode_reread",
    "reread_report_lines",
    "tree_net_trace_identities",
]


# ── result shapes ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CapturedTrace:
    """One fresh unit as it will be stored, plus what the Log report needs."""
    record: NetTrace
    identity: str                     # name:, else net: (the record's key)
    signature: frozenset[str]         # the unit's pad identity
    track_count: int
    via_count: int


@dataclass
class RereadPlan:
    """What a re-read found. Pure data — the caller decides whether to write it.

    `unchanged` holds identities whose stored geometry already matches the
    board; `missing` holds identities whose copper is NOT on the board any more
    (the records are kept — see the module docstring)."""
    added: list[CapturedTrace] = field(default_factory=list)
    updated: list[tuple[CapturedTrace, tuple[int, int, int, int]]] = field(
        default_factory=list)   # (fresh, (old_tracks, old_vias, new_tracks, new_vias))
    unchanged: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def records(self) -> list[NetTrace]:
        """Every record the plan wants stored (added + refreshed updates)."""
        return [c.record for c in self.added] + [c.record for c, _ in self.updated]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated)


# ── tree walking ───────────────────────────────────────────────────────────

def _walk_nodes(nodes: Iterable[Any]) -> Iterable[Any]:
    for node in nodes:
        yield node
        yield from _walk_nodes(getattr(node, "children", None) or [])


def tree_net_trace_identities(tree: Tree) -> list[str]:
    """The identities of the `net_traces:` records this tree's kind=net_trace
    nodes reference, in document order, deduplicated. The node's ref IS the
    record identity (name:, else a legacy record's net:) — the same seam
    link_trees resolves the node through."""
    out: list[str] = []
    for node in _walk_nodes(tree.nodes):
        if node.kind == "net_trace" and node.ref and node.ref not in out:
            out.append(node.ref)
    return out


def _tree_node_keys(tree: Tree, cfg: Config) -> dict[tuple[str | None, str | None], str]:
    """{(cluster, sheet): node label} for the tree's placement nodes — the
    components the tree owns, i.e. the "nodes" of the design's classification
    table. The label is the Cluster tag (the design's `ch0_dac`), falling back
    to the Entity name for a cluster-less Entity."""
    entities = {e.name: e for e in cfg.entities}
    keys: dict[tuple[str | None, str | None], str] = {}
    for node in _walk_nodes(tree.nodes):
        if node.kind not in (None, "placement"):
            continue
        entity = entities.get(node.ref)
        if entity is None:
            continue
        label = entity.cluster or entity.name
        keys.setdefault((entity.cluster, entity.sheet), label)
    return keys


# ── the area's components ─────────────────────────────────────────────────

@dataclass(frozen=True)
class _Component:
    ref: str
    fp: Any
    role: str | None
    cluster: str | None
    sheet: str | None


def _sheet_of(fp, sheet_names: dict[str, str]) -> str | None:
    """The LEAF sheet name of a footprint, or None when the sheet dictionary
    cannot name it (the same honest "no match" resolve_sheet_path_names
    documents)."""
    names = [n for n in resolve_sheet_path_names(fp, sheet_names) if n]
    return names[-1] if names else None


def _area_components(adapter, footprints: Iterable[Any],
                     sheet_names: dict[str, str]) -> dict[str, _Component]:
    out: dict[str, _Component] = {}
    for fp in footprints:
        if not isinstance(fp, Footprint):
            continue
        out[fp.ref] = _Component(
            ref=fp.ref,
            fp=fp,
            role=(adapter.get_field_value(fp, ROLE_FIELD_NAME) or None),
            cluster=(adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or None),
            sheet=_sheet_of(fp, sheet_names),
        )
    return out


def _pad_label(pad: PadRef, components: dict[str, _Component]) -> str:
    """The identity label of one pad: "ROLE.pad". A component with no Role field
    falls back to its ref — nothing better exists, and the label must be stable
    between the write and the next read."""
    comp = components.get(pad.ref)
    role = (comp.role if comp is not None else None) or pad.ref
    return f"{role}.{pad.pad}"


# ── record building ────────────────────────────────────────────────────────

def _layer_str(layer) -> str:
    from .utils.layers import layer_to_str
    return layer_to_str(layer)


def _item_reference(unit: CopperUnit, item_pads: tuple[PadRef, ...],
                    components: dict[str, _Component],
                    anchor_pad: PadRef) -> tuple[str, str] | None:
    """The (role, pad) reference of ONE item: the pad it touches itself, or —
    when it touches none (a mid-chain via) — the unit's anchor pad. The whole
    unit is ONE net, so ANY of its pads resolves to the same net at apply time;
    that is why a pad-less item needs no literal net either (design §15: the
    capture already knows the pads, it must not re-derive them)."""
    for pad in item_pads:
        comp = components.get(pad.ref)
        if comp is not None and comp.role:
            return (comp.role, pad.pad)
    comp = components.get(anchor_pad.ref)
    if comp is not None and comp.role:
        return (comp.role, anchor_pad.pad)
    return None


def _items_from_unit(adapter, unit: CopperUnit, components: dict[str, _Component],
                     anchor_point, net: str, *, named: bool):
    """The unit's tracks/vias as stored items: local (along/across) offsets from
    the anchor point — a RAW board-frame difference, exactly like
    net_trace_extract — plus the net reference of each item."""
    tracks = []
    for i, t in enumerate(unit.tracks):
        entry: dict[str, Any] = {
            "start_along_mm": round((t.start.x - anchor_point.x) / MM, 4),
            "start_across_mm": round((t.start.y - anchor_point.y) / MM, 4),
            "end_along_mm": round((t.end.x - anchor_point.x) / MM, 4),
            "end_across_mm": round((t.end.y - anchor_point.y) / MM, 4),
            "width_mm": round(t.width_mm, 4),
            "layer": _layer_str(t.layer),
        }
        pads = unit.track_pads[i] if i < len(unit.track_pads) else ()
        ref = _item_reference(unit, pads, components, unit.pads[0]) if named else None
        if ref is not None:
            entry["net_from_role"], entry["net_from_role_pad"] = ref
        else:
            entry["net"] = net
        tracks.append(load_template_track(entry))

    vias = []
    for j, v in enumerate(unit.vias):
        entry = {
            "offset_along_mm": round((v.position.x - anchor_point.x) / MM, 4),
            "offset_across_mm": round((v.position.y - anchor_point.y) / MM, 4),
            "drill_mm": round(v.drill_mm, 4),
            "diameter_mm": round(v.diameter_mm, 4),
        }
        pads = unit.via_pads[j] if j < len(unit.via_pads) else ()
        ref = _item_reference(unit, pads, components, unit.pads[0]) if named else None
        if ref is not None:
            entry["net_from_role"], entry["net_from_role_pad"] = ref
        else:
            entry["net"] = net
        vias.append(load_template_via(entry))

    return tracks, vias


def _same_geometry(old: NetTrace, new: NetTrace) -> bool:
    """True when the stored record already describes the board's copper — the
    comparison the "unchanged" count is built from. Compares only what a
    re-read refreshes (the offsets/widths/layers), never the identity."""
    if len(old.tracks) != len(new.tracks) or len(old.vias) != len(new.vias):
        return False
    for a, b in zip(old.tracks, new.tracks):
        if (a.start_along_mm, a.start_across_mm, a.end_along_mm,
                a.end_across_mm, a.width_mm, a.layer) != \
           (b.start_along_mm, b.start_across_mm, b.end_along_mm,
                b.end_across_mm, b.width_mm, b.layer):
            return False
    for a, b in zip(old.vias, new.vias):
        if (a.offset_along_mm, a.offset_across_mm, a.drill_mm,
                a.diameter_mm) != \
           (b.offset_along_mm, b.offset_across_mm, b.drill_mm,
                b.diameter_mm):
            return False
    return True


def _anchor_context(adapter, unit: CopperUnit, components: dict[str, _Component]):
    """(role, sheet, cluster, pad, point, rotation) for a NEW record: the unit's
    first pad. The anchor role is REQUIRED by the grammar, so a component
    without a Role field is reported by the caller as a skip, not anchored
    somewhere arbitrary."""
    pad = unit.pads[0]
    comp = components.get(pad.ref)
    if comp is None or not comp.role:
        return None
    pad_obj = adapter.get_pad_by_number(comp.fp, pad.pad)
    point = pad_obj.position if pad_obj is not None else comp.fp.position
    return (comp.role, comp.sheet, comp.cluster, pad.pad, point,
            round(comp.fp.angle_deg, 4))


# ── the plan ───────────────────────────────────────────────────────────────

def plan_internode_reread(adapter, cfg: Config, tree: Tree, *,
                          area_items: Iterable[Any],
                          area_footprints: Iterable[Any],
                          sheet_names: dict[str, str] | None = None,
                          zone_count: int | None = None) -> RereadPlan:
    """Plan a re-read of `tree`'s inter-node copper against the LIVE board.

    `area_items`/`area_footprints` define the AREA: the whole board for "read
    everything", or the current selection for "read just this" (design §5). The
    caller collects them; this function never widens or narrows them.

    `zone_count` — the number of zones in the area, when the caller can know it
    (the domain Zone is a name-only stub with no bulk read, so today nobody
    can): a non-zero count is reported as "zones are not read", because their
    absence from the capture must not look like lost copper (design §14).
    """
    _sn = dict(sheet_names or {})
    plan = RereadPlan()
    if zone_count:
        plan.warnings.append(_(
            "zones are not read (see the Z1 work): only tracks and vias count "
            "as copper, so a pour never appears in the capture"))

    node_keys = _tree_node_keys(tree, cfg)
    components = _area_components(adapter, area_footprints, _sn)
    node_by_ref: dict[str, str] = {}
    for comp in components.values():
        if comp.cluster is None:
            continue
        label = node_keys.get((comp.cluster, comp.sheet))
        if label is None:
            label = node_keys.get((comp.cluster, None))
        if label is not None:
            node_by_ref[comp.ref] = label

    identities = tree_net_trace_identities(tree)
    tree_records = [nt for nt in cfg.net_traces
                    if net_trace_effective_name(nt) in set(identities)]
    known = {net_trace_effective_name(nt) for nt in tree_records}
    for ident in identities:
        if ident not in known:
            plan.warnings.append(_(
                "tree {tree!r}: node {ref!r} references no net_traces: record — "
                "skipped").format(tree=tree.name, ref=ident))

    units, unit_warnings = find_copper_units(adapter, area_items,
                                             footprints=area_footprints)
    plan.warnings.extend(unit_warnings)

    # Match keys. A record with a stored signature is matched by it; a legacy
    # one (no signature) only by its net. Both maps are POPPED on use, so one
    # stored record can never satisfy two fresh units.
    by_signature: dict[frozenset[str], NetTrace] = {}
    legacy_by_net: dict[str, NetTrace] = {}
    for nt in tree_records:
        signature = net_trace_pad_signature(nt)
        if signature:
            by_signature.setdefault(signature, nt)
        else:
            legacy_by_net.setdefault(nt.net, nt)

    taken: set[int] = set()
    existing_names = [net_trace_effective_name(nt) for nt in cfg.net_traces]

    for unit in units:
        if classify_unit(unit, node_by_ref) is not CopperVerdict.INTERNODE:
            continue
        net = unit.net_name
        if net is None:
            plan.warnings.append(_(
                "skipped a piece of copper between pads {pads}: it carries no "
                "net name on the board").format(
                    pads=", ".join(f"{p.ref}.{p.pad}" for p in unit.pads)))
            continue
        signature = frozenset(_pad_label(p, components) for p in unit.pads)
        old = by_signature.pop(signature, None)
        if old is None:
            old = legacy_by_net.pop(net, None)

        if old is None:
            context = _anchor_context(adapter, unit, components)
            if context is None:
                plan.warnings.append(_(
                    "skipped a piece of copper between pads {pads}: the pad "
                    "{pad} has no Role field on the board, so the record could "
                    "not be anchored").format(
                        pads=", ".join(f"{p.ref}.{p.pad}" for p in unit.pads),
                        pad=f"{unit.pads[0].ref}.{unit.pads[0].pad}"))
                continue
            role, sheet, cluster, pad, point, rotation = context
            # The generated name uses the TREE NODE labels of the pads'
            # components (the design's `spi_clk__fpga__ch0_dac`), not the
            # "ROLE.pad" identity labels — a name a human reads.
            labels = [node_by_ref.get(p.ref) or _pad_label(p, components)
                      for p in unit.pads]
            name = generate_trace_name(net, labels, existing_names)
            tracks, vias = _items_from_unit(adapter, unit, components, point,
                                            net, named=True)
            record = NetTrace(net=net, anchor_role=role, name=name,
                              anchor_sheet=sheet, anchor_cluster=cluster,
                              anchor_pad=pad, anchor_rotation_deg=rotation,
                              pads=sorted(signature), tracks=tracks, vias=vias)
            existing_names.append(name)
            plan.added.append(CapturedTrace(
                record=record, identity=name, signature=signature,
                track_count=len(tracks), via_count=len(vias)))
            continue

        taken.add(id(old))
        ident = net_trace_effective_name(old)
        named = bool(old.name)
        try:
            anchor_fp, point = resolve_live_anchor(adapter, old, _sn)
        except Exception as e:  # noqa: BLE001 — one broken record must not stop the rest
            plan.warnings.append(_(
                "record {ref!r}: its anchor could not be resolved live ({err}) "
                "— left untouched").format(ref=ident, err=e))
            continue
        tracks, vias = _items_from_unit(adapter, unit, components, point, net,
                                        named=named)
        fresh = dataclasses.replace(
            old, net=net, pads=sorted(signature),
            anchor_rotation_deg=round(anchor_fp.angle_deg, 4),
            tracks=tracks, vias=vias)
        if _same_geometry(old, fresh):
            plan.unchanged.append(ident)
        else:
            plan.updated.append((CapturedTrace(
                record=fresh, identity=ident, signature=signature,
                track_count=len(tracks), via_count=len(vias)),
                (len(old.tracks), len(old.vias), len(tracks), len(vias))))

    for nt in tree_records:
        if id(nt) not in taken:
            plan.missing.append(net_trace_effective_name(nt))
    return plan


def apply_reread_plan(cfg: Config, plan: RereadPlan) -> Config:
    """A NEW Config whose `net_traces` reflect the plan (the input is never
    mutated): an updated record is replaced IN PLACE — preserving its position
    in the list and therefore the order everything else reads — and an added
    record is appended. NOTHING is removed: a `missing` record stays exactly as
    it is (design §6)."""
    updates = {c.identity: c.record for c, _ in plan.updated}
    merged = [updates.get(net_trace_effective_name(nt), nt)
              for nt in cfg.net_traces]
    present = {net_trace_effective_name(nt) for nt in merged}
    merged.extend(c.record for c in plan.added if c.identity not in present)
    return dataclasses.replace(cfg, net_traces=merged)


def reread_report_lines(tree_name: str, plan: RereadPlan) -> list[str]:
    """The re-read report as LOG LINES (design §6 — a list, never a dialog).
    Full names, never abbreviated: they are how a bridge is found in the tree
    and in the flat list."""
    lines = [_("Tree {name!r}: inter-node copper re-read.").format(name=tree_name)]
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
    for ident in plan.missing:
        lines.append(_(
            "  not found:  {name} — the copper is no longer on the board, the "
            "record is kept").format(name=ident))
    lines.append(_("  unchanged:  {count}").format(count=len(plan.unchanged)))
    for warning in plan.warnings:
        lines.append("  " + warning)
    return lines
