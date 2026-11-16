# kicadstamp/forest_reread.py
"""«Reread positions from board» — the arithmetic of the whole forest, and the
DECISION half of it. Qt-free.

The case (16.11.2026): the Grid Origin was moved twice while the board was already
routed, so the profile no longer agrees with the board. Rereading one node at a
time does not scale to a forest of dozens of nodes, and Redraw does not ask — it
just moves everything (and re-creates the cell copper, which tore a hand-routed
board apart).

TWO INTENTIONS, chosen by the user, never guessed by the code:

  * `MODE_ABSORB` — "Origin moved, keep the board": the LARGEST group of equally
    shifted voters gives the tree's new ANCHOR shift, and everything else is read
    as a node afterwards, against the base with that NEW shift;
  * `MODE_KEEP` — "Blocks were moved by hand": anchors are not touched, every node
    keeps its own read.

A tie between two groups with different deltas is NOT resolved by picking one: with
no strict majority the anchor stays put and every node is read by itself (the same
"no majority vote" rule the anchor Read already follows).

WHAT IS PURE HERE: the grouping of the voters' deltas, the anchor choice (with the
tie rule and a per-tree override), the threshold that keeps unmoved nodes out of
the file, the shape of the written value (Cartesian stays Cartesian, Polar stays
Polar — exactly what the node form writes), and the report rows. WHAT IS NOT: the
board reads and the frames; the caller supplies, per node, the offset it read AND
the translation of that node's base (a plain number, `absorb_mm`), because only the
caller walks the layout that owns the bases.

The threshold is the anchor Read's own: `MM_TOLERANCE` / `DEG_TOLERANCE`, the angle
compared through `relative_rotation_deg` (modulo 360).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .i18n import _
from .tree_anchor_read import DEG_TOLERANCE, MM_TOLERANCE
from .tree_position import relative_rotation_deg

#: The dialog's two intentions (the switch at the top of the table).
MODE_ABSORB = "absorb"
MODE_KEEP = "keep"

#: A per-tree override in the table: "keep" leaves that tree's anchor alone.
OVERRIDE_KEEP = "keep"


@dataclass(frozen=True)
class Vote:
    """ONE voter of one tree: where the layout puts it and where the board has it
    are already subtracted into `delta` (mm, board frame). `label` is the display
    name — `mh`, or `fpga/fpga_fpga` for content of an embedded tree."""

    label: str
    delta: tuple[float, float]
    tree: str = ""


@dataclass(frozen=True)
class Group:
    """Voters whose deltas agree within `MM_TOLERANCE` — one rigid move."""

    delta: tuple[float, float]
    labels: tuple[str, ...] = ()

    @property
    def size(self) -> int:
        return len(self.labels)


def _close(a: tuple[float, float], b: tuple[float, float],
           tolerance: float = MM_TOLERANCE) -> bool:
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


def group_votes(votes: Iterable[Vote],
                *, tolerance: float = MM_TOLERANCE) -> list[Group]:
    """Cluster the votes by their delta, BIGGEST GROUP FIRST.

    The first vote of a cluster sets its delta; a later vote joins the FIRST
    cluster it agrees with. Ties keep the order they were read in, so the answer
    is stable across runs of one read (nothing here depends on dict order)."""
    clusters: list[list[Vote]] = []
    for vote in votes:
        for cluster in clusters:
            if _close(cluster[0].delta, vote.delta, tolerance):
                cluster.append(vote)
                break
        else:
            clusters.append([vote])
    groups = [Group(delta=cluster[0].delta,
                    labels=tuple(v.label for v in cluster))
              for cluster in clusters]
    # sorted() is stable: equal sizes keep the read order.
    return sorted(groups, key=lambda g: -g.size)


@dataclass(frozen=True)
class AnchorChoice:
    """`delta` None = the anchor is NOT touched (all nodes read by themselves).
    `reason` is the one line the dialog shows for that tree."""

    delta: Optional[tuple[float, float]] = None
    reason: str = ""


def choose_anchor(groups: list[Group], *, mode: str,
                  override: Optional[str] = None) -> AnchorChoice:
    """The delta the anchor absorbs, or None.

    * `MODE_KEEP`, or the per-tree override "keep" → None, always;
    * `MODE_ABSORB` → the biggest group, but ONLY when it is strictly bigger than
      every group with a DIFFERENT delta. Two equally big groups that disagree mean
      "which block moved by hand is the user's call" — the anchor stays and every
      node is read on its own (the plan's а3, and the anchor Read's own rule);
    * no votes at all → None with the reason said out loud."""
    if override == OVERRIDE_KEEP:
        return AnchorChoice(None, _("anchor kept: chosen for this tree"))
    if mode == MODE_KEEP:
        return AnchorChoice(None, _("anchor kept: blocks were moved by hand"))
    if not groups:
        return AnchorChoice(None, _("anchor kept: no node of this tree votes"))
    biggest = groups[0]
    rivals = [g for g in groups[1:] if not _close(g.delta, biggest.delta)]
    if rivals and rivals[0].size >= biggest.size:
        # How many groups tie for the top size — the number the user is shown. It
        # used to be a literal `{n}` (16.11 acceptance): the sentence went out
        # unformatted, so the dialog said "{n} groups" in every language.
        tied = 1 + len([g for g in rivals if g.size == biggest.size])
        return AnchorChoice(None, _(
            "anchor kept: {n} groups of voters disagree equally — read each node"
        ).format(n=tied))
    return AnchorChoice(biggest.delta, _(
        "anchor follows {n} of {total} voters").format(
            n=biggest.size, total=sum(g.size for g in groups)))


@dataclass(frozen=True)
class NodeRead:
    """What the caller read for ONE node, in the node's OWN base frame.

    `offset_mm` / `rotation_deg` are the live values as the node would STORE them
    (rotation relative to its base). `stored_*` are what the node holds today.
    `absorb_mm` is the translation of the node's base caused by the chosen anchor
    (and, for content, by its module's own move) — already in that base's frame, so
    the new value is plain arithmetic. `error` set = the node could not be read and
    is reported, never guessed."""

    tree: str
    ref: str
    offset_mm: Optional[tuple[float, float]] = None
    rotation_deg: Optional[float] = None
    stored_offset: Optional[tuple[float, float]] = None
    stored_rotation: Optional[float] = None
    polar: bool = False
    absorb_mm: tuple[float, float] = (0.0, 0.0)
    error: Optional[str] = None


@dataclass(frozen=True)
class Change:
    """One node that WILL be written: its value before and after, plus the Δ the
    table shows. `now` is in the node's own representation (see `polar`).

    The ROTATION travels too (`was_rotation`/`now_rotation`, relative to the node's
    base): a node that was only TURNED has Δ = (0, 0) and used to produce an empty
    row, so its angle was never written (16.11 acceptance). `moved`/`turned` say
    which half changed — the dialog shows the other one in its own column."""

    tree: str
    ref: str
    was: tuple[float, float]
    now: tuple[float, float]
    delta: tuple[float, float]
    polar: bool = False
    was_rotation: Optional[float] = None
    now_rotation: Optional[float] = None

    @property
    def moved(self) -> bool:
        return (abs(self.delta[0]) > MM_TOLERANCE
                or abs(self.delta[1]) > MM_TOLERANCE)

    @property
    def turned(self) -> bool:
        if self.was_rotation is None or self.now_rotation is None:
            return False
        return abs(relative_rotation_deg(self.now_rotation,
                                        self.was_rotation)) > DEG_TOLERANCE


@dataclass(frozen=True)
class Skip:
    """One node that will NOT be written, with the reason in words."""

    tree: str
    ref: str
    reason: str


@dataclass(frozen=True)
class AnchorRow:
    """One tree's row: «anchor shift: was → now (N of M voters)». `was`/`now` are
    the stored SHIFT (config frame, mm) — the number the anchor form shows — so the
    row is cellable without Qt, and the dialog and the buffer write read the same
    thing (16.11 acceptance: the anchor rows did not exist in the pure plan)."""

    tree: str
    was: tuple[float, float]
    now: tuple[float, float]
    votes: int
    total: int
    reason: str

    @property
    def moved(self) -> bool:
        return (abs(self.now[0] - self.was[0]) > MM_TOLERANCE
                or abs(self.now[1] - self.was[1]) > MM_TOLERANCE)


def plan_anchor(tree: str, was: tuple[float, float], groups: list, *,
                mode: str, override: Optional[str] = None,
                to_config=None) -> AnchorRow:
    """The tree's anchor row, and the decision behind it.

    `to_config` converts a BOARD-frame delta into the shift's own (config) frame —
    the wrapper passes `board_offset_to_local_mm` at the ANCHOR's angle. The default
    is the identity, which is right for a zero anchor angle and is what the cells
    use; the plan's «anchor shift: было → стало» is then the same arithmetic the
    anchor Read does."""
    choice = choose_anchor(groups, mode=mode, override=override)
    total = sum(g.size for g in groups)
    if choice.delta is None:
        return AnchorRow(tree, was, was, 0, total, choice.reason)
    delta = to_config(choice.delta) if to_config is not None else choice.delta
    return AnchorRow(tree, was, (was[0] + delta[0], was[1] + delta[1]),
                     groups[0].size, total, choice.reason)


@dataclass
class ForestPlan:
    changes: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    anchors: list = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.changes)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def anchors_moved(self) -> int:
        return len([row for row in self.anchors if row.moved])

    @property
    def empty(self) -> bool:
        return not self.changes and not self.skipped and not self.anchors_moved


def to_polar(offset_mm: tuple[float, float]) -> tuple[float, float]:
    """(radius, angle_deg) of a base-frame offset — the SAME conversion the node
    form uses when it shows a Polar node (`atan2(-y, x)`, the KiCad sign)."""
    return (math.hypot(offset_mm[0], offset_mm[1]),
            math.degrees(math.atan2(-offset_mm[1], offset_mm[0])))


def _shown(offset, polar: bool):
    return to_polar(offset) if polar else offset


def plan_forest(reads: Iterable[NodeRead], *, anchors=()) -> ForestPlan:
    """The table: what changes, what is skipped and why, plus the per-tree anchor
    rows (`plan_anchor`) the caller decided on.

    A node is WRITTEN only when its value really moves (Δ > MM_TOLERANCE mm, or a
    turn > DEG_TOLERANCE by modulo 360) — an unmoved node must not put rounding
    noise into the file. A node that could not be read, or that the caller could
    not place, is a SKIP with its reason — never a silent absence."""
    plan = ForestPlan(anchors=list(anchors))
    for read in reads:
        if read.error is not None:
            plan.skipped.append(Skip(read.tree, read.ref, read.error))
            continue
        if read.offset_mm is None:
            plan.skipped.append(Skip(read.tree, read.ref, _(
                "no position was read for this node")))
            continue
        if read.stored_offset is None:
            plan.skipped.append(Skip(read.tree, read.ref, _(
                "the node stores no offset — nothing to rewrite")))
            continue
        new_offset = (read.offset_mm[0] - read.absorb_mm[0],
                      read.offset_mm[1] - read.absorb_mm[1])
        moved = (abs(new_offset[0] - read.stored_offset[0]) > MM_TOLERANCE
                 or abs(new_offset[1] - read.stored_offset[1]) > MM_TOLERANCE)
        turned = (read.rotation_deg is not None
                  and read.stored_rotation is not None
                  and abs(relative_rotation_deg(read.rotation_deg,
                                               read.stored_rotation)) > DEG_TOLERANCE)
        if not moved and not turned:
            continue
        plan.changes.append(Change(
            tree=read.tree, ref=read.ref,
            was=_shown(read.stored_offset, read.polar),
            now=_shown(new_offset, read.polar),
            delta=(new_offset[0] - read.stored_offset[0],
                   new_offset[1] - read.stored_offset[1]),
            polar=read.polar,
            was_rotation=read.stored_rotation,
            now_rotation=read.rotation_deg))
    return plan
