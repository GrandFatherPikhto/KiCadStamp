#!/usr/bin/env python3
"""Ч1-А of plan_2026_10_15_honest_live_read.md: a NON-RIGID cluster is REFUSED
by the reading consumers, at the EXISTING `RIGID_TOLERANCE_MM`.

Fact А (Denis + Claude, 15.10): "Read current position" wrote the frame of a
non-rigid cluster into the config — the fit had always reported `is_rigid`, and
nobody asked. Denis's decision (20.10): the threshold is the existing
`CellFrame.is_rigid`, there is NO second constant; only the consumers that
WRITE pass `strict=True`; the refusal is a Log line (error), never a modal.

These cells are board-free (a fake adapter, like tests/geometry/
test_cell_frame_single_slot.py): the live "instance" is built by PRODUCTION
placement code (`apply_clone_geometry`), so the only thing under test is the
reader's answer.

  * Ж1  a rigid three-role copy turned 30° READS 30° (the happy path);
  * Ж2  one role moved 5 mm -> refused, and the text names THAT role;
  * Ж3  the mcu_mcu fact of the measurement (49.8 mm, R_BOOT1_PD) -> refused,
       R_BOOT1_PD named with its refdes, BOTH exits spelled out;
  * Ж4  two roles swapped (the other shape of the fact) -> refused;
  * Ж5  the SAME non-rigid cluster still answers a DRAWING read (strict=False),
       which is what the two overlay workers ask for;
  * Ж6  at most THREE worst roles are listed — "re-read the cell" is only
       actionable when the list stays readable;
  * Ж7  a ONE-slot cell has nothing to diverge from (`single_slot_rotation`
       unchanged, plan rule: no fit, no refusal).
"""
import pytest

from kicadstamp.cell_frame import RIGID_TOLERANCE_MM
from kicadstamp.config import Cell, ClonePlacement, TemplateComponentSlot
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import NonRigidClusterError, ValidationError
from kicadstamp.geometry.clone_geometry import apply_clone_geometry

from gui.docks import live_position as lp

FRONT = BoardLayer.BL_F_Cu
CLUSTER = "mcu_mcu"

# The two exits Denis asked to see in the refusal, word for word.
EXIT_REDRAW = "put the component(s) back onto the cell's places (Redraw)"
EXIT_REREAD = "re-read the cell from the selection"


class _ClusterAdapter:
    """Exactly the adapter surface `_live_cluster_frame` touches: the refresh it
    always does, the footprint list and the Role/Cluster field reads."""

    def __init__(self, footprints):
        self._fps = list(footprints)

    def refresh_board(self):
        pass

    def get_footprints(self):
        return list(self._fps)

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return fp.role
        if name == CLUSTER_FIELD_NAME:
            return fp.cluster
        return None

    def get_footprint_pads(self, fp):
        return []


def _live_fp(ref, role, x_mm, y_mm, angle=0.0, layer=FRONT):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm),
                   angle_deg=angle, layer=layer)
    fp.role = role
    fp.cluster = CLUSTER
    fp.sheet_path_uuids = []
    return fp


def _cell(name, slots, anchor_role=None):
    return Cell(name=name, layer="F.Cu", anchor_role=anchor_role,
                components=[TemplateComponentSlot(role=role,
                                                  offset_along_mm=along,
                                                  offset_across_mm=across,
                                                  angle_deg=0.0)
                            for role, along, across in slots])


def _placed_live(cell, *, origin_mm=(100.0, 100.0), rotation_deg=0.0,
                 refs=None):
    """The live footprints of a PRODUCTION placement of `cell` — one per role,
    positioned by `apply_clone_geometry`, which is what Redraw uses."""
    refs = refs or {slot.role: f"{slot.role}_1" for slot in cell.components}
    clone = ClonePlacement(cluster=CLUSTER, cell=cell.name,
                           xy=origin_mm, rotation_deg=rotation_deg)
    layout = apply_clone_geometry(clone, cell, refs)
    return {placed.role: _live_fp(placed.ref, placed.role,
                                  placed.position.x / 1e6,
                                  placed.position.y / 1e6,
                                  angle=placed.angle_deg)
            for placed in layout.components}


def _three_role_cell():
    return _cell("three", [("A", 0.0, 0.0), ("B", 10.0, 0.0), ("C", 0.0, 10.0)])


