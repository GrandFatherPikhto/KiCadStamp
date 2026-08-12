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

from .config import (load_config, rule_effective_name, thermal_via_array_effective_name,
                    coordinate_placement_effective_name)
from .runtime_context import RuntimeContext
from .kicad.adapter import KiCadBoardAdapter
from .placement.planner import PlacementPlanner
from .placement.dependency_order import resolve_execution_order
from .placement.services.clone_position_calculator import clone_anchor_id
from .placement.services.via_planner import thermal_anchor_id
from .placement.services.manual_position_calculator import rule_anchor_ids
from .placement.services.coordinate_position_calculator import build_coordinate_moves
from .cluster_matching import cluster_prefix_match
from .placement.executor import BatchExecutor
from .exceptions import PlacerError
from .validation import run_all_checks, check_config_structure
from .registry import (PlacementRegistry, registry_path_for_config,
                       TrackRegistry, track_registry_path_for_config)
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


def drop_disabled_rules(cfg, _logger=None) -> "Config":
    """retired: true always wins, dropped before --only/--cluster ever see
    it — retired means "does not exist on the board right now", not
    "excluded from this particular run" (see Rule docstring in config/models.py).

    CONTRACT: filters never mutate the caller's Config. Each one returns a NEW
    derived Config and leaves the input untouched — the input object is never
    the config the pipeline applies, so a preloaded cfg (e.g. the GUI's shared
    object) is safe to reuse after a run. Pure config math, no adapter — kept
    separate so it's unit‑testable without a live KiCad connection."""
    l = _logger or logger
    disabled_rules = [r for r in cfg.rules if r.retired]
    rules = [r for r in cfg.rules if not r.retired]
    for r in disabled_rules:
        l.info(_("Rule {name!r} (net {net!r}): retired=true, skipped entirely")
               .format(name=rule_effective_name(r), net=r.net))
    return dataclasses.replace(cfg, rules=rules)


