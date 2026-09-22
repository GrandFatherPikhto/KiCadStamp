#!/usr/bin/env python3
"""Unit tests for gui/docks/live_position.py — the shared "read the current
live position of a record's referent" resolvers behind the Config Tree forms'
"Read current position" buttons (design
2026_08_29_config_tree_read_live_position.md). Pure fake-adapter tests, no
Qt, no live board."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock

from kicadstamp.config import Cell, ClonePlacement, TemplateComponentSlot
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.explore import Selected

from gui.docks.live_position import (
    read_anchor_live,
    read_clone_origin_live, read_coordinate_live,
)

MM = 1_000_000


def _make_fp(ref, role=None, nets=None, cluster=None, position=None, angle=0.0):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=position or Vector2.from_xy(0, 0),
                   angle_deg=angle, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._nets = nets or []
    fp._cluster = cluster
    return fp


def _role_or_cluster(fp, field_name):
    if field_name == ROLE_FIELD_NAME:
        return fp._role
    if field_name == CLUSTER_FIELD_NAME:
        return fp._cluster
    return None


def _get_pads(fp):
    """Pads get sequential numbers 1..N with net_name from fp._nets."""
    pads = []
    for i, net in enumerate(fp._nets, start=1):
        p = MagicMock()
        p.number = str(i)
        p.net_name = net
        p.position = Vector2.from_xy(i * MM, 0)
        pads.append(p)
    return pads


def _get_pad_by_number(fp, num):
    return next((p for p in _get_pads(fp) if p.number == str(num)), None)


class _StubPad:
    """A pad with a hand-chosen absolute world position (nm), for the anchor
    offset live tests — real pad geometry is irrelevant to the reader."""
    def __init__(self, x_mm, y_mm):
        self.position = Vector2.from_xy_mm(x_mm, y_mm)


def _adapter(fps):
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_field_value.side_effect = _role_or_cluster
    adapter.get_footprint_pads.side_effect = _get_pads
    adapter.get_pad_by_number.side_effect = _get_pad_by_number
    adapter.get_selected_items.return_value = []
    adapter.get_footprint.side_effect = {fp.ref: fp for fp in fps}.get
    return adapter


class TestReadCoordinateLive:
    def test_single_cluster_role_reads_position_and_angle(self):
        fp = _make_fp("R1", role="R_CLK", cluster="FPGA_FLASH",
                      position=Vector2.from_xy(int(12.5 * MM), int(-7.0 * MM)),
                      angle=90.0)
        adapter = _adapter([fp])
        read = read_coordinate_live(adapter, "FPGA_FLASH", "R_CLK", None, {}, "R_CLK")
        assert read.position == fp.position
        assert read.rotation_deg == 90.0
        assert read.footprint is fp

    def test_ambiguous_cluster_role_is_fatal(self):
        adapter = _adapter([
            _make_fp("R1", role="R_CLK", cluster="FPGA_FLASH"),
            _make_fp("R2", role="R_CLK", cluster="FPGA_FLASH"),
        ])
        with pytest.raises(ValidationError, match="R_CLK"):
            read_coordinate_live(adapter, "FPGA_FLASH", "R_CLK", None, {}, "R_CLK")

    def test_no_match_is_fatal(self):
        adapter = _adapter([_make_fp("R1", role="OTHER", cluster="C")])
        with pytest.raises(ValidationError, match="R_CLK"):
            read_coordinate_live(adapter, "FPGA_FLASH", "R_CLK", None, {}, "R_CLK")


class TestReadAnchorLive:
    def test_ref_anchor_reads_position_and_angle(self):
        fp = _make_fp("U3", role="FPGA", cluster="FPGA",
                      position=Vector2.from_xy(int(1.0 * MM), int(2.0 * MM)),
                      angle=45.0)
        adapter = _adapter([fp])
        read = read_anchor_live(adapter, {"mode": "anchor", "ref": "U3"}, {}, {}, "label")
        assert read.position == fp.position
        assert read.rotation_deg == 45.0

    def test_role_anchor_reads_position(self):
        fp = _make_fp("U3", role="FPGA", cluster="FPGA",
                      position=Vector2.from_xy(int(3.0 * MM), int(4.0 * MM)))
        adapter = _adapter([fp])
        read = read_anchor_live(adapter, {"mode": "anchor", "role": "FPGA"}, {}, {}, "label")
        assert read.position == fp.position
        assert read.rotation_deg == 0.0

    def test_pad_anchor_reads_pad_position(self):
        fp = _make_fp("U3", role="FPGA", cluster="FPGA", nets=["GND", "+3V3"])
        adapter = _adapter([fp])
        # _get_pads gives pad '2' position (2mm, 0).
        read = read_anchor_live(adapter, {"mode": "anchor", "ref": "U3", "pad": "2"}, {}, {}, "label")
        assert read.position == Vector2.from_xy(2 * MM, 0)

    def test_point_anchor_has_no_rotation(self, monkeypatch):
        import gui.docks.live_position as lp
        fp = _make_fp("CONN", role="CONN", cluster="C",
                      position=Vector2.from_xy(int(9.0 * MM), int(8.0 * MM)))
        adapter = _adapter([fp])

        class _Resolved:
            position = fp.position
            footprint = fp

        monkeypatch.setattr(lp, "resolve_point_chain",
                            lambda *a, **k: _Resolved())
        read = read_anchor_live(adapter, {"mode": "point", "point": "Origin"}, {}, {}, "label")
        assert read.position == fp.position
        assert read.rotation_deg is None
        assert read.footprint is fp

    def test_missing_ref_is_fatal(self):
        adapter = _adapter([])
        with pytest.raises(ValidationError, match="U3"):
            read_anchor_live(adapter, {"mode": "anchor", "ref": "U3"}, {}, {}, "label")


class TestReadCloneOriginLive:
    def _cell(self, anchor_role=None, first_offset=(0.0, 0.0)):
        return Cell(
            name="fpga_flash",
            anchor_role=anchor_role,
            components=[
                TemplateComponentSlot(role="CAP_IN",
                                      offset_along_mm=first_offset[0],
                                      offset_across_mm=first_offset[1],
                                      angle_deg=0.0),
                TemplateComponentSlot(role="CAP_OUT", offset_along_mm=2.0,
                                      offset_across_mm=0.0, angle_deg=180.0),
            ],
        )

    def _clone(self, **kw):
        base = dict(cluster="FPGA_FLASH", cell="fpga_flash", xy=(10.0, 20.0))
        base.update(kw)
        return ClonePlacement(**base)

    def test_origin_recovered_from_first_component(self, monkeypatch):
        """CAP_IN at local (0,0), rotation 0 -> the cell origin IS the live
        component's position."""
        import gui.docks.live_position as lp
        cfg = MagicMock()
        cfg.cells = {"fpga_flash": self._cell()}
        fp = _make_fp("C10", role="CAP_IN", cluster="FPGA_FLASH",
                      position=Vector2.from_xy(int(10.0 * MM), int(20.0 * MM)))
        adapter = _adapter([fp])
        clone = self._clone()
        monkeypatch.setattr(lp, "clone_uses_selection_mode", lambda *a, **k: False)
        monkeypatch.setattr(lp, "resolve_roles_by_nets",
                            lambda *a, **k: {"CAP_IN": "C10", "CAP_OUT": "C11"})

        read = read_clone_origin_live(adapter, cfg, clone, {})
        assert read.position == fp.position
        assert read.rotation_deg == 0.0

    def test_origin_recovered_with_offset(self, monkeypatch):
        """CAP_IN at local (1,0), placement rotation 0 (identity rotation, so
        the offset math is convention-independent): component world =
        origin + (1mm, 0). A live component at (11mm, 20mm) means origin
        (10mm, 20mm)."""
        import gui.docks.live_position as lp
        cfg = MagicMock()
        cfg.cells = {"fpga_flash": self._cell(first_offset=(1.0, 0.0))}
        fp = _make_fp("C10", role="CAP_IN", cluster="FPGA_FLASH",
                      position=Vector2.from_xy(int(11.0 * MM), int(20.0 * MM)))
        adapter = _adapter([fp])
        clone = self._clone()
        monkeypatch.setattr(lp, "clone_uses_selection_mode", lambda *a, **k: False)
        monkeypatch.setattr(lp, "resolve_roles_by_nets",
                            lambda *a, **k: {"CAP_IN": "C10", "CAP_OUT": "C11"})

        read = read_clone_origin_live(adapter, cfg, clone, {})
        assert read.position == Vector2.from_xy(int(10.0 * MM), int(20.0 * MM))
        assert read.rotation_deg == 0.0

    def test_rotation_recovered_from_component(self, monkeypatch):
        """CAP_IN at local (0,0) (so the origin equals the live position
        regardless of the rotation convention), placement rotation 90: the
        placement's rotation is read from the component's angle."""
        import gui.docks.live_position as lp
        cfg = MagicMock()
        cfg.cells = {"fpga_flash": self._cell(first_offset=(0.0, 0.0))}
        fp = _make_fp("C10", role="CAP_IN", cluster="FPGA_FLASH",
                      position=Vector2.from_xy(int(10.0 * MM), int(20.0 * MM)),
                      angle=90.0)
        adapter = _adapter([fp])
        clone = self._clone(rotation_deg=90.0)
        monkeypatch.setattr(lp, "clone_uses_selection_mode", lambda *a, **k: False)
        monkeypatch.setattr(lp, "resolve_roles_by_nets",
                            lambda *a, **k: {"CAP_IN": "C10", "CAP_OUT": "C11"})

        read = read_clone_origin_live(adapter, cfg, clone, {})
        assert read.position == fp.position
        assert read.rotation_deg == 90.0

    def test_anchor_role_component_is_the_reference(self, monkeypatch):
        """cell.anchor_role names the MOUNT component (design_2026_09_05 v2) —
        that slot wins over the first slot even when its stored offset is
        nonzero, and the mount (what a placement pins) IS that component's live
        position, not the stored (0,0)."""
        import gui.docks.live_position as lp
        cfg = MagicMock()
        cfg.cells = {"fpga_flash": self._cell(anchor_role="CAP_OUT")}
        # CAP_OUT at stored offset (2,0) -> A=(2,0); the mount is CAP_OUT, so
        # the read returns CAP_OUT's own world position (12,20), not (10,20).
        fp = _make_fp("C11", role="CAP_OUT", cluster="FPGA_FLASH",
                      position=Vector2.from_xy(int(12.0 * MM), int(20.0 * MM)),
                      angle=180.0)
        adapter = _adapter([fp])
        clone = self._clone()
        monkeypatch.setattr(lp, "clone_uses_selection_mode", lambda *a, **k: False)
        monkeypatch.setattr(lp, "resolve_roles_by_nets",
                            lambda *a, **k: {"CAP_IN": "C10", "CAP_OUT": "C11"})

        read = read_clone_origin_live(adapter, cfg, clone, {})
        assert read.position == Vector2.from_xy(int(12.0 * MM), int(20.0 * MM))
        assert read.rotation_deg == 0.0

    def test_unreachable_cell_is_fatal(self):
        cfg = MagicMock()
        cfg.cells = {}
        clone = self._clone()
        with pytest.raises(ValidationError, match="fpga_flash"):
            read_clone_origin_live(_adapter([]), cfg, clone, {})

    def test_no_resolved_component_is_fatal(self, monkeypatch):
        import gui.docks.live_position as lp
        cfg = MagicMock()
        cfg.cells = {"fpga_flash": self._cell()}
        adapter = _adapter([])
        clone = self._clone()
        monkeypatch.setattr(lp, "clone_uses_selection_mode", lambda *a, **k: False)
        monkeypatch.setattr(lp, "resolve_roles_by_nets", lambda *a, **k: {})
        with pytest.raises(ValidationError, match="no component resolved"):
            read_clone_origin_live(adapter, cfg, clone, {})

    def test_resolved_ref_missing_on_board_is_fatal(self, monkeypatch):
        import gui.docks.live_position as lp
        cfg = MagicMock()
        cfg.cells = {"fpga_flash": self._cell()}
        adapter = _adapter([])  # no C10 on the board
        clone = self._clone()
        monkeypatch.setattr(lp, "clone_uses_selection_mode", lambda *a, **k: False)
        monkeypatch.setattr(lp, "resolve_roles_by_nets",
                            lambda *a, **k: {"CAP_IN": "C10", "CAP_OUT": "C11"})
        with pytest.raises(ValidationError, match="not on the live board"):
            read_clone_origin_live(adapter, cfg, clone, {})

    def test_pad_anchor_uses_live_resolved_mount(self, monkeypatch):
        """§A.7 (plan_2026_09_09_cell_anchor_v2): a pad-anchor cell
        (anchor_role+anchor_pad, NO anchor_xy) — read_clone_origin_live MUST
        invert apply_clone_geometry with the SAME live-resolved mount. Forward:
        origin=(10,20), A=(-3.05,-1.295) (the pad's bbox-local point) -> MOUNT
        at origin - A = (13.05,21.295), its pad at the origin (10,20). The
        inverse must recover (10,20); the legacy cell_mount_offset (0,0) would
        wrongly return the fp's own position (13.05,21.295)."""
        import gui.docks.live_position as lp
        cell = Cell(name="padcell", anchor_role="MOUNT", anchor_pad="1",
                    components=[
                        TemplateComponentSlot(role="MOUNT", offset_along_mm=0.0,
                                              offset_across_mm=0.0, angle_deg=0.0),
                        TemplateComponentSlot(role="CAP", offset_along_mm=4.0,
                                              offset_across_mm=0.0, angle_deg=0.0),
                    ])
        cfg = MagicMock()
        cfg.cells = {"padcell": cell}
        mount = _make_fp("C10", role="MOUNT", cluster="PAD",
                         position=Vector2.from_xy_mm(13.05, 21.295))
        cap = _make_fp("C11", role="CAP", cluster="PAD",
                       position=Vector2.from_xy_mm(14.0, 20.0))
        adapter = _adapter([mount, cap])

        def _pad_by_num(fp, num):
            if fp.ref == "C10" and str(num) == "1":
                return _StubPad(10.0, 20.0)  # the mount = the clone origin
            return _get_pad_by_number(fp, num)
        adapter.get_pad_by_number.side_effect = _pad_by_num
        clone = ClonePlacement(cluster="PAD", cell="padcell", xy=(10.0, 20.0))
        monkeypatch.setattr(lp, "clone_uses_selection_mode", lambda *a, **k: False)
        monkeypatch.setattr(lp, "resolve_roles_by_nets",
                            lambda *a, **k: {"MOUNT": "C10", "CAP": "C11"})

        read = read_clone_origin_live(adapter, cfg, clone, {})
        assert read.position == Vector2.from_xy_mm(10.0, 20.0)
        assert read.rotation_deg == 0.0
        assert read.footprint is mount


