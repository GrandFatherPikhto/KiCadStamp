#!/usr/bin/env python3
"""Guardians of the pure decision layer of Tools -> "Extract spoke..."
(stage 5 of the spoke work, plan_2026_09_17_spoke_s5_extract_spoke.md §4-§5).

No KiCad, no Qt, no adapter: kicadstamp/spoke_extraction.py takes plain records,
mm floats and already-built component pools, so every refusal and every verdict
is pinned here on data. The board-reading half of the stage (the dialog's worker)
is tested separately, in tests/gui.

Each test names the guardian and the mutation it kills (С / М of the plan's §5
table) — the table in the stage report is built from these names.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME    # noqa: E402
from kicadstamp.exceptions import ValidationError                       # noqa: E402
from kicadstamp.placement.services.component_pool import ComponentPool  # noqa: E402
from kicadstamp.spoke_extraction import (                               # noqa: E402
    OUTCOME_EXHAUSTED,
    OUTCOME_MATCH,
    OUTCOME_OTHER_PAIR,
    PLANE_NET_MEMBERS,
    OrderedPool,
    PadPoint,
    candidate_cells,
    chain_assignment,
    check_spoke_selection,
    find_pair_owner,
    nearest_pad,
    pads_by_distance,
    pool_capacity,
    pool_outcome,
    spoke_criterion,
    spoke_offset,
    split_plane_nets,
)

CLUSTER = "MCU_PWR_BANK"
BULK = "C_OUT_BULK"
BYPASS = "C_OUT_BYPASS"


# ── stubs ──────────────────────────────────────────────────────────────────

def sel(ref, role, cluster=CLUSTER):
    """One selected component in explore.Selected's shape."""
    return SimpleNamespace(ref=ref, role=role, cluster=cluster)


class _Cell:
    """A cell in the only shape this layer reads: its component roles."""

    def __init__(self, *roles):
        self.components = [SimpleNamespace(role=r) for r in roles]


class _FakePool:
    """ComponentPool's contract — pop(role, pad) + remaining_count(role) — on a
    plain list of refs, so the consumption ORDER is visible in a test."""

    def __init__(self, refs_by_role):
        self._left = {str(role): [str(r) for r in refs]
                      for role, refs in refs_by_role.items()}

    def pop(self, role, spoke_pad):
        left = self._left.get(role)
        if left is None:
            raise ValidationError(f"pool does not know role {role!r}")
        if not left:
            raise ValidationError(f"pool exhausted for role {role!r} (pad {spoke_pad})")
        return left.pop(0)

    def remaining_count(self, role):
        return len(self._left.get(role, []))


def pair(bulk, bypass):
    return {BULK: bulk, BYPASS: bypass}


# ── С1/М1 — the selection refusals ─────────────────────────────────────────

class TestCheckSpokeSelection:
    def test_c1_role_selected_twice_is_refused(self):
        """С1/М1: two components with the SAME Role say nothing about which one
        is the spoke's — refused, with both refdes named."""
        selection, problems = check_spoke_selection(
            [sel("C41", BULK), sel("C42", BULK)])
        assert selection is None
        assert any(BULK in p and "twice" in p for p in problems), problems

    def test_c1_component_without_role_is_refused(self):
        selection, problems = check_spoke_selection(
            [sel("C41", ""), sel("C42", BYPASS)])
        assert selection is None
        assert any("C41" in p for p in problems), problems

    def test_c1_several_clusters_are_refused(self):
        selection, problems = check_spoke_selection(
            [sel("C41", BULK), sel("C42", BYPASS, cluster="FPGA_PWR_BANK")])
        assert selection is None
        assert any("several clusters" in p for p in problems), problems

    def test_c1_nothing_selected_is_refused(self):
        selection, problems = check_spoke_selection([])
        assert selection is None and problems

    def test_one_pair_is_accepted_as_the_identification(self):
        selection, problems = check_spoke_selection(
            [sel("C41", BULK), sel("C42", BYPASS)])
        assert problems == []
        assert selection.cluster == CLUSTER
        assert selection.refs == ("C41", "C42")
        assert selection.role_to_ref == {BULK: "C41", BYPASS: "C42"}


