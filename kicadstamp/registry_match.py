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

from .constants import POSITION_TOLERANCE_MM
from .utils.layers import layer_to_str
from .utils.units import MM

if TYPE_CHECKING:  # avoid a runtime import cycle: registry imports this module
    from .registry import BaseRegistry

__all__ = [
    "PAIR_AGREES",
    "PAIR_AWAY",
    "PAIR_EDITED",
    "PAIR_SHIFTED",
    "TIER_GEOMETRY",
    "TIER_REGISTRY",
    "PlannedCopperMatch",
    "accept_planned_match",
    "at_other_command_place",
    "match_planned_copper",
    "pair_is_shifted",
    "pair_verdict",
    "record_accepts_net",
    "same_identity",
    "same_net",
    "same_place",
    "shifted_entries",
]

#: The width/drill/diameter epsilon `track_matches` and `via_matches` have always
#: used — the numbers are compared exactly, this only absorbs float noise.
_EPS = 1e-6

# How a planned command found its live item. TIER_REGISTRY — by the key's stored
# uuid; TIER_GEOMETRY — by exact shape/net/params, the adoption tier.
TIER_REGISTRY = "registry"
TIER_GEOMETRY = "geometry"

# ── what a matched pair IS (rule 2 of plan_2026_10_26_select_cell_shifted_registry_pair,
#    as Denis revised it on 26.10: «место решает первым») ───────────────────
#
#: The live item lies where the command plans it and every field agrees.
PAIR_AGREES = "agrees"
#: The live item lies where the command plans it, but its width/layer differs —
#: the copper was EDITED ON THE BOARD by hand. It is still this record's copper:
#: the key is ownership, and a redraw will move it to the planned width.
PAIR_EDITED = "edited"
#: The live item is NOT where the command plans it AND either carries another net
#: (or is another kind) OR stands EXACTLY where ANOTHER record of the same instance
#: puts its copper (which accepts the item's net) — Ч1 of
#: plan_2026_10_27_registry_key_at_another_record_place, Denis 27.10: «the proof is
#: the PLACE, not the net». The registry NUMBER IS SHIFTED — the key keeps the uuid
#: of another record's copper. Not ownership, and not a claim on that item either.
PAIR_SHIFTED = "shifted"
#: Not at the planned place, on the same net, and on NOBODY else's place: today's
#: behaviour — the pair is kept and counted "away from the planned place". This is
#: the operator having moved that copper by hand.
PAIR_AWAY = "away"


def _is_via(planned) -> bool:
    """Is the PLANNED side a via? A via is a POINT, a track is a SEGMENT — asked
    of the planned command/record ONLY, never of the live item.

    Two measured reasons: the domain `Track` carries a `drill_mm` field too, so
    `hasattr(..., "drill_mm")` calls every live track a via; and a live item in
    the tests is a MagicMock, which answers `hasattr` for ANY name — the first
    cut of this predicate silently broke the whole reconcile path that way."""
    return not (hasattr(planned, "start") or hasattr(planned, "start_along_mm"))


def same_net(live_item, planned, net: Optional[str] = None) -> bool:
    """True ⇔ the live item carries the net the planned side must have — or
    NEITHER side names a net, in which case this question cannot accuse anybody.

    KIND is deliberately NOT compared: every caller asks a registry of ONE kind
    (a via file or a track file), so a via can never be paired with a track here
    and the branch would be dead code claiming coverage it has none of (rule 38).
    The rule's wording — «другая сеть или вид» — keeps its meaning: this function
    is the part of it that can actually fire.

    `net` — the net name the planned side must carry: a COMMAND has its own
    (`net_name`), while a stored cell record may hold a literal net or a
    (role, pad) reference that only the caller can resolve (`net_resolution`).
    Given explicitly, or the command's own name is used.

    A SIDE WITHOUT A NET (None, or the empty string) is never an accusation: the
    pair is then judged by its PLACE alone — away means «away», not «shifted».
    This is not a formality (Д-1 of the acceptance): strict equality here declared
    every pair of a net-less command — and every pair whose live item carries no
    net — a SHIFTED NUMBER, which would have taken copper away from its own record,
    the very defect Ч1 exists to fix. It also keeps the minimal registry doubles,
    and any caller that predates the field, working.

    THIS QUESTION IS NOT `same_identity`'s: "is this the same copper?" stays
    STRICT (`_same_net_strict`), because the reconcile path deletes by uuid and the
    positional pre-check must not pair a net-less item with a named command. Only
    the VERDICT's "does the pair contradict?" is lenient."""
    expected = net if net is not None else getattr(planned, "net_name", None)
    live_net = getattr(live_item, "net_name", None)
    if not expected or not live_net:
        return True
    return live_net == expected


