# kicadstamp/apply_pipeline.py
"""
apply_pipeline.py — orchestrates the full ``apply`` pipeline.

Extracted from kicadstamp_cli.py's cmd_apply() to separate pipeline orchestration
from CLI argument parsing.  The pipeline is:

    load config
      -> compute known_anchor_ids (for registry protection)
      -> filter (retired, skip, --only, --cluster)
      -> connect KiCad adapter
      -> validate
      -> resolve dependency order
      -> create planner
      -> [dry‑run: plan & print]
      -> [execute: move → refresh → vias → tracks, per level]

The filters (retired / skip / --only / --cluster) NEVER mutate the caller's
Config: each returns a DERIVED Config and the input object is left untouched,
so a preloaded cfg (e.g. the GUI's shared object) is never the config that gets
applied or modified by a run.
"""

import dataclasses
import difflib
import logging

from .config import (Config, load_config, chain_effective_name,
                    thermal_via_array_effective_name,
                    coordinate_placement_effective_name, clone_placement_effective_name,
                    net_trace_effective_name, entity_effective_name)
from .net_trace_planner import net_trace_anchor_id, adopt_net_trace_copper
from .runtime_context import RuntimeContext
from .kicad.adapter import KiCadBoardAdapter
from .placement.planner import PlacementPlanner
from .placement.dependency_order import resolve_execution_order
from .placement.entity_placement import materialize_entity_placements
from .placement.services.clone_position_calculator import (
    clone_anchor_id,
    entity_anchor_id,
)
from .placement.services.via_planner import thermal_anchor_id
from .placement.services.manual_position_calculator import chain_anchor_ids
from .placement.services.coordinate_position_calculator import build_coordinate_moves
from .cluster_matching import cluster_prefix_match
from .placement.executor import BatchExecutor
from .exceptions import PlacerError
from .validation import run_all_checks, check_config_structure
from .registry import (PlacementRegistry, registry_path_for_config,
                       TrackRegistry, track_registry_path_for_config,
                       default_operation_log_dir_for_config,
                       filter_existing_tracks)
from .i18n import _

logger = logging.getLogger(__name__)


# ── Filtering helpers (pure cfg mutation, no adapter) ─────────────────────────


def _split_comma_values(raw: list[str] | None) -> list[str]:
    """--only/--cluster accept both repeating the flag and comma‑separated
    values within one occurrence (e.g. --only a,b --only c -> [a, b, c])."""
    if not raw:
        return []
    result = []
    for item in raw:
        result.extend(part.strip() for part in item.split(",") if part.strip())
    return result


def _matches_any_cluster(candidate: str | None, wanted: list[str]) -> bool:
    if candidate is None:
        return False
    return any(cluster_prefix_match(candidate, w) for w in wanted)


def drop_disabled_chains(cfg, _logger=None) -> "Config":
    """retired: true always wins, dropped before --only/--cluster ever see
    it — retired means "does not exist on the board right now", not
    "excluded from this particular run" (see Chain docstring in config/models.py).

    CONTRACT: filters never mutate the caller's Config. Each one returns a NEW
    derived Config and leaves the input untouched — the input object is never
    the config the pipeline applies, so a preloaded cfg (e.g. the GUI's shared
    object) is safe to reuse after a run. Pure config math, no adapter — kept
    separate so it's unit‑testable without a live KiCad connection."""
    l = _logger or logger
    disabled_chains = [c for c in cfg.chains if c.retired]
    chains = [c for c in cfg.chains if not c.retired]
    for c in disabled_chains:
        l.info(_("Chain {name!r} (net {net!r}): retired=true, skipped entirely")
               .format(name=chain_effective_name(c), net=c.net))
    return dataclasses.replace(cfg, chains=chains)


# Backward-compat alias for the 2026-09-01 Rule -> Chain rename.
drop_disabled_rules = drop_disabled_chains


