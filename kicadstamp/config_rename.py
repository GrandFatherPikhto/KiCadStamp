# kicadstamp/config_rename.py
"""
Profile-config rename tool — propagates a Role/Cluster rename into the
profile YAML files reachable from a profile's include: graph.

The SAME ``renames.yaml`` config that ``schematic_rename_fields.py``
understands (``renames: {Role: {old: new}, Cluster: {old: new}}``) drives
this tool too: ``plan_rename_edits()`` renames the schematic side, and
``plan_profile_rename_edits()`` below renames the profile-config side. One
rename, applied everywhere.

Edit strategy — point-edit, never parse→dump (same reasoning as the
schematic tool): ``profiles/*.yaml`` files are hand-written, with comments
and personal formatting. ``yaml.safe_load()`` + ``yaml.dump()`` would
destroy all of that (PyYAML does not round-trip comments), and
``ruamel.yaml`` is not a project dependency. So, like
``schematic_rename_fields.py``, we parse ONLY to FIND the fields/values
(yaml.compose gives each scalar node's exact byte span in the original
text), then splice the replacement as a substring edit of the original
file text — the surrounding comments/formatting stay untouched.

Scope — only semantically-correct fields are edited, exactly as the
schematic side never confuses a Role VALUE with some other string that
happens to be equal:

  Cluster renames (``renames: Cluster:``):
    - ``cluster:``        — rules[].spokes[].cluster, coordinate_placements[].cluster
    - ``anchor_cluster:`` — rules / clone_placements / coordinate_placements /
                            thermal_via_arrays / net_traces / points
    - ``name:``           — clone_placements[].name ONLY (the Cluster TAG written
                            onto the board's components, read by role_narrowing.py
                            as the placement's own Cluster). Deliberately NOT
                            coordinate_placements[].name / rules[].name /
                            thermal_via_arrays[].name / points keys / cells keys —
                            those are save identities/recipe names, not clusters.

  Role renames (``renames: Role:``):
    - ``role:``                    — cells.*.components[].role,
                                     cells.*.clone_placements[].role,
                                     coordinate_placements[].role
    - ``anchor_role:``             — rules / clone_placements / coordinate_placements /
                                     thermal_via_arrays / net_traces / points /
                                     cells.*.anchor_role (display-only)
    - ``net_from_role:``           — cells.*.vias[] / cells.*.tracks[] /
                                     cells.*.components[].vias[] /
                                     net_traces[].vias[] / net_traces[].tracks[]
    - ``net_template_same_as_role:`` — cells.*.components[]
    - ``refs:`` keys               — clone_placements[].refs / cells.*.clone_placements[].refs
    - ``nets:`` keys               — clone_placements[].nets / cells.*.clone_placements[].nets

  Deliberately NOT touched: ``net_overrides`` (keys/values are NET names),
  ``params`` (placeholder names), ``anchor_sheet``/``sheet`` (sheet names),
  ``anchor_ref`` (refdes), ``anchor_pad``/``net_template_pad``/``net_from_role_pad``
  (pad numbers), and every scalar above as a mapping VALUE (only ``refs``/
  ``nets`` KEYS are roles).

Hierarchical clusters (open question from the handoff): ``cluster:`` /
``anchor_cluster:`` values containing ``/`` as a literal do NOT occur in the
current profiles (verified by diagnostics/config_rename_survey.py — zero
hits) — the ``/``-path is always assembled programmatically from other data
(sheet_names, net names). This tool therefore implements EXACT-VALUE matching
only, mirroring the schematic side, and does NOT rename segment prefixes
(``"Channel_1" -> "Channel_1_v2"`` will NOT rewrite ``"Channel_1/sub"``). If
hierarchical cluster literals ever appear, that must be designed separately —
it is an explicitly uncovered case, not a silent simplification.
"""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import yaml

