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

A CELL'S COPPER HAS TWO LEVELS, and the map key says which. The dry run plans the
cell's own copper under ``make_registry_key(anchor, cell, None, i)`` — the
role part is ``SPOKE_LEVEL_ROLE_PLACEHOLDER`` and ``i`` is the index in the cell's
own ``vias``/``tracks`` — AND each component's copper under
``make_registry_key(anchor, cell, comp.role, i)``, where ``i`` is the index in THAT
COMPONENT's ``vias``/``tracks``. So the record is resolved by the WHOLE key: the
placeholder takes the cell's list, a role takes the list of the component carrying
that role. Dropping the role part (the first version did) removed ``cell_vias[0]``
when a COMPONENT's via 0 was selected — a silent, wrong record. A role the cell
does not have, or an index past the list, removes nothing and is reported as "not
a record of the cell".

Pure and Qt-free: it takes the map, the cell's own record lists (and its
components), and the uuids of the selected copper, and returns the records to
remove (the SAME dicts, so the caller drops them by identity) plus the counters its
Log lines need. It writes NOTHING — not the board, not the registry, not the
config — and it never touches components: only copper is subtracted.
"""
from __future__ import annotations

from dataclasses import dataclass

from .constants import SPOKE_LEVEL_ROLE_PLACEHOLDER

__all__ = [
    "SubtractionOutcome",
    "matched_instance_labels",
    "plan_subtraction",
]

_RECORD_LISTS = {"via": "vias", "track": "tracks"}


@dataclass(frozen=True)
class SubtractionOutcome:
    """What «Subtract selected copper» decided.

    removed — ((kind, record), ...): the cell's own records whose live copper IS
        in the selection. The records are the SAME dicts the caller holds, so it
        drops exactly them (by identity).
    not_ours — how many selected copper items are NOT records of this cell (they
        are ignored, and the Log says so instead of silently dropping them). A map
        entry that cannot be resolved to a record (an unknown role, an index past
        the list) lands here too: it is not this cell's record either.
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


def _record_bucket(kind: str, role_part, cell_vias, cell_tracks, by_role):
    """The record LIST the key's role part points at, or None when the cell has no
    such record: the cell's own list for the spoke-level placeholder, else the
    component's list for the role the key names.

    ONE place: the key grammar is read here only (``kind`` names the via/track
    list, ``role_part`` names the level).
    """
    name = _RECORD_LISTS.get(kind)
    if name is None:
        return None
    if role_part in (None, "", SPOKE_LEVEL_ROLE_PLACEHOLDER):
        return cell_vias if name == "vias" else cell_tracks
    component = by_role.get(str(role_part))
    return None if component is None else (component.get(name) or [])


def plan_subtraction(record_map, cell_vias, cell_tracks,
                     selected_via_uuids, selected_track_uuids,
                     selected_components: int = 0, components=()) -> SubtractionOutcome:
    """Which records the selection names, and the counters for the Log lines.

    A record goes when the LIVE COPPER its own dry run matched is one of the
    selected items — never because it merely sits near the selection. The record is
    found by the WHOLE map key ``(kind, role_part, index)``: the cell's own list for
    the placeholder, the component's list for a role (see the module docstring).
    """
    by_role: dict = {}
    for comp in components or ():
        role = comp.get("role") if isinstance(comp, dict) else None
        if role:
            by_role.setdefault(str(role), comp)
    selected = {"via": set(selected_via_uuids or ()),
                "track": set(selected_track_uuids or ())}
    ours: set = set()
    removed: list = []
    for key, uuid in (getattr(record_map, "by_record", None) or {}).items():
        try:
            kind, role_part, index = key
        except (TypeError, ValueError):       # a foreign key shape: not a record
            continue
        if kind not in selected or uuid is None:
            continue
        bucket = _record_bucket(kind, role_part, cell_vias, cell_tracks, by_role)
        if bucket is None or index < 0 or index >= len(bucket):
            # An unknown role / an index past the list: NOT this cell's record, so
            # it is reported as "ignored" rather than removed from a wrong list.
            continue
        ours.add(uuid)
        if uuid not in selected[kind]:
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
