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

Used for ClonePlacement.anchor_sheet (narrowing ambiguity of anchor_role) and —
since 2026-10-13/14 — for the (Cluster, Sheet) instance addressing of the whole
project (role_narrowing, reead, selection_narrowing, internode_capture): this
module owns BOTH the chain resolution and the ONE "does this sheet name this
instance" predicate, so no caller re-implements either.

ROOT_SHEET ("/") — the ONE marker of the ROOT sheet of the hierarchy
(plan_2026_10_13_root_sheet_clusters Ч1): a symbol on the root sheet has an
EMPTY chain (resolve_sheet_path_names drops the symbol's own uuid, and there is
nothing above it), and that emptiness used to be indistinguishable from "the
sheet did not resolve", which dropped every root cluster. An ABSENT sheet (None
/ "") stays what it always was — "not set", no narrowing — so the marker is the
only way to say "the root instance" explicitly.
"""
import glob
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path

import sexpdata

from .exceptions import ValidationError, format_fatal_error
from .field_overrides import sheet_path_uuids_of
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


class LazySheetNameMap(Mapping):
    """``{uuid: Sheetname}`` computed lazily on first access.

    ``load_config()`` installs this instead of eagerly parsing every
    ``*.kicad_sch`` (sexpdata — ~0.49s for a real project): the parse now
    happens only when a caller actually needs a sheet name (anchor_sheet/sheet
    narrowing, a GUI sheet combo, validation). It is deliberately ALWAYS
    truthy, so the ubiquitous ``sheet_names or {}`` fallbacks never force a
    parse; use ``len()``/``get()``/iteration (or ``dict(sheet_names)``) to
    inspect it, which materialises the map.
    """

    def __init__(self, config_path: str, schematic_dir: str | None,
                 schematic_files: list[str]):
        self._config_path = config_path
        self._schematic_dir = schematic_dir
        self._schematic_files = schematic_files
        self._cache: dict[str, str] | None = None

    def _data(self) -> dict[str, str]:
        if self._cache is None:
            self._cache = build_sheet_name_map(
                self._config_path, self._schematic_dir, self._schematic_files)
        return self._cache

    def __getitem__(self, key: str) -> str:
        return self._data()[key]

    def __iter__(self):
        return iter(self._data())

    def __len__(self) -> int:
        return len(self._data())

    def __bool__(self) -> bool:
        # Without this, Python falls back to __len__() for truthiness, which
        # materialises the map — defeating the "sheet_names or {}" fallback
        # this class exists to keep lazy (see the class docstring above).
        return True

    def __eq__(self, other) -> bool:
        if isinstance(other, LazySheetNameMap):
            return self._data() == other._data()
        if isinstance(other, dict):
            return self._data() == other
        return NotImplemented

    def __repr__(self) -> str:
        return repr(self._data())


def resolve_sheet_path_names(fp, sheet_names: dict[str, str]) -> list[str | None]:
    """
    The footprint's sheet-path uuid chain WITHOUT its last hop (the component's
    own uuid), translated via the dictionary into human‑readable names. None at a
    position if that particular uuid is not found in the dictionary (e.g.
    schematic_dir points elsewhere, or the sheet was renamed/deleted after the
    dictionary was built) — calling code must honestly say "no match", not
    silently treat None as a matching segment.

    The chain is read through the ONE shared reader
    (kicadstamp.field_overrides.sheet_path_uuids_of), so this function accepts
    both footprint shapes (the domain one the adapter hands over, and kipy's raw
    one) instead of naming an attribute itself — 2026-09-20, plan_2026_09_20_
    symbol_uuid_wrong_attribute.md: a hand-rolled read of a shape is exactly how
    the store lost its key for a week."""
    path_uuids = list(sheet_path_uuids_of(fp))
    chain = path_uuids[:-1]
    return [sheet_names.get(u) for u in chain]


# The ONE marker of the root sheet (plan_2026_10_13_root_sheet_clusters, Ч1).
ROOT_SHEET = "/"


def is_root_sheet(sheet) -> bool:
    """True when `sheet` is the ROOT_SHEET marker ("/") — the ONE test every
    caller uses instead of comparing the literal itself."""
    return sheet == ROOT_SHEET


def instance_label(name: object, sheet: object) -> str:
    """`"<name>/<sheet>"` — THE one way an instance's (Cluster, Sheet) pair is
    written for people: the explode plan's rows and their `touches`, the CLI's
    labels, the inter-node node keys and the names derived from them.

    An ABSENT sheet contributes nothing, exactly as before. The ROOT_SHEET marker
    contributes nothing EITHER: "<cluster>//" is noise, and there is exactly ONE
    root sheet of the hierarchy, so the cluster alone already names that instance
    (plan_2026_10_13_root_sheet_clusters Ч1)."""
    if not sheet or sheet == ROOT_SHEET:
        return str(name)
    return f"{name}/{sheet}"


def sheet_display(sheet, absent: str = "—") -> str:
    """The sheet ALONE as a label for people — the ONE wording host for the three
    states an instance's sheet can be in (the instance picker's rows, the Entity
    page's own Sheet field):

      * ROOT_SHEET ("/") -> "(root sheet)": the marker is the config's VALUE, not
        something to show a user; the root is the one sheet with no name;
      * an absent sheet -> `absent` (the caller's own wording: the Entity page
        shows "—", the picker says "(no sheet)");
      * anything else -> the name itself."""
    if is_root_sheet(sheet):
        return _("(root sheet)")
    return str(sheet) if sheet else absent


def sheet_of_names(names: Iterable[str | None]) -> str | None:
    """The INSTANCE-identity of a resolved sheet chain — the ONE owner of that
    derivation (gui/docks/reead.sheet_of and
    selection_narrowing._sheet_identity both delegate here, so the two can never
    group a selection differently):

      * an EMPTY chain (no entries at all) = the symbol stands on the ROOT
        sheet -> ROOT_SHEET. This is exactly what resolve_sheet_path_names
        returns for a root symbol, map or no map — the depth is a property of
        the symbol's own path, not of the name dictionary;
      * else the FIRST named segment (the channel identity: Channel_0/DAC);
      * else (entries present, none named) the chain genuinely did NOT resolve
        -> None, honestly, never ROOT_SHEET. Denis's rule: "не разрешилось" is
        only when a uuid IS in the chain and the dictionary cannot name it.

    `names is None` (the caller has NO chain at all — an unknown input, not an
    empty chain) is also None: unknown must not pass for the root."""
    if names is None:
        return None
    names = tuple(names)
    if not names:
        return ROOT_SHEET
    for seg in names:
        if seg:
            return seg
    return None


def sheet_in_path(names: Iterable[str | None], sheet: str | None) -> bool:
    """True when `sheet` names the instance standing on the resolved chain
    `names`. THE one rule for "does this sheet name this component", shared by
    the role-narrowing cascade (role_narrowing._fp_on_sheet, i.e. every
    anchor_role/sheet search), the inter-node copper capture's node matching
    (internode_capture), the cluster extraction (gui/docks/reead) and the
    address comparison (selection_narrowing) — the callers used to answer the
    same question in several different ways (leaf segment vs any segment), and a
    tree's anchor resolved while its copper did not.

      * ROOT_SHEET ("/") names the ROOT instance — and ONLY an EMPTY chain is
        root: resolve_sheet_path_names returns [] for a symbol on the root sheet
        and [None] (one entry, unnamed) for a sub-sheet whose uuid the
        dictionary cannot name. That distinction is what keeps a root cluster
        from swallowing a same-tagged sub-sheet cluster (and vice versa);
      * any other name is ONE OF THE SEGMENTS, not necessarily the LAST one: the
        component may live deeper than the sheet we are looking for (a DAC
        sub-sheet inside Channel_0 is still Channel_0's);
      * a None/empty `sheet` names nothing and matches nothing: "no sheet given"
        is NOT a wildcard and NOT the root. Callers that mean "any sheet" (a
        config entry with no sheet at all) decide that themselves, explicitly;
      * `names is None` (no chain at all — an unknown input) names nothing
        either: only a real EMPTY chain is the root."""
    if not sheet or names is None:
        return False
    names = tuple(names)
    if sheet == ROOT_SHEET:
        return not names
    return any(name == sheet for name in names)