# gui/docks/entity_index.py
"""The ONE index "which entities sit on this cell/imprint" and "what places
this cell WITHOUT an entity", built in ONE walk over the include: graph.

Why a raw-graph index and not the loaded Config
-----------------------------------------------
`entities:` are shown in the Config tree as children of the cell/imprint
record they reference (plan_2026_10_05_entities_under_cells, Р63). The
reference is by UUID — `entity.cell_uuid` / `entity.imprint_uuid` against the
target record's `uuid` (format 3; format 2 files are lifted to 3 by the reader
BEFORE this index sees them, so the uuids are always present — measured
08.10.2026, see diagnostics/probe_entity_index_lifting.py). The name hint
(`cell:` / `imprint:`) is NEVER used to match.

The tree must keep working when the graph has a DANGLING reference — an entity
whose target is nowhere in the graph is exactly the orphan the plan shows with
a hint and lets the user re-point (3а). `load_config()` REFUSES such a graph
(that refusal is the whole point of format 3), so the index cannot be built
from a loaded Config the way `selection_narrowing.cell_clusters` is; it walks
the RAW per-file dicts `walk_include_tree()` hands back, which never merge and
never validate.

Qt-free on purpose (the rule for this module): it is pure graph logic, so its
unit guards call it DIRECTLY, with no dock and no QApplication.

The "who places it" side (3б) finds a cell used by a chain spoke, a
`clone_placements:` record or a nested `CellPlacement` — each by `cell_uuid`,
the same UUID rule as the entity side. It only REPORTS; nothing is migrated
here (the plan keeps the today behaviour for a placed-but-entityless cell).
"""
from __future__ import annotations

import dataclasses
from collections import defaultdict
from pathlib import Path
from typing import Iterator, Optional

from kicadstamp.config.includes import IncludeTreeNode

__all__ = [
    "PLACED_BY_SPOKE", "PLACED_BY_CLONE", "PLACED_BY_NESTED",
    "RecordRef", "EntityRef", "PlacedBy", "EntityIndex", "build_entity_index",
    "file_parent_map",
]

# The WHAT token of a "placed by" note (3б). The human sentence is built where
# it is shown (config_tree), so this module stays i18n-free.
PLACED_BY_SPOKE = "spoke"
PLACED_BY_CLONE = "clone_placement"
PLACED_BY_NESTED = "nested_cell"


@dataclasses.dataclass(frozen=True)
class RecordRef:
    """One `cells:` / `imprints:` record as the RAW graph holds it."""
    name: str
    uuid: Optional[str]
    file_path: Path


@dataclasses.dataclass(frozen=True)
class EntityRef:
    """One `entities:` record plus WHERE it is declared — the entity's own
    file, which is NOT necessarily the file of the cell it ends up under."""
    data: dict
    file_path: Path
    cell_uuid: Optional[str]
    imprint_uuid: Optional[str]

    @property
    def name(self) -> Optional[str]:
        return self.data.get("name")


