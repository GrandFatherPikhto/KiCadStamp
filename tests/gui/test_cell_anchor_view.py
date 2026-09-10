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
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.explore import Selected
from kicadstamp.exceptions import ValidationError
from kicadstamp.utils.units import MM

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


# ── The overlay frame comes from the LIVE CLUSTER (2026-09-10, plan
#    overlay_frame_from_cluster) ────────────────────────────────────────────
#
# The frame used to come from a PLACEMENT (the union of top-level
# clone_placements and entity placements materialized from the trees). That
# dead-ended right after an extract (Entity exists, no tree node yet) and made
# the overlay follow a stale placement instead of the cluster standing on the
# board. Now only the live cluster is used.

class _ClusterAdapter:
    """Minimal adapter for the live-cluster frame: the footprint list plus the
    Role/Cluster field reads resolve_footprint_by_cluster_role touches.

    `calls` records the live-read sequence and `on_refresh` lets a test MOVE a
    footprint at refresh time — the K.1 (2026-09-10) regression: the GUI poll
    tick is a no-op while connected, so a live read must refresh the board
    itself, and it must do so BEFORE reading footprints."""

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
    """A cell whose stored geometry is ORIG at (0,0) and CAP at (10,-4) — the
    live footprints below stand exactly there, so the frame must reproduce the
    cell's own bbox."""
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
    """The 'FATAL ERROR: ...' line of a format_fatal_error message. The shared
    formatter also appends a generic footer ("Placement stopped, board not
    modified...") whose wording is not the error under test — the honest-error
    assertions look at the title line only."""
    return next(line for line in message.splitlines() if "FATAL ERROR" in line)


def test_frame_refreshes_the_board_before_reading_footprints():
    """K.1 (2026-09-10, plan stale_board_snapshot): a live read refreshes the
    board itself — the GUI's automatic poll tick is a deliberate no-op while
    connected — and it does so BEFORE the first get_footprints()."""
    adapter = _ClusterAdapter(_live_cluster_fps())
    view_mod._live_cluster_frame(adapter, _cell(), "CL", "", {"MCU": "MCU"})

    assert adapter.calls[0] == "refresh"
    assert adapter.calls.count("refresh") == 1
    assert "get_footprints" in adapter.calls


def test_frame_is_built_from_the_refreshed_position():
    """THE live case (Denis, 2026-09-10): the cluster was moved in KiCad AFTER
    the connection was made and "Show bbox" drew the rectangle at the OLD
    position — the cache was stale. A refresh at read time fixes it."""
    adapter = _ClusterAdapter(_live_cluster_fps())
    moved = _live_fp("IC1", "ORIG", "CL", 500.0, 600.0)
    adapter.on_refresh = lambda a: a._fps.__setitem__(0, moved)

    origin, _rotation, _mirror = view_mod._live_cluster_frame(
        adapter, _cell(), "CL", "", {})

    assert origin.x == int(500 * MM) and origin.y == int(600 * MM)


def test_frame_comes_from_the_live_cluster_without_any_placement():
    """THE live case (2026-09-10, 14:38): the cell was just extracted — an Entity
    exists, there is NO tree node and NO clone_placement at all, yet the cluster
    stands on the board. The frame must come from it."""
    adapter = _ClusterAdapter(_live_cluster_fps())
    origin, rotation, mirror = view_mod._live_cluster_frame(
        adapter, _cell(), "CL", "", {"MCU": "MCU"})
    assert origin.x == int(100 * MM) and origin.y == int(200 * MM)
    assert rotation == 0.0
    assert mirror is False


def test_frame_follows_the_cluster_not_a_placement():
    """The frame is where the cluster IS: the anchor component stands 20 mm away
    from where a stale record would have put it, and the frame reads THAT."""
    adapter = _ClusterAdapter([_live_fp("IC1", "ORIG", "CL", 20.0, 30.0)])
    origin, _rotation, _mirror = view_mod._live_cluster_frame(
        adapter, _cell(), "CL", "", {})
    assert origin.x == int(20 * MM) and origin.y == int(30 * MM)


