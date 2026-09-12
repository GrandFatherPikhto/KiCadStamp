#!/usr/bin/env python3
"""Inner copper layers must be ACCEPTED where they describe copper, and still
REJECTED where they describe the mounting SIDE.

Plan: techdocs/handoff/deepseek/plan/plan_2026_09_12_strict_copper_layers.md
(Э1.b, Э4.5, Э4.6); design: design_2026_09_12_cell_copper_layers_full.md §3.1.

The two kinds of `layer:` are not the same thing (design P.1):

  * a TRACK's layer is copper and may be ANY layer of the stack
    (`F.Cu`/`In1.Cu`..`In30.Cu`/`B.Cu`). `extract` already writes the real name
    (template_extraction.py — `layer_to_str(t.layer)`), so the old binary
    loader check made the extractor's own output unloadable:
    "invalid layer='In1.Cu' on track" at load time, for a config the tool
    itself had just written;
  * a component slot / the cell's own layer / `clone_placement` / `entity`
    carry the mounting side, where an inner layer cannot exist — those stay
    binary and are NOT touched by this work.

Э1.b closes the copper case with a SEPARATE `_check_copper_layer_value` (called
only from `_load_template_track`, which serves both `cells:` tracks and
`net_traces:` tracks) and deliberately leaves `_check_layer_value` binary. The
`TestBinarySideChecksStillRejectInnerLayer` guard class at the bottom exists to
keep it that way: whoever later "unifies" the two helpers back into one will
break these tests.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError

INNER = "In1.Cu"


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(dict_to_sexp(data), encoding="utf-8")
    return path


def _cfg(**extra) -> dict:
    """A minimal valid root: explicit F.Cu root layer + empty cells."""
    return {"layer": "F.Cu", "cells": {}, **extra}


class TestCopperSideAcceptsInnerLayer:
    """Э4.5 — these two MUST FAIL before Э1.b (they were red on 38fdc1b)."""

    def test_cell_track_inner_layer_loads(self, tmp_path):
        """A cell track on an inner layer: the loader used to fatal with
        "invalid layer='In1.Cu' on track" — the exact shape `extract` writes
        for a track whose layer differs from the cell's own layer."""
        path = _write(tmp_path, "cell.sexp", _cfg(cells={"c": {"tracks": [
            {"start_along_mm": 0.0, "end_along_mm": 1.0, "width_mm": 0.25,
             "net": "+3V3", "layer": INNER}]}}))
        cfg, _ctx = load_config(str(path))
        assert cfg.cells["c"].tracks[0].layer == INNER

    def test_net_traces_track_inner_layer_loads(self, tmp_path):
        """A net_traces track on an inner layer: rejected twice before Э1.b —
        once by `_check_layer_value` inside `_load_template_track`, once by the
        net_traces "has no layer" guard (which is really "no F/B layer" there).
        The explicit-layer REQUIREMENT stays: only `None` is a missing layer."""
        path = _write(tmp_path, "nt.sexp", _cfg(net_traces=[{
            "net": "N", "name": "bridge", "anchor_role": "FPGA",
            "tracks": [{"start_along_mm": 1.0, "start_across_mm": 2.0,
                        "end_along_mm": 3.0, "end_across_mm": 4.0,
                        "net": "N", "layer": INNER}]}]))
        cfg, _ctx = load_config(str(path))
        assert cfg.net_traces[0].tracks[0].layer == INNER

    def test_net_traces_track_without_layer_is_still_fatal(self, tmp_path):
        """The requirement itself must survive: a layer-less net_traces track
        has no cell to inherit from, so it stays a load-time fatal."""
        path = _write(tmp_path, "nt.sexp", _cfg(net_traces=[{
            "net": "N", "name": "bridge", "anchor_role": "FPGA",
            "tracks": [{"start_along_mm": 1.0, "start_across_mm": 2.0,
                        "end_along_mm": 3.0, "end_across_mm": 4.0,
                        "net": "N"}]}]))
        with pytest.raises(ValidationError, match="has no layer"):
            load_config(str(path))


class TestBinarySideChecksStillRejectInnerLayer:
    """Э4.6 — the guard against "just widen `_check_layer_value`".

    Every field below names the mounting SIDE of a component, not copper:
    an inner layer is physically meaningless there, and P.2.1/P.2.3/P.2.4 of
    the plan forbid loosening them while fixing the copper case.
    """

    def test_component_slot_inner_layer_is_fatal(self, tmp_path):
        path = _write(tmp_path, "slot.sexp", _cfg(cells={"c": {"components": [
            {"role": "R_FB", "layer": INNER}]}}))
        with pytest.raises(ValidationError, match="invalid layer"):
            load_config(str(path))

    def test_cell_own_layer_inner_layer_is_fatal(self, tmp_path):
        path = _write(tmp_path, "cell.sexp", _cfg(cells={"c": {
            "layer": INNER,
            "vias": [{"offset_along_mm": 0.0, "offset_across_mm": 0.0}]}}))
        with pytest.raises(ValidationError, match="invalid layer"):
            load_config(str(path))

    def test_clone_placement_layer_inner_layer_is_fatal(self, tmp_path):
        """`clone_placement.layer` is the placement's side (and the field pair
        mirror/layer is removed wholesale in design stage 3 — not widened)."""
        path = _write(tmp_path, "clone.sexp", _cfg(cells={
            "leaf": {"vias": [{"offset_along_mm": 0.0, "offset_across_mm": 0.0}]},
            "c": {"clone_placements": [
                {"name": "p", "cell": "leaf", "layer": INNER}]}}))
        with pytest.raises(ValidationError, match="invalid layer"):
            load_config(str(path))

    def test_entity_layer_inner_layer_is_fatal(self, tmp_path):
        path = _write(tmp_path, "entity.sexp", _cfg(
            cells={"c": {"vias": [{"offset_along_mm": 0.0, "offset_across_mm": 0.0}]}},
            entities=[{"name": "e", "cell": "c", "layer": INNER}]))
        with pytest.raises(ValidationError, match="invalid layer"):
            load_config(str(path))

    def test_root_layer_inner_layer_is_fatal(self, tmp_path):
        """The root `layer:` is the default side for the chains/ManualSpoke
        path (components) — still binary (plan Э2, "not copper")."""
        path = _write(tmp_path, "root.sexp", {"layer": INNER, "cells": {}})
        with pytest.raises(ValidationError, match="invalid layer"):
            load_config(str(path))


