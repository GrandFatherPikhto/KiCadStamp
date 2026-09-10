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

N (2026-09-11, plan plan_2026_09_11_nested_cell_placement_live_read.md): the
last block exercises the nested-CellPlacement read, which DOES take typed
cells (the nested cell's own definitions) — still no Qt, no real board.
"""
import pytest

from kicadstamp.cell_frame import rotate_ydown_mm
from kicadstamp.config import Cell, TemplateComponentSlot
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME

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

def _frame(origin, mount=(0.0, 0.0), rotation_deg=0.0, mirror=False):
    """The cell frame the engine threads through the matchers (J.1) — the very
    object build_refresh_plan builds. theta=0/mirror=False reproduces the
    historical origin-only behaviour exactly."""
    return mod.CellFrame(placement_origin=origin, rotation_deg=rotation_deg,
                         mirror=mirror, mount=mount)


def _match_copper_leftover(vias, tracks, raw_vias, raw_tracks, components=None,
                           footprints=None, adapter=None, leftover_fatal=True,
                           kind="via"):
    """Helper: run _match_copper directly (no GUI) and return the leftover.

    2026-09-10 (J.1): the matchers take the cell FRAME now, not a bare origin.
    The legacy (0,0)-mount tests build the historical theta=0/mirror=False
    frame, which is byte-for-byte the old behaviour."""
    components = components or [{"role": "ORIG"}]
    footprints = footprints or [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = adapter or _FakeAdapter(roles={"R-ORIG": "ORIG"})
    role_to_ref, _m, origin, mount, problems = mod._cell_selection_context(
        components, footprints, adapter, "import")
    if origin is None:
        return [], problems
    frame = _frame(origin, mount)
    if kind == "via":
        updates, probs, leftover = mod._match_copper(
            vias, raw_vias, frame, role_to_ref, adapter, "via",
            leftover_is_fatal=leftover_fatal)
    else:
        updates, probs, leftover, _removed = mod._match_copper(
            tracks, raw_tracks, frame, role_to_ref, adapter, "track",
            leftover_is_fatal=leftover_fatal)
    return leftover, probs + problems


def test_match_copper_leftover_is_fatal_true_yields_problems_not_leftover():
    """Refresh mode (leftover_is_fatal=True) is unchanged: a live via the cell
    does not describe is a collected 'extra copper' problem and leftover is []."""
    components = [{"role": "ORIG"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    role_to_ref, _m, origin, mount, _p = mod._cell_selection_context(
        components, footprints, adapter, "refresh")
    updates, problems, leftover, removed = mod._match_copper(
        [], [_via("GND", 1.0, 1.0)], _frame(origin, mount), role_to_ref,
        adapter, "via", leftover_is_fatal=True)
    assert updates == []
    assert leftover == []
    assert removed == []
    assert any("extra copper" in p for p in problems)


def test_match_copper_leftover_is_fatal_false_returns_leftover():
    """Import mode (leftover_is_fatal=False): the same unclaimed live via is
    RETURNED as leftover (the caller turns it into a NEW record), not a
    problem."""
    components = [{"role": "ORIG"}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    role_to_ref, _m, origin, mount, _p = mod._cell_selection_context(
        components, footprints, adapter, "import")
    updates, problems, leftover, removed = mod._match_copper(
        [], [_via("GND", 1.0, 1.0)], _frame(origin, mount), role_to_ref,
        adapter, "via", leftover_is_fatal=False)
    assert updates == []
    assert problems == []
    assert len(leftover) == 1
    assert removed == []


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


# ── H.1.1 / H.1.2: the copper LAYER is part of track identity ──────────────
#
# plan_2026_09_10_cell_refresh_symmetric_and_no_dialog.md: `_match_copper`
# grouped tracks by net alone, so the F.Cu stub and the B.Cu tracks of one net
# were one group and "nearest" paired across layers. Once an unpaired record is
# DELETED (H.2) that mis-pairing silently rewrote one record and deleted another.

def _track_on(net, x1, y1, x2, y2, layer=BoardLayer.BL_B_Cu, width=0.65):
    return Track(uuid=f"t-{net}-{x1}-{y1}", net_name=net,
                 start=Vector2.from_xy_mm(x1, y1),
                 end=Vector2.from_xy_mm(x2, y2),
                 width_mm=width, layer=layer)


def _refresh_tracks(records, live_tracks, cell_layer="B.Cu", **kw):
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    return build_refresh_plan(components, [], records, footprints, [],
                              live_tracks, adapter, cell_layer=cell_layer, **kw)


def _stub_record(net="/N"):
    return {"net": net, "layer": "F.Cu", "width_mm": 0.254,
            "start_along_mm": 2.135, "start_across_mm": -5.04,
            "end_along_mm": 3.335, "end_across_mm": -5.04}


def test_track_layer_is_part_of_the_grouping_key():
    """H.1.1: an F.Cu record and a B.Cu live track of the SAME net are never
    paired — with a known cell layer they are different groups entirely."""
    records = [_stub_record()]
    live = [_track_on("/N", 2.135, -5.04, 0.8625, -5.04)]
    with pytest.raises(ValidationError) as e:
        _refresh_tracks(records, live)
    assert "1 record(s) in the cell, 0 live item(s)" in str(e.value)


def test_cell_layer_none_keeps_the_historical_net_only_grouping():
    """Regression guarantee: existing callers that pass no cell_layer keep the
    pre-H.1 net-only grouping (the F.Cu record DOES pair with the B.Cu track)."""
    records = [_stub_record()]
    live = [_track_on("/N", 2.135, -5.04, 0.8625, -5.04)]
    plan = _refresh_tracks(records, live, cell_layer=None,
                           remove_missing=True)
    assert len(plan.track_updates) == 1
    assert plan.removed_track_records == []


# ── H.2.1: remove_missing makes Refresh symmetric ──────────────────────────

def test_remove_missing_returns_the_unpaired_record_by_identity():
    stale = _stub_record()
    records = [stale,
               {"net": "/N", "width_mm": 0.65,
                "start_along_mm": 0.0, "start_across_mm": 0.0,
                "end_along_mm": 1.0, "end_across_mm": 0.0}]
    live = [_track_on("/N", 0.0, 0.0, 1.0, 0.0)]
    plan = _refresh_tracks(records, live, remove_missing=True)
    assert len(plan.track_updates) == 1
    assert plan.removed_track_records == [stale]
    assert plan.removed_track_records[0] is stale   # the SAME object


def test_remove_missing_false_is_still_the_count_fatal():
    """The strict default keeps today's collected fatal, text unchanged."""
    records = [_stub_record(),
               {"net": "/N", "width_mm": 0.65,
                "start_along_mm": 0.0, "start_across_mm": 0.0,
                "end_along_mm": 1.0, "end_across_mm": 0.0}]
    live = [_track_on("/N", 0.0, 0.0, 1.0, 0.0)]
    with pytest.raises(ValidationError) as e:
        _refresh_tracks(records, live)          # remove_missing defaults False
    assert "record(s) in the cell" in str(e.value)


def test_remove_missing_and_add_new_copper_in_one_plan():
    """Both switches at once: the stale record goes, the undescribed live item
    becomes a NEW record — and no live item is ever both paired and new."""
    stale = _stub_record("/N")
    live = [_track_on("/M", 5.0, 5.0, 6.0, 5.0)]
    plan = _refresh_tracks([stale], live, remove_missing=True,
                           add_new_copper=True)
    assert plan.removed_track_records == [stale]
    assert len(plan.new_track_records) == 1
    assert plan.track_updates == []


def test_role_mismatch_is_still_fatal_in_symmetric_mode():
    """H.2.2: the symmetric COMPONENT role match stays a hard fatal even with
    remove_missing=True — it is what catches a partial/foreign selection BEFORE
    any copper is deleted."""
    components = [
        {"role": "ORIG", "offset_along_mm": 0.0, "offset_across_mm": 0.0},
        {"role": "CAP", "offset_along_mm": 1.0, "offset_across_mm": 1.0},
    ]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]      # CAP not selected
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    with pytest.raises(ValidationError) as e:
        build_refresh_plan(components, [], [], footprints, [], [], adapter,
                           remove_missing=True)
    assert "'CAP'" in str(e.value)