def test_frame_cluster_not_on_the_board_is_an_honest_error():
    """No such cluster on the board -> say so, and never mention a placement."""
    adapter = _ClusterAdapter([_live_fp("IC1", "ORIG", "OTHER", 1.0, 1.0)])
    with pytest.raises(ValidationError) as ei:
        view_mod._live_cluster_frame(adapter, _cell(), "PIF_OA_N2V5", "", {})
    title = _fatal_title(str(ei.value))
    assert "not on the live board" in title
    assert "placement" not in title.lower()


def test_frame_role_missing_from_the_cluster_is_an_honest_error():
    """The cluster is there but none of the cell's roles resolves in it."""
    adapter = _ClusterAdapter([_live_fp("IC9", "OTHER_ROLE", "CL", 1.0, 1.0)])
    with pytest.raises(ValidationError) as ei:
        view_mod._live_cluster_frame(adapter, _cell(), "CL", "", {})
    title = _fatal_title(str(ei.value))
    assert "has no footprint in cluster" in title
    assert "placement" not in title.lower()


def test_frame_mirror_comes_from_the_live_footprint_side():
    """Mirror is read from the live footprint's own layer against the cell's."""
    back = _ClusterAdapter(
        [_live_fp("IC1", "ORIG", "CL", 0.0, 0.0, layer=BoardLayer.BL_B_Cu)])
    # Cell on F.Cu, live component on B.Cu -> mirrored.
    _origin, _rotation, mirror = view_mod._live_cluster_frame(
        back, _cell(), "CL", "", {})
    assert mirror is True

    front = _ClusterAdapter(
        [_live_fp("IC1", "ORIG", "CL", 0.0, 0.0, layer=BoardLayer.BL_F_Cu)])
    # Cell on B.Cu, live component on F.Cu -> mirrored too (opposite sides).
    _origin, _rotation, mirror = view_mod._live_cluster_frame(
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
        # A real board stamps each created shape with its own id — the fake must
        # too, or both draws would come back as the same empty uuid and J.2's
        # "old shape replaced by a NEW one" could not be told apart.
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


def test_bbox_and_marker_are_drawn_over_the_live_cluster(monkeypatch):
    """Acceptance: with the cluster on the board and NO placement/tree at all,
    "Show bbox" and "Place marker" both produce a shape — and the rectangle is
    centred on the LIVE cluster's bbox (the stored cell geometry, placed at the
    live frame)."""
    import gui.board_overlay as bo
    view_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    adapter = _OverlayAdapter(_live_cluster_fps())
    cell = _cell()

    uuid = view_mod._draw_bbox_worker(adapter, cell, "CL", "", {},
                                      "User.KiCadStamp")
    assert uuid is not None
    assert len(adapter.created) == 1
    rect = adapter.created[0]
    # Stored bbox is along 0..10 / across -4..0 -> centre (5,-2); the origin is
    # IC1 at the live (100, 200) mm -> the drawn centre must be (105, 198) mm,
    # which IS the live footprints' own bbox centre.
    centre_x_mm = (rect.top_left.x + rect.bottom_right.x) / 2 / MM
    centre_y_mm = (rect.top_left.y + rect.bottom_right.y) / 2 / MM
    assert abs(centre_x_mm - 105.0) <= 0.01
    assert abs(centre_y_mm - 198.0) <= 0.01

    adapter.created.clear()
    assert view_mod._place_marker_worker(
        adapter, cell, "CL", "", {}, "User.KiCadStamp") is not None


def test_replace_marker_worker_removes_the_previous_marker_first(monkeypatch):
    """J.2 (2026-09-10, Denis: "Если он есть, его не надо рисовать ещё!"):
    a second "Place marker" must leave ONE marker on the board — the remembered
    uuid is removed in the SAME worker operation, before the new draw."""
    import gui.board_overlay as bo
    view_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    adapter = _OverlayAdapter(_live_cluster_fps())
    cell = _cell()

    first = view_mod._replace_marker_worker(adapter, cell, "CL", "", {},
                                            "User.KiCadStamp", [])
    assert first is not None
    assert len(adapter.created) == 1

    adapter.created.clear()
    second = view_mod._replace_marker_worker(adapter, cell, "CL", "", {},
                                             "User.KiCadStamp", [first])
    assert second is not None and second != first
    assert adapter.removed == [first]        # the old shape is gone...
    assert len(adapter.created) == 1         # ...and exactly ONE was drawn


def test_replace_bbox_worker_removes_the_previous_bbox_first(monkeypatch):
    """The bbox twin of the marker replacement — a stale rectangle left at the
    previous position was the "marker doesn't land in the bbox" complaint."""
    import gui.board_overlay as bo
    view_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    adapter = _OverlayAdapter(_live_cluster_fps())
    cell = _cell()

    first = view_mod._replace_bbox_worker(adapter, cell, "CL", "", {},
                                          "User.KiCadStamp", [])
    adapter.created.clear()
    second = view_mod._replace_bbox_worker(adapter, cell, "CL", "", {},
                                           "User.KiCadStamp", [first])
    assert second is not None and second != first
    assert adapter.removed == [first]
    assert len(adapter.created) == 1


def test_replace_worker_survives_a_shape_already_swept():
    """A uuid the user already deleted in KiCad (or a stale one from a previous
    session) is NOT an error — the draw simply proceeds."""
    import gui.board_overlay as bo
    view_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")

    class _AngryAdapter(_OverlayAdapter):
        def remove_by_ids(self, uuids):
            raise ValidationError("no such shape")

    adapter = _AngryAdapter(_live_cluster_fps())
    assert view_mod._replace_marker_worker(
        adapter, _cell(), "CL", "", {}, "User.KiCadStamp", ["stale"]) is not None
    assert len(adapter.created) == 1


def test_bbox_worker_reports_the_honest_error(monkeypatch):
    """The worker lets the frame's honest error through (no placement wording)."""
    import gui.board_overlay as bo
    view_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    adapter = _OverlayAdapter([_live_fp("IC1", "ORIG", "OTHER", 1.0, 1.0)])
    with pytest.raises(ValidationError) as ei:
        view_mod._draw_bbox_worker(adapter, _cell(), "CL", "", {},
                                   "User.KiCadStamp")
    assert "not on the live board" in str(ei.value)


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


def test_legacy_role_pad_xy_cell_opens_in_anchor_page(main_window, tmp_path):
    """Фаза B regression: a cell stored in the OLD anchor_xy+anchor_role+
    anchor_pad shape still opens correctly in the new "Cell anchor" page —
    Role/Pad prefilled, no crash. The stored anchor_xy keeps governing the
    mount (GUARD 1); this page is where it is edited/cleared."""
    data = _cell_data()
    data["cells"]["cell1"].update(
        {"anchor_xy": [-8.05, -2.795], "anchor_role": "C1", "anchor_pad": "1"})
    view, _ = _make_view(main_window, tmp_path, data)

    assert view._role_combo.currentText() == "C1"
    assert view._pad_edit.text() == "1"


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


def test_marker_worker_draws_with_settings_layer_and_radius(main_window, tmp_path):
    """THE Phase-D acceptance criterion: a layer/radius changed in the Settings
    "Board overlay" page really reaches create_items — the drawing is wired to
    the settings, not merely stored in gui_state.json.

    2026-09-10 (plan overlay_frame_from_cluster): the marker's frame now comes
    from the LIVE CLUSTER, so the fake adapter serves live footprints for the
    working cluster instead of a monkeypatched read_clone_origin_live."""
    import gui.board_overlay as bo
    from kipy.board_types import BoardLayer
    from kicadstamp.config.models import Cell, TemplateComponentSlot
    from kicadstamp.utils.units import MM

    # The values the Settings page would have persisted.
    view_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    view_mod.settings.state.set(bo.OVERLAY_MARKER_RADIUS_KEY, 0.9)
    view_mod.settings.state.set(bo.OVERLAY_MARKER_STROKE_KEY, 0.05)

    # A cell whose only role stands live at 100/200 mm — no anchor, so the
    # mount is the ORIG slot itself.
    cell = Cell(name="cell1", components=[TemplateComponentSlot(role="ORIG")])
    adapter = _OverlayAdapter([_live_fp("IC1", "ORIG", "CL", 100.0, 200.0)])

    uuid = view_mod._place_marker_worker(adapter, cell, "CL", "", [],
                                         "User.KiCadStamp")
    assert uuid is not None
    assert len(adapter.created) == 1
    circle = adapter.created[0]
    # Layer resolved from the settings DISPLAY name ('User.KiCadStamp').
    assert circle.layer == BoardLayer.BL_User_5
    # The marker sits on the live cluster's mount (IC1 at 100/200 mm).
    assert circle.center.x == int(100 * MM)
    # Radius = the configured 0.9 mm (radius_point sits ON the circle).
    assert circle.radius_point.x == int((100 + 0.9) * MM)
    # Marker stroke = the configured 0.05 mm.
    assert circle.attributes.stroke.width == int(0.05 * MM)


def test_draw_bbox_worker_uses_settings_stroke(main_window, tmp_path, monkeypatch):
    """The bbox outline width also comes from the settings (0.22 seeded below)
    — the draw reaches create_items with the configured stroke.

    2026-09-10 (plan overlay_frame_from_cluster): the frame comes from the LIVE
    CLUSTER now; the stored cell bbox itself is still read through
    cell_content_bbox, so that is what stays stubbed here."""
    import gui.board_overlay as bo
    from kipy.board_types import BoardLayer
    from kicadstamp.config.models import Cell, TemplateComponentSlot
    from kicadstamp.utils.units import MM

    view_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    view_mod.settings.state.set(bo.OVERLAY_BBOX_STROKE_KEY, 0.22)

    cell = Cell(name="cell1", components=[TemplateComponentSlot(role="ORIG")])
    adapter = _OverlayAdapter([_live_fp("IC1", "ORIG", "CL", 100.0, 200.0)])
    monkeypatch.setattr(view_mod, "cell_content_bbox",
                        lambda entry: (0.0, 10.0, 0.0, 10.0))

    uuid = view_mod._draw_bbox_worker(adapter, cell, "CL", "", [],
                                      "User.KiCadStamp")
    assert uuid is not None
    assert len(adapter.created) == 1
    rect = adapter.created[0]
    assert rect.layer == BoardLayer.BL_User_5
    assert rect.attributes.stroke.width == int(0.22 * MM)


def test_stale_overlay_uuids_merge_memory_and_persistence(main_window, tmp_path):
    """J.2: the uuids handed to the replace-worker are BOTH the in-memory one
    and the persisted one (a leftover from a previous session must be replaced
    too), without duplicates."""
    view, _path = _make_view(main_window, tmp_path)
    view._cell_name = "cell1"
    view._marker_uuid = "mem-marker"
    view._bbox_uuid = None
    view._remember_overlay("persisted-marker", None)

    assert view._stale_overlay_uuids("marker") == ["mem-marker",
                                                   "persisted-marker"]
    assert view._stale_overlay_uuids("bbox") == []


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


# ── G.2: the Sheet narrows the Cluster list ───────────────────────────────

def _sel_on_sheet(ref, cluster, sheet_uuid, role=None):
    """A Selected whose footprint resolves to ONE sheet segment via
    sheet_uuid. `.sheet` is left DEGENERATE (all None) on purpose — that is what
    a live Board produces (Board.connect passes no schematic_dir), and what
    snapshot_with_resolved_sheets has to fix."""
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer="F.Cu",
                   sheet_path_uuids=(sheet_uuid, "comp"))
    return Selected(ref=ref, role=role, cluster=cluster, sheet=[None],
                    nets={}, fp=fp)


