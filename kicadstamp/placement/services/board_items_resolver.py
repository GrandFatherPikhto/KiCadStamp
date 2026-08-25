# kicadstamp/placement/services/board_items_resolver.py
"""
board_items_resolver.py — "which live-board items belong to this placement".

One operation, two consumers (see
techdocs/handoff/deepseek/handoff_2026_08_25_clone_item_resolver_select_and_reextract.md):

  1. PlacerDock's "Select on board" button — the resolved items go straight
     to adapter.select_items() so the user can visually verify what a
     ClonePlacement/CoordinatePlacement really owns on the live board.
  2. ExtractDock's "Re-extract from current board state" — the resolved items
     are passed as extract_template_from_selection(items=...) instead of
     adapter.get_selected_items(), so an already-saved extract profile can be
     re-captured after board edits WITHOUT re-selecting everything by hand.

Components — reused machinery, not re-implemented: the same
resolve_roles_by_nets()/resolve_roles_by_selection() branch the real apply
path uses (clone_uses_selection_mode, see clone_position_calculator.py), then
role -> ref -> Footprint via adapter.get_footprint() (which itself reads the
cached adapter.get_footprints() index — no extra linear scan here).

Copper (ClonePlacement only) — the registry is the source of truth, geometry
is NOT recomputed: every via/track ever created by this clone is keyed
f"{anchor_id}|{template}|{role}|{index}" (make_registry_key, registry.py),
where anchor_id = clone_anchor_id(clone). Filtering both registry files by
the f"{anchor_id}|" prefix yields exactly this clone's top-level copper UUIDs
(nested clone_placements use f"{anchor_id}/{nested.name}|..." and are
deliberately excluded by the pipe). UUID -> live item via
adapter.get_items_by_id() (one IPC call), falling back to a single
get_vias()+get_tracks() scan when the adapter is a bare test double without
the point-lookup method.

CoordinatePlacement — one role, one component, no registry copper (the type
never touches registry.py — see its own docstring): resolved via the same
resolve_footprint_by_cluster_role() the "dumb placer" apply path uses.

Read-only: never moves/tags/writes anything. Raises ValidationError only for
the genuinely ambiguous/none component cases the apply path would also fail
on; an empty board side (nothing placed yet) simply yields an empty list —
the caller decides how to surface it (short Log message, not a fatal crash).
"""
import logging
from typing import Any

from ...config import (
    Cell,
    ClonePlacement,
    CoordinatePlacement,
    Config,
    RuntimeContext,
    coordinate_placement_effective_name,
)
from ...domain.board import Footprint, Track, Via
from ...exceptions import ValidationError, format_fatal_error
from ...i18n import _
from .clone_role_resolver import (
    clone_uses_selection_mode,
    resolve_roles_by_nets,
    resolve_roles_by_selection,
)
from .coordinate_position_calculator import resolve_footprint_by_cluster_role

logger = logging.getLogger(__name__)


def _resolve_clone_components(adapter, cell: Cell, clone: ClonePlacement,
                              sheet_names: dict[str, str]) -> list[Footprint]:
    """Role -> Footprint for the clone's cell, via the SAME branch the apply
    path uses (clone_uses_selection_mode). Returns footprints in cell-role
    order. Raises the same ValidationError the resolvers raise on none/
    ambiguous — callers surface it in the Log dock instead of crashing.

    clone.ignore_selection is honoured through the adapter's own
    temporarily_ignore_selection context manager when present (mirroring
    clone_position_calculator.py's apply-time scope)."""
    ignore_ctx = getattr(adapter, "temporarily_ignore_selection", None)

    def run() -> list[Footprint]:
        if clone_uses_selection_mode(clone):
            role_to_ref = resolve_roles_by_selection(
                adapter, cell, clone, sheet_names=sheet_names)
        else:
            role_to_ref = resolve_roles_by_nets(
                adapter, cell, clone, sheet_names=sheet_names)

        items: list[Footprint] = []
        for slot in cell.components:
            ref = role_to_ref.get(slot.role)
            if ref is None:
                continue
            fp = adapter.get_footprint(ref)
            if fp is not None:
                items.append(fp)
        return items

    if callable(ignore_ctx):
        with ignore_ctx(clone.ignore_selection):
            return run()
    return run()


