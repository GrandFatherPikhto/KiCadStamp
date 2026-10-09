# gui/docks/create_entity_flow.py
"""The single "Create entity" flow behind the Config tree's context-menu item
(plan_2026_09_20_create_entity_menu.md Т1/Т2/Т3/Т4; moved out of the DockHub
giant by plan_2026_10_09_cells_and_entities, part 3, Т3.0; reordered in Т3.1).

It lives in its own module on purpose (rule 45, the shape of
gui/docks/add_entities_flow.py and gui/entity_doors.py): DockHub keeps only the
`add_entity_requested` wiring, and everything that DECIDES — which dialog, which
uniqueness rule, which file the record lands in — is here.

Config-only by design (Т4/С7): no `start_long_op`, no board read on the UI
thread — an Entity is legitimately created with KiCad closed. The record is
written to the SAME file the source lives in (Т2/С8), so the source and its
entity travel between profiles together.

Т3.1 — THE INSTANCE PICKER. For a cell the dialog offers the board's instances
of that cell (``instance_candidates``, part 1), so the caller needs the board
snapshot. The snapshot is asked of the DOOR — ``DockHub.refresh_snapshot_and_push(
on_ready=…)``, the ONE "rebuild in the worker, then act on the UI thread"
operation — and ONLY inside `on_ready` are the candidates built from
``connection.snapshot`` and the dialog opened. Nothing reads the board on the
UI thread. With KiCad closed the door still calls `on_ready` (nothing fresher to
rebuild) and the dialog OPENS ANYWAY with an empty, editable combobox and the
yellow "no board snapshot — fit not checked" line.

Т3: a suitable entity for the same source already existing WINS — the second is
not created, and the user is told which one was found. The rule is NOT restated
here: it lives in gui.docks.tree_from_selection.find_entity_for_source, the ONE
function create_cell_and_entity_for_cluster also resolves through.
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QDialog

from kicadstamp.config import load_config
from kicadstamp.config_writer import read_data, upsert_list_entry
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ._common import ERROR_STYLE as _ERROR_STYLE, WARN_STYLE as _WARN_STYLE, \
    display_path, show_message
from .instance_candidates import instance_candidates, snapshot_parts
from .rename import collect_graph_files, name_exists_in_list_section
from .tree_from_selection import find_entity_for_source

logger = logging.getLogger(__name__)


def create_entity_from_tree(hub, source_kind: str, source_name: str,
                            file_path) -> None:
    """ConfigTreeDock's `add_entity_requested` delegate (2026-09-20, plan
    plan_2026_09_20_create_entity_menu.md Т1/Т2/Т3/Т4) — the context menu's
    "Create entity" on a cells: or imprints: leaf.

    See the module docstring for the two promises this flow keeps: it never
    reads the board on the UI thread, and the duplicate rule is the ONE shared
    `find_entity_for_source`, never a second copy."""
    from .create_entity import CreateEntityDialog

    root_path = hub.root_metadata_dock.root_path
    if root_path is None:
        show_message(_("Set the project root first."), _ERROR_STYLE, logger)
        return

    # Т2: the name must be unique across the WHOLE include graph, not just
    # this file — the same rule the loader's own duplicate-name check uses.
    files = collect_graph_files(root_path)
    existing_names = set()
    for path in files:
        for item in (read_data(path).get("entities") or []):
            if isinstance(item, dict) and item.get("name"):
                existing_names.add(item["name"])

    try:
        cfg, ctx = load_config(str(root_path))
    except (ValidationError, OSError) as e:
        show_message(_("Failed to load config: {error}").format(error=e),
                     _ERROR_STYLE, logger)
        return

    source_field = "cell" if source_kind == "cell" else "imprint"
    cell_roles: list = []
    if source_field == "cell":
        cell = (getattr(cfg, "cells", None) or {}).get(source_name)
        if cell is None:
            show_message(
                _("No cell {name!r} in the config.").format(name=source_name),
                _ERROR_STYLE, logger)
            return
        # ONE entry per slot, so multiplicity is the list itself — the SAME
        # rule instance_candidates reads (cell_role_order's callers pass the
        # same shape).
        cell_roles = [c.role for c in (getattr(cell, "components", None) or ())]

    sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
    connection = hub.main_window.connection

    def _taken(cluster, sheet=None):
        """The entity that already covers this INSTANCE — the project's ONE
        duplicate rule, narrowed by the instance's own sheet too. An entity
        WITHOUT a sheet still stands on any sheet."""
        existing = find_entity_for_source(cfg, cell=source_name, cluster=cluster,
                                          sheet=sheet)
        return existing.name if existing is not None else None

    def _open() -> None:
        # Runs on the UI thread AFTER the door rebuilt the snapshot in the
        # worker (or straight away when there is no board). The board is read
        # from the pushed snapshot, never from the door here.
        snapshot = list(getattr(connection, "snapshot", None) or [])
        candidates = ()
        if source_field == "cell":
            parts = snapshot_parts(snapshot, sheet_names)
            candidates = instance_candidates(parts, cell_roles, _taken)
        dialog = CreateEntityDialog(
            hub.main_window, source_kind, source_name, existing_names,
            candidates=candidates, sheet_names=list(sheet_names.values()),
            snapshot_available=bool(snapshot))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, cluster, sheet = dialog.result_data()
        _write_entity(hub, cfg, files, file_path, source_field, source_name,
                      name, cluster, sheet)

    hub.refresh_snapshot_and_push(on_ready=_open)


def _write_entity(hub, cfg, files, file_path, source_field, source_name,
                  name, cluster, sheet) -> None:
    """Store the accepted form as an entities: record — the ONE write point.

    Т3: the duplicate check comes AFTER the form, for BOTH sources, and its key
    is the source PLUS that source's OWN second field — (cell, cluster) for a
    cell, (imprint, sheet) for an imprint. That is exactly why it cannot be
    decided before the dialog (2026-09-20, plan §Т3): one cell on two clusters
    is two legitimate Entities, and so is one imprint on two twin sheets. The
    rule itself is shared, never restated here (see find_entity_for_source)."""
    if source_field == "imprint":
        existing = find_entity_for_source(cfg, imprint=source_name, sheet=sheet)
    else:
        existing = find_entity_for_source(
            cfg, cell=source_name, cluster=cluster, sheet=sheet)
    if existing is not None:
        show_message(
            _("Entity {name!r} already exists for this source — reused, "
              "nothing new was created.").format(name=existing.name),
            _WARN_STYLE, logger)
        return

    # Т2: the name is checked against the WHOLE graph once more, now that
    # the user has had a chance to type one — the dialog's own check is a
    # convenience, this is the authoritative one.
    if name_exists_in_list_section(files, "entities", name):
        show_message(
            _("An entity named {name!r} already exists.").format(name=name),
            _ERROR_STYLE, logger)
        return

    entry = {"name": name, source_field: source_name}
    # cluster: a CELL's tag ONLY — on an imprint-based Entity it is fatal at
    # load (config/models.py:706; С3), so it is dropped here whatever the
    # form hands back, rather than trusted to have omitted the field.
    if source_field == "cell" and cluster:
        entry["cluster"] = cluster
    if sheet:
        entry["sheet"] = sheet

    try:
        upsert_list_entry(file_path, "entities", entry,
                          key_fn=lambda e: e.get("name"))
    except OSError as e:
        show_message(_("Write failed: {error}").format(error=e),
                     _ERROR_STYLE, logger)
        return

    hub.config_tree_dock.refresh()
    hub.config_tree_dock.graph_changed.emit()
    show_message(
        _("Entity {name!r} saved to {path}.").format(
            name=name, path=display_path(file_path)),
        "", logger)
