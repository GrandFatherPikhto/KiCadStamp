# tests/config/test_format3_equivalence.py
"""У2.4 — format-2, minted format-3 and minted-with-lying-hints format-3 load to
the SAME Config (plan §4).

The graph is deliberately small but covers the reference FORMS the loader knows
(record fields, chain spokes, points -> points, a cell's nested clone, a tree
node, a tree anchor point, a tree_instances anchor point, sheet_templates'
nested placements) plus an `include:`. The stub `mint_format3`/`lie_hints` live
in tests/fakes/format3.py and are never imported by the product.

Comparison is `dataclasses.asdict` with the identity fields removed: every
`uuid`/`*_uuid` key and the `folders` table. Everything else must be byte-equal,
so the normalization pass (У2.1) cannot change any non-identity value.
"""
import json
from dataclasses import asdict
from pathlib import Path

from kicadstamp.config import format_version
from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from tests.fakes.format3 import lie_hints, mint_format3


def _root_data() -> dict:
    return {
        "include": ["sub.sexp"],
        "cells": {
            "Cell/A": {"layer": "B.Cu",
                       "clone_placements": [{"name": "n1", "cluster": "K",
                                             "cell": "Cell/B"}]},
            "Cell/B": {"layer": "B.Cu"},
        },
        "points": {"P/A": {"anchor_ref": "IC1"},
                   "P/B": {"anchor_ref": "IC2"},
                   "P/C": {"anchor_point": "P/A"}},
        "entities": [{"name": "E1", "cell": "Cell/A"},
                     {"name": "E2", "imprint": "imp1"}],
        "imprints": [{"name": "imp1", "components": [{"ref": "C1"}]}],
        "chains": [{"net": "GND", "name": "ch1", "anchor_point": "P/A",
                    "spokes": [{"pad": "1", "cell": "Cell/A"}]}],
        "clone_placements": [{"cluster": "K1", "cell": "Cell/A",
                              "anchor_point": "P/A", "name": "cp1",
                              "xy": [0.0, 0.0]}],
        "coordinate_placements": [{"cluster": "CL", "role": "R1", "name": "c1",
                                   "anchor_point": "P/A"}],
        "thermal_via_arrays": [{"name": "tva1", "anchor_point": "P/A",
                                "pad": "1"}],
        "net_traces": [{"name": "nt1", "net": "NET1", "anchor_role": "R1"}],
        "trees": [{"name": "t1", "anchor": {"point": "P/A"},
                   "nodes": [{"ref": "E1", "kind": "placement", "xy": [0.0, 0.0]},
                             {"ref": "nt1", "kind": "net_trace", "xy": [1.0, 0.0]}]}],
        "tree_instances": [{"template": "t1", "name": "ti1", "sheet": "S1",
                            "anchor": {"point": "P/B"}}],
        "sheet_templates": {"tpl": {
            "sheets": ["S2"],
            "clone_placements": [{"cluster": "K2", "cell": "Cell/B",
                                  "anchor_point": "P/B", "xy": [0.0, 0.0]}]}},
    }


def _sub_data() -> dict:
    return {"cells": {"Cell/C": {"layer": "B.Cu"}},
            "entities": [{"name": "E3", "cell": "Cell/C"}]}


def _strip(obj):
    """Drop the identity fields: every `uuid`/`*_uuid` and the `folders` table."""
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items()
                if k != "folders" and k != "uuid" and not k.endswith("_uuid")}
    if isinstance(obj, list):
        return [_strip(x) for x in obj]
    return obj


def _write(path: Path, data: dict, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dict_to_sexp(data, format_number=version), encoding="utf-8")


def test_format2_minted_format3_and_lying_format3_load_the_same(tmp_path, monkeypatch):
    base = {"root.sexp": _root_data(), "sub.sexp": _sub_data()}

    before = tmp_path / "before"
    after = tmp_path / "after"
    lying = tmp_path / "lying"
    for name, data in base.items():
        _write(before / name, data, 2)
        _write(after / name, mint_format3(data), 3)
        _write(lying / name, lie_hints(mint_format3(data)), 3)

    # "before" is a plain format-2 graph — the product's own format.
    before_cfg, _ = load_config(str(before / "root.sexp"))

    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)
    after_cfg, _ = load_config(str(after / "root.sexp"))
    lying_cfg, _ = load_config(str(lying / "root.sexp"))

    expected = _strip(asdict(before_cfg))
    assert _strip(asdict(after_cfg)) == expected
    assert _strip(asdict(lying_cfg)) == expected
