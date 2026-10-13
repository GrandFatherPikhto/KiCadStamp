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

import logging

import pytest
from unittest.mock import MagicMock

from kicadstamp.config import Cell, TemplateComponentSlot, ClonePlacement
from kicadstamp.constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME
from kicadstamp.exceptions import ValidationError
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


def test_foreign_cluster_component_is_named_not_taken():
    """The 'documented residual gap' is CLOSED (2026-10-13, plan
    plan_2026_10_13_cluster_confined_roles, rule 1) — its SUBJECT changes by
    Denis's decision, the fake board stays the same.

    The target cluster (PIF_1V2_VCCINT) has nothing on the board, so the only
    component carrying the role sits in ANOTHER cluster (PIF_2V5_VCCA) — exactly
    the theft the baked +2V5 literal used to commit here. A component of a
    foreign cluster is NEVER taken: the refusal NAMES it together with its own
    cluster, and nothing is moved."""
    fps = [_make_fp("C_A", "C_IN_BULK", ["+2V5"], "PIF_2V5_VCCA")]
    adapter = _adapter(fps)
    clone = ClonePlacement(cluster="PIF_1V2_VCCINT", cell="pif_p2v5_vcca",
                           xy=(0, 0))

    with pytest.raises(ValidationError) as ei:
        resolve_roles_by_nets(adapter, _cell(), clone)
    msg = str(ei.value)
    assert "C_IN_BULK" in msg
    assert "C_A" in msg                  # the near miss is named ...
    assert "PIF_2V5_VCCA" in msg         # ... together with the cluster IT stands in
    assert "PIF_1V2_VCCINT" in msg       # and the cluster the entity asked for


def test_untagged_component_is_named_not_taken():
    """A candidate with an EMPTY Cluster field is not ours either (plan rule 1:
    an entity addresses a CLUSTER, so a part without the tag does not belong to
    it) — and the refusal says so instead of quietly taking it."""
    fps = [_make_fp("C_A", "C_IN_BULK", ["+2V5"], None)]
    adapter = _adapter(fps)
    clone = ClonePlacement(cluster="PIF_1V2_VCCINT", cell="pif_p2v5_vcca",
                           xy=(0, 0))

    with pytest.raises(ValidationError) as ei:
        resolve_roles_by_nets(adapter, _cell(), clone)
    msg = str(ei.value)
    assert "C_A" in msg
    assert "no Cluster field" in msg


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


# ── the OWN-cluster scope of a record with `cluster` (plan 2026-10-13) ──────
#
# Cells К2–К9 of the plan's table. The same fake board every time; what changes
# is how many components of the role carry the clone's cluster and which net
# source (if any) can legitimately separate them.

def _scope_cell(net_template=None):
    return Cell(name="pif_p2v5_vcca", components=[
        TemplateComponentSlot(role="C_IN_BULK", net_template=net_template),
    ])


def test_own_cluster_single_component_decides_over_the_baked_literal(caplog):
    """К2: ONE component of the role inside the own cluster is the answer even
    when the cell's baked literal names a different net — the literal may only
    corroborate (a Log line), it never overrides the component (rule 2). Without
    this, the live regress spoke_vdd_01_mcu (baked +3V3_VDDA against the own
    +3V3_VDD) would refuse a healthy redraw."""
    fps = [_make_fp("C_B", "C_IN_BULK", ["+1V2_VCCINT"], "PIF_1V2_VCCINT")]
    adapter = _adapter(fps)
    clone = ClonePlacement(cluster="PIF_1V2_VCCINT", cell="pif_p2v5_vcca",
                           xy=(0, 0))

    with caplog.at_level(logging.INFO):
        result = resolve_roles_by_nets(adapter, _scope_cell("+2V5"), clone)

    assert result == {"C_IN_BULK": "C_B"}
    assert any("baked net_template" in r.getMessage() for r in caplog.records)


