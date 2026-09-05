#!/usr/bin/env python3
"""Regression: a Cell with a baked LITERAL net_template (captured from its
SOURCE power-rail instance) must auto-resolve for a placement on a DIFFERENT
power rail, picking THAT instance's own cluster-tagged components — never
silently stealing the source instance's components.

Live incident (Denis, 2026-09-05): a new `pif_p1v2_vccint` placement reusing
cell `pif_p2v5_vcca` (whose component slots carry net_template "+2V5" /
"+2V5_VCCA", baked from the +2V5_VCCA instance) grabbed pif_p2v5_vcca_fpga's
components and their nets — the by-nets resolver used the baked +2V5 template
(no clone.nets), found pif_p2v5_vcca_fpga's parts as the ONLY net match and
took them (no ambiguity -> no cluster narrowing).

Fix (auto-nets, derive_role_nets live_pad priority): when the expected net of a
role comes from a LITERAL cell net_template (no {param}, no channel
prefix-remap) AND the placement has its OWN cluster, the resolver first tries
the live target board for that cluster (live_pad). If the cluster has a
deterministic live net for the role, that live net wins over the baked literal;
the literal remains the fallback while the target cluster has nothing on the
board yet. Live net per role -> net matching finds THIS instance's components,
so copper nets (net_from_role) also derive from them.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock

from kicadstamp.config import Cell, TemplateComponentSlot, ClonePlacement
from kicadstamp.constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME
from kicadstamp.placement.services.clone_role_resolver import resolve_roles_by_nets
from kicadstamp.domain.geometry import Vector2, BoardLayer
from kicadstamp.domain.board import Footprint


def _make_fp(ref, role, nets, cluster):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
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


def _cell():
    # Mirrors pif_p2v5_vcca's baked, non-portable component slot net_template.
    return Cell(name="pif_p2v5_vcca", components=[
        TemplateComponentSlot(role="C_IN_BULK", net_template="+2V5"),
    ])


def _two_instances_board():
    """Two physical power-rail instances of the same cell role: the SOURCE
    (+2V5_VCCA) on the baked net +2V5, and a NEW instance (+1V2_VCCINT) on its
    own rail +1V2_VCCINT — the one the resolver must pick for cluster
    PIF_1V2_VCCINT."""
    return [
        _make_fp("C_A", "C_IN_BULK", ["+2V5"], "PIF_2V5_VCCA"),
        _make_fp("C_B", "C_IN_BULK", ["+1V2_VCCINT"], "PIF_1V2_VCCINT"),
    ]


def test_live_pad_wins_over_baked_literal_for_own_cluster():
    """THE regression: a placement on cluster PIF_1V2_VCCINT reusing the
    +2V5-baked cell must resolve to C_B (its own instance), NOT steal C_A (the
    source instance on the baked net)."""
    fps = _two_instances_board()
    adapter = _adapter(fps)
    clone = ClonePlacement(cluster="PIF_1V2_VCCINT", cell="pif_p2v5_vcca",
                           xy=(0, 0))  # no nets: override — the bug case

    result = resolve_roles_by_nets(adapter, _cell(), clone)
    assert result == {"C_IN_BULK": "C_B"}


def test_source_cluster_still_resolves_to_source_instance():
    """Sanity: the original PIF_2V5_VCCA placement keeps resolving to its own
    instance C_A (live net +2V5 equals the baked literal either way)."""
    fps = _two_instances_board()
    adapter = _adapter(fps)
    clone = ClonePlacement(cluster="PIF_2V5_VCCA", cell="pif_p2v5_vcca",
                           xy=(0, 0))

    result = resolve_roles_by_nets(adapter, _cell(), clone)
    assert result == {"C_IN_BULK": "C_A"}


def test_baked_literal_is_fallback_when_target_cluster_absent():
    """If the target cluster has nothing on the board yet, live_pad cannot
    derive a net — the resolver falls back to the baked literal (which then
    still finds the source instance). This preserves today's behaviour and is
    the documented residual gap (no silent guess is made about a net that does
    not exist anywhere)."""
    fps = [_make_fp("C_A", "C_IN_BULK", ["+2V5"], "PIF_2V5_VCCA")]
    adapter = _adapter(fps)
    clone = ClonePlacement(cluster="PIF_1V2_VCCINT", cell="pif_p2v5_vcca",
                           xy=(0, 0))

    result = resolve_roles_by_nets(adapter, _cell(), clone)
    assert result == {"C_IN_BULK": "C_A"}


def _bridging_cell():
    """A cell with a BRIDGING role (ferrite FB between two nodes — its own
    pads carry two different nets, so it has no single lemma-2 net) that names
    a SIBLING role (net_template_same_as_role) whose single net is the rail
    side. Mirrors pif_p2v5_vcca's FB_PI_FLT slot."""
    return Cell(name="pif_p2v5_vcca", components=[
        TemplateComponentSlot(role="FB_PI_FLT", net_template="+2V5",
                              net_template_same_as_role="C_IN_BYPASS"),
    ])


def _bridging_board():
    """Source (+2V5_VCCA) and target (+1V2_VCCINT) instances, each with its own
    ferrite (bridging, two nets) and its rail-side sibling cap on the sibling's
    single net."""
    return [
        _make_fp("FB_A", "FB_PI_FLT", ["+2V5", "+2V5_FB_OUT"], "PIF_2V5_VCCA"),
        _make_fp("C_A", "C_IN_BYPASS", ["+2V5"], "PIF_2V5_VCCA"),
        _make_fp("FB_B", "FB_PI_FLT", ["+1V2_VCCINT", "+1V2_FB_OUT"], "PIF_1V2_VCCINT"),
        _make_fp("C_B", "C_IN_BYPASS", ["+1V2_VCCINT"], "PIF_1V2_VCCINT"),
    ]


def test_bridging_role_live_sibling_net_wins_over_baked_literal():
    """Live repro: pif_p1v2_vccint's FB_PI_FLT kept stealing the source
    instance's ferrite (FB_A) through the baked +2V5 — a bridging role has no
    single lemma-2 net, so a plain live_pad could not derive it. The fix
    resolves it through net_template_same_as_role: the sibling C_IN_BYPASS's
    live net on THIS cluster (+1V2_VCCINT) becomes the expected net and the
    target's own ferrite FB_B is found."""
    adapter = _adapter(_bridging_board())
    clone = ClonePlacement(cluster="PIF_1V2_VCCINT", cell="pif_p2v5_vcca",
                           xy=(0, 0))
    result = resolve_roles_by_nets(adapter, _bridging_cell(), clone)
    assert result == {"FB_PI_FLT": "FB_B"}


def test_bridging_role_source_cluster_still_resolves_to_source():
    """Sanity: the source placement's bridging role still resolves to its own
    ferrite FB_A."""
    adapter = _adapter(_bridging_board())
    clone = ClonePlacement(cluster="PIF_2V5_VCCA", cell="pif_p2v5_vcca",
                           xy=(0, 0))
    result = resolve_roles_by_nets(adapter, _bridging_cell(), clone)
    assert result == {"FB_PI_FLT": "FB_A"}
