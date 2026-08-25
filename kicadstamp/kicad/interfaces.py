"""Board-adapter seam.

``IBoardAdapter`` is the *single* place where the live KiCad board is
abstracted. Since P1-4 of the 2026-08-25 architecture audit it returns domain
DTOs (``Footprint``, ``Pad``, ``Via``, ``Track``, ``Net``, ``Zone`` from
``kicadstamp.domain.board``) instead of raw kipy board-entity classes, so
consumers no longer import ``kipy.board_types`` entity types.

The geometric/enum *value* types (``Vector2``, ``Angle``, ``Box2``,
``BoardLayer``) are also domain types now (``kicadstamp.domain.geometry``,
completed 2026-08-25) — the seam is fully decoupled from kipy value types as
well as board-entity types. The kipy version remains pinned at
``kicad-python==0.7.1`` (``pyproject.toml`` / ``requirements.txt``).

Rules for the seam:

* Consumers must not import kipy board-entity types (``FootprintInstance``,
  ``Via``, ``Track``, ``Pad``, ``Net``, ``Zone``) — use the domain DTOs here.
* DTOs carry an opaque ``_kipy`` back reference for write paths; consumers
  must never touch it. The adapter maps it back via ``unwrap()``.
* New adapter methods follow the same convention (domain DTOs in signatures)
  so the boundary stays well-defined.
"""
from abc import ABC, abstractmethod
from typing import Any

from ..domain.geometry import BoardLayer, Box2, Vector2

from ..domain.board import Footprint, Net, Pad, Track, Via, Zone


class IBoardAdapter(ABC):
    """Facade over a live KiCad board over the kipy IPC API.

    See the module docstring: this class deliberately exposes ONLY domain
    types — board DTOs (``domain/board.py``) and geometry value types
    (``domain/geometry.py``) — never ``kipy.*``. The kipy conversion happens
    solely inside ``adapter.py``.
    """

    @abstractmethod
    def refresh_board(self): ...

    @abstractmethod
    def get_footprint(self, ref: str) -> Footprint | None: ...

    @abstractmethod
    def get_footprint_by_id(self, uuid_str: str) -> Footprint | None: ...

    @abstractmethod
    def get_footprints(self) -> list[Footprint]: ...

    @abstractmethod
    def get_vias(self) -> list[Via]: ...

    @abstractmethod
    def get_tracks(self) -> list[Track]: ...

    @abstractmethod
    def get_selected_items(self) -> list[Any]: ...

    @abstractmethod
    def select_items(self, items: list[Any]): ...

    @abstractmethod
    def get_field_value(self, footprint: Footprint, field_name: str) -> str | None: ...

    @abstractmethod
    def has_field(self, footprint: Footprint, field_name: str) -> bool: ...

    @abstractmethod
    def set_field_value(self, footprint: Footprint, field_name: str, value: str) -> None: ...

    @abstractmethod
    def get_footprint_pads(self, fp: Footprint) -> list[Pad]: ...

    @abstractmethod
    def get_pad_by_number(self, fp: Footprint, number: str) -> Pad | None: ...

    @abstractmethod
    def get_zone_by_name(self, name: str) -> Zone | None: ...

    @abstractmethod
    def get_net_by_name(self, name: str) -> Net | None: ...

    @abstractmethod
    def get_all_nets(self) -> list[Net]: ...

    @abstractmethod
    def get_board_origin(self, kind: str) -> Vector2: ...

    @abstractmethod
    def get_bounding_boxes(self, items) -> list[Box2 | None]: ...

    @abstractmethod
    def begin_commit(self): ...

    @abstractmethod
    def push_commit(self, commit, description: str): ...

    @abstractmethod
    def drop_commit(self, commit): ...

    @abstractmethod
    def update_items(self, items): ...

    @abstractmethod
    def create_items(self, items): ...

    @abstractmethod
    def flip_selected(self, footprints: list[Footprint]): ...

    @abstractmethod
    def commit_with_retry(self, description: str, work_fn, retries: int = 1) -> bool: ...

    @abstractmethod
    def create_via(self, position: Vector2, net: Net, drill_mm: float, diameter_mm: float) -> Via: ...

    @abstractmethod
    def create_track(self, start: Vector2, end: Vector2, width_mm: float,
                     net: Net, layer: BoardLayer) -> Track: ...

    @abstractmethod
    def remove_by_id(self, uuid_str: str) -> bool: ...
