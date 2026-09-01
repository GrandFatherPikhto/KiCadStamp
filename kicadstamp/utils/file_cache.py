# kicadstamp/utils/file_cache.py
"""
file_cache.py — a tiny, general-purpose mtime-keyed cache for single-file
reads, shared by every "raw" reader of the project so that one startup no
longer re-parses the same YAML/.kicad_sch from disk a dozen times.

Rationale (2026-08-15, see techdocs/handoff/plan_2026_08_15_config_read_
cache_startup.md): cProfile on a real GUI startup showed MainWindow()
construction spending 15.0s, of which yaml.safe_load() alone was 11.4s
across 113 calls — the SAME include: graph of ~10 YAML files walked and
parsed ~13 times and the same 12 *.kicad_sch files sexpdata-parsed 48 times,
from 4+ independent raw open()+parse call sites that shared no cache at all.
This is pure redundant I/O + CPU parsing (not something rewriting the GUI in
C++ would meaningfully fix — the same redundant code would still read the
disk 12-13 times, just somewhat faster per pass). The fix is a cache at the
single-file-read layer, not a language change.

Design notes:

- Keyed by (resolved path, mtime_ns): a changed mtime — typically an
  external hand-edit in another editor — is a cache miss on its own, so an
  external edit is picked up on the very next read with NO explicit
  invalidation call. Nothing here registers a filesystem watcher; the
  stat() per read is cheap (~microseconds) compared to the tens of ms a
  missed parse costs.
- Returns the SHARED cached object (no defensive copy) — callers must treat
  it as read-only; the write-path helpers in config_writer (and the two dock
  mutators in gui/docks/entity_delete.py + gui/docks/rename.py) deepcopy
  before mutating. On a MISS the freshly computed object is returned
  (isolated from the stored copy), so a miss caller may mutate freely.
- Guarded by a single module lock held for the whole check-or-populate body
  (including loader(path) itself), so two threads racing on the same file
  parse it once, not twice. CPython dicts are individually atomic under the
  GIL, so this is not a memory-safety issue — it just avoids throwing away
  real work, and the apply/extract CLI operations genuinely do run on a
  background thread in parallel with the GUI thread (gui/worker.py's
  start_long_op).
- Missing-file handling is deliberately NOT this function's job: if
  os.stat() fails, loader(path) is called directly (uncached) so it can
  raise/return whatever it already does for "file doesn't exist" (the real
  callers disagree on this: config/includes.py raises ValidationError,
  config_writer.py returns {}). This cache must not paper over that.
- mtime alone is NOT sufficient invalidation for OUR OWN writes: two
  physical writes to the same file microseconds apart (e.g. the delete-
  then-upsert shape in PlacerDock._do_save) can land on the same mtime_ns
  tick on a coarse-timer filesystem, which would make the second read a
  false cache HIT on the pre-second-write content. invalidate_path() closes
  that gap regardless of clock resolution and MUST be called by every
  writer right after the physical write (the project has exactly one write
  chokepoint: config_writer._write_data).
"""
import contextvars
import copy
import os
import threading
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

# The ConfigWorkingSet module, resolved lazily (see _working_set()) — file_cache
# must not import it at module level: config_working_set imports this module's
# invalidate_* at module level, so a top-level import here would be circular.
_CWS_MOD = None


def _working_set():
    """Lazily resolve the ConfigWorkingSet module (None before first use)."""
    global _CWS_MOD
    if _CWS_MOD is None:
        from .. import config_working_set
        _CWS_MOD = config_working_set
    return _CWS_MOD


_lock = threading.Lock()
_cache: dict[tuple[str, int], object] = {}
# resolved path -> every cache key currently stored for it (there can be more
# than one mtime_ns generation alive briefly under concurrent readers) — kept
# only so invalidate_path() can evict without scanning the whole cache.
_keys_by_path: dict[str, set[tuple[str, int]]] = {}