from .exceptions import FieldsToolError, ValidationError
from .schematic_editing import Edit, EditReport, apply_edits
from .config.includes import _parse_include_entry

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class _PlanContext:
    """Mutable per-file planning state shared by the schema walkers."""
    text: str
    file: str
    role_map: dict[str, str]
    cluster_map: dict[str, str]
    edits: list[Edit] = dataclasses.field(default_factory=list)
    report: list[EditReport] = dataclasses.field(default_factory=list)
    matched_role: set[str] = dataclasses.field(default_factory=set)
    matched_cluster: set[str] = dataclasses.field(default_factory=set)

    def rename_scalar(self, node, location: str, kind: str,
                      rename_map: dict[str, str], matched: set[str]) -> None:
        """Record a rename of one scalar node's value, if it matches
        ``rename_map``. The splice preserves the original quote style: for a
        quoted scalar only the inner text is replaced; for a plain scalar the
        whole token is replaced. Multi-line scalars are skipped (never a
        role/cluster identifier)."""
        old_value = node.value
        if not isinstance(old_value, str) or old_value not in rename_map:
            return
        start, end = node.start_mark.index, node.end_mark.index
        if node.start_mark.line != node.end_mark.line:
            return  # multi-line scalar — not an identifier, skip
        new_value = str(rename_map[old_value])
        if node.style in ("'", '"') and end - start >= 2:
            repl_start, repl_end = start + 1, end - 1
        else:
            repl_start, repl_end = start, end
        if self.text[repl_start:repl_end] == new_value:
            return  # idempotent re-run — nothing to change
        self.edits.append((repl_start, repl_end, new_value))
        self.report.append(EditReport(self.file, [location], kind, old_value,
                                      new_value, "replace"))
        matched.add(old_value)


def _scalar_items(mapping) -> dict:
    """{key: (key_node, value_node)} for a yaml MappingNode with scalar keys."""
    out: dict = {}
    if not isinstance(mapping, yaml.MappingNode):
        return out
    for key_node, value_node in mapping.value:
        if isinstance(key_node, yaml.ScalarNode):
            out[key_node.value] = (key_node, value_node)
    return out


def _visit_value_field(mapping, field: str, location: str, kind: str,
                       rename_map: dict, matched: set, ctx: _PlanContext) -> None:
    items = _scalar_items(mapping)
    pair = items.get(field)
    if pair is None:
        return
    value_node = pair[1]
    if isinstance(value_node, yaml.ScalarNode):
        ctx.rename_scalar(value_node, f"{location}.{field}", kind, rename_map, matched)


def _visit_key_mapping(mapping, location: str, kind: str,
                       rename_map: dict, matched: set, ctx: _PlanContext) -> None:
    """Rename the KEYS of a mapping (refs:/nets: are {role: ...} dicts)."""
    if not isinstance(mapping, yaml.MappingNode):
        return
    for key_node, _value_node in mapping.value:
        if isinstance(key_node, yaml.ScalarNode):
            ctx.rename_scalar(key_node, f"{location}[{key_node.value}]", kind,
                              rename_map, matched)


def _visit_sequence(node, location: str, visitor, ctx: _PlanContext) -> None:
    if not isinstance(node, yaml.SequenceNode):
        return
    for idx, item in enumerate(node.value):
        visitor(item, f"{location}[{idx}]", ctx)


# ── Per-entry visitors ───────────────────────────────────────────────────────

def _visit_spoke(spoke, location: str, ctx: _PlanContext) -> None:
    _visit_value_field(spoke, "cluster", location, "Cluster",
                       ctx.cluster_map, ctx.matched_cluster, ctx)


