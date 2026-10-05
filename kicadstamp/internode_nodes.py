# kicadstamp/internode_nodes.py
"""
internode_nodes.py — the two things a tree's inter-node copper re-read needs
BESIDES the copper itself (plan_2026_10_05_tree_reread_modules, Т1/Т2):

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

Qt-free and testable directly. The role check goes through the SAME cascade
apply uses (clone_role_resolver -> role_narrowing._narrow_by_sheet_cluster_selection)
with the SELECTION step disabled: a re-read often runs WITH a board selection,
and a check that leaned on it would call a record unambiguous that apply, without
a selection, then refuses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

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
from .trees import Tree, _find_tree

__all__ = [
    "AnchorSkip",
    "RoleResolver",
    "choose_anchor_pad",
    "tree_node_keys",
]


# ── Т1: the tree's nodes, module content included ──────────────────────────

def tree_node_keys(tree: Tree, cfg) -> dict[tuple[str | None, str | None], str]:
    """{(cluster, sheet): node label} for the tree's placement nodes — the
    components the tree owns, i.e. the "nodes" of the design's classification
    table — INCLUDING the nodes of every tree embedded through a `kind "module"`
    node, recursively (Т1 of plan_2026_10_05_tree_reread_modules).

    The walk is the same one `trees.find_role_placement_matches` performs: a
    module node's OWN `children` are ordinary nodes of THIS tree (walked in
    place), and the embedded tree is resolved by `_find_tree(cfg, node.ref)`.
    A missing tree is SKIPPED; a cycle (A -> B -> A) is broken by a set of
    already walked tree names, so `tree_node_keys` terminates on any input.

    The KEY is `(cluster, sheet)`, not the label: three channels carry the SAME
    Cluster tag (`DAC_BUF`) on different sheets, and a piece of copper between
    two of them must classify as INTERNODE (two nodes), never as one cluster's
    own copper. The label (Cluster, falling back to the Entity name) stays what
    the record name and the report use."""
    entities = {e.name: e for e in (getattr(cfg, "entities", []) or [])}
    keys: dict[tuple[str | None, str | None], str] = {}
    seen: set[str] = {tree.name}

    def walk(nodes) -> None:
        for node in nodes:
            if node.kind == "module":
                nested = _find_tree(cfg, node.ref)
                if nested is not None and nested.name not in seen:
                    seen.add(nested.name)
                    walk(nested.nodes)
                # A module node's OWN children are ordinary nodes of THIS tree.
                walk(node.children)
                continue
            if node.kind in (None, "placement"):
                entity = entities.get(node.ref)
                if entity is not None:
                    label = getattr(entity, "cluster", None) or getattr(
                        entity, "name", None) or node.ref
                    keys.setdefault(
                        (getattr(entity, "cluster", None),
                         getattr(entity, "sheet", None)), label)
            walk(node.children)

    walk(tree.nodes)
    return keys


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

    def candidates(self, role: str) -> list:
        cached = self._cache.get(role)
        if cached is None:
            cached = _role_candidates(self._adapter, role, None)
            self._cache[role] = cached
        return cached

    def resolves_to(self, role: str, sheet: str | None, cluster: str | None,
                    expected_ref: str) -> bool:
        """True when `role` narrowed by `(sheet, cluster)`, WITHOUT the
        selection step, leaves exactly the footprint `expected_ref`."""
        candidates = self.candidates(role)
        if not candidates:
            return False
        narrowed = _narrow_by_sheet_cluster_selection(
            list(candidates), self._adapter, set(),
            sheet, cluster, self._sheet_names, self._label, role)
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


def _item_reference(item_pads: tuple[PadRef, ...],
                    components: Mapping[str, Any],
                    anchor_pad: PadRef) -> tuple[str, str] | None:
    """The (role, pad) reference of ONE item: the pad it touches itself, or —
    when it touches none (a mid-chain via) — the unit's anchor pad. The whole
    unit is ONE net, so ANY of its pads resolves to the same net at apply time;
    that is why a pad-less item needs no literal net either (design §15: the
    capture already knows the pads, it must not re-derive them).

    Shared by the record builder (`internode_capture._items_from_unit`) and the
    anchor check below: the check must resolve EXACTLY the reference the builder
    would write, so the two must be one rule, never two looks."""
    for pad in item_pads:
        comp = components.get(pad.ref)
        if comp is not None and comp.role:
            return (comp.role, pad.pad)
    comp = components.get(anchor_pad.ref)
    if comp is not None and comp.role:
        return (comp.role, anchor_pad.pad)
    return None


def _reference_resolves(item_pads: tuple[PadRef, ...],
                        components: Mapping[str, Any], anchor_pad: PadRef,
                        resolver: RoleResolver, sheet: str | None,
                        cluster: str | None) -> bool:
    """True when the reference `_item_reference` would write for ONE item
    resolves, under the candidate anchor's `(sheet, cluster)`, to exactly the
    footprint the item touches."""
    for pad in item_pads:
        comp = components.get(pad.ref)
        if comp is not None and comp.role:
            return resolver.resolves_to(comp.role, sheet, cluster, comp.ref)
    comp = components.get(anchor_pad.ref)
    if comp is not None and comp.role:
        return resolver.resolves_to(comp.role, sheet, cluster, comp.ref)
    return True


def _items_resolve(unit: CopperUnit, components: Mapping[str, Any],
                   anchor_pad: PadRef, resolver: RoleResolver,
                   sheet: str | None, cluster: str | None) -> bool:
    """True when EVERY track/via of the unit would redraw from `anchor_pad` —
    each item's recorded reference resolving to the piece it touches."""
    for i in range(len(unit.tracks)):
        pads = unit.track_pads[i] if i < len(unit.track_pads) else ()
        if not _reference_resolves(pads, components, anchor_pad, resolver,
                                   sheet, cluster):
            return False
    for j in range(len(unit.vias)):
        pads = unit.via_pads[j] if j < len(unit.via_pads) else ()
        if not _reference_resolves(pads, components, anchor_pad, resolver,
                                   sheet, cluster):
            return False
    return True


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
         byte-for-byte what the old `pads[0]` rule produced."""
    if not unit.pads:
        return None, AnchorSkip("no_node")
    for pad in unit.pads:
        comp = components.get(pad.ref)
        if comp is None or not comp.role:
            return None, AnchorSkip("no_role", pad=pad)
    if not any(p.ref in node_sheet_by_ref for p in unit.pads):
        return None, AnchorSkip("no_node", pad=unit.pads[0])
    failed: list[str] = []
    for pad in unit.pads:
        if pad.ref not in node_sheet_by_ref:
            # A pad whose tree node is unknown is simply not a candidate — it
            # does NOT block the unit (the addendum, cell (а)).
            continue
        comp = components[pad.ref]
        sheet = node_sheet_by_ref[pad.ref]
        cluster = comp.cluster
        if not resolver.resolves_to(comp.role, sheet, cluster, comp.ref):
            failed.append(comp.role)
            continue
        if not _items_resolve(unit, components, pad, resolver, sheet, cluster):
            failed.append(comp.role)
            continue
        return pad, None
    return None, AnchorSkip("no_candidate", roles=tuple(failed))
