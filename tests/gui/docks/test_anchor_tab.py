# tests/gui/docks/test_anchor_tab.py
"""Tests for gui/entity/anchor_tab.py — the ENTITY page's "Anchor" tab (Role
anchor + Marker anchor) plus the pure selection/narrowing helpers and the
overlay workers it now owns.

Step 4 of plan_2026_10_09_entity_page MOVED the two anchor tabs off the CELL
page onto the ENTITY page, with a FIXED address (the entity's cell / cluster /
sheet / refs) instead of working-context comboboxes. The tests below are the
moved guards: the pure helpers, the live-cluster frame and the overlay workers
run exactly as before; the widget tests build an AnchorTabWidget and hand it a
set_context address.

THE ONE MEANING CHANGE: "Read from selection" fills Role/Pad only from the
ENTITY's own instance — a selection of a FOREIGN cluster is refused with a line
(guarded below).

Headless and board-mutation-free: the helpers run against fake adapters, and the
widget tests prove the offline write path (connection.board = None) — the live
board is needed only for "Read from selection" and the Marker tab's live frame.
"""
from types import SimpleNamespace

import pytest

import gui.entity.anchor_tab as anchor_mod
from gui.entity.anchor_tab import (
    AnchorTabWidget,
    find_pad_owner,
    read_anchor_source,
    resolve_clone_context,
    roles_for_cluster,
)
from gui.entity.page import EntityPage
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.explore import Selected
from kicadstamp.exceptions import ValidationError
from kicadstamp.utils.units import MM


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


class _FakeAdapter:
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
    monkeypatch.setattr(anchor_mod, "KipyPad", pad_cls)
    adapter = _FakeAdapter()
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
    monkeypatch.setattr(anchor_mod, "KipyPad", _dummy_pad_cls())
    adapter = _FakeAdapter()
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
    monkeypatch.setattr(anchor_mod, "KipyPad", _dummy_pad_cls())
    via = Via(uuid="v1", position=Vector2.from_xy(0, 0), net_name=None,
              drill_mm=0.3, diameter_mm=0.6)
    adapter = _FakeAdapter()
    read = read_anchor_source(adapter, [via], ["C1"], "cell1")
    assert read["kind"] == "via"          # NOT "nothing selected"
    assert read["role"] is None


def test_read_anchor_source_nothing_selected_is_fatal(monkeypatch):
    monkeypatch.setattr(anchor_mod, "KipyPad", _dummy_pad_cls())
    adapter = _FakeAdapter()
    with pytest.raises(ValidationError) as ei:
        read_anchor_source(adapter, [], ["C1"], "cell1")
    assert "nothing is selected" in str(ei.value)


def test_read_anchor_source_several_clusters_is_fatal(monkeypatch):
    monkeypatch.setattr(anchor_mod, "KipyPad", _dummy_pad_cls())
    adapter = _FakeAdapter()
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
    monkeypatch.setattr(anchor_mod, "KipyPad", _dummy_pad_cls())
    adapter = _FakeAdapter()
    a = _fp("fp1", "R1")
    adapter.footprints = [a]
    adapter.set_field("fp1", "Role", "NOT_A_ROLE")
    adapter.set_field("fp1", "Cluster", "PIF_3V3_VDD")

    with pytest.raises(ValidationError) as ei:
        read_anchor_source(adapter, [a], ["C1"], "cell1")
    assert "NOT_A_ROLE" in str(ei.value) and "cell1" in str(ei.value)


def test_find_pad_owner_by_uuid():
    adapter = _FakeAdapter()
    fp = _fp("fp1", "R1")
    adapter.footprints = [fp]
    adapter.pads_by_uuid = {"fp1": [
        SimpleNamespace(_kipy=SimpleNamespace(id=SimpleNamespace(value="p1"))),
        SimpleNamespace(_kipy=SimpleNamespace(id=SimpleNamespace(value="p2"))),
    ]}
    assert find_pad_owner(adapter, [fp], "p2") is fp
    assert find_pad_owner(adapter, [fp], "nope") is None


