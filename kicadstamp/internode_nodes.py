# kicadstamp/internode_nodes.py
"""
internode_nodes.py — what a tree's inter-node copper re-read needs BESIDES the
copper itself (plan_2026_10_05_tree_reread_modules, Т1/Т2; and, since Д-1 of
plan_2026_10_15_internode_power_copper_anchor, the items a record stores):

  * TREE NODES WITH MODULE CONTENT (Т1) — `tree_node_keys` walks a tree's
    placement nodes AND, recursively, the nodes of every tree embedded through a
    `kind "module"` node, exactly like `trees.find_role_placement_matches`
    (`kicadstamp/trees.py`): a module node's OWN children are ordinary nodes of
    THIS tree, an embedded tree is looked up by `_find_tree(cfg, node.ref)`, a
    missing tree is skipped, and a cycle is broken by a set of already walked
    tree names.

  * THE ANCHOR A NEW RECORD MUST GET (Т2) — `choose_anchor_pad` picks the pad
    from which the stored record will REDRAW. A record anchored on a role the
    resolver cannot narrow (three AD_DAC, one per channel) is a fatal at apply
    and never redraws; anchoring on the channel side — whose sheet and cluster
    DO narrow — while the unique FPGA end resolves by its own role is exactly
    what net_trace_planner._item_net_name documents.

    TWO TIERS (plan_2026_10_15_internode_power_copper_anchor). Tier 1 is that
    rule, element by element, and wins whenever it has an answer — the choice is
    byte-for-byte the 05.10 one. Tier 2 runs ONLY when tier 1 found NO candidate
    at all: the anchor must still resolve to ITSELF, while an element whose own
    (role, pad) does NOT resolve to the footprint it touches is written with the
    ANCHOR's pair. A piece between clusters that SHARE a role — the three
    C_IN_BULK of a power body, +5V between them — has no tier-1 candidate at all
    (every element at another cluster's C_IN_BULK narrows to the anchor's OWN
    instance, never to the one it touches), so without tier 2 that copper could
    never be re-read: the fact of 15.10. Tier 2 is legal because the whole piece
    is ONE net — an element's reference only has to name SOME pad of the piece,
    which is exactly how a mid-chain via has always been written.

  * THE ITEMS A RECORD STORES — `_items_from_unit` (moved here from
    internode_capture by Д-1) turns the unit's tracks/vias into stored items and
    asks `_item_reference` for each one's pair: the SAME rule the anchor check
    runs, which is exactly why the builder lives beside it — a second copy of
    "which pad does this item own" is impossible.

Qt-free and testable directly. The role check goes through the SAME cascade
apply uses (clone_role_resolver -> role_narrowing._narrow_by_sheet_cluster_selection)
with the SELECTION step disabled: a re-read often runs WITH a board selection,
and a check that leaned on it would call a record unambiguous that apply, without
a selection, then refuses.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass
from typing import Any, Mapping

from .config import load_template_track, load_template_via
from .degenerate_segments import collapse_degenerate_segments
from .i18n import _
from .internode_copper import CopperUnit, PadRef
# The resolver's OWN pieces, imported directly: the candidate sweep and the
# sheet -> Cluster cascade are the SAME functions apply uses, so the anchor check
# can never drift from the redraw (plan Т2-2). Importing the private seams is
# deliberate — clone_role_resolver.py is a >800-line giant (rule 45: giants only
# shrink), so the empty-selection wrapper lives HERE, not there. The selection
# step is disabled by passing an EMPTY selected_refs set, which is the whole
# point: a re-read often runs WITH a selection, and a check that leaned on it
# would bless a record apply-without-selection then refuses.
from .placement.services.clone_role_resolver import _role_candidates
from .placement.services.role_narrowing import _narrow_by_sheet_cluster_selection
from .sheet_names import instance_label
from .trees import Tree, _find_tree
from .utils.units import MM

logger = logging.getLogger(__name__)

__all__ = [
    "AnchorSkip",
    "RoleResolver",
    "TreeNodes",
    "choose_anchor_pad",
    "tree_node_keys",
]


# ── Т1: the tree's nodes, module content included ──────────────────────────

class TreeNodes(_Mapping):
    """The tree's placement nodes AND their OWNER (Т1/Т4-1 of
    plan_2026_10_05_tree_reread_modules).

    It keeps the `{(cluster, sheet): label}` mapping `tree_node_keys` always
    returned — the report, `internode_capture._match_node` and the probes read it
    UNCHANGED, so a module's own copper can be told apart from a label without a
    second look — and adds `owners`: the same key -> the OWNER of that node.

    An owner is `None` for a node that is the tree's OWN (a top-level node, or a
    `kind "module"` node's own children, which are ordinary nodes of THIS tree),
    and the TOPMOST `kind "module"` node through which the node came for a node
    of an EMBEDDED tree (a module nested deeper maps to its topmost module, never
    to itself). That is exactly what lets a piece of copper whose every node
    belongs to ONE module be called `module` — the tree of that module, not the
    parent's, owns it (Т4-1)."""

    def __init__(self, labels: dict[tuple[str | None, str | None], str],
                 owners: dict[tuple[str | None, str | None], Any]) -> None:
        self.labels = labels
        self.owners = owners

    def __getitem__(self, key):
        return self.labels[key]

    def __iter__(self):
        return iter(self.labels)

    def __len__(self) -> int:
        return len(self.labels)

    def __eq__(self, other):
        # Equal to the plain `{(cluster, sheet): label}` dict it replaces, so a
        # caller that only ever wanted the keys never sees the owners (rule 33 —
        # the walk tests keep asserting membership and `== {}` unchanged).
        if isinstance(other, TreeNodes):
            return self.labels == other.labels and self.owners == other.owners
        return self.labels == other

    def __repr__(self) -> str:
        return repr(self.labels)

    def name_label(self, key: tuple[str | None, str | None]) -> str | None:
        """The label a NEW record's NAME uses for `key` (Т4-3): the Cluster
        alone for the tree's OWN node (names of existing records never change),
        `<cluster>/<sheet>` for a node that came through a module. Three channels
        sharing the tag `DAC_BUF` must yield three DISTINCT names
        (`pa_en__dac_buf_channel_0__fpga`, `..._channel_1_...`), not `_2`/`_3`
        suffixes that say nothing about the channel."""
        label = self.labels.get(key)
        if label is None:
            return None
        if self.owners.get(key) is None:
            return label
        cluster, sheet = key
        # The shape lives in ONE owner (sheet_names.instance_label): a ROOT node
        # (sheet is ROOT_SHEET) is named by the Cluster alone — "<cluster>//" names
        # nothing, and there is exactly one root sheet.
        return instance_label(cluster, sheet) if sheet else label


