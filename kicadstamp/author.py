# kicadstamp/author.py
"""
author.py — build ClonePlacement/Rule in real Python (loops, computed
values) instead of hand-writing repetitive config, where copy-paste mistakes
live (wrong nets: key, duplicate anchor_pad:, wrong anchor_sheet — all hit
live in one working session). Config/ClonePlacement/Rule (config/models.py)
are plain dataclasses already — this module adds nothing new to them, just
two ways to get a built list somewhere useful:

  (a) apply_config() — straight into the existing apply pipeline
      (run_apply() already accepts a pre-built Config).
  (b) dump_clone_placements()/dump_rules()/dump_template() — serialize back
      to s-expr (dict_to_sexp, 2026-08-28 — was YAML; the config graph is
      now .sexp/.json only), so generated subsystem files stay diffable/
      reviewable in git even when authored by a script.

The standard --apply/--dry-run CLI entry point wiring (c) lived here too,
but was split out into kicadstamp/author_cli.py so this module stays a pure
library — no argparse / sys.exit / CLI exit-code concerns.

No changes to the planner/executor/registry engine or the config format —
both are strictly additive.
"""
import dataclasses
from typing import Any

from .config import Chain, ClonePlacement, Config, RuntimeContext
from .config.sexp_format import dict_to_sexp
from .constants import DEFAULT_BATCH_SIZE, DEFAULT_TIMEOUT_MS
from .apply_pipeline import RunOptions, run_apply
from .i18n import _

_MISSING = dataclasses.MISSING


def _default_for(f: "dataclasses.Field") -> Any:
    if f.default is not _MISSING:
        return f.default
    if f.default_factory is not _MISSING:  # type: ignore[misc]
        return f.default_factory()
    return _MISSING


