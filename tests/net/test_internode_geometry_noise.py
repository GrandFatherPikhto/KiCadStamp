# tests/net/test_internode_geometry_noise.py
"""«updated» must mean CHANGED COPPER, not another sequence of the same numbers.

Ч1 of plan_2026_10_26_reread_updated_noise. The Ч0 measurement (its report, §2-§5)
found the noise to be ORDER, not precision: of 79 records the re-read called
"updated" on v103, 26 had unchanged counts and 9 of those were the very same
segments in another sequence — the stored record keeps the board order of the
session that wrote it, the fresh capture keeps today's. A tolerance was measured
to be unnecessary (the file round trip loses nothing: 0 of 111 records), and a
tolerance would have eaten real changes, so the comparison is EXACT and
`internode_nodes.same_geometry` compares a MULTISET of elements:

    track — canonicalised ends + width_mm + layer;
    via   — along/across + drill + diameter.

Denis's boundary: a 0.01 mm shift stays `updated`, a mere permutation (or a
reversed segment) is `unchanged`. These cells pin both sides, and the mutation
harness `kicadstamp/diagnostics/deepseek_mutations_reread_noise_2026_10_26.py`
proves each cell can go red.
"""
from types import SimpleNamespace

import dataclasses
import pytest

from kicadstamp.config import (Config, Entity, NetTrace, TemplateTrack,
                               TemplateVia, net_trace_effective_name)
from kicadstamp.config.loader import load_config
from kicadstamp.config_writer import write_config_file
from kicadstamp.domain.board import BoardLayer, Footprint, Track, Via
from kicadstamp.internode_capture import plan_internode_reread
from kicadstamp.internode_nodes import same_geometry
from kicadstamp.net_trace_extract import net_trace_to_dict
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

# ── records ───────────────────────────────────────────────────────────────

def _track(sa=0.0, sac=0.0, ea=10.0, eac=0.0, width=0.25, layer="F.Cu"):
    return TemplateTrack(start_along_mm=sa, start_across_mm=sac,
                         end_along_mm=ea, end_across_mm=eac, width_mm=width,
                         layer=layer, net_from_role="A", net_from_role_pad="1")


def _via(along=1.5, across=-2.25, drill=0.4, diameter=0.8):
    return TemplateVia(offset_along_mm=along, offset_across_mm=across,
                       drill_mm=drill, diameter_mm=diameter,
                       net_from_role="A", net_from_role_pad="1")


def _record(tracks=(), vias=(), name="n__a__b") -> NetTrace:
    return NetTrace(net="N", anchor_role="A", name=name, anchor_pad="1",
                    anchor_cluster="A", anchor_rotation_deg=0.0,
                    pads=["A.1", "B.1"], tracks=list(tracks), vias=list(vias))


# ── the comparison itself ─────────────────────────────────────────────────

def test_a_written_record_reads_back_as_the_same_copper(tmp_path):
    """Cell 1 — the FILE round trip. Measured on v103 as 0 of 111; here the same
    numbers (4 decimals, the write step) must come back unchanged, or a
    tolerance would have to be invented for a loss that does not exist."""
    record = _record(tracks=[_track(sa=-3.9949, sac=2.9949, ea=-5.0008,
                                    eac=2.9949, width=0.2)],
                     vias=[_via()])
    path = tmp_path / "config.sexp"
    write_config_file(path, {"version": 3,
                             "net_traces": [net_trace_to_dict(record)]},
                      graph_root=path)
    loaded, _ctx = load_config(str(path))
    assert len(loaded.net_traces) == 1
    assert same_geometry(loaded.net_traces[0], record)


def test_the_same_copper_in_another_order_is_unchanged():
    """Cell 2 — the measured noise: the stored record's sequence is the board
    order of ITS session, the fresh capture's is today's."""
    first, second, third = (_track(ea=1.0), _track(sa=1.0, ea=2.0),
                            _track(sa=2.0, ea=3.0))
    assert same_geometry(_record([first, second, third]),
                         _record([third, first, second]))


def test_a_track_with_its_ends_swapped_is_unchanged():
    """Cell 3 — a segment is UNORIENTED (Денис: normalise direction, 26.10)."""
    assert same_geometry(_record([_track(sa=1.0, sac=2.0, ea=3.0, eac=4.0)]),
                         _record([_track(sa=3.0, sac=4.0, ea=1.0, eac=2.0)]))


def test_vias_in_another_order_are_unchanged():
    """Cell 13 — the same for the via list."""
    assert same_geometry(_record(vias=[_via(along=1.0), _via(along=2.0)]),
                         _record(vias=[_via(along=2.0), _via(along=1.0)]))


def test_two_identical_tracks_swapped_are_unchanged():
    """Cell 11 — swapping two IDENTICAL elements is not a change either."""
    one, two = _track(ea=1.0), _track(sa=5.0, ea=6.0)
    assert same_geometry(_record([one, two]), _record([two, one]))


