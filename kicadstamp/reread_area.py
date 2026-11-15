# kicadstamp/reread_area.py
"""THE owner of the AREA an inter-node copper read covers (plan §0 п.1–3 of
plan_2026_11_10_extra_copper_by_selection).

Why this module exists. A board SELECTION used to narrow BOTH halves of the read:
`run_internode_reread_worker` took the pad BOUNDARIES from the selected items only,
so «Select inter-node copper» (which selects tracks and vias, never footprints)
followed by «Reread inter-node copper» gave the classifier ZERO pads — every unit
became UNMOORED and every record «not found» (live HP-Power log, 10.11 13:44:
`not taken: unmoored 18`, six records «the copper is no longer on the board»).
Nothing was lost, but six records were declared stale for no reason.

The rule, in one place for every door that needs it («Select inter-node copper»,
«Reread inter-node copper», and tomorrow the extra-copper door):

  * a NON-EMPTY selection narrows the COPPER only — the tracks and vias of the
    selection;
  * the pad boundaries are ALWAYS the whole board's placement places: pads in the
    selection neither add nor remove one;
  * an EMPTY selection means the whole board's copper.

`narrowed` says which of the two happened, so the report can name the area and
the plan can tell «this record was not looked at» from «its copper is gone».
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .domain.board import Track, Via

__all__ = ["RereadArea", "read_reread_area"]


@dataclass
class RereadArea:
    """What ONE read covers: the copper items, the pad boundaries, and whether a
    selection narrowed it."""

    items: list = field(default_factory=list)          # tracks + vias to read
    footprints: list = field(default_factory=list)     # pad boundaries — whole board
    narrowed: bool = False

    @property
    def track_count(self) -> int:
        return sum(1 for item in self.items if not isinstance(item, Via))

    @property
    def via_count(self) -> int:
        return sum(1 for item in self.items if isinstance(item, Via))

    @property
    def has_copper(self) -> bool:
        """False when a selection carried no track and no via at all — the one case
        the caller reports as a single refusal instead of running a read."""
        return bool(self.items)


def read_reread_area(adapter, selected: Optional[Iterable] = None) -> RereadArea:
    """The area of ONE read, from the live board and (optionally) its selection.

    `selected` is the board's current selection as the adapter reports it. A
    non-empty one narrows the COPPER: its tracks and vias are read, and the pads
    come from the WHOLE board — the boundaries of the classification are the
    tree's own placement places, never the selection's (that mix-up is the defect
    this module was written for). Anything else in the selection (footprints,
    graphic items) is ignored: pads in a selection neither add nor remove one.

    An empty selection (or none) means the whole board: every track, every via and
    every footprint.
    """
    chosen = list(selected or [])
    footprints = list(adapter.get_footprints())
    if chosen:
        items = [item for item in chosen if isinstance(item, (Track, Via))]
        return RereadArea(items=items, footprints=footprints, narrowed=True)
    items = list(adapter.get_tracks()) + list(adapter.get_vias())
    return RereadArea(items=items, footprints=footprints, narrowed=False)
