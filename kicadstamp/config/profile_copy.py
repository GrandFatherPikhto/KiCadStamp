# kicadstamp/config/profile_copy.py
"""Copy Cell/Entity/Chain from another profile into the current one BY VALUE
(2026-08-31, plan_2026_08_31_copy_cell_entity_from_profile.md) — a fully
independent copy, unlike `include:` which keeps a live reference to the other
file. After the copy, edits in the source never affect the target.

Design (see techdocs/handoff/deepseek/design_2026_08_31_copy_cell_entity_
from_profile.md):
  * Reads the source RAW — `_load_config_file()` + `resolve_includes()`
    (kicadstamp/config/includes.py), NOT `load_config()`/dataclasses — so
    every field round-trips verbatim and the entry can live anywhere in the
    source's include: graph.
  * Closes DEPENDENCIES: a composite Cell references other Cells through
    `clone_placements[].cell` (Cell.clone_placements/CellPlacement in
    config/models.py); a Chain references Cells through each `spoke.cell`
    (ManualSpoke) and a `points:` entry through `anchor_point`. Copying one
    record without its closure would leave a broken reference in the target,
    so every transitive dependency is copied too (cycle-safe via a visited
    set). Points are part of the Chain closure because `anchor_point` is a
    config-graph reference (unlike nets, which are plain strings) — a Chain
    imported without its point would make the target UNLOADABLE (loader.py's
    _check_anchor_point is a load-time fatal).
  * COLLISION check is done for EVERY name in the closure (plus the record's
    own name) against the TARGET's whole include: graph BEFORE anything is
    written — the file is never left partially written, and a duplicate is a
    clear ValidationError, not a silent overwrite.
  * WRITE reuses the existing config_writer primitives: merge_write() for the
    dict sections (cells:/points:), upsert_list_entry() for the list sections
    (entities:/rules:) — after the collision pre-check those are pure appends.

Electrical fields (nets:/params:/net_overrides:/sheet:) are copied as-is,
without adaptation — the other board's nets almost certainly won't match the
current one, and fixing that is deliberately out of scope (the user re-tunes
it by hand after import).
"""
import copy
import json
from pathlib import Path
from typing import Any, Callable, Optional

from ..config_writer import merge_write, upsert_list_entry
from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _
from ..utils.file_cache import cached_file_read
from .includes import _load_config_file, resolve_includes, walk_include_tree

# Dict sections (keyed by name) vs list sections (each entry carries its own
# identity field) — same split as config/includes.py's _DICT_SECTIONS.
_DICT_SECTIONS = ("cells", "points")
# section -> identity of one list-section entry; rules: falls back to net: when
# name: is absent (config/models.py's rule_effective_name), the rest are name-only.
_LIST_IDENTITY = {
    "entities": lambda e: e.get("name"),
    "chains": lambda e: e.get("name") or e.get("net"),
}


def _load_source(path: Any) -> dict:
    """Source profile's flat, include-resolved raw dict. Every copy_* entry
    point reads the source this way (never through load_config/dataclasses) —
    see the module docstring. Clear ValidationError (not a bare OSError/
    JSONDecodeError) for a missing/unreadable source, so the GUI can show it
    as a normal message."""
    p = Path(path)
    if not p.exists():
        raise ValidationError(format_fatal_error(
            _("Import source {path!r} not found").format(path=str(p)),
            [_("pick an existing config file (.sexp or .json) to import from")]))
    try:
        return resolve_includes(str(p), _load_config_file(p))
    except ValidationError:
        raise
    except (OSError, json.JSONDecodeError) as e:
        raise ValidationError(format_fatal_error(
            _("Cannot read import source {path!r}: {error}").format(path=str(p), error=e),
            [_("check that the file is a valid .sexp or .json config")])) from e


def _collect_cell_closure(source_data: dict, root_cell: str) -> list:
    """Ordered list of every Cell name that must be copied to make `root_cell`
    load in the target: root first, then every Cell transitively referenced
    through `clone_placements[].cell` (composite/recursive Cells, see
    Cell.clone_placements in config/models.py). BFS over the raw dicts with a
    visited set — immune to diamonds and to a (defensively) cyclic cell graph.
    A referenced Cell missing from the source is a clear ValidationError (a
    broken source must not be copied into a broken target silently)."""
    cells = source_data.get("cells") or {}
    if root_cell not in cells:
        raise ValidationError(format_fatal_error(
            _("cell {name!r} not found in import source").format(name=root_cell),
            [_("known cells in the source: {names}").format(
                names=sorted(cells.keys()) if cells else _("(none)"))]))

    ordered: list = []
    visited: set = set()
    queue = [root_cell]
    while queue:
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        ordered.append(name)
        raw = cells.get(name)
        if not isinstance(raw, dict):
            continue
        for placement in (raw.get("clone_placements") or []):
            if not isinstance(placement, dict):
                continue
            dep = placement.get("cell")
            if not dep:
                continue
            if dep not in cells:
                raise ValidationError(format_fatal_error(
                    _("cell {dep!r} referenced by {name!r} is missing from the import source")
                    .format(dep=dep, name=name),
                    [_("a broken source profile cannot be imported — fix the source, "
                       "or import an ancestor cell whose closure is complete")]))
            if dep not in visited:
                queue.append(dep)
    return ordered


