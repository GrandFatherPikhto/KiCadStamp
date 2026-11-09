# gui/docks/trees_instances_sync.py
"""The bookkeeping behind TreesDock's generated `tree_instances:` tabs.

A `tree_instances:` declaration materializes its copy at LOAD
(`expand_tree_instances`), so the dock holds that copy as an ordinary Tree
object. Two consequences, and both were wrong on the live board (07.11):

* the copy must NEVER be written back as a hand-written `trees:` entry — not
  even after its declaration was deleted, when `cfg.tree_instances` no longer
  names it. The marker below is therefore STICKY per buffer tree: it is derived
  from the SAME cfg as the read-only index, but it also remembers what came out
  of an earlier expansion for as long as that tree stays in the buffer. Staged,
  a stale `__switch_b` copy named records that no longer existed and made the
  next load fatal.
* a reload with a DIRTY buffer must STILL refresh the copies: nobody edits them
  by hand (they are read-only), so replacing them wholesale loses nothing,
  while keeping them showed geometry from before the template's edit and kept
  tabs for declarations that were already gone.

The first two functions are pure (lists and sets in, lists and sets out), so the
merge is testable without a dock, a widget or a file. The other two are the two
HALVES of one root re-read — `reload_root` (read + merge, touches no widget) and
`apply_reloaded` (bind the result into the dock) — so the dock's `reload_trees`
and the debounced refresh of generated tabs run the SAME steps instead of two
copies that could drift.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from kicadstamp.config import load_config
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.trees import Tree

logger = logging.getLogger(__name__)


def declared_instance_names(cfg) -> set[str]:
    """The names `cfg` carries as GENERATED instance trees: its own declarations,
    which is exactly what TreesDock._rebuild_instance_index reads its read-only
    index from — one source, so the two views can never disagree."""
    instances = getattr(cfg, "tree_instances", None) or []
    return {ti.name for ti in instances}


def mark_generated(buffer, declared: set[str], previous: set[str]) -> set[str]:
    """The new STICKY marker: what an earlier expansion produced and is still in
    the buffer, plus the names these declarations generate NOW. A tree that left
    the buffer drops out, so nothing is remembered forever."""
    names = {t.name for t in buffer}
    return (previous & names) | (declared & names)


def merge_reload(buffer, fresh, declared: set[str],
                 generated: set[str]) -> list[Tree]:
    """`reload_trees`' DIRTY-buffer merge: hand-written buffer trees stay EXACTLY
    as they are (unsaved edits survive), externally added ones are appended, and
    every generated tree is replaced by its fresh expansion — a generated tree
    whose declaration disappeared goes with it.

    The append is index-based over the HAND-WRITTEN trees only: external writers
    ("Extract tree...") only ever APPEND, so the buffer's tail is exactly what is
    new — a name-based diff would duplicate a tree the user renamed unsaved.
    """
    kept = [t for t in buffer if t.name not in generated]
    fresh_manual = [t for t in fresh if t.name not in declared]
    return kept + fresh_manual[len(kept):] + [t for t in fresh if t.name in declared]


@dataclass(frozen=True)
class Reloaded:
    """What ONE root re-read produced, before a widget is touched.

    `error` — why nothing was read (None = fine). An unreadable file is NOT an empty tree
    list: both callers keep their previous cfg/ctx/_trees and say why, the same discipline
    TreesDock.refresh_ref_candidates follows."""

    cfg: Any = None
    ctx: Any = None
    trees: list = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def names(self) -> set:
        return {t.name for t in self.trees}


def reload_root(dock, *, merge_always: bool = False,
                quiet: bool = False) -> Optional[Reloaded]:
    """HALF ONE of a root re-read: read the config and compute the buffer it implies.

    Touches no widget, writes nothing. `None` = there is nothing to read (no root loaded).

    THE MERGE (`merge_always`). A DIRTY buffer keeps its hand-written trees exactly as they
    are and takes the fresh GENERATED ones (`merge_reload`); a CLEAN one is replaced outright.
    The second rule is what `reload_trees` needs for an EXTERNAL write — somebody else edited a
    hand-written tree and its fresh shape must win. The debounced refresh of the generated tabs
    cannot live by it: it runs on a buffer whose `_dirty` may still be False while the user is
    editing, and `rebuild_clone_pages` re-renders only the CLONE pages, so the template's rows
    would keep pointing at `TreeNode` objects the buffer no longer holds — the next edit of such
    a row is silently lost (Claude, 09.11; reproduced on `extra_sw`). The refresh therefore
    passes `merge_always=True`: hand-written trees stay the SAME objects whatever `_dirty` says.

    `quiet` — the refresh says its own refusals (`InstancesRefresh._report_refusal`, once); a
    second copy of the same sentence from here is noise. `reload_trees` keeps the word."""
    if dock._root_path is None:
        return None
    try:
        cfg, ctx = load_config(str(dock._root_path))
    except (ValidationError, OSError) as e:
        if not quiet:
            logger.warning(_("Trees: root config failed to reload: {error}").format(error=e))
        return Reloaded(error=str(e))
    fresh = list(cfg.trees)
    if not dock._dirty and not merge_always:
        return Reloaded(cfg=cfg, ctx=ctx, trees=fresh)
    declared = declared_instance_names(cfg)
    return Reloaded(cfg=cfg, ctx=ctx,
                    trees=merge_reload(dock._trees, fresh, declared, dock._generated))


def apply_reloaded(dock, reloaded: Reloaded) -> None:
    """HALF TWO: bind `reloaded` into the dock — no widget, no disk, no `_mark_dirty`.

    The ORDER is the invariant of plan_2026-09-03_trees_dock_cfg_trees_desync: `cfg.trees`
    is rebound to the buffer only AFTER the buffer holds its FINAL value, so redraw payloads
    and Save's link_trees round-trip see the same tree objects the dock shows. The read-only
    index follows — `_generated` is recomputed by `_rebuild_instance_index` ALONE (one owner),
    and its marker is sticky per BUFFER tree."""
    dock._cfg = reloaded.cfg
    dock._ctx = reloaded.ctx
    dock._trees = reloaded.trees
    dock._cfg.trees = dock._trees
    dock._rebuild_instance_index()
