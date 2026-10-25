# tests/gui/docks/test_dedupe_dock.py
"""Cells for gui/docks/dedupe.py — the "Дубли меди" panel (plan_2026_10_01_
dedupe_into_kicadstamp §2.3; a page of the Checks tab since Ч1б and GROUP NODES
since Ч2а of plan_2026_10_23_checks_tab_top_level).

What each cell is for:
  * (Ч1б, 23.10) the panel is the FIFTH row of the Checks page — the bottom
    dock is GONE and the bottom area is the Log alone, one View-menu item. That
    cell REPLACES `test_panel_is_tabified_with_the_log_and_has_its_view_menu_action`,
    whose property (a tabified bottom dock with a toggleViewAction) is the one
    Denis's decision removes;
  * (Ч2а, 25.10 + Д-1 of its acceptance) the flat TABLE is gone and so is the
    panel's own tree: the duplicate GROUPS are CHILDREN of the "Copper
    duplicates" row in the PAGE's tree, and the right side shows the QView of
    whatever is selected — the row (Find / Copy / "Clear all" / summary) or ONE
    group (its fields, "Highlight on board", "Clear this group"). The cells that
    used to read the table now read those nodes; every assertion kept;
  * (Д-2) every door that changes the list — Highlight, Find, "Clear this group"
    and "Clear all" — first drops the WHOLE `dedupe` namespace of the overlay
    owner, and only that one: a mark must never outlive the scan that drew it,
    and a cell anchor or a point marker is somebody else's;
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
asks for, that it selects nothing on the board, and that it clears its OWN marks
before every list change.
"""
import logging
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

from gui import connection as connection_mod
from gui import overlay_markers, settings
from gui.connection import UI_READ_RAISE, BoardConnection

from gui.checks_registry import DEDUPE_ROW
from gui.docks.checks import ChecksPage, DEDUPE_GROUP_ROLE
from gui.docks.dedupe import DedupePage, group_label
from gui.worker import is_ui_thread
from kicadstamp.board_dedupe import (ACTION_LOGGER_NAME, apply_summary,
                                     find_track_duplicate_groups,
                                     find_via_duplicate_groups, format_report,
                                     track_position_text, via_position_text)
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
    """The panel ALONE — for the cells about its own doors and QViews."""
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    return DedupePage(main_window, connection=main_window.connection)


def _page(main_window, adapter) -> ChecksPage:
    """The CHECKS PAGE over the same fake board — the tree that owns the row AND
    (since Д-1) the group nodes, which is where the operator sees them."""
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    return ChecksPage(main_window, connection=main_window.connection)


def _scan(panel, qapp) -> None:
    """Click Find and pump until the panel's own operation is over (the worker
    released the shared socket AND the panel left its "running" state)."""
    panel._on_find()
    _pump(qapp, lambda: (not panel._connection.long_op_active
                         and not panel.is_running()))


def _nodes(page) -> list:
    """The duplicate GROUP nodes — the children of the "Copper duplicates" row."""
    row = page._items[DEDUPE_ROW]
    return [row.child(i) for i in range(row.childCount())]


def _detail(panel, name: str) -> str:
    return getattr(panel, f"detail_{name}").text()


def _seed_marks(mapping: dict) -> None:
    """Put `{key: uuid}` into the overlay owner's own state — how the panel finds
    the marks it drew last time (a real highlight writes here through the owner)."""
    settings.state.set(overlay_markers.OVERLAY_MARKERS_KEY, dict(mapping))