# ── H.1.2: a NEW track record must not lose its layer ──────────────────────

def test_new_track_record_keeps_the_other_layer():
    """B.Cu cell + a live F.Cu track -> the NEW record carries layer: F.Cu."""
    live = [_track_on("/N", 1.0, 1.0, 2.0, 1.0, layer=BoardLayer.BL_F_Cu)]
    plan = _import_case(live, cell_layer="B.Cu")
    assert plan.new_track_records[0]["layer"] == "F.Cu"


def test_new_track_record_omits_the_cell_layer():
    """F.Cu cell + the same live F.Cu track -> no `layer` key at all (the
    extractor's own rule: only a DIFFERENT layer is written)."""
    live = [_track_on("/N", 1.0, 1.0, 2.0, 1.0, layer=BoardLayer.BL_F_Cu)]
    plan = _import_case(live, cell_layer="F.Cu")
    assert "layer" not in plan.new_track_records[0]


def test_refresh_add_new_copper_also_keeps_the_other_layer():
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    live = [_track_on("/N", 1.0, 1.0, 2.0, 1.0, layer=BoardLayer.BL_F_Cu)]
    plan = build_refresh_plan(components, [], [], footprints, [], live, adapter,
                              add_new_copper=True, cell_layer="B.Cu")
    assert plan.new_track_records[0]["layer"] == "F.Cu"


