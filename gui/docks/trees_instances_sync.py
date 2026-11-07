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

Both functions are pure (lists and sets in, lists and sets out), so the merge is
testable without a dock, a widget or a file.
"""
from __future__ import annotations

from kicadstamp.trees import Tree


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
