# gui/docks/rename.py
"""
Rename support for ConfigTreeDock's context menu (2026-08-04, Denis: "А мы
можем добавить конекстное меню в конфиг чтобы переименовать плэейсменты,
целлы, профили извлечения и т.д.?") — covers all 7 recognized sections
(cells/clone_placements/thermal_via_arrays/extract_profiles/rules/points/
clone_profiles), including the 3 that have no GUI edit form yet, same
scope limit ConfigTreeDock's own module docstring already documents for
those (read-only elsewhere, but a bare rename doesn't need a form).

Two shapes, matching config/includes.py's _DICT_SECTIONS/_LIST_SECTIONS:

* Dict sections (cells:/points:/extract_profiles:/clone_profiles:) are
  keyed by name — rename_dict_entry() renames the KEY in place.
* List sections (clone_placements:/thermal_via_arrays:/rules:) carry their
  own name: field — rename_list_entry() sets it. rules: is the one
  exception with no required name: (falls back to net:, see config/
  models.py's rule_effective_name()) — renaming one for the first time is
  what actually GIVES it an explicit name:.

Cross-reference cascading (found via direct audit of config/models.py +
every resolver that reads cell:/anchor_point: — see
techdocs/handoff/ for the full citation table): only cells: and points:
are ever referenced BY NAME from elsewhere in the graph — cell: (clone_
placements, nested CellPlacement inside another Cell's components: — the
recursive-Cell case, ManualSpoke) and anchor_point: (clone_placements,
rules, thermal_via_arrays, points: chaining to another point).
clone_placements/thermal_via_arrays/extract_profiles/rules/clone_profiles
are never referenced by name from inside the YAML graph at all — only from
the CLI (`apply --only NAME`, `extract --profile NAME`, `clone-extract
--profile NAME`), which this can't see or fix, only warn about.

rename_references() below is a generic structural walk (any dict key
literally named field_name, anywhere, dict or list, recursively) rather
than one hard-coded field path per section — this is what reaches a
nested cell: reference inside another Cell's own components: for free,
without needing to special-case that shape here.

Operates on the SAME raw read-merge-write primitives as gui/docks/
_common.py (read_data/write_data) — plain PyYAML round-trip, no comment
preservation, consistent with every other write path in this GUI.
"""
import yaml
from pathlib import Path
from typing import List, Optional

from kicadstamp.config import load_config
from kicadstamp.config.includes import walk_include_tree
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ._common import read_data, write_data

# Section -> the field name that references one of its entries by name from
# elsewhere in the graph, or absent if nothing ever does (see module
# docstring's citation summary).
CASCADE_FIELD = {"cells": "cell", "points": "anchor_point"}

DICT_SECTIONS = ("cells", "points", "extract_profiles", "clone_profiles")
LIST_SECTIONS = ("clone_placements", "thermal_via_arrays", "rules", "net_traces")

# rules: entries fall back to net: as their effective name when name: is
# absent (config/models.py's rule_effective_name()) — the only section with
# a fallback identifying field. Public (2026-08-05): gui/docks/entity_delete.py
# needs the same "name or net" identity to match a rules: entry for removal.
# net_traces: entries always carry net: and it IS their --only identity
# (config/models.py's net_trace_effective_name()) — same "single field
# fallback" shape as rules (2026-08-21, plan net_trace_dock).
FALLBACK_KEY = {"rules": "net", "net_traces": "net"}


def _coordinate_placement_effective_name(entry: dict) -> str | None:
    """coordinate_placements: name is optional, defaulting to cluster/role
    (config/models.py's coordinate_placement_effective_name()) — so a
    nameless entry is matched in the tree by that composed display name,
    same "effective name" convention as rules:' net: fallback."""
    return entry.get("name") or (
        f"{entry.get('cluster')}/{entry.get('role')}"
        if entry.get("cluster") and entry.get("role") else None)


def _clone_placement_effective_name(entry: dict) -> str | None:
    """clone_placements: placer_name if set, else name (the Cluster tag) —
    same split as config/models.py's clone_placement_effective_name(),
    2026-08-15. Without this, the tree/Rename/Delete would show and match
    by the physical Cluster tag instead of the save/--only identity the
    user actually manages."""
    return entry.get("placer_name") or entry.get("name")


# Sections whose effective name is a COMPOSED fallback (not a single field) —
# callable(entry) -> identity, alongside FALLBACK_KEY's single-field fallbacks.
EFFECTIVE_NAME_FN = {
    "coordinate_placements": _coordinate_placement_effective_name,
    "clone_placements": _clone_placement_effective_name,
}


