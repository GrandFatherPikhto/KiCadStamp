# tests/gui/test_cell_anchor_view.py
"""Tests for gui/docks/cell_anchor_view.py — the cell-anchor editor's pure
selection/narrowing helpers AND the widget's offline write path (Phase C of
plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md).

Headless and board-mutation-free (same reasoning as test_cell_editor.py): the
selection helpers run against fake adapters, and the widget-level tests prove
the acceptance criterion — hand-picking Role/Pad and saving works with
connection.board = None (the live board is needed only for "Read from
selection" and the Marker tab's live frame).
"""
from types import SimpleNamespace

import pytest

import gui.docks.cell_anchor_view as view_mod
from gui.docks.cell_anchor_view import (
    CellAnchorView,
    find_pad_owner,
    read_anchor_source,
    resolve_clone_context,
    roles_for_cluster,
)
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.domain.board import Footprint, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError

# A fresh Pad stand-in class, patched in place of kipy's Pad per test — the
# selection helpers branch on isinstance(item, KipyPad), so a test's selected
# pad must actually be an instance of the patched class.
def _dummy_pad_cls():
    return type("Pad", (), {"number": "1"})


def _dummy_pad(uuid, pad_cls=None):
    pad = (pad_cls or _dummy_pad_cls())()
    pad.number = "1"
    pad.id = SimpleNamespace(value=uuid)
    return pad


def _fp(uuid, ref, role=None, cluster=None):
    """A domain Footprint DTO with fake Role/Cluster field values."""
    fp = Footprint(ref=ref, uuid=uuid, position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer="F.Cu")
    fp.role = role
    fp.cluster = cluster
    return fp


class FakeAdapter:
    """Duck-typed adapter for the selection helpers — footprints with
    per-uuid Role/Cluster values and per-footprint pad lists."""

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

    def get_selected_items(self):
        return []


# ── Pure selection helpers ────────────────────────────────────────────────

def test_read_anchor_source_pad(monkeypatch):
    pad_cls = _dummy_pad_cls()
    monkeypatch.setattr(view_mod, "KipyPad", pad_cls)
    adapter = FakeAdapter()
    owner = _fp("fp1", "R1", role="C1", cluster="PIF_3V3_VDD")
    adapter.footprints = [owner]
    adapter.pads_by_uuid = {"fp1": [SimpleNamespace(
        _kipy=SimpleNamespace(id=SimpleNamespace(value="p1")))]}
    adapter.set_field("fp1", "Role", "C1")
    adapter.set_field("fp1", "Cluster", "PIF_3V3_VDD")

    read = read_anchor_source(adapter, [_dummy_pad("p1", pad_cls)], ["C1"], "cell1")

    assert read["kind"] == "pad"
    assert read["role"] == "C1"
    assert read["pad"] == "1"
    assert read["cluster"] == "PIF_3V3_VDD"


def test_read_anchor_source_footprint(monkeypatch):
    monkeypatch.setattr(view_mod, "KipyPad", _dummy_pad_cls())
    adapter = FakeAdapter()
    owner = _fp("fp1", "R1")
    adapter.footprints = [owner]
    adapter.set_field("fp1", "Role", "C1")
    adapter.set_field("fp1", "Cluster", "PIF_3V3_VDD")

    read = read_anchor_source(adapter, [owner], ["C1"], "cell1")

    assert read["kind"] == "footprint"
    assert read["role"] == "C1"
    assert read["pad"] is None
    assert read["cluster"] == "PIF_3V3_VDD"


def test_read_anchor_source_via_is_marker_case(monkeypatch):
    monkeypatch.setattr(view_mod, "KipyPad", _dummy_pad_cls())
    via = Via(uuid="v1", position=Vector2.from_xy(0, 0), net_name=None,
              drill_mm=0.3, diameter_mm=0.6)
    adapter = FakeAdapter()
    read = read_anchor_source(adapter, [via], ["C1"], "cell1")
    assert read["kind"] == "via"          # NOT "nothing selected"
    assert read["role"] is None


def test_read_anchor_source_nothing_selected_is_fatal(monkeypatch):
    monkeypatch.setattr(view_mod, "KipyPad", _dummy_pad_cls())
    adapter = FakeAdapter()
    with pytest.raises(ValidationError) as ei:
        read_anchor_source(adapter, [], ["C1"], "cell1")
    assert "nothing is selected" in str(ei.value)


