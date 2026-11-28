# kicadstamp/config/sheet_instance_policy.py
"""How a copied object's SHEET ADDRESS follows its `tree_instances:` instance.

Plan: plan_2026_11_27_tree_instances_nested_sheet_paths (Р2/Р3), Ч0 answer in that
file. THE one owner of the substitution rule — `config/tree_instances.py` has a
giant's line budget and four node kinds that all need the same answer, so the
rule lives here and each kind keeps a one-line call.

THE RULE, on SEGMENT BOUNDARIES, never on text:

    source S = "ChannelL"          target D = "ChannelR"
    S                    -> D                    (the template's own sheet)
    S/ArmP               -> D/ArmP               (a child of the template)
    S/ArmN/Stage         -> D/ArmN/Stage         (any depth)
    ChannelLeft/ArmP     -> unchanged            (NOT a child of S)
    S_ArmP               -> unchanged            (a different name, not a child)
    Power, /             -> unchanged            (external addresses stay external)

`follows_source` answers "is this address the template's own sheet or below it";
`rebase_sheet_path` performs the substitution (or says "not mine", `None`).
Parsing is `sheet_names.address_chain` — the ONE place an address is split, so
`"/"` (the root), an empty address and a multi-segment one behave exactly as they
do for the instance identity (Р1–Р3 of plan_2026_11_23_nested_clone_sheet_instances).

WHY THE RULE IS NOT "always write the instance sheet" (the pre-27.11 contract):
that is what flattened `ChannelL/ArmP` into `ChannelR` and made both arms of a
channel resolve to ONE leaf. WHY IT IS NOT A FATAL EITHER: a profile whose Entity
sits outside the template's own sheet loaded before this change, and turning it
red would break profiles for a defect they never had — such a copy keeps today's
instance sheet and gets a WARNING line instead (Р2: never a silent change, never
a guess).

THE THREE CONSUMERS, each with the fallback ITS kind has always had:

  * `entity_sheet` — an Entity copy (kind placement/unset): a sheet that does not
    follow the source falls back to the instance sheet with a warning; an Entity
    with NO sheet (a flat template) keeps getting the instance sheet silently,
    byte for byte as before;
  * `trace_anchor_sheet` — a `net_traces:` copy whose `anchor_sheet` is where its
    anchor role is searched: an address that does not follow the source is kept
    VERBATIM with a WARNING — a board-wide anchor (Р2: "явные внешние адреса
    остаются внешними") must not be re-pointed at the instance's sheet, which is
    exactly what the unconditional assignment used to do (Т05/М07);
  * `role_address` — a mount/component `(role ... (sheet ...))` address: the
    template's own sheet (or a child of it) follows the instance, anything else is
    a BOARD-WIDE reference kept VERBATIM with the informational line it has always
    logged (that is also the check `tree_instance_root.outward_problem` relies on
    to decide "inside" from "outside").

A source of `None` means the template never said where its own sheet is: nothing
can be compared, so an Entity/anchor keeps today's instance sheet silently and a
role address keeps its verbatim INFO — the expansion stays exactly what it was.
"""
from __future__ import annotations

import logging

from ..i18n import _
from ..sheet_names import ROOT_SHEET, address_chain

logger = logging.getLogger(__name__)

__all__ = [
    "entity_sheet",
    "follows_source",
    "rebase_sheet_path",
    "role_address",
    "trace_anchor_sheet",
]


def _parts(address) -> tuple[str, ...]:
    """An address as its segments — `address_chain`'s own reading ("/" -> ())."""
    return address_chain(address)


def follows_source(address, source) -> bool:
    """True when `address` IS the template's own sheet or sits below it.

    Segment boundaries, not text: `ChannelLeft/ArmP` does not follow `ChannelL`.
    A ROOT source follows NOTHING but itself: the root sheet is the whole
    schematic, not a subtree a copy could live under (its own address is `/`, and
    a child of the root is addressed by name, `/A` would be a second spelling of
    the same thing — never produced, never matched)."""
    if not source:
        return False
    if source == ROOT_SHEET:
        return address == ROOT_SHEET
    wanted = _parts(source)
    parsed = _parts(address)
    return len(parsed) >= len(wanted) and parsed[:len(wanted)] == wanted