def _feed_snapshot(view, sheet_names):
    view._sheet_names = dict(sheet_names)
    view.refresh_known_roles([
        _sel_on_sheet("R1", "PIF_3V3_VDD", "mcu"),
        _sel_on_sheet("R2", "FPGA", "fpga"),
    ])


def _clusters(view):
    return [view._cluster_combo.itemText(i)
            for i in range(view._cluster_combo.count())]


def test_sheet_narrows_the_cluster_list(main_window, tmp_path):
    view, _ = _make_view(main_window, tmp_path)
    _feed_snapshot(view, {"mcu": "MCU", "fpga": "FPGA"})
    assert _clusters(view) == ["FPGA", "PIF_3V3_VDD"]

    view._sheet_combo.setCurrentText("MCU")     # fires _on_sheet_changed
    assert _clusters(view) == ["PIF_3V3_VDD"]


def test_degenerate_live_sheets_are_re_resolved(main_window, tmp_path):
    """THE G.2 trap: a live snapshot's .sheet is a list of None. After the
    snapshot_with_resolved_sheets rebuild the stored snapshot must carry the
    config-based names, otherwise a sheet filter could never match."""
    view, _ = _make_view(main_window, tmp_path)
    _feed_snapshot(view, {"mcu": "MCU", "fpga": "FPGA"})

    resolved = {tuple(s.sheet) for s in view._resolved_snapshot}
    assert resolved == {("MCU",), ("FPGA",)}     # NOT {(None,), (None,)}


