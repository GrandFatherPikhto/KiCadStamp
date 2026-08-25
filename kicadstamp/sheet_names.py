# kicadstamp/sheet_names.py
"""
sheet_names.py — dictionary {uuid: Sheetname} built by direct parsing of
*.kicad_sch files (sexpdata, same format already read by kicadstamp.cloner).
NOT via kipy — see discussion:
  - sheet_path.path_human_readable is broken in this KiCad version (always
    empty string; the field exists in the protocol but is not filled).
  - Direct mapping from UUID in fp.sheet_path.path to uuid from (sheet ...)
    blocks in .kicad_sch — empirically CONFIRMED: path[:-1] (without the last
    element, presumably the component's own uuid) exactly matches the sheet
    UUID chain from the schematic files, with zero conflicts on a real project
    (mishin‑coil, 331 footprints, 15 groups, 0 conflicts, 0 unresolved UUIDs).

Used only for ClonePlacement.anchor_sheet (narrowing ambiguity of anchor_role) —
not needed at all if anchor_sheet is not used in the config.
"""
import glob
import logging
from pathlib import Path


import sexpdata

from .exceptions import ValidationError, format_fatal_error
from .i18n import _
from .utils.file_cache import cached_file_read

logger = logging.getLogger(__name__)


def _children(node, tag: str) -> list[list]:
    if not isinstance(node, list):
        return []
    return [n for n in node[1:] if isinstance(n, list) and n and str(n[0]) == tag]


def _parse_sheet_uuids_uncached(path: str) -> dict[str, str]:
    """{uuid: Sheetname} from all (sheet ...) blocks of ONE .kicad_sch file.
    The uncached core of _parse_sheet_uuids (2026-08-15, see
    kicadstamp/utils/file_cache.py) — the public name is now a thin
    cached_file_read wrapper so the same .kicad_sch is sexpdata-parsed once
    per mtime, not once per dock that calls load_config() for its
    ctx.sheet_names."""
    result = {}
    try:
        with open(path, encoding='utf-8') as f:
            data = sexpdata.load(f)
    except Exception as e:
        logger.warning(_("Failed to parse {path} as .kicad_sch: {type}: {e} — "
                         "skipped, sheet_name dictionary will be incomplete")
                       .format(path=path, type=type(e).__name__, e=e))
        return result
    for sheet in _children(data, 'sheet'):
        uuid_nodes = _children(sheet, 'uuid')
        uuid_val = str(uuid_nodes[0][1]) if uuid_nodes else None
        name = None
        for prop in _children(sheet, 'property'):
            if len(prop) > 1 and str(prop[1]) == 'Sheetname':
                name = str(prop[2])
        if uuid_val and name:
            result[uuid_val] = name
    return result


def _parse_sheet_uuids(path: str) -> dict[str, str]:
    """{uuid: Sheetname} from all (sheet ...) blocks of ONE .kicad_sch file —
    memoized via cached_file_read (keyed by resolved path + mtime_ns), so a
    project's schematic sheets are parsed once per startup no matter how many
    docks each trigger a load_config() to read ctx.sheet_names."""
    return cached_file_read(Path(path), _parse_sheet_uuids_uncached)


def build_sheet_name_map(config_path: str, schematic_dir: str | None,
                         schematic_files: list[str]) -> dict[str, str]:
    """
    Builds {uuid: Sheetname} from schematic_dir (all *.kicad_sch inside,
    non‑recursively — same as netexp's watchdog) + schematic_files (point
    additions for sheets "on the fringe"). Both paths are relative to the YAML
    config itself (config_path).

    Empty if neither is set — this is NOT an error by itself; it becomes fatal
    later only if anchor_sheet is actually needed and the dictionary is empty
    (see validation.py).
    """
    base = Path(config_path).parent
    files = []

    if schematic_dir:
        d = base / schematic_dir
        if not d.is_dir():
            raise ValidationError(format_fatal_error(
                _("schematic_dir {dir!r} not found").format(dir=schematic_dir),
                [_("expected directory {path} (relative to the config file {config!r})")
                 .format(path=d, config=config_path)]
            ))
        files.extend(glob.glob(str(d / "*.kicad_sch")))

    for extra in (schematic_files or []):
        p = base / extra
        if not p.exists():
            raise ValidationError(format_fatal_error(
                _("schematic_files: file {file!r} not found").format(file=extra),
                [_("expected at {path} (relative to the config file {config!r})")
                 .format(path=p, config=config_path)]
            ))
        files.append(str(p))

    result: dict[str, str] = {}
    for f in files:
        result.update(_parse_sheet_uuids(f))

    if files:
        logger.info(_("sheet_names: scanned {count} .kicad_sch files, "
                      "{sheets} sheets in dictionary")
                    .format(count=len(files), sheets=len(result)))
    return result


def resolve_sheet_path_names(fp, sheet_names: dict[str, str]) -> list[str | None]:
    """
    fp.sheet_path.path[:-1] (without the last — the component's own uuid),
    translated via the dictionary into human‑readable names. None at a position
    if that particular uuid is not found in the dictionary (e.g. schematic_dir
    points elsewhere, or the sheet was renamed/deleted after the dictionary was
    built) — calling code must honestly say "no match", not silently treat None
    as a matching segment.
    """
    path_uuids = list(fp.sheet_path_uuids)
    chain = path_uuids[:-1]
    return [sheet_names.get(u) for u in chain]