def _import_case(live_tracks, cell_layer):
    components = [{"role": "ORIG", "offset_along_mm": 0.0,
                   "offset_across_mm": 0.0}]
    footprints = [_fp("R-ORIG", "ORIG", 0.0, 0.0)]
    adapter = _FakeAdapter(roles={"R-ORIG": "ORIG"})
    return build_import_plan(components, [], [], footprints, [],
                             live_tracks, adapter, cell_layer=cell_layer)


# ── J.1 (2026-09-10, plan marker_frame_and_sheets): the refresh expresses the
#    live geometry in the CELL's own frame ──────────────────────────────────
#
# Denis, 2026-09-10: "Якорь меняет точку монтажа. При чтении и перечтении у
# нас ВСЕГДА 0°." The reader must express the live geometry in the cell's frame,
# with the frame DERIVED FROM THE DATA (stored offsets vs live deltas of every
# matched role at once) — never from a placement record. Before this, the
# placement's rotation was baked INTO the cell and applied a second time by the
# next Redraw.

# The measured cell (plan's "до" column, pif_oa_n2v5 with placement rotation
# 270, mirror=False): (role, offset_along_mm, offset_across_mm, angle_deg).
_MEASURED_270 = [
    ("C_IN_BULK", 0.0, 0.0, -90.0),
    ("C_OUT_BULK", -3.37, 0.0, -90.0),
    ("C_OUT_BYPASS", -5.05, -0.295, -90.0),
    ("C_IN_BYPASS", -1.64, -0.295, -90.0),
    ("FB_PI_FLT", -2.1283, -2.55, 180.0),
]
_MEASURED_ORIGIN_MM = (120.0, 60.0)


def _norm(angle):
    """(-180, 180] — the engine's own angle normalisation."""
    return (angle + 180.0) % 360.0 - 180.0


def _spec_components(spec):
    return [{"role": role, "offset_along_mm": along,
             "offset_across_mm": across, "angle_deg": angle}
            for role, along, across, angle in spec]


def _live_cluster(spec, theta, mirror=False, mount=(0.0, 0.0),
                  origin_mm=_MEASURED_ORIGIN_MM):
    """The live footprints of a placement of `spec`, rotated by theta (and
    optionally mirrored) — the FORWARD mapping of apply_clone_geometry: rotate
    the stored offset about the mount, flip X when mirrored, translate to the
    placement origin; angles follow comp_angle (angle + theta, mirrored rule).
    The surrogate role (stored (0,0)) lands exactly on the placement origin."""
    from kicadstamp.cell_frame import rotate_ydown_mm
    fps = []
    for role, along, across, angle in spec:
        rx, ry = rotate_ydown_mm(along - mount[0], across - mount[1], theta)
        if mirror:
            rx = -rx
        live_angle = (angle + theta) % 360.0 if not mirror \
            else (180.0 - (angle + theta)) % 360.0
        fps.append(_fp(f"R-{role}", role, origin_mm[0] + rx, origin_mm[1] + ry,
                       live_angle))
    return fps


