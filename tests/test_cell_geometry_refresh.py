# tests/test_cell_geometry_refresh.py
"""
Pure-module tests for kicadstamp/cell_geometry_refresh.py (plan
techdocs/handoff/deepseek/plan_2026_09_03_cell_geometry_refresh.md) — no Qt,
no live board, no Config: the module's matching is exercised against synthetic
Footprint/Via/Track DTOs and cell list-of-dicts, exactly the representation
CellDock keeps in memory.

The adapter is faked to the module's needs: Role reads (get_field_value) and
net_from_role resolution (get_footprint/get_pad_by_number/get_footprint_pads)
— the module never writes anything through it and never receives cfg/Entity.
"""
import pytest

from kicadstamp import cell_geometry_refresh as mod
from kicadstamp.cell_geometry_refresh import (
    RefreshPlan,
    build_refresh_plan,
    cell_zero_slot_role,
    match_components,
    net_template_regex,
)
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError


# ── Synthetic DTO / adapter helpers ─────────────────────────────────────────

def _fp(ref, role, x_mm, y_mm, angle=0.0):
    return Footprint(ref=ref, uuid=f"uuid-{ref}",
                     position=Vector2.from_xy_mm(x_mm, y_mm),
                     angle_deg=angle, layer=BoardLayer.BL_F_Cu)


def _via(net, x_mm, y_mm):
    return Via(uuid=f"v-{net}-{x_mm}-{y_mm}",
               position=Vector2.from_xy_mm(x_mm, y_mm),
               net_name=net, drill_mm=0.3, diameter_mm=0.6)


def _track(net, x1, y1, x2, y2, width=0.25):
    return Track(uuid=f"t-{net}-{x1}-{x2}", net_name=net,
                 start=Vector2.from_xy_mm(x1, y1),
                 end=Vector2.from_xy_mm(x2, y2),
                 width_mm=width, layer=BoardLayer.BL_F_Cu)


class _Pad:
    """A stub pad — resolve_net_from_role only reads .net_name."""
    def __init__(self, net):
        self.net_name = net


class _RefFP:
    """A stub footprint returned by adapter.get_footprint — pad lookups are
    keyed by .ref, so no real pad geometry is needed."""
    def __init__(self, ref):
        self.ref = ref


class _FakeAdapter:
    """Minimal adapter for refresh tests. roles: {ref: role}; pads: {ref:
    {pad_number: net_name}} — enough for get_field_value and for
    net_resolution.resolve_net_from_role (pad + padless paths)."""
    def __init__(self, roles=None, pads=None):
        self.roles = roles or {}
        self.pads = pads or {}

    def get_field_value(self, fp, name):
        return self.roles.get(fp.ref)

    def get_footprint(self, ref):
        return _RefFP(ref)

    def get_pad_by_number(self, fp, pad):
        net = (self.pads.get(fp.ref) or {}).get(str(pad))
        return _Pad(net) if net is not None else None

    def get_footprint_pads(self, fp):
        return [_Pad(net) for net in (self.pads.get(fp.ref) or {}).values()
                if net is not None]


# ── cell_zero_slot_role ────────────────────────────────────────────────────

def test_zero_slot_single_returns_its_role():
    comps = [
        {"role": "ORIG", "offset_along_mm": 0.0, "offset_across_mm": 0.0},
        {"role": "CAP", "offset_along_mm": 1.0, "offset_across_mm": 2.0},
    ]
    assert cell_zero_slot_role(comps) == "ORIG"


def test_zero_slot_missing_offset_keys_default_to_zero():
    """A freshly-authored slot with no offset keys sits at local (0,0) — same
    convention as a freshly-extracted slot that never wrote them."""
    assert cell_zero_slot_role([{"role": "SOLO"}]) == "SOLO"


def test_zero_slot_none_fatal():
    comps = [{"role": "CAP", "offset_along_mm": 1.0, "offset_across_mm": 0.0}]
    with pytest.raises(ValidationError, match="no zero-offset component"):
        cell_zero_slot_role(comps)


def test_zero_slot_multiple_fatal():
    comps = [
        {"role": "A"},
        {"role": "B", "offset_along_mm": 0.0, "offset_across_mm": 0.0},
    ]
    with pytest.raises(ValidationError, match="ambiguous"):
        cell_zero_slot_role(comps)


# ── match_components ───────────────────────────────────────────────────────

def test_match_components_full():
    comps = [{"role": "A", "offset_along_mm": 1.0},
             {"role": "B", "offset_along_mm": 2.0}]
    matched, missing, extra = match_components(comps, {"A": "R1", "B": "R2"})
    assert missing == []
    assert extra == []
    assert [c["role"] for c in matched] == ["A", "B"]


