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
import difflib
import logging
from pathlib import Path

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _
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
    _load_template_component_slot,
    _load_template_track,
    _load_template_via,
    _load_thermal_via_array,
    _load_tree,
    _load_tree_instance,
    _point_is_footprint_eligible,
)
from .includes import _load_config_file, resolve_includes
from .sheet_templates import expand_sheet_templates
from .tree_instances import expand_tree_instances
from .models import (
    ThermalViaArrayConfig, CoordinatePlacement, NetTrace, Config,
    chain_effective_name, coordinate_placement_effective_name,
    clone_placement_effective_name, net_trace_effective_name,
    entity_effective_name,
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


def load_config(path: str) -> tuple[Config, RuntimeContext]:
    """Load + validate one config file (the root of an include: graph) into
    (Config, RuntimeContext). Memoized by graph mtime via cached_graph_result
    (2026-08-21, plan_2026_08_21_startup_graph_level_cache.md) — the WHOLE
    computation (traversal + merge + validation + sheet-name map), not just
    the raw file reads, is cached one layer above cached_file_read. The
    result is a deep copy on every call, so mutating it can never corrupt
    the cache — same contract as cached_file_read."""
    return cached_graph_result("load_config", path, lambda: _load_config_uncached(path))


def _load_config_uncached(path: str) -> tuple[Config, RuntimeContext]:
    logger.info(_("Loading configuration from {path}").format(path=path))
    data = cached_file_read(Path(path), _load_config_file)
    data = resolve_includes(path, data)
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

    # net_traces: one record per net by design (see NetTrace docstring in
    # config/models.py) — two records on the same net would silently collide
    # under --only=<net> (both would match), and there is no "second instance"
    # concept for a net trace to justify reusing one net name, so it is fatal
    # at load, same duplicate-name discipline as the other list sections.
    _check_duplicate_names(
        net_traces, net_trace_effective_name, "net_traces",
        _("every net_traces entry needs a unique net: — one record per net; "
          "--only=<net> cannot tell same-netted entries apart otherwise"))

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
    logger.debug(_("Config loaded: entities={entities}").format(entities=len(entities)))

    # trees: — optional curated-redraw list section (design_2026_08_27_trees_in_
    # config_file.md). A single seen_refs set is shared across ALL trees of the
    # whole include graph, so the "a record's ref appears in at most one node"
    # invariant (trees.py's rule 2) holds across files, not just per file.
    tree_refs: set[str] = set()
    trees = [_load_tree(t, seen_refs=tree_refs) for t in data.get('trees', [])]
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
    )
    total_spokes = sum(len(c.spokes) for c in cfg.chains)
    logger.debug(_("Config loaded: layer={layer}, cells={cells}, points={points}, chains={chains}, "
                   "spokes={spokes}, clone_placements={clones}").format(
                       layer=cfg.layer, cells=len(cfg.cells), points=len(cfg.points),
                       chains=len(cfg.chains), spokes=total_spokes, clones=len(cfg.clone_placements)))
    return cfg, ctx