def drop_inactive_items(cfg, _logger=None) -> "Config":
    """skip: true — the inline, per-item counterpart of --only/--cluster
    (see Chain/ClonePlacement/ThermalViaArrayConfig.skip docstrings in
    config/models.py). Unlike retired: true (drop_disabled_chains above),
    this must run AFTER known_anchor_ids is computed — a skipped item's
    via/tracks must still count as "known" so reconcile() protects them from
    pruning, it's just not (re)planned this run. Composes with --only/--cluster
    as a further AND-narrowing. Pure config math, no adapter.

    CONTRACT: returns a derived Config; the input object is never mutated (see
    drop_disabled_rules)."""
    l = _logger or logger
    kept_clones = [c for c in cfg.clone_placements if not c.skip]
    dropped_clones = [c for c in cfg.clone_placements if c.skip]
    for c in dropped_clones:
        l.info(_("ClonePlacement {name!r}: skip=true, skipped this run "
                  "(existing via/tracks stay protected)").format(name=c.name))

    narrowed_chains = []
    for c in cfg.chains:
        if c.skip:
            l.info(_("Chain {name!r}: skip=true, skipped this run "
                      "(existing via/tracks stay protected)").format(name=chain_effective_name(c)))
            continue
        kept_spokes = [s for s in c.spokes if not s.skip]
        for s in c.spokes:
            if s.skip:
                l.debug(_("Chain {name!r}: spoke on pad {pad} skip=true, skipped this run")
                         .format(name=chain_effective_name(c), pad=s.pad))
        if kept_spokes:
            narrowed_chains.append(dataclasses.replace(c, spokes=kept_spokes))
        else:
            l.info(_("Chain {name!r}: no non-skipped spokes left, skipped this run "
                      "(existing via/tracks stay protected)").format(name=chain_effective_name(c)))

    kept_tvas = []
    for tva in cfg.thermal_via_arrays:
        if not tva.retired and tva.skip:
            l.info(_("thermal_via_arrays {name!r}: skip=true, skipped this run "
                      "(existing vias stay protected)")
                   .format(name=thermal_via_array_effective_name(tva)))
            continue
        kept_tvas.append(tva)

    # coordinate_placements has no registry involvement at all (see its own
    # docstring in config/models.py), so — unlike rules/clone_placements/
    # thermal_via_arrays above — retired and skip have the SAME practical
    # effect here: just don't run it this time, nothing to protect in a
    # registry that doesn't exist for this type.
    kept_coords = []
    for cp in cfg.coordinate_placements:
        if cp.retired or cp.skip:
            l.info(_("coordinate_placements {name!r}: retired/skip=true, skipped this run")
                   .format(name=coordinate_placement_effective_name(cp)))
            continue
        kept_coords.append(cp)

    # net_traces: same retired/skip semantics as thermal_via_arrays — a
    # skipped record's registry entries stay protected (it remains in cfg,
    # planner skips it), only retired records drop out of known_anchor_ids
    # (see _compute_all_anchor_ids).
    kept_net_traces = []
    for nt in cfg.net_traces:
        if not nt.retired and nt.skip:
            l.info(_("net_traces entry (net {net!r}): skip=true, skipped this run "
                      "(existing copper stays protected)")
                   .format(net=net_trace_effective_name(nt)))
            continue
        kept_net_traces.append(nt)

    return dataclasses.replace(cfg, chains=narrowed_chains,
                               clone_placements=kept_clones,
                               thermal_via_arrays=kept_tvas,
                               coordinate_placements=kept_coords,
                               net_traces=kept_net_traces)


def apply_only_filter(cfg, only_names: list[str], _logger=None) -> "Config":
    """--only: whole-block selection by identity (chain name-or-net, clone_placement
    name, thermal_via_arrays entry name). Raises PlacerError on unmatched names.
    Pure config math, no adapter.

    CONTRACT: returns a derived Config; the input object is never mutated (see
    drop_disabled_chains). A no-op filter (no --only names) returns cfg unchanged."""
    l = _logger or logger
    if not only_names:
        return cfg
    requested = set(only_names)
    matched_chains = [c for c in cfg.chains if chain_effective_name(c) in requested]
    matched_clones = [c for c in cfg.clone_placements
                      if clone_placement_effective_name(c) in requested]
    matched_tvas = [t for t in cfg.thermal_via_arrays
                    if not t.retired and thermal_via_array_effective_name(t) in requested]
    matched_coords = [cp for cp in cfg.coordinate_placements
                      if not cp.retired
                      and coordinate_placement_effective_name(cp) in requested]
    matched_nets = [nt for nt in cfg.net_traces
                    if not nt.retired and net_trace_effective_name(nt) in requested]

    # entities: — recognized by name here (so --only E1 does not fatal), but
    # deliberately NOT cut out of cfg.entities: materialization in
    # _resolve_order runs link_trees over the FULL trees/entities and would
    # fatal on any tree node whose entity was filtered away. The --only
    # narrowing of ENTITY placements happens on the materialized clones
    # (phase 4.1 fix, see _filter_materialized_entities).
    found_names = ({chain_effective_name(c) for c in matched_chains}
                   | {clone_placement_effective_name(c) for c in matched_clones}
                   | {thermal_via_array_effective_name(t) for t in matched_tvas}
                   | {coordinate_placement_effective_name(cp) for cp in matched_coords}
                   | {net_trace_effective_name(nt) for nt in matched_nets}
                   | {entity_effective_name(e) for e in cfg.entities if not e.retired})
    missing = requested - found_names
    if missing:
        all_names = sorted(
            {chain_effective_name(c) for c in cfg.chains}
            | {clone_placement_effective_name(c) for c in cfg.clone_placements}
            | {thermal_via_array_effective_name(t) for t in cfg.thermal_via_arrays if not t.retired}
            | {coordinate_placement_effective_name(cp) for cp in cfg.coordinate_placements
               if not cp.retired}
            | {net_trace_effective_name(nt) for nt in cfg.net_traces if not nt.retired}
            | {entity_effective_name(e) for e in cfg.entities if not e.retired}
        )
        lines = []
        for name in sorted(missing):
            suggestion = difflib.get_close_matches(name, all_names, n=1)
            hint = (_(" (maybe you meant {suggestion!r}?)").format(suggestion=suggestion[0])
                    if suggestion else "")
            lines.append(_("  {name!r} — not found among chains, clone_placements, "
                           "thermal_via_arrays, coordinate_placements, net_traces, "
                           "or entities{hint}").format(name=name, hint=hint))
        raise PlacerError(_("[error] --only: names not found:\n{lines}\nAvailable: {all}")
                          .format(lines="\n".join(lines), all=all_names))

    l.info(_("--only {requested}: chains={chains}, clone_placements={clones}, "
              "thermal_via_arrays={thermal}, coordinate_placements={coords}, "
              "net_traces={nets} (everything else is ignored in this run)")
            .format(requested=sorted(requested),
                    chains=[chain_effective_name(c) for c in matched_chains],
                    clones=[clone_placement_effective_name(c) for c in matched_clones],
                    thermal=[thermal_via_array_effective_name(t) for t in matched_tvas],
                    coords=[coordinate_placement_effective_name(cp) for cp in matched_coords],
                    nets=[net_trace_effective_name(nt) for nt in matched_nets]))
    return dataclasses.replace(cfg, chains=matched_chains,
                               clone_placements=matched_clones,
                               thermal_via_arrays=matched_tvas,
                               coordinate_placements=matched_coords,
                               net_traces=matched_nets)