def test_match_components_missing_role():
    comps = [{"role": "A"}, {"role": "B"}]
    _, missing, extra = match_components(comps, {"A": "R1"})
    assert missing == ["B"]
    assert extra == []


def test_match_components_extra_role():
    comps = [{"role": "A"}]
    _, missing, extra = match_components(comps, {"A": "R1", "X": "RX"})
    assert missing == []
    assert extra == ["X"]


# ── net_template_regex ─────────────────────────────────────────────────────

def test_template_regex_matches_concrete_instantiations():
    regex = net_template_regex("/Channel_{channel}/DAC/DB0")
    assert regex.fullmatch("/Channel_3/DAC/DB0")
    assert regex.fullmatch("/Channel_12/DAC/DB0")


def test_template_regex_rejects_wrong_shape():
    regex = net_template_regex("/Channel_{channel}/DAC/DB0")
    # Different static segment.
    assert not regex.fullmatch("/Channel_3/ADC/DB0")
    # Extra segment — ^...$ anchors required.
    assert not regex.fullmatch("/Channel_3/DAC/DB0/extra")
    # '/' inside a placeholder value — a placeholder is ONE net segment.
    assert not regex.fullmatch("/Channel_3/7/DAC/DB0")


# ── build_refresh_plan: full end-to-end ────────────────────────────────────

def test_build_refresh_plan_end_to_end():
    """A tiny synthetic cell whose live counterparts all moved: components
    (incl. the zero-offset origin), one via, one track — every geometric key
    recomputed from the origin, and NOTHING but geometry in the update dicts."""
    components = [
        {"role": "ORIG", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0, "net_template_same_as_role": "CAP"},
        {"role": "CAP", "offset_along_mm": 1.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0, "net_template": "VCC"},
    ]
    vias = [
        {"offset_along_mm": 0.5, "offset_across_mm": 1.5, "net": "GND",
         "drill_mm": 0.3, "diameter_mm": 0.6},
    ]
    tracks = [
        {"start_along_mm": 0.0, "start_across_mm": 0.0,
         "end_along_mm": 2.0, "end_across_mm": 1.0,
         "width_mm": 0.25, "net": "+3V3", "layer": "F.Cu"},
    ]
    footprints = [
        _fp("R-ORIG", "ORIG", 10.0, 10.0, angle=0.0),
        _fp("R-CAP", "CAP", 11.5, 9.0, angle=90.0),
    ]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})

    plan = build_refresh_plan(
        components, vias, tracks,
        footprints,
        [_via("GND", 11.0, 13.0)],
        [_track("+3V3", 12.0, 10.0, 13.0, 11.0, width=0.3)],
        adapter)

    assert isinstance(plan, RefreshPlan)

    assert len(plan.component_updates) == 2
    by_role = {rec["role"]: new for rec, new in plan.component_updates}
    assert by_role["ORIG"] == {"offset_along_mm": 0.0, "offset_across_mm": 0.0,
                               "angle_deg": 0.0}
    assert by_role["CAP"] == {"offset_along_mm": 1.5, "offset_across_mm": -1.0,
                              "angle_deg": 90.0}

    assert len(plan.via_updates) == 1
    rec, new = plan.via_updates[0]
    assert rec is vias[0]
    assert new == {"offset_along_mm": 1.0, "offset_across_mm": 3.0}

    assert len(plan.track_updates) == 1
    trec, tnew = plan.track_updates[0]
    assert trec is tracks[0]
    assert tnew == {"start_along_mm": 2.0, "start_across_mm": 0.0,
                    "end_along_mm": 3.0, "end_across_mm": 1.0,
                    "width_mm": 0.3}

    # update dicts are PURE geometry — no semantic key can be clobbered by a
    # caller's record.update(new_geo), even if one leaked in.
    for _rec, new in (plan.component_updates + plan.via_updates
                      + plan.track_updates):
        assert set(new) <= {"offset_along_mm", "offset_across_mm", "angle_deg",
                            "start_along_mm", "start_across_mm",
                            "end_along_mm", "end_across_mm", "width_mm"}


