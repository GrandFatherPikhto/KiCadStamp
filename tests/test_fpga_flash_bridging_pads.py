#!/usr/bin/env python3
"""Regression guard: the DECLARED rail disambiguation of the bridging roles
R_FL_HOLD / R_FL_WP in Cell "fpga_flash" is physically consistent with the
cell's own copper.

History (2026-08-29, handoff_2026_08_29_fpga_flash_bridging_pad_hypothesis_
tests.md): H1–H6 used the COPPER GEOMETRY to GUESS which pad of each role sits on
the +3V3_FLASH rail vs the signal (/FPGA/FL_HOLD, /FPGA/FL_WP) — neither role
had net_template_pad/net_template_same_as_role yet, so the GUI auto-fill
(suggest_role_nets_from_cluster) could not resolve a role with two real nets.

Rebaseline (2026-09-03, plan fpga_flash_bridging_pads_rebaseline): the config
now DECLARES the disambiguation — both roles carry
`(net_template_same_as_role "C_OUT_BULK")` (R_FL_HOLD migrated from the older,
pad-number-fragile `(net_template_pad "2")`; see the R_FB_TOP precedent
2026-08-16 and TemplateComponentSlot.net_template_same_as_role's docstring,
kicadstamp/config/models.py). These tests therefore STOP guessing and instead
verify the DECLARATION is physically true: if anyone later points same_as_role
at the wrong role/pad, the mismatch with the real copper is caught here even
though the resolver would still "successfully" resolve a net.

Physical facts verified against the current extract (not assumed):
  - R_FL_HOLD pad "2" shares copper with C_OUT_BYPASS (the rail); pad "1" is an
    isolated singleton copper component (the signal).
  - R_FL_WP pad "2" shares copper with R_PIF's rail-side pad; pad "1" is an
    isolated singleton (the signal).
  - C_OUT_BULK (the config-declared rail ANCHOR role) sits on a SEPARATE rail
    copper island in the extracted cell (it joins C_OUT_BYPASS/FLASH further
    away, through copper the cell does not carry) — the electrical node is the
    rail NET (+3V3_FLASH), so the test asserts the shared/singleton SPLIT, not
    a literal pad-to-C_OUT_BULK copper junction (which does not exist here).

Task W (2026-09-11, prompt_2026_09_11_real_profile_test_fixtures.md): the Cell
body below is a COMMITTED fixture, so this module no longer reads the author's
personal, gitignored profile at all (see _FPGA_FLASH_CELL's own comment).
K.3 (2026-09-10, plan stale_board_snapshot) had added a module-level skip for
"the local profile is not there"; that guard is gone WITH the dependency it
guarded — and it could never have caught the case that actually broke this
module on 2026-09-11: the file PRESENT but EMPTY.

H6 note (kept as a docstring, not a test): pad numbering "1"/"2" is an
arbitrary routing choice for electrically symmetric 2-pin R/C — the live
re-extract recheck lives in tests/integration_tests/test_reextract_pad_numbering.py.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.geometry.cell_copper_connectivity import (
    cell_copper_components, component_containing, component_role_pads,
)

_CELL_NAME = "fpga_flash"
# The two electrically symmetric 2-pin bridging resistors of this cell.
_BRIDGE_ROLES = ("R_FL_HOLD", "R_FL_WP")
_RAIL_NET = "+3V3_FLASH"
_RAIL_ANCHOR_ROLE = "C_OUT_BULK"

# Task W (2026-09-11): the REAL, empirically-extracted fpga_flash Cell body —
# a verbatim sexp_to_dict() parse of the "fpga_flash" cell block from the
# author's preserved copy of the original live profile (byte-identical to a
# second preserved copy — a stable, twice-confirmed extract, not a one-off).
# This test verifies a config DECLARATION against REAL extracted copper
# geometry (module docstring) — the numbers here are NOT synthetic and must
# never be "cleaned up" or hand-edited; a real geometry change belongs in a
# fresh extract, not a hand tweak here.
# Previously this module read the live, gitignored personal profile, which can
# be empty/absent/renamed on any machine at any time — it went to zero cells
# mid-session while being restructured, and this whole module ERRORed instead
# of skipping.
_FPGA_FLASH_CELL = {'vias': [{'offset_along_mm': 5.557,
           'offset_across_mm': -1.43,
           'net_from_role': 'C_OUT_BYPASS',
           'net_from_role_pad': '1'},
          {'offset_along_mm': -1.44,
           'offset_across_mm': 0.8625,
           'net_from_role': 'C_IN_BULK',
           'net_from_role_pad': '1'},
          {'offset_along_mm': 1.8523,
           'offset_across_mm': -1.43,
           'net_from_role': 'C_IN_BYPASS',
           'net_from_role_pad': '2'},
          {'offset_along_mm': -1.618,
           'offset_across_mm': 10.02,
           'net_from_role': 'FLASH',
           'net_from_role_pad': '4'}],
 'components': [{'role': 'C_IN_BULK', 'angle_deg': 90.0, 'net_template': '+3V3'},
                {'role': 'R_FL_HOLD',
                 'offset_along_mm': 7.912,
                 'offset_across_mm': 5.5875,
                 'angle_deg': 90.0,
                 'net_template': '+3V3_FLASH',
                 'net_template_same_as_role': 'C_OUT_BULK'},
                {'role': 'FLASH',
                 'offset_along_mm': 1.9695,
                 'offset_across_mm': 6.8925,
                 'net_template': '+3V3_FLASH',
                 'net_template_pad': '8'},
                {'role': 'R_PIF',
                 'offset_along_mm': 3.1,
                 'offset_across_mm': 2.7075,
                 'net_template': '+3V3',
                 'net_template_same_as_role': 'C_IN_BULK'},
                {'role': 'C_OUT_BULK',
                 'offset_along_mm': 3.7047,
                 'angle_deg': 90.0,
                 'net_template': '+3V3_FLASH'},
                {'role': 'R_FL_WP',
                 'offset_along_mm': -4.098,
                 'offset_across_mm': 6.93,
                 'angle_deg': 90.0,
                 'net_template': '+3V3_FLASH',
                 'net_template_same_as_role': 'C_OUT_BULK'},
                {'role': 'C_IN_BYPASS',
                 'offset_along_mm': 1.8523,
                 'offset_across_mm': 0.295,
                 'angle_deg': 90.0,
                 'net_template': '+3V3'},
                {'role': 'C_OUT_BYPASS',
                 'offset_along_mm': 5.557,
                 'offset_across_mm': 0.295,
                 'angle_deg': -90.0,
                 'net_template': '+3V3_FLASH'}],
 'tracks': [{'start_along_mm': 0.3875,
             'start_across_mm': -0.8625,
             'end_along_mm': 0.9775,
             'end_across_mm': -0.2725,
             'width_mm': 0.65,
             'net_from_role': 'C_IN_BULK',
             'net_from_role_pad': '2'},
            {'start_along_mm': 5.557,
             'start_across_mm': 6.2575,
             'end_along_mm': 7.8395,
             'end_across_mm': 6.2575,
             'width_mm': 0.4,
             'net_from_role': 'R_FL_HOLD',
             'net_from_role_pad': '1'},
            {'start_along_mm': 7.8395,
             'start_across_mm': 6.2575,
             'end_along_mm': 7.912,
             'end_across_mm': 6.185,
             'width_mm': 0.4,
             'net_from_role': 'R_FL_HOLD',
             'net_from_role_pad': '1'},
            {'start_along_mm': 1.8523,
             'start_across_mm': 2.0573,
             'end_along_mm': 2.5025,
             'end_across_mm': 2.7075,
             'width_mm': 0.65,
             'net_from_role': 'R_PIF',
             'net_from_role_pad': '1'},
            {'start_along_mm': 1.8523,
             'start_across_mm': 2.0573,
             'end_along_mm': 1.8523,
             'end_across_mm': 0.8625,
             'width_mm': 0.65,
             'net_from_role': 'C_IN_BYPASS',
             'net_from_role_pad': '1'},
            {'start_across_mm': 0.8625,
             'end_along_mm': -1.44,
             'end_across_mm': 0.8625,
             'width_mm': 0.65,
             'net_from_role': 'C_IN_BULK',
             'net_from_role_pad': '1'},
            {'start_along_mm': -3.44,
             'start_across_mm': 3.51,
             'end_along_mm': -4.098,
             'end_across_mm': 4.168,
             'width_mm': 0.4,
             'net_from_role': 'R_FL_WP',
             'net_from_role_pad': '2'},
            {'start_along_mm': -4.098,
             'start_across_mm': 7.5275,
             'end_along_mm': -1.618,
             'end_across_mm': 7.5275,
             'width_mm': 0.4,
             'net_from_role': 'R_FL_WP',
             'net_from_role_pad': '1'},
            {'start_along_mm': 3.7047,
             'start_across_mm': 0.8625,
             'end_along_mm': 3.7047,
             'end_across_mm': 2.7003,
             'width_mm': 0.65,
             'net_from_role': 'R_PIF',
             'net_from_role_pad': '2'},
            {'start_along_mm': 5.557,
             'start_across_mm': 4.9875,
             'end_along_mm': 7.9095,
             'end_across_mm': 4.9875,
             'width_mm': 0.4,
             'net_from_role': 'R_FL_HOLD',
             'net_from_role_pad': '2'},
            {'start_along_mm': 5.557,
             'start_across_mm': 0.8625,
             'end_along_mm': 5.557,
             'end_across_mm': 4.9875,
             'width_mm': 0.65,
             'net_from_role': 'C_OUT_BYPASS',
             'net_from_role_pad': '2'},
            {'start_along_mm': 7.9095,
             'start_across_mm': 4.9875,
             'end_along_mm': 7.912,
             'end_across_mm': 4.99,
             'width_mm': 0.4,
             'net_from_role': 'R_FL_HOLD',
             'net_from_role_pad': '2'},
            {'start_along_mm': 2.895,
             'start_across_mm': 3.51,
             'end_along_mm': -3.44,
             'end_across_mm': 3.51,
             'width_mm': 0.4,
             'net_from_role': 'R_PIF',
             'net_from_role_pad': '2'},
            {'start_along_mm': 3.6975,
             'start_across_mm': 2.7075,
             'end_along_mm': 2.895,
             'end_across_mm': 3.51,
             'width_mm': 0.4,
             'net_from_role': 'R_PIF',
             'net_from_role_pad': '2'},
            {'start_along_mm': -4.098,
             'start_across_mm': 4.168,
             'end_along_mm': -4.098,
             'end_across_mm': 6.3325,
             'width_mm': 0.4,
             'net_from_role': 'R_FL_WP',
             'net_from_role_pad': '2'},
            {'start_along_mm': 3.7047,
             'start_across_mm': 2.7003,
             'end_along_mm': 3.6975,
             'end_across_mm': 2.7075,
             'width_mm': 0.65,
             'net_from_role': 'R_PIF',
             'net_from_role_pad': '2'},
            {'start_along_mm': 5.557,
             'start_across_mm': -0.2725,
             'end_along_mm': 5.557,
             'end_across_mm': -1.43,
             'width_mm': 0.65,
             'net_from_role': 'C_OUT_BYPASS',
             'net_from_role_pad': '1'},
            {'start_along_mm': -1.618,
             'start_across_mm': 8.7975,
             'end_along_mm': -1.618,
             'end_across_mm': 10.02,
             'width_mm': 0.65,
             'net_from_role': 'FLASH',
             'net_from_role_pad': '4'},
            {'start_along_mm': 0.9775,
             'start_across_mm': -0.2725,
             'end_along_mm': 1.8523,
             'end_across_mm': -0.2725,
             'width_mm': 0.65,
             'net_from_role': 'C_IN_BYPASS',
             'net_from_role_pad': '2'},
            {'start_along_mm': 2.2425,
             'start_across_mm': -0.2725,
             'end_along_mm': 2.8325,
             'end_across_mm': -0.8625,
             'width_mm': 0.65,
             'net_from_role': 'C_IN_BYPASS',
             'net_from_role_pad': '2'},
            {'start_along_mm': 1.8523,
             'start_across_mm': -0.2725,
             'end_along_mm': 2.2425,
             'end_across_mm': -0.2725,
             'width_mm': 0.65,
             'net_from_role': 'C_IN_BYPASS',
             'net_from_role_pad': '2'},
            {'start_along_mm': 4.1375,
             'start_across_mm': -0.8625,
             'end_along_mm': 4.7275,
             'end_across_mm': -0.2725,
             'width_mm': 0.65,
             'net_from_role': 'C_OUT_BULK',
             'net_from_role_pad': '2'},
            {'start_along_mm': 3.7047,
             'start_across_mm': -0.8625,
             'end_along_mm': 4.1375,
             'end_across_mm': -0.8625,
             'width_mm': 0.65,
             'net_from_role': 'C_OUT_BULK',
             'net_from_role_pad': '2'},
            {'start_along_mm': 1.8523,
             'start_across_mm': -0.2725,
             'end_along_mm': 1.8523,
             'end_across_mm': -1.43,
             'width_mm': 0.65,
             'net_from_role': 'C_IN_BYPASS',
             'net_from_role_pad': '2'},
            {'start_along_mm': 2.8325,
             'start_across_mm': -0.8625,
             'end_along_mm': 3.7047,
             'end_across_mm': -0.8625,
             'width_mm': 0.65,
             'net_from_role': 'C_OUT_BULK',
             'net_from_role_pad': '2'},
            {'start_along_mm': 4.7275,
             'start_across_mm': -0.2725,
             'end_along_mm': 5.557,
             'end_across_mm': -0.2725,
             'width_mm': 0.65,
             'net_from_role': 'C_OUT_BYPASS',
             'net_from_role_pad': '1'}]}

def _slot(cell, role: str):
    """The component slot of `role` in `cell`."""
    return next(s for s in cell.components if s.role == role)


@pytest.fixture(scope="module")
def fpga_flash_cell(tmp_path_factory):
    """The fpga_flash Cell, loaded through the real load_config from the
    committed fixture above — no live board, no IPC, and no dependency on any
    local (gitignored) profile file."""
    path = tmp_path_factory.mktemp("fpga_flash") / "cfg.sexp"
    path.write_text(dict_to_sexp({"cells": {_CELL_NAME: _FPGA_FLASH_CELL}}),
                    encoding="utf-8")
    cfg, _ctx = load_config(str(path))
    return cfg.cells[_CELL_NAME]


class TestDeclaredRailDisambiguation:
    """The config declares, for BOTH bridging roles, the preferred rail
    mechanism (net_template_same_as_role -> the +3V3_FLASH rail anchor), not the
    pad-number-fragile net_template_pad — a regression guard on the config
    itself (a revert to net_template_pad, or a wrong anchor, fails here)."""

    @pytest.mark.parametrize("role", _BRIDGE_ROLES)
    def test_bridge_role_declares_same_as_role_rail_anchor(
            self, fpga_flash_cell, role):
        cell = fpga_flash_cell
        slot = _slot(cell, role)
        assert slot.net_template == _RAIL_NET, (
            f"{role} must sit on the {_RAIL_NET} rail")
        assert slot.net_template_same_as_role == _RAIL_ANCHOR_ROLE, (
            f"{role} must declare the rail anchor via net_template_same_as_role "
            f"(== {_RAIL_ANCHOR_ROLE!r}), not the pad-number-fragile "
            f"net_template_pad")
        assert slot.net_template_pad is None
        # The declared anchor is itself on the same rail net.
        anchor = _slot(cell, _RAIL_ANCHOR_ROLE)
        assert anchor.net_template == _RAIL_NET


class TestRailSplitMatchesCopper:
    """The declared rail/signal split is physically real: exactly ONE pad of
    each bridging role shares copper with OTHER components (the rail junction),
    the OTHER pad is an isolated singleton copper component (the signal).
    Hardcoded pad numbers are deliberately avoided (H6 — arbitrary per re-
    extract); the shared-vs-singleton split is the invariant."""

    @pytest.mark.parametrize("role", _BRIDGE_ROLES)
    def test_rail_pad_shares_copper_and_signal_pad_is_isolated(
            self, fpga_flash_cell, role):
        cell = fpga_flash_cell
        components = cell_copper_components(cell)
        shared, isolated = [], []
        for pad in ("1", "2"):
            comp = component_containing(components, role, pad)
            assert comp is not None, f"no copper tagged ({role!r}, pad {pad!r})"
            tags = component_role_pads(comp)
            others = [t for t in tags if t[0] != role]
            (shared if others else isolated).append(pad)
        # Both roles are 2-pin bridging parts: one rail pad (shared copper with
        # at least one other component) and one signal pad (isolated copper).
        assert len(shared) == 1 and len(isolated) == 1, (
            f"{role} should have exactly one rail pad and one isolated signal "
            f"pad — shared={shared}, isolated={isolated}")
        rail_pad = shared[0]
        rail_tags = component_role_pads(
            component_containing(components, role, rail_pad))
        rail_neighbours = sorted({r for (r, _p) in rail_tags if r != role})
        # Current-extract neighbours: R_FL_HOLD -> C_OUT_BYPASS, R_FL_WP -> R_PIF
        # (R_PIF's rail-side pad; C_OUT_BULK — the config anchor — is on a
        # separate rail island in this extract, see the module docstring).
        assert rail_neighbours, f"{role} rail pad must share copper with another role"
