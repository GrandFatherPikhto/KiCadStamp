# tests/gui/test_explode_page.py
"""Cells for the "Разнос" tab + lock + the board DOOR (Р2/Р2а, plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md``).

Real MainWindow (offscreen). The plan/explode/restore/state WORKERS are stubbed
at the module and the page's ``start_long_op`` is made SYNCHRONOUS (the real
worker thread + the gate are covered by tests/gui/test_explode_worker_gate.py) —
so these cells measure the tab's logic, the LOCK and the DOOR on the UI thread.
"""
import logging
from types import SimpleNamespace

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent, QColor
from PyQt6.QtWidgets import QMessageBox

from kicadstamp.domain.geometry import Box2, Vector2
from kicadstamp.explode import ExplodePlan, NetTracePiece

from gui import overlay_markers
from gui import worker as worker_mod
from gui.docks import explode_page as page_mod


def _plan(table=()):
    return ExplodePlan(cell_name="dac_buf", cluster="DAC_BUF", sheet="Channel_0",
                       instance=(), area=Box2(pos=Vector2(0, 0),
                                              size=Vector2(1_000_000, 1_000_000)),
                       instances=(), table=tuple(table), moves=())


def _piece(uuid="u1", ticked=True, touches="PIF"):
    return NetTracePiece(record="rec", kind="track", net="N", layer="F.Cu",
                         length_mm=1.0, uuid=uuid, touches=touches,
                         ticked=ticked, item=object())


_JOURNAL = {"time": "2026-10-05 12:00:00", "cell": "dac_buf",
            "cluster": "DAC_BUF", "sheet": "Channel_0", "items": {"a": 1}}


def _sync_start_long_op(monkeypatch):
    """Make the page's start_long_op run its worker INLINE (the real thread +
    the gate are tested elsewhere)."""
    def _sync(connection, widgets, fn, on_success, on_error, *args, **kwargs):
        try:
            result = fn(*args)
        except Exception as e:  # noqa: BLE001 — mirror the worker's routing
            on_error(str(e))
            return None
        on_success(result)
        return None
    monkeypatch.setattr(page_mod, "start_long_op", _sync)


# Step 2 of plan_2026_10_09_entity_page: the "Explode" tab lives on the ENTITY
# page, so the door keys on an ENTITY and the address comes from its RECORD.
_ENTITY = "amp"
_CELL = "dac_buf"
_CLUSTER = "DAC_BUF"
_SHEET = "Channel_0"
_CONFIG = {
    "cells": {_CELL: {"components": [{"role": "R"}]}},
    "entities": [{"name": _ENTITY, "cell": _CELL, "cluster": _CLUSTER,
                  "sheet": _SHEET}],
}


def _write_root(path):
    from kicadstamp.config.sexp_format import dict_to_sexp
    path.write_text(dict_to_sexp(_CONFIG, format_number=2), encoding="utf-8")
    return path


def _open_via_door(hub, entity=_ENTITY, cell=_CELL, cluster=_CLUSTER,
                   sheet=_SHEET):
    """The ONE door, called the way the ENTITY leaf's "Explode…" item does."""
    hub._open_explode(cell, None, cluster, sheet, entity)


@pytest.fixture
def ex(real_main_window, monkeypatch, tmp_path):
    """A real MainWindow on a fake board, a throwaway config root holding ONE
    entity, the worker stubs and a SYNCHRONOUS page start_long_op; the global gate
    is taken down."""
    w = real_main_window
    w.connection.board = SimpleNamespace(adapter=object())
    root = _write_root(tmp_path / "config.sexp")
    w._dock_hub.root_metadata_dock._path = root
    w._dock_hub.entity_dock.set_root_path(root)
    monkeypatch.setattr(w._dock_hub, "refresh_snapshot_and_push",
                        lambda *a, **k: None)
    _sync_start_long_op(monkeypatch)
    yield w
    w._dock_hub.explode_guard.detach()
    worker_mod.set_long_op_gate(None)


