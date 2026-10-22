#!/usr/bin/env python3
"""The ONE "which side of the board" rule — `cell_frame.mirror_for_layer`.

2026-10-12 (plan_2026_10_12_reread_frame_mirror): the comparison was spelled out
in THREE readers, and the re-read frame proved what a copy costs — a fit was
allowed to CHOOSE a mirror, so a cluster standing on the cell's own side (F.Cu)
re-read into a mirrored cell and the next Redraw turned the parts. The rule now
lives in ONE host, and the criterion is Denis's (п.Д7 of the plan): "are BOTH
sides known". Both known — the layer rule answers; anything else — the caller's
`fallback` (its historical answer: an unconstrained fit), never an invented side.

These guards are board-free on purpose: the rule is pure, and the defect was in
the rule's PLACE, not in any board read.
"""
import pytest

from kicadstamp.cell_frame import frame_for_live_selection, mirror_for_layer
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2

FRONT = BoardLayer.BL_F_Cu
BACK = BoardLayer.BL_B_Cu


# ── К1–К4: both sides known — the rule answers ──────────────────────────────

@pytest.mark.parametrize("fp_layer, cell_layer, expected", [
    (FRONT, "F.Cu", False),   # К1: the instance stands on the cell's own side
    (BACK, "F.Cu", True),     # К2: the instance is on the OTHER side
    (FRONT, "B.Cu", True),    # К3: the cell is the back-sided one
    (BACK, "B.Cu", False),    # К4: both on the back — no mirror either
])
def test_the_mirror_is_the_xor_of_the_two_known_sides(fp_layer, cell_layer,
                                                      expected):
    """К1–К4 (plan_2026_10_12_reread_frame_mirror): the host answers which side
    the live footprint stands on relative to the cell's own `layer` — the four
    cells of the 2×2 table, one test each (a failing row cannot hide the rest)."""
    assert mirror_for_layer(fp_layer, cell_layer) is expected


# ── К5: an unknown side falls back, and the fallback is BOTH-valued ─────────

@pytest.mark.parametrize("fp_layer, cell_layer", [
    (None, "F.Cu"),      # a duck-typed double whose layer is not a BoardLayer
    (FRONT, None),       # a cell record that names no layer
    (None, None),        # neither side is knowable
    (FRONT, BoardLayer.BL_F_Cu),  # the cell side is not the config's string form
])
@pytest.mark.parametrize("fallback", [False, True, None])
def test_an_unknown_side_falls_back_instead_of_inventing_one(
        fp_layer, cell_layer, fallback):
    """К5 (plan_2026_10_12_reread_frame_mirror): the criterion is "BOTH sides
    known" (Denis, 12.10), and it lives HERE rather than in the callers — a path
    name is not a criterion. All THREE fallbacks are checked: the two truth
    values the pad/mount wrapper passes, and `None` — "the fit stays free",
    which is what the re-read frame hands in."""
    assert mirror_for_layer(fp_layer, cell_layer, fallback) is fallback


def test_the_default_fallback_is_no_mirror():
    """К5 (plan_2026_10_12_reread_frame_mirror): with no `fallback` argument the
    answer is `False` — the shape both readers that pass one side only (the two
    `!= (cell.layer == "B.Cu")` call sites) relied on before this task. Their
    behaviour must not change where a duck-typed board object carries no layer."""
    assert mirror_for_layer(None, "F.Cu") is False
    assert mirror_for_layer(FRONT, None) is False


# ── К4 (the third copy): the wrapper still owns its own contract ─────────────

def test_the_relative_mirror_wrapper_keeps_the_cells_documented_default(
        monkeypatch):
    """К4 of the plan's first table (plan_2026_10_12_reread_frame_mirror):
    `tree_position._footprint_relative_mirror` is a WRAPPER now, and its two own
    promises must survive the delegation — a duck-typed cell WITHOUT `layer` reads
    as 'F.Cu' (its documented default, used by every mount-frame caller), and a
    footprint without a usable layer answers with the caller's `fallback`."""
    from kicadstamp import tree_position

    class _DuckCell:
        pass                      # no layer at all — the documented default

    class _DuckFp:
        layer = None              # not a BoardLayer

    assert tree_position._footprint_relative_mirror(
        _DuckFp(), _DuckCell(), fallback=True) is True
    assert tree_position._footprint_relative_mirror(
        _DuckFp(), _DuckCell(), fallback=False) is False
    assert tree_position._footprint_relative_mirror(
        _DuckFp(), _DuckCell(), fallback=False) is False
    # A BACK footprint against that layer-less cell: default 'F.Cu' -> mirrored.
    class _BackFp:
        layer = BACK

    assert tree_position._footprint_relative_mirror(
        _BackFp(), _DuckCell(), fallback=False) is True


