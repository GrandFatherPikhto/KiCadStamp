# tests/gui/test_anchor_selection_core.py
"""Ч2-1 of plan_2026_10_14_tree_group_move_and_mount_rebase: the SHARED half of
the board-selection classification.

The entity page's Anchor tab (`read_anchor_source`) and the mount node's
"Change anchor from selection…" (Ч2) ask the SAME question about a board
selection — which component, which pad, which cluster — and it is answered ONCE,
in `gui.entity.anchor_workers.classify_selection_anchor`. What this file pins is
the SPLIT the plan asks for:

  * the selection's OWN defects (nothing selected, several clusters, several
    roles, a pad whose owner is gone, a footprint with no Role) are refused by
    the shared half, in the wording the tab has always shown;
  * the CELL check ("is this role a component of THIS cell") is NOT in it — a
    mount node has no cell, so the check belongs to the caller that has one
    (`read_anchor_source`), and the last cell below is exactly that boundary;
  * the FOOTPRINT travels back with the answer: the anchor itself is
    role/cluster/pad, while the sheet the component stands on can only be
    resolved from the footprint (cell: a root-sheet component's chain is empty,
    and its instance identity is ROOT_SHEET).
"""
from types import SimpleNamespace

import pytest

from kicadstamp.domain.board import Footprint, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.sheet_names import ROOT_SHEET, resolve_sheet_path_names, sheet_of_names

import gui.entity.anchor_workers as workers_mod
from gui.entity.anchor_workers import classify_selection_anchor


def _dummy_pad_cls():
    return type("Pad", (), {"number": "1"})


def _dummy_pad(uuid, pad_cls):
    pad = pad_cls()
    pad.number = "3"
    pad.id = SimpleNamespace(value=uuid)
    return pad


def _fp(uuid, ref, role=None, cluster=None, sheet_chain=None):
    """A domain Footprint DTO with fake Role/Cluster field values. `sheet_chain`
    is the raw `sheet_path_uuids` a footprint carries (the LAST uuid is the
    symbol's own and is dropped by resolve_sheet_path_names)."""
    fp = Footprint(ref=ref, uuid=uuid, position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer="F.Cu")
    fp.role = role
    fp.cluster = cluster
    if sheet_chain is not None:
        fp.sheet_path_uuids = list(sheet_chain)
    return fp


class _FakeAdapter:
    """Duck-typed adapter for the pure selection helpers (the same shape
    tests/gui/docks/test_anchor_tab.py uses for the tab's own cells)."""

    def __init__(self, footprints=None, pads_by_uuid=None):
        self.footprints = footprints or []
        self.pads_by_uuid = pads_by_uuid or {}
        self.field_values = {}

    def set_field(self, uuid, name, value):
        self.field_values[(uuid, name)] = value

    def get_footprints(self):
        return list(self.footprints)

    def get_field_value(self, fp, field_name):
        return self.field_values.get((getattr(fp, "uuid", None), field_name))

    def get_footprint_pads(self, fp):
        return self.pads_by_uuid.get(getattr(fp, "uuid", None), [])


def _adapter_with_pad(monkeypatch, pad_uuid="p1"):
    """(adapter, owner, pad) — one footprint owning one pad."""
    pad_cls = _dummy_pad_cls()
    monkeypatch.setattr(workers_mod, "KipyPad", pad_cls)
    owner = _fp("fp1", "R1", role="C1", cluster="PIF_3V3_VDD")
    adapter = _FakeAdapter(
        [owner],
        {"fp1": [SimpleNamespace(_kipy=SimpleNamespace(
            id=SimpleNamespace(value=pad_uuid)))]})
    adapter.set_field("fp1", "Role", "C1")
    adapter.set_field("fp1", "Cluster", "PIF_3V3_VDD")
    return adapter, owner, _dummy_pad(pad_uuid, pad_cls)


# ── the happy cells: a footprint and a pad ────────────────────────────────

def test_a_selected_footprint_answers_without_a_pad(monkeypatch):
    monkeypatch.setattr(workers_mod, "KipyPad", _dummy_pad_cls())
    owner = _fp("fp1", "R1")
    adapter = _FakeAdapter([owner])
    adapter.set_field("fp1", "Role", "C1")
    adapter.set_field("fp1", "Cluster", "PIF_3V3_VDD")

    read = classify_selection_anchor(adapter, [owner], "probe")

    assert read["kind"] == "footprint"
    assert read["role"] == "C1"
    assert read["pad"] is None
    assert read["cluster"] == "PIF_3V3_VDD"
    assert read["footprint"] is owner, "the caller needs the footprint for the sheet"


def test_a_selected_pad_answers_with_its_number_and_its_owner(monkeypatch):
    adapter, owner, pad = _adapter_with_pad(monkeypatch)

    read = classify_selection_anchor(adapter, [pad], "probe")

    assert read["kind"] == "pad"
    assert read["role"] == "C1"
    assert read["pad"] == "3"
    assert read["cluster"] == "PIF_3V3_VDD"
    assert read["footprint"] is owner


def test_a_via_is_not_an_error_but_not_an_anchor_either(monkeypatch):
    monkeypatch.setattr(workers_mod, "KipyPad", _dummy_pad_cls())
    via = Via(uuid="v1", position=Vector2.from_xy(0, 0), net_name=None,
              drill_mm=0.3, diameter_mm=0.6)

    read = classify_selection_anchor(_FakeAdapter(), [via], "probe")

    assert read["kind"] == "via", "the caller says 'this is the Marker tab's case'"
    assert (read["role"], read["pad"], read["cluster"]) == (None, None, None)
    assert read["footprint"] is None


