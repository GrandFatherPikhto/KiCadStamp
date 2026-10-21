#!/usr/bin/env python3
"""Ч1 of plan_2026_10_21_role_lookup_steals_across_clusters.md: a NAMED cluster
is a hard confinement of an anchor_role resolve, at ANY candidate count.

The live fact (Denis + Claude, 21.10, board `Tests/Test02`, profile `Test04`):
after KiCad's "Update Footprints from Library" renamed user fields into
`KiLib_Generator`, `resolve_footprint_by_role(… 'R_LED', cluster 'P5V_IN' …)`
answered **R2 of `LDO_P3V3`** — a component of ANOTHER cluster — because the
cascade narrowed by cluster only when 2+ candidates survived
(`role_narrowing._narrow_by_sheet_cluster_selection`, `len(narrowed) > 1`), so a
single candidate was returned unchecked.

The rule (В1 of the plan, decided 21.10): the cluster named by the caller →
the candidate MUST carry it, however many candidates there are; a candidate with
a FOREIGN cluster or no `Cluster` field at all is NOT taken, and the refusal says
why in the words the entity path has used since 2026-10-13
(`role_conflict_reasons.role_not_in_cluster_problems` — one text, one owner).

Cells (the plan's П2 table):
  С1  one candidate, FOREIGN cluster            -> refusal, it is NOT taken
  С2  one candidate, NO `Cluster` field         -> refusal, it is NOT taken
  С3  one candidate, OWN cluster                -> taken (control)
  С4  2+ candidates, ALL own                    -> today's cascade (ambiguous here)
  С5  2+ candidates, NONE own                   -> refusal NAMING every candidate
                                                   (was "ambiguous" before Ч1)
  С6  cluster NOT named                         -> today's behaviour (control)
  С7  the role is on NOTHING                    -> "not found", never re-worded
                                                   as a cluster complaint
  С8  the same rule on the LIVE branch          -> a snapshot hit whose ref has
                                                   left the board re-checks the
                                                   cluster against the live board

The FLAG (cells С17-С19, «Ответ Claude на находку Ч1» of the same plan): the
resolver's `cluster_is_hint=True` is for the ONE caller whose cluster does NOT
address the role it searches — `net_trace_planner._item_net_name`, where
`nt.anchor_cluster` belongs to the record's ANCHOR while the role belongs to the
OTHER end of the copper (the FPGA side of a channel bridge). There the cluster
only NARROWS, exactly as before Ч1:
  С17 a unique role on a FOREIGN cluster     -> resolved (the flag's whole point)
  С18 2+ candidates, one inside the hint     -> that one
  С19 2+ candidates, none inside the hint    -> "ambiguous" (NOT the "is NOT
                                                taken" refusal: nothing here is
                                                addressed by the cluster)
  С20 the flag never turns absence into an answer -> "not found" stays

Mutation checks (Ч1-Т6): make the confinement cascade-shaped again (narrow only
at 2+) and С1 goes red; drop the field check and С2 goes red; re-word С7 as a
cluster complaint and С7 goes red.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.exceptions import ValidationError
from kicadstamp.explore import Selected
from kicadstamp.placement.services.clone_role_resolver import (
    resolve_footprint_by_role,
)


class _Adapter:
    """The adapter surface the resolver touches: a sweep of the board, the field
    pair, the current generation of one refds, and the selection (empty)."""

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


def _fp(ref, role="R_LED", cluster="P5V_IN"):
    return SimpleNamespace(ref=ref, role=role, cluster=cluster,
                           sheet_path_uuids=[], uuid=f"uuid-{ref}")


def _selected(fp):
    """One snapshot row — the shape `connection.snapshot` has."""
    return Selected(ref=fp.ref, role=fp.role, cluster=fp.cluster, sheet=[],
                    nets={}, fp=fp)


def _refusal(adapter, role="R_LED", cluster="P5V_IN", *, snapshot=None):
    with pytest.raises(ValidationError) as exc_info:
        resolve_footprint_by_role(
            adapter, role, None, cluster, {}, "probe", snapshot=snapshot)
    return str(exc_info.value)


# ── С1: the only candidate stands in ANOTHER cluster ────────────────────────

def test_c1_a_single_foreign_candidate_is_not_taken():
    """С1 — the live fact itself: R_LED/P5V_IN must not answer R2 of LDO_P3V3."""
    r2 = _fp("R2", cluster="LDO_P3V3")
    adapter = _Adapter([r2])

    text = _refusal(adapter)

    assert "R2" in text
    assert "carries this role but stands in cluster 'LDO_P3V3' — it is NOT taken" in text
    assert "probe" in text, "the caller's label must prefix the refusal"


# ── С2: the only candidate has NO Cluster field ─────────────────────────────

def test_c2_a_single_untagged_candidate_is_not_taken():
    """С2 — "untagged" must never pass for "mine" (the KiCad rename leaves the
    field GONE, which reads as None)."""
    r1 = _fp("R1", cluster=None)
    adapter = _Adapter([r1])

    text = _refusal(adapter)

    assert "R1" in text
    assert "carries this role but has no Cluster field — it is NOT taken" in text


# ── С3: the only candidate IS the cluster's ─────────────────────────────────

def test_c3_a_single_own_candidate_is_taken():
    """С3 (control): the confinement must not cost the honest answer."""
    r1 = _fp("R1", cluster="P5V_IN")
    adapter = _Adapter([r1])

    resolved = resolve_footprint_by_role(
        adapter, "R_LED", None, "P5V_IN", {}, "probe")

    assert resolved is r1


# ── С4: several candidates, all own ─────────────────────────────────────────

def test_c4_two_own_candidates_keep_todays_ambiguous_refusal():
    """С4 (control): with 2+ OWN candidates nothing new may be claimed — the
    cascade still answers "ambiguous" and names the candidates."""
    adapter = _Adapter([_fp("R1"), _fp("R2")])

    text = _refusal(adapter)

    assert "is ambiguous" in text
    assert "R1" in text and "R2" in text


# ── С5: several candidates, NONE own ────────────────────────────────────────

def test_c5_two_foreign_candidates_are_both_named_as_not_taken():
    """С5 — the cell that CHANGES (В1): before Ч1 this was "ambiguous", which
    told the user to pick one of two components that are BOTH not his."""
    adapter = _Adapter([_fp("R2", cluster="LDO_P3V3"),
                        _fp("R3", cluster="LDO_P3V3")])

    text = _refusal(adapter)

    assert "is ambiguous" not in text
    assert "R2" in text and "R3" in text
    assert text.count("stands in cluster 'LDO_P3V3' — it is NOT taken") == 2


# ── С6: the cluster is NOT named — today's behaviour ────────────────────────

def test_c6_an_unnamed_cluster_keeps_todays_answer():
    """С6 (control): refs-only/sheet-only addressing is not this rule's
    business — a single candidate is answered as before, whatever it carries."""
    r2 = _fp("R2", cluster="LDO_P3V3")
    adapter = _Adapter([r2])

    resolved = resolve_footprint_by_role(adapter, "R_LED", None, None, {}, "probe")

    assert resolved is r2


# ── С7: the role is on nothing ──────────────────────────────────────────────

def test_c7_an_absent_role_still_reads_as_not_found():
    """С7 — a board fact must not be re-worded as a cluster complaint: the
    refusal stays "not found", with no mention of the cluster."""
    adapter = _Adapter([_fp("C1", role="C_MID", cluster="P5V_IN")])

    text = _refusal(adapter)

    assert "anchor_role 'R_LED' not found on any component on the board" in text
    assert "not in cluster" not in text


# ── С8: the same rule on the LIVE branch (a snapshot hit that left the board) ─

def test_c8_the_live_branch_confines_the_cluster_too():
    """С8 — the property belongs to the CLASS, not to one candidate source: the
    snapshot names a P5V_IN ref that the live board no longer has, so the live
    sweep answers — and ITS single candidate is foreign, so it is refused."""
    gone = _fp("R1", cluster="P5V_IN")
    r2 = _fp("R2", cluster="LDO_P3V3")
    adapter = _Adapter([r2])

    text = _refusal(adapter, snapshot=[_selected(gone)])

    assert adapter.ref_reads == ["R1"], "the snapshot hit must be re-read live"
    assert "R2" in text
    assert "stands in cluster 'LDO_P3V3' — it is NOT taken" in text


# ── С17-С20: the FLAG — the cluster is a hint, not an address ───────────────
# One caller: net_trace_planner._item_net_name, where the cluster belongs to the
# record's ANCHOR and the role belongs to the other end of the copper.

def _hinted(adapter, role="R_LED", cluster="P5V_IN", *, snapshot=None):
    """The resolver called the way `_item_net_name` calls it: cluster = a hint."""
    return resolve_footprint_by_role(
        adapter, role, None, cluster, {}, "probe", snapshot=snapshot,
        cluster_is_hint=True)


def _refusal_hinted(adapter, role="R_LED", cluster="P5V_IN"):
    with pytest.raises(ValidationError) as exc_info:
        _hinted(adapter, role=role, cluster=cluster)
    return str(exc_info.value)


def test_c17_a_hint_never_refuses_a_unique_foreign_role():
    """С17 — the copper-end case (the FPGA side of a channel bridge): the note's
    `anchor_cluster` describes the ANCHOR, so a unique role in another cluster
    must resolve, not be refused."""
    fpga = _fp("FPGA1", role="FPGA", cluster="FPGA")
    adapter = _Adapter([fpga])

    assert _hinted(adapter, role="FPGA", cluster="DAC_BUF") is fpga


def test_c18_a_hint_picks_the_instance_it_names():
    """С18 — the hint is still USEFUL: two components share the role, the one in
    the hinted cluster wins (the v103 `net_from_role = C_OUT_BULK` case)."""
    own = _fp("C11", role="C_OUT_BULK", cluster="PIF_CLKVDD")
    other = _fp("C12", role="C_OUT_BULK", cluster="PIF_DVDD")
    adapter = _Adapter([other, own])

    assert _hinted(adapter, role="C_OUT_BULK", cluster="PIF_CLKVDD") is own


def test_c19_a_hint_that_separates_nothing_leaves_the_ambiguity():
    """С19 — with no candidate inside the hinted cluster the honest answer is
    "ambiguous": unlike the addressing case (С5), the cluster here names
    something else, so it may not claim anyone «is NOT taken»."""
    adapter = _Adapter([_fp("R2", cluster="LDO_P3V3"),
                        _fp("R3", cluster="LDO_P3V3")])

    text = _refusal_hinted(adapter)

    assert "is ambiguous" in text
    assert "NOT taken" not in text


def test_c20_the_flag_never_turns_absence_into_an_answer():
    """С20 — a hint is a suggestion, not a source of candidates: a role that is
    on nothing still answers "not found"."""
    adapter = _Adapter([_fp("C1", role="C_MID", cluster="P5V_IN")])

    text = _refusal_hinted(adapter, role="R_LED")

    assert "not found on any component on the board" in text