def rebase_sheet_path(address, source, target) -> str | None:
    """`address` with its `source` prefix replaced by `target`; `None` when the
    address does not follow the source (the caller keeps its own fallback).

    `None` is ALSO returned when the target is empty — no instance sheet is a
    gate fatal elsewhere, and inventing one here is not this module's business."""
    if not target:
        return None
    if not follows_source(address, source):
        return None
    suffix = _parts(address)[len(_parts(source)):]
    head = "" if target == ROOT_SHEET else target
    rebased = "/".join(part for part in (head, *suffix) if part)
    return rebased or ROOT_SHEET


def entity_sheet(address, source, target, *, ref: str,
                 instance_name: str) -> str:
    """The sheet an Entity COPY gets (kind placement/unset).

    `address` is the TEMPLATE Entity's own sheet. A template that never said where
    its record sits (`None`/empty) keeps the old flat behaviour silently; a sheet
    that follows the source is re-based; anything else takes the instance sheet
    with one WARNING naming the record and both addresses."""
    if not address or not source:
        return target
    rebased = rebase_sheet_path(address, source, target)
    if rebased is not None:
        return rebased
    logger.warning(_(
        "tree_instance {name!r}: placement record {ref!r} sits on sheet "
        "{sheet!r}, which is not the template's own {old!r} or below it — the "
        "copy takes the instance sheet {target!r}, as before").format(
            name=instance_name, ref=ref, sheet=address, old=source,
            target=target))
    return target


def trace_anchor_sheet(address, source, target, *, identity: str,
                       instance_name: str) -> str:
    """The `anchor_sheet` a `net_traces:` COPY gets — the same rule as an Entity's
    own sheet, because the record's anchor role is searched on that sheet live
    (that is also what makes the copies' track/via endpoints resolve to the RIGHT
    instance's components instead of the template's)."""
    if not address or not source:
        return target
    rebased = rebase_sheet_path(address, source, target)
    if rebased is not None:
        return rebased
    logger.warning(_(
        "tree_instance {name!r}: net_traces record {ref!r} anchors on sheet "
        "{sheet!r}, which is not the template's own {old!r} or below it — kept "
        "verbatim (an external anchor is not the instance's to move)").format(
            name=instance_name, ref=identity, sheet=address, old=source))
    return address


def role_address(address, source, target, *, ref: str, instance_name: str,
                 kind: str = "mount") -> str:
    """A mount/component `(role ...)` address' sheet after the instance's move.

    INSIDE the template (the source or below it, at ANY depth) -> re-based.
    OUTSIDE -> kept VERBATIM, and the INFO line says so: a board-wide reference
    must not be silently re-pointed at the instance's sheet, and that same test
    is what `tree_instance_root.outward_problem` reads to tell "looks inside"
    from "looks out at the board". `kind` picks the line's wording — the two
    callers' messages predate this module and stay byte-identical."""
    if not address:
        return address            # the caller's "no sheet is a fatal" rule stands
    rebased = rebase_sheet_path(address, source, target)
    if rebased is not None:
        return rebased
    if kind == "component":
        logger.info(_("tree_instance {name!r}: component node {ref!r} addresses "
                      "sheet {sheet!r}, not the template's {old!r} — kept "
                      "verbatim").format(name=instance_name, ref=ref,
                                         sheet=address, old=source))
    else:
        logger.info(_("tree_instance {name!r}: mount node {ref!r} anchors to "
                      "sheet {sheet!r}, not the template's {old!r} — kept "
                      "verbatim").format(name=instance_name, ref=ref,
                                         sheet=address, old=source))
    return address
