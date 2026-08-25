# kicadstamp/domain/__init__.py
"""Domain types shared across KiCadStamp, decoupled from kipy board entities.

See `board.py` for the board-entity DTOs that replace kipy's
FootprintInstance/Via/Track/Pad/Net/Zone at the IBoardAdapter boundary.
"""

from .board import (  # noqa: F401
    Footprint,
    Net,
    Pad,
    Track,
    Via,
    Zone,
    board_item_from_kipy,
    footprint_from_kipy,
    net_from_kipy,
    pad_from_kipy,
    track_from_kipy,
    unwrap,
    via_from_kipy,
    zone_from_kipy,
)

__all__ = [
    "Footprint",
    "Net",
    "Pad",
    "Track",
    "Via",
    "Zone",
    "board_item_from_kipy",
    "footprint_from_kipy",
    "net_from_kipy",
    "pad_from_kipy",
    "track_from_kipy",
    "unwrap",
    "via_from_kipy",
    "zone_from_kipy",
]
