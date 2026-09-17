#!/usr/bin/env python3
"""Guards of the WRITE side of Tools -> "Extract spoke..." (stage 5,
plan_2026_09_17_spoke_s5_extract_spoke.md §2 Р4/Р5, §4 Т5.3, §5 С7/С11).

The write side is pure too: a spoke entry is built, spliced into its chain
(APPENDED at the end when the pad is free, REPLACED at the same index when the
user confirmed "Replace") and validated through the config loader BEFORE anything
touches a file. These tests pin that down with no KiCad: the last one does a real
round trip through the config writer on a temporary file, which is the exact
sequence the dialog's OK runs (minus the board read that extracts the cell).

The chain is the one place a spoke can live (chains:, not the trees — plan Р8 of
the design's §3), so a spoke with a subcluster is never created here: the cluster
written is the one the caller passes, and nothing invents "…/…".
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.config import load_chain, load_manual_spoke          # noqa: E402
from kicadstamp.config_writer import read_data, upsert_list_entry    # noqa: E402
from kicadstamp.spoke_extraction import (                            # noqa: E402
    SpokeOffset,
    chain_with_new_spoke,
    new_chain_dict,
    spoke_entry,
    stage_spoke_write,
)

NET = "+3V3_VDD"
CLUSTER = "MCU_PWR_BANK"
CELL = "mcu_pair"


def _chain(spokes=(), **extra):
    """A chain entry as the config files carry it (raw dict, not a Chain)."""
    entry = {"net": NET, "anchor_ref": "U5",
             "spokes": [dict(s) for s in spokes]}
    entry.update(extra)
    return entry


def _existing(pad, cell=CELL):
    return {"pad": pad, "cell": cell, "cluster": CLUSTER}


def _new(pad="48", cell=CELL, shift=(0.0, 0.0), rotation=0.0):
    return spoke_entry(pad, cell, SpokeOffset(shift[0], shift[1], rotation),
                       cluster=CLUSTER)


# ── the entry itself ───────────────────────────────────────────────────────

class TestSpokeEntry:
    def test_writes_pad_cell_cluster_and_the_read_geometry(self):
        entry = spoke_entry("48", CELL, SpokeOffset(0.5, -1.25, 90.0),
                            cluster=CLUSTER)
        assert entry["pad"] == "48" and entry["cell"] == CELL
        assert entry["cluster"] == CLUSTER
        assert entry["shift_x_mm"] == pytest.approx(0.5)
        assert entry["shift_y_mm"] == pytest.approx(-1.25)
        assert entry["rotation_deg"] == pytest.approx(90.0)
        load_manual_spoke(entry, NET)          # a valid ManualSpoke entry

    def test_a_zero_shift_and_rotation_are_left_out(self):
        """Same convention as the Chain dock's own pad form (a zero is the
        field's default), so an extracted spoke and a hand-typed one are
        byte-identical when nothing has to be stored."""
        entry = spoke_entry("48", CELL, SpokeOffset(0.0, 0.0, 0.0))
        assert entry == {"pad": "48", "cell": CELL}
        load_manual_spoke(entry, NET)

    def test_no_cluster_key_when_there_is_none(self):
        entry = spoke_entry("48", CELL, SpokeOffset(0.0, 0.0, 0.0), cluster=None)
        assert "cluster" not in entry


# ── splice into the chain ──────────────────────────────────────────────────

class TestChainWithNewSpoke:
    def test_a_free_pad_appends_at_the_END(self):
        """The pool is consumed in chain order, so a new spoke goes LAST — that
        is the order pool_outcome's verdict was computed for."""
        chain = _chain([_existing("1"), _existing("12")])
        merged, problems, replaced = chain_with_new_spoke(chain, _new())
        assert problems == [] and replaced is False
        assert [s["pad"] for s in merged["spokes"]] == ["1", "12", "48"]
        load_chain(merged)
        assert chain["spokes"][-1]["pad"] == "12"     # the source dict is untouched

    def test_c7_an_occupied_pad_is_refused_without_replace(self):
        """С7/М7: writing a second spoke onto a pad that already has one would
        place two pairs on the same pin — refused unless the caller confirmed
        "Replace", and the refusal names the pad and the cell it holds."""
        chain = _chain([_existing("48", cell="old_pair")])
        merged, problems, replaced = chain_with_new_spoke(chain, _new())
        assert merged is None and replaced is False
        assert problems and "48" in problems[0] and "old_pair" in problems[0]

    def test_c7_a_retired_spoke_on_the_pad_is_an_occupant_too(self):
        """"Retired" means "not on the board right now" — but the pad is still
        taken, and two entries on one pad would be planned as two spokes."""
        chain = _chain([dict(_existing("48", cell="old_pair"), retired=True)])
        merged, problems, _replaced = chain_with_new_spoke(chain, _new())
        assert merged is None and problems

    def test_c7_replace_keeps_the_pads_position_in_the_chain(self):
        """С7: a replacement is written at the SAME index, so the spokes before
        it keep their components (the pool is consumed in chain order)."""
        chain = _chain([_existing("1"), _existing("48", cell="old_pair"),
                        _existing("12")])
        merged, problems, replaced = chain_with_new_spoke(chain, _new(),
                                                          replace=True)
        assert problems == [] and replaced is True
        assert [s["pad"] for s in merged["spokes"]] == ["1", "48", "12"]
        assert merged["spokes"][1]["cell"] == CELL
        assert merged["spokes"][1]["cluster"] == CLUSTER
        load_chain(merged)

    def test_replace_on_a_free_pad_is_the_plain_append(self):
        chain = _chain([_existing("1")])
        merged, problems, replaced = chain_with_new_spoke(chain, _new(),
                                                          replace=True)
        assert problems == [] and replaced is False
        assert [s["pad"] for s in merged["spokes"]] == ["1", "48"]