def _refresh_spec(spec, theta, mirror=False, origin_role="C_IN_BULK", **kw):
    """A full round trip: the SAME spec re-read from its own rotated/mirrored
    live cluster. mount == the surrogate's stored offset (0,0 here), exactly as
    _cell_selection_context defines it."""
    components = _spec_components(spec)
    footprints = _live_cluster(spec, theta, mirror)
    adapter = _FakeAdapter(roles={f"R-{role}": role for role, *_rest in spec})
    return build_refresh_plan(components, [], [], footprints, [], [], adapter,
                              origin_role=origin_role, **kw)


def _geo_by_role(pairs):
    return {rec["role"]: geo for rec, geo in pairs}


def test_refresh_round_trip_rotation_270_is_idempotent():
    """THE main J.1 guarantee: a cell standing in a 270° placement re-reads to
    its OWN offsets and angles — the placement's rotation is not baked in and
    the next Redraw does not turn the cell again."""
    plan = _refresh_spec(_MEASURED_270, 270.0)
    geo = _geo_by_role(plan.component_updates)
    assert set(geo) == {role for role, *_ in _MEASURED_270}
    for role, along, across, angle in _MEASURED_270:
        assert geo[role]["offset_along_mm"] == pytest.approx(along, abs=1e-4)
        assert geo[role]["offset_across_mm"] == pytest.approx(across, abs=1e-4)
        assert _norm(geo[role]["angle_deg"]) == pytest.approx(_norm(angle),
                                                              abs=1e-6)


def test_refresh_round_trip_zero_rotation_matches_the_old_behaviour():
    """Regression guarantee: an unrotated instance produces exactly the
    historical live-minus-origin offsets and the live angle as-is."""
    plan = _refresh_spec(_MEASURED_270, 0.0)
    geo = _geo_by_role(plan.component_updates)
    for role, along, across, angle in _MEASURED_270:
        assert geo[role]["offset_along_mm"] == pytest.approx(along, abs=1e-4)
        assert geo[role]["offset_across_mm"] == pytest.approx(across, abs=1e-4)
        assert _norm(geo[role]["angle_deg"]) == pytest.approx(_norm(angle),
                                                              abs=1e-6)
    assert plan.warnings == []


def test_refresh_round_trip_mirrored_is_idempotent():
    """The same idempotency for a MIRRORED instance: theta=90 + mirror=True
    re-reads to the stored offsets and angles."""
    plan = _refresh_spec(_MEASURED_270, 90.0, mirror=True)
    geo = _geo_by_role(plan.component_updates)
    for role, along, across, angle in _MEASURED_270:
        assert geo[role]["offset_along_mm"] == pytest.approx(along, abs=1e-4)
        assert geo[role]["offset_across_mm"] == pytest.approx(across, abs=1e-4)
        assert _norm(geo[role]["angle_deg"]) == pytest.approx(_norm(angle),
                                                              abs=1e-6)


def test_refresh_never_writes_an_anchor_key():
    """anchor_xy is not part of the refresh AT ALL: the plan's geometric dicts
    carry only offsets/angle for components, nothing anchor-shaped, so an
    anchored cell's frame cannot drift on re-read (J.1)."""
    plan = _refresh_spec(_MEASURED_270, 270.0)
    for record, geo in plan.component_updates:
        assert set(geo) == {"offset_along_mm", "offset_across_mm", "angle_deg"}
        assert "anchor_xy" not in record


