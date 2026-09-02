# kicadstamp/config/includes.py
"""
includes.py — generic `include:` for splitting one profile YAML into several
files (e.g. by subsystem: ldo.yaml, pi_filters.yaml, dac_channels.yaml — each
carrying whatever mix of extract_profiles/clone_placements/rules/cells
that subsystem needs).

include: is general-purpose and used by BOTH load_config() (rules/
clone_placements/thermal_via_arrays/cells/points) and load_profile() in
kicadstamp_cli.py (extract_profiles/clone_profiles) — the two existing,
otherwise-independent YAML-reading entry points — since a subsystem file is
meant to carry extract_profiles AND clone_placements together, not just one
section. cells_file:/cell_files: used to be a separate, narrower mechanism
just for external Cell files (single-purpose, inline-overrides-external) —
folded into include: on 2026-08-02 (see
handoff_2026_08_02_cells_include_unification.md): an external Cell file is
now just another include:'d file, wrapped in its own cells: key, same as
any other dict section below.

Operates on raw dicts (already yaml.safe_load'd), before any Config/dataclass
parsing — resolve_includes() is called right after yaml.safe_load() in both
load_config() and load_profile(), and everything downstream is unchanged.
"""
import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

from ..exceptions import (
    ValidationError,
    format_fatal_error,
    unknown_extension_config_error,
    yaml_removed_config_error,
)
from ..i18n import _
from ..utils.file_cache import cached_file_read, cached_graph_result
from .aliases import normalize_section_aliases

logger = logging.getLogger(__name__)

# List sections: concatenated (this file's own entries first, then each
# include's, in listed order). YAML order has no functional effect —
# dependency_order.py already reorders rules/clone_placements by real anchor
# dependency at apply time.
_LIST_SECTIONS = ('chains', 'clone_placements', 'thermal_via_arrays', 'coordinate_placements',
                  'net_traces', 'entities', 'trees', 'tree_instances')

# Dict sections: merged key-by-key, fatal on a key defined in two different
# files — these are meant to be genuinely separate subsystem files, so a
# repeated key is far more likely a mistake than an intentional override.
# sheet_templates: (2026-08-16, plan_2026_08_16_sheet_templates.md) joins the
# same merge mechanism — a shared sheet_templates: block can live in its own
# subsystem file like any other dict section.
_DICT_SECTIONS = ('cells', 'points', 'extract_profiles', 'clone_profiles', 'sheet_templates')


def _load_config_file(path: Path) -> dict:
    """open(path) + parse by file extension — the raw config read shared by
    _resolve/_walk below and load_config() in loader.py (which reuses this
    exact loader so all three share ONE cache entry per file, not three
    independent ones). Always a dict, never None (a config whose content is
    empty/scalar-null becomes {}) — cached_file_read requires that.

    Format is selected by extension (2026-08-28, core_yaml_removal — YAML
    support was removed from the config graph): '.sexp' -> sexp_to_dict
    (kicadstamp/config/sexp_format.py), '.json' -> json.load (JSON was
    previously parsed through the YAML fallback; it stays a supported config
    format, only YAML was removed), anything else -> fatal — .yaml/.yml with
    the dedicated "convert with sexp_config_convert.py" message, any other
    extension with the unrecognized-extension message. Unlike
    config_writer._read_data (whose OSError wraps these for the GUI docks'
    `except OSError`), this raises the ValidationError directly — load_config
    already surfaces ValidationError for config problems, and the GUI
    open-root flow handles it.

    sexp_to_dict is imported here (function-level), not at module top: it
    would create a circular import (sexp_format.py imports _LIST_SECTIONS/
    _DICT_SECTIONS from this module at its own module level)."""
    with open(path, 'r', encoding='utf-8') as f:
        suffix = Path(path).suffix.lower()
        if suffix == '.sexp':
            from .sexp_format import sexp_to_dict
            return normalize_section_aliases(sexp_to_dict(f.read()) or {})
        if suffix == '.json':
            return normalize_section_aliases(json.load(f) or {})
        if suffix in ('.yaml', '.yml'):
            raise yaml_removed_config_error(path)
        raise unknown_extension_config_error(path, suffix)


def _parse_include_entry(entry: Any, source_path: str) -> tuple[str, bool]:
    if isinstance(entry, str):
        return entry, True
    if isinstance(entry, dict) and 'path' in entry:
        return entry['path'], bool(entry.get('enabled', True))
    raise ValidationError(format_fatal_error(
        _("include: invalid entry {entry!r} in {source!r}").format(entry=entry, source=source_path),
        [_("each entry must be either a file path string, or a mapping "
           "{{path: <str>, enabled: <bool>}}")]
    ))


