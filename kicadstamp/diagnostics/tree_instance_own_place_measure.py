#!/usr/bin/env python3
"""diagnostics/tree_instance_own_place_measure.py

Measurements for the report of plan_2026_09_12_tree_instance_own_place:

1. §И.1 — the `params:` loss behind Tools -> "Instances...", BEFORE (the old
   hand-written literal) vs AFTER (the overlay by (template, name));
2. §И.7.2 tests 6/8 — the instance's node position as a NUMBER, for a declared
   `(anchor (point ...))` and for a declared `rotation`, laid out OFFLINE
   (origin / literal-xy point need no live board).

Run:  .venv/bin/python diagnostics/tree_instance_own_place_measure.py
"""
import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config.tree_instances import expand_tree_instances
from kicadstamp.config_writer import upsert_tree_instances
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.tree_position import layout_tree_from_anchor
from kicadstamp.utils.units import MM


def _template_data(instances):
    return {
        "cells": {},
        "entities": [{"name": "dac_buf", "cell": "c_dac", "cluster": "DAC_BUF"}],
        "trees": [{
            "name": "dac_buf_tpl", "anchor": {"role": "DAC_BUF"},
            "nodes": [{"ref": "dac_buf", "kind": "placement", "xy": [1.0, 2.0],
                       "rotation": 90.0}],
        }],
        "tree_instances": instances,
    }


def measure_params_loss() -> None:
    """§И.1.1: BEFORE/AFTER on one unchanged dialog write."""
    print("=== 1. §И.1 — params: across one Tools -> Instances... OK ===")
    declaration = {"template": "dac_buf_tpl", "name": "ch1_dac_buf",
                   "sheet": "Channel_1", "cluster": "GRP",
                   "params": {"channel_sheet": "Channel_1"}}
    data = _template_data([declaration])
    before_list = [copy.deepcopy(declaration)]
    dialog_rows = [{"name": "ch1_dac_buf", "sheet": "Channel_1",
                    "cluster": "GRP"}]

    # The pre-fix writer (kept here verbatim): rebuild every row from a literal.
    old_items = [
        {"template": "dac_buf_tpl", "name": r["name"], "sheet": r["sheet"],
         **({"cluster": r["cluster"]} if r.get("cluster") else {})}
        for r in dialog_rows]
    print("BEFORE (on disk) :", before_list)
    print("OLD writer result:", old_items, "-> params", "LOST"
          if "params" not in old_items[0] else "kept")

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "m.sexp"
        p.write_text(dict_to_sexp(data), encoding="utf-8")
        changed = upsert_tree_instances(p, "dac_buf_tpl", dialog_rows)
        after = list(sexp_to_dict(p.read_text(encoding="utf-8"))
                     .get("tree_instances") or [])
    print("AFTER  (on disk) :", after)
    print("write recognised as a no-op:", changed is False)
    print()


def measure_place_numbers() -> None:
    """§И.7.2 tests 6/8, as numbers."""
    print("=== 2. §И.7.2 — the instance's place, by NUMBER ===")
    rows = [
        ("point anchor (offline: a literal-xy point)",
         {"template": "dac_buf_tpl", "name": "ch1_dac_buf",
          "sheet": "Channel_1", "anchor": {"point": "p_home"}}, True),
        ("origin + rotation 90 (offline)",
         {"template": "dac_buf_tpl", "name": "ch1_dac_buf",
          "sheet": "Channel_1", "anchor": {"origin": True},
          "rotation": 90.0}, True),
        ("no place declared (regression: role anchor — needs the LIVE board)",
         {"template": "dac_buf_tpl", "name": "ch1_dac_buf",
          "sheet": "Channel_1"}, False),
    ]
    for label, row, lay_out in rows:
        data = _template_data([row])
        data["points"] = {"p_home": {"xy": [10.0, 20.0]}}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "p.sexp"
            p.write_text(dict_to_sexp(data), encoding="utf-8")
            cfg, _ = load_config(str(p))
        tree = next(t for t in cfg.trees if t.name == "ch1_dac_buf")
        raw = next(t for t in expand_tree_instances(
            _template_data([row]) | {"points": data["points"]})["trees"]
            if t["name"] == "ch1_dac_buf")
        print(f"{label}:")
        print(f"  raw generated anchor = {raw['anchor']}, "
              f"raw rotation = {raw.get('rotation')!r}")
        if not lay_out:
            continue
        pose = layout_tree_from_anchor(tree, adapter=None, cfg=cfg,
                                       sheet_names={})
        pos, rot = pose["dac_buf__ch1_dac_buf"]
        print(f"  root node at ({pos.x / MM:.3f}, {pos.y / MM:.3f}) mm, "
              f"rot {rot:.1f} deg")
    expected = rotate_local_offset(1.0, 2.0, 90.0)
    print(f"rotate_local_offset(1.0, 2.0, 90) = "
          f"({expected.x / MM:.3f}, {expected.y / MM:.3f}) mm")
    print()


if __name__ == "__main__":
    measure_params_loss()
    measure_place_numbers()
