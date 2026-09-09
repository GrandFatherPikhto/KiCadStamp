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
    ImportPlan,
    RefreshPlan,
    build_import_plan,
    build_refresh_plan,
    cell_content_bbox,
    cell_zero_slot_role,
    match_components,
    net_template_regex,
    normalize_cell_anchor_frame,
    resolve_anchor_point,
)
from kicadstamp.config import ClonePlacement, load_cell
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.clone_geometry import apply_clone_geometry


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


def test_build_refresh_plan_role_anchored_frame_preserving():
    """Design_2026_09_05 v2: a role-anchored cell has NO component at the
    stored (0,0). origin_role=MOUNT makes the MOUNT the surrogate and keeps its
    STORED offset (2,1) — the cell's frame/anchor stay truthful — while every
    other element is re-measured relative to the mount's live position."""
    components = [
        {"role": "MOUNT", "offset_along_mm": 2.0, "offset_across_mm": 1.0,
         "angle_deg": 0.0},
        {"role": "CAP", "offset_along_mm": 3.0, "offset_across_mm": 2.0,
         "angle_deg": 0.0},
    ]
    vias = [{"offset_along_mm": 2.5, "offset_across_mm": 1.5, "net": "GND"}]
    footprints = [
        _fp("U-MOUNT", "MOUNT", 12.0, 21.0, angle=0.0),
        _fp("R-CAP", "CAP", 13.5, 23.0, angle=90.0),
    ]
    adapter = _FakeAdapter(roles={"U-MOUNT": "MOUNT", "R-CAP": "CAP"})
    plan = build_refresh_plan(
        components, vias, [], footprints,
        [_via("GND", 12.5, 22.0)], [], adapter,
        origin_role="MOUNT")

    by_role = {rec["role"]: new for rec, new in plan.component_updates}
    # MOUNT keeps its stored offset (2,1) — the frame is preserved, not zeroed.
    assert by_role["MOUNT"] == {"offset_along_mm": 2.0,
                                "offset_across_mm": 1.0, "angle_deg": 0.0}
    # CAP measured from the mount: stored (2,1) + live-relative (1.5, 2.0).
    assert by_role["CAP"] == {"offset_along_mm": 3.5, "offset_across_mm": 3.0,
                              "angle_deg": 90.0}
    # Via measured from the mount: stored (2,1) + live-relative (0.5, 1.0).
    rec, new = plan.via_updates[0]
    assert new == {"offset_along_mm": 2.5, "offset_across_mm": 2.0}


def test_build_refresh_plan_role_anchored_without_origin_role_is_fatal():
    """Without origin_role the legacy zero-slot search runs — a v2 role-anchored
    cell (no component at (0,0)) must fatal honestly, never guess."""
    components = [
        {"role": "MOUNT", "offset_along_mm": 2.0, "offset_across_mm": 1.0,
         "angle_deg": 0.0},
    ]
    footprints = [_fp("U-MOUNT", "MOUNT", 12.0, 21.0, angle=0.0)]
    adapter = _FakeAdapter(roles={"U-MOUNT": "MOUNT"})
    with pytest.raises(ValidationError, match="no zero-offset component"):
        build_refresh_plan(components, [], [], footprints, [], [], adapter)


# ── normalize_cell_anchor_frame (bbox-frame migration, plan S8) ─────────────

def test_normalize_canonical_zero_slot_annotates_role():
    """A canonical cell (content in along>=0/across<=0) whose single component
    sits on stored (0,0) (the legacy zero slot) gains anchor_role identity —
    offsets are NOT touched."""
    entry = {"components": [{"role": "A"}, {"role": "B", "offset_along_mm": 1.0,
                             "offset_across_mm": 0.0}]}
    out, changed = normalize_cell_anchor_frame(entry)
    assert changed is True
    assert out["anchor_role"] == "A"
    assert "anchor_xy" not in out
    by_role = {c["role"]: c for c in out["components"]}
    assert float(by_role["A"].get("offset_along_mm", 0.0)) == 0.0
    assert float(by_role["B"]["offset_along_mm"]) == 1.0


