# gui/cell_entity_choice.py
"""WHICH instance of a cell an action works with — the model behind the cell
page's "Entity" dropdown (part 2 of plan_2026_10_05_entities_under_cells).

The problem this module solves
------------------------------
A cell is a deliberately cluster-agnostic template: the same cell stands on the
board many times (three channels of one filter, say). Every action of the cell
page and of the CellDock therefore has to answer ONE question first — WHICH
standing copy is meant. Until now the answer was three hand-typed fields
(sheet / cluster / refs) plus a remembered context, and a remembered context is
a hint that rots.

Part 2 (Денис, 08.10) replaces that source of the instance with an EXPLICIT
address. An address is an entity of this cell (`entities:` — name, cell link,
cluster, sheet, `refs`) or, since п.2а, a chain spoke that places it. The
dropdown shows them all; the chosen row IS the instance for every tab of the
page and for the CellDock, and "Manual…" (always the last row) keeps today's
hand-typed behaviour for a cell that has neither.

Qt-free on purpose (the rule of this module): the address list, the labels, the
default pick and the "last entity of the cell" store are pure logic, so their
guards call them DIRECTLY, with no dock and no QApplication. The dropdown widget
itself lives in its own small module and only IT knows about Qt.

Why the "last entity" store is a SEPARATE gui_state key: a write of the
remembered cell context ERASES the cell's identified `refs`
(gui/cell_edit_context.py:100), and merely picking a row must never do that.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from kicadstamp.i18n import _
from kicadstamp.selection_narrowing import record_address_matches

from . import settings
from .cell_edit_context import remembered_cell_edit_context, remembered_cell_refs

logger = logging.getLogger(__name__)

__all__ = [
    "MANUAL", "SOURCE_ENTITY", "SOURCE_SPOKE",
    "LAST_ENTITY_KEY", "WORKING_INSTANCE_KEY",
    "InstanceAddress", "entity_address", "entity_address_named",
    "manual_address", "spoke_address",
    "build_choices", "default_index", "explicit_kwargs",
    "address_matches_selection", "cannot_verify_line", "not_the_entity_line",
    "write_applies_line", "remember_last_entity", "remembered_last_entity",
    "remember_working_instance", "working_instance", "ReadInstance",
    "read_instance", "read_instance_of", "instance_for_read",
    "pin_working_instance",
]

# The three kinds of an address row. "Manual…" is a row too: it is not "no
# instance", it is "type the instance by hand, exactly as before".
SOURCE_ENTITY = "entity"
SOURCE_SPOKE = "spoke"
MANUAL = "manual"

# The gui_state.json key holding "the entity this cell was last worked with",
# per (root config, cell name). Deliberately its OWN key — see the module
# docstring: CELL_EDIT_CONTEXT_KEY erases the cell's identified refs.
LAST_ENTITY_KEY = "cell_last_entity"

# The gui_state.json key holding the WORKING INSTANCE of a cell — the address the
# cell page's "Entity" dropdown is on. ONE owner for everyone who reads the
# instance (the CellDock's payload, the mixed-selection door): the dropdown WRITES
# it, each reader reads it AT THE MOMENT OF USE. A copy pushed at a dock would be
# a second source and would drift the moment that dock is opened on another cell,
# or the page is not there to push. Cleared on the "Manual…" row: a stale entity
# record must never win over the hand-typed fields.
WORKING_INSTANCE_KEY = "cell_working_instance"


@dataclasses.dataclass(frozen=True)
class InstanceAddress:
    """One row of the cell's Entity dropdown — an ADDRESS of an instance.

    `source` is SOURCE_ENTITY for an entity of this cell, SOURCE_SPOKE for a
    chain spoke that places it (п.2а) or MANUAL for the "Manual…" fallback.
    `label` is what the user reads; the addressing fields are what an action
    passes on as its OWN instance (`explicit_kwargs`), never the remembered
    context.

    `refs` is the entity's own role -> refdes map (its `refs:` field) — an
    explicit identification, which is why an entity address needs no guesswork.
    An entity that pins NO refdes keeps `refs=None`, NEVER `{}`: the live frame
    reader branches on `role_to_ref is not None`, so an empty map would send it
    down the "identified by refs" path with nothing to identify — the
    "stale identification" fatal Denis hit live on 08.10 (доделка 2б, п.1).

    `resolved` is False for a spoke whose parts the planner cannot find on the
    board (п.2а): the ROW still exists, but an action on it must report a red
    line instead of reading anything.
    """
    source: str
    label: str
    entity_name: Optional[str] = None
    cluster: Optional[str] = None
    sheet: Optional[str] = None
    refs: Optional[dict] = None
    chain: Optional[str] = None
    pad: object = None
    resolved: bool = True

    @property
    def is_manual(self) -> bool:
        return self.source == MANUAL

    @property
    def key(self):
        """Stable identity of the row — what the widget re-selects by after a
        rebuild (`label` is in it too, so a renamed entity is a different row)."""
        return (self.source, self.entity_name, self.chain, self.pad, self.label)


def _refs_map(raw) -> Optional[dict]:
    """The entity's raw `refs:` field as a role -> refdes dict, or None when it
    is absent, empty or not a mapping — the everyday case for a role-resolved
    entity.

    None, NOT `{}`: this function is the ONE place the "no pins" decision is
    made (доделка 2б, п.1). The live frame reader `_live_cluster_frame` branches
    on `role_to_ref is not None`, so an empty map reaches it as "identified by
    refs" and dies as a stale identification instead of resolving the cluster."""
    if not isinstance(raw, dict):
        return None
    return ({str(role): str(ref) for role, ref in raw.items() if role and ref}
            or None)


