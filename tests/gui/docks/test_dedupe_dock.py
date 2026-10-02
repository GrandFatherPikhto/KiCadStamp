# tests/gui/docks/test_dedupe_dock.py
"""Cells for gui/docks/dedupe.py — the "Дубли меди" panel (plan_2026_10_01_
dedupe_into_kicadstamp §2.3).

What each cell is for:
  * the panel is a real dock TABIFIED with the Log and its exact
    toggleViewAction is in the View menu — that is the opening/closing
    mechanism the plan asks for, not a hand-rolled switch;
  * "Очистить" is disabled until BOTH a project root is open (the journal needs
    its actions.log) AND the list is non-empty, and the handler re-checks it, so
    a programmatic call cannot delete unjournaled copper;
  * "Копировать" puts the CORE report text on the clipboard byte for byte;
  * the board is read on the WORKER: the door is armed with refusal="raise"
    (gui.connection) and the scan still succeeds, which is only possible if
    nothing reads connection.board on the UI thread;
  * "Очистить" deletes the extras, journals each to kicadstamp.actions, logs the
    one summary and automatically re-scans.

The board stand-in is the shared tests/fakes/adapter.FakeAdapter (its
remove_by_id really removes and records), driven through the same
start_long_op path production uses.
"""
import logging
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from gui import connection as connection_mod
from gui.connection import UI_READ_RAISE, BoardConnection
from gui.docks.dedupe import DedupeDock
from gui.worker import is_ui_thread
from kicadstamp.board_dedupe import (ACTION_LOGGER_NAME, apply_summary,
                                     find_track_duplicate_groups,
                                     find_via_duplicate_groups, format_report)
from kicadstamp.domain.board import Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.utils.units import MM
from tests.fakes.adapter import FakeAdapter
from tests.gui.conftest import _pump


def _mm(value: float) -> int:
    return int(round(value * MM))


def _via(uuid: str, net, x_mm: float, y_mm: float,
         drill: float = 0.3, diameter: float = 0.6) -> Via:
    return Via(uuid=uuid, position=Vector2(_mm(x_mm), _mm(y_mm)),
               net_name=net, drill_mm=drill, diameter_mm=diameter)


def _track(uuid: str, net, layer: BoardLayer, start_mm, end_mm) -> Track:
    return Track(uuid=uuid,
                 start=Vector2(_mm(start_mm[0]), _mm(start_mm[1])),
                 end=Vector2(_mm(end_mm[0]), _mm(end_mm[1])),
                 net_name=net, width_mm=0.25, layer=layer)


def _board(duplicates: bool = True) -> FakeAdapter:
    """Two duplicate pairs (a via pair whose drill DIFFERS, a track pair) plus
    one lone via and one lone track that must never be touched."""
    vias = [_via("v1", "GND", 1.0, 2.0, drill=0.3, diameter=0.6),
            _via("v2", "GND", 1.0, 2.0, drill=0.4, diameter=0.8),
            _via("v3", "VCC", 9.0, 9.0)]
    tracks = [_track("t1", "SIG", BoardLayer.BL_F_Cu, (0.0, 0.0), (1.0, 1.0)),
              _track("t2", "SIG", BoardLayer.BL_F_Cu, (0.0, 0.0), (1.0, 1.0)),
              _track("t4", "SIG", BoardLayer.BL_F_Cu, (5.0, 5.0), (6.0, 6.0))]
    if not duplicates:
        vias = vias[2:]
        tracks = tracks[2:]
    return FakeAdapter(vias=vias, tracks=tracks)


def _dock(main_window, adapter) -> DedupeDock:
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    return DedupeDock(main_window, connection=main_window.connection)


def _scan(dock, qapp) -> None:
    """Click Find and pump until the worker has released the socket."""
    dock._on_find()
    _pump(qapp, lambda: not dock._connection.long_op_active)


def _cell(dock, row: int, column: int) -> str:
    return dock.table.item(row, column).text()


# ── the dock is a real, tabified dock with a View-menu action ────────────────

