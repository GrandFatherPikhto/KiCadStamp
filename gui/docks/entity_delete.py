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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# backup_file is RE-EXPORTED (2026-09-11, task В.1.3 of
# plan_2026_09_11_tree_instances_and_converter_safety): the single
# implementation moved to kicadstamp/utils/safe_write.py, so the CLI-side
# writers (the tree converter, flatten) do not have to reach into gui/ for it.
# Every existing `from gui.docks.entity_delete import backup_file` keeps working
# unchanged.
from kicadstamp.utils.safe_write import backup_file

from ._common import read_data, write_data
from .rename import CASCADE_FIELD, DICT_SECTIONS, collect_graph_files, entry_effective_name


def _entry_identity(section: str, entry: Dict[str, Any]) -> Any:
    """The identity used to match a list-section entry for removal — `name`,
    falling back per-section (rules: net, coordinate_placements: cluster/role)
    via rename.py's shared entry_effective_name (2026-08-12, Group 1:
    coordinate_placements became a normal named-records section, so a nameless
    entry must be deletable by its cluster/role display name too)."""
    return entry_effective_name(section, entry)


def _remove_entry_from_data(data: Dict[str, Any], section: str, name: str) -> bool:
    """Removes `name` from `section` in `data` (mutated in place) — dict KEY
    for cells:/points:/extract_profiles:/clone_profiles:, matching-identity
    list item for clone_placements:/thermal_via_arrays:/rules:, same identity
    rule (name, falling back to net: for rules:) as rename.py's
    rename_list_entry(). Returns whether anything was actually removed.

    The IN-MEMORY half of the old `_remove_entry()`: delete_entry() now builds
    every touched file's END STATE in memory first and writes LAST (a format-3
    writer validates the whole file it is handed, so a partial write — the
    record removed while a reference to it still stands — is refused), which
    means the removal must be expressible without touching disk."""
    if section in DICT_SECTIONS:
        section_dict = data.get(section) or {}
        if name not in section_dict:
            return False
        del section_dict[name]
        return True
    items = data.get(section) or []
    kept = [e for e in items if not (isinstance(e, dict) and _entry_identity(section, e) == name)]
    if len(kept) == len(items):
        return False
    data[section] = kept
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
    in root_path's include: graph (see module docstring).

    Every touched file's END STATE is built in memory FIRST and written LAST
    (2026-10-04, У3.5 delete-cascade finding): a format-3 writer validates the
    whole dict it is handed, so writing the primary removal before the
    references are pruned is refused when a reference to the record lives IN
    THE SAME FILE (“cannot write — N reference(s) point at a uuid that is
    missing”). Each file is written EXACTLY ONCE, and the write ORDER is
    fixed: the REFERENCING files first, the deleted record's OWN file LAST —
    so the on-disk graph is legal at every intermediate step, not only at the
    end. Each written file is backed up first, once per file.

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

    # entry_path's own backup is unconditional and always first (the documented
    # "backups" contract): the file is snapshotted before any write, even when
    # the removal turns out to find nothing.
    _ensure_backup(entry_path)

    field_name = CASCADE_FIELD.get(section)
    do_cascade = bool(cascade and field_name and root_path is not None)

    # ── 1/2. Build every touched file's END STATE in memory; write nothing. ──
    plans: Dict[Path, Dict[str, Any]] = {}
    changed: Dict[Path, bool] = {}

    def _plan(path: Path) -> Dict[str, Any]:
        if path not in plans:
            plans[path] = copy.deepcopy(read_data(path))
            changed[path] = False
        return plans[path]

    # Primary removal (entry_path's file).
    if _remove_entry_from_data(_plan(entry_path), section, name):
        changed[entry_path] = True

    # Cascade: prune every referencing entry anywhere in the graph. This runs
    # over entry_path TOO — the record may be referenced from its OWN file, the
    # exact case the old write-order could not survive.
    cascade_files: List[Path] = []
    if do_cascade:
        for path in collect_graph_files(root_path):
            if _prune_file_data(_plan(path), field_name, name, on_match=lambda e: None):
                changed[path] = True
                cascade_files.append(path)

    # ── 3. Write: referencing files first, the deleted record's file LAST. ──
    order = [p for p in cascade_files if p != entry_path]
    order.append(entry_path)
    for path in order:
        if changed.get(path):
            _ensure_backup(path)
            write_data(path, plans[path])

    return {"backups": backed_up, "cascade_files": cascade_files}
