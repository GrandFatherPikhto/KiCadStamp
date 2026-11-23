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

The marker's VALUE is reserved (KiCad does not allow "/" in a sheet name): a
sheet that carried it anyway cannot be addressed by its own name, and that case
is WARNED about once by the ONE producer of names (resolve_sheet_path_names).

An OLDER version of the program meeting `sheet "/"` in a profile takes nothing
foreign: the cluster extraction refuses (the Entity is not matched) and the
role resolution refuses on the ambiguity — only "Select cell" can widen to the
whole cluster, exactly as it does for any sheet that matched nothing. That is
why the profile format is NOT bumped; the code is updated on every machine
BEFORE such a profile is opened (Denis, 14.10).
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


def _parse_sheet_blocks_uncached(
        path: str) -> dict[str, tuple[str, str | None]] | None:
    """{uuid: (Sheetname, Sheetfile)} from all (sheet ...) blocks of ONE
    .kicad_sch — the ONE parse (2026-08-15 the map was names-only; the FILE of
    each block came with the nested-instance work,
    plan_2026_11_23_nested_clone_sheet_instances).

    The uncached core, memoized through cached_file_read by `_sheet_blocks`, so
    the same .kicad_sch is sexpdata-parsed ONCE per mtime — the instance
    hierarchy and the name dictionary are two projections of this one read.
    `Sheetfile` is None for a block that carries none (a sheet with no child
    file cannot happen in KiCad, but the parser must not invent one).

    None means «could not be read/parsed». The name dictionary treats that as an
    empty file, exactly as it always did; the instance walk must NOT — a partial
    tree could undercount an instance, so it steps back to Р4 for the WHOLE map
    (Denis, 23.11)."""
    try:
        with open(path, encoding='utf-8') as f:
            data = sexpdata.load(f)
    except Exception as e:
        logger.warning(_("Failed to parse {path} as .kicad_sch: {type}: {e} — "
                         "skipped, sheet_name dictionary will be incomplete")
                       .format(path=path, type=type(e).__name__, e=e))
        return None
    result: dict[str, tuple[str, str | None]] = {}
    for sheet in _children(data, 'sheet'):
        uuid_nodes = _children(sheet, 'uuid')
        uuid_val = str(uuid_nodes[0][1]) if uuid_nodes else None
        name = None
        sheetfile = None
        for prop in _children(sheet, 'property'):
            if len(prop) > 2 and str(prop[1]) == 'Sheetname':
                name = str(prop[2])
            elif len(prop) > 2 and str(prop[1]) == 'Sheetfile':
                sheetfile = str(prop[2])
        if uuid_val and name:
            result[uuid_val] = (name, sheetfile)
    return result


def _sheet_blocks(path: str) -> dict[str, tuple[str, str | None]] | None:
    """`_parse_sheet_blocks_uncached` memoized (resolved path + mtime_ns);
    None = the file could not be read/parsed (see the core's docstring)."""
    return cached_file_read(Path(path), _parse_sheet_blocks_uncached)


def _names_of(blocks: dict[str, tuple[str, str | None]] | None) -> dict[str, str]:
    """{uuid: Sheetname} — the names-only PROJECTION of the one parse above."""
    return {uuid: name for uuid, (name, _sheetfile) in (blocks or {}).items()}


def _parse_sheet_uuids_uncached(path: str) -> dict[str, str]:
    return _names_of(_parse_sheet_blocks_uncached(path))


def _parse_sheet_uuids(path: str) -> dict[str, str]:
    """{uuid: Sheetname} from all (sheet ...) blocks of ONE .kicad_sch file —
    memoized via cached_file_read (keyed by resolved path + mtime_ns), so a
    project's schematic sheets are parsed once per startup no matter how many
    docks each trigger a load_config() to read ctx.sheet_names."""
    return _names_of(_sheet_blocks(path))


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


