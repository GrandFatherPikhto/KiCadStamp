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


@pytest.fixture
def ex(real_main_window, monkeypatch, tmp_path):
    """A real MainWindow on a fake board, a throwaway config root, the worker
    stubs and a SYNCHRONOUS page start_long_op; the global gate is taken down."""
    w = real_main_window
    w.connection.board = SimpleNamespace(adapter=object())
    root = tmp_path / "config.sexp"
    root.write_text("", encoding="utf-8")
    w._dock_hub.root_metadata_dock._path = root
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


# ── doors ───────────────────────────────────────────────────────────────────

def test_entity_door_opens_the_explode_tab_with_the_explicit_instance(ex, monkeypatch):
    """Р2в: the door switches to the PERMANENT "Explode" tab (not a Config right
    page) and shows the cell and the explicit instance in the two lists."""
    _stub_plan(monkeypatch)
    hub = ex._dock_hub
    hub._open_explode("dac_buf", None, "DAC_BUF", "Channel_0")
    page = hub.explode_page
    assert (page._cell_name, page._cluster, page._sheet) == (
        "dac_buf", "DAC_BUF", "Channel_0")
    assert hub.left_tabs.currentWidget() is page
    assert page.cell_combo.currentText() == "dac_buf"
    assert page.instance_combo.currentData() == ("DAC_BUF", "Channel_0")


def test_cell_dock_button_emits_the_door_signal(ex):
    seen = []
    ex._dock_hub.cells_dock.explode_requested.connect(lambda *a: seen.append(a))
    dock = ex._dock_hub.cells_dock
    dock.name_edit.setText("dac_buf")
    dock._on_explode()
    assert seen == [("dac_buf", None, None, None)]


# ── ticks / table ───────────────────────────────────────────────────────────

def test_a_single_untick_reaches_the_plan_as_an_override(ex, monkeypatch):
    captured = {}
    _stub_plan(monkeypatch, captured=captured, plan=_plan((_piece("u1"),)))
    hub = ex._dock_hub
    hub._open_explode("dac_buf", None, "DAC_BUF", "Channel_0")
    assert hub.explode_page._plan is not None
    row = hub.explode_page.tree.topLevelItem(0).child(0)
    row.setCheckState(0, Qt.CheckState.Unchecked)
    assert captured.get("u1") is False


def test_ticks_are_not_checkable_after_explode(ex, monkeypatch):
    _stub_plan(monkeypatch, plan=_plan((_piece("u1"),)))
    monkeypatch.setattr(page_mod, "explode_worker", lambda connection, plan: [])
    _stub_state(monkeypatch, ("has", _JOURNAL))     # the journal now exists
    hub = ex._dock_hub
    hub._open_explode("dac_buf", None, "DAC_BUF", "Channel_0")
    row = hub.explode_page.tree.topLevelItem(0).child(0)
    assert row.flags() & Qt.ItemFlag.ItemIsUserCheckable
    hub.explode_page._explode()
    assert hub.explode_guard.active
    row = hub.explode_page.tree.topLevelItem(0).child(0)
    assert not (row.flags() & Qt.ItemFlag.ItemIsUserCheckable)


def test_a_tee_row_is_highlighted_yellow(ex, monkeypatch):
    _stub_plan(monkeypatch, plan=_plan((_piece("u1", touches="tee"),)))
    hub = ex._dock_hub
    hub._open_explode("dac_buf", None, "DAC_BUF", "Channel_0")
    row = hub.explode_page.tree.topLevelItem(0).child(0)
    assert row.background(0).color() == QColor(255, 250, 205)


# ── lock (Р2а-1: the page must stay USABLE) ─────────────────────────────────

