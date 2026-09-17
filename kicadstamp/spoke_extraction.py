# kicadstamp/spoke_extraction.py
"""The decision layer behind Tools -> "Extract spoke..." — stage 5 of the spoke
work (plan_2026_09_17_spoke_s5_extract_spoke.md, design
design_2026_09_17_spoke_cell_editing.md §0/§1/§2.5/§3/§6).

Why this module exists: a spoke is ONE cell placed many times inside ONE
cluster (measured on the live 3CH-AWG-TIA board: FPGA_PWR_BANK holds
C_FPGA_BULK x25 + C_FPGA_BYPASS x25, one pair per power pin), so the
(cluster, sheet) pair — the identity the ordinary flows use — cannot say WHICH
pair of components is meant. The SELECTION can, and this layer turns that
selection into the two records the config needs: a cell (when none exists yet)
and one spoke of a chain, with the shift and the rotation read off the board.

Pure by construction: records, mm floats and already-built component pools in,
verdicts out — no Qt, no adapter, no IPC. Reading the board belongs to the
caller on the WORKER thread (the project's door rules, `techdocs/me/door.md`),
which is what makes the whole decision layer testable without KiCad (see
tests/test_spoke_extraction.py).

The step ORDER is deliberately the stage-5 diagnostic's own
(kicadstamp/diagnostics/probe_extract_spoke_from_selection.py, Ф1 of the plan):
that probe is the "before / after" ruler Denis re-runs on the live board, so the
dialog and the probe must agree by construction, not by coincidence.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from .exceptions import ValidationError
from .i18n import _

logger = logging.getLogger(__name__)

# A net touching more footprints than this is a plane (GND-like), never a spoke
# rail — the SAME threshold the stage-5 diagnostic uses (design §2.5), kept as
# ONE constant here so the probe and the dialog cannot drift apart.
PLANE_NET_MEMBERS = 40

# What the component pool can do to the spoke being extracted (plan Ф8 / Р3).
# The dialog's OK button follows from `PoolOutcome.kind` instead of re-reading
# the same data and reaching its own conclusion.
OUTCOME_MATCH = "match"              # Ф8.1 — the pool gives the selected pair
OUTCOME_OTHER_PAIR = "other-pair"    # Ф8.2 — another pair; needs the swap checkbox
OUTCOME_EXHAUSTED = "exhausted"      # Ф8.3 — nothing left to fill the spoke with


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """One field of a spoke / cell / slot read from EITHER shape this module
    takes: a loaded config object (ManualSpoke / Cell / TemplateComponentSlot)
    or the raw dict the config writer round-trips (`read_data`,
    `upsert_list_entry`). Both are legitimate at either end of the flow — the
    dialog previews on objects and writes dicts — so the pure layer reads both
    instead of forcing a conversion on every caller."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _refs_text(refs: Iterable[Any]) -> str:
    """The refdes list as one line ("C41, C42") — sorted, so two runs over the
    same board produce the same sentence."""
    return ", ".join(sorted(str(r) for r in refs))


# ── step 1: is this selection a spoke pair at all? ──────────────────────────

@dataclass(frozen=True)
class SpokeSelection:
    """One USABLE spoke selection: the refs, the single Cluster tag and the
    role -> refdes map (the identification stage 1 remembers and every later
    live read uses)."""
    refs: tuple
    cluster: str
    role_to_ref: dict


