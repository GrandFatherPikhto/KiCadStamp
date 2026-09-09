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


# ── Phase D: settings-driven overlay geometry + cleanup (D.1/D.2) ──────────

def test_set_root_path_same_path_keeps_overlay_state(main_window, tmp_path):
    """Regression (Phase D): DockHub re-sends the SAME root on every graph
    refresh (_refresh_graph_dependent_choices), so re-setting it must NOT drop
    the marker/bbox the user is mid-edit with — only an ACTUAL root switch
    cleans up."""
    view, target = _make_view(main_window, tmp_path)
    view._marker_uuid = "marker-uuid-1"
    view._bbox_uuid = "bbox-uuid-1"
    view._remember_overlay("marker-uuid-1", "bbox-uuid-1")

    view.set_root_path(target)                # same root (graph refresh)
    assert view._marker_uuid == "marker-uuid-1"
    assert view._bbox_uuid == "bbox-uuid-1"

    view.set_root_path(tmp_path / "other.sexp")   # real project switch
    assert view._marker_uuid is None
    assert view._bbox_uuid is None


def test_marker_worker_draws_with_settings_layer_and_radius(main_window, tmp_path,
                                                            monkeypatch):
    """THE Phase-D acceptance criterion: a layer/radius changed in the Settings
    "Board overlay" page really reaches create_items — the drawing is wired to
    the settings, not merely stored in gui_state.json."""
    import gui.board_overlay as bo
    from kipy.board_types import BoardLayer
    from kicadstamp.config.models import Cell
    from kicadstamp.domain.geometry import Vector2
    from kicadstamp.utils.units import MM
    from gui.docks.live_position import LiveRead

    KS_LAYER = BoardLayer.BL_User_5

    class _Board:
        def __init__(self):
            self.names = {KS_LAYER: "User.KiCadStamp",
                          BoardLayer.BL_Dwgs_User: "User.Drawings"}

        def get_enabled_layers(self):
            return list(self.names)

        def get_layer_name(self, layer):
            return self.names.get(layer, str(layer))

        def get_shapes(self):
            return []

    class _Adapter:
        def __init__(self):
            self._board = _Board()
            self.created = []
            self.selected = []

        def refresh_board(self):
            pass

        def create_items(self, items):
            items = list(items)
            self.created.extend(items)
            return items

        def select_items(self, items):
            self.selected.append(list(items))

        def remove_by_ids(self, uuids):
            return True

    # The values the Settings page would have persisted.
    view_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    view_mod.settings.state.set(bo.OVERLAY_MARKER_RADIUS_KEY, 0.9)
    view_mod.settings.state.set(bo.OVERLAY_MARKER_STROKE_KEY, 0.05)

    cell = Cell(name="cell1", anchor_xy=(10.0, 5.0))
    cfg = SimpleNamespace(cells={"cell1": cell})
    clone = SimpleNamespace(cell="cell1", mirror=False)

    def _fake_read_live(adapter_, cfg_, clone_, sheet_names):
        return LiveRead(position=Vector2.from_xy(int(100 * MM), int(200 * MM)),
                        rotation_deg=0.0, footprint=None)

    monkeypatch.setattr(view_mod, "read_clone_origin_live", _fake_read_live)

    adapter = _Adapter()
    uuid = view_mod._place_marker_worker(adapter, cfg, clone, "cell1", [],
                                         "User.KiCadStamp")
    assert uuid is not None
    assert len(adapter.created) == 1
    circle = adapter.created[0]
    # Layer resolved from the settings DISPLAY name ('User.KiCadStamp').
    assert circle.layer == KS_LAYER
    assert circle.center.x == int(100 * MM)
    # Radius = the configured 0.9 mm (radius_point sits ON the circle).
    assert circle.radius_point.x == int((100 + 0.9) * MM)
    # Marker stroke = the configured 0.05 mm.
    assert circle.attributes.stroke.width == int(0.05 * MM)


