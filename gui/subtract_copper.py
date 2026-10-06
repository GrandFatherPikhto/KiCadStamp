# gui/subtract_copper.py
"""«Subtract selected copper» — the WHOLE flow (С-2 and С-2а of
plan_2026_10_06_prune_absent_cell_copper; Denis 2026-10-06).

The action takes the CURRENT board selection, works out which of the edited cell's
records that copper IS (the exact pairing of «Select cell» — see
`kicadstamp/absent_copper_prune.py`), and hands the caller the records to drop. It
never touches components, never writes the registry, and never guesses: the
DECISION itself lives in `kicadstamp/subtract_selection.py` (Qt-free, pure).

ONE owner of the flow, modelled on `gui/explode_wiring.py` (Р3а-6, 3f74692b):

  * the READ — `SubtractWiring.open`: the guards, the payload, `start_long_op`;
  * the WORKER — `run_subtract_worker`: plain data in, plain data out;
  * the APPLICATION — `SubtractWiring.finish`: drop the records by identity from
    the cell's OWN lists AND from the `vias` list of the component that carries
    them (a cell's copper has TWO levels — `kicadstamp/subtract_selection.py`);
  * the REPORT — `subtract_report_lines`: PURE, a result dict in and
    ``[(text, level)]`` out, with no Qt type and no widget anywhere in it.

`gui/docks/cell_editor.py` keeps ONE-LINE delegates with the SAME names, because
the dock's button, the Config tree's context item and the cells call THOSE names.
A CellDock is handed in (a COLLABORATOR, not a copy of it): every call goes
through the dock's own attributes, so there is still exactly ONE dock state — and
no widget is built here (§45: a giant keeps wiring only).

Board touches, all on the WORKER (`gui.worker.start_long_op`):
  * the selection is read through an adapter the worker creates and closes in a
    ``finally``;
  * each candidate instance's dry run builds its OWN ``ApplyPipeline`` inside
    `absent_copper_prune.instance_record_copper_map` (its own socket, closed there).

Instance: the ONE rule of «Select cell» (`resolve_action_instance`) — explicit,
remembered, else the config's single record. Several records is a CHOICE: the
instance whose dry run matched the selected copper is taken; two matching is an
ambiguity the caller REFUSES red (never a guess — the record of the wrong
instance would take copper away from a live one); and when the runs DID plan but
none of them matched, the answer is the ordinary "nothing to subtract" (С-2а-2).
"""
from __future__ import annotations

import logging

from kicadstamp.i18n import _

from .connection import worker_timeout_ms
from .docks._common import ERROR_STYLE as _ERROR_STYLE, style_for_level
from .worker import start_long_op

logger = logging.getLogger(__name__)

__all__ = ["SubtractWiring", "run_subtract_worker", "subtract_report_lines"]


def _instance_label(cluster, sheet) -> str:
    return _("{cluster} on {sheet}").format(
        cluster=cluster,
        sheet=sheet if sheet is not None else _("(no sheet)"))


def _selection(payload, adapter) -> tuple:
    """(selected vias, tracks, footprints, uuids) — the split the action needs."""
    from kicadstamp.domain.board import Footprint, Track, Via

    items = adapter.get_selected_items()
    vias = [i for i in items if isinstance(i, Via)]
    tracks = [i for i in items if isinstance(i, Track)]
    footprints = [i for i in items if isinstance(i, Footprint)]
    uuids = {getattr(i, "uuid", None) for i in vias + tracks} - {None}
    return vias, tracks, footprints, uuids


