# tests/gui/test_dock_sizing.py
"""S.1/S.2/S.3 — the three sizing complaints of techdocs/me/scroll.md (plan
prompt_2026_09_10_splitters_and_log_sizing.md):

* S.1 the Log dock cannot be shrunk: the left-area docks sat at their content
  minimums with a `Preferred` vertical policy, so shrinking the log freed height
  that nothing above accepted.
* S.2 the Log could not be grown enough to push the upper pages into a scroll:
  `QStackedWidget.minimumSizeHint()` is the MAX over all pages, so one tall
  HIDDEN page floored the whole Config dock.
* S.3 the splitters persisted `[0, 0]`: a hidden splitter reports `[0, 0]`, and
  the value was written unconditionally on quit, snapping the divider left.

The measurements these tests encode are the ones the probes in `diagnostics/`
printed before/after the fix.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QFrame, QScrollArea, QSizePolicy, QTreeWidget,
                             QWidget)

from gui import settings
from gui.docks._common import SplitterSizeKeeper
from gui.docks.config_tree import ConfigTreeDock
from gui.docks.trees_dock import TreesDock


class _StubSplitter(QObject):
    """Minimal QSplitter stand-in whose `sizes()` the test controls outright.

    A real hidden splitter reports `[0, 0]`, which is awkward to reproduce in a
    unit test (you would have to show, hide and wait for Qt to re-lay-out), so
    the keeper is fed exactly the values under test."""

    splitterMoved = pyqtSignal(int, int)

    def __init__(self, sizes, count: int = 2, collapsible: bool = False):
        super().__init__()
        self._sizes = list(sizes)
        self._count = count
        self._collapsible = collapsible

    def sizes(self) -> list:
        return list(self._sizes)

    def count(self) -> int:
        return self._count

    def childrenCollapsible(self) -> bool:
        return self._collapsible


# ── T: the CENTRAL tab widget is the elastic centre ────────────────────────

def test_central_widget_is_the_elastic_tab_group(real_main_window):
    """Task T: the Components/Config/Trees group is the window's central
    QTabWidget — the elastic element that was MISSING while they were docks.
    With centralWidget() == None QMainWindow handed every spare pixel to the
    Log dock regardless of the docks' size policies (S.1 measured exactly
    that), so neither resizeDocks() nor a separator drag could shrink it."""
    hub = real_main_window._dock_hub
    central = real_main_window.centralWidget()
    assert central is hub.left_tabs
    assert central.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
    # 1, not 0 (Qt's "unset" sentinel), so the log can be grown past the tabs'
    # content height — the pages inside then scroll.
    assert central.minimumHeight() == 1


def test_the_three_left_widgets_are_central_tabs(real_main_window):
    """They are pages of the central QTabWidget now, not docks (task T); only
    the Log is still a real top-level dock."""
    hub = real_main_window._dock_hub
    assert hub.left_tabs.count() == 3
    assert hub.left_tabs.widget(0) is hub.tree_dock
    assert hub.left_tabs.widget(1) is hub.config_tree_dock
    assert hub.left_tabs.widget(2) is hub.trees_dock
    assert hub.left_tabs.tabPosition() == hub.left_tabs.TabPosition.South
    assert hub.docks == [hub.log_dock]


def test_show_left_page_brings_a_central_tab_to_the_front(real_main_window):
    """show_left_page() replaces the old dock show()/raise_() pair."""
    hub = real_main_window._dock_hub
    hub.show_left_page(hub.config_tree_dock)
    assert hub.left_tabs.currentWidget() is hub.config_tree_dock
    hub.show_left_page(hub.trees_dock)
    assert hub.left_tabs.currentWidget() is hub.trees_dock


def test_log_dock_is_not_expanding(real_main_window):
    """The log already receives all the slack — making IT expanding would
    harden the very defect being fixed."""
    log = real_main_window._dock_hub.log_dock
    assert (log.sizePolicy().verticalPolicy()
            != QSizePolicy.Policy.Expanding)
    assert (log.widget().sizePolicy().verticalPolicy()
            != QSizePolicy.Policy.Expanding)


def test_shrinking_the_log_grows_the_central_widget(real_main_window):
    """T acceptance by measurement: forcing the log small must hand the height
    to the central tab group. Before T nothing grew — the log absorbed 100% of
    every window resize (measured 509 -> 309 -> 230 for windows 1000/800/700)."""
    win = real_main_window
    log = win._dock_hub.log_dock
    win.resize(1900, 1000)
    win.show()
    QTest.qWaitForWindowExposed(win)
    QTest.qWait(80)

    central = win.centralWidget()
    before = central.height()
    try:
        log.setMaximumHeight(200)
        QTest.qWait(80)
        after = central.height()
    finally:
        log.setMaximumHeight(16777215)
        QTest.qWait(40)
    assert after > before, (
        f"shrinking the log did not grow the central tab group "
        f"({before} -> {after})")


def test_resize_docks_actually_resizes_the_log(real_main_window):
    """T acceptance: resizeDocks([log], [200]) used to be silently IGNORED (the
    log stayed at its content-driven height); with an elastic centre it is
    honoured exactly."""
    win = real_main_window
    log = win._dock_hub.log_dock
    win.resize(1900, 1000)
    win.show()
    QTest.qWaitForWindowExposed(win)
    QTest.qWait(80)

    win.resizeDocks([log], [200], Qt.Orientation.Vertical)
    QTest.qWait(80)
    assert log.height() == 200


def test_log_height_is_stable_across_window_resizes(real_main_window):
    """T acceptance: with an elastic centre the log HOLDS its height while the
    window is resized (it used to move 1:1 with the window)."""
    win = real_main_window
    log = win._dock_hub.log_dock
    win.resize(1900, 1000)
    win.show()
    QTest.qWaitForWindowExposed(win)
    QTest.qWait(80)

    heights = []
    for h in (1000, 800, 700):
        win.resize(1900, h)
        QTest.qWait(70)
        heights.append(log.height())
    assert len(set(heights)) == 1, f"log height moved with the window: {heights}"


# ── S.2: the right stack must stop flooring the Config dock ────────────────

def test_right_stack_minimum_height_is_capped(real_main_window):
    """S.2: `right_stack.minimumSizeHint().height()` was the MAX over all
    pages (495 px, held by the hidden ThermalViaArrayDock) — it must collapse."""
    stack = real_main_window._dock_hub.config_tree_dock.right_stack
    assert stack.minimumSizeHint().height() <= 5


def test_config_dock_height_floor_drops(real_main_window):
    """S.2 acceptance: with the stack floor gone the Config dock's own minimum
    drops from 522 to tens of pixels."""
    dock = real_main_window._dock_hub.config_tree_dock
    assert dock.minimumSizeHint().height() < 200


def test_right_pages_are_wrapped_in_a_min_height_one_scroll_area(main_window):
    """S.2: every page is wrapped with all four settings — widgetResizable,
    an EXPLICIT minimum height of 1 (Qt treats 0 as "unset"), NoFrame and
    as-needed bars. The dock still hands back the ORIGINAL page."""
    dock = ConfigTreeDock(main_window)
    page = QWidget()
    index = dock.add_right_page(page)

    area = dock.right_stack.widget(index)
    assert isinstance(area, QScrollArea)
    assert area.widgetResizable() is True
    assert area.minimumHeight() == 1
    assert area.frameShape() == QFrame.Shape.NoFrame
    assert area.widget() is page
    # The public accessors unwrap, so callers keep getting their own dock.
    assert dock.right_page_at(index) is page


def test_wrapping_does_not_grow_the_page_minimum_width(main_window):
    """S.2 gotcha: a plain QScrollArea reports a small content-INDEPENDENT
    minimum width, which shrank the whole left dock area. The wrap must be
    horizontally transparent — its width floor is the page's own."""
    dock = ConfigTreeDock(main_window)
    page = QWidget()
    index = dock.add_right_page(page)
    area = dock.right_stack.widget(index)
    assert (area.minimumSizeHint().width()
            == page.minimumSizeHint().width())


