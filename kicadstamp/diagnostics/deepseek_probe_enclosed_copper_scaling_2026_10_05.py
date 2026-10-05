# kicadstamp/diagnostics/deepseek_probe_enclosed_copper_scaling_2026_10_05.py
"""Scaling probe for the pad-classification loop of ``explode_connectivity``.

"Select enclosed copper" classifies EVERY piece of copper against EVERY foreign
pad on the board (``plan_2026_10_05_select_enclosed_copper.md``) — unlike the
"Разнос" plan, which only ever classifies against the pads of the clusters that
actually leave the area. On the FPGA board that is ~1800 copper pieces against
~1500 pads, and ``_classify_graph`` tests each item against each pad in a flat
Python loop.

This probe builds a synthetic board of that size and times the three entry
points, so the before/after of the broad-phase (if the measurement asks for one)
is a NUMBER, not a guess (deepseek.md §31: the sign goes with a measurement).

Run (main checkout interpreter)::

    .venv/bin/python kicadstamp/diagnostics/deepseek_probe_enclosed_copper_scaling_2026_10_05.py
"""
from __future__ import annotations

import random
import time

from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.domain.board import Footprint, Pad, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.explode_connectivity import (
    build_cell_islands,
    cell_pad_areas,
    classify_copper,
    copper_classes,
    copper_pieces,
    pad_areas,
)

F_CU = BoardLayer.BL_F_Cu

N_FOOTPRINTS = 1500
N_INSTANCE = 30
N_CLUSTERS = 40
N_TRACKS = 1800
N_VIAS = 200


def _footprint(i: int) -> Footprint:
    item = Footprint(ref=f"R{i}", uuid=f"fp-{i}",
                     position=Vector2.from_xy_mm((i % 60) * 3.0, (i // 60) * 3.0),
                     angle_deg=0.0, layer=F_CU)
    item._fields = {CLUSTER_FIELD_NAME: f"C{i % N_CLUSTERS}"}
    return item


def _pad(fp: Footprint) -> Pad:
    return Pad(number="1", net_name="N",
               position=Vector2(fp.position.x, fp.position.y),
               size=Vector2.from_xy_mm(0.5, 0.5), angle_rad=0.0,
               shape="rect", copper_layers=None)


class _Board:
    def __init__(self, fps, pads_by_uuid, tracks, vias):
        self._fps = fps
        self._pads = pads_by_uuid
        self._tracks = tracks
        self._vias = vias

    def get_footprints(self):
        return list(self._fps)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.uuid, ()))

    def get_field_value(self, fp, name):
        return getattr(fp, "_fields", {}).get(name)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)


def _build(rng):
    fps = [_footprint(i) for i in range(N_FOOTPRINTS)]
    pads_by_uuid = {fp.uuid: [_pad(fp)] for fp in fps}
    tracks = []
    for i in range(N_TRACKS):
        fp = fps[rng.randrange(N_FOOTPRINTS)]
        dx = rng.uniform(-1.0, 1.0)
        dy = rng.uniform(-1.0, 1.0)
        x = fp.position.x / 1_000_000.0
        y = fp.position.y / 1_000_000.0
        tracks.append(Track(uuid=f"t-{i}", net_name="N",
                            start=Vector2.from_xy_mm(x, y),
                            end=Vector2.from_xy_mm(x + dx, y + dy),
                            width_mm=0.25, layer=F_CU))
    vias = []
    for i in range(N_VIAS):
        fp = fps[rng.randrange(N_FOOTPRINTS)]
        vias.append(Via(uuid=f"v-{i}",
                        position=Vector2.from_xy_mm(fp.position.x / 1_000_000.0,
                                                    fp.position.y / 1_000_000.0),
                        net_name="N", drill_mm=0.3, diameter_mm=0.6))
    return _Board(fps, pads_by_uuid, tracks, vias)


def _timed(label, fn):
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    print(f"{label:<34} {elapsed:8.3f} s")
    return result


def main() -> None:
    rng = random.Random(20261005)
    board = _build(rng)
    fps = board.get_footprints()
    instance = fps[:N_INSTANCE]
    tracks, vias = board.get_tracks(), board.get_vias()

    cell_pads = cell_pad_areas(board, instance)
    islands = build_cell_islands(cell_pads, [])

    class_fps = {"cell": instance}
    for fp in fps[N_INSTANCE:]:
        class_fps.setdefault("C" + str(fp._fields[CLUSTER_FIELD_NAME]),
                             []).append(fp)
    all_pads = pad_areas(board, class_fps)
    foreign_pads = pad_areas(board, {k: v for k, v in class_fps.items()
                                     if k != "cell"})

    print(f"copper items: {len(tracks) + len(vias)}, pads: {len(all_pads)}, "
          f"instance pads: {len(cell_pads)}")
    _timed("copper_classes", lambda: copper_classes(tracks, vias, all_pads))
    _timed("copper_pieces",
           lambda: copper_pieces(tracks, vias, islands, foreign_pads))
    _timed("classify_copper",
           lambda: classify_copper(tracks, vias, islands, foreign_pads))


if __name__ == "__main__":
    main()
