"""Board-adapter seam.

``IBoardAdapter`` is the *single* place where kipy board types are allowed to
leak into the rest of KiCadStamp. It intentionally returns kipy types
(``FootprintInstance``, ``Via``, ``Track``, ``Pad``, ``Net``, ``Zone``,
``Vector2``, ``Box2``, ``Angle``, ``BoardLayer``) directly, so consumers
(planner, executors, registry, undo, channel_copy, template extraction)
compile against the real kipy API.

This is a deliberate, documented leak (2026-08-25, architecture audit P1-4
"minimum" scope):

* The kipy version is **pinned** at ``kicad-python==0.7.1`` in
  ``pyproject.toml`` and ``requirements.txt`` (``requirements-dev.txt`` and
  ``requirements-diagnostics.txt`` inherit it via ``-r requirements.txt``).
* Swapping kipy/KiCad out from under this interface would not be a local
  adapter change — it would cascade through every module that touches board
  types. Until that is fixed, the pin is what makes the seam safe.

Follow-up (P1-4 "ideal" scope, separate stage): introduce domain DTOs
(``Footprint``, ``Pad``, ``Via``, ``Track``, ``Net``, ``Zone``, ``Point2``,
``Layer``) and map kipy <-> DTO here, so the rest of the package no longer
imports ``kipy.board_types``/``kipy.geometry``. Until then:

* Keep every kipy import in consumers going through the types named above.
* Do not reach into kipy internals that are not part of this interface.
* New adapter methods follow the same convention (kipy types in signatures)
  so the later DTO migration has one well-defined boundary to convert.
"""
from abc import ABC, abstractmethod
from typing import Any
from kipy.board_types import FootprintInstance, Zone, Net, Pad, Via
from kipy.geometry import Vector2

class IBoardAdapter(ABC):
    """Facade over a live KiCad board over the kipy IPC API.

    See the module docstring: this class deliberately exposes kipy types;
    the kipy version is pinned and the DTO migration is a planned follow-up.
    """
    @abstractmethod
    def refresh_board(self): ...

    @abstractmethod
    def get_footprint(self, ref: str) -> FootprintInstance | None: ...

    @abstractmethod
    def get_footprints(self) -> list[FootprintInstance]: ...

    @abstractmethod
    def get_vias(self) -> list[Via]: ...

    @abstractmethod
    def get_selected_items(self) -> list[Any]: ...

    @abstractmethod
    def get_field_value(self, footprint: FootprintInstance, field_name: str) -> str | None: ...

    @abstractmethod
    def get_footprint_pads(self, fp: FootprintInstance) -> list[Pad]: ...

    @abstractmethod
    def get_pad_by_number(self, fp: FootprintInstance, number: str) -> Pad | None: ...

    @abstractmethod
    def get_zone_by_name(self, name: str) -> Zone | None: ...

    @abstractmethod
    def get_net_by_name(self, name: str) -> Net | None: ...

    @abstractmethod
    def get_board_origin(self, kind: str) -> Vector2: ...

    @abstractmethod
    def get_bounding_boxes(self, items) -> list[Any | None]: ...

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
    def flip_selected(self, footprints: list[FootprintInstance]): ...

    @abstractmethod
    def commit_with_retry(self, description: str, work_fn, retries: int = 1) -> bool: ...

    @abstractmethod
    def create_via(self, position: Vector2, net: Net, drill_mm: float, diameter_mm: float) -> Via: ...

    @abstractmethod
    def remove_by_id(self, uuid_str: str) -> bool: ...