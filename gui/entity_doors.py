# gui/entity_doors.py
"""The ENTITY door: "a tree item came FROM an entity — work THAT entity".

Both rules of доделка 2б, п.4 live here and nowhere else:

  * `pin_door_instance` — publish the entity as the cell's WORKING INSTANCE
    before the action reads anything (`cell_working_instance`, owned by
    gui/cell_entity_choice);
  * `open_cell_anchor_for_entity` — open the cell PAGE pinned to that entity
    (`opened_from`), so the dropdown lands on the entity the item came from.

Why a module of their own: `gui/dock_hub.py` is a giant and rule 45 lets it only
SHRINK, and because the rule is more than the two call sites — the tree emits a
NAME, this module turns it into an instance (through the part-1 index,
`entity_index.entity_named`), and the hub only delegates.

Qt-free on purpose: nothing here builds or reads a widget. It CALLS methods on
the objects the hub owns (`cell_anchor_view`, `config_tree_dock`), which is what
lets the guards drive it with a stand-in hub (a SimpleNamespace carrying just the
docks) instead of a whole window.
"""
from __future__ import annotations

from .cell_entity_choice import pin_working_instance


def pin_door_instance(hub, cell_name, entity_name) -> bool:
    """Make the entity a door came from the cell's working instance.

    The page's "Entity" dropdown owns the store, and every reader — CellDock's
    payload, the mixed-selection door — reads it AT THE MOMENT OF USE. A board
    item of an entity leaf therefore has to publish its own entity before the
    action that follows, or that action reads whatever entity the page was last
    on: open channel_1 and the action reads channel_0 (Denis, 08.10).

    When the cell PAGE is already open on this cell, the dropdown — not this
    module — is the writer: the page is asked to move to that entity and its own
    apply publishes the record (2в, п.2: one writer, one store). Otherwise the
    record is written here, as before.

    The name is resolved through the part-1 index the tree already built — never
    a second walk of it. A name the graph no longer knows clears the record
    (the door's own explicit cluster/sheet is then in charge); no name at all (a
    CELL leaf) writes nothing. Returns True when the record was published.

    Nothing is read from `hub` beyond the docks it owns, so a stand-in hub
    works."""
    if not entity_name:
        return False
    view = getattr(hub, "cell_anchor_view", None)
    if getattr(view, "_cell_name", None) == cell_name:
        if view.show_entity_instance(entity_name):
            return True
    index = getattr(getattr(hub, "config_tree_dock", None), "_entity_index", None)
    root = getattr(getattr(hub, "root_metadata_dock", None), "root_path", None)
    return pin_working_instance(root, cell_name, entity_name, index)


def open_cell_anchor_for_entity(hub, cell_name, file_path, entity_name) -> None:
    """Open the cell PAGE on that entity — the entity leaf's "Edit cell...".

    Same page as the cell menu's "Cell anchor..." (`DockHub._edit_cell_anchor`),
    but the dropdown lands on THIS entity instead of the cell's last/first one,
    and the working instance is published first: a page that silently works
    another channel is exactly what this item exists to stop. A name the graph
    no longer knows still opens the page (the honest default decides) — the
    store is simply cleared by `pin_door_instance`."""
    pin_door_instance(hub, cell_name, entity_name)
    hub.cell_anchor_view.load_entry(cell_name, file_path,
                                    opened_from=entity_name)
    hub._focus_config_tree_dock()
    hub.config_tree_dock.show_page(hub._cell_anchor_page)


def select_cell_from_tree(hub, name, file_path, cluster=None, sheet=None,
                          entity=None) -> None:
    """ConfigTreeDock's cell_select_requested delegate (Н5) — the context menu's
    "Select cell": drive CellDock's own entry point (it loads the cell when it is
    not the currently open one and runs the SAME worker the CellDock button runs
    — one function for every door). An ENTITY leaf sends the explicit
    (cluster, sheet) AND its name; a CELL leaf sends None for both."""
    pin_door_instance(hub, name, entity)
    hub.cells_dock.select_cell_requested(name, file_path, cluster, sheet)


def select_cell_components_from_tree(hub, name, file_path, cluster=None,
                                     sheet=None, entity=None) -> None:
    """ConfigTreeDock's cell_select_components_requested delegate (СЦ-1) — drive
    CellDock's components-only entry point (same instance rules)."""
    pin_door_instance(hub, name, entity)
    hub.cells_dock.select_cell_components_requested(name, file_path, cluster,
                                                    sheet)


def select_enclosed_copper_from_tree(hub, name, file_path=None, cluster=None,
                                     sheet=None, entity=None) -> None:
    """ConfigTreeDock's cell_select_enclosed_requested delegate — the ONE door of
    the context-menu item (gui/select_enclosed_copper.py)."""
    pin_door_instance(hub, name, entity)
    from .select_enclosed_copper import select_enclosed_copper
    select_enclosed_copper(hub, name, file_path, cluster, sheet)


def open_explode(hub, name, file_path=None, cluster=None, sheet=None,
                 entity=None) -> None:
    """Д8 (Р3а-6): the flow lives in gui/explode_wiring.py — the delegate the
    menus and the cells call. `entity` (2б, п.4) is the entity the item came
    from, None for a CELL leaf or the CellDock's own button."""
    pin_door_instance(hub, name, entity)
    hub.explode_wiring.open_tab(name, file_path, cluster, sheet)


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
