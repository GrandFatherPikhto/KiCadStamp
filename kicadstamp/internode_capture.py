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
from typing import Any, Iterable, Mapping

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
from .internode_nodes import (
    AnchorSkip,
    RoleResolver,
    _item_reference,
    choose_anchor_pad,
    tree_node_keys,
)
from .net_trace_planner import resolve_live_anchor
from .sheet_names import resolve_sheet_path_names, sheet_in_path
from .trees import Tree
from .utils.units import MM

logger = logging.getLogger(__name__)

__all__ = [
    "AreaClassification",
    "CapturedTrace",
    "RereadPlan",
    "apply_reread_plan",
    "capture_unit",
    "capture_units",
    "internode_units",
    "plan_internode_reread",
    "reread_report_lines",
    "tree_net_trace_identities",
]

# The order the report lists copper the classification did NOT take in: the
# design's own table order (Р1), minus INTERNODE — that is what a re-read takes.
# MODULE rides between FOREIGN and CLUSTER: it is copper INSIDE a module, which
# that module's own tree re-reads, so the parent names it and leaves it (Т4-1).
_DISCARD_ORDER = (CopperVerdict.FOREIGN, CopperVerdict.MODULE,
                  CopperVerdict.CLUSTER, CopperVerdict.UNMOORED,
                  CopperVerdict.STUB)


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
    # ── what the re-read did NOT take, and why (Э4 of
    # plan_2026_09_15_internode_copper_sheets_and_nets) ────────────────────
    # `discarded` counts units by verdict (CopperVerdict.value -> count); only
    # the non-zero ones are ever reported. `matched_components` /
    # `unmatched_components` count the area's components that carry a Cluster and
    # did / did not match a node of this tree, and `node_keys` is that tree's own
    # key list ("PIF_DVDD/Channel_0", ...) — together they turn "the re-read sees
    # no copper" from silence into one line that names the tree and what its
    # nodes wait for (the live complaint this stage exists for).
    discarded: dict[str, int] = field(default_factory=dict)
    matched_components: int = 0
    unmatched_components: int = 0
    node_keys: list[str] = field(default_factory=list)

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


# ── the area's components ─────────────────────────────────────────────────

@dataclass(frozen=True)
class _Component:
    # `sheet` is the LEAF of the path and is DIAGNOSTICS ONLY (the probe prints
    # it to show what the old leaf rule would have done); `path` is what the
    # matching reads — every named segment of the hierarchical path, root first.
    ref: str
    fp: Any
    role: str | None
    cluster: str | None
    sheet: str | None
    path: tuple[str, ...] = ()


def _sheet_path(fp, sheet_names: dict[str, str]) -> tuple[str, ...]:
    """Every NAMED segment of a footprint's hierarchical sheet path, root first
    ('Channel_0', 'DAC'). A segment the dictionary cannot name is dropped:
    resolve_sheet_path_names reports it honestly as None, and an unnamed segment
    must match nothing (the same rule role_narrowing._fp_on_sheet follows)."""
    return tuple(n for n in resolve_sheet_path_names(fp, sheet_names) if n)


def _area_components(adapter, footprints: Iterable[Any],
                     sheet_names: dict[str, str]) -> dict[str, _Component]:
    out: dict[str, _Component] = {}
    for fp in footprints:
        if not isinstance(fp, Footprint):
            continue
        path = _sheet_path(fp, sheet_names)
        out[fp.ref] = _Component(
            ref=fp.ref,
            fp=fp,
            role=(adapter.get_field_value(fp, ROLE_FIELD_NAME) or None),
            cluster=(adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or None),
            sheet=path[-1] if path else None,
            path=path,
        )
    return out


def _node_key_label(label: str, sheet: str | None) -> str:
    """One tree node key as the report and the ambiguity warning name it —
    "PIF_DVDD/Channel_0", or just the label for a sheet-less key."""
    return f"{label}/{sheet}" if sheet else label