def apply_cluster_filter(cfg, cluster_paths: list[str], _logger=None) -> "Config":
    """--cluster — a second, independent selection axis (physical instance /
    Cluster field, not name). Composes with --only via AND only, never OR.
    Pure config math, no adapter.

    CONTRACT: returns a derived Config; the input object is never mutated (see
    drop_disabled_chains). A no-op filter (no --cluster paths) returns cfg unchanged."""
    l = _logger or logger
    if not cluster_paths:
        return cfg
    matched_clones = [c for c in cfg.clone_placements
                      if _matches_any_cluster(c.anchor_cluster, cluster_paths)]
    matched_tvas = [t for t in cfg.thermal_via_arrays
                    if not t.retired and _matches_any_cluster(t.anchor_cluster, cluster_paths)]
    # cp.cluster is the moved component's own physical-instance field here
    # (CoordinatePlacement's anchor-relative mode also has an anchor_cluster
    # narrowing field, but --cluster selects by the instance being acted on,
    # same as it selects a Chain/ClonePlacement by its own anchor_cluster) —
    # the SAME prefix-match convention still applies to it.
    matched_coords = [cp for cp in cfg.coordinate_placements
                      if not cp.retired and _matches_any_cluster(cp.cluster, cluster_paths)]
    matched_nets = [nt for nt in cfg.net_traces
                    if not nt.retired and _matches_any_cluster(nt.anchor_cluster, cluster_paths)]
    # entities: — NOT narrowed here (materialization in _resolve_order runs
    # link_trees over the full trees/entities and must not see a filtered-out
    # entity's tree node); the --cluster narrowing of entity placements
    # happens on the materialized clones (_filter_materialized_entities). We
    # only need to know whether any entity's cluster hits, so a --cluster that
    # selects exclusively entities does not fatal as "matched nothing".
    entity_cluster_hit = any(
        not e.retired and e.cluster is not None
        and _matches_any_cluster(e.cluster, cluster_paths)
        for e in cfg.entities)

    narrowed_chains = []
    for c in cfg.chains:
        kept_spokes = [s for s in c.spokes if _matches_any_cluster(s.cluster, cluster_paths)]
        if kept_spokes:
            narrowed_chains.append(dataclasses.replace(c, spokes=kept_spokes))
        else:
            l.debug(_("Chain {name!r}: no spokes match --cluster {paths}, chain dropped")
                     .format(name=chain_effective_name(c), paths=cluster_paths))

    if (not narrowed_chains and not matched_clones and not matched_tvas
            and not matched_coords and not matched_nets and not entity_cluster_hit):
        raise PlacerError(_("[error] --cluster {paths}: matched nothing among chains' spokes, "
                            "clone_placements, thermal_via_arrays, coordinate_placements, "
                            "net_traces, or entities")
                          .format(paths=cluster_paths))

    l.info(_("--cluster {paths}: chains={chains} (spokes narrowed), "
              "clone_placements={clones}, thermal_via_arrays={thermal}, "
              "coordinate_placements={coords}, net_traces={nets}")
            .format(paths=cluster_paths,
                    chains=[chain_effective_name(c) for c in narrowed_chains],
                    clones=[c.name for c in matched_clones],
                    thermal=[thermal_via_array_effective_name(t) for t in matched_tvas],
                    coords=[coordinate_placement_effective_name(cp) for cp in matched_coords],
                    nets=[net_trace_effective_name(nt) for nt in matched_nets]))
    return dataclasses.replace(cfg, chains=narrowed_chains,
                               clone_placements=matched_clones,
                               thermal_via_arrays=matched_tvas,
                               coordinate_placements=matched_coords,
                               net_traces=matched_nets)