def test_read_anchor_source_several_clusters_is_fatal(monkeypatch):
    monkeypatch.setattr(view_mod, "KipyPad", _dummy_pad_cls())
    adapter = FakeAdapter()
    a = _fp("fp1", "R1")
    b = _fp("fp2", "R2")
    adapter.footprints = [a, b]
    adapter.set_field("fp1", "Role", "C1")
    adapter.set_field("fp1", "Cluster", "PIF_3V3_VDD")
    adapter.set_field("fp2", "Role", "U_ADC")
    adapter.set_field("fp2", "Cluster", "AD_DAC/IC2")

    with pytest.raises(ValidationError) as ei:
        read_anchor_source(adapter, [a, b], ["C1", "U_ADC"], "cell1")
    message = str(ei.value)
    assert "several clusters" in message
    assert "AD_DAC/IC2" in message and "PIF_3V3_VDD" in message


def test_read_anchor_source_role_not_in_cell_is_fatal(monkeypatch):
    monkeypatch.setattr(view_mod, "KipyPad", _dummy_pad_cls())
    adapter = FakeAdapter()
    a = _fp("fp1", "R1")
    adapter.footprints = [a]
    adapter.set_field("fp1", "Role", "NOT_A_ROLE")
    adapter.set_field("fp1", "Cluster", "PIF_3V3_VDD")

    with pytest.raises(ValidationError) as ei:
        read_anchor_source(adapter, [a], ["C1"], "cell1")
    assert "NOT_A_ROLE" in str(ei.value) and "cell1" in str(ei.value)


def test_find_pad_owner_by_uuid():
    adapter = FakeAdapter()
    fp = _fp("fp1", "R1")
    adapter.footprints = [fp]
    adapter.pads_by_uuid = {"fp1": [
        SimpleNamespace(_kipy=SimpleNamespace(id=SimpleNamespace(value="p1"))),
        SimpleNamespace(_kipy=SimpleNamespace(id=SimpleNamespace(value="p2"))),
    ]}
    assert find_pad_owner(adapter, [fp], "p2") is fp
    assert find_pad_owner(adapter, [fp], "nope") is None


def test_roles_for_cluster_narrows():
    adapter = FakeAdapter()
    for ref, role, cluster in (("R1", "C1", "PIF_3V3_VDD"),
                               ("R2", "C2", "PIF_3V3_VDD"),
                               ("R3", "C1", "AD_DAC/IC2"),
                               ("R4", "OTHER", "AD_DAC/IC2")):
        fp = _fp(ref, ref)
        adapter.footprints.append(fp)
        adapter.set_field(ref, "Role", role)
        adapter.set_field(ref, "Cluster", cluster)
    # OTHER is present on the board but only on AD_DAC/IC2 — narrowing to
    # PIF_3V3_VDD must drop it from the Role choices.
    roles = roles_for_cluster(adapter, ["C1", "C2", "OTHER"], "PIF_3V3_VDD")
    assert roles == ["C1", "C2"]


def test_roles_for_cluster_falls_back_without_adapter_or_cluster():
    assert roles_for_cluster(None, ["A", "B"], "PIF") == ["A", "B"]
    assert roles_for_cluster(None, ["A", "B"], "") == ["A", "B"]


def test_resolve_clone_context_none_one_many(monkeypatch):
    monkeypatch.setattr(view_mod, "clone_placement_effective_name",
                        lambda cp: cp.name)
    cfg = SimpleNamespace()
    one = SimpleNamespace(name="p1", cell="cell1", cluster="PIF_3V3_VDD",
                          sheet=None)
    cfg.clone_placements = [one]
    assert resolve_clone_context(cfg, "cell1", "PIF_3V3_VDD", "") is one
    assert resolve_clone_context(cfg, "other_cell", "PIF_3V3_VDD", "") is None

    two = SimpleNamespace(name="p2", cell="cell1", cluster="PIF_3V3_VDD",
                          sheet=None)
    cfg.clone_placements = [one, two]
    with pytest.raises(ValidationError) as ei:
        resolve_clone_context(cfg, "cell1", "PIF_3V3_VDD", "")
    assert "several clone placements" in str(ei.value)
    assert "p1" in str(ei.value) and "p2" in str(ei.value)


# ── Widget: the offline write path (board = None) ─────────────────────────

def _cell_data():
    return {"cells": {
        "cell1": {
            "layer": "F.Cu",
            "components": [
                {"role": "C1", "offset_along_mm": 1.0, "offset_across_mm": -2.0},
                {"role": "C2", "offset_along_mm": 3.0, "offset_across_mm": -4.0},
            ],
            "vias": [],
            "tracks": [],
            "clone_placements": [],
        }
    }}