def _match_node(
    comp: _Component,
    node_keys: dict[tuple[str | None, str | None], str],
) -> tuple[tuple[str, str | None] | None, list[str]]:
    """((node label, node sheet), conflicting node names) for ONE area component.

    A component belongs to the node keyed `(cluster, sheet)` when its Cluster
    equals `cluster` AND `sheet` is ANY segment of its sheet path (or the key
    carries no sheet at all) — the SAME "sheet anywhere in the path" seam the
    role-narrowing cascade uses (sheet_names.sheet_in_path), so a tree's anchor
    and its copper can no longer answer the same question differently (Э1 of
    plan_2026_09_15_internode_copper_sheets_and_nets). A key WITH a sheet
    outranks the sheet-less key `(cluster, None)`: a component matching both
    belongs to the sheet-specific node.

    TWO DIFFERENT node keys matching ONE component is an AMBIGUITY, never a
    choice — that is what "two nodes of one Cluster on Channel_0 and DAC" means
    for a component living on ['Channel_0', 'DAC']. The component is left
    unmatched and the conflicting keys are returned so the caller can report
    them by name (the resolver's contract: it never guesses)."""
    if comp.cluster is None:
        return None, []
    matched = [(sheet, label)
               for (cluster, sheet), label in node_keys.items()
               if cluster == comp.cluster and sheet is not None
               and sheet_in_path(comp.path, sheet)]
    if len(matched) > 1:
        return None, [_node_key_label(label, sheet) for sheet, label in matched]
    if matched:
        sheet, label = matched[0]
        return (label, sheet), []
    label = node_keys.get((comp.cluster, None))
    if label is not None:
        return (label, None), []
    return None, []


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


def _items_from_unit(adapter, unit: CopperUnit, components: dict[str, _Component],
                     anchor_point, anchor_pad: PadRef, net: str, *, named: bool):
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
        ref = _item_reference(pads, components, anchor_pad) if named else None
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
        ref = _item_reference(pads, components, anchor_pad) if named else None
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


def _anchor_skip_message(unit: CopperUnit, skip: AnchorSkip | None,
                         allow_legacy: bool) -> str | None:
    """The Log warning for a NEW unit that got no anchor (Т2 of
    plan_2026_10_05_tree_reread_modules). The three kinds are named, never
    guessed around; `no_role` is the only one `allow_legacy` suppresses (a
    caller that cannot reference a pad at all gets silence, as before).

    `no_role` names the pad that has no Role (not necessarily `pads[0]`): such a
    pad enters the record's `pads:` signature as a bare ref, and a ref does not
    survive cloning, so the record cannot be written honestly."""
    pads = ", ".join(f"{p.ref}.{p.pad}" for p in unit.pads)
    if skip is None:
        return None
    if skip.kind == "no_role":
        if not allow_legacy:
            return None
        pad = skip.pad or unit.pads[0]
        return _(
            "skipped a piece of copper between pads {pads}: the pad {pad} "
            "has no Role field on the board, so the record could not be "
            "anchored").format(pads=pads, pad=f"{pad.ref}.{pad.pad}")
    if skip.kind == "no_node":
        pad = skip.pad or unit.pads[0]
        return _(
            "skipped a piece of copper between pads {pads}: the anchor pad "
            "{pad} belongs to no node of this tree, so the record has no "
            "anchor sheet to narrow its role").format(
                pads=pads, pad=f"{pad.ref}.{pad.pad}")
    roles = ", ".join(skip.roles) or "-"
    return _(
        "skipped a piece of copper between pads {pads}: no pad of the piece "
        "anchors the record — the role(s) {roles} do not narrow to the piece "
        "under the node's sheet and cluster").format(pads=pads, roles=roles)