# ── Compute helper ────────────────────────────────────────────────────────────

def _compute_all_anchor_ids(cfg) -> set[str]:
    """Build the FULL set of anchor IDs (before --only/--cluster narrow)
    for registry.reconcile()'s known_anchor_ids protection."""
    ids = {clone_anchor_id(c) for c in cfg.clone_placements if not c.retired}
    # entities: — registry protection by Entity.name (phase 3.1): an entity's
    # name: anchor id joins known_anchor_ids so a --only-filtered run never
    # prunes copper belonging to an entity outside the selection. No physical
    # vias/tracks exist yet (apply is Phase 4), so this is future-proofing.
    ids |= {entity_anchor_id(e) for e in cfg.entities if not e.retired}
    for c in cfg.chains:
        ids |= chain_anchor_ids(c)
    ids |= {thermal_anchor_id(t) for t in cfg.thermal_via_arrays if not t.retired}
    # net_traces: an --only-filtered net trace must keep its registry entries
    # protected, same as the other sections (registry.py reconcile's protected
    # 'net:' prefix — see the startswith list there).
    ids |= {net_trace_anchor_id(nt) for nt in cfg.net_traces if not nt.retired}
    return ids


def _filter_materialized_entities(clones, only: list[str] | None,
                                  cluster: list[str] | None) -> list:
    """Apply --only/--cluster to materialized Entity placements (phase 4.1
    fix). Entity placements are materialized from the FULL cfg (link_trees
    runs over ALL trees/entities — see materialize_entity_placements), so
    --only/--cluster must NOT cut cfg.entities in apply_only_filter/
    apply_cluster_filter first (that would make link_trees fatal on a tree
    node whose entity was filtered away). Instead the materialized clones are
    narrowed HERE by the same axes as regular clone_placements:
    --only by the clone's effective name (== Entity.name), --cluster by the
    clone's cluster tag (== Entity.cluster)."""
    if not clones:
        return clones
    if only:
        wanted = set(only)
        clones = [c for c in clones
                  if clone_placement_effective_name(c) in wanted]
    if cluster:
        clones = [c for c in clones if _matches_any_cluster(c.cluster, cluster)]
    return clones


# ── Pipeline ──────────────────────────────────────────────────────────────────

