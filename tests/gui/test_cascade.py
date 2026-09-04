#!/usr/bin/env python3
"""
Tests for gui/docks/cascade.py — the shared "Redraw dependents" cascade
(§2 of plan_2026_08_21_anchor_dependency_tree_cascade_redraw.md): static
topological order (cascade_records) and sequential --only application with
per-record status (run_cascade) — the §2.3/§2.5 behaviour, against a mocked
ApplyPipeline (no live board).
"""
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from unittest.mock import MagicMock

from kicadstamp.config import Config, Cell, TemplateComponentSlot, ClonePlacement
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.trees import load_trees

import gui.docks.cascade as cascade_mod
from gui.docks.cascade import (
    cascade_records, run_cascade, run_curated_forest_redraw, run_curated_tree_redraw,
    run_single_node_redraw_worker,
)


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

    results = run_cascade("/root.sexp", None, None, ["A", "B", "C"])

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


def test_run_curated_tree_redraw_runs_pipeline_per_plan_name(monkeypatch, tmp_path):
    """The shim links trees, plans the curated redraw over selected_refs, and
    runs ONE ApplyPipeline(--only=[name]) per plan name with a rigid-group
    position override (plan_2026_08_29_tree_live_rigid_redraw.md §1)."""
    cfg = _curated_cfg()
    trees = _load_tree(tmp_path,
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2))\n'
        '      (node (ref "CL_B") (xy 3 4)))')
    selected = {"CL_B"}

    calls = []

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg=None, preloaded_ctx=None,
                     only=None, dry_run=False, position_overrides=None):
            calls.append((list(only or []), position_overrides))

        def run(self):
            pass

    monkeypatch.setattr(cascade_mod, "ApplyPipeline", _FakePipeline)
    monkeypatch.setattr(cascade_mod, "KiCadBoardAdapter",
                        lambda **k: MagicMock())

    results, warnings = run_curated_tree_redraw("/root.sexp", cfg, None, trees,
                                                "t", selected)

    # CL_B is the only selected node without an inline anchor -> one run, with
    # a rigid-group position override (origin anchor -> CL_B's own spot).
    assert [c[0] for c in calls] == [["CL_B"]]
    assert calls[0][1] is not None and "CL_B" in calls[0][1]
    assert warnings == []
    assert results == [("CL_B", True, None)]


def test_run_curated_tree_redraw_warns_and_logs_parent_not_selected(monkeypatch, tmp_path, caplog):
    """A selected node whose parent isn't selected produces a plan warning,
    which is returned AND logged via the module logger."""
    cfg = _curated_cfg()
    trees = _load_tree(tmp_path,
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2)\n'
        '            (node (ref "CL_B") (xy 3 4))))')
    selected = {"CL_B"}  # parent CL_A not selected -> warning

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg=None, preloaded_ctx=None,
                     only=None, dry_run=False, position_overrides=None):
            pass

        def run(self):
            pass

    monkeypatch.setattr(cascade_mod, "ApplyPipeline", _FakePipeline)
    monkeypatch.setattr(cascade_mod, "KiCadBoardAdapter",
                        lambda **k: MagicMock())

    results, warnings = run_curated_tree_redraw("/root.sexp", cfg, None, trees,
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
        run_curated_tree_redraw("/root.sexp", cfg, None, trees, "no_such_tree", {"CL_A"})
        assert False, "expected ValidationError"
    except ValidationError:
        pass


# ── run_curated_forest_redraw: multi-tree redraw in forest order (plan 4.2) ─

def test_run_curated_forest_redraw_cross_tree_order(monkeypatch, tmp_path):
    """Multi-tree redraw plans across ALL trees in the global forest order: a
    tree whose ANCHOR points at a selected node of another tree applies that
    node FIRST (cross-tree edge), before its own top-level node."""
    cfg = _curated_cfg()
    t1 = tmp_path / "t1.trees"
    t1.write_text(_tree_text('(tree (name "t1") (anchor (origin))\n'
                             '      (node (ref "CL_A") (xy 1 2)))'), encoding="utf-8")
    t2 = tmp_path / "t2.trees"
    t2.write_text(_tree_text('(tree (name "t2") (anchor (ref "CL_A"))\n'
                             '      (node (ref "CL_B") (xy 3 4)))'), encoding="utf-8")
    trees = load_trees(str(t1)) + load_trees(str(t2))
    selected = {"CL_A", "CL_B"}

    calls = []

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg=None, preloaded_ctx=None,
                     only=None, dry_run=False, position_overrides=None):
            calls.append(list(only or []))

        def run(self):
            pass

    monkeypatch.setattr(cascade_mod, "ApplyPipeline", _FakePipeline)
    monkeypatch.setattr(cascade_mod, "KiCadBoardAdapter", lambda **k: MagicMock())

    results, warnings = run_curated_forest_redraw("/root.sexp", cfg, None,
                                                  trees, selected)

    # t2 is anchored on CL_A (a selected node of t1) -> CL_A applied first
    assert [c[0] for c in calls] == ["CL_A", "CL_B"]
    assert results == [("CL_A", True, None), ("CL_B", True, None)]


