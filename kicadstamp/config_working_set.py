# kicadstamp/config_working_set.py
"""
ConfigWorkingSet — in-memory staged overlay of the config graph
(2026-09-01, plan techdocs/handoff/deepseek/plan_2026_09_01_project_save_model.md).

"Полный staging": every GUI dock edit lands in the working set instead of on
disk. Every reader that goes through cached_file_read() (config/includes
._load_config_file, config_writer._read_data, gui/yaml_io.load_data — the
single raw-read chokepoint) sees the staged content for a dirty file, so the
config tree, the name collectors, load_config and Redraw/Apply all reflect
the working state immediately ("сработало"), while the final files stay
untouched until the global Save flushes the working set to disk.

The working set is a process-global singleton (like the file cache) and OFF
by default (`enabled`): CLI runs and pre-existing unit tests never enable it,
so every write helper falls straight through to its physical write exactly as
before. The GUI enables it when a project root is set and clears it on
root-change/close (with the same unsaved-changes guard the tree already has).

NOT to be confused with the retired PendingRegistry (gui/docks/pending.py) —
that staged BOARD edits; this is the config graph in memory.

Flush order (plan step 5 — validated BEFORE any write, so a cross-file
inconsistency aborts with nothing written):
  1. validate the staged graph via load_config(root) — with the read
     interception it reads the staged graph and catches cross-file
     inconsistencies (e.g. an Entity renamed in file A while file B still
     references the old name);
  2. backup every existing dirty file into history/ (next to the root config);
  3. write each dirty file to a temp sibling + os.replace() in one pass
     (per-file atomic); __new__ files are created, __deleted__ files removed;
  4. invalidate caches and clear the working set.
"""
import copy
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .utils.file_cache import invalidate_graph_path, invalidate_path

logger = logging.getLogger(__name__)

# Sentinel values stored in a staged dict to mark a whole-file lifecycle state.
# They can never collide with a real config key: real config files are dicts of
# top-level section names; a lone sentinel key inside the staged overlay is a
# structural marker, not part of the file's content.
_NEW = "__kicadstamp_new__"        # file does not exist on disk yet; create on flush
_DELETED = "__kicadstamp_deleted__"  # delete the physical file on flush