def test_exploded_disables_the_tab_strip_and_shows_the_explode_tab(ex):
    """Р2в: while exploded the TAB STRIP is disabled and the current tab is
    pinned to "Explode"; the page and its buttons stay usable (disabling the
    CONTAINER would disable the page too — Р2а-1, measured offscreen). The Config
    tree is NO LONGER disabled: Config is unreachable through a disabled strip."""
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    assert not hub.left_tabs.tabBar().isEnabled()
    assert hub.left_tabs.currentWidget() is hub.explode_page
    assert hub.explode_page.isEnabled()
    assert hub.explode_page.restore_button.isEnabled()
    assert not hub.explode_page.cell_combo.isEnabled()      # lists frozen
    assert hub.config_tree_dock.tree.isEnabled()            # not disabled any more


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
    """Р2в: a restart with a live journal shows the "Explode" tab (its lists hold
    the journal's own cell/instance) and the lock is up."""
    hub = ex._dock_hub
    _stub_state(monkeypatch, ("has", _JOURNAL))
    hub.refresh_explode_state()
    assert hub.explode_guard.active
    assert hub.explode_page.exploded_from_journal
    assert hub.left_tabs.currentWidget() is hub.explode_page
    assert hub.explode_page.cell_combo.currentText() == "dac_buf"
    assert hub.explode_page.instance_combo.currentData() == ("DAC_BUF", "Channel_0")


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
    reconcile FIRST."""
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

    hub.reconcile_overlay(ex.connection)

    assert overlay_markers.owner.reconcile in order         # the reconcile RAN
    assert page_mod.explode_state_worker in order           # and the state READ ran
    assert order.index(overlay_markers.owner.reconcile) < order.index(
        page_mod.explode_state_worker)                      # reconcile FIRST


def test_a_failed_automatic_state_read_is_quiet(ex, monkeypatch, caplog):
    """Р2б-2: the connect/refresh state read is housekeeping — a failure leaves
    the lock ALONE (`apply_unknown`) and is a DEBUG line, never the red line
    (and never a dropped lock) every connect on a busy socket would otherwise
    print."""
    hub = ex._dock_hub
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

def test_both_doors_use_the_same_resolver(ex, monkeypatch, tmp_path):
    from gui.select_cell import resolve_action_instance
    from gui.cell_edit_context import remember_cell_edit_context
    _stub_plan(monkeypatch)
    hub = ex._dock_hub
    root = hub.root_metadata_dock.root_path
    cfg = SimpleNamespace(entities=[SimpleNamespace(cell="dac_buf", cluster="A",
                                                    sheet=None),
                                    SimpleNamespace(cell="dac_buf", cluster="B",
                                                    sheet="S")],
                          clone_placements=[])
    # No memory + two records -> "choose" with BOTH candidates.
    choice = resolve_action_instance(cfg, root, "dac_buf")
    assert choice.kind == "choose"
    assert {c for c, _s in choice.candidates} == {"A", "B"}
    # The remembered context wins for BOTH doors.
    remember_cell_edit_context(root, "dac_buf", "B", "S")
    assert resolve_action_instance(cfg, root, "dac_buf").kind == "remembered"
    hub._open_explode("dac_buf")              # no explicit cluster
    assert (hub.explode_page._cluster, hub.explode_page._sheet) == ("B", "S")


def test_multiple_records_without_memory_open_with_the_list(ex, monkeypatch):
    """Р2в: a cell placed by SEVERAL records opens the tab with NO instance
    picked — the INSTANCE list IS the choice (the submenu is gone)."""
    _stub_plan(monkeypatch)
    hub = ex._dock_hub
    cfg = SimpleNamespace(cells={}, entities=[
        SimpleNamespace(cell="dac_buf", cluster="A", sheet=None),
        SimpleNamespace(cell="dac_buf", cluster="B", sheet="S")],
        clone_placements=[])
    _patch_cfg(monkeypatch, cfg)
    hub._open_explode("dac_buf")
    page = hub.explode_page
    assert hub.left_tabs.currentWidget() is page
    items = [page.instance_combo.itemData(i)
             for i in range(page.instance_combo.count())]
    assert set(items) == {("A", None), ("B", "S")}
    assert page.instance_combo.currentIndex() == -1        # no default
    assert (page._cluster, page._sheet) == (None, None)
    assert page._plan is None                              # nothing to plan yet


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
    root = tmp_path / "config.sexp"
    root.write_text("", encoding="utf-8")
    w._dock_hub.root_metadata_dock._path = root
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
    hub._open_explode("dac_buf", None, "DAC_BUF", "Channel_0")   # open + recalc
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


# ── Р2в: the permanent tab and its two lists ────────────────────────────────

def test_the_explode_tab_is_permanent(ex):
    """Р2в: "Explode" is a tab of the central group from construction — it exists
    without any menu door."""
    hub = ex._dock_hub
    assert hub.left_tabs.indexOf(hub.explode_page) != -1


def test_a_root_change_fills_the_cell_list(ex, monkeypatch):
    """Р2в: the CELL list is the file's own cells, filled from the same
    root_changed source every dock follows (no door needed)."""
    cfg = SimpleNamespace(cells={"dac_buf": object(), "pif": object()},
                          entities=[], clone_placements=[])
    _patch_cfg(monkeypatch, cfg)
    hub = ex._dock_hub
    hub._sync_root_to_docks(hub.root_metadata_dock.root_path)
    page = hub.explode_page
    assert [page.cell_combo.itemText(i)
            for i in range(page.cell_combo.count())] == ["dac_buf", "pif"]


def test_picking_an_instance_in_the_list_replans(ex, monkeypatch):
    """Р2в: picking an instance in the tab's own list runs the SAME plan worker
    as the door / "Recalculate"."""
    _stub_plan(monkeypatch, plan=_plan((_piece("u1"),)))
    hub = ex._dock_hub
    cfg = SimpleNamespace(cells={"dac_buf": object()}, entities=[
        SimpleNamespace(cell="dac_buf", cluster="A", sheet=None),
        SimpleNamespace(cell="dac_buf", cluster="B", sheet="S")],
        clone_placements=[])
    _patch_cfg(monkeypatch, cfg)
    page = hub.explode_page
    page.set_root_path(hub.root_metadata_dock.root_path)
    page.cell_combo.setCurrentText("dac_buf")
    assert page._plan is None                       # several: no default
    page.instance_combo.setCurrentIndex(1)          # pick "B on S"
    assert (page._cluster, page._sheet) == ("B", "S")
    assert page._plan is not None


def test_no_ui_thread_board_read_when_changing_the_lists(real_main_window,
                                                         monkeypatch, tmp_path):
    """Р2в DOOR: picking a CELL / INSTANCE in the two lists reads
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
    page.cell_combo.setCurrentText("dac_buf")
    page.instance_combo.setCurrentIndex(1)
    hub.explode_guard.detach()
    worker_mod.set_long_op_gate(None)
