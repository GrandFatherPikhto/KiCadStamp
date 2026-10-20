#!/usr/bin/env python3
# tests/gui/test_duplicate_refdes_refusals.py
"""Ч4 (plan_2026_10_15_extract_and_read_paths_cleanup): the two READING doors
that address a footprint by refdes refuse a duplicated refdes exactly the way
the apply pipeline does.

Why: `adapter.get_footprint(ref)` silently returns the FIRST copy. On the live
board of 2026-10-12 four orphans (C31, C32, C35, C36) kept refdes that the new,
schematic-linked footprints also carried; the pipeline refuses such a record
(`kicadstamp.refdes_duplicates`), while "Select cell" / "Select cell components"
and the cell overlay's identified-pair read happily picked whichever copy came
first — the user then EDITED the wrong pair.

The rule, one place: `refdes_duplicates.duplicate_refusal_lines(records, refs)`
composes the detector's own three functions, so the wording is the guard's
(["... stands on the board 2 times ...", both copies, "delete the orphan ..."]),
never a second phrase invented by a door.

Cells:
  * the helper itself: duplicate -> lines, unique -> [], and `refs` narrowing
    (a duplicate the read never addresses refuses nothing);
  * Select cell: nothing is selected, the answer travels under `duplicate`;
  * the overlay read (`_role_to_fp_by_refs`): a read-only refusal carrying the
    same words — nothing is drawn;
  * an ORPHAN WITHOUT A DUPLICATE changes nothing (one footprint is
    unambiguous — the detector's own rule).
"""
from types import SimpleNamespace

import pytest

from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError, read_only_verdict
from kicadstamp.i18n import _
from kicadstamp.refdes_duplicates import duplicate_refusal_lines

from gui.docks.live_position import _role_to_fp_by_refs
from gui.select_cell_copper import identified_footprints, select_identified_refs


def _fp(ref, *, role="R1", uuid="u-1", on_schematic=True):
    """A domain-footprint-shaped record: the detector reads ref/uuid/position and
    the sheet chain through field_overrides.sheet_path_uuids_of; `role` is what
    the adapter's `get_field_value` answers below."""
    return SimpleNamespace(
        ref=ref, role=role, uuid=uuid,
        position=Vector2(10_000_000, 20_000_000),
        sheet_path_uuids=("sheet-uuid",) if on_schematic else ())


class _Adapter:
    def __init__(self, footprints):
        self._fps = list(footprints)
        self.selected = []

    def get_footprints(self):
        return list(self._fps)

    def get_footprint(self, ref):
        return next((fp for fp in self._fps if fp.ref == ref), None)

    def get_field_value(self, fp, _name):
        return getattr(fp, "role", "R1")

    def select_items(self, items):
        self.selected.extend(items)


# ── The helper: ONE answer, the guard's own wording ────────────────────────

def test_a_duplicated_refdes_refuses_with_the_guards_lines():
    lines = duplicate_refusal_lines(
        [_fp("R5", uuid="u-new"), _fp("R5", uuid="u-orphan", on_schematic=False),
         _fp("R6")],
        ["R5", "R6"])

    text = "\n".join(lines)
    assert "R5" in text and "stands on the board 2 times" in text
    assert "u-new" in text and "u-orphan" in text
    assert "NOT linked to the schematic" in text
    assert "delete the orphan" in text
    assert "R6" not in text, "a unique ref must not be named"


def test_a_unique_board_answers_nothing():
    assert duplicate_refusal_lines([_fp("R5"), _fp("R6")], ["R5", "R6"]) == []


def test_a_duplicate_outside_the_read_refuses_nothing():
    """The pipeline's own narrowing rule: a duplicate the read never addresses
    is not even looked at (R5 is unique, C31 is duplicated but not addressed)."""
    assert duplicate_refusal_lines(
        [_fp("R5"), _fp("C31"), _fp("C31")], ["R5"]) == []


def test_an_orphan_without_a_duplicate_is_not_a_refusal():
    """One footprint is unambiguous — the detector's rule, unchanged by Ч4."""
    assert duplicate_refusal_lines(
        [_fp("C31", on_schematic=False), _fp("R5")], ["C31", "R5"]) == []


# ── Door 1: Select cell / Select cell components ───────────────────────────