def test_duplicated_refdes_in_own_cluster_is_refused_before_any_narrowing():
    """К3 + the plan's mutation "copies are checked AFTER the cascade": two
    copies of ONE refdes inside the own cluster, at DIFFERENT distances from the
    anchor — proximity must NOT pick the closer copy. A net, a sheet, a
    selection and proximity can never tell two copies of one symbol apart."""
    fps = [
        _make_fp("C31", "C_IN_BULK", ["+1V2_VCCINT"], "PIF_1V2_VCCINT"),
        _make_fp("C31", "C_IN_BULK", ["+1V2_VCCINT"], "PIF_1V2_VCCINT"),
    ]
    fps[0].position = Vector2.from_xy_mm(10.0, 10.0)
    fps[1].position = Vector2.from_xy_mm(90.0, 90.0)
    adapter = _adapter(fps)
    clone = ClonePlacement(cluster="PIF_1V2_VCCINT", cell="pif_p2v5_vcca",
                           xy=(0, 0))

    with pytest.raises(ValidationError) as ei:
        resolve_roles_by_nets(adapter, _scope_cell(), clone,
                              anchor_position=Vector2.from_xy_mm(0.0, 0.0))

    msg = str(ei.value)
    assert "C31" in msg
    assert "delete the extra copy" in msg
    assert "distinct roles" not in msg


def test_distinct_components_of_one_role_need_distinct_roles():
    """К4: several DIFFERENT refdes carry the role inside the own cluster and the
    live net does not separate them — the refusal asks for distinct roles in the
    schematic (the R9/D5 "R_PROT" case), never for "the closest one"."""
    fps = [
        _make_fp("C31", "C_IN_BULK", ["+1V2_VCCINT"], "PIF_1V2_VCCINT"),
        _make_fp("C32", "C_IN_BULK", ["+1V2_VCCINT"], "PIF_1V2_VCCINT"),
    ]
    adapter = _adapter(fps)
    clone = ClonePlacement(cluster="PIF_1V2_VCCINT", cell="pif_p2v5_vcca",
                           xy=(0, 0))

    with pytest.raises(ValidationError) as ei:
        resolve_roles_by_nets(adapter, _scope_cell(), clone)

    msg = str(ei.value)
    assert "C31" in msg and "C32" in msg
    assert "distinct roles" in msg
    assert "delete the extra copy" not in msg


def test_explicit_net_separates_two_components_of_one_role():
    """К5: a REAL net source (explicit nets:) still narrows two components of one
    role INSIDE the own cluster — the deterministic source keeps working."""
    fps = [
        _make_fp("C31", "C_IN_BULK", ["+1V2_VCCINT"], "PIF_1V2_VCCINT"),
        _make_fp("C32", "C_IN_BULK", ["+1V2_FB_OUT"], "PIF_1V2_VCCINT"),
    ]
    adapter = _adapter(fps)
    clone = ClonePlacement(cluster="PIF_1V2_VCCINT", cell="pif_p2v5_vcca",
                           xy=(0, 0), nets={"C_IN_BULK": "+1V2_FB_OUT"})

    assert resolve_roles_by_nets(adapter, _scope_cell(), clone) == {
        "C_IN_BULK": "C32"}


def test_cluster_absent_from_the_board_asks_for_the_tag():
    """К8: the own cluster is on the board NOWHERE — the refusal asks to tag this
    instance's components, instead of silently falling back to a foreign part."""
    fps = [_make_fp("C_A", "C_IN_BULK", ["+2V5"], "PIF_2V5_VCCA")]
    adapter = _adapter(fps)
    clone = ClonePlacement(cluster="PIF_1V2_VCCINT", cell="pif_p2v5_vcca",
                           xy=(0, 0))

    with pytest.raises(ValidationError) as ei:
        resolve_roles_by_nets(adapter, _scope_cell(), clone)

    msg = str(ei.value)
    assert "cluster tag 'PIF_1V2_VCCINT'" in msg
    assert "tag this instance's components" in msg


def test_record_without_cluster_keeps_the_old_behaviour():
    """К9: a record WITHOUT `cluster` (refs/sheet-only addressing) is untouched
    by this rule — its candidates are not filtered by any cluster at all."""
    from kicadstamp.config import CellPlacement

    cell = Cell(name="nested", components=[TemplateComponentSlot(role="X")])
    fps = [_make_fp("A", "X", ["NET1"], "SOME_OTHER_CLUSTER")]
    adapter = _adapter(fps)
    nested = CellPlacement(name="inner", cell="nested", xy=(0, 0),
                           nets={"X": "NET1"})

    assert resolve_roles_by_nets(adapter, cell, nested) == {"X": "A"}
