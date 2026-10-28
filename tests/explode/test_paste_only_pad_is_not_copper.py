# tests/explode/test_paste_only_pad_is_not_copper.py
"""A pad whose padstack reports ONLY paste is not copper at all.

plan_2026_10_27_paste_only_pad_is_not_copper (Denis, 27.10). Live defect on v103:
«Select enclosed copper» for `dac_buf_channel_0` selected 51 segments of the +3V3
In2 bus — 42 of them the bus all over the board. The instance has no pad on +3V3 at
all: the piece was made a CANDIDATE by `D14` — a `Diode_SMD:D_SOD-882` whose two
pads carry `(layers "F.Paste")` — because `_pad_copper_layers` dropped every
non-copper layer, found NO copper, and answered `None`, which the field's contract
reads as «unknown = EVERY copper layer». The paste window became a through-copper
pad, the In2 track under it «touched two instance pads», the piece was carved, and
what was left was the bus.

The rule (plan §3):

  * the padstack DID report layers and none of them is copper -> ``()``: the pad has
    no copper anywhere. ``None`` keeps its historical meaning «the padstack
    reported no layer list»;
  * ``pad_areas`` / ``cell_pad_areas`` skip such a pad: it is neither an island
    anchor nor a FOREIGN pad, so a via in its window does not «touch» it either;
  * the field docstrings name the third value.
"""
from __future__ import annotations

from types import SimpleNamespace

from kipy.board_types import BoardLayer as KipyBoardLayer

from kicadstamp.domain.board import (BoardLayer, _pad_copper_layers,
                                     pad_from_kipy)
from kicadstamp.utils.units import MM
from kicadstamp.enclosed_copper import enclosed_copper
from kicadstamp.explode_connectivity import (cell_pad_areas, has_copper,
                                             pad_areas)
from tests.explode.board import ExplodeBoard, F_CU, fp, pad, track, via


def _kipy(name: str):
    return KipyBoardLayer.Value(name)


def _kipy_pad(number, x_mm, y_mm, layers, w_mm=0.9, h_mm=0.6):
    """A pad EXACTLY as kipy reports it: `padstack.layers` carries the REAL layer
    list (``F.Paste`` for the paste opening), `copper_layers[0]` the size/shape.

    Going through ``pad_from_kipy`` is the point: the cells below must fail when
    `_pad_copper_layers` answers ``None`` for a paste-only stack (mutation M1), not
    only when a hand-written ``copper_layers=()`` is ignored."""
    size = SimpleNamespace(x=int(w_mm * MM), y=int(h_mm * MM))
    layer = SimpleNamespace(size=size, shape=None, offset=None,
                            trapezoid_delta=None)
    padstack = SimpleNamespace(layers=list(layers), copper_layers=[layer],
                               angle=None)
    return pad_from_kipy(SimpleNamespace(
        number=number, net=None,
        position=SimpleNamespace(x=int(x_mm * MM), y=int(y_mm * MM)),
        padstack=padstack))


def _copper(result) -> set:
    from kicadstamp.domain.board import Track, Via
    return {getattr(i, "uuid", None) for i in result.items
            if isinstance(i, (Track, Via))}


# ── cells 1–3: the ONE place that turns a layer list into copper_layers ─────

def test_a_paste_only_stack_has_no_copper_layers():
    """Cell 1: `layers = [F.Paste]` — the padstack DID answer, and not one layer
    is copper. `()` («no copper at all»), NOT `None` («unknown = every layer»).
    Mutation M1 (answer None again) dies here."""
    assert _pad_copper_layers(SimpleNamespace(layers=[_kipy("BL_F_Paste")])) == ()


def test_a_copper_stack_keeps_its_copper_layer_only():
    """Cell 2: `[F.Cu, F.Paste, F.Mask]` — the paste and mask members are dropped
    and the one copper layer stays. Mutation M4 («everything that is not copper is
    the answer, copper included») dies here."""
    assert _pad_copper_layers(SimpleNamespace(
        layers=[_kipy("BL_F_Cu"), _kipy("BL_F_Paste"), _kipy("BL_F_Mask")])
    ) == (BoardLayer.BL_F_Cu,)


def test_an_unreported_stack_stays_unknown():
    """Cell 3: no `layers` at all (or an empty one) — «unknown» = every copper
    layer, the historical reading every hand-made double relies on."""
    assert _pad_copper_layers(SimpleNamespace()) is None
    assert _pad_copper_layers(SimpleNamespace(layers=None)) is None
    assert _pad_copper_layers(SimpleNamespace(layers=[])) is None


# ── cell 4: the two pad doors skip such a pad ───────────────────────────────

def test_paste_only_pads_are_neither_cell_nor_foreign_pads():
    """Cell 4: a footprint with ONE copper pad and ONE paste-only pad contributes
    the copper pad only — as an instance island and as a foreign pad alike.
    Mutation M2 (no skip in `cell_pad_areas`) and M3 (none in `pad_areas`) die
    here."""
    copper_pad = pad("1", 0.0, 0.0, copper_layers=(F_CU,))
    paste_pad = pad("", 1.0, 0.0, copper_layers=())
    assert has_copper(copper_pad) and not has_copper(paste_pad)
    assert has_copper(pad("2", 0.0, 0.0))          # None = unknown = copper

    own = fp("u1", "D14", "DAC_BUF", 0.0, 0.0)
    board = ExplodeBoard(footprints=[own], pads={"u1": [copper_pad, paste_pad]})

    assert [name for _area, _layers, name in cell_pad_areas(board, [own])] == \
        ["D14.1"]
    # `pad_areas` labels the CELL class `cell:<ref>:<pad>`; the same footprint
    # seen as a FOREIGN class keeps its own label but still contributes ONE pad.
    assert [label for _area, label, _layers in
            pad_areas(board, {"cell": [own]})] == ["cell:D14:1"]
    assert [label for _area, label, _layers in
            pad_areas(board, {"DAC_BUF": [own]})] == ["DAC_BUF"]