def test_normalize_canonical_default_without_zero_slot_is_noop():
    """A canonical bbox-default cell with no component on (0,0) already lives in
    the right frame — nothing to do."""
    entry = {"components": [
        {"role": "A", "offset_along_mm": 1.0, "offset_across_mm": 0.0},
        {"role": "B", "offset_along_mm": 2.0, "offset_across_mm": -1.0},
    ]}
    out, changed = normalize_cell_anchor_frame(entry)
    assert changed is False
    assert out == entry


def test_normalize_skips_already_anchored_cell():
    entry = {"components": [{"role": "A", "offset_along_mm": 2.0,
                             "offset_across_mm": 1.0}],
             "anchor_role": "A", "anchor_xy": [2.0, 1.0]}
    out, changed = normalize_cell_anchor_frame(entry)
    assert changed is False
    assert out == entry


def test_normalize_non_canonical_reroots_and_records_mount():
    """An interior-mount cell (content straddles (0,0)) is re-rooted to the
    bbox corner; the old mount (component M) becomes anchor_xy + anchor_role."""
    entry = {"components": [
        {"role": "M", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
        {"role": "O", "offset_along_mm": -1.0, "offset_across_mm": 2.0,
         "angle_deg": 90.0},
    ]}
    out, changed = normalize_cell_anchor_frame(entry)
    assert changed is True
    assert out["anchor_role"] == "M"
    assert out["anchor_xy"] == [1.0, -2.0]
    by_role = {c["role"]: c for c in out["components"]}
    assert _mm_eq(by_role["M"]["offset_along_mm"], 1.0)
    assert _mm_eq(by_role["M"]["offset_across_mm"], -2.0)
    assert _mm_eq(by_role["O"]["offset_along_mm"], 0.0)
    assert _mm_eq(by_role["O"]["offset_across_mm"], 0.0)
    # Now canonical: content in along>=0 / across<=0.
    bbox = cell_content_bbox(out)
    assert bbox[0] >= -1e-6 and bbox[3] <= 1e-6


def _mm_eq(a, b, tol=1e-6):
    return abs(float(a) - float(b)) < tol


def test_normalize_preserves_placement_geometry():
    """The whole point of the migration: re-rooting + anchor must NOT move the
    board. Placing the ORIGINAL (mount at (0,0), no anchor) and the NORMALIZED
    cell (anchor on M) with the same clone yields identical world positions."""
    orig = {"components": [
        {"role": "M", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
        {"role": "O", "offset_along_mm": -1.0, "offset_across_mm": 2.0,
         "angle_deg": 90.0},
    ]}
    out, _ = normalize_cell_anchor_frame(orig)
    clone = ClonePlacement(cluster="x", cell="c", xy=(10.0, 20.0))
    roles = {"M": "U1", "O": "R2"}
    layout_old = apply_clone_geometry(clone, load_cell("c", orig), roles)
    layout_new = apply_clone_geometry(clone, load_cell("c", out), roles)
    old = {c.role: c for c in layout_old.components}
    new = {c.role: c for c in layout_new.components}
    for role in ("M", "O"):
        assert old[role].position == new[role].position
        assert old[role].angle_deg == new[role].angle_deg


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


# ── Import vias/tracks from selection (Part B, plan
#    fpga_oscill_missing_copper_and_cell_import) ────────────────────────────

def _match_copper_leftover(vias, tracks, raw_vias, raw_tracks, components=None,
                           footprints=None, adapter=None, leftover_fatal=True,
                           kind="via"):
    """Helper: run _match_copper directly (no GUI) and return the leftover."""
    components = components or [{"role": "ORIG"}]
    footprints = footprints or [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = adapter or _FakeAdapter(roles={"R-ORIG": "ORIG"})
    role_to_ref, _m, origin, problems = mod._cell_selection_context(
        components, footprints, adapter, "import")
    if origin is None:
        return [], problems
    if kind == "via":
        updates, probs, leftover = mod._match_copper(
            vias, raw_vias, origin, role_to_ref, adapter, "via",
            leftover_is_fatal=leftover_fatal)
    else:
        updates, probs, leftover = mod._match_copper(
            tracks, raw_tracks, origin, role_to_ref, adapter, "track",
            leftover_is_fatal=leftover_fatal)
    return leftover, probs + problems


def test_match_copper_leftover_is_fatal_true_yields_problems_not_leftover():
    """Refresh mode (leftover_is_fatal=True) is unchanged: a live via the cell
    does not describe is a collected 'extra copper' problem and leftover is []."""
    components = [{"role": "ORIG"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    role_to_ref, _m, origin, _p = mod._cell_selection_context(
        components, footprints, adapter, "refresh")
    updates, problems, leftover = mod._match_copper(
        [], [_via("GND", 1.0, 1.0)], origin, role_to_ref, adapter, "via",
        leftover_is_fatal=True)
    assert updates == []
    assert leftover == []
    assert any("extra copper" in p for p in problems)


def test_match_copper_leftover_is_fatal_false_returns_leftover():
    """Import mode (leftover_is_fatal=False): the same unclaimed live via is
    RETURNED as leftover (the caller turns it into a NEW record), not a
    problem."""
    components = [{"role": "ORIG"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    role_to_ref, _m, origin, _p = mod._cell_selection_context(
        components, footprints, adapter, "import")
    updates, problems, leftover = mod._match_copper(
        [], [_via("GND", 1.0, 1.0)], origin, role_to_ref, adapter, "via",
        leftover_is_fatal=False)
    assert updates == []
    assert problems == []
    assert len(leftover) == 1


def test_build_import_plan_empty_cell_imports_literal_via():
    """The fpga_oscill case: a cell with components but NO vias/tracks imports
    every live via/track as a NEW record; an unrecognised net stays a literal
    (adapter reports no pads -> classifier falls back to literal)."""
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    plan = build_import_plan(components, [], [], footprints,
                             [_via("SOME_NET", 2.0, 3.0)],
                             [_track("TRACK_NET", 0.0, 0.0, 4.0, 0.0)],
                             adapter)
    assert isinstance(plan, ImportPlan)
    assert len(plan.new_via_records) == 1
    v = plan.new_via_records[0]
    assert v["offset_along_mm"] == 2.0
    assert v["offset_across_mm"] == 3.0
    assert v["net"] == "SOME_NET"  # literal fallback
    assert v["drill_mm"] == 0.3 and v["diameter_mm"] == 0.6
    assert len(plan.new_track_records) == 1
    t = plan.new_track_records[0]
    assert t["start_along_mm"] == 0.0 and t["end_along_mm"] == 4.0
    assert t["width_mm"] == 0.25
    assert t["net"] == "TRACK_NET"


def test_build_import_plan_gnd_becomes_literal_never_none():
    """Import NEVER writes `net: null`: a live via on GND that no selected
    role's pad carries (no pad evidence in the adapter) becomes the plain
    literal 'GND' — the rule-net -> None convention is NOT applied to Import
    (a ClonePlacement/Entity-world feature where via.net=None is fatal always).
    `rule_nets` no longer exists on build_import_plan at all."""
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})  # no pads -> no role on GND
    plan = build_import_plan(components, [], [], footprints,
                             [_via("GND", 1.0, 1.0)], [], adapter)
    assert len(plan.new_via_records) == 1
    rec = plan.new_via_records[0]
    assert rec["net"] == "GND"
    assert "net_from_role" not in rec


def test_build_import_plan_never_writes_net_none_for_any_named_net():
    """Regression (plan 2026_09_04_import_never_writes_null_net): Import NEVER
    emits `net: null`. The recorded bug: GND via/tracks with no selected role
    pad on GND were classified as rule-net -> net None, and Apply/Redraw then
    fatalled (a ClonePlacement via.net=None is FATAL). Now every NON-EMPTY
    live net ends as net_from_role (a selected role genuinely carries it) or
    as a literal net — the resulting record's net specifier is never None.
    The synthetic adapter reports NO pads, so no role touches any net and both
    the GND via and the GND track must take the literal path."""
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})  # no pad evidence at all
    plan = build_import_plan(
        components, [], [], footprints,
        [_via("GND", 1.0, 1.0), _via("+3V3", 2.0, 2.0)],
        [_track("GND", 0.0, 0.0, 3.0, 0.0),
         _track("NET_A", 3.0, 0.0, 5.0, 0.0)],
        adapter)
    # The structural invariant: no new Import record ever carries net None.
    for rec in plan.new_via_records + plan.new_track_records:
        assert rec.get("net") is not None or rec.get("net_from_role") is not None
    assert [r["net"] for r in plan.new_via_records] == ["GND", "+3V3"]
    assert [r["net"] for r in plan.new_track_records] == ["GND", "NET_A"]


def test_build_import_plan_net_from_role_via_gets_role_not_literal(monkeypatch):
    """A live via on a net that one selected role's pad carries is classified
    through the extractor's OWN classifier (_suggest_net_from_role) -> the NEW
    record gets net_from_role, not a literal net."""
    components = [
        {"role": "ORIG", "offset_along_mm": 0.0, "offset_across_mm": 0.0},
        {"role": "CAP", "offset_along_mm": 5.0, "offset_across_mm": 0.0},
    ]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0),
                  _fp("R-CAP", "CAP", 5.0, 0.0)]
    # CAP carries VCC_NET on pad 1 — the classifier must map a VCC_NET via to
    # role CAP (net_from_role), NOT write a literal 'VCC_NET'.
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})

    # Fake the extractor's _selection_role_nets (adapter pads would be empty in
    # this stub) — verify build_import_plan routes through it 1:1.
    monkeypatch.setattr(
        mod, "_selection_role_nets",
        lambda a, fps: {"ORIG": {"1": {"OTHER"}}, "CAP": {"1": {"VCC_NET"}}})
    plan = build_import_plan(components, [], [], footprints,
                             [_via("VCC_NET", 5.0, 0.0)], [], adapter)
    assert len(plan.new_via_records) == 1
    rec = plan.new_via_records[0]
    assert rec["net_from_role"] == "CAP"
    assert "net" not in rec