def capture_unit(adapter, unit: CopperUnit, *,
                 components: dict[str, _Component],
                 node_by_ref: dict[str, str],
                 node_sheet_by_ref: Mapping[str, str | None],
                 sheet_names: dict[str, str],
                 existing: NetTrace | None = None,
                 existing_names: Iterable[str] = (),
                 allow_legacy: bool = True,
                 resolver: RoleResolver | None = None,
                 ) -> tuple[NetTrace | None, str | None]:
    """(record, skip_reason) for ONE inter-node unit — THE single place a unit
    becomes a `net_traces:` record, shared by the dialog's capture (plan §Э5)
    and by the re-read (plan §Э4), so the two can never produce different
    copper (the whole point of Э5's "one mechanism").

    `existing` — the record this unit was matched to, when there is one: its
    IDENTITY is kept (a re-read refreshes geometry, it never renames) and so is
    its representation — a NAMED record gets (role, pad) references, a LEGACY
    one stays on literal nets. `node_by_ref` maps a component to the tree node
    it belongs to (its Cluster tag), which names the record and labels the
    report; a component outside it makes the unit FOREIGN and it is never
    passed in here (the classification is the caller's).

    `node_sheet_by_ref` maps EVERY component of the area to the sheet of the
    tree node it belongs to — `None` for a node that carries no sheet. It is
    REQUIRED and deliberately has no default: a NEW record's anchor_sheet IS
    that node's sheet (Э3 of plan_2026_09_15_internode_copper_sheets_and_nets),
    so a caller that cannot name the node must say so and get a skip, rather
    than silently anchoring the record on the component's leaf segment — which
    is exactly the defect this argument removes."""
    net = unit.net_name
    if net is None:
        return None, _(
            "skipped a piece of copper between pads {pads}: it carries no net "
            "name on the board").format(
                pads=", ".join(f"{p.ref}.{p.pad}" for p in unit.pads))

    signature = frozenset(_pad_label(p, components) for p in unit.pads)
    named = existing is None or bool(existing.name)

    if existing is None:
        if resolver is None:
            resolver = RoleResolver(adapter, sheet_names)
        anchor_pad, skip = choose_anchor_pad(
            unit, components, node_sheet_by_ref, resolver=resolver)
        if anchor_pad is None:
            return None, _anchor_skip_message(unit, skip, allow_legacy)
        comp = components[anchor_pad.ref]
        pad_obj = adapter.get_pad_by_number(comp.fp, anchor_pad.pad)
        point = pad_obj.position if pad_obj is not None else comp.fp.position
        role = comp.role
        sheet = node_sheet_by_ref[anchor_pad.ref]
        cluster = comp.cluster
        pad = anchor_pad.pad
        rotation = round(comp.fp.angle_deg, 4)
        labels = [node_by_ref.get(p.ref) or _pad_label(p, components)
                  for p in unit.pads]
        name = generate_trace_name(net, labels, existing_names)
    else:
        try:
            anchor_fp, point = resolve_live_anchor(adapter, existing, sheet_names)
        except Exception as e:  # noqa: BLE001 — reported, never fatal here
            return None, _(
                "record {ref!r}: its anchor could not be resolved live ({err}) "
                "— left untouched").format(
                    ref=net_trace_effective_name(existing), err=e)
        role, sheet, cluster = existing.anchor_role, existing.anchor_sheet, \
            existing.anchor_cluster
        pad = existing.anchor_pad
        rotation = round(anchor_fp.angle_deg, 4)
        name = existing.name
        # An EXISTING record keeps its anchor (Т2-3): the item fall-back stays
        # the unit's first pad, byte-for-byte what capture_unit always wrote.
        anchor_pad = unit.pads[0]

    tracks, vias = _items_from_unit(adapter, unit, components, point, anchor_pad,
                                    net, named=named)
    record = NetTrace(net=net, anchor_role=role, name=name,
                      anchor_sheet=sheet, anchor_cluster=cluster,
                      anchor_pad=pad, anchor_rotation_deg=rotation,
                      pads=sorted(signature), tracks=tracks, vias=vias)
    if existing is not None:
        record = dataclasses.replace(record, retired=existing.retired,
                                     skip=existing.skip, comment=existing.comment)
    return record, None


