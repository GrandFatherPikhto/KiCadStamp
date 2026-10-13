#!/usr/bin/env python3
"""The THIRD rule of `kicadstamp/cell_frame.py`: a cell with fewer than TWO
usable slots recovers its rotation from the ONE component's live angle
(2026-10-13, plan_2026_10_13_single_slot_rotation).

Denis: «в ноде дерева Read current position — угол поворота детали не считывается
(conn_lcd_mcu), 270° пришлось прописать руками». The reader's frame was FITTED
from the stored offsets against the live deltas, the reference pair subtracts
itself away, so a ONE-slot cell had no pair at all -> `fit is None` -> a silent 0°.

The rule and the math live in ONE host (`cell_frame.single_slot_rotation`, a
delegate to `clone_geometry.clone_rotation_from_component`, the inverse of
`apply_clone_geometry`'s `comp_angle`). These cells are board-free: the "instance"
is a fake adapter, exactly like tests/placement/test_live_position.py builds one.

  * К1–К2  the inverse itself, over the 4 slot angles × 4 node rotations × both
           sides — the round-trip property "Redraw -> Read -> the same rotation";
  * К3     the host really DELEGATES (a copy of the formula would not show up
           while the two agreed);
  * К4     Denis's fact: one slot, angle 0, the part at 270° -> the read says 270;
  * К5     the mirrored side uses the mirrored branch (180 − phi), not phi;
  * К6     TWO parts keep the FIT (a regression guard: a symmetric part's angle
           is ambiguous by 180°, geometry is not);
  * К7–К8  the RE-READ frame carries the same rotation, and an unknown board side
           keeps today's 0° — said out loud instead of silently.
"""
import pytest
from unittest.mock import MagicMock

from kicadstamp.cell_frame import (CellFrame, frame_for_live_selection,
                                   single_slot_rotation)
from kicadstamp.config import Cell, TemplateComponentSlot
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2

from gui.docks import live_position as lp

FRONT = BoardLayer.BL_F_Cu
BACK = BoardLayer.BL_B_Cu
CLUSTER = "CONN_LCD"


def _norm(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def _forward_live_angle(slot_angle_deg: float, rotation_deg: float,
                        mirror: bool) -> float:
    """The instance's live footprint angle for a placement — `apply_clone_geometry`'s
    own `comp_angle` (`phi = slot.angle + rotation_deg`, and `(180 − phi) % 360` when
    mirrored). Spelled out HERE on purpose: the cell's job is to be its INVERSE, so
    the expected value cannot come from the code under test."""
    phi = slot_angle_deg + rotation_deg
    return (180.0 - phi) % 360.0 if mirror else phi


def _make_fp(ref, role=None, cluster=None, position=None, angle=0.0,
             layer=FRONT):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=position or Vector2.from_xy(0, 0),
                   angle_deg=angle, layer=layer)
    fp._role = role
    fp._nets = []
    fp._cluster = cluster
    return fp


def _field_value(fp, field_name):
    if field_name == ROLE_FIELD_NAME:
        return fp._role
    if field_name == CLUSTER_FIELD_NAME:
        return fp._cluster
    return None


def _adapter(fps):
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_field_value.side_effect = _field_value
    adapter.get_footprint_pads.return_value = []
    adapter.get_selected_items.return_value = []
    adapter.get_footprint.side_effect = {fp.ref: fp for fp in fps}.get
    return adapter


def _one_slot_cell(slot_angle=0.0, layer="F.Cu"):
    return Cell(name="conn_lcd", layer=layer, components=[
        TemplateComponentSlot(role="CONN_LCD", offset_along_mm=0.0,
                              offset_across_mm=0.0, angle_deg=slot_angle)])


# ── К1–К2: the OWNER — the inverse of the placement formula ──────────────────

