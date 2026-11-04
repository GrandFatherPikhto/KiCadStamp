# kicadstamp/placement/in_place_entities.py
"""A record that NO tree places is materialized AT ITS LIVE PLACE.

Denis, 28.10.2026: «ячейка через запись должна читаться, перечитываться и находить
свою медь вне зависимости от того, стоит запись в дереве или нет. Дерево — способ
РАССТАВИТЬ экземпляры, а не условие, без которого их медь не видна.»

WHY. `materialize_entity_placements` walks `cfg.trees` only, so an Entity with a
cell and a cluster but no placement node yields no `ClonePlacement` at all — the
redraw planner then produces no copper for it, and «Select cell» has nothing left
but the registry («copper — registry only: 0»; HP-Power, 04.11, `dcdc24p15n5_sw_a`).
This module is the missing branch: the record's pose is READ from the live cluster
of its own cell (kicadstamp/live_cluster_frame.py — the ONE reader the overlay and
the tree-node read use), and a transient `ClonePlacement` is built exactly the way
a tree node builds one, so every existing planner path applies it unchanged.

READ-ONLY BY DECISION (plan §8.1). The branch is behind
`ApplyPipeline(in_place_entities=True)` and that flag is refused with `dry_run=False`:
only «Select cell» and «Subtract selected copper» turn it on, both dry runs. A real
Apply that would MOVE something, create copper or write a registry file for a record
outside trees is a separate decision (§7.1/§7.7 п.5 of the plan).

WHAT IS *NOT* DONE HERE, on purpose:
* a record a tree places is skipped — the tree decides, byte for byte (one record,
  one place);
* an imprint record is skipped — it has its own plan (imprint_apply.py);
* a record whose cell cannot be identified WITHOUT the user's selection is skipped
  with a warning: an apply must never depend on what happens to be selected today
  (kicadstamp/live_cluster_frame.py is not consulted twice — the same reader that
  measured the frame answered the question);
* nothing is read from the board until `--only`/`--cluster` asked for the record
  (§7.4: live reads only for what the run requested).

THE ROLE MAP travels INTO the transient clone as `ClonePlacement.refs` (step 0 of
resolve_roles_by_nets — "explicit override, bypassing the search"). That is what
keeps a twin instance safe: `DCDC24P15N5` stands twice on HP-Power (SW A and SW B,
eleven identical roles each), so a second identification by cluster alone would be
the very channel swap the tree_instances defect was about. `refs` is the same map
the frame was fitted against, so the plan and the frame cannot disagree.
"""
from __future__ import annotations

import dataclasses
import logging

from ..cluster_matching import matches_any_cluster
from ..config import ClonePlacement
from ..exceptions import NonRigidClusterError, ValidationError
from ..i18n import _
from ..live_cluster_frame import live_cluster_frame
from .services.clone_role_resolver import clone_uses_selection_mode

logger = logging.getLogger(__name__)

__all__ = ["materialize_in_place_entity_placements"]


def _wanted(entity, only_set: set | None, cluster_paths: list | None) -> bool:
    """`--only`/`--cluster` over ONE record, BEFORE any board read.

    WIDER-OR-EQUAL is the contract (the same one `_structural_candidates` /
    `_tree_is_wanted` keep for trees): both identities an Entity exposes are
    checked — its effective name AND its cluster — so a name that only a cluster
    matches still reaches the record. The final narrowing stays the caller's
    `_filter_materialized_entities`, which is idempotent on the result."""
    if only_set is not None \
            and not ({entity.name, entity.cluster or entity.name} & only_set):
        return False
    if cluster_paths is not None \
            and not matches_any_cluster(entity.cluster, cluster_paths):
        return False
    return True


def _no_cell_reason(cfg, entity) -> str | None:
    """Why this record cannot be read live at all, or None when it can — the same
    three words `gui/docks/live_position.entity_mount_fallback_reason` uses (one
    msgid each, so the two doors phrase the same fact identically)."""
    if getattr(entity, "cell", None) is None:
        return _("the Entity has no cell")
    if cfg.cells.get(entity.cell) is None:
        return _("cell {cell!r} is not in the config").format(cell=entity.cell)
    if not getattr(entity, "cluster", None):
        return _("the Entity has no cluster")
    return None