def test_refresh_turned_instance_reports_the_rotation_in_warnings():
    """A turned instance is NOT an error — but the GUI needs to say what
    happened (the geometry was expressed in the cell's frame; anchor untouched)."""
    plan = _refresh_spec(_MEASURED_270, 270.0)
    assert len(plan.warnings) == 1
    # The angle is reported in the project's (-180, 180] convention, so a 270°
    # placement reads as -90° (the same rotation).
    assert "-90" in plan.warnings[0]
    assert "anchor_xy" in plan.warnings[0]


def test_refresh_moved_slot_lands_in_new_offsets_and_warns():
    """A slot that genuinely moved on the board (~0.8 mm, the plan's FB_PI_FLT
    case) honestly lands in NEW offsets; every other slot keeps its stored
    offset, and the non-rigid cluster is reported as a warning instead of being
    silently 'straightened'."""
    theta = 270.0
    components = _spec_components(_MEASURED_270)
    footprints = _live_cluster(_MEASURED_270, theta)
    shift_mm = 0.8
    for fp in footprints:
        if fp.ref == "R-FB_PI_FLT":
            fp.position = Vector2.from_xy_mm(fp.position.x / 1_000_000 + shift_mm,
                                             fp.position.y / 1_000_000)
    adapter = _FakeAdapter(roles={f"R-{role}": role for role, *_rest in _MEASURED_270})

    plan = build_refresh_plan(components, [], [], footprints, [], [], adapter,
                              origin_role="C_IN_BULK")
    geo = _geo_by_role(plan.component_updates)

    # The moved slot: its live point moved +0.8 mm along the BOARD's X — in the
    # CELL's frame (this placement is turned 270°) that is -0.8 mm across.
    assert geo["FB_PI_FLT"]["offset_along_mm"] == pytest.approx(-2.1283, abs=1e-3)
    assert geo["FB_PI_FLT"]["offset_across_mm"] == pytest.approx(-2.55 - shift_mm,
                                                                 abs=1e-3)
    # Everyone else keeps exactly what the cell already said — the frame is
    # snapped to the orthogonal grid, so a neighbour's move does not skew them.
    for role, along, across, _angle in _MEASURED_270:
        if role == "FB_PI_FLT":
            continue
        assert geo[role]["offset_along_mm"] == pytest.approx(along, abs=1e-4)
        assert geo[role]["offset_across_mm"] == pytest.approx(across, abs=1e-4)
    # Honest report: a non-rigid cluster (plus the rotation line).
    assert len(plan.warnings) == 2
    assert "not a rigid copy" in plan.warnings[0]
    assert "0.8" in plan.warnings[0]


def test_import_from_a_rotated_instance_uses_the_cell_frame():
    """Import (the additive twin) shares the SAME frame: a via sitting at the
    placement origin + 1 mm along the board's X lands in the cell's frame at
    (1, 0) rotated back by 270 -> (0, -1) with a negative X flip for mirror."""
    components = _spec_components(_MEASURED_270)
    footprints = _live_cluster(_MEASURED_270, 270.0)
    adapter = _FakeAdapter(roles={f"R-{role}": role for role, *_rest in _MEASURED_270})
    live = [_via("GND", _MEASURED_ORIGIN_MM[0] + 1.0, _MEASURED_ORIGIN_MM[1])]

    plan = build_import_plan(components, [], [], footprints, live, [], adapter)
    rec = plan.new_via_records[0]
    assert rec["offset_along_mm"] == pytest.approx(0.0, abs=1e-4)
    assert rec["offset_across_mm"] == pytest.approx(-1.0, abs=1e-4)


# ═══════════════════════════════════════════════════════════════════════════
# N (2026-09-11): a nested CellPlacement re-read from the live board
# (plan plan_2026_09_11_nested_cell_placement_live_read.md §N)
# ═══════════════════════════════════════════════════════════════════════════

_PARENT_COMPS = [
    {"role": "PORIG", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
     "angle_deg": 0.0},
    {"role": "PCAP", "offset_along_mm": 10.0, "offset_across_mm": -4.0,
     "angle_deg": 0.0},
]
# The nested placement's cell — its role names are deliberately DISJOINT from
# the parent's (the collision case has its own test and is refused).
_NESTED_SLOTS = [("NORIG", 0.0, 0.0), ("NCAP", 10.0, -4.0)]


