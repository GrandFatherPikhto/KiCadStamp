#!/usr/bin/env python3
"""Regression guard: the DECLARED rail disambiguation of the bridging roles
R_FL_HOLD / R_FL_WP in Cell "fpga_flash" is physically consistent with the
cell's own copper (profile profiles/3ch-awg-tia-v103/config.sexp — the working
KiCadStamp profile of the 3CH-AWG-TIA board).

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

H6 note (kept as a docstring, not a test): pad numbering "1"/"2" is an
arbitrary routing choice for electrically symmetric 2-pin R/C — the live
re-extract recheck lives in tests/integration_tests/test_reextract_pad_numbering.py.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.config import load_config
from kicadstamp.geometry.cell_copper_connectivity import (
    cell_copper_components, component_containing, component_role_pads,
)

_PROFILE = (Path(__file__).resolve().parents[1]
            / "profiles" / "3ch-awg-tia-v103" / "config.sexp")
_CELL_NAME = "fpga_flash"
_CLUSTER = "FPGA_FLASH"
# The two electrically symmetric 2-pin bridging resistors of this cell.
_BRIDGE_ROLES = ("R_FL_HOLD", "R_FL_WP")
# The rail net both roles' rail side sits on, and the config-declared rail
# anchor role they both reference via net_template_same_as_role.
_RAIL_NET = "+3V3_FLASH"
_RAIL_ANCHOR_ROLE = "C_OUT_BULK"


def _slot(cell, role: str):
    """The component slot of `role` in `cell`."""
    return next(s for s in cell.components if s.role == role)


@pytest.fixture(scope="module")
def fpga_flash():
    """(cell, entity) for Cell fpga_flash and its FPGA_FLASH-cluster Entity,
    loaded through the real load_config — no live board, no IPC. The profile is
    on the Entity/Placement model since 2026-08-30, where the Entity NAME no
    longer equals the Cluster tag (data drift fixed at the 2026-09-03 rebaseline):
    the FPGA_FLASH Entity is found by name ("fpga_flash_fpga") or, as a
    fallback, by entity.cluster == "FPGA_FLASH" — never by name == Cluster."""
    cfg, _ctx = load_config(str(_PROFILE))
    cell = cfg.cells[_CELL_NAME]
    entity = next((e for e in cfg.entities if e.name == "fpga_flash_fpga"), None)
    if entity is None:
        entity = next(e for e in cfg.entities if e.cluster == _CLUSTER)
    return cell, entity


class TestDeclaredRailDisambiguation:
    """The config declares, for BOTH bridging roles, the preferred rail
    mechanism (net_template_same_as_role -> the +3V3_FLASH rail anchor), not the
    pad-number-fragile net_template_pad — a regression guard on the config
    itself (a revert to net_template_pad, or a wrong anchor, fails here)."""

    @pytest.mark.parametrize("role", _BRIDGE_ROLES)
    def test_bridge_role_declares_same_as_role_rail_anchor(self, fpga_flash, role):
        cell, _entity = fpga_flash
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
    def test_rail_pad_shares_copper_and_signal_pad_is_isolated(self, fpga_flash, role):
        cell, _entity = fpga_flash
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
