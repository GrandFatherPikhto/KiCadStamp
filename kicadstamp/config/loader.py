# kicadstamp/config/loader.py

"""
config/loader.py — all YAML loading/validation logic for dataclasses
from config/models.py: load_config() (entry point) and all _load_* functions.
Split from monolithic config.py by the same refactoring as models.py.

Implementation notes (T3.1 god-file decomposition, 2026-08-05):
the per-entry loaders and their *_KNOWN_KEYS sets moved verbatim to
config/entries.py — the pure, single-entry validators (one YAML dict in,
one dataclass out) used both by load_config() below and by the GUI docks for
single-entry validation/rebuild. loader.py now keeps only load_config(), the
orchestration: include resolution, root-level deprecation checks,
duplicate/cross-entry validation, path resolution, and the
RuntimeContext/Config construction. The entry loaders are re-imported here so
this module's namespace — and therefore kicadstamp/config/__init__.py's
`from .loader import ...` surface — is unchanged.
"""
import copy
import difflib
import logging
from pathlib import Path

from ..constants import ROLE_CLUSTER_SOURCES, ROLE_CLUSTER_SOURCE_REGISTRY
from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _
from ..trees import check_mount_anchor_drift
from ..runtime_context import RuntimeContext
from ..utils.file_cache import cached_file_read, cached_graph_result

# NOTE (2026-08-11, arch step 6): the `..sheet_names` import below is a
# DELIBERATE, documented exception to config/loader's "pure YAML-schema
# description" goal (see runtime_context.py's module docstring for the full
# rationale). Short version: the YAML schema itself references schematic
# sheets — anchor_sheet (Rule/ClonePlacement/Point/ThermalViaArrayConfig)
# narrows anchor_role ambiguity, and resolving that anchor needs the real
# {uuid: Sheetname} dictionary. That dictionary is runtime-computed data
# (parsed from *.kicad_sch, NOT part of the YAML schema), so it is threaded
# via RuntimeContext rather than stored on Config — but it is BUILT here,
# once, at load, because every downstream consumer (validation,
# dependency_order, the planners, clone_role_resolver, template_extraction)
# needs the same map, and building it once alongside the config is the single
# construction point (no lazy per-consumer rebuilds, no divergence). The cost
# is bounded: sheet_names.py is a leaf (imports only exceptions/i18n), so
# config gains no cycle and no heavier dependency (no geometry/placement/
# adapter). Deliberately NOT refactored out — deferred/rebuild would spread
# the logic and risk divergence for zero architectural gain.
from ..sheet_names import LazySheetNameMap
from ..utils.paths import resolve_config_relative_path
from .entries import (
    _check_layer_value,
    _load_cell,
    _load_cell_placement,
    _load_clone_placement,
    _load_coordinate_placement,
    _load_entity,
    _load_manual_spoke,
    _load_net_trace,
    _load_point,
    _load_chain,
    _load_imprint,
    _load_template_component_slot,
    _load_template_track,
    _load_template_via,
    _load_thermal_via_array,
    _load_tree,
    _load_tree_instance,
    _point_is_footprint_eligible,
)
from .format_version import current_format
from .includes import _load_config_file, resolve_includes, walk_include_tree
from .sheet_templates import expand_sheet_templates
from .tree_instances import expand_tree_instances
from .models import (
    ThermalViaArrayConfig, CoordinatePlacement, NetTrace, Config,
    ImprintConfig, chain_effective_name, coordinate_placement_effective_name,
    clone_placement_effective_name, net_trace_effective_name,
    entity_effective_name, imprint_effective_name,
)

logger = logging.getLogger(__name__)


