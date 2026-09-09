#!/usr/bin/env python3
"""Tests for component_resolver.py.

Phase 2 (2026-07-31, see handoff_2026_07_31_consolidated.md §8): the
"anchor_ref -> footprint, or fatal" branch was written near-identically three
times (Rule via ComponentResolver, ClonePlacement, ThermalViaArrayConfig).
resolve_footprint_by_ref() is now the single shared implementation; the
ref-vs-role DECISION and the role branch itself stay with each caller
on purpose (ClonePlacement's role branch needs {placeholder} substitution
that Rule/ThermalViaArrayConfig don't have) — not tested here, already
covered by test_clone_role_resolver.py.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kipy.board_types import FootprintInstance

from kicadstamp.exceptions import ValidationError
from kicadstamp.config import Cell, TemplateComponentSlot
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.services.component_resolver import (
    ComponentResolver, resolve_footprint_by_ref, resolve_pad_mount,
)


def _make_fp(ref):
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = ref
    return fp


class TestResolveFootprintByRef:
    def test_found_returns_footprint(self):
        fp = _make_fp("U5")
        adapter = MagicMock()
        adapter.get_footprint.side_effect = lambda ref: fp if ref == "U5" else None

        result = resolve_footprint_by_ref(adapter, "U5", label="my label")
        assert result is fp

    def test_not_found_raises_validation_error_with_default_hint(self):
        adapter = MagicMock()
        adapter.get_footprint.return_value = None

        with pytest.raises(ValidationError) as exc_info:
            resolve_footprint_by_ref(adapter, "U99", label="rule (net '+3V3')")
        message = str(exc_info.value)
        assert "rule (net '+3V3')" in message
        assert "U99" in message
        assert "typo" in message  # default hint present

    def test_not_found_uses_custom_hint_when_given(self):
        adapter = MagicMock()
        adapter.get_footprint.return_value = None

        with pytest.raises(ValidationError) as exc_info:
            resolve_footprint_by_ref(adapter, "U99", label="clone_x",
                                     not_found_hint="a very specific hint")
        assert "a very specific hint" in str(exc_info.value)


class TestComponentResolverAnchorFp:
    def test_ref_branch_delegates_to_resolve_footprint_by_ref(self):
        fp = _make_fp("U5")
        adapter = MagicMock()
        adapter.get_footprint.side_effect = lambda ref: fp if ref == "U5" else None
        resolver = ComponentResolver(adapter, config=MagicMock(), sheet_names={})

        result = resolver.resolve_anchor_fp("U5", None, None, None, label="test")
        assert result is fp

    def test_ref_branch_not_found_is_fatal(self):
        adapter = MagicMock()
        adapter.get_footprint.return_value = None
        resolver = ComponentResolver(adapter, config=MagicMock(), sheet_names={})

        with pytest.raises(ValidationError):
            resolver.resolve_anchor_fp("U99", None, None, None, label="test")

    def test_role_branch_still_used_when_ref_is_none(self):
        fp = _make_fp("U7")
        adapter = MagicMock()
        adapter.get_footprints.return_value = [fp]
        adapter.get_field_value.side_effect = lambda f, name: "FPGA" if name == "Role" else None
        adapter.get_selected_items.return_value = []
        resolver = ComponentResolver(adapter, config=MagicMock(), sheet_names={})

        result = resolver.resolve_anchor_fp(None, "FPGA", None, None, label="test")
        assert result is fp


# ── resolve_pad_mount (phase A, plan_2026_09_09_cell_anchor_v2 §A.4/§A.5) ──

class _Pad:
    """A live pad with a REAL absolute position (mm) — resolve_pad_mount reads
    pad.position after the caller reads it via get_pad_by_number."""

    def __init__(self, x_mm, y_mm):
        self.position = Vector2.from_xy_mm(x_mm, y_mm)


def _pad_cell(**kw):
    return Cell(name="padcell", components=[
        TemplateComponentSlot(role="MOUNT", offset_along_mm=-5.05,
                              offset_across_mm=-0.295, angle_deg=0.0),
    ], **kw)


def _mount_fp(x_mm=10.0, y_mm=20.0, angle=0.0, back=False):
    return Footprint(ref="C-OUT", uuid="uuid-C-OUT",
                     position=Vector2.from_xy_mm(x_mm, y_mm),
                     angle_deg=angle,
                     layer=BoardLayer.BL_B_Cu if back else BoardLayer.BL_F_Cu)


def _pad_mount_adapter(fps, pads):
    """adapter whose get_footprint resolves by ref and get_pad_by_number reads
    a positioned pad per (ref, number) — pads: {ref: {num: _Pad}}."""
    adapter = MagicMock()
    adapter.get_footprint.side_effect = {fp.ref: fp for fp in fps}.get
    adapter.get_pad_by_number.side_effect = (
        lambda fp, num: (pads.get(fp.ref) or {}).get(str(num)))
    return adapter


class TestResolvePadMount:
    def test_pad_anchor_resolves_live_mount(self):
        """anchor_role+anchor_pad (no anchor_xy) -> the pad's bbox-local point:
        fp at (10,20) identity, cell local (0,0) at (15.05,20.295), pad at
        (12,19) -> (-3.05, -1.295)."""
        fp = _mount_fp()
        adapter = _pad_mount_adapter([fp], {"C-OUT": {"1": _Pad(12.0, 19.0)}})
        result = resolve_pad_mount(
            adapter, _pad_cell(anchor_role="MOUNT", anchor_pad="1"),
            {"MOUNT": "C-OUT"}, "clone X")
        assert result[0] == pytest.approx(-3.05, abs=1e-6)
        assert result[1] == pytest.approx(-1.295, abs=1e-6)

    def test_anchor_xy_wins_returns_none(self):
        """GUARD 1: anchor_xy set (+role+pad) -> None; no live read happens —
        the pad lookup would raise if invoked."""
        fp = _mount_fp()

        def _forbidden(*_a, **_k):
            raise AssertionError("GUARD 1: live pad read must not happen")
        adapter = _pad_mount_adapter([fp], {})
        adapter.get_pad_by_number.side_effect = _forbidden
        result = resolve_pad_mount(
            adapter, _pad_cell(anchor_role="MOUNT", anchor_pad="1",
                               anchor_xy=(2.0, 3.0)),
            {"MOUNT": "C-OUT"}, "clone X")
        assert result is None

    def test_role_only_anchor_is_offline_none(self):
        """anchor_role without anchor_pad is the offline role-centre case — the
        caller must NOT try a live pad read for it."""
        adapter = _pad_mount_adapter([], {})
        assert resolve_pad_mount(
            adapter, _pad_cell(anchor_role="MOUNT"), {"MOUNT": "C-OUT"},
            "clone X") is None

    def test_no_pad_anchor_returns_none(self):
        """No anchor_role/anchor_pad at all -> None (cell_mount_offset keeps the
        whole offline decision)."""
        adapter = _pad_mount_adapter([], {})
        assert resolve_pad_mount(adapter, _pad_cell(), {}, "clone X") is None

    def test_unresolved_anchor_role_is_fatal(self):
        """GUARD 3: the declared anchor role does not resolve on the board —
        FATAL, never a silent (0,0) fallback (which would shift the whole cell
        content)."""
        adapter = _pad_mount_adapter([], {})
        with pytest.raises(ValidationError, match="not resolved on the board"):
            resolve_pad_mount(
                adapter, _pad_cell(anchor_role="MOUNT", anchor_pad="1"),
                {"CAP": "C-CAP"}, "clone X")

    def test_resolved_ref_missing_on_board_is_fatal(self):
        adapter = _pad_mount_adapter([], {})  # C-OUT not on the board
        with pytest.raises(ValidationError, match="not on the live board"):
            resolve_pad_mount(
                adapter, _pad_cell(anchor_role="MOUNT", anchor_pad="1"),
                {"MOUNT": "C-OUT"}, "clone X")

    def test_anchor_role_not_a_cell_component_is_fatal_not_stopiteration(self):
        """GUARD 4: anchor_role names a role that is not among this cell's own
        components -> explicit ValidationError (never a bare next() whose
        StopIteration would escape)."""
        fp = _mount_fp()
        adapter = _pad_mount_adapter([fp], {"C-OUT": {"1": _Pad(12.0, 19.0)}})
        cell = _pad_cell(anchor_role="GHOST", anchor_pad="1")
        with pytest.raises(ValidationError, match="not a component of this cell"):
            resolve_pad_mount(adapter, cell, {"GHOST": "C-OUT"}, "clone X")

    def test_missing_pad_on_anchor_footprint_is_fatal(self):
        fp = _mount_fp()
        adapter = _pad_mount_adapter([fp], {"C-OUT": {}})  # no pad '1'
        with pytest.raises(ValidationError, match="has no pad"):
            resolve_pad_mount(
                adapter, _pad_cell(anchor_role="MOUNT", anchor_pad="1"),
                {"MOUNT": "C-OUT"}, "clone X")
