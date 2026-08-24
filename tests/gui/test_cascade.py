#!/usr/bin/env python3
"""
Tests for gui/docks/cascade.py — the shared "Redraw dependents" cascade
(§2 of plan_2026_08_21_anchor_dependency_tree_cascade_redraw.md): static
topological order (cascade_records) and sequential --only application with
per-record status (run_cascade) — the §2.3/§2.5 behaviour, against a mocked
ApplyPipeline (no live board).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from kicadstamp.config import Config, Cell, TemplateComponentSlot, ClonePlacement

import gui.docks.cascade as cascade_mod
from gui.docks.cascade import cascade_records, run_cascade


def _chain_cfg():
    cp_cell = Cell(name="cp_cell", components=[TemplateComponentSlot(role="R_CP")])
    c1_cell = Cell(name="c1_cell", components=[TemplateComponentSlot(role="R_C1")])
    return Config(
        cells={"cp_cell": cp_cell, "c1_cell": c1_cell},
        coordinate_placements=[],
        clone_placements=[
            ClonePlacement(cluster="PRODUCER", cell="cp_cell", xy=(0.0, 0.0)),
            ClonePlacement(cluster="CONSUMER", cell="c1_cell", xy=(0.0, 0.0),
                           anchor_role="R_CP"),
        ],
    )


def test_cascade_records_parent_before_child():
    """The static cascade from the producer places it first, then its
    dependent — the §2.2 topological guarantee."""
    cfg = _chain_cfg()
    records = cascade_records(cfg, "clone:PRODUCER")
    assert [r.name for r in records] == ["PRODUCER", "CONSUMER"]


def test_run_cascade_sequential_order_and_partial_failure(monkeypatch):
    """§2.3/§2.5: run_cascade applies ONE --only run per name, in the given
    order, and reports per-record success/failure without aborting the rest
    of the chain when a middle record fails."""
    calls = []

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg=None, preloaded_ctx=None,
                     only=None, dry_run=False):
            self.only = only

        def run(self):
            calls.append(list(self.only))
            if self.only == ["B"]:
                raise RuntimeError("boom")

    monkeypatch.setattr(cascade_mod, "ApplyPipeline", _FakePipeline)

    results = run_cascade("/root.yaml", None, None, ["A", "B", "C"])

    assert calls == [["A"], ["B"], ["C"]]
    assert results == [("A", True, None), ("B", False, "boom"), ("C", True, None)]