def drop_inactive_items(cfg, _logger=None) -> "Config":
    """skip: true — the inline, per-item counterpart of --only/--cluster
    (see Rule/ClonePlacement/ThermalViaArrayConfig.skip docstrings in
    config/models.py). Unlike retired: true (drop_disabled_rules above),
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

    narrowed_rules = []
    for r in cfg.rules:
        if r.skip:
            l.info(_("Rule {name!r}: skip=true, skipped this run "
                      "(existing via/tracks stay protected)").format(name=rule_effective_name(r)))
            continue
        kept_spokes = [s for s in r.spokes if not s.skip]
        for s in r.spokes:
            if s.skip:
                l.debug(_("Rule {name!r}: spoke on pad {pad} skip=true, skipped this run")
                         .format(name=rule_effective_name(r), pad=s.pad))
        if kept_spokes:
            narrowed_rules.append(dataclasses.replace(r, spokes=kept_spokes))
        else:
            l.info(_("Rule {name!r}: no non-skipped spokes left, skipped this run "
                      "(existing via/tracks stay protected)").format(name=rule_effective_name(r)))

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

    return dataclasses.replace(cfg, rules=narrowed_rules,
                               clone_placements=kept_clones,
                               thermal_via_arrays=kept_tvas,
                               coordinate_placements=kept_coords)


def apply_only_filter(cfg, only_names: list[str], _logger=None) -> "Config":
    """--only: whole-block selection by identity (rule name-or-net, clone_placement
    name, thermal_via_arrays entry name). Raises PlacerError on unmatched names.
    Pure config math, no adapter.

    CONTRACT: returns a derived Config; the input object is never mutated (see
    drop_disabled_rules). A no-op filter (no --only names) returns cfg unchanged."""
    l = _logger or logger
    if not only_names:
        return cfg
    requested = set(only_names)
    matched_rules = [r for r in cfg.rules if rule_effective_name(r) in requested]
    matched_clones = [c for c in cfg.clone_placements if c.name in requested]
    matched_tvas = [t for t in cfg.thermal_via_arrays
                    if not t.retired and thermal_via_array_effective_name(t) in requested]
    matched_coords = [cp for cp in cfg.coordinate_placements
                      if coordinate_placement_effective_name(cp) in requested]

    found_names = ({rule_effective_name(r) for r in matched_rules}
                   | {c.name for c in matched_clones}
                   | {thermal_via_array_effective_name(t) for t in matched_tvas}
                   | {coordinate_placement_effective_name(cp) for cp in matched_coords})
    missing = requested - found_names
    if missing:
        all_names = sorted(
            {rule_effective_name(r) for r in cfg.rules}
            | {c.name for c in cfg.clone_placements}
            | {thermal_via_array_effective_name(t) for t in cfg.thermal_via_arrays if not t.retired}
            | {coordinate_placement_effective_name(cp) for cp in cfg.coordinate_placements}
        )
        lines = []
        for name in sorted(missing):
            suggestion = difflib.get_close_matches(name, all_names, n=1)
            hint = (_(" (maybe you meant {suggestion!r}?)").format(suggestion=suggestion[0])
                    if suggestion else "")
            lines.append(_("  {name!r} — not found among rules, clone_placements, "
                           "thermal_via_arrays, or coordinate_placements{hint}")
                         .format(name=name, hint=hint))
        raise PlacerError(_("[error] --only: names not found:\n{lines}\nAvailable: {all}")
                          .format(lines="\n".join(lines), all=all_names))

    l.info(_("--only {requested}: rules={rules}, clone_placements={clones}, "
              "thermal_via_arrays={thermal}, coordinate_placements={coords} "
              "(everything else is ignored in this run)")
            .format(requested=sorted(requested),
                    rules=[rule_effective_name(r) for r in matched_rules],
                    clones=[c.name for c in matched_clones],
                    thermal=[thermal_via_array_effective_name(t) for t in matched_tvas],
                    coords=[coordinate_placement_effective_name(cp) for cp in matched_coords]))
    return dataclasses.replace(cfg, rules=matched_rules,
                               clone_placements=matched_clones,
                               thermal_via_arrays=matched_tvas,
                               coordinate_placements=matched_coords)


def apply_cluster_filter(cfg, cluster_paths: list[str], _logger=None) -> "Config":
    """--cluster — a second, independent selection axis (physical instance /
    Cluster field, not name). Composes with --only via AND only, never OR.
    Pure config math, no adapter.

    CONTRACT: returns a derived Config; the input object is never mutated (see
    drop_disabled_rules). A no-op filter (no --cluster paths) returns cfg unchanged."""
    l = _logger or logger
    if not cluster_paths:
        return cfg
    matched_clones = [c for c in cfg.clone_placements
                      if _matches_any_cluster(c.anchor_cluster, cluster_paths)]
    matched_tvas = [t for t in cfg.thermal_via_arrays
                    if not t.retired and _matches_any_cluster(t.anchor_cluster, cluster_paths)]
    # cp.cluster is the physical-instance field itself here (not an
    # "anchor_cluster narrowing" field like the others above — this type has
    # no separate anchor concept, see CoordinatePlacement's own docstring),
    # but the SAME prefix-match convention still applies to it.
    matched_coords = [cp for cp in cfg.coordinate_placements
                      if _matches_any_cluster(cp.cluster, cluster_paths)]

    narrowed_rules = []
    for r in cfg.rules:
        kept_spokes = [s for s in r.spokes if _matches_any_cluster(s.cluster, cluster_paths)]
        if kept_spokes:
            narrowed_rules.append(dataclasses.replace(r, spokes=kept_spokes))
        else:
            l.debug(_("Rule {name!r}: no spokes match --cluster {paths}, rule dropped")
                     .format(name=rule_effective_name(r), paths=cluster_paths))

    if not narrowed_rules and not matched_clones and not matched_tvas and not matched_coords:
        raise PlacerError(_("[error] --cluster {paths}: matched nothing among rules' spokes, "
                            "clone_placements, thermal_via_arrays, or coordinate_placements")
                          .format(paths=cluster_paths))

    l.info(_("--cluster {paths}: rules={rules} (spokes narrowed), "
              "clone_placements={clones}, thermal_via_arrays={thermal}, "
              "coordinate_placements={coords}")
            .format(paths=cluster_paths,
                    rules=[rule_effective_name(r) for r in narrowed_rules],
                    clones=[c.name for c in matched_clones],
                    thermal=[thermal_via_array_effective_name(t) for t in matched_tvas],
                    coords=[coordinate_placement_effective_name(cp) for cp in matched_coords]))
    return dataclasses.replace(cfg, rules=narrowed_rules,
                               clone_placements=matched_clones,
                               thermal_via_arrays=matched_tvas,
                               coordinate_placements=matched_coords)


# ── Compute helper ────────────────────────────────────────────────────────────

def _compute_all_anchor_ids(cfg) -> set[str]:
    """Build the FULL set of anchor IDs (before --only/--cluster narrow)
    for registry.reconcile()'s known_anchor_ids protection."""
    ids = {clone_anchor_id(c) for c in cfg.clone_placements if not c.retired}
    for r in cfg.rules:
        ids |= rule_anchor_ids(r)
    ids |= {thermal_anchor_id(t) for t in cfg.thermal_via_arrays if not t.retired}
    return ids


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

        # Internal state
        self.cfg = preloaded_cfg
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
        cfg = drop_disabled_rules(self.cfg)
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
        logger.info(_("Resolving item execution order (dependency chain — see dependency_order.py)..."))
        self.items = resolve_execution_order(self.adapter, self.cfg, sheet_names=self.sheet_names)
        logger.info(_("Execution order: {order}")
                    .format(order=" -> ".join(it.label for it in self.items)))

    def _create_planner(self) -> None:
        self.planner = PlacementPlanner(self.adapter, self.cfg, sheet_names=self.sheet_names)

    # ── Dry‑run ─────────────────────────────────────────────────────────────

    def _dry_run(self) -> list[str]:
        """Plan without touching the board and return a printable report.

        Builds a structured list of lines instead of printing to stdout
        directly (П.8) — the CLI layer prints them (``print("\\n".join(...))``)
        and a future GUI panel could render them. Also stored on
        ``self.dry_run_report`` so library callers can grab the report
        without going through stdout at all.
        """
        coordinate_moves = (build_coordinate_moves(self.adapter, self.cfg.coordinate_placements)
                           if self.cfg.coordinate_placements else [])
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
        executor = BatchExecutor(self.adapter, self.cfg, batch_size=self.batch_size)
        registry = PlacementRegistry(
            self.adapter,
            self.cfg.registry_path or registry_path_for_config(self.config_path),
        )
        track_registry = TrackRegistry(
            self.adapter,
            self.cfg.track_registry_path or track_registry_path_for_config(self.config_path),
        )

        # --- Phase 0: coordinate_placements ("dumb placer") — self-
        # contained absolute-position moves, no dependency on anything else
        # in this run (each one resolves its OWN existing footprint by
        # Cluster+Role, see coordinate_position_calculator.py), so it never
        # needs dependency_order.py's producer/consumer graph. Runs BEFORE
        # Phase 1 so a Rule/ClonePlacement anchor that happens to coincide
        # with a coordinate-placed component sees its FINAL position, not a
        # stale pre-move one. No registry involvement (see
        # CoordinatePlacement's own docstring) — always applied
        # unconditionally, same idempotency model Phase 1's own moves below
        # already use.
        if self.cfg.coordinate_placements:
            coordinate_moves = build_coordinate_moves(self.adapter, self.cfg.coordinate_placements)
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
        vias_to_create = registry.reconcile(all_vias, known_anchor_ids=self.all_anchor_ids)
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
        tracks_to_create = track_registry.reconcile(all_tracks,
                                                     known_anchor_ids=self.all_anchor_ids)
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
