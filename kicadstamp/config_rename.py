# kicadstamp/config_rename.py
"""
Profile-config rename tool — propagates a Role/Cluster rename into the
profile config files reachable from a profile's include: graph.

The SAME ``renames.sexp`` config that ``schematic_rename_fields.py``
understands (``renames: {Role: {old: new}, Cluster: {old: new}}``) drives
this tool too: ``plan_rename_edits()`` renames the schematic side, and
``plan_profile_rename_edits()`` below renames the profile-config side. One
rename, applied everywhere.

Edit strategy — dict round-trip, never point-edit: the project config format
is s-expr now (2026-08-28, core_yaml_removal), and ``sexp_format.py`` has no
comment syntax at all (on read or write — ``flatten.py`` already noted the
same fact). The old byte-splicing machinery (``yaml.compose`` keeping each
scalar node's exact byte span in the original text) existed ONLY to preserve
hand-written YAML comments/formatting, which .sexp does not have — so it is
gone. We parse with ``sexp_to_dict``, mutate the dict along the known schema,
and write back with ``dict_to_sexp``. The role/cluster field table below is
unchanged and remains the domain knowledge of "what is a role/cluster".

Scope — only semantically-correct fields are edited, exactly as the
schematic side never confuses a Role VALUE with some other string that
happens to be equal:

  Cluster renames (``renames: Cluster:``):
    - ``cluster:``        — rules[].spokes[].cluster, coordinate_placements[].cluster,
                            clone_placements[].cluster (the Cluster TAG written onto
                            the board's components, read by role_narrowing.py as
                            the placement's own Cluster)
    - ``anchor_cluster:`` — rules / clone_placements / coordinate_placements /
                            thermal_via_arrays / net_traces / points
    (``name:`` of clone_placements is the SAVE/--only identity, NOT a Cluster —
    deliberately not touched; same for rules[].name / thermal_via_arrays[].name /
    coordinate_placements[].name / points keys / cells keys.)

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
from typing import Any, Dict, List, Tuple

from .exceptions import FieldsToolError, ValidationError
from .schematic_editing import EditReport
from .config.includes import _parse_include_entry
from .config.sexp_format import dict_to_sexp, sexp_to_dict

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class _Ctx:
    """Mutable per-file planning state shared by the schema walkers."""
    file: str
    role_map: Dict[str, str]
    cluster_map: Dict[str, str]
    report: List[EditReport] = dataclasses.field(default_factory=list)
    matched_role: set = dataclasses.field(default_factory=set)
    matched_cluster: set = dataclasses.field(default_factory=set)
    changed: bool = False

    def _map(self, kind: str) -> Dict[str, str]:
        return self.role_map if kind == "Role" else self.cluster_map

    def _matched(self, kind: str) -> set:
        return self.matched_role if kind == "Role" else self.matched_cluster

    def rename_value(self, entry: Dict[str, Any], field: str, location: str,
                     kind: str) -> None:
        """Rename ``entry[field]`` in place if it is a scalar value in the
        kind's rename map (exact-value match, never a segment prefix)."""
        old_value = entry.get(field)
        rename_map = self._map(kind)
        if not isinstance(old_value, str) or old_value not in rename_map:
            return
        new_value = str(rename_map[old_value])
        entry[field] = new_value
        self.report.append(EditReport(self.file, [location], kind, old_value,
                                      new_value, "replace"))
        self._matched(kind).add(old_value)
        self.changed = True

    def rename_key(self, mapping: Dict[Any, Any], location: str, kind: str) -> None:
        """Rename the KEYS of a mapping (refs:/nets: are {role: ...} dicts)."""
        rename_map = self._map(kind)
        for key in list(mapping.keys()):
            if isinstance(key, str) and key in rename_map:
                new_key = str(rename_map[key])
                mapping[new_key] = mapping.pop(key)
                self.report.append(EditReport(self.file, [f"{location}[{key}]"],
                                              kind, key, new_key, "replace"))
                self._matched(kind).add(key)
                self.changed = True


# ── Per-entry visitors ───────────────────────────────────────────────────────

