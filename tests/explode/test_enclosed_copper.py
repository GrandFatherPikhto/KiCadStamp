# tests/explode/test_enclosed_copper.py
"""«Select enclosed copper» at the CORE level (plan
``plan_2026_10_05_select_enclosed_copper.md``; Denis 2026-10-05; С1 rework).

What a connected piece of copper must satisfy to be taken for a cell instance:
it touches TWO DIFFERENT pads of the instance (С1-1 — a piece reaching only ONE
pad is a dead-end, «висячая дорожка»), and NO pad of any other component on the
board. A piece reaching a foreign pad is CARVED, not dropped whole
(``plan_2026_10_09_enclosed_copper_carve``, Denis 2026-10-09): the foreign-reaching
branches are cut off as dangling and the remainder is re-checked with the SAME
rule — the C3 cells below. Inside a takeable piece the hanging branches are
TRIMMED (С1-2). The board is the "Разнос" stand-in (``tests/explode/board.py``),
so the SAME shape-connectivity owner (``kicadstamp/explode_connectivity.py``) is
exercised, never a second geometry.

Numbers in ``tests/explode`` are per the PLAN, not per the file (deepseek.md §37):
the C-numbers below are «Выбора замкнутой меди», and each test's docstring names
the mutation it is meant to kill. The C2 cell was REWRITTEN by the plan's author
in С1 («тупик НЕ берётся»); C3-c was REWRITTEN the same way by
``plan_2026_10_09_enclosed_copper_carve`` (the T-branch is now carved, not dropped
whole) — these are the author's edits, not §33 bypasses.
"""
from __future__ import annotations

from kicadstamp.enclosed_copper import enclosed_copper

from tests.explode.board import ExplodeBoard, F_CU, B_CU, fp, pad, track, via


def _uuids(result) -> set:
    return {getattr(i, "uuid", None) for i in result.items}


def _copper(result) -> set:
    """The copper uuids only (the components carry ``uuid`` too)."""
    from kicadstamp.domain.board import Track, Via
    return {getattr(i, "uuid", None) for i in result.items
            if isinstance(i, (Track, Via))}


# ── C1 — the instance's components AND the piece between them ───────────────

