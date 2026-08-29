#!/usr/bin/env python3
"""Hypothesis tests for the "signal pad" of the bridging roles R_FL_HOLD and
R_FL_WP in Cell "fpga_flash" (profile profiles/3ch-awg-tia-v103/3ch-awg-tia.sexp).

Task (2026-08-29, handoff_2026_08_29_fpga_flash_bridging_pad_hypothesis_tests.md):
a two-pin resistor, one pad on a hierarchical signal net (/FPGA/FL_HOLD,
/FPGA/FL_WP), the other on the +3V3_FLASH rail. Both roles have NO
net_template_pad / net_template_same_as_role in the config, so the GUI Nets-tab
auto-fill (suggest_role_nets_from_cluster) leaves them for manual entry — the
candidate carries TWO non-rule nets, breaking lemma 2. Before Denis decides
whether (and how) to fix the config, ALL hypotheses H1-H6 are re-checked HERE
by tests, purely from already-committed data (the extracted cell's own
tracks/vias) — no live board, no IPC.

H1  R_FL_HOLD pad "1" = signal (/FPGA/FL_HOLD), pad "2" = rail (+3V3_FLASH).
H2  reversed for R_FL_HOLD.
H3  R_FL_WP pad "1" = signal (/FPGA/FL_WP), pad "2" = rail (+3V3_FLASH).
H4  reversed for R_FL_WP.
H5  no lemma-2-safe sibling role exists whose own net equals the signal side,
    so net_template_same_as_role has nothing to fill in this cell.
H6  the pad-instability risk (R_FB_TOP precedent, 2026-08-16): even if H1/H3
    hold for the CURRENT extracted template, pad numbering is an arbitrary
    routing choice for electrically symmetric 2-pin R/C — not a guarantee for a
    future re-extract.

The copper-connectivity helper (cell_copper_components) is deliberately a
reusable module-level function, not inline test code — it will be reused if
other hintless bridging roles show up in other cells of the same profile.

Scope: tests + reusable connectivity helper ONLY. The profile config and the
resolver/config engine are used as-is, never modified.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.config import load_config
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.geometry.cell_copper_connectivity import (
    cell_copper_components, component_containing, component_role_pads,
    component_shared_point,
)
from kicadstamp.net_resolution import resolve_net
from kicadstamp.placement.services.clone_role_resolver import suggest_role_nets_from_cluster

_PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "3ch-awg-tia-v103" / "3ch-awg-tia.sexp"
_CELL_NAME = "fpga_flash"
_CLUSTER = "FPGA_FLASH"

# The rail every {FLASH}-family role resolves to (via the FPGA_FLASH params).
_RAIL_NET = "+3V3_FLASH"


# The copper-connectivity helpers (cell_copper_components, component_containing,
# component_role_pads, component_shared_point) moved to core 2026-08-29
# (kicadstamp/geometry/cell_copper_connectivity.py, plan
# 2026_08_29_bridging_pad_connectivity_guard.md §2.1) — they are imported at
# the top of this file, no longer duplicated here.


def _rail_family_roles(cell) -> set[str]:
    """Roles of the cell whose net_template is the literal rail placeholder
    {FLASH} — they all resolve to +3V3_FLASH via the FPGA_FLASH params. In this
    cell exactly {FLASH, R_PIF, C_OUT_BULK, C_OUT_BYPASS} (asserted in H5)."""
    return {slot.role for slot in cell.components if slot.net_template == "{FLASH}"}


def _shared_point_between(component, role: str, pad: str, other_tag) -> tuple[float, float]:
    """Thin wrapper over component_shared_point (core, moved 2026-08-29) — kept
    so the H1-H4 assertion call sites stay unchanged."""
    return component_shared_point(component, (role, pad), other_tag)


def signal_pad_for_role(components, cell, role: str) -> str | None:
    """The pad of `role` that is NOT co-located with a {FLASH}-family (rail)
    role — i.e. the signal pad (H1/H3). None if no such pad is found."""
    rail_roles = _rail_family_roles(cell)
    for pad in ("1", "2"):
        comp = component_containing(components, role, pad)
        if comp is None:
            continue
        tags = component_role_pads(comp)
        if not any(r in rail_roles for (r, _p) in tags):
            return pad
    return None


# ---------------------------------------------------------------------------
# Mock-adapter helpers (same shape as tests/test_clone_role_resolver.py)
# ---------------------------------------------------------------------------


def _make_fp(ref, role=None, nets=None, cluster=None):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._nets = nets or []
    fp._cluster = cluster
    return fp


def _role_or_cluster(fp, field_name):
    """adapter.get_field_value side_effect — reads either the Role or the
    Cluster pseudo-field off the fake footprint."""
    if field_name == ROLE_FIELD_NAME:
        return fp._role
    if field_name == CLUSTER_FIELD_NAME:
        return fp._cluster
    return None


def _get_pads(fp):
    """Pads get sequential numbers 1..N: a candidate with nets [a, b] has
    pad '1' -> a, pad '2' -> b."""
    pads = []
    for i, net in enumerate(fp._nets, start=1):
        p = MagicMock()
        p.number = str(i)
        p.net_name = net
        pads.append(p)
    return pads


def _get_pad_by_number(fp, num):
    return next((p for p in _get_pads(fp) if p.number == str(num)), None)


# ---------------------------------------------------------------------------
# Fixture: the loaded cell + its FPGA_FLASH clone_placement (config-only)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fpga_flash():
    """(cell, clone) for Cell fpga_flash and the FPGA_FLASH clone_placement,
    loaded through the real load_config — no live board, no IPC."""
    cfg, _ctx = load_config(str(_PROFILE))
    cell = cfg.cells[_CELL_NAME]
    clone = next(cp for cp in cfg.clone_placements if cp.cluster == _CLUSTER)
    return cell, clone


# ---------------------------------------------------------------------------
# H1-H4 (3.2): which pad is the signal pad, decided from connectivity
# ---------------------------------------------------------------------------


class TestHypothesesH1ToH4:
    """H1-H4 — the pad roles decided purely from the copper connectivity of the
    EXTRACTED fpga_flash cell. The {FLASH}-family roles all resolve to
    +3V3_FLASH, so a pad whose copper component co-locates with one of them is
    on the RAIL; the other pad is on the SIGNAL. Explicit asserts (with the
    predicted neighbour and joint coordinate) instead of trusting Denis's
    file-read guess."""

    @pytest.mark.parametrize(
        "role,expected_rail_neighbour,expected_joint",
        [
            # Denis's read of the file BEFORE the tests ran: R_FL_HOLD pad 2
            # joins C_OUT_BYPASS pad 2 at the shared point (3.5875, -1.905).
            ("R_FL_HOLD", ("C_OUT_BYPASS", "2"), (3.5875, -1.905)),
            # R_FL_WP pad 2 joins R_PIF pad 2 at (-5.4095, -3.3825).
            ("R_FL_WP", ("R_PIF", "2"), (-5.4095, -3.3825)),
        ],
    )
    def test_pad_2_is_on_the_rail(self, fpga_flash, role, expected_rail_neighbour, expected_joint):
        """H1/H3 (pad '1' = signal, pad '2' = rail) vs H2/H4 (reversed): pad
        '2''s copper component must contain a {FLASH}-family (rail) role's
        segment — and specifically the predicted neighbour, joined at the
        predicted coordinate. If this fails, Denis's prediction was wrong and
        the ACTUAL connectivity wins."""
        cell, _clone = fpga_flash
        components = cell_copper_components(cell)
        rail_roles = _rail_family_roles(cell)

        comp = component_containing(components, role, "2")
        assert comp is not None, f"no copper tagged ({role!r}, pad '2') in cell {_CELL_NAME!r}"
        tags = component_role_pads(comp)
        rail_tags = {(r, p) for (r, p) in tags if r in rail_roles}
        assert rail_tags, (
            f"hypothesis H1/H3 (pad '2' = rail) REJECTED: {role} pad '2' is in a "
            f"copper component with NO {_RAIL_NET}-family (rail) role — "
            f"component tags: {sorted(tags)}"
        )
        assert expected_rail_neighbour in rail_tags, (
            f"pad '2' IS on the rail but not via the predicted neighbour "
            f"{expected_rail_neighbour} — actual rail tags: {sorted(rail_tags)}"
        )
        # The physical joint that makes them one copper node (the "why").
        assert _shared_point_between(comp, role, "2", expected_rail_neighbour) == expected_joint

    @pytest.mark.parametrize("role", ["R_FL_HOLD", "R_FL_WP"])
    def test_pad_1_is_on_the_signal(self, fpga_flash, role):
        """H1/H3 (pad '1' = signal): pad '1''s copper component must contain NO
        {FLASH}-family (rail) role. Together with test_pad_2_is_on_the_rail this
        confirms H1 (R_FL_HOLD) / H3 (R_FL_WP) and rejects H2/H4."""
        cell, _clone = fpga_flash
        components = cell_copper_components(cell)
        rail_roles = _rail_family_roles(cell)

        comp = component_containing(components, role, "1")
        assert comp is not None, f"no copper tagged ({role!r}, pad '1') in cell {_CELL_NAME!r}"
        tags = component_role_pads(comp)
        rail_tags = {(r, p) for (r, p) in tags if r in rail_roles}
        assert not rail_tags, (
            f"hypothesis H2/H4 (pad '1' = rail) would be TRUE: {role} pad '1' is "
            f"co-located with rail role(s) {sorted(rail_tags)}"
        )


# ---------------------------------------------------------------------------
# H5 (3.3): net_template_same_as_role has nothing to fill in this cell
# ---------------------------------------------------------------------------


class TestHypothesisH5:
    """H5 — is net_template_same_as_role applicable for R_FL_HOLD/R_FL_WP? It
    needs a lemma-2-safe sibling role whose OWN net equals the bridging role's
    identifying (signal) net. The {FLASH}-family roles all resolve to the RAIL
    (+3V3_FLASH) — the OTHER side of the bridge — so none of them can fill
    same_as_role for the signal side. Verified here with explicit asserts, not
    by trusting the docstring (TemplateComponentSlot.net_template_same_as_role,
    kicadstamp/config/models.py)."""

    def test_no_sibling_role_resolves_to_either_signal_net(self, fpga_flash):
        """Every role OTHER than R_FL_HOLD/R_FL_WP themselves: its own net
        (net_template resolved via params, else the clone_placement nets entry)
        must differ from both signal nets — otherwise same_as_role WOULD be
        fillable and H5 would be rejected."""
        cell, clone = fpga_flash
        params = clone.params
        signal_nets = {r: clone.nets[r] for r in ("R_FL_HOLD", "R_FL_WP")}

        for slot in cell.components:
            if slot.role in signal_nets:
                continue  # a role cannot be its own same_as_role sibling
            if slot.net_template is not None:
                own_net = resolve_net(slot.net_template, params, {})
            else:
                own_net = clone.nets.get(slot.role)
            for role, signal_net in signal_nets.items():
                assert own_net != signal_net, (
                    f"H5 REJECTED: role {slot.role!r} resolves to the signal net "
                    f"{signal_net!r} of {role} — net_template_same_as_role WOULD "
                    f"be fillable (no longer 'nothing to fill')"
                )

    def test_flash_family_resolves_to_the_rail_not_the_signal(self, fpga_flash):
        """The concrete {FLASH} family: exactly {FLASH, R_PIF, C_OUT_BULK,
        C_OUT_BYPASS}, each resolving via params to +3V3_FLASH — the RAIL,
        which is the WRONG side for same_as_role of R_FL_HOLD/R_FL_WP (their
        identifying net is the signal)."""
        cell, clone = fpga_flash
        rail_roles = _rail_family_roles(cell)
        assert rail_roles == {"FLASH", "R_PIF", "C_OUT_BULK", "C_OUT_BYPASS"}
        for role in sorted(rail_roles):
            slot = next(s for s in cell.components if s.role == role)
            assert resolve_net(slot.net_template, clone.params, {}) == _RAIL_NET
        assert _RAIL_NET not in (clone.nets["R_FL_HOLD"], clone.nets["R_FL_WP"])


# ---------------------------------------------------------------------------
# 3.4 End-to-end through the REAL resolver (no re-implementation)
# ---------------------------------------------------------------------------


class TestSuggestRoleNetsEndToEnd:
    """3.4 — the real suggest_role_nets_from_cluster, on the SAME mock data
    shape the live board presents today: R_FL_HOLD/R_FL_WP as bridging
    footprints with the signal pad (discovered in 3.2) carrying /FPGA/FL_HOLD
    or /FPGA/FL_WP and the other pad on +3V3_FLASH. With the pad hint it must
    return EXACTLY the ground-truth net from the clone_placement nets: block —
    proving the whole path (pad discovery + existing engine, unchanged) really
    produces the correct answer."""

    def _adapter(self, footprints):
        adapter = MagicMock()
        adapter.get_footprints.return_value = footprints
        adapter.get_field_value.side_effect = _role_or_cluster
        adapter.get_footprint_pads.side_effect = _get_pads
        adapter.get_pad_by_number.side_effect = _get_pad_by_number
        return adapter

    def test_pad_hint_yields_the_ground_truth_signal_nets(self, fpga_flash):
        cell, clone = fpga_flash
        components = cell_copper_components(cell)
        signal_pads = {role: signal_pad_for_role(components, cell, role)
                       for role in ("R_FL_HOLD", "R_FL_WP")}
        # The 3.2 discovery restated: the signal pad of BOTH roles is pad "1".
        assert signal_pads == {"R_FL_HOLD": "1", "R_FL_WP": "1"}

        # Exactly the situation the live board presents today (per H1/H3):
        # pad '1' = signal net, pad '2' = +3V3_FLASH rail.
        footprints = [
            _make_fp("R_FL_HOLD-1", role="R_FL_HOLD", cluster=_CLUSTER,
                     nets=["/FPGA/FL_HOLD", _RAIL_NET]),
            _make_fp("R_FL_WP-1", role="R_FL_WP", cluster=_CLUSTER,
                     nets=["/FPGA/FL_WP", _RAIL_NET]),
        ]
        hints = {role: (pad, None) for role, pad in signal_pads.items()}
        result = suggest_role_nets_from_cluster(self._adapter(footprints), hints, _CLUSTER)
        # Ground truth: the clone_placement nets: block (lines ~390-399).
        assert result == {"R_FL_HOLD": clone.nets["R_FL_HOLD"],
                          "R_FL_WP": clone.nets["R_FL_WP"]}
        assert result == {"R_FL_HOLD": "/FPGA/FL_HOLD", "R_FL_WP": "/FPGA/FL_WP"}


# ---------------------------------------------------------------------------
# H6 (3.5): the pad-instability risk, documented as a test
# ---------------------------------------------------------------------------


class TestHypothesisH6:
    """H6 — the pad-numbering instability risk, DOCUMENTED AS A TEST, not just
    a comment.

    Precedent: R_FB_TOP, found live 2026-08-16 — its identifying net sat on
    pad 2 in one routed instance and pad 1 in another, at IDENTICAL component
    position/orientation (verified geometrically, not a rotation/mirror
    artifact). Reason: for an electrically symmetric 2-pin R/C, which pad ends
    up "1" vs "2" is an arbitrary ROUTING choice made independently per
    instance — see TemplateComponentSlot.net_template_same_as_role's docstring
    (kicadstamp/config/models.py).

    Conclusion recorded here: H1/H3 are confirmed for the CURRENT extracted
    template (tests above), but that is NOT a guarantee for a FUTURE re-extract
    of this cell from the live board. If net_template_pad is ever chosen for
    R_FL_HOLD/R_FL_WP it remains a deliberate, documented trade-off; the
    cross-instance-safe alternative net_template_same_as_role has nothing to
    fill from in this cell (H5). The skipped test below is the reminder of what
    to re-check on re-extract.
    """

    def test_roles_are_electrically_symmetric_two_pin_bridging_parts(self, fpga_flash):
        """The instability risk applies because these ARE electrically
        symmetric 2-pin parts: both pads have their own copper in the cell and
        are NOT shorted together (bridging), and the designated net of each
        role is a signal net (neither the rail nor GND)."""
        cell, clone = fpga_flash
        components = cell_copper_components(cell)
        for role in ("R_FL_HOLD", "R_FL_WP"):
            comp_1 = component_containing(components, role, "1")
            comp_2 = component_containing(components, role, "2")
            assert comp_1 is not None and comp_2 is not None, \
                f"{role} should be a 2-pin part (copper on both pads)"
            # Different copper components => the two pads are NOT shorted in
            # the extracted cell (a bridging part, not a jumper).
            assert component_role_pads(comp_1) != component_role_pads(comp_2)
            # The role's designated net (clone_placement nets:) is the signal,
            # i.e. neither the rail the other pad is on, nor a rule net.
            assert clone.nets[role] not in (_RAIL_NET, "GND")

    @pytest.mark.skip(reason=(
        "Re-verify the signal-pad numbering of R_FL_HOLD/R_FL_WP if Cell "
        "'fpga_flash' is ever re-extracted from the live board: electrically "
        "symmetric 2-pin R/C pad '1' vs '2' is an arbitrary routing choice "
        "(R_FB_TOP precedent 2026-08-16) — H1/H3 hold for the CURRENT "
        "extracted template only, not as a cross-instance guarantee (see the "
        "TestHypothesisH6 docstring)."
    ))
    def test_re_extract_must_recheck_pad_numbering(self):
        """Skipped placeholder — the reminder itself is the point (H6): nothing
        to run now, but the reason string names exactly what must be re-checked
        if Cell fpga_flash is ever re-extracted."""
        raise AssertionError("this test is skipped by design")
