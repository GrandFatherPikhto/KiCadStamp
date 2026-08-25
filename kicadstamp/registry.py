# kicadstamp/registry.py
"""
registry.py — placement registry for vias and tracks between runs.

Allows: (1) not touching a via/track that is already exactly in the right place;
(2) if the position in the config changed — delete the old via/track by its
stored UUID and create a new one at the new location; (3) prune — delete
vias/tracks whose keys no longer appear in the current config (spoke/component
removed from YAML entirely).

Composite key — anchor_id/template_name/role/index:
  anchor_id: f"pad:{spoke_pad}" for KiCadStamp (anchor = IC pad number).
             Future extension: f"ref:{anchor_ref}" for section cloning.
  role: component role (unique within a cell, see config.py) for component‑level
        vias/tracks, or None for spoke‑level vias/tracks.
  index: 0‑based index within the specific list (vias or tracks) — since roles
         within a cell are unique and the order of lists is stable between
         runs, this is sufficient without additional discrimination.

CHANGED (after reports of "glitches mercilessly"): registry.json is now ONLY
an index key->uuid, not the source of truth for position/net/parameters.
Previously "already correctly placed" was decided by comparing numbers from the
JSON with numbers from the same JSON — if a via was manually deleted, undone,
PCB reloaded from git, or a run crashed between record_created() (JSON already
written) and the actual board commit (see known IPC crashes on begin_commit/
push_commit) — the JSON lied that everything was in place, while the board was
empty. Silently, until visual inspection. Now reconcile() reads adapter.get_vias()
once and uses the live via with the stored UUID as the source of truth for
position/net/drill/diameter; a registry entry whose UUID is not found alive on
the board is considered stale (not fatal — just recreate as if the entry never
existed).
"""
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import TypeVar, Generic

from .placement.commands import ViaCommand, TrackCommand
from .utils.units import MM
from .utils.layers import layer_to_str
from .constants import POSITION_TOLERANCE_MM, SPOKE_LEVEL_ROLE_PLACEHOLDER
from .i18n import _

logger = logging.getLogger(__name__)

_POSITION_TOLERANCE_MM = POSITION_TOLERANCE_MM


def make_registry_key(anchor_id: str, template_name: str, role: str | None, index: int) -> str:
    """Build a composite registry key for vias/tracks.

    ``anchor_id`` — e.g. ``"pad:{pad}"`` or ``"thermal:{name}"``.
    ``template_name`` — the spoke/clone cell name (param kept as
    ``template_name`` for registry backward compatibility).
    ``role`` — component role (unique within a cell) or ``None`` for
    spoke‑level vias/tracks (substituted by :data:`SPOKE_LEVEL_ROLE_PLACEHOLDER`).
    ``index`` — 0‑based index within the specific list (vias or tracks).

    Homed here, not in placement/commands.py: the registry key is the registry's
    own identity concept, so placement builds it by importing from the registry
    (single direction placement -> registry) instead of the registry depending
    on placement to produce its keys.
    """
    role_part = role if role is not None else SPOKE_LEVEL_ROLE_PLACEHOLDER
    return f"{anchor_id}|{template_name}|{role_part}|{index}"




def registry_path_for_config(config_path: str) -> str:
    """<config>.yaml -> <config>.registry.json, next to the config itself."""
    p = Path(config_path)
    return str(p.with_suffix("").with_suffix(".registry.json"))


def track_registry_path_for_config(config_path: str) -> str:
    """<config>.yaml -> <config>.tracks.registry.json — separate file from vias,
    record schema is different (two points+width+layer, not drill/diameter)."""
    p = Path(config_path)
    return str(p.with_suffix("").with_suffix(".tracks.registry.json"))


@dataclass
class RegistryEntry:
    uuid: str
    x_mm: float
    y_mm: float
    net: str
    drill_mm: float
    diameter_mm: float


@dataclass
class TrackRegistryEntry:
    uuid: str
    start_x_mm: float
    start_y_mm: float
    end_x_mm: float
    end_y_mm: float
    width_mm: float
    net: str
    layer: str