def test_roles_for_cluster_narrows():
    """Э3 (plan_2026_09_14_ui_thread_offenders): the hint reads the board SNAPSHOT
    (role/cluster as FIELD VALUES, explore.Selected) — never the adapter."""
    snapshot = [SimpleNamespace(role=role, cluster=cluster)
                for role, cluster in (("C1", "PIF_3V3_VDD"),
                                      ("C2", "PIF_3V3_VDD"),
                                      ("C1", "AD_DAC/IC2"),
                                      ("OTHER", "AD_DAC/IC2"))]
    roles = roles_for_cluster(snapshot, ["C1", "C2", "OTHER"], "PIF_3V3_VDD")
    assert roles == ["C1", "C2"]


def test_roles_for_cluster_falls_back_without_snapshot_or_cluster():
    """An OFFLINE session (no snapshot at all) and an empty Cluster both fall
    back to the FULL cell role list — the hint never hides a valid role."""
    assert roles_for_cluster(None, ["A", "B"], "PIF") == ["A", "B"]
    assert roles_for_cluster([], ["A", "B"], "PIF") == ["A", "B"]
    assert roles_for_cluster(None, ["A", "B"], "") == ["A", "B"]


def test_resolve_clone_context_none_one_many(monkeypatch):
    monkeypatch.setattr(anchor_mod, "clone_placement_effective_name",
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


# ── The overlay frame comes from the LIVE CLUSTER (2026-09-10) ─────────────

class _ClusterAdapter:
    """Minimal adapter for the live-cluster frame: the footprint list plus the
    Role/Cluster field reads resolve_footprint_by_cluster_role touches."""

    def __init__(self, footprints):
        self._fps = list(footprints)
        self.calls = []
        self.on_refresh = None

    def refresh_board(self):
        self.calls.append("refresh")
        if self.on_refresh is not None:
            self.on_refresh(self)

    def get_footprints(self):
        self.calls.append("get_footprints")
        return list(self._fps)

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return fp.role
        if name == CLUSTER_FIELD_NAME:
            return fp.cluster
        return None


def _live_fp(ref, role, cluster, x_mm, y_mm, angle=0.0,
             layer=BoardLayer.BL_F_Cu):
    fp = Footprint(ref=ref, uuid=f"u-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm),
                   angle_deg=angle, layer=layer)
    fp.role = role
    fp.cluster = cluster
    fp.sheet_path_uuids = []
    return fp


def _cell(**overrides):
    """A cell whose stored geometry is ORIG at (0,0) and CAP at (10,-4)."""
    comps = [SimpleNamespace(role="ORIG", offset_along_mm=0.0,
                             offset_across_mm=0.0, angle_deg=0.0),
             SimpleNamespace(role="CAP", offset_along_mm=10.0,
                             offset_across_mm=-4.0, angle_deg=0.0)]
    base = dict(name="cell1", layer="F.Cu", components=comps, anchor_xy=None,
                anchor_pad=None, anchor_role="ORIG", vias=[], tracks=[],
                clone_placements=[])
    base.update(overrides)
    return SimpleNamespace(**base)


def _live_cluster_fps(origin_x_mm=100.0, origin_y_mm=200.0, cluster="CL"):
    return [_live_fp("IC1", "ORIG", cluster, origin_x_mm, origin_y_mm),
            _live_fp("IC2", "CAP", cluster, origin_x_mm + 10.0,
                     origin_y_mm - 4.0)]


def _fatal_title(message: str) -> str:
    """The 'FATAL ERROR: ...' line of a format_fatal_error message."""
    return next(line for line in message.splitlines() if "FATAL ERROR" in line)


def test_frame_refreshes_the_board_before_reading_footprints():
    """K.1: a live read refreshes the board itself, BEFORE the first
    get_footprints()."""
    adapter = _ClusterAdapter(_live_cluster_fps())
    anchor_mod._live_cluster_frame(adapter, _cell(), "CL", "", {"MCU": "MCU"})

    assert adapter.calls[0] == "refresh"
    assert adapter.calls.count("refresh") == 1
    assert "get_footprints" in adapter.calls


def test_frame_is_built_from_the_refreshed_position():
    """THE live case (Denis, 2026-09-10): the cluster was moved in KiCad AFTER
    the connection was made — a refresh at read time fixes it."""
    adapter = _ClusterAdapter(_live_cluster_fps())
    moved = _live_fp("IC1", "ORIG", "CL", 500.0, 600.0)
    adapter.on_refresh = lambda a: a._fps.__setitem__(0, moved)

    origin, _rotation, _mirror = anchor_mod._live_cluster_frame(
        adapter, _cell(), "CL", "", {})

    assert origin.x == int(500 * MM) and origin.y == int(600 * MM)


def test_frame_comes_from_the_live_cluster_without_any_placement():
    adapter = _ClusterAdapter(_live_cluster_fps())
    origin, rotation, mirror = anchor_mod._live_cluster_frame(
        adapter, _cell(), "CL", "", {"MCU": "MCU"})
    assert origin.x == int(100 * MM) and origin.y == int(200 * MM)
    assert rotation == 0.0
    assert mirror is False


def test_frame_follows_the_cluster_not_a_placement():
    adapter = _ClusterAdapter([_live_fp("IC1", "ORIG", "CL", 20.0, 30.0)])
    origin, _rotation, _mirror = anchor_mod._live_cluster_frame(
        adapter, _cell(), "CL", "", {})
    assert origin.x == int(20 * MM) and origin.y == int(30 * MM)


def test_frame_cluster_not_on_the_board_is_an_honest_error():
    adapter = _ClusterAdapter([_live_fp("IC1", "ORIG", "OTHER", 1.0, 1.0)])
    with pytest.raises(ValidationError) as ei:
        anchor_mod._live_cluster_frame(adapter, _cell(), "PIF_OA_N2V5", "", {})
    title = _fatal_title(str(ei.value))
    assert "not on the live board" in title
    assert "placement" not in title.lower()


def test_frame_role_missing_from_the_cluster_is_an_honest_error():
    adapter = _ClusterAdapter([_live_fp("IC9", "OTHER_ROLE", "CL", 1.0, 1.0)])
    with pytest.raises(ValidationError) as ei:
        anchor_mod._live_cluster_frame(adapter, _cell(), "CL", "", {})
    title = _fatal_title(str(ei.value))
    assert "has no footprint in cluster" in title
    assert "placement" not in title.lower()


def test_frame_mirror_comes_from_the_live_footprint_side():
    back = _ClusterAdapter(
        [_live_fp("IC1", "ORIG", "CL", 0.0, 0.0, layer=BoardLayer.BL_B_Cu)])
    _origin, _rotation, mirror = anchor_mod._live_cluster_frame(
        back, _cell(), "CL", "", {})
    assert mirror is True

    front = _ClusterAdapter(
        [_live_fp("IC1", "ORIG", "CL", 0.0, 0.0, layer=BoardLayer.BL_F_Cu)])
    _origin, _rotation, mirror = anchor_mod._live_cluster_frame(
        front, _cell(layer="B.Cu"), "CL", "", {})
    assert mirror is True


class _OverlayBoard:
    def __init__(self):
        from kipy.board_types import BoardLayer
        self.names = {BoardLayer.BL_User_5: "User.KiCadStamp"}

    def get_enabled_layers(self):
        return list(self.names)

    def get_layer_name(self, layer):
        return self.names.get(layer, str(layer))

    def get_shapes(self):
        return []


class _OverlayAdapter(_ClusterAdapter):
    """The cluster adapter plus the little overlay IPC surface draw_bbox/
    draw_marker need."""

    def __init__(self, footprints):
        super().__init__(footprints)
        self._board = _OverlayBoard()
        self.created = []
        self.removed = []
        self._next_id = 0

    def create_items(self, items):
        items = list(items)
        for item in items:
            self._next_id += 1
            item.id.value = f"shape-{self._next_id}"
        self.created.extend(items)
        return items

    def select_items(self, items):
        pass

    def remove_by_ids(self, uuids):
        self.removed.extend(uuids)
        return True

    def get_enabled_layers(self):
        return self._board.get_enabled_layers()

    def get_layer_name(self, layer):
        return self._board.get_layer_name(layer)

    def get_shapes(self):
        return self._board.get_shapes()


def test_bbox_and_marker_are_drawn_over_the_live_cluster(monkeypatch):
    """Acceptance: with the cluster on the board and NO placement/tree at all,
    "Show bbox" and "Place marker" both produce a shape centred on the LIVE
    cluster's bbox."""
    import gui.board_overlay as bo
    anchor_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    adapter = _OverlayAdapter(_live_cluster_fps())
    cell = _cell()

    bbox_key = anchor_mod.overlay_markers.cell_anchor_key("/r", "c", "bbox")
    uuid = anchor_mod._ensure_bbox_worker(adapter, cell, "CL", "", {},
                                          bbox_key, "User.KiCadStamp")
    assert uuid is not None
    assert len(adapter.created) == 1
    rect = adapter.created[0]
    centre_x_mm = (rect.top_left.x + rect.bottom_right.x) / 2 / MM
    centre_y_mm = (rect.top_left.y + rect.bottom_right.y) / 2 / MM
    assert abs(centre_x_mm - 105.0) <= 0.01
    assert abs(centre_y_mm - 198.0) <= 0.01

    adapter.created.clear()
    marker_key = anchor_mod.overlay_markers.cell_anchor_key("/r", "c", "marker")
    assert anchor_mod._ensure_marker_worker(
        adapter, cell, "CL", "", {}, marker_key,
        "User.KiCadStamp") is not None


def test_ensure_marker_worker_removes_the_previous_marker_first(monkeypatch):
    """J.2: a second "Place marker" for the SAME key must leave ONE marker on
    the board (E.2.2)."""
    import gui.board_overlay as bo
    anchor_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    adapter = _OverlayAdapter(_live_cluster_fps())
    cell = _cell()
    key = anchor_mod.overlay_markers.cell_anchor_key("/r", "c", "marker")

    first = anchor_mod._ensure_marker_worker(adapter, cell, "CL", "", {},
                                             key, "User.KiCadStamp")
    assert first is not None
    assert len(adapter.created) == 1

    adapter.created.clear()
    second = anchor_mod._ensure_marker_worker(adapter, cell, "CL", "", {},
                                              key, "User.KiCadStamp")
    assert second is not None and second != first
    assert adapter.removed == [first]
    assert len(adapter.created) == 1


def test_ensure_bbox_worker_removes_the_previous_bbox_first(monkeypatch):
    import gui.board_overlay as bo
    anchor_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    adapter = _OverlayAdapter(_live_cluster_fps())
    cell = _cell()
    key = anchor_mod.overlay_markers.cell_anchor_key("/r", "c", "bbox")

    first = anchor_mod._ensure_bbox_worker(adapter, cell, "CL", "", {},
                                           key, "User.KiCadStamp")
    adapter.created.clear()
    second = anchor_mod._ensure_bbox_worker(adapter, cell, "CL", "", {},
                                            key, "User.KiCadStamp")
    assert second is not None and second != first
    assert adapter.removed == [first]
    assert len(adapter.created) == 1


def test_ensure_worker_survives_a_shape_already_swept():
    """A uuid the user already deleted in KiCad is NOT an error — the draw
    simply proceeds."""
    import gui.board_overlay as bo
    anchor_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")

    class _AngryAdapter(_OverlayAdapter):
        def remove_by_ids(self, uuids):
            raise ValidationError("no such shape")

    adapter = _AngryAdapter(_live_cluster_fps())
    key = anchor_mod.overlay_markers.cell_anchor_key("/r", "c", "marker")
    anchor_mod.settings.state.set(
        anchor_mod.overlay_markers.OVERLAY_MARKERS_KEY, {key: "stale"})
    assert anchor_mod._ensure_marker_worker(
        adapter, _cell(), "CL", "", {}, key, "User.KiCadStamp") is not None
    assert len(adapter.created) == 1


def test_bbox_worker_reports_the_honest_error(monkeypatch):
    import gui.board_overlay as bo
    anchor_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    adapter = _OverlayAdapter([_live_fp("IC1", "ORIG", "OTHER", 1.0, 1.0)])
    key = anchor_mod.overlay_markers.cell_anchor_key("/r", "c", "bbox")
    with pytest.raises(ValidationError) as ei:
        anchor_mod._ensure_bbox_worker(adapter, _cell(), "CL", "", {},
                                       key, "User.KiCadStamp")
    assert "not on the live board" in str(ei.value)


# ── The AnchorTabWidget: fixed address, offline write path ────────────────

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


def _anchor(main_window, tmp_path, data=None, cluster="CL1", sheet="",
            file_path=None):
    """An AnchorTabWidget wired to ONE entity's address the way the entity page
    does: set_root_path, then set_context(cell, cluster, sheet, file)."""
    target = tmp_path / "root.sexp"
    target.write_text(
        dict_to_sexp(data if data is not None else _cell_data(),
                     format_number=2), encoding="utf-8")
    tab = AnchorTabWidget(main_window, connection=main_window.connection,
                          parent=main_window)
    tab.set_root_path(target)
    tab.set_context("cell1", cluster, sheet,
                    file_path if file_path is not None else target,
                    entity_count=1)
    return tab, target


class _PoisonedAdapter:
    """Blows up on ANY attribute access — a UI path that still reaches the
    adapter for the Role hint fails LOUDLY instead of quietly reading the board."""

    def __getattr__(self, name):
        raise AssertionError(
            f"the board adapter was read for the Role hint: {name}")


def test_role_combo_lists_cell_components_without_board(main_window, tmp_path):
    tab, _ = _anchor(main_window, tmp_path)
    items = [tab._role_combo.itemText(i) for i in range(tab._role_combo.count())]
    assert items == ["C1", "C2"]


def test_role_hint_reads_the_fed_snapshot_not_the_board(main_window, tmp_path):
    """Э5.2: the narrowing is a HINT and takes it from the page's own snapshot;
    with a POISONED adapter the combo still narrows and nothing raises."""
    tab, _ = _anchor(main_window, tmp_path)
    main_window.connection.board = SimpleNamespace(adapter=_PoisonedAdapter())
    tab.refresh_known_roles([SimpleNamespace(role="C1", cluster="CL"),
                             SimpleNamespace(role="C2", cluster="CL"),
                             SimpleNamespace(role="C1", cluster="OTHER")])

    tab._fill_role_choices(["C1", "C2", "OTHER"], "CL")

    items = [tab._role_combo.itemText(i) for i in range(tab._role_combo.count())]
    assert items == ["C1", "C2"], "the hint read the adapter, not the snapshot"


def test_role_hint_tolerates_an_empty_snapshot(main_window, tmp_path):
    tab, _ = _anchor(main_window, tmp_path)
    main_window.connection.board = None
    tab.refresh_known_roles([])
    tab._fill_role_choices(["C1", "C2"], "CL")
    items = [tab._role_combo.itemText(i) for i in range(tab._role_combo.count())]
    assert items == ["C1", "C2"]


def test_legacy_role_pad_xy_cell_opens_in_anchor_tab(main_window, tmp_path):
    """A cell stored in the OLD anchor_xy+anchor_role+anchor_pad shape opens
    with Role/Pad prefilled."""
    data = _cell_data()
    data["cells"]["cell1"].update(
        {"anchor_xy": [-8.05, -2.795], "anchor_role": "C1", "anchor_pad": "1"})
    tab, _ = _anchor(main_window, tmp_path, data)

    assert tab._role_combo.currentText() == "C1"
    assert tab._pad_edit.text() == "1"


def test_the_applies_line_names_the_cell_and_the_entity_count(main_window,
                                                              tmp_path):
    """The line that replaces the removed write_applies_line: the anchor is one
    value per CELL, so the tab says WHICH cell and how many entities share it."""
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp(_cell_data(), format_number=2),
                      encoding="utf-8")
    tab = AnchorTabWidget(main_window, connection=main_window.connection,
                          parent=main_window)
    tab.set_root_path(target)
    tab.set_context("cell1", "CL1", "", target, entity_count=5)

    assert "cell1" in tab._applies_label.text()
    assert "5" in tab._applies_label.text()


