# gui/docks/trees_anchor_read.py
"""The GUI half of «Read current position» on a TREE ANCHOR — the wiring only.

The arithmetic is `kicadstamp.tree_anchor_read` (Qt-free, celled without a
board); this module gathers what it needs and writes the answer into the form. It
lives here because `gui/docks/trees_dock.py` is a giant (rule 45) — the form
keeps the button and ONE call.

WHAT IS READ (and in WHICH frame): the tree laid out from its CURRENT anchor (the
predicted poses — `layout_tree_from_anchor`), then the live pose of every node
that votes, ABSOLUTELY — `trees_base_pose.child_live_pose`, the very reader the
offset read subtracts a base from, so the two cannot resolve a node differently.
No anchor of an embedded tree is resolved: an embedded tree's own anchor is not
used by the layout, may not resolve, and must not affect the answer. The tree's
OWN anchor is resolved once, for its ANGLE only — the shift is stored in that
frame and `board_offset_to_local_mm` needs it.

WHAT VOTES (§1 of 16.11, Denis's `main` = `mh` + three modules): a top-level
`placement`/`component` node, AND the same kinds of node inside a tree that a
top-level `module` node embeds, recursively, labelled `module/ref`. Children,
`mount`, `copper`, `point` and `external` never vote.

WHAT IS WRITTEN: nothing but the form's Shift X/Y fields — through the form's own
`_on_shift_edited`, so the CONFIG-frame value and the board-frame display are
converted by the existing rule (the 9887468 trap: never a cached base). Apply/Save
stay the user's own actions, and nothing is written to the board at all.

ONE kind of answer (`AnchorReadOutcome`), never a `(outcome, refusal)` pair where
both halves can be None: 16.11 a click on an embedded tree's form answered
`(None, None)` and the caller read `.ok` off nothing — the crash is gone by
construction, not by a guard.
"""
from __future__ import annotations

import logging
from typing import Optional

from kicadstamp.i18n import _
from kicadstamp.tree_anchor_read import (AnchorReadOutcome, VotingPose,
                                         read_anchor_shift)
from kicadstamp.tree_position import layout_tree_from_anchor, local_offset_to_board_mm
from kicadstamp.utils.units import MM

from ..connection import ui_thread_board_read
from .trees_embedding import embedding_marker

logger = logging.getLogger(__name__)

#: The kinds whose node places itself from the tree anchor — the only voters (a
#: child is relative to its parent, a mount hangs from a LIVE role).
VOTING_KINDS = ("placement", "component")

#: The button's label — the SAME words the node form's read uses (one action,
#: one name).
READ_LABEL = "Read current position"


def _vector_mm(vector) -> tuple[float, float]:
    return (vector.x / MM, vector.y / MM)


def _label(chain, ref) -> str:
    return "/".join(list(chain) + [str(ref)])


def collect_voters(tree, forest) -> tuple[list, Optional[str]]:
    """([(label, node)], refusal | None) — every node that votes, by PATH.

    A top-level `placement`/`component` votes; a top-level `module` votes by its
    CONTENTS — the top-level `placement`/`component` nodes of the tree it
    references, recursively through nested modules, each labelled `fpga/ref` so a
    refusal names the module it lives in.

    Two refusals of its own, both instead of a silent wrong answer:
    * a `module` node that references NO tree of this config — its content cannot
      be laid out or read, and answering "the shift is unchanged" while a whole
      module went unmeasured is exactly the trap this button exists for;
    * two voting nodes with the SAME ref. A record's ref is unique across the
      whole config, but a `component` node's ref is a LOCAL name (a local kind),
      so it can repeat between an embedded tree and its host — and the layout's
      pose map is keyed by ref, so the two poses could not be told apart."""
    voters: list = []
    seen: dict = {}

    def walk(cur, chain, stack):
        for node in cur.nodes:
            label = _label(chain, node.ref)
            if node.kind in VOTING_KINDS:
                first = seen.get(node.ref)
                if first is not None:
                    return _(
                        "tree anchor: two nodes named {ref!r} may vote ({a} and {b}) — "
                        "a local name is not unique across embedded trees, so their "
                        "live poses cannot be told apart").format(
                            ref=node.ref, a=first, b=label)
                seen[node.ref] = label
                voters.append((label, node))
            elif node.kind == "module":
                child = forest.get(node.ref)
                if child is None:
                    return _(
                        "tree anchor: module node {ref!r} references no tree of this "
                        "config — its content cannot be read, so no shift can be "
                        "derived").format(ref=node.ref)
                if child.name in stack:
                    continue                      # link_trees refuses cycles anyway
                refusal = walk(child, chain + [str(node.ref)], stack + [child.name])
                if refusal is not None:
                    return refusal
        return None

    refusal = walk(tree, [], [tree.name])
    return voters, refusal