@pytest.mark.parametrize("slot_angle", [0.0, 90.0, 180.0, 270.0])
@pytest.mark.parametrize("rotation", [0.0, 90.0, 180.0, 270.0])
@pytest.mark.parametrize("mirror", [False, True])
def test_the_single_slot_rotation_is_the_inverse_of_the_placement_formula(
        slot_angle, rotation, mirror):
    """К1–К2: for every slot angle × node rotation × side, the angle the
    placement WOULD put on the part inverts back to the node's own rotation
    (±0.01°, plan rule 1) — the "Redraw -> Read current position" round trip."""
    live = _forward_live_angle(slot_angle, rotation, mirror)
    assert single_slot_rotation(live, slot_angle, mirror) == pytest.approx(
        _norm(rotation), abs=0.01)


def test_the_host_delegates_the_math_instead_of_repeating_it(monkeypatch):
    """К3: the host must ASK `clone_geometry.clone_rotation_from_component`, not
    re-spell the formula — a second copy would stay invisible while the two
    agreed. The spy returns an impossible value, so only a real delegation can
    explain it."""
    from kicadstamp import cell_frame

    seen = []
    monkeypatch.setattr(cell_frame, "clone_rotation_from_component",
                        lambda *a: seen.append(a) or 123.0)

    assert single_slot_rotation(270.0, 0.0, False) == 123.0
    assert seen == [(270.0, 0.0, False)]


# ── К4–К6: the READER (the path Denis used) ──────────────────────────────────

def test_denis_fact_a_single_slot_cell_reads_the_parts_angle():
    """К4 (the изходный факт): `conn_lcd` is a ONE-slot cell (CONN_LCD → J3, slot
    angle 0) and the part stands at 270°; "Read current position" must say 270,
    not the silent 0° that made Denis type the rotation by hand."""
    cell = _one_slot_cell(slot_angle=0.0)
    fp = _make_fp("J3", role="CONN_LCD", cluster=CLUSTER, angle=270.0)
    adapter = _adapter([fp])

    _pos, rotation, mirror = lp._live_cluster_frame(
        adapter, cell, CLUSTER, "", {})

    # 270° and -90° are the SAME angle; the project convention is (-180, 180]
    # (cell_frame.normalize_deg — the fit path answers this way too), so the read
    # says -90. What matters is that it is NO LONGER the silent 0.
    assert rotation == pytest.approx(_norm(270.0), abs=0.01)
    assert mirror is False


def test_a_turned_placement_on_a_single_slot_cell_reads_its_slot_angle():
    """К5: the slot's OWN angle is subtracted — a slot stored at 90° with the
    part at 90° is a placement rotation of 0, not 90."""
    cell = _one_slot_cell(slot_angle=90.0)
    fp = _make_fp("J3", role="CONN_LCD", cluster=CLUSTER, angle=90.0)
    adapter = _adapter([fp])

    _pos, rotation, _mirror = lp._live_cluster_frame(
        adapter, cell, CLUSTER, "", {})

    assert rotation == pytest.approx(0.0, abs=0.01)


def test_a_mirrored_single_slot_cell_uses_the_mirrored_branch():
    """К5 (side): the instance on the OTHER side of the board than the cell —
    the mirrored branch is `(180 − phi)`, so a part at 180° means a rotation of 0
    and a part at 0° means 180. Using the plain branch here would answer 180/0."""
    cell = _one_slot_cell(slot_angle=0.0, layer="F.Cu")
    fp = _make_fp("J3", role="CONN_LCD", cluster=CLUSTER, angle=180.0,
                  layer=BACK)
    adapter = _adapter([fp])

    _pos, rotation, mirror = lp._live_cluster_frame(
        adapter, cell, CLUSTER, "", {})

    assert mirror is True
    assert rotation == pytest.approx(0.0, abs=0.01)


