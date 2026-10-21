# tests/selection/test_cell_refresh_origin_renamed.py
"""Cells for plan_2026_10_21_cell_refresh_origin_role_renamed: refreshing a cell
"from the selection" when the role that used to be the cell's ORIGIN no longer
exists on the board.

The fact (3CH-AWG-TIA-v103, 21.10): the board's role `OSC` was renamed to `XTAL`;
the WHOLE cluster was selected and the refresh refused with "role 'OSC' (the
cell's zero-offset origin) is not in the current selection". Denis's rule: a whole
cluster in the selection means the cell is simply updated — the old origin role's
existence is irrelevant. What must still stop the write is a plan that would leave
NO component at the cell's local (0,0), because the next read refuses such a cell
(`cell_zero_slot_role`) — and silently re-rooting it would shift every instance.

No board, no Qt, no Config: the module's own representation (cell list-of-dicts +
Footprint/Via/Track DTOs + a fake adapter), exactly as
tests/trees/test_cell_geometry_refresh.py builds them.
"""
import pytest

from kicadstamp.cell_frame import frame_for_live_selection, rotate_ydown_mm
from kicadstamp.cell_geometry_refresh import build_refresh_plan
from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError

_LAYER = "F.Cu"


# ── the module's own shapes (same stubs as the refresh cells) ───────────────

def _fp(ref, x_mm, y_mm, angle=0.0):
    return Footprint(ref=ref, uuid=f"uuid-{ref}",
                     position=Vector2.from_xy_mm(x_mm, y_mm),
                     angle_deg=angle, layer=BoardLayer.BL_F_Cu)


def _track(net, x1, y1, x2, y2, width=0.2):
    return Track(uuid=f"t-{net}-{x1}-{x2}", net_name=net,
                 start=Vector2.from_xy_mm(x1, y1), end=Vector2.from_xy_mm(x2, y2),
                 width_mm=width, layer=BoardLayer.BL_F_Cu)


class _Pad:
    def __init__(self, net):
        self.net_name = net


class _RefFP:
    def __init__(self, ref):
        self.ref = ref


class _FakeAdapter:
    """Role reads + pad nets — everything `build_refresh_plan` asks for."""

    def __init__(self, roles, pads=None):
        self.roles = roles
        self.pads = pads or {}

    def get_field_value(self, fp, name):
        assert name == ROLE_FIELD_NAME
        return self.roles.get(fp.ref)

    def get_footprint(self, ref):
        return _RefFP(ref)

    def get_pad_by_number(self, fp, pad):
        net = (self.pads.get(fp.ref) or {}).get(str(pad))
        return _Pad(net) if net is not None else None

    def get_footprint_pads(self, fp):
        return [_Pad(net) for net in (self.pads.get(fp.ref) or {}).values()
                if net is not None]


def _plan(components, footprints, adapter, tracks=(), live_tracks=(), **kw):
    return build_refresh_plan(
        components, [], list(tracks), list(footprints), [], list(live_tracks),
        adapter, add_new_copper=True, remove_missing=True,
        reconcile_components=True, cell_layer=_LAYER, **kw)


# ── 1: the origin role was RENAMED — the plan is built ──────────────────────

# The real cell (config.sexp:5930): XTAL_IN (0, 2.6825), XTAL_OUT (−1.9, 2.7088),
# OSC (nothing written = its local (0,0) origin). On the board OSC is now XTAL.
_LEGACY_COMPONENTS = [
    {"role": "XTAL_IN", "offset_across_mm": 2.6825, "angle_deg": -90.0},
    {"role": "XTAL_OUT", "offset_along_mm": -1.9, "offset_across_mm": 2.7088,
     "angle_deg": -90.0},
    {"role": "OSC", "angle_deg": -180.0},
]


def _renamed_instance():
    return [_fp("XTAL", 100.0, 100.0, angle=-180.0),
            _fp("XTAL_IN", 100.0, 102.6825, angle=-90.0),
            _fp("XTAL_OUT", 98.1, 102.7088, angle=-90.0)]


def _renamed_adapter():
    return _FakeAdapter({"XTAL": "XTAL", "XTAL_IN": "XTAL_IN",
                         "XTAL_OUT": "XTAL_OUT"})


def test_a_renamed_origin_role_no_longer_stops_the_update():
    """The fact itself: the whole cluster is selected, `OSC` is gone, the update
    goes through — and the part that stood on the old origin lands on the cell's
    (0,0) under its NEW role, so the cell stays canonical for the next read."""
    plan = _plan(_LEGACY_COMPONENTS, _renamed_instance(), _renamed_adapter())

    added = {rec["role"]: rec for rec in plan.new_component_records}
    assert set(added) == {"XTAL"}
    assert added["XTAL"]["offset_along_mm"] == 0.0
    assert added["XTAL"]["offset_across_mm"] == 0.0
    assert [rec["role"] for rec in plan.removed_component_records] == ["OSC"]
    kept = {rec["role"]: geo for rec, geo in plan.component_updates}
    assert kept["XTAL_IN"] == {"offset_along_mm": 0.0,
                               "offset_across_mm": 2.6825, "angle_deg": -90.0}
    assert kept["XTAL_OUT"]["offset_along_mm"] == -1.9


