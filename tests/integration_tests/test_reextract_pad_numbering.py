#!/usr/bin/env python3
"""H6 recheck — executable, live-board integration test (2026-09-01).

The pad-numbering stability of the bridging roles R_FL_HOLD / R_FL_WP in Cell
"fpga_flash" (test_fpga_flash_bridging_pads.py, hypothesis H6): for an
electrically symmetric 2-pin R/C which pad ends up "1" vs "2" is an arbitrary
ROUTING choice (R_FB_TOP precedent 2026-08-16), so H1/H3 (signal pad == "1")
hold only for the CURRENTLY extracted template — not as a cross-instance
guarantee. This test performs a REAL re-extract of the fpga_flash cluster from
the LIVE board (the same resolver + run_extract_to_file path the dock's
Re-extract/Re-read use) and re-verifies the signal-pad numbering.

Scope: integration-only (needs a running KiCad with the 3ch-awg-tia board
loaded and the fpga_flash cluster actually placed). Skips — never fails — when
the live environment can't produce a fresh fpga_flash cell (no real profile,
fpga_flash not placed, or the re-extracted cell lacks the two bridging roles).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import load_config
from kicadstamp.extract_writer import run_extract_to_file
from kicadstamp.geometry.cell_copper_connectivity import (
    cell_copper_components, component_containing, component_role_pads,
)
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.placement.services.board_items_resolver import resolve_clone_board_items
from kicadstamp.registry import (
    registry_path_for_config, track_registry_path_for_config,
)
from kicadstamp.template_extraction import extract_template_from_selection

# The real profile may live under either of the two working dirs Denis used.
_PROFILE_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "profiles" / "3ch-awg-tia-v103" / "3ch-awg-tia.sexp",
    Path(__file__).resolve().parents[2] / "profiles" / "3ch-awg-tia-v103-01" / "3ch-awg-tia.sexp",
]
_CELL = "fpga_flash"
_CLUSTER = "FPGA_FLASH"


def _rail_family_roles(cell) -> set[str]:
    """Roles of the cell whose net_template is the literal rail placeholder
    {FLASH} — in fpga_flash exactly {FLASH, R_PIF, C_OUT_BULK, C_OUT_BYPASS}
    (asserted in test_fpga_flash_bridging_pads.py H5)."""
    return {slot.role for slot in cell.components if slot.net_template == "{FLASH}"}


@pytest.mark.integration
def test_reextract_keeps_r_fl_signal_pads_on_1(adapter, tmp_path):
    """Re-extract fpga_flash from the live board and re-verify H1/H3: the
    R_FL_HOLD / R_FL_WP signal pad (the pad whose copper component carries NO
    {FLASH}-family rail role) must still be pad '1' — a flip to pad '2' would
    confirm the H6 instability on a live re-extract."""
    profile = next((p for p in _PROFILE_CANDIDATES if p.exists()), None)
    if profile is None:
        pytest.skip("real profile absent (profiles/ is gitignored)")
    cfg, ctx = load_config(str(profile))

    # Same materialization path ExtractDock._sub_placement_catalog uses for
    # Entity-placed cells (the profile is on the Entity/Placement model).
    try:
        clones = materialize_entity_placements(adapter, cfg, ctx.sheet_names)
    except Exception as e:  # no live fpga_flash placement resolvable
        pytest.skip(f"cannot materialize Entity placements on this board: {e}")
    clone = next((c for c in clones if c.cell == _CELL), None)
    if clone is None:
        pytest.skip(f"no placed Entity materializes cell {_CELL!r} on the live board")

    registry_path = ctx.registry_path or registry_path_for_config(str(profile))
    track_registry_path = (ctx.track_registry_path
                           or track_registry_path_for_config(str(profile)))
    try:
        items = resolve_clone_board_items(
            adapter, cfg, ctx, clone,
            registry_path=registry_path, track_registry_path=track_registry_path)
    except Exception as e:
        pytest.skip(f"cannot resolve fpga_flash live copper: {e}")
    if not items:
        pytest.skip("fpga_flash has no live items (not placed on the board?)")

    target = tmp_path / "reextract.sexp"
    result = run_extract_to_file(
        adapter,
        name=_CELL,
        params={},
        items=items,
        net_template_role={},
        rule_nets=set(),
        origin_kwargs={},
        target_path=target,
        save_profile=False,
        profile_key=_CELL,
        profile_path=None,
        placer_path=str(profile),
        raw_selection=False,
        extract_fn=extract_template_from_selection)
    assert not result.get("error"), result.get("error")

    try:
        fresh_cfg, _ = load_config(str(target))
    except Exception as e:
        pytest.skip(f"freshly re-extracted cell file did not load: {e}")
    cell = fresh_cfg.cells.get(_CELL)
    if cell is None:
        pytest.skip(f"re-extract produced no {_CELL!r} cell")
    components = cell_copper_components(cell)
    rail_roles = _rail_family_roles(cell)

    for role in ("R_FL_HOLD", "R_FL_WP"):
        comp = component_containing(components, role, "1")
        if comp is None:
            pytest.skip(f"fresh cell lacks {role} pad '1' copper — cannot verify")
        tags = component_role_pads(comp)
        rail_tags = {(r, p) for (r, p) in tags if r in rail_roles}
        assert not rail_tags, (
            f"H6 REJECTED after live re-extract: {role} pad '1' now carries "
            f"rail role(s) {sorted(rail_tags)} — the signal-pad numbering "
            f"flipped on re-extract")
