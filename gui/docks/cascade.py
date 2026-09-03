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
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.i18n import _
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.link_trees import link_trees
from kicadstamp.tree_position import (
    PositionOverride,
    _anchor_base_live_position,
    apply_rigid_override,
    capture_rigid_state,
    curated_forest_module_content,
    curated_redraw_plan,
    curated_redraw_plan_forest,
    layout_tree_from_base,
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
        except PlacerError as e:
            # ValidationError/PlacerError family — already a well-formatted,
            # expected "fatal at the boundary" message (format_fatal_error); no
            # traceback needed (a per-record failure must not abort the rest).
            results.append((name, False, str(e)))
            logger.warning(_("Redraw dependents: {name!r} — FAILED: {error}")
                           .format(name=name, error=e))
        except Exception as e:  # noqa: BLE001 — genuinely unexpected, keep the traceback
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
        except PlacerError as e:
            # ValidationError/PlacerError family — already a well-formatted,
            # expected "fatal at the boundary" message (format_fatal_error); no
            # traceback needed (a per-record failure must not abort the rest).
            results.append((name, False, str(e)))
            logger.warning(_("Tree redraw: {name!r} — FAILED: {error}").format(name=name, error=e))
        except Exception as e:  # noqa: BLE001 — genuinely unexpected, keep the traceback
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


def run_curated_forest_redraw(config_path: str, cfg, ctx, trees: list[Tree],
                              selected_refs: set[str]
                              ) -> tuple[List[Tuple[str, bool, Optional[str]]], List[str]]:
    """Multi-tree curated redraw (plan 4.2 / design §6 + plan 2026-09-02 P3
    module embedding, design P3 D5): plans the run in the global FOREST order
    (curated_redraw_plan_forest) and applies each name in that order with a
    NON-persistent PositionOverride, choosing the override source per record:

      - NORMAL selected nodes: rigid-group capture/apply as before
        (capture_rigid_state -> apply_rigid_override re-projects the captured
        local offset into the parent's CURRENT live frame) — unchanged;
      - MODULE-CONTENT records (content_refs from curated_forest_module_content):
        NO rigid capture/apply — their absolute position comes from the stage-2
        LAYOUT (layout_tree_from_base) laid from each flow root's LIVE anchor
        (_anchor_base_live_position supports origin/auto/role/point/ref), which
        covers a root's whole reachable content (nested modules included) in one
        pass. Module content has no live "parent" to re-project against — it is
        a computed marker chain, so the pure layout value IS the override.

    With no module active both content_refs and flow_roots are empty and the
    function behaves exactly as the pre-module forest redraw. Returns
    (run_cascade-style per-name results, the plan's warnings)."""
    linked = link_trees(cfg, trees)
    names, warnings = curated_redraw_plan_forest(linked, selected_refs)
    for warning in warnings:
        logger.warning(warning)

    adapter = KiCadBoardAdapter(timeout_ms=20000)
    adapter.refresh_board()
    sheet_names = ctx.sheet_names if ctx else {}

    # Stage 2 (design P3 D5): lay each NORMAL flow root from its LIVE anchor and
    # merge the absolute overrides of everything its module markers reach.
    content_refs, flow_root_names = curated_forest_module_content(linked, selected_refs)
    by_tree = {t.name: t for t in trees}
    stage2: Dict[str, Any] = {}
    for root_name in flow_root_names:
        root = by_tree.get(root_name)
        if root is None:
            continue
        try:
            base_pos, base_rot = _anchor_base_live_position(
                adapter, cfg, root, sheet_names)
            stage2.update(layout_tree_from_base(
                root, base_pos, base_rot if base_rot is not None else 0.0,
                by_tree,
                # Own-anchor nodes (plan tree_node_own_anchor §2) need the live
                # board to lay out from their own (role) anchor — the pure
                # module-embedding callers pass nothing, this live caller has
                # the adapter right here.
                adapter=adapter, cfg=cfg, sheet_names=sheet_names))
        except Exception as e:  # noqa: BLE001 — honest, module content stays put
            logger.warning(_("Forest redraw: root tree {name!r} — stage-2 base "
                             "unavailable ({error}); its embedded module content "
                             "is left in place").format(name=root_name, error=e))

    captures: Dict[str, Any] = {}
    parent_map: Dict[str, Any] = {}
    for tree in linked:
        tree_captures, tree_parent_map = capture_rigid_state(
            adapter, cfg, tree, names, sheet_names)
        captures.update(tree_captures)
        parent_map.update(tree_parent_map)

    results: List[Tuple[str, bool, Optional[str]]] = []
    for name in names:
        logger.info(_("Forest redraw: applying {name!r}").format(name=name))
        override = None
        if name in content_refs:
            # Module content: the stage-2 absolute layout value, not rigid.
            if name in stage2:
                pos, rot = stage2[name]
                override = PositionOverride(position=pos, rotation_deg=rot)
            else:
                logger.warning(_("Forest redraw: module content {name!r} has no "
                                 "stage-2 layout value — applied from its own "
                                 "record").format(name=name))
        else:
            cap = captures.get(name)
            if cap is not None:
                parent_ref, parent_record, _is_anchor = parent_map[name]
                try:
                    override = apply_rigid_override(adapter, cfg, parent_ref,
                                                    parent_record, cap, sheet_names)
                except Exception as e:  # noqa: BLE001 — honest fallback, never break the chain
                    logger.warning(_("Forest redraw: {name!r} — rigid override failed "
                                     "({error}); falling back to the record's own position")
                                   .format(name=name, error=e))
        try:
            pipeline = ApplyPipeline(
                config_path=config_path, preloaded_cfg=cfg, preloaded_ctx=ctx,
                only=[name], dry_run=False,
                position_overrides={name: override} if override else None)
            pipeline.run()
            results.append((name, True, None))
            logger.info(_("Forest redraw: {name!r} — ok").format(name=name))
        except PlacerError as e:
            # ValidationError/PlacerError family — already a well-formatted,
            # expected "fatal at the boundary" message (format_fatal_error); no
            # traceback needed (a per-record failure must not abort the rest).
            results.append((name, False, str(e)))
            logger.warning(_("Forest redraw: {name!r} — FAILED: {error}").format(name=name, error=e))
        except Exception as e:  # noqa: BLE001 — genuinely unexpected, keep the traceback
            logger.exception("Forest redraw: %s failed", name)
            results.append((name, False, str(e)))
            logger.warning(_("Forest redraw: {name!r} — FAILED: {error}").format(name=name, error=e))
        adapter.refresh_board()
    return results, warnings


def run_curated_forest_redraw_worker(payload: dict) -> tuple:
    """start_long_op worker entry point for a multi-tree redraw — thin adapter
    over run_curated_forest_redraw (plain data in, plain data out)."""
    return run_curated_forest_redraw(
        payload["config_path"], payload["cfg"], payload["ctx"], payload["trees"],
        payload["selected_refs"])
