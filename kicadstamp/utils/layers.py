# kicadstamp/utils/layers.py
"""BoardLayer <-> 'F.Cu'/'B.Cu' conversions — single source of truth.

Previously duplicated in four places (P0-4 of the 2026-08-25 architecture
audit): placement/executor/base.py (BoardLayer -> str), registry.py's local
``_layer_to_str`` copy, channel_copy.py's ``_layer_to_board``/
``_board_layer_to_str`` pair, and an inline string parse in undo.py.
Consolidated here.
"""
from ..domain.geometry import BoardLayer


def layer_to_str(layer: BoardLayer) -> str:
    """BoardLayer -> 'F.Cu'/'B.Cu'."""
    return "B.Cu" if layer == BoardLayer.BL_B_Cu else "F.Cu"


def layer_from_str(text: str) -> BoardLayer:
    """'B.Cu...' -> BL_B_Cu, anything else -> BL_F_Cu — tolerant substring
    match (undo's operation log stores the original layer as a plain string,
    and channel_copy's config layer values are exactly 'F.Cu'/'B.Cu')."""
    return BoardLayer.BL_B_Cu if "B.Cu" in text else BoardLayer.BL_F_Cu
