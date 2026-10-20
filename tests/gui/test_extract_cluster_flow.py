#!/usr/bin/env python3
# tests/gui/test_extract_cluster_flow.py
"""Ч3 (plan_2026_10_15_extract_and_read_paths_cleanup): the "Extract cluster…"
flow lives in gui/extract_cluster_flow.py, read the board ONLY on a worker with
its OWN adapter, and report through the Log — the nine modals it used to open
are gone.

What the cells defend, one property each:

* every former window is a Log line of the right severity, with the wording
  unchanged (one parametrized row per window);
* the UI half never names `connection.board` (AST — the door census
  `tools/door_lint.py` says the same thing from the outside, and the suspect
  count drops 24 -> 23 with this change);
* the worker builds its own adapter from the payload's timeout, refreshes once,
  closes it in `finally`, and answers plain data — including on failure;
* the reuse branch writes NOTHING: the config file is byte-identical afterwards.

The tenth window of this flow is deliberately out of Ч3 and named in the plan:
`DockHub._show_snapshot_refresh_error`, the shared error path of
`_refresh_snapshot_then` used by BOTH Extract flows.
"""
import ast
import logging
import pathlib
from types import SimpleNamespace

import pytest

import gui.extract_cluster_flow as flow_mod
from gui.extract_cluster_flow import ExtractClusterFlow, run_extract_cluster_worker
from kicadstamp.config_working_set import WORKING_SET, set_active_graph_root


# ── Fakes ───────────────────────────────────────────────────────────────────

def _write(path, data=None):
    """A FORMAT 3 root: opening a format-2 file under the gate LIFTS it on disk
    (a legitimate open-time write + .bak), which would drown the "reuse writes
    nothing" property in an unrelated one."""
    from kicadstamp.config.sexp_format import dict_to_sexp
    path.write_text(dict_to_sexp(data if data is not None else {},
                                 format_number=3), encoding="utf-8")


def _connection(snapshot=(), connected=True):
    return SimpleNamespace(is_connected=connected, snapshot=list(snapshot),
                           long_op_active=False, timeout_ms=1000)


def _hub(connection, root_path, *, selection=(), raw=()):
    """The collaborator surface ExtractClusterFlow touches — nothing else."""
    tree_dock = SimpleNamespace(
        refresh=lambda: None,
        graph_changed=SimpleNamespace(emit=lambda: None))
    return SimpleNamespace(
        main_window=SimpleNamespace(connection=connection),
        root_metadata_dock=SimpleNamespace(root_path=root_path),
        _selection_footprints=list(selection),
        _selection_raw_items=list(raw),
        config_tree_dock=tree_dock,
        _refresh_snapshot_then=lambda _title, on_ready: on_ready())


def _cluster(cluster="PIF_AVDD", sheet="Channel_1"):
    return SimpleNamespace(cluster=cluster, sheet=sheet)


def _clusters(monkeypatch, clusters):
    """Pin `fully_selected_clusters` (imported lazily by the flow) so a row can
    reach the dialog / reuse branches without a real board selection."""
    monkeypatch.setattr("gui.docks.reead.fully_selected_clusters",
                        lambda *a, **k: list(clusters))


def _dialog(monkeypatch, *, existing, name="pif_avdd_channel_1"):
    class _FakeDialog:
        def __init__(self, parent, clusters, cfg, selection_footprints=()):
            self._clusters = clusters
            self.existing = existing

        def exec(self):
            from PyQt6.QtWidgets import QDialog
            return QDialog.DialogCode.Accepted

        def selected_cluster(self):
            return self._clusters[0]

        def entity_name(self):
            return name

        def origin_override(self):
            return (None, None)

    monkeypatch.setattr("gui.docks.extract_cluster_dialog.ExtractClusterDialog",
                        _FakeDialog)


def _record_start_long_op(monkeypatch):
    """The flow's worker start, recorded instead of run: these rows are about
    what the UI half SAYS, not about the extraction itself."""
    seen = []
    monkeypatch.setattr(flow_mod, "start_long_op",
                        lambda *a, **k: seen.append((a, k)) or "op-handle")
    return seen


# ── One row per former window ──────────────────────────────────────────────

ERROR = logging.ERROR
INFO = logging.INFO


def _case_not_connected(caplog, monkeypatch, tmp_path):
    hub = _hub(None, tmp_path / "root.sexp")
    ExtractClusterFlow(hub)._now()


