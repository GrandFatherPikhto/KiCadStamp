# tests/gui/docks/test_dedupe_dock.py
"""Cells for gui/docks/dedupe.py — the "Дубли меди" panel (plan_2026_10_01_
dedupe_into_kicadstamp §2.3).

What each cell is for:
  * (Ч1б, 23.10) the panel is the FIFTH row of the Checks page — the bottom
    dock is GONE and the bottom area is the Log alone, one View-menu item. That
    cell REPLACES `test_panel_is_tabified_with_the_log_and_has_its_view_menu_action`,
    whose property (a tabified bottom dock with a toggleViewAction) is the one
    Denis's decision removes;
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

from kicadstamp.board_dedupe import (track_position_text, via_position_text)
from gui.docks.dedupe import DedupePage
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


def _panel(main_window, adapter) -> DedupePage:
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    return DedupePage(main_window, connection=main_window.connection)


def _scan(panel, qapp) -> None:
    """Click Find and pump until the panel's own operation is over (the worker
    released the shared socket AND the panel left its "running" state)."""
    panel._on_find()
    _pump(qapp, lambda: (not panel._connection.long_op_active
                         and not panel.is_running()))


def _cell(panel, row: int, column: int) -> str:
    return panel.table.item(row, column).text()


# ── the panel lives in the Checks tree; the bottom area is the Log alone ──────

def test_the_panel_is_the_fifth_row_of_the_checks_page_and_not_a_bottom_dock(
        real_main_window):
    """Ч1б (Denis, 23.10: «справа снизу Copper Duplicates убираем»).

    REPLACES `test_panel_is_tabified_with_the_log_and_has_its_view_menu_action`:
    that cell pinned a bottom dock tabified with the Log and its own
    `toggleViewAction` in the View menu — exactly what the decision removes. What
    stands in its place:

    * the bottom area holds the Log and nothing else (`DockHub.docks` has ONE
      entry, so the View menu has ONE item);
    * the copper-duplicates panel is the LAST row of the Checks tree, its QView is
      that very page, and it stays reachable from the "Checks" menu — so nothing
      the old dock offered is lost."""
    from kicadstamp.i18n import _
    from gui.checks_registry import CHECKS, DEDUPE_ROW

    hub = real_main_window._dock_hub
    page = hub.checks_page

    assert hub.docks == [hub.log_dock]
    view_menu = next(action.menu()
                     for action in real_main_window.menuBar().actions()
                     if action.text().replace("&", "") == "View")
    assert [a.text().replace("&", "") for a in view_menu.actions()] == ["Log"]

    titles = [page.tree.topLevelItem(i).text(0)
              for i in range(page.tree.topLevelItemCount())]
    assert len(titles) == len(CHECKS) + 1
    assert titles[-1].endswith(_("Copper duplicates"))
    page.select(DEDUPE_ROW)
    assert page.pane.currentWidget() is page.dedupe
    assert page.dedupe.find_button.isVisibleTo(page) is True

    checks_menu = next(action.menu()
                       for action in real_main_window.menuBar().actions()
                       if action.text().replace("&", "") == "Checks")
    texts = [a.text().replace("&", "") for a in checks_menu.actions() if a.text()]
    assert _("Copper duplicates") in texts
    assert _("Clear copper duplicates") in texts


# ── "Очистить" gating ────────────────────────────────────────────────────────

def test_clear_needs_both_a_root_and_a_found_list(main_window, qapp, tmp_path):
    """К3 (§2.3) — the button is inactive while the list is empty OR no root
    config is open: the journal is the root config's actions.log, so a removal
    with nowhere to journal it must not be offered."""
    panel = _panel(main_window, _board())
    assert panel.clear_button.isEnabled() is False     # nothing found, no root

    panel.set_root_path(tmp_path / "root.sexp")
    assert panel.clear_button.isEnabled() is False     # root, still nothing found

    _scan(panel, qapp)
    assert panel.table.rowCount() == 2
    assert panel.clear_button.isEnabled() is True

    # The other half: rows exist but no root is open -> still disabled.
    other = _panel(main_window, _board())
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
    panel = _panel(main_window, adapter)
    _scan(panel, qapp)
    assert panel.table.rowCount() > 0

    before = panel._active_op
    panel._on_clear()          # no root_path

    assert panel._active_op is before, \
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
    panel = _panel(main_window, adapter)

    _scan(panel, qapp)

    expected_report = format_report(
        find_via_duplicate_groups(adapter.get_vias()),
        find_track_duplicate_groups(adapter.get_tracks()))
    assert panel._report_text == expected_report
    assert panel.table.rowCount() == 2

    assert [_cell(panel, 0, c) for c in range(8)] == [
        "via", "GND", "(1.0000, 2.0000)", "", "2", "v1", "v2",
        "drill/diameter: [(0.3, 0.6), (0.4, 0.8)]"]
    assert [_cell(panel, 1, c) for c in range(8)] == [
        "track", "SIG", "(0.0000,0.0000) -> (1.0000,1.0000)", "F.Cu",
        "2", "t1", "t2", ""]


def test_table_row_and_report_line_name_the_same_coordinates_in_the_same_order(
        main_window, qapp):
    """К3/§5.1 (acceptance) — ONE group, two views, identical words.

    The track is stored (5,0)->(1,0) ON PURPOSE: the pair is unordered, so the
    report normalises it, and the panel's row must say exactly the same thing.
    Before the fix the row printed the RAW ends with a space after each comma
    ("(5.0000, 0.0000) -> (1.0000, 0.0000)") while the copied text said
    "(1.0000,0.0000) -> (5.0000,0.0000)" — and the panel's own docstring promised
    the two read as one. The positions now come from the core's builders, which
    is what makes "same coordinates, same order" a property rather than a
    coincidence."""
    track = _track("t1", "SIG", BoardLayer.BL_F_Cu, (5.0, 0.0), (1.0, 0.0))
    twin = _track("t2", "SIG", BoardLayer.BL_F_Cu, (5.0, 0.0), (1.0, 0.0))
    via = _via("v1", "GND", 1.0, 2.0)
    twin_via = _via("v2", "GND", 1.0, 2.0)
    adapter = FakeAdapter(vias=[via, twin_via], tracks=[track, twin])
    panel = _panel(main_window, adapter)

    _scan(panel, qapp)

    # The literal text first (this is what pins the NORMALISED order, and what
    # the pre-fix row failed), then the shared-builder property for both kinds.
    assert _cell(panel, 1, 2) == "(1.0000,0.0000) -> (5.0000,0.0000)"
    for row, position in ((0, via_position_text(via)),
                          (1, track_position_text(track))):
        assert _cell(panel, row, 2) == position
        assert f"@ {position} mm" in panel._report_text


def test_find_with_no_board_tells_the_user_and_touches_nothing(
        main_window, qapp, caplog):
    """К3 — no connection is a message in the Log, not a crash and not a scan."""
    main_window.connection.board = None
    panel = DedupePage(main_window, connection=main_window.connection)

    _scan(panel, qapp)

    assert panel.table.rowCount() == 0
    assert any("Connect to KiCad first." in r.getMessage()
               for r in caplog.records)


# ── "Копировать" is exactly the core text ────────────────────────────────────

def test_copy_puts_the_last_report_on_the_clipboard(main_window, qapp):
    """К3 (§2.3) — "Копировать" кладёт в буфер ровно текст отчёта ядра, чтобы
    его можно было сразу переслать: byte for byte the CLI's own output."""
    panel = _panel(main_window, _board())
    assert panel.copy_button.isEnabled() is False       # nothing scanned yet

    _scan(panel, qapp)
    assert panel.copy_button.isEnabled() is True

    panel._on_copy()

    assert QApplication.clipboard().text() == panel._report_text
    assert "Duplicate via groups: 1" in panel._report_text
    assert "Total: 2 extra duplicate item(s); one copy per group is kept." \
        in panel._report_text


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
    panel = DedupePage(main_window, connection=connection)
    monkeypatch.setattr(connection_mod, "ui_thread_predicate", is_ui_thread)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal", UI_READ_RAISE)

    _scan(panel, qapp)

    assert adapter.refresh_count == 1
    assert panel.table.rowCount() == 2