def _same_net_strict(live_item, planned, net: Optional[str] = None) -> bool:
    """Strict net equality — the «same copper?» half. A missing net on either side
    is a mismatch here (`via_matches`/`track_matches` have always answered so)."""
    expected = net if net is not None else getattr(planned, "net_name", None)
    return getattr(live_item, "net_name", None) == expected


def record_accepts_net(live_item, *, declared: bool, expected: Optional[str]) -> bool:
    """True ⇔ the record on the OTHER side of a suspected shift ACCEPTS this item's
    net — the gate of the «another record's place» rule (plan §3, Denis 27.10).

    Three outcomes, and only one of them releases a key:

    * ``declared`` False — the neighbour names no net source at all, or names one
      this door cannot resolve (``expected`` None): we cannot say the item is that
      record's copper, so the pair is NOT released by this path;
    * declared and resolved — the item's net must be exactly that net;
    * a resolved net that differs — the pre-existing «another net» path judges the
      pair, this one stays out of the way.

    NOT `same_net`: that one is the VERDICT's lenient question («does the pair
    contradict?»), where silence accuses nobody. Here silence is not permission —
    it is the absence of proof, and releasing a key on no proof would take copper
    away from its own record (the Д-1 lesson, opposite direction)."""
    if not declared or expected is None:
        return False
    return getattr(live_item, "net_name", None) == expected


def same_identity(live_item, planned, net: Optional[str] = None) -> bool:
    """True ⇔ the live item and the planned command/record agree by KIND, NET,
    LAYER and WIDTH/DRILL-DIAMETER. THE PLACE IS NOT ASKED HERE — that is the
    whole point of the pair of predicates: a pair may sit at the planned place and
    still differ in a field (hand-edited copper), and it may differ in a field and
    not be this record's copper at all (a shifted number).

    `planned` is a ViaCommand/TrackCommand OR a stored cell record; a field the
    record does not carry (`layer`/`width_mm` omitted — the cell's own default is
    inherited) is NOT compared: an absent field is never an accusation.

    The NET is asked STRICTLY here (unlike `same_net`, which is the verdict's own
    lenient question) — see `same_net`'s docstring for why the two must differ."""
    if not _same_net_strict(live_item, planned, net):
        return False
    if _is_via(planned):
        return (abs(live_item.drill_mm - planned.drill_mm) < _EPS
                and abs(live_item.diameter_mm - planned.diameter_mm) < _EPS)
    width = getattr(planned, "width_mm", None)
    if width is not None and abs(live_item.width_mm - width) >= _EPS:
        return False
    layer = getattr(planned, "layer", None)
    if layer is not None and layer_to_str(live_item.layer) != layer_to_str(layer):
        return False
    return True


def same_place(live_item, cmd) -> bool:
    """True ⇔ the live item sits where the COMMAND plans it, within the registry's
    own `POSITION_TOLERANCE_MM` (the tolerance `_live_matches` uses — never a
    second one). A track is unoriented: either end may answer for the other."""
    def close(a, b) -> bool:
        return (abs(a[0] - b[0]) / MM <= POSITION_TOLERANCE_MM
                and abs(a[1] - b[1]) / MM <= POSITION_TOLERANCE_MM)

    if _is_via(cmd):
        if not hasattr(live_item, "position"):
            return False
        return close((live_item.position.x, live_item.position.y),
                     (cmd.position.x, cmd.position.y))
    if not hasattr(live_item, "start"):
        return False
    live_start = (live_item.start.x, live_item.start.y)
    live_end = (live_item.end.x, live_item.end.y)
    cmd_start = (cmd.start.x, cmd.start.y)
    cmd_end = (cmd.end.x, cmd.end.y)
    return ((close(live_start, cmd_start) and close(live_end, cmd_end))
            or (close(live_start, cmd_end) and close(live_end, cmd_start)))


def pair_is_shifted(*, at_place: bool, other_record_place: bool,
                    net_agrees: bool) -> bool:
    """THE rule, in the form EVERY door can ask — Ч1 of
    plan_2026_10_27_registry_key_at_another_record_place (Denis, 27.10).

    A pair is a shifted number ⇔ it is NOT at its own place AND

      * it stands EXACTLY where ANOTHER record of the same instance puts its
        copper, that record ACCEPTING the item's net (`other_record_place`) — the
        proof is the PLACE, so the net of the pair itself does not matter here
        (`via|1` on v103: GND on GND, 0.0000 mm from `via|5`'s place against
        18.19 mm from its own, and invisible to the net rule for exactly that
        reason); or
      * its net is not its own record's (`net_agrees` False) — the pre-existing
        rule of plan_2026_10_26_select_cell_shifted_registry_pair.

    Away, on its own net, on NOBODY's place — «away»: the operator moved that
    copper by hand. PLACE DECIDES FIRST: a pair at its own place is ownership
    whatever else is true of it.

    The three facts are asked by the CALLER because their vocabulary belongs to
    the door: a command door compares with another planned COMMAND (`same_place`),
    the at-current-place and registry-only doors with another RECORD in the cell
    frame (`pair_agrees_with_record`). This function is the accusation itself, and
    it is the only one."""
    if at_place:
        return False
    return bool(other_record_place) or not net_agrees


