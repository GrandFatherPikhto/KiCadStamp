# tests/gui/docks/test_dedupe_dock.py
"""Cells for gui/docks/dedupe.py — the "Дубли меди" panel (plan_2026_10_01_
dedupe_into_kicadstamp §2.3; a page of the Checks tab since Ч1б and a TREE OF
GROUPS since Ч2а of plan_2026_10_23_checks_tab_top_level).

What each cell is for:
  * (Ч1б, 23.10) the panel is the FIFTH row of the Checks page — the bottom
    dock is GONE and the bottom area is the Log alone, one View-menu item. That
    cell REPLACES `test_panel_is_tabified_with_the_log_and_has_its_view_menu_action`,
    whose property (a tabified bottom dock with a toggleViewAction) is the one
    Denis's decision removes;
  * (Ч2а, 25.10) the flat TABLE is gone: one tree node per duplicate group, the
    group's own fields in the QView below it, and two doors that act on ONE group
    — "Highlight on board" (overlay markers, the operator's board selection is
    never touched) and "Clear this group" (the same core call, journal and
    refusals as "Clear all"). The cells that used to read the table now read the
    tree — every assertion kept, the helpers renamed;
  * "Очистить" is disabled until BOTH a project root is open (the journal needs
    its actions.log) AND the list is non-empty, and the handlers re-check it, so
    a programmatic call cannot delete unjournaled copper;
  * "Копировать" puts the CORE report text on the clipboard byte for byte;
  * the board is read on the WORKER: the door is armed with refusal="raise"
    (gui.connection) and the scan still succeeds, which is only possible if
    nothing reads connection.board on the UI thread;
  * clearing deletes the extras, journals each to kicadstamp.actions, logs the
    one summary and automatically re-scans.

The board stand-in is the shared tests/fakes/adapter.FakeAdapter (its
remove_by_id really removes and records), driven through the same
start_long_op path production uses. The overlay itself is gui/board_overlay's
business (its own cells cover the drawing) — HERE the owner's `ensure_marker` is
replaced by a recorder, so what is pinned is WHICH points and keys this panel
asks for and that it selects nothing on the board.
"""
import logging
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from gui import connection as connection_mod
from gui import overlay_markers
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


def _nodes(panel):
    return [panel.tree.topLevelItem(i)
            for i in range(panel.tree.topLevelItemCount())]


def _cell(panel, row: int, column: int) -> str:
    return panel.tree.topLevelItem(row).text(column)


