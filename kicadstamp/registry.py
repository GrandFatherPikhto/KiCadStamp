# kicadstamp/registry.py
"""
registry.py — placement registry for vias and tracks between runs.

Allows: (1) not touching a via/track that is already exactly in the right place;
(2) if the position in the config changed — delete the old via/track by its
stored UUID and create a new one at the new location; (3) prune — delete
vias/tracks whose keys no longer appear in the current config (cell/component
removed from the config entirely).

Composite key — anchor_id/template_name/role/index:
  anchor_id: f"name:{name}" for KiCadStamp (a clone placement's own name);
             `pad:<pad>` was the retired spoke anchor, kept only in
             PROTECTED_ANCHOR_PREFIXES for legacy entries (see below).
             Future extension: f"ref:{anchor_ref}" for section cloning.
  role: component role (unique within a cell, see config.py) for component‑level
        vias/tracks, or None for cell‑level vias/tracks.
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
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, TypeVar, Generic

# The registry key is the registry's OWN identity concept: placement builds it by
# importing from the registry (single direction placement -> registry). So this
# module must NOT import `placement` at RUNTIME — `ViaCommand`/`TrackCommand` are
# needed only in annotations, which `from __future__ import annotations` turns
# into strings. That module-level import used to be the registry end of the
# registry <-> placement import cycle (registry -> placement.commands ->
# placement/__init__ -> executor -> registry), which made `import
# kicadstamp.registry` fail in a fresh process (found again in У5.5 (г): the
# registry-upgrade test could not be collected on its own).
if TYPE_CHECKING:
    from .placement.commands import ViaCommand, TrackCommand

from .utils.units import MM
from .utils.layers import layer_to_str
# The four config-derived default-path helpers live in kicadstamp/utils/paths.py
# (import-light) and are re-exported here so the public API
# (`from kicadstamp.registry import registry_path_for_config, ...`) is unchanged
# for existing consumers. They must NOT be defined here: kicadstamp.registry is
# reached late/through placement.executor (which imports registry back — a latent
# import cycle), while cli_common.peek_* and the GUI RootMetadataDock need these
# defaults in a fresh process BEFORE that heavy chain is loaded.
from .utils.paths import (  # noqa: F401 — re-export
    default_log_file_for_config,
    default_operation_log_dir_for_config,
    registry_path_for_config,
    registry_paths_for_config,
    track_registry_path_for_config)
from .constants import POSITION_TOLERANCE_MM, CELL_LEVEL_ROLE_PLACEHOLDER
# Positions of copper identity live in track_identity.py (доделка Р); the names
# are re-exported here, so `from kicadstamp.registry import track_matches, ...`
# stays the public door for channel_copy / net_trace_planner / imprint_apply /
# apply_pipeline and for the tests.
from .track_identity import filter_existing_tracks, track_matches  # noqa: F401 — re-export
# The registry FILES' serialisation lives in `registry_io.py` (Ч1 of
# plan_2026_10_27_orphan_anchor_keys_own_nothing: this file stood at 775 lines against
# an 800-line ceiling and the orphan rule touches `reconcile`, so the extraction went
# first, as its own commit). Re-exported here, so
# `from kicadstamp.registry import load_registry_entries, ...` stays the public door
# for gui/mixed_selection, kicadstamp/explode, kicadstamp/internode_cell_copper,
# kicadstamp/absent_copper_prune, placement/services/board_items_resolver and the
# tests that monkeypatch `kicadstamp.registry.load_registry_entries`.
from .registry_io import (  # noqa: F401 — re-export
    RegistryEntry,
    TrackRegistryEntry,
    load_registry,
    load_registry_entries,
    load_track_registry,
    save_registry,
    save_track_registry,
)
from .exceptions import ValidationError, format_fatal_error
from .i18n import _

logger = logging.getLogger(__name__)

_POSITION_TOLERANCE_MM = POSITION_TOLERANCE_MM

# The anchor_id prefixes whose copper is PROTECTED from a prune in an
# --only/--cluster run (reconcile's known_anchor_ids). ONE list: every anchor_id
# a builder produces must start with one of these, or selecting another record
# with --only silently DELETES this record's vias/tracks from the board.
# `point:` was missing from the original inline tuple (found 2026-10-04): a
# Point-anchored clone_placement (anchor_point set — clone_anchor_id's first
# branch since 2026-08-06) lost its copper on any --only run. Guarded by
# tests/placement/test_registry_anchor_prefix_protection.py, which enumerates
# every builder's output.
# (imprint: NOT in this list, and not a gap — imprint copper never reaches a
# registry at all: execute_imprint_plans runs through BatchExecutor.execute with
# registry=None (imprint_apply.py), so no `imprint:` record is ever stored and
# prune has nothing to delete; imprint idempotency is positional. A stretch test
# asserts the registry files stay untouched across an imprint execution — if
# imprints ever DO start writing registry entries, that test fails and
# `imprint:` must join both this constant and
# apply_pipeline._compute_all_anchor_ids.)
# `pad:` — the LEGACY spoke anchor (built ONLY by the removed
# manual_position_calculator) — is KEPT on purpose: no builder produces it today,
# but dropping the prefix would let reconcile treat a legacy pad-keyed entry as
# unprotected and DELETE its copper on an --only/--cluster run. Such an entry is
# either detached by the Д2 registry lift or refused by the schema gate
# (config/registry_upgrade.py), and
# tests/placement/test_registry_anchor_prefix_protection.py enumerates this tuple.
PROTECTED_ANCHOR_PREFIXES = ("anchor:", "role:", "name:", "point:",
                             "thermal:", "pad:", "net:")


def make_registry_key(anchor_id: str, template_name: str, role: str | None, index: int) -> str:
    """Build a composite registry key for vias/tracks.

    ``anchor_id`` — e.g. ``"name:{name}"`` or ``"thermal:{name}"``.
    ``template_name`` — the clone cell name (param kept as
    ``template_name`` for registry backward compatibility).
    ``role`` — component role (unique within a cell) or ``None`` for
    cell‑level vias/tracks (substituted by :data:`CELL_LEVEL_ROLE_PLACEHOLDER`).
    ``index`` — 0‑based index within the specific list (vias or tracks).

    Homed here, not in placement/commands.py: the registry key is the registry's
    own identity concept, so placement builds it by importing from the registry
    (single direction placement -> registry) instead of the registry depending
    on placement to produce its keys.
    """
    role_part = role if role is not None else CELL_LEVEL_ROLE_PLACEHOLDER
    return f"{anchor_id}|{template_name}|{role_part}|{index}"


def record_key_part(name: str, uuid: str | None) -> str:
    """The RECORD-identifying part of a registry key (Р-У5.1/Р-У5.2, У5.3).

    ONE point of the format gate: in format 3 the part is the record's UUID, in
    format 2 its NAME. Callers pass the record's own uuid (``record.uuid``, or
    the reference field such as ``clone.anchor_point_uuid``) and the name it
    stood for. Only the VALUE changes — every PREFIX of the key is untouched,
    and so is the physics (``pad:``/``anchor:``/``role:``/offsets/index).

    A record WITHOUT a uuid under the gate is a PROGRAM ERROR (Р-У5.7): the
    format-3 checks (loader) and the writer stamp guarantee every record
    carries one, so this is reached only by a builder that bypassed both.
    Fatal, NEVER a fallback to the name: one registry mixing name- and
    uuid-keys would have ``reconcile`` prune the difference, deleting half of
    the profile's copper on the next apply.

    Not a §0 record (a synthesized ``role:``-only nested cell) has no uuid to
    read — such a caller does not come through here, it keeps its deterministic
    name (see clone_position_calculator._resolve_content).

    Imported lazily: ``kicadstamp.registry`` is reached by placement before the
    config package is fully loaded, so a module-level import of
    ``current_format`` would add an import edge the registry does not need."""
    from .config.format_version import current_format
    if current_format() >= 3:
        if not uuid:
            raise ValidationError(format_fatal_error(
                _("registry key: record {name!r} has no uuid").format(name=name),
                [_("format 3 gives every record a uuid (checked at load and at "
                   "write) — falling back to the name would split one registry "
                   "into name- and uuid-keys, and prune would then delete the "
                   "difference")]))
        return uuid
    return name


# registry_path_for_config / track_registry_path_for_config /
# default_log_file_for_config / default_operation_log_dir_for_config are
# imported (re-exported) from kicadstamp/utils/paths.py above — see that
# module's header comment for why they live there, not here.


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
                  known_anchor_ids: set | None = None,
                  live_items=None) -> list:
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
        # live_items — optional pre-fetched live board items, passed by a
        # caller that already has them (apply_pipeline fetches get_tracks()
        # once and feeds both this and the positional pre-check); when None,
        # fetched here as before (_get_live_items).
        if live_items is None:
            live_items = self._get_live_items()
        live_by_uuid = {item.uuid: item for item in live_items}

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
        # A uuid a LIVE key of this run holds is not stale, even when the key
        # being pruned below points at the very same item — dropping the key must
        # not take the copper this run needs (Ч1 of
        # plan_2026_10_27_orphan_anchor_keys_own_nothing: an orphan key owns
        # nothing, so the record that really owns that copper may bind the same
        # uuid, and two keys on one item is not a reason to delete it).
        live_uuids = {self.entries[key].uuid for key in seen_keys
                      if key in self.entries}

        stale_keys = set()
        for key in set(self.entries.keys()) - seen_keys:
            anchor_id = key.split('|', 1)[0]
            # anchor_id WAS seen this run -> the item itself was processed,
            # this specific key just isn't part of its current plan anymore
            # (cell edited) -> genuinely stale, prune below, known_anchor_ids
            # does not apply here (see IMPORTANT note above).
            if (anchor_id not in seen_anchor_ids
                    and known_anchor_ids is not None
                    and anchor_id.startswith(PROTECTED_ANCHOR_PREFIXES)
                    and anchor_id in known_anchor_ids):
                logger.debug(_("  {key}: not processed in this run (--only filtered "
                               "{anchor_id!r}), but it is still in the config — NOT pruned")
                             .format(key=key, anchor_id=anchor_id))
                continue
            stale_keys.add(key)

        for key in stale_keys:
            entry = self.entries.pop(key)
            if entry.uuid in to_delete:
                # A key of THIS run already named this item for deletion (its live
                # item stands off the planned place, so the record recreates it).
                # Repeating the uuid would put the same id into ONE remove_by_ids
                # call, and a rejected packet deletes nothing while the new copper
                # is drawn anyway (Д-1 of the acceptance): the key goes, the uuid
                # is named once.
                logger.info(_("  prune: {key} no longer appears in config — the key "
                              "is dropped, the item ({uuid}) is already being deleted "
                              "by a key of this run")
                            .format(key=key, uuid=entry.uuid))
                continue
            if entry.uuid in live_uuids:
                logger.info(_("  prune: {key} no longer appears in config — the key "
                              "is dropped, the item ({uuid}) stays: a key of this "
                              "run holds it")
                            .format(key=key, uuid=entry.uuid))
                continue
            if entry.uuid not in live_by_uuid:
                # The stored uuid is not on the board any more — there is nothing
                # to delete, so the key alone goes (the 6 dead-uuid keys of the
                # v103 orphan cluster, §7.2). Naming a deletion that cannot happen
                # would put a board write on a missing id.
                logger.info(_("  prune: {key} no longer appears in config — the key "
                              "is dropped, the item ({uuid}) is not on the board")
                            .format(key=key, uuid=entry.uuid))
                continue
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

    def adopt_live(self, key: str, entry) -> None:
        """Bind ``key`` to an already-built entry and persist — the ONE write door
        of the at-current-place adoption (plan_2026_10_07_adopt_at_current_place).
        The CALLER builds the entry from the live item and decides which key may
        adopt which item (the registry never inspects ownership); this only
        persists the decision, exactly like ``record_created``. Never deletes."""
        self.entries[key] = entry
        self._save_entries(self.entries)

    def forget_key(self, key: str) -> bool:
        """Remove ONE entry and persist — the write door of the companion rule (Ч1
        of plan_2026_10_27_registry_key_at_another_record_place).

        WHY a released key must go: the key's pair was a SHIFTED NUMBER, i.e. the
        stored uuid is ANOTHER record's copper. If that record found no copper of
        its own, the entry keeps the foreign uuid, and ``reconcile`` — seeing an
        item that is not where THIS key's command plans it — puts the uuid into
        ``to_delete`` while the real owner, bound to it in the same pass, counts it
        "already in place". The copper is then deleted and never redrawn. With the
        entry gone, reconcile sees "no entry" and simply creates this record's own
        command.

        Lives here because the registry is the schema's OWNER: the caller decides
        WHICH key may go (the registry never judges ownership), this only persists
        the decision, exactly like ``adopt_live``. Returns True when an entry was
        removed — a key already gone is not an error, it is the same outcome."""
        if key not in self.entries:
            return False
        del self.entries[key]
        self._save_entries(self.entries)
        return True


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
        """Checks PLANNED via against REAL via on the board (not against JSON
        entry). Delegates to the shared via_matches() predicate — see its
        docstring (mirrors how TrackRegistry._live_matches delegates to
        track_matches)."""
        return via_matches(live_via, via)

    def _build_entry(self, via: ViaCommand, uuid: str) -> RegistryEntry:
        return RegistryEntry(
            uuid=uuid,
            x_mm=via.position.x / MM,
            y_mm=via.position.y / MM,
            net=via.net_name,
            drill_mm=via.drill_mm,
            diameter_mm=via.diameter_mm,
        )


# ── Via matching and positional pre-check ───────────────────────────────────


def via_matches(live_via, via: ViaCommand) -> bool:
    """Shared predicate: does the live board via match the planned ViaCommand?
    Position (within POSITION_TOLERANCE_MM) + net + drill + diameter. Used by
    BOTH the UUID-path reconcile (PlacementRegistry._live_matches) and the
    positional pre-check (filter_existing_vias) — one predicate, so the two
    can never disagree (mirrors track_matches / filter_existing_tracks, 2026-08-31;
    extended to vias 2026-09-08, plan_2026_09_08_via_positional_precheck.md:
    vias never got the same positional protection tracks got 2026-08-31).

    The two halves live in `registry_match` (Ч1 of
    plan_2026_10_26_select_cell_shifted_registry_pair): `same_place` and
    `same_identity`, the same tolerances as before, in ONE place for both kinds.
    This predicate is their composition, unchanged in meaning."""
    from .registry_match import same_identity, same_place

    return same_place(live_via, via) and same_identity(live_via, via)


def filter_existing_vias(to_create: list[ViaCommand], live_vias) -> list[ViaCommand]:
    """Positional pre-check of vias — unregistered-copper idempotency (analog of
    filter_existing_tracks, extended 2026-09-08 — plan_2026_09_08_via_
    positional_precheck.md): vias never got the same protection tracks got
    2026-08-31, so a cell-produced via (a clone cell's own via, e.g. the live
    profile's FPGA power-bank cells) whose registry entry stops matching the board
    for ANY reason — lost/overwritten registry state, a run interrupted between
    via creation and registry save, etc. — was recreated unconditionally on every
    subsequent redraw; a live profile accumulated up to 4 literal duplicates per
    via this way. Applied STRICTLY AFTER registry.reconcile(), on its to_create
    list — same ordering rule as filter_existing_tracks (a pre-reconcile skip
    would drop the key from seen_keys and make prune delete the REGISTERED via).
    SKIP-ONLY: never removes and never adopts foreign copper into the registry.
    Returns the filtered list, logs a skip counter."""
    if not to_create:
        return to_create
    kept: list[ViaCommand] = []
    skipped = 0
    for cmd in to_create:
        if any(via_matches(live, cmd) for live in live_vias):
            skipped += 1
            logger.debug(_("  via for {owner}: already exists, skipped (positional pre-check)")
                         .format(owner=cmd.owner_ref))
            continue
        kept.append(cmd)
    if skipped:
        logger.info(_("Skipped {count} vias already present on the board (positional pre-check)")
                    .format(count=skipped))
    return kept


def adopt_matching_unowned(reg: BaseRegistry, planned_cmds: list,
                           live_items=None) -> int:
    """One-time ownership migration for ANY planned copper (Bug 3, 2026-09-05):
    claim a live item that exactly matches a planned-but-unregistered command
    into the registry, so a later reposition of the anchor deletes that same
    item (by its now-registered UUID) instead of orphaning it at the old place.
    Generalizes net_trace_planner.adopt_net_trace_copper to the WHOLE plan.

    The matching is ``registry_match.match_planned_copper`` — the SAME routine
    the read-only "Select cell" path calls, so the claiming path and the read
    path can never disagree (plan_2026_10_05_select_cell_split, СЦ-1).

    SAFE BY CONSTRUCTION (never deletes, never steals foreign copper): claims
    only a command whose key is set AND absent from the registry (a REGISTRY-tier
    hit is the record's own entry); the live item must match the plan exactly
    (``_live_matches`` — the predicate reconcile uses) and its UUID must not be
    owned by any entry of THIS registry (copper owned under another key — e.g. a
    legacy clone_placement while an Entity materializes the same cell — is never
    taken). Runs BEFORE reconcile() so seen_keys / stale-prune see the adopted
    keys; returns the count claimed (0 -> the file is not rewritten)."""
    from .registry_match import TIER_GEOMETRY, match_planned_copper

    # СЦ-4-1: only commands whose key is NOT in the registry may enter the
    # matcher. A command whose key IS present but whose stored uuid is stale
    # would otherwise consume the live item by GEOMETRY, and a DIFFERENT
    # record's keyless-but-same-geometry command (the documented legacy
    # clone_placement + Entity case) would then go unadopted — the redraw
    # would draw a DUPLICATE. This is the old behaviour, kept deliberately.
    candidates = [c for c in planned_cmds
                  if getattr(c, "registry_key", None) is not None
                  and c.registry_key not in reg.entries]

    adopted = 0
    for m in match_planned_copper(reg, candidates, live_items=live_items):
        if m.tier != TIER_GEOMETRY or m.live is None:
            continue
        key = m.command.registry_key
        reg.entries[key] = reg._build_entry(m.command, m.live.uuid)
        adopted += 1
        logger.info("  adopted existing copper (%s) into the registry under %s",
                    type(m.command).__name__, key)
    if adopted:
        reg._save_entries(reg.entries)
    return adopted


def registries_empty_for(config_path) -> bool:
    """True when BOTH the via and track registries of a config are empty or
    absent — i.e. this profile has never run a copper-placing apply yet (a
    "first run" in a fresh / newly-copied profile). Pure file check, no Qt, no
    adapter: the GUI uses it to decide whether to show the "adopt existing
    copper?" heads-up before a redraw (Bug 3, 2026-09-05)."""
    config_path = str(config_path)
    # Н3: the paths come from the SAME decision as apply/lift, honouring an
    # explicit registry_path:/track_registry_path: in the config. Reading only
    # the defaults made this hint inspect DIFFERENT files than a redraw used.
    from .cli_common import peek_registry_paths  # lazy — registry stays import-light
    via_path, trk_path = peek_registry_paths(config_path)
    try:
        return not load_registry(via_path) and not load_track_registry(trk_path)
    except ValidationError:
        # Under the format-3 gate an UNLIFTED (schema-1) registry is a FATAL in
        # load_registry/load_track_registry (Н3). This function is only a "first
        # run" heads-up, called from a Qt redraw slot that must never raise
        # (gui/docks/_common.py:67 calls it OUTSIDE a try — a ValidationError
        # escaping a Qt slot takes PyQt6 down). Treat the unlifted registry as
        # NOT empty: the hint stays silent and the run itself refuses, loudly,
        # with its own fatal in the Log.
        return False


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