def _case_no_root(caplog, monkeypatch, tmp_path):
    hub = _hub(_connection(), None)
    ExtractClusterFlow(hub)._now()


def _case_broken_config(caplog, monkeypatch, tmp_path):
    hub = _hub(_connection(), tmp_path / "no_such_root.sexp")
    ExtractClusterFlow(hub)._now()


def _case_no_cluster(caplog, monkeypatch, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root)
    _clusters(monkeypatch, [])
    hub = _hub(_connection(), root)
    ExtractClusterFlow(hub)._now()


def _case_reused_by_the_dialog(caplog, monkeypatch, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root)
    _clusters(monkeypatch, [_cluster()])
    _dialog(monkeypatch, existing=True)
    seen = _record_start_long_op(monkeypatch)
    hub = _hub(_connection(), root)
    ExtractClusterFlow(hub)._now()
    assert seen == [], "the reuse branch must not start the worker"


def _case_reused_after_the_read(caplog, monkeypatch, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root)
    hub = _hub(_connection(), root)
    ExtractClusterFlow(hub)._finish({"entity": None, "cells": {}}, root,
                                    _cluster(), "pif_avdd_channel_1")


def _case_save_failed(caplog, monkeypatch, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root)

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("kicadstamp.config_writer.write_data", _boom)
    hub = _hub(_connection(), root)
    ExtractClusterFlow(hub)._finish(
        {"entity": {"name": "E", "cell": "c1"}, "cells": {}, "cell_new": True},
        root, _cluster(), "E")


def _case_saved_with_a_new_cell(caplog, monkeypatch, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root)
    # The format-3 writer needs the ACTIVE graph root, exactly as the config
    # tree dock sets it in production before any write goes through it.
    set_active_graph_root(root)
    hub = _hub(_connection(), root)
    ExtractClusterFlow(hub)._finish(
        {"entity": {"name": "E", "cell": "c1"},
         "cells": {"c1": {"components": [{"role": "DAC"}]}}, "cell_new": True},
        root, _cluster(), "E")
    WORKING_SET.flush(root)


def _case_saved_cell_existed(caplog, monkeypatch, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {"c1": {"components": [{"role": "DAC"}]}}})
    set_active_graph_root(root)
    hub = _hub(_connection(), root)
    ExtractClusterFlow(hub)._finish(
        {"entity": {"name": "E", "cell": "c1"}, "cells": {}, "cell_new": False},
        root, _cluster(), "E")
    WORKING_SET.flush(root)


def _case_worker_failed(caplog, monkeypatch, tmp_path):
    hub = _hub(_connection(), tmp_path / "root.sexp")
    ExtractClusterFlow(hub)._failed("socket died")


#: (label, runner, level, fragment) — the NINE windows the flow used to open,
#: plus the worker's own failure line (the one NEW wording Ч3 introduces).
WINDOWS = [
    ("not connected", _case_not_connected, ERROR, "Not connected."),
    ("no project root", _case_no_root, ERROR, "Set the project root first."),
    ("broken config", _case_broken_config, ERROR, "Failed to load config:"),
    ("no fully selected cluster", _case_no_cluster, ERROR,
     "No fully selected Cluster found"),
    ("reused (the dialog knew)", _case_reused_by_the_dialog, INFO,
     "already exists for this Cluster + sheet"),
    ("reused (the entity appeared)", _case_reused_after_the_read, INFO,
     "already exists for this Cluster + sheet"),
    ("save failed", _case_save_failed, ERROR, "Failed to save the Entity:"),
    ("saved with a new cell", _case_saved_with_a_new_cell, INFO,
     "with its new cell"),
    ("saved, the cell existed", _case_saved_cell_existed, INFO,
     "already existed"),
    ("the worker itself failed", _case_worker_failed, ERROR,
     "Failed to extract the cluster:"),
]


@pytest.mark.parametrize("label, runner, level, fragment", WINDOWS,
                         ids=[row[0] for row in WINDOWS])
def test_each_former_window_is_a_log_line(label, runner, level, fragment,
                                          caplog, monkeypatch, tmp_path):
    """The window is GONE and its exact wording survives as a Log record of the
    severity the Ч0 map assigned. The runner drives the REAL flow method; a
    returning modal would raise out of it (the legacy cells arm QMessageBox to
    explode), so "no window" is structural here, not an assertion on a spy."""
    caplog.clear()
    runner(caplog, monkeypatch, tmp_path)

    matching = [r for r in caplog.records
                if r.levelno == level and fragment in r.getMessage()]
    assert matching, (
        f"{label}: no {logging.getLevelName(level)} line carrying {fragment!r}; "
        f"saw {[(r.levelname, r.getMessage()[:60]) for r in caplog.records]}")