# ── "Очистить" ───────────────────────────────────────────────────────────────

def test_clear_deletes_the_extras_journals_and_rescans(
        main_window, qapp, tmp_path, caplog):
    """К3 (§2.3/§2.4) — Clear removes only the extras, journals each deletion,
    logs the one summary, and automatically re-scans: after the rescan the board
    has no duplicates left, so the table is empty."""
    adapter = _board()
    panel = _panel(main_window, adapter)
    panel.set_root_path(tmp_path / "root.sexp")
    _scan(panel, qapp)

    with caplog.at_level(logging.INFO, logger=ACTION_LOGGER_NAME):
        panel._on_clear()
        # Two operations in a row: the clear, then the deferred auto-rescan.
        # The table only empties at the END of the rescan, so this cannot
        # return in the gap between the two.
        _pump(qapp, lambda: (not main_window.connection.long_op_active
                             and panel.table.rowCount() == 0))

    assert adapter.removed == ["v2", "t2"]
    assert panel.table.rowCount() == 0

    journal = [r.getMessage() for r in caplog.records
               if r.name == ACTION_LOGGER_NAME]
    assert "removed via uuid=v2 net='GND'" in journal[0]
    assert "kept=v1" in journal[0]
    assert any(apply_summary(2, 2) == r.getMessage() for r in caplog.records)


def test_clear_keeps_the_lone_items(main_window, qapp, tmp_path, caplog):
    """К3 — the survivor set is exactly "one per group": the lone v3 and t4 are
    never named to remove_by_id (they were never a duplicate of anything)."""
    adapter = _board()
    panel = _panel(main_window, adapter)
    panel.set_root_path(tmp_path / "root.sexp")
    _scan(panel, qapp)

    panel._on_clear()
    _pump(qapp, lambda: (not main_window.connection.long_op_active
                         and panel.table.rowCount() == 0))

    assert {v.uuid for v in adapter.get_vias()} == {"v1", "v3"}
    assert {t.uuid for t in adapter.get_tracks()} == {"t1", "t4"}