# ── С2/М2 — the spoke criterion ────────────────────────────────────────────

class TestSpokeCriterion:
    def test_c2_roles_unique_in_the_cluster_are_no_spoke(self):
        """С2/М2: the criterion is the CLUSTER's multiplicity — all ones means
        an ordinary cluster, and the dialog must refuse to call it a spoke."""
        assert spoke_criterion({BULK: 1, BYPASS: 1}) == {}

    def test_c2_a_repeated_role_is_the_evidence(self):
        assert spoke_criterion({BULK: 4, BYPASS: 4}) == {BULK: 4, BYPASS: 4}

    def test_c2_mixed_counts_report_only_the_repeated_ones(self):
        assert spoke_criterion({BULK: 2, BYPASS: 1}) == {BULK: 2}


# ── candidates (Р2.3) ──────────────────────────────────────────────────────

class TestCandidateCells:
    def test_cells_with_exactly_this_role_set(self):
        cfg = SimpleNamespace(cells={
            "mcu_pair": _Cell(BULK, BYPASS),
            "mcu_bypass_only": _Cell(BYPASS),
            "fpga_pair": _Cell("C_FPGA_BULK", "C_FPGA_BYPASS"),
        })
        assert candidate_cells(cfg, {BULK, BYPASS}) == ["mcu_pair"]

    def test_no_candidate_when_no_cell_matches(self):
        cfg = SimpleNamespace(cells={"other": _Cell("X")})
        assert candidate_cells(cfg, {BULK, BYPASS}) == []


# ── С3/М3 — the plane rule ─────────────────────────────────────────────────

class TestPlaneNets:
    def test_c3_gnd_like_net_is_dropped(self):
        """С3/М3: a net with more members than the threshold is a plane, never a
        spoke rail — and the count is kept so the dialog can say why."""
        kept, dropped = split_plane_nets([("+3V3_VDD", 3), ("GND", 236)])
        assert kept == ("+3V3_VDD",)
        assert dropped == (("GND", 236),)

    def test_c3_threshold_is_the_probe_one(self):
        assert PLANE_NET_MEMBERS == 40
        kept, dropped = split_plane_nets([("N", PLANE_NET_MEMBERS),
                                          ("M", PLANE_NET_MEMBERS + 1)])
        assert kept == ("N",) and dropped == (("M", PLANE_NET_MEMBERS + 1),)


# ── С4/М4 — the nearest pad ────────────────────────────────────────────────

class TestNearestPad:
    def test_c4_nearest_pad_wins_not_the_first_one(self):
        """С4/М4: the anchor's pads are listed in board order, and picking the
        first one puts the spoke on the wrong pin."""
        pads = [PadPoint(pad="48", x_mm=30.0, y_mm=0.0),      # first, far away
                PadPoint(pad="12", x_mm=1.0, y_mm=0.5),       # nearest
                PadPoint(pad="7", x_mm=5.0, y_mm=0.0)]
        assert nearest_pad(pads, (0.0, 0.0)).pad == "12"

    def test_c4_order_is_by_distance_and_stable(self):
        pads = [PadPoint(pad="a", x_mm=2.0, y_mm=0.0),
                PadPoint(pad="b", x_mm=1.0, y_mm=0.0),
                PadPoint(pad="c", x_mm=1.0, y_mm=0.0)]
        assert [p.pad for p in pads_by_distance(pads, (0.0, 0.0))] == ["b", "c", "a"]

    def test_c4_no_pads_no_choice(self):
        assert nearest_pad([], (0.0, 0.0)) is None


# ── С5/М5 — the stored shift ───────────────────────────────────────────────