@pytest.mark.parametrize("shift_mm", [0.01, 0.0002])
def test_a_shift_is_updated(shift_mm):
    """Cells 4-5 — the operator's boundary: 0.01 mm stays `updated`; so does
    0.0002 mm, because it is a number the file CAN carry (4 decimals) and the
    round trip proves it survives. A tolerance would have to be justified, and
    the measurement gave none."""
    before = _record([_track(sa=1.0, ea=2.0)])
    after = _record([_track(sa=1.0 + shift_mm, ea=2.0)])
    assert not same_geometry(before, after)


def test_another_width_is_updated():
    """Cell 6."""
    assert not same_geometry(_record([_track(width=0.2)]),
                             _record([_track(width=0.25)]))


def test_another_layer_is_updated():
    """Cell 7."""
    assert not same_geometry(_record([_track(layer="F.Cu")]),
                             _record([_track(layer="B.Cu")]))


def test_an_extra_via_is_updated():
    """Cell 8."""
    assert not same_geometry(_record(vias=[_via()]),
                             _record(vias=[_via(), _via(along=4.0)]))


def test_a_missing_via_is_updated():
    """Cell 9."""
    assert not same_geometry(_record(vias=[_via(), _via(along=4.0)]),
                             _record(vias=[_via()]))


def test_two_tracks_trading_their_widths_are_updated():
    """Cell 10 — order is normalised, the ELEMENTS are not. Two segments keep
    their places and trade widths: the multiset of (ends, width, layer) differs,
    so this is a real change. Width sitting INSIDE the element (next to the ends)
    is what makes the swap a change instead of a re-ordering."""
    first = _track(sa=0.0, ea=1.0, width=0.2)
    second = _track(sa=5.0, ea=6.0, width=0.5)
    assert not same_geometry(
        _record([first, second]),
        _record([_track(sa=0.0, ea=1.0, width=0.5),
                 _track(sa=5.0, ea=6.0, width=0.2)]))


def test_two_tracks_swapped_are_unchanged_even_at_different_widths():
    """Cell 10b — and the other side of it: swapping the SAME two elements is
    only a permutation, whatever their widths are. Sequence is not copper; the
    Ч0 measurement is exactly this case on v103 (9 of 79 records)."""
    thin, thick = _track(sa=0.0, ea=1.0, width=0.2), _track(sa=5.0, ea=6.0,
                                                            width=0.5)
    assert same_geometry(_record([thin, thick]), _record([thick, thin]))


def test_another_order_with_one_shifted_end_is_updated():
    """Cell 12 — a mixed pair: the sort must not swallow a moved end."""
    first, second = _track(ea=1.0), _track(sa=1.0, ea=2.0)
    moved = _track(sa=1.01, ea=2.0)
    assert not same_geometry(_record([first, second]),
                             _record([moved, first]))


def test_losing_one_of_two_identical_tracks_is_updated():
    """Cell 15 — a lost duplicate is a lost piece of copper (the count guard)."""
    twin = _track(ea=1.0)
    assert not same_geometry(_record([twin, twin]), _record([twin]))


def test_a_duplicate_moving_between_two_places_is_updated():
    """Cell 14 — MULTISET, not set: the same THREE segments on both sides, but
    one place holds two copies where the other holds one. A set would call this
    "unchanged" (same distinct elements) while a piece of copper has moved; the
    multiplicity is part of the copper."""
    first, second = _track(ea=1.0), _track(sa=5.0, ea=6.0)
    assert not same_geometry(
        _record([first, first, second]),
        _record([first, second, second]))


# ── the door: the re-read itself ──────────────────────────────────────────

