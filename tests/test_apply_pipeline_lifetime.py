#!/usr/bin/env python3
"""С4/С5/С6/С7 of plan_2026_09_14_apply_pipeline_socket_leak.md §P.4 — the
lifetime of the adapter an ApplyPipeline owns.

The pipeline builds its own KiCadBoardAdapter in _connect_adapter() — one kipy
client, one pynng socket (kicadstamp/kicad/adapter.py:72) — and run()
deliberately does NOT close it: PlacerDock reads ``pipeline.adapter`` AFTER
run() has returned (its _tag_cluster reads footprints and writes ``Cluster=``
through it), so the lifetime belongs to the CALLER, not to run() (plan §P.2).
These four guards pin that contract:

  С4 — close() is idempotent: the second and the tenth call are no-ops;
  С5 — close() never raises, even when the socket is already broken — the same
       contract as KiCadBoardAdapter.close(), which it delegates to;
  С6 — run_apply() (the library entry point that owns its pipeline outright)
       closes it AND still returns the dry-run report whole;
  С7 — the context-manager form closes when the body raises.

The no-live-adapter guard (a stand-in adapter, no kipy) sits on every test: the
cost of a real KiCadBoardAdapter here would be a real IPC connection.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock

import pytest

import kicadstamp.apply_pipeline as ap_mod
from kicadstamp.apply_pipeline import ApplyPipeline, RunOptions, run_apply


def _pipeline_with_adapter(adapter=None):
    """A real ApplyPipeline whose adapter is a stand-in — no kipy involved."""
    pipeline = ApplyPipeline("board.sexp")
    pipeline.adapter = adapter if adapter is not None else MagicMock()
    return pipeline


def test_close_is_idempotent():
    """С4 (kills M4 — dropping the repeat-call guard): closing twice must not
    touch the socket twice, and the adapter reference is dropped so a later use
    of ``pipeline.adapter`` is loud (None) instead of a silent write onto a
    socket that is already gone."""
    adapter = MagicMock()
    pipeline = _pipeline_with_adapter(adapter)

    pipeline.close()
    pipeline.close()
    pipeline.close()

    adapter.close.assert_called_once_with()
    assert pipeline.adapter is None


def test_close_never_propagates_a_broken_socket():
    """С5 (kills M7 — letting the adapter's own failure escape): a broken
    socket is often exactly WHY close() is being called, so the failure is
    logged and dropped, never thrown into a caller that is unwinding."""
    adapter = MagicMock()
    adapter.close.side_effect = RuntimeError("socket already broken")
    pipeline = _pipeline_with_adapter(adapter)

    pipeline.close()  # must not raise

    adapter.close.assert_called_once_with()
    assert pipeline.adapter is None


def test_run_apply_closes_the_adapter_and_keeps_the_dry_run_report(monkeypatch):
    """С6 (kills a close that eats the return value, and pins run_apply as a
    closing caller): the whole pipeline is stubbed except _connect_adapter —
    the step that really builds the socket — so this is a counter, not a
    mock."""
    created = {"n": 0}
    closed = {"n": 0}

    class _SpyAdapter:
        def __init__(self, **kwargs):
            created["n"] += 1

        def refresh_board(self):
            # _connect_adapter() really calls this right after building the
            # adapter — a no-op here keeps that step real without a board.
            pass

        def close(self):
            closed["n"] += 1

    monkeypatch.setattr(ap_mod, "create_board_adapter", _SpyAdapter)
    for step in ("_load_config", "_filter_config", "_validate",
                 "_resolve_order", "_create_planner", "_execute"):
        monkeypatch.setattr(ap_mod.ApplyPipeline, step, lambda self: None)
    monkeypatch.setattr(ap_mod.ApplyPipeline, "_dry_run",
                        lambda self: ["=== DRY RUN ===", "line"])

    result = run_apply(RunOptions(config_path="board.sexp", dry_run=True))

    assert result == ["=== DRY RUN ===", "line"], \
        "the close must not swallow the dry-run report"
    assert created["n"] == 1
    assert closed["n"] == 1, "run_apply owns its pipeline — it must close it"


def test_context_manager_closes_when_the_body_raises():
    """С7 (kills M6 — an __exit__ that only closes on the happy path): the
    exception must still propagate (the cascade's per-name failure branch
    depends on it) while the socket goes back."""
    adapter = MagicMock()
    pipeline = _pipeline_with_adapter(adapter)

    with pytest.raises(RuntimeError, match="boom"):
        with pipeline:
            raise RuntimeError("boom")

    adapter.close.assert_called_once_with()
    assert pipeline.adapter is None