def at_other_command_place(live_item, cmd, siblings) -> list:
    """The OTHER planned commands of the same instance that put their copper
    EXACTLY where this live item stands — the COMMAND vocabulary of «another
    record's place».

    A witness must (a) name a different key, (b) carry the same `anchor|cell` head
    of the registry key — without that head an entry cannot even be said to belong
    to the same instance, and such a key never witnesses, (c) be of the same KIND
    (asked of the PLANNED sides only: one registry per kind), (d) put its copper
    within `POSITION_TOLERANCE_MM` of the item (`same_place` — the predicate the
    registry matches with), and (e) ACCEPT the item's net (`record_accepts_net`, a
    command always names one).

    BLINDNESS, measured and deliberate (Ч1 of plan_2026_10_27_…, §8.4/§8.9): when
    the instance stands away from where the tree places it, the sibling commands
    are at the TREE place while the copper is at the CURRENT one, so no witness is
    found here at all — `ldo_adj_n2v5` on v103 stands 3.462 mm away and this door
    sees none of its three pairs. That case is served by the FRAME vocabulary
    (`absent_copper_prune.at_another_record_place`) in the at-current-place pass,
    which every real run and «Select cell» already go through. Building a second
    cell frame here was refused on purpose: one measurement, one place."""
    own_key = getattr(cmd, "registry_key", None)
    own_instance = _instance_of(own_key)
    if own_instance is None:
        return []
    out: list = []
    for other in siblings or ():
        if other is cmd:
            continue
        other_key = getattr(other, "registry_key", None)
        if other_key is None or other_key == own_key:
            continue
        if _instance_of(other_key) != own_instance:
            continue
        if _is_via(other) != _is_via(cmd):
            continue
        if not same_place(live_item, other):
            continue
        if not record_accepts_net(live_item, declared=True,
                                  expected=getattr(other, "net_name", None)):
            continue
        out.append(other)
    return out


def _instance_of(key) -> Optional[str]:
    """``anchor|cell`` of a registry key — the INSTANCE identity (a key is
    `anchor|cell|role|index`), or None for a key that carries no such head."""
    parts = str(key or "").split("|")
    return "|".join(parts[:2]) if len(parts) == 4 else None


def pair_verdict(match: PlannedCopperMatch, *, other_record_place: bool = False,
                 siblings=None) -> str:
    """ONE verdict per matched pair — the rule, in the one place it lives.

    PLACE FIRST (Denis, 26.10): at the planned place the pair is ownership even
    when a field differs (that is the operator editing the board — 34 of 45 pairs
    on v103). Away from the place it is a SHIFTED NUMBER when its net (or kind)
    differs, or when it stands at ANOTHER record's place (`pair_is_shifted`,
    Denis 27.10); away with the same net on nobody's place stays today's "kept and
    counted".

    ``other_record_place`` — the caller already knows the answer in ITS vocabulary
    (a cell frame, a refused tree's registry). ``siblings`` — the planned commands
    of the same call, when the caller has them: the command vocabulary is then
    asked here, so no caller has to repeat the loop.

    `match.live is None` is not this rule's business — it answers PAIR_AGREES, and
    every caller checks `.found` first anyway."""
    if match.live is None:
        return PAIR_AGREES
    if same_place(match.live, match.command):
        return (PAIR_AGREES if same_identity(match.live, match.command)
                else PAIR_EDITED)
    witness = other_record_place or bool(at_other_command_place(
        match.live, match.command, siblings or ()))
    if pair_is_shifted(at_place=False, other_record_place=witness,
                       net_agrees=same_net(match.live, match.command)):
        return PAIR_SHIFTED
    return PAIR_AWAY


