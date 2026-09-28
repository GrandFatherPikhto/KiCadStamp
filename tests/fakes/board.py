# tests/fakes/board.py
"""The two duck-typed board doubles the gui tests need — as TWO classes.

Ф1.4c. Fifteen classes named `_FakeBoard` turned out to be two families that
share only a NAME, not a surface (see
techdocs/handoff/deepseek/handoff/note_2026_09_28_F1_4c_fakeboard_finding.md):

  * :class:`FakeBoardOverlay` — what ``gui/board_overlay.py`` reads: enabled
    layers, layer names, shapes.
  * :class:`FakeBoardLayers` — what ``gui/board_layers.py`` reads: enabled and
    VISIBLE layers, layer names, and the copper-layer COUNT.

Merging them into one class would need a name-fallback strategy plus two sets of
attribute spellings (``enabled`` vs ``layers``) — the god-fake the plan warns
against. Each keeps its own rules; the file-specific constants (which BoardLayer
values, which names, the name fallback) live in the LOCAL subclass, which is why
the `DEFAULT_*` hooks exist.

Correspondence cell: tests/test_fakes_conformance.py pins both surfaces against
the layer reads ``IBoardAdapter`` declares.
"""
from __future__ import annotations


class FakeBoardOverlay:
    """The board surface ``gui/board_overlay.py`` reads."""

    #: Set by the local subclass: the layer values that file speaks in.
    DEFAULT_LAYERS: tuple = ()
    #: Set by the local subclass: layer -> name for that file's layers.
    DEFAULT_NAMES: dict = {}

    def __init__(self, layers=None, shapes=None, names=None) -> None:
        self.layers = list(self.DEFAULT_LAYERS if layers is None else layers)
        self.shapes = list(shapes or ())
        self.names = dict(self.DEFAULT_NAMES if names is None else names)

    def get_enabled_layers(self) -> list:
        return list(self.layers)

    def get_layer_name(self, layer) -> str:
        return self.names.get(layer, str(layer))

    def get_shapes(self) -> list:
        return list(self.shapes)


class FakeBoardLayers:
    """The board surface ``gui/board_layers.py`` reads."""

    #: Set by the local subclass: the default enabled stackup.
    DEFAULT_ENABLED: tuple = ()
    #: Set by the local subclass: layer -> name (empty = use the fallback).
    DEFAULT_NAMES: dict = {}

    #: Read by the tests and by diagnostics/probe_board_copper_layers.py, NOT by
    #: gui/ or kicadstamp/ — the seam deliberately does not declare it. The
    #: conformance cell requires exactly this list.
    FAKE_ONLY = ("get_copper_layer_count",)

    def __init__(self, enabled=None, visible=None, names=None,
                 copper_count: int = 4) -> None:
        self.enabled = list(self.DEFAULT_ENABLED if enabled is None else enabled)
        self.visible = list(self.enabled if visible is None else visible)
        self.names = dict(self.DEFAULT_NAMES if names is None else names)
        self.copper_count = copper_count

    def get_enabled_layers(self) -> list:
        return list(self.enabled)

    def get_visible_layers(self) -> list:
        return list(self.visible)

    def get_layer_name(self, layer) -> str:
        if layer in self.names:
            return self.names[layer]
        return self._fallback_name(layer)

    def _fallback_name(self, layer) -> str:
        """Overridden per file: the two files that read `board_layers.py` name
        their layers from their own module tables, not from here."""
        return str(layer)

    def get_copper_layer_count(self) -> int:
        return self.copper_count


__all__ = ["FakeBoardLayers", "FakeBoardOverlay"]
