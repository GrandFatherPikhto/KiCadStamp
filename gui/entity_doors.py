# gui/entity_doors.py
"""The ENTITY door: "a tree item came FROM an entity — work THAT entity".

The rules of доделка 2б, п.4 and of часть 3, п.2 live here and nowhere else:

  * `door_address` — the entity's EXPLICIT address (cluster, sheet and its own
    `refs:` pins), resolved from the NAME the signal carries through the part-1
    index and handed to the action AS AN ARGUMENT (`expected_address`). That is
    what stops a read from working whichever entity the cell page was last on —
    Denis' live case of 08.10;
  * `open_cell_dock_for_entity` — open the CELL's map (CellDock) for that entity,
    its own address riding along (step 5 of plan_2026_10_09_entity_page: the CELL
    page and its instance dropdown are gone, so "Edit cell..." shows the MAP);
  * `refresh_cell_from_selection` / `import_cell_from_selection` /
    `subtract_cell_from_selection` — the board doors of the tree: ONE function
    per action, each handing the resolved address over.

Why a module of their own: `gui/dock_hub.py` is a giant and rule 45 lets it only
SHRINK, and because the rule is more than the call sites — the tree emits a NAME,
this module turns it into an instance (through the part-1 index,
`entity_index.entity_named`), and the hub only delegates.

Qt-free on purpose: nothing here builds or reads a widget. It CALLS methods on
the objects the hub owns (`cell_anchor_view`, `config_tree_dock`), which is what
lets the guards drive it with a stand-in hub (a SimpleNamespace carrying just the
docks) instead of a whole window.
"""
from __future__ import annotations

from typing import Optional

from .entity.address import (
    InstanceAddress,
    entity_address,
    entity_address_named,
)


def door_address(hub, entity_name, cluster=None,
                 sheet=None) -> Optional[InstanceAddress]:
    """The EXPLICIT address of the entity a board door came from (часть 3, п.2).

    The entity NAME rides in the signal (2б, п.4); the address — cluster, sheet
    and the entity's own `refs:` pins — is resolved HERE, through the part-1
    index the tree already built (`entity_address_named`), and handed to the
    action as an ARGUMENT. The working-instance store is NOT the path any more:
    it stays only as the cell page's memory of what its dropdown shows.

    The index is asked FIRST, and not as a nicety: it is the same lookup
    `pin_working_instance` makes, and it is the only way to get the entity's
    `refs:` pins, which a (cluster, sheet) signal cannot carry.

    When the index no longer knows the name (an entity renamed or removed since
    the tree was drawn — an action started from a stale tree), the leaf's OWN
    (cluster, sheet) are used: exactly the values the item displayed. With
    neither, None — the action then keeps its ordinary rules, never a guess."""
    index = getattr(getattr(hub, "config_tree_dock", None), "_entity_index", None)
    row = entity_address_named(index, entity_name)
    if row is not None:
        return row
    if entity_name and (cluster or sheet):
        return entity_address({"name": entity_name, "cluster": cluster,
                               "sheet": sheet})
    return None


def open_cell_dock_for_entity(hub, cell_name, file_path, entity_name) -> None:
    """Open the CELL's MAP for that entity — the entity leaf's "Edit cell...".

    Step 5 of plan_2026_10_09_entity_page: the CELL page is GONE, so this item
    shows the SAME map editor a CELL leaf's "Edit cell..." does (CellDock's form
    + the Cell dialog), but hands it the entity's OWN address (`door_address`) so
    the dock's board actions work on THAT instance and never guess one. The map
    itself reads no board."""
    address = door_address(hub, entity_name)
    hub.cells_dock.load_entry(cell_name, file_path, expected_address=address)
    hub._open_cell_dialog()


def select_cell_from_tree(hub, name, file_path, cluster=None, sheet=None,
                          entity=None) -> None:
    """ConfigTreeDock's cell_select_requested delegate (Н5) — the context menu's
    "Select cell": drive CellDock's own entry point (it loads the cell when it is
    not the currently open one and runs the SAME worker the CellDock button runs
    — one function for every door). An ENTITY leaf sends the explicit
    (cluster, sheet) AND its name; a CELL leaf sends None for both."""
    hub.cells_dock.select_cell_requested(
        name, file_path, cluster, sheet,
        expected_address=door_address(hub, entity, cluster, sheet))


def select_cell_components_from_tree(hub, name, file_path, cluster=None,
                                     sheet=None, entity=None) -> None:
    """ConfigTreeDock's cell_select_components_requested delegate (СЦ-1) — drive
    CellDock's components-only entry point (same instance rules)."""
    hub.cells_dock.select_cell_components_requested(
        name, file_path, cluster, sheet,
        expected_address=door_address(hub, entity, cluster, sheet))


def select_enclosed_copper_from_tree(hub, name, file_path=None, cluster=None,
                                     sheet=None, entity=None) -> None:
    """ConfigTreeDock's cell_select_enclosed_requested delegate — the ONE door of
    the context-menu item (gui/select_enclosed_copper.py)."""
    from .select_enclosed_copper import select_enclosed_copper
    select_enclosed_copper(hub, name, file_path, cluster, sheet)


