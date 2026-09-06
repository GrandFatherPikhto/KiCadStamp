# tests/test_cell_placement_copy.py
"""Config-level tests for the OFFLINE "Copy placement from cell..." core
(kicadstamp/cell_placement_copy.py, plan
techdocs/handoff/deepseek/plan_2026_09_06_copy_placement_from_cell.md).

The fixture pair mirrors the real 2026-09-06 scenario: a +5V donor cell
(pif_p5v-like — components + full net_from_role copper) copied into an
identical-geometry -5V target (pif_n5v-like — components, no copper). The copy
must overlay component GEOMETRY by role (never the target's own net_template)
and APPEND the donor's vias/tracks as-is; nets are role-relative (net_from_role)
so they re-resolve on the target instance at apply time (net_autoresolve §4.1).
"""
import copy

import pytest

from kicadstamp.cell_placement_copy import (
    PlacementCopyPlan,
    build_placement_copy_plan,
)
from kicadstamp.exceptions import ValidationError


def _source():
    return [
        {"role": "C_IN_BYPASS", "offset_along_mm": 4.5, "offset_across_mm": -2.345,
         "angle_deg": -90.0, "net_template": "+5V_DIRTY"},
        {"role": "C_OUT_BYPASS", "offset_along_mm": 9.8, "offset_across_mm": -2.345,
         "angle_deg": -90.0, "net_template": "+5V"},
        {"role": "C_IN_BULK", "angle_deg": -90.0, "net_template": "+5V_DIRTY"},
        {"role": "C_OUT_BULK", "offset_along_mm": 7.0, "offset_across_mm": -2.345,
         "angle_deg": -90.0, "net_template": "+5V"},
    ]


def _source_vias():
    return [
        {"offset_along_mm": 4.5, "offset_across_mm": 0.2925, "drill_mm": 0.4,
         "diameter_mm": 0.8, "net_from_role": "C_IN_BYPASS",
         "net_from_role_pad": "2"},
    ]


def _source_tracks():
    return [
        {"start_along_mm": 7.0, "start_across_mm": -1.4825,
         "end_along_mm": 9.8, "end_across_mm": -1.4825, "width_mm": 0.8,
         "net_from_role": "C_OUT_BULK", "net_from_role_pad": "2"},
    ]


def _target():
    """The pif_n5v-like target: SAME component geometry/roles as the source but
    its own (-5V) net_templates and NO copper."""
    comps = copy.deepcopy(_source())
    for c in comps:
        c["net_template"] = c["net_template"].replace("+5V", "-5V")
    return comps


# ── Happy path ─────────────────────────────────────────────────────────────

def test_identical_geometry_overlay_is_noop_and_copper_appended():
    target = _target()
    plan = build_placement_copy_plan(_source(), _source_vias(), _source_tracks(), target)

    assert isinstance(plan, PlacementCopyPlan)
    # identical donor geometry -> no component updates
    assert plan.component_updates == []
    # copper deep-copied and appended (never the donor's own dict objects)
    assert [v["net_from_role"] for v in plan.new_via_records] == ["C_IN_BYPASS"]
    assert len(plan.new_track_records) == 1
    assert plan.new_via_records[0] is not _source_vias()[0]
    assert plan.new_track_records[0] is not _source_tracks()[0]
    assert plan.skipped_roles == []
    # inputs untouched
    assert target == _target()


def test_component_overlay_updates_geometry_keeps_net_template():
    target = _target()
    for c in target:
        if c["role"] == "C_OUT_BULK":
            c["offset_along_mm"] = 99.0  # wrong, donor says 7.0
    plan = build_placement_copy_plan(_source(), _source_vias(), _source_tracks(), target)

    assert len(plan.component_updates) == 1
    slot, new_geo = plan.component_updates[0]
    assert slot["role"] == "C_OUT_BULK"
    assert slot is target[3]  # same dict object -> Apply updates the live list
    assert new_geo["offset_along_mm"] == 7.0
    assert new_geo["offset_across_mm"] == -2.345
    assert new_geo["angle_deg"] == -90.0
    # geometry keys written on Apply; net_template NEVER touched by new_geo
    slot.update(new_geo)
    assert slot["net_template"] == "-5V"
    assert slot["offset_along_mm"] == 7.0