def _stub_plan(monkeypatch, captured=None, plan=None):
    def _fake(connection, config_path, cell, cluster, sheet, margin, gap,
              overrides):
        if captured is not None:
            captured.update(overrides)
        return plan if plan is not None else _plan()
    monkeypatch.setattr(page_mod, "plan_worker", _fake)


def _stub_state(monkeypatch, result):
    monkeypatch.setattr(page_mod, "explode_state_worker",
                        lambda connection: result)


def _patch_cfg(monkeypatch, cfg):
    """Make BOTH the dock hub (`_load_cfg`) and the page (`_reload_cfg`) see
    `cfg` — each imports `load_config` from kicadstamp.config.loader at CALL
    time, so patching the module attribute reaches both."""
    import kicadstamp.config.loader as loader
    monkeypatch.setattr(loader, "load_config",
                        lambda path: (cfg, SimpleNamespace(sheet_names={})))


def test_the_page_hands_its_file_to_the_tab(ex):
    """Р3а-1 (приёмка): the ENTITY page hands the tab the entity's OWNING FILE —
    the door into the tab must not have to ask anyone else for it."""
    hub = ex._dock_hub
    page = hub.entity_dock
    page._entity_data = {"name": _ENTITY, "cell": _CELL}
    page._entity_file = "/tmp/page.sexp"
    page._sync_explode_context()
    assert hub.explode_page._file_path == "/tmp/page.sexp"


def test_the_door_does_not_reload_the_same_entity(ex, monkeypatch):
    """Р3а-1 (приёмка): opening the SAME entity by name must NOT reload the page
    (a reload drops unsaved input — the page-merge rule)."""
    hub = ex._dock_hub
    _open_via_door(hub)                      # load the entity once
    loads = []
    monkeypatch.setattr(hub.entity_dock, "load_entity",
                        lambda name: loads.append(name))
    _open_via_door(hub)                      # same entity — no reload
    assert loads == []


# ── doors ───────────────────────────────────────────────────────────────────

def test_entity_door_opens_the_entity_page_on_the_explode_tab(ex, monkeypatch):
    """Step 2: the door opens the ENTITY page on the requested entity and selects
    the Explode tab; the tab's address comes from the entity RECORD."""
    _stub_plan(monkeypatch)
    hub = ex._dock_hub
    _open_via_door(hub)
    page = hub.explode_page
    assert hub.config_tree_dock.current_right_page_index() == hub._entity_page
    assert hub.entity_dock.tabs.currentWidget() is page
    assert hub.entity_dock._current_name == _ENTITY
    assert (page._cell_name, page._cluster, page._sheet) == (
        "dac_buf", "DAC_BUF", "Channel_0")


# ── ticks / table ───────────────────────────────────────────────────────────

def test_a_single_untick_reaches_the_plan_as_an_override(ex, monkeypatch):
    captured = {}
    _stub_plan(monkeypatch, captured=captured, plan=_plan((_piece("u1"),)))
    hub = ex._dock_hub
    _open_via_door(hub)
    assert hub.explode_page._plan is not None
    row = hub.explode_page.tree.topLevelItem(0).child(0)
    row.setCheckState(0, Qt.CheckState.Unchecked)
    assert captured.get("u1") is False


def test_ticks_are_not_checkable_after_explode(ex, monkeypatch):
    _stub_plan(monkeypatch, plan=_plan((_piece("u1"),)))
    monkeypatch.setattr(page_mod, "explode_worker", lambda connection, plan: [])
    _stub_state(monkeypatch, ("has", _JOURNAL))     # the journal now exists
    hub = ex._dock_hub
    _open_via_door(hub)
    row = hub.explode_page.tree.topLevelItem(0).child(0)
    assert row.flags() & Qt.ItemFlag.ItemIsUserCheckable
    hub.explode_page._explode()
    assert hub.explode_guard.active
    row = hub.explode_page.tree.topLevelItem(0).child(0)
    assert not (row.flags() & Qt.ItemFlag.ItemIsUserCheckable)


def test_a_tee_row_is_highlighted_yellow(ex, monkeypatch):
    _stub_plan(monkeypatch, plan=_plan((_piece("u1", touches="tee"),)))
    hub = ex._dock_hub
    _open_via_door(hub)
    row = hub.explode_page.tree.topLevelItem(0).child(0)
    assert row.background(0).color() == QColor(255, 250, 205)