def test_build_import_plan_never_mutates_existing_records():
    """Import is purely additive: existing vias/tracks dicts are untouched, and
    NEW records are separate dict objects (never references to existing ones).
    Existing records still need their live counterparts in the selection (a
    named net present in the cell but absent live is a tier-2 fatal, NOT
    softened by Import) — so the live items carry both the matching existing
    copper and the genuinely-new copper."""
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    existing_via = {"offset_along_mm": 1.0, "offset_across_mm": 1.0,
                    "net": "GND"}
    existing_track = {"start_along_mm": 0.0, "start_across_mm": 0.0,
                      "end_along_mm": 1.0, "end_across_mm": 1.0,
                      "width_mm": 0.25, "net": "+3V3"}
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    via_snapshot = [dict(existing_via)]
    track_snapshot = [dict(existing_track)]
    plan = build_import_plan(
        components, [existing_via], [existing_track], footprints,
        # Existing GND via's live counterpart + a genuinely-new via.
        [_via("GND", 1.0, 1.0), _via("NEW_NET", 2.0, 2.0)],
        # Existing +3V3 track's live counterpart + a genuinely-new track.
        [_track("+3V3", 0.0, 0.0, 1.0, 1.0),
         _track("NEW_NET", 2.0, 2.0, 3.0, 2.0)],
        adapter)
    # Existing dicts untouched, and only the genuinely-new copper imported.
    assert existing_via == via_snapshot[0]
    assert existing_track == track_snapshot[0]
    assert [r["net"] for r in plan.new_via_records] == ["NEW_NET"]
    assert [r["net"] for r in plan.new_track_records] == ["NEW_NET"]
    # New records are distinct objects, never the existing ones.
    assert plan.new_via_records[0] is not existing_via
    assert plan.new_track_records[0] is not existing_track