def _check_duplicate_names(items, name_fn, section_label: str, hint: str) -> None:
    """Fatal on two entries resolving to the same name — shared duplicate-name
    collision check (the thermal_via_arrays and coordinate_placements blocks
    were structurally identical copies; 2026-08-12, Group 3 consolidation).
    `name_fn` extracts the compared name from one item (may be a derived
    effective name, e.g. coordinate_placement_effective_name); `section_label`
    names the YAML section in the error message; `hint` explains why names
    must be unique within the list (--only cannot tell same-named entries
    apart)."""
    seen: dict[str, int] = {}
    for item in items:
        name = name_fn(item)
        seen[name] = seen.get(name, 0) + 1
    dup_names = sorted(name for name, count in seen.items() if count > 1)
    if dup_names:
        raise ValidationError(format_fatal_error(
            _("duplicate name(s) in {section}: {names}").format(
                section=section_label, names=dup_names),
            [hint]
        ))


# ── format 3 (UUID) checks ─────────────────────────────────────────────────
# Awake only when the build's CURRENT format is 3+ — current_format(), read at
# call time (plan У1.4, gate clarified 02.10): in У3 they guard the 2->3
# converter itself. The product (current_format() == 2) sleeps; tests pin
# format_version.CURRENT_FORMAT = 3.
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
    for f in graph_files:
        _f3_collect(_load_config_file(Path(f)), f, records, folders, refs)

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


def load_config(path: str) -> tuple[Config, RuntimeContext]:
    """Load + validate one config file (the root of an include: graph) into
    (Config, RuntimeContext). Memoized by graph mtime via cached_graph_result
    (2026-08-21, plan_2026_08_21_startup_graph_level_cache.md) — the WHOLE
    computation (traversal + merge + validation + sheet-name map), not just
    the raw file reads, is cached one layer above cached_file_read. The
    result is a deep copy on every call, so mutating it can never corrupt
    the cache — same contract as cached_file_read.

    THIS FUNCTION ALSO WRITES TO DISK (Т4, 25.09.2026): before the cached
    computation it runs `upgrade_graph_on_disk(path)`, which lifts every file of
    the include: graph whose on-disk FORMAT number is older than this build's and
    leaves a `.bak` with the previous bytes next to each one it rewrites (see
    kicadstamp/config/upgrade_on_disk.py and docs/config.md, «Версия формата»).
    The first open of a profile written by an older build therefore rewrites its
    files — the price of never reading a stale grammar, accepted by Denis.

    Three facts keep that harmless:
    - the sweep runs OUTSIDE the cached computation (never inside a cached body,
      where a write would invalidate the very cache entry being built);
    - it is a no-op as soon as the graph is current — one os.stat per file, no
      parse, no write, no `.bak`;
    - the in-memory result is identical either way, because the READERS lift by
      default (Т2): a graph that could not be lifted on disk (read-only
      directory, failed copy) still loads, with the WARNING/ERROR in the Log.
    """
    from .upgrade_on_disk import upgrade_graph_on_disk  # lazy — avoids an import cycle

    upgrade_graph_on_disk(path)
    return cached_graph_result("load_config", path, lambda: _load_config_uncached(path))