def _nested_cell(name="nested_cell", slots=None, layer="F.Cu"):
    return Cell(name=name, layer=layer, components=[
        TemplateComponentSlot(role=role, offset_along_mm=along,
                              offset_across_mm=across, angle_deg=0.0)
        for role, along, across in (slots or _NESTED_SLOTS)])


class _NestedBoardAdapter(_FakeAdapter):
    """_FakeAdapter + the board-wide surface resolve_roles_by_nets needs
    (get_footprints / get_selected_items / the Cluster field)."""

    def __init__(self, footprints, roles, clusters=None):
        super().__init__(roles=roles, pads={})
        self._footprints = list(footprints)
        self.clusters = clusters or {}

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return self.roles.get(fp.ref)
        if name == CLUSTER_FIELD_NAME:
            return self.clusters.get(fp.ref)
        return None

    def get_footprints(self):
        return list(self._footprints)

    def get_selected_items(self):
        return []


def _fp_on(ref, role, x_mm, y_mm, angle=0.0, layer=BoardLayer.BL_F_Cu):
    return Footprint(ref=ref, uuid=f"uuid-{ref}",
                     position=Vector2.from_xy_mm(x_mm, y_mm),
                     angle_deg=angle, layer=layer)


def _world(origin_mm, theta, slots, mirror=False):
    """The live world positions of a rigid placement of a cell whose slots are
    `slots` = [(role, along, across)] at `origin_mm`, rotated by theta and
    x-flipped when the instance is MIRRORED — the exact composition
    apply_clone_geometry performs."""
    placed = []
    for role, along, across in slots:
        dx, dy = rotate_ydown_mm(along, across, theta)
        if mirror:
            dx = -dx
        placed.append((role, origin_mm[0] + dx, origin_mm[1] + dy))
    return placed


def _rigid_board(parent_origin=(100.0, 200.0), parent_theta=0.0,
                 nested_xy=(3.0, 2.0), nested_theta=0.0,
                 parent_layer=BoardLayer.BL_F_Cu, nested_layer=BoardLayer.BL_F_Cu,
                 parent_mirror=False, cluster="CL"):
    """A live rigid composite: the parent cell's own roles + one nested
    placement's roles. Returns (selection_fps, board_fps, adapter) — the
    selection deliberately holds ONLY the parent's own roles (today's mandatory
    contract for Update from selection; the nested content is resolved
    board-wide, exactly as Apply resolves it)."""
    parent_slots = [(c["role"], c["offset_along_mm"], c["offset_across_mm"])
                    for c in _PARENT_COMPS]
    parent_world = _world(parent_origin, parent_theta, parent_slots,
                          mirror=parent_mirror)
    dx, dy = rotate_ydown_mm(nested_xy[0], nested_xy[1], parent_theta)
    if parent_mirror:
        dx = -dx
    nested_world = _world((parent_origin[0] + dx, parent_origin[1] + dy),
                          parent_theta + nested_theta, _NESTED_SLOTS,
                          mirror=parent_mirror)

    selection = [_fp_on(f"P{i}", role, x, y, parent_theta, parent_layer)
                 for i, (role, x, y) in enumerate(parent_world)]
    nested_fps = [_fp_on(f"N{i}", role, x, y, parent_theta + nested_theta,
                         nested_layer)
                  for i, (role, x, y) in enumerate(nested_world)]
    roles = {}
    clusters = {}
    for fp, (role, _x, _y) in zip(selection + nested_fps,
                                  parent_world + nested_world):
        roles[fp.ref] = role
        clusters[fp.ref] = cluster
    board = selection + nested_fps
    return selection, board, _NestedBoardAdapter(board, roles, clusters)


def _refresh(nested_records, cells, selection, adapter, **kw):
    return build_refresh_plan(_PARENT_COMPS, [], [], selection, [], [], adapter,
                              nested_placements=nested_records, cells=cells,
                              sheet_names={}, **kw)