# ── Ж1: the happy path — a rigid copy still reads ────────────────────────────

def test_a_rigid_three_role_copy_reads_at_minus_ninety_degrees():
    """Ж1: the cluster IS a rigid copy of the cell, turned −90° in KiCad. The
    strict read must answer exactly what the non-strict one always did.

    The plan's cell says "30°"; `fit_cell_frame` SNAPS the angle to the nearest
    multiple of 90° by construction (a cell frame is orthogonal — that snapping
    is what keeps an unaffected slot's stored offset when a neighbour moves), so
    a rigid copy can only stand at an orthogonal angle. −90° is the orthogonal
    case the mcu_mcu fact itself names ("кадр −90 против U5 +90")."""
    cell = _three_role_cell()
    fps = _placed_live(cell, rotation_deg=-90.0)
    adapter = _ClusterAdapter(list(fps.values()))

    _pos, rotation, mirror = lp._live_cluster_frame(
        adapter, cell, CLUSTER, "", {}, strict=True)

    assert rotation == pytest.approx(-90.0, abs=0.01)
    assert mirror is False


# ── Ж2: one role out of place — the text names it ────────────────────────────

def test_a_role_moved_five_mm_is_refused_with_its_name_and_both_exits():
    """Ж2: C stands 5 mm away from where the cell puts it. The refusal carries
    the cluster and the cell, C's OWN deviation, and BOTH exits — and it is a
    `ValidationError` (the readers' one refusal class), so nothing about the
    callers' error handling changes."""
    cell = _three_role_cell()
    fps = _placed_live(cell)
    moved = fps["C"]
    fps["C"] = _live_fp(moved.ref, "C",
                        moved.position.x / 1e6 + 5.0,
                        moved.position.y / 1e6)
    adapter = _ClusterAdapter(list(fps.values()))

    with pytest.raises(NonRigidClusterError) as ei:
        lp._live_cluster_frame(adapter, cell, CLUSTER, "", {}, strict=True)

    text = str(ei.value)
    assert isinstance(ei.value, ValidationError)
    assert "three" in text and CLUSTER in text
    assert f"C ({moved.ref})" in text
    assert "5.0" in text                      # C's own deviation, not a guess
    assert EXIT_REDRAW in text and EXIT_REREAD in text


# ── Ж3: the mcu_mcu fact of the 19.10 measurement ───────────────────────────

def test_the_mcu_mcu_fact_is_refused_naming_r_boot1_pd():
    """Ж3 (the fact cell Denis named): `mcu_mcu` has TWO roles — U5 and
    R_BOOT1_PD 50.3 mm apart in the cell — and on the board R_BOOT1_PD stands
    right next to U5, so the fit reports 49.8 mm at tolerance 0.05. "Read
    current position" must refuse instead of writing a frame that is off by
    half the cell; the text names R_BOOT1_PD with its refdes.

    The numbers are the measurement's own (49.8 mm, the worst role R_BOOT1_PD) —
    if the tolerance or the fit's maths ever move, this cell says so."""
    cell = _cell("mcu_mcu", [("U5", 0.0, 0.0), ("R_BOOT1_PD", 50.3, 0.0)],
                 anchor_role="U5")
    adapter = _ClusterAdapter([
        _live_fp("U5", "U5", 100.0, 100.0, angle=90.0),
        _live_fp("R7", "R_BOOT1_PD", 100.5, 100.0, angle=-90.0)])

    with pytest.raises(NonRigidClusterError) as ei:
        lp._live_cluster_frame(adapter, cell, CLUSTER, "", {}, strict=True)

    text = str(ei.value)
    assert "mcu_mcu" in text
    assert "49.800" in text                     # the measured residual
    assert "R_BOOT1_PD (R7)" in text
    assert EXIT_REDRAW in text and EXIT_REREAD in text
    # ~1000× the tolerance: a replaced part cluster, not a rounding argument,
    # so "make the tolerance wider" (a second constant) cannot be the answer.
    assert 49.8 > 100 * RIGID_TOLERANCE_MM


# ── Ж4: the other shape of the fact — two roles swapped ─────────────────────