def check_spoke_selection(selected) -> tuple[Optional[SpokeSelection], list[str]]:
    """Step 1 of the dialog (plan Р2.1) on records with .ref/.role/.cluster
    (explore.Selected's shape).

    Returns (selection, problems): `problems` is a list of ready-to-show
    sentences, and the selection is None whenever it is not empty. The refusal
    ORDER is the "Fill from selection" one (gui/cell_identification.py) and not a
    free choice — the two flows must classify the same selection identically:
    nothing selected -> a component without a Role -> a Role twice -> no
    Cluster / several Clusters.
    """
    items = list(selected or ())
    if not items:
        return None, [_("nothing is selected on the board — select the pair of "
                        "components of ONE spoke (with its copper, if you want "
                        "the copper in the cell)")]
    refs = tuple(sorted(str(_field(s, "ref", "?")) for s in items))
    problems: list[str] = []

    untagged = [str(_field(s, "ref", "?")) for s in items if not _field(s, "role")]
    if untagged:
        problems.append(_("no Role on {refs} — every component of the pair must "
                          "be tagged with a Role").format(
                              refs=", ".join(untagged)))

    counts: dict[str, int] = {}
    for item in items:
        role = _field(item, "role")
        if role:
            counts[str(role)] = counts.get(str(role), 0) + 1
    twice = sorted(role for role, n in counts.items() if n > 1)
    if twice:
        problems.append(_("the same Role is selected twice ({roles}) — select "
                          "exactly one component per Role").format(
                              roles=", ".join(twice)))

    clusters = {str(c) for c in (_field(s, "cluster") for s in items) if c}
    if len(clusters) > 1:
        problems.append(_("the selection spans several clusters ({clusters}) — "
                          "a spoke belongs to ONE cluster").format(
                              clusters=", ".join(sorted(clusters))))
    elif not clusters:
        problems.append(_("no Cluster on {refs} — the spoke's cluster is the "
                          "one the pair stands in").format(refs=", ".join(refs)))

    if problems:
        return None, problems
    role_to_ref: dict[str, str] = {}
    for item in items:
        role_to_ref[str(_field(item, "role"))] = str(_field(item, "ref"))
    return SpokeSelection(refs=refs, cluster=next(iter(clusters)),
                          role_to_ref=role_to_ref), []


def spoke_criterion(role_counts: Mapping[str, int]) -> dict[str, int]:
    """Step 2 (plan Р2.2): the SPOKE evidence — every role of the selection that
    occurs more than once inside its cluster, as {role: count}.

    Empty means the roles are unique: the (cluster, sheet) pair already pins the
    instance down, this is an ordinary cluster, and "Extract spoke" must say so
    and disable OK instead of writing a spoke nobody asked for (design Р3
    "молча не переключается"). A repeated role is ALSO what a role-mapping
    mistake looks like (R13/R67 on v103), which is why the count is reported
    rather than swallowed."""
    return {str(role): int(n) for role, n in (role_counts or {}).items()
            if int(n) > 1}


def candidate_cells(cfg, role_set: Iterable[str]) -> list[str]:
    """Step 3 (plan Р2.3): the cells whose own role set is EXACTLY the
    selection's — the "reuse an existing cell" candidates of the dialog's cell
    combo. Sorted, so the combo has a stable order across refreshes."""
    wanted = {str(r) for r in role_set}
    out = []
    for name, cell in (getattr(cfg, "cells", None) or {}).items():
        roles = {str(_field(slot, "role"))
                 for slot in (getattr(cell, "components", None) or ())}
        if roles == wanted:
            out.append(str(name))
    return sorted(out)


# ── step 4: which net, chain and pad the pair belongs to ────────────────────

def split_plane_nets(nets_with_counts: Iterable[tuple[str, int]]
                     ) -> tuple[tuple[str, ...], tuple[tuple[str, int], ...]]:
    """Step 4a (plan Р2.4): the nets of the selection's pads split by the plane
    rule — (kept, dropped). A net with more members than PLANE_NET_MEMBERS is a
    plane (GND-like: on the live board GND touches 236 footprints and is on
    nearly every pad of the pair), never a spoke rail. `dropped` keeps the
    counts so the dialog can SAY which net was ignored and why instead of
    quietly offering nothing."""
    kept: list[str] = []
    dropped: list[tuple[str, int]] = []
    for net, count in nets_with_counts:
        if int(count) > PLANE_NET_MEMBERS:
            dropped.append((str(net), int(count)))
        else:
            kept.append(str(net))
    return tuple(kept), tuple(dropped)


@dataclass(frozen=True)
class PadPoint:
    """One pad candidate on the board: its number, its absolute centre (mm) and
    the footprint carrying it. `role` is that footprint's Role field — for a
    FOREIGN pad (no chain on the net yet) it is what a new chain's anchor_role
    is taken from."""
    pad: str
    x_mm: float
    y_mm: float
    ref: str = ""
    role: Optional[str] = None


def pads_by_distance(pads: Iterable[PadPoint], centre_mm: tuple[float, float]
                     ) -> list[PadPoint]:
    """`pads` sorted by their distance to `centre_mm` (the middle of the
    selection) — the order the anchor-pad combo shows. Stable, so the same board
    twice gives the same list; a tie keeps the caller's order."""
    cx, cy = float(centre_mm[0]), float(centre_mm[1])
    return sorted(pads, key=lambda p: (p.x_mm - cx) ** 2 + (p.y_mm - cy) ** 2)