@pytest.mark.parametrize("parent_theta", [0.0, 90.0, 180.0, -90.0])
def test_nested_read_of_a_still_board_changes_nothing(parent_theta):
    """§N.4.1/§N.4.2: with the nested placement exactly where the live board has
    it, the read returns the SAME xy/rotation — the trivial case is the gate
    against an invented extra shift, and it must hold for a rotated parent too
    (the whole point of expressing xy in the parent's frame)."""
    selection, _board, adapter = _rigid_board(parent_theta=parent_theta,
                                              nested_xy=(3.0, 2.0))
    record = {"name": "n1", "cell": "nested_cell", "xy": [3.0, 2.0]}
    plan = _refresh([record], {"nested_cell": _nested_cell()}, selection, adapter)

    assert plan.nested_updates == []
    assert plan.nested_reports == []
    assert not [w for w in plan.warnings if w.startswith("nested")]


@pytest.mark.parametrize("parent_theta", [0.0, 90.0, 180.0, -90.0])
def test_nested_read_writes_the_parent_local_offset(parent_theta):
    """§N.4.2 (the main test): when the cell's stored xy is WRONG, the read
    writes the position expressed in the parent's frame — re-projecting it by
    the parent's theta (point_to_world) reproduces EXACTLY the live position the
    nested cluster stands at."""
    nested_xy = (3.0, 2.0)
    selection, board, adapter = _rigid_board(parent_theta=parent_theta,
                                             nested_xy=nested_xy)
    record = {"name": "n1", "cell": "nested_cell", "xy": [0.0, 0.0]}
    plan = _refresh([record], {"nested_cell": _nested_cell()}, selection, adapter)

    assert len(plan.nested_updates) == 1
    updated_record, new_geo = plan.nested_updates[0]
    assert updated_record is record
    x, y = new_geo["xy"]
    assert (x, y) == pytest.approx(nested_xy, abs=1e-3)
    assert "rotation_deg" not in new_geo          # 0° is the default (omitted)
    assert "mirror" not in new_geo
    # point_to_world_mm: rotating the stored offset by the PARENT's theta and
    # adding the parent's origin lands exactly on the live nested origin (the
    # N0 footprint of the nested cluster).
    live = next(fp for fp in board if fp.ref == "N0")
    dx, dy = rotate_ydown_mm(x, y, parent_theta)
    assert (100.0 + dx, 200.0 + dy) == pytest.approx(
        (live.position.x / 1e6, live.position.y / 1e6), abs=1e-3)
    assert len(plan.nested_reports) == 1 and "'n1'" in plan.nested_reports[0]


def test_nested_read_captures_the_nested_rotation_and_mirror():
    """§N.4.3/§N.4.4: a nested instance turned 90° relative to the parent stores
    rotation_deg = 90 (the parent frame's own rotation is taken out by
    angle_to_cell), and a nested cluster standing on the BACK side stores
    mirror: True."""
    selection, _board, adapter = _rigid_board(
        parent_theta=180.0, nested_xy=(3.0, 2.0), nested_theta=90.0,
        nested_layer=BoardLayer.BL_B_Cu)
    record = {"name": "n1", "cell": "nested_cell", "xy": [0.0, 0.0]}
    plan = _refresh([record], {"nested_cell": _nested_cell()}, selection, adapter)

    assert len(plan.nested_updates) == 1
    _record, new_geo = plan.nested_updates[0]
    assert new_geo["rotation_deg"] == pytest.approx(90.0, abs=1e-6)
    assert new_geo["mirror"] is True
    assert new_geo["xy"] == pytest.approx([3.0, 2.0], abs=1e-3)


def test_nested_read_is_idempotent_after_applying_the_update():
    """§N.4.4: applying the plan's own update and reading again is a no-op (the
    classic "reread rotates the cell a little more" bug)."""
    selection, _board, adapter = _rigid_board(parent_theta=90.0, nested_xy=(3.0, 2.0))
    record = {"name": "n1", "cell": "nested_cell", "xy": [0.0, 0.0]}
    cells = {"nested_cell": _nested_cell()}

    plan = _refresh([record], cells, selection, adapter)
    assert len(plan.nested_updates) == 1
    for key in ("xy", "rotation_deg", "mirror"):
        record.pop(key, None)
    record.update(plan.nested_updates[0][1])

    second = _refresh([record], cells, selection, adapter)
    assert second.nested_updates == []
    assert second.nested_reports == []