def _owner_keys() -> set:
    return set(overlay_markers.OverlayMarkerOwner().keys())


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
      that very page (the ROW page of the panel's own stack, Д-1), and it stays
      reachable from the "Checks" menu — so nothing the old dock offered is lost."""
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
    assert page.dedupe.stack.currentWidget() is page.dedupe.row_page
    assert page.dedupe.find_button.isVisibleTo(page) is True

    checks_menu = next(action.menu()
                       for action in real_main_window.menuBar().actions()
                       if action.text().replace("&", "") == "Checks")
    texts = [a.text().replace("&", "") for a in checks_menu.actions() if a.text()]
    assert _("Copper duplicates") in texts
    assert _("Clear copper duplicates") in texts


# ── Д-1: the row and a group are two different QViews ────────────────────────

def test_the_row_shows_the_page_doors_and_a_group_shows_its_fields(
        main_window, qapp):
    """Д-1 of the Ч2а acceptance: ONE tree, TWO QViews.

    * the ROW "Copper duplicates" -> Find / Copy / "Clear all" / summary, and no
      group fields at all;
    * a GROUP node -> that group's every field and the two doors that act on it
      alone. Both live in the SAME panel widget, so the right pane never shows a
      check's bytes next to a duplicate group."""
    page = _page(main_window, _board())
    panel = page.dedupe
    _scan(panel, qapp)

    page.select(DEDUPE_ROW)
    assert page.pane.currentWidget() is panel
    assert panel.stack.currentWidget() is panel.row_page
    assert panel.find_button.isVisibleTo(page) is True
    assert panel.highlight_button.isVisibleTo(page) is False
    assert _detail(panel, "kind") == ""

    page.select_dedupe_group(1)
    assert page.pane.currentWidget() is panel
    assert panel.stack.currentWidget() is panel.group_page
    assert panel.highlight_button.isVisibleTo(page) is True
    assert panel.clear_group_button.isVisibleTo(page) is True
    assert _detail(panel, "kind") == "track"
    assert _detail(panel, "keep") == "t1"


def test_a_rescan_keeps_the_selected_group_by_its_kept_uuid(
        main_window, qapp):
    """Д-1: the child nodes are REBUILT after every scan, so the selection has to
    survive it by something stable — the group's KEPT uuid (the core's own
    identity in the report's words), never the row index.

    And when that group is gone the selection falls back to the ROW: showing the
    fields of whichever group took its index would be a lie about what was
    cleared."""
    adapter = _board()
    page = _page(main_window, adapter)
    _scan(page.dedupe, qapp)
    assert [node.text(0).split("  ")[0] for node in _nodes(page)] == ["via", "track"]

    page.select_dedupe_group(1)                       # the track group, keep t1
    assert _detail(page.dedupe, "keep") == "t1"

    _scan(page.dedupe, qapp)                          # the same board: nothing moved
    assert page.selected_group_index() == 1
    assert _detail(page.dedupe, "keep") == "t1"

    main_window.connection.board = SimpleNamespace(adapter=_board(duplicates=False))
    _scan(page.dedupe, qapp)
    assert _nodes(page) == []
    assert page.selected_group_index() is None
    assert page.dedupe.stack.currentWidget() is page.dedupe.row_page
    assert _detail(page.dedupe, "keep") == ""


# ── "Очистить" gating ────────────────────────────────────────────────────────

def test_clear_needs_both_a_root_and_a_found_list(main_window, qapp, tmp_path):
    """К3 (§2.3) — the button is inactive while the list is empty OR no root
    config is open: the journal is the root config's actions.log, so a removal
    with nowhere to journal it must not be offered. "Clear this group" follows
    the SAME rule AND needs a selected GROUP (Д-1: after a scan the ROW is
    selected, so the group door stays off until a group is picked)."""
    page = _page(main_window, _board())
    panel = page.dedupe
    assert panel.clear_button.isEnabled() is False     # nothing found, no root
    assert panel.clear_group_button.isEnabled() is False

    panel.set_root_path(tmp_path / "root.sexp")
    assert panel.clear_button.isEnabled() is False     # root, still nothing found
    assert panel.clear_group_button.isEnabled() is False

    _scan(panel, qapp)
    assert len(_nodes(page)) == 2
    assert panel.clear_button.isEnabled() is True
    assert panel.clear_group_button.isEnabled() is False   # the ROW is selected
    page.select_dedupe_group(0)
    assert panel.clear_group_button.isEnabled() is True    # a group node is now

    # The other half: groups exist but no root is open -> still disabled.
    other = _page(main_window, _board())
    _scan(other.dedupe, qapp)
    assert len(_nodes(other)) == 2
    other.select_dedupe_group(0)
    assert other.dedupe.clear_button.isEnabled() is False
    assert other.dedupe.clear_group_button.isEnabled() is False


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
    page = _page(main_window, adapter)
    _scan(page.dedupe, qapp)
    page.select_dedupe_group(0)
    assert page.dedupe.has_groups() is True

    before = page.dedupe._active_op
    page.dedupe._on_clear()          # no root_path
    page.dedupe._on_clear_group()    # no root_path either

    assert page.dedupe._active_op is before, \
        "Clear started an operation while it had nowhere to journal the removal"
    qapp.processEvents()
    assert adapter.removed == []


# ── "Найти" fills the group nodes from the core ──────────────────────────────

def test_find_fills_the_group_nodes_with_the_core_groups_and_report(
        main_window, qapp):
    """К3 (§2.3) — one CHILD NODE per group under the "Copper duplicates" row,
    and the node says what tells groups apart: type, net, position, copy count
    and a drill mismatch (which does NOT split the group). The kept/deleted UUIDs
    live in that group's QView; the node is the summary line.

    REPLACED (Ч2а) `test_find_fills_the_table_with_the_core_groups_and_report` and
    re-aimed at the PAGE's tree (Д-1 of that acceptance): the table went, every
    field it showed is still asserted — five of them on the node, the two UUID
    columns in the detail."""
    adapter = _board()
    page = _page(main_window, adapter)

    _scan(page.dedupe, qapp)

    expected_report = format_report(
        find_via_duplicate_groups(adapter.get_vias()),
        find_track_duplicate_groups(adapter.get_tracks()))
    assert page.dedupe._report_text == expected_report

    nodes = _nodes(page)
    assert [node.text(0) for node in nodes] == [
        "via  GND  (1.0000, 2.0000)  ×2  "
        "⚠ drill/diameter: [(0.3, 0.6), (0.4, 0.8)]",
        "track  SIG  (0.0000,0.0000) -> (1.0000,1.0000)  ×2",
    ]
    # The node IS the core's dict rendered by ONE function, not a second format.
    assert [node.text(0) for node in nodes] == [
        group_label(fields) for fields in page.dedupe.groups()]
    # The kept uuid travels in the node's data: it is what a rescan selects by.
    assert [node.data(0, 0x0100) for node in nodes] == [
        (DEDUPE_GROUP_ROLE, 0, "v1"), (DEDUPE_GROUP_ROLE, 1, "t1")]
    assert page._items[DEDUPE_ROW].isExpanded() is True


def test_the_group_detail_shows_every_field_of_the_core_group(
        main_window, qapp):
    """Ч2а: the group's QView carries ALL of it — type, net, position, layer,
    copies, the UUID kept, the UUIDs deleted and the warning — for the SELECTED
    node, and it follows the selection."""
    page = _page(main_window, _board())
    panel = page.dedupe
    _scan(panel, qapp)

    page.select_dedupe_group(0)
    assert _detail(panel, "kind") == "via"
    assert _detail(panel, "net") == "GND"
    assert _detail(panel, "position") == "(1.0000, 2.0000)"
    assert _detail(panel, "layer") == ""
    assert _detail(panel, "copies") == "2"
    assert _detail(panel, "keep") == "v1"
    assert _detail(panel, "delete") == "v2"
    assert _detail(panel, "warning") == "drill/diameter: [(0.3, 0.6), (0.4, 0.8)]"

    page.select_dedupe_group(1)
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

    REPLACED (Ч2а) `test_table_row_and_report_line_...`: the row became a node;
    the assertions are the same."""
    track = _track("t1", "SIG", BoardLayer.BL_F_Cu, (5.0, 0.0), (1.0, 0.0))
    twin = _track("t2", "SIG", BoardLayer.BL_F_Cu, (5.0, 0.0), (1.0, 0.0))
    via = _via("v1", "GND", 1.0, 2.0)
    twin_via = _via("v2", "GND", 1.0, 2.0)
    adapter = FakeAdapter(vias=[via, twin_via], tracks=[track, twin])
    page = _page(main_window, adapter)

    _scan(page.dedupe, qapp)

    # The literal text first (this is what pins the NORMALISED order, and what
    # the pre-fix row failed), then the shared-builder property for both kinds.
    texts = [node.text(0) for node in _nodes(page)]
    assert "track  SIG  (1.0000,0.0000) -> (5.0000,0.0000)  ×2" == texts[1]
    for text, position in ((texts[0], via_position_text(via)),
                           (texts[1], track_position_text(track))):
        assert position in text
        assert f"@ {position} mm" in page.dedupe._report_text


def test_find_with_no_board_tells_the_user_and_touches_nothing(
        main_window, qapp, caplog):
    """К3 — no connection is a message in the Log, not a crash and not a scan."""
    main_window.connection.board = None
    page = ChecksPage(main_window, connection=main_window.connection)

    _scan(page.dedupe, qapp)

    assert page.dedupe.group_count() == 0
    assert _nodes(page) == []
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
    slot and leave the list empty."""
    adapter = _board()
    connection = BoardConnection(timeout_ms=10)
    connection.board = SimpleNamespace(adapter=adapter)
    page = ChecksPage(main_window, connection=connection)
    monkeypatch.setattr(connection_mod, "ui_thread_predicate", is_ui_thread)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal", UI_READ_RAISE)

    _scan(page.dedupe, qapp)

    assert adapter.refresh_count == 1
    assert page.dedupe.group_count() == 2
    assert len(_nodes(page)) == 2


# ── "Очистить" ───────────────────────────────────────────────────────────────

def test_clear_deletes_the_extras_journals_and_rescans(
        main_window, qapp, tmp_path, caplog):
    """К3 (§2.3/§2.4) — Clear removes only the extras, journals each deletion,
    logs the one summary, and automatically re-scans: after the rescan the board
    has no duplicates left, so the row has no children left."""
    adapter = _board()
    page = _page(main_window, adapter)
    page.dedupe.set_root_path(tmp_path / "root.sexp")
    _scan(page.dedupe, qapp)

    with caplog.at_level(logging.INFO, logger=ACTION_LOGGER_NAME):
        page.dedupe._on_clear()
        # Two operations in a row: the clear, then the deferred auto-rescan.
        # The nodes only go at the END of the rescan, so this cannot return in
        # the gap between the two.
        _pump(qapp, lambda: (not main_window.connection.long_op_active
                             and page.dedupe.group_count() == 0))

    assert adapter.removed == ["v2", "t2"]
    assert _nodes(page) == []

    journal = [r.getMessage() for r in caplog.records
               if r.name == ACTION_LOGGER_NAME]
    assert "removed via uuid=v2 net='GND'" in journal[0]
    assert "kept=v1" in journal[0]
    assert any(apply_summary(2, 2) == r.getMessage() for r in caplog.records)


def test_clear_keeps_the_lone_items(main_window, qapp, tmp_path, caplog):
    """К3 — the survivor set is exactly "one per group": the lone v3 and t4 are
    never named to remove_by_id (they were never a duplicate of anything)."""
    adapter = _board()
    page = _page(main_window, adapter)
    page.dedupe.set_root_path(tmp_path / "root.sexp")
    _scan(page.dedupe, qapp)

    page.dedupe._on_clear()
    _pump(qapp, lambda: (not main_window.connection.long_op_active
                         and page.dedupe.group_count() == 0))

    assert {v.uuid for v in adapter.get_vias()} == {"v1", "v3"}
    assert {t.uuid for t in adapter.get_tracks()} == {"t1", "t4"}


# ── Ч2а: "Clear this group" ─────────────────────────────────────────────────

def test_clear_this_group_removes_only_that_groups_extras(
        main_window, qapp, tmp_path, caplog):
    """Ч2а: the group's own door removes the SELECTED group's extras, journals
    them like any other removal, leaves the other group untouched, and the group
    leaves the tree after the auto-rescan."""
    adapter = _board()
    page = _page(main_window, adapter)
    page.dedupe.set_root_path(tmp_path / "root.sexp")
    _scan(page.dedupe, qapp)
    assert len(_nodes(page)) == 2

    page.select_dedupe_group(0)                                # the via group
    with caplog.at_level(logging.INFO, logger=ACTION_LOGGER_NAME):
        page.dedupe._on_clear_group()
        _pump(qapp, lambda: (not main_window.connection.long_op_active
                             and page.dedupe.group_count() == 1))

    assert adapter.removed == ["v2"], "only the selected group's extra went"
    journal = [r.getMessage() for r in caplog.records
               if r.name == ACTION_LOGGER_NAME]
    assert any("removed via uuid=v2" in line for line in journal)
    # The track pair is still there — the other group was not touched.
    assert [node.text(0).split("  ")[0] for node in _nodes(page)] == ["track"]
    assert {t.uuid for t in adapter.get_tracks()} == {"t1", "t2", "t4"}


def test_clear_this_group_does_nothing_while_a_check_runs(
        main_window, qapp, tmp_path, monkeypatch):
    """Д-1 applies to the group's door too: while the PAGE is busy the button is
    off and the door refuses, so a check run cannot be interleaved with a removal."""
    adapter = _board()
    page = _page(main_window, adapter)
    page.dedupe.set_root_path(tmp_path / "root.sexp")
    _scan(page.dedupe, qapp)
    page.select_dedupe_group(0)
    page.dedupe.set_page_busy_provider(lambda: True)
    try:
        assert page.dedupe.clear_group_button.isEnabled() is False
        before = page.dedupe._active_op
        page.dedupe._on_clear_group()
        assert page.dedupe._active_op is before
        qapp.processEvents()
        assert adapter.removed == []
    finally:
        page.dedupe.set_page_busy_provider(lambda: False)


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
    page = _page(main_window, adapter)
    _scan(page.dedupe, qapp)

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

    page.select_dedupe_group(0)                                # the via group
    page.dedupe._on_highlight()
    _pump(qapp, lambda: not page.dedupe.is_running())
    assert asked == [("dedupe/v1", 1.0, 2.0), ("dedupe/v2", 1.0, 2.0)]

    asked.clear()
    page.select_dedupe_group(1)                                # the track group
    page.dedupe._on_highlight()
    _pump(qapp, lambda: not page.dedupe.is_running())
    assert asked == [("dedupe/t1", 0.5, 0.5), ("dedupe/t2", 0.5, 0.5)]

    assert selected == [], "the panel must never select copper on the board"


def test_highlight_does_nothing_without_a_selected_group(main_window, qapp):
    """The button is off when no group node is selected (an empty scan or the
    ROW), and the door refuses from code as well."""
    adapter = _board(duplicates=False)
    page = _page(main_window, adapter)
    _scan(page.dedupe, qapp)
    assert _nodes(page) == []
    assert page.dedupe.highlight_button.isEnabled() is False
    before = page.dedupe._active_op
    page.dedupe._on_highlight()
    assert page.dedupe._active_op is before


# ── Д-2: the panel's own marks never outlive the scan that drew them ─────────

def test_find_takes_the_previous_highlight_off_and_spares_other_spaces(
        main_window, qapp, monkeypatch):
    """Д-2 of the Ч2а acceptance: `NS_DEDUPE` was created and never cleared, so a
    mark stayed on the overlay layer (and in gui_state.json) after the copper it
    described was gone. Find now drops the WHOLE `dedupe` namespace first — and
    ONLY it: a cell anchor's marker and a point's marker on the same layer are
    other consumers' keys (E.2.1) and must survive."""
    adapter = _board()
    removed = []
    monkeypatch.setattr(adapter, "remove_by_ids",
                        lambda uuids: removed.extend(uuids), raising=False)
    monkeypatch.setattr(adapter, "select_items", lambda items: None, raising=False)
    page = _page(main_window, adapter)
    _seed_marks({"dedupe/v1": "marker-v1",
                 "dedupe/v2": "marker-v2",
                 "cell-anchor/root/cell/marker": "marker-cell",
                 "point/p1": "marker-point"})

    _scan(page.dedupe, qapp)

    assert removed == ["marker-v1", "marker-v2"], \
        "the panel's own marks go (and they go to the board, not only the map)"
    assert _owner_keys() == {"cell-anchor/root/cell/marker", "point/p1"}


def test_highlight_replaces_the_previous_groups_marks_before_drawing(
        main_window, qapp, monkeypatch):
    """Д-2: "Highlight" A, then B — A's circles must be OFF the board before B's
    go on. Pinned by ONE event list, so the ORDER is a property too: a "remove
    after draw" would leave both groups marked until the next press."""
    adapter = _board()
    events = []
    monkeypatch.setattr(adapter, "remove_by_ids",
                        lambda uuids: events.append(("remove", tuple(uuids))),
                        raising=False)
    monkeypatch.setattr(adapter, "select_items", lambda items: None, raising=False)

    def fake_ensure_marker(self, adapter_, key, x_mm, y_mm, **_kwargs):
        events.append(("draw", key))
        return "overlay-" + key

    monkeypatch.setattr(overlay_markers.OverlayMarkerOwner, "ensure_marker",
                        fake_ensure_marker)
    page = _page(main_window, adapter)
    _scan(page.dedupe, qapp)
    _seed_marks({"dedupe/v1": "marker-v1", "cell-anchor/root/cell/marker": "marker-cell"})
    events.clear()

    page.select_dedupe_group(1)                                # the track group
    page.dedupe._on_highlight()
    _pump(qapp, lambda: not page.dedupe.is_running())

    assert events[0] == ("remove", ("marker-v1",))
    assert events[1:] == [("draw", "dedupe/t1"), ("draw", "dedupe/t2")]
    assert "cell-anchor/root/cell/marker" in _owner_keys()


@pytest.mark.parametrize("door,left", [("clear", 0), ("clear_group", 1)])
def test_both_clears_take_the_marks_off(main_window, qapp, tmp_path, monkeypatch,
                                        door, left):
    """Д-2: a removal makes every mark of the OLD list a lie — the copper under it
    is gone. Both clearing doors drop the `dedupe` namespace BEFORE they delete
    anything, and again only it.

    TWO things this cell learned the hard way (Д-3 of the acceptance):

    * the ORDER is part of the property, so ONE event list pins it: the marks go
      first, then copper;
    * waiting for `not is_running()` alone is NOT the end of the operation: the
      clear is followed by a deferred AUTO-RESCAN, and a cell that returned in that
      gap let the rescan run inside the NEXT test (`pytest -n 4`: the worker then
      touched a deleted widget — "QPushButton has been deleted"). So the wait is
      the neighbours': the list settles (`group_count()`), which only the rescan's
      own `_finish_scan` does."""
    adapter = _board()
    events = []
    monkeypatch.setattr(adapter, "remove_by_ids",
                        lambda uuids: events.append(("marks", tuple(uuids))),
                        raising=False)
    monkeypatch.setattr(adapter, "select_items", lambda items: None, raising=False)
    original_remove_by_id = adapter.remove_by_id

    def recording_remove_by_id(uuid_str):
        events.append(("copper", uuid_str))
        return original_remove_by_id(uuid_str)

    monkeypatch.setattr(adapter, "remove_by_id", recording_remove_by_id)

    page = _page(main_window, adapter)
    page.dedupe.set_root_path(tmp_path / "root.sexp")
    _scan(page.dedupe, qapp)

    # Seeded AFTER the scan: the scan's own clear would have taken them already.
    _seed_marks({"dedupe/v1": "marker-v1", "point/p1": "marker-point"})
    page.select_dedupe_group(0)
    getattr(page.dedupe, f"_on_{door}")()
    _pump(qapp, lambda: (not main_window.connection.long_op_active
                         and not page.dedupe.is_running()
                         and page.dedupe.group_count() == left))

    marks = [event for event in events if event[0] == "marks"]
    copper = [event for event in events if event[0] == "copper"]
    assert marks == [("marks", ("marker-v1",))], \
        "the panel's own mark went, and nobody else's"
    assert copper, "the clear really did delete copper"
    assert events.index(marks[0]) < events.index(copper[0]), \
        "the marks must go BEFORE the copper they describe"
    assert "marker-v1" not in [uuid for _, uuid in copper]
    assert _owner_keys() == {"point/p1"}