# ── the footprint is what the SHEET is resolved from (Ч2 п.2) ─────────────

def test_a_root_sheet_component_resolves_to_the_root_marker(monkeypatch):
    """The plan's cell "деталь на корневом листе → sheet \"/\"" — and the
    division of labour it pins: the shared half hands over the FOOTPRINT, the
    sheet is derived from it by the ONE owner of that derivation
    (resolve_sheet_path_names + sheet_of_names), never guessed here."""
    monkeypatch.setattr(workers_mod, "KipyPad", _dummy_pad_cls())
    owner = _fp("fp1", "R1", sheet_chain=("own-uuid",))   # nothing above the symbol
    adapter = _FakeAdapter([owner])
    adapter.set_field("fp1", "Role", "C1")

    read = classify_selection_anchor(adapter, [owner], "probe")

    assert resolve_sheet_path_names(read["footprint"], {}) == []
    assert sheet_of_names(resolve_sheet_path_names(read["footprint"], {})) == ROOT_SHEET


# ── the selection's own defects ───────────────────────────────────────────

def test_nothing_selected_is_a_fatal(monkeypatch):
    monkeypatch.setattr(workers_mod, "KipyPad", _dummy_pad_cls())
    with pytest.raises(ValidationError) as ei:
        classify_selection_anchor(_FakeAdapter(), [], "probe")
    assert "nothing is selected" in str(ei.value)
    assert "probe" in str(ei.value), "the caller's label names the place"


def test_two_clusters_are_a_fatal(monkeypatch):
    monkeypatch.setattr(workers_mod, "KipyPad", _dummy_pad_cls())
    a = _fp("fp1", "R1")
    b = _fp("fp2", "R2")
    adapter = _FakeAdapter([a, b])
    adapter.set_field("fp1", "Cluster", "PIF_3V3_VDD")
    adapter.set_field("fp2", "Cluster", "AD_DAC/IC2")

    with pytest.raises(ValidationError) as ei:
        classify_selection_anchor(adapter, [a, b], "probe")

    assert "several clusters" in str(ei.value)
    assert "PIF_3V3_VDD" in str(ei.value) and "AD_DAC/IC2" in str(ei.value)


def test_two_roles_are_a_fatal(monkeypatch):
    monkeypatch.setattr(workers_mod, "KipyPad", _dummy_pad_cls())
    a = _fp("fp1", "R1")
    b = _fp("fp2", "R2")
    adapter = _FakeAdapter([a, b])
    adapter.set_field("fp1", "Role", "C1")
    adapter.set_field("fp1", "Cluster", "ONE")
    adapter.set_field("fp2", "Role", "U_ADC")
    adapter.set_field("fp2", "Cluster", "ONE")

    with pytest.raises(ValidationError) as ei:
        classify_selection_anchor(adapter, [a, b], "probe")

    assert "several roles" in str(ei.value)


def test_a_footprint_without_a_role_field_is_a_fatal(monkeypatch):
    monkeypatch.setattr(workers_mod, "KipyPad", _dummy_pad_cls())
    owner = _fp("fp1", "R1")
    adapter = _FakeAdapter([owner])
    adapter.set_field("fp1", "Cluster", "CL")

    with pytest.raises(ValidationError) as ei:
        classify_selection_anchor(adapter, [owner], "probe")

    assert "no Role field" in str(ei.value)


def test_a_pad_whose_owner_is_gone_is_a_fatal(monkeypatch):
    pad_cls = _dummy_pad_cls()
    monkeypatch.setattr(workers_mod, "KipyPad", pad_cls)
    adapter = _FakeAdapter([_fp("fp1", "R1")], {})

    with pytest.raises(ValidationError) as ei:
        classify_selection_anchor(adapter, [_dummy_pad("gone", pad_cls)], "probe")

    assert "no footprint owner" in str(ei.value)


def test_two_footprints_of_one_cluster_and_role_still_answer(monkeypatch):
    """The shared half does NOT count the items: two footprints of the SAME
    cluster carrying the SAME role answer exactly like one would. "Exactly one
    footprint" is the CALLER's rule (Ч2 п.1 — the mount action refuses two
    selected footprints), and putting that rule here would silently change the
    entity page's own behaviour, which reads a multi-select happily."""
    monkeypatch.setattr(workers_mod, "KipyPad", _dummy_pad_cls())
    a = _fp("fp1", "R1")
    b = _fp("fp2", "R1")
    adapter = _FakeAdapter([a, b])
    for uuid in ("fp1", "fp2"):
        adapter.set_field(uuid, "Role", "C1")
        adapter.set_field(uuid, "Cluster", "CL")

    read = classify_selection_anchor(adapter, [a, b], "probe")

    assert read["kind"] == "footprint" and read["role"] == "C1"


# ── the boundary of the split: the CELL check is NOT here ─────────────────

def test_the_cell_check_is_the_callers_and_not_the_shared_halves(monkeypatch):
    """Ч2-1's whole point. A role that is not a component of "the cell in hand"
    is refused by `read_anchor_source` — and NOT by the shared half, which has
    no cell to judge against. A mount node's anchor comes through exactly this
    door, so the shared half must stay silent about cells."""
    monkeypatch.setattr(workers_mod, "KipyPad", _dummy_pad_cls())
    owner = _fp("fp1", "R1")
    adapter = _FakeAdapter([owner])
    adapter.set_field("fp1", "Role", "NOT_A_ROLE")
    adapter.set_field("fp1", "Cluster", "CL")

    read = classify_selection_anchor(adapter, [owner], "probe")

    assert read["role"] == "NOT_A_ROLE", \
        "the shared half must not know about any cell's component list"