def test_the_surrogate_choice_cannot_change_a_rigid_plan():
    """Denis's determinism cell: on a rigid instance ANY matched role may anchor
    the translation — the plan must not depend on WHICH one the host picked (that
    is why the fallback is an answer, not a guess)."""
    matched = [{"role": "A", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
                "angle_deg": 0.0},
               {"role": "B", "offset_along_mm": 3.0, "offset_across_mm": 4.0,
                "angle_deg": 0.0}]
    # A genuinely RIGID copy turned by 90°: B's live delta is the stored one put
    # through the project's own rotation, so any surrogate must give one frame.
    dx, dy = rotate_ydown_mm(3.0, 4.0, 90.0)
    b_live = (100.0 + dx, 100.0 + dy)
    fps = [_fp("A1", 100.0, 100.0, angle=90.0), _fp("B1", *b_live, angle=90.0)]
    role_to_ref = {"A": "A1", "B": "B1"}

    first = frame_for_live_selection(matched, fps, role_to_ref,
                                     ("A", 0.0, 0.0, 100.0, 100.0), (0.0, 0.0),
                                     _LAYER)
    second = frame_for_live_selection(matched, fps, role_to_ref,
                                      ("B", 3.0, 4.0, *b_live), (0.0, 0.0), _LAYER)

    assert (first.placement_origin.x, first.placement_origin.y) == \
        (second.placement_origin.x, second.placement_origin.y)
    assert first.rotation_deg == second.rotation_deg
    assert first.mirror == second.mirror
    assert first.residual_mm == second.residual_mm


# ── 2: the departed role's copper gets the NEW role ─────────────────────────

def test_the_recreated_copper_takes_the_new_role_not_its_neighbour():
    """FINDING of the recon (plan п.4): the re-created record's `net_from_role`
    is decided by a GEOMETRIC tiebreak against the cell's components — with the
    OLD list the new role has no slot, so the tiebreak can only pick a
    neighbour, and the copper would be placed from the wrong pad next time.

    Two single-net roles share the net on purpose: that is the shape where the
    tiebreak decides, and the track lies on the NEW role's place."""
    components = [{"role": "A"}, {"role": "B", "offset_across_mm": 5.0}]
    tracks = [{"start_along_mm": 0.0, "start_across_mm": 0.0,
               "end_along_mm": 0.0, "end_across_mm": 1.0, "width_mm": 0.2,
               "net_from_role": "A", "net_from_role_pad": "1"}]
    footprints = [_fp("A2", 100.0, 100.0), _fp("B", 100.0, 105.0)]
    adapter = _FakeAdapter({"A2": "A2", "B": "B"}, pads={"A2": {"1": "N"},
                                                         "B": {"1": "N"}})

    plan = _plan(components, footprints, adapter, tracks,
                 [_track("N", 100.0, 100.0, 100.0, 101.0)])

    assert len(plan.new_track_records) == 1
    new = plan.new_track_records[0]
    assert new["net_from_role"] == "A2"
    assert new["start_along_mm"] == 0.0 and new["start_across_mm"] == 0.0


def test_the_copper_of_a_role_that_STAYED_keeps_its_own_role():
    """Control for the cell above: an untouched role's copper is matched, not
    re-created — nothing about it changes."""
    components = [{"role": "A"}, {"role": "B", "offset_across_mm": 5.0}]
    tracks = [{"start_along_mm": 0.0, "start_across_mm": 0.0,
               "end_along_mm": 0.0, "end_across_mm": 1.0, "width_mm": 0.2,
               "net_from_role": "B", "net_from_role_pad": "1"}]
    footprints = [_fp("A", 100.0, 100.0), _fp("B", 100.0, 105.0)]
    adapter = _FakeAdapter({"A": "A", "B": "B"}, pads={"A": {"1": "N"},
                                                       "B": {"1": "N"}})

    plan = _plan(components, footprints, adapter, tracks,
                 [_track("N", 100.0, 105.0, 100.0, 106.0)])

    assert plan.new_track_records == []
    assert len(plan.track_updates) == 1
    assert plan.track_updates[0][0] is tracks[0]


# ── 3: nothing lands on the cell's (0,0) — refused in words ─────────────────

def test_a_plan_that_empties_the_origin_is_refused_with_the_read_only_verdict():
    """The old origin's part MOVED: the frame is preserved, so nobody lands on
    (0,0) and the next read would refuse the cell. No silent re-rooting — the
    write is refused, and the footer is the READ-ONLY one (nothing was placed on
    the board, and the config is not at fault)."""
    components = [{"role": "A"}, {"role": "B", "offset_across_mm": 5.0}]
    footprints = [_fp("A2", 102.0, 100.0), _fp("B", 100.0, 105.0)]
    adapter = _FakeAdapter({"A2": "A2", "B": "B"})

    with pytest.raises(ValidationError) as exc:
        _plan(components, footprints, adapter)

    text = str(exc.value)
    assert "no component would stand at the cell's local (0, 0)" in text
    assert "extract the cluster again" in text
    assert "Nothing was placed or modified on the board" in text
    assert "Fix the config" not in text