# ── lock (Р2а-1: the page must stay USABLE) ─────────────────────────────────

def test_exploded_locks_the_page_context_and_the_strips(ex):
    """Step 2: while exploded the ENTITY page's tab strip (the other tabs), the
    Config tree and the window's tab strip are disabled; the right view is pinned
    to the ENTITY page, whose Explode tab is current and usable (isEnabled
    accounts for ancestors — Р2а-1)."""
    hub = ex._dock_hub
    page = hub.entity_dock
    hub.explode_guard.set_from_journal(_JOURNAL)
    assert not hub.left_tabs.tabBar().isEnabled()
    assert not page.tabs.tabBar().isEnabled()
    assert not hub.config_tree_dock.tree.isEnabled()
    assert hub.config_tree_dock.current_right_page_index() == hub._entity_page
    assert page.tabs.currentWidget() is hub.explode_page
    assert hub.explode_page.isEnabled()
    assert hub.explode_page.restore_button.isEnabled()


def test_gate_refuses_board_ops_unless_allowed(ex, monkeypatch):
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    started = []
    monkeypatch.setattr(worker_mod.LongOpController, "start",
                        lambda self, fn, *a: started.append(fn))
    errors = []
    fn = lambda: 1
    worker_mod.start_long_op(ex.connection, [], fn, lambda r: None, errors.append)
    assert started == [] and errors == [hub.explode_guard.refusal_line()]
    allowed_errors = []
    worker_mod.start_long_op(ex.connection, [], fn, lambda r: None,
                             allowed_errors.append, allowed_while_exploded=True)
    assert started == [fn] and allowed_errors == []


# ── state (Р2а-2: no board read on the UI thread) ───────────────────────────

def test_restart_with_a_journal_opens_exploded(ex, monkeypatch):
    """Step 2: a restart with a live journal shows the ENTITY page on the Explode
    tab with the JOURNAL's address (read purely from the journal — the entity
    record may not even be loadable), and the lock is up."""
    hub = ex._dock_hub
    page = hub.explode_page
    _stub_state(monkeypatch, ("has", _JOURNAL))
    hub.refresh_explode_state()
    assert hub.explode_guard.active
    assert page.exploded_from_journal
    assert hub.config_tree_dock.current_right_page_index() == hub._entity_page
    assert hub.entity_dock.tabs.currentWidget() is page
    assert (page._cell_name, page._cluster, page._sheet) == (
        "dac_buf", "DAC_BUF", "Channel_0")


def test_no_journal_for_a_known_board_clears_the_lock(ex, monkeypatch):
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    assert hub.explode_guard.active
    _stub_state(monkeypatch, ("none", None))
    hub.refresh_explode_state()
    assert not hub.explode_guard.active


def test_unknown_board_identity_leaves_the_lock(ex, monkeypatch):
    """Р2а-2: an unreadable board identity must NOT clear the lock — a missing
    journal would otherwise read as "not exploded" and drop the guard while the
    clusters are still shifted aside."""
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    assert hub.explode_guard.active
    _stub_state(monkeypatch, ("unknown", None))
    hub.refresh_explode_state()
    assert hub.explode_guard.active          # LEFT as it was