def capture_units(adapter, units: Iterable[CopperUnit], *,
                  area_footprints: Iterable[Any],
                  node_by_ref: dict[str, str],
                  node_sheet_by_ref: Mapping[str, str | None],
                  sheet_names: dict[str, str] | None = None,
                  existing_names: Iterable[str] = (),
                  ) -> tuple[list[CapturedTrace], list[str]]:
    """Capture a batch of ALREADY-CLASSIFIED inter-node units as NEW records —
    the dialog's half of capture_unit (a tree being built has nothing to match
    against yet). Returns (captures, warnings).

    `node_sheet_by_ref` is the dialog's knowledge of which sheet each component's
    TREE NODE lives on (its cluster's sheet — gui/dock_hub.py builds it from the
    dialog's own ReReadCluster records) and is REQUIRED for the same reason as
    in capture_unit: it is the anchor_sheet a NEW record gets."""
    _sn = dict(sheet_names or {})
    components = _area_components(adapter, area_footprints, _sn)
    # ONE resolver per batch: its per-run candidate cache is what keeps the
    # anchor check from re-sweeping the board for every unit (Т2-5).
    resolver = RoleResolver(adapter, _sn)
    names = list(existing_names)
    out: list[CapturedTrace] = []
    warnings: list[str] = []
    for unit in units:
        record, warning = capture_unit(adapter, unit, components=components,
                                       node_by_ref=node_by_ref,
                                       node_sheet_by_ref=node_sheet_by_ref,
                                       sheet_names=_sn,
                                       existing_names=names,
                                       resolver=resolver)
        if warning:
            warnings.append(warning)
        if record is None:
            continue
        identity = net_trace_effective_name(record)
        names.append(identity)
        out.append(CapturedTrace(
            record=record, identity=identity,
            signature=frozenset(record.pads or ()),
            track_count=len(record.tracks), via_count=len(record.vias)))
    return out, warnings


# ── the classifier — the ONE place both the re-read and "Select inter-node
#    copper" go through (plan_2026_10_05_tree_reread_modules T5-1) ───────────

@dataclass
class AreaClassification:
    """The AREA's copper classified against `tree`'s nodes — the ONE classifier's
    result, shared by the re-read (which turns `units` into records) and "Select
    inter-node copper" (which highlights `units`).

    `units` are ONLY the pieces classify_unit took (INTERNODE); `discarded` counts
    the rest by verdict (including MODULE — copper inside one module, Т4-1);
    `warnings` is the honest list of what could not be decided. The component
    maps and the `resolver` are carried so the record builder reuses the SAME
    node identities and the SAME per-run candidate cache — a second pass over the
    same copper must never answer differently."""
    units: list[CopperUnit] = field(default_factory=list)
    discarded: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    matched_components: int = 0
    unmatched_components: int = 0
    node_keys: list[str] = field(default_factory=list)
    components: dict[str, _Component] = field(default_factory=dict)
    node_key_by_ref: dict[str, tuple[str | None, str | None]] = field(
        default_factory=dict)
    node_name_label_by_ref: dict[str, str] = field(default_factory=dict)
    anchor_sheet_by_ref: dict[str, str | None] = field(default_factory=dict)
    resolver: Any = None