def _detail(panel, name: str) -> str:
    return getattr(panel, f"detail_{name}").text()


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
    with nowhere to journal it must not be offered. Ч2а: "Clear this group" follows
    the SAME rule."""
    panel = _panel(main_window, _board())
    assert panel.clear_button.isEnabled() is False     # nothing found, no root
    assert panel.clear_group_button.isEnabled() is False

    panel.set_root_path(tmp_path / "root.sexp")
    assert panel.clear_button.isEnabled() is False     # root, still nothing found
    assert panel.clear_group_button.isEnabled() is False

    _scan(panel, qapp)
    assert panel.tree.topLevelItemCount() == 2
    assert panel.clear_button.isEnabled() is True
    assert panel.clear_group_button.isEnabled() is True   # a node is selected

    # The other half: groups exist but no root is open -> still disabled.
    other = _panel(main_window, _board())
    _scan(other, qapp)
    assert other.tree.topLevelItemCount() == 2
    assert other.clear_button.isEnabled() is False
    assert other.clear_group_button.isEnabled() is False


def test_clear_does_nothing_when_programmatically_called_without_a_root(
        main_window, qapp):
    """К3 — the disabled button is not the only guard: the handlers refuse too,
    so a programmatic call cannot delete copper that cannot be journaled.

    The assertion pins that NO NEW OPERATION was started (`_active_op` is the
    controller of the scan that already ran). A bare "adapter.removed == []"
    right after the call is BLIND here — measured: with the guard removed the
    cell still passed, because the worker had not run yet. Hence the identity
    check, which is the half that actually fails when the guard goes."""
    adapter = _board()
    panel = _panel(main_window, adapter)
    _scan(panel, qapp)
    assert panel.tree.topLevelItemCount() > 0

    before = panel._active_op
    panel._on_clear()          # no root_path
    panel._on_clear_group()    # no root_path either

    assert panel._active_op is before, \
        "Clear started an operation while it had nowhere to journal the removal"
    qapp.processEvents()
    assert adapter.removed == []


# ── "Найти" fills the group nodes from the core ──────────────────────────────

def test_find_fills_the_group_nodes_with_the_core_groups_and_report(
        main_window, qapp):
    """К3 (§2.3) — one NODE per group, and the node says what tells groups apart:
    type, net, position, copy count and a drill mismatch (which does NOT split the
    group). The kept/deleted UUIDs live in that group's QView (Ч2а).

    REPLACED (Ч2а) `test_find_fills_the_table_with_the_core_groups_and_report`: the
    table went, every field it showed is still asserted — five of them on the node,
    the two UUID columns in the detail."""
    adapter = _board()
    panel = _panel(main_window, adapter)

    _scan(panel, qapp)

    expected_report = format_report(
        find_via_duplicate_groups(adapter.get_vias()),
        find_track_duplicate_groups(adapter.get_tracks()))
    assert panel._report_text == expected_report
    assert panel.tree.topLevelItemCount() == 2

    assert [_cell(panel, 0, c) for c in range(5)] == [
        "via", "GND", "(1.0000, 2.0000)", "2",
        "drill/diameter: [(0.3, 0.6), (0.4, 0.8)]"]
    assert [_cell(panel, 1, c) for c in range(5)] == [
        "track", "SIG", "(0.0000,0.0000) -> (1.0000,1.0000)", "2", ""]


def test_the_group_detail_shows_every_field_of_the_core_group(
        main_window, qapp):
    """Ч2а: the group's QView carries ALL of it — type, net, position, layer,
    copies, the UUID kept, the UUIDs deleted and the warning — for the SELECTED
    node, and it follows the selection."""
    adapter = _board()
    panel = _panel(main_window, adapter)
    _scan(panel, qapp)

    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    assert _detail(panel, "kind") == "via"
    assert _detail(panel, "net") == "GND"
    assert _detail(panel, "position") == "(1.0000, 2.0000)"
    assert _detail(panel, "layer") == ""
    assert _detail(panel, "copies") == "2"
    assert _detail(panel, "keep") == "v1"
    assert _detail(panel, "delete") == "v2"
    assert _detail(panel, "warning") == \
        "drill/diameter: [(0.3, 0.6), (0.4, 0.8)]"

    panel.tree.setCurrentItem(panel.tree.topLevelItem(1))
    assert _detail(panel, "kind") == "track"
    assert _detail(panel, "layer") == "F.Cu"
    assert _detail(panel, "keep") == "t1"
    assert _detail(panel, "delete") == "t2"
    assert _detail(panel, "warning") == ""


def test_tree_node_and_report_line_name_the_same_coordinates_in_the_same_order(
        main_window, qapp):
    """К3/§5.1 (acceptance) — ONE group, two views, identical words.

    The track is stored (5,0)->(1,0) ON PURPOSE: the pair is unordered, so the
    report normalises it, and the panel's node must say exactly the same thing.
    Before the fix the panel printed the RAW ends with a space after each comma
    ("(5.0000, 0.0000) -> (1.0000, 0.0000)") while the copied text said
    "(1.0000,0.0000) -> (5.0000,0.0000)" — and the panel's own docstring promised
    the two read as one. The positions now come from the core's builders, which
    is what makes "same coordinates, same order" a property rather than a
    coincidence.

    REPLACED (Ч2а) `test_table_row_and_report_line_...`: the row became a node; the
    assertions are the same."""
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

    assert panel.tree.topLevelItemCount() == 0
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
    slot and leave the tree empty."""
    adapter = _board()
    connection = BoardConnection(timeout_ms=10)
    connection.board = SimpleNamespace(adapter=adapter)
    panel = DedupePage(main_window, connection=connection)
    monkeypatch.setattr(connection_mod, "ui_thread_predicate", is_ui_thread)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal", UI_READ_RAISE)

    _scan(panel, qapp)

    assert adapter.refresh_count == 1
    assert panel.tree.topLevelItemCount() == 2


# ── "Очистить" ───────────────────────────────────────────────────────────────

def test_clear_deletes_the_extras_journals_and_rescans(
        main_window, qapp, tmp_path, caplog):
    """К3 (§2.3/§2.4) — Clear removes only the extras, journals each deletion,
    logs the one summary, and automatically re-scans: after the rescan the board
    has no duplicates left, so the tree is empty."""
    adapter = _board()
    panel = _panel(main_window, adapter)
    panel.set_root_path(tmp_path / "root.sexp")
    _scan(panel, qapp)

    with caplog.at_level(logging.INFO, logger=ACTION_LOGGER_NAME):
        panel._on_clear()
        # Two operations in a row: the clear, then the deferred auto-rescan.
        # The tree only empties at the END of the rescan, so this cannot
        # return in the gap between the two.
        _pump(qapp, lambda: (not main_window.connection.long_op_active
                             and panel.tree.topLevelItemCount() == 0))

    assert adapter.removed == ["v2", "t2"]
    assert panel.tree.topLevelItemCount() == 0

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
                         and panel.tree.topLevelItemCount() == 0))

    assert {v.uuid for v in adapter.get_vias()} == {"v1", "v3"}
    assert {t.uuid for t in adapter.get_tracks()} == {"t1", "t4"}