def test_nested_absent_from_the_board_is_reported_and_untouched():
    """§N.4.5: a nested placement whose cluster is not on the live board gets an
    honest Log line and its record is NOT touched (no fatal, no deletion)."""
    selection, _board, adapter = _rigid_board()
    adapter._footprints = list(selection)          # no nested content at all
    record = {"name": "n1", "cell": "nested_cell", "xy": [3.0, 2.0]}
    plan = _refresh([record], {"nested_cell": _nested_cell()}, selection, adapter)

    assert plan.nested_updates == []
    assert record["xy"] == [3.0, 2.0]
    assert any("n1" in w for w in plan.warnings)


def test_nested_sharing_a_role_name_with_the_parent_is_refused():
    """§N.4.6 / §N.1 Q2: when the nested cell reuses one of the PARENT's role
    names the two instances cannot be told apart — the read REFUSES (Log line,
    record untouched) instead of silently writing the parent's geometry."""
    selection, _board, adapter = _rigid_board()
    colliding = _nested_cell(slots=[("NORIG", 0.0, 0.0), ("PORIG", 10.0, -4.0)])
    record = {"name": "n1", "cell": "nested_cell", "xy": [0.0, 0.0]}
    plan = _refresh([record], {"nested_cell": colliding}, selection, adapter)

    assert plan.nested_updates == []
    assert record["xy"] == [0.0, 0.0]
    assert any("shares a role name" in w for w in plan.warnings)


def test_nested_missing_cell_is_reported_and_untouched():
    """An unresolvable definition (the named cell is not in the config) is a Log
    line, not a crash and not a deletion."""
    selection, _board, adapter = _rigid_board()
    record = {"name": "n1", "cell": "ghost", "xy": [0.0, 0.0]}
    plan = _refresh([record], {}, selection, adapter)

    assert plan.nested_updates == []
    assert any("ghost" in w for w in plan.warnings)


def test_mirrored_parent_instance_refuses_the_nested_read():
    """§N.4.3 deviation, documented: a MIRRORED parent cannot legally exist for
    a composite cell (clone_position_calculator refuses to mirror a cell that
    has nested clone_placements), so the relative mirror has no defined
    composition — the read refuses with a Log line rather than inventing one."""
    selection, _board, adapter = _rigid_board(parent_layer=BoardLayer.BL_B_Cu,
                                              parent_mirror=True)
    record = {"name": "n1", "cell": "nested_cell", "xy": [0.0, 0.0]}
    plan = _refresh([record], {"nested_cell": _nested_cell()}, selection, adapter)

    assert plan.nested_updates == []
    assert any("mirrored" in w for w in plan.warnings)


def test_role_only_nested_placement_reads_its_position():
    """§N.1 Q3: a `role:`-only nested placement is covered by the same
    mechanism — its synthesized one-slot cell resolves the role live and the
    position is written in the parent's frame (the rotation comes from that
    component's own live angle, the synthesized slot being at 0°)."""
    selection, _board, adapter = _rigid_board(parent_theta=90.0)
    solo = _fp_on("S1", "SOLO_ROLE", 95.0, 198.0, 90.0)
    adapter._footprints = list(selection) + [solo]
    adapter.roles["S1"] = "SOLO_ROLE"
    record = {"name": "solo", "role": "SOLO_ROLE"}

    plan = _refresh([record], {}, selection, adapter)

    assert len(plan.nested_updates) == 1
    _record, new_geo = plan.nested_updates[0]
    dx, dy = rotate_ydown_mm(new_geo["xy"][0], new_geo["xy"][1], 90.0)
    assert (dx, dy) == pytest.approx((solo.position.x / 1e6 - 100.0,
                                      solo.position.y / 1e6 - 200.0), abs=1e-3)
