#!/usr/bin/env python3
"""Ч1 of plan_2026_10_21_role_lookup_steals_across_clusters.md: the guard against
KiCad's "Update Footprints from Library" field rename (bug report 4).

The signature is the BUG's own fingerprint, not a guess (решение В2, 21.10):

    a `KiLib_Generator` field is present  AND  `Role` or `Cluster` is PHYSICALLY
    absent on that footprint.

The control samples come from the `.history` snapshots Denis measured on
`Tests/Test02` (the data below mirrors them, not invented shapes):

  * C4 — run 1 of the bug: `Role=C_MID`, `KiLib_Generator=P5V_IN`, no `Cluster`;
  * C2 — a part whose texts match no library field: `Role`, `Cluster` AND
    `KiLib_Generator` all present — NOT damage;
  * U1 — "Remove text items" run: NO Role, NO Cluster, and no `KiLib_Generator`
    at all. U1 WAS damaged there (both fields were deleted), but the guard does
    not see it: SOT-223 carries no `KiLib_Generator` in its library copy, so the
    rename left no fingerprint. That is the signature's documented limit — and
    no theft follows, because a part without `Role` is not a candidate at all.

Cells:
  С9   C4 alone                       -> refusal naming C4, KiLib_Generator, F8
  С10  C2 alone                       -> silence
  С11  U1 alone                       -> silence (outside the signature's reach,
                                         see the bullet above)
  С12  C4 + C2 + U1                   -> only C4 is named, count 1
  С13  12 damaged parts               -> 10 named + "and 2 more"
  С14  the damage hidden by OUR store -> still refused (the guard asks the
                                        BOARD through has_field, never the value
                                        in force)
  С15  the GUI Redraw path            -> ApplyPipeline.run refuses before any
                                        move is planned
  С16  the CLI `apply` path           -> run_apply refuses with the same text

Mutation checks (Ч1-Т6): remove the guard call from run_all_checks and С9/С15/С16
go red; widen the signature to "Role/Cluster missing" without KiLib_Generator and
С11 goes red; make the guard read get_field_value instead of has_field and С14
goes red.
"""
from types import SimpleNamespace

import pytest

from kicadstamp import apply_pipeline
from kicadstamp.apply_pipeline import ApplyPipeline, RunOptions, run_apply
from kicadstamp.config import Config
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.exceptions import ValidationError
from kicadstamp.field_override_adapter import FieldOverrideAdapter
from kicadstamp.field_overrides import FieldOverrides
from kicadstamp.role_cluster_damage import (
    KILIB_GENERATOR_FIELD_NAME,
    check_role_cluster_fields_intact,
    damaged_footprints,
)
from kicadstamp.validation import run_all_checks

LIB = KILIB_GENERATOR_FIELD_NAME


class _Board:
    """The board surface these cells touch — and the record of any WRITE, so a
    cell can assert that a refused run moved nothing."""

    def __init__(self, footprints):
        self._fps = list(footprints)
        self.writes = []
        self.ignore_selection = False

    def get_footprints(self):
        return list(self._fps)

    def get_field_value(self, fp, name):
        return fp.fields.get(name)

    def has_field(self, fp, name):
        return name in fp.fields

    def get_selected_items(self):
        return []

    def get_board_filename(self):
        return None

    def refresh_board(self):
        pass

    def close(self):
        pass

    def set_field_values_bulk(self, *args, **kwargs):
        self.writes.append((args, kwargs))


def _fp(ref, **fields):
    return SimpleNamespace(ref=ref, fields=dict(fields), uuid=f"uuid-{ref}",
                           sheet_path_uuids=[f"uuid-{ref}"], position=None)


def _c4():
    """Run 1 of the bug on Test02: the Cluster name became the library field."""
    return _fp("C4", **{ROLE_FIELD_NAME: "C_MID", LIB: "P5V_IN"})


def _c2():
    """A part the bug does NOT damage: the library field sits beside ours."""
    return _fp("C2", **{ROLE_FIELD_NAME: "C_IN_BULK",
                        CLUSTER_FIELD_NAME: "P5V_IN", LIB: "SMD_2terminal"})


def _u1():
    """The "all boxes ticked" case: both fields gone, no `KiLib_Generator` left —
    damaged, but invisible to this signature (see the module docstring)."""
    return _fp("U1")


def _text(board):
    with pytest.raises(ValidationError) as exc_info:
        check_role_cluster_fields_intact(board)
    return str(exc_info.value)


# ── С9: the signature itself ────────────────────────────────────────────────

def test_c9_a_renamed_cluster_is_refused_and_names_f8():
    """С9 — the one fact this guard exists for: C4 lost its Cluster to the
    library field's name, and the refusal must say WHICH component and what to
    press (no dialog, the Log line is the whole message)."""
    text = _text(_Board([_c4()]))

    assert "C4" in text and "Cluster" in text
    assert LIB in text
    assert "Update PCB from Schematic (F8)" in text


