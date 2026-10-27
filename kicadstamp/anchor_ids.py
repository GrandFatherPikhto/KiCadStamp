# kicadstamp/anchor_ids.py
"""WHICH anchors the current config produces — and which registry keys are ORPHANS.

ONE home for the two questions that must never be answered twice (plan
plan_2026_10_27_orphan_anchor_keys_own_nothing, Denis 27.10): «which anchors does the
config protect?» (``reconcile``'s ``known_anchor_ids``) and «is this key's record
still in the config?» (an ORPHAN KEY).

WHY one home. Ч0 measured what a disagreement costs: an entity ``fpga_oscill_fpga``
was rebuilt between 15:04 and 15:12 (cell uuid e8b2c497 -> e00d0b65), its 25 registry
keys stayed behind, and reading the instance next to it subtracted 19 LIVE items as
«another record's copper» — while the first real Redraw would have deleted them
(prune: the anchor is not among ``known_anchor_ids``). One builder, one answer.

DIRECTION. The set is built from the very builders the planner writes keys with
(``clone_anchor_id`` / ``entity_anchor_id`` + ``anchor_ids_with_nested``,
``thermal_anchor_id``, ``net_trace_anchor_id``) — no key parsing, no second list. The
heavy modules are imported INSIDE the function: this module must stay cheap enough to
import from a GUI worker and from ``registry.py``'s callers without dragging
``placement`` in.

NOT here: who OWNS a key (``selection_narrowing.is_own_key``) and what to do with an
orphan (the doors decide: read it, free it, prune it).
"""
from __future__ import annotations

__all__ = [
    "anchor_part",
    "config_anchor_ids",
    "key_is_orphan",
    "orphan_keys",
    "orphan_uuids",
]


def config_anchor_ids(cfg) -> set[str]:
    """Every anchor_id the CURRENT config produces — the SAME set ``reconcile``
    protects from a prune (it used to be built privately in ``apply_pipeline``).

    Each clone/entity contributes its OWN anchor_id AND every nested
    ``"<outer>/<nested.name>"`` at every depth: ``reconcile`` matches an anchor_id
    WHOLE, so a nested key missing from this set is pruned by any ``--only`` run that
    excludes its placement, silently deleting the nested cell's copper (У5.1б).
    ``retired`` records produce nothing. Thermal arrays and net traces each have their
    own builder and their own prefix.
    """
    from .net_trace_planner import net_trace_anchor_id
    from .placement.services.clone_position_calculator import (
        anchor_ids_with_nested,
        clone_anchor_id,
        entity_anchor_id,
    )
    from .placement.services.via_planner import thermal_anchor_id

    cells = getattr(cfg, "cells", None) or {}
    ids: set[str] = set()
    for c in getattr(cfg, "clone_placements", None) or ():
        if getattr(c, "retired", False):
            continue
        ids |= anchor_ids_with_nested(clone_anchor_id(c),
                                      getattr(c, "cell", None), cells)
    for e in getattr(cfg, "entities", None) or ():
        if getattr(e, "retired", False):
            continue
        ids |= anchor_ids_with_nested(entity_anchor_id(e),
                                      getattr(e, "cell", None), cells)
    ids |= {thermal_anchor_id(t)
            for t in getattr(cfg, "thermal_via_arrays", None) or ()
            if not getattr(t, "retired", False)}
    ids |= {net_trace_anchor_id(nt)
            for nt in getattr(cfg, "net_traces", None) or ()
            if not getattr(nt, "retired", False)}
    return ids


def anchor_part(key) -> str:
    """The ANCHOR part of a registry key (``anchor|cell|role|index``) — the first
    part, split exactly as ``reconcile`` has always split it."""
    return str(key or "").split("|", 1)[0]


def key_is_orphan(key, anchors) -> bool:
    """True ⇔ the config produces NO record this key's anchor names.

    The definition is EXACTLY the one prune already uses for every prefix (Denis,
    27.10): if prune is trusted to delete that copper from the board, the same answer
    can be trusted to say it owns nothing when it is READ. No branch per prefix — a
    `net:` or `thermal:` key whose record is gone is the same nobody's copper.
    """
    return anchor_part(key) not in anchors


def orphan_keys(via_entries, track_entries, anchors) -> frozenset:
    """The keys over BOTH registry files whose anchor the config no longer produces."""
    return frozenset(
        key
        for entries in (via_entries or {}, track_entries or {})
        for key in entries
        if key_is_orphan(key, anchors))


def orphan_uuids(via_entries, track_entries, anchors) -> frozenset:
    """The uuids held by ORPHAN keys ONLY.

    A uuid that a LIVE key holds as well is NOT returned: an orphan must not take
    ownership away from a record that still exists (the register of two keys on one
    item is the Ч1-of-27.10 defect, and this rule must not become its mirror)."""
    live: set = set()
    orphans: set = set()
    for entries in (via_entries or {}, track_entries or {}):
        for key, entry in entries.items():
            uuid = getattr(entry, "uuid", None)
            if not uuid:
                continue
            (orphans if key_is_orphan(key, anchors) else live).add(uuid)
    return frozenset(orphans - live)
