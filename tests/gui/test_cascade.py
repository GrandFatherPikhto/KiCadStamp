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
from kicadstamp.exceptions import ValidationError
from kicadstamp.link_trees import link_trees
from kicadstamp.tree_position import curated_redraw_plan
from kicadstamp.trees import load_trees

import gui.docks.cascade as cascade_mod
from gui.docks.cascade import cascade_records, run_cascade, run_curated_tree_redraw


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


# ── run_curated_tree_redraw: trees -> link -> plan -> run_cascade ─────────

def _tree_text(body):
    return "(kicadstamp-trees\n" + body + ")"


def _curated_cfg():
    """Two clone_placements (CL_A/CL_B) that a curated tree can reference."""
    return Config(
        cells={},
        clone_placements=[
            ClonePlacement(cluster="CL_A", cell="c", xy=(0.0, 0.0)),
            ClonePlacement(cluster="CL_B", cell="c", xy=(1.0, 1.0)),
        ],
    )


def _load_tree(tmp_path, body):
    path = tmp_path / "curated.trees"
    path.write_text(_tree_text(body), encoding="utf-8")
    return load_trees(str(path))


def test_run_curated_tree_redraw_calls_run_cascade_with_plan_names(monkeypatch, tmp_path, caplog):
    """The shim links trees, plans the curated redraw over selected_refs, and
    feeds exactly curated_redraw_plan's name list into run_cascade."""
    cfg = _curated_cfg()
    trees = _load_tree(tmp_path,
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2))\n'
        '      (node (ref "CL_B") (xy 3 4)))')
    selected = {"CL_B"}

    calls = []
    monkeypatch.setattr(cascade_mod, "run_cascade",
                        lambda cp, c, x, names: (calls.append(names), [])[1])

    results, warnings = run_curated_tree_redraw("/root.yaml", cfg, None, trees,
                                                "t", selected)

    linked = link_trees(cfg, trees)
    expected_names, expected_warnings = curated_redraw_plan(
        next(t for t in linked if t.name == "t"), selected)
    assert calls == [expected_names]
    assert results == []
    assert warnings == expected_warnings
    # No warnings expected for an origin anchor + top-level selection.
    assert warnings == []


def test_run_curated_tree_redraw_warns_and_logs_parent_not_selected(monkeypatch, tmp_path, caplog):
    """A selected node whose parent isn't selected produces a plan warning,
    which is returned AND logged via the module logger."""
    cfg = _curated_cfg()
    trees = _load_tree(tmp_path,
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2)\n'
        '            (node (ref "CL_B") (xy 3 4))))')
    selected = {"CL_B"}  # parent CL_A not selected -> warning

    monkeypatch.setattr(cascade_mod, "run_cascade", lambda *a, **k: [])

    results, warnings = run_curated_tree_redraw("/root.yaml", cfg, None, trees,
                                                "t", selected)

    assert any("CL_B" in w and "CL_A" in w for w in warnings)
    assert any("not in selection" in w for w in warnings)
    # The warning is also logged (module logger), not just returned.
    assert any("not in selection" in r.message for r in caplog.records)


def test_run_curated_tree_redraw_unknown_tree_is_fatal(monkeypatch, tmp_path):
    """A tree_name absent from `trees` is a ValidationError (lookup/config
    problem), before any redraw runs."""
    cfg = _curated_cfg()
    trees = _load_tree(tmp_path,
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2)))')

    monkeypatch.setattr(cascade_mod, "run_cascade", lambda *a, **k: [])

    try:
        run_curated_tree_redraw("/root.yaml", cfg, None, trees, "no_such_tree", {"CL_A"})
        assert False, "expected ValidationError"
    except ValidationError:
        pass