def test_manual_role_save_works_without_board(main_window, tmp_path):
    """THE acceptance criterion of Phase C: with connection.board = None,
    hand-picking Role/Pad still writes the anchor to the file."""
    assert main_window.connection.board is None
    tab, target = _anchor(main_window, tmp_path)
    saved = []
    tab.saved.connect(lambda: saved.append(True))

    tab._role_combo.setCurrentText("C1")
    tab._pad_edit.setText("7")
    tab._on_set_component_anchor()

    entry = sexp_to_dict(target.read_text(encoding="utf-8"))["cells"]["cell1"]
    assert entry["anchor_role"] == "C1"
    assert entry["anchor_pad"] == "7"
    assert "anchor_xy" not in entry     # GUARD 1 — live pad resolution enabled
    assert saved == [True]


def test_role_only_save_drops_anchor_xy_and_pad(main_window, tmp_path):
    data = _cell_data()
    data["cells"]["cell1"]["anchor_xy"] = [5.0, 6.0]
    data["cells"]["cell1"]["anchor_role"] = "OLD"
    tab, target = _anchor(main_window, tmp_path, data)

    tab._role_combo.setCurrentText("C2")
    tab._pad_edit.clear()
    tab._on_set_component_anchor()

    entry = sexp_to_dict(target.read_text(encoding="utf-8"))["cells"]["cell1"]
    assert entry["anchor_role"] == "C2"
    assert "anchor_pad" not in entry
    assert "anchor_xy" not in entry      # stale marker anchor must not survive


