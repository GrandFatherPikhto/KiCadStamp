#!/usr/bin/env python3
"""Tests for point_resolver.py — resolving a Point to an absolute position
(+ footprint, when eligible). See handoff_2026_07_31_consolidated.md and
config/points.py for the design."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2
from kipy.board_types import Pad, FootprintInstance

from kicadstamp.config import Point
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.point_resolver import (ResolvedPoint, resolve_point,
                                                           resolve_point_chain)

MM = 1_000_000


def _make_pad(number, x_mm, y_mm):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    return pad


def _make_fp(ref, x_mm=0.0, y_mm=0.0, role=None, pads=()):
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp._role = role
    fp._pads = list(pads)
    return fp


def _adapter_for(fps):
    by_ref = {fp.ref: fp for fp in fps}
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_footprint.side_effect = lambda ref: by_ref.get(ref)
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    adapter.get_selected_items.return_value = []
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in getattr(fp, "_pads", []) if p.number == num), None)
    return adapter


class TestResolvePointAnchorRefBase:
    def test_footprint_centre_no_shift(self):
        fp = _make_fp("U5", x_mm=10.0, y_mm=20.0)
        adapter = _adapter_for([fp])
        point = Point(name="p", anchor_ref="U5")

        resolved = resolve_point(adapter, point, resolved_points={})

        assert resolved.position.x == int(10.0 * MM)
        assert resolved.position.y == int(20.0 * MM)
        assert resolved.footprint is fp

    def test_with_shift_moves_position_and_clears_footprint(self):
        fp = _make_fp("U5", x_mm=10.0, y_mm=20.0)
        adapter = _adapter_for([fp])
        point = Point(name="p", anchor_ref="U5", shift_x_mm=2.5, shift_y_mm=-1.0)

        resolved = resolve_point(adapter, point, resolved_points={})

        assert resolved.position.x == int(12.5 * MM)
        assert resolved.position.y == int(19.0 * MM)
        assert resolved.footprint is None

    def test_with_anchor_pad_uses_pad_position_and_no_footprint(self):
        pad = _make_pad("12", x_mm=11.0, y_mm=21.0)
        fp = _make_fp("U5", x_mm=10.0, y_mm=20.0, pads=[pad])
        adapter = _adapter_for([fp])
        point = Point(name="p", anchor_ref="U5", anchor_pad="12")

        resolved = resolve_point(adapter, point, resolved_points={})

        assert resolved.position.x == int(11.0 * MM)
        assert resolved.position.y == int(21.0 * MM)
        assert resolved.footprint is None

    def test_missing_anchor_pad_is_fatal(self):
        fp = _make_fp("U5", pads=[])
        adapter = _adapter_for([fp])
        point = Point(name="p", anchor_ref="U5", anchor_pad="99")

        with pytest.raises(ValidationError, match="has no pad"):
            resolve_point(adapter, point, resolved_points={})

    def test_missing_anchor_ref_is_fatal(self):
        adapter = _adapter_for([])
        point = Point(name="p", anchor_ref="DOES_NOT_EXIST")

        with pytest.raises(ValidationError):
            resolve_point(adapter, point, resolved_points={})


class TestResolvePointAnchorRoleBase:
    def test_role_resolves_to_footprint(self):
        fp = _make_fp("U7", x_mm=5.0, y_mm=6.0, role="FPGA")
        adapter = _adapter_for([fp])
        point = Point(name="p", anchor_role="FPGA")

        resolved = resolve_point(adapter, point, resolved_points={})

        assert resolved.position.x == int(5.0 * MM)
        assert resolved.position.y == int(6.0 * MM)
        assert resolved.footprint is fp


class TestResolvePointXyLiteral:
    def test_literal_xy_no_footprint(self):
        adapter = _adapter_for([])
        point = Point(name="p", xy=(3.0, 4.0))

        resolved = resolve_point(adapter, point, resolved_points={})

        assert resolved.position.x == int(3.0 * MM)
        assert resolved.position.y == int(4.0 * MM)
        assert resolved.footprint is None

    def test_literal_xy_with_shift(self):
        adapter = _adapter_for([])
        point = Point(name="p", xy=(3.0, 4.0))
        # shift_x_mm/shift_y_mm default to 0.0 here — xy+shift together is
        # rejected at load time (see test_points_loading.py), this just
        # confirms the resolver's own math for the (0, 0) default case.
        resolved = resolve_point(adapter, point, resolved_points={})
        assert resolved.position.x == int(3.0 * MM)


class TestResolvePointBoardOrigin:
    """anchor_origin (2026-08-06) — a LIVE board value read via
    adapter.get_board_origin(), not a config literal like xy."""

    def test_drill_origin_no_shift(self):
        adapter = _adapter_for([])
        adapter.get_board_origin.return_value = Vector2.from_xy(int(7.0 * MM), int(8.0 * MM))
        point = Point(name="p", anchor_origin="drill")

        resolved = resolve_point(adapter, point, resolved_points={})

        adapter.get_board_origin.assert_called_once_with("drill")
        assert resolved.position.x == int(7.0 * MM)
        assert resolved.position.y == int(8.0 * MM)
        assert resolved.footprint is None

    def test_grid_origin_with_shift(self):
        adapter = _adapter_for([])
        adapter.get_board_origin.return_value = Vector2.from_xy(int(7.0 * MM), int(8.0 * MM))
        point = Point(name="p", anchor_origin="grid", shift_x_mm=1.0, shift_y_mm=-2.0)

        resolved = resolve_point(adapter, point, resolved_points={})

        adapter.get_board_origin.assert_called_once_with("grid")
        assert resolved.position.x == int(8.0 * MM)
        assert resolved.position.y == int(6.0 * MM)
        assert resolved.footprint is None


class TestResolvePointAnchorPointChain:
    def test_chain_looks_up_already_resolved_point_no_recursion(self):
        """The referenced point must already be in resolved_points — this
        does NOT call the adapter to resolve it again."""
        adapter = MagicMock()  # no methods configured — must not be called
        base = ResolvedPoint(position=Vector2.from_xy(int(10 * MM), int(20 * MM)),
                             footprint=_make_fp("U5"))
        point = Point(name="child", anchor_point="parent", shift_x_mm=1.0)

        resolved = resolve_point(adapter, point, resolved_points={"parent": base})

        assert resolved.position.x == int(11 * MM)
        assert resolved.position.y == int(20 * MM)
        assert resolved.footprint is None  # shift applied — cleared
        adapter.get_footprint.assert_not_called()
        adapter.get_footprints.assert_not_called()

    def test_chain_with_zero_shift_keeps_footprint(self):
        adapter = MagicMock()
        fp = _make_fp("U5")
        base = ResolvedPoint(position=Vector2.from_xy(0, 0), footprint=fp)
        point = Point(name="child", anchor_point="parent")

        resolved = resolve_point(adapter, point, resolved_points={"parent": base})

        assert resolved.footprint is fp

    def test_chain_from_a_base_with_no_footprint_stays_none(self):
        """Chaining off a point that already has no footprint (e.g. it was
        itself shifted) can't magically produce one again, even with zero
        shift here."""
        adapter = MagicMock()
        base = ResolvedPoint(position=Vector2.from_xy(0, 0), footprint=None)
        point = Point(name="child", anchor_point="parent")

        resolved = resolve_point(adapter, point, resolved_points={"parent": base})

        assert resolved.footprint is None


class TestResolvePointChain:
    """resolve_point_chain() — the standalone, points-only dependency walk
    added 2026-08-05 for gui/docks/points.py's Resolve button (see its own
    docstring): unlike planner.py's full dependency-order pass, it must
    never be coupled to unrelated rules/clone_placements, and must not even
    touch an unrelated OTHER point in the same dict that happens to be
    broken."""

    def test_resolves_a_point_with_no_chain_directly(self):
        fp = _make_fp("U5", x_mm=10.0, y_mm=20.0)
        adapter = _adapter_for([fp])
        points = {"p": Point(name="p", anchor_ref="U5")}

        resolved = resolve_point_chain(adapter, points, "p")

        assert resolved.position.x == int(10.0 * MM)
        assert resolved.footprint is fp

    def test_resolves_a_chain_of_points_recursively(self):
        fp = _make_fp("U5", x_mm=10.0, y_mm=20.0)
        adapter = _adapter_for([fp])
        points = {
            "base": Point(name="base", anchor_ref="U5"),
            "child": Point(name="child", anchor_point="base", shift_x_mm=1.0),
        }

        resolved = resolve_point_chain(adapter, points, "child")

        assert resolved.position.x == int(11.0 * MM)
        assert resolved.position.y == int(20.0 * MM)
        assert resolved.footprint is None  # shift applied

    def test_resolves_a_chain_two_levels_deep(self):
        fp = _make_fp("U5", x_mm=1.0, y_mm=1.0)
        adapter = _adapter_for([fp])
        points = {
            "base": Point(name="base", anchor_ref="U5"),
            "mid": Point(name="mid", anchor_point="base"),
            "tip": Point(name="tip", anchor_point="mid", shift_y_mm=2.0),
        }

        resolved = resolve_point_chain(adapter, points, "tip")

        assert resolved.position.x == int(1.0 * MM)
        assert resolved.position.y == int(3.0 * MM)

    def test_an_unrelated_broken_point_in_the_same_dict_is_never_touched(self):
        """Found live 2026-08-05: a config file routinely holds several
        points:, and one being broken must not block previewing an
        unrelated other one — gui/docks/points.py's Resolve button relies
        on this by only including points it can successfully load into the
        dict passed here in the first place; this pins down that
        resolve_point_chain itself only visits what the chain needs."""
        fp = _make_fp("U5", x_mm=1.0, y_mm=2.0)
        adapter = _adapter_for([fp])
        points = {
            "good": Point(name="good", anchor_ref="U5"),
            "broken": Point(name="broken", anchor_ref="DOES_NOT_EXIST"),
        }

        resolved = resolve_point_chain(adapter, points, "good")

        assert resolved.position.x == int(1.0 * MM)

    def test_missing_anchor_point_target_is_fatal(self):
        adapter = _adapter_for([])
        points = {"child": Point(name="child", anchor_point="ghost")}

        with pytest.raises(ValidationError, match="not found"):
            resolve_point_chain(adapter, points, "child")

    def test_cycle_is_fatal(self):
        adapter = _adapter_for([])
        points = {
            "a": Point(name="a", anchor_point="b"),
            "b": Point(name="b", anchor_point="a"),
        }

        with pytest.raises(ValidationError, match="cycle"):
            resolve_point_chain(adapter, points, "a")
