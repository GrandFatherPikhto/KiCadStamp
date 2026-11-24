# tests/gui/test_read_extra_copper.py
"""GUI cells л–н of plan_2026_11_10_extra_copper_by_selection.

  * л — the dock draws the EXTRA records under their own «Extra copper (n)»
    pseudo-row and leaves the inter-node ones in «Copper (n)»; both rows are the
    same VIEW-only marker, checkable and foldable exactly like the old single one;
  * м — the door is ONE function for two menus: the tree-root item routes the tree
    it was clicked on, and a door called with a tree acts on THAT tree, never on
    the active tab;
  * н — the SELECTION is read on the WORKER (`adapter.get_selected_items`), and
    the records come from `plan_extra_copper`; the door itself touches no board.
"""
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem

from gui.docks.trees_copper_groups import render_copper_groups
from gui.docks.trees_view_helpers import _is_copper_group_item, extra_record_ids

from tests.net.test_extra_copper import _stub_area
from tests.net.test_extra_copper_s0 import _AreaBoard, _cfg, _tree


def _tree_of(*refs):
    return SimpleNamespace(nodes=[SimpleNamespace(kind="net_trace", ref=r,
                                                  children=[])
                                  for r in refs])


# ── л: the rows ───────────────────────────────────────────────────────────

def test_l_the_dock_draws_two_rows_and_keeps_each_owner_apart(qapp):
    widget = QTreeWidget()
    root = QTreeWidgetItem(widget)
    drawn: list[str] = []

    render_copper_groups(
        root, _tree_of("n__a", "extra_rec", "n__b"), copper_expanded=False,
        extra_ids={"extra_rec"},
        render_node=lambda item, node: item.addChild(QTreeWidgetItem([node.ref])))

    rows = [root.child(i) for i in range(root.childCount())]
    assert [row.text(0) for row in rows] == ["Copper (2)", "Extra copper (1)"]
    assert [row.child(0).text(0) for row in rows] == ["n__a", "extra_rec"]
    # the same VIEW-only marker, checkable and tristate, on BOTH rows
    for row in rows:
        assert _is_copper_group_item(row.data(0, Qt.ItemDataRole.UserRole))
        assert row.flags() & Qt.ItemFlag.ItemIsUserCheckable
        assert row.flags() & Qt.ItemFlag.ItemIsUserTristate
        assert row.isExpanded() is False
    assert drawn == []


def test_l_without_extra_records_one_row_is_drawn(qapp):
    widget = QTreeWidget()
    root = QTreeWidgetItem(widget)

    render_copper_groups(
        root, _tree_of("n__a", "n__b"), copper_expanded=True, extra_ids=set(),
        render_node=lambda item, node: item.addChild(QTreeWidgetItem([node.ref])))

    assert root.childCount() == 1
    assert root.child(0).text(0) == "Copper (2)"
    assert root.child(0).isExpanded() is True


def test_l_the_extra_ids_come_from_the_config_records():
    from kicadstamp.config import NetTrace

    extra = NetTrace(net="N", anchor_role="A", name="extra_rec", pads=["A.1"],
                     category="extra")
    plain = NetTrace(net="M", anchor_role="A", name="plain_rec", pads=["A.2"])
    cfg = SimpleNamespace(net_traces=[extra, plain])

    assert extra_record_ids(cfg) == {"extra_rec"}


# ── м: one door, two menus ────────────────────────────────────────────────

def test_m_the_tree_root_menu_routes_its_own_tree_to_the_door():
    from gui.docks.trees_dock import TreesDock

    calls = []
    hub = SimpleNamespace(read_extra_copper=lambda tree: calls.append(tree))
    dock_stub = SimpleNamespace(
        _main_window=SimpleNamespace(_dock_hub=hub))
    clicked = _tree_of()

    TreesDock._tree_menu(dock_stub, "read_extra_copper", clicked)

    assert calls == [clicked]


def test_m_the_door_acts_on_the_tree_it_was_handed(monkeypatch):
    from gui import read_extra_copper as door

    started = []
    monkeypatch.setattr("gui.worker.start_long_op",
                        lambda *a, **k: started.append((a, k)) or "op")
    passed, active = _tree_of(), _tree_of()
    dock = SimpleNamespace(
        _current_tree=lambda: active,
        _warn_read_only_instance=lambda tree: False,
        _cfg=object(), _root_path=__import__("pathlib").Path("config.sexp"),
        _ctx=SimpleNamespace(sheet_names={"A": "S"}),
        _main_window=SimpleNamespace(connection=object()), _active_op=None)

    door.read_extra_copper(dock, trigger=None, tree=passed)

    assert len(started) == 1
    args, kwargs = started[0]
    assert args[5]["tree"] is passed
    assert args[5]["sheet_names"] == {"A": "S"}
    assert kwargs["busy_text"]
    assert dock._active_op == "op"


def test_m_a_read_only_instance_is_refused_before_any_worker(monkeypatch):
    from gui import read_extra_copper as door

    started = []
    monkeypatch.setattr("gui.worker.start_long_op",
                        lambda *a, **k: started.append(a) or "op")
    dock = SimpleNamespace(
        _current_tree=_tree_of,
        _warn_read_only_instance=lambda tree: True,
        _cfg=object(), _root_path=None,
        _ctx=SimpleNamespace(sheet_names={}),
        _main_window=SimpleNamespace(connection=object()), _active_op=None)

    door.read_extra_copper(dock, tree=None)

    assert started == [] and dock._active_op is None


# ── н: the selection is read on the worker ────────────────────────────────

class _WorkerBoard(_AreaBoard):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.selected_reads = 0
        self.closed = False

    def refresh_board(self):
        pass

    def close(self):
        self.closed = True

    def get_selected_items(self):
        self.selected_reads += 1
        return super().get_selected_items()


def test_n_the_worker_reads_the_selection_and_returns_the_plan(monkeypatch):
    from gui import read_extra_copper as door

    board, fps, items = _stub_area()
    board = _WorkerBoard(board._fps, board._pads, tracks=board._tracks,
                         vias=board._vias, selected=items)
    built = []

    def _factory(**kw):
        built.append(kw)
        return board

    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter", _factory)

    result = door.run_read_extra_copper_worker({
        "cfg": _cfg(), "tree": _tree(net_trace_refs=()),
        "config_path": None, "sheet_names": {}, "timeout_ms": 1234})

    assert built and built[0]["timeout_ms"] == 1234
    assert board.selected_reads == 1 and board.closed is True
    assert result["narrowed_to_selection"] is True
    assert len(result["added"]) == 1
    assert result["cfg"].net_traces[0].category == "extra"
    assert result["plan"].area_tracks == 1


def test_n_the_door_itself_never_reads_the_board(monkeypatch):
    """`read_extra_copper` only wires the worker: no adapter is built and no
    board method is called on the UI thread (the `ui_thread_board_read` rule)."""
    from gui import read_extra_copper as door

    monkeypatch.setattr("gui.worker.start_long_op", lambda *a, **k: "op")
    calls = []
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda **kw: calls.append(kw))
    dock = SimpleNamespace(
        _current_tree=_tree_of,
        _warn_read_only_instance=lambda tree: False,
        _cfg=object(), _root_path=__import__("pathlib").Path("config.sexp"),
        _ctx=SimpleNamespace(sheet_names={}),
        _main_window=SimpleNamespace(connection=object()), _active_op=None)

    door.read_extra_copper(dock, tree=None)

    assert calls == []