# ── Ш1 (plan_2026_09_22_board_door_finish): identity from the polled snapshot ──
# The property: with `snapshot=` given, read_coordinate_live() answers the
# IDENTITY question from that already-read list — no get_footprints(), no
# get_field_value() — and takes the POSITION from the adapter's CURRENT
# generation, never from the snapshot's cached footprint (whose position is as
# old as the poll that built the list). The cells (rule 35): identity without a
# sweep, position from the live generation, no match, ambiguity, sheet
# narrowing, and the ref gone from the live board. The default (snapshot=None)
# keeps the historical adapter sweep — TestReadCoordinateLive above pins it.

def _selected(fp, role, cluster):
    """One snapshot row (explore.Selected) — the shape connection.snapshot has."""
    return Selected(ref=fp.ref, role=role, cluster=cluster, sheet=[], nets={}, fp=fp)


def _snapshot_only_adapter(live_fps):
    """An adapter that answers `get_footprint` and DIES on either call the
    snapshot branch must not make — a whole-board sweep or a field scan then
    fails loudly instead of passing unnoticed."""
    adapter = _adapter(live_fps)

    def _sweep(*a, **k):
        raise AssertionError("get_footprints() was called: the board was swept")
    adapter.get_footprints.side_effect = _sweep

    def _field(*a, **k):
        raise AssertionError("get_field_value() was called: a field was scanned")
    adapter.get_field_value.side_effect = _field
    return adapter


