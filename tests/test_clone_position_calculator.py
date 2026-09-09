#!/usr/bin/env python3
"""Plan 2026-09-07 (handoff absolute_clone_geometric_role_narrowing):
geometric role narrowing (resolve_roles_by_nets step 5 — physical proximity
to the anchor of THIS clone_placement, role_narrowing.py) was structurally
unreachable for Entity/tree_instances-materialized clones and any
absolute-coordinate ClonePlacement: _resolve_anchor returns None for them
(no anchor_ref/anchor_role/anchor_point), so resolve_roles_by_nets got
anchor_position=None and step 5 never fired — even though the clone's own
world position is already knowable from its xy.

Fix: _resolve_one_level computes a SEPARATE `role_narrowing_anchor` fallback
via clone_layout_origin (the exact function that will later place this
clone's origin) and passes it to resolve_roles_by_selection/
resolve_roles_by_nets ONLY. The real anchor_position used by
apply_clone_geometry is deliberately left untouched — feeding the fallback
back into geometry would double-apply clone.xy's shift (§0 trap).

These tests drive ClonePositionCalculator.compute_raw_positions end to end
(the path _resolve_anchor -> _resolve_one_level -> resolve_roles_by_nets ->
apply_clone_geometry) on a fake live board.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock

from kicadstamp.config import Config, ClonePlacement, Cell, TemplateComponentSlot
from kicadstamp.constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME
from kicadstamp.domain.geometry import Vector2, BoardLayer
from kicadstamp.domain.board import Footprint
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.clone_position_calculator import ClonePositionCalculator
from kicadstamp.placement.services.clone_role_resolver import resolve_roles_by_nets
from kicadstamp.placement.services.point_resolver import ResolvedPoint

MM = 1_000_000


def _make_fp(ref, role, nets, cluster=None, x_mm=0.0, y_mm=0.0):
    """A live-board footprint in domain units: ref/role/cluster/nets/position."""
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy(int(x_mm * MM), int(y_mm * MM)),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._cluster = cluster
    fp._nets = list(nets)
    return fp


def _field(fp, name):
    if name == ROLE_FIELD_NAME:
        return fp._role
    if name == CLUSTER_FIELD_NAME:
        return fp._cluster
    return None


def _pads(fp):
    """Sequential pad numbers 1..N; net i -> pad str(i) (same convention as
    test_clone_role_resolver.py)."""
    out = []
    for i, n in enumerate(fp._nets, start=1):
        p = MagicMock()
        p.number = str(i)
        p.net_name = n
        out.append(p)
    return out


def _adapter(fps):
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_field_value.side_effect = lambda fp, name: _field(fp, name)
    adapter.get_footprint_pads.side_effect = _pads
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in _pads(fp) if p.number == str(num)), None)
    adapter.get_selected_items.return_value = []
    return adapter


def _two_role_shared_net_cell():
    """A cell with TWO roles on the SAME shared power net, but at DIFFERENT
    local offsets from the clone origin (mirrors C_IN_BYPASS at (0,0) and
    C_IN_BULK at (10,0) inside one pif_* filter block)."""
    return Cell(name="filter_block", components=[
        TemplateComponentSlot(role="C_IN_BYPASS", offset_along_mm=0.0, offset_across_mm=0.0),
        TemplateComponentSlot(role="C_IN_BULK", offset_along_mm=10.0, offset_across_mm=0.0),
    ])


def _two_blocks_board():
    """Two physical filter blocks, all four parts on the shared +3V3 rail.
    Block A (PIF_AVDD) sits at (0,0); block B (PIF_DVDD) at (100,200) — the
    block the absolute clone below targets. Per role the two candidates differ
    ONLY by position (same net, no sheet/cluster signal that would disambiguate
    earlier), so step 5 proximity narrowing is the deciding signal."""
    return [
        _make_fp("C_A_BYPASS", "C_IN_BYPASS", ["+3V3"], cluster="PIF_AVDD",
                 x_mm=0.0, y_mm=0.0),
        _make_fp("C_A_BULK", "C_IN_BULK", ["+3V3"], cluster="PIF_AVDD",
                 x_mm=10.0, y_mm=0.0),
        _make_fp("C_B_BYPASS", "C_IN_BYPASS", ["+3V3"], cluster="PIF_DVDD",
                 x_mm=100.0, y_mm=200.0),
        _make_fp("C_B_BULK", "C_IN_BULK", ["+3V3"], cluster="PIF_DVDD",
                 x_mm=110.0, y_mm=200.0),
    ]


def _absolute_clone():
    """An ABSOLUTE-coordinate top-level ClonePlacement — the Entity/
    tree_instances shape: xy set, NO anchor_ref/anchor_role/anchor_point, so
    _resolve_anchor returns None (before this plan that meant resolve_roles_
    by_nets got anchor_position=None and step 5 never fired)."""
    return ClonePlacement(cluster="ch1_dac_buf", cell="filter_block",
                          xy=(100.0, 200.0),
                          nets={"C_IN_BYPASS": "+3V3", "C_IN_BULK": "+3V3"})


class TestAbsoluteCloneGeometricRoleNarrowing:
    def test_two_same_net_roles_resolve_by_proximity(self):
        """THE live scenario of the plan: an absolute clone whose two roles
        share one net (+3V3) and whose physical candidates differ only by
        position. Before the fix resolve_roles_by_nets got anchor_position=None
        and fatally failed on the ambiguity; now the clone's own world origin
        (clone.xy) reaches step 5 and each role resolves to its NEAREST
        physical candidate (block B = the target of xy)."""
        adapter = _adapter(_two_blocks_board())
        cell = _two_role_shared_net_cell()
        clone = _absolute_clone()
        calc = ClonePositionCalculator(adapter, Config(layer="F.Cu", cells={cell.name: cell}))

        placed, _vias, _tracks = calc.compute_raw_positions([clone])

        # Both roles of block B (the target), never block A's parts.
        by_dest = {p.dest: p.ref for p in placed}
        assert by_dest[Vector2.from_xy(int(100.0 * MM), int(200.0 * MM))] == "C_B_BYPASS"
        assert by_dest[Vector2.from_xy(int(110.0 * MM), int(200.0 * MM))] == "C_B_BULK"

    def test_negative_control_anchor_none_still_fatal(self):
        """Documents WHY the fallback matters: the same board/cell with
        anchor_position=None (the structurally-None value BEFORE this plan)
        leaves BOTH roles ambiguous and fatals — proving the end-to-end test
        above genuinely depends on the new role_narrowing_anchor (no earlier
        cascade step happens to narrow these candidates)."""
        adapter = _adapter(_two_blocks_board())
        clone = _absolute_clone()
        with pytest.raises(ValidationError, match="ambiguity"):
            resolve_roles_by_nets(adapter, _two_role_shared_net_cell(), clone,
                                  anchor_position=None)

    def test_insufficient_proximity_gap_still_fatal(self):
        """Regression: the 2x-gap guard of step 5 must keep refusing to guess.
        With the fallback ACTIVE, two candidates EQUIDISTANT from the computed
        role_narrowing_anchor stay ambiguous and raise the same fatal as today
        — the new code enables an existing, honest disambiguator; it does not
        turn a coin toss into a guess."""
        cell = Cell(name="one_role", components=[
            TemplateComponentSlot(role="C_IN_BULK"),
        ])
        fps = [
            _make_fp("C_L", "C_IN_BULK", ["+3V3"], x_mm=90.0, y_mm=200.0),
            _make_fp("C_R", "C_IN_BULK", ["+3V3"], x_mm=110.0, y_mm=200.0),
        ]
        adapter = _adapter(fps)
        clone = ClonePlacement(cluster="block_x", cell="one_role", xy=(100.0, 200.0),
                               nets={"C_IN_BULK": "+3V3"})
        calc = ClonePositionCalculator(adapter, Config(layer="F.Cu", cells={cell.name: cell}))

        with pytest.raises(ValidationError, match="ambiguity"):
            calc.compute_raw_positions([clone])


class TestGeometryUnchangedRegression:
    def test_absolute_clone_geometry_no_double_shift(self):
        """Regression for the §0 trap: role_narrowing_anchor must NOT leak
        into apply_clone_geometry. The absolute clone's real origin stays
        clone.xy applied ONCE — a double-shift bug would land this component
        at (205, 405) instead of (105, 205)."""
        cell = Cell(name="single", components=[
            TemplateComponentSlot(role="R1", offset_along_mm=5.0, offset_across_mm=5.0),
        ])
        fps = [_make_fp("C1", "R1", ["NET1"], x_mm=105.0, y_mm=205.0)]
        adapter = _adapter(fps)
        clone = ClonePlacement(cluster="abs1", cell="single", xy=(100.0, 200.0),
                               nets={"R1": "NET1"})
        calc = ClonePositionCalculator(adapter, Config(layer="F.Cu", cells={cell.name: cell}))

        placed, _vias, _tracks = calc.compute_raw_positions([clone])

        assert len(placed) == 1
        assert placed[0].ref == "C1"
        assert placed[0].dest.x == int(105.0 * MM)
        assert placed[0].dest.y == int(205.0 * MM)

    def test_anchor_based_clone_unaffected(self):
        """Regression: for a clone WITH an anchor (the ordinary, non-Entity
        case) role_narrowing_anchor takes the `anchor_position is not None`
        branch — i.e. it equals the ORIGINAL anchor_position, so an ambiguity
        resolved by physical proximity uses the true anchor exactly as before
        this plan. Nothing about the anchored path may change."""
        cell = Cell(name="tpl", components=[
            TemplateComponentSlot(role="C_IN_BYPASS"),
        ])
        fps = [
            _make_fp("C_FAR", "C_IN_BYPASS", ["+3V3"], x_mm=0.0, y_mm=0.0),
            _make_fp("C_NEAR", "C_IN_BYPASS", ["+3V3"], x_mm=50.0, y_mm=60.0),
        ]
        adapter = _adapter(fps)
        anchor_fp = _make_fp("ANCHOR", "FPGA", ["OTHER"])
        clone = ClonePlacement(cluster="anchored", cell="tpl", xy=(0.0, 0.0),
                               anchor_point="A",
                               nets={"C_IN_BYPASS": "+3V3"})
        cfg = Config(layer="F.Cu", cells={cell.name: cell}, clone_placements=[clone])
        resolved_points = {
            "A": ResolvedPoint(position=Vector2.from_xy(int(50.0 * MM), int(60.0 * MM)),
                               footprint=anchor_fp),
        }
        calc = ClonePositionCalculator(adapter, cfg, resolved_points=resolved_points)

        placed, _vias, _tracks = calc.compute_raw_positions([clone])

        assert [p.ref for p in placed] == ["C_NEAR"]


class _Pad:
    """A live pad with a REAL absolute position (what get_pad_by_number returns
    on the anchor footprint) — the standard _pads() MagicMocks only carry
    number/net_name, which is not enough for the pad-anchor mount resolution."""

    def __init__(self, num, net, x_mm, y_mm):
        self.number = str(num)
        self.net_name = net
        self.position = Vector2.from_xy_mm(x_mm, y_mm)


class TestPadAnchorDeclarativeMount:
    """Phase A (plan_2026_09_09_cell_anchor_v2 §A.4/§A.5): a pad-anchor cell
    (anchor_role + anchor_pad, NO anchor_xy) has its mount A resolved from the
    LIVE anchor component at apply time and handed to apply_clone_geometry as
    resolved_mount. End-to-end through ClonePositionCalculator.
    compute_raw_positions on a mock board: the placed geometry must match a
    manual recomputation through the pad-as-mount semantics (never the silent
    legacy (0,0) that would shift the content)."""

    def _cell(self, **kw):
        return Cell(name="padcell", components=[
            TemplateComponentSlot(role="MOUNT", offset_along_mm=0.0,
                                  offset_across_mm=0.0, angle_deg=0.0),
            TemplateComponentSlot(role="CAP", offset_along_mm=4.0,
                                  offset_across_mm=0.0, angle_deg=0.0),
        ], **kw)

    def _anchor_fp(self, x_mm, y_mm, angle, back=False, pad_x_mm=None, pad_y_mm=None):
        fp = Footprint(ref="C-OUT", uuid="uuid-C-OUT",
                       position=Vector2.from_xy_mm(x_mm, y_mm),
                       angle_deg=angle,
                       layer=BoardLayer.BL_B_Cu if back else BoardLayer.BL_F_Cu)
        fp._role = "MOUNT"
        fp._cluster = "PAD"
        fp._nets = ["+MOUNT"]
        fp._pad = _Pad("1", "+MOUNT", pad_x_mm or 0.0, pad_y_mm or 0.0)
        return fp

    def _adapter(self, anchor_fp, forbid_pad_lookup=False):
        cap_fp = _make_fp("C-CAP", "CAP", ["+CAP"], cluster="PAD",
                          x_mm=0.0, y_mm=0.0)
        fps = [anchor_fp, cap_fp]
        by_ref = {fp.ref: fp for fp in fps}
        by_pad = {"C-OUT": [p for p in [anchor_fp._pad] if p is not None],
                  "C-CAP": [_Pad("1", "+CAP", 0.0, 0.0)]}

        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = _field
        adapter.get_footprint.side_effect = by_ref.get
        adapter.get_footprint_pads.side_effect = lambda fp: by_pad.get(fp.ref, [])
        if forbid_pad_lookup:
            def _no_pad_lookup(*_a, **_k):
                raise AssertionError("GUARD 1: live pad read must not happen "
                                     "when anchor_xy is set")
            adapter.get_pad_by_number.side_effect = _no_pad_lookup
        else:
            adapter.get_pad_by_number.side_effect = (
                lambda fp, num: next((p for p in by_pad.get(fp.ref, [])
                                      if p.number == str(num)), None))
        adapter.get_selected_items.return_value = []
        return adapter

    def _clone(self, cell, **kw):
        return ClonePlacement(cluster="PAD", cell=cell.name, xy=(100.0, 200.0),
                              nets={"MOUNT": "+MOUNT", "CAP": "+CAP"}, **kw)

    def _run(self, anchor_fp, cell):
        adapter = self._adapter(anchor_fp)
        calc = ClonePositionCalculator(
            adapter, Config(layer="F.Cu", cells={cell.name: cell}))
        placed, _vias, _tracks = calc.compute_raw_positions([self._clone(cell)])
        return {p.ref: p.dest for p in placed}

    def test_live_rotated_anchor_pad_becomes_the_mount(self):
        """A live anchor fp UNDER ANGLE (90°) — the resolved A is the pad's
        bbox-local (-3.05,-1.295) (MOUNT stored at (0,0), pad at origin_ref +
        R90(-3.05,-1.295)); apply places content from A so the MOUNT lands at
        origin + (3.05, 1.295), i.e. the pad IS the mount, not legacy (0,0)."""
        anchor = self._anchor_fp(200.0, 100.0, angle=90.0,
                                 pad_x_mm=198.705, pad_y_mm=103.05)
        by_ref = self._run(anchor, self._cell(anchor_role="MOUNT", anchor_pad="1"))
        mount = by_ref["C-OUT"]
        cap = by_ref["C-CAP"]
        # Manual recompute: A = (-3.05,-1.295); rotation_deg 0 ->
        # MOUNT world = origin + (0 - A) = (100+3.05, 200+1.295),
        # CAP world   = origin + ((4,0) - A) = (100+7.05, 200+1.295).
        assert mount.x / MM == pytest.approx(103.05, abs=1e-6)
        assert mount.y / MM == pytest.approx(201.295, abs=1e-6)
        assert cap.x / MM == pytest.approx(107.05, abs=1e-6)
        assert cap.y / MM == pytest.approx(201.295, abs=1e-6)

    def test_legacy_zero_mount_would_misplace(self):
        """Negative control proving the mount is NOT (0,0): if the legacy
        cell_mount_offset (0,0) were used, MOUNT would land at the origin
        (100,200) — the pad-anchor geometry must land it at (103.05, 201.295)
        instead, exactly the offset the pad's live position implies."""
        anchor = self._anchor_fp(200.0, 100.0, angle=90.0,
                                 pad_x_mm=198.705, pad_y_mm=103.05)
        mount = self._run(anchor, self._cell(anchor_role="MOUNT", anchor_pad="1"))["C-OUT"]
        assert mount.x / MM != pytest.approx(100.0, abs=1e-9)
        assert mount.y / MM != pytest.approx(200.0, abs=1e-9)

    def test_anchor_xy_wins_no_live_resolve(self):
        """GUARD 1: anchor_xy present (+anchor_role+anchor_pad, the v2 form the
        2026-09-08/09 commits wrote) — the stored mount wins and NO live pad
        read happens. The anchor fp still resolves by net, but any pad-number
        lookup is FORBIDDEN here (it would fatal "has no pad" if the mount were
        wrongly re-derived live); the content is placed from the STORED
        anchor_xy=(2,3)."""
        cell = self._cell(anchor_role="MOUNT", anchor_pad="1", anchor_xy=(2.0, 3.0))
        anchor = self._anchor_fp(200.0, 100.0, angle=90.0,
                                 pad_x_mm=198.705, pad_y_mm=103.05)
        adapter = self._adapter(anchor, forbid_pad_lookup=True)
        calc = ClonePositionCalculator(
            adapter, Config(layer="F.Cu", cells={cell.name: cell}))
        placed, _vias, _tracks = calc.compute_raw_positions([self._clone(cell)])
        by_ref = {p.ref: p.dest for p in placed}
        mount = by_ref["C-OUT"]
        # A = stored (2,3) -> MOUNT world = origin + (0-2, 0-3) = (98, 197).
        assert mount.x / MM == pytest.approx(98.0, abs=1e-6)
        assert mount.y / MM == pytest.approx(197.0, abs=1e-6)
        assert by_ref["C-CAP"].x / MM == pytest.approx(102.0, abs=1e-6)
        assert by_ref["C-CAP"].y / MM == pytest.approx(197.0, abs=1e-6)