class TestSpokeOffset:
    def test_c5_shift_is_frame_minus_pad(self):
        offset = spoke_offset((10.0, -2.0), (7.5, 1.0), 0.0)
        assert offset.shift_x_mm == pytest.approx(2.5)
        assert offset.shift_y_mm == pytest.approx(-3.0)
        assert offset.rotation_deg == pytest.approx(0.0)

    def test_c5_anchor_rotation_does_not_rotate_the_shift(self):
        """С5/М5: `chains:` stores ABSOLUTE mm shifts and a spoke has no parent
        frame, so a 90° frame must not rotate the shift vector — rotating it
        would move the pair by the anchor's angle."""
        offset = spoke_offset((0.0, 5.0), (0.0, 1.0), 90.0)
        assert offset.shift_x_mm == pytest.approx(0.0)
        assert offset.shift_y_mm == pytest.approx(4.0)     # NOT (−4, 0)
        assert offset.rotation_deg == pytest.approx(90.0)


# ── С6/М6 + Ф6 — the pool consumption ──────────────────────────────────────

class TestChainAssignment:
    def test_f6_consumption_mirrors_compute_raw_positions(self):
        """Ф6: one pop per CONSUMING spoke, in chain order — a retired spoke, a
        spoke whose cell is missing and a spoke whose pad the anchor lacks take
        nothing (asserted by the exact refs each pad ends up with)."""
        cells = {"pair": _Cell(BULK, BYPASS)}
        pool = _FakePool({BULK: ["C41", "C43"], BYPASS: ["C42", "C44"]})
        spokes = [{"pad": "1", "cell": "pair", "cluster": CLUSTER},
                  {"pad": "2", "cell": "pair", "cluster": CLUSTER, "retired": True},
                  {"pad": "3", "cell": "gone", "cluster": CLUSTER},
                  {"pad": "99", "cell": "pair", "cluster": CLUSTER},
                  {"pad": "48", "cell": "pair", "cluster": CLUSTER}]
        assignment, problem = chain_assignment(
            spokes, cells, {CLUSTER: pool}, {"1", "48"})
        assert problem is None
        assert assignment == {"1": pair("C41", "C42"),
                              "48": pair("C43", "C44")}

    def test_c6_a_spoke_appended_last_gets_the_leftover_pair(self):
        """С6/М6: the pool is popped in chain order, so a spoke APPENDED at the
        end gets what is left (C43, C44 here) — not the selected pair. Dropping
        the new spoke from the simulation (М6) would leave its pad unfilled and
        turn the verdict into a false "pool exhausted"."""
        cells = {"pair": _Cell(BULK, BYPASS)}
        pool = _FakePool({BULK: ["C41", "C43"], BYPASS: ["C42", "C44"]})
        spokes = [{"pad": "1", "cell": "pair", "cluster": CLUSTER},
                  {"pad": "48", "cell": "pair", "cluster": CLUSTER}]   # appended
        assignment, _problem = chain_assignment(
            spokes, cells, {CLUSTER: pool}, {"1", "48"})
        assert assignment["48"] == pair("C43", "C44")
        outcome = pool_outcome(assignment, "48", {"C41", "C42"},
                               owner=("MCU Vdd", "1"),
                               cluster=CLUSTER, total_pairs=2, consumers=2)
        assert outcome.kind == OUTCOME_OTHER_PAIR

    def test_replacing_a_spoke_keeps_its_position_in_the_chain(self):
        """A replacement is written at the SAME index, so the spokes before it
        keep their pairs — only the ones after it shift."""
        cells = {"pair": _Cell(BULK, BYPASS)}
        pool = _FakePool({BULK: ["C41", "C43"], BYPASS: ["C42", "C44"]})
        spokes = [{"pad": "1", "cell": "pair", "cluster": CLUSTER},
                  {"pad": "48", "cell": "pair", "cluster": CLUSTER}]
        assignment, _problem = chain_assignment(
            spokes, cells, {CLUSTER: pool}, {"1", "48"})
        assert assignment["1"] == pair("C41", "C42")

    def test_exhausted_pool_is_reported_not_guessed(self):
        cells = {"pair": _Cell(BULK, BYPASS)}
        pool = _FakePool({BULK: ["C41"], BYPASS: ["C42"]})
        spokes = [{"pad": "1", "cell": "pair", "cluster": CLUSTER},
                  {"pad": "48", "cell": "pair", "cluster": CLUSTER}]
        assignment, problem = chain_assignment(
            spokes, cells, {CLUSTER: pool}, {"1", "48"})
        assert "48" not in assignment
        assert problem and "exhausted" in problem

    def test_capacity_is_the_narrowest_role(self):
        pool = _FakePool({BULK: ["C41", "C43"], BYPASS: ["C42"]})
        assert pool_capacity(pool, [BULK, BYPASS]) == 1


