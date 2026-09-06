"""Copper-layer extension (plan_2026_09_05_scheme_list.md Step 0).

The domain BoardLayer now models the FULL copper stack (F.Cu + In1..In30 +
B.Cu), and every layer conversion (domain <-> kipy, domain <-> copper name)
maps every copper layer explicitly instead of silently collapsing inner
layers to F.Cu (the old binary F/B behaviour).

The existing 2-layer logic must be untouched: BL_F_Cu/BL_B_Cu keep their
historical enum values and 'F.Cu'/'B.Cu' map to exactly those members.
"""
import pytest

from kicadstamp.domain.geometry import BoardLayer
from kicadstamp.domain.board import layer_from_kipy, layer_to_kipy
from kicadstamp.utils.layers import (
    COPPER_LAYER_STRINGS,
    layer_from_str,
    layer_to_str,
)

# The project's real stack (P0.1, done_2026_09_05_scheme_list_p1.md).
REAL_STACK = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")


class TestBoardLayerEnum:
    def test_has_full_copper_set(self):
        assert len(COPPER_LAYER_STRINGS) == 32
        assert BoardLayer.BL_F_Cu.value == 0
        assert BoardLayer.BL_B_Cu.value == 32  # historical value unchanged
        # Inner layers present in order
        for i in range(1, 31):
            assert getattr(BoardLayer, f"BL_In{i}_Cu").value == i

    def test_historical_members_unchanged(self):
        # 2-layer logic compares by identity — members keep their meaning.
        assert layer_to_str(BoardLayer.BL_F_Cu) == "F.Cu"
        assert layer_to_str(BoardLayer.BL_B_Cu) == "B.Cu"


class TestCopperNameRoundTrip:
    @pytest.mark.parametrize("name", COPPER_LAYER_STRINGS)
    def test_roundtrip_every_copper_name(self, name):
        layer = layer_from_str(name)
        assert layer_to_str(layer) == name
        assert layer_from_str(name) is layer

    @pytest.mark.parametrize("name", REAL_STACK)
    def test_real_stack_names_map_to_distinct_members(self, name):
        """The real 4-layer stack must produce four DIFFERENT members (the old
        binary mapper collapsed In1.Cu/In2.Cu into F.Cu)."""
        members = {layer_from_str(n) for n in REAL_STACK}
        assert len(members) == 4
        assert layer_from_str("F.Cu") is BoardLayer.BL_F_Cu
        assert layer_from_str("In1.Cu") is BoardLayer.BL_In1_Cu
        assert layer_from_str("In2.Cu") is BoardLayer.BL_In2_Cu
        assert layer_from_str("B.Cu") is BoardLayer.BL_B_Cu

    def test_tolerant_whitespace_and_unknown(self):
        assert layer_from_str("  In1.Cu ") is BoardLayer.BL_In1_Cu
        # Historical lenient fallback preserved for legacy undo-log strings.
        assert layer_from_str("B.Cu") is BoardLayer.BL_B_Cu
        assert layer_from_str("some B.Cu suffix") is BoardLayer.BL_B_Cu
        assert layer_from_str("totally-unknown") is BoardLayer.BL_F_Cu


class TestKipyCopperMapping:
    """domain/board.py maps every kipy copper layer explicitly (the live probe
    2026-09-06: BL_F_Cu=3, BL_In1_Cu=4..BL_In30_Cu=33, BL_B_Cu=34)."""

    def _kipy_value(self, member_name: str):
        from kipy.board_types import BoardLayer as KipyBoardLayer
        return KipyBoardLayer.Value(member_name)

    @pytest.mark.parametrize("n", [None] + list(range(1, 31)) + ["B"])
    def test_kipy_to_domain_and_back(self, n):
        if n is None:
            domain = BoardLayer.BL_F_Cu
            kipy_name = "BL_F_Cu"
        elif n == "B":
            domain = BoardLayer.BL_B_Cu
            kipy_name = "BL_B_Cu"
        else:
            domain = getattr(BoardLayer, f"BL_In{n}_Cu")
            kipy_name = f"BL_In{n}_Cu"
        kipy_value = self._kipy_value(kipy_name)
        assert layer_from_kipy(kipy_value) is domain
        assert layer_to_kipy(domain) == kipy_value

    def test_unknown_kipy_value_keeps_historical_f_cu_fallback(self):
        # A non-copper kipy layer (e.g. Edge.Cuts = 47) — never a real routed
        # copper member — keeps the historical F.Cu fallback so test doubles
        # and sentinels behave as they always did. Every REAL copper layer is
        # mapped explicitly (see test_kipy_to_domain_and_back).
        from kipy.board_types import BoardLayer as KipyBoardLayer
        edge_cuts = KipyBoardLayer.Value("BL_Edge_Cuts")
        assert layer_from_kipy(edge_cuts) is BoardLayer.BL_F_Cu
