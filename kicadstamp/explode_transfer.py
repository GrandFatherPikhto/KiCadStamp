# kicadstamp/explode_transfer.py
"""Р3-1 of plan ``plan_2026_10_05_explode_r2_r3_tab_and_reread.md`` (design РЗ9):
hand the TRANSFERRED copper pieces from their ``net_traces`` records to the cell.

While the clusters are exploded a cell re-read may NOT subtract the selected
inter-cluster copper (the cell needs it): each such piece becomes a new record of
the cell, and its former ``net_traces`` record must let it go — otherwise two
records own one piece and the next redraw draws a copy of it.

The rule (plan Р3-3), decided 05.10.2026:

* the FILE's record is edited — the pieces leave its ``tracks`` / ``vias`` — through
  the SAME write path the ``net_traces`` dock's Save uses (``upsert_list_entry``),
  so the record's ``uuid`` survives (the "replace by name from the form" path would
  lose it — see plan_2026_10_05_uuid_tails.md ч.0);
* the registry keys of that record are **RELEASED, not renumbered**: every
  ``net_trace_registry_key(nt, i)`` of the record AND of every ``tree_instances``
  copy is deleted from both registries. Why releasing is safe: the redraws reclaim
  "their" copper by exact geometry (``adopt_net_trace_copper`` /
  ``adopt_matching_unowned``, before ``reconcile``, on every run), so the remaining
  pieces are re-owned under fresh numbers and the transferred one is claimed by the
  cell's redraw. It also SELF-HEALS: if the working set is dropped without a Save,
  the file's record is unchanged and the redraw takes all its pieces by geometry
  again — renumbering would leave keys pointing at other records' copper;
* a record left with NO pieces is NOT deleted silently: a yellow line says so.

QT-FREE, no widget and no board: it edits the config file and the two registry
files only (the board is not touched — the copper stays where it is).
"""
from __future__ import annotations

import copy
import logging
from collections import defaultdict
from pathlib import Path

from .config_writer import _read_data, upsert_list_entry
from .i18n import _
from .net_trace_planner import net_trace_registry_key
from .config import net_trace_effective_name
from .registry import (
    load_registry,
    load_track_registry,
    save_registry,
    save_track_registry,
)
from .selection_narrowing import NetTraceTransfer
from .utils.paths import registry_paths_for_config

logger = logging.getLogger(__name__)

__all__ = ["NetTraceTransfer", "apply_transfers"]


def _raw_identity(entry: dict) -> str:
    """The raw-dict identity of a ``net_traces`` entry — the mirror of
    ``net_trace_effective_name`` (``name:``, else the legacy ``net:``)."""
    return str(entry.get("name") or entry.get("net") or "")


def _section_of(kind: str) -> str:
    """The ``net_traces`` list a piece of this kind lives in."""
    return "vias" if "via" in str(kind).lower() else "tracks"


def apply_transfers(config_path, cfg, transfers) -> list[str]:
    """Let the ``net_traces`` records of ``transfers`` give their pieces away.

    Returns the yellow Log lines (an emptied record, the affected copies). Nothing
    is touched when there are no transfers. Only the records named by ``transfers``
    are edited — never another record's copper."""
    transfers = list(transfers or ())
    if not transfers:
        return []

    by_identity: dict[str, dict[str, set]] = defaultdict(
        lambda: {"vias": set(), "tracks": set()})
    for tr in transfers:
        by_identity[tr.identity][_section_of(tr.kind)].add(tr.index)

    raw = _read_data(Path(config_path))
    raw_entries = raw.get("net_traces") or []
    records_all = list(getattr(cfg, "net_traces", None) or ())

    via_path, trk_path = registry_paths_for_config(
        str(config_path), getattr(cfg, "registry_path", None),
        getattr(cfg, "track_registry_path", None))
    via_entries = load_registry(via_path)
    track_entries = load_track_registry(trk_path)

    lines: list[str] = []
    release: list[str] = []
    for identity, sections in sorted(by_identity.items()):
        entry = next((e for e in raw_entries
                      if isinstance(e, dict) and _raw_identity(e) == identity), None)
        if entry is None:
            lines.append(_("net_traces {name}: the record is not in {path} — its "
                           "pieces were left as they are").format(
                name=identity, path=str(config_path)))
            continue
        records = [nt for nt in records_all
                   if net_trace_effective_name(nt) == identity]
        # The plan's guard (Р3-3 п.3): the FILE record is edited by INDEX, which is
        # only correct while every materialized copy has the SAME list lengths as
        # the template. A copy that is not 1:1 means the index means something else
        # — refuse this record instead of editing the wrong piece.
        if not _copies_are_one_to_one(records, entry):
            lines.append(_("net_traces {name}: a tree_instances copy is not 1:1 "
                           "with the template — left as it is, tell the developer").format(
                name=identity))
            continue

        new_entry = copy.deepcopy(entry)
        for section, indices in sections.items():
            items = new_entry.get(section)
            if not isinstance(items, list):
                continue
            new_entry[section] = [item for i, item in enumerate(items)
                                  if i not in indices]
        upsert_list_entry(Path(config_path), "net_traces", new_entry,
                          key_fn=_raw_identity)

        # Р3-3 п.2: RELEASE every key of the record and of its copies.
        for nt in records:
            for i in range(len(getattr(nt, "vias", ()) or ())
                           + len(getattr(nt, "tracks", ()) or ())):
                release.append(net_trace_registry_key(nt, i))

        if not (new_entry.get("vias") or new_entry.get("tracks")):
            lines.append(_("net_traces {name} is now empty — delete the record by "
                           "hand").format(name=identity))
        if len(records) > 1:
            lines.append(_("the transfer also applies to its {n} tree_instances "
                           "cop(y/ies)").format(n=len(records) - 1))

    if release:
        dropped = 0
        for key in release:
            if via_entries.pop(key, None) is not None:
                dropped += 1
            if track_entries.pop(key, None) is not None:
                dropped += 1
        if dropped:
            save_registry(via_path, via_entries)
            save_track_registry(trk_path, track_entries)
            logger.info("explode transfer: released %d registry key(s)", dropped)

    return lines


def _copies_are_one_to_one(records, entry: dict) -> bool:
    """True when every materialized copy's ``vias``/``tracks`` have the same length
    as the FILE record's (the precondition for editing it by index)."""
    want = (len(entry.get("vias") or []), len(entry.get("tracks") or []))
    for nt in records:
        got = (len(getattr(nt, "vias", ()) or ()),
               len(getattr(nt, "tracks", ()) or ()))
        if got != want:
            return False
    return True