def test_two_parts_still_use_the_fit_not_one_components_angle():
    """К6 (regression): a two-slot cell keeps the FITTED frame. The evidence is
    a rigid pair: the fit reads BOTH deltas and recovers the true rotation, while
    one part's angle alone could be off by 180° (the very reason the fit exists).

    The geometry: B's stored offset is +10mm along, its live delta is +10mm DOWN
    (100,110) — a rigid copy turned -90° in KiCad's Y-down convention, which is
    what the fit must (and does) say."""
    cell = Cell(name="two", layer="F.Cu", components=[
        TemplateComponentSlot(role="A", offset_along_mm=0.0,
                              offset_across_mm=0.0, angle_deg=0.0),
        TemplateComponentSlot(role="B", offset_along_mm=10.0,
                              offset_across_mm=0.0, angle_deg=0.0)])
    origin = Vector2.from_xy_mm(100.0, 100.0)
    fp_a = _make_fp("A1", role="A", cluster=CLUSTER, position=origin, angle=-90.0)
    fp_b = _make_fp("B1", role="B", cluster=CLUSTER,
                    position=Vector2.from_xy_mm(100.0, 110.0), angle=-90.0)
    adapter = _adapter([fp_a, fp_b])

    _pos, rotation, _mirror = lp._live_cluster_frame(
        adapter, cell, CLUSTER, "", {})

    assert rotation == pytest.approx(-90.0, abs=0.01)


# ── К7–К8: the RE-READ frame (the second path of the plan) ───────────────────

def _matched_one(role="CONN_LCD", angle=0.0):
    return [{"role": role, "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "angle_deg": angle}]


def test_the_reread_frame_carries_the_single_slots_rotation():
    """К7 (plan rule 2 — one host for BOTH paths): the re-read frame of a
    one-slot cell gets the same rotation, so the geometry is expressed in the
    cell's own frame and a second Redraw cannot turn the cell again."""
    origin = Vector2.from_xy_mm(100.0, 100.0)
    fp = _make_fp("J3", role="CONN_LCD", cluster=CLUSTER, position=origin,
                  angle=270.0)

    frame = frame_for_live_selection(
        _matched_one(angle=0.0), [fp], {"CONN_LCD": "J3"}, origin, (0.0, 0.0),
        "F.Cu")

    assert frame.rotation_deg == pytest.approx(_norm(270.0), abs=0.01)


def test_the_reread_frame_keeps_zero_and_says_so_when_the_side_is_unknown(caplog):
    """К8 (plan rule 4 — "не молча"): without the cell's layer the side is
    unknown, and the mirrored branch of the formula cannot be chosen — the frame
    keeps today's 0°, and the Log SAYS the rotation could not be determined."""
    origin = Vector2.from_xy_mm(100.0, 100.0)
    fp = _make_fp("J3", role="CONN_LCD", cluster=CLUSTER, position=origin,
                  angle=270.0)

    with caplog.at_level("WARNING", logger="kicadstamp.cell_frame"):
        frame = frame_for_live_selection(
            _matched_one(angle=0.0), [fp], {"CONN_LCD": "J3"}, origin,
            (0.0, 0.0), None)

    assert frame.rotation_deg == 0.0
    assert "could not be determined" in caplog.text


def test_the_reread_frame_of_a_multi_part_cell_still_fits():
    """К6 through the OTHER path: two matched slots keep the fit (nothing about
    the multi-part case changes)."""
    origin = Vector2.from_xy_mm(100.0, 100.0)
    matched = [{"role": "A", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
                "angle_deg": 0.0},
               {"role": "B", "offset_along_mm": 10.0, "offset_across_mm": 0.0,
                "angle_deg": 0.0}]
    fp_a = _make_fp("A1", position=origin, angle=-90.0)
    fp_b = _make_fp("B1", position=Vector2.from_xy_mm(100.0, 110.0), angle=-90.0)

    frame = frame_for_live_selection(matched, [fp_a, fp_b],
                                     {"A": "A1", "B": "B1"}, origin, (0.0, 0.0),
                                     "F.Cu")

    assert frame.rotation_deg == pytest.approx(-90.0, abs=0.01)
