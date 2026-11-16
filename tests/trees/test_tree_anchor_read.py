# tests/trees/test_tree_anchor_read.py
"""Cells а, б, в, г, ж, з, и of plan_2026_11_16_tree_anchor_read_current_position
— the arithmetic of «Read current position» on a TREE ANCHOR, no board and no Qt.

The live case (Denis, 16.11): a routed board, and the Grid Origin was moved AFTER
the tree had been laid out. The anchor resolves from the new Origin, the
components still stand where the old one put them, and the next Redraw would move
the whole tree — routing included — by that shift. The cure is the anchor's own
`(shift x y)`, computed from where the VOTING nodes stand versus where the layout
puts them. `VotingPose` is the input: what the layout says and what the board
says, both absolute and in mm.
"""
from kicadstamp.tree_anchor_read import (DEG_TOLERANCE, MM_TOLERANCE, VotingPose,
                                         read_anchor_shift)


def _vote(ref, predicted, live, *, predicted_rot=0.0, live_rot=None):
    return VotingPose(ref=ref, predicted_mm=predicted,
                      predicted_rot_deg=predicted_rot, live_mm=live,
                      live_rot_deg=predicted_rot if live_rot is None else live_rot)


# ── а: the Origin moved by (3, −2) → the shift becomes (−3, +2) ──────────

def test_a_a_moved_origin_gives_the_opposite_shift():
    votes = [_vote("U1", (10.0, 10.0), (7.0, 12.0)),
             _vote("U2", (40.0, 10.0), (37.0, 12.0))]

    out = read_anchor_shift(votes, anchor_rot_deg=0.0)

    assert out.ok and out.refusal is None
    assert out.shift == (-3.0, 2.0)
    assert out.count == 2


def test_a_the_delta_is_added_to_the_shift_already_stored():
    votes = [_vote("U1", (10.0, 10.0), (7.0, 12.0))]

    out = read_anchor_shift(votes, anchor_rot_deg=0.0, old_shift=(5.0, 5.0))

    assert out.shift == (2.0, 7.0)


# ── б: a turned anchor gets the delta in ITS OWN frame ───────────────────

def test_b_a_turned_anchor_converts_the_delta_into_its_own_frame():
    votes = [_vote("U1", (10.0, 10.0), (7.0, 12.0))]

    out = read_anchor_shift(votes, anchor_rot_deg=90.0)

    # board (−3, +2) at a 90°-turned anchor is (−2, −3) in the anchor's frame —
    # the same pair the anchor form's own shift handler uses.
    assert out.shift == (-2.0, -3.0)


# ── в: one node moved separately → refusal, both deltas named ─────────────

def test_v_nodes_that_did_not_move_together_are_refused():
    votes = [_vote("U1", (10.0, 10.0), (7.0, 12.0)),
             _vote("U2", (40.0, 10.0), (39.0, 10.0))]

    out = read_anchor_shift(votes, anchor_rot_deg=0.0)

    assert not out.ok and out.shift is None
    assert "U1" in out.refusal and "U2" in out.refusal
    assert "(-3.000, 2.000)" in out.refusal      # the first delta
    assert "(-1.000, 0.000)" in out.refusal      # the odd one


def test_v_a_delta_within_the_tolerance_is_the_same_move():
    votes = [_vote("U1", (10.0, 10.0), (7.0, 12.0)),
             _vote("U2", (40.0, 10.0), (37.0 + MM_TOLERANCE / 2, 12.0))]

    out = read_anchor_shift(votes, anchor_rot_deg=0.0)

    assert out.ok


# ── г: a node was TURNED → refusal, the node and the angle named ──────────

def test_g_a_turned_node_is_refused_with_its_angle():
    votes = [_vote("U1", (10.0, 10.0), (10.0, 10.0), live_rot=5.0)]

    out = read_anchor_shift(votes, anchor_rot_deg=0.0)

    assert not out.ok and out.shift is None
    assert "U1" in out.refusal and "5" in out.refusal
    assert "turn" in out.refusal.lower()


def test_g_a_rotation_within_the_tolerance_still_reads():
    votes = [_vote("U1", (10.0, 10.0), (10.0, 10.0),
                   live_rot=DEG_TOLERANCE / 2)]

    out = read_anchor_shift(votes, anchor_rot_deg=0.0)

    assert out.ok and out.shift == (0.0, 0.0)


# ── ж: nothing to read ───────────────────────────────────────────────────

def test_zh_no_voting_node_is_refused():
    out = read_anchor_shift([], anchor_rot_deg=0.0)

    assert not out.ok and out.shift is None
    assert "nothing to read" in out.refusal


# ── з: children do not vote ──────────────────────────────────────────────

def test_z_only_the_top_level_nodes_vote():
    """The caller passes ONLY the top-level `placement`/`component` nodes — a
    child is relative to its parent and rides along, and a `mount` hangs from a
    live role, so neither can disagree with the anchor. The cell pins the
    arithmetic for that set: one vote is enough and nothing else is consulted."""
    votes = [_vote("U_TOP", (0.0, 0.0), (2.0, 3.0))]

    out = read_anchor_shift(votes, anchor_rot_deg=0.0)

    assert out.ok and out.shift == (2.0, 3.0) and out.count == 1


# ── и: a second read changes nothing ─────────────────────────────────────

def test_i_a_second_read_keeps_the_shift():
    first = read_anchor_shift([_vote("U1", (10.0, 10.0), (7.0, 12.0))],
                              anchor_rot_deg=0.0)
    assert first.shift == (-3.0, 2.0)

    # The layout moved WITH the corrected shift, so every node now sits exactly
    # where the tree predicts it.
    second = read_anchor_shift([_vote("U1", (7.0, 12.0), (7.0, 12.0))],
                               anchor_rot_deg=0.0, old_shift=first.shift)

    assert second.ok and second.shift == first.shift