def _live_items_by_uuid(adapter, uuids: list[str]) -> list[Any]:
    """UUID strings -> live board items. Prefers the adapter's point lookup
    (get_items_by_id, one IPC call for the whole batch); falls back to a
    single get_vias()+get_tracks() scan for bare test doubles."""
    if not uuids:
        return []
    get_by_ids = getattr(adapter, "get_items_by_id", None)
    if callable(get_by_ids):
        return list(get_by_ids(uuids) or [])
    live: dict[str, Any] = {}
    for item in list(adapter.get_vias()) + list(adapter.get_tracks()):
        live[item.uuid] = item
    return [live[u] for u in uuids if u in live]


def _resolve_clone_copper(adapter, anchor_id: str,
                          registry_path: str | None,
                          track_registry_path: str | None) -> list[Any]:
    """Live via/track objects ever created by THIS clone — by filtering both
    registry files for the f"{anchor_id}|" key prefix, then resolving each
    entry's stored UUID to a live board item. No registry file at the given
    path -> no copper (nothing was ever placed for this clone)."""
    # Local import — registry.py's own top-level import chain touches the
    # whole kicadstamp.placement package; deferred so a bare test importing
    # this module first never hits the partially-initialised-module cycle
    # (the same pattern ExtractDock's _registry_uuids documents).
    from ...registry import load_registry, load_track_registry

    uuids: list[str] = []
    prefix = f"{anchor_id}|"
    if registry_path:
        for key, entry in load_registry(registry_path).items():
            if key.startswith(prefix):
                uuids.append(entry.uuid)
    if track_registry_path:
        for key, entry in load_track_registry(track_registry_path).items():
            if key.startswith(prefix):
                uuids.append(entry.uuid)
    return _live_items_by_uuid(adapter, uuids)


def resolve_clone_board_items(
    adapter,
    cfg: Config | None,
    ctx: RuntimeContext | None,
    clone_or_coord: ClonePlacement | CoordinatePlacement,
    *,
    registry_path: str | None = None,
    track_registry_path: str | None = None,
) -> list[Footprint | Via | Track]:
    """
    The actual list of FootprintInstance/Via/Track currently belonging to
    `clone_or_coord` on the live board.

    ClonePlacement — its cell's components (resolved by the same nets/
    selection branch apply uses) plus every via/track the registry records
    under this clone's anchor_id (live UUIDs only; stale registry entries
    contribute nothing, mirroring reconcile()'s "stale is not fatal").
    CoordinatePlacement — just its single component (no registry copper).

    adapter — any IBoardAdapter duck-type (live board reads happen here).
    cfg — the loaded Config (for the clone's cell); may be None only if
        clone_or_coord is a CoordinatePlacement.
    ctx — RuntimeContext (for sheet_names; registry paths are taken from the
        explicit registry_path/track_registry_path args, so the caller
        resolves them the same way apply_pipeline._execute does).
    registry_path/track_registry_path — resolved absolute paths of the via/
        track registry files; None skips the corresponding copper lookup.

    Returns an empty list (NOT a crash) when nothing is on the board yet;
    raises ValidationError only for the same none/ambiguous component cases
    the apply path itself would fail on.
    """
    sheet_names = ctx.sheet_names if ctx is not None else {}

    if isinstance(clone_or_coord, CoordinatePlacement):
        cp = clone_or_coord
        return [resolve_footprint_by_cluster_role(
            adapter, cp.cluster, cp.role,
            coordinate_placement_effective_name(cp),
            sheet=cp.sheet, sheet_names=sheet_names)]

    clone = clone_or_coord
    cell = cfg.cells.get(clone.cell) if cfg is not None else None
    if cell is None:
        raise ValidationError(format_fatal_error(
            _("cell {cell!r} not found in config").format(cell=clone.cell),
            [_("extract/save the cell and make sure include: is wired (see Extract)")]))

    items: list[Footprint | Via | Track] = list(
        _resolve_clone_components(adapter, cell, clone, sheet_names))

    # Local import — clone_position_calculator imports registry at module
    # top; deferred for the same reason as the registry import above.
    from .clone_position_calculator import clone_anchor_id

    anchor_id = clone_anchor_id(clone)
    items.extend(_resolve_clone_copper(adapter, anchor_id, registry_path, track_registry_path))
    return items