def test_c9_a_renamed_role_is_refused_too():
    """The bug renames whichever field the pairing picks by TEXT similarity —
    `Role` is the other half of the same signature."""
    board = _Board([_fp("R1", **{CLUSTER_FIELD_NAME: "P5V_IN", LIB: "P5V_IN"})])

    text = _text(board)

    assert "R1" in text and "Role" in text


# ── С10 / С11: the two controls the measurement gave us ─────────────────────

def test_c10_a_library_field_beside_both_our_fields_is_silent():
    """С10 (control) — C2 keeps Role and Cluster; the library field beside them
    is not damage, and refusing over it would block every library update."""
    assert damaged_footprints(_Board([_c2()])) == []
    check_role_cluster_fields_intact(_Board([_c2()]))  # no raise


def test_c11_a_part_without_the_fingerprint_is_silent():
    """С11 (control) — U1 has neither field AND no library field at all. This
    run DID damage it, yet the guard stays silent by design: with no
    `KiLib_Generator` there is no rename to see, and a part without `Role` is
    not a candidate, so no theft can follow. Refusing over it would also block
    every freshly tagged board."""
    assert damaged_footprints(_Board([_u1()])) == []
    check_role_cluster_fields_intact(_Board([_u1()]))  # no raise


# ── С12 / С13: naming, and the count ────────────────────────────────────────

def test_c12_only_the_damaged_parts_are_named():
    """С12 — a healthy part and an untagged one must not be dragged into the
    refusal: the user needs the three damaged refdes, not the whole board."""
    text = _text(_Board([_c4(), _c2(), _u1()]))

    assert "1 component(s)" in text
    assert "C4 (Cluster)" in text
    assert "C2" not in text and "U1" not in text


def test_c13_the_refusal_stops_counting_out_at_ten():
    """С13 — a damaged board is the whole board (9-11 parts on Test02): the
    message prints the first ten and the remainder as a number."""
    board = _Board([_fp(f"C{i}", **{LIB: "X"}) for i in range(1, 13)])

    text = _text(board)

    assert "12 component(s)" in text
    assert "C10 (Role/Cluster)" in text
    assert "and 2 more" in text


# ── С14: the guard asks the BOARD, not the value in force ───────────────────

def test_c14_our_stored_value_does_not_hide_the_damage():
    """С14 — the override store can hold our Cluster for a symbol KiCad renamed
    the field on; the value in force would then look perfect. The guard reads
    has_field (board-only by design), so the damage is still refused."""
    store = FieldOverrides()
    store.set("uuid-C4", "C4", CLUSTER_FIELD_NAME, "P5V_IN", "test")
    layered = FieldOverrideAdapter(_Board([_c4()]), store)

    assert layered.get_field_value(_c4(), CLUSTER_FIELD_NAME) == "P5V_IN"
    text = _text(layered)
    assert "C4" in text


# ── С15 / С16: the two doors that must both refuse ──────────────────────────

def _install(monkeypatch, board):
    monkeypatch.setattr(apply_pipeline, "create_board_adapter",
                        lambda *a, **k: board)
    return board


def test_c15_the_gui_redraw_path_refuses_before_any_move(monkeypatch):
    """С15 — Redraw/cascade go through ApplyPipeline.run(); the refusal must
    come from pre-validation, so not a single move or write happens."""
    board = _install(monkeypatch, _Board([_c4()]))
    pipeline = ApplyPipeline("board.sexp", preloaded_cfg=Config())

    with pytest.raises(ValidationError) as exc_info:
        pipeline.run()

    assert "Update PCB from Schematic (F8)" in str(exc_info.value)
    assert board.writes == []


def test_c16_the_cli_apply_path_refuses_with_the_same_text(monkeypatch):
    """С16 — the CLI's own entry point (run_apply, what cmd_apply calls) must
    answer with the identical text: one wording, one door."""
    board = _install(monkeypatch, _Board([_c4()]))

    with pytest.raises(ValidationError) as exc_info:
        run_apply(RunOptions(config_path="board.sexp"), cfg=Config())

    assert "Update PCB from Schematic (F8)" in str(exc_info.value)
    assert board.writes == []


# ── the ordering the refusal relies on ─────────────────────────────────────

def test_the_guard_is_checked_before_any_config_check():
    """The guard sits right after check_board_identity: a damaged board makes
    every role/cluster check below it describe the wreckage, so it must be the
    error that surfaces on a config that is otherwise empty."""
    with pytest.raises(ValidationError) as exc_info:
        run_all_checks(_Board([_c4()]), Config())

    assert "Update PCB from Schematic (F8)" in str(exc_info.value)
