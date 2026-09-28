# tests/fakes/explore_board.py
"""`FakeExploreBoard` — the stand-in for `kicadstamp.explore.Board`, the object
sitting behind `connection.board`.

Ф1.4d-6. FOUR GUI files carried a byte-identical three-line copy of this class
(the docstrings differed, the code did not):

  * tests/gui/test_rules_read_position.py:29
  * tests/gui/test_trees_dock.py:1693
  * tests/gui/test_placer_read_position.py:23
  * tests/gui/test_placer_select_on_board.py:35

What all four do with it is put it on the connection and let the dock's
connection check pass: production reads `connection.board.adapter`
(gui/main_window.py:1172, gui/fieldstool_window.py:653, gui/docks/trees_dock.py's
`_live_adapter()`), and the real `Board.__init__` really does store
`self.adapter` (kicadstamp/explore.py:150-152). The adapter itself is never used
in those cells — the resolvers they exercise are monkeypatched.

TWO things this file deliberately does NOT become:

  * a fuller Board. The four cells need `.adapter` and nothing else, and the
    `test_the_shared_board_stand_in_stays_a_one_attribute_duck_type` cell keeps
    it that way. A cell that needs `select()`/`refresh()` should subclass this
    (tests/gui/test_main_window_selection.py's richer board is that shape).
  * a home for every `connection.board = ...` in the suite. That assignment
    appears ~177 times in many spellings — `object()`, `SimpleNamespace(adapter=
    adapter)`, per-file classes. Widening the rule to those is a separate
    decision (Ф1.4e / Ф1.7), not this extraction.

The default adapter is a sentinel `object()`, NOT None, and that is the whole
point: the docks ask `getattr(board, "adapter", None) is not None`, so a None
default would defeat the condition these cells set up. Once set, the sentinel is
never touched — which is exactly why it can be a bare object.
"""
from __future__ import annotations

#: Handed to `.adapter` when the caller passes nothing: non-None on purpose (see
#: the module docstring). A bare object() is enough because nothing reads it.
_DEFAULT_ADAPTER = object()


class FakeExploreBoard:
    """See the module docstring: `connection.board` with a live `.adapter`."""

    def __init__(self, adapter=_DEFAULT_ADAPTER) -> None:
        self.adapter = adapter


__all__ = ["FakeExploreBoard"]
