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
from kicadstamp.placement.services.component_resolver import (
    ComponentResolver, resolve_footprint_by_ref,
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