def _prune_defaults(obj: Any) -> Any:
    """dataclass instance -> plain dict, dropping any field equal to its
    default (scalar default or default_factory() instance) — keeps
    generated output close to the hand-written minimal style the s-expr
    writer already uses. Required fields (no default at all, e.g.
    ClonePlacement.name/xy, Chain.net/spokes) are always
    kept regardless of value. Recurses into nested dataclasses and lists of
    them (only nesting that exists in these models: Chain.spokes -> List[ManualSpoke])."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result = {}
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            default = _default_for(f)
            if default is not _MISSING and value == default:
                continue
            # ClonePlacement polar offset (2026-08-12, Group 2 fix): when
            # radius_mm/angle_deg are set, xy is the meaningless (0.0, 0.0)
            # placeholder — writing it would make a reload of the dumped YAML
            # fatal ("clone_placement has both xy and radius_mm/angle_deg").
            if (isinstance(obj, ClonePlacement) and f.name == "xy"
                    and (obj.radius_mm is not None or obj.angle_deg is not None)):
                continue
            if dataclasses.is_dataclass(value):
                result[f.name] = _prune_defaults(value)
            elif isinstance(value, list):
                result[f.name] = [_prune_defaults(v) if dataclasses.is_dataclass(v) else v
                                   for v in value]
            elif isinstance(value, tuple):
                # e.g. ClonePlacement.xy — plain yaml.dump (see dump_clone_placements/
                # dump_rules below) has no clean representer for tuples, it would
                # emit an unreadable !!python/tuple tag that config/loader.py's
                # yaml.safe_load can't parse back. A list dumps as plain [x, y].
                result[f.name] = list(value)
            else:
                result[f.name] = value
        return result
    return obj


def _require_fragment_graph_root(path: str, graph_root) -> None:
    """Refuse a format-3 fragment write without the graph root it belongs to.

    dump_clone_placements/dump_chains write a FRAGMENT: its records reference
    §0 records (cells:, points:, …) that live in the profile root the fragment
    is later include:'d into, NOT in the fragment itself. Under format 3 the
    writer's stamp resolves every reference UUID against the graph of an
    explicit `graph_root` (У4.2, the same contract as gui/docks/entity_export);
    a fragment alone has no such graph, so the write is refused with a hint
    rather than let the stamp fail on a dangling reference. Under format 2 no
    stamp runs and `graph_root` is inert."""
    from .config.format_version import current_format
    from .exceptions import ValidationError, format_fatal_error

    if current_format() >= 3 and graph_root is None:
        raise ValidationError(format_fatal_error(
            _("format 3: cannot write the fragment — the graph root is not set"),
            [_("in {path}: pass graph_root — the root config the fragment will "
               "be included into; a fragment cannot resolve its own references")
             .format(path=path)]))


def dump_clone_placements(clones: list[ClonePlacement], path: str, *,
                          graph_root=None) -> None:
    """Writes {'clone_placements': [...]} to path as s-expr — a file directly
    usable via include: (see kicadstamp/config/includes.py) or as a whole
    profile. The caller is responsible for naming the output .sexp (the
    config graph is s-expr/.json only since 2026-08-28).

    `graph_root` is the profile root the fragment will be included into —
    REQUIRED under format 3, where the write resolves the fragment's reference
    UUIDs against that graph (see _require_fragment_graph_root); ignored under
    format 2."""
    data = {"clone_placements": [_prune_defaults(c) for c in clones]}
    # Through the ONE config writer (Т3): it stamps the CURRENT format number,
    # writes atomically, and takes the `.bak` itself when the target is still an
    # older format.
    from .config_writer import write_config_file

    _require_fragment_graph_root(path, graph_root)
    write_config_file(path, data, graph_root=graph_root)


def dump_chains(chains: list[Chain], path: str, *, graph_root=None) -> None:
    """Writes {'chains': [...]} to path as s-expr — same include:-ready shape
    and same `graph_root` contract as dump_clone_placements."""
    data = {"chains": [_prune_defaults(c) for c in chains]}
    # Through the ONE config writer (Т3), same contract as dump_clone_placements.
    from .config_writer import write_config_file

    _require_fragment_graph_root(path, graph_root)
    write_config_file(path, data, graph_root=graph_root)


# Backward-compat alias for the 2026-09-01 Rule -> Chain rename.
dump_rules = dump_chains


def dump_template(template_dict: dict, path: str) -> None:
    """Writes a template_extraction.extract_template_from_selection() result
    (already {name: {...}} shaped) wrapped as {'cells': {name: {...}}} to
    path as s-expr, ready for include: (cells_file:/cell_files: were folded
    into include: 2026-08-02 — see
    handoff_2026_08_02_cells_include_unification.md — include: expects the
    wrapped shape, same as an inline cells: block). Always overwrites the
    whole file, matching dump_clone_placements/dump_rules — a script
    re-running extract for one subsystem should produce a clean, idempotent
    regeneration of its own dedicated file, not accumulate into a shared one.
    Use cmd_extract/the CLI directly if you want the merge behaviour
    instead."""
    # Through the ONE config writer (Т3), same contract as dump_clone_placements.
    from .config_writer import write_config_file

    write_config_file(path, {"cells": template_dict})


def apply_config(cfg: Config, config_path: str, *, ctx: RuntimeContext | None = None,
                 dry_run: bool = False,
                 only: list[str] | None = None, cluster: list[str] | None = None,
                 timeout_ms: int = DEFAULT_TIMEOUT_MS, batch_size: int = DEFAULT_BATCH_SIZE,
                 no_collision_check: bool = False, collision_margin: float = 0.2
                 ) -> list[str] | None:
    """Runs cfg through the exact same pipeline a config-driven `apply` run
    uses (run_apply() already accepts a pre-built Config — this just builds
    the typed :class:`~kicadstamp.apply_pipeline.RunOptions` it needs).

    config_path is NOT cosmetic: when cfg.registry_path/cfg.track_registry_path
    are unset, run_apply derives them FROM IT (registry_path_for_config() /
    track_registry_path_for_config(): '<config-dir>/registry/<stem>.registry.json'
    and '<config-dir>/tracks/<stem>.tracks.registry.json' — subfolders next to
    the config, 2026-09-04 plan root_metadata_path_defaults). A throwaway
    placeholder here would misfile or collide registries between unrelated
    scripted runs — exactly the class of bug fixed in this project before
    (registry prune granularity, thermal via duplication). Either point
    config_path at a real (possibly nonexistent-on-disk) path that identifies
    this run, or set cfg.registry_path/cfg.track_registry_path explicitly
    yourself.

    Deliberately does not re-run validation.run_all_checks() first: run_apply
    already does, before resolve_execution_order and before any board
    mutation — a separate pre-check here would only duplicate that work.
    """
    options = RunOptions(
        config_path=config_path,
        timeout_ms=timeout_ms,
        batch_size=batch_size,
        dry_run=dry_run,
        no_selection=False,
        no_collision_check=no_collision_check,
        collision_margin=collision_margin,
        only=only,
        cluster=cluster,
    )
    return run_apply(options, cfg=cfg, ctx=ctx)
