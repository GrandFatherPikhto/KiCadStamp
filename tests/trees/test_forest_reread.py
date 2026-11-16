# tests/trees/test_forest_reread.py
"""Cells а, а1–а3, а5, б–е, к of plan_2026_11_16_reread_forest_from_board, commit 1
— the DECISION half of «Reread positions from board», without a board and
without Qt.

The reads themselves (which frame, which base) belong to the caller; what is
celled here is what the plan asks to be decided: which group the anchor follows,
when the anchor is left alone, what stays out of the file, and what shape the
value is written in.
"""
from kicadstamp.forest_reread import (MODE_ABSORB, MODE_KEEP, OVERRIDE_KEEP,
                                      NodeRead, Vote, choose_anchor, group_votes,
                                      plan_forest, to_polar)


# ── а1/а2: Origin moved — the biggest group gives the new anchor shift ────

def test_a2_the_biggest_group_gives_the_anchor_delta():
    """а2: `main` 16.11 — three modules' content at (−0.01, −3.08), `mh` at
    (0, −0.09). The anchor takes the modules' delta (3 of 4 votes)."""
    groups = group_votes([Vote("mh", (0.0, -0.09)),
                          Vote("power/J1", (-0.01, -3.08)),
                          Vote("fpga/IC1", (-0.01, -3.08)),
                          Vote("mcu/U4", (-0.01, -3.08))])

    choice = choose_anchor(groups, mode=MODE_ABSORB)

    assert choice.delta == (-0.01, -3.08)
    assert "3 of 4" in choice.reason


def test_a3_two_equal_groups_leave_the_anchor_alone():
    """а3: with no strict majority the user decides which block moved — the anchor
    stays and every node is read on its own."""
    groups = group_votes([Vote("a", (1.0, 0.0)), Vote("b", (1.0, 0.0)),
                          Vote("c", (0.0, 2.0)), Vote("d", (0.0, 2.0))])

    choice = choose_anchor(groups, mode=MODE_ABSORB)

    assert choice.delta is None
    assert "disagree equally" in choice.reason


def test_a5_keep_anchors_never_touches_the_anchor():
    """а5: "blocks were moved by hand" — the anchor stays even with one big
    group, and the per-tree override says the same."""
    groups = group_votes([Vote("a", (1.0, 0.0)), Vote("b", (1.0, 0.0)),
                          Vote("c", (1.0, 0.0))])

    assert choose_anchor(groups, mode=MODE_KEEP).delta is None
    assert choose_anchor(groups, mode=MODE_ABSORB,
                         override=OVERRIDE_KEEP).delta is None


def test_votes_within_the_tolerance_are_one_group():
    """0.003 mm apart is the same move — an unmoved board must not split into
    groups and must not shift the anchor."""
    groups = group_votes([Vote("a", (1.0, 0.0)), Vote("b", (1.003, 0.0))])

    assert len(groups) == 1
    assert groups[0].size == 2


# ── а: nothing moved — nothing is written, nothing is skipped ─────────────

def test_a_a_still_board_produces_an_empty_plan():
    plan = plan_forest([
        NodeRead("main", "mh", offset_mm=(6.49, -84.99),
                 rotation_deg=0.0, stored_offset=(6.49, -84.99),
                 stored_rotation=0.0)])

    assert plan.empty


# ── а2, the node half: `mh` is read against the NEW anchor base ───────────

def test_a2_the_node_is_read_against_the_base_with_the_new_shift():
    """а2: `mh` stands 0.09 mm off its layout (Δ = (0, −0.09)), the anchor absorbs
    the modules' (−0.01, −3.08). Its live offset in the OLD base frame is
    (6.49, −85.08), so against the base with the NEW shift it is (6.50, −82.00) —
    the anchor's Δ is NOT left in the node (that is what absorbing it means)."""
    plan = plan_forest([
        NodeRead("main", "mh", offset_mm=(6.49, -85.08),
                 rotation_deg=0.0, stored_offset=(6.49, -84.99),
                 stored_rotation=0.0, absorb_mm=(-0.01, -3.08))])

    assert plan.changed == 1
    change = plan.changes[0]
    assert change.was == (6.49, -84.99)
    # float arithmetic on the millimetre grid: compare to a nanometre-ish epsilon
    assert abs(change.now[0] - 6.50) < 1e-9
    assert abs(change.now[1] - (-82.00)) < 1e-9
    assert abs(change.delta[0] - 0.01) < 1e-9
    assert abs(change.delta[1] - 2.99) < 1e-9


# ── б/в/г: which node moves is the reads' business, the plan's is to say it ─

def test_b_one_node_shifted_by_a_millimetre_is_written():
    plan = plan_forest([
        NodeRead("t", "u1", offset_mm=(1.0, 5.0), rotation_deg=0.0,
                 stored_offset=(0.0, 5.0), stored_rotation=0.0)])

    assert plan.changed == 1
    assert plan.changes[0].delta == (1.0, 0.0)


def test_k_a_three_micrometre_difference_is_not_written():
    """к: Δ = 0.003 mm is below the tolerance — the file must not get rounding
    noise."""
    plan = plan_forest([
        NodeRead("t", "u1", offset_mm=(0.003, 0.0), rotation_deg=0.0,
                 stored_offset=(0.0, 0.0), stored_rotation=0.0)])

    assert plan.empty


# ── Shape: Cartesian stays Cartesian, Polar stays Polar ──────────────────

def test_the_polar_shape_survives_the_reread():
    """Ч0 п.3: a Polar node is written back as (radius, angle) — the same numbers
    the node form shows (`atan2(-y, x)`) — never silently rewritten as xy."""
    plan = plan_forest([
        NodeRead("t", "u1", offset_mm=(0.0, 3.0), rotation_deg=0.0,
                 stored_offset=(0.0, 0.0), stored_rotation=0.0, polar=True)])

    radius, angle = plan.changes[0].now
    assert abs(radius - 3.0) < 1e-9
    assert abs(angle - (-90.0)) < 1e-9
    assert to_polar((3.0, 0.0)) == (3.0, 0.0)


# ── Skips are always spoken ──────────────────────────────────────────────

def test_j_a_node_that_could_not_be_read_is_skipped_with_its_reason():
    """ж: a non-rigid cell, a mirror, a missing part — one row with the reason,
    and the rest of the forest still reads."""
    plan = plan_forest([
        NodeRead("t", "u1", error="the instance is MIRRORED on the live board"),
        NodeRead("t", "u2", offset_mm=(1.0, 0.0), rotation_deg=0.0,
                 stored_offset=(0.0, 0.0), stored_rotation=0.0)])

    assert plan.skipped_count == 1
    assert "MIRRORED" in plan.skipped[0].reason
    assert plan.changed == 1


def test_a_node_without_a_stored_offset_is_skipped_not_invented():
    plan = plan_forest([NodeRead("t", "u1", offset_mm=(1.0, 0.0),
                                 rotation_deg=0.0, stored_offset=None)])

    assert plan.changed == 0
    assert plan.skipped_count == 1


# ── и: a turned anchor / 180° must not invent a rotation change ──────────

def test_i_a_node_at_180_degrees_gets_no_rotation_change():
    plan = plan_forest([
        NodeRead("t", "u1", offset_mm=(0.0, 0.0), rotation_deg=180.0,
                 stored_offset=(0.0, 0.0), stored_rotation=-180.0)])

    assert plan.empty
