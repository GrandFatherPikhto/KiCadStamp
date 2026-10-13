# kicadstamp/author.py
"""
author.py — build a config in real Python (loops, computed values) instead of
hand-writing repetitive blocks, where copy-paste mistakes live. Config
dataclasses (config/models.py) are already plain, so this module adds nothing
new to them, just two ways to get a built record somewhere useful:

  (a) apply_config() — straight into the existing apply pipeline
      (run_apply() already accepts a pre-built Config).
  (b) dump_template() — serialize back to s-expr (dict_to_sexp, 2026-08-28 —
      was YAML; the config graph is now .sexp/.json only), so generated
      subsystem files stay diffable/reviewable in git even when authored by a
      script.

The dump_clone_placements() / --apply CLI pair went with the retired
`clone_placements:` section (2026-10-13, plan_2026_10_08_remove_placer): a
script that still writes that section now produces a file the loader refuses.

No changes to the planner/executor/registry engine or the config format —
both are strictly additive.
"""
import dataclasses
from typing import Any

from .config import Config, RuntimeContext
from .config.sexp_format import dict_to_sexp
from .constants import DEFAULT_BATCH_SIZE, DEFAULT_TIMEOUT_MS
from .apply_pipeline import RunOptions, run_apply
from .i18n import _

_MISSING = dataclasses.MISSING


def dump_template(template_dict: dict, path: str) -> None:
    """Writes a template_extraction.extract_template_from_selection() result
    (already {name: {...}} shaped) wrapped as {'cells': {name: {...}}} to
    path as s-expr, ready for include: (cells_file:/cell_files: were folded
    into include: 2026-08-02 — see
    handoff_2026_08_02_cells_include_unification.md — include: expects the
    wrapped shape, same as an inline cells: block). Always overwrites the
    whole file, matching dump_clone_placements — a script
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