class SheetHierarchy:
    """The .kicad_sch tree walked from ONE root file — WITH multiplicities.

    ``kicadstamp/schematic_discovery.walk_schematic_hierarchy`` cannot serve here:
    it dedups diamonds ON PURPOSE («which files are reachable»), while an INSTANCE
    is a PLACE — the same file standing under two sheets is two instances
    (Signal In A / Signal In B of HP-ISO-Channel-v099). This class answers the
    plan's Р1, `экз(X, F)`: `instances(file, target)` is how many times the
    subtree of `file` contains `target`, memoized per file.

    Two maps, and the difference matters (Р1 is written in terms of them):

      * ``block_file`` — `файл(блок)`: the SHEET FILE the block points at, i.e.
        its ``Sheetfile`` resolved against the directory of the file the block
        lives in. THIS is what `F` of Р1 comes from;
      * ``block_parent`` — the file the block itself lives in (where it was
        parsed from), for diagnostics. It is NOT `файл(блок)`."""

    def __init__(self, root: str, children: dict[str, tuple[str, ...]],
                 block_file: dict[str, str], block_parent: dict[str, str]):
        self.root = root
        self.children = children
        self.block_file = block_file
        self.block_parent = block_parent
        self._reach: dict[str, dict[str, int]] = {}

    def _reach_from(self, file: str, chain: tuple[str, ...] = ()) -> dict[str, int]:
        cached = self._reach.get(file)
        if cached is not None:
            return cached
        counts = {file: 1}
        for child in self.children.get(file, ()):
            if child in chain or child == file:
                # A cycle is invalid in KiCad; break it instead of recursing
                # forever. The memo below is therefore a LOWER bound on such a
                # (broken) schematic — the one place this walk may undercount.
                continue
            for name, count in self._reach_from(child, chain + (file,)).items():
                counts[name] = counts.get(name, 0) + count
        self._reach[file] = counts
        return counts

    def instances(self, file: str | None, target: str) -> int:
        """``экз(file, target)`` — 0 for an unknown file, never a guess."""
        if not file or not target:
            return 0
        return self._reach_from(file).get(target, 0)


def sheet_map(sheet_names):
    """The map ITSELF when it is one — never a copy, never a rebuild.

    A `dict(sheet_names)` on the way to a caller silently DROPS the instance
    hierarchy (`LazySheetNameMap.hierarchy`), and the caller then falls back to
    the first-segment rule with nothing failing — that is mutation row
    «иерархия теряется по дороге к вызывающему». None -> {}."""
    return {} if sheet_names is None else sheet_names


def sheet_map_is_empty(sheet_names) -> bool:
    """`not sheet_names` made HONEST for the lazy map: its `__bool__` is always
    True by design (that means "do not force a parse", not "not empty"). Use
    this where the caller asks "is the dictionary really empty?"."""
    if not sheet_names:
        return True
    return len(sheet_names) == 0


def build_sheet_hierarchy(root_sheet: str | None) -> SheetHierarchy | None:
    """The walk from the ROOT .kicad_sch over ``(property "Sheetfile" ...)``.

    None means «no hierarchy»: the callers then fall back to the OLD
    first-segment rule (Р4). Two ways to get it — no root configured, or ANY file
    of the tree missing. The second one is Denis's rule (23.11): a PARTIAL tree
    could undercount an instance and hand out a wrong address silently, so the
    whole map steps back and ONE Log line names the file that was not found.

    ``Sheetfile`` is resolved against the CURRENT file's own directory (the rule
    `kicadstamp/schematic_discovery` and `config/includes.py` already follow) and
    compared by RESOLVED path, never by file name: a subdirectory, a relative
    hop or a different case on a case-insensitive filesystem must not split one
    file into two."""
    if not root_sheet:
        return None
    root = Path(root_sheet).resolve()
    if not root.is_file():
        _warn_no_hierarchy(root_sheet)
        return None
    children: dict[str, tuple[str, ...]] = {}
    block_file: dict[str, str] = {}
    block_parent: dict[str, str] = {}
    pending = [root]
    seen: set[str] = set()
    while pending:
        path = pending.pop()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        blocks = _sheet_blocks(key)
        if blocks is None:
            _warn_no_hierarchy(key)
            return None
        kids: list[str] = []
        for uuid, (_name, sheetfile) in blocks.items():
            block_parent[uuid] = key
            if not sheetfile:
                continue
            child = (path.parent / sheetfile).resolve()
            if not child.is_file():
                _warn_no_hierarchy(str(child))
                return None
            block_file[uuid] = str(child)     # файл(блок) — F of Р1
            kids.append(str(child))
            pending.append(child)
        children[key] = tuple(kids)
    return SheetHierarchy(str(root), children, block_file, block_parent)