# clone_placements: which field Rename actually writes — placer_name once an
# entry already has one (2026-08-15 split: Cluster is a physical board tag,
# only the save/--only identity is what Rename is for), else name (pre-split
# entries, single field, unchanged).
RENAME_TARGET_FIELD = {
    "clone_placements": lambda entry: "placer_name" if entry.get("placer_name") else "name",
}


def entry_effective_name(section: str, entry: dict):
    """The identity used to match/rename/delete a list-section entry in the
    tree — `name`, falling back per-section (rules: net, coordinate_placements:
    cluster/role), the same convention config/models.py's effective-name
    functions document. Public: gui/docks/entity_delete.py's _entry_identity
    uses it too (2026-08-12, Group 1: coordinate_placements became a normal
    named-records section, so its composed fallback must be recognized there
    as well)."""
    fn = EFFECTIVE_NAME_FN.get(section)
    if fn is not None:
        return fn(entry)
    fallback_key = FALLBACK_KEY.get(section)
    return entry.get("name") or (entry.get(fallback_key) if fallback_key else None)


def collect_graph_files(root_path: Path) -> List[Path]:
    """Every physical file reachable from root_path via include:, deduped by
    resolved path — walk_include_tree() itself does NOT dedupe a diamond
    include (the same file reachable from two branches, see its own
    docstring: "both occurrences are walked and shown independently"), so a
    rename walking every file must dedupe here or it would rewrite (and
    report) the same file twice."""
    node = walk_include_tree(str(root_path))
    seen: dict = {}

    def walk(n) -> None:
        seen.setdefault(n.path.resolve(), n.path)
        for child in n.children:
            walk(child)

    walk(node)
    return list(seen.values())


def name_exists_in_graph(files: List[Path], section: str, name: str) -> bool:
    """True if `name` is already used by an entry in a DICT section (cells:/
    points:/extract_profiles:/clone_profiles:) anywhere in the graph.
    config/includes.py's resolve_includes() already treats the same key
    appearing in this section in two different files as FATAL (dict
    sections are merged key-by-key, fatal on a repeat) — a rename that
    would create exactly that must be refused up front, not discovered
    later at the next `apply`/`extract` run."""
    for path in files:
        if name in (read_data(path).get(section) or {}):
            return True
    return False


def collect_all_point_names(root_path: Path) -> List[str]:
    """Every points: key reachable from root_path via include: — used by
    gui/docks/rules.py's Point-chain combo (a Rule's own anchor_point can
    name a point declared in ANY file in the graph, points: resolving
    globally the same way cells: does — see collect_all_cell_names above,
    same reasoning)."""
    names: set = set()
    for path in collect_graph_files(root_path):
        names.update((read_data(path).get("points") or {}).keys())
    return sorted(names)


def collect_all_cell_names(root_path: Path) -> List[str]:
    """Every cells: key reachable from root_path via include: — used by
    gui/docks/rules.py's spoke.cell combo (2026-08-05, Denis: "Чтобы
    назначать разные целлы разным спицам? Да, думаю комбобоксик"). resolve_
    includes() already treats a cells: key repeated across two files as
    FATAL (dict sections merge key-by-key), so a plain union here can never
    silently collide — same reasoning name_exists_in_graph above relies on."""
    names: set = set()
    for path in collect_graph_files(root_path):
        names.update((read_data(path).get("cells") or {}).keys())
    return sorted(names)


def collect_section_entries(root_path: Path, section: str) -> dict:
    """Merged name -> entry dict for a DICT section (cells:/points:/
    extract_profiles:/clone_profiles:/sheet_templates:) across the whole
    include: graph rooted at root_path (root first, then includes in walk
    order). Read-only — the docks' display/autocomplete after the file
    pickers were removed (2026-08-21, plan flatten_and_single_file_gui):
    a cells:/points: entry can live in ANY included file, so the dock now
    reads the whole graph instead of one hand-picked file. A duplicate name
    across two files is already fatal in resolve_includes()/load_config()
    (dict sections merge key-by-key), so a plain update() can never
    silently collide here."""
    merged: dict = {}
    for path in collect_graph_files(root_path):
        merged.update(read_data(path).get(section) or {})
    return merged