def test_panel_is_tabified_with_the_log_and_has_its_view_menu_action(
        real_main_window):
    """К3 (§2.3) — "вкладка в паре с логом", "включается из меню Вид, как любая
    панель". The entry is Qt's own toggleViewAction for THIS dock, sitting in the
    View menu — not a separate switch."""
    hub = real_main_window._dock_hub
    dock = hub.dedupe_dock

    assert dock in hub.docks
    assert dock in real_main_window.tabifiedDockWidgets(hub.log_dock)

    view_menu = next(action.menu()
                     for action in real_main_window.menuBar().actions()
                     if action.text().replace("&", "") == "View")
    assert dock.toggleViewAction() in view_menu.actions()


# ── "Очистить" gating ────────────────────────────────────────────────────────

def test_clear_needs_both_a_root_and_a_found_list(main_window, qapp, tmp_path):
    """К3 (§2.3) — the button is inactive while the list is empty OR no root
    config is open: the journal is the root config's actions.log, so a removal
    with nowhere to journal it must not be offered."""
    dock = _dock(main_window, _board())
    assert dock.clear_button.isEnabled() is False     # nothing found, no root

    dock.set_root_path(tmp_path / "root.sexp")
    assert dock.clear_button.isEnabled() is False     # root, still nothing found

    _scan(dock, qapp)
    assert dock.table.rowCount() == 2
    assert dock.clear_button.isEnabled() is True

    # The other half: rows exist but no root is open -> still disabled.
    other = _dock(main_window, _board())
    _scan(other, qapp)
    assert other.table.rowCount() == 2
    assert other.clear_button.isEnabled() is False


def test_clear_does_nothing_when_programmatically_called_without_a_root(
        main_window, qapp):
    """К3 — the disabled button is not the only guard: the handler refuses too,
    so a programmatic call cannot delete copper that cannot be journaled.

    The assertion pins that NO NEW OPERATION was started (`_active_op` is the
    controller of the scan that already ran). A bare "adapter.removed == []"
    right after the call is BLIND here — measured: with the guard removed the
    cell still passed, because the worker had not run yet. Hence the identity
    check, which is the half that actually fails when the guard goes."""
    adapter = _board()
    dock = _dock(main_window, adapter)
    _scan(dock, qapp)
    assert dock.table.rowCount() > 0

    before = dock._active_op
    dock._on_clear()          # no root_path

    assert dock._active_op is before, \
        "Clear started an operation while it had nowhere to journal the removal"
    qapp.processEvents()
    assert adapter.removed == []


# ── "Найти" fills the table from the core ────────────────────────────────────

def test_find_fills_the_table_with_the_core_groups_and_report(
        main_window, qapp):
    """К3 (§2.3) — the list shows type, net, position, layer, copy count, the
    UUID kept and the UUIDs deleted, and a drill mismatch is visible in the row
    (it does NOT split the group)."""
    adapter = _board()
    dock = _dock(main_window, adapter)

    _scan(dock, qapp)

    expected_report = format_report(
        find_via_duplicate_groups(adapter.get_vias()),
        find_track_duplicate_groups(adapter.get_tracks()))
    assert dock._report_text == expected_report
    assert dock.table.rowCount() == 2

    assert [_cell(dock, 0, c) for c in range(8)] == [
        "via", "GND", "(1.0000, 2.0000)", "", "2", "v1", "v2",
        "drill/diameter: [(0.3, 0.6), (0.4, 0.8)]"]
    assert [_cell(dock, 1, c) for c in range(8)] == [
        "track", "SIG", "(0.0000, 0.0000) -> (1.0000, 1.0000)", "F.Cu",
        "2", "t1", "t2", ""]


def test_find_with_no_board_tells_the_user_and_touches_nothing(
        main_window, qapp, caplog):
    """К3 — no connection is a message in the Log, not a crash and not a scan."""
    main_window.connection.board = None
    dock = DedupeDock(main_window, connection=main_window.connection)

    _scan(dock, qapp)

    assert dock.table.rowCount() == 0
    assert any("Connect to KiCad first." in r.getMessage()
               for r in caplog.records)