def _refs_label(refs: dict) -> str:
    """The refdes of a `refs:` map, sorted — "C43, C44" (the values only: the
    roles belong to the entity's own form, not to an address line)."""
    return ", ".join(sorted(str(ref) for ref in refs.values() if ref))


def entity_label(data: dict) -> str:
    """The dropdown line of one `entities:` record (п.1).

    An entity that pins refdes reads "<name> — refs C43, C44"; otherwise
    "<name> — <cluster> on <sheet>"; an entity with neither is the honest
    "<name> — not placed" (it has no instance to name yet)."""
    name = str(data.get("name") or "?")
    refs = _refs_map(data.get("refs"))
    if refs:
        return _("{name} — refs {refs}").format(name=name,
                                                refs=_refs_label(refs))
    cluster = data.get("cluster")
    if cluster:
        sheet = data.get("sheet")
        return _("{name} — {cluster} on {sheet}").format(
            name=name, cluster=cluster,
            sheet=sheet if sheet else _("(no sheet)"))
    return _("{name} — not placed").format(name=name)


def entity_address(data: dict) -> InstanceAddress:
    """One `entities:` RAW record as an address row.

    `refs` is the record's own pins, or None when it pins none (see
    `_refs_map`: an empty map is NEVER produced here)."""
    return InstanceAddress(
        source=SOURCE_ENTITY,
        label=entity_label(data),
        entity_name=(str(data["name"]) if data.get("name") else None),
        cluster=(str(data["cluster"]) if data.get("cluster") else None),
        sheet=(str(data["sheet"]) if data.get("sheet") else None),
        refs=_refs_map(data.get("refs")),
    )


def entity_addresses(index, cell_uuid) -> list:
    """The cell's own entities as address rows, in the index's order (sorted by
    name, part 1). The index is the ONE built by gui/docks/entity_index.py — a
    second walk of the graph is exactly what that module exists to prevent."""
    if index is None:
        return []
    return [entity_address(ref.data)
            for ref in index.entities_for_cell(cell_uuid)]


def entity_address_named(index, entity_name) -> Optional[InstanceAddress]:
    """The address of the entity NAMED, through the part-1 index (or None).

    ONE lookup for the two callers that turn a NAME a signal carried back into an
    address: the door that publishes the cell's working instance
    (`pin_working_instance`, 2б п.4), and the door that hands the address STRAIGHT
    to an action as an argument (part 3, п.2). The index is
    `gui/docks/entity_index.py`'s own — a second walk of the graph is exactly what
    that module exists to prevent."""
    if index is None or not entity_name:
        return None
    ref = index.entity_named(entity_name)
    if ref is None:
        return None
    return entity_address(ref.data)


def manual_address() -> InstanceAddress:
    """"Manual…" — the fallback row that keeps today's hand-typed fields."""
    return InstanceAddress(source=MANUAL, label=_("Manual…"))


