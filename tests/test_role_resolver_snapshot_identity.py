# tests/test_role_resolver_snapshot_identity.py
"""The snapshot branch of `resolve_footprint_by_role` — the А+ decision of
plan_2026_09_22_live_adapter_class (measured in Т2-4: 2 whole-board sweeps
-> 0, 668 adapter calls -> 4 for one node re-hang).

The property table (rule 35), one test per cell:

  #  snapshot               adapter                       expectation
  1  given, role unique     counting: must stay untouched  identity in memory,
                                                           object from the adapter
  2  given                  position DIFFERS from the snap  the LIVE position wins
  3  given, no match        -                              fatal, the sweep's wording
  4  given, two matches     -                              fatal "ambiguous"
  5  given, ref left board  get_footprint -> None          fatal, never the stale fp
  6  NOT given (None)       1 get_footprints + 1 field/fp   the sweep, byte for byte
  7  given, role ambiguous  -                              sheet narrowing still applies
     two matches, sheet given
  8  EMPTY list             counting: the sweep must run   an empty snapshot is NO
                                                           snapshot (the state before
                                                           the connection's first poll)

Cells left empty ON PURPOSE: nothing asserts the CLUSTER step's adapter reads,
because that step only runs when 2+ candidates survive the sheet narrowing — i.e.
exactly cell 7's shape, where it is the very thing being narrowed. A test for the
cluster prefix step belongs with that step's own change, not here.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.clone_role_resolver import (
    resolve_footprint_by_role,
)

ROLE = "R_CLK"
CLUSTER = "FPGA_OSCILL"


def _fp(ref: str, *, role=ROLE, cluster=CLUSTER, x_mm=1.0, sheet_uuids=()):
    # The chain ENDS with the footprint's own uuid — that is what
    # resolve_sheet_path_names walks (same convention as the real board read, and
    # as tests/test_imprint_capture.py's fakes); without it the path resolves to
    # an empty list and a sheet narrowing silently narrows nothing.
    path = tuple(sheet_uuids) + ((f"uuid-{ref}",) if sheet_uuids else ())
    return Footprint(ref=ref, uuid=f"uuid-{ref}", layer=BoardLayer.BL_F_Cu,
                     position=Vector2.from_xy_mm(x_mm, 2.0), angle_deg=0.0,
                     sheet_path_uuids=path)


def _selected(fp, *, role=ROLE, cluster=CLUSTER):
    """One `explore.Selected` row — it carries the identity AND the footprint the
    snapshot copied at poll time."""
    return SimpleNamespace(role=role, cluster=cluster, fp=fp)


class _CountingAdapter:
    """Counts every read, so a test can assert that the snapshot branch made NONE
    of them, and answers `get_footprint` from its OWN (live) generation."""

    def __init__(self, fps, *, live_positions=None):
        self.calls = {"get_footprints": 0, "get_field_value": 0,
                      "get_footprint": 0, "get_selected_items": 0}
        self._fps = list(fps)
        self._live_positions = dict(live_positions or {})

    def get_footprints(self):
        self.calls["get_footprints"] += 1
        return list(self._fps)

    def get_field_value(self, fp, field):
        self.calls["get_field_value"] += 1
        role = self._role_of(fp)
        return (ROLE if role == ROLE and field == ROLE_FIELD_NAME
                else CLUSTER if field == CLUSTER_FIELD_NAME and role == ROLE
                else "")

    def get_selected_items(self):
        self.calls["get_selected_items"] += 1
        return []

    def get_footprint(self, ref):
        self.calls["get_footprint"] += 1
        for fp in self._fps:
            if fp.ref == ref:
                x_mm = self._live_positions.get(ref)
                if x_mm is not None:
                    # A NEW object: the point is that the resolver returns the
                    # adapter's current generation, not the snapshot's copy.
                    return _fp(ref, x_mm=x_mm)
                return fp
        return None

    @staticmethod
    def _role_of(fp):
        return ROLE if fp.ref == "R_ANCHOR" else ""


def _resolve(adapter, *, snapshot=None, sheet=None, cluster=None,
             sheet_names=None):
    return resolve_footprint_by_role(
        adapter, ROLE, sheet, cluster, sheet_names or {}, label="probe",
        snapshot=snapshot)


def test_the_snapshot_answers_the_identity_without_a_single_adapter_sweep():
    """Cell 1 — the whole point: with a snapshot the role question costs no
    get_footprints and no per-footprint field scan."""
    anchor = _fp("R_ANCHOR")
    adapter = _CountingAdapter([anchor])
    adapter.calls = {key: 0 for key in adapter.calls}      # ignore the check below

    resolved = _resolve(adapter, snapshot=[_selected(anchor)])

    assert resolved.ref == "R_ANCHOR"
    assert adapter.calls["get_footprints"] == 0
    assert adapter.calls["get_field_value"] == 0
    assert adapter.calls["get_footprint"] == 1      # position, from the live side


def test_the_position_comes_from_the_adapter_not_from_the_snapshot():
    """Cell 2 — `Selected.fp` is as old as the poll; a resolver whose caller needs
    "where is it NOW" must re-read the ref (the Ш1 lesson, same rule)."""
    anchor = _fp("R_ANCHOR", x_mm=1.0)
    adapter = _CountingAdapter([anchor], live_positions={"R_ANCHOR": 42.0})

    resolved = _resolve(adapter, snapshot=[_selected(anchor)])

    assert resolved.position.x == 42_000_000, \
        "the snapshot's stale position leaked into the answer"


def test_a_snapshot_with_no_matching_role_is_fatal():
    """Cell 3 — the absence says the same thing it says on the sweep."""
    other = _fp("R_OTHER", role="SOMETHING_ELSE")
    adapter = _CountingAdapter([other])

    with pytest.raises(ValidationError) as excinfo:
        _resolve(adapter, snapshot=[_selected(other, role="SOMETHING_ELSE")])

    assert "not found on any component" in str(excinfo.value)
    assert adapter.calls["get_footprints"] == 0


def test_an_ambiguous_snapshot_role_is_fatal():
    """Cell 4 — two rows with the role, no sheet/cluster to narrow with."""
    first, second = _fp("R_ANCHOR"), _fp("R_ANCHOR2")
    adapter = _CountingAdapter([first, second])
    snapshot = [_selected(first), _selected(second)]
    snapshot[1].fp = second
    adapter._fps = [second]        # so get_field_value/reporting stays coherent

    with pytest.raises(ValidationError) as excinfo:
        _resolve(adapter, snapshot=snapshot)

    assert "is ambiguous" in str(excinfo.value)
    assert "R_ANCHOR" in str(excinfo.value) and "R_ANCHOR2" in str(excinfo.value)


def test_a_snapshot_ref_that_left_the_board_is_fatal_not_stale():
    """Cell 5 — deleted since the poll that built the snapshot: the canonical
    absence, never the stale object."""
    anchor = _fp("R_ANCHOR")
    adapter = _CountingAdapter([])                  # the board no longer has it

    with pytest.raises(ValidationError) as excinfo:
        _resolve(adapter, snapshot=[_selected(anchor)])

    assert "not found on any component" in str(excinfo.value)


def test_without_a_snapshot_the_sweep_is_unchanged():
    """Cell 6 — the default keeps the historical path byte for byte: one
    get_footprints, one field read per footprint, and the swept object itself."""
    anchor = _fp("R_ANCHOR")
    decoy = _fp("R_DECOY", role="")
    adapter = _CountingAdapter([anchor, decoy])

    resolved = _resolve(adapter)

    assert resolved is anchor, "the sweep must return its own object"
    assert adapter.calls["get_footprints"] == 1
    assert adapter.calls["get_field_value"] == 2    # one per footprint
    assert adapter.calls["get_footprint"] == 0      # no extra re-read


def test_the_snapshot_pair_is_narrowed_by_sheet():
    """Cell 7 — the snapshot supplies the CANDIDATES; the narrowing cascade still
    runs on them (the sheet is what tells the two clones apart)."""
    first = _fp("R_ANCHOR", sheet_uuids=("sheet-a",))
    second = _fp("R_ANCHOR_B", sheet_uuids=("sheet-b",))
    # (the chain becomes ("sheet-a", "uuid-R_ANCHOR") etc. — see _fp)
    adapter = _CountingAdapter([first, second])
    snapshot = [_selected(first), _selected(second)]

    # The sheet narrowing resolves a NAME out of the path uuids, so the map is what
    # makes "SheetB" (the anchor's `anchor_sheet`) mean the second footprint.
    resolved = _resolve(adapter, snapshot=snapshot, sheet="SheetB",
                        sheet_names={"sheet-a": "SheetA", "sheet-b": "SheetB"})

    assert resolved.ref == "R_ANCHOR_B"


def test_an_empty_snapshot_is_no_snapshot():
    """Cell 8 — `connection.snapshot` is `[]` until the first poll rebuilds it, and
    believing that emptiness would report a board nobody has looked at yet as
    "this role is on nothing". An empty list therefore takes the sweep.

    Found live: this exact shape turned
    tests/gui/test_trees_dock.py::test_move_to_recalculates_the_offset_after_the_re_hang
    from green into a fatal (whose modal then hung the suite)."""
    anchor = _fp("R_ANCHOR")
    adapter = _CountingAdapter([anchor])

    resolved = _resolve(adapter, snapshot=[])

    assert resolved is anchor, "an empty snapshot must not answer for the board"
    assert adapter.calls["get_footprints"] == 1