def _visit_spoke(spoke: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    ctx.rename_value(spoke, "cluster", f"{location}.cluster", "Cluster")


def _visit_chain(chain: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    ctx.rename_value(chain, "anchor_role", f"{location}.anchor_role", "Role")
    ctx.rename_value(chain, "anchor_cluster", f"{location}.anchor_cluster", "Cluster")
    for i, spoke in enumerate(chain.get("spokes", []) or []):
        if isinstance(spoke, dict):
            _visit_spoke(spoke, f"{location}.spokes[{i}]", ctx)


def _visit_placement_kind(entry: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    """Shared for clone_placements / coordinate_placements entries — both now
    carry their Cluster in a separate `cluster:` field (2026-08-24 split), so
    the `name` field of neither is a Cluster and is never touched here."""
    ctx.rename_value(entry, "role", f"{location}.role", "Role")
    ctx.rename_value(entry, "cluster", f"{location}.cluster", "Cluster")
    ctx.rename_value(entry, "anchor_role", f"{location}.anchor_role", "Role")
    ctx.rename_value(entry, "anchor_cluster", f"{location}.anchor_cluster", "Cluster")
    for field in ("refs", "nets"):
        mapping = entry.get(field)
        if isinstance(mapping, dict):
            ctx.rename_key(mapping, f"{location}.{field}", "Role")


def _visit_clone_placement(entry: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    _visit_placement_kind(entry, location, ctx)


def _visit_coordinate_placement(entry: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    _visit_placement_kind(entry, location, ctx)


def _visit_thermal_via_array(entry: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    ctx.rename_value(entry, "anchor_role", f"{location}.anchor_role", "Role")
    ctx.rename_value(entry, "anchor_cluster", f"{location}.anchor_cluster", "Cluster")


def _visit_via(via: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    ctx.rename_value(via, "net_from_role", f"{location}.net_from_role", "Role")


def _visit_track(track: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    ctx.rename_value(track, "net_from_role", f"{location}.net_from_role", "Role")


def _visit_net_trace(entry: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    ctx.rename_value(entry, "anchor_role", f"{location}.anchor_role", "Role")
    ctx.rename_value(entry, "anchor_cluster", f"{location}.anchor_cluster", "Cluster")
    for i, via in enumerate(entry.get("vias", []) or []):
        if isinstance(via, dict):
            _visit_via(via, f"{location}.vias[{i}]", ctx)
    for i, track in enumerate(entry.get("tracks", []) or []):
        if isinstance(track, dict):
            _visit_track(track, f"{location}.tracks[{i}]", ctx)


def _visit_point(point: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    ctx.rename_value(point, "anchor_role", f"{location}.anchor_role", "Role")
    ctx.rename_value(point, "anchor_cluster", f"{location}.anchor_cluster", "Cluster")


def _visit_component_slot(slot: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    ctx.rename_value(slot, "role", f"{location}.role", "Role")
    ctx.rename_value(slot, "net_template_same_as_role",
                     f"{location}.net_template_same_as_role", "Role")
    for i, via in enumerate(slot.get("vias", []) or []):
        if isinstance(via, dict):
            _visit_via(via, f"{location}.vias[{i}]", ctx)


def _visit_cell_placement(entry: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    # Nested CellPlacement: role:/refs:/nets:, closed boundary (no anchor_*).
    ctx.rename_value(entry, "role", f"{location}.role", "Role")
    for field in ("refs", "nets"):
        mapping = entry.get(field)
        if isinstance(mapping, dict):
            ctx.rename_key(mapping, f"{location}.{field}", "Role")


def _visit_cell(cell: Dict[str, Any], location: str, ctx: _Ctx) -> None:
    ctx.rename_value(cell, "anchor_role", f"{location}.anchor_role", "Role")
    for i, slot in enumerate(cell.get("components", []) or []):
        if isinstance(slot, dict):
            _visit_component_slot(slot, f"{location}.components[{i}]", ctx)
    for i, via in enumerate(cell.get("vias", []) or []):
        if isinstance(via, dict):
            _visit_via(via, f"{location}.vias[{i}]", ctx)
    for i, track in enumerate(cell.get("tracks", []) or []):
        if isinstance(track, dict):
            _visit_track(track, f"{location}.tracks[{i}]", ctx)
    for i, placement in enumerate(cell.get("clone_placements", []) or []):
        if isinstance(placement, dict):
            _visit_cell_placement(placement, f"{location}.clone_placements[{i}]", ctx)


def _walk_file(data: Dict[str, Any], ctx: _Ctx) -> None:
    """Descend the parsed dict of ONE file along the known schema."""

    def visit_list(section: str, visitor) -> None:
        for i, entry in enumerate(data.get(section, []) or []):
            if isinstance(entry, dict):
                visitor(entry, f"{section}[{i}]", ctx)

    visit_list("chains", _visit_chain)
    visit_list("clone_placements", _visit_clone_placement)
    visit_list("coordinate_placements", _visit_coordinate_placement)
    visit_list("thermal_via_arrays", _visit_thermal_via_array)
    visit_list("net_traces", _visit_net_trace)

    for key, point in (data.get("points") or {}).items():
        if isinstance(point, dict):
            _visit_point(point, f"points.{key}", ctx)
    for key, cell in (data.get("cells") or {}).items():
        if isinstance(cell, dict):
            _visit_cell(cell, f"cells.{key}", ctx)


# ── Include-graph walk ───────────────────────────────────────────────────────

def _collect_include_files(root: Path) -> Dict[Path, Dict[str, Any]]:
    """Read every file reachable through include: from `root` (the file itself
    plus all included files, recursively). Returns {path: parsed dict}, deduped
    by resolved path (diamonds are read once, same as resolve_includes). Cycles
    are fatal. include: parsing reuses config/includes.py's entry parser.
    s-expr has no comments/formatting to preserve, so parsing is a plain
    sexp_to_dict (no byte-level compose needed — 2026-08-28, yaml_removal)."""
    files: Dict[Path, Dict[str, Any]] = {}

    def walk(path: Path, ancestors: set) -> None:
        if path in files:
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FieldsToolError(f"cannot read profile file {path}: {exc}") from exc
        try:
            data = sexp_to_dict(text) or {}
        except ValidationError as exc:
            raise FieldsToolError(f"cannot parse profile file {path}: {exc}") from exc
        files[path] = data
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
    renames_cfg: Dict[str, Dict[str, str]],
) -> Tuple[Dict[str, Dict[str, Any]], List[EditReport], List[str]]:
    """Plan Role/Cluster renames across a profile's whole include: graph.

    Args:
        profile_path: the profile ROOT .sexp (the file load_config() would
            read — include: entries are followed from it, never a bare
            ``profiles/**/*.sexp`` glob, so other profiles' files are never
            touched).
        renames_cfg: the ``renames:`` map from the shared renames.sexp config
            (``{Role: {old: new}, Cluster: {old: new}}``).

    Returns (mutated_by_file, report, unmatched):
        mutated_by_file maps file path -> the MUTATED data dict (only files
        with at least one change are present); report lists the individual
        renames (EditReport, same shape as the schematic side); unmatched
        lists rename entries that matched nothing anywhere (for the CALLER to
        warn about — an unmatched old_value is just as likely a harmless
        re-run as a typo; renaming is idempotent).
    """
    profile_path = Path(profile_path)
    if not profile_path.is_file():
        raise FieldsToolError(f"profile {profile_path!s} not found")

    role_map = {str(k): str(v) for k, v in (renames_cfg.get("Role") or {}).items()}
    cluster_map = {str(k): str(v) for k, v in (renames_cfg.get("Cluster") or {}).items()}

    files = _collect_include_files(profile_path)
    mutated_by_file: Dict[str, Dict[str, Any]] = {}
    report: List[EditReport] = []
    matched_role: set = set()
    matched_cluster: set = set()

    for path, data in files.items():
        ctx = _Ctx(file=str(path), role_map=role_map, cluster_map=cluster_map)
        _walk_file(data, ctx)
        if ctx.changed:
            mutated_by_file[str(path)] = data
        report.extend(ctx.report)
        matched_role |= ctx.matched_role
        matched_cluster |= ctx.matched_cluster

    unmatched: List[str] = []
    for old_value in role_map:
        if old_value not in matched_role:
            unmatched.append(f"Role: {old_value!r}")
    for old_value in cluster_map:
        if old_value not in matched_cluster:
            unmatched.append(f"Cluster: {old_value!r}")

    return mutated_by_file, report, unmatched


# ── Write pipeline (profile-specific: s-expr self-verify) ────────────────────

def write_profile_files(mutated_by_file: Dict[str, Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """Per file, independently: .bak -> write dict_to_sexp -> re-parse with
    sexp_to_dict as a self-verify -> on failure, restore the original text
    and record the file as failed, then continue with the rest. Returns
    (written, failed)."""
    written: List[str] = []
    failed: List[str] = []
    for file, data in mutated_by_file.items():
        with open(file, encoding="utf-8") as fh:
            original = fh.read()
        bak_path = file + ".bak"
        with open(bak_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(original)
        try:
            new_text = dict_to_sexp(data)
            sexp_to_dict(new_text)  # self-verify before touching the target
        except Exception as exc:
            logger.error("%s: result does not serialize/parse as s-expr (%s: %s) — "
                         "restoring from %s",
                         file, type(exc).__name__, exc, bak_path)
            failed.append(file)
            continue
        with open(file, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
        written.append(file)
        logger.info("%s: written, backup at %s, sexp_to_dict self-verify OK", file, bak_path)
    return written, failed


def print_profile_report(report: List[EditReport], write_mode: bool) -> None:
    print(f"\n=== PROFILE {'WRITE' if write_mode else 'DRY-RUN'}: {len(report)} edit(s) ===")
    for r in sorted(report, key=lambda r: (r.file, r.refs)):
        loc = ",".join(r.refs)
        print(f"  [{Path(r.file).name}] {loc}: {r.field} {r.old_value!r} -> {r.new_value!r}")
