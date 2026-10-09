# gui/entity/address.py
"""The ADDRESS of a placed instance of a cell — the model and the rules. Qt-free.

Step 5 of plan_2026_10_09_entity_page introduces this module in place of
gui/cell_entity_choice.py, which carried the same model AND the CELL page's
"Entity" dropdown. The dropdown, the working-instance store and the page went;
the ADDRESS stayed, because a board door still names the entity it came from and
the readers still have to work THAT instance.

What lives here is exactly the address and the read rules:
  * ``InstanceAddress`` — an entity of a cell: its name, cluster, sheet and its
    OWN ``refs:`` pins (None, NEVER ``{}``, when it pins none — see ``_refs_map``);
  * ``entity_address`` / ``entity_address_named`` — a raw ``entities:`` record,
    or a NAME resolved through the part-1 index (gui/docks/entity_index.py), as
    an address;
  * ``address_matches_selection`` — the ONE product comparison of an address
    with the (cluster, sheet) a board selection resolved to
    (kicadstamp.selection_narrowing.record_address_matches);
  * ``cannot_verify_line`` / ``not_the_entity_line`` — the yellow/red lines of a
    read pinned to an entity (a selection with no Cluster; a selection of
    another instance);
  * ``ReadInstance`` / ``read_instance_of`` — the read instance of an EXPLICIT
    address, and ONLY of it.

NO STORE, deliberately. ``read_instance_of`` takes the address ARGUMENT a door
handed over (gui/entity_doors.door_address) and never consults a remembered or
working instance — that store, and the fallback to it, left with the dropdown
(step 5). A reader WITHOUT an address does NOT read the board at all: it says so
and stops. Guessing an instance is exactly what the store's removal forbids.

The module never touches the board: doors resolve the address, workers read with
it. Qt-free on purpose — the guards call these directly, with no QApplication.
"""
from __future__ import annotations

import dataclasses
from typing import Optional

from kicadstamp.i18n import _
from kicadstamp.selection_narrowing import record_address_matches

__all__ = [
    "InstanceAddress", "entity_address", "entity_address_named",
    "address_matches_selection", "cannot_verify_line", "not_the_entity_line",
    "ReadInstance", "read_instance_of", "read_instance_or_report",
]


@dataclasses.dataclass(frozen=True)
class InstanceAddress:
    """One entity of a cell as an ADDRESS: its name, its (cluster, sheet) and its
    own ``refs:`` pins.

    ``refs`` is None when the entity pins nothing — NEVER ``{}``: the live frame
    reader branches on ``role_to_ref is not None``, so an empty map would send it
    down the "identified by refs" path with nothing to identify (the stale
    identification Denis hit live on 08.10 — доделка 2б, п.1)."""
    entity_name: Optional[str] = None
    cluster: Optional[str] = None
    sheet: Optional[str] = None
    refs: Optional[dict] = None


def _refs_map(raw) -> Optional[dict]:
    """The entity's raw ``refs:`` field as a role -> refdes dict, or None when it
    is absent, empty or not a mapping.

    None, NOT ``{}`` — the ONE place the "no pins" decision is made (see
    ``InstanceAddress.refs``)."""
    if not isinstance(raw, dict):
        return None
    return ({str(role): str(ref) for role, ref in raw.items() if role and ref}
            or None)


def entity_address(data: dict) -> InstanceAddress:
    """One ``entities:`` RAW record as an address."""
    return InstanceAddress(
        entity_name=(str(data["name"]) if data.get("name") else None),
        cluster=(str(data["cluster"]) if data.get("cluster") else None),
        sheet=(str(data["sheet"]) if data.get("sheet") else None),
        refs=_refs_map(data.get("refs")),
    )


def entity_address_named(index, entity_name) -> Optional[InstanceAddress]:
    """The address of the entity NAMED, through the part-1 index (or None).

    The index is ``gui/docks/entity_index.py``'s own build — a second walk of the
    graph is exactly what that module exists to prevent. ONE lookup for the door
    that turns a NAME a signal carried (``gui/entity_doors.door_address``) back
    into an address."""
    if index is None or not entity_name:
        return None
    ref = index.entity_named(entity_name)
    if ref is None:
        return None
    return entity_address(ref.data)