class ApplyPipeline:
    """
    Orchestrates the full ``apply`` pipeline.

    Separated from argument parsing so the pipeline is independently testable
    and cmd_apply() stays a thin delegator.
    """

    def __init__(
        self,
        config_path: str,
        *,
        timeout_ms: int = 20000,
        batch_size: int = 10,
        dry_run: bool = False,
        no_selection: bool = False,
        no_collision_check: bool = False,
        collision_margin: float = 0.2,
        only: list[str] | None = None,
        cluster: list[str] | None = None,
        position_overrides=None,
        isolate_spokes: dict[str, set[str]] | None = None,
        preloaded_cfg=None,
        preloaded_ctx=None,
    ):
        self.config_path = config_path
        self.timeout_ms = timeout_ms
        self.batch_size = batch_size
        self.dry_run = dry_run
        self.no_selection = no_selection
        self.no_collision_check = no_collision_check
        self.collision_margin = collision_margin
        self.only = only or []
        self.cluster = cluster or []
        # Per-run absolute placement overrides keyed by record effective name
        # (tree rigid-group redraw — plan_2026_08_29_tree_live_rigid_redraw.md;
        # PositionOverride in kicadstamp/tree_position.py). Non-persistent:
        # replaces the record's own position/rotation resolution for this run
        # only; the saved config is never rewritten. None/empty = normal.
        self.position_overrides = position_overrides or {}
        # GUI "Redraw spoke" isolation (2026-09-05): chain effective name ->
        # set of spoke pad numbers that must actually be placed this run. The
        # siblings of such a chain stay in the config (no skip) but only RESERVE
        # their shared ComponentPool slots — full-chain consumption, so an
        # isolated spoke keeps the exact components a full chain redraw assigns
        # to it (bug: "Redraw spoke ворует компоненты у соседней спицы").
        # Non-persistent, per-run only; None/empty = normal (no isolation).
        self.isolate_spokes = isolate_spokes or {}

        # Internal state
        self.cfg = preloaded_cfg
        # Bug #3 fix (2026-08-30): the FULL, pre-filter config. _filter_config()
        # replaces self.cfg with the --only/--cluster-narrowed copy, but
        # _resolve_order()'s entity materialization must run over the WHOLE
        # config — a tree mixes placement and rule/coordinate/... nodes, and
        # link_trees fatals on any node whose section --only narrowed away.
        # Captured here (and refreshed in _load_config) BEFORE any narrowing.
        self._full_cfg = preloaded_cfg
        self.ctx: RuntimeContext | None = preloaded_ctx
        # Cached sheet-name map ({} when there is no ctx) — computed once instead
        # of repeating `self.ctx.sheet_names if self.ctx else {}` in every step.
        self.sheet_names: dict[str, str] = preloaded_ctx.sheet_names if preloaded_ctx else {}
        self.adapter: KiCadBoardAdapter | None = None
        self.planner: PlacementPlanner | None = None
        self.items = None
        self.all_anchor_ids: set[str] = set()
        # П.8: populated by _dry_run() — a structured, printable report that
        # the CLI layer prints and a future GUI panel could render. The
        # library itself never prints to stdout; it only produces this.
        self.dry_run_report: list[str] | None = None

    # ── Pipeline steps ──────────────────────────────────────────────────────

    def _load_config(self) -> None:
        if self.cfg is None:
            logger.info(_("Loading config: {config}").format(config=self.config_path))
            self.cfg, self.ctx = load_config(self.config_path)
            self.sheet_names = self.ctx.sheet_names if self.ctx else {}
            # Bug #3 fix — the FULL config must be captured here, before
            # _filter_config() narrows self.cfg (see __init__).
            self._full_cfg = self.cfg

    def _filter_config(self) -> None:
        # Structural checks (duplicate clone identity, cell‑definition cycles,
        # etc.) run on the FULL config, before --only/--cluster narrow it —
        # a duplicate involving a clone_placement outside this run's --only
        # selection is still a real config defect and must not go unreported
        # just because this particular run doesn't touch it (see
        # check_config_structure's docstring).
        check_config_structure(self.cfg, sheet_names=self.sheet_names)

        # Filters return a DERIVED Config and never mutate the caller's object
        # (see the filter docstrings) — self.cfg is replaced with each filter's
        # result, so a preloaded cfg (e.g. the GUI's shared object) is never the
        # config that gets applied or modified by this run.
        cfg = drop_disabled_chains(self.cfg)
        self.all_anchor_ids = _compute_all_anchor_ids(cfg)
        cfg = drop_inactive_items(cfg)
        cfg = apply_only_filter(cfg, _split_comma_values(self.only))
        cfg = apply_cluster_filter(cfg, _split_comma_values(self.cluster))
        self.cfg = cfg

    def _connect_adapter(self) -> None:
        logger.info(_("Connecting to KiCad (timeout {timeout} ms)").format(timeout=self.timeout_ms))
        self.adapter = KiCadBoardAdapter(timeout_ms=self.timeout_ms)
        if self.no_selection:
            self.adapter.ignore_selection = True
            logger.info(_("--no-selection: current PCB editor selection will be ignored for this run"))
        self.adapter.refresh_board()

    def _validate(self) -> None:
        run_all_checks(self.adapter, self.cfg, sheet_names=self.sheet_names)

    def _resolve_order(self) -> None:
        # Entity placements (Entity/Placement split, phase 4.1): entities carry
        # no position — they are placed via their trees: node. Materialize each
        # kind="placement" node into a TRANSIENT absolute ClonePlacement so the
        # existing clone planning machinery applies it (no config rewrite).
        # Materialization runs over the FULL cfg (self._full_cfg, bug #3 fix):
        # a tree mixes placement and rule/coordinate/thermal_via/net_trace
        # nodes, and link_trees resolves EVERY node's ref — passing the
        # --only/--cluster-NARROWED self.cfg would fatal on the first node
        # whose section got filtered away (TreesDock-Redraw: all 13 fpga-tree
        # nodes). The narrowing happens on the materialized clones instead
        # (_filter_materialized_entities), exactly as before.
        # self.only/self.cluster are the RAW CLI lists (action="append"), where
        # one element may be comma-separated ("--only a,b") — split them the
        # same way the regular filters do, or "--only E1,E2" would silently
        # produce an empty materialized list (quiet data loss, 4.1-fix 2).
        materialized = _filter_materialized_entities(
            materialize_entity_placements(self.adapter, self._full_cfg,
                                          sheet_names=self.sheet_names,
                                          position_overrides=self.position_overrides),
            _split_comma_values(self.only), _split_comma_values(self.cluster))
        if materialized:
            logger.info(_("Materialized {count} entity placement(s) from trees "
                          "into the apply plan").format(count=len(materialized)))
            self.cfg = dataclasses.replace(
                self.cfg, clone_placements=list(self.cfg.clone_placements) + materialized)
        logger.info(_("Resolving item execution order (dependency chain — see dependency_order.py)..."))
        self.items = resolve_execution_order(
            self.adapter, self.cfg, sheet_names=self.sheet_names,
            isolate_spokes=self.isolate_spokes)
        logger.info(_("Execution order: {order}")
                    .format(order=" -> ".join(it.label for it in self.items)))

    def _create_planner(self) -> None:
        self.planner = PlacementPlanner(self.adapter, self.cfg, sheet_names=self.sheet_names,
                                        position_overrides=self.position_overrides,
                                        isolate_spokes=self.isolate_spokes)

    # ── Dry‑run ─────────────────────────────────────────────────────────────

    def _dry_run(self) -> list[str]:
        """Plan without touching the board and return a printable report.

        Builds a structured list of lines instead of printing to stdout
        directly (П.8) — the CLI layer prints them (``print("\\n".join(...))``)
        and a future GUI panel could render them. Also stored on
        ``self.dry_run_report`` so library callers can grab the report
        without going through stdout at all.
        """
        coordinate_moves = (build_coordinate_moves(
                                self.adapter, self.cfg.coordinate_placements,
                                points=self.cfg.points, sheet_names=self.sheet_names,
                                position_overrides=self.position_overrides)
                           if self.cfg.coordinate_placements else [])
        # NOTE (2026-08-12, Group 2 review): a dry run does NOT apply Phase 0 —
        # build_coordinate_moves only COMPUTES MoveCommands, nothing moves on
        # the board — so Phase 1 below necessarily resolves Chain/ClonePlacement
        # anchors from their CURRENT positions, not the post-Phase-0 ones a real
        # apply would use (refresh_board() here would be a pure no-op, the board
        # is unchanged). The divergence is honest and documented in the report
        # below, same as the existing "planned from the CURRENT board" note.
        moves = self.planner.plan_items(self.items)
        vias = self.planner.plan_vias()
        tracks = self.planner.plan_tracks()
        lines: list[str] = []
        lines.append("\n=== DRY RUN ===")
        lines.append(_("Order: {order}").format(order=" -> ".join(it.label for it in self.items)))
        if coordinate_moves:
            lines.append(_("Coordinate placements (Phase 0, before the order above):"))
            for m in coordinate_moves:
                lines.append(_("  {ref}: ({x:.3f}, {y:.3f}) mm, angle={angle:.1f}°")
                             .format(ref=m.ref, x=m.position.x / 1e6, y=m.position.y / 1e6,
                                     angle=m.angle.degrees))
            lines.append(_("(Phase 0 moves are NOT applied in a dry run — the "
                           "Chain/ClonePlacement moves below are still planned from "
                           "their CURRENT positions, not from the post-Phase-0 board "
                           "a real apply would produce)"))
        lines.append(_("Moves:"))
        for m in moves:
            lines.append(_("  {ref}: ({x:.3f}, {y:.3f}) mm, angle={angle:.1f}°")
                         .format(ref=m.ref, x=m.position.x / 1e6, y=m.position.y / 1e6,
                                 angle=m.angle.degrees))
        lines.append("\n" + _("Vias:"))
        for v in vias:
            lines.append(_("  via for {owner}: ({x:.3f}, {y:.3f}) mm, net={net}")
                         .format(owner=v.owner_ref, x=v.position.x / 1e6, y=v.position.y / 1e6,
                                 net=v.net_name))
        lines.append("\n" + _("Tracks:"))
        for t in tracks:
            lines.append(_("  track for {owner}: ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, "
                           "net={net}, width={w} mm")
                         .format(owner=t.owner_ref, sx=t.start.x / 1e6, sy=t.start.y / 1e6,
                                 ex=t.end.x / 1e6, ey=t.end.y / 1e6, net=t.net_name, w=t.width_mm))
        if self.cfg.net_traces:
            lines.append(_("(net_traces entries are included in the vias/tracks sections above; "
                           "their anchors are resolved from CURRENT component positions, so a real "
                           "apply after a Chain/CoordinatePlacement moves the anchor will follow it)"))
        lines.append("\n" + _("(thermal via keepout is computed based on CURRENT component positions, "
                              "not the target ones — may slightly differ from the real run)"))
        lines.append(_("(track collisions with other copper/components are NOT checked by this tool — "
                       "rely on KiCad DRC after placement)"))
        lines.append(_("(items later in the dependency chain above are ALSO planned from the CURRENT "
                       "board, not the post-move board of their prerequisite — a real apply may place "
                       "them differently; rerun without --dry-run for the true chained result)"))
        self.dry_run_report = lines
        return lines

    # ── Execution ───────────────────────────────────────────────────────────

    def _execute(self) -> None:
        ctx = self.ctx
        # Resolved absolute paths live on RuntimeContext (P1-3). Fall back to
        # the raw Config value only when no ctx was preloaded — author.py's
        # apply_config may be called with ctx=None and a manually built Config
        # whose registry_path is already absolute (see its docstring).
        registry_path = ((ctx.registry_path if ctx else self.cfg.registry_path)
                         or registry_path_for_config(self.config_path))
        track_registry_path = ((ctx.track_registry_path if ctx else self.cfg.track_registry_path)
                               or track_registry_path_for_config(self.config_path))
        executor = BatchExecutor(
            self.adapter, self.cfg, batch_size=self.batch_size,
            operation_log_dir=((ctx.operation_log_dir if ctx else self.cfg.operation_log_dir)
                               or default_operation_log_dir_for_config(self.config_path)),
        )
        registry = PlacementRegistry(self.adapter, registry_path)
        track_registry = TrackRegistry(self.adapter, track_registry_path)

        # --- Phase 0: coordinate_placements ("dumb placer") — self-
        # contained absolute-position moves, no dependency on anything else
        # in this run (each one resolves its OWN existing footprint by
        # Cluster+Role, see coordinate_position_calculator.py), so it never
        # needs dependency_order.py's producer/consumer graph. Runs BEFORE
        # Phase 1 so a Chain/ClonePlacement anchor that happens to coincide
        # with a coordinate-placed component sees its FINAL position, not a
        # stale pre-move one. No registry involvement (see
        # CoordinatePlacement's own docstring) — always applied
        # unconditionally, same idempotency model Phase 1's own moves below
        # already use.
        if self.cfg.coordinate_placements:
            coordinate_moves = build_coordinate_moves(
                self.adapter, self.cfg.coordinate_placements,
                points=self.cfg.points, sheet_names=self.sheet_names,
                position_overrides=self.position_overrides)
            logger.info(_("Coordinate placements: {count} moves").format(count=len(coordinate_moves)))
            coordinate_failed = executor.execute_moves(
                coordinate_moves,
                check_collisions=not self.no_collision_check,
                collision_margin_mm=self.collision_margin,
            )
            if coordinate_failed:
                logger.warning(_("Failed to move (coordinate_placements): {refs}")
                               .format(refs=sorted(set(coordinate_failed))))
            self.adapter.refresh_board()

        # --- Phase 1: moves, one dependency-order item at a time ---
        self.planner.begin_planning()
        failed_refs: list[str] = []
        for idx, item in enumerate(self.items):
            if idx > 0:
                self.adapter.refresh_board()
            if item.anchor_ref is not None and item.anchor_ref in failed_refs:
                logger.warning(_("{label}: anchor {ref!r} failed to move earlier in this run — "
                                 "this item's placement is based on its OLD position")
                               .format(label=item.label, ref=item.anchor_ref))
            item_moves = self.planner.plan_item(item)
            logger.info(_("  {label}: {count} moves")
                        .format(label=item.label, count=len(item_moves)))
            item_failed = executor.execute_moves(
                item_moves,
                check_collisions=not self.no_collision_check,
                collision_margin_mm=self.collision_margin,
            )
            failed_refs.extend(item_failed)
        if failed_refs:
            logger.warning(_("Failed to move: {refs}").format(refs=sorted(set(failed_refs))))

        # --- Reload board ---
        logger.info(_("Reloading board data before planning vias..."))
        self.adapter.refresh_board()

        # --- Phase 2: vias ---
        all_vias = self.planner.plan_vias()
        # net_traces are captured from ALREADY-EXISTING hand-routed copper, so
        # the first apply (board unchanged since extract) must not duplicate
        # it: claim any planned net-trace via/track already sitting exactly at
        # the planned position into the registries (one-time migration, see
        # net_trace_planner.adopt_net_trace_copper). plan_vias() above already
        # populated the planner's net-trace caches.
        adopt_net_trace_copper(
            self.adapter, registry, track_registry,
            self.planner._net_trace_vias or [], self.planner._net_trace_tracks or [])
        vias_to_create, vias_to_delete = registry.reconcile(all_vias,
                                                            known_anchor_ids=self.all_anchor_ids)
        # Batch deletion — kipy's remove_items_by_id() accepts a list, so N
        # stale vias go out as a single IPC request (see adapter.remove_by_ids).
        if vias_to_delete:
            self.adapter.remove_by_ids(vias_to_delete)
        logger.info(_("Planned vias: {total}, actually to create (registry filtered already "
                       "correctly placed): {to_create}")
                    .format(total=len(all_vias), to_create=len(vias_to_create)))
        logger.info(_("Applying vias..."))
        failed_vias = executor.execute_vias(vias_to_create, registry=registry)
        if failed_vias:
            logger.warning(_("Failed to create vias near: {refs}")
                           .format(refs=sorted(set(failed_vias))))

        # --- Phase 3: tracks ---
        all_tracks = self.planner.plan_tracks()
        # Live tracks fetched ONCE for both consumers below — reconcile() would
        # otherwise call adapter.get_tracks() again internally (registry.py's
        # _get_live_items) just to build its uuid index, and the positional
        # pre-check needs the same list.
        live_tracks = self.adapter.get_tracks()
        tracks_to_create, tracks_to_delete = track_registry.reconcile(
            all_tracks, known_anchor_ids=self.all_anchor_ids, live_items=live_tracks)
        # Batch deletion — same single-IPC rationale as the via path above.
        if tracks_to_delete:
            self.adapter.remove_by_ids(tracks_to_delete)
        # Positional pre-check of tracks — unregistered-copper idempotency
        # (analog of the via pre-check). Run UNCONDITIONALLY (2026-08-31,
        # plan_2026_08_31_duplicate_tracks_after_tree_redraw): a repeated redraw
        # of the same cell through two different mechanisms (a legacy
        # clone_placement AND an Entity materialized from a tree) plans the
        # SAME physical tracks under DIFFERENT registry keys (point:/role:/name:
        # anchors), so the registry path alone cannot recognise them as already
        # placed — the pre-check is the only cross-key dedup. STRICTLY AFTER
        # reconcile(), on its to_create list: a pre-reconcile skip would drop
        # the key from seen_keys and make prune delete the REGISTERED tool
        # track (see plan_2026_08_16_position_based_copper_idempotency.md).
        # Skip-only — never removes/adopts foreign copper, so it can only
        # prevent a literal duplicate, never remove needed copper.
        tracks_to_create = filter_existing_tracks(tracks_to_create, live_tracks)
        logger.info(_("Planned tracks: {total}, actually to create (registry filtered already "
                       "correctly placed): {to_create}")
                    .format(total=len(all_tracks), to_create=len(tracks_to_create)))
        logger.info(_("Applying tracks..."))
        failed_tracks = executor.execute_tracks(tracks_to_create, registry=track_registry)
        if failed_tracks:
            logger.warning(_("Failed to create tracks near: {refs}")
                           .format(refs=sorted(set(failed_tracks))))

        if not failed_refs and not failed_vias and not failed_tracks:
            logger.info(_("✅ All operations completed successfully"))
        else:
            logger.warning(_("⚠️ Some operations failed – check the log."))

    # ── Public entry point ──────────────────────────────────────────────────

    def run(self) -> list[str] | None:
        """Run the full pipeline.

        Returns the dry-run report (list of lines) when dry_run is set,
        otherwise None. The library never prints — the CLI layer decides how
        to present the returned report (П.8).
        """
        self._load_config()
        self._filter_config()
        self._connect_adapter()
        self._validate()
        self._resolve_order()
        self._create_planner()

        if self.dry_run:
            return self._dry_run()
        self._execute()
        return None