def _resolve(path: str, data: dict[str, Any], ancestors: set[Path], resolved: set[Path],
             is_root: bool = True) -> dict[str, Any]:
    base_dir = Path(path).parent
    if not isinstance(data, dict):
        raise ValidationError(format_fatal_error(
            _("{file!r}: top level must be a YAML mapping, got {type}").format(
                file=path, type=type(data).__name__),
            [_("check for a stray/misplaced list (e.g. list items left without "
               "a wrapping 'clone_placements:'/'rules:' key)")]
        ))

    for section in _LIST_SECTIONS:
        raw = data.get(section)
        if raw is not None and not isinstance(raw, list):
            raise ValidationError(format_fatal_error(
                _("{file!r}: {section!r} must be a list, got {type}").format(
                    file=path, section=section, type=type(raw).__name__),
                [_("{section!r} entries are a YAML list ('- name: ...'); "
                   "{dict_sections} are mappings — check for a mixed-up "
                   "section key").format(section=section, dict_sections=_DICT_SECTIONS)]
            ))
    for section in _DICT_SECTIONS:
        raw = data.get(section)
        if raw is not None and not isinstance(raw, dict):
            raise ValidationError(format_fatal_error(
                _("{file!r}: {section!r} must be a mapping, got {type}").format(
                    file=path, section=section, type=type(raw).__name__),
                [_("{section!r} entries are a YAML mapping ('key: {{...}}'); "
                   "{list_sections} are lists — check for a mixed-up "
                   "section key").format(section=section, list_sections=_LIST_SECTIONS)]
            ))

    # An included (non-root) file's top-level keys outside _LIST_SECTIONS/
    # _DICT_SECTIONS/'include' have no defined multi-file merge semantics —
    # they used to be silently computed here and then dropped by the caller
    # (only _LIST_SECTIONS/_DICT_SECTIONS get pulled up, see below), a real,
    # repeatedly-hit class of bug (layer:, schematic_dir:, an un-wrapped
    # cells: shape — all found live on boards/3ch-awg-tia). Fatal instead
    # of guessing a merge rule for an arbitrary scalar/mapping key.
    # (thermal_via_arrays: used to be exactly this kind of unmergeable
    # scalar-shaped block under its old singular name thermal_via_array: —
    # generalized to a real list section 2026-08-02, see
    # handoff_2026_08_02_thermal_via_arrays_list.md, so it's fine to include
    # now, same as rules:/clone_placements:.)
    if not is_root:
        unsupported = sorted(k for k in data.keys()
                             if k not in _LIST_SECTIONS and k not in _DICT_SECTIONS and k != 'include')
        if unsupported:
            keys_str = ', '.join(repr(k) for k in unsupported)
            raise ValidationError(format_fatal_error(
                _("include: {file!r} has top-level key(s) not supported inside an included file: {keys}")
                .format(file=path, keys=keys_str),
                [_("include: only merges {list_sections} (lists) and {dict_sections} (mappings) "
                   "from an included file — anything else (e.g. layer:, schematic_dir:, "
                   "registry_path:) has no defined way to merge across multiple "
                   "included files and was previously silently dropped. Move {keys} to the root "
                   "config file instead")
                 .format(list_sections=_LIST_SECTIONS, dict_sections=_DICT_SECTIONS, keys=keys_str)]
            ))

    merged: dict[str, Any] = {}

    for section in _LIST_SECTIONS:
        merged[section] = list(data.get(section) or [])
    for section in _DICT_SECTIONS:
        merged[section] = dict(data.get(section) or {})
    for key, value in data.items():
        if key in _LIST_SECTIONS or key in _DICT_SECTIONS or key == 'include':
            continue
        merged[key] = value

    for entry in data.get('include', []) or []:
        include_str, enabled = _parse_include_entry(entry, path)
        if not enabled:
            logger.info(_("include: {file!r} disabled, skipped (not opened)").format(file=include_str))
            continue

        include_path = (base_dir / include_str).resolve()
        if not include_path.exists():
            raise ValidationError(format_fatal_error(
                _("include: file {file!r} not found").format(file=include_str),
                [_("expected at {path} (relative to {source!r}, not the current "
                   "working directory)").format(path=include_path, source=path)]
            ))
        # Two different situations used to be conflated into one fatal check here:
        # a true cycle (include_path is an ancestor of the file we're resolving right
        # now — the chain would loop forever) and a diamond (include_path was already
        # fully resolved somewhere else in the tree, via an unrelated branch — normal
        # reuse, not a contradiction, needed for content files meant to be shared
        # between subsystems/projects). ancestors tracks only the current path from
        # the root down (a cycle is only a cycle relative to where you currently are);
        # resolved is global for the whole resolve_includes() call (a diamond is a
        # diamond no matter which branch reaches it first).
        if include_path in ancestors:
            raise ValidationError(format_fatal_error(
                _("include: cycle detected — {file!r} is included from {source!r}, "
                  "but is already being resolved higher up the same include chain")
                .format(file=str(include_path), source=path),
                [_("the include: chain loops back on itself — remove one of the "
                   "include: entries that closes the loop")]
            ))
        if include_path in resolved:
            logger.info(_("include: {file!r} already resolved elsewhere in the tree "
                          "(diamond) — reusing, not re-merging").format(file=str(include_path)))
            continue
        resolved.add(include_path)

        include_data = cached_file_read(include_path, _load_config_file)
        include_merged = _resolve(str(include_path), include_data, ancestors | {include_path},
                                  resolved, is_root=False)

        for section in _LIST_SECTIONS:
            merged[section].extend(include_merged.get(section) or [])
        for section in _DICT_SECTIONS:
            for key, value in (include_merged.get(section) or {}).items():
                if key in merged[section]:
                    raise ValidationError(format_fatal_error(
                        _("include: duplicate {section} key {key!r}").format(section=section, key=key),
                        [_("defined in {a!r} and again via include {b!r}")
                         .format(a=path, b=include_str)]
                    ))
                merged[section][key] = value

        logger.info(_("include: merged {file!r} into {source!r}").format(file=include_str, source=path))

    return merged