def _collect_point_closure(source_data: dict, root_point: str) -> list:
    """Ordered list of every `points:` name that must be copied to keep
    `root_point` loadable: root first, then every Point transitively chained
    through `anchor_point` (points chain to other points, see
    config/points.py). Needed for Chain import — a Chain's anchor_point is a
    config-graph reference, and loader.py's _check_anchor_point is a
    load-time fatal on a missing point. Cycle-safe BFS, same shape as
    _collect_cell_closure."""
    points = source_data.get("points") or {}
    if root_point not in points:
        raise ValidationError(format_fatal_error(
            _("point {name!r} not found in import source").format(name=root_point),
            [_("known points in the source: {names}").format(
                names=sorted(points.keys()) if points else _("(none)"))]))

    ordered: list = []
    visited: set = set()
    queue = [root_point]
    while queue:
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        ordered.append(name)
        raw = points.get(name)
        if not isinstance(raw, dict):
            continue
        dep = raw.get("anchor_point")
        if not dep:
            continue
        if dep not in points:
            raise ValidationError(format_fatal_error(
                _("point {dep!r} chained from {name!r} is missing from the import source")
                .format(dep=dep, name=name),
                [_("a broken source profile cannot be imported — fix the source first")]))
        if dep not in visited:
            queue.append(dep)
    return ordered


def _target_files(target_root: Path) -> list:
    """Every physical file in the target's include: graph (root first, then
    includes in walk order), deduped by resolved path — used by the collision
    check below. Empty list for a target root that does not exist yet (a
    brand-new profile has nothing to collide with)."""
    if not target_root.exists():
        return []
    node = walk_include_tree(str(target_root))
    seen: dict = {}

    def walk(n) -> None:
        seen.setdefault(n.path.resolve(), n.path)
        for child in n.children:
            walk(child)

    walk(node)
    return list(seen.values())


def _target_has_entry(target_root: Path, section: str, name: str) -> bool:
    """True if `name` already exists in `section` anywhere in the target's
    include: graph. Dict sections (cells:/points:) are keyed by name; list
    sections (entities:/rules:) match each entry by its identity field (name,
    or net: fallback for rules: — the SAME identity rule_effective_name uses,
    so an imported entry can never silently collide with an existing one)."""
    for path in _target_files(target_root):
        data = cached_file_read(path, _load_config_file)
        if section in _DICT_SECTIONS:
            if name in (data.get(section) or {}):
                return True
        else:
            identity = _LIST_IDENTITY[section]
            for entry in (data.get(section) or []):
                if isinstance(entry, dict) and identity(entry) == name:
                    return True
    return False


def _check_no_collision(target_root: Path, section: str, names, label: str) -> None:
    """First collision among `names` in `section` of the target's include
    graph -> clear ValidationError naming the colliding entry and its kind.
    Called for the WHOLE closure before any write, so a duplicate can never
    leave the target half-copied."""
    for name in names:
        if _target_has_entry(target_root, section, name):
            raise ValidationError(format_fatal_error(
                _("{label} {name!r} already exists in the target profile")
                .format(label=label, name=name),
                [_("an existing entry with this name is already reachable in the target's "
                   "include: graph — import was refused, nothing was written. Rename or "
                   "delete the existing entry, or pick a different source entry")]))


def _write_dict_section(target_path: Path, section: str, entries: dict) -> None:
    """One merge_write() per copied entry (config_writer's read-merge-write
    keeps every other key/file content untouched). Deep-copies each raw dict
    so a later write to the same source data can never share a mutated object
    with an already-persisted one."""
    for name, raw in entries.items():
        merge_write(target_path, {section: {name: copy.deepcopy(raw)}}, section=section)


def _append_list_entry(target_path: Path, section: str, entry: dict) -> None:
    """Append one list-section entry (entities:/rules:) via upsert_list_entry
    — after the collision pre-check the identity can never match an existing
    entry, so this is a pure append, reusing the tested config_writer
    primitive instead of a hand-rolled read-append-write."""
    upsert_list_entry(target_path, section, copy.deepcopy(entry),
                      key_fn=_LIST_IDENTITY[section])


# ── Public entry points ─────────────────────────────────────────────────────

