#!/usr/bin/env python3
"""Ч1-Б of plan_2026_10_15_honest_live_read.md: "Снимок не говорит «нет»."

Fact Б (Denis, 15.10): the board was switched in KiCad while the connection's
snapshot still described the PREVIOUS one. The snapshot had no (Role, Cluster)
pair for the cluster standing in front of him, the resolver believed the cache,
and the read refused a placement that was perfectly possible — "cluster X is not
on the live board" about a board nobody had looked at.

The rule, ONE for both resolvers that take `snapshot`:
a non-empty snapshot is a CACHE of identity, never an authority. A HIT is free
and is used as before; a MISS — no candidate, or a candidate whose ref has left
the board — falls through to the live sweep, and only the live board may answer
"not there". An AMBIGUITY from whichever source produced it is an answer and is
raised at once.

Cells:
  С1  a (Role, Cluster) pair ABSENT from the snapshot but present on the live
      board resolves from the live board (resolve_footprint_by_cluster_role);
  С2  the same for a bare ROLE (resolve_footprint_by_role — `_role_candidates`);
  С3  a snapshot ref that left the board resolves to the LIVE ref of the same
      Role+Cluster (the exact shape of fact Б: the snapshot names the old ref);
  С4  absent from BOTH keeps the canonical refusal — the cache never turns a
      real absence into a guess;
  С5  a HIT still costs no sweep — the miss is the only thing that pays.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.exceptions import ValidationError
from kicadstamp.explore import Selected
from kicadstamp.placement.services.clone_role_resolver import (
    resolve_footprint_by_role,
)
from kicadstamp.placement.services.coordinate_position_calculator import (
    resolve_footprint_by_cluster_role,
)


class _Adapter:
    """The adapter surface the two resolvers touch, counting the SWEEP so a cell
    can say whether the snapshot's answer was free."""

    def __init__(self, footprints):
        self._fps = list(footprints)
        self.sweeps = 0
        self.ref_reads = []

    def get_footprints(self):
        self.sweeps += 1
        return list(self._fps)

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return fp.role
        if name == CLUSTER_FIELD_NAME:
            return fp.cluster
        return None

    def get_footprint(self, ref):
        self.ref_reads.append(ref)
        return next((fp for fp in self._fps if fp.ref == ref), None)

    def get_selected_items(self):
        return []


def _fp(ref, role="ROLE_A", cluster="CL_A"):
    return SimpleNamespace(ref=ref, role=role, cluster=cluster,
                           sheet_path_uuids=[], uuid=f"uuid-{ref}")


def _selected(fp):
    """One snapshot row — the shape `connection.snapshot` has."""
    return Selected(ref=fp.ref, role=fp.role, cluster=fp.cluster, sheet=[],
                    nets={}, fp=fp)


# ── С1: a pair missing from the snapshot ────────────────────────────────────

def test_a_pair_absent_from_the_snapshot_resolves_from_the_live_board():
    """С1 (fact Б, the cluster case): the snapshot is from the previous board
    and knows nothing about CL_A. The cluster is on the live board, so the read
    must succeed there — the snapshot's silence is not a board fact."""
    live = _fp("NEW1")
    adapter = _Adapter([live])
    foreign_snapshot = [_selected(_fp("OLD9", role="OTHER", cluster="OTHER"))]

    resolved = resolve_footprint_by_cluster_role(
        adapter, "CL_A", "ROLE_A", "label", snapshot=foreign_snapshot)

    assert resolved is live
    assert adapter.sweeps == 1, "the miss must be answered by the live sweep"


# ── С2: a bare role missing from the snapshot ──────────────────────────────

def test_a_role_absent_from_the_snapshot_resolves_from_the_live_board():
    """С2 (the same rule one level up — `_role_candidates`): the anchor_role
    resolver must not let a stale snapshot decide that the role is nowhere."""
    live = _fp("NEW1", role="ANCHOR", cluster="CL_A")
    adapter = _Adapter([live])
    foreign_snapshot = [_selected(_fp("OLD9", role="OTHER", cluster="OTHER"))]

    resolved = resolve_footprint_by_role(
        adapter, "ANCHOR", None, None, {}, "label", snapshot=foreign_snapshot)

    assert resolved is live


# ── С3: the snapshot names a ref that is no longer there ───────────────────

def test_a_snapshot_ref_that_left_the_board_resolves_to_the_live_ref():
    """С3 (fact Б in its exact shape): the snapshot HAS the pair, but under the
    PREVIOUS board's ref `OLD1`, which the live board no longer has. The live
    board carries the same Role+Cluster as `NEW1` — the read must answer NEW1,
    not "no component tagged ROLE_A with Cluster CL_A".

    Mutation check: restore the old "a vanished snapshot ref is the not-found
    fatal" and this cell goes red."""
    live = _fp("NEW1")
    adapter = _Adapter([live])
    stale_snapshot = [_selected(_fp("OLD1"))]

    cluster_resolved = resolve_footprint_by_cluster_role(
        adapter, "CL_A", "ROLE_A", "label", snapshot=stale_snapshot)
    role_resolved = resolve_footprint_by_role(
        adapter, "ROLE_A", None, None, {}, "label", snapshot=stale_snapshot)

    assert cluster_resolved is live
    assert role_resolved is live
    assert "OLD1" in adapter.ref_reads     # the snapshot's ref WAS tried first


