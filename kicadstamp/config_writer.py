# kicadstamp/config_writer.py
"""Pure read-merge-write config helpers for the GUI docks' write paths —
moved out of gui/docks/_common.py (Phase 2 of the gui god-file decomposition,
see techdocs/handoff/handoff_2026_08_05_architecture_fixes_roadmap.md):
these are plain file operations with no Qt dependency, so they belong in
core. gui/docks/_common.py is now a thin facade re-exporting them, so every
existing importer keeps working unchanged.

The read here deliberately does NOT swallow exceptions (unlike
gui/config_io.load_data, which is for read-only browsing) — these helpers are
on the docks' WRITE path, where a broken file must surface as an OSError the
caller turns into an on-screen error message.
"""
import copy
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from kicadstamp.config.aliases import normalize_section_aliases
from kicadstamp.config.format_version import (
    VERSION_KEY,
    current_format,
    lift_loaded_dict,
    read_version,
)
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.utils.safe_write import backup_file, write_text_atomic
from kicadstamp.exceptions import (
    ValidationError,
    unknown_extension_config_error,
    yaml_removed_config_error,
)
from kicadstamp.i18n import _
from kicadstamp.utils.file_cache import cached_file_read, invalidate_graph_path, invalidate_path
from .config_working_set import WORKING_SET

logger = logging.getLogger(__name__)

# Project root — used by display_path() below to show paths relative to it
# when possible. Used to also be where FilePickerDock's file-tree was
# rooted (gui/docks/file_picker.py, removed 2026-08-03 — see
# handoff_2026_08_03_gui_tree_risks_resolved.md — replaced by ConfigTreeDock's
# "Open Root file" action, a plain QFileDialog with no directory browser).
# NOTE: parents[1] here (not parents[2] like the old gui/docks/_common.py) —
# this file lives one level deeper: kicadstamp/config_writer.py.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

def _raise_unsupported_config_format(path: Path, suffix: str) -> None:
    """Raise the fatal for a config file whose format the core no longer
    supports, wrapped in OSError with the ValidationError kept as __cause__
    (2026-08-28 — fix for a live bug: a bare ValidationError escaped the GUI
    docks' `except OSError` around read_data/write_data and would crash a Qt
    slot; _read_data's docstring documents the same class of incident as
    already hit live 2026-08-04). .yaml/.yml get the dedicated "YAML removed
    — convert with sexp_config_convert.py" message; anything else gets the
    generic unrecognized-extension message."""
    if suffix in (".yaml", ".yml"):
        cause = yaml_removed_config_error(path)
    else:
        cause = unknown_extension_config_error(path, suffix)
    raise OSError(str(cause)) from cause