def open_explode(hub, name, file_path=None, cluster=None, sheet=None,
                 entity=None) -> None:
    """Д8 (Р3а-6): the flow lives in gui/explode_wiring.py — the delegate the
    menus and the cells call. The tab lives on the ENTITY page (step 2 of
    plan_2026_10_09_entity_page), so the ENTITY `entity` is the key: the page is
    opened ON it and the address is read from that entity's record. A door without
    an entity is refused by `open_tab` (there is none left — the CELL leaf has no
    Explode item, and the CellDock button is gone)."""
    hub.explode_wiring.open_tab(entity, file_path)


def _address_kwargs(hub, cluster, sheet, entity) -> dict:
    """`{"expected_address": addr}` when the item names an ENTITY, else {}.

    ONE place builds the argument of the three board doors below. A CELL leaf
    (no entity) passes nothing at all: the call shape stays exactly what it was
    for every existing caller and stand-in, and the read keeps its ordinary
    rules. An entity leaf says MORE, never less."""
    address = door_address(hub, entity, cluster, sheet)
    return {} if address is None else {"expected_address": address}


def refresh_cell_from_selection(hub, name, file_path, cluster=None, sheet=None,
                                entity=None, choose_layers=False) -> None:
    """The tree's "Update from selection" door (cell leaf AND entity leaf).

    The flow was three lines of `gui/dock_hub.py`; it lives here because the
    part-3 rule is about WHICH instance the read works with, and that decision
    (`_address_kwargs`) is this module's. `choose_layers` picks the dialog leg of
    the same read, exactly as before."""
    hub.cells_dock.refresh_from_selection_requested(
        name, file_path, choose_layers=choose_layers,
        **_address_kwargs(hub, cluster, sheet, entity))


def import_cell_from_selection(hub, name, file_path, cluster=None, sheet=None,
                               entity=None, choose_layers=False) -> None:
    """The tree's "Add selected copper" door — the additive counterpart of
    `refresh_cell_from_selection`, same address rule."""
    hub.cells_dock.import_from_selection_requested(
        name, file_path, choose_layers=choose_layers,
        **_address_kwargs(hub, cluster, sheet, entity))


def subtract_cell_from_selection(hub, name, file_path, cluster=None, sheet=None,
                                 entity=None) -> None:
    """The tree's "Subtract selected copper" door — same address rule (С-2 flow,
    no layer dialog: the pairing is by each record's own live copper)."""
    hub.cells_dock.subtract_from_selection_requested(
        name, file_path, **_address_kwargs(hub, cluster, sheet, entity))


def _loaded_cell_uuid(dock) -> Optional[str]:
    """The uuid of the cell the dock (or the page) has loaded, or None.

    ONE reader for both sides: a CellDock answers through its
    `_loaded_entry_on_disk`, the cell page through its `_current_entry` — the two
    methods that already own "the entry this form was loaded from"."""
    for name in ("_loaded_entry_on_disk", "_current_entry"):
        getter = getattr(dock, name, None)
        if callable(getter):
            uuid = (getter() or {}).get("uuid")
            if uuid:
                return uuid
    return None


def instance_state(dock) -> Optional[bool]:
    """Does ANYTHING give the cell `dock` (or the page) has loaded an instance?

    True — nobody places it AND no entity claims it (a drawing: 3б, 2в, п.5);
    False — it HAS an instance (an entity, or a placer);
    None — the dock cannot judge (no part-1 index provider, or no uuid).

    ONE question for every caller (2г, п.1): the CellDock's board buttons, the
    page's read-only state and the tree all ask it here, of the index the Config
    tree already built — never a second walk of the graph, and never a third
    copy of the rule."""
    provider = getattr(dock, "entity_index_provider", None)
    index = provider() if provider is not None else None
    uuid = _loaded_cell_uuid(dock)
    if index is None or not uuid:
        return None
    return (not index.has_entity_for_cell(uuid)
            and not index.placed_by_cell(uuid))


def unplaced_without_entity(dock) -> bool:
    """True when NOTHING places the cell `dock` has loaded AND no entity claims
    it (2в, п.5) — the CellDock twin of the page's read-only case (3б): its board
    buttons have no instance to read, so they must be OFF.

    "Cannot judge" (no provider / no uuid) counts as NOT unplaced, so a bare dock
    keeps today's behaviour — every bare-dock test relies on that."""
    return instance_state(dock) is True


def read_only_cell(dock, door_flag: bool) -> bool:
    """The ONE "this cell is a drawing" answer for the cell PAGE (3б; made
    per-refill in 2г, п.1).

    Same question as `unplaced_without_entity`, asked of the same index — so
    creating an entity on an orphan (the tree's `graph_changed` refills the form)
    lifts the read-only state by itself, at the next refill, with no special
    case. `door_flag` is what the DOOR decided when it opened the page and is the
    fallback for a page without the index (bare pages in tests)."""
    state = instance_state(dock)
    return door_flag if state is None else state