def tree_node_keys(tree: Tree, cfg) -> TreeNodes:
    """`TreeNodes` for the tree's placement nodes — the components the tree
    owns, i.e. the "nodes" of the design's classification table — INCLUDING the
    nodes of every tree embedded through a `kind "module"` node, recursively
    (Т1 of plan_2026_10_05_tree_reread_modules).

    The walk is the same one `trees.find_role_placement_matches` performs: a
    module node's OWN `children` are ordinary nodes of THIS tree (walked in
    place, and they belong to THIS tree, not to the module), and the embedded
    tree is resolved by `_find_tree(cfg, node.ref)`. A missing tree is SKIPPED;
    a cycle (A -> B -> A) is broken by a set of already walked tree names, so
    `tree_node_keys` terminates on any input. A node reached THROUGH a module
    carries that TOPMOST module as its owner, so "the copper inside one module"
    is distinguishable from "the copper between two of them" (Т4-1).

    The KEY is `(cluster, sheet)`, not the label: three channels carry the SAME
    Cluster tag (`DAC_BUF`) on different sheets, and a piece of copper between
    two of them must classify as INTERNODE (two nodes), never as one cluster's
    own copper. The label (Cluster, falling back to the Entity name) stays what
    the report uses; `name_label` gives the record-name label."""
    entities = {e.name: e for e in (getattr(cfg, "entities", []) or [])}
    labels: dict[tuple[str | None, str | None], str] = {}
    owners: dict[tuple[str | None, str | None], Any] = {}
    seen: set[str] = {tree.name}

    def walk(nodes, owner) -> None:
        for node in nodes:
            if node.kind == "module":
                nested = _find_tree(cfg, node.ref)
                if nested is not None and nested.name not in seen:
                    seen.add(nested.name)
                    # The FIRST module entered owns everything reached through
                    # it; a module nested deeper keeps that topmost owner.
                    walk(nested.nodes, owner if owner is not None else node)
                # A module node's OWN children are ordinary nodes of THIS tree.
                walk(node.children, owner)
                continue
            if node.kind in (None, "placement"):
                entity = entities.get(node.ref)
                if entity is not None:
                    key = (getattr(entity, "cluster", None),
                           getattr(entity, "sheet", None))
                    label = getattr(entity, "cluster", None) or getattr(
                        entity, "name", None) or node.ref
                    labels.setdefault(key, label)
                    owners.setdefault(key, owner)
            walk(node.children, owner)

    walk(tree.nodes, None)
    return TreeNodes(labels, owners)