# ── С10/М10, С11/М11 — what the pool gives the spoke (Ф8/Р3) ────────────────

class TestPoolOutcome:
    def test_f8_1_the_pair_matches_and_ok_is_free(self):
        assignment = {"48": pair("C41", "C42")}
        outcome = pool_outcome(assignment, "48", {"C41", "C42"}, cluster=CLUSTER,
                               total_pairs=4, consumers=1)
        assert outcome.kind == OUTCOME_MATCH
        assert outcome.ok is True and outcome.needs_swap_confirm is False
        assert "C41, C42" in outcome.message
        assert "NOT the selected pair" not in outcome.message

    def test_c10_another_pair_needs_the_swap_checkbox_and_names_the_owner(self):
        """С10/М10: the pair is somebody else's — the sentence must name BOTH
        pairs and the owning spoke (chain + pad), and OK must wait for the
        explicit "Components may swap" checkbox (М10: reporting a match)."""
        assignment = {"1": pair("C41", "C42"), "48": pair("C43", "C44")}
        outcome = pool_outcome(assignment, "48", {"C41", "C42"}, chain_name="MCU Vdd",
                               owner=("MCU Vdd", "1"),
                               cluster=CLUSTER, total_pairs=4, consumers=2)
        assert outcome.kind == OUTCOME_OTHER_PAIR
        assert outcome.ok is False and outcome.needs_swap_confirm is True
        assert "C43, C44" in outcome.message        # what the spoke WILL get
        assert "C41, C42" in outcome.message        # the selected pair
        assert "NOT the selected pair" in outcome.message
        assert "MCU Vdd" in outcome.message and "pad 1" in outcome.message
        assert outcome.owner_chain == "MCU Vdd" and outcome.owner_pad == "1"

    def test_c10_owner_is_found_across_chains(self):
        """Ф8.2's "why": the owner may sit in ANOTHER chain on the same net, and
        it is still named."""
        current = [("MCU Vdd", {"1": pair("C41", "C42")}),
                   ("MCU Vdd 2", {"9": pair("C43", "C44")})]
        assert find_pair_owner(current, {"C41", "C42"}) == ("MCU Vdd", "1")
        owner = find_pair_owner(current, {"C43", "C44"})
        assignment = {"48": pair("C45", "C46")}
        outcome = pool_outcome(assignment, "48", {"C43", "C44"},
                               chain_name="MCU Vdd", owner=owner,
                               cluster=CLUSTER, total_pairs=4, consumers=2)
        assert outcome.owner_chain == "MCU Vdd 2" and outcome.owner_pad == "9"
        assert "MCU Vdd 2" in outcome.message

    def test_c11_exhausted_pool_refuses_ok_and_no_checkbox_helps(self):
        """С11/М11: a spoke the pool cannot fill must not be written at all —
        the message carries the numbers, and there is no way to confirm it."""
        outcome = pool_outcome({}, "48", {"C41", "C42"}, cluster=CLUSTER,
                               total_pairs=4, consumers=4)
        assert outcome.kind == OUTCOME_EXHAUSTED
        assert outcome.ok is False and outcome.needs_swap_confirm is False
        assert "No free pair left" in outcome.message
        assert "4 pairs, 4 spokes" in outcome.message
        assert CLUSTER in outcome.message

    def test_no_owner_known_is_still_not_a_match(self):
        assignment = {"48": pair("C43", "C44")}
        outcome = pool_outcome(assignment, "48", {"C41", "C42"}, cluster=CLUSTER,
                               total_pairs=4, consumers=2)
        assert outcome.kind == OUTCOME_OTHER_PAIR
        assert outcome.needs_swap_confirm is True
        assert outcome.owner_chain is None