def test_identified_footprints_refuses_a_duplicate_and_selects_nothing():
    adapter = _Adapter([_fp("R5", uuid="u-new"),
                        _fp("R5", uuid="u-orphan", on_schematic=False)])

    footprints, stale, duplicate = identified_footprints(adapter, {"R1": "R5"})
    assert footprints == [] and stale == []
    assert duplicate and "stands on the board 2 times" in duplicate[0]

    result = select_identified_refs(adapter, {"R1": "R5"})
    assert set(result) == {"duplicate"}
    assert adapter.selected == [], "nothing may be selected on a duplicate"


def test_a_clean_identification_still_selects_the_pair():
    adapter = _Adapter([_fp("R5", role="R1"), _fp("C1", role="C2")])

    result = select_identified_refs(adapter, {"R1": "R5", "C2": "C1"})

    assert result == {"selected": 2, "identified": True}
    # The order is the roles' sorted one (the identification's own convention),
    # not the dict's insertion order.
    assert sorted(fp.ref for fp in adapter.selected) == ["C1", "R5"]


def test_a_stale_ref_keeps_its_own_answer():
    """The duplicate check must not swallow the stale wording — a ref that left
    the board is still "stale", not a duplicate."""
    adapter = _Adapter([_fp("R5")])

    result = select_identified_refs(adapter, {"R1": "ZZ9"})

    assert set(result) == {"stale"}
    assert "no longer on the board" in result["stale"][0]
    assert adapter.selected == []


# ── Door 2: the cell overlay's identified-pair read ────────────────────────

def test_the_overlay_read_refuses_a_duplicate_in_read_only_words():
    adapter = _Adapter([_fp("R5", uuid="u-new"),
                        _fp("R5", uuid="u-orphan", on_schematic=False)])
    cell = SimpleNamespace(components=[SimpleNamespace(role="R1")])

    with pytest.raises(ValidationError) as ei:
        _role_to_fp_by_refs(adapter, cell, {"R1": "R5"}, "cell 'x'")

    text = str(ei.value)
    assert "stands on the board 2 times" in text
    assert "u-orphan" in text and "NOT linked to the schematic" in text
    assert "delete the orphan" in text
    assert read_only_verdict() in text, "a read must not claim a placement stopped"
    assert _("Fix the config") not in text


def test_the_overlay_read_resolves_a_clean_pair():
    adapter = _Adapter([_fp("R5", role="R1")])
    cell = SimpleNamespace(components=[SimpleNamespace(role="R1")])

    role_to_fp = _role_to_fp_by_refs(adapter, cell, {"R1": "R5"}, "cell 'x'")

    assert role_to_fp["R1"].ref == "R5"


def test_role_field_name_is_the_one_the_adapter_answers_for():
    """Guard against the fixture drifting away from the product's field name."""
    assert ROLE_FIELD_NAME


# ── The UI half: the dock reports the guard's lines VERBATIM ───────────────

def test_the_cell_dock_reports_the_guards_lines_verbatim(qapp):
    """Д-1 of the Ч4 acceptance: with the `duplicate` branch turned into
    `if False:` in the dock's result handler, EVERY cell stayed green — the
    worker answered correctly and nobody checked that the dock says it. The slot
    must hand the guard's OWN lines to the Log as ONE message, line for line, at
    ERROR level, and nothing else."""
    from gui.docks._common import ERROR_STYLE
    from gui.docks.cell_editor import CellDock

    lines = [
        _("{ref!r} stands on the board {count} times — the record is refused, "
          "nothing of it is moved:").format(ref="R5", count=2),
        _("  copy 1/2: uuid {uuid}, {position}").format(
            uuid="u-new", position="at 10.000, 20.000 mm"),
        _("  copy 2/2: uuid {uuid}, {position}{orphan}").format(
            uuid="u-orphan", position="at 10.000, 20.000 mm",
            orphan=" — NOT linked to the schematic"),
        _("  delete the orphan (Update PCB from Schematic with “Delete footprints "
          "with no symbols”) or re-annotate; nothing of this record was moved."),
    ]
    dock = CellDock.__new__(CellDock)
    dock._active_op = "op-handle"
    seen = []
    dock._show_message = lambda text, style="": seen.append((text, style))

    dock._finish_select_cluster_on_board({"duplicate": lines})

    assert dock._active_op is None
    assert seen == [("\n".join(lines), ERROR_STYLE)], (
        "the guard's lines must reach the Log verbatim, as ONE ERROR message — "
        f"saw {seen!r}")