# ── Т2: the anchor a NEW record redraws from ───────────────────────────────

class RoleResolver:
    """Selection-free role resolution with a PER-RUN candidate cache (Т2-5).

    `resolves_to` answers the exact question apply will ask later: does `role`,
    narrowed by `(sheet, cluster)`, come down to EXACTLY `expected_ref`? The
    candidate sweep of a role is done ONCE per run and reused — the anchor check
    runs for every unit and every pad, and a per-unit sweep would re-read the
    whole board a hundred times over (the cache the plan asks for, and the only
    thing that keeps the check off the meter)."""

    def __init__(self, adapter, sheet_names: Mapping[str, str] | None = None,
                 label: str = "internode anchor check") -> None:
        self._adapter = adapter
        self._sheet_names = dict(sheet_names or {})
        self._label = label
        self._cache: dict[str, list] = {}
        # Т4-4: the RESULT of narrowing is cached too, keyed by the whole
        # question. The anchor check asks the SAME (role, sheet, cluster) once
        # per pad and once per item of every unit, and every call emits an INFO
        # `role_narrowing` line — a live `fpga` re-read wrote 532 of them. The
        # candidate sweep alone is not enough; the cascade must run ONCE.
        self._narrow_cache: dict[
            tuple[str, str | None, str | None], list] = {}

    def candidates(self, role: str) -> list:
        cached = self._cache.get(role)
        if cached is None:
            cached = _role_candidates(self._adapter, role, None)
            self._cache[role] = cached
        return cached

    def narrowed(self, role: str, sheet: str | None,
                 cluster: str | None) -> list:
        """The candidates of `role` narrowed by `(sheet, cluster)` WITHOUT the
        selection step — the cascade result, cached per run (Т4-4)."""
        key = (role, sheet, cluster)
        cached = self._narrow_cache.get(key)
        if cached is None:
            candidates = self.candidates(role)
            cached = [] if not candidates else _narrow_by_sheet_cluster_selection(
                list(candidates), self._adapter, set(),
                sheet, cluster, self._sheet_names, self._label, role)
            self._narrow_cache[key] = cached
        return cached

    def resolves_to(self, role: str, sheet: str | None, cluster: str | None,
                    expected_ref: str) -> bool:
        """True when `role` narrowed by `(sheet, cluster)`, WITHOUT the
        selection step, leaves exactly the footprint `expected_ref`."""
        narrowed = self.narrowed(role, sheet, cluster)
        return len(narrowed) == 1 and narrowed[0].ref == expected_ref


