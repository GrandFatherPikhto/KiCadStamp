# kicadstamp/config/format3.py
"""Format 3 (UUID) — the ONE table of reference forms, the record walk, the
graph checks/normalization (У1/У2) and the WRITER STAMP (У4.1).

Moved out of `config/loader.py` (У4.1, plan §5): the У4 writer stamp must reuse
the SAME reference-form table as the loader's checks/normalization, so a new
form cannot enter one and be missed by the other. `loader.py` re-exports every
name below, so existing importers (`tests/fakes/format3.py` imports
`loader._f3_refs`) keep working unchanged.

Everything here is FROZEN in the product while `current_format() < 3`: the
writer stamp is gated by the caller (`config_writer._serialize`), the graph
checks are gated in `loader._load_config_uncached`.
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from uuid import uuid4

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _
from ..utils.file_cache import cached_file_read
from .includes import _load_config_file, walk_include_tree

logger = logging.getLogger(__name__)

# ── the §0 sections and the reference forms (moved from loader.py) ─────────
_F3_LIST_SECTIONS = ("chains", "clone_placements", "thermal_via_arrays",
                     "coordinate_placements", "net_traces", "entities",
                     "imprints")
_F3_DICT_SECTIONS = ("cells", "points")
# Free-form dict sections that are §0 records too (Н6) — their entries are plain
# dicts carrying a "uuid" key.
_F3_FREE_SECTIONS = ("extract_profiles", "clone_profiles", "sheet_templates")
# reference field on a record -> the section its `<field>_uuid` resolves in.
_F3_REF_TARGET = {"cell": "cells", "imprint": "imprints", "anchor_point": "points"}
# tree node kind -> the section its `ref_uuid` resolves in (Н2). Local kinds
# (module/mount/copper/component) and "external" reference no record.
_F3_NODE_KIND_TARGET = {"placement": "entities", "clone": "clone_placements",
                        "chain": "chains", "rule": "chains",
                        "coordinate": "coordinate_placements",
                        "net_trace": "net_traces", "point": "points"}

# Every section whose records carry a UUID (the §0 set).
_F3_RECORD_SECTIONS = tuple(_F3_DICT_SECTIONS) + tuple(_F3_FREE_SECTIONS) + tuple(_F3_LIST_SECTIONS)


def _f3_files(root_path: str) -> list[str]:
    """Every DISTINCT file of the include: graph, by its own path.

    Deduplicated by path: walk_include_tree does NOT dedupe a diamond (the same
    file reachable from two branches) on purpose, so without this a file
    included twice would contribute every record twice and a duplicate-name
    check would false-fatal."""
    out: list[str] = []
    seen: set = set()

    def walk(node) -> None:
        p = str(node.path)
        if p not in seen:
            seen.add(p)
            out.append(p)
        for child in node.children:
            walk(child)

    walk(walk_include_tree(str(root_path)))
    return out


def _f3_walk_nodes(nodes, out: list) -> None:
    for n in nodes or []:
        out.append(n)
        _f3_walk_nodes(n.get("children"), out)


class _F3Ref:
    """ONE reference form in a format-3 graph (plan У2.1.3).

    Describes WHERE the name field and its UUID sibling live — a record's own
    field, a chain spoke's, an anchor mapping's, a nested tree node's — and the
    section the UUID must resolve in. The SAME description drives the dangling
    check AND the loader's UUID->name normalization, so a new form cannot enter
    one and be missed by the other."""

    __slots__ = ("label", "target", "holder", "name_field", "uuid_field")

    def __init__(self, label: str, target: str, holder: dict,
                 name_field: str, uuid_field: str) -> None:
        self.label = label
        self.target = target
        self.holder = holder
        self.name_field = name_field
        self.uuid_field = uuid_field

    @property
    def uuid(self):
        return self.holder.get(self.uuid_field)


def _f3_records(data: dict):
    """Yield (section, name, uuid, index) for every §0 record in ONE dict.

    The single record walk shared by the per-file check (which tags records
    with their file) and by the normalizer's uuid->name map. `index` is the
    record's position in its list section (-1 for dict sections) — the П2
    "no name" diagnostic, never a name."""
    for section in _F3_DICT_SECTIONS:
        for name, rec in (data.get(section) or {}).items():
            yield (section, name, rec.get("uuid"), -1)
    for section in _F3_FREE_SECTIONS:
        for name, rec in (data.get(section) or {}).items():
            uuid = rec.get("uuid") if isinstance(rec, dict) else None
            yield (section, name, uuid, -1)
    for section in _F3_LIST_SECTIONS:
        for i, rec in enumerate(data.get(section) or []):
            if not isinstance(rec, dict):
                continue
            # П2: an unnamed record stays None — the "<section>[i]" label is a
            # DIAGNOSTIC for the "no name" fatal, never a name fed to the
            # uniqueness check (Р43 requires a name; В36 mints one on lift).
            yield (section, rec.get("name"), rec.get("uuid"), i)


def _f3_refs(data: dict):
    """Yield one _F3Ref per reference present in ONE dict (plan У2.1.3).

    A reference is emitted whenever its NAME field is present; whether it also
    carries a UUID is the check's business (У2.2 fatal), never a reason to skip
    it. Forms: a record's own fields (_F3_REF_TARGET), chain spokes, points ->
    points, cells' nested clone_placements, sheet_templates' nested
    clone_placements/coordinate_placements, tree nodes (_F3_NODE_KIND_TARGET)
    and a tree's / a tree_instances declaration's (anchor (point …))."""
    for section in _F3_LIST_SECTIONS:
        for i, rec in enumerate(data.get(section) or []):
            if not isinstance(rec, dict):
                continue
            nm = rec.get("name") or f"{section}[{i}]"
            label = f"{section} {nm!r}"
            for field, target in _F3_REF_TARGET.items():
                if rec.get(field) is not None:
                    yield _F3Ref(label, target, rec, field, field + "_uuid")
            if section == "chains":
                for sp in rec.get("spokes") or []:
                    if isinstance(sp, dict) and sp.get("cell") is not None:
                        yield _F3Ref(f"chain {nm!r} spoke {sp.get('pad')!r}",
                                     "cells", sp, "cell", "cell_uuid")
    # tree_instances: a LIST section in the include merge, but NOT a §0 record
    # section (a declaration gets no UUID of its own). Its OWN `anchor:` is the
    # TREE anchor grammar, so a (point …) there references a points: record and
    # must carry the UUID like any other reference (the template expansion runs
    # after the normalization pass and inherits the resolved name).
    for ti in data.get("tree_instances") or []:
        if not isinstance(ti, dict):
            continue
        anchor = ti.get("anchor")
        if isinstance(anchor, dict) and anchor.get("point") is not None:
            yield _F3Ref(f"tree_instances {ti.get('name')!r} anchor", "points",
                         anchor, "point", "point_uuid")
    for name, p in (data.get("points") or {}).items():
        if isinstance(p, dict) and p.get("anchor_point") is not None:
            yield _F3Ref(f"point {name!r}", "points", p,
                         "anchor_point", "anchor_point_uuid")
    for cell_name, cell in (data.get("cells") or {}).items():
        if not isinstance(cell, dict):
            continue
        for ncp in cell.get("clone_placements") or []:
            if isinstance(ncp, dict) and ncp.get("cell") is not None:
                yield _F3Ref(f"cell {cell_name!r} nested placement", "cells",
                             ncp, "cell", "cell_uuid")
    # sheet_templates: their nested clone/coordinate placements are copied into
    # the list sections by expand_sheet_templates — but the expansion runs AFTER
    # the normalization pass, so the reference must be resolved HERE, on the
    # template, and the copies inherit the real target name (У2.3(в)).
    for tpl_name, tpl in (data.get("sheet_templates") or {}).items():
        if not isinstance(tpl, dict):
            continue
        for cp in tpl.get("clone_placements") or []:
            if not isinstance(cp, dict):
                continue
            for field, target in (("cell", "cells"), ("anchor_point", "points")):
                if cp.get(field) is not None:
                    yield _F3Ref(f"sheet_template {tpl_name!r} clone_placement",
                                 target, cp, field, field + "_uuid")
        for cp in tpl.get("coordinate_placements") or []:
            if isinstance(cp, dict) and cp.get("anchor_point") is not None:
                yield _F3Ref(f"sheet_template {tpl_name!r} coordinate_placement",
                             "points", cp, "anchor_point", "anchor_point_uuid")
    for tree in data.get("trees") or []:
        if not isinstance(tree, dict):
            continue
        anchor = tree.get("anchor")
        if isinstance(anchor, dict) and anchor.get("point") is not None:
            yield _F3Ref(f"tree {tree.get('name')!r} anchor", "points",
                         anchor, "point", "point_uuid")
        nodes: list = []
        _f3_walk_nodes(tree.get("nodes"), nodes)
        for n in nodes:
            target = _F3_NODE_KIND_TARGET.get(n.get("kind"))
            if target is not None and n.get("ref") is not None:
                yield _F3Ref(f"tree {tree.get('name')!r} node {n.get('ref')!r}",
                             target, n, "ref", "ref_uuid")


def _f3_record_holder(data: dict, section: str, name, index: int) -> dict | None:
    """The record dict a `_f3_records` row lives in — a dict-section KEY or a
    list-section position. None when the row is not a dict (defensive)."""
    if section in _F3_FREE_SECTIONS or section in _F3_DICT_SECTIONS:
        rec = (data.get(section) or {}).get(name)
        return rec if isinstance(rec, dict) else None
    items = data.get(section) or []
    if 0 <= index < len(items) and isinstance(items[index], dict):
        return items[index]
    return None


def _f3_collect(data: dict, file_path: str, records: list, folders: list,
                refs: list) -> None:
    """Append one file's records, folders and refs, each tagged with its file."""
    for section, name, uuid, i in _f3_records(data):
        records.append((section, name, uuid, file_path, i))
    for ref in _f3_refs(data):
        refs.append((ref.label, ref.target, ref.uuid, file_path))
    for section, table in (data.get("folders") or {}).items():
        for path_key, uuid in (table or {}).items():
            folders.append((section, path_key, uuid, file_path))