def _warn_no_hierarchy(file: str) -> None:
    """ONE Log line, no modal window (Р4, Denis 23.11): whatever made the tree
    incomplete — a missing file or one that cannot be parsed — the answer steps
    back to the old rule for the WHOLE map, so a partial tree can never
    undercount an instance silently."""
    logger.warning(_("sheet hierarchy: {file} not found — instances fall back "
                     "to the first sheet").format(file=file))


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
                 schematic_files: list[str], root_sheet: str | None = None):
        self._config_path = config_path
        self._schematic_dir = schematic_dir
        self._schematic_files = schematic_files
        self._root_sheet = root_sheet
        self._cache: dict[str, str] | None = None
        self._hierarchy: SheetHierarchy | None = None
        self._hierarchy_built = False

    @property
    def hierarchy(self) -> SheetHierarchy | None:
        """The instance tree from the profile's ``root_sheet``, or None (Р4).

        Built on FIRST access (one walk, the .kicad_sch reads shared with the name
        dictionary through `_sheet_blocks`), so a caller that never asks about an
        instance pays nothing — the same laziness the name map itself keeps."""
        if not self._hierarchy_built:
            self._hierarchy = build_sheet_hierarchy(self._root_sheet)
            self._hierarchy_built = True
        return self._hierarchy

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
    return _chain_and_names(fp, sheet_names)[1]


def _block_uuids(fp) -> list[str]:
    """The sheet-path chain WITHOUT the symbol's own last hop — the same list
    `resolve_sheet_path_names` names, read through the ONE shared reader."""
    return list(sheet_path_uuids_of(fp))[:-1]


def _chain_and_names(fp, sheet_names) -> tuple[list[str], list[str | None]]:
    """(uuid chain, resolved names) from ONE read of the footprint's path — the
    instance rule needs the uuids, its callers need the names, and the raw read
    must not be done twice."""
    chain = _block_uuids(fp)
    names = [sheet_names.get(u) for u in chain]
    _warn_reserved_sheet_name(names)
    return chain, names


# The ONE marker of the root sheet (plan_2026_10_13_root_sheet_clusters, Ч1).
# Its VALUE is reserved: KiCad does not allow "/" in a sheet name (Denis, 14.10),
# and a sheet that carried it anyway could not be addressed by its own name any
# more — sheet_in_path would read the marker as the root and sheet_of_names would
# report it as the instance identity. That case is WARNED about once, by the ONE
# producer of names (see _warn_reserved_sheet_name), never swallowed silently.
ROOT_SHEET = "/"

# True once the reserved-name warning has been written. A full-board read calls
# resolve_sheet_path_names PER FOOTPRINT, so an undeduped line would number in
# the hundreds for one click; one honest line per process names the fact (a
# module global so a test can reset it).
_reserved_name_warned = False


def _warn_reserved_sheet_name(names) -> None:
    """Say ONCE, loudly, that a schematic sheet is literally named ROOT_SHEET —
    the marker's value is reserved (see the constant above). Silent when there is
    no such name, which is every real profile."""
    global _reserved_name_warned
    if _reserved_name_warned or ROOT_SHEET not in names:
        return
    _reserved_name_warned = True
    logger.warning(_(
        "a schematic sheet is named {marker!r} — that value is reserved for the "
        "ROOT sheet of the hierarchy, so this sheet can no longer be addressed "
        "by its own name (its chain: {chain!r})").format(
            marker=ROOT_SHEET, chain=list(names)))


def is_root_sheet(sheet) -> bool:
    """True when `sheet` is the ROOT_SHEET marker ("/") — the ONE test every
    caller uses instead of comparing the literal itself."""
    return sheet == ROOT_SHEET