# ── Х3 — OrderedPool's parity with a REAL ComponentPool ─────────────────────
# OrderedPool's own docstring and gui/docks/extract_spoke.py's header both
# promise "a parity guard pins the replay against a real ComponentPool" — this
# is that guard. Refs are deliberately SCRAMBLED so lexicographic and natural
# order disagree: C2 < C9 < C10, while as strings 'C10' < 'C2'.

NET = "+3V3_VDD"
CONSUMPTION = [(BULK, "1"), (BYPASS, "1"), (BULK, "2"), (BYPASS, "2"),
               (BULK, "3"), (BYPASS, "3")]

# (ref, role, cluster, net) in board order — two decoys included:
# C7 has the right role on the WRONG net, C5 the right role in the WRONG cluster.
_POOL_BOARD = [
    ("C10", BULK, CLUSTER, NET), ("C2", BULK, CLUSTER, NET),
    ("C9", BULK, CLUSTER, NET), ("C8", BYPASS, CLUSTER, NET),
    ("C1", BYPASS, CLUSTER, NET), ("C3", BYPASS, CLUSTER, NET),
    ("C7", BULK, CLUSTER, "GND"), ("C5", BULK, "OTHER_PWR", NET),
]


class _PoolBoard:
    """The tiny board surface ComponentPool reads — footprints, Role/Cluster
    fields, pad nets — enough to build a REAL pool without KiCad."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._fps = [SimpleNamespace(ref=row[0]) for row in self._rows]

    def get_footprints(self):
        return list(self._fps)

    def _row_for(self, fp):
        return next(row for row in self._rows if row[0] == fp.ref)

    def get_field_value(self, fp, field):
        row = self._row_for(fp)
        return {ROLE_FIELD_NAME: row[1], CLUSTER_FIELD_NAME: row[2]}.get(field)

    def get_footprint_pads(self, fp):
        return [SimpleNamespace(number="1", net_name=self._row_for(fp)[3])]


class TestOrderedPoolParity:
    def test_c7_the_replay_reproduces_the_real_pool_exactly(self):
        """Х3/М7: OrderedPool is the UI-thread replay of a pool read on the
        worker — its pop() sequence must be IDENTICAL to ComponentPool's on the
        same roles/pads, or every "which pair will this spoke get" verdict the
        dialog shows is fiction."""
        drained = ComponentPool(_PoolBoard(_POOL_BOARD), NET, [BULK, BYPASS],
                                CLUSTER)
        order = {role: [drained.pop(role, "?")
                        for _ in range(drained.remaining_count(role))]
                 for role in (BULK, BYPASS)}
        replay = OrderedPool(order, net_name=NET)

        real = ComponentPool(_PoolBoard(_POOL_BOARD), NET, [BULK, BYPASS],
                             CLUSTER)
        assert ([real.pop(role, pad) for role, pad in CONSUMPTION]
                == [replay.pop(role, pad) for role, pad in CONSUMPTION])

    def test_c7_the_order_is_natural_not_lexicographic(self):
        """Ф6/М7б: the real pool sorts NATURALLY (C2 < C9 < C10). Without this
        assertion a replay built from an unsorted order would look
        self-consistent while both disagreed with the config the Ф8 verdict
        rests on."""
        pool = ComponentPool(_PoolBoard(_POOL_BOARD), NET, [BULK, BYPASS],
                             CLUSTER)
        bulk = [pool.pop(BULK, "?") for _ in range(pool.remaining_count(BULK))]
        assert bulk == ["C2", "C9", "C10"]