class ConfigWorkingSet:
    """The staged overlay. See the module docstring for the model.

    Callbacks (`add_listener`) are plain zero-arg callables — this module is
    core and must not depend on Qt; the GUI subscribes (debounced refresh +
    dirty indicator).
    """

    def __init__(self) -> None:
        # resolved path string -> full staged dict (the file's ENTIRE content,
        # already merged; read-merge-write against it accumulates naturally).
        self._staged: Dict[str, dict] = {}
        # resolved path string -> True when the file doesn't exist on disk yet.
        self._new: set = set()
        # resolved path string -> True when the physical file is to be deleted.
        self._deleted: set = set()
        # Whether staging is active. OFF by default — CLI and pre-existing unit
        # tests never turn it on, so helpers write physically as before.
        self.enabled: bool = False
        self._listeners: List[Callable[[], None]] = []

    # ── read side (used by cached_file_read / _read_data) ─────────────────

    def is_staged(self, resolved: str) -> bool:
        return resolved in self._staged

    def staged_content(self, resolved: str) -> Optional[dict]:
        """The staged dict for `resolved` (the file's whole content), or None
        when the file is not staged. The caller must deepcopy before mutating
        (same contract as the file cache's shared objects)."""
        return self._staged.get(resolved)

    def is_new(self, resolved: str) -> bool:
        return resolved in self._new

    # ── write side ───────────────────────────────────────────────────────

    def stage_write(self, path: Path, data: dict) -> None:
        """Stage a whole-file write: `data` becomes the file's entire staged
        content (the write helpers already read-merged it). Marks the file
        dirty, records it as a to-be-created file when it doesn't exist on
        disk, drops any pending delete, invalidates the graph cache (otherwise
        a stale cached Config would shadow the staged state) and notifies
        listeners."""
        resolved = str(path.resolve())
        if not path.exists():
            self._new.add(resolved)
        self._deleted.discard(resolved)
        self._staged[resolved] = copy.deepcopy(data)
        invalidate_graph_path(path)
        self._notify()

    def stage_delete(self, path: Path) -> None:
        """Stage a whole-file delete (carried out by flush). Marks the file
        deleted and drops any staged content/new marker for it."""
        resolved = str(path.resolve())
        self._deleted.add(resolved)
        self._staged.pop(resolved, None)
        self._new.discard(resolved)
        invalidate_graph_path(path)
        self._notify()

    def dirty_paths(self) -> List[Path]:
        """Every staged file (including staged-deleted ones) as a Path, in
        stage order. New/deleted markers are resolved by flush from
        _new/_deleted."""
        return [Path(p) for p in (set(self._staged) | set(self._deleted))]

    def is_dirty(self) -> bool:
        return bool(self._staged or self._deleted)

    def clear(self) -> None:
        """Drop the whole working set (Discard, or a root switch/close). Does
        NOT touch disk; cache invalidation is done by the next physical read
        (or explicitly by the caller via reload-from-disk)."""
        changed = bool(self._staged or self._new or self._deleted)
        self._staged.clear()
        self._new.clear()
        self._deleted.clear()
        if changed:
            self._notify()

    # ── listeners (GUI subscribes for debounced refresh + dirty ●) ────────

    def add_listener(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:  # a listener must never break staging
                logger.exception("ConfigWorkingSet listener failed")

    # ── flush (the global Save) ───────────────────────────────────────────

    def flush(self, root: Path) -> List[str]:
        """Commit the working set to disk in the plan's atomic order. Returns a
        list of human-readable error messages (empty on success).

        1. validate the STAGED graph first via load_config(root) — nothing is
           written on a cross-file inconsistency;
        2. backup every existing dirty file into history/ (project root);
        3. write each dirty file to a temp sibling + os.replace() (per-file
           atomic), create __new__ files, remove __deleted__ files;
        4. invalidate the caches and clear the working set.
        """
        errors: List[str] = []
        dirty = sorted(set(self._staged) | set(self._deleted))
        if not dirty:
            return errors

        # 1. Validate BEFORE any write. load_config reads through the staged
        #    overlay (cached_file_read interception), so this validates the
        #    exact state that is about to be committed.
        from .config.loader import load_config  # lazy — avoids import cycles
        try:
            load_config(str(root))
        except Exception as e:  # noqa: BLE001 — any failure aborts the flush
            errors.append(str(e))
            return errors

        root_dir = root.resolve().parent if root else None

        # 2. Backup existing dirty files into history/ (project root).
        for resolved in dirty:
            path = Path(resolved)
            if path.exists():
                try:
                    backup_to_history(path, root_dir)
                except OSError as e:
                    errors.append(
                        "history backup failed for {path}: {error}".format(
                            path=path, error=e))

        # 3. Write all dirty files (temp + os.replace), create new, delete.
        from .config_writer import _serialize  # lazy — avoids import cycles
        self.enabled = False  # physical writes must not re-stage
        try:
            for resolved in dirty:
                path = Path(resolved)
                try:
                    if resolved in self._deleted:
                        if path.exists():
                            path.unlink()
                    else:
                        if resolved in self._new:
                            path.parent.mkdir(parents=True, exist_ok=True)
                        data = self._staged[resolved]
                        tmp = path.with_name(path.name + ".tmp")
                        with open(tmp, "w", encoding="utf-8") as f:
                            f.write(_serialize(path, data))
                        os.replace(tmp, path)
                except OSError as e:
                    errors.append("write failed for {path}: {error}".format(
                        path=path, error=e))
                    continue
                invalidate_path(path)
                invalidate_graph_path(path)
        finally:
            self.enabled = True  # staging resumes (re-enabled even on failure)

        # 4. Clear the working set (successful or not — the written files are
        #    now authoritative; anything left staged is reported by errors).
        self.clear()
        return errors


# Process-global singleton — the GUI enables it when a project root is set.
WORKING_SET = ConfigWorkingSet()


def _history_dir(root_dir: Optional[Path]) -> Path:
    """The project's history/ directory — created automatically next to the
    root config file (the project root), exactly once, lazily."""
    base = (root_dir or Path(".")).resolve()
    d = base / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup_to_history(path: Path, root_dir: Optional[Path] = None) -> Path:
    """Timestamped copy of `path` into the project's history/ directory.

    history/ lives next to the root config file (the project root) and is
    created automatically. The backup name encodes the file's path RELATIVE to
    the root (so two files that share a stem never collide), plus a sortable
    timestamp; an existing backup is never overwritten (a collision adds a
    counter suffix). Only existing files are backed up — a to-be-created
    (__new__) file has nothing to copy.
    """
    resolved = path.resolve()
    hdir = _history_dir(root_dir)
    try:
        rel = resolved.relative_to(root_dir.resolve() if root_dir else Path(".").resolve())
        rel_part = str(rel).replace(os.sep, "__")
    except ValueError:
        rel_part = resolved.name
    stem = Path(rel_part).stem
    suffix = Path(rel_part).suffix or path.suffix or ""
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    target = hdir / "{stem}_{stamp}{suffix}".format(stem=stem, stamp=stamp, suffix=suffix)
    n = 1
    while target.exists():
        target = hdir / "{stem}_{stamp}_{n}{suffix}".format(
            stem=stem, stamp=stamp, n=n, suffix=suffix)
        n += 1
    shutil.copy2(resolved, target)
    return target
