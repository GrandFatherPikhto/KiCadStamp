# tests/gui/test_role_list_freshness.py
"""S.4 gates for plan_2026_09_11_stale_snapshot_role_lists.md — the Role/Cluster
lists must not freeze at connect time (K.2 #1/#2/#9).

``MainWindow._poll``'s automatic tick is a deliberate no-op once connected, so
``BoardConnection.snapshot`` — the ONE source of every Role/Cluster/NET
suggestion list — kept the board's connect-time values until the user hit
Refresh. Two triggers now provide freshness at the point of use, both through
``DockHub.refresh_snapshot_and_push`` (a worker-thread rebuild, then the
existing distribution; never a direct adapter call on the UI thread — the
Commit H hang):

  * T1 — a TREE dialog opening (TreesDock reads its candidates lazily there);
  * T2 — a Config right-QView page switch (the docks whose combos live on
    those pages).

These tests pin down: the fresh value really reaches the lists, the rebuild
happens EXACTLY once per trigger (not once per dock) and never on the UI
thread, and the offline session keeps the previous behaviour (empty lists,
free-text input, no crash).
"""
import threading
from types import SimpleNamespace

from PyQt6.QtWidgets import QDialog, QSplitter

import gui.docks.trees_dock as trees_dock_mod
from gui.docks.trees_dock import AnchorFormWidget
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.trees import Tree
from tests.gui.conftest import _pump


def _row(ref, role, cluster):
    """A Selected-like row: only role/cluster/ref matter to the lists."""
    return SimpleNamespace(ref=ref, role=role, cluster=cluster)


class _LiveConnection:
    """A BoardConnection stand-in with a LIVE board behind it: ``refresh()``
    swaps the frozen snapshot for the next one, counting the rebuilds and
    recording the thread each ran on."""

    def __init__(self, snapshots):
        self.board = SimpleNamespace(adapter=object(), refresh=lambda: None)
        self._pending = [list(s) for s in snapshots]
        self.snapshot = self._pending.pop(0)
        self.long_op_active = False
        self.refresh_calls = 0
        self.refresh_threads = []

    def refresh(self):
        self.refresh_calls += 1
        self.refresh_threads.append(threading.current_thread().name)
        if self._pending:
            self.snapshot = self._pending.pop(0)
        return None


def _combo_items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_page_switch_rebuilds_once_and_refreshes_the_dock_lists(
        qapp, real_main_window):
    """T2/S.4 #1 + #3 + #5 (K.2 #1) — the Config-page combos used to show the
    roles/clusters of connect time: a page switch rebuilds the snapshot
    EXACTLY once, on the worker thread, and the values added on the board
    since the connection reach the docks' lists."""
    hub = real_main_window._dock_hub
    connection = _LiveConnection([[_row("R1", "OLD_ROLE", "CL1")],
                                 [_row("R1", "OLD_ROLE", "CL1"),
                                  _row("R2", "NEW_ROLE", "CL2")]])
    real_main_window.connection = connection
    hub.placer_dock.refresh_known_roles(connection.snapshot)  # the connect state
    assert "CL2" not in _combo_items(hub.placer_dock.cluster_edit)
    hub._config_right_page_index = 0                          # a real switch

    hub._on_config_right_page_changed(3)

    assert connection.long_op_active          # the rebuild owns the socket now
    _pump(qapp, lambda: not connection.long_op_active)

    # S.4 #3 — ONE rebuild for the whole trigger, not one per dock.
    assert connection.refresh_calls == 1
    # S.4 #5 — and it never ran on the UI thread.
    assert connection.refresh_threads[0] != threading.main_thread().name
    # S.4 #1 — the new known values reached the docks' combos.
    assert "CL2" in _combo_items(hub.placer_dock.cluster_edit)
    # ...and the tree dock's own Tag lists (S.3.2: it is in the distribution now).
    hub.tree_dock._connection = connection    # the dock's own reference
    hub.tree_dock.refresh_known_lists()
    assert "NEW_ROLE" in _combo_items(hub.tree_dock.tag_role_combo)


def test_tree_node_dialog_opens_with_the_refreshed_candidates(
        qapp, real_main_window, monkeypatch):
    """T1/S.4 #2 + #3 (K.2 #2) — the node dialog builds its Role/Cluster
    candidate lists at open time; the flow rebuilds the snapshot first (once,
    off the UI thread), so a role tagged on the board after connecting is
    offered in the dialog that opens next."""
    hub = real_main_window._dock_hub
    dock = hub.trees_dock
    connection = _LiveConnection([[_row("R1", "OLD_ROLE", "CL1")],
                                 [_row("R1", "OLD_ROLE", "CL1"),
                                  _row("R2", "NEW_ROLE", "CL2")]])
    real_main_window.connection = connection
    tree = Tree(name="T", anchor=None, nodes=[])
    dock._trees = [tree]

    captured = {}

    class _FakeNodeDialog:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def exec(self):
            return QDialog.DialogCode.Rejected     # cancel — nothing is staged

    monkeypatch.setattr(trees_dock_mod, "_NodeDialog", _FakeNodeDialog)

    dock._add_node_flow(tree)
    assert connection.long_op_active
    _pump(qapp, lambda: not connection.long_op_active)

    assert connection.refresh_calls == 1               # S.4 #3
    assert connection.refresh_threads[0] != threading.main_thread().name
    assert "NEW_ROLE" in captured["role_candidates"]   # S.4 #2
    assert "CL2" in captured["cluster_candidates"]