def test_role_save_rejects_role_not_in_cell(main_window, tmp_path, caplog):
    tab, target = _anchor(main_window, tmp_path)
    tab._role_combo.addItem("GHOST")
    tab._role_combo.setCurrentText("GHOST")
    tab._on_set_component_anchor()
    entry = sexp_to_dict(target.read_text(encoding="utf-8"))["cells"]["cell1"]
    assert "anchor_role" not in entry
    assert any("GHOST" in r.message and "not a component" in r.message
               for r in caplog.records)


def test_clear_anchor_removes_all_three(main_window, tmp_path):
    data = _cell_data()
    data["cells"]["cell1"].update(
        {"anchor_xy": [5.0, 6.0], "anchor_role": "C1", "anchor_pad": "7"})
    tab, target = _anchor(main_window, tmp_path, data)
    tab._on_clear_anchor()
    entry = sexp_to_dict(target.read_text(encoding="utf-8"))["cells"]["cell1"]
    assert "anchor_xy" not in entry
    assert "anchor_role" not in entry
    assert "anchor_pad" not in entry


def test_read_from_selection_refuses_a_foreign_cluster(main_window, tmp_path,
                                                       caplog):
    """THE MEANING CHANGE of step 4 (Денис, 09.10.2026): the anchor belongs to
    ONE entity's instance. A selection sitting in ANOTHER cluster is refused
    with a line — never silently read as this entity's anchor."""
    tab, _ = _anchor(main_window, tmp_path, cluster="CL1")
    owner = _fp("fp1", "R1")
    adapter = _FakeAdapter(footprints=[owner])
    adapter.set_field("fp1", "Role", "C1")
    adapter.set_field("fp1", "Cluster", "SOME_OTHER_CLUSTER")
    adapter.get_selected_items = lambda: [owner]
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    before = tab._role_combo.currentText()
    caplog.clear()

    tab._on_read_from_selection()

    assert tab._role_combo.currentText() == before, (
        "a foreign cluster must not become this entity's anchor")
    assert any("SOME_OTHER_CLUSTER" in r.message
               and "THIS instance" in r.message for r in caplog.records)


