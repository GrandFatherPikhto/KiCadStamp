# kicadstamp/absent_copper_prune.py
"""The record -> LIVE COPPER map of ONE cell instance — the basis of С-2
«Subtract selected copper» (plan_2026_10_06_prune_absent_cell_copper).

The pair is EXACT, never "nearest": a dry run of THIS instance's recording
(``ApplyPipeline(only=[record], dry_run=True)`` → ``plan_copper()``) produces the
commands the redraw would place, and ``registry_match.match_planned_copper``
matches each command to the live board — tier 1 by the registry's stored uuid,
tier 2 by exact geometry. That is the SAME matching «Select cell» uses
(plan_2026_10_05_select_cell_split, СЦ-2), so the invariant holds:

    you can subtract exactly what «Select cell» would highlight.

The record-identifying TAIL of ``make_registry_key`` ("via"/"track", the role
placeholder for spoke-level copper, the 0-based index in the cell's list) is the
key: every command of one dry run shares the anchor and template, so that tail
tells the records apart — the ONE association the plan allows ("запись ↔ команда
— по индексу в ключе реестра").

History: the first С-1 rule asked only the REGISTRY ("delete a record when the
uuid the registry stored for it is gone") and then tried to answer "is the copper
still on the board" with a ``BoardCopperPresence`` verdict. С-1 made the read
STRICTLY the selection, which removed the need for that verdict AND its callers —
so the presence dataclass and ``instance_copper_presence`` are gone (dead code is
not left behind), and what remains is the MAP С-2 needs.

The pipeline builds its OWN adapter (``pipeline.adapter``) and is closed in a
``finally`` (deepseek.md / door.md: "свой сокет — свой finally"). Nothing here
reads the board from the UI thread, and nothing is written — not the registry,
not the board, not the config. Callers decide what to do with the map.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .registry import (
    PlacementRegistry,
    TrackRegistry,
    registry_paths_for_config,
)
from .registry_match import match_planned_copper

__all__ = [
    "RecordCopperMap",
    "instance_record_copper_map",
    "record_copper_map_for",
]


@dataclass(frozen=True)
class RecordCopperMap:
    """``{(kind, role_part, index): live_uuid}`` for ONE instance's dry run.

    ``planned`` — how many commands the dry run produced (0 = the record placed
    no copper: a refused tree, an unrealized record, a chain-only placement). The
    caller must then subtract NOTHING and say why — "we could not check", never
    "the cell has no copper".
    """

    by_record: dict = field(default_factory=dict)
    planned: int = 0

    @property
    def empty(self) -> bool:
        """True when the dry run planned nothing at all."""
        return self.planned == 0

    def live_uuid(self, kind: str, role_part: str, index: int) -> Optional[str]:
        """The live uuid this record's command matched, or None."""
        return self.by_record.get((kind, role_part, int(index)))


def instance_record_copper_map(config_path: str, record_name: str,
                               timeout_ms: Optional[int],
                               cell_identity: Optional[str]) -> RecordCopperMap:
    """Dry-run ``record_name`` on the live board and report, per cell record, the
    live copper its command matched (registry uuid first, then exact geometry).

    A pipeline that cannot be prepared or planned raises — the CALLER turns that
    into "could not check" (an empty ``RecordCopperMap()``), never a subtraction.
    """
    from .apply_pipeline import ApplyPipeline

    pipeline = None
    try:
        pipeline = ApplyPipeline(str(config_path), only=[record_name],
                                 dry_run=True, timeout_ms=timeout_ms)
        pipeline.run()
        vias, tracks = pipeline.plan_copper()
        return record_copper_map_for(pipeline.adapter, str(config_path),
                                     cell_identity, vias, tracks)
    finally:
        if pipeline is not None:
            pipeline.close()


def record_copper_map_for(adapter, config_path: str,
                          cell_identity: Optional[str],
                          planned_vias, planned_tracks) -> RecordCopperMap:
    """The SAME map for a caller that ALREADY holds an adapter (and the dry run's
    commands) — one rule, one place: the read-only match is ``_map_for``."""
    return _map_for(adapter, config_path, cell_identity, planned_vias,
                    planned_tracks)


def _map_for(adapter, config_path: str, cell_identity: Optional[str],
             planned_vias, planned_tracks) -> RecordCopperMap:
    """The read-only match of one dry run's commands against the live board."""
    via_path, trk_path = registry_paths_for_config(config_path)
    via_reg = PlacementRegistry(adapter, via_path)
    trk_reg = TrackRegistry(adapter, trk_path)

    def _live(getter):
        fn = getattr(adapter, getter, None)
        return list(fn() or ()) if fn else []

    by_record: dict = {}
    for kind, reg, cmds, live in (
            ("via", via_reg, planned_vias, _live("get_vias")),
            ("track", trk_reg, planned_tracks, _live("get_tracks"))):
        for m in match_planned_copper(reg, list(cmds or ()), live_items=live):
            if m.live is None:
                continue
            parts = str(getattr(m.command, "registry_key", None) or "").split("|")
            if len(parts) != 4 or parts[1] != cell_identity:
                # A command of some OTHER record in the same run (net_traces,
                # a chain, a thermal array): not this cell's record.
                continue
            try:
                index = int(parts[3])
            except ValueError:
                continue
            by_record[(kind, parts[2], index)] = getattr(m.live, "uuid", None)
    planned = len(planned_vias or ()) + len(planned_tracks or ())
    return RecordCopperMap(by_record=by_record, planned=planned)