def test_sheet_that_does_not_reduce_keeps_the_full_list(main_window, tmp_path):
    view, _ = _make_view(main_window, tmp_path)
    view._sheet_names = {"mcu": "MCU"}
    view.refresh_known_roles([
        _sel_on_sheet("R1", "PIF_3V3_VDD", "mcu"),
        _sel_on_sheet("R2", "PIF_AVDD", "mcu"),
    ])

    view._sheet_combo.setCurrentText("MCU")     # both clusters on MCU

    assert _clusters(view) == ["PIF_3V3_VDD", "PIF_AVDD"]


def test_empty_sheet_restores_the_full_list(main_window, tmp_path):
    view, _ = _make_view(main_window, tmp_path)
    _feed_snapshot(view, {"mcu": "MCU", "fpga": "FPGA"})
    view._sheet_combo.setCurrentText("MCU")
    assert _clusters(view) == ["PIF_3V3_VDD"]

    view._sheet_combo.setCurrentText("")
    assert _clusters(view) == ["FPGA", "PIF_3V3_VDD"]


def test_selected_cluster_survives_narrowing_when_it_matches(main_window, tmp_path):
    view, _ = _make_view(main_window, tmp_path)
    _feed_snapshot(view, {"mcu": "MCU", "fpga": "FPGA"})
    view._cluster_combo.setCurrentText("PIF_3V3_VDD")

    view._sheet_combo.setCurrentText("MCU")     # PIF_3V3_VDD IS on MCU

    assert view._cluster_combo.currentText() == "PIF_3V3_VDD"