def nearest_pad(pads: Iterable[PadPoint], centre_mm: tuple[float, float]
                ) -> Optional[PadPoint]:
    """The NEAREST pad of the anchor on this net (plan Р2.4/С4) — never simply
    the first pad the footprint happens to list: the anchor has many pads on the
    rail, and the first one belongs to a different pin."""
    ordered = pads_by_distance(pads, centre_mm)
    return ordered[0] if ordered else None


# ── step 6: what the spoke stores (Ф2) ──────────────────────────────────────

@dataclass(frozen=True)
class SpokeOffset:
    """What a `chains:` spoke entry stores for one identified pair: the raw
    absolute shift (mm) from the anchor pad centre to the spoke origin, and the
    frame's own rotation."""
    shift_x_mm: float
    shift_y_mm: float
    rotation_deg: float


def spoke_offset(frame_origin_mm: tuple[float, float],
                 pad_position_mm: tuple[float, float],
                 rotation_deg: float) -> SpokeOffset:
    """The shift/rotation a NEW spoke stores (Ф2): shift = the cell frame's
    origin MINUS the anchor pad, both in ABSOLUTE board mm, plus the frame's own
    rotation. Measured on the live board, this recipe reproduces the stored
    spoke start to 0.0000 mm / 0.000° on all 24 spokes (design §2.1).

    The anchor's rotation is deliberately NOT applied: a spoke has no parent
    frame (ManualSpoke's contract, kicadstamp/config/models.py), the shift is
    the raw vector from the pad centre to the spoke origin, and rotating it here
    would move the pair by the anchor's angle (С5/М5)."""
    return SpokeOffset(
        shift_x_mm=float(frame_origin_mm[0]) - float(pad_position_mm[0]),
        shift_y_mm=float(frame_origin_mm[1]) - float(pad_position_mm[1]),
        rotation_deg=float(rotation_deg))


# ── step 5: what the pool gives the spoke (Ф6, Ф8) ──────────────────────────

def chain_assignment(spokes, cells, pools_by_cluster, anchor_pad_numbers
                     ) -> tuple[dict[str, dict], Optional[str]]:
    """Step 5a: {pad: role_to_ref} for every CONSUMING spoke of one chain, in
    chain order — the exact consumption
    ManualPositionCalculator.compute_raw_positions performs (plan Ф6): a retired
    spoke, a spoke whose cell is missing from `cells`, and a spoke whose pad the
    anchor does not carry all consume NOTHING (they `continue` before the pop),
    while every other spoke pops one component per role slot in the order the
    spokes are WRITTEN.

    That order is the whole reason Ф8 exists: a spoke appended at the end gets
    whatever is left, not automatically the pair the user just selected.

    `pools_by_cluster` — {cluster: pool}, each pool implementing ComponentPool's
    contract (pop(role, pad) + remaining_count(role)): ComponentPool itself in
    production, a plain stub in tests. Returns (assignment, problem); `problem`
    is the first refusal (an exhausted pool, a role the pool does not know, a
    cluster with no pool at all) and None when every consuming spoke was filled.
    """
    assignment: dict[str, dict] = {}
    for spoke in spokes or ():
        if _field(spoke, "retired"):
            continue
        pad = str(_field(spoke, "pad"))
        cell = (cells or {}).get(_field(spoke, "cell"))
        if cell is None or pad not in anchor_pad_numbers:
            continue
        cluster = _field(spoke, "cluster")
        pool = pools_by_cluster.get(cluster)
        if pool is None:
            return assignment, _("no component pool for cluster {cluster}").format(
                cluster=cluster)
        try:
            assignment[pad] = {
                _field(slot, "role"): pool.pop(_field(slot, "role"), pad)
                for slot in (getattr(cell, "components", None) or ())}
        except ValidationError as exc:                        # pool dry / unknown role
            return assignment, str(exc).strip()
    return assignment, None


def pool_capacity(pool, roles: Iterable[str]) -> int:
    """How many complete sets of `roles` the pool can still give — the smallest
    remaining count across the roles (a pool with 4 bulk and 3 bypass caps fills
    three spokes, not four). Used for the "N pairs, M spokes" numbers in the
    exhausted message, so the user sees the SHORTAGE, not just its consequence."""
    counts = [pool.remaining_count(role) for role in roles]
    return min(counts) if counts else 0