def find_dict_entry_file(root_path: Optional[Path], section: str, name: str) -> Optional[Path]:
    """The physical file where a DICT-section entry (cells:/points:/...)
    named `name` lives, anywhere in the include: graph rooted at root_path.
    Used to write an EDIT of an existing entry back to ITS OWN file instead
    of the root (2026-08-21 review fix — see
    handoff_2026_08_21_flatten_gui_stage2_review_fix.md). None when
    root_path is None or the name is absent."""
    if root_path is None:
        return None
    for path in collect_graph_files(root_path):
        if name in (read_data(path).get(section) or {}):
            return path
    return None


def find_list_entry_file(root_path: Optional[Path], section: str, entry: dict) -> Optional[Path]:
    """The physical file where a LIST-section entry (rules:/clone_placements:/
    thermal_via_arrays:/coordinate_placements:/net_traces:) with the same
    effective identity as `entry` lives — same "write an edit back to its
    own file" purpose as find_dict_entry_file. Identity via
    entry_effective_name(), the one formula the loader's own duplicate-name
    checks use, so the match is unambiguous by construction."""
    if root_path is None:
        return None
    identity = entry_effective_name(section, entry)
    for path in collect_graph_files(root_path):
        for item in (read_data(path).get(section) or []):
            if isinstance(item, dict) and entry_effective_name(section, item) == identity:
                return path
    return None


def collect_all_rule_nets(root_path: Path) -> List[str]:
    """Distinct net: values of every rule reachable from root_path via
    include: — the net picker for Rules' Bulk-set Cell dialog (2026-08-20,
    plan rule_spoke_fixes stage 3). A net's rules routinely live in DIFFERENT
    included files, so this walks the whole graph, not just the current
    file."""
    nets: set = set()
    for path in collect_graph_files(root_path):
        for rule in (read_data(path).get("rules") or []):
            if isinstance(rule, dict) and rule.get("net"):
                nets.add(rule["net"])
    return sorted(nets)


def collect_rules_by_net(root_path: Path, net: str) -> List[tuple]:
    """Every (file_path, rule_dict) in the include: graph rooted at
    root_path whose rule's net: equals `net` — what the Bulk-set Cell action
    must touch: ALL of a net's spokes, even when the rules live in different
    included files (2026-08-20, plan rule_spoke_fixes stage 3)."""
    result: List[tuple] = []
    for path in collect_graph_files(root_path):
        for rule in (read_data(path).get("rules") or []):
            if isinstance(rule, dict) and rule.get("net") == net:
                result.append((path, rule))
    return result


def collect_all_sheet_names(root_path: Path) -> List[str]:
    """Distinct human-readable sheet-instance names reachable from
    root_path's schematic_dir/schematic_files (RuntimeContext.sheet_names,
    built in config/loader.py's load_config, see kicadstamp/sheet_names.py)
    — the SAME live-proven mechanism PlacerDock's own Redraw already uses
    (_load_target_config), just for combo autocomplete instead of
    resolution. Empty list on any load failure (broken root config) — the
    Sheet fields stay free-text-editable either way, this is autocomplete,
    not validation. NOTE: deliberately NOT a walk of the include: graph
    (unlike collect_all_cell_names/collect_all_point_names) — Sheet is not
    a YAML dictionary section, it is a parse result of the project's
    *.kicad_sch files, normally declared only in the root config itself."""
    try:
        _, ctx = load_config(str(root_path))
    except (ValidationError, OSError, yaml.YAMLError):
        return []
    return sorted(set(ctx.sheet_names.values()))


def rename_dict_entry(path: Path, section: str, old_name: str, new_name: str) -> None:
    """cells:/points:/extract_profiles:/clone_profiles: — renames the dict
    KEY in place (same position among the section's other entries), value
    untouched. Raises OSError if old_name is missing, or new_name already
    exists in THIS file's section (name_exists_in_graph above is the
    graph-wide check callers should run first — this is a local backstop,
    not a substitute for it)."""
    data = read_data(path)
    section_dict = data.get(section) or {}
    if old_name not in section_dict:
        raise OSError(_("{name!r} not found in {section}: of {path}")
                      .format(name=old_name, section=section, path=path))
    if new_name in section_dict:
        raise OSError(_("{name!r} already exists in {section}: of {path}")
                      .format(name=new_name, section=section, path=path))
    # Rebuild the dict rather than pop+reinsert — keeps the renamed entry in
    # its original position instead of moving it to the end for no reason.
    data[section] = {(new_name if key == old_name else key): value
                     for key, value in section_dict.items()}
    write_data(path, data)


