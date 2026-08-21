# kicadstamp/config_writer.py
"""Pure read-merge-write config helpers for the GUI docks' write paths —
moved out of gui/docks/_common.py (Phase 2 of the gui god-file decomposition,
see techdocs/handoff/handoff_2026_08_05_architecture_fixes_roadmap.md):
these are plain file operations with no Qt dependency, so they belong in
core. gui/docks/_common.py is now a thin facade re-exporting them, so every
existing importer keeps working unchanged.

The read here deliberately does NOT swallow exceptions (unlike
gui/yaml_io.load_data, which is for read-only browsing) — these helpers are
on the docks' WRITE path, where a broken file must surface as an OSError the
caller turns into an on-screen error message.
"""
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml

from kicadstamp.i18n import _
from kicadstamp.utils.file_cache import cached_file_read, invalidate_graph_path, invalidate_path

logger = logging.getLogger(__name__)

# Project root — used by display_path() below to show paths relative to it
# when possible. Used to also be where FilePickerDock's file-tree was
# rooted (gui/docks/file_picker.py, removed 2026-08-03 — see
# handoff_2026_08_03_gui_tree_risks_resolved.md — replaced by ConfigTreeDock's
# "Open Root file" action, a plain QFileDialog with no directory browser).
# NOTE: parents[1] here (not parents[2] like the old gui/docks/_common.py) —
# this file lives one level deeper: kicadstamp/config_writer.py.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_data(path: Path) -> dict:
    """Read an existing config file's YAML/JSON content (or {} when it
    doesn't exist yet). Raises OSError on read/parse errors instead of
    returning {} — the merge-write helpers are on the docks' write path,
    where a broken file must surface to the user, not be silently treated
    as empty (unlike gui/yaml_io.load_data). FIXED (2026-08-04): a malformed
    file used to raise the raw yaml.YAMLError/json.JSONDecodeError instead
    — neither is an OSError, so every caller's `except OSError` (e.g.
    PlacerDock._do_save's, written against exactly this docstring's
    promise) missed it, and the raw exception propagated uncaught out of a
    Qt slot, which PyQt6 aborts the whole process on by default. Found
    live: Placer's Save crashed the entire GUI over one stray character in
    an unrelated part of the target YAML file.

    The read+parse itself goes through cached_file_read (2026-08-15, see
    kicadstamp/utils/file_cache.py) so the docks' repeated read-merge-write
    cycles on the same file — and the collectors that read every graph file
    once per dock — parse it from disk ONCE, not once per call. Contract is
    UNCHANGED: {} for a missing file, OSError — never ValidationError — on
    a malformed file, YAML/JSON by extension. Missing-file is handled here,
    before the cache, so a file that doesn't exist yet is never cached as
    "absent forever" and appears on the next call once it's created."""
    if not path.exists():
        return {}

    def _uncached_read(p: Path) -> dict:
        with open(p, "r", encoding="utf-8") as f:
            try:
                return (json.load(f) if p.suffix.lower() == ".json"
                        else yaml.safe_load(f)) or {}
            except (json.JSONDecodeError, yaml.YAMLError) as e:
                raise OSError(_("{path} is not valid {kind}: {error}").format(
                    path=path, kind="JSON" if p.suffix.lower() == ".json" else "YAML",
                    error=e)) from e

    return cached_file_read(path, _uncached_read)


def _write_data(path: Path, data: dict) -> None:
    """Write merged content back in the same format (YAML/JSON by file
    extension) it was read in. Every GUI dock write path
    (merge_write/add_list_entry/upsert_*/_remove_entry) funnels through
    this ONE physical-write chokepoint, which is why invalidate_path() AND
    invalidate_graph_path() live here and nowhere else: mtime alone can't
    tell two writes to the same file microseconds apart apart on a
    coarse-timer filesystem (the delete-then-upsert shape of
    PlacerDock._do_save), so BOTH cache layers are explicitly dropped for
    this path right after the write — the single-file cache and the
    graph-level result cache (see kicadstamp/utils/file_cache.py's
    invalidate_path()/invalidate_graph_path() docstrings)."""
    with open(path, "w", encoding="utf-8") as f:
        if path.suffix.lower() == ".json":
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=False)
        else:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    invalidate_path(path)
    invalidate_graph_path(path)


