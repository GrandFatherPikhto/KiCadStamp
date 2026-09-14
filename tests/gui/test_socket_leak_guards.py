#!/usr/bin/env python3
"""С1/С2 of plan_2026_09_14_apply_pipeline_socket_leak.md §P.4 — the hot path
must not leak sockets.

Found live (plan §P.0): "Redraw the whole tree" on profiles/3ch-awg-tia-v103
built 23 kipy clients per click (22 per-name ApplyPipelines + the cascade's
own adapter) and closed NONE of them; the GC then finalized them in batches,
each finalize costing the calling thread 2.0 s
(``pynng Socket.close() did not return within 2.0s``) — 6.0 s of an 18.7 s
action, cured only by restarting the GUI.

One KiCadBoardAdapter == one kipy.KiCad == one pynng Req0 socket
(kicadstamp/kicad/adapter.py:72), so the socket count IS the adapter count:
both guards below count adapter constructions against adapter close() calls.
The stand-in behaves like a MagicMock (what the cascade's rigid-group helpers
already get in tests/gui/test_cascade.py) except for close(), which is a REAL
counting method — a MagicMock child would report "called" even when nobody
calls it. ApplyPipeline.run()'s own _connect_adapter() is left REAL, so what
is counted is what the pipeline really builds.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from unittest.mock import MagicMock

from kicadstamp.config import Config, ClonePlacement
from kicadstamp.exceptions import PlacerError
from kicadstamp.trees import load_trees

import kicadstamp.apply_pipeline as ap_mod
import gui.docks.cascade as cascade_mod
from gui.docks.cascade import run_curated_forest_redraw


class _AdapterSpy:
    """KiCadBoardAdapter stand-in, counting constructions and closes."""

    created = 0
    closed = 0

    def __init__(self, **kwargs):
        _AdapterSpy.created += 1
        self._mock = MagicMock()

    def __getattr__(self, name):
        return getattr(self._mock, name)

    def close(self):
        _AdapterSpy.closed += 1


def _install_spy(monkeypatch):
    _AdapterSpy.created = 0
    _AdapterSpy.closed = 0
    # BOTH construction sites: the cascade's own adapter and the per-name
    # pipeline's (apply_pipeline.py::_connect_adapter).
    monkeypatch.setattr(cascade_mod, "KiCadBoardAdapter", _AdapterSpy)
    monkeypatch.setattr(ap_mod, "KiCadBoardAdapter", _AdapterSpy)


def _neuter_pipeline_steps(monkeypatch, on_validate=None):
    """Keep run()'s REAL _connect_adapter (the step that builds the socket
    under test) and stub every other step, so a guard needs no config file and
    no KiCad. ``on_validate(pipeline)`` may raise, simulating a name whose run
    fails after its adapter was built."""
    for step in ("_load_config", "_filter_config", "_resolve_order",
                 "_create_planner", "_execute"):
        monkeypatch.setattr(ap_mod.ApplyPipeline, step, lambda self: None)

    def _validate(self):
        if on_validate is not None:
            on_validate(self)

    monkeypatch.setattr(ap_mod.ApplyPipeline, "_validate", _validate)


def _fixture(names: int, tmp_path):
    """cfg with `names` clone_placements + ONE tree whose `names` top-level
    nodes are all selected — curated_redraw_plan_forest turns that into
    exactly `names` per-name --only runs."""
    clusters = [f"CL_{i:02d}" for i in range(names)]
    cfg = Config(
        cells={},
        clone_placements=[ClonePlacement(cluster=c, cell="c", xy=(float(i), 0.0))
                          for i, c in enumerate(clusters)],
    )
    body = "".join(f'      (node (ref "{c}") (xy {i} 0))\n'
                   for i, c in enumerate(clusters))
    tree_path = tmp_path / "guards.trees"
    tree_path.write_text('(kicadstamp-trees\n'
                         '(tree (name "t") (anchor (origin))\n' + body + '))',
                         encoding="utf-8")
    return cfg, load_trees(str(tree_path)), set(clusters)


def test_forest_redraw_closes_every_adapter_it_creates(monkeypatch, tmp_path):
    """С1 — the hot path does not leak: 5 names -> 5 pipeline adapters + the
    cascade's own; every one of them is closed before the redraw returns.

    Kills M1 (dropping the close in the per-name pipeline) and any later
    regression in run_curated_forest_redraw that leaves the cascade's own
    adapter behind: the assertion is closed == created, not just "closed > 0".
    """
    _install_spy(monkeypatch)
    _neuter_pipeline_steps(monkeypatch)
    cfg, trees, selected = _fixture(5, tmp_path)

    results, _warnings = run_curated_forest_redraw(
        "/nonexistent/root.sexp", cfg, None, trees, selected)

    assert [r[1] for r in results] == [True] * 5
    assert _AdapterSpy.created == 6, "5 per-name pipelines + the cascade's own"
    assert _AdapterSpy.closed == _AdapterSpy.created, \
        "a socket built on the redraw path was never closed"


def test_a_failed_name_releases_its_socket_like_a_successful_one(monkeypatch, tmp_path):
    """С2 — closing lives in the `with`/finally, not on the happy path: the
    name whose run() raises PlacerError still hands its socket back, and the
    remaining names keep applying (per-name failure isolation is the
    cascade's contract).

    Kills M2 (moving the close onto the happy path): that name's adapter is
    built and then abandoned.
    """
    _install_spy(monkeypatch)

    def _fail_the_middle_name(pipeline):
        if list(pipeline.only or []) == ["CL_02"]:
            raise PlacerError("boom")

    _neuter_pipeline_steps(monkeypatch, on_validate=_fail_the_middle_name)
    cfg, trees, selected = _fixture(5, tmp_path)

    results, _warnings = run_curated_forest_redraw(
        "/nonexistent/root.sexp", cfg, None, trees, selected)

    assert results == [("CL_00", True, None), ("CL_01", True, None),
                       ("CL_02", False, "boom"), ("CL_03", True, None),
                       ("CL_04", True, None)], \
        "a failed name must not abort the rest of the redraw"
    assert _AdapterSpy.created == 6, "the failed name built an adapter too"
    assert _AdapterSpy.closed == _AdapterSpy.created, \
        "the failed name's socket was left behind"