# ── Graph-level result cache (2026-08-21, the layer ABOVE cached_file_read,
#    see cached_graph_result below) ───────────────────────────────────────
# keyed by (kind, resolved_root_path) -> (shared result, {resolved file path:
# mtime_ns} of every file that computation actually read). The result is
# SHARED (no defensive copy) — callers must not mutate it (see
# cached_graph_result below; the GUI write-path docks copy first).
_graph_cache: dict[tuple[str, str], tuple[object, dict[str, int]]] = {}
# reverse index: resolved file path -> the (kind, root) keys whose known file
# set includes it — kept only so invalidate_graph_path() can evict without
# scanning the whole graph cache.
_graph_keys_by_path: dict[str, set[tuple[str, str]]] = {}
# Active per-computation trace: while a graph computation runs, every
# cached_file_read() records the (path, mtime_ns) it stat'ed into this dict.
# A ContextVar (not a plain global) so each thread — and each nested
# computation — gets its own, independently restorable trace.
_trace: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "file_cache_graph_trace", default=None)


def cached_file_read(path: Path, loader: Callable[[Path], T]) -> T:
    """Memoize loader(path), keyed by (resolved path, mtime_ns) — a changed
    mtime (typically an external hand-edit) is a cache miss on its own.
    Always returns a deep copy (hit or miss) so no caller can corrupt the
    cache — or another caller's view — by mutating what it got back.
    loader(path) must never return None for a successfully-read file (both
    current callers already guarantee this via their own `or {}` fallback).

    Missing-file handling is intentionally NOT this function's job — if
    os.stat() fails, loader(path) is called directly (uncached) so it can
    raise/return whatever it already does for "file doesn't exist" (the
    three real callers disagree on this: includes.py raises ValidationError,
    config_writer.py returns {}) — this cache must not paper over that.

    This function alone does NOT guarantee a write is always visible to the
    next read — see invalidate_path() below for why mtime alone isn't
    enough, and why every writer must call it.

    Returns the SHARED cached object (no defensive copy) — callers must not
    mutate it; the write-path mutators (config_writer's merge_write/
    add_list_entry/upsert_list_entry/add_include/disable_include and the
    entity_delete/rename dock helpers) deepcopy before mutating. The miss
    case returns the freshly computed object, isolated from the stored copy."""
    resolved = str(path.resolve())
    # Staged mode (GUI edit-in-memory, 2026-09-01, plan project_save_model): a
    # dirty file's content lives in the working set, not on disk — return it
    # before any stat/disk read. This also covers to-be-created (__new__) files
    # that don't exist on disk yet. The staged dict is shared working-set state,
    # so a deep copy is returned (the same read-only contract as the cache).
    cws = _working_set()
    if cws is not None and cws.WORKING_SET.enabled:
        staged = cws.WORKING_SET.staged_content(resolved)
        if staged is not None:
            return copy.deepcopy(staged)
    try:
        mtime_ns = os.stat(resolved).st_mtime_ns
    except OSError:
        return loader(path)
    # Report this (path, mtime) to the active graph-computation trace, if any —
    # this is what lets cached_graph_result() learn the FULL file set a
    # computation touched, for free (the stat() already happens here on every
    # read, cache hit or miss alike).
    trace = _trace.get()
    if trace is not None:
        trace[resolved] = mtime_ns
    key = (resolved, mtime_ns)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        result = loader(path)
        _cache[key] = copy.deepcopy(result)
        _keys_by_path.setdefault(resolved, set()).add(key)
        return result


def invalidate_path(path: Path) -> None:
    """Drop every cached generation of `path`. MUST be called by every
    writer, right after the physical write, in addition to (not instead of)
    the mtime-keyed cache above — see the plan's "Инвалидация" section for
    why: two writes to the same file microseconds apart (e.g. delete-then-
    upsert in one Save) can land on the same mtime_ns tick on some
    filesystems/OSes, which would make the second read a false cache HIT on
    the pre-second-write content. Explicit invalidation closes that gap
    regardless of clock resolution. Safe to call on a path that was never
    cached (no-op)."""
    resolved = str(path.resolve())
    with _lock:
        for key in _keys_by_path.pop(resolved, ()):
            _cache.pop(key, None)


def _graph_mtimes_unchanged(files: dict[str, int]) -> bool:
    """True when every stored (path, mtime_ns) still matches what's on disk —
    a handful of os.stat() calls (microseconds), the whole re-validation a
    graph-cache HIT performs. A missing file (deleted since the computation)
    is a miss, so the next full computation re-raises/re-discovers it the
    same way the uncached path would."""
    for path, mtime in files.items():
        try:
            if os.stat(path).st_mtime_ns != mtime:
                return False
        except OSError:
            return False
    return True


