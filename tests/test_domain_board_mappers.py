# tests/test_domain_board_mappers.py
"""Фаза B (plan_2026_09_09_cell_anchor_v2 §B.5) — board_item_from_kipy must map
a selected PAD to the Pad DTO.

```get_selected_items``` returns raw kipy Pads when the user clicks a pad (they
name no footprint themselves, §0.2); leaving them opaque made every consumer see
``ref=None``/``net_name=None`` even though ``pad_from_kipy`` and the Pad DTO
already existed.
"""
from kipy.board_types import Pad as KipyPad
from kipy.geometry import Vector2 as KipyVector2

from kicadstamp.domain.board import Pad, board_item_from_kipy


def _kipy_pad(number="1", x=1000, y=2000):
    pad = KipyPad()
    pad.number = number
    pad.position = KipyVector2.from_xy(x, y)
    return pad


def test_board_item_from_kipy_maps_pad_to_dto():
    pad = _kipy_pad()
    dto = board_item_from_kipy(pad)
    assert isinstance(dto, Pad)
    assert dto.number == "1"
    assert (dto.position.x, dto.position.y) == (1000, 2000)
    # The opaque back-reference is kept for the adapter's write path.
    assert dto._kipy is pad


def test_board_item_from_kipy_leaves_unknown_items_untouched():
    class _Drawing:
        pass

    drawing = _Drawing()
    assert board_item_from_kipy(drawing) is drawing
