# kicadstamp/absent_copper_prune.py
"""Н4 п.5, corrected: is a cell record's copper STILL on the board?
(plan_2026_10_06_prune_absent_cell_copper; Denis 2026-10-05/06).

The first Н4 п.5 rule asked only the REGISTRY: "delete a record when the uuid
the registry stored for it is gone from the board". That is not enough. A cell
whose copper was re-read / re-routed has its registry uuids all stale while the
copper itself is on the board under NEW uuids — those records would be erased
although their copper never left. The rule Denis stated is BOTH halves:

    a record with no live pair is deleted ONLY when its copper is not found
    EITHER by the registry uuid OR by geometry.

This module answers the geometry half, read-only, the SAME way "Select cell"
does (plan_2026_10_05_select_cell_split, СЦ-2): a dry run of THIS instance's
recording (``ApplyPipeline(only=[record], dry_run=True)`` →
``plan_copper()``) produces the commands the redraw would place, and
``registry_match.match_planned_copper`` matches them to the live board — tier 1
by the registry's stored uuid, tier 2 by exact geometry. A record is "on the
board" when its command matched by EITHER tier.

The pipeline builds its OWN adapter (``pipeline.adapter``) and is closed in a
``finally`` (deepseek.md / door.md: "свой сокет — свой finally"). Nothing here
reads the board from the UI thread, and nothing is written — not the registry,
not the board, not the config. Callers decide what to do with the verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .constants import SPOKE_LEVEL_ROLE_PLACEHOLDER
from .registry import (
    PlacementRegistry,
    TrackRegistry,
    registry_paths_for_config,
)
from .registry_match import match_planned_copper

__all__ = [
    "BoardCopperPresence",
    "instance_copper_presence",
]


@dataclass(frozen=True)
class BoardCopperPresence:
    """Which of THIS instance's planned copper records are on the board.

    ``on_board`` — a frozenset of ``(kind, role_part, index)``: the
    record-identifying TAIL of ``make_registry_key`` ("via"/"track", the role
    placeholder for spoke-level copper, the 0-based index in the cell's list).
    Every command of one dry run shares the same anchor and template (the
    recording record's identity is the template part), so that tail tells the
    records apart — the ONE association the plan allows ("запись ↔ команда — по
    индексу в ключе реестра, make_registry_key").
    ``planned`` — how many commands the dry run produced (0 = the record placed
    no copper: a refused tree, an unrealized record, a chain-only placement).
    ``checked`` — True only when the dry run actually produced commands; a
    ``checked`` False presence means "we could NOT check" and the deletion rule
    must keep EVERY unpaired record."""

    on_board: frozenset = frozenset()
    planned: int = 0
    checked: bool = False

    def has(self, kind: str, role: Optional[str], index: Optional[int]) -> bool:
        """True when the record at ``index`` with ``role`` matched live copper."""
        if index is None:
            return False
        role_part = role if role is not None else SPOKE_LEVEL_ROLE_PLACEHOLDER
        return (kind, role_part, int(index)) in self.on_board


def instance_copper_presence(config_path: str, record_name: str,
                             timeout_ms: Optional[int], cell_identity: Optional[str]
                             ) -> BoardCopperPresence:
    """Dry-run ``record_name`` on the live board and report which of its planned
    copper commands found live copper (registry uuid, then geometry).

    A pipeline that cannot be prepared or planned raises — the CALLER turns that
    into "could not check" (a fresh ``BoardCopperPresence()``), never a deletion.
    """
    from .apply_pipeline import ApplyPipeline

    pipeline = None
    try:
        pipeline = ApplyPipeline(str(config_path), only=[record_name],
                                 dry_run=True, timeout_ms=timeout_ms)
        pipeline.run()
        vias, tracks = pipeline.plan_copper()
        return _presence_for(pipeline.adapter, str(config_path), cell_identity,
                             vias, tracks)
    finally:
        if pipeline is not None:
            pipeline.close()


def _presence_for(adapter, config_path: str, cell_identity: Optional[str],
                  planned_vias, planned_tracks) -> BoardCopperPresence:
    """The read-only match of one dry run's commands against the live board."""
    via_path, trk_path = registry_paths_for_config(config_path)
    via_reg = PlacementRegistry(adapter, via_path)
    trk_reg = TrackRegistry(adapter, trk_path)

    def _live(getter):
        fn = getattr(adapter, getter, None)
        return list(fn() or ()) if fn else []

    on_board: set = set()
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
            on_board.add((kind, parts[2], index))
    planned = len(planned_vias or ()) + len(planned_tracks or ())
    return BoardCopperPresence(on_board=frozenset(on_board), planned=planned,
                               checked=planned > 0)