def _classify_area(adapter, cfg: Config, tree: Tree, *,
                   area_items: Iterable[Any],
                   area_footprints: Iterable[Any],
                   sheet_names: dict[str, str] | None = None
                   ) -> AreaClassification:
    """The area's copper classified against `tree` — the SHARED step of
    `plan_internode_reread` and `internode_units` (T5-1).

    It collects the tree's nodes (`tree_node_keys`), matches the area's
    components to them (`_match_node`), splits the copper into units and runs
    `classify_unit` once per unit. Everything a caller needs to go on is carried
    out: the INTERNODE units, the discarded counters, the warnings, and the node
    maps + the run's `RoleResolver`."""
    _sn = dict(sheet_names or {})
    out = AreaClassification()
    node_keys = tree_node_keys(tree, cfg)
    out.components = _area_components(adapter, area_footprints, _sn)
    # Э1: a component belongs to a node when ANY segment of its sheet path names
    # that node's sheet (the SAME seam the role resolver uses — sheet_in_path),
    # so a tree's anchor and its copper can no longer disagree. The node's OWN
    # sheet — never the component's leaf — is what a NEW record stores as its
    # anchor_sheet (Э3), so the anchor narrows the role to THIS instance.
    #
    # Т1: a node's IDENTITY is `(cluster, sheet)`, not its label — three channels
    # reuse the SAME Cluster tag (`DAC_BUF`) on different sheets, so a label that
    # names one node cannot tell two apart. classify_unit gets the KEY; the
    # LABEL keeps naming the record and the report.
    node_owner_by_ref: dict[str, Any] = {}
    for comp in out.components.values():
        match, conflicts = _match_node(comp, node_keys)
        if conflicts:
            out.warnings.append(_(
                "component {ref!r} (Cluster {cluster!r}, sheet path {path}) "
                "matches {count} nodes of tree {tree!r} at once: {nodes} — the "
                "component is left out of the tree, it is never guessed").format(
                    ref=comp.ref, cluster=comp.cluster,
                    path="/".join(comp.path) or "-", count=len(conflicts),
                    tree=tree.name, nodes=", ".join(conflicts)))
            continue
        if match is None:
            if comp.cluster is not None:
                out.unmatched_components += 1
            continue
        label, sheet = match
        key = (comp.cluster, sheet)
        out.node_key_by_ref[comp.ref] = key
        # Т4-3: the name label of a node reached through a module is
        # `<cluster>/<sheet>`; Т4-1: its OWNER is that module.
        out.node_name_label_by_ref[comp.ref] = node_keys.name_label(key) or label
        node_owner_by_ref[comp.ref] = node_keys.owners.get(key)
        out.anchor_sheet_by_ref[comp.ref] = sheet
        out.matched_components += 1
    out.node_keys = [_node_key_label(label, sheet)
                     for (_cluster, sheet), label in node_keys.items()]
    # ONE resolver for the whole run — its per-run candidate cache keeps the
    # anchor check (Т2) from re-sweeping the board for every unit.
    out.resolver = RoleResolver(adapter, _sn)

    units, unit_warnings = find_copper_units(adapter, area_items,
                                             footprints=area_footprints)
    out.warnings.extend(unit_warnings)
    for unit in units:
        verdict = classify_unit(unit, out.node_key_by_ref,
                                module_owner_by_ref=node_owner_by_ref)
        if verdict is not CopperVerdict.INTERNODE:
            # Э4: the copper the classification did NOT take is COUNTED, not
            # silently dropped — the report names it by verdict (the live
            # complaint was an empty report while 766 units existed).
            out.discarded[verdict.value] = out.discarded.get(verdict.value, 0) + 1
            continue
        out.units.append(unit)
    return out