class TestReadCoordinateLiveIdentifiesFromTheSnapshot:
    def test_the_snapshot_answers_identity_without_a_board_sweep(self):
        """Cell 1 — the row in the snapshot names the footprint, so neither
        get_footprints() nor get_field_value() is reached (both raise here)."""
        live = _make_fp("R1", position=Vector2.from_xy_mm(5.0, 6.0), angle=30.0)
        stale = _make_fp("R1", position=Vector2.from_xy_mm(1.0, 2.0))
        adapter = _snapshot_only_adapter([live])

        read = read_coordinate_live(adapter, "FPGA_FLASH", "R_CLK", None, {}, "R_CLK",
                                    snapshot=[_selected(stale, "R_CLK", "FPGA_FLASH")])

        assert read.footprint is live

    def test_the_position_comes_from_the_live_board_not_from_the_snapshot(self):
        """Cell 2 — the snapshot row carries its own (stale) footprint object;
        the read must say where the component IS, i.e. return the adapter's
        current object, never the snapshot's cached one."""
        live = _make_fp("R1", position=Vector2.from_xy_mm(5.0, 6.0), angle=30.0)
        stale = _make_fp("R1", position=Vector2.from_xy_mm(1.0, 2.0), angle=0.0)
        adapter = _snapshot_only_adapter([live])

        read = read_coordinate_live(adapter, "FPGA_FLASH", "R_CLK", None, {}, "R_CLK",
                                    snapshot=[_selected(stale, "R_CLK", "FPGA_FLASH")])

        assert read.position == Vector2.from_xy_mm(5.0, 6.0)
        assert read.rotation_deg == 30.0

    def test_a_snapshot_with_no_matching_row_is_fatal(self):
        """Cell 3 — the same canonical none-match fatal the adapter sweep gives."""
        other = _make_fp("R9")
        adapter = _snapshot_only_adapter([other])

        with pytest.raises(ValidationError, match="no component tagged"):
            read_coordinate_live(adapter, "FPGA_FLASH", "R_CLK", None, {}, "R_CLK",
                                 snapshot=[_selected(other, "OTHER", "ELSEWHERE")])

    def test_an_ambiguous_snapshot_pair_is_fatal(self):
        """Cell 4 — two rows sharing the exact (Role, Cluster) pair: the same
        fatal-if-not-unique check; the first is never simply taken."""
        first = _make_fp("R1")
        second = _make_fp("R2")
        adapter = _snapshot_only_adapter([first, second])
        snapshot = [_selected(first, "R_CLK", "FPGA_FLASH"),
                    _selected(second, "R_CLK", "FPGA_FLASH")]

        with pytest.raises(ValidationError, match="expected exactly one"):
            read_coordinate_live(adapter, "FPGA_FLASH", "R_CLK", None, {}, "R_CLK",
                                 snapshot=snapshot)

    def test_the_snapshot_pair_is_narrowed_by_sheet(self):
        """Cell 5 — two rows on two instances of a reused sheet: the sheet
        narrows to the one the form asked for, exactly as on the adapter path."""
        ch0 = _make_fp("R1")
        ch0.sheet_path_uuids = ("sheet-0", "own-0")
        ch1 = _make_fp("R2")
        ch1.sheet_path_uuids = ("sheet-1", "own-1")
        adapter = _snapshot_only_adapter([ch0, ch1])
        snapshot = [_selected(ch0, "R_CLK", "FPGA_FLASH"),
                    _selected(ch1, "R_CLK", "FPGA_FLASH")]

        read = read_coordinate_live(
            adapter, "FPGA_FLASH", "R_CLK", "Channel_1",
            {"sheet-0": "Channel_0", "sheet-1": "Channel_1"}, "R_CLK",
            snapshot=snapshot)

        assert read.footprint.ref == "R2"

    def test_a_snapshot_ref_that_left_the_board_is_fatal_not_stale(self):
        """Cell 6 — the row names a ref the live board no longer has (deleted or
        renamed since the poll): fatal, and never the snapshot's own object —
        the point of the cell is that a vanished footprint must not be answered
        with the position it had when the list was built."""
        stale = _make_fp("R1", position=Vector2.from_xy_mm(1.0, 2.0))
        adapter = _snapshot_only_adapter([])          # no R1 on the live board

        with pytest.raises(ValidationError, match="no component tagged"):
            read_coordinate_live(adapter, "FPGA_FLASH", "R_CLK", None, {}, "R_CLK",
                                 snapshot=[_selected(stale, "R_CLK", "FPGA_FLASH")])

