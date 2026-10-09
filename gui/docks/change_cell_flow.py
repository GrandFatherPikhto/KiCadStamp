# gui/docks/change_cell_flow.py
"""The "Change cell…" flow — moving an entity onto another cell
(plan_2026_10_09_cells_and_entities, part 2).

It generalizes the orphan's old "Point to cell…" to ANY cell entity and lives in
its own module (rule 45, the shape of entity_doors.py / add_entities_flow.py);
DockHub keeps only the one wiring line.

THE CHOICE AND THE APPLY ARE SPLIT (Денис, 09.10.2026 — plan_2026_10_09_entity_page
step 1):

  * :func:`apply_cell_change` is THE ONE apply point — it writes `cell` +
    `cell_uuid` into the entity's OWN file in ONE working-set edit, emits ONE
    graph_changed and says what the change costs. BOTH callers go through it:
    the "Change cell…" menu item and the Entity page's Cell combobox. There is
    never a second writer (the rig's row «комбобокс пишет своей записью мимо
    apply_cell_change» dies if one appears);
  * :func:`change_cell_for_entity` is the CHOOSER: the door refreshes the
    snapshot in the worker ("Add entities…" pattern), then a dialog picks the
    cell and hands it to :func:`apply_cell_change`;
  * :func:`cell_choices` is the same candidate rule for a picker that must NOT
    block on a board read (the page's combobox) — it reads the LAST PUSHED
    snapshot only; no snapshot (or a dangling graph) offers every cell, fit not
    checked.

What the flow does, end to end:

  1. the cell LIST is ``instance_candidates.choose_cells`` — the project's ONE
     "which cells fit this instance" rule — fed with the parts of THIS entity's
     instance (its own (cluster, sheet)); only FITTING cells plus the entity's
     CURRENT cell become rows, the rest are counted (Денис, 09.10.2026: no huge
     list of every cell). The instance is read from a snapshot the DOOR refreshes
     in the worker, never on the UI thread; the page reuses the snapshot the hub
     already pushed;
  2. an ORPHAN (no cluster, or nothing of it on the board) has no instance to
     check against, so EVERY cell is offered with a yellow line saying the fit
     was not checked;
  3. on apply the entity's OWN file gets `cell` + `cell_uuid` in ONE working-set
     edit (the record is matched by name, its uuid is kept — no second record);
  4. the Log says what the change costs: the copper the entity owns under the
     OLD cell (its registry keys, counted by the project's ONE ownership rule
     ``selection_narrowing.is_own_key`` through
     ``absent_copper_prune.own_registry_entries``) will not be produced after the
     change, so the NEXT Redraw deletes exactly those pieces and draws the new
     layout. N = 0 gets its OWN wording — "no copper of the old layout is
     recorded" — never a silent "0 pieces".
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtWidgets import QDialog

from kicadstamp.absent_copper_prune import own_registry_entries
from kicadstamp.config import load_config
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ._common import ERROR_STYLE as _ERROR_STYLE, show_message, upsert_list_entry
from .change_cell import ChangeCellDialog
from .entity_delete import backup_file
from .instance_candidates import (CellChoices, CellSpec, choose_cells,
                                  instance_parts, snapshot_parts)

logger = logging.getLogger(__name__)


def all_cells_rows(cells) -> list:
    """An orphan's rows: EVERY cell, fit not checked — the picker says so in its
    yellow line. One row type (CellCandidate), so the pickers stay simple."""
    from .instance_candidates import CellCandidate
    return [CellCandidate(name=c.name, uuid=c.uuid, fits=True, reason="")
            for c in cells]


def cell_role_order(components) -> list:
    """A cell's OWN component roles in the cell's own order — ONE entry per slot,
    so multiplicity is the list itself (the order the Refs table and its tooltips
    follow, never the alphabet). `components` is a loaded Cell's slot list OR the
    raw ``components:`` list of a cell entry (objects and dicts are both read).

    THE one place the "a cell's roles, in the cell's order" rule lives: the CELL
    page's Refs line (gui/docks/cell_instance_mixin._cell_role_order), the
    CELL-SPEC picker (change_cell_flow._resolve_cells) and the ENTITY page's Refs
    tab (gui/entity/page._cell_roles) all call it — the roles a picker offers and
    the roles a table tags can never disagree. Deliberately ORDERED with
    multiplicity, unlike tree_from_selection.cell_component_roles (a SET, for the
    "does the cluster resolve every role" question)."""
    out: list = []
    for slot in components or ():
        role = slot.get("role") if isinstance(slot, dict) \
            else getattr(slot, "role", None)
        if role:
            out.append(role)
    return out


def old_layout_copper_count(cfg, config_path, entity) -> int:
    """The copper pieces THIS entity owns under the cell it stands on NOW.

    The ONE ownership rule, through the product's own enumerator: a registry key
    counts only when ``selection_narrowing.is_own_key`` calls it this instance's
    — the `name:` branch matches this entity's record, `role:` its (cluster,
    sheet) address, `anchor:` its refs. Copper of ANOTHER entity of the same cell
    therefore never inflates N (the case the deliverable called out: five
    entities share `fpga_pwr_spoke_right`).

    0 for an entity without a cell (an orphan, an imprint-based entity) — there
    is no old layout to lose.
    """
    cell_name = (entity or {}).get("cell")
    if not cell_name:
        return 0
    cell = (getattr(cfg, "cells", None) or {}).get(cell_name)
    if cell is None:
        return 0
    from kicadstamp.registry import load_registry_entries, record_key_part
    from kicadstamp.selection_narrowing import cell_record_addresses

    identity = record_key_part(cell_name, getattr(cell, "uuid", None))
    own_addresses = cell_record_addresses(cfg, cell_name)
    address = (entity.get("cluster"), entity.get("sheet"))
    raw_refs = entity.get("refs")
    refs = frozenset(raw_refs.values()) if isinstance(raw_refs, dict) else frozenset()
    via_entries, track_entries, _owner = load_registry_entries(config_path, cfg)
    return len(own_registry_entries(via_entries, track_entries, identity,
                                    own_addresses, address, refs))


def _resolve_cells(root_path, index=None):
    """(cfg, ctx, cells) of the project graph rooted at `root_path`.

    A DANGLING graph (the entity names a cell that is nowhere) makes load_config
    FATAL — that is exactly what the RAW `index` (ConfigTreeDock's `_entity_index`)
    exists for. The cells then come from the index (names + uuids only, no roles),
    the fit can only be "not checked", and the picker says so. cfg is None in that
    case — the callers key "orphan by graph" off it.
    """
    try:
        cfg, ctx = load_config(str(root_path))
    except (ValidationError, OSError):
        cfg, ctx = None, None
    if cfg is not None:
        cells = [CellSpec(
                     name=name, uuid=getattr(cell, "uuid", None),
                     roles=tuple(cell_role_order(
                         getattr(cell, "components", None) or ())))
                 for name, cell in (getattr(cfg, "cells", None) or {}).items()]
    else:
        names = index.names_for("cells") if index is not None else []
        cells = [CellSpec(name=n, uuid=index.target_uuid("cells", n))
                 for n in names]
    return cfg, ctx, cells


def cell_choices(root_path, snapshot, entity, index=None) -> CellChoices:
    """WHICH cells a picker offers for ONE entity — the ONE rule for BOTH places
    of choice (the Entity page's combobox and the "Change cell…" dialog).

    The rows are the FITTING cells plus the entity's CURRENT cell (always offered,
    marked; when it does not fit its row says so); every other non-fitting cell is
    NOT a row, only counted in ``others`` (Денис, 09.10.2026 — "зачем этот
    огромный список со всеми целлами?"). An ORPHAN — a dangling graph (cfg is
    None) or nothing of the instance on the board — cannot be checked at all:
    then EVERY cell is offered, fit not checked (that is how an orphan is fixed),
    and there is nothing to count.

    `snapshot` is the LAST PUSHED board snapshot (``connection.snapshot``); the
    sheet chains are resolved through the config, exactly as the door does, but NO
    rebuild is triggered here — the picker never blocks the UI thread on a board
    read.
    """
    if root_path is None:
        return CellChoices()
    cfg, ctx, cells = _resolve_cells(root_path, index)
    if not cells:
        return CellChoices()
    parts = snapshot_parts(snapshot or [],
                           dict(getattr(ctx, "sheet_names", None) or {}))
    cluster = (entity or {}).get("cluster")
    sheet = (entity or {}).get("sheet")
    instance = instance_parts(parts, cluster, sheet) if cluster else []
    if cfg is None or not instance:
        return CellChoices(candidates=tuple(all_cells_rows(cells)), orphan=True)
    return choose_cells(instance, cells, (entity or {}).get("cell"))


def change_cell_for_entity(hub, entity, file_path) -> None:
    """ConfigTreeDock's `change_cell_requested` delegate (part 2) — the CHOOSER.

    Opens a dialog over the SAME candidate rule (the door refreshes the snapshot
    in the worker first); the apply itself is :func:`apply_cell_change`, shared
    with the Entity page's Cell combobox.
    """
    root_path = hub.root_metadata_dock.root_path
    if root_path is None:
        show_message(_("Set the project root first."), _ERROR_STYLE, logger)
        return
    if not isinstance(entity, dict):
        return
    index = getattr(hub.config_tree_dock, "_entity_index", None)
    _cfg, _ctx, cells = _resolve_cells(root_path, index)
    if not cells:
        show_message(_("The config has no cells to point at."),
                     _ERROR_STYLE, logger)
        return
    connection = hub.main_window.connection

    def _open() -> None:
        # The SAME rule the Entity page's combobox uses — one function, so the
        # dialog and the box can never offer a different set of cells.
        choices = cell_choices(root_path,
                               getattr(connection, "snapshot", None) or [],
                               entity, index)
        dialog = ChangeCellDialog(hub.main_window, choices.candidates,
                                  orphan=choices.orphan, others=choices.others)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.result_data()
        if chosen:
            apply_cell_change(hub, entity, file_path, chosen)

    hub.refresh_snapshot_and_push(on_ready=_open)


def apply_cell_change(hub, entity, file_path, chosen) -> None:
    """THE one apply point: set the entity's cell to `chosen`, in its OWN file,
    in ONE working-set edit, then say what it costs.

    BOTH the "Change cell…" chooser and the Entity page's Cell combobox call
    THIS — the page never writes its own record. Nothing is written when the
    entity already stands on `chosen` (byte-identical file, no graph_changed).
    """
    root_path = hub.root_metadata_dock.root_path
    if root_path is None:
        show_message(_("Set the project root first."), _ERROR_STYLE, logger)
        return
    if not isinstance(entity, dict) or not chosen:
        return
    if chosen == entity.get("cell"):
        logger.info(_("entity {name!r} already uses cell {cell!r} — nothing "
                      "changed").format(name=entity.get("name"), cell=chosen))
        return
    index = getattr(getattr(hub, "config_tree_dock", None), "_entity_index", None)
    cfg, _ctx, cells = _resolve_cells(root_path, index)
    if not cells:
        show_message(_("The config has no cells to point at."),
                     _ERROR_STYLE, logger)
        return
    uuid = next((c.uuid for c in cells if c.name == chosen), None)
    old_count = old_layout_copper_count(cfg, root_path, entity)

    updated = dict(entity)
    updated["cell"] = chosen
    updated["cell_uuid"] = uuid
    try:
        backup_file(Path(file_path))
        upsert_list_entry(Path(file_path), "entities", updated,
                          key_fn=lambda e: e.get("name"))
    except (OSError, ValidationError) as e:
        show_message(_("Write failed: {error}").format(error=e),
                     _ERROR_STYLE, logger)
        return

    hub.config_tree_dock.refresh()
    hub.config_tree_dock.graph_changed.emit()
    name = entity.get("name")
    if old_count:
        logger.info(_(
            "entity {name!r} now uses cell {cell!r} — the next Redraw replaces "
            "its copper ({count} pieces of the old layout)").format(
                name=name, cell=chosen, count=old_count))
    else:
        logger.info(_(
            "entity {name!r} now uses cell {cell!r} — no copper of the old "
            "layout is recorded").format(name=name, cell=chosen))