@dataclasses.dataclass(frozen=True)
class PlacedBy:
    """One record that places a cell WITHOUT an entity (3б).

    `owner` is the raw owner record; its display name is resolved by the
    caller through the ONE effective-name rule (`entry_effective_name`), which
    lives next to the tree — this module never copies it. `owner_name` is set
    only for a nested `CellPlacement`, whose owner is the enclosing CELL and
    whose name is the cell's dict KEY, not a field of `owner`."""
    kind: str
    section: str
    owner: dict
    owner_name: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class EntityIndex:
    """The whole-graph index built by :func:`build_entity_index`."""
    cells_by_uuid: dict
    imprints_by_uuid: dict
    cells_by_name: dict
    imprints_by_name: dict
    entities_by_cell: dict
    entities_by_imprint: dict
    placed_by: dict
    orphans: tuple

    def entities_for_cell(self, cell_uuid: Optional[str]) -> tuple:
        return self.entities_by_cell.get(cell_uuid, ())

    def entities_for_imprint(self, imprint_uuid: Optional[str]) -> tuple:
        return self.entities_by_imprint.get(imprint_uuid, ())

    def has_entity_for_cell(self, cell_uuid: Optional[str]) -> bool:
        return bool(self.entities_for_cell(cell_uuid))

    def placed_by_cell(self, cell_uuid: Optional[str]) -> tuple:
        return self.placed_by.get(cell_uuid, ())

    def names_for(self, section: str) -> list:
        """Sorted record names of `cells` / `imprints` — the "Point to …"
        picker (3а) and the close-name hint's candidate list."""
        table = self.cells_by_name if section == "cells" else self.imprints_by_name
        return sorted(table)

    def target_uuid(self, section: str, name: str) -> Optional[str]:
        """The uuid of the named `cells:` / `imprints:` record, from the RAW
        graph — the write target of "Point to cell…" (3а). None when absent."""
        table = self.cells_by_name if section == "cells" else self.imprints_by_name
        ref = table.get(name)
        return ref.uuid if ref is not None else None

    def entity_named(self, name) -> Optional["EntityRef"]:
        """The CELL entity with that NAME, from the same raw graph.

        CELL entities only, deliberately: the doors of 2б, п.4 are the board
        items of a CELL's entity leaf, every one of them carries that entity's
        cell (`entity.get("cell")`), and the item opens a CELL page — an
        imprint-linked entity has no such page, so looking through the imprint
        table here would be a promise nothing keeps (the gap C4 of the 2б
        acceptance named.)

        An entity NAME is unique across the WHOLE include graph (that is what
        `gui/docks/rename.py`'s collect_entities enforces), so ONE lookup by name
        serves every door — which is why the door can carry the name in a signal
        and get the record (and its cluster/sheet/refs) back here, through the
        SAME part-1 index the tree draws entities with. None when the name is
        gone (an entity renamed or removed since the tree was drawn): the caller
        then refuses to guess."""
        if not name:
            return None
        for refs in self.entities_by_cell.values():
            for ref in refs:
                if ref.name == name:
                    return ref
        return None


def _iter_nodes(root: IncludeTreeNode) -> Iterator[IncludeTreeNode]:
    """Every file node of the include: graph, root first, then children
    depth-first — ONE traversal, no merge, and each FILE once.

    A diamond (one file reached through two branches) is where this matters:
    `walk_include_tree` builds a SEPARATE node object per entry, so without the
    by-path skip the file's records would be collected once per entry — the
    entity shown twice under its cell and one "placed by" per entry (доделка 1а,
    п.1). Cells and imprints only LOOKED deduped: their tables are built with
    `setdefault`, which is no defence for a list like `entities`."""
    seen: set = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.path in seen:
            continue
        seen.add(node.path)
        yield node
        stack.extend(reversed(node.children))