def _read_data(path: Path) -> dict:
    """Read an existing config file's YAML/JSON content (or {} when it
    doesn't exist yet). Raises OSError on read/parse errors instead of
    returning {} — the merge-write helpers are on the docks' write path,
    where a broken file must surface to the user, not be silently treated
    as empty (unlike gui/config_io.load_data). FIXED (2026-08-04): a malformed
    file used to raise the raw yaml.YAMLError/json.JSONDecodeError instead
    — neither is an OSError, so every caller's `except OSError` (e.g.
    PlacerDock._do_save's, written against exactly this docstring's
    promise) missed it, and the raw exception propagated uncaught out of a
    Qt slot, which PyQt6 aborts the whole process on by default. Found
    live: Placer's Save crashed the entire GUI over one stray character in
    an unrelated part of the target YAML file.

    The read+parse itself goes through cached_file_read (2026-08-15, see
    kicadstamp/utils/file_cache.py) so the docks' repeated read-merge-write
    cycles on the same file — and the collectors that read every graph file
    once per dock — parse it from disk ONCE, not once per call. Contract is
    UNCHANGED: {} for a missing file, OSError — never ValidationError — on
    a malformed file. Missing-file is handled here, before the cache, so a
    file that doesn't exist yet is never cached as "absent forever" and
    appears on the next call once it's created.

    Format is selected by file extension (2026-08-28, core_yaml_removal —
    YAML support was removed from the config graph entirely): .json -> JSON,
    .sexp -> s-expr, anything else (including legacy .yaml/.yml) -> fatal
    OSError (ValidationError as __cause__) — .yaml/.yml with the dedicated
    "convert with sexp_config_convert.py" message, any other extension with
    the unrecognized-extension message."""
    # Staged mode (2026-09-01, plan project_save_model): a dirty file's content
    # lives in the working set, not on disk — this also covers to-be-created
    # (__new__) files that don't exist yet.
    if WORKING_SET.enabled:
        staged = WORKING_SET.staged_content(str(path.resolve()))
        if staged is not None:
            return copy.deepcopy(staged)
    if not path.exists():
        return {}

    def _uncached_read(p: Path) -> dict:
        suffix = p.suffix.lower()
        # EACH BRANCH LIFTS EXACTLY ONCE, and it lifts at the parse site.
        #
        # Д1 (Т2 acceptance): this used to run ONE shared parser and then wrap
        # the result in lift_loaded_dict for both formats. For .sexp the parser
        # had ALREADY taken the number out and lifted the content, so the
        # wrapper read the numberless dict as "format 1" and ran the whole chain
        # a SECOND time — a file already at the current format went through
        # 1 -> 2 and 2 -> 3 all over again (measured: diagnostics/
        # probe_format_double_lift.py). Invisible at 1 -> 2 (identity); the
        # zero-origin step would add the body angle twice and move the board —
        # and this is the GUI docks' read-merge-write path, so the doubly-lifted
        # content reaches the disk.
        if suffix == ".json":
            kind = "JSON"

            def parser(f):
                # A raw JSON dict has no parse layer of its own, so the lift
                # belongs here. normalize_section_aliases runs FIRST, like
                # includes._load_config_file does, so a step sees canonical keys
                # (legacy `rules:` -> `chains:`, 2026-09-01 rename).
                return lift_loaded_dict(
                    normalize_section_aliases(json.load(f) or {}), str(p))
        elif suffix == ".sexp":
            kind = "s-expr"

            def parser(f):
                # sexp_to_dict applies the aliases (apply_aliases default True)
                # AND lifts; path= names the FILE in a "format too new"
                # refusal, and it is the step context (a step needing the
                # profile path refuses a bare "<config>").
                return sexp_to_dict(f.read(), path=str(p))
        else:
            _raise_unsupported_config_format(p, suffix)
        try:
            with open(p, "r", encoding="utf-8") as f:
                return parser(f)
        except (json.JSONDecodeError, ValidationError) as e:
            raise OSError(_("{path} is not valid {kind}: {error}").format(
                path=path, kind=kind, error=e)) from e

    return cached_file_read(path, _uncached_read)


