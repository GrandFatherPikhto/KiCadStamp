# gui/docks/trees_move_flow.py
"""The "Move to…" flows of the trees dock and the PURE half they are built on —
which nodes a selection moves, which parents may take them, and how a node's
numbers are re-expressed when its base changes
(plan_2026_10_14_tree_group_move_and_mount_rebase, Ч1 п.2–п.7).

Why this module exists at all: `gui/docks/trees_dock.py` is a giant (7323 lines
before this plan, and the plan forbids growing it), so every NEW branch of the
group flow lands here instead.

The module has TWO halves, and they are deliberately kept apart by name:

  * the PURE half — a walk over a `Tree`'s nodes with no widgets, no adapter and
    no config, so it can be read and tested as data (`group_roots`,
    `forbidden_subtrees`, `move_candidates`, `split_untouchable`,
    `reexpress_offset`, `local_polar_from_xy`, `plan_rehang` and friends);
  * the FLOW BODIES — `run_single_move`, `run_group_move` and `apply_group_move`,
    which DO need the dock (its parent picker, its status/log channels, its
    guard methods). Each takes the dock as its first argument and reaches every
    read through `dock.<name>` or the `trees_dock` module attribute, never
    through a `from … import` at module level: the door guards patch those names
    BY MODULE ATTRIBUTE, so a smuggled import would be invisible to them and a
    broken one would never be caught (Д3 of the Ч0 review).

What does NOT live here, and why (Д3 of the Ч0 review):

  * the pivot-ref RULE (`_pivot_ref_mount_parent`) stays in `trees_dock.py` and is
    handed in as the `pivot_rejects` callable — one expression of the rule, the
    same one the node form's Parent combo uses, and the same one the existing
    guards monkeypatch;
  * the offset arithmetic LIVES HERE (`reexpress_offset`) — the Ч0 review's Д3
    allows exactly this much to move: `_reparented_offset` STAYS in the dock (the
    existing guards patch that name by module attribute and must keep driving the
    real thing) and calls this, so the formula has ONE expression every entry of
    the plan can be run through — the single re-hang, the group, and the mount
    anchor rebase of Ч2;
  * the base RESOLUTION (which frame a node's numbers are expressed against), the
    board reads and every widget (the parent picker, the messages) stay in the
    dock, which owns the door signs;
  * the copper kinds set is passed in (`TreesDock._COPPER_ROW_KINDS`), never
    re-listed here — a second copy of "which kinds are rows under the Copper
    fold" is exactly the drift this project refuses.

Ч1 п.3 ("выделены предок и его потомок → переносится только предок") and Ч1 п.7
("перенесённые узлы встают у нового родителя в своём прежнем взаимном порядке
документа") are both answered by ONE function, `group_roots`, whose walk order IS
the document order.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from PyQt6.QtWidgets import QInputDialog

from kicadstamp.domain.geometry import Vector2
from kicadstamp.i18n import _
from kicadstamp.tree_position import (_snap_mm, board_offset_to_local_mm,
                                      board_rotation_to_local_deg,
                                      local_offset_to_board_mm,
                                      local_rotation_to_board_deg,
                                      rotate_offset_mm)
from kicadstamp.trees import Tree, TreeNode
from kicadstamp.utils.units import MM

from ..worker import defer_while_socket_busy

logger = logging.getLogger(__name__)


def group_roots(tree: Tree, nodes: Sequence[TreeNode]) -> list[TreeNode]:
    """The nodes a selection really moves: every selected node that has NO
    selected ancestor, in DOCUMENT (pre-order) order.

    This is Ч1 п.3 and Ч1 п.7 in one walk. A selected descendant of a selected
    node rides with its ancestor (moving the ancestor moves the whole subtree, so
    moving the descendant again would be a second, contradictory instruction),
    and the returned order is the order the nodes come back in under their new
    parent — the document order the redraw reads as its tie-breaker, NOT the
    order the user happened to click rows in.

    Identity, not equality: `TreeNode` is a dataclass with value equality (and an
    unhashable list field), so ids are used for "is selected", exactly like the
    dock's own `_in_list`. A node that is not in `tree` at all is IGNORED rather
    than raising: the selection comes from the tree's own widget, so a foreign
    node means a bug somewhere else, and dropping it cannot corrupt the tree."""
    selected = {id(node) for node in nodes}
    if not selected:
        return []
    out: list[TreeNode] = []

    def _walk(children: Sequence[TreeNode], inside_selected: bool) -> None:
        for child in children:
            if id(child) in selected:
                if not inside_selected:
                    out.append(child)
                _walk(child.children, True)
            else:
                _walk(child.children, inside_selected)

    _walk(tree.nodes, False)
    return out


def forbidden_subtrees(tree: Tree, roots: Sequence[TreeNode]) -> list[TreeNode]:
    """Every node of `tree` that may NOT become the new parent of `roots`: each
    root itself and its whole subtree, in document order (Ч1 п.4 — "кандидаты в
    родители минус каждый выделенный узел и его поддерево").

    A node cannot be moved into its own subtree: that is a structural invariant
    (`_reparent_node` refuses it too), so the picker must not even offer it."""
    root_ids = {id(node) for node in roots}
    out: list[TreeNode] = []

    def _walk(children: Sequence[TreeNode], inside: bool) -> None:
        for child in children:
            within = inside or id(child) in root_ids
            if within:
                out.append(child)
            _walk(child.children, within)

    _walk(tree.nodes, False)
    return out


def move_candidates(
        tree: Tree, roots: Sequence[TreeNode],
        pivot_rejects: Callable[[TreeNode, Optional[TreeNode]],
                                Optional[TreeNode]],
) -> list[tuple[str, Optional[TreeNode]]]:
    """The parent picker's rows for a group: `("(top level)", None)` first (when
    the rule allows it), then every node a GROUP may be re-hung under, by ref.

    A candidate is offered only when the pivot-ref rule accepts it for EVERY node
    of the group: the rule pins a node's base to a live component, so a parent
    that would pin ONE member of the group would make the next config load a
    fatal (`_validate_tree_pivot_ref`, reason "mount-ancestor"), no matter what
    the other members say. `pivot_rejects(node, candidate)` is that rule —
    `trees_dock._pivot_ref_mount_parent`, handed in by the dock — and it is asked
    per node, never re-expressed here (Ч1 п.4).

    `roots` must be `group_roots`'s answer: forbidden parents are computed from it
    (a selected descendant is already covered by its ancestor's subtree).

    The rule is asked until the FIRST refusal for a candidate (one member being
    pinned settles that candidate, so asking the rest cannot change the answer):
    the walk stays one pass over the tree, times the group. What that gives up is
    naming WHICH member blocked a candidate — nothing in this plan needs that."""
    forbidden = forbidden_subtrees(tree, roots)
    forbidden_ids = {id(node) for node in forbidden}
    rows: list[tuple[str, Optional[TreeNode]]] = []
    if all(pivot_rejects(node, None) is None for node in roots):
        rows.append((_("(top level)"), None))

    def _walk(children: Sequence[TreeNode]) -> None:
        for child in children:
            if id(child) in forbidden_ids:
                continue        # its whole subtree is forbidden with it
            if all(pivot_rejects(node, child) is None for node in roots):
                rows.append((child.ref, child))
            _walk(child.children)

    _walk(tree.nodes)
    return rows


def split_untouchable(nodes: Sequence[TreeNode], copper_kinds
                      ) -> tuple[list[TreeNode], list[TreeNode]]:
    """`(movable, excluded)` for a group that is about to be moved: the nodes a
    "Move to…" may re-hang, and the ones it may not.

    A copper node (`net_trace`, and the legacy `copper` container) is a fold under
    the "Copper" pseudo-node and carries no position of its own — its copper is
    offset from its OWN anchor pad — so it has nothing to re-hang and no place in
    the group order (design §12.1, Ч1 п.5). The caller names the kinds
    (`TreesDock._COPPER_ROW_KINDS`) and reports the excluded ones in the Log: the
    user asked for a group move, so a member silently dropped from it would be a
    lie about what happened."""
    movable: list[TreeNode] = []
    excluded: list[TreeNode] = []
    for node in nodes:
        (excluded if node.kind in copper_kinds else movable).append(node)
    return movable, excluded


def log_copper_skipped(excluded: Sequence[TreeNode]) -> None:
    """Ч1 п.5 — a copper row cannot ride with the group (its copper is offset
    from its OWN anchor pad), so it is NAMED rather than silently left behind.

    SEPARATE from `movable_roots` on purpose: the Ч3 cursor asks `movable_roots`
    on EVERY mouse move (`dragMoveEvent`), and a Log line per move would flood
    the Log with the same sentence. The FLOWS name it once, when the move is
    really attempted."""
    if excluded:
        logger.warning(_GROUP_COPPER_SKIPPED.format(
            nodes=", ".join(sorted(node.ref for node in excluded))))


def movable_roots(tree: Tree, nodes: Sequence[TreeNode], copper_kinds
                  ) -> tuple[list[TreeNode], list[TreeNode]]:
    """`(roots, excluded)` for a move of `nodes`: the REAL nodes it re-hangs, and
    the copper rows it must leave behind.

    The copper rows are dropped (Ч1 п.5) and a selected descendant of a selected
    node rides with its ancestor (`group_roots`, Ч1 п.3 + п.7). THE one place the
    selection becomes a move list — the Ч1 picker, the Ч3 cursor and the drop all
    read it, so a drop cannot move a different set of nodes than the menu entry
    for the same selection would. Silent: the caller names the excluded ones
    (`log_copper_skipped`) where a move is really attempted."""
    movable, excluded = split_untouchable(nodes, copper_kinds)
    return group_roots(tree, movable), excluded


# ── The offset arithmetic: one base replaced by another ─────────────────────
#
# THE single expression of "the base under these numbers changed, so the numbers
# must change with it". `_reparented_offset` (a re-hang) and the mount anchor
# rebase of Ч2 are two CALLERS of it, not two copies: the formula is subtle
# enough (a board-frame hop, a snap that mirrors the offset widget's own round
# trip) that a second copy would drift within one commit.

#: A node's (xy | polar, rotation) after a base change: `(xy, polar, rotation)`.
ReexpressedOffset = tuple[Optional[tuple[float, float]],
                          Optional[tuple[float, float]], float]


def reexpress_offset(xy: Optional[tuple[float, float]],
                     polar: Optional[tuple[float, float]],
                     rotation: float,
                     old_base: tuple[Vector2, Optional[float]],
                     new_base: tuple[Vector2, Optional[float]],
                     ) -> ReexpressedOffset:
    """`(xy, polar, rotation)` for a node whose base moved from `old_base` to
    `new_base`, chosen so the node does NOT move on the BOARD.

    A node's `xy`/`polar` are stored in its base's local frame and its `rotation`
    is RELATIVE to that base, so replacing the base silently changes what the
    stored numbers mean. The absolute board pose is preserved here: the board
    offset against the new base is the old one plus the base SHIFT (old - new),
    and the absolute board angle is untouched, so only the stored relative angle
    changes when the base turned.

    Each base is `(position_nm, rotation_deg | None)`: a base with no angle
    concept (an origin/point anchor) stores rotation exactly like the form does —
    0.0 is the definition there, not a guess.

    The representation is preserved: a polar node comes back polar (a silent
    switch to xy would read as somebody else's edit in the s-expr diff), and a
    node with no stored offset is the CALLER's case to refuse (it has no frame to
    rewrite) — this function always answers with numbers.

    WHY the snap: `_snap_mm` mirrors the offset widget's own round trip (the form
    stores what it shows), so a re-hang through the menu and the same re-hang
    through the form land on bit-identical numbers."""
    old_pos, old_deg = old_base
    new_pos, new_deg = new_base
    old_rot = old_deg if old_deg is not None else 0.0
    new_rot = new_deg if new_deg is not None else 0.0

    # abs = base + board offset  =>  new board offset = old + (old base - new).
    dx_mm = (old_pos.x - new_pos.x) / MM
    dy_mm = (old_pos.y - new_pos.y) / MM

    board_rot = local_rotation_to_board_deg(rotation, old_rot)
    new_rotation = board_rotation_to_local_deg(board_rot, new_rot)

    if polar is not None:
        # The offset takes the SAME board-frame hop the form's widget does
        # (rotate to the board frame, shift, read the radius and angle back).
        bx, by = rotate_offset_mm(
            polar[0], 0.0, local_rotation_to_board_deg(polar[1], old_rot))
        bx = _snap_mm(bx + dx_mm)
        by = _snap_mm(by + dy_mm)
        radius = _snap_mm(math.hypot(bx, by))
        angle = _snap_mm(math.degrees(math.atan2(-by, bx)))
        return (None, (radius, board_rotation_to_local_deg(angle, new_rot)),
                new_rotation)

    bx, by = local_offset_to_board_mm(xy, old_rot)
    new_xy = board_offset_to_local_mm(
        (_snap_mm(bx + dx_mm), _snap_mm(by + dy_mm)), new_rot)
    return (new_xy, None, new_rotation)


def local_polar_from_xy(xy: tuple[float, float], base_rot: float
                        ) -> tuple[float, float]:
    """The `(radius, angle)` twin of a LOCAL `xy` offset in a base turned
    `base_rot` — the polar convention `reexpress_offset` reads back, expressed
    through the SAME two primitives (the vector is taken to the board frame by the
    base rotation, and the angle is expressed against the base again) rather than
    a hand-rolled atan2 that could pick the other sign.

    Needed because a board READ answers in xy (`_resolve_live_offset`), while a
    polar node must stay polar (Л.2): turning it into xy would read as somebody
    else's edit in the s-expr diff."""
    bx, by = local_offset_to_board_mm(xy, base_rot)
    radius = _snap_mm(math.hypot(bx, by))
    angle = _snap_mm(math.degrees(math.atan2(-by, bx)))
    return (radius, board_rotation_to_local_deg(angle, base_rot))


