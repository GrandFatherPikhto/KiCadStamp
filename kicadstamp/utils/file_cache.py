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
- Always returns a deep copy, on hit AND miss, so no caller can corrupt the
  cache — or another caller's view — by mutating what it got back. Auditing
  every downstream mutator instead (the callers build shallow copies of
  top-level dicts that still alias nested objects) is fragile and expensive;
  deepcopy of a config file is microseconds, negligible next to the parse
  it replaces.
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
import copy
import os
import threading
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_cache: dict[tuple[str, int], object] = {}
# resolved path -> every cache key currently stored for it (there can be more
# than one mtime_ns generation alive briefly under concurrent readers) — kept
# only so invalidate_path() can evict without scanning the whole cache.
_keys_by_path: dict[str, set[tuple[str, int]]] = {}


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
    enough, and why every writer must call it."""
    resolved = str(path.resolve())
    try:
        mtime_ns = os.stat(resolved).st_mtime_ns
    except OSError:
        return loader(path)
    key = (resolved, mtime_ns)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            return copy.deepcopy(cached)
        result = loader(path)
        _cache[key] = copy.deepcopy(result)
        _keys_by_path.setdefault(resolved, set()).add(key)
        return copy.deepcopy(result)


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