def test_build_refresh_plan_does_not_mutate_inputs():
    components = [{"role": "ORIG"}, {"role": "CAP", "offset_along_mm": 1.0}]
    vias = [{"offset_along_mm": 0.5, "offset_across_mm": 1.5, "net": "GND"}]
    tracks = [{"start_along_mm": 0.0, "start_across_mm": 0.0,
               "end_along_mm": 2.0, "end_across_mm": 1.0,
               "width_mm": 0.25, "net": "+3V3"}]
    footprints = [
        _fp("R-ORIG", "ORIG", 0.0, 0.0),
        _fp("R-CAP", "CAP", 1.5, 0.0),
    ]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})
    plan = build_refresh_plan(
        components, vias, tracks, footprints,
        [_via("GND", 0.5, 1.5)],
        [_track("+3V3", 0.0, 0.0, 2.0, 1.0)],
        adapter)
    # The records themselves are untouched (updates are separate dicts) —
    # the caller applies record.update(new_geo) later.
    assert components == [{"role": "ORIG"},
                          {"role": "CAP", "offset_along_mm": 1.0}]
    assert vias == [{"offset_along_mm": 0.5, "offset_across_mm": 1.5,
                     "net": "GND"}]
    # And plan records ARE the same dict objects (so the caller's in-place
    # update lands in the loaded cell's lists).
    assert plan.component_updates[0][0] is components[0]


# ── role problems (symmetric, collected) ───────────────────────────────────

