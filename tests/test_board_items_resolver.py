#!/usr/bin/env python3
"""Tests for board_items_resolver.py — resolving which live-board items
(components + registered copper) currently belong to a ClonePlacement /
CoordinatePlacement. See handoff_2026_08_25_clone_item_resolver_select_and_reextract.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import BoardLayer, Footprint, Track, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.config import (Cell, ClonePlacement, Config, CoordinatePlacement,
                               RuntimeContext, TemplateComponentSlot)
from kicadstamp.placement.services.board_items_resolver import resolve_clone_board_items
from kicadstamp.placement.services.clone_position_calculator import clone_anchor_id
from kicadstamp.registry import (RegistryEntry, TrackRegistryEntry, make_registry_key,
                                 save_registry, save_track_registry)


def _make_fp(ref, role=None, nets=None, cluster=None):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._nets = nets or []
    fp._cluster = cluster
    return fp


def _pads_for(fp):
    pads = []
    for i, net in enumerate(fp._nets, start=1):
        p = MagicMock()
        p.number = str(i)
        p.net_name = net
        pads.append(p)
    return pads


def _field_value(fp, field_name):
    if field_name == ROLE_FIELD_NAME:
        return fp._role
    if field_name == CLUSTER_FIELD_NAME:
        return fp._cluster
    return None


def _by_nets_adapter(fps):
    by_ref = {fp.ref: fp for fp in fps}
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_footprint.side_effect = lambda ref: by_ref.get(ref)
    adapter.get_field_value.side_effect = _field_value
    adapter.get_selected_items.return_value = []
    adapter.get_footprint_pads.side_effect = _pads_for
    adapter.temporarily_ignore_selection = None
    return adapter


def _cell():
    return Cell(name="cella", components=[
        TemplateComponentSlot(role="A"),
        TemplateComponentSlot(role="B"),
    ])


def _clone():
    # No anchor set -> clone_anchor_id == f"name:{cluster}".
    return ClonePlacement(cluster="myclone", cell="cella", xy=(0.0, 0.0),
                          nets={"A": "NET_A", "B": "NET_B"})


def _write_copper(tmp_path, anchor_id):
    via_key = make_registry_key(anchor_id, "cella", None, 0)
    track_key = make_registry_key(anchor_id, "cella", None, 0)
    foreign_key = make_registry_key("name:other", "cella", None, 0)

    via_path = tmp_path / "vias.registry.json"
    track_path = tmp_path / "tracks.registry.json"
    save_registry(str(via_path), {
        via_key: RegistryEntry(uuid="via-1", x_mm=0.0, y_mm=0.0, net="NET",
                               drill_mm=0.3, diameter_mm=0.6),
        foreign_key: RegistryEntry(uuid="via-foreign", x_mm=0.0, y_mm=0.0, net="NET",
                                   drill_mm=0.3, diameter_mm=0.6),
    })
    save_track_registry(str(track_path), {
        track_key: TrackRegistryEntry(uuid="track-1", start_x_mm=0.0, start_y_mm=0.0,
                                      end_x_mm=1.0, end_y_mm=0.0, width_mm=0.2,
                                      net="NET", layer="F.Cu"),
    })
    return str(via_path), str(track_path)


def _live_copper():
    via = Via(uuid="via-1", position=Vector2.from_xy(0, 0), net_name="NET",
              drill_mm=0.3, diameter_mm=0.6)
    track = Track(uuid="track-1", start=Vector2.from_xy(0, 0), end=Vector2.from_xy(1, 0),
                  net_name="NET", width_mm=0.2, layer=BoardLayer.BL_F_Cu)
    return via, track


class TestResolveCloneBoardItems:
    def test_by_nets_components_plus_registry_copper(self, tmp_path):
        fps = [_make_fp("R1", "A", nets=["NET_A"]), _make_fp("R2", "B", nets=["NET_B"])]
        adapter = _by_nets_adapter(fps)
        via, track = _live_copper()
        by_uuid = {"via-1": via, "track-1": track}
        adapter.get_items_by_id.side_effect = lambda uuids: [by_uuid[u] for u in uuids]

        clone = _clone()
        cfg = Config(cells={"cella": _cell()})
        reg_path, track_path = _write_copper(tmp_path, clone_anchor_id(clone))

        items = resolve_clone_board_items(adapter, cfg, RuntimeContext(), clone,
                                          registry_path=reg_path,
                                          track_registry_path=track_path)

        assert items == [fps[0], fps[1], via, track]

    def test_by_selection_components(self):
        fps = [_make_fp("R1", "A"), _make_fp("R2", "B")]
        adapter = _by_nets_adapter(fps)
        adapter.get_selected_items.return_value = fps
        clone = ClonePlacement(cluster="myclone", cell="cella", xy=(0.0, 0.0),
                               by_selection=True)

        items = resolve_clone_board_items(adapter, Config(cells={"cella": _cell()}),
                                          RuntimeContext(), clone)

        assert items == fps

    def test_copper_excludes_other_anchor_ids(self, tmp_path):
        fps = [_make_fp("R1", "A", nets=["NET_A"]), _make_fp("R2", "B", nets=["NET_B"])]
        adapter = _by_nets_adapter(fps)
        via, track = _live_copper()
        by_uuid = {"via-1": via, "track-1": track, "via-foreign": via}
        adapter.get_items_by_id.side_effect = lambda uuids: [by_uuid[u] for u in uuids]

        clone = _clone()
        cfg = Config(cells={"cella": _cell()})
        reg_path, track_path = _write_copper(tmp_path, clone_anchor_id(clone))

        items = resolve_clone_board_items(adapter, cfg, RuntimeContext(), clone,
                                          registry_path=reg_path,
                                          track_registry_path=track_path)

        # "via-foreign" (a different anchor_id) must never be returned.
        assert all(getattr(i, "uuid", None) != "via-foreign" for i in items)
        assert items == [fps[0], fps[1], via, track]

    def test_nothing_registered_returns_components_only(self, tmp_path):
        """No registry files at all -> no copper, but the (already on board)
        components still resolve — the empty-copper case must not crash."""
        fps = [_make_fp("R1", "A", nets=["NET_A"]), _make_fp("R2", "B", nets=["NET_B"])]
        adapter = _by_nets_adapter(fps)
        adapter.get_items_by_id = None

        clone = _clone()
        cfg = Config(cells={"cella": _cell()})

        items = resolve_clone_board_items(adapter, cfg, RuntimeContext(), clone,
                                          registry_path=str(tmp_path / "none.registry.json"),
                                          track_registry_path=str(tmp_path / "none.tracks.registry.json"))

        assert items == fps

    def test_scan_fallback_when_no_get_items_by_id(self, tmp_path):
        """A bare adapter double (no get_items_by_id) falls back to a single
        get_vias()+get_tracks() scan keyed by uuid."""
        fps = [_make_fp("R1", "A", nets=["NET_A"]), _make_fp("R2", "B", nets=["NET_B"])]
        adapter = _by_nets_adapter(fps)
        adapter.get_items_by_id = None
        via, track = _live_copper()
        adapter.get_vias.return_value = [via]
        adapter.get_tracks.return_value = [track]

        clone = _clone()
        cfg = Config(cells={"cella": _cell()})
        reg_path, track_path = _write_copper(tmp_path, clone_anchor_id(clone))

        items = resolve_clone_board_items(adapter, cfg, RuntimeContext(), clone,
                                          registry_path=reg_path,
                                          track_registry_path=track_path)

        assert items == [fps[0], fps[1], via, track]

    def test_coordinate_placement_single_footprint(self):
        fp = _make_fp("R1", "A", cluster="C1")
        adapter = MagicMock()
        adapter.get_footprints.return_value = [fp]
        adapter.get_field_value.side_effect = _field_value
        cp = CoordinatePlacement(cluster="C1", role="A")

        items = resolve_clone_board_items(adapter, None, RuntimeContext(), cp)

        assert items == [fp]


class TestExtractItemsParity:
    """extract_template_from_selection(items=X) must produce the SAME cell as
    an adapter whose get_selected_items() returns X — the whole point of the
    re-extract feature: the resolver's list replaces the mouse selection, and
    the downstream extractor must not care where the items came from."""

    def test_explicit_items_matches_selection(self):
        from kicadstamp.template_extraction import extract_template_from_selection

        fps = [_make_fp("R1", "A"), _make_fp("R2", "B")]

        def adapter_with(items):
            adapter = MagicMock()
            adapter.get_selected_items.return_value = items
            adapter.get_field_value.side_effect = _field_value
            adapter.get_footprint_pads.return_value = []
            return adapter

        explicit = extract_template_from_selection(adapter_with([]), "cell", items=fps)
        via_selection = extract_template_from_selection(adapter_with(fps), "cell")

        assert explicit == via_selection
        roles = [c["role"] for c in explicit["cell"]["components"]]
        assert roles == ["A", "B"]