def _check_format3_graph(root_path: str) -> dict:
    """Per-FILE checks (place = the file the record lives in, Н7) plus graph-wide
    uniqueness and dangling refs (Н2/Н4/Н5/Н6). Returns the merged folder table
    (В39): a folder row may stand in each file that has records under it, but a
    (section, path) has ONE uuid across the graph, and a mismatch is a fatal
    naming both files."""
    records: list = []
    folders: list = []
    refs: list = []
    graph_files = _f3_files(root_path)
    # cached_file_read, NOT _load_config_file directly (У4, finding Н1): the
    # loader reads through this cache, which honours the ConfigWorkingSet — so
    # the check sees the SAME graph the program works in (staged edits included),
    # not the older bytes on disk.
    for f in graph_files:
        _f3_collect(cached_file_read(Path(f), _load_config_file), f, records, folders, refs)

    # П2: a format-3 record MUST carry a name (Р43) — its own fatal, with the
    # file, section and index; never a phantom "duplicate full name".
    for section, name, uuid, f, i in records:
        if name is None:
            raise ValidationError(format_fatal_error(
                _("format 3: record without a name — {section}[{index}] in {path}").format(
                    section=section, index=i, path=f),
                [_("a format-3 record needs a name (Р43): the converter mints one "
                   "when it lifts the file (В36)")]))

    # П1/Н4: duplicate FULL name within a section — anywhere in the graph, the
    # SAME file included (Р43 uniqueness is per section, no per-file caveat).
    name_src: dict = {}
    for section, name, uuid, f, i in records:
        key = (section, name)
        if key in name_src:
            raise ValidationError(format_fatal_error(
                _("format 3: duplicate full name {name!r} in {section} — in {a} "
                  "and {b}").format(name=name, section=section,
                                    a=name_src[key], b=f),
                [_("names are unique within a section across the whole graph "
                   "(Р43): rename one of the two records")]))
        name_src[key] = f

    # record without a UUID — place is the record's OWN file (Н7)
    for section, name, uuid, f, i in records:
        if not uuid:
            raise ValidationError(format_fatal_error(
                _("format 3: record {name!r} in {section} has no uuid").format(
                    name=name, section=section),
                [_("in {path}: every record of a format-3 file carries a UUID; "
                   "add (uuid \"…\") or lift the file with the converter")
                 .format(path=f)]))

    # folder-table merge + one uuid per (section, path) (В39)
    merged_folders: dict = {}
    folder_src: dict = {}
    for section, path_key, uuid, f in folders:
        if not uuid:
            raise ValidationError(format_fatal_error(
                _("format 3: folder {path!r} in {section} has no uuid").format(
                    path=path_key, section=section),
                [_("in {path}: a folder row carries a UUID").format(path=f)]))
        prev = merged_folders.setdefault(section, {}).get(path_key)
        if prev is None:
            merged_folders[section][path_key] = uuid
            folder_src[(section, path_key)] = f
        elif prev != uuid:
            raise ValidationError(format_fatal_error(
                _("format 3: folder {path!r} in {section} has uuid {a} in {fa} "
                  "and {b} in {fb}").format(
                      path=path_key, section=section, a=prev, b=uuid,
                      fa=folder_src[(section, path_key)], fb=f),
                [_("one folder path in a section has ONE uuid across the whole "
                   "graph (В39): the seed is uuid5(NS, \"<section>|folder:<path>\")")]))

    # duplicate uuid across records AND distinct folders (Н4а)
    owner: dict = {}
    for section, name, uuid, f, i in records:
        if uuid in owner:
            raise ValidationError(format_fatal_error(
                _("format 3: duplicate uuid {uuid} — {a} ({fa}) and {b} ({fb})").format(
                    uuid=uuid, a=owner[uuid][0], fa=owner[uuid][1],
                    b=f"{section} {name!r}", fb=f),
                [_("a UUID must identify exactly one record or folder")]))
        owner[uuid] = (f"{section} {name!r}", f)
    for section, path_key, uuid, f in folders:
        desc = f"{section} folder {path_key!r}"
        if uuid in owner and owner[uuid][0] != desc:
            raise ValidationError(format_fatal_error(
                _("format 3: duplicate uuid {uuid} — {a} ({fa}) and {b} ({fb})").format(
                    uuid=uuid, a=owner[uuid][0], fa=owner[uuid][1], b=desc, fb=f),
                [_("a UUID must identify exactly one record or folder")]))
        owner.setdefault(uuid, (desc, f))

    # dangling references — place is the REFERENCING record's file (Н7)
    uuids_by_section: dict = {}
    for section, name, uuid, f, i in records:
        uuids_by_section.setdefault(section, set()).add(uuid)
    for label, target, uuid, f in refs:
        # У2.2: after У2 a missing UUID would mean resolving by the (possibly
        # lying) name hint — forbidden by §0. The converter (У3) puts a UUID in
        # every reference, so a format-3 reference without one is a fatal.
        if uuid is None:
            raise ValidationError(format_fatal_error(
                _("format 3: {label} has no uuid").format(label=label),
                [_("in {path}: every reference to a record carries the target's "
                   "UUID (§0); the name is only a hint. Add the sibling UUID or "
                   "lift the file with the converter").format(path=f)]))
        if uuid not in uuids_by_section.get(target, set()):
            raise ValidationError(format_fatal_error(
                _("format 3: {label} references uuid {uuid}, which is not in "
                  "{target}").format(label=label, uuid=uuid, target=target),
                [_("in {path}: a reference UUID must name an existing {target} "
                   "record").format(path=f, target=target)]))

    logger.debug(_("Format-3 checks passed: {files} files, {records} records")
                 .format(files=len(graph_files), records=len(records)))
    return merged_folders