def shifted_entries(reg: "BaseRegistry", planned_cmds: list,
                    live_items=None) -> dict:
    """``{key: live_uuid}`` — the keys of `planned_cmds` whose stored uuid points
    at ANOTHER record's copper: away from the planned place AND either on another
    net or exactly at a sibling command's place (`at_other_command_place`).

    THE one place that decides it: `match_planned_copper` frees these keys and
    items with this very result, and a door that wants to NAME the count (the
    «Select cell» line) asks the same function instead of repeating the rule.

    Only a pair that is AWAY is asked for witnesses — a pair at its own place can
    never be shifted (place decides first), and scanning every command against
    every other one would cost O(n²) for nothing (measured: 65 of 2110 pairs on
    v103 are away, and those are the only ones scanned)."""
    planned = list(planned_cmds or ())
    if not planned:
        return {}
    live = (list(reg._get_live_items() or ()) if live_items is None
            else list(live_items))
    by_uuid = {getattr(item, "uuid", None): item for item in live}
    out: dict = {}
    for cmd in planned:
        key = getattr(cmd, "registry_key", None)
        entry = reg.entries.get(key) if key else None
        if entry is None:
            continue
        item = by_uuid.get(getattr(entry, "uuid", None))
        if item is None:
            continue
        if same_place(item, cmd):
            continue                     # place decides first: never a shift
        if pair_verdict(PlannedCopperMatch(command=cmd, live=item,
                                           tier=TIER_REGISTRY),
                        siblings=planned) == PAIR_SHIFTED:
            out[key] = entry.uuid
    return out


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
                         live_items=None, *, orphan_uuids=frozenset(),
                         ) -> list[PlannedCopperMatch]:
    """READ-ONLY: match every planned command to a live board item, two tiers.

    ``reg`` — a PlacementRegistry (vias) or TrackRegistry (tracks); its
    ``entries`` (key -> uuid), ``_live_matches`` (planned-vs-live predicate) and
    ``_get_live_items`` (adapter.get_vias/get_tracks) are the only members used,
    so this works for either kind without a branch. Nothing here writes.

    ``live_items`` — optional pre-fetched board items (a caller that already read
    them once passes them so the two paths cannot read at different times);
    when None the registry fetches them (``_get_live_items``).

    ``orphan_uuids`` — the uuids held ONLY by registry keys whose record is no
    longer in the config (`anchor_ids.orphan_uuids`). Such an entry owns nothing
    (Ч1 of plan_2026_10_27_orphan_anchor_keys_own_nothing): it neither blocks its
    item from the record that really owns it nor claims it. An empty set is the
    old behaviour, so every caller that does not know the anchors is unchanged.

    Order is significant: the live list is scanned in board order and the FIRST
    not-owned match wins — the same "first matching item" rule
    ``adopt_matching_unowned`` has always used, so adoption is byte-for-byte
    unchanged.

    A SHIFTED NUMBER of THIS call is the one exception (Ч1 of
    plan_2026_10_26_select_cell_shifted_registry_pair, widened by Ч1 of
    plan_2026_10_27_registry_key_at_another_record_place): when the entry under a
    key of this very call points at copper of another net OR at copper standing
    exactly where a SIBLING command of this call puts its own, that key neither
    occupies the key (tier 1 skips it, so geometry may still find the record's own
    copper) nor claims the item (tier 2 may take it for the record it does belong
    to). A key whose command is NOT in this call keeps its ownership — the rule
    never weakens an entry it cannot judge.

    WHAT THIS DOOR CANNOT SEE (measured on v103, §8.4/§8.9 of that plan, and NOT
    to be «fixed» here): the sibling vocabulary is a PLANNED COMMAND, so a witness
    is found only while the instance stands where the tree places it. When it
    stands away (`ldo_adj_n2v5`: 3.462 mm), the sibling commands are at the TREE
    place and the copper at the CURRENT one, and no witness is found at all — this
    door then reports those pairs as «away», keeping their keys. The case is
    covered by the FRAME vocabulary
    (`absent_copper_prune.at_another_record_place`) in the pass every real run and
    «Select cell» go through; a second cell frame here was refused on purpose.
    `adopt_matching_unowned` is not expected to repair a rotation either: it is
    handed only the commands whose key the registry does NOT know (`registry.py`),
    and on a rotation every key is known — its call has no candidates at all, as the
    Ч1 cells pin.
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
    # never taken by geometry; `taken` grows as this call matches commands. A
    # uuid held only by an orphan entry (`orphan_uuids`) is not ownership — the
    # key outlived its record, the copper is nobody's.
    owned = {e.uuid for e in reg.entries.values()} - set(orphan_uuids or ())
    taken: set = set()

    # The shifted numbers of THIS call — judged only where a command of this call
    # names the key, so an entry nobody asked about is never released.
    released = set(shifted_entries(reg, planned, live_items=live_items).values())
    free = owned - released

    out: list[PlannedCopperMatch] = []
    for cmd in planned:
        key = getattr(cmd, "registry_key", None)
        live = None
        tier = None

        # Tier 1 — the registry knows the uuid of the copper THIS record placed,
        # unless that pair is a shifted number (then the key is simply free).
        entry = reg.entries.get(key) if key else None
        if entry is not None and entry.uuid not in released:
            candidate = live_by_uuid.get(entry.uuid)
            if candidate is not None and getattr(candidate, "uuid", None) not in taken:
                live, tier = candidate, TIER_REGISTRY

        # Tier 2 — geometry, for copper no registry entry has claimed.
        if live is None:
            blocked = free | taken
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
