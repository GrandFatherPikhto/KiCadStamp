# kicadstamp/registry_match.py
"""Read-only matching of planned copper against the live board — ONE routine
for both the claiming (adoption) path and the "which copper is this record's"
read path (plan ``plan_2026_10_05_select_cell_split.md``, СЦ-1/СЦ-2; Denis
2026-10-05).

Lives in its own module, NOT in ``registry.py``: that file is at the 800-line
limit (deepseek.md §45), so the matching moves HERE and ``registry.py`` imports
it — the registry file shrinks, it does not grow.

The rule mirrors the already-canonical pair
``net_trace_planner.match_net_trace_pieces`` / ``adopt_net_trace_copper``:
"the claiming path and the read-only path can never disagree" — both call the
SAME function. There is ONE strict tier order per planned command:

  1. REGISTRY — the key's stored uuid, resolved against the live board;
  2. GEOMETRY — the registry's own ``_live_matches`` predicate (via_matches /
     track_matches — the same predicate reconcile uses) for copper no registry
     entry knows yet.

Tier 2 NEVER takes a uuid owned by ANY entry of this registry (either under
another key — e.g. a legacy clone_placement while an Entity materializes the
same cell — is never stolen) and never reuses a live item already taken by
another command of this same call. A command found neither way comes back with
``live=None`` (copper recorded in the config but not on the board — a normal
answer, never an error).

WRITES NOTHING: not the registry, not the board, not the config. The caller
decides what to do with the verdicts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # avoid a runtime import cycle: registry imports this module
    from .registry import BaseRegistry

__all__ = [
    "TIER_GEOMETRY",
    "TIER_REGISTRY",
    "PlannedCopperMatch",
    "accept_planned_match",
    "match_planned_copper",
]

# How a planned command found its live item. TIER_REGISTRY — by the key's stored
# uuid; TIER_GEOMETRY — by exact shape/net/params, the adoption tier.
TIER_REGISTRY = "registry"
TIER_GEOMETRY = "geometry"


@dataclass
class PlannedCopperMatch:
    """One planned command and the live board item it matched (or none).

    ``command`` — the ViaCommand/TrackCommand the redraw planner produced for
        this record (its ``registry_key`` is what the tiers key on).
    ``live`` — the matching board item, or None when the command is recorded but
        not on the board.
    ``tier`` — ``TIER_REGISTRY`` / ``TIER_GEOMETRY`` / None (no match).
    """

    command: Any
    live: Any = None
    tier: Optional[str] = None

    @property
    def found(self) -> bool:
        return self.live is not None


def match_planned_copper(reg: "BaseRegistry", planned_cmds: list,
                         live_items=None) -> list[PlannedCopperMatch]:
    """READ-ONLY: match every planned command to a live board item, two tiers.

    ``reg`` — a PlacementRegistry (vias) or TrackRegistry (tracks); its
    ``entries`` (key -> uuid), ``_live_matches`` (planned-vs-live predicate) and
    ``_get_live_items`` (adapter.get_vias/get_tracks) are the only members used,
    so this works for either kind without a branch. Nothing here writes.

    ``live_items`` — optional pre-fetched board items (a caller that already read
    them once passes them so the two paths cannot read at different times);
    when None the registry fetches them (``_get_live_items``).

    Order is significant: the live list is scanned in board order and the FIRST
    not-owned match wins — the same "first matching item" rule
    ``adopt_matching_unowned`` has always used, so adoption is byte-for-byte
    unchanged.
    """
    planned = list(planned_cmds or ())
    if not planned:
        return []
    if live_items is None:
        live_items = list(reg._get_live_items() or ())
    else:
        live_items = list(live_items)
    live_by_uuid = {getattr(item, "uuid", None): item for item in live_items}
    # A live item claimed by ANY registry entry (its own record or another) is
    # never taken by geometry; `taken` grows as this call matches commands.
    owned = {e.uuid for e in reg.entries.values()}
    taken: set = set()

    out: list[PlannedCopperMatch] = []
    for cmd in planned:
        key = getattr(cmd, "registry_key", None)
        live = None
        tier = None

        # Tier 1 — the registry knows the uuid of the copper THIS record placed.
        entry = reg.entries.get(key) if key else None
        if entry is not None:
            candidate = live_by_uuid.get(entry.uuid)
            if candidate is not None and getattr(candidate, "uuid", None) not in taken:
                live, tier = candidate, TIER_REGISTRY

        # Tier 2 — geometry, for copper no registry entry has claimed.
        if live is None:
            blocked = owned | taken
            for candidate in live_items:
                if getattr(candidate, "uuid", None) in blocked:
                    continue
                if reg._live_matches(candidate, cmd):
                    live, tier = candidate, TIER_GEOMETRY
                    break

        if live is not None:
            taken.add(getattr(live, "uuid", None))
        out.append(PlannedCopperMatch(command=cmd, live=live, tier=tier))
    return out


def accept_planned_match(reg: "BaseRegistry", match: PlannedCopperMatch) -> bool:
    """READ-ONLY verdict: may this match be USED as the record's pair?

    Tier 2 (geometry) — always: the match WAS found by that very predicate.
    Tier 1 (the registry's stored uuid) — only when the live item really lies
    where the command PLANS the copper (``reg._live_matches`` — the registry's
    own predicate and its ``POSITION_TOLERANCE_MM`` tolerance, never a second
    one). The key's ``index`` is the record's number in the cell's list AT THE
    LAST REDRAW: editing that list ("Refresh geometry from selection"
    strictly, "Add", "Subtract") shifts the numbers and nobody renumbers the
    registry, so ``…|k`` can keep the uuid of ANOTHER record's copper — and
    when a record was deleted its copper is usually still on the board, so
    tier 1 would hand it to the record that now sits at index k (finding of
    the f3e116d0 acceptance; plan_2026_10_07_registry_pair_frame_check,
    rule 1).

    No cell frame is needed here: a tree that was NOT refused is redrawn, so
    the command's OWN place is exact even when the live cluster is not a rigid
    copy of the cell. A rejected pair is simply "not checked" — the caller
    counts it and never subtracts it."""
    if match.live is None:
        return False
    if match.tier != TIER_REGISTRY:
        return True
    return bool(reg._live_matches(match.live, match.command))