# ── Task V: ONE merged page (Source / Role anchor / Marker anchor) ─────────
#
# prompt_2026_09_11_cell_page_merge.md — the cell's identity block moved onto
# the shared CellIdentityWidget, the page is what a cell selection opens, and
# the Name/Comment fields write the top-level clone_placements record only
# (never a tree-materialized one).

def _view_with_placements(main_window, tmp_path, placements):
    data = {"cells": {"cell1": {
        "layer": "F.Cu",
        "components": [{"role": "C1", "offset_along_mm": 0.0,
                        "offset_across_mm": 0.0}],
    }}, "clone_placements": placements}
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp(data), encoding="utf-8")
    view = CellAnchorView(main_window, connection=main_window.connection)
    view.set_root_path(target)
    view.load_entry("cell1", target)
    view._cluster_combo.setCurrentText("CL1")
    return view, target


def test_merged_page_has_source_and_two_anchor_tabs(main_window, tmp_path):
    """The merged page: Source, Role anchor, Marker anchor — and NO
    "Placement" tab (positions live in the trees; a tab here would create a
    duplicate top-level record shadowing the tree — the mine the plan warns
    about)."""
    view, _ = _make_view(main_window, tmp_path)
    titles = [view._tabs.tabText(i) for i in range(view._tabs.count())]
    assert titles == ["Source", "Role anchor", "Marker anchor"]


def test_identity_block_is_the_shared_widget_in_both_pages(real_main_window):
    """One class, two places — the duplication is what desynchronised the two
    cell pages this task merges."""
    from gui.docks._cell_identity import CellIdentityWidget
    hub = real_main_window._dock_hub
    for owner in (hub.cell_anchor_view._identity, hub.placer_dock._name_row):
        assert isinstance(owner, CellIdentityWidget)
    assert (type(hub.cell_anchor_view._identity)
            is type(hub.placer_dock._name_row))