def _load_config_uncached(path: str) -> tuple[Config, RuntimeContext]:
    logger.info(_("Loading configuration from {path}").format(path=path))
    data = cached_file_read(Path(path), _load_config_file)
    data = resolve_includes(path, data)
    # Format-3 (У2): the У1.3 checks read the RAW graph files, so their place is
    # independent of the template expansions — they move here, right after the
    # include merge. The normalization pass runs AFTER them (it relies on a graph
    # whose UUIDs are unique and whose names exist) and BEFORE the expansions
    # below, which copy records AND references: every copy must already hold the
    # REAL target name, never the file's possibly-lying hint (У2.1.1/У2.1.2).
    if current_format() >= 3:
        data["folders"] = _check_format3_graph(path)
        # Normalize on a DEEP COPY (К1): the record dicts arrived here through
        # resolve_includes' SHALLOW merges, so they are the very objects
        # cached_file_read hands out on a cache hit (and the on-disk sweep reads
        # every graph file first, making the loader's own read a hit). Mutating
        # them would rewrite the hints for every later reader — including a
        # config_writer read — while the disk still holds the old bytes.
        data = copy.deepcopy(data)
        _normalize_format3_refs(data)
    # sheet_templates: expansion (2026-08-16) — must run after include
    # resolution (a template can live in an included subsystem file) and
    # BEFORE any per-entry loader/duplicate-name check, so template-generated
    # entries are indistinguishable from hand-written ones (see
    # kicadstamp/config/sheet_templates.py).
    data = expand_sheet_templates(data)
    # tree_instances: expansion (2026-09-02, plan tree_instances) — dict-level,
    # after include resolution AND after sheet_templates (a template may live
    # in an included file, or reference a sheet-template-generated entity),
    # BEFORE any per-entry loader/duplicate-name check, so the materialized
    # trees/entities flow through the SAME _load_tree/_load_entity path as
    # hand-written ones (rule 2 seen_refs + duplicate-name checks for free).
    # Unlike expand_sheet_templates, the raw 'tree_instances:' key is KEPT —
    # the loader parses it into cfg.tree_instances below (see
    # kicadstamp/config/tree_instances.py).
    data = expand_tree_instances(data)

    if 'target_ref' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'target_ref' at root of config"),
            [_("global target_ref has been removed (see discussion v117): each spoke "
               "rule now has its own anchor – write anchor_ref: <ref> inside the rule "
               "in rules; each thermal_via_arrays entry has its own anchor_ref field")]
        ))
    if 'side' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'side' at root of config"),
            [_("use layer: F.Cu or layer: B.Cu instead (layer for ManualSpoke rules; "
               "back -> B.Cu)")]
        ))
    root_layer = data.get('layer', 'F.Cu')
    _check_layer_value(root_layer, _("at root of config"))

    if 'thermal_via_array' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'thermal_via_array' at root of config"),
            [_("generalized to a list 2026-08-02 (a second IC needing thermal vias — AD9707 — "
               "showed up): rename to 'thermal_via_arrays:' and wrap the single block in a "
               "YAML list ('- name: ...'), e.g.\n"
               "thermal_via_arrays:\n"
               "  - name: {name}\n"
               "    ...")
             .format(name=data['thermal_via_array'].get('name', '<name>')
                     if isinstance(data['thermal_via_array'], dict) else '<name>')]
        ))

    thermal_vias: list[ThermalViaArrayConfig] = [
        _load_thermal_via_array(tva_data) for tva_data in data.get('thermal_via_arrays', [])
    ]

    # Fatal on collision: two entries with the same name would silently
    # collide under --only, same reasoning/shape as the rules' --only
    # collision check just below where this used to live (see the rules
    # loop) — shared duplicate-name validator, see _check_duplicate_names.
    # Unlike rules, name here is always explicit (required above), so this is
    # a plain duplicate check, no derived-name fallback involved.
    _check_duplicate_names(
        thermal_vias, lambda tva: tva.name, "thermal_via_arrays",
        _("every thermal_via_arrays entry needs a unique name: — --only cannot tell "
          "same-named entries apart otherwise"))

    coordinate_placements: list[CoordinatePlacement] = [
        _load_coordinate_placement(cp_data) for cp_data in data.get('coordinate_placements', [])
    ]

    # Same duplicate-name collision check as thermal_via_arrays above — the
    # name here is USUALLY derived (cluster/role), not explicit, but --only
    # still needs it to be unique across the whole list.
    _check_duplicate_names(
        coordinate_placements, coordinate_placement_effective_name, "coordinate_placements",
        _("every coordinate_placements entry needs a unique name (explicit, or the "
          "default cluster/role pair) — --only cannot tell same-named entries apart "
          "otherwise"))

    net_traces: list[NetTrace] = [
        _load_net_trace(nt_data) for nt_data in data.get('net_traces', [])
    ]

    # net_traces: the identity is name: (2026-09-12, plan_2026_09_12_internode_
    # copper_core Э2; design §11) — the net is an attribute now, so several
    # records on ONE net are legal (they are several bridges of that net). Two
    # records on the same NAME still collide under --only, so the same
    # duplicate-name discipline as the other list sections applies — read
    # through net_trace_effective_name, so a legacy record without name: is
    # checked by its net (the old behaviour, unchanged).
    _check_duplicate_names(
        net_traces, net_trace_effective_name, "net_traces",
        _("every net_traces entry needs a unique name: — two bridges of one net "
          "are legal, but they must carry different name: values (name: is the "
          "record's --only identity, and it falls back to net: when absent); "
          "--only cannot tell same-named entries apart otherwise"))

    # imprints: — recorded live-board snapshots (design_2026_09_05_scheme_
    # list.md, plan P1). A list section like thermal_via_arrays/clone_
    # placements; records normally live in an included .json file but the
    # section is format-agnostic (see _load_imprint in config/entries.py).
    imprints: list[ImprintConfig] = [
        _load_imprint(sl_data) for sl_data in data.get('imprints', [])
    ]

    # Same duplicate-name collision check as the other list sections — the
    # name is the Entity.imprint / --only identity.
    _check_duplicate_names(
        imprints, imprint_effective_name, "imprints",
        _("every imprints entry needs a unique name — --only and "
          "Entity.imprint references cannot tell same-named records apart "
          "otherwise"))

    # Cross-record ref uniqueness (design §9.2, plan §0.2): a real ref may be
    # recorded in at most ONE Imprint. Cloning one record onto another
    # sheet would otherwise move a component another record still expects, and
    # "Record..." of a new snapshot over an already-recorded ref must be a
    # clear error, not a silent double-ownership.
    ref_owner: dict[str, str] = {}
    dup_ref_problems: list[str] = []
    for sl in imprints:
        for comp in sl.components:
            prev = ref_owner.get(comp.ref)
            if prev is not None and prev != sl.name:
                dup_ref_problems.append(
                    _("{ref} (in both {a} and {b})").format(ref=comp.ref, a=prev, b=sl.name))
            ref_owner[comp.ref] = sl.name
    if dup_ref_problems:
        raise ValidationError(format_fatal_error(
            _("ref(s) recorded in more than one imprints entry: {refs}").format(
                refs=", ".join(sorted(set(dup_ref_problems)))),
            [_("one component can belong to at most one Imprint — cloning "
               "one record would move a component another record expects; "
               "duplicate refs are also fatal at \"Record...\" capture time")]))

    cells_data = dict(data.get('cells', {}) or {})

    # Deprecated pre-rename key names (see handoff_2026_08_01_metalanguage_p2_p3.md) —
    # same "recognise + fatal with a rename hint" treatment as origin_x_mm/
    # origin_y_mm/side above: these are root-level keys, not covered by any
    # check_unknown_keys() call, so leaving the old names unhandled would
    # have made them silently do nothing instead of failing loudly.
    if 'templates_file' in data or 'template_files' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated fields 'templates_file'/'template_files'"),
            [_("renamed to cells_file:/cell_files: (the class became Cell, was "
               "SpokeTemplate), and those were themselves folded into include: on "
               "2026-08-02 — see the 'cells_file'/'cell_files' error below for the "
               "current way to do this")]
        ))

    if 'cells_file' in data or 'cell_files' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field(s) 'cells_file'/'cell_files' at root of config"),
            [_("folded into include: 2026-08-02 (one mechanism for splitting ANY "
               "section across files — rules:/clone_placements:/thermal_via_arrays:/"
               "cells:/points:/extract_profiles:/clone_profiles: — instead of cells "
               "having its own separate, differently-shaped mechanism): list the "
               "external file(s) under include: instead, and add a 'cells:' key "
               "wrapping what used to be that file's whole content, e.g.\n"
               "include:\n"
               "  - templates/a.yaml\n"
               "  - templates/b.yaml\n"
               "(each of those files needs 'cells:' at its own top level now, same "
               "shape as an inline cells: block here)")]
        ))

    cells = {name: _load_cell(name, cdata) for name, cdata in cells_data.items()}

    points_data = dict(data.get('points', {}) or {})
    points = {name: _load_point(name, pdata) for name, pdata in points_data.items()}

    chains = [_load_chain(chain_data) for chain_data in data.get('chains', [])]

    # Fatal on collision: two chains resolving to the same --only identity
    # (same net, neither disambiguated with an explicit name) would silently
    # both match the same --only call — catch it at load time, not at --only
    # time, and point at exactly which chains collided.
    seen_names: dict[str, list[str]] = {}
    for chain in chains:
        seen_names.setdefault(chain_effective_name(chain), []).append(
            chain.anchor_ref or chain.anchor_role or "?"
        )
    for effective_name, anchors in seen_names.items():
        if len(anchors) > 1:
            raise ValidationError(format_fatal_error(
                _("{count} chains resolve to the same --only identity {name!r} "
                  "(anchors: {anchors})").format(count=len(anchors), name=effective_name,
                                                  anchors=", ".join(anchors)),
                [_("give at least one of them an explicit name: to disambiguate "
                   "(e.g. name: {name}_a) – --only cannot tell them apart otherwise")
                 .format(name=effective_name)]
            ))

    clone_placements = [_load_clone_placement(cp) for cp in data.get('clone_placements', [])]

    # entities: — NEW section (design_2026_08_30_entity_placement_grammar.md):
    # the "what" of a placement, WITHOUT position (position lives only in a
    # trees: node, kind "placement"). Same duplicate-name discipline as the
    # other list sections: --only and trees: node refs cannot tell same-named
    # entities apart otherwise.
    entities = [_load_entity(e_data) for e_data in data.get('entities', [])]
    _check_duplicate_names(
        entities, entity_effective_name, "entities",
        _("every entities entry needs a unique name: — --only and trees: node "
          "refs (kind 'placement') cannot tell same-named entities apart "
          "otherwise"))

    # Cross-check: every imprint-based Entity's reference must name an
    # existing imprints entry (the cell-based symmetric check is
    # validation.check_entity_cells_exist — cells: is a dict the loader does
    # not own the existence of; imprints: is a list section parsed right
    # here, so this reference check belongs in the loader like the
    # anchor_point cross-checks). A dangling name would otherwise only
    # surface as a confusing fatal at Apply/Redraw time (P4).
    scheme_names = {sl.name for sl in imprints}
    missing_scheme_refs = sorted(
        e.imprint for e in entities
        if e.imprint is not None and e.imprint not in scheme_names)
    if missing_scheme_refs:
        raise ValidationError(format_fatal_error(
            _("entity references a missing imprints entry: {names}").format(
                names=", ".join(missing_scheme_refs)),
            [_("an imprint-based Entity names the recorded Imprint it "
               "clones; every such name must exist in imprints: (which may "
               "live in an included .json file — check include:)")]))

    logger.debug(_("Config loaded: entities={entities}").format(entities=len(entities)))

    # trees: — optional curated-redraw list section (design_2026_08_27_trees_in_
    # config_file.md). A single seen_refs set is shared across ALL trees of the
    # whole include graph, so the "a record's ref appears in at most one node"
    # invariant (trees.py's rule 2) holds across files, not just per file.
    #
    # The duplicate-name check runs TWICE on purpose: first on the RAW dicts,
    # before a single node is parsed (2026-09-13, plan_2026_09_13_tree_duplicate_
    # name_diagnosis Э1), then again on the loaded Trees below. Two same-named
    # trees normally also share their node refs (the case that produced the live
    # report: an already-materialized instance left in trees: next to its own
    # tree_instances: declaration), and then _load_tree — which walks the trees
    # one by one with the shared seen_refs — trips over trees.py's rule 2 and
    # reports "a record's position source must be exactly one": a SYMPTOM, in a
    # message that never mentions the duplicate name at all. The raw check fires
    # first and says the plain truth. Entries that are not mappings, or whose
    # name is not a string, are deliberately skipped here: _load_tree has better
    # messages for them ("entry must be a mapping, got ...") and must keep them.
    _check_duplicate_names(
        [t for t in (data.get('trees') or [])
         if isinstance(t, dict) and isinstance(t.get('name'), str)],
        lambda t: t['name'], "trees",
        _("every trees entry needs a unique name — curated redraw cannot tell "
          "same-named trees apart otherwise (a duplicate name may also arrive via "
          "include: from another file)"))
    tree_refs: set[str] = set()
    trees = [_load_tree(t, seen_refs=tree_refs) for t in data.get('trees', [])]
    # Second echelon (cheap, and it covers a name the loaders themselves could
    # derive): the same check on the loaded Trees.
    _check_duplicate_names(
        trees, lambda t: t.name, "trees",
        _("every trees entry needs a unique name — curated redraw cannot tell "
          "same-named trees apart otherwise (a duplicate name may also arrive via "
          "include: from another file)"))
    logger.debug(_("Config loaded: trees={trees}").format(trees=len(trees)))

    # tree_instances: — the RAW short declarations, kept on Config even after
    # the dict-level expansion above: cfg.tree_instances is the persistence
    # source and the GUI's read-only-instance index (P1/P2). No duplicate-name
    # check here by design — two declarations with the same name materialize
    # two generated trees with the same name, which the trees duplicate-name
    # check above already catches on the generated set.
    tree_instances = [_load_tree_instance(x) for x in data.get('tree_instances', [])]

    # Cross‑validation of layer/mirror
    for cp in clone_placements:
        cell = cells.get(cp.cell)
        if cell is None:
            continue
        placement_layer = cp.layer if cp.layer is not None else cell.layer
        layer_changed = placement_layer != cell.layer
        if cp.mirror and not layer_changed:
            raise ValidationError(format_fatal_error(
                _("mirror without layer change in clone_placement {name!r}").format(
                    name=clone_placement_effective_name(cp)),
                [_("cell {cell!r} is on {cell_layer}, placement layer is {place_layer} – "
                   "mirror without changing side is physically meaningless: either set layer to "
                   "{opposite}, or remove mirror").format(
                       cell=cp.cell, cell_layer=cell.layer, place_layer=placement_layer,
                       opposite='B.Cu' if cell.layer == 'F.Cu' else 'F.Cu')]
            ))
        if layer_changed and not cp.mirror:
            raise ValidationError(format_fatal_error(
                _("layer changed without mirror in clone_placement {name!r}").format(
                    name=clone_placement_effective_name(cp)),
                [_("cell {cell!r} is on {cell_layer}, placement layer is {place_layer} – "
                   "flipped footprints on non‑flipped sites are nonsense; add mirror: true, "
                   "or remove the layer override").format(
                       cell=cp.cell, cell_layer=cell.layer, place_layer=placement_layer)]
            ))

    # Same layer/mirror cross-validation for entities: — an Entity is the
    # "what" of a former ClonePlacement, so it inherits the exact same
    # physical rule (mirror without a layer change / layer change without
    # mirror is nonsense). Missing cell is skipped here (structural cell
    # existence is a validation.py concern, mirroring the clone path).
    for ent in entities:
        cell = cells.get(ent.cell)
        if cell is None:
            continue
        placement_layer = ent.layer if ent.layer is not None else cell.layer
        layer_changed = placement_layer != cell.layer
        if ent.mirror and not layer_changed:
            raise ValidationError(format_fatal_error(
                _("mirror without layer change in entity {name!r}").format(
                    name=entity_effective_name(ent)),
                [_("cell {cell!r} is on {cell_layer}, entity layer is {place_layer} – "
                   "mirror without changing side is physically meaningless: either set layer "
                   "to {opposite}, or remove mirror").format(
                       cell=ent.cell, cell_layer=cell.layer, place_layer=placement_layer,
                       opposite='B.Cu' if cell.layer == 'F.Cu' else 'F.Cu')]
            ))
        if layer_changed and not ent.mirror:
            raise ValidationError(format_fatal_error(
                _("layer changed without mirror in entity {name!r}").format(
                    name=entity_effective_name(ent)),
                [_("cell {cell!r} is on {cell_layer}, entity layer is {place_layer} – "
                   "flipped footprints on non‑flipped sites are nonsense; add mirror: true, "
                   "or remove the layer override").format(
                       cell=ent.cell, cell_layer=cell.layer, place_layer=placement_layer)]
            ))

    # Cross-validation of anchor_point references — every value must name an
    # existing points: entry; Rule/thermal_via_array additionally need a
    # footprint-eligible target (see _point_is_footprint_eligible), because
    # they look up a specific named pad on the resolved component
    # (spoke.pad/tva.pad) — a bare coordinate doesn't work for them.
    # ClonePlacement and Point-to-Point chains only ever need a coordinate,
    # so any point (shifted, xy-literal, or not) is fine there.
    def _check_anchor_point(owner_label: str, anchor_point: str | None, needs_footprint: bool):
        if anchor_point is None:
            return
        if anchor_point not in points:
            suggestion = difflib.get_close_matches(anchor_point, sorted(points.keys()), n=1)
            hint = (_(" (did you mean {suggestion!r}?)").format(suggestion=suggestion[0])
                    if suggestion else "")
            raise ValidationError(format_fatal_error(
                _("{owner}: anchor_point {name!r} not found in points:{hint}")
                .format(owner=owner_label, name=anchor_point, hint=hint),
                [_("known points: {names}").format(names=sorted(points.keys()))]
            ))
        if needs_footprint and not _point_is_footprint_eligible(points, anchor_point):
            raise ValidationError(format_fatal_error(
                _("{owner}: anchor_point {name!r} has no footprint to anchor on")
                .format(owner=owner_label, name=anchor_point),
                [_("point {name!r} has a shift, is xy-literal, or chains to one that does — "
                   "{owner} needs a live component to look up a specific pad from, a bare "
                   "coordinate is not enough. Use this point with a clone_placement instead, "
                   "or give it shift_x_mm=0/shift_y_mm=0 and no xy")
                 .format(name=anchor_point, owner=owner_label)]
            ))

    for pname, point in points.items():
        _check_anchor_point(_("point {name!r}").format(name=pname), point.anchor_point,
                            needs_footprint=False)
    for chain in chains:
        _check_anchor_point(_("chain (net {net!r})").format(net=chain.net), chain.anchor_point,
                            needs_footprint=True)
    for cp in clone_placements:
        _check_anchor_point(_("clone_placement {name!r}").format(
            name=clone_placement_effective_name(cp)), cp.anchor_point,
            needs_footprint=False)
    for ccp in coordinate_placements:
        # Anchor-relative CoordinatePlacement (2026-08-12, Group 0): only ever
        # needs a coordinate (like ClonePlacement), not a footprint — a
        # shifted or xy-literal Point works fine.
        _check_anchor_point(_("coordinate_placements entry {name!r}")
                            .format(name=coordinate_placement_effective_name(ccp)),
                            ccp.anchor_point, needs_footprint=False)
    for tva in thermal_vias:
        _check_anchor_point(_("thermal_via_arrays entry {name!r}").format(name=tva.name),
                            tva.anchor_point, needs_footprint=True)

    schematic_dir = data.get('schematic_dir')
    schematic_files = data.get('schematic_files', []) or []

    # Deliberate exception (see the `..sheet_names` import note above): the
    # sheet-name map must be built HERE, at load time. anchor_sheet in the
    # config references real schematic sheets, and this is the single
    # construction point for the runtime map shared by the whole pipeline.
    sheet_names = LazySheetNameMap(path, schematic_dir, schematic_files)

    config_dir = Path(path).parent
    # RAW values from the YAML (exactly what the user wrote, relative to that
    # YAML) stay on Config — Config is a pure description of the YAML schema.
    # The RESOLVED absolute paths below go onto RuntimeContext instead (P1-3,
    # 2026-08-25): the schema/runtime split that keeps Config free of
    # filesystem-derived data.
    registry_path = data.get('registry_path')
    track_registry_path = data.get('track_registry_path')
    log_file = data.get('log_file')
    operation_log_dir = data.get('operation_log_dir')
    root_sheet = data.get('root_sheet')

    resolved_registry_path = (resolve_config_relative_path(config_dir, registry_path)
                              if registry_path else None)
    resolved_track_registry_path = (resolve_config_relative_path(config_dir, track_registry_path)
                                    if track_registry_path else None)
    resolved_log_file = (resolve_config_relative_path(config_dir, log_file)
                         if log_file else None)
    resolved_operation_log_dir = (resolve_config_relative_path(config_dir, operation_log_dir)
                                  if operation_log_dir else None)
    resolved_root_sheet = (resolve_config_relative_path(config_dir, root_sheet)
                           if root_sheet else None)

    # board_name — NOT a path, deliberately not resolved relative to the YAML
    # (see Config.board_name's docstring): it's a board/project identity string
    # compared by basename stem only, and the config and the live board live in
    # unrelated directory trees.
    board_name = data.get('board_name')

    # role_cluster_source (2026-09-18, plan_2026_09_18_field_overrides_store Т3):
    # which side the resolver reads Role/Cluster from. An ABSENT key reads as
    # "registry" — the default — so every profile written before this change
    # keeps loading exactly as before (the migration guard, Т7). An UNKNOWN
    # value is FATAL: a typo must never silently fall back to the board, because
    # that would look precisely like "our override store stopped working".
    role_cluster_source = data.get('role_cluster_source', ROLE_CLUSTER_SOURCE_REGISTRY)
    if role_cluster_source not in ROLE_CLUSTER_SOURCES:
        raise ValidationError(format_fatal_error(
            _("unknown role_cluster_source {value!r}").format(value=role_cluster_source),
            [_("role_cluster_source says which side the resolver reads Role/"
               "Cluster from: {names}. Omit the key entirely for the default "
               "({default})").format(names=", ".join(ROLE_CLUSTER_SOURCES),
                                      default=ROLE_CLUSTER_SOURCE_REGISTRY)]))

    # sheet_names + the resolved path fields are runtime-computed data (NOT
    # part of the YAML schema), so they are threaded via RuntimeContext rather
    # than stored on Config — keeping Config a pure description of the YAML
    # schema (see runtime_context.py).
    ctx = RuntimeContext(
        sheet_names=sheet_names,
        registry_path=resolved_registry_path,
        track_registry_path=resolved_track_registry_path,
        log_file=resolved_log_file,
        operation_log_dir=resolved_operation_log_dir,
        root_sheet=resolved_root_sheet,
    )

    cfg = Config(
        layer=root_layer,
        cells=cells,
        points=points,
        thermal_via_arrays=thermal_vias,
        imprints=imprints,
        chains=chains,
        entities=entities,
        clone_placements=clone_placements,
        coordinate_placements=coordinate_placements,
        net_traces=net_traces,
        trees=trees,
        tree_instances=tree_instances,
        place_components=data.get('place_components', True),
        skip_existing_components=data.get('skip_existing_components', False),
        via_keepout_clearance_mm=data.get('via_keepout_clearance_mm', 0.2),
        via_search_step_mm=data.get('via_search_step_mm', 0.1),
        via_search_max_radius_mm=data.get('via_search_max_radius_mm', 3.0),
        via_search_n_directions=data.get('via_search_n_directions', 8),
        schematic_dir=schematic_dir,
        schematic_files=schematic_files,
        root_sheet=root_sheet,
        registry_path=registry_path,
        track_registry_path=track_registry_path,
        log_file=log_file,
        operation_log_dir=operation_log_dir,
        board_name=board_name,
        role_cluster_source=role_cluster_source,
        # Per-section folder table (format 3; {} on format 2) — see Config.folders.
        folders=data.get('folders', {}) or {},
    )
    # Load-time drift guard (plan §Y.3): a live base — a kind "mount" node's
    # anchor or the tree's own (role ...) anchor — must never name a role the
    # same tree places, or every Redraw silently drifts. Fatal, not a warning.
    check_mount_anchor_drift(cfg)
    total_spokes = sum(len(c.spokes) for c in cfg.chains)
    logger.debug(_("Config loaded: layer={layer}, cells={cells}, points={points}, chains={chains}, "
                   "spokes={spokes}, clone_placements={clones}").format(
                       layer=cfg.layer, cells=len(cfg.cells), points=len(cfg.points),
                       chains=len(cfg.chains), spokes=total_spokes, clones=len(cfg.clone_placements)))
    return cfg, ctx