def test_read_from_selection_accepts_the_entitys_own_cluster(main_window,
                                                             tmp_path):
    tab, _ = _anchor(main_window, tmp_path, cluster="CL1")
    owner = _fp("fp1", "R1")
    adapter = _FakeAdapter(footprints=[owner])
    adapter.set_field("fp1", "Role", "C1")
    adapter.set_field("fp1", "Cluster", "CL1")
    adapter.get_selected_items = lambda: [owner]
    main_window.connection.board = SimpleNamespace(adapter=adapter)

    tab._on_read_from_selection()

    assert tab._role_combo.currentText() == "C1"


# ── Phase D: settings-driven overlay geometry + cleanup (D.1/D.2) ──────────

def test_set_root_path_same_path_keeps_overlay_state(main_window, tmp_path):
    """DockHub re-sends the SAME root on every graph refresh — re-setting it
    must NOT drop the overlay keys the user is mid-edit with; only an ACTUAL
    root switch cleans up."""
    tab, target = _anchor(main_window, tmp_path)
    marker_key, bbox_key = tab._marker_key(), tab._bbox_key()
    anchor_mod.settings.state.set(anchor_mod.overlay_markers.OVERLAY_MARKERS_KEY,
                                  {marker_key: "marker-uuid-1",
                                   bbox_key: "bbox-uuid-1"})

    tab.set_root_path(target)                # same root (graph refresh)
    assert tab._overlay.has_key(marker_key)
    assert tab._overlay.has_key(bbox_key)

    tab.set_root_path(tmp_path / "other.sexp")   # real project switch
    assert not tab._overlay.has_key(marker_key)
    assert not tab._overlay.has_key(bbox_key)


