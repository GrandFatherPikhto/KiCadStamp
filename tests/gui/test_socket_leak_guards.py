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
import ast
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
ROOT = Path(__file__).resolve().parent.parent.parent

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kicadstamp.config import Config, ClonePlacement
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import PlacerError
from kicadstamp.trees import Tree, TreeAnchor, load_trees

import kicadstamp.apply_pipeline as ap_mod
import gui.docks.cascade as cascade_mod
import gui.docks.copper_select as copper_mod
import gui.docks.trees_dock as td_mod
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
    monkeypatch.setattr(cascade_mod, "create_board_adapter", _AdapterSpy)
    monkeypatch.setattr(ap_mod, "create_board_adapter", _AdapterSpy)


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


# ── Кn (plan_2026_09_22_door_guard_aim_and_socket_close) — the WORKERS that build
# their own socket must hand it back, on BOTH paths ────────────────────────────
# One KiCadBoardAdapter == one kipy.KiCad == one pynng Req0 socket (see the header),
# so a worker that builds an adapter owns a socket. Two halves, because they answer
# different questions:
#   * the behaviour half is parametrized over (worker x path) — six workers, the
#     happy path and a refresh_board() that raises — and asserts `created == closed`
#     for every row. "closed > 0" would pass with three of four sockets handed back,
#     which is exactly the leak this step is about;
#   * the class half scans gui/ and requires every `create_board_adapter(...)` call to
#     sit in a function that closes the very object it built. That is the only cell
#     here which would have caught Т2-6 on its own, and the only one a FIFTH worker
#     cannot slip past — the list in the first half is written by hand, and forgetting
#     to extend a hand-written list is how the fourth leak got in.

_TREES_WORKERS = ("run_internode_reread_worker", "run_anchor_live_position_worker",
                  "run_anchor_base_mm_worker", "run_extract_new_cell_worker")
_COPPER_WORKERS = ("run_select_record_copper_worker",
                   "run_identify_copper_worker")
_ALL_WORKERS = _TREES_WORKERS + _COPPER_WORKERS
# The three workers with no try/except by contract: a failure travels OUT of them (the
# dock's start_long_op reports it), and their socket must go back all the same. The
# other three answer with their own "unavailable" / None, which is their contract.
_RAISES_OUT = {"run_internode_reread_worker",
               "run_select_record_copper_worker",
               "run_identify_copper_worker"}


class _WorkerAdapterSpy:
    """KiCadBoardAdapter stand-in for a WORKER: counts constructions and closes, and
    can be told to raise out of refresh_board(). Everything else is a MagicMock, so a
    worker that reaches for a board method does not crash on the stand-in."""

    def __init__(self, *, fail_refresh=False):
        self._mock = MagicMock()
        self.created = 0
        self.closed = 0
        self.fail_refresh = fail_refresh

    def __call__(self, **_factory_kwargs):
        self.created += 1
        return self

    def __getattr__(self, name):
        return getattr(self._mock, name)

    def close(self):
        self.closed += 1

    def refresh_board(self):
        if self.fail_refresh:
            raise RuntimeError("no board in this test")


def _plain_tree(ref=None):
    return Tree(name="t", anchor=TreeAnchor(ref=ref), nodes=[])


def _payload_for(worker_name, monkeypatch, tmp_path):
    """The payload that worker needs, with every seam BELOW the adapter stubbed: what
    is measured is the socket, not the read — so each row names its OWN seams, because
    each worker reads a different thing."""
    if worker_name == "run_internode_reread_worker":
        import kicadstamp.internode_capture as capture_mod

        monkeypatch.setattr(capture_mod, "plan_internode_reread",
                            lambda *a, **k: SimpleNamespace(added=[]))
        monkeypatch.setattr(capture_mod, "apply_reread_plan", lambda cfg, plan: cfg)
        return {"cfg": Config(), "tree": _plain_tree(), "sheet_names": {}}
    if worker_name in ("run_anchor_live_position_worker",
                       "run_anchor_base_mm_worker"):
        monkeypatch.setattr(td_mod, "_anchor_base_live_position",
                            lambda *a, **k: (Vector2.from_xy(1_000, 2_000), 0.0))
        return {"cfg": object(), "tree": _plain_tree(ref="U1"), "sheet_names": {}}
    if worker_name == "run_extract_new_cell_worker":
        monkeypatch.setattr(
            "gui.docks.tree_from_selection.extract_new_cell_for_instantiation",
            lambda *a, **k: {"new_cell": {"components": [{"role": "R1"}]}})
        return {"cluster": "CL", "cell_name": "new_cell", "selected": [],
                "raw_items": [], "absolute": False}
    config_path = tmp_path / "root.sexp"
    config_path.write_text("", encoding="utf-8")
    if worker_name == "run_select_record_copper_worker":
        import kicadstamp.net_trace_planner as planner_mod

        monkeypatch.setattr(planner_mod, "find_live_copper",
                            lambda *a, **k: SimpleNamespace(
                                found=[], identity="rec", expected_count=0,
                                missing_count=0, found_by_registry=0,
                                found_by_geometry=0, reason=None))
        return {"record": object(), "config_path": str(config_path),
                "sheet_names": {}}
    monkeypatch.setattr(copper_mod, "identify_selected_copper",
                        lambda *a, **k: identify_copper_result())
    return {"cfg": Config(), "config_path": str(config_path), "sheet_names": {}}


