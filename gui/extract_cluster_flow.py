# gui/extract_cluster_flow.py
"""Tools → Trees → "Extract cluster…" — the flow behind ONE fully-selected
Cluster becoming a standalone flat Entity (2026-09-03, plan extract_cluster_entity),
moved OUT of the dock_hub giant on 2026-10-20 (Ч3 of plan_2026_10_15_extract_and
_read_paths_cleanup).

Why the move: the flow used to read the live board from the UI thread — the
shared adapter went straight into `create_cell_and_entity_for_cluster`, which
runs `extract_template_from_selection` (one Role field read PER component, plus
the pads of every kept track's endpoints). `tools/door_lint.py --all` named it
as the last Extract-line suspect. Now:

* `run_extract_cluster_worker` is the ONLY half that touches a board, and it
  builds its OWN adapter (the `gui/select_cell_copper.py` pattern: create, one
  `refresh_board()`, `close()` in `finally`) — so the shared kipy socket is not
  borrowed behind the UI's back and no `ui_thread_board_read` sign is needed;
* the UI half only runs CHECKS, shows the dialog, and WRITES the config
  (backup_file + read_data + write_data, the same read-merge-write contract
  every config_writer-based dialog in this project uses);
* the nine modal windows the flow used to open are Log lines (Denis 15.10:
  dialogs are interruptions; the Log is the one place a dock reports), with the
  severities the Ч0 map assigned: Not connected / Set the project root / no
  fully-selected Cluster — WARNING; config load and save failures — ERROR;
  "reused" and "saved" — info.

One window of this flow is deliberately NOT converted and is named in the Ч3
report: `DockHub._show_snapshot_refresh_error` — the shared error path of
`_refresh_snapshot_then`, which BOTH Extract flows use; it stays a modal until
the Extract-tree flow gets its own part.

The dialog is still MODAL and still on the UI thread, and the cluster list is
still computed from `connection.snapshot` (MEMBERSHIP only, no geometry): the
human picks the cluster between the two worker steps.
"""
import logging

from PyQt6.QtWidgets import QDialog

from kicadstamp.i18n import _

from .connection import worker_timeout_ms
from .docks._common import ERROR_STYLE as _ERROR_STYLE, show_message
from .docks.extract_diagnostics import (format_cluster_rejections,
                                       rejections_log_detail)
from .worker import start_long_op

logger = logging.getLogger(__name__)


# ── The worker: the ONLY place this flow touches the board ──────────────────

def run_extract_cluster_worker(payload: dict) -> dict:
    """start_long_op worker entry — plain data in, plain data out.

    Builds its OWN board adapter (a pynng REQ socket is single-owner, so it
    cannot be the UI's poll adapter; `start_long_op` holds the long-op token for
    the whole call anyway) and closes it in `finally` — on the happy path AND
    when a read raises through it.

    The config is re-read HERE, fresh: `resolve_cluster_entity` decides whether
    the Entity already exists, and that answer must not come from a Config the
    UI loaded before the dialog was open. `cells` starts EMPTY on purpose, so
    the dict that comes back carries only the cells this run generated — the UI
    half merges exactly those into the file it re-reads before writing.

    Returns {"entity": dict | None, "cells": {name: dict}, "cell_new": bool} or
    {"error": str}. `entity is None` means the (cluster, sheet) Entity already
    exists — the reuse branch, nothing to write."""
    from kicadstamp.adapter_factory import create_board_adapter
    from kicadstamp.config import load_config

    from .docks.tree_from_selection import create_cell_and_entity_for_cluster

    adapter = None
    try:
        cfg, _ctx = load_config(payload["root_path"])
        adapter = create_board_adapter(timeout_ms=payload["timeout_ms"])
        adapter.refresh_board()
        cells: dict = {}
        ent = create_cell_and_entity_for_cluster(
            adapter, payload["cluster"], cfg, cells,
            payload.get("selection_footprints") or [],
            payload.get("selection_raw_items") or [],
            entity_name=payload.get("entity_name"),
            origin_role=payload.get("origin_role"),
            origin_pad=payload.get("origin_pad"))
        return {"entity": ent, "cells": cells,
                "cell_new": bool(ent and ent["cell"] not in cfg.cells)}
    except Exception as e:  # noqa: BLE001 — reported as a Log line, no modal
        return {"error": str(e)}
    finally:
        if adapter is not None:
            adapter.close()


# ── The UI half ─────────────────────────────────────────────────────────────

