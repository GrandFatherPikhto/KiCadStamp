# gui/docks/change_cell_flow.py
"""The "Change cell…" flow — moving an entity onto another cell
(plan_2026_10_09_cells_and_entities, part 2).

It generalizes the orphan's old "Point to cell…" to ANY cell entity and lives in
its own module (rule 45, the shape of entity_doors.py / add_entities_flow.py);
DockHub keeps only the one wiring line.

What the flow does, end to end:

  1. the cell LIST is ``instance_candidates.cell_candidates`` — the project's ONE
     "which cells fit this instance" rule — fed with the parts of THIS entity's
     instance (its own (cluster, sheet)). The instance is read from a snapshot the
     DOOR refreshes in the worker ("Add entities…" pattern), never on the UI
     thread;
  2. an ORPHAN (no cluster, or nothing of it on the board) has no instance to
     check against, so EVERY cell is offered with a yellow line saying the fit
     was not checked;
  3. on OK the entity's OWN file gets `cell` + `cell_uuid` in ONE working-set
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
from .instance_candidates import (CellSpec, cell_candidates, instance_parts,
                                  snapshot_parts)

logger = logging.getLogger(__name__)


def all_cells_rows(cells) -> list:
    """An orphan's rows: EVERY cell, fit not checked — the dialog says so in its
    yellow line. One row type (CellCandidate), so the dialog stays simple."""
    from .instance_candidates import CellCandidate
    return [CellCandidate(name=c.name, uuid=c.uuid, fits=True, reason="")
            for c in cells]


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


def change_cell_for_entity(hub, entity, file_path) -> None:
    """ConfigTreeDock's `change_cell_requested` delegate (part 2)."""
    root_path = hub.root_metadata_dock.root_path
    if root_path is None:
        show_message(_("Set the project root first."), _ERROR_STYLE, logger)
        return
    if not isinstance(entity, dict):
        return
    # A DANGLING graph (the entity names a cell that is nowhere) makes
    # load_config FATAL — that is exactly what the RAW index exists for. The
    # cells then come from the index (names + uuids only, no roles), the fit can
    # only be "not checked", and the dialog says so.
    index = getattr(hub.config_tree_dock, "_entity_index", None)
    try:
        cfg, ctx = load_config(str(root_path))
    except (ValidationError, OSError):
        cfg, ctx = None, None

    if cfg is not None:
        cells = [CellSpec(name=name, uuid=getattr(cell, "uuid", None),
                          roles=tuple(c.role for c in
                                      (getattr(cell, "components", None) or ())))
                 for name, cell in (getattr(cfg, "cells", None) or {}).items()]
    else:
        names = index.names_for("cells") if index is not None else []
        cells = [CellSpec(name=n, uuid=index.target_uuid("cells", n))
                 for n in names]
    if not cells:
        show_message(_("The config has no cells to point at."),
                     _ERROR_STYLE, logger)
        return
    cluster, sheet = entity.get("cluster"), entity.get("sheet")
    sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
    connection = hub.main_window.connection

    def _open() -> None:
        parts = snapshot_parts(getattr(connection, "snapshot", None) or [],
                               sheet_names)
        instance = instance_parts(parts, cluster, sheet) if cluster else []
        # An orphan on EITHER count — the graph cannot place it, or nothing of
        # its instance is on the board — offers every cell, fit not checked.
        orphan = cfg is None or not instance
        candidates = (all_cells_rows(cells) if orphan
                      else cell_candidates(instance, cells))
        dialog = ChangeCellDialog(hub.main_window, candidates, orphan=orphan)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.result_data()
        if chosen:
            _write(hub, cfg, root_path, entity, file_path, chosen, cells)

    hub.refresh_snapshot_and_push(on_ready=_open)


def _write(hub, cfg, config_path, entity, file_path, chosen, cells) -> None:
    """Write cell + cell_uuid in ONE edit, then say what it costs."""
    if chosen == entity.get("cell"):
        logger.info(_("entity {name!r} already uses cell {cell!r} — nothing "
                      "changed").format(name=entity.get("name"), cell=chosen))
        return
    uuid = next((c.uuid for c in cells if c.name == chosen), None)
    old_count = old_layout_copper_count(cfg, config_path, entity)

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