def spoke_address(chain_name, pad, *, cluster=None, sheet=None,
                  resolved=True) -> InstanceAddress:
    """One chain spoke as an address row (п.2а): "<chain> — pad <pad>".

    The row is built from what the PLANNER resolved for that spoke (its chain,
    its pad, its (cluster, sheet)) — this module never derives an instance from
    a cluster/sheet by itself. A spoke whose parts are not on the board keeps
    its row with `resolved=False`, so the action can report a red line."""
    return InstanceAddress(
        source=SOURCE_SPOKE,
        label=_("{chain} — pad {pad}").format(chain=chain_name, pad=pad),
        cluster=(str(cluster) if cluster else None),
        sheet=(str(sheet) if sheet else None),
        chain=str(chain_name) if chain_name else None,
        pad=pad,
        resolved=bool(resolved),
    )


def build_choices(entities=(), spokes=(), *, manual: bool = True) -> list:
    """The dropdown in the plan's order (п.1 / п.2а): the cell's ENTITIES first
    (the index already sorted them by name), then its SPOKES (grouped by chain
    by the caller), then the "Manual…" fallback LAST."""
    rows = list(entities) + list(spokes)
    if manual:
        rows.append(manual_address())
    return rows


def default_index(choices, *, opened_from: Optional[str] = None,
                  last_entity: Optional[str] = None) -> int:
    """Which row is selected when the page opens (п.4).

    Opened FROM an entity (its "Edit cell…" / click / menu) — that entity's row,
    by NAME. Opened from the CELL — the last entity chosen for this cell
    (`last_entity`), else the first entity by name, else the first RESOLVABLE
    spoke. A cell with neither keeps "Manual…" selected, so a cell placed by
    spokes behaves exactly as today. -1 when there is nothing to select (a cell
    without an entity and without a placer: the dropdown is empty, п.3)."""
    if opened_from:
        for i, row in enumerate(choices):
            if row.source == SOURCE_ENTITY and row.entity_name == opened_from:
                return i
    if last_entity:
        for i, row in enumerate(choices):
            if row.source == SOURCE_ENTITY and row.entity_name == last_entity:
                return i
    for i, row in enumerate(choices):
        if row.source == SOURCE_ENTITY:
            return i
    for i, row in enumerate(choices):
        if row.source == SOURCE_SPOKE and row.resolved:
            return i
    for i, row in enumerate(choices):
        if row.is_manual:
            return i
    return 0 if choices else -1


def explicit_kwargs(address, *, refs=None) -> dict:
    """The EXPLICIT instance an action on `address` passes on (п.5): the
    (cluster, sheet, refs) of the chosen row — never the remembered context.

    An entity with pinned refdes contributes them as `refs`, so the instance is
    named exactly; a row without a cluster contributes nothing for it (an
    unplaced entity has no instance to address). "Manual…" contributes nothing
    at all: there the page's own hand-typed fields ARE the instance."""
    if address is None or address.is_manual:
        return {}
    out: dict = {}
    if address.cluster is not None:
        out["cluster"] = address.cluster
    if address.sheet is not None:
        out["sheet"] = address.sheet
    pinned = address.refs if refs is None else refs
    if pinned:
        out["refs"] = dict(pinned)
    return out


def address_matches_selection(address, cluster, sheet) -> bool:
    """True when (cluster, sheet) — as READ OFF the board selection — is the
    address of `address` (п.5).

    The comparison is the ONE product rule
    (kicadstamp.selection_narrowing.record_address_matches: cluster by prefix,
    sheet only when both sides carry one) — never a second rule written here. A
    row without a cluster (an entity that is not placed) matches nothing: it names
    no instance to read."""
    if address is None or address.is_manual or not address.cluster:
        return False
    return record_address_matches((address.cluster, address.sheet),
                                  (cluster, sheet))


def cannot_verify_line(address) -> str:
    """The YELLOW line of a read pinned to an entity while the selection carries
    no Cluster at all (п.5).

    Such a read may proceed — tagging a fresh pair is exactly what the buttons do
    — but it must not pass SILENTLY: the user has to see that nothing checked the
    selection against the entity."""
    return _("the selection carries no Cluster — cannot verify it is entity "
             "{entity!r}; reading it as that entity's instance").format(
        entity=getattr(address, "entity_name", None))


def not_the_entity_line(entity_name) -> str:
    """The refusal of a board read that is pinned to an entity row while the
    SELECTION belongs to another instance (п.5) — a red line, never a silent
    read of someone else's pair."""
    return _("the selection is not entity {entity!r} — nothing read").format(
        entity=entity_name)