def run_subtract_worker(payload: dict) -> dict:
    """start_long_op worker — plain data in, plain data out (no widget).

    Returns one of:
      {"removed": [(kind, record), ...], "not_ours": n, "components": k,
       "planned": p, "cluster": c, "sheet": s, "cell": name}
      {"empty": True, ...}      — EVERY candidate's dry run planned nothing:
                                  subtract NOTHING (the check could not run)
      {"ambiguous": [labels...]} — several instances matched: refuse
      {"no_copper": True, "components": k} — only components are selected
      {"error": text}
    """
    from kicadstamp.absent_copper_prune import instance_record_copper_map
    from kicadstamp.adapter_factory import create_board_adapter
    from kicadstamp.config import load_config
    from kicadstamp.registry import record_key_part
    from kicadstamp.subtract_selection import (
        matched_instance_labels,
        plan_subtraction,
    )

    from .select_cell import instance_recording_name, resolve_action_instance

    root = payload["root_path"]
    cell_name = payload["cell_name"]
    try:
        cfg, _ctx = load_config(root)
    except Exception as e:  # noqa: BLE001 — reported as a Log line, no modal
        return {"error": str(e)}

    choice = resolve_action_instance(cfg, root, cell_name, payload.get("cluster"),
                                     payload.get("sheet"), None)
    if choice.kind == "no-record":
        return {"error": choice.message}
    cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
    cell_identity = record_key_part(cell_name, getattr(cell, "uuid", None))

    adapter = None
    try:
        adapter = create_board_adapter(timeout_ms=payload["timeout_ms"],
                                       config_path=payload.get("config_path"))
        adapter.refresh_board()
        vias, tracks, footprints, selected = _selection(payload, adapter)
        if not selected:
            return {"no_copper": True, "components": len(footprints),
                    "cell": cell_name}
        instances = (list(choice.candidates) if choice.kind == "choose"
                     else [(choice.cluster, choice.sheet)])
        maps: list = []
        for cluster, sheet in instances:
            record = instance_recording_name(cfg, cell_name, cluster, sheet)
            if record is None:
                continue
            maps.append((_instance_label(cluster, sheet), cluster, sheet,
                         instance_record_copper_map(root, record,
                                                    payload["timeout_ms"],
                                                    cell_identity)))
        if not maps:
            # No record places the cell at any candidate instance (a chain-only
            # placement): the dry run could not check it, so nothing is subtracted.
            return {"empty": True, "planned": 0, "cell": cell_name,
                    "cluster": choice.cluster, "sheet": choice.sheet}
        if choice.kind == "choose":
            labels = matched_instance_labels(
                [(label, rmap) for label, _c, _s, rmap in maps], selected)
            if len(labels) > 1:
                return {"ambiguous": list(labels), "cell": cell_name}
            if labels:
                label = labels[0]
            elif all(rmap.empty for _l, _c, _s, rmap in maps):
                # EVERY candidate's dry run planned NOTHING: the check could not
                # run at all — subtract nothing and let the caller say why.
                return {"empty": True, "planned": 0, "cell": cell_name}
            else:
                # The runs DID plan, but the selection is not this cell's copper.
                # That is the SAME answer one instance gives — "nothing to
                # subtract" plus the ignored count — never the red "could not
                # match" line, which would claim a check that never happened. The
                # counters are identical for every run here: no run holds a
                # selected uuid, so `removed` is empty whichever is taken; take a
                # NON-empty one, because an empty map answers "nothing was
                # planned" on its own. That run's cluster/sheet ride along in the
                # answer, but nothing was removed and no Log line names them.
                label = next(m[0] for m in maps if not m[3].empty)
        else:
            label = maps[0][0]
        chosen = next(m for m in maps if m[0] == label)
        _label, cluster, sheet, record_map = chosen
        if record_map.empty:
            return {"empty": True, "planned": 0, "cell": cell_name,
                    "cluster": cluster, "sheet": sheet}
        outcome = plan_subtraction(
            record_map, payload.get("vias") or [], payload.get("tracks") or [],
            {getattr(v, "uuid", None) for v in vias},
            {getattr(t, "uuid", None) for t in tracks},
            selected_components=len(footprints),
            components=payload.get("components") or [])
        return {"cell": cell_name, "cluster": cluster, "sheet": sheet,
                "removed": list(outcome.removed), "not_ours": outcome.not_ours,
                "components": outcome.components, "planned": outcome.planned}
    except Exception as e:  # noqa: BLE001 — reported as a Log line, no modal
        return {"error": str(e)}
    finally:
        if adapter is not None:
            adapter.close()


def subtract_report_lines(result: dict) -> list:
    """The Log lines for a finished subtraction: ``[(text, level), ...]``.

    PURE: the level is the NAME the dock maps to its own style
    (`gui.docks._common.style_for_level`) — the same "success"/"warn"/"error"
    vocabulary the mixed-selection prelude already speaks — so this module needs
    no widget and no style constant. ONE place for every branch of the worker's
    answer, so the CellDock never grows a second copy of a message.

    The record lines reuse the ONE formatter of the refresh/import report
    (`record_report_line`), imported HERE: the giant imports this module at
    start-up, so a module-level import would close a cycle.
    """
    from .docks.cell_editor import _problem_lines, record_report_line

    cell = result.get("cell", "?")
    if result.get("error"):
        # Н4.7: a collected fatal is one red line per problem, never a dialog.
        return [(line, "error") for line in _problem_lines(result["error"])]
    if result.get("no_copper"):
        return [(_("no copper is selected — nothing was subtracted "
                   "({count} component(s) are ignored)").format(
                       count=result.get("components", 0)), "warn")]
    if result.get("ambiguous"):
        return [(_("the selection matches {count} instances of cell {cell!r} "
                   "({labels}) — select the copper of ONE instance and subtract "
                   "again").format(count=len(result["ambiguous"]), cell=cell,
                                   labels="; ".join(result["ambiguous"])),
                 "error")]
    if result.get("empty"):
        return [(_("could not match the selection to the cell's records — the dry "
                   "run planned nothing"), "error")]
    removed = list(result.get("removed") or ())
    lines: list = []
    if removed:
        lines += [(record_report_line("-", record, kind), "warn")
                  for kind, record in removed]
        lines.append((_("subtracted {count} record(s) — Save to write the change")
                      .format(count=len(removed)), "success"))
    else:
        lines.append((_("nothing to subtract — the selection holds no copper "
                        "record of cell {cell!r}").format(cell=cell), "success"))
    if result.get("not_ours"):
        lines.append((_("{count} selected item(s) are not records of cell {cell!r} "
                        "— ignored").format(count=result["not_ours"], cell=cell),
                      "warn"))
    return lines