def test_missing_cell_role_in_selection_fatal():
    components = [{"role": "ORIG"}, {"role": "CAP", "offset_along_mm": 1.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    with pytest.raises(ValidationError,
                       match="role 'CAP'.*not in the selection"):
        build_refresh_plan(components, [], [], footprints, [], [], adapter)


def test_extra_selection_role_fatal():
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0),
                  _fp("R-X", "X", 5.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG", "R-X": "X"})
    with pytest.raises(ValidationError, match="role 'X'.*not in the cell"):
        build_refresh_plan(components, [], [], footprints, [], [], adapter)


def test_duplicate_role_in_selection_fatal():
    components = [{"role": "ORIG"}]
    footprints = [_fp("R1", "ORIG", 0.0, 0.0), _fp("R2", "ORIG", 5.0, 0.0)]
    adapter = _FakeAdapter(roles={"R1": "ORIG", "R2": "ORIG"})
    with pytest.raises(ValidationError, match="appears twice in selection"):
        build_refresh_plan(components, [], [], footprints, [], [], adapter)


def test_origin_role_not_in_selection_fatal():
    components = [{"role": "ORIG"}, {"role": "CAP", "offset_along_mm": 1.0}]
    footprints = [_fp("R-CAP", "CAP", 5.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-CAP": "CAP"})
    with pytest.raises(ValidationError,
                       match="zero-offset origin.*not in the current selection"):
        build_refresh_plan(components, [], [], footprints, [], [], adapter)


# ── copper tier 2: concrete nets ───────────────────────────────────────────

def test_via_1_to_1_direct_match():
    components = [{"role": "ORIG"}]
    vias = [{"offset_along_mm": 0.5, "offset_across_mm": 0.5, "net": "GND"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    plan = build_refresh_plan(components, vias, [], footprints,
                              [_via("GND", 2.0, 1.0)], [], adapter)
    assert plan.via_updates[0][1] == {"offset_along_mm": 2.0,
                                      "offset_across_mm": 1.0}


def test_via_n_to_n_nearest_wins_over_naive_order():
    """Two vias on the SAME net whose live items are presented in the WRONG
    order — greedy nearest must still pair each cell record with its
    geometrically correct live item (a naive index zip would fail here)."""
    components = [{"role": "ORIG"}]
    vias = [
        {"offset_along_mm": 1.0, "offset_across_mm": 0.0, "net": "GND"},
        {"offset_along_mm": 3.0, "offset_across_mm": 0.0, "net": "GND"},
    ]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    # Live order deliberately swapped relative to the cell order.
    plan = build_refresh_plan(components, vias, [], footprints,
                              [_via("GND", 3.1, 0.05),
                               _via("GND", 1.05, -0.05)], [], adapter)
    by_old = {rec["offset_along_mm"]: new for rec, new in plan.via_updates}
    # record@(1,0) -> live@(1.05,-0.05); record@(3,0) -> live@(3.1,0.05).
    assert by_old[1.0] == {"offset_along_mm": 1.05, "offset_across_mm": -0.05}
    assert by_old[3.0] == {"offset_along_mm": 3.1, "offset_across_mm": 0.05}


def test_net_count_mismatch_fatal_with_both_numbers():
    components = [{"role": "ORIG"}]
    vias = [
        {"offset_along_mm": 0.0, "offset_across_mm": 0.0, "net": "GND"},
        {"offset_along_mm": 1.0, "offset_across_mm": 0.0, "net": "GND"},
    ]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    with pytest.raises(ValidationError, match="'GND'.*2 record.*1 live"):
        build_refresh_plan(components, vias, [], footprints,
                           [_via("GND", 0.0, 0.0)], [], adapter)


def test_existing_net_with_no_live_fatal():
    components = [{"role": "ORIG"}]
    vias = [{"offset_along_mm": 0.0, "offset_across_mm": 0.0, "net": "GND"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    with pytest.raises(ValidationError, match="'GND'.*1 record.*0 live"):
        build_refresh_plan(components, vias, [], footprints, [], [], adapter)


def test_extra_live_net_not_described_fatal():
    """A live via whose net has no cell record at all — extra copper (design
    §2.5), never silently ignored."""
    components = [{"role": "ORIG"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    with pytest.raises(ValidationError, match="extra copper.*GND"):
        build_refresh_plan(components, [], [], footprints,
                           [_via("GND", 1.0, 1.0)], [], adapter)


# ── copper tier 1: parametrized templates ──────────────────────────────────

def test_parametrized_via_matched_by_shape_refreshes_geometry():
    """A via whose net is a parametrized literal ('{channel}' written at
    extract time) matches its live counterpart BY SHAPE — geometry recomputed,
    and the update carries NO net key (the net string is never touched)."""
    components = [{"role": "ORIG"}]
    vias = [{"offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "net": "/Channel_{channel}/DAC/DB0"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    plan = build_refresh_plan(components, vias, [], footprints,
                              [_via("/Channel_3/DAC/DB0", 2.0, 1.0)],
                              [], adapter)
    assert len(plan.via_updates) == 1
    rec, new = plan.via_updates[0]
    assert rec is vias[0]
    assert new == {"offset_along_mm": 2.0, "offset_across_mm": 1.0}
    assert "net" not in new


def test_parametrized_via_does_not_block_neighbour_literal_via():
    """Regression: a parametrized via and a plain literal via in the SAME run
    each match their own live counterpart — the template record neither blocks
    nor distorts the literal one."""
    components = [{"role": "ORIG"}]
    vias = [
        {"offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "net": "/Channel_{channel}/DAC/DB0"},
        {"offset_along_mm": 5.0, "offset_across_mm": 5.0, "net": "GND"},
    ]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    plan = build_refresh_plan(
        components, vias, [], footprints,
        [_via("/Channel_3/DAC/DB0", 1.0, 1.0),
         _via("GND", 6.0, 5.5)], [], adapter)
    assert len(plan.via_updates) == 2
    updates = {rec["net"]: new for rec, new in plan.via_updates}
    assert updates["/Channel_{channel}/DAC/DB0"] == {"offset_along_mm": 1.0,
                                                    "offset_across_mm": 1.0}
    assert updates["GND"] == {"offset_along_mm": 6.0, "offset_across_mm": 5.5}


def test_template_group_count_mismatch_fatal_naming_template():
    """Two cell records with the SAME template but only one live item of that
    shape — the fatal names the TEMPLATE (not a net) and both numbers."""
    components = [{"role": "ORIG"}]
    vias = [
        {"offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "net": "/Channel_{channel}/DAC/DB0"},
        {"offset_along_mm": 1.0, "offset_across_mm": 0.0,
         "net": "/Channel_{channel}/DAC/DB0"},
    ]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    with pytest.raises(
            ValidationError,
            match=r"template '/Channel_\{channel\}/DAC/DB0'.*2 record.*1 live"):
        build_refresh_plan(components, vias, [], footprints,
                           [_via("/Channel_3/DAC/DB0", 0.0, 0.0)],
                           [], adapter)


# ── copper tier 3: net: null elimination ───────────────────────────────────

def test_net_null_matched_by_positional_elimination():
    """Two net:null vias + two leftover live vias whose nets no named record
    claims -> positional match, geometry updated, NO 'net' key (their net
    stays null)."""
    components = [{"role": "ORIG"}]
    vias = [
        {"offset_along_mm": 0.0, "offset_across_mm": 0.0, "net": None},
        {"offset_along_mm": 2.0, "offset_across_mm": 0.0, "net": None},
    ]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    # Leftover live — GND here, but the match is PURE positional.
    plan = build_refresh_plan(components, vias, [], footprints,
                              [_via("GND", 2.2, 0.1), _via("GND", -0.1, 0.1)],
                              [], adapter)
    assert len(plan.via_updates) == 2
    by_old = {rec["offset_along_mm"]: new for rec, new in plan.via_updates}
    assert by_old[0.0] == {"offset_along_mm": -0.1, "offset_across_mm": 0.1}
    assert by_old[2.0] == {"offset_along_mm": 2.2, "offset_across_mm": 0.1}
    for _rec, new in plan.via_updates:
        assert "net" not in new


def test_net_null_count_mismatch_fatal_rule_net():
    components = [{"role": "ORIG"}]
    vias = [{"offset_along_mm": 0.0, "offset_across_mm": 0.0, "net": None}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    with pytest.raises(ValidationError,
                       match="rule-net.*1 record.*0 unclaimed live"):
        build_refresh_plan(components, vias, [], footprints, [], [], adapter)


def test_net_null_never_touches_chains_or_entity():
    """The module takes no cfg/Entity/chains anywhere — build_refresh_plan's
    signature is (lists + footprints + raw items + adapter) only, so the
    net:null elimination is structurally incapable of consulting a Rule/Chain.
    Exercised by calling the public API with a net:null via; any accidental
    Chain dependency would be an AttributeError/TypeError here, not a silent
    wrong net."""
    components = [{"role": "ORIG"}]
    vias = [{"offset_along_mm": 0.0, "offset_across_mm": 0.0, "net": None}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    plan = build_refresh_plan(components, vias, [], footprints,
                              [_via("GND", 1.0, 1.0)], [], adapter)
    assert len(plan.via_updates) == 1
    assert plan.via_updates[0][1] == {"offset_along_mm": 1.0,
                                      "offset_across_mm": 1.0}


# ── copper tier 2: net_from_role ───────────────────────────────────────────

def test_net_from_role_via_resolved_with_correct_role_to_ref(monkeypatch):
    """Regression (plan §3.5): a net_from_role via must be resolved through
    net_resolution.resolve_net_from_role with the role_to_ref the module built
    from the SELECTION — a wrong map (role -> another ref's net) must yield a
    different net and the test must fail."""
    captured = {}

    def fake_resolve(role, pad, role_to_ref, adapter, rule_nets=None):
        captured["role"] = role
        captured["pad"] = pad
        captured["role_to_ref"] = dict(role_to_ref)
        return "VCC_NET"

    monkeypatch.setattr(mod, "resolve_net_from_role", fake_resolve)
    components = [{"role": "ORIG"}, {"role": "CAP", "offset_along_mm": 1.0}]
    vias = [{"offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "net_from_role": "CAP", "net_from_role_pad": "2"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0),
                  _fp("R-CAP", "CAP", 5.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})
    plan = build_refresh_plan(components, vias, [], footprints,
                              [_via("VCC_NET", 1.0, 1.0)], [], adapter)
    assert len(plan.via_updates) == 1
    assert captured == {"role": "CAP", "pad": "2",
                        "role_to_ref": {"ORIG": "R-ORIG", "CAP": "R-CAP"}}
    assert plan.via_updates[0][1] == {"offset_along_mm": 1.0,
                                      "offset_across_mm": 1.0}


def test_net_from_role_real_resolver_live_pads():
    """End-to-end through the REAL resolve_net_from_role: the adapter's pad map
    decides the net, so the live via is matched on the pad's actual net."""
    components = [{"role": "ORIG"}, {"role": "CAP", "offset_along_mm": 1.0}]
    vias = [{"offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "net_from_role": "CAP", "net_from_role_pad": "2"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0),
                  _fp("R-CAP", "CAP", 5.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG", "R-CAP": "CAP"},
                           pads={"R-CAP": {"2": "VCC_NET"}})
    plan = build_refresh_plan(components, vias, [], footprints,
                              [_via("VCC_NET", 1.0, 1.0)], [], adapter)
    assert len(plan.via_updates) == 1
    assert plan.via_updates[0][1] == {"offset_along_mm": 1.0,
                                      "offset_across_mm": 1.0}


# ── track matching ─────────────────────────────────────────────────────────

def test_track_1_to_1_direct_match():
    components = [{"role": "ORIG"}]
    tracks = [{"start_along_mm": 0.0, "start_across_mm": 0.0,
               "end_along_mm": 1.0, "end_across_mm": 1.0,
               "width_mm": 0.25, "net": "+3V3"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    plan = build_refresh_plan(components, [], tracks, footprints, [],
                              [_track("+3V3", 1.0, 0.0, 2.0, 1.0, width=0.4)],
                              adapter)
    assert len(plan.track_updates) == 1
    rec, new = plan.track_updates[0]
    assert rec is tracks[0]
    assert new == {"start_along_mm": 1.0, "start_across_mm": 0.0,
                   "end_along_mm": 2.0, "end_across_mm": 1.0,
                   "width_mm": 0.4}
    assert "net" not in new