def empty_dictionary_hint(schematic_dir, schematic_files) -> str:
    """The ONE wording for "the sheet-name dictionary is empty" — its two
    causes are DIFFERENT facts and must not share one sentence (2026-10-21,
    plan_2026_10_21_root_sheet_anchor_on_flat_schematic):

      * neither schematic_dir nor schematic_files is configured -> the map
        could not have been built at all; the config lacks its source;
      * they ARE configured and were read, yet the map holds 0 sheets -> the
        schematic simply has no sub-sheets (single-page), so a sheet name
        other than the ROOT_SHEET marker can never match anything. The old
        "you need schematic_dir" text was a lie exactly here — live proof:
        profiles/Test04/logs/actions.log printed "scanned 1 .kicad_sch files,
        0 sheets in dictionary" right beside it.

    The ROOT marker is neither cause's business (is_root_sheet): it names the
    EMPTY chain, which needs no dictionary at all — that is why it must not be
    refused when the map is empty.

    Called by validation.check_anchor_sheet_configured (the fatal's hint line)
    and by sheet_narrowing_disabled_line below, so the two doors can never tell
    the user two different stories. Takes the two VALUES rather than a Config
    on purpose: this module must not import the config package."""
    if schematic_dir or schematic_files:
        return _("the schematic has no sub-sheets (it is a single-page "
                 "schematic), so a sheet name other than the root sheet can "
                 "never be found — remove anchor_sheet, or use {root} for the "
                 "root sheet").format(root=ROOT_SHEET)
    return _("you need schematic_dir (or schematic_files) at the root of the "
             "config — path to the folder with *.kicad_sch files, relative to "
             "this YAML")


