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


def _raw_entry(path, identity: str):
    """The RAW ``net_traces`` entry with this identity in `path`, or None."""
    for item in (_read_data(Path(path)).get("net_traces") or []):
        if isinstance(item, dict) and _raw_identity(item) == identity:
            return item
    return None


def _records_of(cfg, identity: str) -> list:
    """Every loaded record with this identity — the template AND its
    materialized ``tree_instances`` copies."""
    return [nt for nt in (getattr(cfg, "net_traces", None) or ())
            if net_trace_effective_name(nt) == identity]


def precheck_transfers(cfg, transfers, entry_files) -> list[str]:
    """EVERY check that can refuse a transfer, BEFORE anything is written.

    Returns the refusal lines when the WHOLE read must be refused (an empty list
    means "all performable"). A read that cannot hand a piece over must not give it
    to the cell either: the piece would end up owned by two records and the redraw
    would draw a copy of it (Р3а-2).

    ``entry_files`` is {identity: physical file} — the caller finds each record's
    file across the include graph (``gui/docks/rename.find_list_entry_file``, the
    ONE host of that rule); the kernel never walks the GUI graph itself."""
    transfers = list(transfers or ())
    if not transfers:
        return []
    files = dict(entry_files or {})
    refusals: list[str] = []
    for identity in sorted({tr.identity for tr in transfers}):
        path = files.get(identity)
        if not path:
            refusals.append(_(
                "net_traces {name}: the record's file was not found in the "
                "project graph — the read is refused, nothing was changed").format(
                name=identity))
            continue
        entry = _raw_entry(path, identity)
        if entry is None:
            refusals.append(_(
                "net_traces {name}: the record is not in {path} — the read is "
                "refused, nothing was changed").format(
                name=identity, path=str(path)))
            continue
        if not _copies_are_one_to_one(_records_of(cfg, identity), entry):
            refusals.append(_(
                "net_traces {name}: a tree_instances copy is not 1:1 with the "
                "template — the read is refused, nothing was changed").format(
                name=identity))
    return refusals


def _section_of(kind: str) -> str:
    """The ``net_traces`` list a piece of this kind lives in."""
    return "vias" if "via" in str(kind).lower() else "tracks"


def apply_transfers(config_path, cfg, transfers, *, entry_files=None) -> list[str]:
    """Let the ``net_traces`` records of ``transfers`` give their pieces away.

    ``entry_files`` is {identity: physical file} (Р3а-2) — each record is edited in
    ITS OWN file, so a record living in an include is handled like one in the root.

    EVERY check runs first (`precheck_transfers`): when any transfer cannot be
    performed, its refusal lines are returned and NOTHING is written — not one
    record, not one registry key. A half-applied transfer would leave two owners.

    Returns the yellow Log lines (an emptied record, the affected copies)."""
    transfers = list(transfers or ())
    if not transfers:
        return []
    refusals = precheck_transfers(cfg, transfers, entry_files)
    if refusals:
        return refusals
    files = dict(entry_files or {})

    by_identity: dict[str, dict[str, set]] = defaultdict(
        lambda: {"vias": set(), "tracks": set()})
    for tr in transfers:
        by_identity[tr.identity][_section_of(tr.kind)].add(tr.index)

    via_path, trk_path = registry_paths_for_config(
        str(config_path), getattr(cfg, "registry_path", None),
        getattr(cfg, "track_registry_path", None))
    via_entries = load_registry(via_path)
    track_entries = load_track_registry(trk_path)

    lines: list[str] = []
    release: list[str] = []
    for identity, sections in sorted(by_identity.items()):
        # Prechecked: the file exists and holds the record, and every copy is 1:1.
        path = files[identity]
        entry = _raw_entry(path, identity)
        new_entry = copy.deepcopy(entry)
        for section, indices in sections.items():
            items = new_entry.get(section)
            if not isinstance(items, list):
                continue
            new_entry[section] = [item for i, item in enumerate(items)
                                  if i not in indices]
        upsert_list_entry(Path(path), "net_traces", new_entry,
                          key_fn=_raw_identity)

        records = _records_of(cfg, identity)
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
