# tests/gui/test_overlay_markers.py
"""Unit tests for gui/overlay_markers.py — the OWNER of the board-overlay
marker map (Задание Е, plan_2026_09_11_overlay_markers_owner.md).

Covers the task's E.5 checklist: idempotency by key, replacement in a new
point, recreating a shape deleted from under us, reconciliation (drop dead
keys / log orphans without touching them), adapter-less safety, namespace
isolation, legacy-state migration and malformed legacy state.

No live KiCad — the adapter and its `_board` are fakes (the module is duck
typed by construction: create_items / remove_by_ids / select_items /
refresh_board / `_board`). tests/gui/conftest.py's autouse `isolated_settings`
gives every test a fresh gui_state.json.
"""
from kipy.board_types import BoardCircle, BoardLayer, BoardRectangle
from kipy.geometry import Vector2 as KipyVector2

import gui.overlay_markers as markers_mod
from gui import settings
from gui.overlay_markers import OverlayMarkerOwner
from kicadstamp.utils.units import MM

LAYER = BoardLayer.BL_Dwgs_User
OTHER_LAYER = BoardLayer.BL_User_5

ROOT = "/root/a"
CELL = "cellA"

MARKER_KEY = markers_mod.cell_anchor_key(ROOT, CELL, "marker")
BBOX_KEY = markers_mod.cell_anchor_key(ROOT, CELL, "bbox")
SCOPE = markers_mod.cell_anchor_scope(ROOT, CELL)


class _FakeBoard:
    """Duck-typed `_board` — the exact surface board_overlay reads."""

    def __init__(self, layers=None, shapes=None):
        self.layers = list(layers) if layers is not None else [LAYER, OTHER_LAYER]
        self.shapes = list(shapes) if shapes is not None else []
        self.names = {LAYER: "User.Drawings", OTHER_LAYER: "User.KiCadStamp"}

    def get_enabled_layers(self):
        return list(self.layers)

    def get_layer_name(self, layer):
        return self.names.get(layer, str(layer))

    def get_shapes(self):
        return list(self.shapes)


class FakeAdapter:
    """Records IPC; created shapes ARE registered on the fake board and
    remove_by_ids() really removes them, so get_shapes() reflects the live
    state (what idempotency and reconciliation must see)."""

    def __init__(self, board=None):
        self._board = board if board is not None else _FakeBoard()
        self.created = []
        self.removed = []
        self.refreshes = 0
        self._next_id = 0

    def refresh_board(self):
        self.refreshes += 1

    def create_items(self, items):
        items = list(items)
        # The real board assigns a uuid on create; a freshly built kipy shape
        # carries an EMPTY id.value, so the fake must do the same or every
        # created shape would collide on "".
        for item in items:
            self._next_id += 1
            item.id.value = f"shape-{self._next_id}"
        self.created.extend(items)
        self._board.shapes = list(self._board.shapes) + items
        return items

    def select_items(self, items):
        pass

    def remove_by_ids(self, uuid_strs):
        doomed = {str(u) for u in uuid_strs}
        self.removed.extend(uuid_strs)
        self._board.shapes = [s for s in self._board.shapes
                              if str(s.id.value) not in doomed]
        return True


def _circle(x_mm, y_mm, layer=LAYER):
    c = BoardCircle()
    c.id.value = f"orphan-{x_mm}-{y_mm}"
    c.center = KipyVector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    c.layer = layer
    c.attributes.stroke.width = int(0.1 * MM)
    return c


def _live_circles(adapter, layer=LAYER):
    return [s for s in adapter._board.shapes
            if isinstance(s, BoardCircle) and s.layer == layer]


def _owner():
    return OverlayMarkerOwner()


# ── E.5.1: one key -> one circle, no leftover ──────────────────────────────

def test_ensure_marker_twice_leaves_exactly_one_circle():
    adapter = FakeAdapter()
    owner = _owner()
    first = owner.ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    assert first and owner.uuid_for(MARKER_KEY) == first
    owner.ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    circles = _live_circles(adapter)
    assert len(circles) == 1
    # The previous uuid was removed (replaced), never orphaned on the layer.
    assert first in adapter.removed
    assert owner.uuid_for(MARKER_KEY) == str(circles[0].id.value)


# ── E.5.2: a new point moves the one circle ────────────────────────────────

