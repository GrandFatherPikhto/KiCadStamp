# tests/fakes/live_board.py
"""`FakeLiveBoardAdapter` — the live-board stand-in the two redraw/reconcile
harnesses shared under the name `_MockAdapter`.

Ф1.4d-5. The two copies carried the same live-board behaviour with four real
differences, and the redraw copy's own docstring says why:

  * UUID SOURCE. The reposition copy numbered items by POSITION
    (`f"uuid-{len(self.live_tracks)}"`); the redraw copy used a monotonic
    counter, because "UUIDs come from ONE monotonic counter so a via and a track
    never collide on a uuid". The counter is the compatible superset — the
    reposition file asserts no literal uuid — so the base carries the counter and
    BOTH files keep working, the collision guarantee included.
  * VIAS. The reposition copy returned a bare `[]` from `get_vias()` and its
    `remove_by_ids` touched tracks only; the redraw copy keeps a `live_vias`
    list, creates vias, and clears both kinds. Keeping the vias costs the
    reposition file nothing (it creates none) and the two stop disagreeing.
  * THE FOOTPRINT. The reposition copy takes it — `_MockAdapter(fp)` — because it
    MUTATES `.position` between runs to simulate the user moving the FPGA; the
    redraw copy builds its own inside `__init__`. So the base REQUIRES it and the
    redraw file passes its own `_make_fp()`, which keeps that file's fixture in
    that file instead of hiding a footprint inside the shared fake.
  * THE IGNORE-SELECTION CONTEXT. A nested class in one copy, a module-level
    class in the other — same behaviour. The base keeps one, and it deliberately
    does NOT swallow exceptions: dependency_order and clone_position_calculator
    use it as a plain `with`.

And one method is DROPPED here, on evidence: both copies defined
`get_footprint_by_ref`, and it is called NOWHERE — not in production (it exists
on neither `IBoardAdapter` nor the concrete adapter) and not by any test. The
seam's name for that read is `get_footprint(ref)`, which the base keeps. The
conformance cell asserts the dead name does not come back, because a fake
carrying a method no production caller can reach is exactly what these cells
exist to catch.

`temporarily_ignore_selection` is a REAL production method
(kicadstamp/kicad/adapter.py:184, called by dependency_order and
clone_position_calculator) that the ABC omits, so it is declared in SEAM_GAPS —
the same two-way check tests/fakes/adapter.py uses for the same reason.

The constructor is NOT pinned against anything: `(fp)` is a test-shaped entry
point, like `FakeNetBoard`'s `.adapter`, not a stand-in for how production builds
an adapter.
"""
from __future__ import annotations

from unittest.mock import MagicMock


class _IgnoreSelection:
    """What `temporarily_ignore_selection` returns: a no-op `with` body that does
    not suppress an exception raised inside it (`__exit__` returns False)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeLiveBoardAdapter:
    """See the module docstring: the real surface, a live item list behind it."""

    #: Real methods the ABC does not declare. The conformance cell checks each one
    #: really exists on the concrete adapter, so this cannot become a dumping
    #: ground for whatever a fake happens to grow.
    SEAM_GAPS = ("temporarily_ignore_selection",)

    def __init__(self, fp) -> None:
        self.live_tracks = []
        self.live_vias = []
        self._uuid_counter = 0
        self._fp = fp

    # ── footprint / role / net resolution ───────────────────────────────────
    def get_footprints(self):
        return [self._fp]

    def get_footprint(self, ref):
        return self._fp if ref == "U1" else None

    def get_field_value(self, footprint, field_name):
        if hasattr(footprint, "get_field_value"):
            return footprint.get_field_value(field_name)
        return None

    def get_pad_by_number(self, fp, number):
        return fp.pad(number)

    def get_footprint_pads(self, fp):
        return list(fp.pads.values())

    def get_net_by_name(self, name):
        n = MagicMock()
        n.name = name
        return n

    def get_selected_items(self):
        return []

    # ── the live board ──────────────────────────────────────────────────────
    def get_tracks(self):
        return list(self.live_tracks)

    def get_vias(self):
        return list(self.live_vias)

    def create_track(self, start, end, width_mm, net, layer):
        t = MagicMock()
        t.start = start
        t.end = end
        t.width_mm = width_mm
        t.net_name = net.name if hasattr(net, "name") else net
        t.layer = layer
        t.uuid = None
        return t

    def create_via(self, position, net, drill_mm, diameter_mm):
        v = MagicMock()
        v.position = position
        v.drill_mm = drill_mm
        v.diameter_mm = diameter_mm
        v.net_name = net.name if hasattr(net, "name") else net
        v.uuid = None
        return v

    def create_items(self, items):
        # ONE counter for tracks AND vias: they share a uuid namespace on a real
        # board, and the whole point of the redraw harness is that a via and a
        # track never collide.
        for item in items:
            self._uuid_counter += 1
            item.uuid = f"uuid-{self._uuid_counter}"
        return items

    def commit_with_retry(self, description, work_fn, retries=1):
        work_fn()
        return True

    def remove_by_ids(self, uuid_strs):
        uuid_strs = set(uuid_strs)
        self.live_tracks[:] = [t for t in self.live_tracks
                               if t.uuid not in uuid_strs]
        self.live_vias[:] = [v for v in self.live_vias
                             if v.uuid not in uuid_strs]
        return True

    def refresh_board(self):
        pass

    def temporarily_ignore_selection(self, active):
        return _IgnoreSelection()


__all__ = ["FakeLiveBoardAdapter"]
