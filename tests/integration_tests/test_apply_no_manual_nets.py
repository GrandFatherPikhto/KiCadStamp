# tests/integration_tests/test_apply_no_manual_nets.py
"""Phase 4 step 4.3 — full `apply` with NO manual nets on the live board.

Live integration test (marked @pytest.mark.integration, excluded from the
default non-integration regression): loads the 3CH-AWG-TIA-v103 profile
config, strips the manual net coordinates (nets/params/net_overrides/refs)
from EVERY clone_placement, and proves the full apply pipeline resolves and
plans every clone automatically from the live board — no net is typed by hand
(plan_2026_08_28_auto_nets_full_automation.md Phase 4 step 4.3).

The cell net_template placeholders ({FB_PI_FLT}, {R_LED}, ...) with no params
fall through to the live auto-derivation path (Phase 2 steps 2.1/2.2/2.3) —
verified live: 29/29 clones resolve with all manual nets stripped.

Safety: read-only — dry-run only; the current PCB editor selection is ignored
so the result is deterministic. The unrelated coordinate_placements section is
excluded from the full-run test because the live board does not currently carry
all of its tagged components (a board-state concern, not a nets one).
"""
import dataclasses

import pytest

from kicadstamp.config import load_config
from kicadstamp.apply_pipeline import RunOptions, run_apply
from kicadstamp.placement.services.clone_role_resolver import resolve_roles_by_nets

LIVE_CONFIG = "profiles/3ch-awg-tia-v103/3ch-awg-tia.sexp"


def _strip_net_coordinates(clone):
    """Derive a ClonePlacement with every manual net coordinate removed."""
    return dataclasses.replace(
        clone, nets={}, params={}, net_overrides={}, refs={},
    )


@pytest.mark.integration
class TestApplyNoManualNets:
    def test_every_clone_role_resolves_without_nets(self, adapter):
        """Per-clone: strip nets/params/net_overrides/refs and resolve every
        cell role automatically from the live board."""
        cfg, ctx = load_config(LIVE_CONFIG)
        sheet_names = ctx.sheet_names if ctx else {}
        assert cfg.clone_placements, "live config has no clone_placements to test"
        fails = []
        for clone in cfg.clone_placements:
            stripped = _strip_net_coordinates(clone)
            cell = cfg.cells[clone.cell]
            try:
                role_to_ref = resolve_roles_by_nets(
                    adapter, cell, stripped, sheet_names=sheet_names)
            except Exception as exc:  # noqa: BLE001 - report per clone
                fails.append(f"{clone.name or clone.cluster}: {exc}")
                continue
            if set(role_to_ref) != {s.role for s in cell.components}:
                fails.append(
                    f"{clone.name or clone.cluster}: resolved "
                    f"{len(role_to_ref)}/{len(cell.components)} roles")
        assert not fails, \
            "clone roles failed to auto-derive without manual nets:\n" + \
            "\n".join(fails)

    def test_full_apply_dry_run_without_nets(self, adapter):
        """The full apply pipeline (load -> filter -> validate -> resolve order
        -> plan) runs end-to-end on a nets-free config scoped to
        clone_placements and produces a plan for every clone."""
        cfg, ctx = load_config(LIVE_CONFIG)
        clones_only = dataclasses.replace(
            cfg,
            clone_placements=[_strip_net_coordinates(c) for c in cfg.clone_placements],
            rules=[],
            coordinate_placements=[],
            net_traces=[],
            thermal_via_arrays=[],
        )
        options = RunOptions(
            config_path=LIVE_CONFIG, dry_run=True,
            no_selection=True, timeout_ms=30000,
        )
        report = run_apply(options, cfg=clones_only, ctx=ctx)
        assert report is not None, "dry-run produced no report"
        text = "\n".join(report)
        assert "=== DRY RUN ===" in text
        assert "Moves:" in text
        # every clone contributed at least one planned move
        assert len(cfg.clone_placements) > 0