def test_the_overlay_reconcile_runs_before_the_state_read(ex, monkeypatch):
    """Р2б-1: the explode-state read must NOT run before the overlay reconcile.
    `refresh_explode_state()` starts a long op, so with the old order
    `long_op_active` was already True when the reconcile was about to start and
    EVERY connect/refresh skipped the overlay reconcile. Both must run, the
    reconcile FIRST.

    08.10.2026 (plan_2026_10_08_door_noise_on_connect п.2): the function the UI
    thread dispatches is `DockHub.reconcile_overlay_worker`, which takes the
    adapter INSIDE itself (door §31: the worker before the sign). So "the
    reconcile ran" is now recorded WHERE IT RUNS — the owner's own reconcile
    call — instead of by the name of the function put into the worker queue.
    That is strictly STRONGER than the old anchor: an assertion on the wrapper's
    name would also pass for a wrapper that never calls the reconcile at all.
    All three asserts survive: the reconcile ran, the state read ran, and the
    reconcile ran FIRST."""
    hub = ex._dock_hub
    order = []

    def _sync(connection, widgets, fn, on_success, on_error, *args, **kwargs):
        order.append(fn)
        try:
            result = fn(*args)
        except Exception as e:  # noqa: BLE001 — mirror the worker's routing
            on_error(str(e))
            return None
        on_success(result)
        return None

    # reconcile_overlay imports start_long_op from gui.worker at CALL time.
    monkeypatch.setattr(worker_mod, "start_long_op", _sync)
    monkeypatch.setattr(page_mod, "start_long_op", _sync)
    monkeypatch.setattr(page_mod, "explode_state_worker",
                        lambda connection: ("none", None))
    # The reconcile call itself is the RECORD (see the docstring): `_sync` runs
    # the worker fn immediately, so this append happens from INSIDE the wrapper,
    # while the state read only lands in `order` afterwards, from on_success.
    monkeypatch.setattr(overlay_markers.owner, "reconcile",
                        lambda adapter, **kwargs: order.append("reconcile") or {})

    hub.reconcile_overlay(ex.connection)

    assert "reconcile" in order                             # the reconcile RAN
    assert page_mod.explode_state_worker in order           # and the state READ ran
    assert order.index("reconcile") < order.index(
        page_mod.explode_state_worker)                      # reconcile FIRST


def test_a_failed_automatic_state_read_is_quiet(ex, monkeypatch, caplog):
    """Р2б-2: the connect/refresh state read is housekeeping — a failure leaves
    the lock ALONE (`apply_unknown`) and is a DEBUG line, never the red line
    (and never a dropped lock) every connect on a busy socket would otherwise
    print."""
    hub = ex._dock_hub
    # Step 2: the lock now shows the journal's address on the ENTITY page's
    # Explode tab (no record load, so no "cell not found" line).
    hub.explode_guard.set_from_journal(_JOURNAL)
    assert hub.explode_guard.active

    def _boom(connection):
        raise RuntimeError("busy socket")

    monkeypatch.setattr(page_mod, "explode_state_worker", _boom)
    with caplog.at_level(logging.DEBUG):
        hub.refresh_explode_state()

    assert hub.explode_guard.active                         # the lock was LEFT alone
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("explode state read failed" in r.getMessage()
               for r in caplog.records)


# ── restore / forget ────────────────────────────────────────────────────────

def test_successful_restore_clears_everything(ex, monkeypatch):
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    monkeypatch.setattr(page_mod, "restore_worker", lambda connection: ["ok"])
    hub.explode_page.request_restore()
    assert not hub.explode_guard.active
    assert not hub.explode_page.banner.isVisible()


def test_failed_restore_keeps_the_lock(ex, monkeypatch):
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)

    def _boom(connection):
        raise RuntimeError("restore failed")

    monkeypatch.setattr(page_mod, "restore_worker", _boom)
    hub.explode_page.request_restore()
    assert hub.explode_guard.active           # still exploded


def test_forget_journal_deletes_the_file_and_clears_the_lock(ex, monkeypatch):
    hub = ex._dock_hub
    monkeypatch.setattr(page_mod, "forget_journal_worker", lambda connection: None)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    hub.explode_guard.set_from_journal(_JOURNAL)
    hub.explode_page._forget_journal()
    assert not hub.explode_guard.active


# ── exit ────────────────────────────────────────────────────────────────────

def test_exit_cancel_keeps_the_window(ex, monkeypatch):
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    closed = []
    monkeypatch.setattr(ex, "close", lambda: closed.append(True))
    ev = QCloseEvent()
    ex.closeEvent(ev)
    assert not ev.isAccepted() and closed == []


def test_exit_return_and_exit_closes_after_success(ex, monkeypatch):
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    closed = []
    monkeypatch.setattr(ex, "close", lambda: closed.append(True))

    def _ok_restore(on_success=None, on_error=None):
        on_success()

    hub.explode_page.request_restore = _ok_restore
    ex.closeEvent(QCloseEvent())
    assert closed == [True]