def test_instance_components_and_the_piece_between_them_are_taken():
    """C1: both instance components are in the result, and the piece joining
    them (TWO pads) is taken. Mutation: "only copper goes into the selection"
    (the components dropped) dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0)],
        tracks=[track("t12", F_CU, 0.0, 0.0, 5.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _uuids(result) == {"u1", "u2", "t12"}
    assert result.pieces == 1
    assert result.not_taken_foreign == 0
    assert result.not_taken_dangling == 0


# ── C2 — a dead-end (ONE pad) is NOT taken; min_cell_pads=1 takes it ────────

def test_dead_end_from_one_instance_pad_is_not_taken():
    """C2 (С1-1): a stub from ONE instance pad that goes nowhere is a
    «висячая дорожка» and is NOT taken. Mutation: "the threshold is 1 again"
    dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0)],
        tracks=[track("stub", F_CU, 0.0, 0.0, 2.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _copper(result) == set()
    assert result.not_taken_dangling == 1


def test_dead_end_is_taken_when_the_threshold_is_one():
    """C2-b (С1-1): the single-pad knob — ``min_cell_pads=1`` TAKES the dead-end
    (a pure dead-end is never emptied by the pruning)."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0)],
        tracks=[track("stub", F_CU, 0.0, 0.0, 2.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints(), min_cell_pads=1)
    assert _copper(result) == {"stub"}
    assert result.not_taken_dangling == 0


# ── C3 — a foreign pad: the piece is CARVED, not dropped whole (plan
#    ``plan_2026_10_09_enclosed_copper_carve``; Denis 2026-10-09) ─────────────

def test_dead_end_to_a_foreign_pad_is_not_taken():
    """C3: a stub from ONE instance pad straight to a foreign pad is a dead-end —
    the carve empties it and an EMPTIED piece is NOT taken (so the old whole-drop
    rule and the new carve agree here). Mutation: "a foreign cluster is treated as
    own" / "an emptied carve is returned whole" keeps copper alive."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u9", "R9", "DAC", 5.0, 0.0)],
        tracks=[track("nt", F_CU, 0.0, 0.0, 5.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u9": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints()[:1])
    assert _copper(result) == set()
    assert result.not_taken_foreign == 1
    assert result.carved == 0 and result.pieces == 0 and result.pruned == 0


def test_piece_reaching_a_component_without_cluster_is_not_taken():
    """C3-b: a connector / test point carries no Cluster and is FOREIGN too.
    Mutation: "a component without a Cluster is not counted as foreign"."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("j1", "J1", None, 5.0, 0.0)],
        tracks=[track("nt", F_CU, 0.0, 0.0, 5.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "j1": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints()[:1])
    assert _copper(result) == set()
    assert result.not_taken_foreign == 1


def test_tee_to_a_foreign_pad_carves_the_branch():
    """C3-c REWRITTEN by the plan's author (``plan_2026_10_09_enclosed_copper_
    carve``, «Новое правило»): the T-branch onto a foreign pad is CUT OFF and the
    A↔B backbone IS taken — a piece reaching a foreign pad is no longer dropped
    whole. Mutation: "a foreign pad on a T-branch drops the whole piece" (carve
    off) dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0),
                    fp("u9", "R9", "DAC", 2.5, 3.0)],
        tracks=[track("cross", F_CU, 0.0, 0.0, 5.0, 0.0),
                track("stem", F_CU, 2.5, 0.0, 2.5, 3.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)],
              "u9": [pad("1", 2.5, 3.0)]})
    result = enclosed_copper(board, board.get_footprints()[:2])
    assert _copper(result) == {"cross"}
    assert result.carved == 1 and result.pieces == 1
    assert result.pruned == 1
    assert result.not_taken_foreign == 0


def test_copper_between_twins_of_the_same_cell_is_not_taken():
    """C3-d: the twin of the SAME cell on another channel is a foreign instance,
    so the copper running from Channel_0's R1 to Channel_1's R1 is not taken.
    Mutation: "another instance of the same cluster is counted as own"."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "Channel_0", 0.0, 0.0),
                    fp("u1b", "R1", "Channel_1", 5.0, 0.0)],
        tracks=[track("between", F_CU, 0.0, 0.0, 5.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u1b": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints()[:1])
    assert _copper(result) == set()
    assert result.not_taken_foreign == 1


# ── C11..C13 — the carve at the CORE (plan_2026_10_09_enclosed_copper_carve) ─

def test_a_chain_through_a_foreign_pad_is_cut_at_the_foreign_pad():
    """C11 — Denis' fact: inst A — inst B — foreign C as ONE chain. The carve cuts
    the B—C leg (its far end rests on a foreign pad, not an anchor), so only A—B
    is taken. Mutation: "the carve is not done" (A—B not selected) and "the carve
    drops the whole piece" (B—C still selected) both die here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 2.0, 0.0),
                    fp("u9", "R9", "DAC", 4.0, 0.0)],
        tracks=[track("ab", F_CU, 0.0, 0.0, 2.0, 0.0),
                track("bc", F_CU, 2.0, 0.0, 4.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 2.0, 0.0)],
              "u9": [pad("1", 4.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints()[:2])
    assert _copper(result) == {"ab"}
    assert "bc" not in _copper(result)
    assert result.carved == 1 and result.pieces == 1 and result.pruned == 1
    assert result.not_taken_foreign == 0


def test_a_foreign_pad_between_two_instance_pads_takes_nothing():
    """C12 — A — foreign F — B is the ONLY path: neither leg dangles (each is held
    up by the other and its own pad), so the prune keeps the whole chain — the
    RE-CHECK is what finds the remainder still touching F. Mutation: "the re-check
    of the remainder is disabled" lets A—F—B through and dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u9", "R9", "DAC", 2.0, 0.0),
                    fp("u2", "R2", "FPGA", 4.0, 0.0)],
        tracks=[track("af", F_CU, 0.0, 0.0, 2.0, 0.0),
                track("fb", F_CU, 2.0, 0.0, 4.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u9": [pad("1", 2.0, 0.0)],
              "u2": [pad("1", 4.0, 0.0)]})
    result = enclosed_copper(
        board, [board.get_footprints()[0], board.get_footprints()[2]])
    assert _copper(result) == set()
    assert result.not_taken_foreign == 1 and result.carved == 0
    assert result.pieces == 0 and result.pruned == 0


def test_a_carved_piece_that_empties_is_not_taken_whole():
    """C13 — the A↔B connection runs through the track BODY (pad A -> THROUGH pad
    B -> out to a foreign pad C): pruning against the instance pads only empties
    the track and ``prune_dangling``'s С2-3 leg hands it back WHOLE — yet the
    re-check re-classifies that remnant and drops it (it still touches C), so no
    copper reaching C is selected. Mutation: "the re-check's foreign filter is
    dropped" dies here (the returned remnant would then be taken)."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0),
                    fp("u9", "R9", "DAC", 5.4, 0.0)],
        tracks=[track("through", F_CU, 0.0, 0.0, 5.4, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)],
              "u9": [pad("1", 5.4, 0.0)]})
    result = enclosed_copper(board, board.get_footprints()[:2])
    assert _copper(result) == set()
    assert result.not_taken_foreign == 1 and result.carved == 0
    assert result.pieces == 0 and result.pruned == 0


