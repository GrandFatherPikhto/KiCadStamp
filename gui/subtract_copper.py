# gui/subtract_copper.py
"""«Subtract selected copper» — the GUI half (С-2 of
plan_2026_10_06_prune_absent_cell_copper; Denis 2026-10-06).

The worker drives ONE action: take the CURRENT board selection, find out which of
the edited cell's records that copper IS (the exact pairing of «Select cell» — see
`kicadstamp/absent_copper_prune.py`), and hand the caller the records to drop. It
never touches components, never writes the registry, and never guesses: the decision
itself lives in `kicadstamp/subtract_selection.py` (Qt-free, pure).

Board touches, all on the WORKER (`gui.worker.start_long_op`):
  * the selection is read through an adapter this module creates and closes in a
    ``finally``;
  * each candidate instance's dry run builds its OWN ``ApplyPipeline`` inside
    `absent_copper_prune.instance_record_copper_map` (its own socket, closed there).

Instance: the ONE rule of «Select cell» (`resolve_action_instance`) — explicit,
remembered, else the config's single record. Several records is a CHOICE: the
instance whose dry run matched the selected copper is taken; two matching is an
ambiguity the caller REFUSES red (never a guess: the record of the wrong instance
would take copper away from a live one); none matching means there is nothing to
subtract.
"""
from __future__ import annotations

import logging

from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

__all__ = ["run_subtract_worker"]


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
      {"empty": True, ...}      — the dry run planned nothing: subtract NOTHING
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