def _fp(ref, role, cluster, x_mm, y_mm):
    fp = Footprint(ref=ref, uuid=f"fp-{ref}",
                   position=SimpleNamespace(x=int(x_mm * 1e6),
                                            y=int(y_mm * 1e6)),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._cluster = cluster
    return fp


def _pad(number, net, x_mm, y_mm):
    return SimpleNamespace(number=str(number), net_name=net,
                           position=SimpleNamespace(x=int(x_mm * 1e6),
                                                    y=int(y_mm * 1e6)))


def _board_track(x1, y1, x2, y2, net="N"):
    return Track(uuid=f"t{x1}{y1}{x2}{y2}",
                 start=SimpleNamespace(x=int(x1 * 1e6), y=int(y1 * 1e6)),
                 end=SimpleNamespace(x=int(x2 * 1e6), y=int(y2 * 1e6)),
                 net_name=net, width_mm=0.25, layer=BoardLayer.BL_F_Cu)


class _Board:
    """Just enough board for the re-read: two components, one net, copper items
    handed in by the test (the same double `test_internode_capture.py` uses)."""

    def __init__(self, footprints, pads_by_ref):
        self._fps = list(footprints)
        self._pads = pads_by_ref

    def get_footprints(self):
        return list(self._fps)

    def get_selected_items(self):
        return []

    def get_field_value(self, fp, name):
        return {"Role": getattr(fp, "_role", None),
                "Cluster": getattr(fp, "_cluster", None)}.get(name)

    def get_footprint(self, ref):
        return next((f for f in self._fps if f.ref == ref), None)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, []))

    def get_pad_by_number(self, fp, num):
        for pad in self._pads.get(fp.ref, []):
            if str(pad.number) == str(num):
                return pad
        return None

    def get_bounding_boxes(self, items):
        boxes = []
        for item in items:
            box = SimpleNamespace()
            box.pos = SimpleNamespace(x=int(item.position.x - 0.3e6),
                                      y=int(item.position.y - 0.3e6))
            box.size = SimpleNamespace(x=600_000, y=600_000)
            box.inflate = lambda _d: None
            boxes.append(box)
        return boxes


def _two_segment_area():
    """R1 (node A) at (10,10), R2 (node B) at (20,10), TWO tracks joined at
    (15,10) — a joint that is on no pad, so the two form ONE unit with two
    stored segments (a one-segment unit cannot show a permutation)."""
    footprints = [_fp("R1", "A", "A", 10, 10), _fp("R2", "B", "B", 20, 10)]
    pads = {"R1": [_pad("1", "N", 10, 10)], "R2": [_pad("1", "N", 20, 10)]}
    items = [_board_track(10, 10, 15, 10), _board_track(15, 10, 20, 10)]
    return _Board(footprints, pads), footprints, items


def _cfg(net_traces=()):
    return Config(entities=[Entity(name="e_a", cell="c", cluster="A"),
                            Entity(name="e_b", cell="c", cluster="B")],
                  net_traces=list(net_traces))


def _tree(refs=()):
    nodes = [TreeNode(ref="e_a", kind="placement", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[]),
             TreeNode(ref="e_b", kind="placement", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[])]
    nodes += [TreeNode(ref=r, kind="net_trace", xy=None, polar=None,
                       rotation=0.0, name=None, group=None, children=[])
              for r in refs]
    return Tree(name="t", anchor=TreeAnchor(is_origin=True), nodes=nodes)


def test_the_reread_calls_the_same_copper_in_reverse_order_unchanged():
    """Cell 2 at the door — the CAPTURE order must not matter: the record the
    first re-read creates is `unchanged` when the very same board is read with
    its copper items in the opposite order."""
    board, footprints, items = _two_segment_area()
    first = plan_internode_reread(board, _cfg(), _tree(), config_path=None,
                                  area_items=items, area_footprints=footprints)
    assert len(first.added) == 1
    created = first.added[0].record
    identity = net_trace_effective_name(created)
    assert len(created.tracks) == 2

    second = plan_internode_reread(board, _cfg([created]), _tree([identity]),
                                   config_path=None,
                                   area_items=list(reversed(items)),
                                   area_footprints=footprints)
    assert second.unchanged == [identity]
    assert second.updated == []


def test_a_stored_record_with_its_tracks_reversed_is_unchanged():
    """The measured defect itself: the STORED sequence against today's capture."""
    board, footprints, items = _two_segment_area()
    first = plan_internode_reread(board, _cfg(), _tree(), config_path=None,
                                  area_items=items, area_footprints=footprints)
    created = first.added[0].record
    identity = net_trace_effective_name(created)
    stored = dataclasses.replace(created, tracks=list(reversed(created.tracks)))

    second = plan_internode_reread(board, _cfg([stored]), _tree([identity]),
                                   config_path=None, area_items=items,
                                   area_footprints=footprints)
    assert second.unchanged == [identity]
    assert second.updated == []


def test_a_stored_record_with_one_end_moved_is_updated():
    """The other side of the same door: a moved end is still `updated`, so the
    normalisation did not turn the comparison into "whatever"."""
    board, footprints, items = _two_segment_area()
    first = plan_internode_reread(board, _cfg(), _tree(), config_path=None,
                                  area_items=items, area_footprints=footprints)
    created = first.added[0].record
    identity = net_trace_effective_name(created)
    moved_track = dataclasses.replace(created.tracks[0],
                                     end_along_mm=created.tracks[0].end_along_mm
                                     + 0.01)
    stored = dataclasses.replace(created, tracks=[moved_track,
                                                  created.tracks[1]])

    second = plan_internode_reread(board, _cfg([stored]), _tree([identity]),
                                   config_path=None, area_items=items,
                                   area_footprints=footprints)
    assert [net_trace_effective_name(c.record) for c, _ in second.updated] == \
        [identity]