# ── The source order of a re-hang: recalculate, else read, else refuse ──────
#
# Denis, 14.10: "при смене родительской ноды координаты должны пересчитываться",
# and "если нельзя пересчитать — просто прочитать текущие координаты с платы".
# THIS is that order, as data. The two readers are handed in — they are the dock's,
# and they own the door signs and the caches — so the rule itself can be read and
# tested without a board.


@dataclass
class RehangPlan:
    """What a re-hang will write, node by node — the answer of `plan_rehang`.

    `shifts`     — [(node, (xy, polar, rotation) | None)], in the order the nodes
                   were given. `None` means "nothing to change": a mount node's
                   frame is its own anchor, and a node storing no offset has no
                   frame to rewrite. The caller writes nothing for those.
    `from_board` — [(node, reason)] whose numbers were READ off the board because
                   the recalculation was impossible, with that reason. The board
                   and the config may disagree (the board may simply not be
                   redrawn yet), so the SOURCE has to be visible in the Log.
    `refused`    — [(node, reason)] that could be neither recalculated nor read,
                   with both reasons. A non-empty list means NOTHING moves at all
                   (plan Ч1 п.6): the caller refuses the whole re-hang and names
                   these nodes in one Log line."""

    shifts: list[tuple[TreeNode, Optional[ReexpressedOffset]]] = field(
        default_factory=list)
    from_board: list[tuple[TreeNode, str]] = field(default_factory=list)
    refused: list[tuple[TreeNode, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the re-hang can happen — every node either recomputed or
        read. All-or-nothing is THIS property, not a convention the callers keep."""
        return not self.refused


def plan_rehang(nodes: Sequence[TreeNode],
                recalculate: Callable[[TreeNode], Optional[ReexpressedOffset]],
                read_live: Callable[[TreeNode], ReexpressedOffset],
                ) -> RehangPlan:
    """The offsets of a re-hang, per node, in the source order of the plan's
    "сквозное правило":

      1. `recalculate(node)` — the arithmetic of `reexpress_offset` (old base →
         new base). A `None` answer is NOT a failure: it is the node saying its
         numbers do not depend on the parent (a mount node's frame is its own
         anchor) or that it stores no offset at all — nothing to hold still, so
         nothing is written and nothing is asked. A raised exception means the
         recalculation really was impossible;
      2. `read_live(node)` — the node's CURRENT place read off the board relative
         to the NEW parent. Reached ONLY when the recalculation raised: that is
         the case the plan is about (the old base is gone from the board, which is
         usually why the node is being moved);
      3. neither — the node is refused with BOTH reasons.

    The two callables are the caller's: the dock's own readers, with their door
    signs, so no board call is hidden in here. This function owns the ORDER, the
    per-node exception bookkeeping and the all-or-nothing answer, so that rule
    exists ONCE — the group (Ч1), the single node (which then asks the user) and
    the drop (Ч3) all run through it."""
    plan = RehangPlan()
    for node in nodes:
        try:
            shift = recalculate(node)
        except Exception as exc:        # noqa: BLE001 — see the clauses below
            reason = str(exc)
            try:
                shift = read_live(node)
            except Exception as read_exc:   # noqa: BLE001 — the refusal path
                plan.refused.append(
                    (node, _("{first}; reading it from the board failed too: "
                             "{second}").format(first=reason, second=read_exc)))
                continue
            plan.from_board.append((node, reason))
        plan.shifts.append((node, shift))
    return plan


# ── Group "Move to…" (Ч1 of plan_2026_10_14_tree_group_move_and_mount_rebase) ──
#
# Ч1 п.2: several SELECTED nodes, ONE new parent, inside the SAME tree. Ч1 п.6:
# all or nothing — one node that can neither be recalculated nor read from the
# board cancels the whole move. Ч1 п.10: every refusal is a Log line, never a
# dialog (rule 43); the right-hand panel and the status row show the group's
# COUNT instead of an editor, so nothing in this flow can be written by accident.

#: The door sign of a re-hang's base resolve — ONE wording for EVERY entry that
#: resolves bases to hold a node (or a group) still: the single "Move to…", the
#: group move and the dropped-on-node move of Ч3. What it covers is measured to
#: the LEAVES (diagnostics/probe_2026_09_22_rehang_offset_cost.py): per node TWO
#: anchor resolves — the OLD and the NEW base — and, per Entity-typed parent, ONE
#: whole-board `adapter.refresh_board()` PLUS the full-board re-read it forces on
#: the very next line (the adapter's caches are emptied, so the first
#: `get_footprint(ref)` walks all 332 footprints over IPC). Neither is removed by
#: the snapshot, which is why the sign names them (Кj: signing a cost without
#: naming it is what this project refuses).
_REHANG_SIGN = (
    "resolve the new parent's live base for a re-hang (two anchor resolves, "
    "identity from the snapshot) — plus, per Entity-typed parent, ONE whole-board "
    "adapter.refresh_board() AND the full-board re-read it forces on the next "
    "line, both sent from the UI thread")

#: The door sign of the FALLBACK read: the recalculation was impossible (usually
#: because the OLD base is gone from the board, which is why the node is being
#: moved at all), so the node's CURRENT place is read relative to the NEW parent
#: — one base resolve plus the child's own record/address read.
_READ_LIVE_SIGN = (
    "read the node's CURRENT place from the board for a re-hang whose "
    "recalculation was impossible — one new-parent base resolve plus the "
    "node's own live read (identity from the snapshot)")

#: Where a re-hang's numbers came from when the recalculation was impossible: the
#: board and the config may disagree (the board may simply not be redrawn yet), so
#: the SOURCE is named instead of assumed.
_OFFSET_READ_FROM_BOARD = _(
    "{node}: offset read from the board (recalculation impossible: {reason})")

#: Ч1 п.6/п.10 — ONE line naming the nodes and the reasons; nothing moves.
_GROUP_MOVE_REFUSED = _(
    "{nodes}: offset neither recalculated nor read from the board — move "
    "cancelled ({reason})")

#: The SINGLE-node twin of the line above (доделка п.4.3 of the Ч1 acceptance):
#: one node DOES get a question right after the refusal, so "move cancelled"
#: would contradict the question the very next line asks. Same reason, no verdict.
_MOVE_OFFSET_REFUSED = _(
    "{node}: offset neither recalculated nor read from the board ({reason})")

#: Ч1 п.5 — a copper node cannot ride with the group (its copper is offset from
#: its OWN anchor pad), so it is NAMED rather than silently left behind.
_GROUP_COPPER_SKIPPED = _(
    "Move to…: copper nodes do not move ({nodes}) — a copper node's copper is "
    "offset from its own anchor pad")

#: "What just happened", the same sentence Ч3 п.5 asks for on a drop, so both
#: entries into the group move read alike in the Log.
_TREE_MOVE_DONE = _("moved: {nodes} — new parent: {parent}")


def rehang_under_known_parent(dock, tree: Tree, roots: Sequence[TreeNode],
                              new_parent: Optional[TreeNode], *,
                              ask_single: bool) -> None:
    """Re-hang `roots` under `new_parent` — THE one body of "the new parent is
    already known", shared by the Ч1 picker, the Ч3 drop and the single dialog
    path. What differs is ONLY the flag:

      * `ask_single=True` (the single "Move to…"): for ONE node the user is
        asked, in the form's own words, whether to re-hang WITHOUT recalculating
        when the offset can be neither recomputed nor read;
      * `ask_single=False` (a group, and EVERY drop): nothing is asked — a modal
        question in the middle of a drag is a bad gesture (Денис, 14.10:
        "брошенный мышью узел ни о чём не спрашивает"), so a lone DROPPED node
        refuses through the same Log line the group uses. The question stays
        reachable through "Move to…".

    Everything else is the dock's: the board reads, the arithmetic and the door
    signs are reached through `dock.*` / the `trees_dock` module attribute (Д3 of
    the Ч0 review), exactly as `_rehang_plan` documents, so no read is hidden
    here.

    ONE deferred retry wraps the WHOLE call (Т2-4б): the read, the offsets and the
    structural half are one unit, so a busy socket cannot half-apply a group.

    TWO THINGS THIS SHAPE DOES NOT GUARD — named (Кj's "мелочи"), not hidden:

    1. the body does its work SYNCHRONOUSLY instead of starting a worker, so
       defer_while_socket_busy's "proceed() must NOT touch the adapter itself" is
       bent here. What closes the race is not the helper but the `socket_busy`
       check INSIDE `dock._rehang_offset_or_ask` / `dock._rehang_plan`, made one
       line before the read; the helper contributes the deferral and the liveness
       check. The helper's docstring carries that second legitimate shape, so the
       document and the code agree.
    2. the retry's liveness check asks only whether the DOCK was deleted (the
       helper's own `_gone(); owner=dock`). `tree`, `roots` and `new_parent` are
       CAPTURED BY THE CLOSURE, so a root switch inside the 120 ms window would
       write into a tree the config no longer holds. There is no board write, the
       window is 120 ms and the owner usually loses liveness first — accepted and
       named here rather than growing a second staleness mechanism beside the
       dock's own root tracking."""
    def _rehang() -> None:
        if ask_single and len(roots) == 1:
            # The single dialog path keeps its OWN question, its own Log line and
            # its OWN structural seam: `_rehang_offset_or_ask` asks,
            # `_reparent_node` moves ONE node, and a refused structural half
            # restores the numbers — the shape the existing guards drive (they
            # monkeypatch exactly these two names). The group's
            # `apply_group_move` / `_reparent_nodes` pair is NOT used here: a cell
            # of the Ч2 acceptance pins the single path's rollback through
            # `_reparent_node`, and moving it onto the group seam would quietly
            # change which name refuses.
            node = roots[0]
            proceed, shift = dock._rehang_offset_or_ask(
                tree, node, dock._find_parent(tree, node), new_parent)
            if not proceed:
                return   # the user refused a re-hang that cannot be held still
            previous = (node.xy, node.polar, node.rotation)
            if shift is not None:
                # Written BEFORE the structural move so the (immediate) rebuild
                # renders the node in its new frame; rolled back if the
                # structural half refuses — a refused move must leave the node
                # byte-for-byte as it was.
                node.xy, node.polar, node.rotation = shift
            if not dock._reparent_node(tree, node, new_parent,
                                       defer_rebuild=False):
                node.xy, node.polar, node.rotation = previous
            return
        plan = dock._rehang_plan(
            tree, roots, new_parent,
            {id(node): dock._find_parent(tree, node) for node in roots})
        if not plan.ok:
            # Ч1 п.6 — ALL OR NOTHING: ONE line names the nodes and the reasons,
            # and the structural half is never reached. A dropped single node
            # lands here too (ask_single=False): same line, same refusal, no
            # question.
            logger.error(_GROUP_MOVE_REFUSED.format(
                nodes=", ".join(node.ref for node, _why in plan.refused),
                reason="; ".join(
                    "{ref}: {why}".format(ref=node.ref, why=why)
                    for node, why in plan.refused)))
            return
        for from_node, reason in plan.from_board:
            logger.info(_OFFSET_READ_FROM_BOARD.format(
                node=from_node.ref, reason=reason))
        if not apply_group_move(dock, tree, plan.shifts, new_parent):
            logger.warning(
                "Refusing to move %s: the structural half refused (see the "
                "line above)", ", ".join(node.ref for node in roots))
            return
        logger.info(_TREE_MOVE_DONE.format(
            nodes=", ".join(node.ref for node in roots),
            parent=_("(top level)") if new_parent is None else new_parent.ref))

    connection = getattr(dock._main_window, "connection", None)
    defer_while_socket_busy(connection, (), _rehang, dock._warn_rehang_busy,
                            owner=dock)


def run_single_move(dock, tree: Tree, node: TreeNode) -> None:
    """FORK-C: a parent-picker dialog, no drag&drop. The candidate list
    excludes the node itself and its own descendants (a structural
    invariant — you cannot move a node into its own subtree) and, when the
    node IS the tree's pivot-ref, every mount node with its whole subtree:
    a re-hang there pins the node's base to a LIVE component and the LOADER
    would refuse the config at the next load (kicadstamp.trees
    _validate_tree_pivot_ref, reason "mount-ancestor"). Both rules are
    enforced by CONSTRUCTION through the shared _pivot_ref_mount_parent —
    the same single expression of the pivot-ref rule the form's combo uses,
    never a second copy of it.

    The offset is recalculated so the node does NOT physically move — the
    form's own arithmetic, on the TreeNode itself (dock._reparented_offset,
    reached through dock._rehang_plan). The structural half belongs to
    _reparent_node alone: it owns the ONE removal path (by identity, never
    list.remove) and the ONE rebuild trigger. This flow has neither, and it
    rebuilds IMMEDIATELY (defer_rebuild=False): there is no embedded form to
    destroy here — the one-turn deferral exists only because the form's combo
    calls _reparent_node from inside its own apply() (Л.4).

    When the new base does not resolve there is nothing to hold the node
    still with: the Log says so and the user is asked, in the form's own
    words, whether to re-hang WITHOUT recalculating; a "No" leaves the tree
    and the node exactly as they were."""
    from . import trees_dock

    forbidden = dock._collect_subtree(node)
    candidates: list[tuple[str, Optional[TreeNode]]] = [(_("(top level)"), None)]
    for top in tree.nodes:
        dock._collect_move_candidates(top, forbidden, candidates)
    candidates = [row for row in candidates
                  if trees_dock._pivot_ref_mount_parent(tree, node, row[1]) is None]

    labels = [label for label, _t in candidates]
    choice, ok = QInputDialog.getItem(
        dock, _("Move to…"), _("New parent:"), labels, 0, False)
    if not ok:
        return
    new_parent = candidates[labels.index(choice)][1]
    if new_parent is node or dock._in_list(new_parent, forbidden):
        return  # structural invariant — the dialog never offered it
    pinned = trees_dock._pivot_ref_mount_parent(tree, node, new_parent)
    if pinned is not None:
        # Never offered above, so reaching this means the dialog was
        # bypassed: refuse instead of writing a tree the loader will kill.
        logger.warning(
            "Refusing to move %r under mount node %r: %r is the tree's "
            "pivot-ref and a node under a mount node does not follow the "
            "tree", node.ref, pinned.ref, node.ref)
        return
    old_parent = dock._find_parent(tree, node)
    if old_parent is new_parent:
        # Picking the parent the node already has is not a re-hang — the
        # form's combo treats it the same way (its _apply_parent_change
        # returns early): no board read, no dirty flag, no rebuild.
        return
    # THE one re-hang body, with the single dialog path's own question
    # (ask_single=True): the deferred retry, the named non-guards and the
    # roll-back of the numbers all live in rehang_under_known_parent now — one
    # expression for the picker, the group and a drop.
    rehang_under_known_parent(dock, tree, [node], new_parent, ask_single=True)


def run_group_move(dock, tree: Tree, clicked: TreeNode) -> None:
    """Ч1 п.2 — "Move to…" for the SELECTION: several nodes, ONE new parent,
    inside the SAME tree. Falls back to the single-node flow (with its own
    question) when only one node really moves.

    The group is the SELECTION, not the clicked row: Ч1 п.1 makes a
    right-click outside the selection collapse it onto the clicked row first,
    and a right-click INSIDE it keeps the group — which is how a group move is
    asked for from any row of the group.

    A copper node is NAMED and dropped from the group (Ч1 п.5): it carries no
    position of its own (its copper is offset from its own anchor pad), and
    moving "most of" a selection silently would be a lie about what happened.

    Nothing here is a dialog on the refusal path (Ч1 п.10): a group that
    cannot be held still says so in the Log, in ONE line, and the tree and
    every node stay exactly as they were."""
    from . import trees_dock

    selection = dock._selected_real_nodes(tree)
    if not dock._in_list(clicked, selection):
        selection = [clicked]        # a bypassed right-click rule
    roots, excluded = movable_roots(tree, selection, trees_dock._COPPER_ROW_KINDS)
    log_copper_skipped(excluded)
    if len(roots) < 2:
        # Ч1 п.3 can leave ONE root out of a large selection (an ancestor
        # whose descendants are selected too): that IS the single-node case,
        # so it goes through the single flow — one picker, one question, no
        # second path to keep in step.
        dock._move_node_flow(tree, roots[0] if roots else clicked)
        return

    def _pivot_rejects(node, candidate):
        """The ONE pivot-ref rule, bound to THIS tree — `move_candidates` asks
        it per group node, and the rule itself stays in the dock module (Ч1 п.4:
        one expression, never a second copy in the flow module)."""
        return trees_dock._pivot_ref_mount_parent(tree, node, candidate)

    candidates = move_candidates(tree, roots, _pivot_rejects)
    labels = [label for label, _parent in candidates]
    choice, ok = QInputDialog.getItem(
        dock, _("Move to…"), _("New parent:"), labels, 0, False)
    if not ok:
        return
    new_parent = candidates[labels.index(choice)][1]
    for node in roots:
        pinned = trees_dock._pivot_ref_mount_parent(tree, node, new_parent)
        if pinned is not None:
            # Never offered above, so reaching this means the picker was
            # bypassed: refuse instead of writing a tree the loader kills.
            logger.warning(
                "Refusing to move %r under mount node %r: %r is the tree's "
                "pivot-ref and a node under a mount node does not follow "
                "the tree", node.ref, pinned.ref, node.ref)
            return
    if all(dock._find_parent(tree, node) is new_parent for node in roots):
        return                       # every node already hangs there

    # The drop's twin: the parent is known, and nothing is asked. ONE body with
    # the picker — a drop cannot apply a different set of checks or a different
    # refusal than "Move to…" does (Ч3 п.1).
    rehang_under_known_parent(dock, tree, roots, new_parent, ask_single=False)


def apply_group_move(dock, tree: Tree, shifts, new_parent) -> bool:
    """Write the planned offsets and re-hang the group in ONE structural step.
    `shifts` — [(node, (xy, polar, rotation) | None)] in document order, where
    None means "nothing to change" (a mount node's frame is its own anchor, a
    node storing no offset has no frame to rewrite).

    The offsets are written BEFORE the structural move, so the immediate
    rebuild renders the nodes in their new frame, and rolled back if the
    structural half refuses — a refused group leaves every node byte for byte
    as it was, exactly like the single-node path."""
    written: list = []
    for node, shift in shifts:
        if shift is None:
            continue
        written.append((node, (node.xy, node.polar, node.rotation)))
        node.xy, node.polar, node.rotation = shift
    if not dock._reparent_nodes(tree, [node for node, _shift in shifts],
                                new_parent, defer_rebuild=False):
        for node, previous in written:
            node.xy, node.polar, node.rotation = previous
        return False
    return True


# ── Ч3: a DROP is the same re-hang, with the parent already chosen ──────────
#
# Ч3 п.1 (plan_2026_10_14_tree_group_move_and_mount_rebase): dragging the
# selection ONTO a node IS "Move to…" under that node. There is no second set of
# checks — `drop_refusal` below is built from the SAME pieces the picker's
# candidate list is (`forbidden_subtrees` and the pivot-ref rule asked per node),
# so a target the cursor accepts cannot be a target the move then refuses.
#
# Ч3 п.3/п.4 (Д-6/Д-7): a structurally impossible target is refused SILENTLY — the
# cursor says "no" and the Log stays empty; only a move that CANNOT BE HELD STILL
# (the all-or-nothing refusal of `plan_rehang`) leaves a Log line, and a lone
# dropped node refuses exactly like a group (Денис, 14.10: "брошенный мышью узел
# ни о чём не спрашивает" — the question stays with "Move to…").


def drop_refusal(tree: Tree, roots: Sequence[TreeNode],
                 target: Optional[TreeNode],
                 pivot_rejects: Callable[[TreeNode, Optional[TreeNode]],
                                         Optional[TreeNode]]
                 ) -> Optional[str]:
    """None when `roots` may be dropped on `target`; otherwise the reason it may
    not — ONE predicate for both the cursor (`dragMoveEvent`) and the drop
    itself, so the two can never disagree about a target.

    `target is None` is the empty space under the rows — "(top level)", exactly
    the picker's own first row. A target that IS one of the roots, or sits
    anywhere under one, is refused: a node cannot be moved into its own subtree,
    the structural invariant `_reparent_nodes` refuses too. The pivot-ref rule is
    asked per node through `pivot_rejects` (the dock's own
    `_pivot_ref_mount_parent`, Ч1 п.4 — one expression): a target that would pin
    ONE member of the group refuses the whole group, because the next config load
    would be a fatal."""
    if target is None:
        for node in roots:
            if pivot_rejects(node, None) is not None:
                return ("the top level would pin {ref} to a mount"
                        .format(ref=node.ref))
        return None
    if not isinstance(target, TreeNode):
        return "the row under the cursor is not a node"
    forbidden = {id(node) for node in forbidden_subtrees(tree, roots)}
    if id(target) in forbidden:
        return "the target is one of the dropped nodes or a descendant of one"
    for node in roots:
        pinned = pivot_rejects(node, target)
        if pinned is not None:
            return ("{ref} is the tree's pivot-ref and a node under mount "
                    "{mount} does not follow the tree"
                    .format(ref=node.ref, mount=pinned.ref))
    return None


def run_drop_move(dock, tree: Tree, target: Optional[TreeNode]) -> None:
    """Ч3 п.1/п.5 — the selection DROPPED on `target` (None = the empty space):
    the Ч1 "Move to…" with the parent already chosen, and NOTHING asked.

    The target is re-checked here even though the cursor has already refused the
    impossible ones: `dropEvent` can be reached through a path that never ran
    `dragMoveEvent` (a test, a programmatic drop), and a write the cursor would
    not have allowed must not become possible just because the check was skipped.
    The check is the SAME `drop_refusal` — never a second copy.

    A drop on the parent the nodes already hang under changes nothing (the menu
    entry returns for the same case): there is no re-hang to do, so no read, no
    dirty flag and no rebuild."""
    from . import trees_dock

    selection = dock._selected_real_nodes(tree)
    roots, excluded = movable_roots(tree, selection, trees_dock._COPPER_ROW_KINDS)
    log_copper_skipped(excluded)
    if not roots:
        return                       # nothing movable in the selection
    refusal = drop_refusal(
        tree, roots, target,
        lambda node, candidate: trees_dock._pivot_ref_mount_parent(
            tree, node, candidate))
    if refusal is not None:
        return                       # the cursor says "no"; nothing happens
    if all(dock._find_parent(tree, node) is target for node in roots):
        return                       # already under the target
    rehang_under_known_parent(dock, tree, roots, target, ask_single=False)