def test_overlay_copies_layer_and_per_component_vias():
    source = _source()
    source[0]["layer"] = "B.Cu"
    source[0]["vias"] = [
        {"offset_along_mm": 4.5, "offset_across_mm": 0.9, "drill_mm": 0.4,
         "diameter_mm": 0.8, "net_from_role": "C_IN_BYPASS", "net_from_role_pad": "2"},
    ]
    target = _target()
    for c in target:
        if c["role"] == "C_IN_BYPASS":
            c.pop("layer", None)
            c["vias"] = []
    plan = build_placement_copy_plan(source, _source_vias(), _source_tracks(), target)

    updates = {slot["role"]: new_geo for slot, new_geo in plan.component_updates}
    assert "C_IN_BYPASS" in updates
    assert updates["C_IN_BYPASS"]["layer"] == "B.Cu"
    assert len(updates["C_IN_BYPASS"]["vias"]) == 1
    assert updates["C_IN_BYPASS"]["vias"][0] is not source[0]["vias"][0]


# ── Fatal validation ───────────────────────────────────────────────────────

def test_missing_net_from_role_role_is_fatal():
    target = [c for c in _target() if c["role"] != "C_OUT_BULK"]
    with pytest.raises(ValidationError) as ei:
        build_placement_copy_plan(_source(), _source_vias(), _source_tracks(), target)
    assert "C_OUT_BULK" in str(ei.value)


def test_per_component_via_net_from_role_role_is_validated():
    source = _source()
    source[0]["vias"] = [
        {"offset_along_mm": 1.0, "offset_across_mm": 2.0, "drill_mm": 0.3,
         "diameter_mm": 0.6, "net_from_role": "C_OUT_BULK", "net_from_role_pad": "2"},
    ]
    target = [c for c in _target() if c["role"] != "C_OUT_BULK"]
    with pytest.raises(ValidationError) as ei:
        build_placement_copy_plan(source, [], [], target)
    assert "C_OUT_BULK" in str(ei.value)


@pytest.mark.parametrize("bad_net", ["+5V", "/Channel_0/PIF", "{rail}"])
def test_foreign_or_parametrized_literal_net_is_fatal(bad_net):
    via = {"offset_along_mm": 1.0, "offset_across_mm": 2.0, "drill_mm": 0.3,
           "diameter_mm": 0.6, "net": bad_net}
    with pytest.raises(ValidationError):
        build_placement_copy_plan(_source(), [via], [], _target())


def test_netless_copper_is_fatal():
    via = {"offset_along_mm": 1.0, "offset_across_mm": 2.0, "drill_mm": 0.3,
           "diameter_mm": 0.6}
    with pytest.raises(ValidationError):
        build_placement_copy_plan(_source(), [via], [], _target())


def test_rule_net_literal_is_allowed():
    via = {"offset_along_mm": 1.0, "offset_across_mm": 2.0, "drill_mm": 0.3,
           "diameter_mm": 0.6, "net": "GND"}
    plan = build_placement_copy_plan(_source(), [via], [], _target())
    assert len(plan.new_via_records) == 1
    assert plan.new_via_records[0]["net"] == "GND"


def test_empty_target_cell_is_fatal():
    with pytest.raises(ValidationError) as ei:
        build_placement_copy_plan(_source(), _source_vias(), _source_tracks(), [])
    assert "components" in str(ei.value)


def test_nothing_to_copy_is_fatal():
    # identical donor geometry AND no donor copper -> there is literally nothing
    # the copy would change or add
    with pytest.raises(ValidationError) as ei:
        build_placement_copy_plan(_source(), [], [], _target())
    assert "nothing to copy" in str(ei.value)


def test_source_only_role_not_referenced_is_skipped_not_fatal():
    source = copy.deepcopy(_source())
    source.append({"role": "EXTRA", "offset_along_mm": 3.0})
    plan = build_placement_copy_plan(source, _source_vias(), _source_tracks(), _target())
    assert plan.skipped_roles == ["EXTRA"]


def test_inputs_never_mutated():
    source_comp = _source()
    source_vias = _source_vias()
    source_tracks = _source_tracks()
    target = _target()
    before = (copy.deepcopy(source_comp), copy.deepcopy(source_vias),
              copy.deepcopy(source_tracks), copy.deepcopy(target))
    build_placement_copy_plan(source_comp, source_vias, source_tracks, target)
    assert (source_comp, source_vias, source_tracks, target) == before
