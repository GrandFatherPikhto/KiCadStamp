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

The set has a SECOND use — the per-read filter (Э5): the set is decided on the
UI thread and carried in the worker's payload, and the worker keeps only the live
tracks whose layer is in it (`filter_tracks_by_layers`). Copper that is not read
must never reach the matcher, where it would look like copper the cell does not
describe; filtering after the matcher would be too late.

The set has a THIRD use — the remembered CHOICE (Э3): the dialog offers this list
with checkboxes, and what the user confirms is stored in gui_state.json
(`remembered_read_layers` / `remember_read_layers`) as the starting point of the
next read. Only the choice is stored, never the list itself: the list is derived
from the live board every time, so it cannot go stale, and no `Cell` grows a
layer field.

TWO LAYER WORLDS meet here, and the canonical NAME is the bridge between them:

  * `enabled_copper_layers(board)` speaks the BOARD's values (kipy: F.Cu=3 ..
    B.Cu=34, see above) and READS the live board;
  * `filter_tracks_by_layers` speaks the DOMAIN values a `Track` carries
    (kicadstamp.domain.geometry.BoardLayer, F.Cu=0 .. B.Cu=32 — a different
    numbering!) and matches by the canonical name, never by a value.

`board` here is the live board handle the reads go through (`adapter._board` in
the GUI, a duck-typed fake in tests), exposing get_enabled_layers(),
get_layer_name(layer), get_visible_layers() and get_copper_layer_count().
"""
from typing import Any, Iterable, NamedTuple, Optional

from kipy.board_types import BoardLayer

from kicadstamp.domain.board import Track, layer_from_kipy
from kicadstamp.utils.layers import layer_to_str

from . import settings

__all__ = [
    "ALL_COPPER_LAYERS",
    "CopperLayer",
    "LayerChoice",
    "READ_LAYERS_KEY",
    "copper_layer_order",
    "enabled_copper_layers",
    "filter_tracks_by_layers",
    "layer_choices",
    "layers_to_remember",
    "live_copper_name",
    "remember_read_layers",
    "remembered_read_layers",
    "selection_layer_names",
]

# The "every layer" value of the layer set (see filter_tracks_by_layers). None is
# chosen deliberately as the fast path's answer: it needs NO board read to mean
# "all of them", and it is exactly the pre-filter behaviour (P.3.1 of the plan —
# the fast path stays fast). An EMPTY collection is a different answer: nothing
# is read at all.
ALL_COPPER_LAYERS: Optional[Any] = None


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


def live_copper_name(layer) -> Optional[str]:
    """The canonical copper name of a DOMAIN layer value (what a `Track` carries
    from the live board), or None when the value is not a copper layer at all.

    `utils.layers.layer_to_str` is the strict single source of truth for the
    name — it raises for a non-copper layer, and that is exactly the answer
    wanted here: a non-copper item is not a layer the user could have checked,
    so it can never be "kept". A track is always copper in KiCad; this stays
    defensive, since silently naming such an item 'F.Cu' (the tolerant parser's
    historical fallback) would let it join an F.Cu read it does not belong to."""
    try:
        return layer_to_str(layer)
    except (ValueError, KeyError, TypeError):
        return None


# ── Э3: the remembered choice and the dialog's rule order ────────────────────
#
# Stored in gui_state.json — GUI STATE, not project config and not a profile's
# `settings:`: the choice is a per-machine convenience (on the second machine it
# is its own, accepted deliberately), and it must never travel with the project.
# settings.state merges per key, so no other dock's state can clobber it.
READ_LAYERS_KEY = "cell_read_layers"


class LayerChoice(NamedTuple):
    """One row of the layer dialog.

    copper         — the live layer it stands for (name, user name, hidden flag);
    checked        — the state the dialog OPENS with, after Э3's rules;
    empty          — no copper on this layer in the current selection;
    auto_unchecked — unchecked BY THE RULE rather than by the user (remembered as
                     checked, empty right now). Only `layers_to_remember` cares,
                     and it is what keeps an auto-uncheck out of the memory.
    """
    copper: CopperLayer
    checked: bool
    empty: bool
    auto_unchecked: bool


def remembered_read_layers() -> Optional[list[str]]:
    """The layer set the user last confirmed in the dialog — canonical copper
    names, or None when nothing was ever remembered (the first run: everything
    starts checked). A stored value of an unexpected shape is treated as
    "nothing remembered" rather than crashing a read."""
    raw = settings.state.get(READ_LAYERS_KEY)
    if not isinstance(raw, (list, tuple)):
        return None
    return [str(name) for name in raw]


def remember_read_layers(names: Iterable[str]) -> None:
    """Store the user's MANUAL choice (see `layers_to_remember`) — never the
    automatic unchecking of layers that merely happened to be empty."""
    settings.state.set(READ_LAYERS_KEY, sorted(set(names)))


def selection_layer_names(items) -> set[str]:
    """The canonical copper names that carry copper IN THIS SELECTION.

    Tracks only: vias are layer-less and components stand on a side of the board
    (P.2). `items` is the live selection as the ~400 ms poll tick already
    distributes it, so the dialog never asks the board itself (P.3.4)."""
    names = set()
    for item in items or ():
        if isinstance(item, Track):
            name = live_copper_name(item.layer)
            if name:
                names.add(name)
    return names


def layer_choices(copper_layers, remembered, present_names) -> list[LayerChoice]:
    """The dialog's opening state — Э3's rule order, in one place:

      1. the STARTING point is the remembered set (`None` = nothing remembered
         yet = every layer checked);
      2. over it, layers EMPTY in the current selection are unchecked and marked
         `empty` — and that auto-uncheck is flagged (`auto_unchecked`) so it can
         be kept OUT of the memory (see `layers_to_remember`).

    A layer the user unchecked MANUALLY and which also happens to be empty right
    now is NOT flagged: the user's own "no" is the stronger reason and must
    survive in the memory."""
    remembered_names = None if remembered is None else set(remembered)
    rows = []
    for copper in copper_layers:
        empty = copper.copper_name not in present_names
        remembered_checked = (remembered_names is None
                              or copper.copper_name in remembered_names)
        rows.append(LayerChoice(
            copper=copper,
            checked=remembered_checked and not empty,
            empty=empty,
            auto_unchecked=remembered_checked and empty))
    return rows


def layers_to_remember(choices, checked_names, touched_names) -> list[str]:
    """What OK may persist: the user's OWN choice only.

    `checked_names` is the dialog's FINAL state and `touched_names` the boxes the
    user actually toggled; both come from the widget, never from the opening
    rules. Storing the auto-unchecking would let one narrow selection silently
    erase the choice — the layer came off because nothing happened to be on it
    that time, and the next read would start with it off although the board has
    copper there. So an auto-unchecked layer is kept in the memory UNLESS the
    user touched its box (checking it on and off again is a decision too)."""
    checked = set(checked_names or ())
    touched = set(touched_names or ())
    return [row.copper.copper_name for row in choices
            if row.copper.copper_name in checked
            or (row.auto_unchecked and row.copper.copper_name not in touched)]


def filter_tracks_by_layers(tracks, layers) -> list:
    """The live TRACKS this read is allowed to see — the Э5 filter, standing
    right after the selection is split and BEFORE any matching.

    `layers` is the layer set decided on the UI thread and carried in the
    worker's payload: a collection of canonical copper names
    ('F.Cu'/'In1.Cu'/.../'B.Cu'), or ALL_COPPER_LAYERS for "every layer".

    ALL_COPPER_LAYERS returns the list UNCHANGED (the fast path — no board read,
    the historical behaviour, and the reason None is the sentinel). An EMPTY
    collection is a different answer: nothing at all is read. Any other value is
    a membership test on the canonical name.

    Vias are layer-less (a through-hole has no layer) and components stand on a
    SIDE of the board, not on a layer — neither is ever passed through here
    (P.2 of the plan): the caller filters tracks only."""
    if layers is ALL_COPPER_LAYERS:
        return list(tracks)
    selected = set(layers)
    return [track for track in tracks
            if live_copper_name(track.layer) in selected]