def _visit_rule(rule, location: str, ctx: _PlanContext) -> None:
    _visit_value_field(rule, "anchor_role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)
    _visit_value_field(rule, "anchor_cluster", location, "Cluster",
                       ctx.cluster_map, ctx.matched_cluster, ctx)
    items = _scalar_items(rule)
    spokes = items.get("spokes")
    if spokes is not None:
        _visit_sequence(spokes[1], f"{location}.spokes", _visit_spoke, ctx)


def _visit_placement_kind(entry, location: str, ctx: _PlanContext,
                          include_name_as_cluster: bool) -> None:
    """Shared for clone_placements / coordinate_placements entries."""
    if include_name_as_cluster:
        _visit_value_field(entry, "name", location, "Cluster",
                           ctx.cluster_map, ctx.matched_cluster, ctx)
    _visit_value_field(entry, "role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)
    _visit_value_field(entry, "cluster", location, "Cluster",
                       ctx.cluster_map, ctx.matched_cluster, ctx)
    _visit_value_field(entry, "anchor_role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)
    _visit_value_field(entry, "anchor_cluster", location, "Cluster",
                       ctx.cluster_map, ctx.matched_cluster, ctx)
    items = _scalar_items(entry)
    refs = items.get("refs")
    if refs is not None:
        _visit_key_mapping(refs[1], f"{location}.refs", "Role",
                           ctx.role_map, ctx.matched_role, ctx)
    nets = items.get("nets")
    if nets is not None:
        _visit_key_mapping(nets[1], f"{location}.nets", "Role",
                           ctx.role_map, ctx.matched_role, ctx)


def _visit_clone_placement(entry, location: str, ctx: _PlanContext) -> None:
    _visit_placement_kind(entry, location, ctx, include_name_as_cluster=True)


def _visit_coordinate_placement(entry, location: str, ctx: _PlanContext) -> None:
    _visit_placement_kind(entry, location, ctx, include_name_as_cluster=False)


def _visit_thermal_via_array(entry, location: str, ctx: _PlanContext) -> None:
    _visit_value_field(entry, "anchor_role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)
    _visit_value_field(entry, "anchor_cluster", location, "Cluster",
                       ctx.cluster_map, ctx.matched_cluster, ctx)


def _visit_via(via, location: str, ctx: _PlanContext) -> None:
    _visit_value_field(via, "net_from_role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)


def _visit_track(track, location: str, ctx: _PlanContext) -> None:
    _visit_value_field(track, "net_from_role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)


def _visit_net_trace(entry, location: str, ctx: _PlanContext) -> None:
    _visit_value_field(entry, "anchor_role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)
    _visit_value_field(entry, "anchor_cluster", location, "Cluster",
                       ctx.cluster_map, ctx.matched_cluster, ctx)
    items = _scalar_items(entry)
    for field, visitor, sub in (("vias", _visit_via, "vias"),
                                ("tracks", _visit_track, "tracks")):
        seq = items.get(field)
        if seq is not None:
            _visit_sequence(seq[1], f"{location}.{sub}", visitor, ctx)


def _visit_point(point, location: str, ctx: _PlanContext) -> None:
    _visit_value_field(point, "anchor_role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)
    _visit_value_field(point, "anchor_cluster", location, "Cluster",
                       ctx.cluster_map, ctx.matched_cluster, ctx)


def _visit_component_slot(slot, location: str, ctx: _PlanContext) -> None:
    _visit_value_field(slot, "role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)
    _visit_value_field(slot, "net_template_same_as_role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)
    items = _scalar_items(slot)
    vias = items.get("vias")
    if vias is not None:
        _visit_sequence(vias[1], f"{location}.vias", _visit_via, ctx)


def _visit_cell_placement(entry, location: str, ctx: _PlanContext) -> None:
    # Nested CellPlacement: role:/refs:/nets:, closed boundary (no anchor_*).
    _visit_value_field(entry, "role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)
    items = _scalar_items(entry)
    refs = items.get("refs")
    if refs is not None:
        _visit_key_mapping(refs[1], f"{location}.refs", "Role",
                           ctx.role_map, ctx.matched_role, ctx)
    nets = items.get("nets")
    if nets is not None:
        _visit_key_mapping(nets[1], f"{location}.nets", "Role",
                           ctx.role_map, ctx.matched_role, ctx)


def _visit_cell(cell, location: str, ctx: _PlanContext) -> None:
    _visit_value_field(cell, "anchor_role", location, "Role",
                       ctx.role_map, ctx.matched_role, ctx)
    items = _scalar_items(cell)
    components = items.get("components")
    if components is not None:
        _visit_sequence(components[1], f"{location}.components",
                        _visit_component_slot, ctx)
    for field, visitor, sub in (("vias", _visit_via, "vias"),
                                ("tracks", _visit_track, "tracks")):
        seq = items.get(field)
        if seq is not None:
            _visit_sequence(seq[1], f"{location}.{sub}", visitor, ctx)
    nested = items.get("clone_placements")
    if nested is not None:
        _visit_sequence(nested[1], f"{location}.clone_placements",
                        _visit_cell_placement, ctx)


def _walk_file(node, ctx: _PlanContext) -> None:
    """Descend the composed node tree of ONE file along the known schema."""
    if not isinstance(node, yaml.MappingNode):
        return
    sections = _scalar_items(node)

    def visit_list(section: str, visitor) -> None:
        pair = sections.get(section)
        if pair is not None:
            _visit_sequence(pair[1], section, visitor, ctx)

    visit_list("rules", _visit_rule)
    visit_list("clone_placements", _visit_clone_placement)
    visit_list("coordinate_placements", _visit_coordinate_placement)
    visit_list("thermal_via_arrays", _visit_thermal_via_array)
    visit_list("net_traces", _visit_net_trace)

    points = sections.get("points")
    if points is not None and isinstance(points[1], yaml.MappingNode):
        for key_node, value_node in points[1].value:
            if isinstance(key_node, yaml.ScalarNode):
                _visit_point(value_node, f"points.{key_node.value}", ctx)

    cells = sections.get("cells")
    if cells is not None and isinstance(cells[1], yaml.MappingNode):
        for key_node, value_node in cells[1].value:
            if isinstance(key_node, yaml.ScalarNode):
                _visit_cell(value_node, f"cells.{key_node.value}", ctx)


# ── Include-graph walk ───────────────────────────────────────────────────────

def _collect_include_files(root: Path) -> dict[Path, str]:
    """Read every file reachable through include: from `root` (the file itself
    plus all included files, recursively). Returns {path: raw_text}, deduped by
    resolved path (diamonds are read once, same as resolve_includes). Cycles
    are fatal. include: parsing reuses config/includes.py's entry parser."""
    files: dict[Path, str] = {}

    def walk(path: Path, ancestors: set[Path]) -> None:
        if path in files:
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FieldsToolError(f"cannot read profile file {path}: {exc}") from exc
        files[path] = text
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise FieldsToolError(f"cannot parse profile file {path}: {exc}") from exc
        if not isinstance(data, dict):
            return
        for entry in data.get("include", []) or []:
            try:
                include_str, enabled = _parse_include_entry(entry, str(path))
            except ValidationError as exc:
                raise FieldsToolError(f"include error in {path}: {exc}") from exc
            if not enabled:
                continue
            child = (path.parent / include_str).resolve()
            if child in ancestors:
                raise FieldsToolError(
                    f"include: cycle detected — {child!s} is included from "
                    f"{path}, but is already being resolved higher up the same "
                    f"include chain")
            walk(child, ancestors | {child})

    walk(root.resolve(), {root.resolve()})
    return files


# ── Public entry point ───────────────────────────────────────────────────────

def plan_profile_rename_edits(
    profile_path: Path,
    renames_cfg: dict[str, dict[str, str]],
) -> tuple[dict[str, list[Edit]], dict[str, str], list[EditReport], list[str]]:
    """Plan Role/Cluster renames across a profile's whole include: graph.

    Args:
        profile_path: the profile ROOT .yaml (the file load_config() would
            read — include: entries are followed from it, never a bare
            ``profiles/**/*.yaml`` glob, so other profiles' files are never
            touched).
        renames_cfg: the ``renames:`` map from the shared renames.yaml config
            (``{Role: {old: new}, Cluster: {old: new}}``).

    Returns (edits_by_file, file_texts, report, unmatched), same shape as
    plan_rename_edits: edits_by_file maps file path -> list of byte-offset
    Edits, file_texts holds the original text for the writer, and unmatched
    lists rename entries that matched nothing anywhere (for the CALLER to warn
    about — an unmatched old_value is just as likely a harmless re-run as a
    typo; renaming is idempotent).
    """
    profile_path = Path(profile_path)
    if not profile_path.is_file():
        raise FieldsToolError(f"profile {profile_path!s} not found")

    role_map = {str(k): str(v) for k, v in (renames_cfg.get("Role") or {}).items()}
    cluster_map = {str(k): str(v) for k, v in (renames_cfg.get("Cluster") or {}).items()}

    files = _collect_include_files(profile_path)
    edits_by_file: dict[str, list[Edit]] = {}
    file_texts: dict[str, str] = {}
    report: list[EditReport] = []
    matched_role: set[str] = set()
    matched_cluster: set[str] = set()

    for path, text in files.items():
        path_str = str(path)
        file_texts[path_str] = text
        try:
            node = yaml.compose(text)
        except yaml.YAMLError as exc:
            raise FieldsToolError(f"cannot parse profile file {path}: {exc}") from exc
        ctx = _PlanContext(text=text, file=path_str, role_map=role_map,
                           cluster_map=cluster_map)
        _walk_file(node, ctx)
        edits_by_file[path_str] = ctx.edits
        report.extend(ctx.report)
        matched_role |= ctx.matched_role
        matched_cluster |= ctx.matched_cluster

    unmatched: list[str] = []
    for old_value in role_map:
        if old_value not in matched_role:
            unmatched.append(f"Role: {old_value!r}")
    for old_value in cluster_map:
        if old_value not in matched_cluster:
            unmatched.append(f"Cluster: {old_value!r}")

    return edits_by_file, file_texts, report, unmatched


# ── Write pipeline (profile-specific: YAML self-verify, not sexpdata) ────────

def write_profile_files(edits_by_file: dict[str, list[Edit]],
                        file_texts: dict[str, str]) -> tuple[list[str], list[str]]:
    """Per file, independently: .bak -> splice -> write -> re-parse with
    yaml.safe_load as a self-verify -> on failure, restore the original text
    and record the file as failed, then continue with the rest. Returns
    (written, failed)."""
    written: list[str] = []
    failed: list[str] = []
    for file, edits in edits_by_file.items():
        if not edits:
            continue
        original = file_texts[file]
        bak_path = file + ".bak"
        with open(bak_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(original)
        new_text = apply_edits(original, edits)
        with open(file, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
        try:
            with open(file, encoding="utf-8") as fh:
                yaml.safe_load(fh)
        except Exception as exc:
            logger.error("%s: result does not parse as YAML (%s: %s) — restoring from %s",
                         file, type(exc).__name__, exc, bak_path)
            with open(file, "w", encoding="utf-8", newline="") as fh:
                fh.write(original)
            failed.append(file)
            continue
        written.append(file)
        logger.info("%s: written, backup at %s, yaml.safe_load self-verify OK", file, bak_path)
    return written, failed


def print_profile_report(report: list[EditReport], write_mode: bool) -> None:
    print(f"\n=== PROFILE {'WRITE' if write_mode else 'DRY-RUN'}: {len(report)} edit(s) ===")
    for r in sorted(report, key=lambda r: (r.file, r.refs)):
        loc = ",".join(r.refs)
        print(f"  [{Path(r.file).name}] {loc}: {r.field} {r.old_value!r} -> {r.new_value!r}")
