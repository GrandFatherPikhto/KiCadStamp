# gui/select_cell.py
"""Н5 — ONE "Select cell" implementation for every door
(plan_2026_10_04_refresh_mixed_cluster_selection, Н5; Denis 2026-10-05).

"Select cell" highlights on the board exactly what a read WOULD read: the
chosen instance's components (from the BOARD, `resolve_context_footprints`) PLUS
the copper the registries recorded for THIS cell at THIS instance — an
`anchor:<ref>` piece of its own instance, never copper of another record. The
"which copper is mine" rule is the SAME `is_own_key` the mixed-read subtraction
uses, not a copy.

The pure half (instance resolution + the ownership filter) lives here so the
tree menu item, the DockHub delegate and the CellDock button can all call it;
the live reads/`adapter.select_items` stay in the worker (Qt-free by design,
like gui/mixed_selection.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from kicadstamp.cell_instance import resolve_context_footprints
from kicadstamp.i18n import _
from kicadstamp.registry import (
    load_registry_entries,
    record_key_part,
)
from kicadstamp.selection_narrowing import (
    cell_record_addresses,
    is_own_key,
)


def cell_instances(cfg, cell_name: str, remembered_cluster=None,
                   remembered_sheet=None) -> list:
    """[(cluster, sheet)] a "Select cell" action may target for `cell_name`.

    The remembered context wins (it is what the user last worked with); else
    every config record that PLACES the cell (entities + clone_placements,
    materialized copies included), deduped in file order. Empty -> the cell is
    placed by no record and has no context: nothing to guess."""
    if not cell_name:
        return []
    if remembered_cluster:
        return [(str(remembered_cluster), remembered_sheet)]
    out: list = []
    seen: set = set()
    for attr in ("entities", "clone_placements"):
        for rec in getattr(cfg, attr, ()) or ():
            if getattr(rec, "cell", None) != cell_name:
                continue
            cluster = getattr(rec, "cluster", None)
            if not cluster:
                continue
            key = (str(cluster), getattr(rec, "sheet", None))
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def effective_instance(cluster, sheet, remembered_cluster,
                       remembered_sheet) -> tuple:
    """Н5-2: the instance a "Select cell" action runs on.

    An EXPLICIT cluster is taken AS-IS — its own sheet, even None — never
    overridden by the remembered one (else picking "DAC_BUF on Channel_1" while
    Channel_0 is remembered would select Channel_0). Without an explicit cluster
    the REMEMBERED PAIR is used."""
    if cluster is not None:
        return cluster, sheet
    return remembered_cluster, remembered_sheet


@dataclass(frozen=True)
class InstanceChoice:
    """The ONE decision "which instance of this cell does an action target"
    (Р2а-3), shared by "Select cell" and "Explode…".

    kind: "explicit"  — an explicit (cluster, sheet) was given, used as-is;
          "remembered" — the cell's remembered (cluster, sheet);
          "single"      — the ONE config record that places the cell;
          "choose"      — several records: `candidates` to pick from (submenu);
          "none"        — nothing to choose (identified refs win; no cluster);
          "no-record"   — no record places the cell: `message` says so."""

    kind: str
    cluster: Optional[str] = None
    sheet: object = None
    candidates: tuple = ()
    message: str = ""


def resolve_action_instance(cfg, root_path, cell_name, cluster=None, sheet=None,
                            refs=None) -> InstanceChoice:
    """The ONE instance-resolution rule for BOTH doors (Р2а-3): an explicit
    address is taken as-is; else the remembered context; else (identified `refs`
    make the cluster optional) "none"; else the config's own records — one is
    "single", several are "choose", none is "no-record". Qt-free: the caller
    decides what each kind means (a submenu is `pick_instance`)."""
    from .cell_edit_context import remembered_cell_edit_context
    if cluster is not None:
        chosen_cluster, chosen_sheet = effective_instance(cluster, sheet, None, None)
        return InstanceChoice("explicit", chosen_cluster, chosen_sheet)
    remembered_cluster, remembered_sheet = remembered_cell_edit_context(
        root_path, cell_name)
    if remembered_cluster:
        return InstanceChoice("remembered", str(remembered_cluster), remembered_sheet)
    if refs:
        # Identified refs (Н5б) make the Cluster optional — nothing to choose.
        return InstanceChoice("none")
    instances = cell_instances(cfg, cell_name)
    if not instances:
        return InstanceChoice("no-record", message=_(
            "No remembered cluster for cell {name!r} — extract it from a board "
            "cluster, or remember one via the cell-anchor page’s “Fill from "
            "selection”.").format(name=cell_name))
    if len(instances) == 1:
        return InstanceChoice("single", instances[0][0], instances[0][1])
    return InstanceChoice("choose", candidates=tuple(instances))


def pick_instance(parent, candidates, on_pick) -> None:
    """The ONE per-instance submenu ("<cluster> on <sheet>"), shared by both
    doors. Qt stays here (lazy import) so the resolver above is import-clean."""
    from PyQt6.QtGui import QCursor
    from PyQt6.QtWidgets import QMenu
    menu = QMenu(parent)
    for cluster, sheet in candidates:
        action = menu.addAction(_("{cluster} on {sheet}").format(
            cluster=cluster,
            sheet=sheet if sheet is not None else _("(no sheet)")))
        action.triggered.connect(
            lambda checked=False, c=cluster, s=sheet: on_pick(c, s))
    menu.exec(QCursor.pos())


@dataclass
class SelectPlan:
    """What "Select cell" should highlight on the board.

    footprints — the instance's board components.
    copper — the live via/track items the registries recorded for THIS cell at
        THIS instance (its own `anchor:<ref>` copper included).
    missing_registry — registry uuids of this cell that are NOT on the board
        (reported as a number, never an error).
    line — the honest Log line with the counts."""

    footprints: list
    copper: list
    missing_registry: int = 0
    line: str = ""


def select_cell_targets(adapter, cfg, config_path: str, cell_name: str,
                        cluster: str, sheet, sheet_names,
                        own_refs=None) -> SelectPlan:
    """The components + OWN copper of the (cluster, sheet) instance."""
    board_footprints = adapter.get_footprints()
    footprints = resolve_context_footprints(
        adapter, board_footprints, cluster, sheet, sheet_names)
    refs = frozenset(
        own_refs if own_refs is not None
        else [getattr(f, "ref", None) for f in footprints if getattr(f, "ref", None)])

    cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
    cell_uuid = getattr(cell, "uuid", None) if cell is not None else None
    cell_identity = record_key_part(cell_name, cell_uuid)
    own_addresses = cell_record_addresses(cfg, cell_name)
    chosen_address = (cluster, sheet)
    _via_e, _trk_e, owner = load_registry_entries(config_path, cfg)

    # A fake/older adapter may not expose the copper reads; "no copper" is then
    # the honest answer (the components are still selected).
    _get_vias = getattr(adapter, "get_vias", None)
    _get_tracks = getattr(adapter, "get_tracks", None)
    live_vias = {getattr(v, "uuid", None): v
                 for v in (_get_vias() if _get_vias else [])}
    live_tracks = {getattr(t, "uuid", None): t
                   for t in (_get_tracks() if _get_tracks else [])}
    copper: list = []
    missing = 0
    for uuid, key in owner.items():
        if not is_own_key(key, cell_identity, own_addresses, chosen_address, refs):
            continue
        item = live_vias.get(uuid) or live_tracks.get(uuid)
        if item is None:
            missing += 1
        else:
            copper.append(item)

    line = _("selected: {components} component(s), {vias} via(s), "
             "{tracks} track(s) — {cell} on {sheet}").format(
        components=len(footprints), vias=_count_kind(copper, "via"),
        tracks=_count_kind(copper, "track"), cell=cell_name,
        sheet=sheet if sheet is not None else _("(no sheet)"))
    if missing:
        line += _("; the registry remembers {n} more, not on the board").format(
            n=missing)
    return SelectPlan(footprints=footprints, copper=copper,
                      missing_registry=missing, line=line)


def _count_kind(items, kind: str) -> int:
    from kicadstamp.domain.board import Track, Via
    cls = Via if kind == "via" else Track
    return sum(1 for i in items if isinstance(i, cls))