# ── cell 5: the live shape — a SOD-882 under an In2 bus ─────────────────────

def _sod882_board():
    """D14 of the live case, built the way the BOARD reports it: two F.Cu pads, two
    PASTE-only pads (``padstack.layers = [F.Paste]``), and the +3V3 In2 bus running
    under the paste windows on its way to a foreign component.

    The bus is THREE segments, like the real one: the piece between the two windows
    is a sub-piece of its own, so a carve leaves something to be taken if the
    windows are read as pads (mutation M2) — with ONE segment the carve would cut
    the piece whole and the cell would pass for the wrong reason."""
    d14 = fp("u14", "D14", "DAC_BUF", 0.0, 0.0)
    foreign = fp("u99", "R99", "LDO_P3V3", 5.0, -1.0)
    return ExplodeBoard(
        footprints=[d14, foreign],
        tracks=[track("t_left", BoardLayer.BL_In2_Cu, -5.0, -1.0, -0.9, -1.0,
                      width_mm=0.8),
                track("t_mid", BoardLayer.BL_In2_Cu, -0.9, -1.0, 0.9, -1.0,
                      width_mm=0.8),
                track("t_right", BoardLayer.BL_In2_Cu, 0.9, -1.0, 5.0, -1.0,
                      width_mm=0.8)],
        pads={
            "u14": [_kipy_pad("1", -1.0, 1.0, [_kipy("BL_F_Cu")]),
                    _kipy_pad("2", 1.0, 1.0, [_kipy("BL_F_Cu")]),
                    # the paste windows, straight over the bus
                    _kipy_pad("", -1.0, -1.0, [_kipy("BL_F_Paste")]),
                    _kipy_pad("", 1.0, -1.0, [_kipy("BL_F_Paste")])],
            "u99": [_kipy_pad("1", 5.0, -1.0, [_kipy("BL_F_Cu")])],
        })


def test_the_in2_bus_under_paste_windows_is_not_taken():
    """Cell 5 (the live defect): with the paste windows read as copper, the bus
    touched TWO «instance pads» and a foreign one — a carve, and 51 segments were
    selected. With the rule the piece touches NO instance pad: it is not a
    candidate at all, so nothing of the bus is taken and nothing is counted.

    Mutation M1 (the stack answers ``None`` again) dies here. M2 (no skip in
    `cell_pad_areas`) does NOT — a track cannot touch a ``()``-layered pad anyway,
    because `_touches_pad` asks the layer and ``()`` has none; only a VIA gets
    through that way, which is exactly what cell 6 pins."""
    board = _sod882_board()
    result = enclosed_copper(board, [board.get_footprint("D14")])

    assert _copper(result) == set()
    assert result.pieces == 0
    assert result.carved == 0
    assert result.not_taken_foreign == 0
    assert result.not_taken_dangling == 0


# ── cell 6: a via inside the paste window ───────────────────────────────────

def test_a_via_in_the_paste_window_does_not_touch_the_pad():
    """Cell 6: a via is through — `_touches_pad` never asks its layer — so a
    paste-only pad used to «contain» it and the via became a one-pad dead end.
    With the rule the pad is not a pad: the via is not a candidate at all.
    Mutation M2 (no skip in `cell_pad_areas`) dies here."""
    d14 = fp("u14", "D14", "DAC_BUF", 0.0, 0.0)
    board = ExplodeBoard(
        footprints=[d14],
        vias=[via("v1", 0.0, 0.0)],
        pads={"u14": [_kipy_pad("", 0.0, 0.0, [_kipy("BL_F_Paste")])]})

    result = enclosed_copper(board, [d14])

    assert _copper(result) == set()
    assert result.pieces == 0
    assert result.not_taken_dangling == 0


# ── cell 7: the ordinary case is untouched ──────────────────────────────────

def test_copper_between_two_copper_pads_is_still_taken():
    """Cell 7: the normal piece between two REAL copper pads is taken exactly as
    before. Mutation M4 (dropping `F.Cu` too) dies here."""
    board = ExplodeBoard(
        footprints=[fp("u1", "R41", "DAC_BUF", 0.0, 0.0),
                    fp("u2", "R42", "DAC_BUF", 5.0, 0.0)],
        tracks=[track("t12", F_CU, 0.0, 0.0, 5.0, 0.0)],
        # Through the SAME cut the board goes through: mutation M4 lives in
        # `_pad_copper_layers`, so a hand-written `copper_layers=(F_CU,)` here
        # would keep the cell green while the product lost every pad's copper.
        pads={"u1": [_kipy_pad("1", 0.0, 0.0, [_kipy("BL_F_Cu")])],
              "u2": [_kipy_pad("1", 5.0, 0.0, [_kipy("BL_F_Cu"),
                                               _kipy("BL_B_Cu")])]})

    result = enclosed_copper(board, board.get_footprints())

    assert _copper(result) == {"t12"}
    assert result.pieces == 1
    assert result.not_taken_foreign == 0 and result.not_taken_dangling == 0
