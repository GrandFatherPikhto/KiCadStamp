# tests/fakes/imprint_adapter.py
"""One stand-in for the imprint capture/diff adapter seam.

Ф1.4e family B. `tests/gui/test_imprint.py` and `tests/test_imprint_capture.py`
carried the SAME five-method adapter — pads keyed by footprint ref, boxes around
items, the same Footprint/Pad/Via half-sizes — differing only in docstring and in
whether the constructor takes defensive copies. That is ONE form, so it moves
here and both files import it.

This is deliberately NOT the story of the overlay pair
(`test_board_overlay.py` / `test_overlay_markers.py`): those share only method
NAMES, diverge in behaviour (real removal + board-assigned ids vs record-only),
and each relies on its OWN file-local `_FakeBoard` default (its own layer
constants) — the "two families that share a name, not a surface" lesson already
written down in tests/fakes/board.py. They stay local.

All five reads — including `get_bounding_boxes` — are declared on `IBoardAdapter`
itself, so the fake invents nothing: ``SEAM_GAPS`` is empty. Correspondence cell:
tests/test_fakes_conformance.py.
"""
from __future__ import annotations

from kicadstamp.domain.board import Footprint, Pad, Via
from kicadstamp.domain.geometry import Box2, Vector2
from kicadstamp.utils.units import MM


class FakeImprintAdapter:
    """Mock board adapter: pads keyed by footprint ref, boxes around items."""

    #: Empty: every method below is declared on `IBoardAdapter` itself, so this
    #: fake invents nothing. Kept explicit so the conformance cell has one place
    #: to read and would fail loudly if a name were added without a seam entry.
    SEAM_GAPS = ()

    def __init__(self, footprints, tracks, vias, pads_by_ref):
        self._fps = list(footprints)
        self._tracks = list(tracks)
        self._vias = list(vias)
        self._pads = dict(pads_by_ref)

    def get_footprints(self):
        return list(self._fps)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, []))

    def get_bounding_boxes(self, items):
        out = []
        for it in items:
            if isinstance(it, Footprint):
                half = int(2.0 * MM)
            elif isinstance(it, Pad):
                # Real pad boxes are the closure filter's ANCHOR set — without
                # them capture falls back to the both-ends rule and drops the
                # (perfectly valid) line copper.
                half = int(0.5 * MM)
            elif isinstance(it, Via):
                half = max(int((it.diameter_mm / 2) * MM), int(0.25 * MM))
            else:
                out.append(None)
                continue
            p = it.position
            out.append(Box2(pos=Vector2.from_xy(p.x - half, p.y - half),
                            size=Vector2.from_xy(2 * half, 2 * half)))
        return out
