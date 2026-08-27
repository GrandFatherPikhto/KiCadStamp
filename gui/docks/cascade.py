# gui/docks/cascade.py
"""
Shared "Redraw dependents" cascade (§2 of
plan_2026_08_21_anchor_dependency_tree_cascade_redraw.md).

One action, one implementation — AnchorTreeDock's context menu and PlacerDock's
button both funnel through here. The cascade is:

  1. STATIC: build the anchor graph over the loaded Config and take the start
     record + its transitive dependents in topological order (cascade_records —
     pure and fast, runs on the UI thread).
  2. LIVE: apply each record with its OWN ApplyPipeline ``--only`` run, one at
     a time in that order (run_cascade — worker-thread-safe).

Sequential runs (not one merged ``--only`` list) are deliberate: each record
then resolves its anchor from the board the previous record just moved — the
exact topological guarantee §2.2 requires — AND the caller gets true
per-record success/failure for the §2.5 log report. Per-record failures never
abort the rest of the chain; they are logged with their order position.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from kicadstamp.anchor_graph import build_anchor_graph, redraw_records_in_order
from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.link_trees import link_trees
from kicadstamp.tree_position import curated_redraw_plan
from kicadstamp.trees import Tree

logger = logging.getLogger(__name__)


def cascade_records(cfg, start_key: str) -> list:
    """Static cascade order (start first, parents before children) as
    kicadstamp.anchor_graph.Record objects. Raises ValidationError on a
    broken anchor (anchor_role into nowhere) or a dependency cycle."""
    graph = build_anchor_graph(cfg)
    return redraw_records_in_order(graph, start_key)


def run_cascade(config_path: str, cfg, ctx,
                names: List[str]) -> List[Tuple[str, bool, Optional[str]]]:
    """Worker-thread-safe: ONE ApplyPipeline --only run per name, in the
    given topological order. Returns [(name, ok, error), ...] in the same
    order — never touches a widget, only logs (which the Log dock's root
    logger handler picks up, see gui/docks/log_panel.py).

    The preloaded cfg/ctx are shared across runs: apply_pipeline's filters
    never mutate their input (each filter returns a derived Config), so a
    run's --only narrowing can never leak into the next record's run.
    """
    results: List[Tuple[str, bool, Optional[str]]] = []
    for name in names:
        logger.info(_("Redraw dependents: applying {name!r}").format(name=name))
        try:
            pipeline = ApplyPipeline(
                config_path=config_path, preloaded_cfg=cfg, preloaded_ctx=ctx,
                only=[name], dry_run=False)
            pipeline.run()
            results.append((name, True, None))
            logger.info(_("Redraw dependents: {name!r} — ok").format(name=name))
        except Exception as e:  # noqa: BLE001 — a per-record failure must not abort the rest
            logger.exception("Redraw dependents: %s failed", name)
            results.append((name, False, str(e)))
            logger.warning(_("Redraw dependents: {name!r} — FAILED: {error}")
                           .format(name=name, error=e))
    return results


def run_cascade_worker(payload: Dict[str, Any]) -> list:
    """start_long_op worker entry point — plain data in, plain data out
    (never touches a widget)."""
    return run_cascade(
        payload["config_path"], payload["cfg"], payload["ctx"], payload["names"])


def run_curated_tree_redraw(config_path: str, cfg, ctx, trees: list[Tree],
                            tree_name: str, selected_refs: set[str]
                            ) -> tuple[List[Tuple[str, bool, Optional[str]]], List[str]]:
    """Links `trees` against cfg, finds tree_name, plans a curated redraw over
    selected_refs (curated_redraw_plan), logs its structural warnings, and
    runs the resulting name list via run_cascade — the same per-name
    --only/topological-order machinery the plain "Redraw dependents" cascade
    already uses. Returns (run_cascade's per-name results, the plan's own
    warnings) so a future caller (CLI probe, GUI dock) can surface both.

    Raises ValidationError if tree_name isn't among trees (a lookup/config
    problem, not a redraw failure — same "fatal at the boundary" discipline
    as link_trees/load_trees)."""
    linked = link_trees(cfg, trees)
    tree = next((t for t in linked if t.name == tree_name), None)
    if tree is None:
        raise ValidationError(
            _("Tree {name!r} not found (known: {known})")
            .format(name=tree_name, known=", ".join(t.name for t in linked) or _("none")))
    names, warnings = curated_redraw_plan(tree, selected_refs)
    for warning in warnings:
        logger.warning(warning)
    results = run_cascade(config_path, cfg, ctx, names)
    return results, warnings


def run_curated_tree_redraw_worker(payload: dict) -> tuple:
    """start_long_op worker entry point for the trees dock — thin adapter
    over run_curated_tree_redraw (plain data in, plain data out, no widget
    access), symmetric to run_cascade_worker. The dock keeps the trees
    (plain dataclasses, not QObjects) in the payload so the worker thread can
    link/plan/run without touching the UI."""
    return run_curated_tree_redraw(
        payload["config_path"], payload["cfg"], payload["ctx"], payload["trees"],
        payload["tree_name"], payload["selected_refs"])