def read_tree_anchor(form) -> AnchorReadOutcome:
    """The whole read: refusals first, then one layout, then one live pose per
    voter, then the arithmetic. ALWAYS one `AnchorReadOutcome`.

    Called INSIDE `ui_thread_board_read(...)`: every board access below happens
    right here, once, on the caller's sign."""
    from .trees_base_pose import child_live_pose
    from .trees_dock import _anchor_base_live_position

    dock = getattr(form, "_dock", None)
    tree = getattr(form, "_tree", None)
    if tree is None or dock is None:
        return AnchorReadOutcome(refusal=_(
            "Read current position needs a saved tree and its dock."))
    # The two facts about the TREE come before any board access: both are true
    # whether or not a board is connected, and they are the reason the button is
    # off / the read cannot mean anything.
    anchor = tree.anchor
    if anchor is not None and anchor.is_self:
        return AnchorReadOutcome(refusal=_(
            "This anchor is a live (self) anchor — it already follows the board, so "
            "there is no shift to read."))
    trees = list(getattr(dock, "_trees", []) or [])
    marker = embedding_marker(trees, tree)
    if marker is not None:
        owner, marker_ref = marker
        # The button's OWN words: the Redraw line ("…place it through that module,
        # not from this tab") was logged by the wrong action on 16.11 18:13.
        return AnchorReadOutcome(refusal=_(
            "Read current position: tree {tree!r} is placed by module {marker!r} in "
            "tree {owner!r} — its own anchor is not used; read on {owner!r}").format(
                tree=tree.name, marker=marker_ref, owner=owner))
    adapter = form._live_adapter()
    if adapter is None:
        return AnchorReadOutcome(refusal=_(
            "Not connected — the tree anchor cannot be read."))

    cfg = getattr(dock, "_cfg", None)
    sheet_names = dict(getattr(form, "_sheet_names", {}) or {})
    snapshot = form._live_snapshot()
    forest = {t.name: t for t in trees}
    predicted = layout_tree_from_anchor(tree, forest, adapter=adapter, cfg=cfg,
                                       sheet_names=sheet_names, snapshot=snapshot)
    voters, refusal = collect_voters(tree, forest)
    if refusal is not None:
        return AnchorReadOutcome(refusal=refusal)

    votes: list = []
    for label, node in voters:
        pose = predicted.get(node.ref)
        if pose is None:
            # Laid out nowhere (the layout skipped it for a reason of its own, a
            # mount base that did not resolve among them). Silence here would let
            # the remaining voters answer "the shift is unchanged" while a node of
            # this tree went unmeasured — the §1 remark on df8d1442.
            return AnchorReadOutcome(refusal=_(
                "tree anchor: node {ref!r} is not in the layout — its pose cannot be "
                "predicted, so no shift can be derived").format(ref=label))
        address = node.anchor if node.kind == "component" else None
        try:
            live_pos, live_rot, mirrored = child_live_pose(
                cfg, adapter, sheet_names, node.ref, node.kind,
                component_address=address, snapshot=snapshot)
        except Exception as exc:        # noqa: BLE001 — reported, never fatal here
            votes.append(VotingPose(ref=label, predicted_mm=_vector_mm(pose[0]),
                                    predicted_rot_deg=pose[1], error=str(exc)))
            continue
        if mirrored:
            votes.append(VotingPose(
                ref=label, predicted_mm=_vector_mm(pose[0]),
                predicted_rot_deg=pose[1],
                error=_("this node's instance is MIRRORED on the live board")))
            continue
        votes.append(VotingPose(
            ref=label,
            predicted_mm=_vector_mm(pose[0]),
            predicted_rot_deg=pose[1],
            live_mm=_vector_mm(live_pos),
            live_rot_deg=live_rot))

    # The shift lives in the base frame BEFORE the tree turns: its conversion
    # angle is THIS tree's own anchor angle. No other anchor is resolved — the
    # content is read absolutely (see the module docstring).
    _pos, anchor_rot = _anchor_base_live_position(adapter, cfg, tree, sheet_names,
                                                 snapshot=snapshot)
    return read_anchor_shift(votes, anchor_rot_deg=anchor_rot or 0.0,
                             old_shift=form._stored_anchor_shift)


def apply_outcome(form, outcome: AnchorReadOutcome) -> bool:
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
    tooltip: a self anchor is live already, a tree placed by a module must be read
    on the module's tree, and a tree that does not exist yet has no anchor."""
    button = getattr(form, "read_anchor_button", None)
    if button is None:
        return
    tree = getattr(form, "_tree", None)
    anchor = getattr(tree, "anchor", None) if tree is not None else None
    reason = ""
    if tree is None:
        reason = _("Create the tree first.")
    elif anchor is not None and anchor.is_self:
        reason = _("This is a live (self) anchor — it already follows the board, so "
                   "there is no shift to read.")
    else:
        dock = getattr(form, "_dock", None)
        marker = embedding_marker(list(getattr(dock, "_trees", []) or []), tree)
        if marker is not None:
            owner, marker_ref = marker
            reason = _(
                "This tree is placed by module {marker!r} in tree {owner!r} — read "
                "its anchor on {owner!r}.").format(marker=marker_ref, owner=owner)
    button.setEnabled(not reason)
    button.setToolTip(reason or _(
        "Read where the tree's top-level components really stand and correct the "
        "anchor's Shift X/Y — node offsets, the anchor point and the tree's angle "
        "are not touched."))


def read_and_apply(form) -> bool:
    """The button's body: read, then write the form fields. False when refused.
    ONE signed read: the layout and every voter's live pose happen inside
    `read_tree_anchor`, under this single sign."""
    try:
        with ui_thread_board_read(
                reason="read the tree anchor: one layout from the current anchor "
                       "plus one live pose per voting node"):
            outcome = read_tree_anchor(form)
    except Exception as exc:              # noqa: BLE001 — one Log line, never a crash
        logger.warning("  " + _("Read current position failed: {error}").format(
            error=exc))
        return False
    return apply_outcome(form, outcome)
