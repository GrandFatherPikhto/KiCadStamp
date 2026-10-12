# kicadstamp/apply_duplicate_guard.py
"""The apply pipeline's ONE door guard for a duplicated refdes (2026-10-12).

Why (plan_2026_10_12_duplicate_refdes_guard)
--------------------------------------------
Denis remade the MCU power section, re-annotated and updated the board without
"Delete footprints with no symbols". The old footprints stayed as ORPHANS and
four of them kept refdes the new ones also carry, so Redraw moved the orphan
instead of the real footprint — every consumer addressed a footprint by its BARE
refdes (``move_executor.py:27``, ``flip_manager.py:20/29``). A duplicate refdes
has no single right answer, so the rule is not "pick better": it is REFUSE.

What is refused
---------------
A RECORD WHOLE — an ``Item`` of the dependency order, a Phase-0
``coordinate_placement`` move, an imprint plan. Nothing of it is executed: no
moves, no flips, no vias, no tracks. The record is ATOMIC because its copper is
planned from the positions of ALL its roles — moving only the non-duplicated
components would leave the cell torn (a via/track reaching for a footprint that
stayed behind in the orphan stack).

The refdes set is the run's OWN resolved roles (``Item.produces`` +
``Item.anchor_ref``), never the whole board: a duplicate no record of this run
touches can never refuse anything ("не мешает").

An ORPHAN WITH NO DUPLICATE is NOT refused — one footprint, one answer, nothing
to pick wrongly. It is surfaced only by the "Add entities" window
(``refdes_duplicates.orphan_short_reason``).

Where it sits
-------------
One call per entry kind, all of them in ``ApplyPipeline.run`` (items + imprint
plans) or its Phase-0 block (coordinate moves), so Apply, Redraw and --only all
pass through the SAME detector (``refdes_duplicates``). The detection and the
refusal LOGIC live here; ``apply_pipeline`` only calls it.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable

from .i18n import _
from .refdes_duplicates import (duplicate_log_lines, duplicate_refdes_groups,
                                duplicate_summary_line, refdes_duplicate_of)

logger = logging.getLogger(__name__)

__all__ = [
    "guard_duplicate_refdes", "guard_duplicate_moves", "guard_duplicate_plans",
]


def _item_refs(item) -> set[str]:
    """Every refdes an Item resolves or is anchored on — the run's OWN roles."""
    refs = {str(r) for r in (getattr(item, "produces", None) or ()) if r}
    anchor = getattr(item, "anchor_ref", None)
    if anchor:
        refs.add(str(anchor))
    return refs


def _guard(adapter, entries: Iterable,
           refs_of: Callable, label_of: Callable, log) -> tuple[list, list]:
    """The ONE door decision, shared by every entry kind.

    ``(allowed, report_lines)``: entries whose refdes stand twice are dropped
    WHOLE, both copies and the orphan are named in the Log (ERROR), and the
    run's closing line lists the refused records. ``report_lines`` carries that
    same closing line for the dry-run report, which never reads the Log.
    """
    entries = list(entries or ())
    if not entries:
        return entries, []
    # No board to read (a unit-built pipeline calls a step with adapter=None, and
    # the dry-run report is built the same way): there is nothing to compare
    # against, so nothing is refused — the guard never invents a duplicate.
    if getattr(adapter, "get_footprints", None) is None:
        return entries, []
    wanted: set[str] = set()
    for entry in entries:
        wanted |= refs_of(entry)
    if not wanted:
        return entries, []
    duplicates = duplicate_refdes_groups(adapter.get_footprints(), refs=wanted)
    if not duplicates:
        return entries, []
    allowed: list = []
    refused: list = []
    for entry in entries:
        ref = refdes_duplicate_of(refs_of(entry), duplicates)
        if ref is None:
            allowed.append(entry)
        else:
            refused.append((entry, ref))
    if not refused:
        return entries, []
    for _entry, ref in refused:
        for line in duplicate_log_lines(ref, duplicates[ref]):
            log.error(line)
    summary = duplicate_summary_line(label_of(entry) for entry, _ref in refused)
    if summary:
        log.warning(summary)
    return allowed, ([summary] if summary else [])


def guard_duplicate_refdes(adapter, items: Iterable, log=None) -> tuple[list, list]:
    """Refuse every dependency-order Item whose resolved refdes stands twice.

    Called once, right after ``ApplyPipeline._resolve_order()`` — before a
    single move is planned, so a refused item contributes NO moves and (because
    ``plan_item`` is never called for it) NO copper: its vias/tracks never
    enter the planner's accumulated plan."""
    return _guard(adapter, items, _item_refs,
                  lambda item: getattr(item, "label", str(item)), log or logger)


def guard_duplicate_moves(adapter, moves: Iterable, log=None) -> tuple[list, list]:
    """Refuse every Phase-0 (coordinate_placements) move whose refdes stands
    twice — the same detector, the same refusal, one entry per move."""
    return _guard(adapter, moves, lambda move: {str(move.ref)},
                  lambda move: _("move of {ref}").format(ref=move.ref),
                  log or logger)


def guard_duplicate_plans(adapter, plans: Iterable, log=None) -> tuple[list, list]:
    """Refuse every imprint plan whose recorded refdes stand twice — an imprint
    record is atomic too (its vias/tracks are planned from the same region)."""
    return _guard(adapter, plans,
                  lambda plan: {str(m.ref) for m in (getattr(plan, "moves", None) or ())},
                  lambda plan: _("imprint {name}").format(
                      name=getattr(plan, "entity_name", str(plan))),
                  log or logger)