def _serialize(path: Path, data: dict,
               format_number: int | None = None) -> str:
    """Serialize `data` to the text form for `path`'s extension — shared by
    write_config_file() below and by the working set's atomic flush (which
    writes to a temp sibling then os.replace(), see
    kicadstamp/config_working_set.py). .json -> JSON, .sexp -> s-expr,
    anything else -> fatal OSError (ValidationError as __cause__), raised
    BEFORE any file is opened.

    BOTH formats stamp the FORMAT number here, so a flushed file is born in the
    current format exactly like a directly written one: the s-expr side through
    dict_to_sexp (which does it itself), the JSON side right here — the readers
    of both formats are the same readers, so the two must not disagree.
    `format_number=None` means CURRENT_FORMAT; the raw converters are the only
    callers that pass their own, and they do not come through here."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        # current_format() at CALL time — see format_version.current_format for
        # why the constant must not be bound by a from-import.
        number = current_format() if format_number is None else format_number
        # The number is FIRST, and it is never taken from `data`: a `version`
        # key already in the dict is dropped, so it can neither be duplicated
        # nor win over the parameter (the same rule as dict_to_sexp).
        out = {VERSION_KEY: number}
        out.update({k: v for k, v in data.items() if k != VERSION_KEY})
        return json.dumps(out, indent=2, ensure_ascii=False, sort_keys=False)
    if suffix == ".sexp":
        return dict_to_sexp(data, format_number)
    _raise_unsupported_config_format(path, suffix)


def serialize_config(path: Path | str, data: dict,
                     format_number: int | None = None) -> str:
    """The PUBLIC name of the ONE serializer (`_serialize`).

    For a caller that must look at the exact text it is about to write BEFORE
    writing anything: the on-disk upgrade sweep (Т4/У2) re-parses it and refuses
    to write when the round trip does not come back as the lifted content. Same
    function, so the text that was checked and the text that gets written cannot
    drift apart."""
    return _serialize(Path(path), data, format_number=format_number)


def write_config_file(path: Path, data: dict, *,
                      format_number: int | None = None,
                      always_backup: bool = False,
                      backup: bool = True,
                      serialized_text: str | None = None) -> None:
    """Write ONE config file — the single place the `.bak` contract lives.

    The rule (Denis, 24.09.2026): when the file on disk is still an OLDER
    format, take `backup_file(path)` FIRST, then write. That is the one write
    that is not just an edit — the reader lifts the content in memory, so once
    the new bytes land the old-format content survives nowhere else, and the
    `.bak` is the only way back. An ordinary edit of a current-format file
    needs no copy from here (the existing conventions — the delete/rename flows,
    the tree converter — take their own).

    Putting it in ONE function is deliberate, for the same reason the read side
    lifts by default: three read-modify-write paths plus the GUI save plus the
    on-disk upgrade (Т4) all need it, and a fourth path added tomorrow must not
    be able to forget. `dict_to_sexp` could not own this: it is a pure
    serialization to a string and knows no path, and half its callers write
    nothing.

    The write is atomic (`write_text_atomic`), so a crash or a full disk leaves
    the target with its previous bytes, and `newline=""` keeps the output
    byte-identical across platforms. Both cache layers are dropped afterwards
    (mtime alone cannot separate two writes microseconds apart on a coarse-timer
    filesystem — see kicadstamp/utils/file_cache.py).

    A file that has become NEWER underneath us (another machine lifted it and
    Syncthing delivered it) raises instead of being overwritten: our content is
    older, so writing would destroy what we do not know. The ValidationError is
    wrapped in OSError for the same reason `_read_data` wraps its parse errors —
    a raw ValidationError escaping into a Qt slot aborts PyQt6 (measured, see
    the module docstring).

    `always_backup=True` is for a caller that changes CONTENT irreversibly, not
    the format — a one-time migration tool. Such a caller must be reversible
    whatever the file's format is, so it asks for the copy unconditionally
    instead of taking its own (which would put TWO copies of one file next to
    it, measured in test_migrate_legacy_pad_anchors).

    `backup=False` is for a caller that has ALREADY taken a copy of the previous
    bytes — the GUI's global Save (ConfigWorkingSet.flush copies every dirty file
    into the project's `.history/` before writing). It still refuses a file that
    became newer and still writes atomically; it just does not leave a SECOND
    copy of the same bytes beside the file.

    Order (Д5 of the Т3 acceptance): SERIALIZE first, then the copy, then the
    write. A target with a foreign extension (`old.yaml`) is refused by the
    serializer — and with the old order the `.bak` had already been taken for a
    write that never happened.

    `serialized_text` is for a caller that must look at the exact bytes BEFORE
    writing them and then write THOSE bytes: the on-disk upgrade sweep (Т4/У2)
    serializes once, re-parses that text to prove the round trip keeps the
    meaning, and hands it here. Without it the check would gate one text while
    the write produced another (two serializations of the same data are equal
    today, so a bug between them would be invisible — measured 25.09.2026: the
    mutation «lift by inserting the version line» survived until this went in).
    The text MUST come from `serialize_config`, so the one serializer still owns
    the bytes and the `.bak`/atomic/invalidation contract below is unchanged."""
    target = Path(path)
    text = (serialized_text if serialized_text is not None
            else _serialize(target, data, format_number=format_number))
    if target.exists():
        try:
            stale = read_version(target) < current_format()
        except ValidationError as e:
            raise OSError(str(e)) from e
        if (stale or always_backup) and backup:
            backup_file(target)
    write_text_atomic(target, text)
    invalidate_path(target)
    invalidate_graph_path(target)


def _write_data(path: Path, data: dict) -> None:
    """Write merged content back in the same format (JSON/s-expr by file
    extension) it was read in. Every GUI dock write path
    (merge_write/add_list_entry/upsert_*/_remove_entry) funnels through
    this ONE chokepoint, and the physical part — the atomic write, the `.bak`
    rule and the BOTH-cache invalidation — now lives in write_config_file(),
    so every other writer in the codebase gets the same contract instead of
    having to remember it.

    Format is selected by file extension, symmetric to _read_data (2026-08-28,
    core_yaml_removal — YAML support removed): .json -> JSON, .sexp -> s-expr,
    anything else -> fatal OSError (ValidationError as __cause__), raised
    BEFORE the file is opened, so a bad extension never creates an empty file
    on disk.

    STAGED MODE (2026-09-01, plan project_save_model): when the
    ConfigWorkingSet is enabled the write goes into the working set instead of
    to disk — the global Save flushes it (see kicadstamp/config_working_set.py).
    CLI runs and pre-existing unit tests never enable it, so they hit the
    physical path unchanged."""
    if WORKING_SET.enabled:
        WORKING_SET.stage_write(path, data)
        return
    write_config_file(path, data)


# Public aliases — these two are consumed across the gui/ package boundary
# (gui/docks/_common.py re-exports them to every dock), and the documented
# rule is "gui must not import the private names" (config/__init__.py). The
# underscore-prefixed originals stay for the internal callers in this file
# (merge_write/add_list_entry/...); the public name is what crosses packages.
read_data = _read_data
write_data = _write_data


def merge_write(path: Path, new_data: dict, section: Optional[str] = None) -> bool:
    """Same read-merge-write shape as kicadstamp_cli.py's cmd_extract:
    existing content in the target file is kept, only what's in new_data
    is added/replaced — a target file is routinely home to several
    cells/profiles accumulated over time, not exclusively owned by this
    one write.

    section=None: new_data is merged directly at the file's top level.
    section='cells'/'extract_profiles'/etc.: new_data is
    {section: {key: {...}}} — only that one nested dict gets merged,
    every OTHER top-level key already in the file (clone_placements:,
    include:, ...) is left untouched.
    Returns whether the specific key being written already existed.
    """
    existing = copy.deepcopy(_read_data(path))
    if section is None:
        key = next(iter(new_data))
        overwritten = key in existing
        existing.update(new_data)
    else:
        new_section = new_data[section]
        key = next(iter(new_section))
        target_section = existing.setdefault(section, {})
        overwritten = key in target_section
        target_section.update(new_section)
    _write_data(path, existing)
    return overwritten


def add_list_entry(path: Path, section: str, entry: str) -> bool:
    """Appends `entry` (a path string, relative to `path`'s own
    directory — the same resolution rule config/includes.py uses for
    include: itself) to that list section in `path`, unless an entry
    already there resolves to the same file. Read-merge-write like
    merge_write(), but for a list section (include:) instead of a dict
    one — every other key in the file is left untouched. Returns whether
    an entry was actually added."""
    existing = copy.deepcopy(_read_data(path))
    items = existing.setdefault(section, [])
    if not isinstance(items, list):
        raise OSError(_("{section}: in {path} is not a list — refusing to touch it")
                      .format(section=section, path=path))
    base_dir = path.parent
    target = (base_dir / entry).resolve()
    for existing_entry in items:
        existing_str = existing_entry if isinstance(existing_entry, str) \
            else (existing_entry or {}).get('path')
        if existing_str and (base_dir / existing_str).resolve() == target:
            return False
    items.append(entry)
    _write_data(path, existing)
    return True


def upsert_list_entry(path: Path, section: str, entry: Dict[str, Any], key: str = "name",
                      key_fn: Optional[Callable[[Dict[str, Any]], Any]] = None) -> bool:
    """Read-merge-write like merge_write()/add_list_entry(), but for a list
    section whose entries are dicts matched by identity, not by list
    membership: an entry whose identity already exists gets REPLACED in
    place (same position), a new one gets appended. Every other top-level
    key in the file (cells:, include:, extract_profiles:, ...) is left
    untouched. Shared shape for clone_placements: (see
    upsert_clone_placement), thermal_via_arrays: (ConfigTreeDock's Add
    thermal via pad, 2026-08-03), and rules: (gui/docks/rules.py, 2026-08-05)
    — all three are "list of dict entries" sections in exactly this way.

    key_fn (callable, entry -> identity) overrides the default `entry.get(key)`
    — rules: needs this because a Rule's identity for --only falls back to
    net: when name: is absent (config/models.py's rule_effective_name()),
    unlike clone_placements:/thermal_via_arrays: which always require an
    explicit name:."""
    identity = key_fn if key_fn is not None else (lambda e: e.get(key))
    existing = copy.deepcopy(_read_data(path))
    items = existing.setdefault(section, [])
    if not isinstance(items, list):
        raise OSError(_("{section}: in {path} is not a list — refusing to touch it")
                      .format(section=section, path=path))
    overwritten = False
    for i, existing_entry in enumerate(items):
        if isinstance(existing_entry, dict) and identity(existing_entry) == identity(entry):
            items[i] = entry
            overwritten = True
            break
    if not overwritten:
        items.append(entry)
    _write_data(path, existing)
    return overwritten


def upsert_clone_placement(path: Path, entry: Dict[str, Any]) -> bool:
    """clone_placements:-specific name kept for the existing call sites/
    tests — see upsert_list_entry, the general form this now delegates to.
    Identity is name if set, else cluster (the Cluster tag) — split
    2026-08-15 so changing which Cluster an already-saved entry tags no
    longer creates a duplicate on save."""
    return upsert_list_entry(path, "clone_placements", entry,
                             key_fn=lambda e: e.get("name") or e.get("cluster"))


def upsert_entity(path: Path, entry: Dict[str, Any]) -> bool:
    """entities:-specific upsert (Entity/Placement split, 2026-08-30) — see
    upsert_list_entry, the general form this delegates to. Identity is
    `name` (REQUIRED on every Entity, see config/entries.py::_load_entity),
    so a renamed entry replaces its old record in place instead of
    appending a duplicate."""
    return upsert_list_entry(path, "entities", entry, key_fn=lambda e: e.get("name"))


def _entity_origin_to_anchor(origin: Dict[str, Any]) -> dict:
    """Origin-tab generic fields -> a trees: anchor dict (v2 grammar):
    xy -> (origin); point -> (point ...); anchor -> (ref ...)/(role ...)
    with the optional sheet/cluster/pad narrowing. Same mapping as the
    ClonePlacement path's _build_entry_dict, only the output lands on the
    TREE (Entity carries no position fields by design)."""
    mode = origin.get("mode")
    if mode == "xy":
        return {"origin": True}
    if mode == "point":
        return {"point": origin["point"]}
    # mode == "anchor"
    if origin.get("ref"):
        out: dict = {"ref": origin["ref"]}
    else:
        out = {"role": origin["role"]}
        if origin.get("sheet"):
            out["sheet"] = origin["sheet"]
        if origin.get("cluster"):
            out["cluster"] = origin["cluster"]
        if origin.get("pad"):
            out["pad"] = origin["pad"]
    return out


def _entity_origin_to_node(entity_name: str, origin: Dict[str, Any], rotation: float) -> dict:
    """Origin-tab generic fields -> a trees: node dict (kind "placement").
    Absolute mode writes node.xy (or polar); Anchor/Point mode writes the
    SHIFT into node.xy (ClonePlacement's "xy carries the shift" convention —
    see placer.py::_build_entry_dict), so the node is exactly the "position
    source" the Entity/Placement split assigns to trees."""
    out: dict = {"ref": entity_name, "kind": "placement"}
    if "radius" in origin:  # polar (absolute OR anchor/point polar offset)
        out["polar"] = [origin["radius"], origin["angle"]]
    elif origin.get("mode") == "xy":
        out["xy"] = [origin["x"], origin["y"]]
    else:  # anchor/point: node.xy carries the flat shift
        out["xy"] = [origin.get("shift_x", 0.0), origin.get("shift_y", 0.0)]
    if rotation:
        out["rotation"] = rotation
    return out


def upsert_entity_placement(path: Path, entity_name: str, origin: Dict[str, Any],
                            rotation: float = 0.0) -> bool:
    """Write/update the trees: node that PLACES `entity_name` (kind
    "placement") — PlacerDock's "save Origin = write node.xy/node.anchor"
    step (phase 5.2, stage 2). `origin` is the AnchorOriginWidget.build()
    generic shape (mode xy/anchor/point + x/y or radius/angle or
    shift_x/shift_y + ref/role/sheet/pad/cluster/point); the tree ANCHOR is
    derived from the mode and the node offset from the position.

    Rules (position lives ONLY in trees, so the write must keep the
    link_trees "a ref appears in at most one node" invariant):
      - the node is first removed from wherever it currently sits (top-level
        or nested), then placed under the tree whose anchor EQUALS the
        requested one — changing the anchor mode MOVES the node
      - if no tree has that exact anchor yet, a single-node tree named after
        the entity is appended
      - every other root key / tree / node is preserved untouched.

    Returns whether anything changed. Raises OSError on a non-list trees:
    section or a write failure (same contract as upsert_entity)."""
    data = copy.deepcopy(_read_data(path))
    trees = data.setdefault("trees", [])
    if not isinstance(trees, list):
        raise OSError(_("trees: in {path} is not a list — refusing to touch it")
                      .format(path=path))

    anchor = _entity_origin_to_anchor(origin)
    node = _entity_origin_to_node(entity_name, origin, rotation)
    changed = False

    def _prune_nodes(nodes: list) -> tuple[list, bool]:
        """Recursively drop every node whose ref == entity_name from `nodes`
        (a tree's "nodes" list, or a node's own "children" list on deeper
        levels — the grammar stores child nodes under "children", NOT "nodes",
        see trees.py::_node_to_dict). Returns (kept, removed); recurses into
        each KEPT node's own children. A tree's top-level list is "nodes", so
        the caller passes tree["nodes"] here and this same helper handles the
        nested "children" key on every level — no guessing by key name."""
        kept = []
        removed = False
        for n in nodes:
            if not isinstance(n, dict):
                kept.append(n)
                continue
            if n.get("ref") == entity_name:
                removed = True
                continue
            children = n.get("children")
            if isinstance(children, list):
                new_children, child_removed = _prune_nodes(children)
                if child_removed:
                    n["children"] = new_children
                    removed = True
            kept.append(n)
        return kept, removed

    for tree_dict in trees:
        if not isinstance(tree_dict, dict):
            continue
        nodes = tree_dict.get("nodes")
        if isinstance(nodes, list):
            new_nodes, tree_removed = _prune_nodes(nodes)
            if tree_removed:
                tree_dict["nodes"] = new_nodes
                changed = True

    target = next((td for td in trees
                   if isinstance(td, dict) and td.get("anchor") == anchor), None)
    if target is None:
        trees.append({"name": entity_name, "anchor": anchor, "nodes": [node]})
        changed = True
    else:
        nodes = target.setdefault("nodes", [])
        if node not in nodes:
            nodes.append(node)
            changed = True

    if changed:
        _write_data(path, data)
    return changed


def _find_node_by_ref(nodes: list, ref: str) -> Optional[dict]:
    """Recursively find the node dict whose ref == ref inside `nodes` (a
    tree's top-level "nodes" list, or a node's own nested "children" — the
    grammar stores children under "children", NOT "nodes"; see
    trees.py::_node_to_dict). Returns the node dict or None."""
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("ref") == ref:
            return n
        hit = _find_node_by_ref(n.get("children") or [], ref)
        if hit is not None:
            return hit
    return None


def append_tree_child_node(path: Path, tree_name: str,
                           parent_ref: Optional[str],
                           node_dict: Dict[str, Any]) -> bool:
    """Append ONE placement node to an EXISTING tree as a CHILD of an existing
    node (parent_ref) — or as a new TOP-LEVEL node of that tree (parent_ref is
    None) — P6 "Place Imprint" (plan_2026_09_05_scheme_list.md §6.2): the
    node is written INTO the already-existing tree named `tree_name`, NEVER a
    new tree (Denis, 2026-09-06: "Нам не нужно новое дерево").

    `path` is the physical file that OWNS the tree — the caller resolves it
    via find_list_entry_file(root_path, "trees", {"name": tree_name}) in the
    GUI layer before calling (config_writer is core and cannot walk the gui
    include graph; the single-file read-merge-write here is the same shape as
    every other config_writer helper). The node is appended to
    parent["children"] (recursive ref lookup) or to tree["nodes"] (top-level).

    Rules (position lives ONLY in trees, so the write must keep the link_trees
    "a ref appears in at most one node" invariant): the caller guarantees the
    node's ref is a NEW, unique Entity name (no existing node can carry it —
    link_trees fatals on a placement ref that does not resolve to an Entity,
    so a pre-existing node with this ref cannot exist).

    Returns whether anything changed. Raises OSError when the trees: section
    is not a list, the named tree does not exist in `path`, or parent_ref is
    set but no node with that ref exists anywhere in the tree (a missing
    tree/parent is a hard error — never a silent find-or-create)."""
    data = copy.deepcopy(_read_data(path))
    trees = data.setdefault("trees", [])
    if not isinstance(trees, list):
        raise OSError(_("trees: in {path} is not a list — refusing to touch it")
                      .format(path=path))
    tree_dict = next((t for t in trees
                      if isinstance(t, dict) and t.get("name") == tree_name), None)
    if tree_dict is None:
        raise OSError(_("tree {name!r} not found in {path}")
                      .format(name=tree_name, path=path))
    if parent_ref is None:
        target = tree_dict.setdefault("nodes", [])
    else:
        parent = _find_node_by_ref(tree_dict.get("nodes") or [], parent_ref)
        if parent is None:
            raise OSError(_("tree {name!r} has no node {ref!r} to append under")
                          .format(name=tree_name, ref=parent_ref))
        target = parent.setdefault("children", [])
    if node_dict in target:
        return False
    target.append(node_dict)
    _write_data(path, data)
    return True


# Fields Tools -> "Instances..." OWNS and may therefore CLEAR: the dialog's
# row REPLACES each of them (a field absent from a row means "not set" — the
# dialog's own blank-cell contract, see its rows()), while every OTHER key of
# an existing declaration survives a write verbatim (2026-09-12,
# plan_2026_09_12_tree_instance_own_place §И.1). A future editable axis is
# declared HERE, in one place, and nowhere else — `anchor`/`rotation` (the
# §И.5 columns: an empty Rotation cell and the "Inherit from template" action
# both mean "drop the key") are the reason this list is data, not a literal.
_INSTANCE_DIALOG_EDITABLE_KEYS = ("cluster", "rotation", "anchor")


def upsert_tree_instances(path: Path, template: str, rows: list) -> bool:
    """Replace every tree_instances: entry instantiating `template` with
    `rows` (each a {name, sheet, cluster?} dict — cluster OPTIONAL, 2026-09-03,
    plan tree_instances_cluster); entries of OTHER templates are preserved and
    a section left with no entries at all is dropped. Read-merge-write like the
    other config_writer helpers; returns whether anything changed (an
    identical resulting list is a no-op write).

    `rows` are the dialog's pre-validated SHORT declarations (2026-09-02, P3:
    Tools -> "Instances...") — this helper only persists them; materialization
    into full Tree + Entity records happens at the NEXT load
    (config/tree_instances.py::expand_tree_instances), never here. A blank/
    missing `cluster` in a row is omitted from the persisted dict entirely
    (the key is NOT written as null/"") so declarations that don't need the
    cluster axis stay clean on disk.

    WRITE OVER, NOT INSTEAD (2026-09-12, plan_2026_09_12_tree_instance_own_place
    §И.1): a row whose (template, name) matches an EXISTING declaration is
    written ON TOP of that declaration — every field the row does not mention
    survives verbatim — instead of being rebuilt from a hand-written literal.

    The old literal shape was a silent DATA LOSS: any field outside the
    hard-coded {name, sheet, cluster} set was gone after one Tools ->
    "Instances..." OK (found live with `params:` — the v1.3 axis feeding the
    {placeholder} substitution in net_template, erased unnoticed). Naming the
    dialog's own fields one by one would have to be repeated for every future
    axis (`anchor`/`rotation` are next), so the rule is structural: the ROW
    describes the editable fields, the DECLARATION keeps everything else.

    A key of the row whose value is None/"" means "not set" and REMOVES that
    key from the persisted declaration; a key the row does not carry at all
    leaves the declaration's value alone, EXCEPT for the fields named in
    _INSTANCE_DIALOG_EDITABLE_KEYS — those the dialog OWNS, so their absence
    from a row is the same "not set" (its blank-Cluster contract is exactly
    this). A row naming a declaration that does not exist yet is created fresh
    from the row, exactly as before."""
    existing = copy.deepcopy(_read_data(path))
    before_list = list(existing.get("tree_instances") or [])
    # Index THIS template's existing declarations by name so each row can be
    # overlaid on its own declaration; setdefault keeps the first declaration
    # when a name is (invalidly) duplicated.
    prev_by_name: Dict[str, Dict[str, Any]] = {}
    for entry in before_list:
        if isinstance(entry, dict) and entry.get("template") == template:
            name = entry.get("name")
            if isinstance(name, str):
                prev_by_name.setdefault(name, entry)
    kept = [e for e in before_list
            if not (isinstance(e, dict) and e.get("template") == template)]
    new_items = list(kept)
    for r in rows:
        merged = dict(prev_by_name.get(r["name"]) or {})
        merged["template"] = template
        for key, value in r.items():
            if key == "template":
                continue
            if value is None or value == "":
                # Blank value -> the key is not written at all (never as
                # null/""), so a declaration that does not use an axis stays
                # clean on disk. Required fields are validated by the caller.
                merged.pop(key, None)
            else:
                merged[key] = value
        # A field the dialog OWNS but whose row does not carry it is "not set"
        # — the dialog's own contract for a blank Cluster cell (rows() omits
        # the key rather than sending ""). Without this, clearing a cluster
        # would silently keep the old one. Every field OUTSIDE this set is not
        # the dialog's business and survives verbatim.
        for key in _INSTANCE_DIALOG_EDITABLE_KEYS:
            if key not in r:
                merged.pop(key, None)
        new_items.append(merged)
    if new_items == before_list:
        return False
    if new_items:
        existing["tree_instances"] = new_items
    else:
        existing.pop("tree_instances", None)
    _write_data(path, existing)
    return True


def _include_entry_target(entry: Any, base_dir: Path) -> Optional[Path]:
    """Resolved path an include: entry (string or {path:, enabled:} dict)
    points at, or None for a malformed entry — shared by add_include()/
    disable_include() for matching an existing entry against a target
    file, same resolution rule config/includes.py's _parse_include_entry
    uses."""
    entry_str = entry if isinstance(entry, str) else (entry or {}).get("path")
    return (base_dir / entry_str).resolve() if entry_str else None


def add_include(path: Path, entry: str) -> bool:
    """Like add_list_entry(path, "include", entry), but if an entry already
    there resolves to the same file and is currently disabled
    (enabled: false), RE-ENABLES it instead of adding a duplicate line —
    ConfigTreeDock's Add-file action (2026-08-03) re-including a file
    previously removed via disable_include() below should undo that, not
    pile up a second entry for the same path. Returns whether anything
    changed (added or re-enabled)."""
    existing = copy.deepcopy(_read_data(path))
    items = existing.setdefault("include", [])
    if not isinstance(items, list):
        raise OSError(_("include: in {path} is not a list — refusing to touch it").format(path=path))
    base_dir = path.parent
    target = (base_dir / entry).resolve()
    for i, existing_entry in enumerate(items):
        if _include_entry_target(existing_entry, base_dir) != target:
            continue
        if isinstance(existing_entry, dict) and existing_entry.get("enabled") is False:
            items[i] = existing_entry["path"]  # re-enabled == plain string form again
            _write_data(path, existing)
            return True
        return False  # already there and already enabled
    items.append(entry)
    _write_data(path, existing)
    return True


def disable_include(path: Path, target: Path) -> bool:
    """Soft-removes an include: entry pointing at `target` — sets
    enabled: false rather than erasing the line (ConfigTreeDock's Remove-
    file action, 2026-08-03: "стирать файл — это экстремизм", toggle-able
    back via add_include() above, not a destructive delete). Converts a
    plain string entry into the {path:, enabled: false} mapping form
    add_include()/config/includes.py's _parse_include_entry already
    understand. Returns whether an entry was found and changed."""
    existing = copy.deepcopy(_read_data(path))
    items = existing.get("include") or []
    base_dir = path.parent
    for i, existing_entry in enumerate(items):
        if _include_entry_target(existing_entry, base_dir) != target.resolve():
            continue
        if isinstance(existing_entry, dict) and existing_entry.get("enabled") is False:
            return False  # already disabled
        entry_str = existing_entry if isinstance(existing_entry, str) else existing_entry["path"]
        items[i] = {"path": entry_str, "enabled": False}
        _write_data(path, existing)
        return True
    return False


# include: only ever merges these top-level keys from an included file
# (config/includes.py's _LIST_SECTIONS/_DICT_SECTIONS) — everything else is
# fatal there (no defined multi-file merge behaviour). A file assigned a
# GUI role (or added as an include: target) can perfectly well ALSO be a
# full root config in its own right (registry_path/schematic_dir/...) if
# it was set up that way before — found live 2026-08-01: writing include:
# blindly in that case leaves the including file unloadable next time
# anything reads it.
INCLUDABLE_KEYS = frozenset(
    {"chains", "clone_placements", "cells", "points", "extract_profiles", "clone_profiles",
     "trees", "include"})


def _load_data_tolerant(path: Path) -> dict:
    """Tolerant read for non_includable_keys() below — mirrors
    gui/config_io.load_data (missing/malformed/unsupported file -> {}), but
    lives in core because kicadstamp must never import from gui/. Like
    _read_data, only .sexp/.json are read (2026-08-28, core_yaml_removal); a
    .yaml/.yml or any other extension is simply not a supported config format
    and yields {}."""
    if WORKING_SET.enabled and path is not None:
        staged = WORKING_SET.staged_content(str(path.resolve()))
        if staged is not None:
            return copy.deepcopy(staged)
    if path is None or not path.exists():
        return {}
    suffix = path.suffix.lower()
    try:
        with open(path, "r", encoding="utf-8") as f:
            if suffix == ".json":
                return lift_loaded_dict(json.load(f) or {}, str(path))
            if suffix == ".sexp":
                return sexp_to_dict(f.read(), path=str(path)) or {}
            return {}
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return {}


def non_includable_keys(path: Path) -> set:
    """Top-level keys in `path` that include: can't merge — see
    INCLUDABLE_KEYS. Shared by ExtractDock (after a successful extract,
    wiring the Placer file's include:) and ConfigTreeDock's Add-file
    action (2026-08-03, must not offer to include a file that would make
    the including file unloadable)."""
    return set(_load_data_tolerant(path).keys()) - INCLUDABLE_KEYS


def display_path(path: Path) -> str:
    """Path shown in labels: relative to PROJECT_ROOT when possible (the
    Files dock's tree is rooted there), absolute otherwise (a file
    outside that tree)."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)