def _normalize_format3_refs(data: dict) -> int:
    """У2.1 — resolve every format-3 reference by UUID and overwrite its name
    field with the target record's full name.

    Runs on the MERGED graph (after resolve_includes), BEFORE
    expand_sheet_templates/expand_tree_instances, so template copies see the
    real target names. The У1 checks have already proven the graph clean
    (unique UUIDs, unique full names, every reference carrying a UUID), so the
    UUID -> name map is unambiguous and this pass cannot guess. Consumers keep
    reading the name field — nothing downstream is changed and no RecordRef is
    introduced. Returns the number of references rewritten."""
    names: dict = {}
    for section, name, uuid, _i in _f3_records(data):
        if uuid:
            names.setdefault(section, {})[uuid] = name
    rewritten = 0
    for ref in _f3_refs(data):
        uuid = ref.uuid
        target_names = names.get(ref.target)
        if uuid is None or not target_names or uuid not in target_names:
            # _check_format3_graph already fataled on this (У2.2); defensive.
            continue
        ref.holder[ref.name_field] = target_names[uuid]
        rewritten += 1
    if rewritten:
        logger.debug(_("Format-3 references normalized by UUID: {count}")
                     .format(count=rewritten))
    return rewritten


def _check_expanded_uuids_unique(data: dict) -> None:
    """У5.2: after the template expansions, every UUID in the graph must still
    be unique — a DERIVED copy's uuid (Р-У5.3) could collide with another copy
    or with an original. `_check_format3_graph` ran on the RAW files BEFORE the
    expansions, so this is the only place that sees the generated records.

    Runs on the expanded dict (``_f3_records`` walks the sections the copies were
    appended to: entities / net_traces / clone_placements /
    coordinate_placements). A collision is a fatal NAMING BOTH records — the
    graph the loader is about to build would otherwise carry two records under
    one UUID, and every later reference would resolve to the first."""
    owner: dict = {}
    for section, name, uuid, i in _f3_records(data):
        if not uuid:
            continue
        label = f"{section} {name!r}" if name is not None else f"{section}[{i}]"
        prev = owner.get(uuid)
        if prev is not None and prev != label:
            raise ValidationError(format_fatal_error(
                _("format 3: duplicate uuid {uuid} after template expansion — "
                  "{a} and {b}").format(uuid=uuid, a=prev, b=label),
                [_("a generated copy's computed UUID must be unique across the "
                   "whole graph (it is a function of the ORIGINAL record's uuid "
                   "plus the instance identity) — two records sharing one UUID "
                   "would resolve every reference to the first")]))
        owner[uuid] = label