def test_embedded_anchor_form_refreshes_its_candidates_in_place(
        qapp, real_main_window, tmp_path):
    """S.3.2/T1 + P.5.2 (plan_2026_09_11_pivot_ref_mount_ancestor) — the EMBEDDED
    tree forms are long-lived pages, so their Role/Cluster suggestion combos are
    refreshed IN PLACE: the list gains the value added since the connection and
    an in-progress typed value survives (nothing is rebuilt, so nothing staged is
    lost).

    This test walks the PROD path: a real tree page (a QSplitter holding the tree
    and its form panel) built by the dock itself, NOT a bare form wrapper stacked
    into tree_tabs. The old test stacked dock._form_action_row(form) directly as
    a tab, so it passed against a shape the running app never produces — while
    refresh_known_lists, which passed the splitter straight to _embedded_form_of,
    found no form and never called set_candidates (P.3)."""
    hub = real_main_window._dock_hub
    dock = hub.trees_dock
    connection = _LiveConnection([[_row("R1", "OLD_ROLE", "CL1")]])
    real_main_window.connection = connection
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"trees": [
        {"name": "T", "anchor": {"role": "OLD_ROLE", "cluster": "CL1"}}]}),
        encoding="utf-8")
    dock.set_root_file(root)                         # builds the REAL pages

    # The PROD shape: a QSplitter page, not a bare wrapper. The old code passed
    # this splitter to _embedded_form_of and got None back.
    page = dock.tree_tabs.widget(0)
    assert isinstance(page, QSplitter)
    assert dock._embedded_form_of(page) is None

    form = dock._embedded_form_of(dock._panel_page(dock._active_form_panel()))
    assert isinstance(form, AnchorFormWidget)        # the anchor form really on screen
    form.role_edit.setCurrentText("HALF_TYPED")      # the user is mid-edit
    connection.snapshot = [_row("R1", "OLD_ROLE", "CL1"),
                           _row("R2", "NEW_ROLE", "CL2")]

    dock.refresh_known_lists()

    assert "NEW_ROLE" in _combo_items(form.role_edit)   # P.5.2 item 7
    assert "CL2" in _combo_items(form.cluster_edit)
    # P.5.2 item 8 — populate, don't restrict: the typed draft survives.
    assert form.role_edit.currentText() == "HALF_TYPED"


def test_tree_dialog_opens_before_the_cross_dock_distribution(
        qapp, real_main_window, monkeypatch):
    """Э5 (plan_2026_09_12_combo_refresh_deadlock.md) — the tree dialog takes
    its candidates from the rebuilt SNAPSHOT (`_live_roles`/`_live_clusters`)
    and from the config graph, never from the neighbouring docks' combos, so
    the eight-dock distribution must not sit between the user's click and the
    dialog. Before this, "Add node" ran every dock's combo repopulation first —
    the caught freeze hung inside NetTraceDock's role combo, a dock unrelated
    to adding a node."""
    hub = real_main_window._dock_hub
    dock = hub.trees_dock
    connection = _LiveConnection([[_row("R1", "OLD_ROLE", "CL1")],
                                  [_row("R1", "OLD_ROLE", "CL1"),
                                   _row("R2", "NEW_ROLE", "CL2")]])
    real_main_window.connection = connection
    tree = Tree(name="T", anchor=None, nodes=[])
    dock._trees = [tree]

    timeline = []
    captured = {}

    class _FakeNodeDialog:
        def __init__(self, *args, **kwargs):
            timeline.append("dialog_opened")
            captured.update(kwargs)

        def exec(self):
            return QDialog.DialogCode.Rejected     # cancel — nothing is staged

    monkeypatch.setattr(trees_dock_mod, "_NodeDialog", _FakeNodeDialog)
    monkeypatch.setattr(hub, "push_known_lists",
                        lambda *a, **k: timeline.append("push_known_lists"))

    dock._add_node_flow(tree)
    _pump(qapp, lambda: not connection.long_op_active)

    assert timeline == ["dialog_opened", "push_known_lists"]
    # The dialog's candidates are the FRESH snapshot values, i.e. they do not
    # depend on the (now later) dock distribution.
    assert "NEW_ROLE" in captured["role_candidates"]
    assert "CL2" in captured["cluster_candidates"]


def test_offline_trigger_keeps_the_previous_behaviour(
        qapp, real_main_window, monkeypatch):
    """S.4 #4 — without a live board there is nothing to rebuild: the trigger
    is a silent no-op (no dock churn at all, no crash), and the tree dialog
    still opens on the empty cached snapshot — the combos stay searchable
    free-text pickers ("populate, don't restrict")."""
    hub = real_main_window._dock_hub
    dock = hub.trees_dock
    offline = SimpleNamespace(board=None, snapshot=[], long_op_active=False)
    real_main_window.connection = offline
    tree = Tree(name="T", anchor=None, nodes=[])
    dock._trees = [tree]
    pushed = []
    monkeypatch.setattr(hub, "push_known_lists",
                        lambda *a, **k: pushed.append(a))

    hub._on_config_right_page_changed(4)       # T2 — must do nothing at all
    assert pushed == []

    captured = {}

    class _FakeNodeDialog:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(trees_dock_mod, "_NodeDialog", _FakeNodeDialog)
    dock._add_node_flow(tree)                  # T1 — opens at once, empty lists

    assert captured["role_candidates"] == []
    assert captured["cluster_candidates"] == []
    assert pushed == []                        # still nothing pushed offline
