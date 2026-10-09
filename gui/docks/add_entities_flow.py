# gui/docks/add_entities_flow.py
"""The "Add entities…" flow behind the Config tree's cell-leaf menu item
(plan_2026_10_09_cells_and_entities, part 1).

It lives in its own module on purpose (rule 45, the shape of
gui/entity_doors.py): DockHub keeps only the `add_entities_requested` wiring,
and everything that DECIDES — what the instances are, which are taken, how the
records are written — is here.

The flow, end to end:

  1. read the include graph's `entities:` names (the uniqueness rule the loader
     itself uses) and the cell's slot roles from the LOADED config;
  2. ask the DOOR for a fresh snapshot — `DockHub.refresh_snapshot_and_push(
     on_ready=…)`, the ONE "rebuild in the worker, then act on the UI thread"
     operation (the "Add node" pattern) — and only inside `on_ready` build the
     candidates from `connection.snapshot` and open the dialog. No board read
     happens on the UI thread;
  3. write every CHECKED instance in ONE working-set edit and emit ONE
     `graph_changed`.

Duplicates are the project's ONE rule, `tree_from_selection.
find_entity_for_source`, re-checked per row — never a second copy. No tree node
is created: the entities are made exactly as the single "Create entity" makes
them, and placement lives in trees: alone (part 1, п.6).
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtWidgets import QDialog

from kicadstamp.config import load_config
from kicadstamp.config_writer import read_data, write_data
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ._common import ERROR_STYLE as _ERROR_STYLE, show_message
from .add_entities import AddEntitiesDialog
from .instance_candidates import instance_candidates, snapshot_parts
from .rename import collect_graph_files, name_exists_in_list_section
from .tree_from_selection import find_entity_for_source

logger = logging.getLogger(__name__)


def _graph_entity_names(files) -> set:
    """Every `entities:` name across the include graph — the uniqueness domain
    the loader enforces (a name must be unique in the WHOLE graph, not one
    file)."""
    names = set()
    for path in files:
        for item in (read_data(path).get("entities") or []):
            if isinstance(item, dict) and item.get("name"):
                names.add(item["name"])
    return names


def write_entities_one_edit(path, entries) -> None:
    """Append every `entries` record to `path`'s `entities:` list in ONE
    working-set edit.

    Deliberately NOT `upsert_list_entry` in a loop (that would stage the whole
    file once per row) and deliberately NOT a new helper in config_writer.py
    (a giant, rule 45): this is a read-merge-write with a SINGLE `write_data`,
    which in staged mode is ONE `stage_write` — the "one edit, one
    graph_changed" contract of the plan. The read and the write both go through
    config_writer, so the working set stays the one source of truth."""
    path = Path(path)
    data = read_data(path)
    items = data.setdefault("entities", [])
    if not isinstance(items, list):
        raise OSError(_("entities: in {path} is not a list — refusing to touch "
                        "it").format(path=path))
    items.extend(dict(e) for e in entries)
    write_data(path, data)


def add_entities_from_tree(hub, cell_name: str, file_path) -> None:
    """ConfigTreeDock's `add_entities_requested` delegate (part 1, Т1.5/Т1.6)."""
    root_path = hub.root_metadata_dock.root_path
    if root_path is None:
        show_message(_("Set the project root first."), _ERROR_STYLE, logger)
        return

    files = collect_graph_files(root_path)
    existing_names = _graph_entity_names(files)
    try:
        cfg, ctx = load_config(str(root_path))
    except (ValidationError, OSError) as e:
        show_message(_("Failed to load config: {error}").format(error=e),
                     _ERROR_STYLE, logger)
        return

    cell = (getattr(cfg, "cells", None) or {}).get(cell_name)
    if cell is None:
        show_message(_("No cell {name!r} in the config.").format(name=cell_name),
                     _ERROR_STYLE, logger)
        return
    cell_roles = [c.role for c in (getattr(cell, "components", None) or ())]

    sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
    connection = hub.main_window.connection

    def _taken(cluster, sheet=None):
        """The entity that already covers this INSTANCE — the project's ONE
        duplicate rule, narrowed by the instance's own sheet too (fixed
        09.10.2026): one cluster standing on several sheets is several
        instances, so an entity on Channel_0 must not mark Channel_1 spent. An
        entity WITHOUT a sheet still stands on any sheet."""
        existing = find_entity_for_source(cfg, cell=cell_name, cluster=cluster,
                                          sheet=sheet)
        return existing.name if existing is not None else None

    def _open() -> None:
        parts = snapshot_parts(getattr(connection, "snapshot", None) or [],
                               sheet_names)
        candidates = instance_candidates(parts, cell_roles, _taken)
        fits = [c for c in candidates if c.fits]
        taken = [c for c in fits if c.taken]
        lack = [c for c in candidates if not c.fits]
        logger.info(
            _("{fit} instances fit cell {cell}, {taken} taken, {lack} lack "
              "roles").format(fit=len(fits), cell=cell_name,
                              taken=len(taken), lack=len(lack)))
        if not fits:
            show_message(_("No instance of the board fits cell {cell!r}.").format(
                cell=cell_name), _ERROR_STYLE, logger)
            return

        dialog = AddEntitiesDialog(hub.main_window, cell_name, candidates,
                                   existing_names)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        _write_chosen(hub, cfg, files, cell_name, file_path,
                      dialog.result_data())

    hub.refresh_snapshot_and_push(on_ready=_open)


def _write_chosen(hub, cfg, files, cell_name, file_path, chosen) -> None:
    """Write the checked rows in ONE edit, re-checking the graph first.

    The dialog already gated its own rows; this is the authoritative pass with
    the graph in hand, exactly like "Create entity" re-checks after the form
    (the graph may have changed while the dialog was open)."""
    entries = []
    skipped = 0
    seen: set = set()
    for row in chosen:
        if not row.name or row.name in seen:
            skipped += 1
            continue
        if name_exists_in_list_section(files, "entities", row.name):
            skipped += 1
            continue
        if find_entity_for_source(cfg, cell=cell_name, cluster=row.cluster,
                                  sheet=row.sheet) is not None:
            skipped += 1
            continue
        seen.add(row.name)
        entry = {"name": row.name, "cell": cell_name}
        if row.cluster:
            entry["cluster"] = row.cluster
        if row.sheet:
            entry["sheet"] = row.sheet
        entries.append(entry)

    if not entries:
        show_message(_("Nothing to add — every checked instance was already "
                       "accounted for."), _ERROR_STYLE, logger)
        return
    try:
        write_entities_one_edit(file_path, entries)
    except OSError as e:
        show_message(_("Write failed: {error}").format(error=e),
                     _ERROR_STYLE, logger)
        return

    hub.config_tree_dock.refresh()
    hub.config_tree_dock.graph_changed.emit()
    logger.info(_("{count} entities added to cell {cell}").format(
        count=len(entries), cell=cell_name))
    if skipped:
        logger.warning(_("{count} checked instance(s) were skipped — already "
                         "accounted for.").format(count=skipped))