def test_marker_worker_draws_with_settings_layer_and_radius(main_window,
                                                            tmp_path):
    """THE Phase-D acceptance criterion: a layer/radius changed in Settings
    really reaches create_items."""
    import gui.board_overlay as bo
    from kipy.board_types import BoardLayer
    from kicadstamp.config.models import Cell, TemplateComponentSlot
    from kicadstamp.utils.units import MM as _MM

    anchor_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    anchor_mod.settings.state.set(bo.OVERLAY_MARKER_RADIUS_KEY, 0.9)
    anchor_mod.settings.state.set(bo.OVERLAY_MARKER_STROKE_KEY, 0.05)

    cell = Cell(name="cell1", components=[TemplateComponentSlot(role="ORIG")])
    adapter = _OverlayAdapter([_live_fp("IC1", "ORIG", "CL", 100.0, 200.0)])

    key = anchor_mod.overlay_markers.cell_anchor_key("/r", "c", "marker")
    uuid = anchor_mod._ensure_marker_worker(adapter, cell, "CL", "", [],
                                            key, "User.KiCadStamp")
    assert uuid is not None
    circle = adapter.created[0]
    assert circle.layer == BoardLayer.BL_User_5
    assert circle.center.x == int(100 * _MM)
    assert circle.radius_point.x == int((100 + 0.9) * _MM)
    assert circle.attributes.stroke.width == int(0.05 * _MM)