def test_the_reuse_branch_leaves_the_config_byte_identical(monkeypatch, tmp_path):
    """A reuse writes NOTHING — not to the disk and not even into the working
    set: `write_data` is not called at all, no .bak appears, and the file comes
    back byte for byte.

    All three are asserted because a write STAGES into WORKING_SET and only a
    flush touches the disk: a bytes-only cell stays green while the reuse
    quietly stages a rewrite (measured on the Ч3 mutation rig)."""
    import kicadstamp.config_writer as config_writer

    root = tmp_path / "root.sexp"
    _write(root)
    before = root.read_bytes()
    _clusters(monkeypatch, [_cluster()])
    _dialog(monkeypatch, existing=True)
    _record_start_long_op(monkeypatch)
    real_write_data = config_writer.write_data
    calls = []

    def _spy(*a, **k):
        calls.append(a)
        return real_write_data(*a, **k)

    monkeypatch.setattr(config_writer, "write_data", _spy)
    hub = _hub(_connection(), root)

    ExtractClusterFlow(hub)._now()

    assert calls == [], "a reuse must not call write_data"
    assert root.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["root.sexp"]


# ── The board lives in the worker only ─────────────────────────────────────

def test_the_ui_half_never_names_the_board():
    """AST over the flow module: the board is not reached EITHER way — not as an
    attribute (`connection.board`) and not as the STRING `"board"` handed to
    `getattr`/`hasattr`/`setattr`, which is how the old code spelled the very
    same read and how a mutation re-introduces it without an Attribute node
    (measured on the Ч3 rig: an attribute-only sweep let that mutant live).

    The door census (`tools/door_lint.py --all`) is the same property from the
    outside — it drops from 24 suspects to 23 with this change."""
    source = pathlib.Path(flow_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = [node.lineno for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute) and node.attr == "board"]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in ("getattr", "hasattr", "setattr"):
            continue
        if any(isinstance(arg, ast.Constant) and arg.value == "board"
               for arg in node.args[1:2]):
            offenders.append(node.lineno)
    assert offenders == [], (
        f"the flow reaches the board at line(s) {sorted(offenders)} — the board "
        "belongs to run_extract_cluster_worker, which builds its OWN adapter")


class _FakeAdapter:
    def __init__(self):
        self.refreshed = 0
        self.closed = 0

    def refresh_board(self):
        self.refreshed += 1

    def close(self):
        self.closed += 1


def test_the_worker_builds_its_own_adapter_and_closes_it(monkeypatch, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root)
    built, factory_kwargs = [], []

    def _factory(**kwargs):
        factory_kwargs.append(kwargs)
        adapter = _FakeAdapter()
        built.append(adapter)
        return adapter

    seen = {}

    def _cell_and_entity(adapter, c, cfg, cells, fps, raw, **kwargs):
        seen["adapter"] = adapter
        seen["kwargs"] = kwargs
        return {"name": "E", "cell": "c1"}

    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter", _factory)
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.create_cell_and_entity_for_cluster",
        _cell_and_entity)

    result = run_extract_cluster_worker({
        "root_path": str(root), "cluster": _cluster(),
        "entity_name": "E", "timeout_ms": 1234})

    # Only the timeout crossed the boundary — no adapter, no widget, no cfg.
    assert factory_kwargs == [{"timeout_ms": 1234}]
    assert seen["adapter"] is built[0], "the worker must hand over ITS OWN adapter"
    assert built[0].refreshed == 1, "the fresh adapter must be refreshed once"
    assert built[0].closed == 1, "the worker must close its own adapter"
    assert result == {"entity": {"name": "E", "cell": "c1"}, "cells": {},
                      "cell_new": True}


def test_a_failing_worker_answers_plain_data_and_closes_nothing(monkeypatch, tmp_path):
    """A dead socket is a result, not an exception crossing the thread — and the
    adapter that was never built is never closed either."""
    root = tmp_path / "root.sexp"
    _write(root)

    def _factory(**_kwargs):
        raise RuntimeError("no KiCad")

    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter", _factory)

    result = run_extract_cluster_worker({
        "root_path": str(root), "cluster": _cluster(), "timeout_ms": 1000})

    assert result == {"error": "no KiCad"}