# ── С4: absent from both — the refusal stands ──────────────────────────────

def test_a_role_on_neither_the_snapshot_nor_the_board_keeps_its_refusal():
    """С4: the fallback is not an invitation to guess. With the role on neither
    source, the canonical refusal is raised — the LIVE board's own wording, not
    a message about the snapshot."""
    adapter = _Adapter([])
    foreign_snapshot = [_selected(_fp("OLD9", role="OTHER", cluster="OTHER"))]

    with pytest.raises(ValidationError) as role_err:
        resolve_footprint_by_role(adapter, "ROLE_A", None, None, {}, "label",
                                  snapshot=foreign_snapshot)
    with pytest.raises(ValidationError) as cluster_err:
        resolve_footprint_by_cluster_role(adapter, "CL_A", "ROLE_A", "label",
                                          snapshot=foreign_snapshot)

    assert "not found on any component" in str(role_err.value)
    assert "no component tagged" in str(cluster_err.value)


# ── С6-С7: a HIT must name the SAME part (Д-3 of the Ч1 acceptance) ────────

def test_a_snapshot_ref_naming_another_part_is_a_miss():
    """С6 (Д-3): the snapshot's row says (ROLE_A, CL_A) under refdes `R21`, and
    the live board HAS an `R21` — but it is a DIFFERENT component (another
    Role/Cluster). Denis's hole: before this, the resolver returned that
    unrelated part silently, because a refdes means nothing on a board the
    snapshot was not built from. The pair is verified against the live
    footprint, and a mismatch is a MISS, so the live board answers with the ref
    that really carries the pair.

    Mutation check: drop the pair check (`_live_pair_matches`) and this cell
    answers R21."""
    stale_fp = _fp("R21", role="ROLE_A", cluster="CL_A")   # the OLD board's R21
    live_impostor = _fp("R21", role="OTHER", cluster="CL_B")
    right = _fp("NEW1", role="ROLE_A", cluster="CL_A")
    adapter = _Adapter([live_impostor, right])
    stale_snapshot = [_selected(stale_fp)]

    resolved = resolve_footprint_by_cluster_role(
        adapter, "CL_A", "ROLE_A", "label", snapshot=stale_snapshot)

    assert resolved is right, \
        "the snapshot's refdes was believed on a board it was not built from"


def test_a_role_snapshot_ref_naming_another_part_is_a_miss():
    """С7 — the same rule in the ROLE resolver: its one field of identity is the
    Role, so that is what is verified on the live footprint (mutation: drop the
    ROLE read in `_current_generation` and this answers R21)."""
    stale_fp = _fp("R21", role="ANCHOR", cluster="CL_A")   # the OLD board's R21
    live_impostor = _fp("R21", role="OTHER", cluster="CL_B")
    right = _fp("NEW1", role="ANCHOR", cluster="CL_A")
    adapter = _Adapter([live_impostor, right])
    stale_snapshot = [_selected(stale_fp)]

    resolved = resolve_footprint_by_role(
        adapter, "ANCHOR", None, None, {}, "label", snapshot=stale_snapshot)

    assert resolved is right


def test_the_right_role_under_another_cluster_is_only_a_cluster_miss():
    """С7 (cont.): a Role-only resolver never asks about clusters, so a live ref
    with the right Role is its answer — while the CLUSTER resolver, which did
    ask, treats the same footprint as a miss and finds the right instance."""
    live = _fp("R21", role="ROLE_A", cluster="CL_B")
    right = _fp("NEW1", role="ROLE_A", cluster="CL_A")
    adapter = _Adapter([live, right])
    stale_snapshot = [_selected(_fp("R21"))]

    by_role = resolve_footprint_by_role(
        adapter, "ROLE_A", None, None, {}, "label", snapshot=stale_snapshot)
    by_cluster = resolve_footprint_by_cluster_role(
        adapter, "CL_A", "ROLE_A", "label", snapshot=stale_snapshot)

    assert by_role is live
    assert by_cluster is right


# ── С5: the hit is still free ──────────────────────────────────────────────

def test_a_snapshot_hit_costs_no_sweep():
    """С5 (the win of plan_2026_09_22_board_door_finish must survive Ч1-Б): a
    HIT answers from memory — not one `get_footprints()`. Only the miss pays,
    which is why the fallback above is affordable at all."""
    live = _fp("NEW1")
    adapter = _Adapter([live])
    matching_snapshot = [_selected(_fp("NEW1"))]

    cluster_resolved = resolve_footprint_by_cluster_role(
        adapter, "CL_A", "ROLE_A", "label", snapshot=matching_snapshot)
    assert adapter.sweeps == 0

    role_resolved = resolve_footprint_by_role(
        adapter, "ROLE_A", None, None, {}, "label", snapshot=matching_snapshot)
    assert adapter.sweeps == 0
    assert cluster_resolved is live and role_resolved is live
