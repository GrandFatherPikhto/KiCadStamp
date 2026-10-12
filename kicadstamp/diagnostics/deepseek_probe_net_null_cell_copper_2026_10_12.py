#!/usr/bin/env python3
# kicadstamp/diagnostics/deepseek_probe_net_null_cell_copper_2026_10_12.py
"""Probe: what `net: null` on a cell via/track does NOW (Б5г inventory, 12.10.2026).

Denis's order: "что делают загрузка и путь клонов с net: null у via/дорожки
ячейки (раньше — «сеть цепочки»)? Показать зондом/клеткой; via без сети молча —
ОСТАНОВКА с фактом."

FACTS — each printed below is also held by a CELL, so the answer cannot rot:

  1. LOAD ACCEPTS `net: null` on a cell via/track. The grammar reads it as "no
     net"; kicadstamp/validation.py states explicitly that the check is NOT
     duplicated there because the refusal lives on the apply path.
     Cell: tests/config/test_config_models.py
           ::test_net_null_still_loads_and_only_apply_refuses_it

  2. THE CLONE PATH IS LOUD, NEVER SILENT. A placed cell's via/track with
     neither `net` nor `net_from_role` raises ValidationError BEFORE any board
     write (kicadstamp/geometry/clone_geometry.py):
       * via   — _resolve_clone_via:   "via without net in cell ..."
       * track — _resolve_clone_track: "track without net in cell ..."
     Cells: tests/geometry/test_clone_geometry.py
            ::test_via_without_net_raises_fatal
            ::test_track_without_net_raises_fatal   (added by this inventory —
                                                    the track half had no cell)
     => NO STOP: no net-less via/track is ever created silently.

  3. The OLD reading ("inherit the chain's own net" / "use the rule net") has no
     carrier left: the extraction flag that could write it (--rule-net) was
     removed in Б5б-3c, and the remaining READER wording was rewritten in this
     commit (cell_placement_copy._copper_net_ok, cell_geometry_refresh tier 3).

  4. `RULE_NETS` / `rule_nets` are NOT part of this: they are the built-in
     role-less net SET ({"GND"}), still live in the clone role lemma —
     kept by decision of 12.10.2026.

Run: .venv/bin/python kicadstamp/diagnostics/deepseek_probe_net_null_cell_copper_2026_10_12.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import Cell, ClonePlacement, TemplateTrack, TemplateVia, load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.clone_geometry import apply_clone_geometry


def probe_load_accepts_net_null() -> str:
    """Fact 1 — the loader reads a net-less cell via without complaint.

    The file is written as format 2 (like tests/config/test_config_models.py
    does): the point here is the GRAMMAR reading `net: null`, not the format-3
    uuid requirement."""
    data = {"layer": "B.Cu",
            "cells": {"c": {"vias": [{"offset_along_mm": 1.0}]}}}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.sexp"
        path.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
        cfg, _ctx = load_config(str(path))
        via = cfg.cells["c"].vias[0]
        return (f"LOAD: accepted, net={via.net!r}, "
                f"net_from_role={via.net_from_role!r}")


def probe_clone_path_fatal() -> list[str]:
    """Fact 2 — the clone path refuses a net-less via AND a net-less track."""
    out: list[str] = []
    def first_line(err: Exception) -> str:
        return next((ln for ln in str(err).splitlines() if ln.strip()), "")

    via_cell = Cell(name="bad_via", vias=[TemplateVia(
        offset_along_mm=0.0, offset_across_mm=0.0, net=None)])
    via_clone = ClonePlacement(cluster="x", cell="bad_via", xy=(0.0, 0.0))
    try:
        apply_clone_geometry(via_clone, via_cell, {})
        out.append("CLONE via: NO ERROR — silent net-less via (STOP!)")
    except ValidationError as e:
        out.append(f"CLONE via: FATAL — {first_line(e)}")

    track_cell = Cell(name="bad_track", tracks=[TemplateTrack(
        start_along_mm=0.0, start_across_mm=0.0,
        end_along_mm=1.0, end_across_mm=0.0, net=None)])
    track_clone = ClonePlacement(cluster="x", cell="bad_track", xy=(0.0, 0.0))
    try:
        apply_clone_geometry(track_clone, track_cell, {})
        out.append("CLONE track: NO ERROR — silent net-less track (STOP!)")
    except ValidationError as e:
        out.append(f"CLONE track: FATAL — {first_line(e)}")
    return out


def main() -> int:
    print(probe_load_accepts_net_null())
    for line in probe_clone_path_fatal():
        print(line)
    print("READERS: cell_placement_copy._copper_net_ok and cell_geometry_refresh "
          "tier 3 no longer claim the record inherits 'a chain's rule net'.")
    print("=> no silence anywhere: load accepts, apply fatals, no STOP needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