def address_matches_selection(address, cluster, sheet) -> bool:
    """True when (cluster, sheet) — as READ OFF the board selection — is the
    address of ``address``.

    The comparison is the ONE product rule (``record_address_matches``: cluster
    by prefix, sheet only when both sides carry one). An address without a
    cluster (an entity that is not placed) matches nothing: it names no instance
    to read."""
    if address is None or not address.cluster:
        return False
    return record_address_matches((address.cluster, address.sheet),
                                  (cluster, sheet))


def cannot_verify_line(address) -> str:
    """The YELLOW line of a read pinned to an entity while the selection carries
    no Cluster at all.

    Such a read may proceed — tagging a fresh pair is exactly what the buttons do
    — but it must not pass SILENTLY: the user has to see that nothing checked the
    selection against the entity."""
    return _("the selection carries no Cluster — cannot verify it is entity "
             "{entity!r}; reading it as that entity's instance").format(
        entity=getattr(address, "entity_name", None))


def not_the_entity_line(entity_name, cluster, sheet,
                        selection_entity_name=None) -> str:
    """The refusal of a board read pinned to an entity while the SELECTION
    belongs to another instance — a red line, never a silent read of someone
    else's pair.

    It names BOTH addresses — what the SELECTION is (the cluster and sheet the
    narrowing itself read off the board) and WHICH entity was expected (часть 3,
    п.6). ``selection_entity_name`` (доделка 3а, п.1) names the selection's own
    entity of the SAME cell when the caller found one, and says which leaf to use
    instead; None keeps the part-3 wording byte for byte."""
    if selection_entity_name:
        return _("the selection is {cluster} / {sheet} — entity {selection!r} of "
                 "this cell, not {entity!r}; use the item under {selection!r} — "
                 "nothing read").format(
            cluster=cluster, sheet=sheet if sheet else _("(no sheet)"),
            selection=selection_entity_name, entity=entity_name)
    return _("the selection is {cluster} / {sheet}, not entity {entity!r} — "
             "nothing read").format(
        cluster=cluster, sheet=sheet if sheet else _("(no sheet)"),
        entity=entity_name)


@dataclasses.dataclass(frozen=True)
class ReadInstance:
    """What ONE board read must use as its instance, built from an EXPLICIT
    address: the address itself and its (cluster, sheet, refs).

    ``address`` is None only for the empty instance of an address with no
    cluster (an entity that pins no instance) — the caller then keeps its
    ordinary refusal instead of reading someone else's pair."""
    address: Optional[InstanceAddress] = None
    cluster: Optional[str] = None
    sheet: Optional[str] = None
    refs: Optional[dict] = None

    @property
    def is_pinned(self) -> bool:
        return self.address is not None


def read_instance_of(address) -> ReadInstance:
    """The read instance of an EXPLICIT address — the row a DOOR handed over
    (part 3, п.2), with NO store consulted (step 5 removed it).

    An address without a cluster (an entity pinning no instance) yields the same
    empty instance the old "Manual…" row did, so the caller keeps its ordinary
    refusal instead of reading someone else's pair."""
    if address is None:
        return ReadInstance()
    return ReadInstance(address=address, cluster=address.cluster,
                        sheet=address.sheet, refs=address.refs)


def read_instance_or_report(expected_address, dock_address, verb: str):
    """`(instance, refusal_line)` for a board READ of a CELL (Update / Import).

    The address a DOOR handed over WINS; else the one the dock was LOADED with;
    with NEITHER there is nothing to read, so the caller gets the ONE refusal line
    (the two reads differ only in `verb` — "Update" / "Import"). The caller shows
    the line and stops when it is not None; otherwise it reads with `instance`.
    """
    address = expected_address if expected_address is not None else dock_address
    if address is not None:
        return read_instance_of(address), None
    if verb == "Import":
        return None, _("Import needs an entity's address — open it from an "
                       "ENTITY leaf.")
    return None, _("Update needs an entity's address — open it from an "
                   "ENTITY leaf.")
