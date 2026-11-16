# kicadstamp/tree_anchor_read.py
"""«Read current position» of a TREE ANCHOR — the arithmetic, and nothing else.

The case (Denis, 16.11): a routed board, and the Grid Origin was moved AFTER the
tree had been laid out. The anchor resolves to the new Origin, the components on
the board still stand where the OLD one put them, and the next Redraw would move
the whole tree (and the routing) by the Origin's shift. The cure is to correct
the anchor's own `(shift x y)` — the ONE number the tree's content is laid out
from — and this module computes it.

THE CONTRACT: after this read, a redraw of the tree moves nothing. Only
`(shift x y)` changes; node `xy`s, the anchor's point and the tree's angle are
never touched.

WHAT VOTES: a TOP-LEVEL node that places itself — kind `placement` or `component`,
plus the SAME kind of node inside a tree a top-level `module` node embeds
(§1 of 16.11: Denis's `main` is `mh` and three modules, and the content is what
really stands on the board). A CHILD is relative to its parent and moves with
it; a `mount` node's base is a LIVE ROLE, not the tree anchor;
`copper`/`point`/`external` place no record of their own. None of them votes.

The caller supplies, per voting node, where the LAYOUT puts it and where the
board actually has it — both ABSOLUTE (mm, degrees) — the live half is read
elsewhere, once per node, through the project's own reader. Because both sides
are absolute, the tree's own angle and the anchor's angle cancel: they never
enter this comparison. What is left here is: do the deltas agree, was anything
turned, and what is the new shift.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .i18n import _
from .tree_position import board_offset_to_local_mm, relative_rotation_deg

#: Two deltas closer than this are "the same move" (mm) — the board's own
#: placement precision, the same 0.005 a node read compares with.
MM_TOLERANCE = 0.005

#: A node that was TURNED is refused: the anchor's shift cannot express a
#: rotation, and the tree's angle is not what this read changes.
DEG_TOLERANCE = 0.01


@dataclass(frozen=True)
class VotingPose:
    """ONE node that votes: its LABEL, the LAYOUT's pose (absolute, mm + degrees)
    and the LIVE one, also absolute. `error` is set when the live read failed for
    this node — then the other three live fields are meaningless.

    `ref` is a LABEL, not necessarily a bare ref: a node inside an embedded tree
    arrives as `fpga/fpga_fpga`, so a refusal names the module it lives in — the
    same words the tree row would show. Nothing here parses it."""

    ref: str
    predicted_mm: tuple[float, float]
    predicted_rot_deg: float
    live_mm: Optional[tuple[float, float]] = None
    live_rot_deg: Optional[float] = None
    error: Optional[str] = None

    @property
    def delta(self) -> Optional[tuple[float, float]]:
        """(dx, dy) the node has really moved, board mm — None when unread."""
        if self.live_mm is None:
            return None
        return (self.live_mm[0] - self.predicted_mm[0],
                self.live_mm[1] - self.predicted_mm[1])


@dataclass(frozen=True)
class AnchorReadOutcome:
    """The answer: a new `(shift x, shift y)` in the CONFIG frame — ready for the
    anchor form's shift fields — or ONE refusal line. `count` is the number of
    nodes that voted."""

    shift: Optional[tuple[float, float]] = None
    refusal: Optional[str] = None
    count: int = 0

    @property
    def ok(self) -> bool:
        return self.shift is not None


def _mm_text(delta: tuple[float, float]) -> str:
    return "({:.3f}, {:.3f})".format(delta[0], delta[1])


def read_anchor_shift(votes: Iterable[VotingPose], *,
                      anchor_rot_deg: float,
                      old_shift: Optional[tuple[float, float]] = None
                      ) -> AnchorReadOutcome:
    """The new anchor shift, or the ONE line saying why there is none.

    `anchor_rot_deg` is the ANCHOR's own angle: the shift is stored in the base
    frame BEFORE the tree turns, so the board-frame delta is converted at that
    angle (`board_offset_to_local_mm` — the project's own pair, no second
    formula). `old_shift` None means "no shift stored yet" = (0, 0)."""
    votes = list(votes)
    if not votes:
        return AnchorReadOutcome(refusal=_(
            "nothing to read: no top-level placement or component node of this "
            "tree places itself — a shift cannot be derived from children, "
            "mounts or modules"))

    failed = [vote for vote in votes if vote.error]
    if failed:
        return AnchorReadOutcome(refusal=_(
            "tree anchor: node {ref!r} could not be read — {error}").format(
                ref=failed[0].ref, error=failed[0].error))

    # ABSOLUTE against ABSOLUTE, by modulo 360: `relative_rotation_deg` is the
    # project's own (−180, 180] normalisation, so a node standing still at 180°
    # or 270° compares as 0, not as a "turn" (the §1 hole of 16.11: one side was
    # relative to the anchor and the other absolute, so ANY turned anchor or any
    # 180°/270° part refused falsely).
    turned = [vote for vote in votes
              if vote.live_rot_deg is not None
              and abs(relative_rotation_deg(vote.live_rot_deg,
                                            vote.predicted_rot_deg)) > DEG_TOLERANCE]
    if turned:
        vote = turned[0]
        return AnchorReadOutcome(refusal=_(
            "tree anchor: node {ref!r} is turned {live}° instead of {pred}° — the "
            "anchor's shift cannot express a rotation, and the tree's own angle "
            "is not something this read changes").format(
                ref=vote.ref, live=round(vote.live_rot_deg, 3),
                pred=round(vote.predicted_rot_deg, 3)))

    deltas = [(vote, vote.delta) for vote in votes]
    first_delta = deltas[0][1]
    spread = [vote for vote, delta in deltas
              if abs(delta[0] - first_delta[0]) > MM_TOLERANCE
              or abs(delta[1] - first_delta[1]) > MM_TOLERANCE]
    if spread:
        # No majority vote: a tree on a board is not rigid, and WHICH node moved
        # by hand is Denis's call — the line names every node, closest to the
        # first one first.
        ordered = sorted(deltas, key=lambda pair: (
            abs(pair[1][0] - first_delta[0]) + abs(pair[1][1] - first_delta[1])))
        listed = ", ".join(f"{vote.ref} {_mm_text(delta)}"
                           for vote, delta in ordered)
        return AnchorReadOutcome(refusal=_(
            "tree anchor: the nodes did not move together — {listed} (mm, board "
            "frame); fix the odd one by hand, or move the tree as a whole").format(
                listed=listed))

    delta = board_offset_to_local_mm(first_delta, anchor_rot_deg)
    old = old_shift or (0.0, 0.0)
    return AnchorReadOutcome(shift=(old[0] + delta[0], old[1] + delta[1]),
                             count=len(votes))
