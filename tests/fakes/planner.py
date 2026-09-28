# tests/fakes/planner.py
"""`FakePlanner` — the stand-in for `kicadstamp.placement.planner.PlacementPlanner`.

Ф1.4d-4. Six classes named `_FakePlanner` were in play, and like the resolver
family they were not one shape — but this time the SURFACE was identical (the
real planner's five public methods) while the RESULTS differed:

  * three copies in tests/test_apply_pipeline_coordinate_placements.py planned
    NOTHING (all three reads returned []), and were installed on a pipeline that
    already existed: `pipeline.planner = _FakePlanner()`;
  * one module-level copy in tests/test_dry_run_report.py returned one command of
    each kind, so the report has all three sections;
  * two copies in tests/gui/test_placer_dock.py replaced the planner CLASS in
    `gui/docks/placer.py`'s namespace and returned `_FakeMove` lists from
    `plan_item` — the per-item read the tagging path makes.

So the results come in through four class attributes, one per read, and the base
defaults to planning nothing:

    class _FakePlanner(FakePlanner):
        PLAN_ITEM_RESULT = (_FakeMove("U_OWN", "DAC_BUF"), ...)

`plan_*` returns `list(self.PLAN_*_RESULT)` — a COPY, on purpose: handing out the
class attribute itself would let one cell append to what every later cell sees
(the mistake Ф1.4b's shared counter made, in a different dress).

Both drift directions are pinned by the conformance cell:

  * the fake may invent nothing — `gui/docks/placer.py` replaces the real class
    module-wide, so a name production calls would be an AttributeError, and a
    name production does NOT call would be a test pinning a surface that cannot
    be reached (this is why `plan_item` is here: it IS real, at planner.py:79,
    and `begin_planning` too, at :69);
  * every method's parameters must equal the real ones — `plan_items(items)`,
    `plan_vias()`, `plan_tracks()` and `plan_item(item)`, with those names.

ONE DELIBERATE DIVERGENCE, written down rather than smuggled in: the real
constructor is `(adapter, config, sheet_names=None, position_overrides=None,
isolate_spokes=None)` — adapter and config are REQUIRED — while this base gives
them `None` defaults, because three of the four install sites put the stand-in on
a pipeline that already exists and have nothing to hand it. The cell asserts the
real requiredness and the fake's relaxation separately, so relaxing further (or
the real class growing a new required argument) fails there instead of being
absorbed.
"""
from __future__ import annotations


class FakePlanner:
    """See the module docstring: the real surface, the results injected."""

    #: What each read hands back. Empty on the base — it plans NOTHING.
    PLAN_ITEM_RESULT: tuple = ()
    PLAN_ITEMS_RESULT: tuple = ()
    PLAN_VIAS_RESULT: tuple = ()
    PLAN_TRACKS_RESULT: tuple = ()

    def __init__(self, adapter=None, config=None, sheet_names=None,
                 position_overrides=None, isolate_spokes=None) -> None:
        # The attribute names mirror the real class: it stores `self.cfg` (the
        # parameter is `config`) and normalises the three optional mappings.
        self.adapter = adapter
        self.cfg = config
        self.sheet_names = sheet_names or {}
        self.position_overrides = position_overrides or {}
        self.isolate_spokes = isolate_spokes or {}

    def begin_planning(self) -> None:
        """Nothing to reset: the results are class attributes, not accumulators."""

    def plan_item(self, item):
        return list(self.PLAN_ITEM_RESULT)

    def plan_items(self, items):
        return list(self.PLAN_ITEMS_RESULT)

    def plan_vias(self):
        return list(self.PLAN_VIAS_RESULT)

    def plan_tracks(self):
        return list(self.PLAN_TRACKS_RESULT)


__all__ = ["FakePlanner"]