def test_persisted_overlay_key_drives_the_button_state(main_window, tmp_path):
    """The Marker buttons are enabled from the owner's KEY presence (the map is
    the single source of truth)."""
    tab, _path = _anchor(main_window, tmp_path)
    marker_key = tab._marker_key()
    anchor_mod.settings.state.set(anchor_mod.overlay_markers.OVERLAY_MARKERS_KEY,
                                  {marker_key: "persisted-marker"})

    tab._reload_form()

    assert tab._read_marker_button.isEnabled()
    assert tab._remove_marker_button.isEnabled()
    assert not tab._hide_bbox_button.isEnabled()
    assert tab._remove_overlay_button.isEnabled()


def test_overlay_keys_persist_across_widget_recreation(main_window, tmp_path):
    """Persisted overlay keys survive a 'restart' — a fresh AnchorTabWidget over
    the same gui_state.json asks the same owner and sees them."""
    tab1, _ = _anchor(main_window, tmp_path)
    key = tab1._marker_key()
    anchor_mod.settings.state.set(anchor_mod.overlay_markers.OVERLAY_MARKERS_KEY,
                                  {key: "marker-uuid-1"})

    tab2, _ = _anchor(main_window, tmp_path)   # 'restart'
    assert tab2._overlay.has_key(tab2._marker_key())