def identify_copper_result():
    """The read-only answer of "Whose copper is this?", as plain data — the seam this
    cell stubs so that what is left to measure is the socket."""
    return copper_mod.IdentifyResult(total=0)


@pytest.mark.parametrize("worker_name", _ALL_WORKERS)
@pytest.mark.parametrize("fail_refresh", [False, True],
                         ids=["happy", "refresh-raises"])
def test_a_worker_hands_its_own_socket_back(worker_name, fail_refresh, monkeypatch,
                                            tmp_path):
    """Кn — every worker that builds its OWN adapter closes it, on BOTH paths: the
    happy one and a refresh_board() that raises. The two rows are one cell each (rule
    35): a close moved onto the happy path is exactly the shape that keeps a failing
    operation's socket, and the trees_dock sample itself says "the finally, not a
    happy-path close".

    The seam is the FACTORY, per module — trees_dock imports it at module level, the
    copper workers import it lazily inside themselves, so each half is stubbed where
    its own import looks.

    Mutation check: dropping that worker's `finally: adapter.close()` must turn BOTH
    this parametrized id and the structural cell below red; a red on only one of them
    is a finding, not a kill (the harness reports which id actually bit)."""
    spy = _WorkerAdapterSpy(fail_refresh=fail_refresh)
    if worker_name in _COPPER_WORKERS:
        monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter", spy)
        runner = getattr(copper_mod, worker_name)
    else:
        monkeypatch.setattr(td_mod, "create_board_adapter", spy)
        runner = getattr(td_mod, worker_name)
    payload = _payload_for(worker_name, monkeypatch, tmp_path)

    if fail_refresh and worker_name in _RAISES_OUT:
        # No try/except in that worker by contract, so the failure travels out of it —
        # and the socket goes back anyway, which is what the assert below reads.
        with pytest.raises(RuntimeError):
            runner(payload)
    else:
        runner(payload)

    assert spy.created == 1, \
        "the worker did not build the adapter this cell stubs — a stale seam?"
    assert spy.closed == spy.created, (
        "a worker left its socket behind: created == closed is the property, "
        "closed > 0 would hide three of four")


def _adapter_construction_owners(path):
    """(line, owner name, closes_its_own_object) for every `create_board_adapter(...)`
    call in `path` — parsed, never grepped (step-style probes carry the name inside a
    docstring, and a text scan mistakes that for a call). The "closes" half compares
    the NAME the result was assigned to, because "somebody in this function calls
    close()" is not the property: `if adapter is not None: adapter.close()` is."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        func = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(func, ast.Name)
                and func.id == "create_board_adapter"):
            continue
        owner = node
        while (not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
               and owner in parents):
            owner = parents[owner]
        assigned = [target.id for sub in ast.walk(owner)
                    if isinstance(sub, ast.Assign) and sub.value is node
                    for target in sub.targets if isinstance(target, ast.Name)]
        closes = any(
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute) and sub.func.attr == "close"
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == name
            for sub in ast.walk(owner) for name in assigned)
        found.append((node.lineno, getattr(owner, "name", "<module>"), closes))
    return found


def test_gui_never_builds_a_board_adapter_it_does_not_close():
    """Кn — the CLASS cell: every `create_board_adapter(...)` call in gui/ sits in a
    function that closes the very object it built.

    This is the only cell here which would have caught Т2-6's leak on its own, and the
    only one a FIFTH worker cannot slip past: the parametrized list above is written by
    hand, and forgetting to extend a hand-written list is how the fourth leak got in —
    the same disease Кm found in the door's own watchdog, a list trusted to memory.

    Scope, deliberately, and named so the empty cells are visible rather than
    accidental: gui/ only. kicadstamp/apply_pipeline.py owns its adapter through its own
    context manager (its callers enter `with ApplyPipeline(...)`), and mcp_server/,
    tools/ and kicadstamp/diagnostics/ are long-lived processes or probes; following
    ownership ACROSS files would turn this cell into the transitive analysis nobody
    maintains. Widening it is a step of its own, not a line to add here."""
    offenders = [f"{path.relative_to(ROOT)}:{line} ({owner})"
                 for path in sorted((ROOT / "gui").rglob("*.py"))
                 for line, owner, closes in _adapter_construction_owners(path)
                 if not closes]

    assert offenders == [], (
        "a board adapter is built where nothing closes it, so the socket goes back to "
        "the garbage collector — the incident KiCadBoardAdapter.close() was written "
        f"for: {offenders}")