def load_registry(path: str) -> dict[str, RegistryEntry]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k: RegistryEntry(**v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(_("Failed to read registry {path}: {type}: {e} — "
                         "treating registry as empty (all vias will be created anew)")
                       .format(path=path, type=type(e).__name__, e=e))
        return {}


def save_registry(path: str, entries: dict[str, RegistryEntry]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: asdict(v) for k, v in entries.items()}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_track_registry(path: str) -> dict[str, TrackRegistryEntry]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k: TrackRegistryEntry(**v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(_("Failed to read track registry {path}: {type}: {e} — "
                         "treating registry as empty (all tracks will be created anew)")
                       .format(path=path, type=type(e).__name__, e=e))
        return {}


def save_track_registry(path: str, entries: dict[str, TrackRegistryEntry]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: asdict(v) for k, v in entries.items()}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Generic base ──────────────────────────────────────────────────────────────

TEntry = TypeVar('TEntry')


class BaseRegistry(ABC, Generic[TEntry]):
    """
    Generic base for placement/track registries.
    Subclasses provide: entry type, live-item fetching, match logic, persistence.
    """

    def __init__(self, adapter, path: str):
        self.adapter = adapter
        self.path = path
        self.entries: dict[str, TEntry] = self._load_entries()

    # ── Abstract hooks ──────────────────────────────────────────────────────

    @abstractmethod
    def _load_entries(self) -> dict[str, TEntry]:
        """Load persisted entries from disk."""

    @abstractmethod
    def _save_entries(self, entries: dict[str, TEntry]) -> None:
        """Persist entries to disk."""

    @abstractmethod
    def _get_live_items(self):
        """Return live items from the board (e.g. adapter.get_vias())."""

    @abstractmethod
    def _live_matches(self, live_item, cmd) -> bool:
        """Check if a live board item matches the planned command."""

    @abstractmethod
    def _build_entry(self, cmd, uuid: str) -> TEntry:
        """Create a new entry from a command and its created UUID."""

    # ── Shared logic ────────────────────────────────────────────────────────

    def reconcile(self, planned_cmds,
                  known_anchor_ids: set | None = None) -> list:
        """
        Returns (to_create, to_delete): the subset of planned_cmds that
        actually need to be created (already correctly placed ones are
        excluded), plus the UUIDs of stale items to delete — both those whose
        position/parameters changed and those whose key does not appear in
        this run at all (prune). reconcile() itself NO LONGER deletes from the
        board (P0-3, 2026-08-25): the caller performs the deletions, keeping
        the registry a pure persistence/planning component.

        Source of truth for "is it already placed correctly" is the LIVE item on
        the board (one query for the whole reconcile), not the numbers stored in
        JSON: a registry entry whose UUID is not among the live items is
        considered stale and recreated as if it never existed.

        known_anchor_ids — the FULL (before any --only filtering) set of
        anchor_id (see clone_anchor_id() in clone_position_calculator.py —
        physical binding anchor_ref/anchor_pad, NOT clone.name, specifically so
        that renaming a clone_placement does not erase history if the physical
        anchor remains the same). Without it (None) prune behaves as before:
        everything not in this run is stale. With it — an entry whose anchor_id
        (entirely, not by name) is still in known_anchor_ids is skipped (not
        pruned), even if it was not among planned_cmds in THIS run: it was just
        filtered out by --only, not removed from YAML. Otherwise --only A in one
        run and --only B in the next would mutually delete each other's items —
        a real bug caught in practice.

        IMPORTANT (found 2026-07-28): known_anchor_ids protection only applies
        to an anchor_id that was NOT SEEN AT ALL this run (--only/--cluster
        excluded the whole item). If the item WAS processed this run (its
        anchor_id appears among seen_keys) but a SPECIFIC key under it is
        missing from planned_cmds — e.g. its cell's via/track list shrank
        or got reordered — that key is genuinely stale and must be pruned, not
        protected just because the item as a whole is still "known". Before
        this distinction existed, editing a cell's via/track list left the
        orphaned old entries stuck on the board forever (real case: 3 tracks
        from an earlier ldo_adj_subsystem revision, at indices no longer used
        by the current cell, never got cleaned up run after run).
        """
        to_create: list = []
        to_delete: list[str] = []
        seen_keys = set()
        live_by_uuid = {item.uuid: item for item in self._get_live_items()}

        for cmd in planned_cmds:
            if cmd.registry_key is None:
                to_create.append(cmd)
                continue
            seen_keys.add(cmd.registry_key)

            existing = self.entries.get(cmd.registry_key)
            if existing is None:
                to_create.append(cmd)
                continue

            live_item = live_by_uuid.get(existing.uuid)
            if live_item is None:
                logger.warning(_("  {key}: registry has an entry (uuid {uuid}), "
                                 "but no such item is on the board — registry is out of sync "
                                 "(manually deleted, Undo, PCB reloaded from git, or previous run "
                                 "crashed between registry write and board commit); recreating "
                                 "as if the entry never existed")
                               .format(key=cmd.registry_key, uuid=existing.uuid))
                del self.entries[cmd.registry_key]
                to_create.append(cmd)
                continue

            if self._live_matches(live_item, cmd):
                logger.debug(_("  {key}: already correctly placed (checked against "
                               "live item {uuid}), skipped")
                             .format(key=cmd.registry_key, uuid=existing.uuid))
                continue

            logger.info(_("  {key}: position/parameters changed, deleting old item ({uuid}) "
                          "and creating a new one")
                        .format(key=cmd.registry_key, uuid=existing.uuid))
            to_delete.append(existing.uuid)
            del self.entries[cmd.registry_key]
            to_create.append(cmd)

        seen_anchor_ids = {key.split('|', 1)[0] for key in seen_keys}

        stale_keys = set()
        for key in set(self.entries.keys()) - seen_keys:
            anchor_id = key.split('|', 1)[0]
            # anchor_id WAS seen this run -> the item itself was processed,
            # this specific key just isn't part of its current plan anymore
            # (cell edited) -> genuinely stale, prune below, known_anchor_ids
            # does not apply here (see IMPORTANT note above).
            if (anchor_id not in seen_anchor_ids
                    and known_anchor_ids is not None
                    and anchor_id.startswith(('anchor:', 'role:', 'name:', 'thermal:', 'pad:', 'net:'))
                    and anchor_id in known_anchor_ids):
                logger.debug(_("  {key}: not processed in this run (--only filtered "
                               "{anchor_id!r}), but it is still in the config — NOT pruned")
                             .format(key=key, anchor_id=anchor_id))
                continue
            stale_keys.add(key)

        for key in stale_keys:
            entry = self.entries.pop(key)
            logger.info(_("  prune: {key} no longer appears in config, deleting ({uuid})")
                        .format(key=key, uuid=entry.uuid))
            to_delete.append(entry.uuid)

        self._save_entries(self.entries)
        return to_create, to_delete

    def record_created(self, cmd, created_uuid: str) -> None:
        """Called by the executor immediately after successfully creating a specific item."""
        if cmd.registry_key is None:
            return
        self.entries[cmd.registry_key] = self._build_entry(cmd, created_uuid)
        self._save_entries(self.entries)


# ── Via registry ──────────────────────────────────────────────────────────────

class PlacementRegistry(BaseRegistry[RegistryEntry]):
    """
    Lives for one run: reconcile() called once before creating vias,
    record_created() as each specific via is successfully created.
    """

    def _load_entries(self) -> dict[str, RegistryEntry]:
        return load_registry(self.path)

    def _save_entries(self, entries: dict[str, RegistryEntry]) -> None:
        save_registry(self.path, entries)

    def _get_live_items(self):
        return self.adapter.get_vias()

    def _live_matches(self, live_via, via: ViaCommand) -> bool:
        """Checks PLANNED via against REAL via on the board (not against JSON entry)."""
        x_mm = via.position.x / MM
        y_mm = via.position.y / MM
        live_x_mm = live_via.position.x / MM
        live_y_mm = live_via.position.y / MM
        live_net = live_via.net_name
        return (
            abs(live_x_mm - x_mm) <= _POSITION_TOLERANCE_MM
            and abs(live_y_mm - y_mm) <= _POSITION_TOLERANCE_MM
            and live_net == via.net_name
            and abs(live_via.drill_mm - via.drill_mm) < 1e-6
            and abs(live_via.diameter_mm - via.diameter_mm) < 1e-6
        )

    def _build_entry(self, via: ViaCommand, uuid: str) -> RegistryEntry:
        return RegistryEntry(
            uuid=uuid,
            x_mm=via.position.x / MM,
            y_mm=via.position.y / MM,
            net=via.net_name,
            drill_mm=via.drill_mm,
            diameter_mm=via.diameter_mm,
        )


# ── Track matching and positional pre-check ──────────────────────────────────


def _points_close(a_x_mm: float, a_y_mm: float, b_x_mm: float, b_y_mm: float) -> bool:
    """Two points match within POSITION_TOLERANCE_MM (0.01 mm), in mm."""
    return (
        abs(a_x_mm - b_x_mm) <= _POSITION_TOLERANCE_MM
        and abs(a_y_mm - b_y_mm) <= _POSITION_TOLERANCE_MM
    )


def track_matches(live_track, cmd: TrackCommand) -> bool:
    """Shared bidirectional predicate: does the live board track match the
    planned TrackCommand? A segment is UNORIENTED — both start↔start/end↔end
    AND start↔end/end↔start count as the same geometry. Also compares net
    (full hierarchical name as a string), width, and layer (via _layer_to_str).

    Used by BOTH the UUID-path reconcile (TrackRegistry._live_matches) and the
    positional pre-check (filter_existing_tracks) — one predicate, so the two
    can never disagree (plan_2026_08_16_position_based_copper_idempotency.md).
    The bidirectional match also fixes a latent reversal bug: a live track that
    got flipped (start↔end) between runs no longer looks like a position change
    (see plan_2026_08_16_position_based_copper_idempotency.md, Task 1.2)."""
    live_start_x_mm, live_start_y_mm = live_track.start.x / MM, live_track.start.y / MM
    live_end_x_mm, live_end_y_mm = live_track.end.x / MM, live_track.end.y / MM
    start_x_mm, start_y_mm = cmd.start.x / MM, cmd.start.y / MM
    end_x_mm, end_y_mm = cmd.end.x / MM, cmd.end.y / MM

    same_orientation = (
        _points_close(live_start_x_mm, live_start_y_mm, start_x_mm, start_y_mm)
        and _points_close(live_end_x_mm, live_end_y_mm, end_x_mm, end_y_mm)
    )
    swapped_orientation = (
        _points_close(live_start_x_mm, live_start_y_mm, end_x_mm, end_y_mm)
        and _points_close(live_end_x_mm, live_end_y_mm, start_x_mm, start_y_mm)
    )
    if not (same_orientation or swapped_orientation):
        return False

    live_net = live_track.net_name
    if live_net != cmd.net_name:
        return False
    if abs(live_track.width_mm - cmd.width_mm) >= 1e-6:
        return False
    if layer_to_str(live_track.layer) != layer_to_str(cmd.layer):
        return False
    return True


def filter_existing_tracks(to_create: list[TrackCommand], live_tracks) -> list[TrackCommand]:
    """Positional pre-check of tracks — unregistered-copper idempotency
    (analog of ViaPlanner._via_already_exists, but applied STRICTLY AFTER
    registry.reconcile(), on its to_create list: a pre-reconcile skip would
    drop the key from seen_keys and make prune delete the REGISTERED tool
    track, see plan_2026_08_16_position_based_copper_idempotency.md).

    Skips any command whose geometry+net+width+layer already exists among the
    live tracks (manually drawn, created by another mechanism, or a previous
    run's channel-copy). SKIP-ONLY: never removes and never adopts foreign
    copper into the registry. Returns the filtered list, logs a skip counter."""
    if not to_create:
        return to_create
    kept: list[TrackCommand] = []
    skipped = 0
    for cmd in to_create:
        if any(track_matches(live, cmd) for live in live_tracks):
            skipped += 1
            logger.debug(_("  track for {owner}: already exists, skipped (positional pre-check)")
                         .format(owner=cmd.owner_ref))
            continue
        kept.append(cmd)
    if skipped:
        logger.info(_("Skipped {count} tracks already present on the board (positional pre-check)")
                    .format(count=skipped))
    return kept


# ── Track registry ────────────────────────────────────────────────────────────

class TrackRegistry(BaseRegistry[TrackRegistryEntry]):
    """
    Track placement registry between runs — same logic as PlacementRegistry
    (see its docstring: live board as source of truth, JSON only index key->uuid,
    prune with known_anchor_ids), but matching is by two points + width + layer
    instead of position+drill+diameter.
    """

    def _load_entries(self) -> dict[str, TrackRegistryEntry]:
        return load_track_registry(self.path)

    def _save_entries(self, entries: dict[str, TrackRegistryEntry]) -> None:
        save_track_registry(self.path, entries)

    def _get_live_items(self):
        return self.adapter.get_tracks()

    def _live_matches(self, live_track, track: TrackCommand) -> bool:
        """Checks PLANNED track against REAL track on the board (not against JSON
        entry). Delegates to the shared bidirectional track_matches() predicate —
        behavior-preserving except for the latent reversal fix: a live track that
        got flipped (start↔end) between runs no longer triggers "position changed
        -> delete+recreate" (see plan_2026_08_16_position_based_copper_idempotency.md,
        Task 1.2)."""
        return track_matches(live_track, track)

    def _build_entry(self, track: TrackCommand, uuid: str) -> TrackRegistryEntry:
        return TrackRegistryEntry(
            uuid=uuid,
            start_x_mm=track.start.x / MM,
            start_y_mm=track.start.y / MM,
            end_x_mm=track.end.x / MM,
            end_y_mm=track.end.y / MM,
            width_mm=track.width_mm,
            net=track.net_name,
            layer=layer_to_str(track.layer),
        )
