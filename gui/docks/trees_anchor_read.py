# gui/docks/trees_anchor_read.py
"""The GUI half of «Read current position» on a TREE ANCHOR — the wiring only.

The arithmetic is `kicadstamp.tree_anchor_read` (Qt-free, celled without a
board); this module gathers what it needs from the live board and writes the
answer into the form. It lives here because `gui/docks/trees_dock.py` is a giant
(rule 45) — the form keeps the button and ONE call.

WHAT IS READ: the tree laid out from its CURRENT anchor (the predicted poses —
`layout_tree_from_anchor`), then the LIVE pose of every TOP-LEVEL node that
places itself (`placement`, `component`), each through the ONE dispatcher a node
read uses (`_resolve_live_offset` with no parent = relative to the anchor), on
ONE connection snapshot.

WHAT IS WRITTEN: nothing but the form's Shift X/Y fields — through the form's own
`_on_shift_edited`, so the CONFIG-frame value and the board-frame display are
converted by the existing rule (the 9887468 trap: never a cached base). Apply/Save
stay the user's own actions, and nothing is written to the board at all.
"""
from __future__ import annotations

import logging

from kicadstamp.i18n import _
from kicadstamp.tree_anchor_read import VotingPose, read_anchor_shift
from kicadstamp.tree_position import (layout_tree_from_anchor,
                                      local_offset_to_board_mm,
                                      rotate_local_offset)
from kicadstamp.utils.units import MM

from ..connection import ui_thread_board_read
from .trees_embedding import refuse_embedded_tree

logger = logging.getLogger(__name__)

#: The kinds whose TOP-LEVEL node places itself from the tree anchor — the only
#: voters (a child is relative to its parent, a mount hangs from a LIVE role).
VOTING_KINDS = ("placement", "component")

#: The button's label — the SAME words the node form's read uses (one action,
#: one name).
READ_LABEL = "Read current position"


def _vector_mm(vector) -> tuple[float, float]:
    return (vector.x / MM, vector.y / MM)


def read_tree_anchor(form):
    """(outcome | None, refusal | None) — runs the reads and the arithmetic.

    Called INSIDE `ui_thread_board_read(...)`: every board access below happens
    right here, once, on the caller's sign."""
    from .trees_dock import _anchor_base_live_position, _resolve_live_offset

    dock = getattr(form, "_dock", None)
    tree = getattr(form, "_tree", None)
    if tree is None or dock is None:
        return None, _("Read current position needs a saved tree and its dock.")
    # The two facts about the TREE come before any board access: both are true
    # whether or not a board is connected, and they are the reason the button is
    # off / the read cannot mean anything.
    anchor = tree.anchor
    if anchor is not None and anchor.is_self:
        return None, _(
            "This anchor is a live (self) anchor — it already follows the board, "
            "so there is no shift to read.")
    if refuse_embedded_tree(getattr(dock, "_trees", []) or [], tree):
        return None, None                  # the refusal is already logged
    adapter = form._live_adapter()
    if adapter is None:
        return None, _("Not connected — the tree anchor cannot be read.")

    cfg = getattr(dock, "_cfg", None)
    sheet_names = dict(getattr(form, "_sheet_names", {}) or {})
    snapshot = form._live_snapshot()
    forest = {t.name: t for t in getattr(dock, "_trees", []) or []}
    predicted = layout_tree_from_anchor(tree, forest, adapter=adapter, cfg=cfg,
                                       sheet_names=sheet_names)
    anchor_pos, anchor_rot = _anchor_base_live_position(
        adapter, cfg, tree, sheet_names, snapshot=snapshot)

    votes: list[VotingPose] = []
    for node in tree.nodes:
        if node.kind not in VOTING_KINDS:
            continue
        pose = predicted.get(node.ref)
        if pose is None:
            continue
        address = node.anchor if node.kind == "component" else None
        try:
            (local_x, local_y), live_rot = _resolve_live_offset(
                cfg, adapter, sheet_names, tree, None, node.ref, node.kind,
                component_address=address, snapshot=snapshot)
        except Exception as exc:          # noqa: BLE001 — reported, never fatal here
            votes.append(VotingPose(ref=node.ref, predicted_mm=_vector_mm(pose[0]),
                                    predicted_rot_deg=pose[1], error=str(exc)))
            continue
        delta = rotate_local_offset(local_x, local_y, anchor_rot)
        votes.append(VotingPose(
            ref=node.ref,
            predicted_mm=_vector_mm(pose[0]),
            predicted_rot_deg=pose[1],
            live_mm=((anchor_pos.x + delta.x) / MM,
                     (anchor_pos.y + delta.y) / MM),
            live_rot_deg=live_rot))

    return read_anchor_shift(votes, anchor_rot_deg=anchor_rot,
                             old_shift=form._stored_anchor_shift), None


def apply_outcome(form, outcome) -> bool:
    """Write the outcome into the FORM: the two Shift fields, then the form's own
    handler turns them into the stored CONFIG-frame value. One Log line either
    way; the fields are never touched on a refusal."""
    if not outcome.ok:
        logger.warning("  " + str(outcome.refusal))
        return False
    base = form._conversion_base_deg()
    anchor_rot = base[1] if base else 0.0
    board_shift = local_offset_to_board_mm(outcome.shift, anchor_rot)
    before = form._stored_anchor_shift or (0.0, 0.0)
    form.shift_x_edit.setText(str(round(board_shift[0], 6)))
    form.shift_y_edit.setText(str(round(board_shift[1], 6)))
    form._on_shift_edited()
    logger.info(_(
        "tree {name!r}: shift ({x0:.3f}, {y0:.3f}) → ({x1:.3f}, {y1:.3f}) mm, "
        "read from {n} node(s)").format(
            name=form._tree.name if form._tree is not None else "?",
            x0=before[0], y0=before[1],
            x1=outcome.shift[0], y1=outcome.shift[1], n=outcome.count))
    return True


def update_button_state(form) -> None:
    """Disable the button where there is nothing to read and say WHY in its
    tooltip: a self anchor is live already, and a tree that does not exist yet has
    no anchor to correct."""
    button = getattr(form, "read_anchor_button", None)
    if button is None:
        return
    tree = getattr(form, "_tree", None)
    anchor = getattr(tree, "anchor", None) if tree is not None else None
    reason = ""
    if tree is None:
        reason = _("Create the tree first.")
    elif anchor is not None and anchor.is_self:
        reason = _("This is a live (self) anchor — it already follows the board, "
                   "so there is no shift to read.")
    button.setEnabled(not reason)
    button.setToolTip(reason or _(
        "Read where the tree's top-level components really stand and correct the "
        "anchor's Shift X/Y — node offsets, the anchor point and the tree's angle "
        "are not touched."))


def read_and_apply(form) -> bool:
    """The button's body: read, then write the form fields. False when refused.
    ONE signed read: the layout and every voting node's live pose happen inside
    `read_tree_anchor`, under this single sign."""
    try:
        with ui_thread_board_read(
                reason="read the tree anchor: one layout from the current anchor "
                       "plus one live pose per top-level node"):
            outcome, refusal = read_tree_anchor(form)
    except Exception as exc:              # noqa: BLE001 — one Log line, never a crash
        logger.warning("  " + _("Read current position failed: {error}").format(
            error=exc))
        return False
    if refusal is not None:
        logger.warning("  " + refusal)
        return False
    return apply_outcome(form, outcome)