def test_self_scrolling_page_is_not_wrapped(main_window):
    """S.2 gotcha: nesting a scroll area around a widget that already scrolls
    (QPlainTextEdit/tree/table) is the project's documented anti-pattern."""
    dock = ConfigTreeDock(main_window)
    tree = QTreeWidget()
    index = dock.add_right_page(tree)
    assert dock.right_stack.widget(index) is tree
    assert dock.right_page_at(index) is tree


def test_squeezing_the_centre_makes_the_config_page_scroll(real_main_window):
    """S.2 becomes the behaviour Denis asked for only once the centre can be
    squeezed (task T): with the central tab group forced small, a tall Config
    right page must SCROLL — not be clipped, and not force the whole dock tall
    (its wrap's minimum height is 1)."""
    win = real_main_window
    hub = win._dock_hub
    hub.show_left_page(hub.config_tree_dock)
    win.resize(1900, 1000)
    win.show()
    QTest.qWaitForWindowExposed(win)
    QTest.qWait(80)

    dock = hub.config_tree_dock
    page = QWidget()
    page.setMinimumHeight(600)
    index = dock.add_right_page(page)
    dock.set_current_page(index)
    QTest.qWait(60)
    area = dock.right_stack.widget(index)
    assert area.minimumSizeHint().height() == 1

    central = win.centralWidget()
    central.setMaximumHeight(120)
    try:
        QTest.qWait(150)
        assert central.height() <= 150       # the centre really was squeezed
        assert area.verticalScrollBar().maximum() > 0
    finally:
        central.setMaximumHeight(16777215)
        QTest.qWait(60)