def write_applies_line(cell_name, address) -> str:
    """The Log line of a WRITE made while an ENTITY row is in force (п.6).

    The write goes to the CELL's file — the one record every entity of that cell
    stands on — so the change reaches them all. That is exactly what a user must
    be told out loud, once, in the wording the plan fixed; the line is empty on
    the "Manual…" row (nothing was said about an entity there)."""
    if address is None or address.is_manual:
        return ""
    return _("cell {cell!r} updated from entity {entity!r} ({cluster} on {sheet}) "
             "— the change applies to every entity of the cell").format(
        cell=cell_name, entity=address.entity_name, cluster=address.cluster,
        sheet=address.sheet if address.sheet else _("(no sheet)"))


def pin_working_instance(root_path, cell_name, entity_name, index=None) -> bool:
    """Make the entity a DOOR came from the cell's working instance (2б, п.4).

    Returns True when the record was published. The door (a board item of an
    ENTITY leaf) knows the name it came from and NOTHING else the page needs, so
    the name is resolved back into an ADDRESS through the part-1 index
    (`gui/docks/entity_index.entity_named` — the ONE index, never a second walk).

    A name that no longer resolves CLEARS the record instead of leaving a
    DIFFERENT entity in it: `read_instance` answers from that store AT THE MOMENT
    OF USE, so a stale row would silently read another channel — the very defect
    п.4 exists for. Cleared, the door's own explicit (cluster, sheet) is in
    charge, exactly as before part 2.

    Without an index the caller has nothing to resolve with: nothing is written
    (False) — never a guess."""
    if root_path is None or not cell_name or not entity_name:
        return False
    row = entity_address_named(index, entity_name)
    if row is None or not (row.cluster or row.refs):
        remember_working_instance(root_path, cell_name, None)
        return False
    remember_working_instance(root_path, cell_name, row)
    return True


def remember_last_entity(root_path, cell_name, entity_name) -> None:
    """Remember the entity NAME the user last chose for `cell_name` (п.4) — a
    convenience for the next open, never a source of truth (the reader degrades
    silently when the entity is gone). Best-effort, never raises.

    Deliberately NOT `remember_cell_edit_context`: that write would erase the
    cell's identified `refs` (gui/cell_edit_context.py:100)."""
    if root_path is None or not cell_name or not entity_name:
        return
    try:
        state = settings.state.get(LAST_ENTITY_KEY, {})
        if not isinstance(state, dict):
            state = {}
        per_root = state.setdefault(str(root_path), {})
        if not isinstance(per_root, dict):
            per_root = state[str(root_path)] = {}
        per_root[str(cell_name)] = str(entity_name)
        settings.state.set(LAST_ENTITY_KEY, state)
    except Exception:  # noqa: BLE001 — a state write must never break a pick
        logger.warning("Failed to remember the last entity of %r — state write "
                       "skipped", cell_name)


@dataclasses.dataclass(frozen=True)
class ReadInstance:
    """What ONE board read of a cell must use as its instance.

    `address` is the cell page's row when an ENTITY is in force (the read is then
    PINNED to it: see `address_matches_selection` / `not_the_entity_line`), None on
    the "Manual…" row. (cluster, sheet, refs) are the values every reader takes —
    the entity's own when it has one, else the cell's remembered ones."""
    address: Optional[InstanceAddress] = None
    cluster: Optional[str] = None
    sheet: Optional[str] = None
    refs: Optional[dict] = None

    @property
    def is_pinned(self) -> bool:
        return self.address is not None


def read_instance(root_path, cell_name) -> ReadInstance:
    """The ONE call a board READ of `cell_name` makes to learn its instance.

    The cell page's "Entity" dropdown has the last word (п.1): with an entity
    chosen its own (cluster, sheet) and its `refs:` pins ARE the instance, read
    from the working-instance store AT THE MOMENT OF USE. Otherwise — the
    "Manual…" row, or a cell the page was never used on — the cell's remembered
    context and its identified refs answer, exactly as they did before part 2.

    Never raises: an empty state is the everyday case, and the caller then reads
    with nothing remembered (the same refusal it always had)."""
    address = working_instance(root_path, cell_name)
    if address is not None and address.cluster:
        return ReadInstance(address=address, cluster=address.cluster,
                            sheet=address.sheet, refs=address.refs)
    cluster, sheet = remembered_cell_edit_context(root_path, cell_name)
    return ReadInstance(cluster=cluster, sheet=sheet,
                        refs=remembered_cell_refs(root_path, cell_name))


