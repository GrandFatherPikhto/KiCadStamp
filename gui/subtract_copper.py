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

When the DRIFT GUARD refused the tree, the dry run plans nothing and the exact
pair cannot be built — so the map falls back to the REGISTRY ALONE
(`kicadstamp.absent_copper_prune.registry_record_copper_map`, the SAME
`is_own_key` filter «Select cell»'s fallback uses). The guard is never bypassed
and the config/registry/board are only READ. The red "planned nothing" line then
stays only for the instance where the dry run AND the registry are both empty
(plan_2026_10_07_refused_tree_matching).
"""
from __future__ import annotations

import logging

from kicadstamp.i18n import _

from .entity.address import read_instance_of
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


def _empty_result(entry, cell_name: str) -> dict:
    """The "nothing could be checked" answer for ONE candidate entry
    ``(label, cluster, sheet, record, map)``.

    It carries the map's ``source``/``without_registry`` so the report can still
    say how many records the registry had no key for — the reason the map is
    empty is named, never silent."""
    _label, cluster, sheet, record, record_map = entry
    return {"empty": True, "planned": 0, "cell": cell_name,
            "cluster": cluster, "sheet": sheet,
            "source": record_map.source, "record": record or "",
            "not_checked": record_map.without_registry,
            **_refusal_counts(record_map)}


def _refused_anything(result: dict) -> bool:
    """True when the map DID offer pairs and the check refused them.

    The report needs the difference: an empty map with nothing offered is "we
    could not check" (the red line), while an empty map whose pairs were REFUSED
    is "we checked and refused" — saying "the dry run planned nothing" would then
    name the wrong reason (plan_2026_10_07_registry_pair_frame_check, step 4).

    A non-rigid frame counts only when the registry DID have own keys
    (``entries_total``): with no key at all nothing was refused, so that stays the
    red "nothing to check" answer."""
    return bool(result.get("disagreed") or result.get("orphan_keys")
                or (result.get("not_rigid") and result.get("entries_total")))


def _refusal_counts(record_map) -> dict:
    """The reasons the map did NOT take a pair the registry offered, as plain
    data — the ONE place the worker reads them, so a caller (the report, a
    future one) never has to know the map's field names."""
    return {"disagreed": record_map.disagreed,
            "orphan_keys": record_map.orphan_keys,
            "not_rigid": record_map.not_rigid,
            "frame_residual_mm": record_map.frame_residual_mm,
            "entries_total": record_map.entries_total}


def run_subtract_worker(payload: dict) -> dict:
    """start_long_op worker — plain data in, plain data out (no widget).

    For EVERY candidate instance the map is the EXACT pair of «Select cell» (the
    instance recording's own dry run). When that dry run is EMPTY — a tree the
    drift guard refused (the guard is NEVER bypassed: a leaked "materialize the
    refused tree" flag would bring the live 4.75 mm ``fpga`` drift back into a
    real apply), an unrealized record, a chain-only placement — the map is built
    by the REGISTRY ALONE (`registry_record_copper_map`, the SAME ``is_own_key``
    filter «Select cell»'s fallback uses). The red "planned nothing" answer
    therefore stays ONLY for the instance where BOTH the dry run AND the registry
    are empty: nothing could be checked at all.

    Returns one of:
      {"removed": [(kind, record), ...], "not_ours": n, "components": k,
       "planned": p, "cluster": c, "sheet": s, "cell": name,
       "source": "dry_run"|"registry", "record": rec, "not_checked": n}
      {"empty": True, ...}      — every candidate's dry run AND registry are
                                  empty: subtract NOTHING (nothing was checked)
      {"ambiguous": [labels...]} — several instances matched: refuse
      {"no_copper": True, "components": k} — only components are selected
      {"error": text}
    """
    from kicadstamp.absent_copper_prune import (
        RecordCopperMap,
        instance_record_copper_map,
        registry_record_copper_map,
    )
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
        cfg, ctx = load_config(root)
    except Exception as e:  # noqa: BLE001 — reported as a Log line, no modal
        return {"error": str(e)}
    sheet_names = dict(getattr(ctx, "sheet_names", None) or {})

    choice = resolve_action_instance(cfg, root, cell_name, payload.get("cluster"),
                                     payload.get("sheet"), payload.get("refs"))
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
            # A chain-only placement has no record to dry-run: the map starts
            # empty and the registry fallback below still gets its chance.
            record_map = (instance_record_copper_map(root, record,
                                                     payload["timeout_ms"],
                                                     cell_identity)
                          if record is not None else RecordCopperMap())
            if record_map.empty:
                # The dry run planned nothing (a refused tree / an unrealized
                # record): fall back to the registry for the SAME instance.
                record_map = registry_record_copper_map(
                    adapter, root, cfg, cell_name, cluster, sheet,
                    own_refs=payload.get("refs"), sheet_names=sheet_names)
            maps.append((_instance_label(cluster, sheet), cluster, sheet,
                         record, record_map))
        if choice.kind == "choose":
            labels = matched_instance_labels(
                [(label, rmap) for label, _c, _s, _r, rmap in maps], selected)
            if len(labels) > 1:
                return {"ambiguous": list(labels), "cell": cell_name}
            if labels:
                label = labels[0]
            elif all(rmap.empty for _l, _c, _s, _r, rmap in maps):
                # EVERY candidate's dry run AND registry are empty: nothing could
                # be checked at all — subtract nothing and let the caller say why.
                return _empty_result(maps[0], cell_name)
            else:
                # The runs DID plan (or the registry DID hold copper), but the
                # selection is not this cell's copper. That is the SAME answer one
                # instance gives — "nothing to subtract" plus the ignored count —
                # never the red "could not match" line, which would claim a check
                # that never happened. Take a NON-empty map, because an empty one
                # answers "nothing was planned" on its own.
                label = next(m[0] for m in maps if not m[4].empty)
        else:
            label = maps[0][0]
        chosen = next(m for m in maps if m[0] == label)
        _label, cluster, sheet, record, record_map = chosen
        if record_map.empty:
            return _empty_result(chosen, cell_name)
        outcome = plan_subtraction(
            record_map, payload.get("vias") or [], payload.get("tracks") or [],
            {getattr(v, "uuid", None) for v in vias},
            {getattr(t, "uuid", None) for t in tracks},
            selected_components=len(footprints),
            components=payload.get("components") or [])
        return {"cell": cell_name, "cluster": cluster, "sheet": sheet,
                "removed": list(outcome.removed), "not_ours": outcome.not_ours,
                "components": outcome.components, "planned": outcome.planned,
                "source": record_map.source, "record": record or "",
                "not_checked": record_map.without_registry,
                **_refusal_counts(record_map)}
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

    A map matched by the registry alone (a refused tree) adds ONE yellow line
    saying so — the REASON (the drift guard's own message) is already a red line
    in the Log and is NEVER repeated here — plus, when present, the honest "N
    record(s) have no registry entry — not checked" count and the refusal notes
    of `absent_copper_prune.not_checked_reasons` (a pair that does not sit where
    the record puts it, an ORPHAN key, a non-rigid frame): the ONE wording
    «Select cell» uses too, so the same refusal reads the same on both doors.

    The record lines reuse the ONE formatter of the refresh/import report
    (`record_report_line`), imported HERE: the giant imports this module at
    start-up, so a module-level import would close a cycle.
    """
    from kicadstamp.absent_copper_prune import not_checked_reasons

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
    lines: list = []
    if result.get("source") == "registry" and result.get("planned", 0) > 0:
        lines.append((_("the redraw planner produced no copper for record "
                        "{record} (reason — in the Log above); matched by the "
                        "registry only").format(
                            record=result.get("record") or "?"), "warn"))
    refused = _refused_anything(result)
    if result.get("empty"):
        # Nothing was subtracted either way, but WHY differs: a map with no pair
        # at all is "we could not check" (red); a map whose offered pairs the
        # place check refused is "we checked and refused" — the yellow reasons
        # below name each refusal, and claiming "the dry run planned nothing"
        # would name the wrong one.
        if refused:
            lines.append((_("nothing was subtracted — no pair of the registry "
                            "could be checked"), "warn"))
        else:
            lines.append((_("could not match the selection to the cell's records "
                            "— the dry run planned nothing"), "error"))
    else:
        removed = list(result.get("removed") or ())
        if removed:
            lines += [(record_report_line("-", record, kind), "warn")
                      for kind, record in removed]
            lines.append((_("subtracted {count} record(s) — Save to write the "
                            "change").format(count=len(removed)), "success"))
        else:
            lines.append((_("nothing to subtract — the selection holds no copper "
                            "record of cell {cell!r}").format(cell=cell),
                          "success"))
        if result.get("not_ours"):
            lines.append((_("{count} selected item(s) are not records of cell "
                            "{cell!r} — ignored").format(
                                count=result["not_ours"], cell=cell), "warn"))
    if result.get("not_checked"):
        lines.append((_("{count} record(s) have no registry entry — not checked")
                      .format(count=result["not_checked"]), "warn"))
    for note in not_checked_reasons(
            not_rigid=bool(result.get("not_rigid")),
            frame_residual_mm=result.get("frame_residual_mm"),
            disagreed=int(result.get("disagreed") or 0),
            orphan_keys=int(result.get("orphan_keys") or 0)):
        lines.append((note, "warn"))
    return lines


class SubtractWiring:
    """See the module docstring: the ONE host of the «Subtract selected copper»
    flow. Its only collaborator is the CellDock handed to the constructor."""

    def __init__(self, dock) -> None:
        self._dock = dock

    # ── the door ────────────────────────────────────────────────────────────
    def open(self, expected_address=None) -> None:
        """Button / context action: subtract the cell records the selection names.

        `expected_address` is the DOOR's address (step 5): the dock's own entry
        passes the address it was LOADED with; a CELL leaf passes nothing and the
        refusal inside `_open_with_instance` answers — nothing is read."""
        self._open_with_instance(expected_address)

    def _open_with_instance(self, expected_address) -> None:
        """The ONE subtraction door (С-2; часть 3, п.2 grew it an address).

        `expected_address` is the address a BOARD door handed over — the entity
        leaf's own, resolved by gui/entity_doors.door_address. Given, it IS the
        instance (the working-instance store is not consulted); None keeps the
        cell's remembered context, exactly as before.

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
        # Step 5 of plan_2026_10_09_entity_page: the address comes from the DOOR
        # and ONLY from it — the working-instance store is gone. A CELL leaf names
        # no instance, so NOTHING is read: never a fallback to a remembered pair.
        if expected_address is None:
            dock._show_message(_("Subtract needs an entity's address — open it "
                                 "from an ENTITY leaf."), _ERROR_STYLE)
            return
        instance = read_instance_of(expected_address)
        payload = {
            "timeout_ms": worker_timeout_ms(connection),
            "components": list(dock._components),
            "vias": list(dock._vias),
            "tracks": list(dock._tracks),
            "root_path": str(dock._root_path) if dock._root_path else None,
            # С-2а-4: the worker's own adapter must be built for THIS profile,
            # exactly like every other board-touching worker (gui/docks/cascade.py
            # passes its config_path) — without this key the factory was handed
            # None and fell back to its default.
            "config_path": str(dock._root_path) if dock._root_path else None,
            "cell_name": dock.name_edit.text().strip(),
            "cluster": instance.cluster,
            "sheet": instance.sheet,
            # доделка 3а, п.2: the entity's pins ride in the payload — the
            # registry fallback narrows which `anchor:<ref>` keys are this cell's.
            "refs": dict(instance.refs) if instance.refs else None,
        }
        dock._active_op = start_long_op(
            connection, (),
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

    def requested(self, name: str, file_path, expected_address=None) -> None:
        """ConfigTreeDock's cell_subtract_requested delegate (С-2): the context
        menu's "Subtract selected copper..." — load the requested cell when it is
        not the one open, then run the SAME read as the dock's own button.
        `expected_address` (часть 3, п.2) is the entity leaf's own address."""
        dock = self._dock
        if dock.name_edit.text().strip() != name:
            dock.load_entry(name, file_path)
        self._open_with_instance(expected_address)

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
