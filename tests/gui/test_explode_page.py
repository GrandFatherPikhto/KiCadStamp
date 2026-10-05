# tests/gui/test_explode_page.py
"""Cells for the "Разнос" tab + lock (Р2, plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md`` §Р2-7).

Real MainWindow (offscreen). The plan/explode/restore WORKERS are stubbed at the
module and the page's ``start_long_op`` is made SYNCHRONOUS (the real worker
thread + the gate are covered by tests/gui/test_explode_worker_gate.py) — so
these cells measure the tab's logic and the LOCK deterministically.
"""
from types import SimpleNamespace

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent, QColor
from PyQt6.QtWidgets import QMessageBox

from kicadstamp import explode_journal as journal_mod
from kicadstamp.domain.geometry import Box2, Vector2
from kicadstamp.explode import ExplodePlan, NetTracePiece

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


@pytest.fixture
def ex(real_main_window, monkeypatch, tmp_path):
    """A real MainWindow on a fake board, a throwaway config root, the module
    worker stubs and a SYNCHRONOUS page start_long_op; the global worker gate is
    taken back down on exit."""
    w = real_main_window
    w.connection.board = SimpleNamespace(adapter=object())
    root = tmp_path / "config.sexp"
    root.write_text("", encoding="utf-8")
    w._dock_hub.root_metadata_dock._path = root
    # A page switch fires the snapshot-freshness rebuild — a board op of its own;
    # these cells measure the tab, not snapshot freshness.
    monkeypatch.setattr(w._dock_hub, "refresh_snapshot_and_push",
                        lambda *a, **k: None)

    def _sync(connection, widgets, fn, on_success, on_error, *args, **kwargs):
        try:
            result = fn(*args)
        except Exception as e:  # noqa: BLE001 — mirror the worker's routing
            on_error(str(e))
            return None
        on_success(result)
        return None

    monkeypatch.setattr(page_mod, "start_long_op", _sync)
    yield w
    w._dock_hub.explode_guard.detach()
    worker_mod.set_long_op_gate(None)


def _stub_plan(monkeypatch, captured=None, plan=None):
    def _fake(adapter, config_path, cell, cluster, sheet, margin, gap, overrides):
        if captured is not None:
            captured.update(overrides)
        return plan if plan is not None else _plan()
    monkeypatch.setattr(page_mod, "plan_worker", _fake)


def _journal(monkeypatch, value):
    monkeypatch.setattr(journal_mod, "journal_status", lambda adapter, *a, **k: value)


# ── doors ───────────────────────────────────────────────────────────────────

def test_entity_door_opens_the_page_with_the_explicit_instance(ex, monkeypatch):
    _stub_plan(monkeypatch)
    hub = ex._dock_hub
    hub._open_explode("dac_buf", None, "DAC_BUF", "Channel_0")
    page = hub.explode_page
    assert (page._cell_name, page._cluster, page._sheet) == (
        "dac_buf", "DAC_BUF", "Channel_0")
    assert hub.config_tree_dock.current_right_page_index() == hub._explode_page


def test_cell_door_uses_the_remembered_instance(ex, monkeypatch):
    """The cell door (no explicit cluster) resolves EXACTLY like "Select cell":
    the remembered (Cluster, Sheet) is the shared source."""
    from gui.cell_edit_context import remember_cell_edit_context
    _stub_plan(monkeypatch)
    hub = ex._dock_hub
    remember_cell_edit_context(hub.root_metadata_dock.root_path, "dac_buf",
                               "DAC_BUF", "Channel_0")
    hub.cells_dock.explode_requested.emit("dac_buf", None, None, None)
    page = hub.explode_page
    assert (page._cluster, page._sheet) == ("DAC_BUF", "Channel_0")


def test_cell_dock_button_emits_the_door_signal(ex):
    """The CellDock "Explode…" button names only the cell (instance resolved by
    DockHub, like "Select cell")."""
    seen = []
    ex._dock_hub.cells_dock.explode_requested.connect(lambda *a: seen.append(a))
    dock = ex._dock_hub.cells_dock
    dock.name_edit.setText("dac_buf")
    dock._on_explode()
    assert seen == [("dac_buf", None, None, None)]


# ── ticks ───────────────────────────────────────────────────────────────────

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
    monkeypatch.setattr(page_mod, "explode_worker", lambda adapter, plan: [])
    hub = ex._dock_hub
    hub._open_explode("dac_buf", None, "DAC_BUF", "Channel_0")
    row = hub.explode_page.tree.topLevelItem(0).child(0)
    assert row.flags() & Qt.ItemFlag.ItemIsUserCheckable
    _journal(monkeypatch, _JOURNAL)          # the board is now exploded
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


# ── lock ────────────────────────────────────────────────────────────────────

def test_exploded_pins_the_right_page_and_disables_left_tabs(ex):
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    assert not hub.left_tabs.isEnabled()
    # A switch away is rolled back to the "Разнос" page.
    hub.config_tree_dock.set_current_page(1)
    assert hub.config_tree_dock.current_right_page_index() == hub._explode_page


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


def test_successful_restore_clears_everything(ex, monkeypatch):
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    _journal(monkeypatch, None)              # the journal is gone after restore
    monkeypatch.setattr(page_mod, "restore_worker", lambda adapter: ["ok"])
    hub.explode_page.request_restore()
    assert not hub.explode_guard.active
    assert hub.left_tabs.isEnabled()
    assert not hub.explode_page.banner.isVisible()


def test_failed_restore_keeps_the_lock(ex, monkeypatch):
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)

    def _boom(adapter):
        raise RuntimeError("restore failed")

    monkeypatch.setattr(page_mod, "restore_worker", _boom)
    hub.explode_page.request_restore()
    assert hub.explode_guard.active           # still exploded
    assert not hub.left_tabs.isEnabled()


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


# ── post-crash / journal ────────────────────────────────────────────────────

def test_restart_with_a_journal_opens_exploded(ex, monkeypatch):
    hub = ex._dock_hub
    _journal(monkeypatch, _JOURNAL)
    hub.refresh_explode_state()
    assert hub.explode_guard.active
    assert hub.explode_page.exploded_from_journal
    assert not hub.left_tabs.isEnabled()
    assert hub.config_tree_dock.current_right_page_index() == hub._explode_page


def test_forget_journal_deletes_the_file_and_clears_the_lock(ex, monkeypatch, tmp_path):
    hub = ex._dock_hub
    jpath = tmp_path / "j.json"
    jpath.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(journal_mod, "journal_path", lambda adapter, *a, **k: jpath)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    hub.explode_guard.set_from_journal(_JOURNAL)
    _journal(monkeypatch, None)              # after deletion the journal is gone
    hub.explode_page._forget_journal()
    assert not jpath.exists()
    assert not hub.explode_guard.active


def test_deleting_the_journal_by_hand_clears_the_lock(ex, monkeypatch):
    """active comes from the JOURNAL, never memory: once the file is gone, the
    next check clears the lock."""
    hub = ex._dock_hub
    hub.explode_guard.set_from_journal(_JOURNAL)
    assert hub.explode_guard.active
    _journal(monkeypatch, None)
    hub.refresh_explode_state()
    assert not hub.explode_guard.active
    assert hub.left_tabs.isEnabled()