def build_entity_index(root: IncludeTreeNode) -> EntityIndex:
    """Build the index from an :class:`IncludeTreeNode` in ONE pass.

    Collect every cells:/imprints:/entities: record and every "placer" of a
    cell, then resolve the entity→target links by UUID. A link whose UUID is
    unknown anywhere in the graph leaves the entity in `orphans` (3а); an
    entity with no reference at all is an orphan too."""
    cells: list = []
    imprints: list = []
    entities: list = []
    placed: dict = defaultdict(list)

    for node in _iter_nodes(root):
        sections = node.sections

        for name, rec in (sections.get("cells") or {}).items():
            if not isinstance(rec, dict):
                continue
            cell_name = str(name)
            cells.append(RecordRef(cell_name, rec.get("uuid"), node.path))
            for nested in rec.get("clone_placements") or []:
                if isinstance(nested, dict) and nested.get("cell") is not None:
                    _add_placed(placed, nested.get("cell_uuid"),
                                PLACED_BY_NESTED, "cells", nested, cell_name)

        for rec in (sections.get("imprints") or []):
            if isinstance(rec, dict) and rec.get("name"):
                imprints.append(RecordRef(str(rec["name"]), rec.get("uuid"),
                                          node.path))

        for rec in (sections.get("entities") or []):
            if isinstance(rec, dict):
                entities.append(EntityRef(rec, node.path,
                                          rec.get("cell_uuid"),
                                          rec.get("imprint_uuid")))

        for chain in (sections.get("chains") or []):
            if not isinstance(chain, dict):
                continue
            for spoke in chain.get("spokes") or []:
                if isinstance(spoke, dict) and spoke.get("cell") is not None:
                    _add_placed(placed, spoke.get("cell_uuid"),
                                PLACED_BY_SPOKE, "chains", chain)

        for cp in (sections.get("clone_placements") or []):
            if isinstance(cp, dict) and cp.get("cell") is not None:
                _add_placed(placed, cp.get("cell_uuid"),
                            PLACED_BY_CLONE, "clone_placements", cp)

    cells_by_uuid: dict = {}
    cells_by_name: dict = {}
    for ref in cells:
        if ref.uuid:
            cells_by_uuid.setdefault(ref.uuid, ref)
        cells_by_name.setdefault(ref.name, ref)
    imprints_by_uuid: dict = {}
    imprints_by_name: dict = {}
    for ref in imprints:
        if ref.uuid:
            imprints_by_uuid.setdefault(ref.uuid, ref)
        imprints_by_name.setdefault(ref.name, ref)

    entities_by_cell: dict = defaultdict(list)
    entities_by_imprint: dict = defaultdict(list)
    orphans: list = []
    for ref in entities:
        if ref.cell_uuid and ref.cell_uuid in cells_by_uuid:
            entities_by_cell[ref.cell_uuid].append(ref)
        elif ref.imprint_uuid and ref.imprint_uuid in imprints_by_uuid:
            entities_by_imprint[ref.imprint_uuid].append(ref)
        else:
            orphans.append(ref)

    def _sorted(mapping: dict) -> dict:
        return {key: tuple(sorted(items, key=lambda r: str(r.name or "")))
                for key, items in mapping.items()}

    return EntityIndex(
        cells_by_uuid=cells_by_uuid,
        imprints_by_uuid=imprints_by_uuid,
        cells_by_name=cells_by_name,
        imprints_by_name=imprints_by_name,
        entities_by_cell=_sorted(entities_by_cell),
        entities_by_imprint=_sorted(entities_by_imprint),
        placed_by={key: tuple(items) for key, items in placed.items()},
        orphans=tuple(sorted(orphans, key=lambda r: str(r.name or ""))),
    )


def _add_placed(placed: dict, cell_uuid: Optional[str], kind: str,
                section: str, owner: dict, owner_name: Optional[str] = None) -> None:
    """Record ONE placer under its cell's UUID. A placer with NO cell_uuid
    (a raw record whose lift could not mint one) names no target and is
    dropped — the entity side is UUID-only too, so both sides agree."""
    if not cell_uuid:
        return
    placed[cell_uuid].append(PlacedBy(kind, section, owner, owner_name))


def file_parent_map(root: IncludeTreeNode) -> dict:
    """``{file path -> its parent file's path, or None for the root}`` for
    every file node.

    The file-level block of a context menu ("Add included file…" / "Remove
    this file") needs the parent of the file it acts on. For an entity leaf
    under a cell from ANOTHER file that target is the entity's own declaring
    file, so its parent is needed at BUILD time, before that file node is
    reached — hence a pre-pass. First occurrence wins on a diamond (the same
    file shown under two branches): any one parent can disable the include."""
    parents: dict = {}

    def walk(node: IncludeTreeNode, parent: Optional[Path]) -> None:
        parents.setdefault(node.path, parent)
        for child in node.children:
            walk(child, node.path)

    walk(root, None)
    return parents
