# gui/docks/trees_mount_rebase.py
"""Ч2 of plan_2026_10_14_tree_group_move_and_mount_rebase — "Change anchor from
selection…" on a `mount` node of the trees dock.

WHAT IT DOES. The context menu of a `mount` node offers one new action: take the
anchor (role + cluster + sheet, and the pad when a pad was picked) from the board
SELECTION — EXACTLY one footprint or exactly one pad — and re-seat the node on
it. Denis, 14.10: "трудно переназначать точки маунтов у нод дерева. Удалить,
снова создать…" — this is that, without the delete.

The question the action asks is what happens to the group hanging under the node:

  * "Group stays in place" — the mount keeps its absolute BOARD pose, so its
    stored numbers change and its place does not. BOTH the position and the angle
    are part of that pose: the new support's body may stand at another angle, and
    the mount's axes are its anchor's angle plus its own `rotation`;
  * "Group moves to the new anchor" — the group TRAVELS with the anchor without
    turning: the stored numbers keep meaning the same board-frame vector from the
    anchor, so only the anchor's ANGLE enters the arithmetic.

WHY THE OLD ANCHOR MATTERS. "Stays in place" needs the place the group is at
NOW, and that place is the OLD anchor's pose. When the old support is gone from
the board — the very case the action exists for — there is nothing to hold the
group still WITH, so the option is not offered at all: the numbers stay as they
are, the node's axes come from the new anchor (said in the Log, because that is a
real change nobody sees in the numbers) and every DIRECT child is re-read from
the board relative to the node's new pose (Д1 of the Ч1 acceptance). A child that
cannot be read (a `module`/`copper` node, an unplaced record) withholds that
option too: a PARTLY re-read group must never exist.

WHERE THE READS LIVE. Every board read is the dock's own, reached through the
module attribute — `dock._live_adapter()`, `trees_dock.ui_thread_board_read`,
`trees_dock._mount_anchor_pose` (the node's OWN ANCHOR frame, and its POINT frame
from the same read), `trees_dock._resolve_live_offset` (a child, in the POINT
frame) — exactly like `trees_move_flow`'s flow bodies and for the same reason
(Д3 of the Ч0 review): the door guards patch those names BY MODULE ATTRIBUTE, so
a smuggled `from … import` would be invisible to them and a broken one would
never be caught.

WHICH FRAME IS WHICH (the 2026-10-14 доделка). The node's `xy`/`rotation` are
stored against its ANCHOR; its CHILDREN are stored against its POINT (anchor +
own xy/rotation) — the frame the redraw composes them from. The dock answers both
from one read, which is why this module never composes the point itself.

WHAT IS *NOT* HERE: the offset arithmetic (ONE expression of it lives in
`trees_move_flow.reexpress_offset`, this module only names the two bases), the
anchor RESOLUTION rules (the loader's own `mount_node_base` ->
`resolve_internal_mount_match` answers ambiguity, module crossing and cycles, with
its own words) and the selection classification
(`gui.entity.anchor_workers.classify_selection_anchor`). NOTHING is written to
the board (Ч2 п.4): the board is only read; a changed config reaches it through
Redraw.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtWidgets import QInputDialog

from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.sheet_names import resolve_sheet_path_names, sheet_of_names
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

from ..entity.anchor_workers import classify_selection_anchor
from .trees_move_flow import local_polar_from_xy, reexpress_offset

logger = logging.getLogger(__name__)

# ── What the user is told ──────────────────────────────────────────────────
#
# Ч2 п.1: a selection this action cannot use is a Log line, never a dialog
# (rule 43) — and nothing changes. The wording names the ONE shape that works.
_SELECT_ONE = _(
    "Change anchor: select exactly one footprint or one pad on the board")

# The angle section of the Ч2 addendum: when the OLD anchor is gone the mount's
# axes follow the NEW anchor — the numbers do not show that (they are unchanged),
# so it is SAID. It is also the line that says "the group moves" means "the
# numbers stay as they are" in that one case.
_AXES_FROM_NEW_ANCHOR = _(
    "{mount}: axes taken from the new anchor ({angle}°)")

# Д1 of the Ч1 acceptance: the children were re-read off the board instead of
# being held still by arithmetic — the source has to be visible, because the
# board and the config may disagree.
_OLD_ANCHOR_GONE = _(
    "{mount}: old anchor not found on the board — children re-read from the "
    "board ({n})")

# Ч2 п.7: "stays in place" is honoured by re-reading every DIRECT child, so one
# child the board cannot answer for withholds the option — and SAYS so, with the
# reasons: a missing row the user cannot explain is exactly the silent refusal
# this project refuses.
_CHILDREN_UNREADABLE = _(
    "Change anchor: the group cannot be held still — \"Group stays in place\" "
    "is not offered ({reasons})")

# ── The door signs ─────────────────────────────────────────────────────────
#
# A sign names WHY the read stayed on the UI thread and WHAT it costs, measured
# to the leaves (deepseek.md §31; Кj/Кk of plan_2026_09_22_live_adapter_class).
# The three resolves below are the same CLASS of cost the re-hang's base resolve
# carries — diagnostics/probe_2026_09_22_rehang_offset_cost.py measured that one,
# and this action was written at the same seam rather than re-measured:
#
#   * per base resolve, the identity comes from the connection's snapshot (the
#     caller passes it), so the 332 per-footprint FIELD scans are gone;
#   * what stays is, per Entity-typed base, ONE whole-board
#     `adapter.refresh_board()` plus the full-board re-read it forces on the very
#     next line (the adapter's caches are emptied, so the first
#     `get_footprint(ref)` walks the board over IPC);
#   * and the action stays on the UI thread because the picker dialog and the
#     write happen in the same turn — deferring it to a worker would need the
#     dialog to survive the round trip, which is a second flow, not a cheaper one.
_SELECTION_SIGN = (
    "read the board selection for the mount anchor (identity and geometry of the "
    "selected footprint/pad, plus the whole-board footprint list a pad's owner is "
    "found in) — the SAME class as the re-hang's base resolve, sent from the UI "
    "thread because the dialog and the write happen in the same turn")
_OLD_ANCHOR_SIGN = (
    "resolve the OLD mount anchor's live base to hold the group still — one "
    "anchor resolve, identity from the snapshot, plus (per Entity-typed anchor) "
    "ONE whole-board adapter.refresh_board() and the full-board re-read it forces "
    "on the next line")
_NEW_ANCHOR_SIGN = (
    "resolve the NEW mount anchor's live base (and, with it, the loader's own "
    "ambiguity/cycle verdict for the new anchor) — one anchor resolve, identity "
    "from the snapshot, plus (per Entity-typed anchor) ONE whole-board "
    "adapter.refresh_board() and the whole-board re-read it forces")
_CHILD_READ_SIGN = (
    "re-read a direct child of a re-anchored mount from the board, relative to "
    "the node's NEW pose — one base resolve (its angle converts a polar child) "
    "plus the child's own record/address read")


# ── The numbers: one base replaced by another, in two ways ────────────────

def rebased_offset(xy, polar, rotation, old_base, new_base, *,
                   keep_absolute_place: bool):
    """The mount node's `(xy, polar, rotation)` after its anchor moved from
    `old_base` to `new_base` — the ONE place the two offers of Ч2 п.3 differ, and
    both go through `trees_move_flow.reexpress_offset` (one expression of the
    formula, never a second copy).

    `keep_absolute_place=True` — "group stays in place": the node keeps its
    absolute BOARD pose, so its numbers absorb the base's SHIFT as well as its
    angle. That is `reexpress_offset`'s own job, unchanged.

    `keep_absolute_place=False` — "group moves to the new anchor": the numbers keep
    meaning the same BOARD vector from the anchor, so the group travels with it
    without turning. Only the base's ANGLE enters the arithmetic — where that
    vector LANDs is the new anchor's business, not the numbers' — which is
    `reexpress_offset` asked for a base that stayed where it was and turned.

    Returns None when the node stores no offset at all (its point IS its anchor —
    nothing to re-express), the same "nothing to change" answer
    `trees_dock._reparented_offset` gives."""
    if xy is None and polar is None:
        return None
    old_pos, old_deg = old_base
    new_pos, new_deg = new_base
    if keep_absolute_place:
        return reexpress_offset(xy, polar, rotation, (old_pos, old_deg),
                                (new_pos, new_deg))
    return reexpress_offset(xy, polar, rotation, (old_pos, old_deg),
                            (old_pos, new_deg))


def _degrees(deg) -> str:
    """The angle as the Log writes it — a plain degree number, never "None": a
    base with no angle concept counts as 0.0, exactly like the form does."""
    return f"{(0.0 if deg is None else deg):g}"


# ── What the action already knows before it asks anything ─────────────────

@dataclass
class RebasePlan:
    """The answer of `_plan` — what the three branches of Ч2 work from.

    `old_base` / `new_base` — `(position_nm, rotation_deg | None)` of the poses
    the mount's OWN numbers are measured from: the ANCHOR poses. `None` for the
    OLD one is NOT an error: it is the case the action exists for (the old
    support is gone from the board), and it is why "group stays in place" may
    have to be withheld. `None` for the NEW one IS a refusal — nothing to stand
    on.

    `new_point_rot` — the rotation of the node's POINT under the NEW anchor (the
    anchor's angle plus the node's own `rotation`): the frame its CHILDREN are
    stored in, and therefore the angle the node's axes now lie in. Taken from the
    SAME read as `new_base` (`trees_dock._mount_anchor_pose`), so the children's
    frame and the angle the Log prints can never disagree with the anchor pose
    the arithmetic used.

    `children` — [(child, (xy, polar, rotation))] of the DIRECT children re-read
    against the node's new POINT pose, in document order, filled ONLY when the
    old base did not resolve. `child_failures` — [(child, reason)] of the ones
    the board could not answer for: non-empty means the children were NOT re-read
    (the caller withholds the option instead of writing half a group)."""

    old_base: Optional[tuple] = None
    new_base: Optional[tuple] = None
    old_reason: str = ""
    new_reason: str = ""
    new_point_rot: Optional[float] = None
    children: list = field(default_factory=list)
    child_failures: list = field(default_factory=list)


def _plan(dock, tree: Tree, node: TreeNode, anchor: TreeAnchor) -> RebasePlan:
    """Everything the action needs from the board, read once.

    `_mount_anchor_pose` answers BOTH frames of the node from that one read: the
    node's OWN numbers are measured against its ANCHOR (what `rebased_offset`
    re-expresses, and what a re-anchor really changes), while its CHILDREN hang
    from its POINT — the anchor composed with the node's own xy/rotation. The
    2026-10-14 доделка split the two: the dock used to hand out the anchor frame
    for the children as well, so the numbers written into them and the numbers the
    redraw read out of them lived in different frames.

    The NEW anchor is put on the node IN PLACE while it is resolved and taken off
    again on every path (including the failure one). That is not a trick: the
    resolver reaches `mount_node_base` -> `resolve_internal_mount_match`, which
    decides ambiguity, module crossing and the CYCLE from the node's ANCHOR *and*
    from where the node SITS in the tree — so the REAL node is the only object
    that can answer it. A synthetic twin has no place in the tree and the cycle
    check would sail straight past it."""
    from . import trees_dock

    sheet_names = (dock._ctx.sheet_names
                   if getattr(dock, "_ctx", None) is not None else {})
    connection = getattr(dock._main_window, "connection", None)
    snapshot = getattr(connection, "snapshot", None)
    plan = RebasePlan()
    old_anchor = node.anchor

    with trees_dock.ui_thread_board_read(reason=_OLD_ANCHOR_SIGN):
        try:
            old = trees_dock._mount_anchor_pose(
                dock._cfg, dock._live_adapter(), sheet_names, tree, node,
                snapshot=snapshot)
            plan.old_base = (old.anchor_pos, old.anchor_rot)
        except Exception as exc:    # noqa: BLE001 — the old support being GONE is the case
            plan.old_reason = str(exc)

    node.anchor = anchor
    try:
        try:
            with trees_dock.ui_thread_board_read(reason=_NEW_ANCHOR_SIGN):
                new = trees_dock._mount_anchor_pose(
                    dock._cfg, dock._live_adapter(), sheet_names, tree, node,
                    snapshot=snapshot)
            plan.new_base = (new.anchor_pos, new.anchor_rot)
            plan.new_point_rot = new.point_rot
        except Exception as exc:    # noqa: BLE001 — the refusal path
            plan.new_reason = str(exc)
        if plan.new_base is not None and plan.old_base is None:
            plan.children, plan.child_failures = _reread_children(
                dock, tree, node, plan.new_point_rot, sheet_names, snapshot)
    finally:
        node.anchor = old_anchor
    return plan


def _reread_children(dock, tree: Tree, node: TreeNode, point_rot, sheet_names,
                     snapshot):
    """`(shifts, failures)` for every DIRECT child of `node`, re-read off the
    board against the node's NEW POINT pose (`point_rot`) — the frame the
    children's numbers are stored in — the Д1 fallback of "group stays in
    place" when the OLD anchor is gone.

    A child-mount carrying an anchor of its OWN is SKIPPED: its frame is its own
    anchor, not the parent's, so a re-anchor cannot move it (Ч0 п.5), and
    re-reading it would only rewrite numbers nobody asked about. A child that
    cannot be read is NAMED, never guessed, and ONE failure is enough to give up
    on the whole set — the caller withholds the offer rather than writing a
    half-re-read group (Ч2 п.7).

    The numbers keep the child's REPRESENTATION (Л.2 of the Ч1 acceptance): a
    polar child comes back polar, through the same `local_polar_from_xy` the
    re-hang uses, never a silent switch to xy."""
    from . import trees_dock

    shifts, failures = [], []
    for child in node.children:
        if child.kind == "mount" and child.anchor is not None:
            continue
        try:
            with trees_dock.ui_thread_board_read(reason=_CHILD_READ_SIGN):
                offset, rotation = trees_dock._resolve_live_offset(
                    dock._cfg, dock._live_adapter(), sheet_names, tree, node,
                    child.ref, child.kind,
                    component_address=(child.anchor
                                       if child.kind == "component" else None),
                    snapshot=snapshot)
        except Exception as exc:    # noqa: BLE001 — a refusal is a result here
            failures.append((child, str(exc)))
            continue
        new_rot = point_rot if point_rot is not None else 0.0
        rot = rotation if rotation is not None else child.rotation
        if child.polar is None:
            shifts.append((child, (offset, None, rot)))
        else:
            shifts.append((child, (None,
                                   local_polar_from_xy(offset, new_rot), rot)))
    if failures:
        return [], failures
    return shifts, failures


# ── The selection -> anchor (Ч2 п.1/п.2) ──────────────────────────────────

def _anchor_from_selection(dock):
    """`(TreeAnchor | None, refusal)` for the board SELECTION.

    EXACTLY one footprint or exactly one pad (Ч2 п.1): two footprints are
    ambiguous about which one was meant, a track/via is not a component at all
    and an empty selection is nothing — each of them is ONE Log line with its own
    reason, and the config is not touched.

    The classification is the SHARED half
    (`gui.entity.anchor_workers.classify_selection_anchor`, Ч2-1) — a mount node
    has no cell, so nothing cell-shaped is asked; the SHEET the component stands
    on is derived from the footprint the shared half hands back, by the ONE owner
    of that derivation (`sheet_of_names`, root = "/")."""
    from . import trees_dock

    with trees_dock.ui_thread_board_read(reason=_SELECTION_SIGN):
        adapter = dock._live_adapter()
        items = list(adapter.get_selected_items() or []) if adapter is not None else []
        try:
            read = classify_selection_anchor(adapter, items, _("mount anchor"))
        except ValidationError as exc:
            return None, str(exc)
    if len(items) != 1 or read["kind"] not in ("pad", "footprint"):
        # The shared half answers about the SELECTION, not about how many rows
        # were highlighted — "exactly one" is this action's own rule.
        return None, _SELECT_ONE
    sheet_names = (dock._ctx.sheet_names
                   if getattr(dock, "_ctx", None) is not None else {})
    footprint = read["footprint"]
    sheet = (sheet_of_names(resolve_sheet_path_names(footprint, sheet_names))
             if footprint is not None else None)
    return TreeAnchor(role=read["role"], is_origin=False, anchor_sheet=sheet,
                      anchor_cluster=read["cluster"],
                      anchor_pad=read["pad"]), ""


# ── The action itself ─────────────────────────────────────────────────────

def _options(plan: RebasePlan) -> list[tuple[str, str]]:
    """The picker's rows, `[(label, mode)]`, in the order they are offered.

    "Group stays in place" is offered only when it can be HONOURED: with the old
    anchor resolved the arithmetic holds the mount — and therefore its whole
    group — still; with the old anchor gone it is honoured by re-reading every
    direct child, so ONE child the board could not answer for removes the row
    (Ч2 п.7/Д1), and the reason goes to the Log."""
    rows: list[tuple[str, str]] = []
    if plan.old_base is not None or not plan.child_failures:
        rows.append((_("Group stays in place"), "stay"))
    rows.append((_("Group moves to the new anchor"), "move"))
    return rows


def run_mount_rebase(dock, tree: Tree, node: TreeNode) -> None:
    """The body of the dock's `_mount_anchor_flow` (Ч2 п.1–п.4), taking the dock
    as its first argument — the same shape `trees_move_flow`'s flow bodies have.

    Order: the selection -> the anchor (refuse with its reason if it cannot be
    read), the NEWanchor's base (refuse with the resolver's reason), the option
    list, the picker, the write. Every refusal is a Log line and leaves the node
    and the tree exactly as they were; a `QInputDialog` is a CHOICE, never a
    warning, so rule 43 does not apply to it (Д2 of the Ч1 acceptance)."""
    if node.kind != "mount" or node.anchor is None:
        return          # the menu offers this action for a mount node alone

    anchor, refusal = _anchor_from_selection(dock)
    if anchor is None:
        logger.warning(refusal)
        return
    plan = _plan(dock, tree, node, anchor)
    if plan.new_base is None:
        # The NEW anchor is the ONE thing that must resolve: without it there is
        # nothing to stand on. The reason is the loader's own (ambiguity, a module
        # crossing, a cycle), worded by the resolver itself.
        logger.error(plan.new_reason)
        return
    if plan.child_failures:
        # Ч2 п.7/Д1: the children could not be re-read, so "Group stays in
        # place" is not in the list at all — and the reason is in the Log.
        logger.warning(_CHILDREN_UNREADABLE.format(
            reasons="; ".join("{ref}: {why}".format(ref=child.ref, why=why)
                              for child, why in plan.child_failures)))
    options = _options(plan)
    labels = [label for label, _mode in options]
    choice, ok = QInputDialog.getItem(
        dock, _("Change anchor from selection…"),
        _("What happens to the group?"), labels, 0, False)
    if not ok:
        return
    _apply(dock, node, anchor, plan, dict(options)[choice])


def _apply(dock, node: TreeNode, anchor: TreeAnchor, plan: RebasePlan,
           mode: str) -> None:
    """Write the chosen branch onto the node: the anchor always, the numbers only
    where the branch needs them. NOTHING is written to the board (Ч2 п.4) — the
    config is marked changed and the form is re-filled, and Redraw is what
    reaches the board."""
    if mode == "stay":
        if plan.old_base is not None:
            shift = rebased_offset(node.xy, node.polar, node.rotation,
                                   plan.old_base, plan.new_base,
                                   keep_absolute_place=True)
            if shift is not None:
                node.xy, node.polar, node.rotation = shift
        else:
            # Д1: the old support is gone, so there is no old place to hold the
            # mount still BY — its numbers stay as they are (its axes are the new
            # anchor's now, which is what the Log line says) and every direct
            # child is re-read against the node's new pose. The children the
            # re-read could not answer for are named; "stay" is only offered when
            # there are none.
            for child, child_shift in plan.children:
                child.xy, child.polar, child.rotation = child_shift
    elif plan.old_base is not None:
        shift = rebased_offset(node.xy, node.polar, node.rotation,
                               plan.old_base, plan.new_base,
                               keep_absolute_place=False)
        if shift is not None:
            node.xy, node.polar, node.rotation = shift
    node.anchor = anchor
    if plan.old_base is None:
        # The node's RESULTING axes — the new anchor's angle plus the node's own
        # rotation: exactly the angle its children now lie in (the point frame),
        # not the bare base angle, which is only half of the turn.
        logger.info(_AXES_FROM_NEW_ANCHOR.format(
            mount=node.ref, angle=_degrees(plan.new_point_rot)))
    if mode == "stay" and plan.old_base is None:
        logger.info(_OLD_ANCHOR_GONE.format(mount=node.ref,
                                           n=len(plan.children)))
    dock._mark_dirty()
    dock._reload_node_form()