def read_instance_of(address) -> ReadInstance:
    """The read instance of an EXPLICIT address (part 3, п.2) — the row a DOOR
    handed over, with the working-instance STORE never consulted.

    The store answers "what the cell page's dropdown is on" and is read AT THE
    MOMENT OF USE; a door that already knows its entity must not ask it again —
    that window is what let a live read work another channel (Denis, 08.10). An
    address without a cluster (an entity pinning no instance) yields the same
    empty instance the store's "Manual…" row does, so the caller keeps its
    ordinary refusal instead of reading someone else's pair."""
    return ReadInstance(address=address, cluster=address.cluster,
                        sheet=address.sheet, refs=address.refs)


def instance_for_read(root_path, cell_name, expected_address=None) -> ReadInstance:
    """The instance ONE read of `cell_name` must use (часть 3, п.2).

    `expected_address` — the address a BOARD DOOR handed over (an entity leaf's
    own, resolved by gui/entity_doors.door_address): it IS the instance and the
    working-instance store is not consulted at all. Without one the store answers,
    exactly as it did before part 2 — the page's own "Entity" dropdown record.

    Lives here, with the other address rules, so a guard calls it DIRECTLY: the
    rule is which SOURCE wins, and no dock is needed to ask that."""
    if expected_address is not None:
        return read_instance_of(expected_address)
    return read_instance(root_path, cell_name)


def remember_working_instance(root_path, cell_name, address) -> None:
    """Record the WORKING INSTANCE of `cell_name` — the row the dropdown is on.

    An ENTITY address stores its name, (cluster, sheet) and pins (the shape of an
    `entities:` record, so the reader builds the same InstanceAddress with
    `entity_address`); the "Manual…" row or None CLEARS the record. Best-effort
    and never raises — a state write must never break a pick.

    The stored pins CAN rot (the entity's own `refs:` may change in the config):
    like every other hint here they are only a hint, and the live frame reader
    refuses a stale identification instead of reading someone else's pair."""
    if root_path is None or not cell_name:
        return
    try:
        state = settings.state.get(WORKING_INSTANCE_KEY, {})
        if not isinstance(state, dict):
            state = {}
        per_root = state.setdefault(str(root_path), {})
        if not isinstance(per_root, dict):
            per_root = state[str(root_path)] = {}
        if address is None or address.is_manual:
            per_root.pop(str(cell_name), None)
            if not per_root:
                state.pop(str(root_path), None)
        else:
            per_root[str(cell_name)] = {
                "name": address.entity_name,
                "cluster": address.cluster,
                "sheet": address.sheet,
                # None, never {}: the round-trip through `entity_address` must
                # not turn "no pins" back into an empty identification (п.1).
                "refs": dict(address.refs) if address.refs else None,
            }
        settings.state.set(WORKING_INSTANCE_KEY, state)
    except Exception:  # noqa: BLE001 — a state write must never break a pick
        logger.warning("Failed to remember the working instance of %r — state "
                       "write skipped", cell_name)


def working_instance(root_path, cell_name) -> Optional[InstanceAddress]:
    """The working instance recorded for `cell_name` (an ENTITY address), or None
    when nothing is recorded or the "Manual…" row is in force.

    Never raises: "no record" is the everyday case (a project opened before this
    key existed, or a manual instance), and the caller then keeps reading the
    remembered context exactly as it did before."""
    if root_path is None or not cell_name:
        return None
    try:
        state = settings.state.get(WORKING_INSTANCE_KEY, {}) or {}
        if not isinstance(state, dict):
            return None
        per_root = state.get(str(root_path))
        if not isinstance(per_root, dict):
            return None
        row = per_root.get(str(cell_name))
        if not isinstance(row, dict) or not row.get("name"):
            return None
        return entity_address(row)
    except Exception:  # noqa: BLE001 — best-effort read, never fatal
        return None


def remembered_last_entity(root_path, cell_name) -> Optional[str]:
    """The entity NAME remembered for `cell_name`, or None (nothing recorded, a
    malformed entry). Never raises — "nothing remembered" is the everyday case."""
    if root_path is None or not cell_name:
        return None
    try:
        state = settings.state.get(LAST_ENTITY_KEY, {}) or {}
        if not isinstance(state, dict):
            return None
        per_root = state.get(str(root_path))
        if not isinstance(per_root, dict):
            return None
        name = per_root.get(str(cell_name))
        return str(name) if name else None
    except Exception:  # noqa: BLE001 — best-effort read, never fatal
        return None