def test_exit_return_and_exit_does_not_close_on_failure(ex, monkeypatch):
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    closed = []
    monkeypatch.setattr(ex, "close", lambda: closed.append(True))

    def _fail_restore(on_success=None, on_error=None):
        if on_error is not None:
            on_error("boom")

    hub.explode_page.request_restore = _fail_restore
    ex.closeEvent(QCloseEvent())
    assert closed == []                       # the program did NOT close


# ── Р2а-3: one instance rule for BOTH doors ─────────────────────────────────

def test_the_door_takes_its_address_from_the_entity_record(ex, monkeypatch):
    """Step 2: the door keys on the ENTITY and the tab takes its address from the
    entity RECORD — not from a resolver and not from the working-instance store.
    Rоняет мутацию «адрес Explode не от сущности»."""
    _stub_plan(monkeypatch)
    hub = ex._dock_hub
    _open_via_door(hub)
    page = hub.explode_page
    assert (page._cell_name, page._cluster, page._sheet) == (
        "dac_buf", "DAC_BUF", "Channel_0")
    assert page._file_path == hub.entity_dock._entity_file


# ── Р2а-2 DOOR: no UI-thread board read on any path ─────────────────────────

def test_no_ui_thread_board_read_on_any_path(real_main_window, monkeypatch,
                                             tmp_path):
    """On a REAL BoardConnection with the UI-thread predicate ARMED in `raise`
    mode, opening the tab, recalculating, exploding, clicking a row, refreshing
    the state, restoring and quitting with "Put back and quit" read
    `connection.board` on NO UI-thread path. Every worker is stubbed, so the
    guard sees ONLY the UI-thread code of the page + DockHub."""
    import threading
    from gui import connection as conn_mod
    from gui.connection import BoardConnection

    w = real_main_window
    conn = BoardConnection()
    conn.board = SimpleNamespace(adapter=object())   # setter; getter is guarded
    w.connection = conn
    root = _write_root(tmp_path / "config.sexp")
    w._dock_hub.root_metadata_dock._path = root
    w._dock_hub.entity_dock.set_root_path(root)
    monkeypatch.setattr(w._dock_hub, "refresh_snapshot_and_push",
                        lambda *a, **k: None)

    _sync_start_long_op(monkeypatch)
    monkeypatch.setattr(page_mod, "plan_worker",
                        lambda *a, **k: _plan((_piece("u1"),)))
    monkeypatch.setattr(page_mod, "explode_worker", lambda *a, **k: [])
    monkeypatch.setattr(page_mod, "restore_worker", lambda *a, **k: ["ok"])
    monkeypatch.setattr(page_mod, "forget_journal_worker", lambda *a, **k: None)
    monkeypatch.setattr(page_mod, "select_worker", lambda *a, **k: None)
    monkeypatch.setattr(page_mod, "explode_state_worker",
                        lambda *a, **k: ("has", _JOURNAL))

    main = threading.current_thread()
    monkeypatch.setattr(conn_mod, "ui_thread_predicate",
                        lambda: threading.current_thread() is main)
    monkeypatch.setattr(conn_mod, "ui_thread_read_refusal", conn_mod.UI_READ_RAISE)

    hub = w._dock_hub
    _open_via_door(hub)                     # open + recalc
    page = hub.explode_page
    page._explode()
    page._on_row_clicked(page.tree.topLevelItem(0).child(0), 0)
    hub.refresh_explode_state()
    assert hub.explode_guard.active           # the state stub says "has"
    page.request_restore()
    assert not hub.explode_guard.active       # a successful restore clears it
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    w.closeEvent(QCloseEvent())               # quit -> request_restore (inactive)
    hub.explode_guard.detach()
    worker_mod.set_long_op_gate(None)


