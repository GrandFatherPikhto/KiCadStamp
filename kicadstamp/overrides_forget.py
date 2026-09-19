# kicadstamp/overrides_forget.py
"""When a stored value has DONE its job — and the explicit "forget" (Т6).

Design: `design_2026_09_18_field_overrides_store.md`; plan
`plan_2026_09_18_field_overrides_store.md`, task Т6 / guard С5.

The rule, in one sentence: **a record goes away when the SCHEMATIC and the record
agree.** The board is not part of that test, and that is not a detail:

  * the board is rewritten by F8 (Update PCB from Schematic), so "the board
    happens to carry our value" is a passing state, never a decision. Dropping the
    note there would lose the intended value at the very next F8 — the thing the
    store exists to prevent (М5);
  * while the note lives, OUR value outranks EVERYTHING (plan §0) — including a
    later schematic edit. So the moment the schematic itself carries our value,
    keeping the note turns it from a reminder into a VETO over the schematic: the
    user types a new Role in KiCad, and the resolver silently ignores it.

Hence this module reads the SCHEMATIC — the durable side — and never the board.
It does so in two shapes, because two callers already hold the answer in
different forms:

  * ``values_by_uuid(components)`` — the GUI's `gui/schema_model` view, which the
    fieldstool window has already read (and re-reads on every Rescan);
  * ``values_by_uuid_from_sheet(root_sheet_path)`` — straight from the
    ``.kicad_sch`` tree, for the CLI, which must not import ``gui/``.

Both use the SAME property spans the splice writes (`schematic_blocks`), so the
two halves cannot disagree about what "the schematic says".

Deliberately NOT here: any opinion about the board, any file I/O beyond the
schematic read, and any judgment about who WROTE the schematic value. If the
schematic says what our note says, the note is redundant — whether the value
arrived through Apply, through `overrides-apply --to schematic`, or because the
user typed it in KiCad.
"""
from __future__ import annotations

from .constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from .field_overrides import OVERRIDABLE_FIELD_NAMES
from .schematic_blocks import find_property_value_span
from .schematic_discovery import load_schematic_tree

# The fields this module compares. Same pair the store may carry (В2) — reading
# anything else would be comparing a note that cannot exist.
COMPARED_FIELDS = OVERRIDABLE_FIELD_NAMES


def _values_of_block(block, file_texts) -> dict:
    """{field: value} for one (symbol ...) block — the spans KiCad actually
    stores, read exactly the way gui/schema_model.py reads them."""
    span_text = file_texts[block.file][block.start:block.end]
    out: dict = {}
    for field in COMPARED_FIELDS:
        span = find_property_value_span(span_text, field)
        if span is not None:
            out[field] = span_text[span[0]:span[1]]
    return out


def values_by_uuid(components) -> dict:
    """``{symbol uuid: {field: value}}`` from read schematic components.

    Duck-typed on purpose (``role``/``cluster``/``divergent``/``symbol_uuids``):
    this is `gui/schema_model.SchematicComponent`'s shape, and the module must not
    import the GUI to know it.

    A DIVERGENT component — its units disagreeing INSIDE the schematic — is left
    out entirely: "the schematic says X" would be a guess, and a guess must never
    drop a note the user cannot retype from memory."""
    out: dict = {}
    for component in (components or ()):
        if getattr(component, "divergent", False):
            continue
        values = {ROLE_FIELD_NAME: component.role or "",
                  CLUSTER_FIELD_NAME: component.cluster or ""}
        for symbol_uuid in (getattr(component, "symbol_uuids", None) or ()):
            if symbol_uuid:
                out[str(symbol_uuid)] = dict(values)
    return out


def values_by_uuid_from_sheet(root_sheet_path: str) -> dict:
    """The same map, read STRAIGHT from the ``.kicad_sch`` tree (the CLI's half).

    One uuid carried by SEVERAL blocks with DIFFERENT values is left out — a file
    that says two things about one symbol cannot authorise dropping a note. (A
    block without a top-level uuid is simply absent: the store is keyed by uuid,
    so there is nothing to compare it with.)"""
    _files, file_texts, blocks = load_schematic_tree(root_sheet_path)
    out: dict = {}
    ambiguous: set = set()
    for block in (blocks or ()):
        if not block.uuid:
            continue
        symbol_uuid = str(block.uuid)
        values = _values_of_block(block, file_texts)
        if symbol_uuid in out and out[symbol_uuid] != values:
            ambiguous.add(symbol_uuid)
            continue
        out[symbol_uuid] = values
    for symbol_uuid in ambiguous:
        out.pop(symbol_uuid, None)
    return out


def redundant_records(records, schematic_values) -> list:
    """The records the SCHEMATIC already carries — the ones that can go (С5).

    A record whose component the schematic does not carry at all stays: nothing
    was confirmed, so nothing is dropped (an empty answer is not an agreement)."""
    doomed = []
    for record in (records or ()):
        values = (schematic_values or {}).get(record.symbol_uuid)
        if not values:
            continue
        if values.get(record.field) == record.value:
            doomed.append(record)
    return doomed


def forget_records(store, records_to_forget, *, save: bool = True) -> int:
    """Drop these records from ``store`` (and write the file, unless this is a
    dry run). Returns how many actually left — 0 is a legal answer, not an
    error."""
    dropped = 0
    for record in (records_to_forget or ()):
        dropped += store.forget(record.symbol_uuid, record.field)
    if dropped and save:
        store.save()
    return dropped