def test_cleanup_forgets_overlay_keys(main_window, tmp_path):
    """The explicit per-cell cleanup: the cell's marker+bbox KEYS are dropped
    from the owner; offline it never dispatches IPC and never raises."""
    tab, target = _anchor(main_window, tmp_path)
    marker_key, bbox_key = tab._marker_key(), tab._bbox_key()
    anchor_mod.settings.state.set(anchor_mod.overlay_markers.OVERLAY_MARKERS_KEY,
                                  {marker_key: "marker-uuid-1",
                                   bbox_key: "bbox-uuid-1"})

    tab.cleanup()                     # no live board -> state only

    assert not tab._overlay.has_key(marker_key)
    assert not tab._overlay.has_key(bbox_key)


def test_cleanup_all_overlays_sync_removes_every_persisted_uuid(qapp,
                                                                main_window):
    """The GUI-exit cleanup: every overlay uuid the owner tracks is removed from
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
    key = anchor_mod.overlay_markers.cell_anchor_key
    anchor_mod.settings.state.set(anchor_mod.overlay_markers.OVERLAY_MARKERS_KEY, {
        key("/root/a", "cellA", "marker"): "m1",
        key("/root/a", "cellA", "bbox"): "b1",
        key("/root/b", "cellB", "marker"): "m2",
    })

    anchor_mod.cleanup_all_overlays_sync(main_window.connection, timeout_s=5.0)

    assert sorted(adapter.removed) == ["b1", "m1", "m2"]
    assert anchor_mod.overlay_markers.owner.all_uuids() == []


def test_the_anchor_writes_the_cell_template_not_the_placement(main_window,
                                                               tmp_path):
    """The anchor tabs edit cells: (the TEMPLATE), never a clone_placements
    record."""
    data = _cell_data()
    data["clone_placements"] = [{"name": "cell1", "cell": "cell1",
                                 "cluster": "CL1"}]
    tab, target = _anchor(main_window, tmp_path, data)

    tab._role_combo.setCurrentText("C1")
    tab._on_set_component_anchor()

    written = sexp_to_dict(target.read_text(encoding="utf-8"))
    assert written["cells"]["cell1"]["anchor_role"] == "C1"
    assert "anchor_role" not in written["clone_placements"][0]


# ── Step-4 wiring: the Anchor tab lives on the ENTITY page ────────────────

def test_the_entity_page_carries_the_anchor_tab_with_the_entitys_address(
        main_window, tmp_path):
    """DockHub hands the ONE AnchorTabWidget to the ENTITY page (the "Anchor"
    tab), and opening a record feeds it that record's FIXED address — its cell,
    its (cluster, sheet), its refs and how many entities share the cell (the N of
    the applies line). The old working-context page had no such address."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({
        "cells": {"cell1": {"components": [{"role": "C1"}]}},
        "entities": [{"name": "e1", "cell": "cell1", "cluster": "CL1",
                      "refs": {"C1": "C74"}},
                     {"name": "e2", "cell": "cell1", "cluster": "CL1"}]},
        format_number=2), encoding="utf-8")
    page = EntityPage(main_window)
    page.set_root_path(root)
    tab = AnchorTabWidget(main_window, connection=main_window.connection,
                          parent=main_window)
    page.add_anchor_tab(tab)

    titles = [page.tabs.tabText(i) for i in range(page.tabs.count())]
    assert "Anchor" in titles
    assert page.tabs.indexOf(tab) != -1

    page.load_entity("e1")

    assert tab._cell_name == "cell1"
    assert tab._cluster == "CL1"
    assert tab._refs == {"C1": "C74"}
    assert tab._entity_count == 2


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """У3.5: the format-3 writer resolves a reference's UUID against the ACTIVE
    GRAPH ROOT. These cells write a self-contained config."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)