def test_two_components_on_the_origin_are_refused_too():
    """The other half of the same precondition: the CELL is canonical (one slot
    on (0,0)), but B has MOVED onto the origin, so the plan would leave TWO
    there. Refused, never resolved by picking one."""
    components = [{"role": "A"}, {"role": "B", "offset_across_mm": 5.0}]
    footprints = [_fp("A", 100.0, 100.0), _fp("B", 100.0, 100.0)]
    adapter = _FakeAdapter({"A": "A", "B": "B"})

    with pytest.raises(ValidationError) as exc:
        _plan(components, footprints, adapter)

    assert "2 components would stand at the cell's local (0, 0)" in str(exc.value)
    assert "Nothing was placed or modified on the board" in str(exc.value)


# ── 4: nothing to measure from at all ──────────────────────────────────────

def test_a_selection_with_no_cell_role_says_it_cannot_measure():
    components = [{"role": "A"}, {"role": "B", "offset_across_mm": 5.0}]
    footprints = [_fp("X", 100.0, 100.0)]
    adapter = _FakeAdapter({"X": "X"})

    with pytest.raises(ValidationError) as exc:
        _plan(components, footprints, adapter)

    text = str(exc.value)
    assert "none of the cell's roles is in the current selection" in text
    assert "Nothing was placed or modified on the board" in text


# ── 5: control — the origin IS there, the plan is today's ──────────────────

def test_the_origin_in_the_selection_keeps_the_plan_unchanged():
    """The control the whole plan stands on: with the origin role in the
    selection the frame is anchored on it, every stored offset is reproduced
    exactly, and no component is added or removed."""
    components = [{"role": "A"}, {"role": "B", "offset_along_mm": 1.0}]
    footprints = [_fp("A", 100.0, 100.0), _fp("B", 101.0, 100.0)]
    adapter = _FakeAdapter({"A": "A", "B": "B"})

    plan = _plan(components, footprints, adapter)

    assert plan.new_component_records == []
    assert plan.removed_component_records == []
    kept = {rec["role"]: geo for rec, geo in plan.component_updates}
    assert kept["A"]["offset_along_mm"] == 0.0
    assert kept["A"]["offset_across_mm"] == 0.0
    assert kept["B"]["offset_along_mm"] == 1.0
    assert plan.warnings == []


# ── 6: a v2 cell — the (0,0) rule does not apply ───────────────────────────

_V2_COMPONENTS = [
    {"role": "M", "offset_along_mm": -2.0, "offset_across_mm": 3.0},
    {"role": "A"},
    {"role": "B", "offset_along_mm": 1.0},
]


def test_a_v2_cell_is_exempt_from_the_zero_slot_rule():
    """`anchor_xy` IS the origin of a v2 cell, and the refresh never touches it:
    with the anchor role and one member selected, no component sits on (0,0) —
    and that is fine, because the read of a v2 cell does not ask for a zero
    slot. The frame still lands the mount on its live place."""
    footprints = [_fp("M", 100.0, 100.0), _fp("B", 103.0, 100.0)]
    adapter = _FakeAdapter({"M": "M", "B": "B"})

    plan = _plan(_V2_COMPONENTS, footprints, adapter, origin_role="M")

    kept = {rec["role"]: geo for rec, geo in plan.component_updates}
    assert kept["B"]["offset_along_mm"] == 1.0
    assert [rec["role"] for rec in plan.removed_component_records] == ["A"]


def test_a_v2_cell_survives_its_mount_role_leaving_the_selection():
    """The anchor role gone from the selection: the mount STILL comes from the
    config (it is what the frame subtracts), and the translation is measured from
    another matched role — the same plan, anchored the same way."""
    footprints = [_fp("A", 102.0, 97.0), _fp("B", 103.0, 97.0)]
    adapter = _FakeAdapter({"A": "A", "B": "B"})

    plan = _plan(_V2_COMPONENTS, footprints, adapter, origin_role="M")

    kept = {rec["role"]: geo for rec, geo in plan.component_updates}
    assert kept["A"]["offset_along_mm"] == 0.0
    assert kept["A"]["offset_across_mm"] == 0.0
    assert kept["B"]["offset_along_mm"] == 1.0


# ── 7: the verdict of a READ ────────────────────────────────────────────────

def test_a_role_problem_carries_the_read_only_verdict():
    """The second falsehood of the fact: "Placement stopped, board not modified.
    Fix the config" — nothing is placed here, and the board is what changed."""
    components = [{"role": "A"}, {"role": "B", "offset_along_mm": 1.0}]
    footprints = [_fp("B", 101.0, 100.0)]
    adapter = _FakeAdapter({"B": "B"})

    with pytest.raises(ValidationError) as exc:
        build_refresh_plan(components, [], [], footprints, [], [], adapter)

    text = str(exc.value)
    assert "role 'A' is in the cell but not in the selection" in text
    assert "Nothing was placed or modified on the board" in text
    assert "Fix the config" not in text