# ── the WRITER STAMP (У4.1) ────────────────────────────────────────────────

class _Format3Index:
    """Graph-wide indices the writer stamp resolves against: full name -> uuid
    and uuid -> full name per section, plus folder path -> uuid per section.
    Built over EVERY file of the graph, with the file being written replaced by
    the dict about to be written (so a record created in this very write is
    findable)."""

    __slots__ = ("by_name", "by_uuid", "folders")

    def __init__(self) -> None:
        self.by_name: dict = {}
        self.by_uuid: dict = {}
        self.folders: dict = {}


def _index_add_record(index: _Format3Index, section: str, name, uuid, file_path: str) -> None:
    """Index one record, refusing a duplicate full name or a duplicate uuid —
    the same uniqueness the loader enforces (Р43/Н4), so the stamp never writes
    a graph that would fail to load."""
    if not uuid:
        return
    existing = index.by_name.setdefault(section, {}).get(name)
    if existing is not None and existing != uuid:
        raise ValidationError(format_fatal_error(
            _("format 3: duplicate full name {name!r} in {section}").format(
                name=name, section=section),
            [_("in {path}: names are unique within a section across the whole "
               "graph (Р43) — rename one of the two records").format(path=file_path)]))
    index.by_name[section][name] = uuid
    prev_name = index.by_uuid.setdefault(section, {}).get(uuid)
    if prev_name is not None and prev_name != name:
        raise ValidationError(format_fatal_error(
            _("format 3: duplicate uuid {uuid} — {a!r} and {b!r}").format(
                uuid=uuid, a=prev_name, b=name),
            [_("a UUID must identify exactly one record")]))
    index.by_uuid[section][uuid] = name