def sheet_narrowing_disabled_line(schematic_dir, schematic_files) -> str:
    """The GUI's ONE Log line for "sheet names are unavailable" — the V.3 line
    gui/dock_hub._warn_if_sheet_narrowing_disabled prints once per root. It
    lives HERE so the dock keeps only the call (the dock is a giant) and so the
    sentence is testable without a Qt window; the CAUSE still comes from
    empty_dictionary_hint, the one owner of that distinction."""
    return _("sheet names cannot be resolved, so sheet-based narrowing "
             "(Extract cluster/tree, anchor_sheet) cannot work: {hint}"
             ).format(hint=empty_dictionary_hint(schematic_dir, schematic_files))


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
    """The OLD (Р4) instance-identity of a resolved sheet chain: the first named
    segment. Since plan_2026_11_23_nested_clone_sheet_instances the ONE owner of
    a component's address is `instance_sheet_of(fp, sheet_names)` — it needs the
    uuids, and a clone nested inside a clone cannot be told apart by names
    alone. This function stays as its fallback and for callers that really have
    nothing but a chain (a plain dict without a hierarchy, a stored record):

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


def instance_sheet_of(fp, sheet_names) -> str | None:
    """THE instance address of the sheet `fp` stands on (Р1/Р2/Р4 of
    plan_2026_11_23_nested_clone_sheet_instances) — the ONE function a caller
    with a footprint must use instead of
    `sheet_of_names(resolve_sheet_path_names(fp, sheet_names))`: the uuids are
    what the count needs, and they are lost as soon as only the names remain.

    Р1: the address is the SHORTEST chain prefix b1…bk whose sheet file occurs
    EXACTLY ONCE under the file of that block, i.e. the first k with
    `экз(файл(bk), файл(bn)) == 1`. Р2: those names joined by "/". The search
    always stops: `экз(F, F) == 1`. When the first sheet already has one copy
    the answer is one name — byte for byte what the old rule said, which is why
    every profile where clones sit at the root (Channel_0/DAC) reads as before.

    Р4 — the answer changes ONLY inside a real hierarchy:

      * a plain dict (tests, CLI without schematic_dir) or a tree the walk
        refused -> `sheet_of_names(names)`, the old first-segment rule;
      * a block of the chain that the tree does not know, or a prefix segment
        the dictionary cannot name -> also the old rule: inventing a path from
        an unknown uuid would be worse than saying less."""
    sheet_names = sheet_map(sheet_names)
    chain, names = _chain_and_names(fp, sheet_names)
    hierarchy = getattr(sheet_names, "hierarchy", None)
    if hierarchy is None:
        return sheet_of_names(names)
    address = _address_of(chain, hierarchy, sheet_names)
    return address if address is not None else sheet_of_names(names)


def _address_of(chain: Iterable[str], hierarchy: SheetHierarchy,
                sheet_names) -> str | None:
    """The Р1/Р2 address of ONE chain of sheet blocks, or None when the tree
    cannot answer (a block missing from it, or a segment the dictionary cannot
    name) — the caller then says less (Р4) instead of inventing a path.

    An EMPTY chain is not «no instance»: it is the ROOT sheet, and
    `sheet_of_names` is the one place that says so."""
    chain = tuple(chain)
    if not chain:
        return None
    files = [hierarchy.block_file.get(uuid) for uuid in chain]
    if any(file is None for file in files):
        return None
    target = files[-1]
    for index, file in enumerate(files):
        if hierarchy.instances(file, target) != 1:
            continue
        prefix = [sheet_names.get(uuid) for uuid in chain[:index + 1]]
        if any(not segment for segment in prefix):
            return None
        return "/".join(prefix)
    return None


def address_chain(address: str | None) -> tuple[str, ...]:
    """An ADDRESS written for people («ISO In/Signal In A», "/") as the chain of
    sheet names `sheet_in_path` compares against.

    ROOT_SHEET gives the EMPTY chain — exactly the chain a symbol on the root
    sheet has — and every other address its non-empty parts. This is the ONE
    place an address is turned back into a chain: the callers that compare an
    ADDRESS with a chain (`tree_from_selection.find_entity_for_source`,
    `imprint_apply.plan_imprint`) used to hand-roll it, and a `(sheet,)` or a
    local `split("/")` turned a nested address into ONE link — so a
    multi-segment address silently matched nothing, not even itself."""
    if not address or address == ROOT_SHEET:
        return ()
    return tuple(part for part in str(address).split("/") if part)


def all_instance_addresses(sheet_names) -> list[str]:
    """Every INSTANCE ADDRESS of the sheet tree, parents first then sorted —
    for the GUI's sheet combos, so «ISO In/Signal In A» can be PICKED and not
    only typed. Empty without a hierarchy (Р4: the old single names are what
    the dictionary already gives the caller)."""
    hierarchy = getattr(sheet_names, "hierarchy", None)
    if hierarchy is None:
        return []
    by_parent: dict[str, list[str]] = {}
    for uuid, parent in hierarchy.block_parent.items():
        by_parent.setdefault(parent, []).append(uuid)
    out: list[str] = []

    def walk(file: str, chain: tuple[str, ...], ancestors: tuple[str, ...]):
        for uuid in by_parent.get(file, ()):
            child = hierarchy.block_file.get(uuid)
            if child is None or child in ancestors:
                continue          # no child file / an invalid cycle
            path = chain + (uuid,)
            address = _address_of(path, hierarchy, sheet_names)
            if address:
                out.append(address)
            walk(child, path, ancestors + (child,))

    walk(hierarchy.root, (), (hierarchy.root,))
    return sorted(set(out))


def sheet_combo_names(sheet_names) -> list[str]:
    """Every name a Sheet combo should offer: the dictionary's own sheet names
    PLUS the instance addresses of the hierarchy (`all_instance_addresses`), so
    «ISO In/Signal In A» can be PICKED and not only typed.

    THE one list for every combo — `collect_all_sheet_names` (the rename /
    points / vias / node forms) and the Create-entity form both come here; a
    second copy of this union is exactly how the two would drift apart."""
    names = set(sheet_names.values()) if sheet_names is not None else set()
    names.update(all_instance_addresses(sheet_names))
    return sorted(names)


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
      * a MULTI-SEGMENT address ("ISO In/Signal In A", Р3 of
        plan_2026_11_23_nested_clone_sheet_instances) matches when the chain
        STARTS with exactly those segments; deeper segments may follow (a
        component inside `Signal In A` is still its instance), but the same
        names under another parent must NOT match. ONE name keeps its old
        "any segment" meaning, so every existing profile reads as before —
        KiCad forbids "/" inside a sheet name, which makes the record
        unambiguous;
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
    if "/" in sheet:
        wanted = tuple(part for part in sheet.split("/") if part)
        if not wanted:
            return False
        return len(names) >= len(wanted) and all(
            names[i] == part for i, part in enumerate(wanted))
    return any(name == sheet for name in names)