def test_ensure_marker_moves_to_a_new_point():
    adapter = FakeAdapter()
    owner = _owner()
    owner.ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    owner.ensure_marker(adapter, MARKER_KEY, 30.0, 5.0)
    circles = _live_circles(adapter)
    assert len(circles) == 1
    assert circles[0].center.x == int(30.0 * MM)
    assert circles[0].center.y == int(5.0 * MM)


# ── E.5.3: the shape was deleted from under us ─────────────────────────────

def test_ensure_marker_recreates_a_shape_deleted_by_the_user():
    adapter = FakeAdapter()
    owner = _owner()
    owner.ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    # The user deletes the circle in KiCad's own canvas.
    adapter._board.shapes = []
    new_uuid = owner.ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    assert len(_live_circles(adapter)) == 1
    assert owner.uuid_for(MARKER_KEY) == new_uuid


def test_ensure_marker_survives_an_owner_restart():
    """The map is the persisted gui_state entry — a NEW owner instance (a GUI
    restart) sees the same key and still keeps one circle."""
    adapter = FakeAdapter()
    _owner().ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    OverlayMarkerOwner().ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    assert len(_live_circles(adapter)) == 1


# ── Orphan leftover (crash) is replaced, not stacked (Р5 + §О.3.3) ─────────

def test_ensure_marker_replaces_an_unkeyed_leftover_within_tolerance():
    adapter = FakeAdapter()
    # A crash left a circle behind; the persisted map was lost.
    orphan = _circle(10.05, 20.05)
    adapter._board.shapes = [orphan]
    owner = _owner()
    owner.ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    circles = _live_circles(adapter)
    assert len(circles) == 1
    assert circles[0].center.x == int(10.0 * MM)
    assert str(orphan.id.value) in adapter.removed


def test_ensure_marker_leaves_a_far_away_unkeyed_circle_alone():
    adapter = FakeAdapter()
    other = _circle(100.0, 100.0)
    adapter._board.shapes = [other]
    owner = _owner()
    owner.ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    assert other in adapter._board.shapes
    assert len(_live_circles(adapter)) == 2


def test_ensure_bbox_replaces_its_own_rectangle_only():
    adapter = FakeAdapter()
    owner = _owner()
    first = owner.ensure_bbox(adapter, BBOX_KEY, 0.0, 0.0, 5.0, 5.0)
    owner.ensure_bbox(adapter, BBOX_KEY, 1.0, 1.0, 6.0, 6.0)
    rects = [s for s in adapter._board.shapes
             if isinstance(s, BoardRectangle) and s.layer == LAYER]
    assert len(rects) == 1
    assert first in adapter.removed
    assert owner.uuid_for(BBOX_KEY) == str(rects[0].id.value)


# ── E.5.4/.5: reconciliation ───────────────────────────────────────────────

def test_reconcile_drops_a_key_whose_shape_is_gone():
    settings.state.set(markers_mod.OVERLAY_MARKERS_KEY, {MARKER_KEY: "dead-uuid"})
    adapter = FakeAdapter()
    report = _owner().reconcile(adapter)
    assert report["dropped"] == 1
    assert "dead-uuid" not in _owner().all_uuids()


def test_reconcile_logs_orphans_and_leaves_them_alone(caplog):
    adapter = FakeAdapter()
    orphan = _circle(1.0, 2.0)
    adapter._board.shapes = [orphan]
    settings.state.set(markers_mod.OVERLAY_MARKERS_KEY, {})
    report = _owner().reconcile(adapter)
    assert report["orphans"] == 1
    assert orphan in adapter._board.shapes          # NOT deleted
    assert adapter.removed == []
    assert any("no owner key" in r.message for r in caplog.records)


def test_reconcile_keeps_a_key_whose_shape_is_still_there():
    adapter = FakeAdapter()
    owner = _owner()
    owner.ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    report = owner.reconcile(adapter)
    assert report["dropped"] == 0
    assert owner.has_key(MARKER_KEY)


def test_reconcile_without_adapter_is_a_safe_noop():
    assert _owner().reconcile(None) == {"skipped": "no-board"}


# ── E.5.6: no adapter -> safe, no exception, no modal ──────────────────────

