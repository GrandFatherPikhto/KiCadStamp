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
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.link_trees import link_trees
from kicadstamp.tree_position import (
    apply_rigid_override, capture_rigid_state, curated_redraw_plan,
)
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

    # Rigid-group redraw (plan_2026_08_29_tree_live_rigid_redraw.md §1): each
    # selected node is placed at its LIVE-captured offset from its parent,
    # re-projected into the parent's CURRENT (post-move) frame, so moving /
    # rotating the anchor moves everything attached with it. The capture
    # happens ONCE before anything moves; the apply re-reads the parent's live
    # position/rotation at apply time (parent already redrawn earlier in this
    # topological order, or hand-moved before Redraw — both read identically,
    # live). Non-persistent: only the physical movement, via the calculator
    # position_overrides (Option 1 — handoff …step0.md §3-§4).
    adapter = KiCadBoardAdapter(timeout_ms=20000)
    adapter.refresh_board()
    sheet_names = ctx.sheet_names if ctx else {}
    captures, parent_map = capture_rigid_state(adapter, cfg, tree, names, sheet_names)

    results: List[Tuple[str, bool, Optional[str]]] = []
    for name in names:
        logger.info(_("Tree redraw: applying {name!r}").format(name=name))
        override = None
        cap = captures.get(name)
        if cap is not None:
            parent_ref, parent_record, _is_anchor = parent_map[name]
            try:
                override = apply_rigid_override(adapter, cfg, parent_ref, parent_record,
                                                cap, sheet_names)
            except Exception as e:  # noqa: BLE001 — honest fallback, never break the chain
                logger.warning(_("Tree redraw: {name!r} — rigid override failed "
                                 "({error}); falling back to the record's own position")
                               .format(name=name, error=e))
        try:
            pipeline = ApplyPipeline(
                config_path=config_path, preloaded_cfg=cfg, preloaded_ctx=ctx,
                only=[name], dry_run=False,
                position_overrides={name: override} if override else None)
            pipeline.run()
            results.append((name, True, None))
            logger.info(_("Tree redraw: {name!r} — ok").format(name=name))
        except Exception as e:  # noqa: BLE001 — a per-record failure must not abort the rest
            logger.exception("Tree redraw: %s failed", name)
            results.append((name, False, str(e)))
            logger.warning(_("Tree redraw: {name!r} — FAILED: {error}").format(name=name, error=e))
        # Sync this module's adapter with the board after the run, so the NEXT
        # child's apply_rigid_override reads the parent's post-move position.
        adapter.refresh_board()
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