def materialize_in_place_entity_placements(adapter, cfg, linked, sheet_names=None, *,
                                           only: list[str] | None = None,
                                           cluster: list[str] | None = None
                                           ) -> list[ClonePlacement]:
    """Every Entity that NO tree places -> one transient ClonePlacement at its
    live place (see the module docstring).

    `linked` — the SAME `link_trees(cfg, cfg.trees)` forest the tree walk
    materializes from, so "is this record placed by a tree?" is answered on the
    forest the caller already built and never by a second walk.

    `adapter` None (every config-only test) -> []; the branch needs the board.
    Purely in-memory: nothing here writes, and the saved config is untouched."""
    if adapter is None or not getattr(cfg, "entities", None):
        return []
    # Lazy: entity_placement imports THIS module (it owns the one materializer), so
    # a module-level import here would be a cycle. These two names are the OWNERS of
    # "where a tree places this Entity" and "how an Entity becomes a clone" — the
    # branch calls them instead of repeating their conditions.
    from .entity_placement import _find_entity_node, _to_clone

    sheet_names = sheet_names or {}
    # Empty == no narrowing (M4: only=[] must never mean "nothing wanted").
    only_set = set(only) if only else None
    cluster_paths = list(cluster) if cluster else None
    out: list[ClonePlacement] = []
    for entity in cfg.entities:
        if getattr(entity, "retired", False) or getattr(entity, "skip", False):
            continue
        if getattr(entity, "imprint", None) is not None:
            continue
        if not _wanted(entity, only_set, cluster_paths):
            continue
        if _find_entity_node(linked, entity.name):
            continue                       # a tree places it: the tree decides
        reason = _no_cell_reason(cfg, entity)
        if reason is not None:
            logger.warning(
                _("Entity materialization: {name!r} is placed by no tree and has "
                  "no live place ({reason}) — its copper is not planned")
                .format(name=entity.name, reason=reason))
            continue
        cell = cfg.cells[entity.cell]
        cluster_name = entity.cluster
        sheet = getattr(entity, "sheet", None) or ""
        try:
            # refresh=False: the caller has just refreshed the board (a dry run
            # connects and reads), and a refresh per record would drop the
            # adapter's caches once per record.
            frame = live_cluster_frame(adapter, cell, cluster_name, sheet,
                                       sheet_names, strict=True, refresh=False)
        except NonRigidClusterError as exc:
            logger.warning(
                _("Entity materialization: {name!r} — its live cluster is not a "
                  "rigid copy of the cell (worst deviation {deviation} mm), so "
                  "its copper is not planned")
                .format(name=entity.name,
                        deviation=f"{getattr(exc, 'residual_mm', 0.0):.3f}"))
            continue
        except ValidationError as exc:
            logger.warning(
                _("Entity materialization: {name!r} is placed by no tree and has "
                  "no live place ({reason}) — its copper is not planned")
                .format(name=entity.name, reason=exc))
            continue
        if bool(getattr(entity, "mirror", False)) != frame.mirror:
            # The record and the board disagree about the side. "In place" cannot
            # settle that: drawing it either way would move the copper to the
            # other side of the board.
            logger.warning(
                _("Entity materialization: {name!r} — the record says "
                  "mirror={record} and the live instance is mirror={live}; its "
                  "copper is not planned")
                .format(name=entity.name, record=bool(getattr(entity, "mirror", False)),
                        live=frame.mirror))
            continue
        probe = _to_clone(entity, frame.origin, frame.rotation_deg)
        if clone_uses_selection_mode(probe, adapter=adapter, cell=cell,
                                     sheet_names=sheet_names):
            logger.warning(
                _("Entity materialization: {name!r} is placed by no tree and its "
                  "cell cannot be identified without a selection — its copper is "
                  "not planned").format(name=entity.name))
            continue
        clone = dataclasses.replace(probe, mirror=frame.mirror,
                                    refs=dict(frame.role_to_ref))
        logger.info(
            _("Entity materialization: {name!r} is placed by no tree — read at "
              "its live place ({cluster!r} on {sheet!r})")
            .format(name=entity.name, cluster=cluster_name, sheet=sheet))
        out.append(clone)
    if out:
        logger.info(
            _("Entity materialization: {count} placement(s) outside trees "
              "materialized at their live places").format(count=len(out)))
    return out