def test_build_import_plan_does_not_duplicate_existing_records():
    """An existing via already described by tiers 1-3 (matched to a live item)
    must NOT be imported a SECOND time — only genuinely-unclaimed live copper
    becomes a new record."""
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    existing_via = {"offset_along_mm": 1.0, "offset_across_mm": 1.0,
                    "net": "GND"}
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    # Live has TWO vias on GND: one matches the existing GND record (tier 2,
    # 1:1) — claimed, NOT imported — and one extra GND via... but a count
    # mismatch (1 existing vs 2 live) is a tier-2 FATAL even in import. So use
    # distinct nets to exercise the non-duplication cleanly: existing net is
    # matched and skipped, a net the cell has no record for is imported.
    plan = build_import_plan(
        components, [existing_via], [], footprints,
        [_via("GND", 1.0, 1.0),   # matches existing -> tier 2 claims it
         _via("+3V3_NEW", 5.0, 5.0)],  # no existing record -> imported
        [], adapter)
    assert len(plan.new_via_records) == 1
    assert plan.new_via_records[0]["net"] == "+3V3_NEW"


def test_build_import_plan_missing_role_fatal_like_refresh():
    """Import requires the SAME clean symmetric role match as refresh: a role
    in the cell but absent from the selection is a fatal, not softened."""
    components = [{"role": "ORIG"}, {"role": "CAP", "offset_along_mm": 1.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]  # CAP missing
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    with pytest.raises(ValidationError,
                       match="role 'CAP'.*not in the selection"):
        build_import_plan(components, [], [], footprints,
                          [_via("GND", 1.0, 1.0)], [], adapter)


# ── Refresh add_new_copper: 'Update from selection...' just ADDS the copper
#    (2026-09-05, plan update_from_selection_adds_copper) ────────────────────

def test_refresh_add_new_copper_empty_cell_adds_records_never_fatal():
    """The pif_p5v case (cell read when there was no copper yet; copper drawn
    later): with add_new_copper=True, live via/track copper the cell has NO
    record for becomes NEW records instead of an 'extra copper' fatal — and
    every new record carries a concrete net (never `net: null`)."""
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})  # no pads -> literal nets
    plan = build_refresh_plan(
        components, [], [], footprints,
        [_via("GND", 1.0, 1.0), _via("+5V", 2.0, 2.0)],
        [_track("+5V", 0.0, 0.0, 3.0, 0.0)],
        adapter, add_new_copper=True)
    assert isinstance(plan, RefreshPlan)
    # The zero-slot ORIG component is matched as usual; its live position
    # equals its stored (0,0) so the recomputed geometry is a no-op (the
    # preview omits zero-Δ rows; the plan still carries the update).
    assert len(plan.component_updates) == 1
    assert plan.component_updates[0][1] == {
        "offset_along_mm": 0.0, "offset_across_mm": 0.0, "angle_deg": 0.0}
    assert len(plan.new_via_records) == 2
    assert [r["net"] for r in plan.new_via_records] == ["GND", "+5V"]
    assert plan.new_via_records[0]["offset_along_mm"] == 1.0
    assert plan.new_via_records[1]["offset_across_mm"] == 2.0
    assert len(plan.new_track_records) == 1
    t = plan.new_track_records[0]
    assert t["net"] == "+5V"
    assert t["start_along_mm"] == 0.0 and t["end_along_mm"] == 3.0
    # The structural invariant shared with Import: no NEW record ever net:null.
    for rec in plan.new_via_records + plan.new_track_records:
        assert rec.get("net") is not None or rec.get("net_from_role") is not None