class SubtractWiring:
    """See the module docstring: the ONE host of the «Subtract selected copper»
    flow. Its only collaborator is the CellDock handed to the constructor."""

    def __init__(self, dock) -> None:
        self._dock = dock

    # ── the door ────────────────────────────────────────────────────────────
    def open(self) -> None:
        """Button / context action: subtract the cell records the selection names.

        No layer dialog: the pairing is by each record's own live copper (the
        registry uuid, then exact geometry), so layers never enter it — and only
        copper is subtracted, never a component."""
        dock = self._dock
        connection = getattr(dock._main_window, "connection", None)
        # Door contract (gui/connection.py's own refusal text): a PRESENCE check
        # belongs on connection.is_connected, never on connection.board. This
        # action needs no board handle on the UI thread at all — the worker builds
        # its OWN adapter and closes it.
        if connection is None or not getattr(connection, "is_connected", False):
            dock._show_message(_("Connect to KiCad first."), _ERROR_STYLE)
            return
        if not dock._components:
            dock._show_message(_("Load a cell with components first."), _ERROR_STYLE)
            return
        if dock._path is None:
            dock._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        if dock._active_op is not None:
            return
        payload = {
            "timeout_ms": worker_timeout_ms(connection),
            "components": list(dock._components),
            "vias": list(dock._vias),
            "tracks": list(dock._tracks),
            "root_path": str(dock._root_path) if dock._root_path else None,
            "cell_name": dock.name_edit.text().strip(),
            "cluster": dock._remembered_cluster_value(),
            "sheet": dock._remembered_sheet_value(),
        }
        dock._active_op = start_long_op(
            connection, (dock.subtract_copper_button,),
            self.run, self.finish, self.failed, payload)

    # ── the worker and the two halves of its answer ──────────────────────────
    def run(self, payload: dict) -> dict:
        """Worker thread: the whole decision lives in the Qt-free modules."""
        return run_subtract_worker(payload)

    def finish(self, result: dict) -> None:
        """UI thread: apply the worker's decision, then print its report.

        The records leave the cell BEFORE the report is printed, exactly like the
        refresh/import halves — the report describes what happened, not a plan."""
        dock = self._dock
        dock._active_op = None
        removed = list(result.get("removed") or ())
        if removed:
            self._apply_removed(removed)
        for text, level in subtract_report_lines(result):
            dock._show_message(text, style_for_level(level))

    def failed(self, message: str) -> None:
        """The worker itself died (a crashed thread, not a described refusal)."""
        self._dock._active_op = None
        self._dock._show_message(
            _("Subtract selected copper failed: {error}").format(error=message),
            _ERROR_STYLE)

    def requested(self, name: str, file_path) -> None:
        """ConfigTreeDock's cell_subtract_requested delegate (С-2): the context
        menu's "Subtract selected copper..." — load the requested cell when it is
        not the one open, then run the SAME read as the dock's own button."""
        dock = self._dock
        if dock.name_edit.text().strip() != name:
            dock.load_entry(name, file_path)
        self.open()

    # ── the application of the verdict ──────────────────────────────────────
    def _apply_removed(self, removed: list) -> None:
        """Drop the subtracted records by IDENTITY from the cell's OWN lists AND
        from the `vias` list of every component that carries them.

        WHY both levels (С-2а-1, the blocker): the map key says which level the
        record belongs to (`kicadstamp/subtract_selection.py`) — the spoke-level
        placeholder indexes the cell's own `vias`/`tracks`, a ROLE indexes THAT
        COMPONENT's `vias`. Dropping only from the cell's lists left a component's
        via in place while the Log claimed it was subtracted: the next Save wrote
        it straight back. Only `vias` needs the second level — a component slot
        carries no tracks (TemplateComponentSlot).

        The component dicts here are the very ones the worker was handed (the
        payload carried `list(dock._components)`, and `load_entry` only
        SHALLOW-copies each slot), so `id()` finds the record in both. Then the
        tables and the autostage, exactly like a manual row Delete. Nothing is
        written to disk here."""
        dock = self._dock
        vias = [r for kind, r in removed if kind == "via"]
        tracks = [r for kind, r in removed if kind == "track"]
        dock._drop_records(vias, dock._vias)
        dock._drop_records(tracks, dock._tracks)
        for component in dock._components:
            component_vias = component.get("vias")
            if component_vias:
                dock._drop_records(vias, component_vias)
        dock._refresh_all_tables()
        dock._autostage()