def test_draw_bbox_worker_uses_settings_stroke(main_window, tmp_path, monkeypatch):
    """The bbox outline width also comes from the settings (0.22 seeded below)
    — the draw reaches create_items with the configured stroke."""
    import gui.board_overlay as bo
    from kipy.board_types import BoardLayer
    from kicadstamp.config.models import Cell
    from kicadstamp.domain.geometry import Vector2
    from kicadstamp.utils.units import MM
    from gui.docks.live_position import LiveRead

    KS_LAYER = BoardLayer.BL_User_5

    class _Board:
        def __init__(self):
            self.names = {KS_LAYER: "User.KiCadStamp"}

        def get_enabled_layers(self):
            return list(self.names)

        def get_layer_name(self, layer):
            return self.names.get(layer, str(layer))

        def get_shapes(self):
            return []

    class _Adapter:
        def __init__(self):
            self._board = _Board()
            self.created = []

        def refresh_board(self):
            pass

        def create_items(self, items):
            items = list(items)
            self.created.extend(items)
            return items

        def select_items(self, items):
            pass

        def remove_by_ids(self, uuids):
            return True

    view_mod.settings.state.set(bo.OVERLAY_BBOX_STROKE_KEY, 0.22)

    cell = Cell(name="cell1")
    cfg = SimpleNamespace(cells={"cell1": cell})
    clone = SimpleNamespace(cell="cell1", mirror=False)

    def _fake_read_live(adapter_, cfg_, clone_, sheet_names):
        return LiveRead(position=Vector2.from_xy(int(100 * MM), int(200 * MM)),
                        rotation_deg=0.0, footprint=None)

    monkeypatch.setattr(view_mod, "read_clone_origin_live", _fake_read_live)
    monkeypatch.setattr(view_mod, "cell_content_bbox", lambda entry: (0.0, 10.0, 0.0, 10.0))

    adapter = _Adapter()
    uuid = view_mod._draw_bbox_worker(adapter, cfg, clone, "cell1", [],
                                      "User.KiCadStamp")
    assert uuid is not None
    assert len(adapter.created) == 1
    rect = adapter.created[0]
    assert rect.layer == KS_LAYER
    assert rect.attributes.stroke.width == int(0.22 * MM)


def test_overlay_uuids_persist_across_view_recreation(main_window, tmp_path):
    """Persisted marker/bbox uuids survive a 'restart' — a fresh CellAnchorView
    over the same gui_state.json re-reads them (Phase D reuses the Phase-C
    cell_anchor_overlay mechanism; no second store)."""
    view1, _ = _make_view(main_window, tmp_path)
    view1._marker_uuid = "marker-uuid-1"
    view1._bbox_uuid = "bbox-uuid-1"
    view1._remember_overlay("marker-uuid-1", "bbox-uuid-1")

    view2, _ = _make_view(main_window, tmp_path)   # 'restart'
    assert view2._marker_uuid == "marker-uuid-1"
    assert view2._bbox_uuid == "bbox-uuid-1"


def test_cleanup_forgets_overlay_and_drops_persisted_uuids(main_window, tmp_path):
    """The explicit per-cell cleanup (button / page leave / root change): the
    in-memory + persisted uuids are dropped; offline it never dispatches IPC
    and never raises."""
    view, target = _make_view(main_window, tmp_path)
    view._marker_uuid = "marker-uuid-1"
    view._bbox_uuid = "bbox-uuid-1"
    view._remember_overlay("marker-uuid-1", "bbox-uuid-1")

    view.cleanup()                     # no live board -> state only

    assert view._marker_uuid is None
    assert view._bbox_uuid is None
    root = str(target)
    per_cell = (view_mod.settings.state.get(view_mod.board_overlay.OVERLAY_STATE_KEY, {})
                .get(root, {}))
    assert per_cell.get("cell1") == {"marker": None, "bbox": None}


def test_cleanup_all_overlays_sync_removes_every_persisted_uuid(qapp, main_window):
    """The GUI-exit cleanup: every persisted marker/bbox uuid is removed from
    the board (by uuid, on the worker thread) and the map is cleared."""
    from types import SimpleNamespace as _NS

    class _Adapter:
        def __init__(self):
            self.removed = []
            self._board = _NS()

        def refresh_board(self):
            pass

        def create_items(self, items):
            return list(items)

        def select_items(self, items):
            pass

        def remove_by_ids(self, uuids):
            self.removed.extend(uuids)
            return True

    adapter = _Adapter()
    main_window.connection.board = _NS(adapter=adapter)
    view_mod.settings.state.set(view_mod.board_overlay.OVERLAY_STATE_KEY, {
        "/root/a": {"cellA": {"marker": "m1", "bbox": "b1"}},
        "/root/b": {"cellB": {"marker": "m2", "bbox": None}},
    })

    view_mod.cleanup_all_overlays_sync(main_window.connection, timeout_s=5.0)

    assert sorted(adapter.removed) == ["b1", "m1", "m2"]
    assert view_mod.board_overlay.persisted_overlay_uuids() == []
