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

from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.config.models import (
    clone_placement_effective_name,
    entity_effective_name,
)
from kicadstamp.i18n import _
from kicadstamp.registry import (
    PlacementRegistry,
    TrackRegistry,
    load_registry_entries,
    registry_paths_for_config,
)
from kicadstamp.registry_match import (
    TIER_REGISTRY,
    match_planned_copper,
)
from kicadstamp.selection_narrowing import (
    is_own_key,
    record_address_matches,
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
    copper — the live via/track items recorded for THIS cell at THIS instance
        (its own `anchor:<ref>` copper included), found by registry then by
        geometry.
    missing_registry — registry uuids of this cell that are NOT on the board
        (reported as a number, never an error).
    line — the honest Log line with the counts.

    СЦ-2 counters (the read path): copper_by_registry ('R'), copper_by_geometry
    ('G'), copper_missing ('K' — recorded by the planner but not on the board),
    copper_planned (how many commands the redraw planner produced)."""

    footprints: list
    copper: list
    missing_registry: int = 0
    line: str = ""
    copper_by_registry: int = 0
    copper_by_geometry: int = 0
    copper_missing: int = 0
    copper_planned: int = 0


def select_cell_targets(adapter, cfg, config_path: str, cell_name: str,
                        cluster: str, sheet, sheet_names,
                        own_refs=None) -> SelectPlan:
    """The components + OWN copper of the (cluster, sheet) instance.

    The ownership loop lives in the CORE now (plan_2026_10_07_refused_tree_matching):
    ``absent_copper_prune.own_registry_entries`` runs the ONE ``is_own_key`` filter
    over the SAME key tail — the registry-only map of «Subtract selected copper»
    calls it too, so the fallback and this read can never disagree."""
    from kicadstamp.absent_copper_prune import (
        own_instance_context,
        own_registry_entries,
    )

    footprints, cell_identity, own_addresses, chosen_address, refs = \
        own_instance_context(adapter, cfg, cell_name, cluster, sheet,
                             sheet_names=sheet_names, own_refs=own_refs)
    via_entries, track_entries, _owner = load_registry_entries(config_path, cfg)

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
    for kind, _role_part, _index, uuid in own_registry_entries(
            via_entries, track_entries, cell_identity, own_addresses,
            chosen_address, refs):
        item = (live_vias if kind == "via" else live_tracks).get(uuid)
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
                      missing_registry=missing,
                      copper_by_registry=len(copper),
                      copper_planned=len(copper) + missing, line=line)


def _count_kind(items, kind: str) -> int:
    from kicadstamp.domain.board import Track, Via
    cls = Via if kind == "via" else Track
    return sum(1 for i in items if isinstance(i, cls))


def instance_recording_name(cfg, cell_name: str, cluster, sheet) -> Optional[str]:
    """The name of the entity / clone_placement record that PLACES `cell_name`
    on the (cluster, sheet) instance — the ``--only`` identity the redraw planner
    needs to plan exactly this record's copper.

    Entities win over clone_placements (an Entity is the tree-materialized
    owner). The address comparison is the ONE product rule
    (``record_address_matches``: cluster by prefix, sheet only when both carry
    one) — no second comparison here. None when NO record places the cell at
    that instance (e.g. only a chain spoke does): the caller then skips geometry
    and reports it, it never guesses."""
    if not cell_name:
        return None
    chosen = (cluster, sheet)
    for e in (getattr(cfg, "entities", ()) or ()):
        if getattr(e, "cell", None) != cell_name or getattr(e, "retired", False):
            continue
        if record_address_matches((getattr(e, "cluster", None),
                                   getattr(e, "sheet", None)), chosen):
            return entity_effective_name(e)
    for c in (getattr(cfg, "clone_placements", ()) or ()):
        if getattr(c, "cell", None) != cell_name or getattr(c, "retired", False):
            continue
        if record_address_matches((getattr(c, "cluster", None),
                                   getattr(c, "sheet", None)), chosen):
            return clone_placement_effective_name(c)
    return None


def instance_placed_by_chain(cfg, cell_name: str, cluster) -> bool:
    """True when a NON-retired chain spoke places `cell_name` at `cluster`.

    Chains are NOT planned per instance by the "Select cell" read (that is a
    separate task, «Разнос» Р4-2): a cell placed only by a spoke gets
    registry-only copper and an honest Log line, never a guessed geometry."""
    if not cell_name:
        return False
    for chain in (getattr(cfg, "chains", ()) or ()):
        for spoke in (getattr(chain, "spokes", ()) or ()):
            if getattr(spoke, "cell", None) != cell_name:
                continue
            if getattr(spoke, "retired", False) or getattr(spoke, "skip", False):
                continue
            if cluster and cluster_prefix_match(
                    str(getattr(spoke, "cluster", None) or ""), cluster):
                return True
    return False


def select_cell_copper_targets(adapter, cfg, config_path: str, cell_name: str,
                               cluster, sheet, sheet_names,
                               planned_vias, planned_tracks,
                               own_refs=None) -> SelectPlan:
    """The components + the RECORDED copper of the (cluster, sheet) instance.

    The copper is found in two tiers over the commands the REDRAW PLANNER
    produced for THIS one record (``ApplyPipeline.plan_copper`` — the same
    planning a real apply / dry run uses, so a preview and a selection can never
    disagree), matched with ``registry_match.match_planned_copper`` — the SAME
    routine the adoption path calls:

      * tier 1 — REGISTRY (``is_own_key``, the unchanged ownership rule): 'R';
      * tier 2 — GEOMETRY, for what the registry did not find: 'G'.

    A live item the registry owns under ANOTHER record is never taken by
    geometry. A command found by neither tier is recorded-but-absent ('K').
    Nothing is written anywhere (the registry is read, never saved; the board is
    selected, never edited)."""
    from kicadstamp.absent_copper_prune import own_instance_context

    footprints, cell_identity, own_addresses, chosen_address, refs = \
        own_instance_context(adapter, cfg, cell_name, cluster, sheet,
                             sheet_names=sheet_names, own_refs=own_refs)

    _get_vias = getattr(adapter, "get_vias", None)
    _get_tracks = getattr(adapter, "get_tracks", None)
    live_vias = list(_get_vias() if _get_vias else [])
    live_tracks = list(_get_tracks() if _get_tracks else [])

    via_path, trk_path = registry_paths_for_config(
        str(config_path), getattr(cfg, "registry_path", None),
        getattr(cfg, "track_registry_path", None))
    via_reg = PlacementRegistry(adapter, via_path)
    trk_reg = TrackRegistry(adapter, trk_path)

    matches = list(match_planned_copper(via_reg, planned_vias, live_items=live_vias))
    matches += list(match_planned_copper(trk_reg, planned_tracks, live_items=live_tracks))

    copper: list = []
    seen: set = set()
    by_registry = by_geometry = missing = 0
    for m in matches:
        key = getattr(m.command, "registry_key", None)
        if not is_own_key(key, cell_identity, own_addresses, chosen_address, refs):
            continue
        if m.live is None:
            missing += 1
            continue
        uuid = getattr(m.live, "uuid", None)
        if uuid is not None and uuid in seen:
            continue
        if uuid is not None:
            seen.add(uuid)
        if m.tier == TIER_REGISTRY:
            by_registry += 1
        else:
            by_geometry += 1
        copper.append(m.live)

    line = _("Select cell: {components} component(s); copper — {by_registry} by "
             "registry, {by_geometry} by geometry; recorded but not on the board "
             "— {missing}").format(
        components=len(footprints), by_registry=by_registry,
        by_geometry=by_geometry, missing=missing)
    return SelectPlan(footprints=footprints, copper=copper,
                      copper_by_registry=by_registry,
                      copper_by_geometry=by_geometry,
                      copper_missing=missing,
                      copper_planned=len(planned_vias) + len(planned_tracks),
                      line=line)


def cell_has_recorded_copper(cell) -> bool:
    """True when the cell's RECORD carries copper (cell-level vias/tracks or any
    component slot's vias) — used to tell "the planner gave nothing" (СЦ-4-2,
    worth an honest line) from "the cell never had copper" (components only)."""
    if cell is None:
        return False
    if getattr(cell, "vias", None) or getattr(cell, "tracks", None):
        return True
    return any(getattr(s, "vias", None)
               for s in (getattr(cell, "components", ()) or ()))


def no_planned_copper_targets(adapter, cfg, config_path: str, cell_name: str,
                              cluster, sheet, sheet_names, record_name: str,
                              own_refs=None) -> SelectPlan:
    """СЦ-4-2: the record carries copper, but the redraw planner produced NO
    command for it (a refused tree, an unrealized record). Then the honest
    answer is REGISTRY-ONLY copper plus a line that says so — never the lying
    "recorded but not on the board — 0". The registry scan is the SAME
    ``select_cell_targets`` (no second copy); only its line is replaced."""
    plan = select_cell_targets(adapter, cfg, config_path, cell_name, cluster,
                               sheet, sheet_names, own_refs=own_refs)
    plan.line = _("Select cell: {components} component(s); the redraw planner "
                  "produced no copper for record {record} (reason — in the Log "
                  "above); copper — registry only: {r}").format(
        components=len(plan.footprints), record=record_name,
        r=plan.copper_by_registry)
    return plan
