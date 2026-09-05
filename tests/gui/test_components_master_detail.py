# tests/gui/test_components_master_detail.py
"""
Components master-detail (2026-09-05, plan components_fieldstool_master_detail):
RoleClusterTreeDock (the "Components" tab of the left dock group) now hosts a
QSplitter — left a QTabWidget (tabs on TOP) with the components tree page and
the shared Pending page, right the embedded fieldstool window. A Pending-table
row click loads that ref into the fieldstool pane and reveals it in the tree.
"""
from unittest.mock import Mock

from PyQt6.QtWidgets import QSplitter, QTabWidget, QWidget

from gui.docks.pending import PendingChangesDock, PendingEdit
from gui.docks.role_cluster_tree import RoleClusterTreeDock


def _make_master_dock(main_window):
    """A directly-constructed master-detail RoleClusterTreeDock with throwaway
    collaborators (unit-level; real_main_window covers the DockHub-wired one)."""
    pending = PendingChangesDock(main_window)
    right = QWidget(main_window)
    dock = RoleClusterTreeDock(
        main_window,
        pending_panel=pending,
        fieldstool_window=right)
    return dock, pending, right


def test_plain_construction_has_no_master_detail(main_window, qapp):
    """Without the collaborators (the way unit tests construct the dock) the
    widget is the plain tree page — the historical layout."""
    dock = RoleClusterTreeDock(main_window)
    assert dock.splitter is None
    assert dock._left_tabs is None
    assert dock.widget() is dock._tree_page


def test_master_detail_layout(main_window, qapp):
    dock, pending, right = _make_master_dock(main_window)
    assert isinstance(dock.splitter, QSplitter)
    assert dock.splitter.count() == 2
    # Right pane = fieldstool window.
    assert dock.splitter.widget(1) is right
    # Left pane = QTabWidget with tabs on TOP, pages = tree page + Pending.
    tabs = dock.splitter.widget(0)
    assert isinstance(tabs, QTabWidget)
    assert tabs.tabPosition() == QTabWidget.TabPosition.North
    assert tabs.count() == 2
    assert tabs.widget(0) is dock._tree_page
    assert tabs.widget(1) is pending


def test_master_detail_missing_pending_yields_single_left_tab(main_window, qapp):
    dock = RoleClusterTreeDock(main_window, fieldstool_window=QWidget(main_window))
    assert dock.splitter is not None
    tabs = dock.splitter.widget(0)
    assert tabs.count() == 1
    assert tabs.widget(0) is dock._tree_page


def test_splitter_and_left_tab_persist_across_restart(main_window, qapp, monkeypatch):
    dock, _pending, _right = _make_master_dock(main_window)
    # An un-shown QSplitter normalizes any setSizes() to its (tiny) current
    # width, so spy sizes() to model a real laid-out splitter position.
    monkeypatch.setattr(dock.splitter, "sizes", lambda: [420, 660])
    dock._left_tabs.setCurrentIndex(1)  # the Pending page
    dock.persist_ui_state()
    # The persisted two-int pixel list + the active tab are written to
    # gui_state.json (same human-readable keys as the Config splitter).
    from gui import settings
    assert settings.state.get("components_splitter_sizes") == [420, 660]
    assert settings.state.get("components_left_tab") == 1

    # A "fresh" dock re-applies exactly the saved sizes/tab on restore.
    # (setSizes is spied rather than compared after the fact: an un-shown
    # QSplitter has no real width, so Qt re-normalizes sizes() afterwards.)
    dock2, _p2, _r2 = _make_master_dock(main_window)
    applied = []
    monkeypatch.setattr(dock2.splitter, "setSizes", lambda s: applied.append(list(s)))
    dock2.restore_ui_state()
    assert applied == [[420, 660]]
    assert dock2._left_tabs.currentIndex() == 1


def test_pending_row_click_emits_ref_activated(main_window, qapp):
    dock = PendingChangesDock(main_window)
    dock.set_edits([PendingEdit("R1", "Role", "OLD", "NEW")])
    picked = []
    dock.ref_activated.connect(picked.append)
    dock._on_cell_clicked(0, 0)
    assert picked == ["R1"]


def test_pending_row_without_ref_emits_nothing(main_window, qapp):
    dock = PendingChangesDock(main_window)
    picked = []
    dock.ref_activated.connect(picked.append)
    dock._on_cell_clicked(0, 0)  # empty table — no ref
    assert picked == []


def test_pending_ref_activated_loads_fieldstool_and_reveals_tree(real_main_window, monkeypatch):
    """DockHub wiring: a Pending row click routes into the embedded fieldstool
    window (pick_leaf -> _on_tree_leaf_picked) AND reveals the ref in the
    Components tree (plan components_fieldstool_master_detail)."""
    hub = real_main_window._dock_hub
    window = hub.fieldstool_dock.window
    leaf_picked = Mock()
    reveal = Mock()
    monkeypatch.setattr(window, "_on_tree_leaf_picked", leaf_picked)
    monkeypatch.setattr(hub.tree_dock, "reveal_ref", reveal)
    hub.pending_dock.ref_activated.emit("R1")
    leaf_picked.assert_called_once_with(["R1"])
    reveal.assert_called_once_with("R1")


def test_pending_dock_is_injected_into_fieldstool_window(real_main_window):
    """The one shared PendingChangesDock instance is BOTH the Components dock's
    left tab page AND what the embedded fieldstool window pushes edits to."""
    hub = real_main_window._dock_hub
    assert hub.fieldstool_dock.window.pending_dock is hub.pending_dock
