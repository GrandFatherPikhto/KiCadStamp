# kicadstamp/diagnostics/deepseek_probe_nested_placement_count_2026_10_09.py
"""Probe for D3 of plan_2026_10_09_entity_page (DeepSeek, 2026-10-09).

THE QUESTION. Mutation M2 of the deepseek_mutations_entities_1a_2026_10_08.py rig
("the placer count is dropped") SURVIVES: no test guards the "one placer - one
mention WITH its count (N)" property of the "placed by" hint (gui/docks/
entity_tree.py `_placed_by_text`). Denis decided to close that property with a
CELL instead of deleting the row, so this probe answers the two questions the
cell's shape depends on:

  1. does a VALID config load where ONE outer cell places ONE sub-cell TWICE
     (two nested `clone_placements` entries, different names, the SAME
     `cell_uuid`)? Unique NAMES are required (kicadstamp/config/entries.py:255)
     while a duplicate TARGET is not checked there - but "no check" is a
     reading, not a fact;
  2. what does `_placed_by_text` actually render for such an index - "(2)" at
     the end of ONE mention (as expected) or something else?

Plus two CONTROLS, so the counter cannot turn out to be the probe's own
invention:
  * ONE nested placer - a mention with NO number;
  * TWO different OWNERS of one sub-cell (a nested placer inside c_outer PLUS a
    `clone_placements:` record of the root) - TWO mentions, also without numbers:
    the counter counts one OWNER, not consecutive index rows. The index groups
    placers by their TARGET cell (`placed_by_cell`), so "two different
    sub-cells" cannot serve as a control - each one simply sits under its own
    uuid.

Run: `.venv/bin/python kicadstamp/diagnostics/deepseek_probe_nested_placement_count_2026_10_09.py`
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from kicadstamp.config.includes import walk_include_tree
from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError

from gui.docks.entity_index import build_entity_index
from gui.docks.entity_tree import EntityTreeMixin

CELL_A = "00000000-0000-0000-0000-0000000000a1"
CELL_B = "00000000-0000-0000-0000-0000000000b1"
CP_UUID = "00000000-0000-0000-0000-0000000000d1"


def _nested(name: str, cell: str, uuid: str) -> dict:
    """One nested placer - the shape the index reads
    (gui/docks/entity_index.py: `nested.get("cell_uuid")`)."""
    return {"name": name, "cell": cell, "cell_uuid": uuid}


def _config(*nested, clone: bool = False) -> dict:
    """The root: sub-cell c_a plus the outer cell c_outer carrying `nested`;
    `clone=True` adds a SECOND owner - a root `clone_placements:` record placing
    the same c_a."""
    return {
        "cells": {
            "c_a": {"uuid": CELL_A, "components": [{"role": "R"}]},
            "c_outer": {"uuid": CELL_B, "components": [{"role": "R"}],
                        "clone_placements": list(nested)},
        },
        **({"clone_placements": [{"name": "cp", "uuid": CP_UUID,
                                  "cluster": "CL_CP", "xy": [0.0, 0.0],
                                  "cell": "c_a", "cell_uuid": CELL_A}]}
           if clone else {}),
    }


def _write(data: dict, directory: Path) -> Path:
    root = directory / "root.sexp"
    root.write_text(dict_to_sexp(data, format_number=3), encoding="utf-8")
    return root


def _render(index, uuid: str) -> str:
    """The very text the "cell ... placed by ..." hint is built from.

    `_placed_by_text` never reads `self`, so a real instance is taken from the
    class WITHOUT `__init__` - no Qt widget is built."""
    placed = index.placed_by_cell(uuid)
    kinds = [(p.kind, p.owner_name) for p in placed]
    text = EntityTreeMixin._placed_by_text(object.__new__(EntityTreeMixin), placed)
    return f"kinds={kinds} text={text!r}"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="probe-nested-count-") as tmp:
        directory = Path(tmp)
        cases = [
            ("TWO placers of ONE sub-cell (the cell's target)",
             _config(_nested("n1", "c_a", CELL_A), _nested("n2", "c_a", CELL_A)),
             CELL_A),
            ("control: ONE placer (no number expected)",
             _config(_nested("n1", "c_a", CELL_A)), CELL_A),
            ("control: TWO different OWNERS (no number expected - the counter "
             "counts one owner)",
             _config(_nested("n1", "c_a", CELL_A), clone=True), CELL_A),
        ]
        for title, data, uuid in cases:
            print(f"\n=== {title}")
            root = _write(data, directory)
            try:
                cfg, _ctx = load_config(str(root))
            except ValidationError as exc:
                print(f"load_config REFUSED: {exc}")
                continue
            cell = cfg.cells["c_outer"]
            print(f"load_config: OK (cells={len(cfg.cells)}, "
                  f"nested of c_outer={len(cell.clone_placements)})")
            index = build_entity_index(walk_include_tree(str(root)))
            print(_render(index, uuid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