class ExtractClusterFlow:
    """The UI half of "Extract cluster…", as a collaborator of DockHub.

    Holds NO state of its own: every piece it needs (the selection, the root
    path, the config tree dock, the snapshot rebuild point) stays on the hub —
    the same shape as `gui/explode_wiring.py`."""

    def __init__(self, hub):
        self._hub = hub

    # -- start ---------------------------------------------------------
    def start(self) -> None:
        """Menu entry. R.2.2: the fully-selected-Cluster detection must not run
        against a snapshot frozen at connect time, so the SHARED rebuild point
        refreshes it on a worker first, then the UI half continues."""
        self._hub._refresh_snapshot_then(_("Extract cluster"), self._now)

    def _now(self) -> None:
        hub = self._hub
        connection = hub.main_window.connection
        if connection is None or not getattr(connection, "is_connected", False):
            show_message(_("Not connected."), _ERROR_STYLE, logger)
            return
        root_path = hub.root_metadata_dock.root_path
        if root_path is None:
            show_message(_("Set the project root first."), _ERROR_STYLE, logger)
            return
        from kicadstamp.config import load_config
        try:
            cfg, ctx = load_config(str(root_path))
        except Exception as e:  # noqa: BLE001 — a broken config must not crash the GUI
            show_message(_("Failed to load config: {error}").format(error=e),
                         _ERROR_STYLE, logger)
            return
        sheet_names = dict(ctx.sheet_names or {})

        from .docks.reead import fully_selected_clusters
        # V.2: the same structured diagnostics as "Extract tree..." — the
        # concrete cause, not the generic "select more" text. Membership only:
        # no geometry is read out of the snapshot here.
        rejections: list = []
        clusters = fully_selected_clusters(
            hub._selection_footprints,
            list(connection.snapshot or []),
            list(cfg.entities),
            (),
            sheet_names=sheet_names,
            rejections=rejections)
        # Diagnostic + defensive filter (same rationale as "Extract tree...": a
        # row must be a sane single-line cluster).
        clusters = [c for c in clusters if c.cluster and "\n" not in c.cluster]
        if not clusters:
            detail = format_cluster_rejections(rejections)
            if detail:
                logging.info("Extract cluster: no fully selected cluster — full "
                             "detail:\n%s", rejections_log_detail(rejections))
            show_message(
                detail or _("No fully selected Cluster found — select ALL "
                            "components of a cluster (its Cluster tag + sheet) "
                            "first."),
                _ERROR_STYLE, logger)
            return

        from .docks.extract_cluster_dialog import ExtractClusterDialog
        dialog = ExtractClusterDialog(
            hub.main_window, clusters, cfg, hub._selection_footprints)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        c = dialog.selected_cluster()
        if c is None:
            return
        entity_name = dialog.entity_name()

        # An Entity already exists for (cluster, sheet): reuse — nothing to
        # write, just refresh + say so.
        if dialog.existing:
            hub.config_tree_dock.refresh()
            self._say_reused(entity_name)
            return
        # Manual origin override (2026-09-04, plan extract_origin_pad_restore):
        # only read when a NEW Entity (+ its cell) is actually created.
        origin_role, origin_pad = dialog.origin_override()

        payload = {
            "root_path": str(root_path),
            "cluster": c,
            "entity_name": entity_name,
            "origin_role": origin_role,
            "origin_pad": origin_pad,
            # COPIES: no live mapping or kipy object crosses the thread boundary
            # — these are domain records out of the board snapshot.
            "selection_footprints": list(hub._selection_footprints or []),
            "selection_raw_items": list(hub._selection_raw_items or []),
            "timeout_ms": worker_timeout_ms(connection),
        }
        hub._extract_cluster_op = start_long_op(
            connection, (), run_extract_cluster_worker,
            lambda result: self._finish(result, root_path, c, entity_name),
            self._failed, payload, busy_text=_("reading the board"))

    # -- the worker's result, on the UI thread -------------------------
    def _finish(self, result: dict, root_path, c, entity_name: str) -> None:
        hub = self._hub
        hub._extract_cluster_op = None
        if result.get("error"):
            show_message(_("Failed to extract the cluster: {error}").format(
                error=result["error"]), _ERROR_STYLE, logger)
            return
        ent = result.get("entity")
        if ent is None:
            # A matching Entity appeared between the dialog and the read —
            # treat it as a reuse, never a duplicate.
            hub.config_tree_dock.refresh()
            self._say_reused(entity_name)
            return

        from kicadstamp.config_writer import read_data, write_data

        from .docks.entity_delete import backup_file
        try:
            backup_file(root_path)
            data = read_data(root_path)
            data.setdefault("cells", {}).update(result.get("cells") or {})
            data.setdefault("entities", []).append(ent)
            write_data(root_path, data)
        except Exception as e:  # noqa: BLE001 — .bak is fresh; report, don't roll back
            show_message(_("Failed to save the Entity: {error}").format(error=e),
                         _ERROR_STYLE, logger)
            return

        # Phase E (plan_2026_09_09_..._phase_e): remember the (Cluster, Sheet)
        # this cell was just created/extracted from — the page-opening hint for
        # the cell-anchor page and the CellDock "Select cluster on the board"
        # button. GUI-local state only, never written into the cell itself.
        from .cell_edit_context import remember_cell_edit_context
        remember_cell_edit_context(root_path, ent["cell"], c.cluster, c.sheet)

        # The new cells:/entities: appear without a restart.
        hub.config_tree_dock.refresh()
        hub.config_tree_dock.graph_changed.emit()
        if result.get("cell_new"):
            show_message(_("Entity {name!r} with its new cell {cell!r} saved to {path}.")
                         .format(name=ent["name"], cell=ent["cell"], path=root_path),
                         "", logger)
        else:
            show_message(_("Entity {name!r} saved to {path} (cell {cell!r} already "
                           "existed).")
                         .format(name=ent["name"], cell=ent["cell"], path=root_path),
                         "", logger)

    def _failed(self, message: str) -> None:
        """start_long_op's own failure path — the worker could not even return a
        result (a dead socket, a killed thread). One Log line, no modal."""
        self._hub._extract_cluster_op = None
        show_message(_("Failed to extract the cluster: {error}").format(error=message),
                     _ERROR_STYLE, logger)

    def _say_reused(self, entity_name: str) -> None:
        """The ONE wording of the reuse answer — both branches (the dialog
        already knew, and the Entity appeared during the read) say it the same."""
        show_message(_("Entity {name!r} already exists for this Cluster + sheet — "
                       "reused, nothing new was created.").format(name=entity_name),
                     "", logger)