# ── S.3: degenerate splitter sizes must never be persisted ─────────────────

def test_keeper_ignores_a_degenerate_hidden_read():
    """S.3: a hidden splitter reports [0, 0]; the keeper keeps the last good
    value instead of adopting it."""
    splitter = _StubSplitter([0, 0])
    keeper = SplitterSizeKeeper(splitter)
    assert keeper.capture() is None          # nothing good seen yet

    splitter._sizes = [70, 486]              # laid out / visible
    assert keeper.capture() == [70, 486]

    splitter._sizes = [0, 0]                 # tabbed away at quit
    assert keeper.capture() == [70, 486]


def test_keeper_rejects_a_zero_pane_when_not_collapsible():
    """A non-collapsible splitter can never legally hold a zero pane."""
    keeper = SplitterSizeKeeper(_StubSplitter([0, 300], collapsible=False))
    assert keeper.capture() is None


def test_keeper_allows_a_declared_collapsed_pane_when_collapsible():
    """A user MAY legally collapse one pane of a collapsible splitter — that
    is not the "hidden widget" all-zero report and must be kept."""
    keeper = SplitterSizeKeeper(_StubSplitter([0, 300], collapsible=True))
    assert keeper.capture() == [0, 300]


def test_config_persist_keeps_the_last_good_size_when_hidden(main_window,
                                                             monkeypatch):
    """S.3 (Config): once a good split is known, a quit while the dock is
    hidden must not overwrite it with [0, 0]."""
    dock = ConfigTreeDock(main_window)
    dock.add_right_page(QWidget())
    dock.splitter.resize(1200, 500)
    dock.splitter.setSizes([300, 900])
    # setSizes() clamps to the children's minimums, so read back what the
    # splitter actually holds instead of the requested pair.
    saved = list(dock.splitter.sizes())
    dock.persist_ui_state()
    assert settings.state.get("config_splitter_sizes") == saved

    # The dock hides at quit -> sizes() reports [0, 0].
    monkeypatch.setattr(dock._splitter_sizes, "_splitter", _StubSplitter([0, 0]))
    dock.persist_ui_state()
    assert settings.state.get("config_splitter_sizes") == saved


def test_config_persist_writes_nothing_when_no_good_size_was_seen(
        main_window, monkeypatch):
    """S.3: with no good size ever observed the previously saved value is left
    untouched (nothing is written)."""
    dock = ConfigTreeDock(main_window)
    dock.add_right_page(QWidget())
    settings.state.set("config_splitter_sizes", [111, 222])
    monkeypatch.setattr(dock._splitter_sizes, "_splitter", _StubSplitter([0, 0]))
    dock.persist_ui_state()
    assert settings.state.get("config_splitter_sizes") == [111, 222]


def test_trees_dock_returns_the_remembered_page_size(main_window):
    """S.3 (Trees): a page splitter that has been laid out yields its
    remembered good size."""
    dock = TreesDock(main_window)
    splitter = _StubSplitter([120, 300])
    keeper = SplitterSizeKeeper(splitter)
    keeper.capture()
    dock._page_splitter_keepers["T"] = keeper
    assert dock._good_page_splitter_sizes("T", splitter) == [120, 300]


def test_trees_dock_hidden_page_yields_no_size(main_window):
    """S.3 (Trees): a NEVER laid-out page (every non-active tab is hidden) has
    no good size — the caller then keeps the previously saved value instead of
    writing [0, 0]."""
    dock = TreesDock(main_window)
    splitter = _StubSplitter([0, 0])
    dock._page_splitter_keepers["T"] = SplitterSizeKeeper(splitter)
    assert dock._good_page_splitter_sizes("T", splitter) is None
