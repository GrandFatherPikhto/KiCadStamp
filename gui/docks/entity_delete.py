# gui/docks/entity_delete.py
"""
Delete support for ConfigTreeDock's context menu (2026-08-05, Denis: "Ещё
надо в контекстном меню... возможность удалять cell, export,
via_thermal_pad, rules и т.д. любую сущность. После удаления делаем backup
файл (где эта сущность хранилась)") — covers all 7 recognized sections,
same "single entry point ConfigTreeDock's context menu calls" shape as
gui/docks/rename.py's rename_entry().

Backup: an entity lives inside a shared, multi-entry YAML file, not its own
file — "back up the entity" means snapshot the WHOLE file it lives in,
before any write. Timestamped, not a single rolling .bak (Denis: a second
delete later in the same session must not clobber the first delete's
recovery point) — see backup_file(). Every file this module is about to
write gets backed up first, exactly once per delete_entry() call even when
cascade touches the same file twice (see delete_entry's _ensure_backup).

Cross-reference cascade: only cells: and points: are ever referenced BY
NAME from elsewhere in the graph (same CASCADE_FIELD table rename.py
already uses — cell: and anchor_point:). Unlike rename's cascade, which
REWRITES the reference to the new name, a delete's cascade REMOVES the
referencing entry entirely — Denis, 2026-08-05: "Предупреждать. Спросить,
удалить ли связанные ссылки? Если да, их тоже удалить." A spoke/
clone_placement/rule/point left pointing at a name that no longer exists
fails to load on the next `apply`/`extract` run anyway, so there is no
useful "leave it dangling" middle ground.

_prune_file_data() is a generic structural walk (matches any dict that
carries `field_name: target`, wherever it sits — a top-level list entry
like a ClonePlacement/Rule/ThermalViaArrayConfig, a nested list entry like
a Rule's own spokes: or a Cell's own clone_placements:, or a DICT-section
entry like a Point's own anchor_point: chaining to another point), same
"walk by key name, not by hardcoded field path" principle as rename.py's
_rename_field_recursive/rename_references — this is what reaches a nested
cell: reference inside another Cell's own clone_placements: for free,
without special-casing that shape here. find_references() runs the exact
same walk over an in-memory copy (no write) to build the confirmation
dialog's report; delete_entry() runs it for real.
"""
import copy
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ._common import read_data, write_data
from .rename import CASCADE_FIELD, DICT_SECTIONS, collect_graph_files, entry_effective_name


# Monotonic per-process counter appended to the timestamp — datetime.now()'s
# microsecond resolution can COLLIDE when two backups are created within the
# same microsecond (found live 2026-08-13 via the flaky
# test_backup_file_two_calls_produce_two_distinct_files): a colliding name
# would silently OVERWRITE the first backup instead of giving it its own
# recovery point, the very failure this timestamp scheme exists to prevent.
_backup_seq = 0


def backup_file(path: Path) -> Path:
    """Timestamped copy of `path` next to itself, e.g.
    'cells.yaml.bak.20260805_161500_123456' — never overwrites an earlier
    backup, see module docstring. Microsecond resolution (not just
    seconds) — a cascade delete can back up several files within the same
    second, and each must still get its own recovery point. A monotonic
    counter suffix guarantees uniqueness even when two calls land within
    the same microsecond. Returns the backup's path (used in the caller's
    summary message)."""
    global _backup_seq
    _backup_seq += 1
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = path.with_name(f"{path.name}.bak.{stamp}.{_backup_seq}")
    shutil.copy2(path, backup_path)
    return backup_path


def _entry_identity(section: str, entry: Dict[str, Any]) -> Any:
    """The identity used to match a list-section entry for removal — `name`,
    falling back per-section (rules: net, coordinate_placements: cluster/role)
    via rename.py's shared entry_effective_name (2026-08-12, Group 1:
    coordinate_placements became a normal named-records section, so a nameless
    entry must be deletable by its cluster/role display name too)."""
    return entry_effective_name(section, entry)


def _remove_entry(path: Path, section: str, name: str) -> bool:
    """Removes `name` from `section` in `path` — dict KEY for cells:/
    points:/extract_profiles:/clone_profiles:, matching-identity list item
    for clone_placements:/thermal_via_arrays:/rules:, same identity rule
    (name, falling back to net: for rules:) as rename.py's
    rename_list_entry(). Returns whether anything was actually removed."""
    data = read_data(path)
    if section in DICT_SECTIONS:
        section_dict = data.get(section) or {}
        if name not in section_dict:
            return False
        del section_dict[name]
    else:
        items = data.get(section) or []
        kept = [e for e in items if not (isinstance(e, dict) and _entry_identity(section, e) == name)]
        if len(kept) == len(items):
            return False
        data[section] = kept
    write_data(path, data)
    return True