def find_pair_owner(current_assignments: Iterable[tuple[str, Mapping[str, dict]]],
                    selected_refs: Iterable[str]) -> Optional[tuple[str, str]]:
    """Step 5b (Ф8.2's "why"): the (chain_name, pad) whose CURRENT assignment is
    exactly the selected pair, or None when no chain on these nets claims it.

    `current_assignments` — an ordered iterable of (chain_name, assignment) as
    chain_assignment() returns them, over every chain on this chain's nets: a
    pair can be claimed by a chain OTHER than the one being written, and the user
    must be told which one before parts start moving."""
    wanted = frozenset(str(r) for r in selected_refs)
    for chain_name, assignment in current_assignments:
        for pad, role_to_ref in (assignment or {}).items():
            if frozenset(str(v) for v in role_to_ref.values()) == wanted:
                return (str(chain_name), str(pad))
    return None


@dataclass(frozen=True)
class PoolOutcome:
    """What the user is told about the pair this spoke will actually get, and
    what OK may do about it (plan Ф8/Р3).

    kind — OUTCOME_MATCH / OUTCOME_OTHER_PAIR / OUTCOME_EXHAUSTED.
    ok — OK may be pressed with nothing else ticked.
    needs_swap_confirm — OK additionally requires the explicit "Components may
        swap" checkbox (never a silent swap: the parts are already routed).
    assigned — role -> refdes the spoke will place ({} when the pool is dry).
    owner_chain/owner_pad — the spoke that claims the selected pair today.
    message — the sentence the dialog shows verbatim.
    """
    kind: str
    ok: bool
    needs_swap_confirm: bool
    assigned: dict
    selected_refs: tuple
    owner_chain: Optional[str] = None
    owner_pad: Optional[str] = None
    message: str = ""


def pool_outcome(assignment: Mapping[str, dict], target_pad, selected_refs, *,
                 chain_name: str = "", owner: Optional[tuple[str, str]] = None,
                 cluster: str = "", total_pairs: int = 0,
                 consumers: int = 0) -> PoolOutcome:
    """Step 5c: the verdict on the pair this spoke will get (Ф8/Р3).

    `assignment` — chain_assignment() of the chain WITH the new (or replacing)
    spoke already in place; `target_pad` — the pad this spoke sits on;
    `selected_refs` — the pair the user selected; `owner` — find_pair_owner()'s
    answer for the CURRENT configuration; `total_pairs`/`consumers` — the free
    capacity and the consuming spokes, for the exhausted sentence.

    Three outcomes, and all three are said out loud:
      * match        — the pool gives exactly the selected pair: OK is free;
      * other-pair   — it gives ANOTHER pair, and the selected one is moved off
                       this pad: OK needs the "Components may swap" checkbox, and
                       the message names both pairs and the owning spoke;
      * exhausted    — nothing is left for one more spoke: writing it would put a
                       spoke in the config that the pool can never fill, so OK is
                       refused and no checkbox can make it true."""
    wanted = tuple(sorted(str(r) for r in (selected_refs or ())))
    assigned = dict((assignment or {}).get(str(target_pad)) or {})
    if not assigned:
        return PoolOutcome(
            kind=OUTCOME_EXHAUSTED, ok=False, needs_swap_confirm=False,
            assigned={}, selected_refs=wanted,
            message=_("No free pair left in cluster {cluster} for one more spoke "
                      "({pairs} pairs, {spokes} spokes)").format(
                          cluster=cluster or "?", pairs=total_pairs,
                          spokes=consumers))
    given = tuple(sorted(str(v) for v in assigned.values()))
    if given == wanted:
        return PoolOutcome(
            kind=OUTCOME_MATCH, ok=True, needs_swap_confirm=False,
            assigned=assigned, selected_refs=wanted,
            message=_("On redraw this spoke will get {refs}").format(
                refs=_refs_text(given)))
    if owner is not None:
        owner_chain, owner_pad = str(owner[0]), str(owner[1])
        message = _("On redraw this spoke will get {got}, NOT the selected pair: "
                    "{selected} is already taken by the spoke on pad {pad} of "
                    "chain {chain}. The selected pair will be moved off this "
                    "pad.").format(got=_refs_text(given), selected=_refs_text(wanted),
                                   pad=owner_pad, chain=owner_chain)
    else:
        owner_chain = owner_pad = None
        message = _("On redraw this spoke will get {got}, NOT the selected pair: "
                    "{selected} is not claimed by any chain on this net — the "
                    "selected pair will be moved off this pad.").format(
                        got=_refs_text(given), selected=_refs_text(wanted))
    return PoolOutcome(
        kind=OUTCOME_OTHER_PAIR, ok=False, needs_swap_confirm=True,
        assigned=assigned, selected_refs=wanted, owner_chain=owner_chain,
        owner_pad=owner_pad, message=message)