class TestExtractWriteLoadPlanChain:
    """Э4.7 — the real user path end to end: extract a cell whose track sits on
    an inner copper layer, write it out, load it back, plan it.

    This chain was broken in two independent places before this stage: the file
    the extractor itself writes could not be LOADED (Э1.b), and even if it had
    been, the clone path would have moved the track onto F.Cu (Э2)."""

    @staticmethod
    def _extraction_adapter():
        """A mock board selection: one R1 footprint (net NET1) and its track on
        In1.Cu. Pads carry real positions/sizes so the extractor's
        bounding-box closure has something to work with."""
        from kicadstamp.domain.board import Footprint, Track

        fp = Footprint(ref="C1", uuid="uuid-C1",
                       position=Vector2.from_xy_mm(0.0, 0.0), angle_deg=0.0,
                       layer=BoardLayer.BL_F_Cu)
        fp._role = "R1"
        pad = MagicMock()
        pad.net_name = "NET1"
        pad.number = "1"
        pad.position = Vector2.from_xy_mm(0.0, 0.0)
        pad._box_size = int(0.6 * 1_000_000)
        fp._pads = [pad]

        track = Track(uuid="uuid-T1",
                      start=Vector2.from_xy_mm(0.0, 0.0),
                      end=Vector2.from_xy_mm(1.0, 0.0),
                      net_name="NET1", width_mm=0.25,
                      layer=BoardLayer.BL_In1_Cu)

        def _bboxes(items):
            out = []
            for item in items:
                pos = getattr(item, "position", None)
                if isinstance(pos, Vector2):
                    size = getattr(item, "_box_size", int(0.4 * 1_000_000))
                    half = size // 2
                    box = MagicMock()
                    box.pos = Vector2.from_xy(pos.x - half, pos.y - half)
                    box.size = Vector2.from_xy(size, size)
                    out.append(box)
                else:
                    out.append(None)
            return out

        adapter = MagicMock()
        adapter.get_selected_items.return_value = [fp, track]
        adapter.get_field_value.side_effect = (
            lambda item, name: getattr(item, "_role", None))
        adapter.get_footprint_pads.side_effect = lambda item: list(getattr(item, "_pads", []))
        adapter.get_bounding_boxes.side_effect = _bboxes
        return adapter

    @staticmethod
    def _planning_adapter():
        """A mock board where the loaded cell's role R1 resolves by net."""
        from kicadstamp.domain.board import Footprint

        fp = Footprint(ref="C1", uuid="uuid-C1",
                       position=Vector2.from_xy_mm(105.0, 205.0), angle_deg=0.0,
                       layer=BoardLayer.BL_F_Cu)
        fp._role = "R1"
        fp._cluster = None
        fp._nets = ["NET1"]
        pad = MagicMock()
        pad.net_name = "NET1"
        pad.number = "1"

        adapter = MagicMock()
        adapter.get_footprints.return_value = [fp]
        adapter.get_field_value.side_effect = (
            lambda item, name: getattr(item, "_role", None))
        adapter.get_footprint_pads.return_value = [pad]
        adapter.get_selected_items.return_value = []
        return adapter

    def test_inner_layer_track_survives_the_whole_chain(self, tmp_path):
        from kicadstamp.template_extraction import extract_template_from_selection
        from kicadstamp.config import ClonePlacement
        from kicadstamp.placement.services.clone_position_calculator import (
            ClonePositionCalculator)

        cell = extract_template_from_selection(
            self._extraction_adapter(), "chain_cell",
            raw_selection=True)["chain_cell"]
        # 1) the extractor writes the REAL copper layer, not F.Cu:
        assert cell["tracks"][0]["layer"] == INNER

        # 2) the file it wrote loads (Э1.b restored exactly this):
        path = _write(tmp_path, "chain.sexp",
                      {"layer": "F.Cu", "cells": {"chain_cell": cell}})
        cfg, _ctx = load_config(str(path))
        assert cfg.cells["chain_cell"].tracks[0].layer == INNER

        # 3) planning keeps it on that layer (Э2 restores exactly this):
        clone = ClonePlacement(cluster="chain", cell="chain_cell", xy=(100.0, 200.0),
                               nets={"R1": "NET1"})
        calc = ClonePositionCalculator(self._planning_adapter(), cfg)
        _placed, _vias, tracks = calc.compute_raw_positions([clone])
        assert len(tracks) == 1
        assert tracks[0].layer is BoardLayer.BL_In1_Cu