# Public aliases — these two are consumed across the gui/ package boundary
# (gui/docks/_common.py re-exports them to every dock), and the documented
# rule is "gui must not import the private names" (config/__init__.py). The
# underscore-prefixed originals stay for the internal callers in this file
# (merge_write/add_list_entry/...); the public name is what crosses packages.
read_data = _read_data
write_data = _write_data


def merge_write(path: Path, new_data: dict, section: Optional[str] = None) -> bool:
    """Same read-merge-write shape as kicadstamp_cli.py's cmd_extract:
    existing content in the target file is kept, only what's in new_data
    is added/replaced — a target file is routinely home to several
    cells/profiles accumulated over time, not exclusively owned by this
    one write.

    section=None: new_data is merged directly at the file's top level.
    section='cells'/'extract_profiles'/etc.: new_data is
    {section: {key: {...}}} — only that one nested dict gets merged,
    every OTHER top-level key already in the file (clone_placements:,
    include:, ...) is left untouched.
    Returns whether the specific key being written already existed.
    """
    existing = _read_data(path)
    if section is None:
        key = next(iter(new_data))
        overwritten = key in existing
        existing.update(new_data)
    else:
        new_section = new_data[section]
        key = next(iter(new_section))
        target_section = existing.setdefault(section, {})
        overwritten = key in target_section
        target_section.update(new_section)
    _write_data(path, existing)
    return overwritten


def add_list_entry(path: Path, section: str, entry: str) -> bool:
    """Appends `entry` (a path string, relative to `path`'s own
    directory — the same resolution rule config/includes.py uses for
    include: itself) to that list section in `path`, unless an entry
    already there resolves to the same file. Read-merge-write like
    merge_write(), but for a list section (include:) instead of a dict
    one — every other key in the file is left untouched. Returns whether
    an entry was actually added."""
    existing = _read_data(path)
    items = existing.setdefault(section, [])
    if not isinstance(items, list):
        raise OSError(_("{section}: in {path} is not a list — refusing to touch it")
                      .format(section=section, path=path))
    base_dir = path.parent
    target = (base_dir / entry).resolve()
    for existing_entry in items:
        existing_str = existing_entry if isinstance(existing_entry, str) \
            else (existing_entry or {}).get('path')
        if existing_str and (base_dir / existing_str).resolve() == target:
            return False
    items.append(entry)
    _write_data(path, existing)
    return True


def upsert_list_entry(path: Path, section: str, entry: Dict[str, Any], key: str = "name",
                      key_fn: Optional[Callable[[Dict[str, Any]], Any]] = None) -> bool:
    """Read-merge-write like merge_write()/add_list_entry(), but for a list
    section whose entries are dicts matched by identity, not by list
    membership: an entry whose identity already exists gets REPLACED in
    place (same position), a new one gets appended. Every other top-level
    key in the file (cells:, include:, extract_profiles:, ...) is left
    untouched. Shared shape for clone_placements: (see
    upsert_clone_placement), thermal_via_arrays: (ConfigTreeDock's Add
    thermal via pad, 2026-08-03), and rules: (gui/docks/rules.py, 2026-08-05)
    — all three are "list of dict entries" sections in exactly this way.

    key_fn (callable, entry -> identity) overrides the default `entry.get(key)`
    — rules: needs this because a Rule's identity for --only falls back to
    net: when name: is absent (config/models.py's rule_effective_name()),
    unlike clone_placements:/thermal_via_arrays: which always require an
    explicit name:."""
    identity = key_fn if key_fn is not None else (lambda e: e.get(key))
    existing = _read_data(path)
    items = existing.setdefault(section, [])
    if not isinstance(items, list):
        raise OSError(_("{section}: in {path} is not a list — refusing to touch it")
                      .format(section=section, path=path))
    overwritten = False
    for i, existing_entry in enumerate(items):
        if isinstance(existing_entry, dict) and identity(existing_entry) == identity(entry):
            items[i] = entry
            overwritten = True
            break
    if not overwritten:
        items.append(entry)
    _write_data(path, existing)
    return overwritten


def upsert_clone_placement(path: Path, entry: Dict[str, Any]) -> bool:
    """clone_placements:-specific name kept for the existing call sites/
    tests — see upsert_list_entry, the general form this now delegates to.
    Identity is placer_name if set, else name (the Cluster tag) — split
    2026-08-15 so changing which Cluster an already-saved entry tags no
    longer creates a duplicate on save."""
    return upsert_list_entry(path, "clone_placements", entry,
                             key_fn=lambda e: e.get("placer_name") or e.get("name"))