def _prune_lists_recursive(node: Dict[str, Any], field_name: str, target: str,
                           on_match: Callable[[Dict[str, Any]], None]) -> bool:
    """Removes, from any list found anywhere inside `node` (recursively),
    dict items that directly carry `field_name: target` — covers every
    nested list shape (Rule.spokes, Cell.clone_placements, ...) without
    hardcoding a field path per shape, see module docstring."""
    changed = False
    for key, value in list(node.items()):
        if isinstance(value, list):
            kept = []
            for item in value:
                if isinstance(item, dict):
                    if item.get(field_name) == target:
                        on_match(item)
                        changed = True
                        continue
                    if _prune_lists_recursive(item, field_name, target, on_match):
                        changed = True
                kept.append(item)
            node[key] = kept
        elif isinstance(value, dict):
            if _prune_lists_recursive(value, field_name, target, on_match):
                changed = True
    return changed


def _prune_file_data(data: Dict[str, Any], field_name: str, target: str,
                     on_match: Callable[[Dict[str, Any]], None]) -> bool:
    """Removes, anywhere in `data` (mutated in place), every entry that
    directly carries `field_name == target`: a DICT-section entry (cells:/
    points:/...) removed by its key, any LIST entry (top-level or nested)
    removed from its list. `on_match` is called once per removed dict, so
    the same walk serves both find_references() (report-only, on a copy)
    and the real deletion."""
    changed = False
    for section in DICT_SECTIONS:
        entries = data.get(section)
        if not isinstance(entries, dict):
            continue
        for name in list(entries.keys()):
            entry = entries[name]
            if not isinstance(entry, dict):
                continue
            if entry.get(field_name) == target:
                # DICT-section entries (cells:/points:/...) carry their name
                # as the dict KEY, not a field inside the value — inject it
                # so _describe_entry() has something to show (e.g. a Point
                # chained via anchor_point: has no "name"/"net"/"pad" field
                # of its own).
                on_match(dict(entry, name=name))
                del entries[name]
                changed = True
                continue
            if _prune_lists_recursive(entry, field_name, target, on_match):
                changed = True
    for key, value in list(data.items()):
        if key in DICT_SECTIONS:
            continue
        if isinstance(value, list):
            kept = []
            for item in value:
                if isinstance(item, dict):
                    if item.get(field_name) == target:
                        on_match(item)
                        changed = True
                        continue
                    if _prune_lists_recursive(item, field_name, target, on_match):
                        changed = True
                kept.append(item)
            data[key] = kept
        elif isinstance(value, dict):
            if _prune_lists_recursive(value, field_name, target, on_match):
                changed = True
    return changed


def _describe_entry(entry: Dict[str, Any]) -> str:
    return entry.get("name") or entry.get("net") or entry.get("pad") or str(entry)[:60]


def find_references(files: List[Path], field_name: str, target: str) -> Dict[Path, List[str]]:
    """Dry-run report of every entry that delete_entry(..., cascade=True)
    would remove, without touching any file (operates on an in-memory deep
    copy) — ConfigTreeDock shows this in the confirmation dialog before
    asking whether to cascade at all."""
    report: Dict[Path, List[str]] = {}
    for path in files:
        data = copy.deepcopy(read_data(path))
        found: List[str] = []
        _prune_file_data(data, field_name, target, on_match=lambda e: found.append(_describe_entry(e)))
        if found:
            report[path] = found
    return report


def delete_entry(root_path: Optional[Path], entry_path: Path, section: str, name: str,
                 cascade: bool) -> Dict[str, List[Path]]:
    """Removes `name` from `section` in `entry_path`, backing up
    `entry_path` first. If `cascade` and `section` has a CASCADE_FIELD
    entry (cells:/points:), also removes every referencing entry anywhere
    in root_path's include: graph (see module docstring) — each file this
    touches is backed up before it's written, once per file even if the
    primary removal and the cascade both touch the same file.

    Callers should run find_references() first to decide whether to ask
    about cascade at all, and only pass cascade=True after the user agreed.

    Returns {"backups": [...], "cascade_files": [...]} for a summary
    message — entry_path's own backup is always first in "backups".
    """
    backed_up: List[Path] = []

    def _ensure_backup(path: Path) -> None:
        if path not in backed_up:
            backup_file(path)
            backed_up.append(path)

    _ensure_backup(entry_path)
    _remove_entry(entry_path, section, name)

    cascade_files: List[Path] = []
    field_name = CASCADE_FIELD.get(section)
    if cascade and field_name and root_path is not None:
        for path in collect_graph_files(root_path):
            data = read_data(path)
            if _prune_file_data(data, field_name, name, on_match=lambda e: None):
                _ensure_backup(path)
                write_data(path, data)
                cascade_files.append(path)

    return {"backups": backed_up, "cascade_files": cascade_files}
