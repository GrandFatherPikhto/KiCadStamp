#!/usr/bin/env python3
"""Tests for kicadstamp/explore.py — read-only Board/select() facade."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import BoardLayer
from kicadstamp.domain.geometry import Vector2

from kicadstamp.constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.explore import Board, selection_signature


def _make_pad(number, net_name):
    pad = MagicMock()
    pad.number = number
    pad.net_name = net_name
    return pad


def _make_fp(ref, role=None, cluster=None, nets=None, sheet_uuid=None):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._cluster = cluster
    fp._pads = [_make_pad(str(i + 1), n) for i, n in enumerate(nets or [])]
    fp.sheet_path_uuids = (
        (sheet_uuid, f"{ref}-own-uuid") if sheet_uuid is not None else ()
    )
    return fp


def _make_via(net_name):
    v = MagicMock()
    v.net_name = net_name
    return v


def _make_track(net_name):
    t = MagicMock()
    t.net_name = net_name
    return t


def _adapter_for(fps, vias=None, tracks=None):
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_field_value.side_effect = lambda fp, name: (
        fp._role if name == ROLE_FIELD_NAME else
        fp._cluster if name == CLUSTER_FIELD_NAME else None
    )
    adapter.get_footprint_pads.side_effect = lambda fp: list(fp._pads)
    adapter.get_vias.return_value = vias or []
    adapter.get_tracks.return_value = tracks or []
    return adapter


def _board(fps, sheet_names=None, vias=None, tracks=None):
    adapter = _adapter_for(fps, vias=vias, tracks=tracks)
    board = Board(adapter, sheet_names or {})
    board.refresh()
    return board, adapter


def test_select_by_role_exact_match():
    fps = [_make_fp("IC2", role="AD_DAC"), _make_fp("C1", role="C_OUT_BYPASS")]
    board, _ = _board(fps)
    result = board.select(role="AD_DAC")
    assert [s.ref for s in result] == ["IC2"]


def test_select_by_cluster_uses_prefix_matching():
    """Real ambiguity hit live 2026-07-28: select(cluster=...) must use the
    SAME segment-prefix semantics as the real anchor_cluster resolver
    (cluster_prefix_match), not exact equality — 'Channel_1' matches
    'Channel_1/1V2_PLL' but must NOT match 'Channel_10'."""
    fps = [
        _make_fp("C1", cluster="Channel_1"),
        _make_fp("C2", cluster="Channel_1/1V2_PLL"),
        _make_fp("C3", cluster="Channel_10"),
    ]
    board, _ = _board(fps)
    result = board.select(cluster="Channel_1")
    assert {s.ref for s in result} == {"C1", "C2"}


def test_select_by_sheet():
    fps = [
        _make_fp("IC2", role="AD_DAC", sheet_uuid="uuid-0"),
        _make_fp("IC3", role="AD_DAC", sheet_uuid="uuid-1"),
    ]
    sheet_names = {"uuid-0": "Channel_0", "uuid-1": "Channel_1"}
    board, _ = _board(fps, sheet_names)
    result = board.select(role="AD_DAC", sheet="Channel_0")
    assert [s.ref for s in result] == ["IC2"]


def test_select_by_net():
    fps = [
        _make_fp("R33", nets=["/Channel_0/DAC/DAC_OUT_P", "/Channel_0/OpAmp/OA_IN_P"]),
        _make_fp("R39", nets=["/Channel_0/OpAmp/OA_OUT_P", "/Channel_0/OpAmp/PA_IN_P"]),
    ]
    board, _ = _board(fps)
    result = board.select(net="/Channel_0/DAC/DAC_OUT_P")
    assert [s.ref for s in result] == ["R33"]
    assert result[0].nets == {"1": "/Channel_0/DAC/DAC_OUT_P", "2": "/Channel_0/OpAmp/OA_IN_P"}


def test_select_combined_filters_and_ambiguity_visible():
    """The exact R_TERM_P ambiguity that caused a live fatal: role repeats
    twice per sheet — select() surfaces both candidates instead of failing,
    letting the ambiguity be seen BEFORE running apply."""
    fps = [
        _make_fp("R33", role="R_TERM_P", sheet_uuid="uuid-0",
                 nets=["/Channel_0/DAC/DAC_OUT_P", "/Channel_0/OpAmp/OA_IN_P"]),
        _make_fp("R39", role="R_TERM_P", sheet_uuid="uuid-0",
                 nets=["/Channel_0/OpAmp/OA_OUT_P", "/Channel_0/OpAmp/PA_IN_P"]),
    ]
    board, _ = _board(fps, {"uuid-0": "Channel_0"})
    result = board.select(role="R_TERM_P", sheet="Channel_0")
    assert {s.ref for s in result} == {"R33", "R39"}


def test_empty_selection_show_does_not_crash(capsys):
    board, _ = _board([])
    board.select(role="NOPE").show()
    assert "(empty)" in capsys.readouterr().out


def test_populated_selection_show_does_not_crash(capsys):
    fps = [_make_fp("IC2", role="AD_DAC", cluster="Channel_0",
                     nets=["NET_A", "NET_B"], sheet_uuid="uuid-0")]
    board, _ = _board(fps, {"uuid-0": "Channel_0"})
    board.select(role="AD_DAC").show()
    out = capsys.readouterr().out
    assert "IC2" in out and "AD_DAC" in out


def test_caching_avoids_refetching_footprints_across_selects():
    fps = [_make_fp("IC2", role="AD_DAC")]
    board, adapter = _board(fps)
    assert adapter.get_footprints.call_count == 1

    board.select(role="AD_DAC")
    board.select(role="OTHER")
    board.select(cluster="X")
    assert adapter.get_footprints.call_count == 1  # still just the one from refresh()

    board.refresh()
    assert adapter.get_footprints.call_count == 2


def test_caching_avoids_refetching_fields_per_footprint():
    fps = [_make_fp("IC2", role="AD_DAC", cluster="Channel_0")]
    board, adapter = _board(fps)

    board.select(role="AD_DAC")
    board.select(cluster="Channel_0")
    board.select(role="AD_DAC", cluster="Channel_0")

    # get_field_value called once for Role and once for Cluster for IC2,
    # not once per select() call.
    assert adapter.get_field_value.call_count == 2


class TestSelectItems:
    """select_items() — raw mixed FootprintInstance/Via/Track list, for
    scripted extract (template_extraction.extract_template_from_selection(items=...))
    instead of live GUI selection."""

    def test_net_filter_includes_matching_vias_and_tracks(self):
        fps = [_make_fp("R33", nets=["/Channel_0/DAC/DAC_OUT_P"])]
        via_match = _make_via("/Channel_0/DAC/DAC_OUT_P")
        via_other = _make_via("GND")
        track_match = _make_track("/Channel_0/DAC/DAC_OUT_P")
        track_other = _make_track("/Channel_1/DAC/DAC_OUT_P")
        board, _ = _board(fps, vias=[via_match, via_other], tracks=[track_match, track_other])

        items = board.select_items(net="/Channel_0/DAC/DAC_OUT_P")

        assert items == [fps[0], via_match, track_match]

    def test_role_only_returns_footprints_no_vias_or_tracks(self):
        fps = [_make_fp("IC2", role="AD_DAC")]
        board, adapter = _board(fps, vias=[_make_via("GND")], tracks=[_make_track("GND")])

        items = board.select_items(role="AD_DAC")

        assert items == [fps[0]]
        adapter.get_vias.assert_not_called()
        adapter.get_tracks.assert_not_called()


# ── selection_signature ─────────────────────────────────────────────────

def test_selection_signature_footprints_key_on_refdes():
    assert selection_signature([_make_fp("R1"), _make_fp("R2")]) == (
        ("fp", "R1"), ("fp", "R2"))


def test_selection_signature_non_footprints_key_by_type_and_net():
    class _Via:
        def __init__(self, net):
            self.net_name = net

    class _Track:
        def __init__(self, net):
            self.net_name = net

    class _NoNet:
        pass

    assert selection_signature([_Via("GND"), _Track("+5V"), _NoNet()]) == (
        ("_Via", "GND"), ("_Track", "+5V"), ("_NoNet", None))


def test_selection_signature_mixed_and_stable():
    items = [_make_fp("R1"), _make_via("GND")]
    assert selection_signature(items) == (("fp", "R1"), ("MagicMock", "GND"))
    assert selection_signature(items) == selection_signature(items)


def test_selection_signature_empty():
    assert selection_signature([]) == ()