def internode_units(adapter, cfg: Config, tree: Tree, *,
                    area_items: Iterable[Any],
                    area_footprints: Iterable[Any],
                    sheet_names: dict[str, str] | None = None
                    ) -> tuple[list[CopperUnit], dict[str, int], list[str]]:
    """The pieces of `tree`'s inter-node copper the classification TAKES from
    `area` — `(units, discarded_by_verdict, warnings)`. THE invariant (T5-1 of
    plan_2026_10_05_tree_reread_modules): this is the SAME classifier the re-read
    uses (`plan_internode_reread` runs the identical `_classify_area`), so
    "Select inter-node copper" highlights exactly what a whole-board re-read
    would take. No second classifier exists."""
    classification = _classify_area(
        adapter, cfg, tree, area_items=area_items,
        area_footprints=area_footprints, sheet_names=sheet_names)
    return classification.units, classification.discarded, \
        classification.warnings


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

    The plan also carries what was NOT taken (Э4 of
    plan_2026_09_15_internode_copper_sheets_and_nets): the discarded units by
    verdict, and — when not one area component matched a node of this tree — the
    tree's own node keys, so the report can say what its nodes wait for instead
    of printing an empty result.

    The classification itself is `_classify_area` — the SAME step
    `internode_units` (and therefore "Select inter-node copper") runs, so the
    re-read and the highlighting can never disagree (T5-1)."""
    _sn = dict(sheet_names or {})
    plan = RereadPlan()
    if zone_count:
        plan.warnings.append(_(
            "zones are not read (see the Z1 work): only tracks and vias count "
            "as copper, so a pour never appears in the capture"))

    classification = _classify_area(
        adapter, cfg, tree, area_items=area_items,
        area_footprints=area_footprints, sheet_names=_sn)
    plan.warnings.extend(classification.warnings)
    plan.discarded = classification.discarded
    plan.matched_components = classification.matched_components
    plan.unmatched_components = classification.unmatched_components
    plan.node_keys = classification.node_keys
    components = classification.components
    resolver = classification.resolver

    identities = tree_net_trace_identities(tree)
    tree_records = [nt for nt in cfg.net_traces
                    if net_trace_effective_name(nt) in set(identities)]
    known = {net_trace_effective_name(nt) for nt in tree_records}
    for ident in identities:
        if ident not in known:
            plan.warnings.append(_(
                "tree {tree!r}: node {ref!r} references no net_traces: record — "
                "skipped").format(tree=tree.name, ref=ident))

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

    for unit in classification.units:
        signature = frozenset(_pad_label(p, components) for p in unit.pads)
        old = by_signature.pop(signature, None)
        if old is None:
            old = legacy_by_net.pop(unit.net_name, None)

        # ONE builder, shared with the dialog's capture (capture_unit) — the
        # re-read and the "Extract tree" dialog must produce the SAME copper.
        record, warning = capture_unit(
            adapter, unit, components=components,
            node_by_ref=classification.node_name_label_by_ref,
            node_sheet_by_ref=classification.anchor_sheet_by_ref,
            sheet_names=_sn, existing=old, existing_names=existing_names,
            resolver=resolver)
        if warning:
            plan.warnings.append(warning)
        if record is None:
            continue
        identity = net_trace_effective_name(record)
        fresh = CapturedTrace(record=record, identity=identity,
                              signature=signature,
                              track_count=len(record.tracks),
                              via_count=len(record.vias))

        if old is None:
            existing_names.append(identity)
            plan.added.append(fresh)
            continue

        taken.add(id(old))
        if _same_geometry(old, record):
            plan.unchanged.append(identity)
        else:
            plan.updated.append((fresh, (len(old.tracks), len(old.vias),
                                         len(record.tracks), len(record.vias))))

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
    and in the flat list.

    The found/updated/unchanged/not-found lines are the historical ones, byte
    for byte (logs and tests read them as substrings). Everything Э4 of
    plan_2026_09_15_internode_copper_sheets_and_nets adds — the discarded
    counters and the "no component matched any node" line — is appended AFTER
    them: silence about thrown-away copper was the second half of the live
    complaint, so the report now ends by saying what it did not take and why."""
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
    if plan.discarded:
        counts = ", ".join(
            "{verdict} {count}".format(verdict=verdict.value,
                                       count=plan.discarded[verdict.value])
            for verdict in _DISCARD_ORDER if plan.discarded.get(verdict.value))
        lines.append(_("  not taken:  {counts} — copper the classification left "
                       "alone").format(counts=counts))
    if plan.unmatched_components and not plan.matched_components:
        lines.append(_(
            "  no component of the area matched any node of tree {tree!r} by "
            "(Cluster, sheet) — the tree's nodes expect: {keys}").format(
                tree=tree_name, keys=", ".join(plan.node_keys) or "-"))
    for warning in plan.warnings:
        lines.append("  " + warning)
    return lines
