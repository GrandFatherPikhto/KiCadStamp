# kicadstamp/config/registry_upgrade.py
"""Lift the profile's copper REGISTRIES to the format-3 key scheme, on disk.

Companion of :mod:`kicadstamp.config.upgrade_on_disk` (which lifts the config
files). ``load_config`` calls this right AFTER ``upgrade_graph_on_disk``, on the
same profile open, so a format-3 build never reads a name-keyed registry against
uuid-keyed planners: a key the registry does not know is "create", and a registry
entry whose key is not produced is "prune" — i.e. deleting copper from the board.

The rules mirror ``upgrade_on_disk.py`` (its points 3-6), pinned by cells in
``tests/placement/test_registry_upgrade_on_disk.py``:

3. ONE registry whose schema is NEWER than this build refuses the WHOLE sweep
   before the first write (pre-pass over both files).
4. A write that fails leaves that file exactly as it was and logs the reason —
   the load continues (the next open tries again). A `.bak` that cannot be taken
   is a REFUSAL (У3.3, Р-У3.5): the copy is the only way back, so the file is
   left untouched and the whole lift stops.
5. A registry already at the target schema is not touched at all: no write, no
   ``.bak``, ``mtime`` unchanged. That no-op costs ONE ``os.stat`` on the warm
   path — the schema probe is cached by ``(resolved, mtime_ns)``, exactly like
   ``format_version.read_version`` (no JSON parse on every open).
6. Nothing is written while the GUI working set holds unsaved changes.

Д2 (plan_2026_10_08_remove_spokes): the SPOKE copper keys (``pad:<pad>|…``) are
DETACHED — dropped from the registry, never mapped. ``pad:`` is the anchor_id
built ONLY by ``manual_position_calculator.compute_raw_positions`` (``anchor_id =
f"pad:{spoke.pad}"``), so the predicate is the code that BUILDS the key, not a
guess. Dropping the entry leaves the board copper unowned (it stays on the board,
adoptable by the cell that now describes it) where a KEPT entry would be pruned
by reconcile on the next apply — deleting the copper. Schema 2 (uuid keys, spokes
still attached) is lifted too, by the detach alone.

7. While the config STILL PLANS the spoke copper (a non-empty ``chains:``) the
   registry is NOT lifted AT ALL — no write, no ``.bak``, the schema stays where
   it is. Lifting it (even keeping the keys) would stamp the target schema and
   turn the lift into a NO-OP for the open that runs AFTER the chains are removed:
   the spoke keys would stay attached with nothing planning them, and reconcile
   would PRUNE them — the very deletion Д2 prevents. Leaving the file alone keeps
   the lift available for that later open, and a WARNING says why.

What changes in a key is ONLY the record-identifying NAME parts (plan §6,
Р-У5.1/Р-У5.2): ``name:<name>`` -> ``name:<uuid>``, ``point:<name>:ox:oy`` ->
``point:<uuid>:ox:oy``, ``thermal:<name>`` -> ``thermal:<uuid>``,
``net:<identity>`` -> ``net:<uuid>`` and the ``template_name`` part (a cell name
or a net-trace identity -> its uuid). Physics stays untouched: ``anchor:``,
``role:``, ``pad:``, the ``thermal_via_array`` literal, offsets, ``index`` and a
nested ``/…`` suffix.

A key whose name does not resolve (an orphan) or resolves in TWO sections at once
(``name:X`` may be an Entity AND a clone_placement) is LEFT UNTOUCHED and listed
in a WARNING (Р-У5.6) — never rewritten or dropped. The next ``apply`` then
prunes it exactly as it would today.

The name index is built from the FULLY MATERIALIZED ``Config`` (the loader's
expansions of ``tree_instances`` / ``sheet_templates`` included): a key may
target a generated copy that exists only after expansion (the У5.0 probe lesson —
resolving against the raw file dictionaries reported 388 phantom orphans).

Diagnostics are plain-English literals, deliberately NOT ``_()`` — the same
precedent as ``kicadstamp/persistence.py``: this runs only on a machine-data
migration path and keeps the gettext catalog free of it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from ..persistence import REGISTRY_SCHEMA_VERSION, REGISTRY_SCHEMA_VERSION_FORMAT3
from ..utils.paths import registry_paths_for_config
from ..utils.safe_write import backup_file, write_text_atomic
from .format_version import current_format
from .models import (
    chain_effective_name,
    clone_placement_effective_name,
    entity_effective_name,
    net_trace_effective_name,
)

logger = logging.getLogger(__name__)

# The schema a format-3 registry carries. Taken from the persistence module's
# constant (REGISTRY_SCHEMA_VERSION_FORMAT3) so the reader, the writer and this
# lift can never drift: 3 = UUID keys with the SPOKE copper keys detached (Д2).
TARGET_SCHEMA_VERSION = REGISTRY_SCHEMA_VERSION_FORMAT3

# How many keys/names a WARNING lists before "…"; the FULL list goes to DEBUG
# right beside it (a live profile can carry ~355 of them — one line, not a wall).
_LISTED_LIMIT = 10


def _listed(items: list[str], limit: int = _LISTED_LIMIT) -> str:
    """``"<N>: a, b, …"`` — the count plus the first ``limit`` entries, for a
    WARNING. The full list goes to DEBUG beside it (:func:`_debug_listed`)."""
    shown = ", ".join(items[:limit])
    if len(items) > limit:
        shown += ", …"
    return "{count}: {shown}".format(count=len(items), shown=shown)


def _debug_listed(path, what: str, items: list[str]) -> None:
    """The FULL list at DEBUG, right beside its truncated WARNING — ONE owner of
    the "count + first N + … in the WARNING, everything in DEBUG" rule."""
    if items:
        logger.debug("registry {path}: {what} (full list): {keys}".format(
            path=path, what=what, keys=", ".join(sorted(items))))

# The PREVIOUS format-3 schema (У5.4): UUID keys, but the spoke copper keys
# (``pad:<pad>|…``) were still attached. A registry at this schema still needs
# the Д2 detach — reconcile would prune those keys and delete their copper.
_UUID_KEY_SCHEMA_VERSION = 2

# The anchor_id prefix of a SPOKE copper key (Д2). ``pad:<pad>`` is built ONLY by
# ``manual_position_calculator.compute_raw_positions`` (``anchor_id =
# f"pad:{spoke.pad}"``), so every four-part key whose anchor_id starts with this
# prefix is spoke copper — decided by the code that BUILDS the keys, not by a
# guess (plan Ч1.1, Ч0 inventory).
_SPOKE_ANCHOR_PREFIX = "pad:"


def _is_spoke_key(key: str) -> bool:
    """True for a registry key whose copper belongs to a SPOKE (Д2).

    ``make_registry_key`` always builds a four-part key
    (``anchor_id|template_name|role|index``); a malformed key is not one we own,
    so it is left alone — exactly as ``map_registry_key`` leaves it."""
    parts = key.split("|")
    return len(parts) == 4 and parts[0].startswith(_SPOKE_ANCHOR_PREFIX)

# The literal net-trace/cell template_name that is NOT a record: a thermal via
# array's keys carry it verbatim (Р-У5.1) and it must survive the lift.
_THERMAL_TEMPLATE = "thermal_via_array"

# A point anchor_id is ``point:<name>:<ox>:<oy>`` (optionally a nested ``/…``):
# the offsets are the only thing after the point name, so a candidate point name
# is the one whose remainder matches this shape.
_POINT_REMAINDER = re.compile(r"^:[-+0-9.eE]+:[-+0-9.eE]+(/.*)?$")


# ── the schema probe: what schema does THIS registry carry right now? ────────
# Keyed by (resolved path, mtime_ns), the same identity format_version's probe
# uses, so a hand edit is a miss with no explicit invalidation and the warm path
# is one os.stat per file. Returns None for an absent or unreadable file (no-op).
_probe_lock = threading.Lock()
_probe_cache: dict[tuple[str, int], int] = {}


def read_registry_schema(path: str | Path) -> int | None:
    """The registry SCHEMA version on disk right now, or None when the file is
    absent (or unreadable — the lift must then leave it alone, not guess).

    A registry without a ``schema_version`` field is legacy: schema 1 (the same
    convention ``check_schema_version`` accepts)."""
    p = Path(path)
    try:
        mtime_ns = os.stat(p).st_mtime_ns
    except OSError:
        return None
    key = (str(p.resolve()), mtime_ns)
    with _probe_lock:
        hit = _probe_cache.get(key)
    if hit is not None:
        return hit
    version = _read_schema_uncached(p)
    if version is not None:
        with _probe_lock:
            _probe_cache[key] = version
    return version


def invalidate_registry_probe(path: str | Path) -> None:
    """Drop every cached schema for *path*. Called by the writer right after the
    physical write — the cache key carries ``mtime_ns``, and two writes inside
    one clock tick would otherwise leave the previous schema cached under a key
    no later read can miss (see ``format_version.invalidate_probe``)."""
    resolved = str(Path(path).resolve())
    with _probe_lock:
        for key in [k for k in _probe_cache if k[0] == resolved]:
            del _probe_cache[key]


def _read_schema_uncached(p: Path) -> int | None:
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    version = raw.get("schema_version")
    if version is None:
        return 1
    return version if isinstance(version, int) and not isinstance(version, bool) else None


# ── the name index of the fully materialized config ─────────────────────────

class _NameIndex:
    """Name -> uuid lookups for every registry key part that is a record name.

    A name found in two sections with different uuids is stored as ``None`` —
    AMBIGUOUS — and Р-У5.6 says such a key is left untouched, never guessed."""

    def __init__(self) -> None:
        self.names: dict[str, str | None] = {}      # entity + clone_placement
        self.points: dict[str, str] = {}            # point name -> uuid
        self.thermal: dict[str, str] = {}           # thermal via array name -> uuid
        self.net: dict[str, str] = {}               # net-trace identity -> uuid
        self.templates: dict[str, str | None] = {}  # cell / net-trace identity


def _add(index: dict[str, str | None], name: str | None, record_uuid: str | None) -> None:
    if not name:
        return
    if record_uuid is None:
        index[name] = None
        return
    if name in index and index[name] != record_uuid:
        index[name] = None
    else:
        index.setdefault(name, record_uuid)


def _build_index(cfg) -> _NameIndex:
    idx = _NameIndex()
    for e in getattr(cfg, "entities", None) or []:
        _add(idx.names, entity_effective_name(e), getattr(e, "uuid", None))
    for c in getattr(cfg, "clone_placements", None) or []:
        _add(idx.names, clone_placement_effective_name(c), getattr(c, "uuid", None))
    for name, point in (getattr(cfg, "points", None) or {}).items():
        idx.points[name] = getattr(point, "uuid", None)
    for tva in getattr(cfg, "thermal_via_arrays", None) or []:
        if tva.name:
            idx.thermal[tva.name] = getattr(tva, "uuid", None)
    for nt in getattr(cfg, "net_traces", None) or []:
        identity = net_trace_effective_name(nt)
        idx.net[identity] = getattr(nt, "uuid", None)
        _add(idx.templates, identity, getattr(nt, "uuid", None))
    for name, cell in (getattr(cfg, "cells", None) or {}).items():
        _add(idx.templates, name, getattr(cell, "uuid", None))
    return idx


# ── key mapping (table Р-У5.1) ──────────────────────────────────────────────

def _map_named(prefixed: str, prefix: str, index: dict[str, str | None]
               ) -> tuple[str | None, str | None]:
    """``<prefix><name>[/nested…]`` -> the mapped key, or a problem.

    Resolution is by RECORD NAME, never by splitting on ``/``: a slash is legal
    inside a cluster name (Р7 — ``FPGA_PWR_BANK/VCCIO/139`` is also a ``name:``
    fallback identity), so a nested suffix is found by matching the longest
    record name that is the value itself or a ``name/…`` prefix. Two matching
    records (a cluster ``X`` nested ``Y`` vs a record literally named ``X/Y``),
    or a name that is itself ambiguous across sections, leave the key alone."""
    value = prefixed[len(prefix):]
    matched: list[tuple[int, str]] = []
    ambiguous_name = False
    for name, uid in index.items():
        if value == name or value.startswith(name + "/"):
            if uid is None:
                ambiguous_name = True
            else:
                matched.append((len(name), name))
    if matched and not ambiguous_name:
        longest = max(length for length, _ in matched)
        best = [name for length, name in matched if length == longest]
        if len(best) == 1:
            name = best[0]
            return prefix + str(index[name]) + value[len(name):], None
    if matched or ambiguous_name:
        return None, "ambiguous"
    return None, "orphan"


def _map_point(prefixed: str, index: dict[str, str]) -> tuple[str | None, str | None]:
    value = prefixed[len("point:"):]
    candidates: list[tuple[int, str, str]] = []
    for name, uid in index.items():
        if value == name:
            candidates.append((len(name), name, ""))
        elif value.startswith(name + ":") and _POINT_REMAINDER.match(value[len(name):]):
            candidates.append((len(name), name, value[len(name):]))
    if len(candidates) == 1:
        _, name, rest = candidates[0]
        return "point:" + str(index[name]) + rest, None
    if candidates:
        return None, "ambiguous"
    return None, "orphan"


def _map_flat(prefixed: str, prefix: str, index: dict[str, str]
              ) -> tuple[str | None, str | None]:
    value = prefixed[len(prefix):]
    if value not in index:
        return None, "orphan"
    uid = index[value]
    if uid is None:
        return None, "ambiguous"
    return prefix + str(uid), None


def _map_anchor_id(anchor_id: str, idx: _NameIndex) -> tuple[str | None, str | None]:
    if anchor_id.startswith("name:"):
        return _map_named(anchor_id, "name:", idx.names)
    if anchor_id.startswith("point:"):
        return _map_point(anchor_id, idx.points)
    if anchor_id.startswith("thermal:"):
        return _map_flat(anchor_id, "thermal:", idx.thermal)
    if anchor_id.startswith("net:"):
        return _map_flat(anchor_id, "net:", idx.net)
    if anchor_id.startswith(("pad:", "anchor:", "role:")):
        # physics — never touched. (The SWEEP detaches `pad:` keys before it gets
        # here — Д2 — so this branch keeps a `pad:` key shape-stable for the
        # direct callers/cells of map_registry_key.)
        return anchor_id, None
    return None, "unknown"              # not a key we own (e.g. imprint:)


def _map_template_name(template_name: str, idx: _NameIndex) -> tuple[str | None, str | None]:
    if template_name == _THERMAL_TEMPLATE:
        return template_name, None
    if template_name not in idx.templates:
        return None, "orphan"
    uid = idx.templates[template_name]
    if uid is None:
        return None, "ambiguous"
    return str(uid), None


def map_registry_key(key: str, idx: _NameIndex) -> tuple[str, str | None]:
    """One registry key -> its format-3 twin, plus a problem marker.

    problem is None (mapped), or one of "orphan" / "ambiguous" (Р-У5.6 — leave
    the key alone) / "unknown" (not a four-part key we own)."""
    parts = key.split("|")
    if len(parts) != 4:
        return key, "unknown"
    anchor_id, template_name, role, index = parts
    new_anchor, problem = _map_anchor_id(anchor_id, idx)
    if problem is not None:
        return key, problem
    new_template, problem = _map_template_name(template_name, idx)
    if problem is not None:
        return key, problem
    return f"{new_anchor}|{new_template}|{role}|{index}", None


# ── the sweep ───────────────────────────────────────────────────────────────

def upgrade_registries_on_disk(config_path: str | Path, cfg) -> list[Path]:
    """Lift the via and track registries of ``config_path`` to the current
    schema (``TARGET_SCHEMA_VERSION``): map name keys to uuids (schema 1) and
    DETACH the spoke copper keys (schema 1 and 2 — Д2).

    A config that still carries a non-empty ``chains:`` PLANS its spoke copper, so
    both registries are left UNTOUCHED (rule 7 above) — the lift waits for the
    open that runs after the chains are gone.

    Returns the files written (empty when nothing needed lifting). Refuses a
    registry NEWER than this build BEFORE the first write (pre-pass); a failed
    write leaves that file as it was and logs the reason.
    """
    if current_format() < 3:
        return []

    from ..config_working_set import WORKING_SET  # lazy — out of the import-light path

    if WORKING_SET.is_dirty():
        # Д2′: the SAME text, but INFO only on the FIRST report of THIS kind in
        # the current dirty epoch (note_skip_report; the working set resets it in
        # clear()/flush) — one line per ACTION, DEBUG afterwards.
        message = (
            "registry schema upgrade on disk skipped: the working set holds unsaved changes")
        if WORKING_SET.note_skip_report("registry schema"):
            logger.info(message)
        else:
            logger.debug(message)
        return []

    # Н3: the SAME explicit-vs-default decision as apply_pipeline — an explicit
    # registry_path:/track_registry_path: in the config is honoured here too, so
    # the lift and the apply can never point at different files.
    via_path, trk_path = registry_paths_for_config(
        str(config_path), getattr(cfg, "registry_path", None),
        getattr(cfg, "track_registry_path", None))
    paths = [Path(via_path), Path(trk_path)]

    # Pre-pass: decide for EVERY file before the first write, so one newer file
    # cannot leave a half-lifted pair on disk.
    schemas: dict[Path, int | None] = {}
    for path in paths:
        schema = read_registry_schema(path)
        if schema is not None and schema > TARGET_SCHEMA_VERSION:
            raise ValueError(
                "registry {path!r} has schema_version {version}, but this build "
                "only supports schema_version up to {expected} — the on-disk "
                "format changed. Regenerate or migrate the file before running."
                .format(path=str(path), version=schema, expected=TARGET_SCHEMA_VERSION))
        schemas[path] = schema

    # Д2 доделка п.1 (дыра первого захода, 09.10.2026): while the config STILL
    # PLANS the spoke copper the registry must NOT be lifted AT ALL. Lifting it
    # (even keeping the keys) would stamp the TARGET schema, and once the
    # `chains:` section is removed the lift becomes a NO-OP for that file — the
    # spoke keys stay attached with nothing planning them, and `reconcile` then
    # PRUNES them (deleting the copper). Left alone, the file keeps the lift
    # available for the open that runs AFTER the chains are gone.
    chains = list(getattr(cfg, "chains", None) or [])

    idx = _build_index(cfg)
    lifted: list[Path] = []
    for path in paths:
        schema = schemas[path]
        if schema is None or schema == TARGET_SCHEMA_VERSION:
            # Absent/unreadable and the target schema are both no-ops.
            continue
        if schema not in (REGISTRY_SCHEMA_VERSION, _UUID_KEY_SCHEMA_VERSION):
            # Unreachable: the pre-pass refuses anything NEWER than the target.
            continue
        if chains:
            logger.warning(
                "registry {path}: left at schema {schema} — the config still has "
                "chains: ({names}); the spoke copper keys are still PLANNED, so "
                "they must stay attached. Remove the chains: section — the NEXT "
                "open detaches the spoke copper and lifts the schema".format(
                    path=path, schema=schema,
                    names=_listed([chain_effective_name(c) for c in chains])))
            _debug_listed(path, "chains",
                          [chain_effective_name(c) for c in chains])
            continue
        # Schema 1 (name keys) is mapped to uuids; schema 2 is already
        # uuid-keyed and only needs the spoke detach. Both end at the target.
        name_keyed = schema == REGISTRY_SCHEMA_VERSION
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            entries = {k: v for k, v in raw.items() if k != "schema_version"}
            new_entries: dict = {}
            problems: list[tuple[str, str]] = []
            detached: list[str] = []
            for key, value in entries.items():
                # Д2: a spoke key is DETACHED — dropped, never mapped, so the
                # board copper it named is left unowned instead of being pruned
                # away on the next apply. (A config that still PLANS this copper
                # never reaches here — see the `chains` skip above.)
                if _is_spoke_key(key):
                    detached.append(key)
                    continue
                new_key, problem = (map_registry_key(key, idx) if name_keyed
                                    else (key, None))
                if problem is not None:
                    if problem != "unknown":
                        problems.append((key, problem))
                    new_key = key
                elif new_key in new_entries:
                    problems.append((key, "collision"))
                    new_key = key
                new_entries[new_key] = value
            data = {"schema_version": TARGET_SCHEMA_VERSION, **new_entries}
            text = json.dumps(data, indent=2, ensure_ascii=False)
        except (OSError, ValueError) as e:
            logger.error(
                "registry {path}: the schema upgrade failed ({error}) — the file "
                "is left as it is; the next open will try again"
                .format(path=path, error=e))
            continue
        # Р-У3.5 (У3.3): a copy that CANNOT be taken is a REFUSAL, not the old
        # log-and-continue. The `.bak` is the only way back, so the file is left
        # exactly as it is and the whole lift stops. Plain English, like the other
        # refusals of this module — it does not go through `_()`.
        try:
            backup = backup_file(path)
        except OSError as e:
            raise ValueError(
                "registry {path}: the previous version could not be saved as a "
                "backup ({error}) — the schema upgrade is refused and the file "
                "is left as it is".format(path=path, error=e)) from e
        try:
            write_text_atomic(path, text)
            invalidate_registry_probe(path)
        except (OSError, ValueError) as e:
            logger.error(
                "registry {path}: the schema upgrade failed ({error}) — the file "
                "is left as it is; the next open will try again"
                .format(path=path, error=e))
            continue
        if detached:
            logger.warning(
                "registry {path}: spoke copper key(s) DETACHED ({listed}) — the "
                "copper stays on the board, unowned (adoptable by the cell that "
                "now describes it)".format(
                    path=path, listed=_listed(sorted(detached))))
            _debug_listed(path, "DETACHED spoke copper keys", detached)
        if problems:
            logger.warning(
                "registry {path}: {count} key(s) are NOT lifted — the record name "
                "is missing from the config or is ambiguous across sections: "
                "{keys}. Such keys are left untouched (the next apply will prune "
                "them exactly as before)".format(
                    path=path, count=len(problems),
                    keys=", ".join(sorted(k for k, _ in problems))))
        logger.warning(
            "registry {path}: schema {old} is outdated, lifted to {new}. The "
            "previous version is saved: {backup}"
            .format(path=path, old=schema,
                    new=TARGET_SCHEMA_VERSION, backup=backup))
        lifted.append(path)
    return lifted