def resolve_includes(path: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively resolves data['include'] (if present), merging list sections
    (rules/clone_placements — concatenated) and dict sections (cells/
    extract_profiles/clone_profiles — merged, fatal on key collision) from
    every included file into data. Returns a new dict; does not mutate data.

    A true cycle (a file including itself, directly or via a longer chain) is
    fatal. A diamond (the same file included from two unrelated branches of
    the tree) is not an error — it's resolved once and reused, not re-merged
    a second time (which would otherwise trip the dict-section duplicate-key
    check on the file's own content).

    path — the file data was loaded from (used to resolve relative include
    paths, and to seed cycle detection with the root itself).
    """
    root_path = Path(path).resolve()
    return _resolve(path, data, ancestors={root_path}, resolved={root_path})


@dataclasses.dataclass
class IncludeTreeNode:
    """One file in the include: graph, for display (GUI Config tree,
    2026-08-03) — deliberately NOT merged across files, unlike
    resolve_includes()'s flat result: each node keeps only its OWN directly
    declared content, so a leaf's physical file is always exactly "whichever
    node it's under" — no separate provenance tracking needed. children is
    this file's own include: list, each walked the same way, recursively."""
    path: Path
    sections: dict[str, Any]  # one key per _LIST_SECTIONS/_DICT_SECTIONS entry, this file's own only
    children: list["IncludeTreeNode"]


def _walk(path: str, ancestors: set[Path]) -> IncludeTreeNode:
    data = cached_file_read(Path(path), _load_config_file)
    if not isinstance(data, dict):
        raise ValidationError(format_fatal_error(
            _("{file!r}: top level must be a YAML mapping, got {type}").format(
                file=path, type=type(data).__name__),
            [_("check for a stray/misplaced list (e.g. list items left without "
               "a wrapping 'clone_placements:'/'rules:' key)")]
        ))

    sections: dict[str, Any] = {}
    for section in _LIST_SECTIONS:
        sections[section] = list(data.get(section) or [])
    for section in _DICT_SECTIONS:
        sections[section] = dict(data.get(section) or {})

    base_dir = Path(path).parent
    children: list[IncludeTreeNode] = []
    for entry in data.get('include', []) or []:
        include_str, enabled = _parse_include_entry(entry, path)
        if not enabled:
            continue

        include_path = (base_dir / include_str).resolve()
        if not include_path.exists():
            raise ValidationError(format_fatal_error(
                _("include: file {file!r} not found").format(file=include_str),
                [_("expected at {path} (relative to {source!r}, not the current "
                   "working directory)").format(path=include_path, source=path)]
            ))
        if include_path in ancestors:
            raise ValidationError(format_fatal_error(
                _("include: cycle detected — {file!r} is included from {source!r}, "
                  "but is already being resolved higher up the same include chain")
                .format(file=str(include_path), source=path),
                [_("the include: chain loops back on itself — remove one of the "
                   "include: entries that closes the loop")]
            ))
        children.append(_walk(str(include_path), ancestors | {include_path}))

    return IncludeTreeNode(path=Path(path), sections=sections, children=children)


def walk_include_tree(path: str) -> IncludeTreeNode:
    """Structure-preserving counterpart of resolve_includes() — walks the
    same include: graph from the same single root, but never merges: each
    file keeps only its own directly declared content, plus its own
    children. Used for display (GUI Config tree), not for loading a working
    Config — that's still resolve_includes()/load_config().

    A true cycle is fatal, same rule and same message as resolve_includes().
    A diamond (the same file reachable from two branches) is NOT deduped
    here — unlike resolve_includes(), there is no merge to protect, so both
    occurrences are walked and shown independently; this is deliberately
    simpler than resolve_includes() for exactly that reason (see
    handoff_2026_08_03_gui_tree_risks_resolved.md).

    Memoized by graph mtime via cached_graph_result (2026-08-21, plan_
    2026_08_21_startup_graph_level_cache.md) — the whole traversal is
    cached one layer above cached_file_read, so the GUI's many per-dock
    graph walks collapse to a single walk per changed graph."""
    return cached_graph_result(
        "walk_include_tree", path, lambda: _walk_include_tree_uncached(path))


def _walk_include_tree_uncached(path: str) -> IncludeTreeNode:
    root_path = Path(path).resolve()
    return _walk(path, ancestors={root_path})