def test_no_ui_thread_board_read_when_quitting_exploded(real_main_window,
                                                        monkeypatch, tmp_path):
    """Р2б-3: the exit window ITSELF — `closeEvent` while exploded, then "Put
    back and quit" — must read `connection.board` on NO UI-thread path. The
    door cell above quits only AFTER the lock is cleared, so this is the branch
    it did not cover (Р2б-3)."""
    import threading
    from gui import connection as conn_mod
    from gui.connection import BoardConnection

    w = real_main_window
    conn = BoardConnection()
    conn.board = SimpleNamespace(adapter=object())   # setter; getter is guarded
    w.connection = conn
    root = tmp_path / "config.sexp"
    root.write_text("", encoding="utf-8")
    w._dock_hub.root_metadata_dock._path = root
    monkeypatch.setattr(w._dock_hub, "refresh_snapshot_and_push",
                        lambda *a, **k: None)

    _sync_start_long_op(monkeypatch)
    monkeypatch.setattr(page_mod, "restore_worker", lambda connection: ["ok"])
    monkeypatch.setattr(page_mod, "explode_state_worker",
                        lambda *a, **k: ("has", _JOURNAL))

    main = threading.current_thread()
    monkeypatch.setattr(conn_mod, "ui_thread_predicate",
                        lambda: threading.current_thread() is main)
    monkeypatch.setattr(conn_mod, "ui_thread_read_refusal", conn_mod.UI_READ_RAISE)

    hub = w._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    assert hub.explode_guard.active
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    closed = []
    monkeypatch.setattr(w, "close", lambda: closed.append(True))

    w.closeEvent(QCloseEvent())               # exploded -> question -> restore -> close
    assert closed == [True]
    assert not hub.explode_guard.active       # the successful restore cleared the lock
    hub.explode_guard.detach()
    worker_mod.set_long_op_gate(None)


def test_the_transfer_runs_on_the_ui_thread_only_when_the_plan_applies(
        ex, monkeypatch):
    """Р3а-1: the ownership transfer is applied on the UI THREAD, together with the
    plan — and NOT AT ALL when the read refuses (a refusal must leave the record
    and the registries untouched)."""
    import threading
    from gui.docks import cell_editor as ce_mod
    from kicadstamp.explode_transfer import NetTraceTransfer

    dock = ex._dock_hub.cells_dock
    dock._root_path = ex._dock_hub.root_metadata_dock.root_path
    # The fixture's config is an empty file; the transfer path re-loads it, so the
    # loader is stubbed (a file read, not the property under test).
    monkeypatch.setattr("kicadstamp.config.load_config",
                        lambda path: (SimpleNamespace(net_traces=[]),
                                      SimpleNamespace(sheet_names={})))
    # Р3а-2: the record's file is found across the include graph and prechecked;
    # both are stubbed here (the property is the ORDER and the THREAD).
    monkeypatch.setattr("gui.docks.rename.find_list_entry_file",
                        lambda root, section, entry: dock._root_path)
    monkeypatch.setattr("kicadstamp.explode_transfer.precheck_transfers",
                        lambda cfg, transfers, files: [])
    calls = []
    monkeypatch.setattr(ce_mod, "apply_transfers",
                        lambda path, cfg, transfers, entry_files=None: calls.append(
                            (threading.current_thread().name, tuple(transfers)))
                        or [])
    monkeypatch.setattr(dock, "_apply_refresh_plan", lambda plan: (0, 1, 0))
    monkeypatch.setattr(dock, "_report_layer_read", lambda report: None)
    monkeypatch.setattr(dock, "_show_message", lambda *a, **k: None)

    plan = SimpleNamespace(component_updates=[{"x": 1}], via_updates=[],
                           track_updates=[], new_via_records=[],
                           new_track_records=[], removed_via_records=[],
                           removed_track_records=[])
    tr = NetTraceTransfer("rec", "track", 0)
    main = threading.current_thread().name

    dock._finish_refresh_geometry({"plan": plan, "transfers": (tr,)})
    assert calls == [(main, (tr,))]          # applied, on the UI thread

    # A refused read returns BEFORE the apply — nothing may be transferred.
    dock._finish_refresh_geometry({"plan": plan, "transfers": (tr,),
                                   "selection_refusal": "boom"})
    assert len(calls) == 1