def _index_dict(index: _Format3Index, d: dict, file_path: str) -> None:
    """Index one file's records and folder rows into `index`."""
    for section, name, uuid, _i in _f3_records(d):
        if name is not None:
            _index_add_record(index, section, name, uuid, file_path)
    for section, table in (d.get("folders") or {}).items():
        for path_key, uuid in (table or {}).items():
            if uuid:
                index.folders.setdefault(section, {}).setdefault(path_key, uuid)


def _load_previous_file(path) -> dict:
    """The file's PREVIOUS content — the working set first, then disk; {} when
    the file is new.

    Used by the stamp to inherit a record's UUID on an IN-PLACE edit (plan §5,
    Н2): a form-built dict (Points / Thermal via / Net trace docks) replaces the
    record whole and carries no `uuid`, so without this the stamp minted a new
    one and every reference to the record dangled."""
    from ..config_working_set import WORKING_SET

    resolved = str(Path(path).resolve())
    if WORKING_SET.enabled:
        staged = WORKING_SET.staged_content(resolved)
        if staged is not None:
            return staged
    if not Path(path).exists():
        return {}
    return cached_file_read(Path(path), _load_config_file)


def _build_format3_index(root: Path, path: Path, data: dict) -> _Format3Index:
    """Index the whole graph; the file `path` contributes `data` (not its
    on-disk bytes), so a record created by THIS write resolves too.

    A root that does not exist on disk yet (a brand-new profile/file written for
    the first time) has no graph to walk: only this write's records participate.
    The file being written but not yet part of the graph (a new include file) is
    indexed the same way."""
    index = _Format3Index()
    resolved = str(Path(path).resolve())
    if not Path(root).exists():
        _index_dict(index, data, str(path))
        return index
    seen_current = False
    for f in _f3_files(str(root)):
        is_current = str(Path(f).resolve()) == resolved
        if is_current:
            seen_current = True
        # cached_file_read (У4, Н1): the index must see the working set too, or a
        # reference to a record that only exists as an unsaved edit would be
        # refused (or resolved against stale disk bytes).
        _index_dict(index, data if is_current else cached_file_read(Path(f), _load_config_file), f)
    if not seen_current:
        _index_dict(index, data, str(path))
    return index