def test_the_relative_mirror_wrapper_delegates_instead_of_comparing(
        monkeypatch):
    """К4 (plan_2026_10_12_reread_frame_mirror): the wrapper must ASK the host,
    not re-spell the comparison — that is the whole point of Т1, and a copy
    quietly re-appearing inside the wrapper would never show up in behaviour
    while the two agreed. The spy returns the OPPOSITE of what the layer rule
    would say, so only a real delegation can explain the answer."""
    from kicadstamp import tree_position

    seen = []

    def _spy(fp_layer, cell_layer, fallback=False):
        seen.append((fp_layer, cell_layer, fallback))
        return True                      # the host's answer is inverted on purpose

    monkeypatch.setattr(tree_position, "mirror_for_layer", _spy)

    class _Fp:
        layer = FRONT

    class _Cell:
        layer = "F.Cu"

    assert tree_position._footprint_relative_mirror(
        _Fp(), _Cell(), fallback=False) is True
    assert seen == [(FRONT, "F.Cu", False)]


# ── Д-1 (2026-10-22, plan_2026_10_21_cell_refresh_origin_role_renamed): WHOSE
#    layer is the reference ────────────────────────────────────────────────────
# Since the refresh anchors its translation on ANY matched role, the reference
# slot may keep its OWN side inside the cell (`layer "B.Cu"` in an F.Cu cell).
# Comparing that footprint's layer with the CELL's layer read an unmirrored
# instance as mirrored; `instance_mirror` compares with the SLOT's own side. The
# frame's `side_layer` names the side of the INSTANCE, never the part's.

def _slot_fp(ref, x_mm, y_mm, layer):
    return Footprint(ref=ref, uuid=f"u-{ref}",
                     position=Vector2.from_xy_mm(x_mm, y_mm),
                     angle_deg=0.0, layer=layer)


_MIXED_SLOTS = [{"role": "CBOT", "offset_along_mm": 3.0, "offset_across_mm": 1.0,
                 "layer": "B.Cu"},
                {"role": "R1", "offset_along_mm": 5.0, "offset_across_mm": 2.0}]


def test_the_instance_side_is_read_off_the_reference_slots_own_side():
    """Д-1г: the reference slot stands on B.Cu and the instance does NOT — the
    frame stays unmirrored, and `side_layer` names the side of the INSTANCE (F.Cu),
    which is what the mirror line compares against the cell's layer."""
    frame = frame_for_live_selection(
        _MIXED_SLOTS,
        [_slot_fp("CB", 103.0, 101.0, BACK), _slot_fp("R1", 105.0, 102.0, FRONT)],
        {"CBOT": "CB", "R1": "R1"}, ("CBOT", 3.0, 1.0, 103.0, 101.0),
        (0.0, 0.0), "F.Cu")

    assert frame.mirror is False
    assert frame.side_layer == "F.Cu"
    assert frame.cell_layer == "F.Cu"


def test_a_mirrored_instance_still_names_the_other_side():
    """Д-1д (control): with the whole instance on the other side the mirror rule
    itself must survive the repair — mirrored, and `side_layer` is the instance's
    side (B.Cu), not the reference part's."""
    frame = frame_for_live_selection(
        _MIXED_SLOTS,
        [_slot_fp("CB", 97.0, 101.0, FRONT), _slot_fp("R1", 95.0, 102.0, BACK)],
        {"CBOT": "CB", "R1": "R1"}, ("CBOT", 3.0, 1.0, 97.0, 101.0),
        (0.0, 0.0), "F.Cu")

    assert frame.mirror is True
    assert frame.side_layer == "B.Cu"