# ── "Копировать" is exactly the core text ────────────────────────────────────

def test_copy_puts_the_last_report_on_the_clipboard(main_window, qapp):
    """К3 (§2.3) — "Копировать" кладёт в буфер ровно текст отчёта ядра, чтобы
    его можно было сразу переслать: byte for byte the CLI's own output."""
    dock = _dock(main_window, _board())
    assert dock.copy_button.isEnabled() is False       # nothing scanned yet

    _scan(dock, qapp)
    assert dock.copy_button.isEnabled() is True

    dock._on_copy()

    assert QApplication.clipboard().text() == dock._report_text
    assert "Duplicate via groups: 1" in dock._report_text
    assert "Total: 2 extra duplicate item(s); one copy per group is kept." \
        in dock._report_text


# ── the door: the board is read on the worker ────────────────────────────────

def test_find_reads_the_board_on_the_worker_not_the_ui_thread(
        main_window, qapp, monkeypatch):
    """К3 (§2.3, door rule) — the guard is pinned on a REAL BoardConnection,
    because that is where the `board` property (and therefore the guard) lives:
    the conftest `_FakeConnection`'s `board` is a plain attribute, so a cell
    built on it is a dummy. Measured: on the fake, injecting a UI-thread read
    kept this cell green.

    With the guard armed to RAISE, the scan still completes — possible only
    because the payload carries the CONNECTION and the worker reads
    connection.board on its own thread. A UI-thread read would raise inside the
    slot and leave the table empty."""
    adapter = _board()
    connection = BoardConnection(timeout_ms=10)
    connection.board = SimpleNamespace(adapter=adapter)
    dock = DedupeDock(main_window, connection=connection)
    monkeypatch.setattr(connection_mod, "ui_thread_predicate", is_ui_thread)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal", UI_READ_RAISE)

    _scan(dock, qapp)

    assert adapter.refresh_count == 1
    assert dock.table.rowCount() == 2


# ── "Очистить" ───────────────────────────────────────────────────────────────

def test_clear_deletes_the_extras_journals_and_rescans(
        main_window, qapp, tmp_path, caplog):
    """К3 (§2.3/§2.4) — Clear removes only the extras, journals each deletion,
    logs the one summary, and automatically re-scans: after the rescan the board
    has no duplicates left, so the table is empty."""
    adapter = _board()
    dock = _dock(main_window, adapter)
    dock.set_root_path(tmp_path / "root.sexp")
    _scan(dock, qapp)

    with caplog.at_level(logging.INFO, logger=ACTION_LOGGER_NAME):
        dock._on_clear()
        # Two operations in a row: the clear, then the deferred auto-rescan.
        # The table only empties at the END of the rescan, so this cannot
        # return in the gap between the two.
        _pump(qapp, lambda: (not main_window.connection.long_op_active
                             and dock.table.rowCount() == 0))

    assert adapter.removed == ["v2", "t2"]
    assert dock.table.rowCount() == 0

    journal = [r.getMessage() for r in caplog.records
               if r.name == ACTION_LOGGER_NAME]
    assert "removed via uuid=v2 net='GND'" in journal[0]
    assert "kept=v1" in journal[0]
    assert any(apply_summary(2, 2) == r.getMessage() for r in caplog.records)


def test_clear_keeps_the_lone_items(main_window, qapp, tmp_path, caplog):
    """К3 — the survivor set is exactly "one per group": the lone v3 and t4 are
    never named to remove_by_id (they were never a duplicate of anything)."""
    adapter = _board()
    dock = _dock(main_window, adapter)
    dock.set_root_path(tmp_path / "root.sexp")
    _scan(dock, qapp)

    dock._on_clear()
    _pump(qapp, lambda: (not main_window.connection.long_op_active
                         and dock.table.rowCount() == 0))

    assert {v.uuid for v in adapter.get_vias()} == {"v1", "v3"}
    assert {t.uuid for t in adapter.get_tracks()} == {"t1", "t4"}