def _drop_graph_entry(key: tuple[str, str]) -> None:
    """Evict one graph-cache entry and remove `key` from every file's reverse
    index — keeps _graph_keys_by_path consistent with _graph_cache whether
    the eviction came from a stale re-check or from invalidate_graph_path()."""
    entry = _graph_cache.pop(key, None)
    if entry is None:
        return
    _, files = entry
    for path in files:
        keys = _graph_keys_by_path.get(path)
        if keys is not None:
            keys.discard(key)
            if not keys:
                _graph_keys_by_path.pop(path, None)


def cached_graph_result(kind: str, root_path: str, compute_fn: Callable[[], T]) -> T:
    """Memoize compute_fn() for ONE include: graph, keyed by (kind,
    resolved_root_path) — the layer ABOVE cached_file_read() (2026-08-21,
    plan_2026_08_21_startup_graph_level_cache.md). Once raw file reads got
    cheap (the single-file mtime cache), the cost left on GUI startup was the
    whole-graph TRAVERSAL/merge/validation being re-run 6-11 times per start
    even when no file changed — this caches the RESULT of one such
    computation, not just the bytes.

    compute_fn() is the full UNCACHED computation (load_config's body,
    walk_include_tree's body) and runs ONLY on a cache miss. While it runs,
    every cached_file_read() it triggers records the (path, mtime_ns) it
    stat'ed into an active trace (a ContextVar — no signature changes anywhere
    in the include recursion). On a later call with the same root_path the
    stored mtimes are re-checked with a handful of os.stat() calls; if they
    all match, the stored result is returned as a deep copy WITHOUT re-running
    any traversal/merge/validation. A changed mtime (an external edit) is a
    miss on its own, same philosophy as cached_file_read one layer down.

    Correctness for a TOPOLOGY change (an include: starting/stopping to point
    at a new file) needs no special logic: to change topology you must edit an
    already-known file (the one carrying the include: line), and that edit
    changes ITS mtime, which is already in the re-checked set.

    `kind` disambiguates different computations over the same root
    ("load_config" vs "walk_include_tree" — different result types).

    Returns the SHARED cached result with NO defensive copy (2026-08-25, a
    deliberate contract change from the old deepcopy-on-every-hit behavior):
    once the Config grew to thousands of records, deepcopying it on each of
    the ~6 load_config / ~11 walk_include_tree hits per GUI startup cost
    ~0.5s — the largest remaining startup cost. The result is therefore an
    immutable-by-convention snapshot: every caller must treat it as
    read-only, and the few GUI write-path docks that mutate a loaded Config
    (or RuntimeContext) must copy it first with dataclasses.replace().
    Missing-file handling is NOT this function's job, exactly like
    cached_file_read: if compute_fn() raises, the exception propagates and
    nothing is cached.
    """
    root = str(Path(root_path).resolve())
    key = (kind, root)
    with _lock:
        entry = _graph_cache.get(key)
        if entry is not None and _graph_mtimes_unchanged(entry[1]):
            return entry[0]
        if entry is not None:
            _drop_graph_entry(key)
    # Compute outside the lock: compute_fn() calls cached_file_read(), which
    # takes _lock itself — holding _lock here would deadlock.
    trace: dict[str, int] = {}
    token = _trace.set(trace)
    try:
        result = compute_fn()
    finally:
        _trace.reset(token)
    with _lock:
        _graph_cache[key] = (result, dict(trace))
        for path in trace:
            _graph_keys_by_path.setdefault(path, set()).add(key)
    return result


def invalidate_graph_path(path: Path) -> None:
    """Drop every graph-level cached result whose known file set includes
    `path` — the graph cache's counterpart to invalidate_path() for the
    single-file cache. MUST be called by config_writer._write_data right
    after the physical write, for the SAME delete-then-upsert race reason:
    two writes microseconds apart can land on the same mtime_ns tick on a
    coarse-timer filesystem, which the mtime re-check alone cannot see — and
    the graph cache could otherwise hand back a stale Config immediately
    after a Save. Safe to call on a path never part of any graph (no-op)."""
    resolved = str(path.resolve())
    with _lock:
        for key in _graph_keys_by_path.pop(resolved, ()):
            _drop_graph_entry(key)