def list_importable(source_path) -> list:
    """Every Cell/Entity/Chain in the source profile, as picker rows for the
    GUI import dialog: [{"kind": "cell"|"entity"|"chain", "name": ..., "info": ...}].
    `name` is what the matching copy_* function takes (dict key for cells,
    entity name, chain effective identity); `info` is a short recognizable
    descriptor (component/nested counts for a cell, cell: for an entity, spoke
    count for a rule). Raises the same clear ValidationError as copy_* on a
    missing/unreadable source."""
    source = _load_source(source_path)
    out: list = []

    for name, raw in (source.get("cells") or {}).items():
        if not isinstance(raw, dict):
            continue
        n_components = len(raw.get("components") or [])
        n_nested = len(raw.get("clone_placements") or [])
        bits = []
        if n_components:
            bits.append(_("{n} components").format(n=n_components))
        if n_nested:
            bits.append(_("{n} nested cells").format(n=n_nested))
        out.append({"kind": "cell", "name": name, "info": ", ".join(bits)})

    for entry in (source.get("entities") or []):
        if isinstance(entry, dict) and entry.get("name"):
            out.append({"kind": "entity", "name": entry["name"],
                        "info": _("cell {cell}").format(cell=entry.get("cell", ""))})

    for entry in (source.get("chains") or []):
        if not isinstance(entry, dict):
            continue
        ident = entry.get("name") or entry.get("net")
        if not ident:
            continue
        out.append({"kind": "chain", "name": ident,
                    "info": _("{n} spokes").format(n=len(entry.get("spokes") or []))})
    return out


def copy_items(source_path, items, target_path, target_root: Optional[Path] = None,
               on_collision: Optional[Callable[[dict], Optional[str]]] = None) -> dict:
    """Copy several Cell/Entity/Chain records from source_path into target_path
    by value in ONE atomic pass (2026-08-31, multi-select in the import
    dialog). `items` is a list of {"kind": "cell"|"entity"|"chain", "name":
    <identity>} rows — exactly what list_importable() produces. The dependency
    closures of ALL selected records are merged into ONE set and the collision
    check runs on the UNION before anything is written, so two records that
    share a Cell (e.g. an Entity and a Chain on the same cell) import cleanly
    instead of the second one tripping on the first one's just-written cell.

    Collision handling (Denis, 2026-08-31 — "перезаписать или оставить, как
    есть"):
      * `on_collision` is a callback receiving the FULL collision summary
        {"cells": [...], "points": [...], "entities": [...], "chains": [...]}
        (names that already exist in the target's include: graph) and returns:
          - "overwrite" -> every colliding name is replaced by the source
            version (merge_write replaces dict keys, upsert_list_entry replaces
            list entries in place);
          - "skip"      -> colliding names are LEFT as-is, everything else is
            still imported;
          - None        -> import cancelled (raises, nothing written).
      * when `on_collision` is None (the default — the single-record wrappers
        and CLI-style use), ANY collision is a clear ValidationError and
        nothing is written (the original fail-loud behaviour).

    Returns {"cells": [...], "points": [...], "entities": [...], "rules": [...]}
    — the names actually written per section (cells/points root-first; with
    "skip", colliding names are excluded). Raises a clear ValidationError on:
    missing/unreadable source, a missing record/name, a missing dependency in
    the source, or (default policy) ANY name collision in the target."""
    source = _load_source(source_path)
    cells_data = source.get("cells") or {}
    points_data = source.get("points") or {}
    entities = source.get("entities") or []
    chains = source.get("chains") or []

    cells_to_write: dict = {}
    points_to_write: dict = {}
    entities_to_write: list = []
    chains_to_write: list = []

    for item in items:
        kind = item.get("kind")
        name = item.get("name")
        if kind == "cell":
            for n in _collect_cell_closure(source, name):
                cells_to_write.setdefault(n, cells_data[n])
        elif kind == "entity":
            raw = next((e for e in entities
                        if isinstance(e, dict) and e.get("name") == name), None)
            if raw is None:
                raise ValidationError(format_fatal_error(
                    _("entity {name!r} not found in import source").format(name=name),
                    [_("known entities in the source: {names}").format(
                        names=sorted(e.get("name") for e in entities if isinstance(e, dict))
                        if entities else _("(none)"))]))
            if raw.get("cell"):
                for n in _collect_cell_closure(source, raw["cell"]):
                    cells_to_write.setdefault(n, cells_data[n])
            entities_to_write.append(raw)
        elif kind == "chain":
            raw = next((c for c in chains if isinstance(c, dict)
                        and ((c.get("name") or c.get("net")) == name)), None)
            if raw is None:
                raise ValidationError(format_fatal_error(
                    _("chain {name!r} not found in import source").format(name=name),
                    [_("known chains in the source: {names}").format(
                        names=sorted((c.get("name") or c.get("net"))
                                     for c in chains if isinstance(c, dict))
                        if chains else _("(none)"))]))
            for spoke in (raw.get("spokes") or []):
                if isinstance(spoke, dict) and spoke.get("cell"):
                    for n in _collect_cell_closure(source, spoke["cell"]):
                        cells_to_write.setdefault(n, cells_data[n])
            if raw.get("anchor_point"):
                for n in _collect_point_closure(source, raw["anchor_point"]):
                    points_to_write.setdefault(n, points_data[n])
            chains_to_write.append(raw)
        else:
            raise ValidationError(format_fatal_error(
                _("unknown import kind {kind!r}").format(kind=kind),
                [_("kind must be 'cell', 'entity' or 'chain'")]))

    root = Path(target_root) if target_root is not None else Path(target_path)
    collisions = {
        "cells": [n for n in cells_to_write if _target_has_entry(root, "cells", n)],
        "points": [n for n in points_to_write if _target_has_entry(root, "points", n)],
        "entities": [e["name"] for e in entities_to_write
                     if _target_has_entry(root, "entities", e["name"])],
        "chains": [(c.get("name") or c.get("net")) for c in chains_to_write
                   if _target_has_entry(root, "chains", c.get("name") or c.get("net"))],
    }
    if any(collisions.values()):
        if on_collision is None:
            # Default policy: fail loudly on the first collision, nothing written.
            section, name = next((s, names[0]) for s, names in collisions.items() if names)
            label = {"cells": _("cell"), "points": _("point"),
                     "entities": _("entity"), "chains": _("chain")}[section]
            _check_no_collision(root, section, [name], label)
        decision = on_collision(collisions)
        if decision is None:
            raise ValidationError(format_fatal_error(
                _("Import cancelled — existing entries were left unchanged"),
                [_("nothing was written")]))
        if decision == "skip":
            for n in collisions["cells"]:
                cells_to_write.pop(n, None)
            for n in collisions["points"]:
                points_to_write.pop(n, None)
            entities_to_write = [e for e in entities_to_write
                                 if e["name"] not in collisions["entities"]]
            chains_to_write = [c for c in chains_to_write
                               if (c.get("name") or c.get("net")) not in collisions["chains"]]
        elif decision != "overwrite":
            raise ValidationError(format_fatal_error(
                _("invalid collision decision {decision!r}").format(decision=decision),
                [_("expected 'overwrite', 'skip' or None (cancel)")]))
        # decision == "overwrite": write everything as-is — merge_write()
        # replaces dict-section keys and upsert_list_entry() replaces
        # list-section entries in place, so the source version wins.

    target = Path(target_path)
    _write_dict_section(target, "cells", cells_to_write)
    _write_dict_section(target, "points", points_to_write)
    for entry in entities_to_write:
        _append_list_entry(target, "entities", entry)
    for entry in chains_to_write:
        _append_list_entry(target, "chains", entry)

    return {
        "cells": list(cells_to_write),
        "points": list(points_to_write),
        "entities": [e["name"] for e in entities_to_write],
        "chains": [(c.get("name") or c.get("net")) for c in chains_to_write],
    }


