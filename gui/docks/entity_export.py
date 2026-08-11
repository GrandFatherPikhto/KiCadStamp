# gui/docks/entity_export.py
"""
Export support for ConfigTreeDock's context menu (2026-08-05, Denis: "И ещё
возможность экспортировать сущность (выделенные сущности) в отдельный
файл"). Pure copy — the exported entries stay exactly where they were, a
new/existing file just also gets a copy of them (Denis: "Запись остаётся на
месте... Перенос пока не делаем").

Shallow: an exported entry is copied exactly as its own dict, no transitive
"also bring whatever it references" bundling (e.g. exporting a composite
Cell does not also copy the sub-cells its own clone_placements: point at) —
same deliberate scope limit as everywhere else in this GUI, can be revisited
if it turns out to be needed.

Two write modes, both operating on ExportItem tuples built by
ConfigTreeDock from the tree's current selection:
* merge (default) — read-merge-write into the target file via the SAME
  merge_write()/upsert_list_entry() primitives every other write path in
  gui/docks/ uses, so an existing target file's other content (its own
  cells:, include:, ...) is left untouched.
* overwrite (Denis: "галочку... смержить, перезаписать") — the target
  file's ENTIRE content is replaced with just the exported entries,
  requested explicitly as a checkbox/button choice in the export dialog,
  not the default.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._common import read_data, write_data, merge_write, upsert_list_entry
from .rename import DICT_SECTIONS


@dataclass
class ExportItem:
    """One selected tree leaf, as ConfigTreeDock builds it from the item's
    UserRole data — source_path is where the entry currently lives (read
    fresh here for DICT sections, since ConfigTreeDock's leaf payload for
    those is just the name, see config_tree.py's _entries())."""
    source_path: Path
    section: str
    name: str
    payload: Any  # the full dict already, for LIST sections; unused for DICT sections


def _rule_key(entry: Dict[str, Any]) -> Any:
    return entry.get("name") or entry.get("net")


def _resolve(item: ExportItem) -> Optional[Any]:
    """The entry's own dict, freshly read for DICT sections (cells:/
    points:/extract_profiles:/clone_profiles:), or the already-full payload
    for LIST sections (clone_placements:/thermal_via_arrays:/rules:)."""
    if item.section in DICT_SECTIONS:
        return (read_data(item.source_path).get(item.section) or {}).get(item.name)
    return item.payload


def export_entries(target_path: Path, items: List[ExportItem], overwrite: bool) -> None:
    """Writes every entry in `items` into target_path — see module
    docstring for merge vs. overwrite. Entries whose source no longer has
    them (a DICT-section entry deleted between selecting and exporting) are
    silently skipped rather than raising, same "the tree can be stale"
    tolerance as the rest of ConfigTreeDock's click routing."""
    resolved = [(item.section, item.name, entry)
               for item in items for entry in [_resolve(item)] if entry is not None]

    if overwrite:
        combined: Dict[str, Any] = {}
        for section, name, entry in resolved:
            if section in DICT_SECTIONS:
                combined.setdefault(section, {})[name] = entry
            else:
                combined.setdefault(section, []).append(entry)
        write_data(target_path, combined)
        return

    for section, name, entry in resolved:
        if section in DICT_SECTIONS:
            merge_write(target_path, {section: {name: entry}}, section=section)
        else:
            key_fn = _rule_key if section == "rules" else None
            upsert_list_entry(target_path, section, entry, key_fn=key_fn)