# ── Module-level convenience entry point ──────────────────────────────────


@dataclasses.dataclass
class RunOptions:
    """Typed replacement for the ad-hoc ``argparse.Namespace`` that
    :func:`cmd_apply` used to read.

    Carries every knob the :class:`ApplyPipeline` needs for one apply run, so
    library callers (:mod:`kicadstamp.author`) can build a fully-typed option
    object instead of synthesizing a fake ``argparse.Namespace``. Defaults
    mirror :class:`ApplyPipeline`'s own keyword defaults (``timeout_ms=20000``,
    ``batch_size=10``, ``collision_margin=0.2``).
    """

    config_path: str
    timeout_ms: int = 20000
    batch_size: int = 10
    dry_run: bool = False
    no_selection: bool = False
    no_collision_check: bool = False
    collision_margin: float = 0.2
    only: list[str] | None = None
    cluster: list[str] | None = None


def run_apply(options: RunOptions, cfg=None, ctx=None) -> list[str] | None:
    """Run the apply pipeline for a fully-typed :class:`RunOptions`.

    *options* — the typed run configuration (:class:`RunOptions`).
    *cfg*     — pre-loaded :class:`~kicadstamp.config.Config` (skips config
                load when given).
    *ctx*     — pre-loaded :class:`RuntimeContext` paired with *cfg*.

    This is the library-facing entry point; the CLI wrapper :func:`cmd_apply`
    and :func:`kicadstamp.author.apply_config` both funnel into it.

    Returns the dry-run report (list of lines) when *options.dry_run* is set,
    else None — the caller decides how to print/present it (П.8).
    """
    pipeline = ApplyPipeline(
        config_path=options.config_path,
        timeout_ms=options.timeout_ms,
        batch_size=options.batch_size,
        dry_run=options.dry_run,
        no_selection=options.no_selection,
        no_collision_check=options.no_collision_check,
        collision_margin=options.collision_margin,
        only=options.only,
        cluster=options.cluster,
        preloaded_cfg=cfg,
        preloaded_ctx=ctx,
    )
    return pipeline.run()


def cmd_apply(args, cfg=None, ctx=None):
    """Thin shim: build a :class:`RunOptions` from an ``argparse.Namespace``
    and hand it to :func:`run_apply`.

    Kept only so :mod:`kicadstamp_cli` can keep calling the same name. New
    callers should use :func:`run_apply` with a real :class:`RunOptions`
    instead of going through a synthetic Namespace.

    Extracted from ``kicadstamp_cli.py`` to break the import cycle:
    ``author.py → kicadstamp_cli.py → author.py``.
    """
    options = RunOptions(
        config_path=args.config,
        timeout_ms=args.timeout_ms,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        no_selection=getattr(args, "no_selection", False),
        no_collision_check=args.no_collision_check,
        collision_margin=args.collision_margin,
        only=getattr(args, "only", None),
        cluster=getattr(args, "cluster", None),
    )
    return run_apply(options, cfg=cfg, ctx=ctx)
