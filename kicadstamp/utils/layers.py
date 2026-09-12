# kicadstamp/utils/layers.py
"""BoardLayer <-> copper-name ('F.Cu'/'In1.Cu'/.../'B.Cu') conversions — single
source of truth.

Previously duplicated in four places (P0-4 of the 2026-08-25 architecture
audit): placement/executor/base.py (BoardLayer -> str), registry.py's local
``_layer_to_str`` copy, channel_copy.py's ``_layer_to_board``/
``_board_layer_to_str`` pair, and an inline string parse in undo.py.
Consolidated here.

2026-09-06 (plan_2026_09_05_scheme_list.md Step 0): the conversions were binary
(F.Cu/B.Cu only — anything not ``B.Cu`` mapped to ``F.Cu``). The domain
``BoardLayer`` now covers the full copper stack (see domain/geometry.py), so
this module maps every copper layer by its KiCad name.

2026-09-12 (plan_2026_09_12_strict_copper_layers.md Э1.a): two parsers, two
jobs — READING legacy data stays tolerant (``layer_from_str``: exact name, else
the historical substring fallback), WRITING to the board is strict
(``layer_from_str_strict``: exact copper name or ValueError). A tolerant parser
on a write path is the same silent F.Cu collapse under a different name, so the
two must not be merged.

2026-09-12 (plan_2026_09_12_mirror_keeps_inner_layers.md, design Р15/Р16): the
MIRROR rule lives here too, as one function both mechanisms call. Mirroring
swaps F.Cu <-> B.Cu only; copper on an INNER layer keeps its own layer, because
an inner layer carries the BOARD's purpose (a ground/power plane) and moving a
local construction to the other side of the SAME board does not move a plane.

This module stays PRESENTATION-FREE: no i18n import (it is deliberately
imported lazily on the CLI path, see config/entries.py) and no log wording —
the Log line for "an inner layer stayed put" is spelled out at its two call
sites from one shared msgid (Э4).
"""
from typing import Iterable

from ..domain.geometry import BoardLayer

# Canonical KiCad copper-layer names in stack order (F.Cu, In1..In30, B.Cu).
COPPER_LAYER_STRINGS = (
    ("F.Cu",) + tuple(f"In{i}.Cu" for i in range(1, 31)) + ("B.Cu",)
)

_STR_BY_LAYER: dict[BoardLayer, str] = {}
_LAYER_BY_STR: dict[str, BoardLayer] = {}
for _name in COPPER_LAYER_STRINGS:
    if _name == "F.Cu":
        _layer = BoardLayer.BL_F_Cu
    elif _name == "B.Cu":
        _layer = BoardLayer.BL_B_Cu
    else:
        _layer = BoardLayer[f"BL_{_name.replace('.', '_')}"]  # 'In2.Cu' -> BL_In2_Cu
    _STR_BY_LAYER[_layer] = _name
    _LAYER_BY_STR[_name] = _layer


def layer_to_str(layer: BoardLayer) -> str:
    """BoardLayer -> KiCad copper-layer name ('F.Cu'/'In1.Cu'/.../'B.Cu')."""
    try:
        return _STR_BY_LAYER[layer]
    except KeyError:
        raise ValueError(f"not a copper layer: {layer!r}") from None


def layer_from_str(text: str) -> BoardLayer:
    """'F.Cu'/'InN.Cu'/'B.Cu' -> the corresponding BoardLayer.

    Tolerant: surrounding whitespace is ignored and an exact copper name maps
    exactly. Anything unrecognised falls back to the historical substring rule
    ('B.Cu' -> BL_B_Cu, otherwise BL_F_Cu) so legacy undo-log/config callers
    (which only ever stored 'F.Cu'/'B.Cu') keep behaving as before.

    Keep this tolerance HERE and nowhere else on the write path — see
    `layer_from_str_strict`.
    """
    key = text.strip()
    if key in _LAYER_BY_STR:
        return _LAYER_BY_STR[key]
    return BoardLayer.BL_B_Cu if "B.Cu" in key else BoardLayer.BL_F_Cu


def layer_from_str_strict(text: str) -> BoardLayer:
    """'F.Cu'/'InN.Cu'/'B.Cu' -> the corresponding BoardLayer.

    STRICT: an exact copper name maps exactly, anything else raises ValueError.
    No whitespace tolerance, no substring fallback, never a silent F.Cu — this
    is the parser for the WRITE path, where the tolerant parser's fallback was
    exactly the defect it is being replaced for.
    """
    try:
        return _LAYER_BY_STR[text]
    except (KeyError, TypeError):  # unknown name, or not a hashable string
        raise ValueError(f"not a copper layer name: {text!r}") from None


def mirror_layer(name: str) -> str:
    """The layer a MIRRORED construction puts `name` on: 'F.Cu' <-> 'B.Cu'.

    An INNER layer ('In1.Cu'...'In30.Cu') is returned UNCHANGED (design Р16 of
    plan_2026_09_12_mirror_keeps_inner_layers.md). Mirroring is not "flip the
    board over" (that would be stackup position p -> N+1-p): it moves a local
    construction to the OTHER SIDE of the SAME board. An inner layer carries the
    board's PURPOSE — on 3CH-AWG-TIA-v103 In1.Cu/In2.Cu are the ground/power
    planes — so carrying In1 to In2 would silently move a ground connection onto
    a power plane. F.Cu/B.Cu are a SIDE, which is exactly what a mirror changes.

    STRICT on the way in (`layer_from_str_strict`): an unknown name raises
    instead of silently becoming one of the outer layers — this runs on the
    WRITE path, where the old binary ternary was precisely that silent loss.

    ONE rule for both mechanisms (Р15): the clone path
    (geometry/clone_geometry.py) and channel-copy (channel_copy.transform_layer)
    must never answer this question differently.
    """
    layer = layer_from_str_strict(name)
    if layer == BoardLayer.BL_F_Cu:
        return "B.Cu"
    if layer == BoardLayer.BL_B_Cu:
        return "F.Cu"
    return _STR_BY_LAYER[layer]


def inner_copper_layers(names: Iterable[str]) -> list[str]:
    """The INNER copper layers among `names`, deduplicated, in STACK order
    (F.Cu/In1..InK/B.Cu — never enum order, see the stackup note in
    diagnostics/probe_board_copper_layers.py). Names are validated by the same
    strict parser as `mirror_layer`, so a typo raises rather than disappearing
    from the list. Used for the "this stayed where it is" Log line (Э4)."""
    wanted = {layer_from_str_strict(n) for n in names}
    return [n for n in COPPER_LAYER_STRINGS
            if n not in ("F.Cu", "B.Cu") and _LAYER_BY_STR[n] in wanted]