def test_a_refused_transfer_refuses_the_whole_read(ex, monkeypatch):
    """Р3а-2: when a transferred record's file cannot be found in the graph, the
    WHOLE read is refused — the plan is NOT applied and nothing is written."""
    from kicadstamp.explode_transfer import NetTraceTransfer

    dock = ex._dock_hub.cells_dock
    dock._root_path = ex._dock_hub.root_metadata_dock.root_path
    monkeypatch.setattr("kicadstamp.config.load_config",
                        lambda path: (SimpleNamespace(net_traces=[]),
                                      SimpleNamespace(sheet_names={})))
    monkeypatch.setattr("gui.docks.rename.find_list_entry_file",
                        lambda root, section, entry: None)     # nowhere in graph
    applied = []
    monkeypatch.setattr(dock, "_apply_refresh_plan",
                        lambda plan: applied.append(plan) or (0, 0, 0))
    monkeypatch.setattr(dock, "_report_layer_read", lambda report: None)
    errors = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style=None: errors.append(text))

    plan = SimpleNamespace(component_updates=[{"x": 1}], via_updates=[],
                           track_updates=[], new_via_records=[],
                           new_track_records=[], removed_via_records=[],
                           removed_track_records=[])
    dock._finish_refresh_geometry(
        {"plan": plan, "transfers": (NetTraceTransfer("rec", "track", 0),)})

    assert applied == []                       # the plan was NOT applied
    assert any("nothing was changed" in line for line in errors)


# ── Р3а-3: the transfer runs ONLY for the journal's instance ────────────────

def test_the_reread_gate_carries_the_journal_only_for_its_instance(
        ex, monkeypatch):
    """Р3а-3: the door asks `ExplodeGuard.transfer_enabled` — the journal's own
    instance carries its address into the read; any OTHER instance carries NONE,
    so the read subtracts the inter-cluster copper with a yellow line."""
    _stub_plan(monkeypatch)
    hub = ex._dock_hub
    page = hub.explode_page
    monkeypatch.setattr(page, "_rebuild", lambda: None)
    monkeypatch.setattr(page, "_recalculate", lambda: None)
    got = {}
    monkeypatch.setattr(hub.cells_dock, "refresh_from_selection_requested",
                        lambda *a, **kw: got.update(args=a, kwargs=kw))
    hub.explode_guard.set_from_journal(_JOURNAL)

    page.set_context("dac_buf", "DAC_BUF", "Channel_0", "/tmp/x.sexp")
    hub.reread_cell_for_explode("dac_buf", "/tmp/x.sexp")
    assert got["args"] == ("dac_buf", "/tmp/x.sexp")
    assert got["kwargs"] == {"explode_transfer": True,
                             "explode_journal": _JOURNAL}

    page.set_context("dac_buf", "PIF_AVDD", "Channel_0", "/tmp/x.sexp")
    hub.reread_cell_for_explode("dac_buf", "/tmp/x.sexp")
    assert got["kwargs"] == {"explode_transfer": True, "explode_journal": None}


def test_transfer_enabled_is_the_shared_address_rule(ex):
    """Р3а-3: `transfer_enabled` is the ONE rule (shared with the worker): the
    journal's cell AND its (cluster, sheet), else False; no journal -> False."""
    guard = ex._dock_hub.explode_guard
    assert not guard.transfer_enabled("dac_buf", "DAC_BUF", "Channel_0")
    guard.set_from_journal(_JOURNAL)
    assert guard.transfer_enabled("dac_buf", "DAC_BUF", "Channel_0")
    assert not guard.transfer_enabled("dac_buf", "PIF_AVDD", "Channel_0")
    assert not guard.transfer_enabled("other", "DAC_BUF", "Channel_0")
    assert not guard.transfer_enabled("dac_buf", "DAC_BUF", "Channel_1")
    guard.set_from_journal(None)
    assert not guard.transfer_enabled("dac_buf", "DAC_BUF", "Channel_0")


# ── Р2в: the permanent tab and its two lists ────────────────────────────────