def test_owner_is_safe_without_an_adapter():
    owner = _owner()
    assert owner.ensure_marker(None, MARKER_KEY, 1.0, 2.0) is None
    assert owner.ensure_bbox(None, BBOX_KEY, 0.0, 0.0, 1.0, 1.0) is None
    assert owner.read_position(None, MARKER_KEY) is None
    owner.remove_key(None, MARKER_KEY)            # state-only, no raise
    owner.remove_scope(None, SCOPE)
    owner.remove_namespace(None, markers_mod.NS_CELL_ANCHOR)
    assert owner.forget_all() == []
    owner.reconcile(None)


# ── E.5.7: namespace isolation ─────────────────────────────────────────────

def test_remove_namespace_never_touches_another_namespace():
    adapter = FakeAdapter()
    owner = _owner()
    point_key = markers_mod.NS_POINT + "/P1"
    owner.ensure_marker(adapter, MARKER_KEY, 1.0, 1.0)
    owner.ensure_marker(adapter, point_key, 2.0, 2.0)
    cell_uuid = owner.uuid_for(MARKER_KEY)
    point_uuid = owner.uuid_for(point_key)

    owner.remove_namespace(adapter, markers_mod.NS_CELL_ANCHOR)

    assert not owner.has_key(MARKER_KEY)
    assert owner.has_key(point_key)
    assert cell_uuid in adapter.removed
    assert point_uuid not in adapter.removed
    assert point_uuid in [str(s.id.value) for s in adapter._board.shapes]


def test_remove_scope_drops_only_that_cells_two_keys():
    adapter = FakeAdapter()
    owner = _owner()
    k1 = markers_mod.cell_anchor_key(ROOT, "cellA", "marker")
    k2 = markers_mod.cell_anchor_key(ROOT, "cellA", "bbox")
    k3 = markers_mod.cell_anchor_key(ROOT, "cellB", "marker")
    owner.ensure_marker(adapter, k1, 1.0, 1.0)
    owner.ensure_bbox(adapter, k2, 0.0, 0.0, 1.0, 1.0)
    owner.ensure_marker(adapter, k3, 5.0, 5.0)

    owner.remove_scope(adapter, markers_mod.cell_anchor_scope(ROOT, "cellA"))

    assert not owner.has_key(k1)
    assert not owner.has_key(k2)
    assert owner.has_key(k3)


def test_remove_scope_does_not_match_a_sibling_cell_with_a_shared_prefix():
    sibling = markers_mod.cell_anchor_key(ROOT, "cellAXX", "marker")
    settings.state.set(markers_mod.OVERLAY_MARKERS_KEY, {sibling: "u1"})
    owner = _owner()
    owner.remove_scope(None, markers_mod.cell_anchor_scope(ROOT, "cellA"))
    assert owner.has_key(sibling)


def test_read_position_reads_the_dragged_centre():
    adapter = FakeAdapter()
    owner = _owner()
    owner.ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    uuid = owner.uuid_for(MARKER_KEY)
    for s in adapter._board.shapes:
        if str(s.id.value) == uuid:
            s.center = KipyVector2.from_xy(int(13.25 * MM), int(21.5 * MM))
    pos = owner.read_position(adapter, MARKER_KEY)
    assert pos is not None
    assert abs(pos[0] - 13.25) < 1e-9
    assert abs(pos[1] - 21.5) < 1e-9


def test_remove_key_forgets_and_deletes():
    adapter = FakeAdapter()
    owner = _owner()
    owner.ensure_marker(adapter, MARKER_KEY, 10.0, 20.0)
    owner.remove_key(adapter, MARKER_KEY)
    assert not owner.has_key(MARKER_KEY)
    assert _live_circles(adapter) == []


def test_forget_all_clears_the_whole_map_without_an_adapter():
    adapter = FakeAdapter()
    owner = _owner()
    owner.ensure_marker(adapter, MARKER_KEY, 1.0, 1.0)
    owner.ensure_marker(adapter, markers_mod.NS_POINT + "/P1", 2.0, 2.0)
    uuids = owner.forget_all()
    assert len(uuids) == 2
    assert owner.keys() == []
    # No adapter passed -> shapes untouched on the board.
    assert len(_live_circles(adapter)) == 2


# ── E.5.8/.9: legacy migration ─────────────────────────────────────────────