def _make_view(main_window, tmp_path, data=None):
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp(data if data is not None else _cell_data()),
                      encoding="utf-8")
    view = CellAnchorView(main_window, connection=main_window.connection)
    view.set_root_path(target)
    view.load_entry("cell1", target)
    return view, target


def test_role_combo_lists_cell_components_without_board(main_window, tmp_path):
    view, _ = _make_view(main_window, tmp_path)
    items = [view._role_combo.itemText(i) for i in range(view._role_combo.count())]
    assert items == ["C1", "C2"]


def test_manual_role_save_works_without_board(main_window, tmp_path):
    """THE acceptance criterion of Phase C: with connection.board = None,
    hand-picking Role/Pad still writes the anchor to the file."""
    assert main_window.connection.board is None
    view, target = _make_view(main_window, tmp_path)
    saved = []
    view.saved.connect(lambda: saved.append(True))

    view._role_combo.setCurrentText("C1")
    view._pad_edit.setText("7")
    view._on_set_component_anchor()

    data = sexp_to_dict(target.read_text(encoding="utf-8"))
    entry = data["cells"]["cell1"]
    assert entry["anchor_role"] == "C1"
    assert entry["anchor_pad"] == "7"
    assert "anchor_xy" not in entry     # GUARD 1 — live pad resolution enabled
    assert saved == [True]


def test_role_only_save_drops_anchor_xy_and_pad(main_window, tmp_path):
    data = _cell_data()
    data["cells"]["cell1"]["anchor_xy"] = [5.0, 6.0]
    data["cells"]["cell1"]["anchor_role"] = "OLD"
    view, target = _make_view(main_window, tmp_path, data)

    view._role_combo.setCurrentText("C2")
    view._pad_edit.clear()
    view._on_set_component_anchor()

    entry = sexp_to_dict(target.read_text(encoding="utf-8"))["cells"]["cell1"]
    assert entry["anchor_role"] == "C2"
    assert "anchor_pad" not in entry
    assert "anchor_xy" not in entry      # stale marker anchor must not survive


def test_role_save_rejects_role_not_in_cell(main_window, tmp_path, caplog):
    view, target = _make_view(main_window, tmp_path)
    # The Role combo is closed (cell components only), but the guard must also
    # hold for any stray value — add one to exercise the check.
    view._role_combo.addItem("GHOST")
    view._role_combo.setCurrentText("GHOST")
    view._on_set_component_anchor()
    entry = sexp_to_dict(target.read_text(encoding="utf-8"))["cells"]["cell1"]
    assert "anchor_role" not in entry
    assert any("GHOST" in r.message and "not a component" in r.message
               for r in caplog.records)


def test_clear_anchor_removes_all_three(main_window, tmp_path):
    data = _cell_data()
    data["cells"]["cell1"].update(
        {"anchor_xy": [5.0, 6.0], "anchor_role": "C1", "anchor_pad": "7"})
    view, target = _make_view(main_window, tmp_path, data)
    view._on_clear_anchor()
    entry = sexp_to_dict(target.read_text(encoding="utf-8"))["cells"]["cell1"]
    assert "anchor_xy" not in entry
    assert "anchor_role" not in entry
    assert "anchor_pad" not in entry


def test_cluster_narrowing_updates_role_combo(main_window, tmp_path):
    """With a (fake) live board, picking the working Cluster narrows the Role
    combo to the roles present on that cluster (C.3)."""
    adapter = FakeAdapter()
    for ref, role, cluster in (("R1", "C1", "PIF_3V3_VDD"),
                               ("R2", "C2", "AD_DAC/IC2")):
        fp = _fp(ref, ref)
        adapter.footprints.append(fp)
        adapter.set_field(ref, "Role", role)
        adapter.set_field(ref, "Cluster", cluster)
    main_window.connection.board = SimpleNamespace(adapter=adapter)

    view, _ = _make_view(main_window, tmp_path)
    # Start with every cell role available (no cluster picked yet).
    assert [view._role_combo.itemText(i) for i in range(view._role_combo.count())] \
        == ["C1", "C2"]

    view._cluster_combo.setCurrentText("PIF_3V3_VDD")   # triggers narrowing
    items = [view._role_combo.itemText(i) for i in range(view._role_combo.count())]
    assert items == ["C1"]