def test_the_explode_tab_is_permanent(ex):
    """Step 2: "Explode" is a tab of the ENTITY page — it exists without any menu
    door, and the window's tab strip is still its three tabs."""
    hub = ex._dock_hub
    page = hub.entity_dock
    assert page.tabs.indexOf(hub.explode_page) != -1
    assert page.tabs.tabText(page.tabs.indexOf(hub.explode_page)) == "Explode"
    assert hub.left_tabs.count() == 3


def test_the_tab_takes_its_cell_from_the_entity_page(ex, monkeypatch):
    """Step 2: the tab has NO cell list — the ENTITY page tells it which cell the
    loaded entity stands on (load_entity -> _sync_explode_context)."""
    _stub_plan(monkeypatch, plan=_plan((_piece("u1"),)))
    hub = ex._dock_hub
    hub._load_entity_page(_ENTITY)
    assert hub.explode_page._cell_name == "dac_buf"


def test_setting_the_context_replans(ex, monkeypatch):
    """Р3а-0: the page hands the cell and the instance over (`set_context`); the
    tab re-plans on the SAME worker path as "Recalculate"."""
    _stub_plan(monkeypatch, plan=_plan((_piece("u1"),)))
    hub = ex._dock_hub
    page = hub.explode_page
    page.set_root_path(hub.root_metadata_dock.root_path)
    page.set_context("dac_buf", "DAC_BUF", "Channel_0")
    assert (page._cell_name, page._cluster, page._sheet) == (
        "dac_buf", "DAC_BUF", "Channel_0")
    assert page._plan is not None


def test_reread_uses_the_page_cell_and_file(ex, monkeypatch):
    """Р3а-0a: the tab's "Re-read" door hands over the PAGE's cell and the cell's
    OWNING FILE — the live AttributeError came from the tab keeping `_file_path`
    itself (it must come from the page)."""
    hub = ex._dock_hub
    page = hub.explode_page
    got = {}
    monkeypatch.setattr(hub, "reread_cell_for_explode",
                        lambda name, fp=None: got.update(name=name, file=fp))
    hub.explode_guard.set_from_journal(_JOURNAL)
    page.set_context("dac_buf", "DAC_BUF", "Channel_0", "/tmp/x.sexp")
    page._reread()
    assert got == {"name": "dac_buf", "file": "/tmp/x.sexp"}


def test_no_ui_thread_board_read_when_the_page_context_changes(
        real_main_window, monkeypatch, tmp_path):
    """Р3а-0 DOOR: the PAGE handing the cell / instance over (`set_context`) reads
    `connection.board` on NO UI-thread path (only `load_config`, a FILE read)."""
    import threading
    from gui import connection as conn_mod
    from gui.connection import BoardConnection

    w = real_main_window
    conn = BoardConnection()
    conn.board = SimpleNamespace(adapter=object())
    w.connection = conn
    root = tmp_path / "config.sexp"
    root.write_text("", encoding="utf-8")
    w._dock_hub.root_metadata_dock._path = root
    monkeypatch.setattr(w._dock_hub, "refresh_snapshot_and_push",
                        lambda *a, **k: None)
    _sync_start_long_op(monkeypatch)
    monkeypatch.setattr(page_mod, "plan_worker", lambda *a, **k: _plan())

    cfg = SimpleNamespace(cells={"dac_buf": object(), "pif": object()}, entities=[
        SimpleNamespace(cell="dac_buf", cluster="A", sheet=None),
        SimpleNamespace(cell="dac_buf", cluster="B", sheet="S")],
        clone_placements=[])
    _patch_cfg(monkeypatch, cfg)

    main = threading.current_thread()
    monkeypatch.setattr(conn_mod, "ui_thread_predicate",
                        lambda: threading.current_thread() is main)
    monkeypatch.setattr(conn_mod, "ui_thread_read_refusal", conn_mod.UI_READ_RAISE)

    hub = w._dock_hub
    page = hub.explode_page
    page.set_root_path(root)
    hub.entity_dock._sync_explode_context()   # the page hands the address over
    page.set_context("dac_buf", "DAC_BUF", "Channel_0")
    hub.explode_guard.detach()
    worker_mod.set_long_op_gate(None)