def _include_entry_target(entry: Any, base_dir: Path) -> Optional[Path]:
    """Resolved path an include: entry (string or {path:, enabled:} dict)
    points at, or None for a malformed entry — shared by add_include()/
    disable_include() for matching an existing entry against a target
    file, same resolution rule config/includes.py's _parse_include_entry
    uses."""
    entry_str = entry if isinstance(entry, str) else (entry or {}).get("path")
    return (base_dir / entry_str).resolve() if entry_str else None


def add_include(path: Path, entry: str) -> bool:
    """Like add_list_entry(path, "include", entry), but if an entry already
    there resolves to the same file and is currently disabled
    (enabled: false), RE-ENABLES it instead of adding a duplicate line —
    ConfigTreeDock's Add-file action (2026-08-03) re-including a file
    previously removed via disable_include() below should undo that, not
    pile up a second entry for the same path. Returns whether anything
    changed (added or re-enabled)."""
    existing = _read_data(path)
    items = existing.setdefault("include", [])
    if not isinstance(items, list):
        raise OSError(_("include: in {path} is not a list — refusing to touch it").format(path=path))
    base_dir = path.parent
    target = (base_dir / entry).resolve()
    for i, existing_entry in enumerate(items):
        if _include_entry_target(existing_entry, base_dir) != target:
            continue
        if isinstance(existing_entry, dict) and existing_entry.get("enabled") is False:
            items[i] = existing_entry["path"]  # re-enabled == plain string form again
            _write_data(path, existing)
            return True
        return False  # already there and already enabled
    items.append(entry)
    _write_data(path, existing)
    return True


def disable_include(path: Path, target: Path) -> bool:
    """Soft-removes an include: entry pointing at `target` — sets
    enabled: false rather than erasing the line (ConfigTreeDock's Remove-
    file action, 2026-08-03: "стирать файл — это экстремизм", toggle-able
    back via add_include() above, not a destructive delete). Converts a
    plain string entry into the {path:, enabled: false} mapping form
    add_include()/config/includes.py's _parse_include_entry already
    understand. Returns whether an entry was found and changed."""
    existing = _read_data(path)
    items = existing.get("include") or []
    base_dir = path.parent
    for i, existing_entry in enumerate(items):
        if _include_entry_target(existing_entry, base_dir) != target.resolve():
            continue
        if isinstance(existing_entry, dict) and existing_entry.get("enabled") is False:
            return False  # already disabled
        entry_str = existing_entry if isinstance(existing_entry, str) else existing_entry["path"]
        items[i] = {"path": entry_str, "enabled": False}
        _write_data(path, existing)
        return True
    return False


# include: only ever merges these top-level keys from an included file
# (config/includes.py's _LIST_SECTIONS/_DICT_SECTIONS) — everything else is
# fatal there (no defined multi-file merge behaviour). A file assigned a
# GUI role (or added as an include: target) can perfectly well ALSO be a
# full root config in its own right (registry_path/schematic_dir/...) if
# it was set up that way before — found live 2026-08-01: writing include:
# blindly in that case leaves the including file unloadable next time
# anything reads it.
INCLUDABLE_KEYS = frozenset(
    {"rules", "clone_placements", "cells", "points", "extract_profiles", "clone_profiles", "include"})


def _load_data_tolerant(path: Path) -> dict:
    """Tolerant read for non_includable_keys() below — mirrors
    gui/yaml_io.load_data (missing/malformed file -> {}), but lives in core
    because kicadstamp must never import from gui/."""
    if path is None or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return (json.load(f) if path.suffix.lower() == ".json" else yaml.safe_load(f)) or {}
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return {}


def non_includable_keys(path: Path) -> set:
    """Top-level keys in `path` that include: can't merge — see
    INCLUDABLE_KEYS. Shared by ExtractDock (after a successful extract,
    wiring the Placer file's include:) and ConfigTreeDock's Add-file
    action (2026-08-03, must not offer to include a file that would make
    the including file unloadable)."""
    return set(_load_data_tolerant(path).keys()) - INCLUDABLE_KEYS


def display_path(path: Path) -> str:
    """Path shown in labels: relative to PROJECT_ROOT when possible (the
    Files dock's tree is rooted there), absolute otherwise (a file
    outside that tree)."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)