def copy_cell(source_path, cell_name, target_path, target_root: Optional[Path] = None) -> list:
    """Copy one Cell (and its whole dependency closure) by value — a thin
    single-record wrapper over copy_items(). Returns the ordered list of
    copied Cell names (root first). Raises a clear ValidationError on:
    missing/unreadable source, missing Cell, a missing dependency in the
    source, or ANY name collision in the target (nothing is written)."""
    result = copy_items(source_path, [{"kind": "cell", "name": cell_name}],
                        target_path, target_root)
    return result["cells"]


def copy_entity(source_path, entity_name, target_path, target_root: Optional[Path] = None) -> list:
    """Copy one Entity (its raw dict verbatim — nets:/params:/net_overrides:/
    sheet: included as-is) plus the Cell closure behind entity.cell, by value —
    a thin single-record wrapper over copy_items(). Returns the ordered list
    of copied Cell names. Collision is refused before any write."""
    result = copy_items(source_path, [{"kind": "entity", "name": entity_name}],
                        target_path, target_root)
    return result["cells"]


def copy_chain(source_path, chain_identity, target_path, target_root: Optional[Path] = None) -> list:
    """Copy one Chain (matched by effective identity — name: or net:) plus its
    Cell closure (every spoke.cell, transitively) and its points closure
    (anchor_point, transitively), by value — a thin single-record wrapper over
    copy_items(). Electrical fields are copied verbatim. Returns the ordered
    list of copied dependency names (cells then points)."""
    result = copy_items(source_path, [{"kind": "chain", "name": chain_identity}],
                        target_path, target_root)
    return result["cells"] + result["points"]


# Backward-compat alias for the 2026-09-01 Rule -> Chain rename.
copy_rule = copy_chain