@dataclass(frozen=True)
class AnchorSkip:
    """Why a NEW unit got no anchor (Т2). `kind` is one of:

      * "no_role"      — some pad has no Role field on the board: the record's
                         `pads:` signature would carry a bare ref, and a ref
                         does not survive cloning/per-numbering, so the record
                         would come back as "not found" plus a fresh one on the
                         very next re-read;
      * "no_node"      — NO pad belongs to a node of this tree, so there is no
                         honest `anchor_sheet` anywhere;
      * "no_candidate" — every pad that HAS a node failed the resolver check.

    `pad` is the pad a warning should name; `roles` are the roles that stayed
    ambiguous ("no_candidate" only)."""
    kind: str
    pad: PadRef | None = None
    roles: tuple[str, ...] = ()


def _item_own_pad(item_pads: tuple[PadRef, ...],
                  components: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """`(role, pad, ref)` of the FIRST pad-bearing component of ONE item — the pad
    the item OWNS — or None when no pad of the item carries a Role (a mid-chain
    via, a track whose ends are a joint, or a legacy component).

    THE one walk of an item's own pad, shared by the pair-builder
    (`_item_reference`) and the anchor check (`_reference_resolves`): the two must
    answer about the SAME pad, so "which pad does this item own" lives here ONCE
    (the 05.10 acceptance's A6/A7: one rule, never two looks)."""
    for pad in item_pads:
        comp = components.get(pad.ref)
        if comp is not None and comp.role:
            return comp.role, pad.pad, comp.ref
    return None


def _item_reference(item_pads: tuple[PadRef, ...],
                    components: Mapping[str, Any], *,
                    resolver: RoleResolver | None = None,
                    sheet: str | None = None,
                    cluster: str | None = None,
                    fallback_pair: tuple[str, str] | None = None
                    ) -> tuple[str, str] | None:
    """The (role, pad) reference of ONE item: the pad it touches ITSELF when that
    pair resolves to its own footprint, else — and when it touches none (a
    mid-chain via) — the FALL-BACK pair. The whole unit is ONE net, so ANY of its
    pads resolves to the same net at apply time; that is why a pad-less item needs
    no literal net either (design §15: the capture already knows the pads, it must
    not re-derive them).

    Shared by the record builder (`_items_from_unit`, right below) and the anchor
    check (`_reference_resolves`): the check must resolve EXACTLY the reference the
    builder would write, so the two are one rule, never two looks.

    `resolver` + `(sheet, cluster)` are the CONTEXT the record will be read back
    with (the anchor's own node): an item's own pair is written only when it
    resolves, under that context, to the very footprint the item touches — the
    second tier of plan_2026_10_15_internode_power_copper_anchor (module
    docstring). Without a `resolver` there is nothing to check against, so the own
    pair stands.

    `fallback_pair` is what everything else gets, and its OWNER picks it — a
    reference is never guessed HERE:
      * a NEW record passes the CHOSEN anchor pad's pair: the anchor check has just
        proved that role resolves to that very pad;
      * an EXISTING record passes the RECORD's own `(anchor_role, anchor_pad)`,
        which resolves to the record's anchor by construction. The piece's FIRST
        pad is not a substitute: under the record's context its role may resolve to
        a THIRD component on ANOTHER net — the wrong-net write A2 forbids, and how
        it came back through the update path (Д-1 of the 15.10 acceptance);
      * `None` means the caller has no VERIFIED pair at all (a record that names no
        anchor pad). The item then keeps the record's literal net — the unit's own
        net name — which is the piece's net; never a pair that could land outside
        the piece."""
    own = _item_own_pad(item_pads, components)
    if own is not None and (resolver is None
                            or resolver.resolves_to(own[0], sheet, cluster, own[2])):
        return own[0], own[1]
    return fallback_pair


def _reference_resolves(item_pads: tuple[PadRef, ...],
                        components: Mapping[str, Any],
                        resolver: RoleResolver, sheet: str | None,
                        cluster: str | None) -> bool:
    """True when the reference `_item_reference` would write for ONE item is the
    item's OWN pair — i.e. it resolves, under the candidate anchor's
    `(sheet, cluster)`, to exactly the footprint the item touches."""
    own = _item_own_pad(item_pads, components)
    if own is None:
        # An item that touches no pad of its own (a mid-chain via) is written
        # with the ANCHOR's pair — and for a CANDIDATE that role is EXACTLY the
        # one the caller resolved to the anchor under this very (sheet, cluster)
        # before offering it, so it cannot fail here. The `True` is the (А7 of
        # the 05.10 acceptance) equivalence, not a second look; mutation А6
        # replaces it by the FIRST pad's role, caught by the anchor being the
        # CHOSEN pad, not `pads[0]`.
        return True
    return resolver.resolves_to(own[0], sheet, cluster, own[2])


def _items_resolve(unit: CopperUnit, components: Mapping[str, Any],
                   resolver: RoleResolver, sheet: str | None,
                   cluster: str | None) -> bool:
    """True when EVERY track/via of the unit would redraw from the candidate
    anchor's `(sheet, cluster)` — each item's OWN pair resolving to the piece it
    touches (tier 1 of `choose_anchor_pad` below)."""
    for i in range(len(unit.tracks)):
        pads = unit.track_pads[i] if i < len(unit.track_pads) else ()
        if not _reference_resolves(pads, components, resolver, sheet, cluster):
            return False
    for j in range(len(unit.vias)):
        pads = unit.via_pads[j] if j < len(unit.via_pads) else ()
        if not _reference_resolves(pads, components, resolver, sheet, cluster):
            return False
    return True


# ── the stored items of ONE unit — the builder `_item_reference` serves ─────

def _layer_str(layer) -> str:
    """BoardLayer -> KiCad copper-layer name, through the ONE shared mapper."""
    from .utils.layers import layer_to_str
    return layer_to_str(layer)


def _items_from_unit(unit: CopperUnit, components: Mapping[str, Any],
                     anchor_point, net: str, *, named: bool,
                     resolver: RoleResolver | None = None,
                     sheet: str | None = None, cluster: str | None = None,
                     fallback_pair: tuple[str, str] | None = None):
    """The unit's tracks/vias as stored items: local (along/across) offsets from
    the anchor point — a RAW board-frame difference, exactly like
    net_trace_extract — plus the net reference of each item, decided by
    `_item_reference`: the SAME rule the anchor check runs, where `resolver` +
    `(sheet, cluster)` and `fallback_pair` are documented.

    Lives HERE, next to that rule (moved out of internode_capture by Д-1 of the
    15.10 acceptance — the rule and its only builder must not drift apart, and
    that file was at the 800-line limit). An item whose reference comes back None
    keeps the record's LITERAL net (`net`, the unit's own net name): a pair that
    cannot be verified is never invented."""
    # A positionally unidentifiable segment is NOT written (plan Ч1): its ends
    # are pulled into one point (a via it touches, else its midpoint) and the
    # neighbours that met it are re-pointed there, so the record stays
    # connected. ONE rule for every capture path — degenerate_segments.
    collapsed = collapse_degenerate_segments(
        [(t.start, t.end) for t in unit.tracks],
        attractors=[v.position for v in unit.vias] + [anchor_point])
    if collapsed.changed:
        logger.info(_("inter-node copper: {count} degenerate track segment(s) "
                      "collapsed (shorter than 2 × tolerance)").format(
                          count=len(collapsed.collapsed)))
        for note in collapsed.collapsed:
            logger.debug("  collapsed track (%d, %d) -> (%d, %d) nm into (%d, %d)",
                         note.start.x, note.start.y, note.end.x, note.end.y,
                         note.collapsed_to.x, note.collapsed_to.y)

    tracks = []
    for i, (start, end) in zip(collapsed.kept_indices, collapsed.segments):
        t = unit.tracks[i]
        entry: dict[str, Any] = {
            "start_along_mm": round((start.x - anchor_point.x) / MM, 4),
            "start_across_mm": round((start.y - anchor_point.y) / MM, 4),
            "end_along_mm": round((end.x - anchor_point.x) / MM, 4),
            "end_across_mm": round((end.y - anchor_point.y) / MM, 4),
            "width_mm": round(t.width_mm, 4),
            "layer": _layer_str(t.layer),
        }
        pads = unit.track_pads[i] if i < len(unit.track_pads) else ()
        ref = (_item_reference(pads, components, resolver=resolver, sheet=sheet,
                               cluster=cluster, fallback_pair=fallback_pair)
               if named else None)
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
        ref = (_item_reference(pads, components, resolver=resolver, sheet=sheet,
                               cluster=cluster, fallback_pair=fallback_pair)
               if named else None)
        if ref is not None:
            entry["net_from_role"], entry["net_from_role_pad"] = ref
        else:
            entry["net"] = net
        vias.append(load_template_via(entry))

    return tracks, vias


def choose_anchor_pad(unit: CopperUnit, components: Mapping[str, Any],
                      node_sheet_by_ref: Mapping[str, str | None], *,
                      resolver: RoleResolver
                      ) -> tuple[PadRef | None, AnchorSkip | None]:
    """(anchor pad, None) or (None, skip) for a NEW record (Т2).

    The order of the checks is the task addendum's:

      1. ANY pad without a Role SKIPS the whole unit — such a pad enters the
         record's `pads:` signature as a bare ref, and a ref does not survive
         cloning, so choosing another anchor cannot heal that.
      2. If NO pad's node is known the unit is skipped: there is no honest
         `anchor_sheet` for its record.
      3. Otherwise the FIRST pad (in `unit.pads` order — the order today's code
         already uses) whose node is known AND for which both the anchor role
         and every item's recorded role resolve to exactly the footprint the
         piece touches is the anchor. When `pads[0]` qualifies the result is
         byte-for-byte what the old `pads[0]` rule produced.

    TIER 2 (plan_2026_10_15_internode_power_copper_anchor) runs ONLY when tier 1
    has no answer at all, and asks less of the ITEMS: the anchor must still
    resolve to ITSELF (the same `resolver.resolves_to`, never weakened), while an
    item that cannot name its own footprint is written with the anchor's pair
    (`_item_reference`). The first candidate wins, exactly as above — so every
    piece the tier-1 rule captured yesterday is captured identically today, and a
    piece whose roles are shared across clusters (power copper) stops being
    skipped forever."""
    if not unit.pads:
        return None, AnchorSkip("no_node")
    for pad in unit.pads:
        comp = components.get(pad.ref)
        if comp is None or not comp.role:
            return None, AnchorSkip("no_role", pad=pad)
    if not any(p.ref in node_sheet_by_ref for p in unit.pads):
        return None, AnchorSkip("no_node", pad=unit.pads[0])
    candidates: list[tuple[PadRef, str | None]] = []
    failed: list[str] = []
    for pad in unit.pads:
        if pad.ref not in node_sheet_by_ref:
            # A pad whose tree node is unknown is simply not a candidate — it
            # does NOT block the unit (the addendum, cell (а)).
            continue
        comp = components[pad.ref]
        sheet = node_sheet_by_ref[pad.ref]
        if not resolver.resolves_to(comp.role, sheet, comp.cluster, comp.ref):
            failed.append(comp.role)
            continue
        candidates.append((pad, sheet))
    for pad, sheet in candidates:
        # Tier 1: every item names its OWN pad — the 05.10 rule.
        if _items_resolve(unit, components, resolver, sheet,
                          components[pad.ref].cluster):
            return pad, None
    if candidates:
        # Tier 2: only where tier 1 had nothing. The anchor's own role resolves
        # (checked above); the items that cannot name their own footprint take
        # its pair — the piece is one net.
        return candidates[0][0], None
    return None, AnchorSkip("no_candidate", roles=tuple(failed))
