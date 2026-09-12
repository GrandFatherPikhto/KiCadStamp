# gui/board_layers.py
"""Copper layers of the LIVE board, in physical stackup order — Э1 of
plan_2026_09_12_cell_layer_dialog.md (design
techdocs/handoff/claude/design_2026_09_12_cell_copper_layers_full.md, Р2/Р12).

Why this is a GUI module (same reasoning as gui/board_overlay.py): it is a READ
the cell editor and its layer dialog perform on the live board, not part of the
board model. It draws nothing and imports no Qt, so it is headless-testable.

The two measured facts this module exists to encode (probe
diagnostics/probe_board_copper_layers.py, live on KiCad 10.0.x, re-measured
2026-09-12):

  1. The kipy layer value is NOT the stackup position: on this FOUR-layer stack
     the values are F.Cu=3, In1.Cu=4, In2.Cu=5, B.Cu=34. That they happen to be
     ascending in stackup order is a coincidence the ORDER test cannot catch
     (Э7.5 of the plan, corrected 2026-09-12) — the trap is POSITION, so
     `position` is produced by enumerating the ordered list, never by the value.
  2. The DISPLAY name belongs to the user (`get_layer_name()`): a renamed layer
     must show ITS name in every list Denis sees. The canonical copper name
     ('F.Cu'/'In1.Cu'/.../'B.Cu') travels alongside it because that is the
     vocabulary the cell's own `tracks:`/`vias:` records and the remembered
     selection use (see kicadstamp/utils/layers.py).

A HIDDEN copper layer is reported (`visible=False`), never dropped and never
worked around (Э2): KiCad's selection cannot see it either, and reading "over"
the selection is not ours to do — a hidden layer is the user's own setting.

`board` here is the live board handle the reads go through (`adapter._board` in
the GUI, a duck-typed fake in tests), exposing get_enabled_layers(),
get_layer_name(layer), get_visible_layers() and get_copper_layer_count().
"""
from typing import Iterable, NamedTuple

from kipy.board_types import BoardLayer

from kicadstamp.domain.board import layer_from_kipy
from kicadstamp.utils.layers import layer_to_str

__all__ = ["CopperLayer", "copper_layer_order", "enabled_copper_layers"]


class CopperLayer(NamedTuple):
    """One copper layer of the live board.

    layer        — the board's own layer value, i.e. what a live Track carries
                   (kept as-is: it is the key any filtering compares against);
    copper_name  — canonical 'F.Cu'/'In1.Cu'/.../'B.Cu' (cell-record vocabulary);
    display_name — `get_layer_name()`, the user-renamable name shown in the UI;
    position     — 1-based STACKUP position, never the layer value;
    visible      — from get_visible_layers() (False = hidden on the board).
    """
    layer: int
    copper_name: str
    display_name: str
    position: int
    visible: bool


def copper_layer_order(layers: Iterable[int]) -> list[int]:
    """The given layers reduced to COPPER, in stackup order: F.Cu, In1..InK
    (ascending), B.Cu. Every non-copper layer (silkscreen, Edge.Cuts, user,
    ...) is dropped — nothing else is ever a candidate for a cell's tracks.

    Built the same way as the measuring probe's copper_stackup_order(): the
    outer layers are singled out explicitly, the inner ones are the
    In1.Cu..In30.Cu range. Callers therefore never need to know a single layer
    VALUE — which is the point, since a value is not a place in the stack (see
    the module docstring)."""
    front = [layer for layer in layers if layer == BoardLayer.BL_F_Cu]
    inner = sorted(layer for layer in layers
                   if BoardLayer.BL_In1_Cu <= layer <= BoardLayer.BL_In30_Cu)
    back = [layer for layer in layers if layer == BoardLayer.BL_B_Cu]
    return front + inner + back


def enabled_copper_layers(board) -> list[CopperLayer]:
    """Every ENABLED copper layer of the live `board`, in stackup order, with
    the user's own layer names and the hidden/visible flag.

    `position` counts places in the stack, so a four-layer board gives B.Cu
    position 4 (its value is 34) and the number of rows equals
    `get_copper_layer_count()` — both asserted by Э7.5's test.

    Names are read per call: `display_name` through the board's
    `get_layer_name()` (a renamed layer shows the user's name), `copper_name`
    through the project's single layer-name bridge
    (domain.board.layer_from_kipy -> utils.layers.layer_to_str), so the record
    vocabulary cannot drift from the rest of the codebase."""
    ordered = copper_layer_order(board.get_enabled_layers())
    visible = set(board.get_visible_layers())
    return [CopperLayer(layer=layer,
                        copper_name=layer_to_str(layer_from_kipy(layer)),
                        display_name=board.get_layer_name(layer),
                        position=position,
                        visible=layer in visible)
            for position, layer in enumerate(ordered, start=1)]