def _folder_prefixes(name: str):
    """The folder rows a record's FULL name implies: "Power/LDO/out" ->
    ["Power", "Power/LDO"]. A name without "/" implies none."""
    if not name or "/" not in name:
        return
    parts = name.split("/")
    for i in range(1, len(parts)):
        yield "/".join(parts[:i])


def _ensure_folders(data: dict, index: _Format3Index) -> None:
    """Add a folder row for every prefix of every record name that has none yet
    (plan §5 п.4). An existing path keeps its UUID from the graph (В39); a new
    path gets uuid4. An empty folder is never removed."""
    new_rows: dict = {}
    for section in _F3_RECORD_SECTIONS:
        for _s, name, _u, _i in ((s, n, u, i) for (s, n, u, i) in _f3_records(data) if s == section):
            if not name:
                continue
            for prefix in _folder_prefixes(name):
                had = (index.folders.get(section, {}).get(prefix)
                       or new_rows.get(section, {}).get(prefix))
                if had:
                    continue
                new_rows.setdefault(section, {})[prefix] = str(uuid4())
    if not new_rows:
        return
    table = data.setdefault("folders", {})
    for section, rows in new_rows.items():
        table.setdefault(section, {}).update(rows)


def stamp_format3(data: dict, root, path) -> dict:
    """The У4.1 writer stamp — run on the dict about to be written, under the
    `current_format() >= 3` gate (`config_writer._serialize`).

    1. every §0 record without a `uuid` gets `uuid4`;
    2. a NEW reference (name present, `<field>_uuid` absent) resolves to the
       target record's UUID by EXACT FULL NAME in the graph; not found -> the
       whole write is refused;
    3. a reference WITH a UUID keeps it and its name hint is rewritten to the
       target's current full name; a UUID missing from the graph (the target was
       deleted) -> the whole write is refused, listing the dangling references
       (so "delete without cascade" cannot leave a dangling reference);
    4. folder rows are minted for every path prefix of the record names, reusing
       an existing path's UUID (В39) and minting uuid4 for a new one;
    5. tree node refs / tree anchors go through the SAME `_f3_refs` table.

    The dict is not mutated: a deep copy is stamped. Returns the stamped copy.
    A dict with no §0 records and no references (root metadata, an empty file)
    is returned unchanged and needs no root."""
    data = copy.deepcopy(data)
    records = list(_f3_records(data))
    refs = list(_f3_refs(data))
    if not records and not refs:
        return data
    if root is None:
        raise ValidationError(format_fatal_error(
            _("format 3: cannot write — the active graph root is not set"),
            [_("in {path}: a format-3 write resolves reference UUIDs against the "
               "project graph; open the profile, or pass --config on the CLI")
             .format(path=path)]))

    # 1. records without a uuid: INHERIT the uuid from a record of the SAME
    # section and the SAME full name in the PREVIOUS version of the SAME file
    # (plan §5, Н2 — an in-place edit from a form must keep its identity, or
    # every reference to it dangles); otherwise uuid4 (a new record or a copy).
    # The previous content is the working set or the disk — the same thing the
    # loader reads.
    previous = _load_previous_file(path)
    inherited: dict = {}
    if previous:
        for p_section, p_name, p_uuid, _p_i in _f3_records(previous):
            if p_uuid and p_name is not None:
                inherited.setdefault((p_section, p_name), p_uuid)
    for section, name, uuid, index in _f3_records(data):
        if uuid:
            continue
        holder = _f3_record_holder(data, section, name, index)
        if holder is None:
            continue
        inherited_uuid = inherited.get((section, name)) if name is not None else None
        holder["uuid"] = inherited_uuid or str(uuid4())

    index = _build_format3_index(Path(root), Path(path), data)

    # 2/3. references.
    dangling: list = []
    for ref in _f3_refs(data):
        current = ref.uuid
        if current is None:
            target_uuid = index.by_name.get(ref.target, {}).get(
                ref.holder.get(ref.name_field))
            if target_uuid is None:
                raise ValidationError(format_fatal_error(
                    _("format 3: {label} names no existing {target} record "
                      "({name!r})").format(
                          label=ref.label, target=ref.target,
                          name=ref.holder.get(ref.name_field)),
                    [_("in {path}: a new reference is resolved once, at write "
                       "time, by the target's exact full name").format(path=path)]))
            ref.holder[ref.uuid_field] = target_uuid
        else:
            target_name = index.by_uuid.get(ref.target, {}).get(current)
            if target_name is None:
                dangling.append(f"{ref.label} -> uuid {current} ({ref.target})")
            else:
                ref.holder[ref.name_field] = target_name
    if dangling:
        raise ValidationError(format_fatal_error(
            _("format 3: cannot write — {count} reference(s) point at a uuid "
              "that is missing from the graph").format(count=len(dangling)),
            [_("in {path}: {items}").format(path=path, items="; ".join(dangling)),
             _("deleting a record leaves its references dangling — delete WITH "
               "cascade, or fix the references first")]))

    # 4. folders.
    _ensure_folders(data, index)
    return data