def test_migrate_legacy_state_preserves_every_uuid():
    import gui.board_overlay as board_overlay
    legacy = {
        ROOT: {CELL: {"marker": "m1", "bbox": "b1"},
               "cellB": {"marker": "m2", "bbox": None}},
        "/root/b": {"cellC": {"marker": None, "bbox": "b3"}},
        "/root/c": "junk",
    }
    settings.state.set(board_overlay.OVERLAY_STATE_KEY, legacy)
    written = markers_mod.migrate_legacy_state()
    assert written == 4
    owner = _owner()
    assert owner.uuid_for(markers_mod.cell_anchor_key(ROOT, CELL, "marker")) == "m1"
    assert owner.uuid_for(markers_mod.cell_anchor_key(ROOT, CELL, "bbox")) == "b1"
    assert owner.uuid_for(markers_mod.cell_anchor_key(ROOT, "cellB", "marker")) == "m2"
    assert owner.uuid_for(markers_mod.cell_anchor_key("/root/b", "cellC", "bbox")) == "b3"


def test_migrate_is_a_noop_once_the_new_key_exists():
    import gui.board_overlay as board_overlay
    settings.state.set(markers_mod.OVERLAY_MARKERS_KEY, {})
    settings.state.set(board_overlay.OVERLAY_STATE_KEY,
                       {ROOT: {CELL: {"marker": "m1"}}})
    assert markers_mod.migrate_legacy_state() == 0


def test_malformed_legacy_state_never_raises():
    import gui.board_overlay as board_overlay
    for junk in ("junk", 42, [1, 2], {ROOT: "not-a-dict"},
                 {ROOT: {CELL: "not-a-dict"}}):
        settings.state.set(board_overlay.OVERLAY_STATE_KEY, junk)
        assert markers_mod.migrate_legacy_state() == 0
        assert _owner().keys() == []


# ── reconcile hook (E.2.4): DockHub dispatches it on a worker ─────────────

def test_dock_hub_reconcile_overlay_dispatches_on_a_worker(monkeypatch):
    from types import SimpleNamespace
    from gui import dock_hub, worker

    calls = []
    monkeypatch.setattr(worker, "start_long_op",
                        lambda *a, **k: calls.append(a) or "controller")

    class _Conn:
        long_op_active = False
        board = SimpleNamespace(adapter=object())

    hub = dock_hub.DockHub.__new__(dock_hub.DockHub)   # no full GUI build
    hub.reconcile_overlay(_Conn())

    assert calls, "reconcile must be dispatched"
    # worker fn is the owner's reconcile (a bound method — compare equal, not
    # identical); the adapter is the first worker arg.
    assert calls[0][2] == markers_mod.owner.reconcile
    assert calls[0][5] is not None


def test_dock_hub_reconcile_overlay_skips_without_a_board(monkeypatch):
    from gui import dock_hub, worker

    calls = []
    monkeypatch.setattr(worker, "start_long_op",
                        lambda *a, **k: calls.append(a) or "controller")

    class _Conn:
        long_op_active = False
        board = None

    hub = dock_hub.DockHub.__new__(dock_hub.DockHub)
    hub.reconcile_overlay(_Conn())
    assert calls == []


def test_dock_hub_reconcile_overlay_skips_while_socket_is_busy(monkeypatch):
    from types import SimpleNamespace
    from gui import dock_hub, worker

    calls = []
    monkeypatch.setattr(worker, "start_long_op",
                        lambda *a, **k: calls.append(a) or "controller")

    class _Conn:
        long_op_active = True
        board = SimpleNamespace(adapter=object())

    hub = dock_hub.DockHub.__new__(dock_hub.DockHub)
    hub.reconcile_overlay(_Conn())
    assert calls == []


# ── board_overlay list/sweep parity (E.5.11) ───────────────────────────────

def test_list_and_sweep_see_the_same_shapes():
    import gui.board_overlay as overlay
    mine = BoardRectangle()
    mine.id.value = "rect-mine"
    mine.layer = LAYER
    other = BoardRectangle()
    other.id.value = "rect-other"
    other.layer = OTHER_LAYER
    adapter = FakeAdapter(_FakeBoard(shapes=[mine, other]))
    listed = {s.uuid for s in overlay.list_overlay_shapes(adapter, LAYER)}
    assert listed == {str(mine.id.value)}
    # sweep_layer() goes through the very same traversal.
    assert overlay.sweep_layer(adapter, LAYER) == len(listed)
    assert adapter.removed == list(listed)