def test_refresh_add_new_copper_mixed_updates_existing_and_adds_only_new():
    """Additive refresh = Refresh + Import in one run: an existing GND via is
    matched and its geometry updated, while only the genuinely-new NEW_NET
    via/track becomes NEW records (the GND via is never duplicated)."""
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    existing_via = {"offset_along_mm": 1.0, "offset_across_mm": 1.0,
                    "net": "GND"}
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    plan = build_refresh_plan(
        components, [existing_via], [], footprints,
        [_via("GND", 1.5, 1.0),        # the existing via's live counterpart
         _via("NEW_NET", 4.0, 4.0)],   # genuinely new
        [_track("NEW_NET", 4.0, 4.0, 6.0, 4.0)],
        adapter, add_new_copper=True)
    # Existing GND via updated (same dict object, geometry recomputed).
    assert len(plan.via_updates) == 1
    rec, new = plan.via_updates[0]
    assert rec is existing_via
    assert new == {"offset_along_mm": 1.5, "offset_across_mm": 1.0}
    # Only the genuinely-new copper is added — GND never duplicated.
    assert [r["net"] for r in plan.new_via_records] == ["NEW_NET"]
    assert [r["net"] for r in plan.new_track_records] == ["NEW_NET"]


def test_refresh_add_new_copper_never_mutates_inputs_and_default_false_empty():
    """add_new_copper never mutates its inputs; NEW records are distinct dict
    objects. Without the flag the new-record fields stay empty (strict
    Refresh's behaviour is untouched — the no-leftover run below is clean)."""
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    existing_via = {"offset_along_mm": 1.0, "offset_across_mm": 1.0,
                    "net": "GND"}
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    via_snapshot = dict(existing_via)
    # Leftover present -> additive plan; inputs untouched, new records distinct.
    plan = build_refresh_plan(
        components, [existing_via], [], footprints,
        [_via("GND", 1.0, 1.0), _via("NEW_NET", 4.0, 4.0)],
        [], adapter, add_new_copper=True)
    assert existing_via == via_snapshot
    assert len(plan.new_via_records) == 1
    assert plan.new_via_records[0] is not existing_via
    # Default False (no flag): same clean run -> no new records, strict shape.
    strict = build_refresh_plan(
        components, [existing_via], [], footprints,
        [_via("GND", 1.0, 1.0)], [], adapter)
    assert strict.new_via_records == [] and strict.new_track_records == []
    assert len(strict.via_updates) == 1


