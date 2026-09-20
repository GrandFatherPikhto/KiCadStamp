# kicadstamp/field_overrides.py
"""The Role/Cluster override store — OUR value outranks the board's.

Design: `design_2026_09_18_field_overrides_store.md` (Р18); plan
`plan_2026_09_18_field_overrides_store.md`, task Т1.

The rule this module exists for (design §0):

    the effective value of a component's Role/Cluster = the value from this
    store when we have one, the live board's value otherwise — OUR value wins
    ALWAYS, not only when the board happens to be empty.

So a role or cluster can be edited in KiCadStamp and take effect immediately,
without being written to the board or to the schematic; and F8 stops being
scary — it rewrites the board, and this file is not touched.

Shape on disk: machine-only json next to the copper registries
(`overrides/<config-stem>.fields.json`, see
`utils.paths.overrides_path_for_config`). It is deliberately NOT a config and is
NEVER pulled in through `include:` — for the same reason the registry is not:
moved to another machine or onto another board it would start lying.

Key: the SYMBOL uuid — the LAST hop of the footprint's sheet-path uuid chain
(`sheet_path_uuids_of(footprint)[-1]`; the chain lives in
`domain.board.Footprint.sheet_path_uuids`, which is what the adapter hands over,
with kipy's raw `sheet_path.path` accepted as the second shape). It is the same
uuid the schematic's `(symbol ...)` block carries — the bridge
`gui/docks/pending.py:54` uses it — and NOT the refdes. Re-annotation (F8) renames
components, and a refdes-keyed value would silently arrive at a FOREIGN component
after it. `ref` is stored next to the key for the Log and for `overrides-list`
only — it is NOT the key.

  2026-09-20, plan_2026_09_20_symbol_uuid_wrong_attribute.md: this paragraph used
  to name `fp.sheet_path.path[-1]` — a kipy attribute. The read therefore raised
  AttributeError on every domain footprint the adapter actually hands over, an
  `except Exception` swallowed it, and the store could not record ANYTHING for a
  week while 5455 tests stayed green (they all built the kipy shape in their mock).
  The rule — both shapes, domain first — now lives in `symbol_uuid_of` /
  `sheet_path_uuids_of` below, and guard С5 forbids a second hand-rolled reader.

One F8 detail is worth knowing before trusting the key: the dialog's "Options"
section has a checkbox, **Re-link footprints to schematic symbols based on their
reference designators**. It is UNCHECKED by default, and an ordinary F8 does not
touch the store. Ticked DELIBERATELY, it re-links footprints by refdes, which
breaks the symbol-uuid link: the store's records are orphaned and the roles have to
be typed again. That is a conscious action in the dialog, not a background threat —
so no scaremongering about F8, just the one caveat next to the key it applies to.

SPARSE by design (В4а of the design): only what a human typed goes in. Filling
the store from the board is catastrophically wrong — with our value winning
always, such a mirror would freeze the board's values forever, silently.

This module is a plain table: no board, no adapter, no kipy, and no file I/O on
a lookup (guard С11). The overlay that applies the table to
`IBoardAdapter.get_field_value` is created by the adapter factory (Т2); the GUI
tables that write into it are Т5.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from .exceptions import ValidationError, format_fatal_error
from .i18n import _
from .persistence import check_schema_version
from .utils.safe_write import backup_file, write_text_atomic

logger = logging.getLogger(__name__)

FIELD_OVERRIDES_SCHEMA_VERSION = 1

# WHO put the value there — for the Log line and for `overrides-list`, never a
# decision key. Plain strings on purpose: they travel to json verbatim.
SOURCE_CELL_TABLE = "cell_table"
SOURCE_IMPRINT_TABLE = "imprint_table"
SOURCE_FIELDSTOOL = "fieldstool"
SOURCE_CLI = "cli"
# Т5б (plan_2026_09_18_field_overrides_store): the Components tree's own
# authoring row ("Tag selected") — the SET-side sibling of the cell editor's
# Refs table, so it needs its own name here rather than borrowing one.
SOURCE_ROLE_CLUSTER_TREE = "role_cluster_tree"

# В2: the store carries Role/Cluster and NOTHING else. `Value`, `Reference`,
# `Datasheet` and `Footprint` are read by FOREIGN tools (BOM, assembly), and an
# override over any of them would be corruption nobody could trace back to
# KiCadStamp. Two names make that impossible by construction — and it is also
# cheaper: the overlay never even consults the store for another field.
OVERRIDABLE_FIELD_NAMES = (ROLE_FIELD_NAME, CLUSTER_FIELD_NAME)


#: Types already reported as "not a footprint shape we understand" — so the Log
#: carries ONE line per SHAPE, never one per row per poll tick (see
#: _warn_foreign_shape).
_SHAPE_WARNED: set[str] = set()


def _uuid_strings(values) -> tuple[str, ...] | None:
    """UUID-ish values (kipy objects with ``.value``, or plain strings) -> a tuple
    of plain strings.

    ``None`` — "this is not a sequence of uuids at all" — for an ABSENT field and
    for a value that cannot be walked (a bare ``Mock`` raises ``TypeError`` on
    ``iter``). The caller reads that as "this shape carries no chain here", never
    as an exception: the reader runs on a poll tick and inside the store's write
    path, so it must not throw on a mock, a proxy or a re-parented object."""
    if values is None:
        return None
    try:
        items = list(values)
    except TypeError:
        return None
    out = []
    for value in items:
        out.append(str(value.value) if hasattr(value, "value") else str(value))
    return tuple(out)


def _footprint_uuid_chain(footprint) -> tuple[str, ...] | None:
    """The footprint's sheet-path uuid chain — ``()`` for "nothing to read",
    ``None`` for "an object of a shape this code does not understand".

    TWO SHAPES, DOMAIN FIRST (2026-09-20, plan_2026_09_20_symbol_uuid_wrong_
    attribute.md):
      * ``sheet_path_uuids`` — ``kicadstamp.domain.board.Footprint``, what EVERY
        adapter read hands over (``get_footprints``/``get_selected_items``)…;
      * ``sheet_path.path`` — the RAW kipy shape (uuid objects with ``.value``),
        still legal input: it is the mapper's own source (``fp._kipy``) and what
        the diagnostics read when they talk to kipy directly.

    Reading only the kipy one is exactly the defect this exists to prevent: the
    attribute was missing, ``except Exception`` swallowed the ``AttributeError``
    and the function answered ``None`` for every footprint on every board.

    The kipy shape is consulted not only when the domain field is ABSENT but also
    when it is present and EMPTY: the domain dataclass defaults
    ``sheet_path_uuids`` to ``()``, so a DTO that carries its chain only in the
    raw field (exactly what the Д2 guards' ``_fp`` fixtures build — a real domain
    object with the id stashed in the kipy one) would otherwise answer "no symbol
    uuid" for a footprint that does have one. Only an object where NEITHER field
    yields a sequence is a foreign shape, and only that gets the warning."""
    if footprint is None:
        return ()          # no footprint is a LEGAL answer, not a defect
    domain = _uuid_strings(getattr(footprint, "sheet_path_uuids", None))
    if domain:
        return domain
    sheet_path = getattr(footprint, "sheet_path", None)
    raw = getattr(sheet_path, "path", None) if sheet_path is not None else None
    legacy = _uuid_strings(raw)
    if legacy:
        return legacy
    if domain is None and legacy is None:
        return None        # neither field: a shape we do not understand
    return ()              # the field is there, it simply says nothing


def _warn_foreign_shape(footprint) -> None:
    """Say ONCE PER TYPE that the object is not a footprint shape we know.

    A hidden defect is how this function stayed broken for a week with 5455 green
    tests; a defect that drowns the Log (one line per row per poll tick, for a
    whole board) is how the NEXT one gets missed. So: a named WARNING, once."""
    type_name = type(footprint).__name__
    if type_name in _SHAPE_WARNED:
        return
    _SHAPE_WARNED.add(type_name)
    logger.warning(
        "symbol uuid: %s is not a footprint shape this code understands — it "
        "carries neither 'sheet_path_uuids' (the domain Footprint the adapter "
        "hands over) nor 'sheet_path.path' (kipy's raw form), so no symbol uuid "
        "can be read for it: the override store and Pending's identity check "
        "will skip it", type_name)


def sheet_path_uuids_of(footprint) -> tuple[str, ...]:
    """The footprint's FULL sheet-path uuid chain as plain strings — the
    hierarchical-sheet identity (the same chain ``gui/schema_model.py`` keys the
    schematic instances by, and the one whose LAST element is the symbol uuid).

    Accepts both shapes (see _footprint_uuid_chain); an empty tuple when there is
    nothing to read, and a once-per-type WARNING when the object is foreign.
    This is the ONE reader of the chain in the shipping tree: ``pending``'s
    ``_board_full_path`` and ``sheet_names.resolve_sheet_path_names`` both call
    it instead of reaching for the attribute themselves (2026-09-20)."""
    chain = _footprint_uuid_chain(footprint)
    if chain is None:
        _warn_foreign_shape(footprint)
        return ()
    return chain


def symbol_uuid_of(footprint) -> str | None:
    """The SYMBOL uuid of a board footprint — the LAST element of its sheet-path
    uuid chain (``domain.board.Footprint.sheet_path_uuids[-1]``; see
    sheet_path_uuids_of for the kipy form and the shapes).

    It is the same uuid the schematic's ``(symbol ...)`` block carries as its
    top-level ``(uuid ...)``: the board/schematic BRIDGE, and therefore the only
    identity a store applied INTO THE SCHEMATIC may be keyed by. The rule lives
    HERE and nowhere else, so the store, the overlay and Pending cannot drift.

    None when it is unavailable (no footprint, an empty chain, an IPC hiccup):
    the caller must then SKIP the override rather than guess a key — a value
    attached to the wrong symbol is corruption nobody would trace back here. An
    object of a FOREIGN shape is not "unavailable": it is a defect, and it is
    named once per type in the Log (see _warn_foreign_shape)."""
    chain = _footprint_uuid_chain(footprint)
    if chain is None:
        _warn_foreign_shape(footprint)
        return None
    return chain[-1] if chain else None


@dataclass(frozen=True)
class FieldOverride:
    """One stored value: WHICH field of WHICH symbol, and who typed it."""

    symbol_uuid: str
    ref: str
    field: str
    value: str
    source: str


class FieldOverrides:
    """The flat table in memory, keyed by (symbol uuid, field name).

    Lookups are dictionary hits (С6/§2.3.1: `get_field_value` is called tens of
    thousands of times per apply), which is why the file is parsed once, at
    load, and never consulted again."""

    def __init__(self, path=None):
        self._path = Path(path) if path is not None else None
        self._entries: dict[tuple[str, str], FieldOverride] = {}

    @property
    def path(self):
        return self._path

    # ── reads ──────────────────────────────────────────────────────────────

    def get(self, symbol_uuid, field: str):
        """Our value for (symbol uuid, field), or None when we have none.

        None means "we have no opinion" — the caller then falls back to the
        board. A field outside Role/Cluster is NEVER answered from here (В2),
        whatever found its way into the file."""
        if not symbol_uuid or field not in OVERRIDABLE_FIELD_NAMES:
            return None
        entry = self._entries.get((str(symbol_uuid), field))
        return entry.value if entry is not None else None

    def has_any(self) -> bool:
        """True when the store holds at least one record — what the Pending
        marker ("the registry is not in force") and the summary Log line ask."""
        return bool(self._entries)

    def records(self) -> list:
        """Every record, sorted by (symbol uuid, field): a deterministic order
        for the file, for `overrides-list` and for byte-comparable tests."""
        return [self._entries[key] for key in sorted(self._entries)]

    # ── writes ─────────────────────────────────────────────────────────────

    def set(self, symbol_uuid, ref, field: str, value, source: str) -> None:
        """Record one value.

        Fatal (loudly) when the field is not Role/Cluster (В2) or the symbol
        uuid is missing (С12): the callers are a table and a CLI, both able to
        report what happened, and guessing would attach OUR value to a foreign
        symbol after the next re-annotation.

        An EMPTY value is legitimate and means "the effective value is empty" —
        that is how a role is deliberately cleared (В4а)."""
        if field not in OVERRIDABLE_FIELD_NAMES:
            raise ValidationError(format_fatal_error(
                _("cannot store {field!r} in the Role/Cluster override store")
                .format(field=field),
                [_("only {names} may be stored here: Value/Reference/Datasheet "
                   "are read by foreign tools (BOM, assembly), and an override "
                   "over them would be corruption nobody could trace back to "
                   "KiCadStamp").format(names=", ".join(OVERRIDABLE_FIELD_NAMES))]))
        key = str(symbol_uuid or "").strip()
        if not key:
            raise ValidationError(format_fatal_error(
                _("cannot store a Role/Cluster override without a symbol uuid"),
                [_("the symbol uuid is the store's key — a refdes-keyed value "
                   "would arrive at a FOREIGN component after re-annotation "
                   "(F8). Resolve the symbol uuid first: refusing loudly is "
                   "better than guessing")]))
        self._entries[(key, field)] = FieldOverride(
            symbol_uuid=key,
            ref=str(ref or ""),
            field=field,
            value=str(value if value is not None else ""),
            source=str(source or ""),
        )

    def forget(self, symbol_uuid, field: str = None) -> int:
        """Drop one field of one component, or ALL of its fields ("передумал",
        Т6). Returns how many records left — 0 when there was nothing to
        forget, which is not an error."""
        key = str(symbol_uuid or "").strip()
        if not key:
            return 0
        if field is not None:
            return 1 if self._entries.pop((key, field), None) is not None else 0
        doomed = [key_pair for key_pair in self._entries if key_pair[0] == key]
        for key_pair in doomed:
            del self._entries[key_pair]
        return len(doomed)

    def save(self):
        """Write the table atomically, keeping a timestamped backup of the
        previous content (С13 — the copper registry writes with a bare
        `write_text` and no backup; that mistake is not repeated).

        Nothing to write AND no file on disk -> no file is created: a store that
        stays empty never appears. An EMPTY table over an EXISTING file IS
        written, though — otherwise forgotten records would come back on the
        next read (В4)."""
        if self._path is None:
            return None
        if not self._entries and not self._path.exists():
            return None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            backup_file(self._path)
        payload = {
            "schema_version": FIELD_OVERRIDES_SCHEMA_VERSION,
            "records": [asdict(record) for record in self.records()],
        }
        write_text_atomic(
            self._path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return self._path


def load_field_overrides(path) -> FieldOverrides:
    """Read the store at `path`.

    * a MISSING file -> an empty store, and nothing is created on disk (С11);
    * a MALFORMED file -> a warning and an empty store, the same leniency the
      copper registry shows: a corrupt store must not take the resolver down
      (the warning is the visible symptom, and Pending will show the board's
      values, i.e. "the store is not in force" by itself);
    * a FUTURE `schema_version` -> FATAL (С14, `check_schema_version`), never a
      silent re-parse of a format this build does not understand;
    * a record naming any field but Role/Cluster, or carrying no symbol uuid, is
      SKIPPED with a warning (В2/С12 defence in depth): such a record can only
      come from a hand-edited file, and it must not reach the resolver.
    """
    store = FieldOverrides(path)
    if path is None:
        return store
    file_path = Path(path)
    if not file_path.exists():
        return store
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — a corrupt store must not crash
        logger.warning(
            _("Failed to read the Role/Cluster override store {path}: "
              "{type}: {e} — treating it as empty").format(
                path=file_path, type=type(e).__name__, e=e))
        return store
    if not isinstance(raw, dict):
        logger.warning(
            _("Failed to read the Role/Cluster override store {path}: not an "
              "object — treating it as empty").format(path=file_path))
        return store

    check_schema_version(raw.get("schema_version"),
                         FIELD_OVERRIDES_SCHEMA_VERSION, file_path,
                         "field override store")

    records = raw.get("records")
    if not isinstance(records, list):
        return store
    for entry in records:
        if not isinstance(entry, dict):
            logger.warning(_("Ignoring a malformed record in the Role/Cluster "
                             "override store {path}").format(path=file_path))
            continue
        field = entry.get("field")
        if field not in OVERRIDABLE_FIELD_NAMES:
            logger.warning(
                _("Ignoring a record for field {field!r} in the Role/Cluster "
                  "override store {path} — only {names} are overridable")
                .format(field=field, path=file_path,
                        names=", ".join(OVERRIDABLE_FIELD_NAMES)))
            continue
        symbol_uuid = str(entry.get("symbol_uuid") or "").strip()
        if not symbol_uuid:
            logger.warning(_("Ignoring a record without a symbol uuid in the "
                             "Role/Cluster override store {path}")
                           .format(path=file_path))
            continue
        store._entries[(symbol_uuid, field)] = FieldOverride(
            symbol_uuid=symbol_uuid,
            ref=str(entry.get("ref") or ""),
            field=field,
            value=str(entry.get("value") if entry.get("value") is not None else ""),
            source=str(entry.get("source") or ""),
        )
    return store