# ── validation BEFORE anything is written ──────────────────────────────────

class TestStageSpokeWrite:
    def test_valid_plan_carries_a_chain_the_loader_accepts(self):
        plan, problems = stage_spoke_write(_chain([_existing("1")]), _new())
        assert problems == [] and plan is not None
        chain = load_chain(plan.chain)
        assert [s.pad for s in chain.spokes] == ["1", "48"]
        assert chain.spokes[-1].cell == CELL
        assert plan.replaced is False and plan.pad == "48"

    def test_c7_replace_requires_the_caller_to_say_so(self):
        """С7/М7 at the extraction level: without replace=True nothing is
        staged at all, so OK cannot write over an existing spoke by accident."""
        chain = _chain([_existing("48", cell="old_pair")])
        plan, problems = stage_spoke_write(chain, _new())
        assert plan is None and problems

    def test_a_broken_spoke_is_refused_before_the_chain_is_touched(self):
        """A cell that asks for BOTH a shift and a radius is a fatal ManualSpoke
        — the plan must not exist at all (the caller then has nothing to write)."""
        bad = _new()
        bad["radius_mm"] = 2.0
        plan, problems = stage_spoke_write(_chain([_existing("1")]), bad)
        assert plan is None and problems

    def test_a_chain_without_a_net_is_refused(self):
        """load_chain itself accepts a netless chain (it only guards the
        ANCHOR), so this refusal is the extraction's own — without a net the
        entry cannot be picked by --only and has no identity in the tree."""
        plan, problems = stage_spoke_write({"anchor_ref": "U5", "spokes": []},
                                           _new())
        assert plan is None
        assert problems == ["Net is required."], problems


# ── a brand-new chain (plan Р3: "нет цепочки") ─────────────────────────────

class TestNewChain:
    def test_c_a_new_chain_resolves_its_anchor_and_takes_the_spoke(self):
        """The anchor comes from the nearest FOREIGN pad — by ROLE (it survives
        re-annotation) narrowed by its cluster, never by refdes."""
        chain = new_chain_dict(NET, anchor_role="VDD_PIN", anchor_cluster=CLUSTER)
        plan, problems = stage_spoke_write(chain, _new())
        assert problems == [] and plan is not None
        loaded = load_chain(plan.chain)
        assert loaded.net == NET and loaded.anchor_role == "VDD_PIN"
        assert loaded.anchor_cluster == CLUSTER
        assert [s.pad for s in loaded.spokes] == ["48"]

    def test_c_two_anchors_at_once_are_refused(self):
        """anchor_ref and anchor_role are mutually exclusive (the loader's own
        rule) — the caller must pick ONE way to name the anchor."""
        chain = new_chain_dict(NET, anchor_ref="U5", anchor_role="VDD_PIN")
        plan, problems = stage_spoke_write(chain, _new())
        assert plan is None and problems

    def test_c_an_anchorless_chain_is_refused(self):
        """The new chain is created from the nearest FOREIGN pad, so an anchor
        is always known; without one the chain cannot resolve at apply time."""
        plan, problems = stage_spoke_write(new_chain_dict(NET), _new())
        assert plan is None and problems


# ── the real write path (temporary config, no KiCad) ───────────────────────

class TestRoundTripThroughTheConfigWriter:
    def test_c7_the_spoke_lands_LAST_in_its_chain_and_survives_a_reload(self, tmp_path):
        """The exact sequence OK runs after the board read: stage -> upsert the
        chain into its file -> read it back. A replacement must land in place,
        a new spoke must land last."""
        path = tmp_path / "config.sexp"
        path.write_text("(kicadstamp-config\n"
                        "  (chains (chain (net \"%s\") (anchor_ref \"U5\")\n"
                        "                  (spokes (spoke (pad \"1\") "
                        "(cell \"%s\"))))))\n" % (NET, CELL), encoding="utf-8")

        plan, problems = stage_spoke_write(_chain([_existing("1")]), _new("48"))
        assert problems == [] and plan is not None
        upsert_list_entry(path, "chains", plan.chain,
                          key_fn=lambda e: e.get("name") or e.get("net"))

        stored = read_data(path)
        assert [s["pad"] for s in stored["chains"][0]["spokes"]] == ["1", "48"]
        assert stored["chains"][0]["spokes"][-1]["cell"] == CELL
