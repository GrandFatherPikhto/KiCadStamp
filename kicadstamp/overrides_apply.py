# kicadstamp/overrides_apply.py
"""Writing our stored Role/Cluster values OUT again — onto the live board or
into the ``.kicad_sch`` (plan_2026_09_18_field_overrides_store, Т5а).

Т5 moved every authoring path (fieldstool's Stage, the cell editor's Refs table,
the Components tree's Tag) INTO the override store, because our value outranks
the board and a board write would have been invisible. The write OUTWARD is not
lost, it becomes an explicit, named action — the CLI's
``overrides-apply --to board|schematic`` and, for the board, the Refs tab's own
"Write to board" button. Why it has to exist at all: the board is the carrier
FOREIGN tools read (BOM, net classes — design §4.2), and ``--to schematic`` is an
OFFLINE splice into ``.kicad_sch``, i.e. a KiCad-closed operation, which until
now was reachable only from the GUI.

Two destinations, two addressings — and the difference is the whole point:

  * BOARD: records are matched to live footprints by SYMBOL UUID (Т1's key),
    never by the ``ref`` stored next to it. That ``ref`` is a label for the Log
    and for ``overrides-list``; an F8 re-annotation renames components, so a
    refdes-keyed write would land on a FOREIGN one (С3's rule, outward).
  * SCHEMATIC: the splice is refdes/property addressed — that is the file
    format's own vocabulary (``schematic_set_fields.plan_set_edits_for_root``),
    so a plan for it speaks (ref, field, value). Two symbols sharing one refdes
    and asking for DIFFERENT values cannot be expressed in a shared
    ``(symbol ...)`` block at all, and that is reported per row instead of being
    silently resolved to one of the two (the same "never pick for the user"
    discipline ``roles_from_state``-style code follows everywhere else).

This module is the pure brain: no board, no adapter, no Qt, no I/O. The CLI
(``kicadstamp/cli.py``) owns the connection and the socket; the GUI's Refs tab
owns its own worker and its own adapter call.

Skip reasons are the SAME KEYS and the SAME ``_()`` strings as
``gui/role_table_model.skip_label`` — one message id per reason, so a CLI line
and a GUI Log line cannot drift apart in the translation catalog.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from .field_overrides import symbol_uuid_of
from .i18n import _

BOARD = "board"
SCHEMATIC = "schematic"
TARGETS = (BOARD, SCHEMATIC)

SKIP_NOT_ON_BOARD = "not_on_board"
SKIP_NO_ROLE_FIELD = "no_role_field"
SKIP_NO_CLUSTER_FIELD = "no_cluster_field"
# The schematic-only reason: one refdes, two symbols, two different values — the
# format physically cannot say that (see schematic_set_fields' own docstring).
SKIP_CONFLICTING_VALUES = "conflicting_values"


def skip_label(reason: str) -> str:
    """The human wording of a skip reason — Log/CLI text, never a fatal."""
    if reason == SKIP_NOT_ON_BOARD:
        return _("not on the board")
    if reason == SKIP_NO_ROLE_FIELD:
        return _("no Role field")
    if reason == SKIP_NO_CLUSTER_FIELD:
        return _("no Cluster field")
    if reason == SKIP_CONFLICTING_VALUES:
        return _("two symbols of this refdes need different values")
    return str(reason)


def skipped_text(skipped) -> str:
    """"R1 (not on the board), C9 (no Cluster field)" — the tail of a plan."""
    return ", ".join("{ref} ({reason})".format(ref=ref, reason=skip_label(reason))
                     for ref, reason in (skipped or ()))


def _no_field_reason(field_name: str) -> str:
    return (SKIP_NO_ROLE_FIELD if field_name == ROLE_FIELD_NAME
            else SKIP_NO_CLUSTER_FIELD)


def _sort_key(update) -> tuple:
    """(ref, field) of one planned update — the order every plan is reported in.

    Deterministic on purpose: a plan is a thing the user READS before writing
    (--dry-run), and the store's own iteration order is keyed by symbol uuid —
    stable, but meaningless to a human. Ref order also makes two runs diffable."""
    target, field_name, _value = update
    return (str(getattr(target, "ref", target)), str(field_name))


@dataclass
class ApplyPlan:
    """What a write will do, per destination — and the two shapes must never be
    confused:

      * BOARD: ``updates`` is ``[(footprint, field, value)]`` — the LIVE
        footprints, already resolved by symbol uuid, ready to be handed to ONE
        ``set_field_values_bulk`` call (one commit, so KiCad's own Ctrl+Z takes
        the whole batch back);
      * SCHEMATIC: ``updates`` is ``[(ref, field, value)]`` — what
        ``plan_set_edits_for_root`` speaks.

    ``skipped`` is ``[(ref, reason)]`` in both: a value the destination cannot
    take is REPORTED by name, never dropped in silence."""
    updates: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


def plan_board_writes(records, footprints, has_field) -> ApplyPlan:
    """Match every stored record to a LIVE footprint and to the field it wants.

    ``records`` — the store's own records (``FieldOverrides.records()``), each
    carrying ``symbol_uuid``, ``ref``, ``field`` and ``value``.
    ``footprints`` — the live board's footprints (a fresh read: never row objects
    the UI built earlier, which may be stale).
    ``has_field(fp, field)`` — the adapter's own per-field check. A footprint
    PHYSICALLY lacking the field cannot be handed it, and the skip is PER FIELD
    and PER COMPONENT: one such footprint must not roll the batch back (the rule
    the Refs table's board write followed before Т5)."""
    by_uuid: dict = {}
    for footprint in (footprints or ()):
        symbol_uuid = symbol_uuid_of(footprint)
        if symbol_uuid:
            by_uuid.setdefault(symbol_uuid, footprint)

    updates: list = []
    skipped: list = []
    for record in (records or ()):
        footprint = by_uuid.get(record.symbol_uuid)
        if footprint is None:
            skipped.append((record.ref, SKIP_NOT_ON_BOARD))
            continue
        if not has_field(footprint, record.field):
            skipped.append((record.ref, _no_field_reason(record.field)))
            continue
        updates.append((footprint, record.field, record.value))
    return ApplyPlan(updates=sorted(updates, key=_sort_key), skipped=skipped)


def plan_schematic_writes(records) -> ApplyPlan:
    """The same records as a refdes-addressed plan for the ``.kicad_sch`` splice.

    A refdes whose symbols want DIFFERENT values for one field is skipped WHOLE
    (both sides of the conflict, not just the second one) — see this module's
    docstring: the format cannot express it, and a partial write would be a
    silent pick."""
    updates: list = []
    skipped: list = []
    chosen: dict = {}
    for record in (records or ()):
        key = (record.ref, record.field)
        if key in chosen:
            if chosen[key] != record.value:
                skipped.append((record.ref, SKIP_CONFLICTING_VALUES))
                updates = [u for u in updates if (u[0], u[1]) != key]
                del chosen[key]          # the first one is gone too: no partial pick
            continue
        chosen[key] = record.value
        updates.append((record.ref, record.field, record.value))
    return ApplyPlan(updates=sorted(updates, key=_sort_key), skipped=skipped)


def fields_config_from_plan(plan) -> dict:
    """[(ref, field, value)] -> ``{ref: {field: value}}`` — the shape
    ``plan_set_edits_for_root`` takes (it is refdes-addressed by the format)."""
    out: dict = {}
    for ref, field_name, value in (plan.updates or ()):
        out.setdefault(ref, {})[field_name] = value
    return out


def board_write_description() -> str:
    """The label KiCad shows for the ONE undo step a batch board write creates —
    the same promise the Refs table's board write made before Т5, and the same
    sentence the GUI's own button produces (one operation, one wording)."""
    return _("Write stored Role/Cluster values to the board")