def test_identity_loads_from_the_top_level_record(main_window, tmp_path):
    view, _ = _view_with_placements(
        main_window, tmp_path,
        [{"name": "cell1", "cell": "cell1", "cluster": "CL1",
          "comment": "hi"}])

    assert view._name_edit.text() == "cell1"
    assert view._comment_edit.text() == "hi"
    assert view._name_edit.isReadOnly() is False
    assert view._comment_edit.isReadOnly() is False


def test_tree_placement_identity_is_read_only_and_never_saved(main_window,
                                                              tmp_path):
    """THE mine: with no top-level clone_placements record (the usual case —
    all 36 real placements materialize from trees) Name/Comment must be
    read-only and committing must write NOTHING, never a duplicate entry that
    shadows the tree."""
    view, target = _view_with_placements(main_window, tmp_path, [])

    assert view._name_edit.isReadOnly() is True
    assert view._comment_edit.isReadOnly() is True
    view._name_edit.setText("sneaky")
    view._comment_edit.setText("sneaky")
    view._on_identity_edited()

    data = sexp_to_dict(target.read_text(encoding="utf-8"))
    assert not data.get("clone_placements")


def test_identity_edit_writes_back_into_the_top_level_record(main_window,
                                                             tmp_path):
    view, target = _view_with_placements(
        main_window, tmp_path,
        [{"name": "cell1", "cell": "cell1", "cluster": "CL1"}])

    view._name_edit.setText("renamed")
    view._comment_edit.setText("new note")
    view._on_identity_edited()

    data = sexp_to_dict(target.read_text(encoding="utf-8"))
    items = data.get("clone_placements")
    assert len(items) == 1                      # updated in place, no duplicate
    assert items[0]["name"] == "renamed"
    assert items[0]["comment"] == "new note"
    assert items[0]["cell"] == "cell1"


def test_anchor_tabs_still_write_the_cell_template(main_window, tmp_path):
    """The anchor tabs edit cells: (the TEMPLATE), never this placement."""
    view, target = _view_with_placements(
        main_window, tmp_path,
        [{"name": "cell1", "cell": "cell1", "cluster": "CL1"}])

    view._role_combo.setCurrentText("C1")
    view._on_set_component_anchor()

    data = sexp_to_dict(target.read_text(encoding="utf-8"))
    assert data["cells"]["cell1"]["anchor_role"] == "C1"
    # The placement record is untouched by the anchor tabs.
    assert "anchor_role" not in data["clone_placements"][0]


def test_identity_works_without_a_board(main_window, tmp_path):
    """Acceptance (Phase C, preserved by task V): the page works with
    connection.board = None."""
    assert main_window.connection.board is None
    view, _ = _view_with_placements(
        main_window, tmp_path,
        [{"name": "cell1", "cell": "cell1", "cluster": "CL1"}])
    assert view._name_edit.text() == "cell1"


def test_cell_selection_by_keyboard_opens_the_merged_page(real_main_window,
                                                          tmp_path):
    """Task V + G.5: moving the CURRENT item (what an arrow key does) opens the
    merged cell page for the picked cell — not the Placer."""
    from PyQt6.QtWidgets import QApplication
    hub = real_main_window._dock_hub
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp({"cells": {
        "A": {"components": [{"role": "C1", "offset_along_mm": 0.0,
                              "offset_across_mm": 0.0}]},
        "B": {"components": [{"role": "C1", "offset_along_mm": 0.0,
                              "offset_across_mm": 0.0}]},
    }}), encoding="utf-8")
    hub.config_tree_dock.set_root_file(target)
    hub.cell_anchor_view.set_root_path(target)

    top = hub.config_tree_dock.tree.topLevelItem(0)
    cells = next(top.child(i) for i in range(top.childCount())
                 if top.child(i).text(0) == "Cells")
    leaf_b = next(cells.child(i) for i in range(cells.childCount())
                  if cells.child(i).text(0) == "B")
    hub.config_tree_dock.tree.setCurrentItem(leaf_b)
    QApplication.processEvents()

    assert hub.config_tree_dock.current_right_page() is hub.cell_anchor_view
    assert hub.cell_anchor_view._cell_name == "B"
