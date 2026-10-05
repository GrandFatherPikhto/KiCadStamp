# tests/gui/test_explode_worker_gate.py
"""Cells for the worker's "clusters are exploded" GATE (Р2-2, plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md``).

One callable is installed in ``gui/worker.py`` (``set_long_op_gate``); it returns
the refusal line when board ops must be blocked, or None when they may run.
``start_long_op`` consults it and, unless the caller passed
``allowed_while_exploded=True``, refuses the op BEFORE any worker exists.

The gate is module-level (GLOBAL), so the reset fixture below is mandatory: a
leaked gate would refuse board ops in the NEXT cell — and, because xdist workers
share one process per worker, in unrelated cells of the same worker.
"""
import pytest

from gui import worker


@pytest.fixture(autouse=True)
def _reset_gate():
    """Install nothing before each cell and take the gate down after it — the
    module slot is global (see the module docstring)."""
    worker.set_long_op_gate(None)
    worker._ACTIVE_CONTROLLERS.clear()
    yield
    worker.set_long_op_gate(None)
    worker._ACTIVE_CONTROLLERS.clear()


def _spy_start(monkeypatch):
    """Replace LongOpController.start with a recorder so no QThread spawns; the
    refusal/allow decision is what these cells measure, not the thread."""
    started = []
    monkeypatch.setattr(worker.LongOpController, "start",
                        lambda self, fn, *a: started.append((fn, a)))
    return started


def test_no_gate_runs_the_op_as_before(monkeypatch, qapp):
    """THE guard cell: with no gate installed, start_long_op behaves exactly as
    before — the worker is started and nothing is refused."""
    started = _spy_start(monkeypatch)
    fn = lambda: 1
    errors = []
    worker.start_long_op(object(), [], fn, lambda r: None, errors.append)
    assert [f for (f, _a) in started] == [fn]
    assert errors == []


def test_gate_message_refuses_without_starting(monkeypatch, qapp, caplog):
    """A gate message means: no worker, on_error gets the message, and the
    refusal is logged RED (logger.error)."""
    started = _spy_start(monkeypatch)
    worker.set_long_op_gate(lambda: "REFUSED-EXPLODED")
    errors = []
    called = []
    import logging
    with caplog.at_level(logging.ERROR):
        worker.start_long_op(object(), [], lambda: called.append(1),
                             lambda r: None, errors.append)
    assert errors == ["REFUSED-EXPLODED"]
    assert started == [] and called == []
    assert any("REFUSED-EXPLODED" in rec.message for rec in caplog.records)


def test_gate_none_message_does_not_refuse(monkeypatch, qapp):
    """A gate that returns None (allowed) must NOT refuse."""
    started = _spy_start(monkeypatch)
    worker.set_long_op_gate(lambda: None)
    fn = lambda: 1
    errors = []
    worker.start_long_op(object(), [], fn, lambda r: None, errors.append)
    assert [f for (f, _a) in started] == [fn] and errors == []


def test_allowed_while_exploded_opens_the_gate(monkeypatch, qapp):
    """allowed_while_exploded=True (the tab's own ops, Select cell, Р3 read) runs
    even when the gate would refuse everything else."""
    started = _spy_start(monkeypatch)
    worker.set_long_op_gate(lambda: "REFUSED-EXPLODED")
    fn = lambda: 1
    errors = []
    worker.start_long_op(object(), [], fn, lambda r: None, errors.append,
                         allowed_while_exploded=True)
    assert [f for (f, _a) in started] == [fn] and errors == []


def test_a_raising_gate_allows(monkeypatch, qapp):
    """A broken guard must never wedge every board operation: an exception from
    the gate is swallowed and treated as allowed."""
    started = _spy_start(monkeypatch)

    def _boom():
        raise RuntimeError("gate is broken")

    worker.set_long_op_gate(_boom)
    fn = lambda: 1
    worker.start_long_op(object(), [], fn, lambda r: None, lambda e: None)
    assert [f for (f, _a) in started] == [fn]