def _raising_pipeline(raised):
    """A fake ApplyPipeline whose run() always raises `raised` — for the
    per-record failure-path tests (no live board)."""

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg=None, preloaded_ctx=None,
                     only=None, dry_run=False, position_overrides=None):
            pass

        def run(self):
            raise raised

    return _FakePipeline


def test_run_cascade_placer_error_no_traceback_unexpected_keeps_it(monkeypatch, caplog):
    """2026-08-30: the ValidationError/PlacerError family is ALREADY a
    well-formatted "fatal at the boundary" message (format_fatal_error) — it
    must be logged as a plain WARNING with NO traceback (logger.exception would
    add ~20 lines of noise per record). A genuinely unexpected Exception keeps
    the traceback as before. The per-record result is identical in both
    branches."""
    # PlacerError path (a BARE PlacerError — the base of ValidationError/
    # ComponentNotFoundError/...) -> warning only, no exc_info
    monkeypatch.setattr(cascade_mod, "ApplyPipeline",
                        _raising_pipeline(PlacerError("expected")))
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        results = run_cascade("/root.sexp", None, None, ["A"])
    assert results == [("A", False, "expected")]
    assert any("FAILED" in r.getMessage() for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records), \
        "PlacerError must NOT log a traceback (logger.exception)"

    # Plain Exception path -> logger.exception (exc_info set), same result
    monkeypatch.setattr(cascade_mod, "ApplyPipeline",
                        _raising_pipeline(RuntimeError("boom")))
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        results = run_cascade("/root.sexp", None, None, ["A"])
    assert results == [("A", False, "boom")]
    assert any(r.exc_info for r in caplog.records), \
        "an unexpected Exception must keep the traceback (logger.exception)"


def test_curated_redraws_keep_placer_error_traceback_split(monkeypatch, tmp_path, caplog):
    """The same no-traceback-for-PlacerError / traceback-for-Exception split
    holds in run_curated_tree_redraw and run_curated_forest_redraw (their
    except blocks are identical to run_cascade's)."""
    cfg = _curated_cfg()
    trees = _load_tree(tmp_path,
        '(tree (name "t") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2))\n'
        '      (node (ref "CL_B") (xy 3 4)))')
    monkeypatch.setattr(cascade_mod, "KiCadBoardAdapter", lambda **k: MagicMock())

    # tree redraw: a bare PlacerError -> warning, no traceback
    monkeypatch.setattr(cascade_mod, "ApplyPipeline",
                        _raising_pipeline(PlacerError("expected")))
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        results, _warnings = run_curated_tree_redraw(
            "/root.sexp", cfg, None, trees, "t", {"CL_B"})
    assert results == [("CL_B", False, "expected")]
    assert not any(r.exc_info for r in caplog.records), \
        "tree redraw: PlacerError must NOT log a traceback"

    # forest redraw: unexpected Exception -> traceback
    monkeypatch.setattr(cascade_mod, "ApplyPipeline",
                        _raising_pipeline(RuntimeError("boom")))
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        results, _warnings = run_curated_forest_redraw(
            "/root.sexp", cfg, None, trees, {"CL_B"})
    assert results and results[0] == ("CL_B", False, "boom")
    assert any(r.exc_info for r in caplog.records), \
        "forest redraw: an unexpected Exception must keep the traceback"


