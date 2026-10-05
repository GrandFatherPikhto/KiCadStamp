# tests/explode/board.py
"""A live-board stand-in for the "Разнос" tests (plan
``plan_2026_10_05_explode_r1_core.md`` §2/§3).

It is deliberately NOT ``tests/fakes/live_board.FakeLiveBoardAdapter``: that fake
serves the redraw/reconcile harnesses and the conformance cell pins its surface
to the seam. This one serves the explode plan/journal and must therefore carry
the reads the plan uses — ``get_bounding_boxes``, ``get_footprint_pads``, the
board identity reads and a TRANSACTIONAL commit (a snapshot on ``begin_commit``,
a real rollback on ``drop_commit``) so "a failed transaction leaves the board
untouched" is a checkable property.

Positions are real domain DTOs; a via/track is moved through ``unwrap`` (the
DTO itself, since its ``_kipy`` is None), exactly like the live probe.
"""
from __future__ import annotations

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Pad, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Box2, Vector2
from kicadstamp.utils.units import MM

F_CU = BoardLayer.BL_F_Cu
B_CU = BoardLayer.BL_B_Cu


def fp(uuid, ref, cluster, x_mm, y_mm, role=None, angle=0.0,
       layer=F_CU, half_mm=1.0) -> Footprint:
    item = Footprint(ref=ref, uuid=uuid, position=Vector2.from_xy_mm(x_mm, y_mm),
                     angle_deg=angle, layer=layer)
    item._explode_half = int(half_mm * MM)
    item._fields = {CLUSTER_FIELD_NAME: cluster}
    if role is not None:
        item._fields[ROLE_FIELD_NAME] = role
    return item


def via(uuid, x_mm, y_mm) -> Via:
    item = Via(uuid=uuid, position=Vector2.from_xy_mm(x_mm, y_mm),
               net_name="GND", drill_mm=0.3, diameter_mm=0.6)
    item._explode_half = int(0.25 * MM)
    return item


def track(uuid, layer, sx_mm, sy_mm, ex_mm, ey_mm, width_mm=0.25) -> Track:
    item = Track(uuid=uuid, start=Vector2.from_xy_mm(sx_mm, sy_mm),
                 end=Vector2.from_xy_mm(ex_mm, ey_mm), net_name="GND",
                 width_mm=width_mm, layer=layer)
    item._explode_half = int(width_mm / 2 * MM)
    return item


def pad(number, x_mm, y_mm, copper_layers=None, w_mm=0.5, h_mm=0.5) -> Pad:
    """A pad whose own area is a plain rectangle of w x h mm (shape RECT)."""
    return Pad(number=number, net_name="GND",
               position=Vector2.from_xy_mm(x_mm, y_mm),
               size=Vector2.from_xy_mm(w_mm, h_mm), angle_rad=0.0,
               shape="rect", copper_layers=copper_layers)


class ExplodeBoard:
    """See the module docstring."""

    def __init__(self, footprints=(), tracks=(), vias=(), pads=None,
                 board_name="board.kicad_pcb", project=("proj", "/tmp/proj")):
        self._fps = list(footprints)
        self._tracks = list(tracks)
        self._vias = list(vias)
        self._pads = dict(pads or {})          # fp.uuid -> [Pad]
        self._fields = {}                      # (uuid, name) -> value
        self._board_name = board_name
        self._project = project
        self.fail_update = False
        self.on_push = None          # a hook to simulate KiCad NOT applying
        self.on_update = None        # a hook to observe the transaction start
        self._snap = None
        self.selected = []

    def set_field(self, uuid, name, value):
        self._fields[(uuid, name)] = value

    def set_cluster(self, item, value):
        self._fields[(item.uuid, CLUSTER_FIELD_NAME)] = value

    def set_role(self, item, value):
        self._fields[(item.uuid, ROLE_FIELD_NAME)] = value

    # ── reads ───────────────────────────────────────────────────────────────
    def get_footprints(self):
        return list(self._fps)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_field_value(self, footprint, field_name):
        own = getattr(footprint, "_fields", {})
        if field_name in own:
            return own[field_name]
        return self._fields.get((footprint.uuid, field_name))

    def get_footprint_pads(self, footprint):
        return list(self._pads.get(footprint.uuid, ()))

    def get_footprint(self, ref):
        return next((f for f in self._fps if f.ref == ref), None)

    def get_selected_items(self):
        return []

    def get_board_filename(self):
        return self._board_name

    def get_board_project(self):
        return self._project

    def get_bounding_boxes(self, items):
        return [_box_of(it) for it in items]

    def refresh_board(self):
        pass

    def close(self):
        pass

    # ── transactional commit ────────────────────────────────────────────────
    def begin_commit(self):
        self._snap = self._capture()
        return object()

    def update_items(self, items):
        if self.on_update is not None:
            self.on_update()
        if self.fail_update:
            raise RuntimeError("update_items failed")
        return None

    def push_commit(self, commit, description):
        self._snap = None
        if self.on_push is not None:
            self.on_push()

    def drop_commit(self, commit):
        if self._snap is not None:
            self._restore(self._snap)
        self._snap = None

    def _capture(self):
        out = {}
        for it in list(self._fps) + list(self._tracks) + list(self._vias):
            out[it.uuid] = _pose_tuple(it)
        return out

    def _restore(self, snap):
        for it in list(self._fps) + list(self._tracks) + list(self._vias):
            if it.uuid in snap:
                _set_pose_tuple(it, snap[it.uuid])


def _box_of(item) -> Box2:
    half = getattr(item, "_explode_half", MM)
    if isinstance(item, Footprint) or isinstance(item, Via):
        cx, cy = item.position.x, item.position.y
        return Box2(pos=Vector2(cx - half, cy - half),
                    size=Vector2(2 * half, 2 * half))
    x1, y1, x2, y2 = item.start.x, item.start.y, item.end.x, item.end.y
    return Box2(pos=Vector2(min(x1, x2) - half, min(y1, y2) - half),
                size=Vector2(abs(x2 - x1) + 2 * half, abs(y2 - y1) + 2 * half))


def _pose_tuple(item):
    if isinstance(item, Footprint):
        return ("fp", item.position.x, item.position.y, item.angle_deg)
    if isinstance(item, Via):
        return ("via", item.position.x, item.position.y)
    return ("track", item.start.x, item.start.y, item.end.x, item.end.y)


def _set_pose_tuple(item, values):
    if isinstance(item, Footprint):
        item.position = Vector2(values[1], values[2])
        item.angle_deg = values[3]
    elif isinstance(item, Via):
        item.position = Vector2(values[1], values[2])
    else:
        item.start = Vector2(values[1], values[2])
        item.end = Vector2(values[3], values[4])


__all__ = ["ExplodeBoard", "fp", "via", "track", "pad", "F_CU", "B_CU"]
