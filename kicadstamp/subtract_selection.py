# kicadstamp/subtract_selection.py
"""С-2 «Subtract selected copper» — which cell records does the CURRENT selection
name? (plan_2026_10_06_prune_absent_cell_copper; Denis 2026-10-06).

Denis: «можем сделать, как в графическом редакторе: Добавить выделенное / Вычесть
выделенное / Строго по выделению». The three actions over a cell's records:

    строго по выделению  «Update from selection»  — the cell BECOMES the selection
    добавить выделенное  «Add selected copper»    — only ADDS
    вычесть выделенное   «Subtract selected copper» — removes the records the
                            selection names, touches nothing else

The pair here is EXACT, never "nearest". The refresh read pairs greedily by the
nearest live item (`cell_geometry_refresh._greedy_nearest`) — right for updating
geometry, DANGEROUS for deletion: a foreign track 0.1 mm away from a record would
delete that record. So the subtraction uses the pairing «Select cell» uses — the
record -> live copper map of the instance
(`absent_copper_prune.instance_record_copper_map`: a dry run of the recording,
matched by the registry uuid and then by exact geometry). The invariant:

    you can subtract exactly what «Select cell» would highlight.

Pure and Qt-free: it takes the map, the cell's OWN record lists and the uuids of
the selected copper, and returns the records to remove (the SAME dicts, so the
caller drops them by identity) plus the counters its Log lines need. It writes
NOTHING — not the board, not the registry, not the config — and it never touches
components: only copper is subtracted.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SubtractionOutcome",
    "matched_instance_labels",
    "plan_subtraction",
]


@dataclass(frozen=True)
class SubtractionOutcome:
    """What «Subtract selected copper» decided.

    removed — ((kind, record), ...): the cell's own records whose live copper IS
        in the selection. The records are the SAME dicts the caller holds, so it
        drops exactly them (by identity).
    not_ours — how many selected copper items are NOT records of this cell (they
        are ignored, and the Log says so instead of silently dropping them).
    components — how many selected items are components (ignored: copper only).
    planned — how many commands the instance's dry run produced.
    empty — the dry run planned NOTHING (a refused tree, an unrealized record, a
        chain-only placement): nothing may be removed and the caller says why.
    """

    removed: tuple = ()
    not_ours: int = 0
    components: int = 0
    planned: int = 0
    empty: bool = False

    @property
    def removed_count(self) -> int:
        return len(self.removed)

    def removed_of(self, kind: str) -> list:
        """The removed records of ONE kind, in the order they were found."""
        return [rec for k, rec in self.removed if k == kind]


def plan_subtraction(record_map, cell_vias, cell_tracks,
                     selected_via_uuids, selected_track_uuids,
                     selected_components: int = 0) -> SubtractionOutcome:
    """Which records the selection names, and the counters for the Log lines.

    A record goes when the LIVE COPPER its own dry run matched is one of the
    selected items — never because it merely sits near the selection.
    """
    vias = list(cell_vias or ())
    tracks = list(cell_tracks or ())
    selected = {"via": set(selected_via_uuids or ()),
                "track": set(selected_track_uuids or ())}
    buckets = {"via": vias, "track": tracks}
    ours: set = set()
    removed: list = []
    for key, uuid in (getattr(record_map, "by_record", None) or {}).items():
        try:
            kind, _role_part, index = key
        except (TypeError, ValueError):       # a foreign key shape: not a record
            continue
        if kind not in buckets or uuid is None:
            continue
        ours.add(uuid)
        bucket = buckets[kind]
        if uuid not in selected[kind] or index < 0 or index >= len(bucket):
            continue
        removed.append((kind, bucket[index]))
    selected_all = selected["via"] | selected["track"]
    planned = int(getattr(record_map, "planned", 0) or 0)
    return SubtractionOutcome(
        removed=tuple(removed),
        not_ours=len(selected_all - ours),
        components=int(selected_components or 0),
        planned=planned,
        empty=planned == 0)


def matched_instance_labels(candidates, selected_uuids) -> tuple:
    """Which candidate instances' copper IS in the selection.

    ``candidates`` — ((label, record_map), ...): the label is what the Log names
    (already human — e.g. "DAC_BUF on Channel_1"), the map is that instance's
    `RecordCopperMap`. Returns the labels whose map holds at least one selected
    uuid, in the given order. Exactly ONE is the instance the action targets;
    two or more is an ambiguity the caller REFUSES (never a guess — deleting the
    records of the wrong instance would take copper away from a live one).
    """
    selected = {u for u in (selected_uuids or ()) if u}
    out: list = []
    for label, record_map in candidates or ():
        uuids = {u for u in (getattr(record_map, "by_record", None) or {}).values()
                 if u}
        if uuids & selected:
            out.append(label)
    return tuple(out)