def test_single_node_redraw_worker_logs_placer_error_text(monkeypatch, caplog):
    """2026-09-04 regression: the node editor's **Redraw** worker
    (run_single_node_redraw_worker) returned the PlacerError text in its result
    tuple but NEVER logged it — while TreesDock._finish_redraw only reports
    "{n} failed — see the log above", so the actual reason appeared nowhere
    (the curated run_curated_tree_redraw has logged the same warning all
    along). The PlacerError branch must log the formatted message explicitly
    and stay traceback-free; an unexpected Exception keeps the traceback."""
    # PlacerError path -> warning WITH the error text, no traceback.
    monkeypatch.setattr(cascade_mod, "ApplyPipeline",
                        _raising_pipeline(PlacerError("expected")))
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        results, warnings = run_single_node_redraw_worker({"ref": "CL_A"})
    assert results == [("CL_A", False, "expected")]
    assert warnings == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("FAILED" in m and "expected" in m for m in messages), \
        "PlacerError text must be logged (was swallowed before the fix)"
    assert not any(r.exc_info for r in caplog.records), \
        "PlacerError must NOT log a traceback (logger.exception)"

    # Plain Exception path -> logger.exception (exc_info set), same result.
    monkeypatch.setattr(cascade_mod, "ApplyPipeline",
                        _raising_pipeline(RuntimeError("boom")))
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        results, warnings = run_single_node_redraw_worker({"ref": "CL_A"})
    assert results == [("CL_A", False, "boom")]
    assert warnings == []
    assert any(r.exc_info for r in caplog.records), \
        "an unexpected Exception must keep the traceback (logger.exception)"


def test_run_curated_forest_redraw_stage2_places_module_content(monkeypatch, tmp_path):
    """P3b (plan 2026-09-02 P3 п.2, design P3 D5): an ACTIVE module's content is
    NOT rigid-captured — its PositionOverride comes from the STAGE-2 LAYOUT laid
    from the flow root's live anchor (an origin root here -> base (0,0)), so the
    content lands at the pure layout value (marker (10,5) + content (1,2))."""
    cfg = _curated_cfg()
    trees = _load_tree(tmp_path,
        '(tree (name "ch0") (anchor (origin))\n'
        '      (node (ref "CL_A") (xy 1 2)))\n'
        '(tree (name "p") (anchor (origin))\n'
        '      (node (ref "ch0") (kind module) (xy 10 5)))')
    selected = {"ch0"}

    calls = []

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg=None, preloaded_ctx=None,
                     only=None, dry_run=False, position_overrides=None):
            calls.append((list(only or []), position_overrides))

        def run(self):
            pass

    monkeypatch.setattr(cascade_mod, "ApplyPipeline", _FakePipeline)
    monkeypatch.setattr(cascade_mod, "KiCadBoardAdapter", lambda **k: MagicMock())

    results, warnings = run_curated_forest_redraw("/root.sexp", cfg, None,
                                                  trees, selected)

    assert [c[0] for c in calls] == [["CL_A"]]
    overrides = calls[0][1]
    assert overrides is not None and "CL_A" in overrides
    ov = overrides["CL_A"]
    # Pure layout from p's (0,0) origin base: marker (10,5) + CL_A (1,2) mm.
    assert abs(ov.position.x - 11 * 1_000_000) < 1
    assert abs(ov.position.y - 7 * 1_000_000) < 1
    assert ov.rotation_deg == 0.0
    assert results == [("CL_A", True, None)]
    assert warnings == []
