# tests/gui/test_board_overlay.py
"""Unit tests for gui/board_overlay.py — the pure board-overlay drawing
helpers (Phase C of plan_2026_09_09_cell_anchor_v2_declarative_and_
board_overlay.md). The module is a direct port of the measured
kicadstamp/diagnostics/probe_board_overlay.py, so these assert the three
gotchas (§0.5) it encodes:

  1. a created shape always carries a NON-ZERO attributes.stroke.width
     (width 0 = KiCad draws nothing);
  2. adapter.select_items() is called after EVERY create AND delete (KiCad
     does not repaint the canvas after an IPC edit);
  3. BoardCircle.radius is set through radius_point, and read_marker reads a
     circle's centre by uuid.

No live KiCad — the adapter and its `_board` are fakes.
"""
from kipy.board_types import BoardCircle, BoardLayer, BoardRectangle
from kipy.geometry import Vector2 as KipyVector2

import gui.board_overlay as overlay
from kicadstamp.utils.units import MM

LAYER = BoardLayer.BL_Dwgs_User
OTHER_LAYER = BoardLayer.BL_User_5


class _FakeBoard:
    """Duck-typed `_board` — get_enabled_layers/get_layer_name/get_shapes."""

    def __init__(self, layers=None, shapes=None):
        self.layers = layers if layers is not None else [LAYER, OTHER_LAYER]
        self.shapes = shapes if shapes is not None else []
        self.names = {LAYER: "User.Drawings", OTHER_LAYER: "User.KiCadStamp"}

    def get_enabled_layers(self):
        return list(self.layers)

    def get_layer_name(self, layer):
        return self.names.get(layer, str(layer))

    def get_shapes(self):
        return self.shapes


class FakeAdapter:
    """Records create/select/remove calls; created shapes are returned as-is
    and (optionally) registered on `_board` so read/sweep see them."""

    def __init__(self, board=None):
        self._board = board if board is not None else _FakeBoard()
        self.created = []
        self.selected = []
        self.removed = []
        self.refreshes = 0

    def refresh_board(self):
        self.refreshes += 1

    def create_items(self, items):
        items = list(items)
        self.created.extend(items)
        return items

    def select_items(self, items):
        self.selected.append(list(items))

    def remove_by_ids(self, uuid_strs):
        self.removed.extend(uuid_strs)
        return True


def _record_created_on_board(adapter):
    """After a create, register the created shapes on the fake board so a
    subsequent read/sweep can find them (what the real KiCad board does)."""
    adapter._board.shapes = list(adapter._board.shapes) + list(adapter.created)


def test_overlay_layers_reads_live_board_not_hardcoded():
    board = _FakeBoard(layers=[LAYER, OTHER_LAYER])
    adapter = FakeAdapter(board)
    layers = overlay.overlay_layers(adapter)
    assert layers == [(LAYER, "User.Drawings"), (OTHER_LAYER, "User.KiCadStamp")]
    # resolve by the DISPLAY name — never a hardcoded enum list.
    assert overlay.resolve_overlay_layer(adapter, "User.Drawings") == LAYER
    assert overlay.resolve_overlay_layer(adapter, "No.Such.Layer") is None


def test_draw_bbox_sets_layer_stroke_and_repaints():
    adapter = FakeAdapter()
    uuid = overlay.draw_bbox(adapter, LAYER, 1.0, 2.0, 5.0, 6.0, 0.15)
    assert isinstance(uuid, str)
    assert len(adapter.created) == 1
    rect = adapter.created[0]
    assert isinstance(rect, BoardRectangle)
    assert rect.layer == LAYER
    # gotcha 1: non-zero stroke width.
    assert rect.attributes.stroke.width == int(0.15 * MM)
    assert rect.top_left.x == int(1.0 * MM) and rect.top_left.y == int(2.0 * MM)
    assert rect.bottom_right.x == int(5.0 * MM) and rect.bottom_right.y == int(6.0 * MM)
    # gotcha 2: repaint after create.
    assert adapter.selected and adapter.selected[-1] == [rect]


def test_draw_marker_sets_radius_via_radius_point_and_repaints():
    adapter = FakeAdapter()
    radius_mm = 0.3
    uuid = overlay.draw_marker(adapter, LAYER, 10.0, 20.0, radius_mm, 0.1)
    assert isinstance(uuid, str)
    assert len(adapter.created) == 1
    circle = adapter.created[0]
    assert isinstance(circle, BoardCircle)
    assert circle.layer == LAYER
    # gotcha 3: radius is expressed as a point on the circle.
    assert circle.center.x == int(10.0 * MM) and circle.center.y == int(20.0 * MM)
    assert circle.radius_point.x == int((10.0 + radius_mm) * MM)
    assert circle.radius_point.y == int(20.0 * MM)
    assert circle.attributes.stroke.width == int(0.1 * MM)
    assert adapter.selected and adapter.selected[-1] == [circle]


def test_read_marker_returns_dragged_centre_in_mm():
    board = _FakeBoard(shapes=[])
    adapter = FakeAdapter(board)
    uuid = overlay.draw_marker(adapter, LAYER, 10.0, 20.0, 0.3, 0.1)
    _record_created_on_board(adapter)
    # Simulate the user dragging the marker in KiCad.
    circle = adapter._board.shapes[0]
    circle.center = KipyVector2.from_xy(int(13.25 * MM), int(21.5 * MM))
    pos = overlay.read_marker(adapter, uuid)
    assert pos is not None
    assert abs(pos[0] - 13.25) < 1e-9
    assert abs(pos[1] - 21.5) < 1e-9


def test_read_marker_returns_none_when_missing():
    adapter = FakeAdapter(_FakeBoard(shapes=[]))
    assert overlay.read_marker(adapter, "no-such-uuid") is None


def test_remove_overlay_calls_remove_and_repaints():
    adapter = FakeAdapter()
    assert overlay.remove_overlay(adapter, []) is True
    ok = overlay.remove_overlay(adapter, ["a", "b"])
    assert ok is True
    assert adapter.removed == ["a", "b"]
    # gotcha 2: repaint after delete.
    assert adapter.selected and adapter.selected[-1] == []


def test_sweep_layer_removes_only_shapes_of_that_layer():
    mine = BoardRectangle()
    mine.layer = LAYER
    other = BoardRectangle()
    other.layer = OTHER_LAYER
    board = _FakeBoard(shapes=[mine, other])
    adapter = FakeAdapter(board)
    count = overlay.sweep_layer(adapter, LAYER)
    assert count == 1
    assert adapter.removed == [str(mine.id.value)]
    assert adapter.selected and adapter.selected[-1] == []


def test_sweep_layer_noop_when_layer_empty():
    board = _FakeBoard(shapes=[])
    adapter = FakeAdapter(board)
    assert overlay.sweep_layer(adapter, LAYER) == 0
    assert adapter.removed == []