def test_two_roles_swapped_are_refused():
    """Ж4: B and C traded places on the board (the shape of the FPGA fact of
    15.10). Every role still resolves — the cluster simply is not the cell —
    so the refusal must come from the FIT, not from a missing footprint."""
    cell = _three_role_cell()
    fps = _placed_live(cell)
    b, c = fps["B"], fps["C"]
    fps["B"] = _live_fp(b.ref, "B", c.position.x / 1e6, c.position.y / 1e6)
    fps["C"] = _live_fp(c.ref, "C", b.position.x / 1e6, b.position.y / 1e6)
    adapter = _ClusterAdapter(list(fps.values()))

    with pytest.raises(NonRigidClusterError) as ei:
        lp._live_cluster_frame(adapter, cell, CLUSTER, "", {}, strict=True)

    text = str(ei.value)
    assert "worst roles" in text
    assert EXIT_REDRAW in text and EXIT_REREAD in text


# ── Ж5: the drawing readers keep drawing ────────────────────────────────────

def test_the_same_non_rigid_cluster_still_answers_a_drawing_read():
    """Ж5: the two overlay workers (`_ensure_bbox_worker` / `_ensure_marker_worker`
    in gui/entity/anchor_workers.py) call this reader with the DEFAULT
    `strict=False` — they draw what stands on the board and never write a
    config, so the very cluster Ж2 refuses still yields a frame for them.

    Mutation check: make `strict` default to True (or raise unconditionally) and
    the overlay of every non-rigid cluster dies — this cell goes red."""
    cell = _three_role_cell()
    fps = _placed_live(cell)
    moved = fps["C"]
    fps["C"] = _live_fp(moved.ref, "C",
                        moved.position.x / 1e6 + 5.0,
                        moved.position.y / 1e6)
    adapter = _ClusterAdapter(list(fps.values()))

    origin, rotation, mirror = lp._live_cluster_frame(
        adapter, cell, CLUSTER, "", {})

    # A frame came back — the point of the cell is that nothing was refused.
    # (The VALUE of a non-rigid frame is a guess by definition; that is exactly
    # why the writing readers no longer accept it.)
    assert origin is not None
    assert isinstance(rotation, float)
    assert mirror is False


# ── Ж6: the list of worst roles stays readable ──────────────────────────────

def test_the_refusal_names_at_most_three_worst_roles():
    """Ж6: five roles, four of them out of place — the text lists the THREE
    worst and no more (2–3 was the decision), ranked by their own deviation."""
    slots = [("A", 0.0, 0.0), ("B", 10.0, 0.0), ("C", 0.0, 10.0),
             ("D", -10.0, 0.0), ("E", 0.0, -10.0)]
    cell = _cell("wide", slots)
    fps = _placed_live(cell)
    for role, shift_mm in (("B", 1.0), ("C", 3.0), ("D", 5.0), ("E", 7.0)):
        fp = fps[role]
        fps[role] = _live_fp(fp.ref, role, fp.position.x / 1e6 + shift_mm,
                             fp.position.y / 1e6)
    adapter = _ClusterAdapter(list(fps.values()))

    with pytest.raises(NonRigidClusterError) as ei:
        lp._live_cluster_frame(adapter, cell, CLUSTER, "", {}, strict=True)

    worst_line = next(line for line in str(ei.value).splitlines()
                      if "worst roles" in line)
    # One entry per role: "role (ref): dev mm", three of them, comma-separated.
    assert worst_line.count("(") == 3
    assert "E (" in worst_line                       # the worst one leads
    assert "D (" in worst_line and "C (" in worst_line
    assert "B (" not in worst_line                   # the mildest falls off


# ── Ж7: a one-slot cell has nothing to diverge from ─────────────────────────

def test_a_one_slot_cell_has_nothing_to_diverge_from():
    """Ж7 (plan rule: `single_slot_rotation` unchanged): with ONE resolved role
    there is no pair to fit, `fit is None`, and the strict read must answer the
    part's own angle instead of refusing — there is nothing it could diverge
    from."""
    cell = _cell("one", [("ONLY", 0.0, 0.0)])
    adapter = _ClusterAdapter([
        _live_fp("J1", "ONLY", 100.0, 100.0, angle=270.0)])

    _pos, rotation, _mirror = lp._live_cluster_frame(
        adapter, cell, CLUSTER, "", {}, strict=True)

    assert rotation == pytest.approx(-90.0, abs=0.01)
