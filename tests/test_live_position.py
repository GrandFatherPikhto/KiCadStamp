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


class TestReadCellAnchorOffsetLive:
    """(ax_mm, ay_mm) of one pad in the cell's OWN local (unrotated,
    unmirrored) frame — the numeric-regression guard (design 2026-09-04_
    cell_internal_anchor §2.2): the exact same risk as the instantiate
    absolute-mode test — a silently wrong sign or an un-inverted rotation/
    mirror would land the rebase anchor somewhere else entirely.

    Setup: CAP_IN sits at the cell's local (0,0) (so the recovered cell
    origin == its live position regardless of rotation/mirror), CAP_OUT at
    local (2,1). We rebase onto CAP_OUT's pad 'A1', whose cell-local offset
    is (3, 1) — the pad's world position is hand-placed from that local
    offset via the FORWARD geometry, and the reader must invert it back."""

    def _cell(self):
        return Cell(name="fpga_flash", components=[
            TemplateComponentSlot(role="CAP_IN", offset_along_mm=0.0,
                                  offset_across_mm=0.0, angle_deg=0.0),
            TemplateComponentSlot(role="CAP_OUT", offset_along_mm=2.0,
                                  offset_across_mm=1.0, angle_deg=180.0),
        ])

    def _run(self, monkeypatch, cap_in_angle, mirror, pad_world_mm):
        import gui.docks.live_position as lp
        cfg = MagicMock()
        cfg.cells = {"fpga_flash": self._cell()}
        c10 = _make_fp("C10", role="CAP_IN", cluster="FPGA_FLASH",
                       position=Vector2.from_xy_mm(10.0, 20.0), angle=cap_in_angle)
        c11 = _make_fp("C11", role="CAP_OUT", cluster="FPGA_FLASH")
        clone = ClonePlacement(cluster="FPGA_FLASH", cell="fpga_flash",
                               xy=(10.0, 20.0), mirror=mirror)
        adapter = MagicMock()
        adapter.get_footprint.side_effect = {c10.ref: c10, c11.ref: c11}.get
        px, py = pad_world_mm
        adapter.get_pad_by_number.side_effect = lambda fp, num: _StubPad(px, py)
        monkeypatch.setattr(lp, "clone_uses_selection_mode", lambda *a, **k: False)
        monkeypatch.setattr(lp, "resolve_roles_by_nets",
                            lambda *a, **k: {"CAP_IN": "C10", "CAP_OUT": "C11"})
        return lp.read_cell_anchor_offset_live(adapter, cfg, clone, {}, "CAP_OUT", "A1")

    def test_unrotated_returns_cell_local_pad_offset(self, monkeypatch):
        # Rotation 0, no mirror: origin == C10 at (10, 20); pad at local (3,1)
        # -> world (13, 21).
        ax, ay = self._run(monkeypatch, cap_in_angle=0.0, mirror=False,
                           pad_world_mm=(13.0, 21.0))
        assert ax == pytest.approx(3.0, abs=1e-6)
        assert ay == pytest.approx(1.0, abs=1e-6)

    def test_rotation_90_is_inverted_back(self, monkeypatch):
        # Placement rotation 90 (CAP_IN angle 90, local (0,0) -> origin stays
        # (10,20)). Forward: world pad = origin + R90(3,1) = (11, 17) —
        # R90(3,1) = (1,-3) (real kipy Vector2.rotate convention).
        ax, ay = self._run(monkeypatch, cap_in_angle=90.0, mirror=False,
                           pad_world_mm=(11.0, 17.0))
        assert ax == pytest.approx(3.0, abs=1e-6)
        assert ay == pytest.approx(1.0, abs=1e-6)

    def test_mirrored_clone_is_unmirrored_back(self, monkeypatch):
        # Mirror, rotation 0 (CAP_IN angle must be 180 under mirror for a
        # 0-rotation placement). Forward world pad = mirror_x(origin, origin +
        # (3,1)) about x=10 -> (7, 21); the reader must report the UNMIRRORED
        # local (3, 1) — the frame rebase_cell_anchor actually shifts.
        ax, ay = self._run(monkeypatch, cap_in_angle=180.0, mirror=True,
                           pad_world_mm=(7.0, 21.0))
        assert ax == pytest.approx(3.0, abs=1e-6)
        assert ay == pytest.approx(1.0, abs=1e-6)

    def test_unresolved_role_is_fatal(self, monkeypatch):
        import gui.docks.live_position as lp
        cfg = MagicMock()
        cfg.cells = {"fpga_flash": self._cell()}
        c10 = _make_fp("C10", role="CAP_IN", cluster="FPGA_FLASH",
                       position=Vector2.from_xy_mm(10.0, 20.0))
        c11 = _make_fp("C11", role="CAP_OUT", cluster="FPGA_FLASH")
        adapter = MagicMock()
        adapter.get_footprint.side_effect = {c10.ref: c10, c11.ref: c11}.get
        clone = ClonePlacement(cluster="FPGA_FLASH", cell="fpga_flash",
                               xy=(10.0, 20.0))
        monkeypatch.setattr(lp, "clone_uses_selection_mode", lambda *a, **k: False)
        monkeypatch.setattr(lp, "resolve_roles_by_nets",
                            lambda *a, **k: {"CAP_IN": "C10"})  # CAP_OUT missing
        with pytest.raises(ValidationError, match="role 'CAP_OUT'.*not on the live board"):
            lp.read_cell_anchor_offset_live(adapter, cfg, clone, {}, "CAP_OUT", "A1")

    def test_missing_pad_is_fatal(self, monkeypatch):
        import gui.docks.live_position as lp
        cfg = MagicMock()
        cfg.cells = {"fpga_flash": self._cell()}
        c10 = _make_fp("C10", role="CAP_IN", cluster="FPGA_FLASH",
                       position=Vector2.from_xy_mm(10.0, 20.0))
        c11 = _make_fp("C11", role="CAP_OUT", cluster="FPGA_FLASH")
        adapter = MagicMock()
        adapter.get_footprint.side_effect = {c10.ref: c10, c11.ref: c11}.get
        adapter.get_pad_by_number.return_value = None  # no such pad
        clone = ClonePlacement(cluster="FPGA_FLASH", cell="fpga_flash",
                               xy=(10.0, 20.0))
        monkeypatch.setattr(lp, "clone_uses_selection_mode", lambda *a, **k: False)
        monkeypatch.setattr(lp, "resolve_roles_by_nets",
                            lambda *a, **k: {"CAP_IN": "C10", "CAP_OUT": "C11"})
        with pytest.raises(ValidationError, match="has no pad"):
            lp.read_cell_anchor_offset_live(adapter, cfg, clone, {}, "CAP_OUT", "A1")