# ── C4 — a piece with no pads at all is NOT taken ───────────────────────────

def test_a_piece_touching_no_instance_pad_is_not_a_candidate():
    """C4 + С2-1: a chain of stitching vias touches NO instance pad — it is not
    taken AND not counted at all (a piece that does not touch the instance is not
    the question). Mutation: "a piece without pads is taken"."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0)],
        tracks=[track("link", F_CU, 0.0, 5.0, 0.0, 6.0)],
        vias=[via("v1", 0.0, 5.0), via("v2", 0.0, 6.0)],
        pads={"u1": [pad("1", 0.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _copper(result) == set()
    assert result.pieces == 0
    assert result.not_taken_foreign == 0
    assert result.not_taken_dangling == 0


# ── C5 — connectivity is by SHAPE (5 um gap, and the end does NOT dangle) ───

def test_track_end_five_microns_from_the_pad_is_taken():
    """C5 (г): the track END is 5 µm off R1's pad EDGE and its body still
    overlaps the pad (pad half 0.25 mm, track half 0.125 mm, end at 0.255 mm),
    the other end is on R2's pad — the piece has TWO pads and the near end is
    NOT dangling. Mutation: "own touch by exact point coincidence" (a point test
    at margin 0 misses the 5 µm cell) dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0)],
        tracks=[track("near", F_CU, 0.255, 0.0, 5.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _copper(result) == {"near"}
    assert result.pruned == 0


def test_a_branch_ending_five_microns_from_a_pad_is_not_dangling():
    """C5-b (г): an R1↔R2 backbone plus a branch whose FAR end sits 5 µm off a
    third pad R3's edge — that end TOUCHES the pad (margin = half width), so the
    branch is not hanging. Mutation: "the endpoint-pad touch is exact point
    coincidence" dies here (the backbone survives, so the never-empty guard
    cannot mask it)."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0),
                    fp("u3", "R3", "FPGA", 2.5, 3.0)],
        tracks=[track("cross", F_CU, 0.0, 0.0, 5.0, 0.0),
                track("branch", F_CU, 2.5, 0.0, 2.5, 2.755)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)],
              "u3": [pad("1", 2.5, 3.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _copper(result) == {"cross", "branch"}
    assert result.pruned == 0


# ── C6..C10 — hanging branches are trimmed (С1-2) ───────────────────────────

def test_a_stub_into_nowhere_is_trimmed_off_the_piece():
    """C6 (а): R1↔R2 plus a T-stub that ends nowhere -> the piece is taken
    WITHOUT the stub. Mutation: "a hanging track is not trimmed" dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0)],
        tracks=[track("cross", F_CU, 0.0, 0.0, 5.0, 0.0),
                track("stub", F_CU, 2.5, 0.0, 2.5, 3.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _copper(result) == {"cross"}
    assert result.pruned == 1


def test_a_stub_ending_in_a_via_is_trimmed_too():
    """C7 (б): the stub ends in a via — BOTH the via and the stub track go.
    Mutation: "a one-pass trim" (no fixed point) dies here: pass one drops only
    the via, pass two then drops the exposed stub."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0)],
        tracks=[track("cross", F_CU, 0.0, 0.0, 5.0, 0.0),
                track("stub", F_CU, 2.5, 0.0, 2.5, 3.0)],
        vias=[via("vd", 2.5, 3.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _copper(result) == {"cross"}
    assert result.pruned == 2


def test_a_via_in_the_middle_of_a_branch_is_kept():
    """C8 (в): an R1↔R2 backbone, and off it a branch t1 -> via -> t2 (another
    layer) -> a third pad R3. The via sits in the MIDDLE (two neighbours) and is
    NOT trimmed. Mutation: "a middle via is trimmed" dies here — the backbone
    survives, so the never-empty guard cannot mask it."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0),
                    fp("u3", "R3", "FPGA", 2.5, 3.0)],
        tracks=[track("cross", F_CU, 0.0, 0.0, 5.0, 0.0),
                track("t1", F_CU, 2.5, 0.0, 2.5, 2.0),
                track("t2", B_CU, 2.5, 2.0, 2.5, 3.0)],
        vias=[via("vm", 2.5, 2.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)],
              "u3": [pad("1", 2.5, 3.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _copper(result) == {"cross", "t1", "t2", "vm"}
    assert result.pruned == 0


def test_a_t_junction_end_is_not_dangling():
    """C9 (д): a stem whose end lands in the MIDDLE of the cross and whose other
    end is on a third instance pad is not dangling. Mutation: "a T-junction
    counts as dangling" dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0),
                    fp("u3", "R3", "FPGA", 2.5, 3.0)],
        tracks=[track("cross", F_CU, 0.0, 0.0, 5.0, 0.0),
                track("stem", F_CU, 2.5, 0.0, 2.5, 3.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)],
              "u3": [pad("1", 2.5, 3.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _copper(result) == {"cross", "stem"}
    assert result.pruned == 0


def test_a_pad_free_ring_hung_by_one_track_survives():
    """C10 (е): a ring without pads, hung by one link, is NOT eaten — every
    element of the ring touches two neighbours, so there is no leaf. (The
    pruning rule is honestly described in ``prune_dangling``'s docstring; this
    cell pins TODAY's behaviour, it does not invent a swallow rule.)"""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0)],
        tracks=[track("cross", F_CU, 0.0, 0.0, 5.0, 0.0),
                track("link", F_CU, 2.5, 0.0, 2.5, -2.0),
                track("ab", F_CU, 2.5, -2.0, 3.5, -2.0),
                track("bc", F_CU, 3.5, -2.0, 3.5, -3.0),
                track("cd", F_CU, 3.5, -3.0, 2.5, -3.0),
                track("da", F_CU, 2.5, -3.0, 2.5, -2.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert {"ab", "bc", "cd", "da"} <= _copper(result)
    assert result.pruned == 0


# ── С2-1 — the counters are about THIS instance only ────────────────────────

def test_a_stray_piece_between_two_foreign_components_is_not_counted():
    """С2-1: a piece joining two FOREIGN components (no instance pad) is not a
    candidate and does NOT feed the counters — they are about THIS instance.
    Mutation: "the counters are the whole board again" dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0),
                    fp("u9", "R9", "DAC", 10.0, 0.0),
                    fp("u8", "R8", "DAC", 15.0, 0.0)],
        tracks=[track("cross", F_CU, 0.0, 0.0, 5.0, 0.0),
                track("stray", F_CU, 10.0, 0.0, 15.0, 0.0),
                track("inter", B_CU, 0.0, 0.0, 10.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)],
              "u9": [pad("1", 10.0, 0.0)], "u8": [pad("1", 15.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints()[:2])
    assert _copper(result) == {"cross"}
    assert result.not_taken_foreign == 1      # only the inter-cluster piece
    assert result.not_taken_dangling == 0


# ── С2-2 — the three touch primitives the С1 mutations slipped through ──────

def test_a_via_inside_an_instance_pad_holds_the_piece():
    """B1 (С2-2): the FPGA's BGA is routed via-in-pad — a via INSIDE pad A, a
    track on another layer to pad B. Nothing is trimmed, the piece is taken.
    Mutation: "a via in an instance pad is dangling" dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0)],
        tracks=[track("t", B_CU, 0.0, 0.0, 5.0, 0.0)],
        vias=[via("vin", 0.0, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _copper(result) == {"vin", "t"}
    assert result.pruned == 0


def test_a_track_end_over_a_pad_of_another_layer_is_dangling():
    """B4 (С2-2): a B.Cu branch ends right under an F.Cu SMD pad of the instance
    with no other contact at that end -> the end HANGS and the branch is trimmed.
    Mutation: "an endpoint pad is touched without its layers" dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 2.5, 3.0),
                    fp("u2", "R2", "FPGA", 0.0, 0.0),
                    fp("u3", "R3", "FPGA", 5.0, 0.0)],
        tracks=[track("cross", F_CU, 0.0, 0.0, 5.0, 0.0),
                track("branch", B_CU, 2.5, 0.0, 2.5, 3.0)],
        vias=[via("vm", 2.5, 0.0)],
        pads={"u1": [pad("1", 2.5, 3.0, copper_layers=(F_CU,))],
              "u2": [pad("1", 0.0, 0.0)], "u3": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert "cross" in _copper(result)
    assert "branch" not in _copper(result)
    assert result.pruned > 0


def test_a_track_end_over_a_track_of_another_layer_is_dangling():
    """B5 (С2-2): an F.Cu track joins the piece through a via at ONE end, and its
    OTHER end lies over the piece's B.Cu track with no via there -> that end
    HANGS and the track is trimmed. Mutation: "an end touches a track of another
    layer" dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0)],
        tracks=[track("cross", F_CU, 0.0, 0.0, 5.0, 0.0),
                track("tb", B_CU, 2.5, 0.0, 2.5, 4.0),
                track("tf", F_CU, 2.5, 4.0, 2.5, 2.0)],
        vias=[via("vm1", 2.5, 0.0), via("vm2", 2.5, 4.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert "cross" in _copper(result)
    assert "tf" not in _copper(result)
    assert result.pruned > 0


# ── С2-3 — the empty-piece guard is a REAL connection ───────────────────────

def test_a_piece_connected_by_the_track_body_through_a_pad_is_kept_whole():
    """С2-3: a track runs from pad A THROUGH pad B and sticks out past it, so its
    far end is free — yet A↔B is a real connection through the track BODY. The
    emptied piece comes back WHOLE. Mutation: "the empty-piece guard is removed"
    dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R1", "FPGA", 0.0, 0.0),
                    fp("u2", "R2", "FPGA", 5.0, 0.0)],
        tracks=[track("through", F_CU, 0.0, 0.0, 5.4, 0.0)],
        pads={"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)]})
    result = enclosed_copper(board, board.get_footprints())
    assert _copper(result) == {"through"}
    assert result.pruned == 0


# ── empty instance ──────────────────────────────────────────────────────────

def test_empty_instance_selects_nothing():
    """No components -> nothing selected and no counter moved (the caller says
    "empty instance" in its own red line)."""
    board = ExplodeBoard()
    result = enclosed_copper(board, [])
    assert result.items == []
    assert result.pieces == 0
    assert result.not_taken_foreign == 0
    assert result.not_taken_dangling == 0
    assert result.pruned == 0