# ── Ч2а: "Clear this group" ─────────────────────────────────────────────────

def test_clear_this_group_removes_only_that_groups_extras(
        main_window, qapp, tmp_path, caplog):
    """Ч2а: the group's own door removes the SELECTED group's extras, journals
    them like any other removal, leaves the other group untouched, and the group
    leaves the tree after the auto-rescan."""
    adapter = _board()
    panel = _panel(main_window, adapter)
    panel.set_root_path(tmp_path / "root.sexp")
    _scan(panel, qapp)
    assert panel.tree.topLevelItemCount() == 2

    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))     # the via group
    with caplog.at_level(logging.INFO, logger=ACTION_LOGGER_NAME):
        panel._on_clear_group()
        _pump(qapp, lambda: (not main_window.connection.long_op_active
                             and panel.tree.topLevelItemCount() == 1))

    assert adapter.removed == ["v2"], "only the selected group's extra went"
    journal = [r.getMessage() for r in caplog.records
               if r.name == ACTION_LOGGER_NAME]
    assert any("removed via uuid=v2" in line for line in journal)
    # The track pair is still there — the other group was not touched.
    assert panel.tree.topLevelItemCount() == 1
    assert _cell(panel, 0, 0) == "track"
    assert {t.uuid for t in adapter.get_tracks()} == {"t1", "t2", "t4"}


def test_clear_this_group_does_nothing_while_a_check_runs(
        main_window, qapp, tmp_path, monkeypatch):
    """Д-1 applies to the group's door too: while the PAGE is busy the button is
    off and the door refuses, so a check run cannot be interleaved with a removal."""
    adapter = _board()
    panel = _panel(main_window, adapter)
    panel.set_root_path(tmp_path / "root.sexp")
    _scan(panel, qapp)
    panel.set_page_busy_provider(lambda: True)
    try:
        assert panel.clear_group_button.isEnabled() is False
        before = panel._active_op
        panel._on_clear_group()
        assert panel._active_op is before
        qapp.processEvents()
        assert adapter.removed == []
    finally:
        panel.set_page_busy_provider(lambda: False)


# ── Ч2а: "Highlight on board" ───────────────────────────────────────────────

def test_highlight_marks_the_groups_members_and_selects_nothing(
        main_window, qapp, monkeypatch):
    """Ч2а: "Highlight on board" asks the overlay owner for ONE marker per MEMBER
    of the selected group, at that member's own place (a via's position, a track's
    MIDDLE), keyed by the member's uuid so a second press replaces instead of
    stacking — and it never selects the group's copper on the board (the
    operator's own selection is not ours to move).

    The overlay DRAWING belongs to gui/board_overlay (its own cells cover it), so
    the owner's `ensure_marker` is a recorder here: what this cell pins is what
    the panel asks for."""
    adapter = _board()
    panel = _panel(main_window, adapter)
    _scan(panel, qapp)

    asked = []

    def fake_ensure_marker(self, adapter_, key, x_mm, y_mm, **_kwargs):
        asked.append((key, round(x_mm, 4), round(y_mm, 4)))
        return "overlay-" + key

    monkeypatch.setattr(overlay_markers.OverlayMarkerOwner, "ensure_marker",
                        fake_ensure_marker)
    selected = []
    # `raising=False`: FakeAdapter has no selection surface at all, and the point is
    # that our door would USE one if it had it — a recorder proves it never asks.
    monkeypatch.setattr(adapter, "select_items",
                        lambda items: selected.append(list(items)),
                        raising=False)

    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))     # the via group
    panel._on_highlight()
    _pump(qapp, lambda: not panel.is_running())
    assert asked == [("dedupe/v1", 1.0, 2.0), ("dedupe/v2", 1.0, 2.0)]

    asked.clear()
    panel.tree.setCurrentItem(panel.tree.topLevelItem(1))     # the track group
    panel._on_highlight()
    _pump(qapp, lambda: not panel.is_running())
    assert asked == [("dedupe/t1", 0.5, 0.5), ("dedupe/t2", 0.5, 0.5)]

    assert selected == [], "the panel must never select copper on the board"


def test_highlight_does_nothing_without_a_selected_group(main_window, qapp):
    """The button is off when no group node is selected (an empty scan), and the
    door refuses from code as well."""
    adapter = _board(duplicates=False)
    panel = _panel(main_window, adapter)
    _scan(panel, qapp)
    assert panel.tree.topLevelItemCount() == 0
    assert panel.highlight_button.isEnabled() is False
    before = panel._active_op
    panel._on_highlight()
    assert panel._active_op is before
