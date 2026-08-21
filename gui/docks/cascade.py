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
from kicadstamp.i18n import _

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