def test_refresh_add_new_copper_role_mismatch_still_fatal():
    """add_new_copper softens ONLY tier 4 (extra copper); the symmetric role
    match stays fatal exactly as strict Refresh and Import."""
    components = [{"role": "ORIG"}, {"role": "CAP", "offset_along_mm": 1.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]  # CAP missing
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    with pytest.raises(ValidationError,
                       match="role 'CAP'.*not in the selection"):
        build_refresh_plan(components, [], [], footprints,
                           [_via("GND", 1.0, 1.0)], [], adapter,
                           add_new_copper=True)


def test_refresh_add_new_copper_net_from_role_via_gets_role_not_literal(
        monkeypatch):
    """A NEW record on a net one selected role's pad carries is classified
    through the extractor's OWN classifier (_suggest_net_from_role) -> the NEW
    record gets net_from_role, not a literal net (same route as Import)."""
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    monkeypatch.setattr(
        mod, "_selection_role_nets",
        lambda a, fps: {"ORIG": {"1": {"GND"}}})
    plan = build_refresh_plan(components, [], [], footprints,
                              [_via("GND", 2.0, 2.0)], [], adapter,
                              add_new_copper=True)
    assert len(plan.new_via_records) == 1
    rec = plan.new_via_records[0]
    assert rec["net_from_role"] == "ORIG"
    assert "net" not in rec


# ── resolve_anchor_point (2026-09-08, plan cell_anchor_take_from_selection) ─

class _AnchorPad:
    """A stub pad with a LIVE absolute position (what get_pad_by_number's real
    Pad carries — the adapter returns pad.position in board coords)."""
    def __init__(self, x_mm, y_mm):
        self.position = Vector2.from_xy_mm(x_mm, y_mm)


class _AnchorAdapter:
    """Minimal adapter for resolve_anchor_point: get_field_value returns the
    role by ref; get_pad_by_number returns a positioned stub pad (by ref and
    number) or None."""
    def __init__(self, roles=None, pads_by_ref=None):
        self.roles = roles or {}
        self.pads_by_ref = pads_by_ref or {}

    def get_field_value(self, fp, name):
        return self.roles.get(fp.ref)

    def get_pad_by_number(self, fp, pad):
        return (self.pads_by_ref.get(fp.ref) or {}).get(str(pad))


def test_resolve_anchor_point_no_pad_is_the_roles_own_stored_centre():
    """pad=None -> the anchor point is the component's OWN stored centre: the
    frame-preserving surrogate formula collapses to (target - origin) = the
    role's stored offset. A centre (role-only) anchor therefore never needs a
    live read beyond the one component itself."""
    fp = _fp("U-MOUNT", "MOUNT", 10.0, 20.0)
    components = [{"role": "MOUNT", "offset_along_mm": -5.05,
                   "offset_across_mm": -0.295}]
    adapter = _AnchorAdapter(roles={"U-MOUNT": "MOUNT"})
    assert resolve_anchor_point(fp, components, adapter) == (
        "MOUNT", -5.05, -0.295)


def test_resolve_anchor_point_with_pad_reads_pad_live_position():
    """pad given -> target = the pad's live absolute position, minus the
    cell's reconstructed live origin: the pad's correct bbox-local point (the
    anchor_xy a v2 Role+Pad anchor needs; closes the yesterday (0,0) bug)."""
    fp = _fp("U-MOUNT", "MOUNT", 10.0, 20.0)
    components = [{"role": "MOUNT", "offset_along_mm": -5.05,
                   "offset_across_mm": -0.295}]
    # Cell local (0,0) lives at (15.05, 20.295); pad "1" sits 3.05/1.295 mm
    # to its lower-left -> bbox-local anchor (-3.05, -1.295).
    adapter = _AnchorAdapter(
        roles={"U-MOUNT": "MOUNT"},
        pads_by_ref={"U-MOUNT": {"1": _AnchorPad(12.0, 19.0)}})
    assert resolve_anchor_point(fp, components, adapter, pad="1") == (
        "MOUNT", -3.05, -1.295)


def test_resolve_anchor_point_footprint_without_role_is_fatal():
    fp = _fp("U-NOROLE", None, 10.0, 20.0)
    components = [{"role": "MOUNT", "offset_along_mm": -5.05,
                   "offset_across_mm": -0.295}]
    adapter = _AnchorAdapter(roles={})
    with pytest.raises(ValidationError, match="no .* field"):
        resolve_anchor_point(fp, components, adapter)


def test_resolve_anchor_point_role_not_a_cell_component_is_fatal():
    fp = _fp("U-OTHER", "OTHER", 10.0, 20.0)
    components = [{"role": "MOUNT", "offset_along_mm": -5.05,
                   "offset_across_mm": -0.295}]
    adapter = _AnchorAdapter(roles={"U-OTHER": "OTHER"})
    with pytest.raises(ValidationError, match="not a component of this cell"):
        resolve_anchor_point(fp, components, adapter)


def test_resolve_anchor_point_missing_pad_is_fatal():
    fp = _fp("U-MOUNT", "MOUNT", 10.0, 20.0)
    components = [{"role": "MOUNT", "offset_along_mm": -5.05,
                   "offset_across_mm": -0.295}]
    adapter = _AnchorAdapter(roles={"U-MOUNT": "MOUNT"}, pads_by_ref={})
    with pytest.raises(ValidationError, match="no pad"):
        resolve_anchor_point(fp, components, adapter, pad="9")


def test_resolve_anchor_point_pif3v3_vdd_shaped_round_trips_through_mount():
    """The live regression shape from the plan (pif_3v3_vdd: Role=C_OUT_BYPASS,
    Pad=1, the role stored at (-5.05,-0.295), the live fp standing at an
    ARBITRARY point): resolve_anchor_point -> the cell_mount_offset of a cell
    written with that anchor_xy (+role+pad) gives EXACTLY the pad's live
    position minus the reconstructed live origin — i.e. the pad really is the
    mount, not the silent (0,0) of yesterday's legacy branch."""
    from kicadstamp.geometry.cell_anchor import cell_mount_offset

    fp = _fp("C-OUT", "C_OUT_BYPASS", 100.0, 55.0)
    components = [{"role": "C_OUT_BYPASS", "offset_along_mm": -5.05,
                   "offset_across_mm": -0.295, "angle_deg": 0.0}]
    # Pad "1" of the live fp at (97.0, 52.5): the anchor it should resolve to.
    adapter = _AnchorAdapter(
        roles={"C-OUT": "C_OUT_BYPASS"},
        pads_by_ref={"C-OUT": {"1": _AnchorPad(97.0, 52.5)}})
    role, along_mm, across_mm = resolve_anchor_point(
        fp, components, adapter, pad="1")
    assert role == "C_OUT_BYPASS"

    # Rebuild a loader-validated Cell from the resolved fields, exactly as
    # CellEditor's _build_cell_dict (mode 2) would write it now.
    cell = load_cell("t", {
        "components": components,
        "anchor_role": role,
        "anchor_pad": "1",
        "anchor_xy": [along_mm, across_mm],
    })
    mount = cell_mount_offset(cell)
    # The mount must NOT be the legacy (0,0): it is the pad's bbox-local point.
    assert mount == (along_mm, across_mm)
    assert mount != (0.0, 0.0)
    # And (mount + reconstructed live origin) == the pad's live position.
    origin_mm = (100.0 + 5.05, 55.0 + 0.295)
    assert (round(origin_mm[0] + mount[0], 4),
            round(origin_mm[1] + mount[1], 4)) == (97.0, 52.5)


def test_resolve_anchor_point_rotated_live_instance_is_unrotated_back():
    """§2b (plan placer_cell_anchor_selection_unify, 2026-09-09): a live
    instance placed at rotation 90 (role stored at (-5.05,-0.295), slot angle
    0) — the old plain-subtraction surrogate (origin = fp.position -
    stored_offset, no rotation inversion) would silently return a WRONG
    anchor. The rotation-aware path must recover the pad's reference-frame
    (-3.05, -1.295) — the SAME bbox-local point an identity instance resolves
    to. World geometry: fp centre = O + R90(s), pad = O + R90(p0) with
    O = (10, 20), R90(x, y) = (y, -x) (real kipy rotate convention)."""
    fp = _fp("U-MOUNT", "MOUNT", 9.705, 25.05, angle=90.0)
    components = [{"role": "MOUNT", "offset_along_mm": -5.05,
                   "offset_across_mm": -0.295, "angle_deg": 0.0}]
    adapter = _AnchorAdapter(
        roles={"U-MOUNT": "MOUNT"},
        pads_by_ref={"U-MOUNT": {"1": _AnchorPad(8.705, 23.05)}})
    role, along_mm, across_mm = resolve_anchor_point(
        fp, components, adapter, pad="1")
    assert role == "MOUNT"
    assert along_mm == pytest.approx(-3.05, abs=1e-6)
    assert across_mm == pytest.approx(-1.295, abs=1e-6)


def test_resolve_anchor_point_mirrored_live_instance_is_unmirrored_back():
    """§2b mirror case: the SAME cell content placed mirrored (fp on B.Cu
    while the cell's own layer is F.Cu, fp angle 180 for a 0-rotation
    mirrored placement). World geometry mirrors every point about the vertical
    axis through O: fp centre = mirror_x(O, O + s), pad = mirror_x(O, O + p0)
    with s = (-5.05,-0.295), p0 = (-3.05,-1.295), O = (10, 20). The resolver
    must still return the reference-frame (-3.05, -1.295)."""
    fp = Footprint(ref="U-MOUNT", uuid="uuid-U-MOUNT",
                   position=Vector2.from_xy_mm(15.05, 19.705),
                   angle_deg=180.0, layer=BoardLayer.BL_B_Cu)
    components = [{"role": "MOUNT", "offset_along_mm": -5.05,
                   "offset_across_mm": -0.295, "angle_deg": 0.0}]
    adapter = _AnchorAdapter(
        roles={"U-MOUNT": "MOUNT"},
        pads_by_ref={"U-MOUNT": {"1": _AnchorPad(13.05, 18.705)}})
    role, along_mm, across_mm = resolve_anchor_point(
        fp, components, adapter, pad="1")
    assert role == "MOUNT"
    assert along_mm == pytest.approx(-3.05, abs=1e-6)
    assert across_mm == pytest.approx(-1.295, abs=1e-6)


def test_resolve_anchor_point_rotation_and_mirror_together():
    """§2b combined rotation+mirror: fp angle = (180 - (slot + R)) % 360, so a
    mirrored 90-rotation instance shows fp angle 90 on B.Cu. World geometry =
    mirror_x(O, O + R90(c)). The resolver must recover the SAME
    reference-frame (-3.05, -1.295) a pure identity instance resolves to."""
    fp = Footprint(ref="U-MOUNT", uuid="uuid-U-MOUNT",
                   position=Vector2.from_xy_mm(10.295, 25.05),
                   angle_deg=90.0, layer=BoardLayer.BL_B_Cu)
    components = [{"role": "MOUNT", "offset_along_mm": -5.05,
                   "offset_across_mm": -0.295, "angle_deg": 0.0}]
    adapter = _AnchorAdapter(
        roles={"U-MOUNT": "MOUNT"},
        pads_by_ref={"U-MOUNT": {"1": _AnchorPad(11.295, 23.05)}})
    role, along_mm, across_mm = resolve_anchor_point(
        fp, components, adapter, pad="1")
    assert role == "MOUNT"
    assert along_mm == pytest.approx(-3.05, abs=1e-6)
    assert across_mm == pytest.approx(-1.295, abs=1e-6)


