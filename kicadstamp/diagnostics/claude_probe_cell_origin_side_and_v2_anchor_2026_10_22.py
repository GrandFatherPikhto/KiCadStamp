#!/usr/bin/env python3
# kicadstamp/diagnostics/claude_probe_cell_origin_side_and_v2_anchor_2026_10_22.py
"""Claude's acceptance of c9cf6291 (plan_2026_10_21_cell_refresh_origin_role_renamed),
2026-10-22: two cases the shipped cells do not cover. No board, no Qt, no Config —
the same stubs as tests/selection/test_cell_refresh_origin_renamed.py.

  П1  The origin role is gone and the FIRST role in the cell's order is a part
      with its OWN side (`layer "B.Cu"` in an F.Cu cell). frame_for_live_selection
      reads the instance's side off the surrogate's footprint against the CELL's
      layer, so a perfectly unmirrored instance is taken as mirrored:
        legacy cell -> refused ("no component would stand at (0, 0)"), nothing
                       written, but the whole cluster IS selected;
        v2 cell     -> WRITTEN with wrong offsets (R1 5,2 -> 4,3, every angle
                       -90) plus a false "stands on the other side" warning.
      Control: the same cell with a top-side role first passes both ways.
  П2  A v2 cell whose anchor_role was renamed on the board: refresh #1 goes
      through and removes the old anchor role from the components, but the
      cell's anchor_role still names it (the cell editor keeps the loaded
      anchor_role), so refresh #2 of the SAME selection refuses with "role 'M'
      (the cell's anchor origin) is not a component of this cell" — the write
      the legacy precondition exists to prevent, on the path it exempts.

Before c9cf6291 both cases refused at refresh #1 (the origin role was required
in the selection). Run: .venv/bin/python -m pytest -q -s <this file>
Expected TODAY: П1 legacy/v2 with bottom_first=True and П2 FAIL; controls pass.

AFTER the Д-1…Д-4 rework (2026-10-22) this file must be GREEN, and Д-2 changed
the П2 step honestly: the plan now TRANSFERS the anchor role, and the fixed dock
writes it, so refresh #2 starts from the role the plan named — the step asserts
that transfer instead of modelling the old dock. The dock itself is pinned by
tests/gui/docks/test_cell_editor.py (Д-2е); without that cell a green probe would
hide a dock that still writes the old name back.
"""
import pytest

from kicadstamp.cell_geometry_refresh import build_refresh_plan
from kicadstamp.cell_origin import plan_components
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError


def _fp(ref, x, y, layer=BoardLayer.BL_F_Cu):
    return Footprint(ref=ref, uuid=f"u-{ref}", position=Vector2.from_xy_mm(x, y),
                     angle_deg=0.0, layer=layer)


class _Adapter:
    def __init__(self, roles):
        self.roles = roles

    def get_field_value(self, fp, name):
        return self.roles.get(fp.ref)

    def get_footprint(self, ref):
        return type("R", (), {"ref": ref})()

    def get_pad_by_number(self, fp, pad):
        return None

    def get_footprint_pads(self, fp):
        return []


def _plan(components, footprints, roles, origin_role=None):
    return build_refresh_plan(components, [], [], footprints, [], [],
                              _Adapter(roles), add_new_copper=True,
                              remove_missing=True, reconcile_components=True,
                              cell_layer="F.Cu", origin_role=origin_role)


def _mixed(bottom_first, origin):
    bot = {"role": "CBOT", "offset_along_mm": 3.0, "offset_across_mm": 1.0,
           "layer": "B.Cu"}
    top = {"role": "R1", "offset_along_mm": 5.0, "offset_across_mm": 2.0}
    return [bot, top, origin] if bottom_first else [top, bot, origin]


# ── П1: the side of the instance read off a part that has its own side ──────

@pytest.mark.parametrize("bottom_first", [True, False])
def test_p1_legacy_whole_cluster_is_updated(bottom_first):
    footprints = [_fp("U", 100, 100), _fp("R1", 105, 102),
                  _fp("CB", 103, 101, BoardLayer.BL_B_Cu)]
    plan = _plan(_mixed(bottom_first, {"role": "OSC"}), footprints,
                 {"U": "XTAL", "R1": "R1", "CB": "CBOT"})
    kept = {r["role"]: g for r, g in plan.component_updates}
    added = {r["role"]: r for r in plan.new_component_records}
    assert (kept["R1"]["offset_along_mm"], kept["R1"]["offset_across_mm"]) == (5.0, 2.0)
    assert (added["XTAL"]["offset_along_mm"], added["XTAL"]["offset_across_mm"]) == (0.0, 0.0)


@pytest.mark.parametrize("bottom_first", [True, False])
def test_p1_v2_offsets_survive_an_unmirrored_instance(bottom_first):
    footprints = [_fp("U", 101, 101), _fp("R1", 105, 102),
                  _fp("CB", 103, 101, BoardLayer.BL_B_Cu)]
    anchor = {"role": "OSC", "offset_along_mm": 1.0, "offset_across_mm": 1.0}
    plan = _plan(_mixed(bottom_first, anchor), footprints,
                 {"U": "XTAL", "R1": "R1", "CB": "CBOT"}, origin_role="OSC")
    kept = {r["role"]: g for r, g in plan.component_updates}
    print("\n", bottom_first, kept, plan.warnings)
    assert (kept["R1"]["offset_along_mm"], kept["R1"]["offset_across_mm"]) == (5.0, 2.0)
    assert not any("other side" in w for w in plan.warnings)


# ── П2: a v2 cell's anchor_role outlives its component ──────────────────────

def test_p2_v2_cell_survives_a_second_refresh_of_the_same_selection():
    components = [{"role": "M", "offset_along_mm": -2.0, "offset_across_mm": 3.0},
                  {"role": "A"}, {"role": "B", "offset_along_mm": 1.0}]
    footprints = [_fp("M", 100.0, 103.0), _fp("A", 102.0, 100.0),
                  _fp("B", 103.0, 100.0)]
    roles = {"M": "M2", "A": "A", "B": "B"}          # M renamed on the board
    first = _plan(components, footprints, roles, origin_role="M")
    after = plan_components(components, first.component_updates,
                            first.new_component_records,
                            first.removed_component_records)
    # Д-2: the plan NAMES the role that inherits the anchor, and the cell editor
    # writes it (Д-2е) — so refresh #2 starts from that role, not from the dead "M".
    assert first.new_anchor_role == "M2"
    _plan(after, footprints, roles, origin_role=first.new_anchor_role)


def test_p2_a_stale_anchor_role_on_disk_is_refused_in_words():
    """Д-2д (control): a cell whose `anchor_role` names no component of it (written
    by an older version, or a transfer the dock never saved) cannot be measured at
    all — the refusal says so and nothing is written, instead of a plan that would
    leave a cell refusing on its very next read."""
    components = [{"role": "A"}, {"role": "B", "offset_along_mm": 1.0}]
    footprints = [_fp("A", 102.0, 100.0), _fp("B", 103.0, 100.0)]

    with pytest.raises(ValidationError) as exc:
        _plan(components, footprints, {"A": "A", "B": "B"}, origin_role="M")

    text = str(exc.value)
    assert "is not a component of this cell" in text
    assert "Nothing was placed or modified on the board" in text