def rename_list_entry(path: Path, section: str, old_name: str, new_name: str) -> None:
    """clone_placements:/thermal_via_arrays:/rules:/coordinate_placements: —
    list of dicts, entry matched by its effective name (name:, or the
    section's fallback — net: for a nameless rules:, cluster/role for a
    nameless coordinate_placements: — see entry_effective_name), then the
    section's RENAME_TARGET_FIELD is set/created on it (name: normally;
    placer_name for a clone_placement that already has one — 2026-08-15
    split, Cluster stays a physical tag). For a rules:/coordinate_placements:
    entry matched only by its fallback, this is what actually gives it an
    explicit name: for the first time, distinct from its fallback."""
    data = read_data(path)
    items = data.get(section) or []

    match = None
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if entry_effective_name(section, entry) == old_name:
            match = entry
            break
    if match is None:
        raise OSError(_("{name!r} not found in {section}: of {path}")
                      .format(name=old_name, section=section, path=path))

    for entry in items:
        if entry is match or not isinstance(entry, dict):
            continue
        if entry_effective_name(section, entry) == new_name:
            raise OSError(_("{name!r} already exists in {section}: of {path}")
                          .format(name=new_name, section=section, path=path))

    target_field = RENAME_TARGET_FIELD.get(section, lambda e: "name")(match)
    match[target_field] = new_name
    write_data(path, data)


def _rename_field_recursive(node, field_name: str, old_value: str, new_value: str) -> bool:
    changed = False
    if isinstance(node, dict):
        for key, value in node.items():
            if key == field_name and value == old_value:
                node[key] = new_value
                changed = True
            elif isinstance(value, (dict, list)):
                changed = _rename_field_recursive(value, field_name, old_value, new_value) or changed
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                changed = _rename_field_recursive(item, field_name, old_value, new_value) or changed
    return changed


def rename_references(files: List[Path], field_name: str, old_value: str, new_value: str) -> List[Path]:
    """Rewrites every `field_name: old_value` to `field_name: new_value`
    anywhere it appears, recursively, across `files` — a generic structural
    walk (matches by key name, not by section-specific field path), so it
    also reaches a nested cell: reference inside another Cell's own
    components: (the recursive-Cell composition case) without needing to
    special-case that shape. Writes back only files that actually changed;
    returns those paths for a summary message."""
    changed_files = []
    for path in files:
        data = read_data(path)
        if _rename_field_recursive(data, field_name, old_value, new_value):
            write_data(path, data)
            changed_files.append(path)
    return changed_files


def rename_entry(root_path: Path, entry_path: Path, section: str,
                 old_name: str, new_name: str) -> List[Path]:
    """The single entry point ConfigTreeDock's context menu calls: renames
    the entry itself (in entry_path, wherever it's actually declared) and,
    for cells:/points: (see CASCADE_FIELD), every reference to it anywhere
    in the whole include: graph rooted at root_path. Returns the list of
    files actually changed (entry_path always included), for a summary
    message.

    Raises OSError (not found, or a name collision) before touching any
    file — collect_graph_files()/name_exists_in_graph() run first, so a
    rejected rename never leaves cells:/points: renamed in isolation with
    its references still pointing at the old name."""
    files = collect_graph_files(root_path)
    if section in DICT_SECTIONS and name_exists_in_graph(files, section, new_name):
        raise OSError(_("{name!r} already exists in {section}: somewhere in this "
                        "include: graph").format(name=new_name, section=section))

    if section in DICT_SECTIONS:
        rename_dict_entry(entry_path, section, old_name, new_name)
    else:
        rename_list_entry(entry_path, section, old_name, new_name)
    changed = [entry_path]

    # entry_path is included here too, not just the OTHER files — a
    # reference to the just-renamed entry may sit in the SAME file (e.g. a
    # clone_placements: entry alongside the cells: entry it targets).
    # rename_references() re-reads every file fresh from disk, so it sees
    # entry_path's already-renamed dict key without re-touching it (that's
    # a dict KEY, not a field literally named field_name — no double
    # application).
    field_name = CASCADE_FIELD.get(section)
    if field_name:
        changed_paths = {p.resolve() for p in changed}
        for path in rename_references(files, field_name, old_name, new_name):
            if path.resolve() not in changed_paths:
                changed.append(path)
                changed_paths.add(path.resolve())
    return changed
