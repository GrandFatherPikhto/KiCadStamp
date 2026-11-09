# tests/gui/docks/test_trees_instances_follow_template.py
"""A template's `tree_instances:` tabs follow it without reloading the trees section.

Plan plan_2026_11_09_tree_instances_follow_template (09.11, live on HP-Power-v099): Denis
deleted a node from `switch_a`; `switch_b`'s tab kept it (and its Entity copy) until the whole
section was reloaded. The expansion was right — `load_config` reads the staged working set —
only the dock's buffer lagged, because copies are generated at LOAD and nothing but the docks'
`save` signals re-read them.

Every cell drives a REAL door (the delete flow, `_mark_dirty`, a dock's `saved` signal) and
then runs the debounce timer's own tail (`InstancesRefresh.flush`) — no cell waits 300 ms, and
no cell pokes the internals the refresh is supposed to use.
"""
from __future__ import annotations

import logging

import pytest

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.trees import TreeNode

from gui.docks.trees_dock import TreesDock

from tests.fakes.write_later import write_later
from tests.gui.docks.test_trees_dock import _dirty_instance_cfg

TEMPLATE = "dac_buf_tpl"
CLONE = "ch1_dac_buf"


@pytest.fixture(autouse=True)
def _qt_app(qapp):
    """A QApplication must exist even when a cell runs FIRST in a worker (SIGABRT
    otherwise) — the same guard tests/gui/docks/test_trees_dock.py carries."""
    return qapp


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """У3.5 A, class (в) — copied from tests/gui/docks/test_trees_dock.py: the format-3
    writer resolves a reference's UUID against the ACTIVE GRAPH ROOT, and these cells write
    self-contained configs, so the root is a path that does not exist (the stamp indexes THIS
    write's own records)."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)


@pytest.fixture
def hub(main_window):
    """A real DockHub — cell «в» has to prove the WIRING of a dock's `saved` signal, not a
    direct call. Cleans up the Log dock exactly like tests/gui/docks/test_imprint_place_gui.py."""
    from gui.dock_hub import DockHub

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    yield hub
    hub.log_dock.remove_handler()
    if hub._log_file_handler is not None:
        logging.getLogger().removeHandler(hub._log_file_handler)
        hub._log_file_handler.close()


def _write(root, data) -> None:
    """`write_later`, not a bare `write_text`: the product's readers are cached by
    `(path, mtime_ns)`, and a cell that REWRITES the same file inside one test must not measure
    the cache (caught by the `--coarse-mtime` leg, which coarsens exactly that stamp)."""
    write_later(root, dict_to_sexp(data, format_number=2))


def _dock(main_window, tmp_path, data=None):
    root = tmp_path / "inst.sexp"
    _write(root, data if data is not None else _dirty_instance_cfg())
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    return dock, root


def _tree(dock, name):
    return next(t for t in dock._trees if t.name == name)


def _refresh(dock) -> bool:
    """The timer's own tail: what a 300 ms quiet period would have run."""
    return dock._instances_refresh.flush()


def _run_for(ms: int) -> None:
    """Let the REAL event loop run for `ms` — a QTimer only fires there, and two cells are
    about the timer itself (its restart on a new edit, and its re-arm over a long operation)."""
    import time

    from PyQt6.QtWidgets import QApplication

    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def _node(ref, xy=(0.0, 1.0)):
    return TreeNode(ref=ref, kind="placement", xy=xy, polar=None, rotation=0.0,
                    name=None, group=None)


def test_a_deleted_template_node_leaves_the_clone_at_once(main_window, tmp_path):
    """Cell а: the delete goes through the dock's own flow (`run_delete_nodes`). The clone
    loses the node and its `_node_items` row, `cfg.entities` loses the copy — and the refresh
    writes NOTHING: the file it finds is the file it leaves. A hand-written tree keeps its
    very object."""
    dock, root = _dock(main_window, tmp_path)
    template = _tree(dock, TEMPLATE)
    victim = next(n for n in template.nodes[0].children if n.ref == "pif_avdd")
    manual = _tree(dock, "manual")
    assert "pif_avdd__ch1_dac_buf" in dock._node_items

    dock._delete_node_flow(template, victim)
    staged = root.read_text(encoding="utf-8")     # the dock's OWN staging, not the refresh
    assert _refresh(dock) is True

    assert [n.ref for n in _tree(dock, CLONE).nodes[0].children] == []
    assert "pif_avdd__ch1_dac_buf" not in dock._node_items
    assert "pif_avdd__ch1_dac_buf" not in {e.name for e in dock._cfg.entities}
    assert root.read_text(encoding="utf-8") == staged, "the refresh must not write"
    assert _tree(dock, "manual") is manual


def test_an_added_template_node_arrives_with_its_geometry(main_window, tmp_path):
    """Cell б: a node added to the template (and staged by `_mark_dirty`) shows up in the
    clone with the template's OWN geometry — the property is «the copy follows», so the
    assertion compares with the template's node rather than hard-coding a transform."""
    dock, _root = _dock(main_window, tmp_path)
    template = _tree(dock, TEMPLATE)
    template.nodes[0].children.append(_node("extra_sw", xy=(3.5, -4.5)))
    dock._mark_dirty()

    assert _refresh(dock) is True

    source = next(n for n in template.nodes[0].children if n.ref == "extra_sw")
    copy = next(n for n in _tree(dock, CLONE).nodes[0].children
                if n.ref == "extra_sw__ch1_dac_buf")
    assert copy.xy == source.xy
    assert "extra_sw__ch1_dac_buf" in dock._node_items


def test_an_entity_cluster_rename_reaches_the_copy(hub, tmp_path):
    """Cell в: the Entity PAGE is another dock, and its `saved` is what `dock_hub` wires to
    the refresh. Renaming a cluster there must reach the copy's Entity without anyone
    reloading the trees section."""
    root = tmp_path / "hub.sexp"
    data = _dirty_instance_cfg()
    _write(root, data)
    hub.trees_dock.set_root_file(root)
    assert "pif_avdd__ch1_dac_buf" in {e.name for e in hub.trees_dock._cfg.entities}

    for entity in data["entities"]:
        if entity["name"] == "pif_avdd":
            entity["cluster"] = "PIF_RENAMED"
    _write(root, data)

    hub.entity_dock.saved.emit()
    assert hub.trees_dock._instances_refresh._timer.isActive(), (
        "another dock's `saved` must reach the refresh through the hub's wiring")
    assert hub.trees_dock._instances_refresh.flush() is True

    copies = {e.name: e for e in hub.trees_dock._cfg.entities}
    assert copies["pif_avdd__ch1_dac_buf"].cluster == "PIF_RENAMED"


def test_a_refused_expansion_keeps_the_clones_and_says_it_once(
        main_window, tmp_path, monkeypatch):
    """Cell г: a template caught mid-edit (a node naming no entity) makes the expansion
    refuse. The clones stay EXACTLY as they were, ONE yellow line names the refusal, the same
    sentence twice in a row adds nothing — and the next good expansion restores everything by
    itself."""
    import gui.docks.trees_instances_refresh as mod

    said = []
    monkeypatch.setattr(mod, "show_message",
                        lambda text, style="", log=None: said.append(text))
    dock, root = _dock(main_window, tmp_path)

    broken = _dirty_instance_cfg()
    broken["trees"][0]["nodes"][0]["children"].append(
        {"ref": "no_such_entity", "kind": "placement", "xy": [0.0, 0.0]})
    _write(root, broken)

    assert _refresh(dock) is False
    assert len(said) == 1, said
    assert [t.name for t in dock._trees] == [TEMPLATE, "manual", CLONE]
    assert "pif_avdd__ch1_dac_buf" in dock._node_items

    assert _refresh(dock) is False                 # the same refusal again
    assert len(said) == 1, "the same sentence twice in a row is noise"

    _write(root, _dirty_instance_cfg(extra_template_node=True))
    assert _refresh(dock) is True
    assert "extra_sw__ch1_dac_buf" in dock._node_items


def test_five_edits_are_one_config_read(main_window, tmp_path, monkeypatch):
    """Cell д: the single shot restarts on every event, so a burst of `_mark_dirty` calls
    costs ONE `load_config` — not five, and not one per keystroke."""
    import gui.docks.trees_instances_sync as sync

    real = sync.load_config
    reads = []
    monkeypatch.setattr(sync, "load_config", lambda path: reads.append(path) or real(path))
    dock, _root = _dock(main_window, tmp_path)
    reads.clear()                                  # the initial load is not the measurement

    for _ in range(5):
        dock._mark_dirty()
    assert dock._instances_refresh._timer.isActive(), "the edit must ARM the refresh"
    assert _refresh(dock) is True

    assert len(reads) == 1, reads


def test_without_declarations_nothing_is_read_and_no_timer_runs(main_window, tmp_path,
                                                                monkeypatch):
    """Cell е: a project that uses no clones pays nothing — `note_change` arms no timer and
    the config is never read."""
    import gui.docks.trees_instances_sync as sync

    real = sync.load_config
    reads = []
    monkeypatch.setattr(sync, "load_config", lambda path: reads.append(path) or real(path))
    data = _dirty_instance_cfg()
    data.pop("tree_instances")
    dock, _root = _dock(main_window, tmp_path, data)
    reads.clear()

    dock._mark_dirty()

    assert dock._instances_refresh._timer.isActive() is False
    assert _refresh(dock) is False
    assert reads == []


def test_a_template_page_and_its_open_form_survive_a_refresh(main_window, tmp_path,
                                                             monkeypatch):
    """Cell ж: the refresh re-renders the CLONE tabs only. The template's page object, the
    form open inside it and the active tab must all be the same ones afterwards — that is the
    whole reason `_rebuild_tabs` (which accepts and closes every form) is not used.

    The `_rebuild_tabs` calls are counted as well: measured while writing this cell, the
    identity assertions ALONE let a mutant that calls the full rebuild slip through (a direct
    `_rebuild_tabs()` does change both objects — probes agreed — yet the mutant stayed green),
    so the DECISION «not the full rebuild» is pinned where it is made."""
    dock, _root = _dock(main_window, tmp_path)
    rebuilt = []
    monkeypatch.setattr(dock, "_rebuild_tabs", lambda: rebuilt.append(1))
    template = _tree(dock, TEMPLATE)
    index = next(i for i, t in enumerate(dock._trees) if t.name == TEMPLATE)
    dock.tree_tabs.setCurrentIndex(index)
    dock._rebuild_active_form_panel()
    page = dock.tree_tabs.widget(index)
    form = dock._embedded_form_of(dock._panel_page(dock._form_panel_of_page(page)))
    assert form is not None, "the template page must have a form to protect"

    template.nodes[0].children.append(_node("extra_sw"))
    dock._mark_dirty()
    assert _refresh(dock) is True

    assert rebuilt == [], "a refresh must NOT rebuild every tab"
    assert dock.tree_tabs.widget(index) is page
    assert dock._embedded_form_of(dock._panel_page(dock._form_panel_of_page(page))) is form
    assert dock._current_tab_tree_name() == TEMPLATE


def test_a_running_operation_defers_the_refresh_and_does_not_lose_it(main_window, tmp_path):
    """Cell з: a socket-holding operation owns the dock. The refresh does not run DURING it —
    and it is not dropped either: the timer re-arms itself and fires once the operation is
    over, with no second `_mark_dirty` and no explicit flush."""
    dock, _root = _dock(main_window, tmp_path)
    template = _tree(dock, TEMPLATE)
    template.nodes[0].children.append(_node("extra_sw"))
    dock._active_op = object()

    dock._mark_dirty()
    assert _refresh(dock) is False
    assert "extra_sw__ch1_dac_buf" not in dock._node_items
    assert dock._instances_refresh._timer.isActive(), "the deferral must re-arm itself"

    dock._active_op = None
    _run_for(500)
    assert "extra_sw__ch1_dac_buf" in dock._node_items


def test_every_edit_restarts_the_single_shot(main_window, tmp_path):
    """The debounce itself: EVERY event RESTARTS the single shot, so a burst of edits ends in
    one read after the last one — a timer that kept its FIRST deadline would read the config
    while the user is still typing.

    The clock is taken out of it: the QTimer is swapped for a recorder (a wall-clock version of
    this cell was red 2 runs out of 3 while it was being written, and a flaky guard is worse
    than none). What is pinned is the CALL — `start()` on every event — while `isActive()`
    answers True after the first one, exactly like a running single shot."""
    from gui.docks.trees_instances_refresh import InstancesRefresh

    dock, _root = _dock(main_window, tmp_path)
    refresher = InstancesRefresh(dock)
    starts = []

    class _Timer:
        def __init__(self):
            self.active = False

        def start(self):
            starts.append("start")
            self.active = True

        def isActive(self):
            return self.active

    refresher._timer = _Timer()

    for _ in range(3):
        refresher.note_change()

    assert starts == ["start"] * 3, "every event must RESTART the single shot"


def test_the_refresh_is_neither_a_dirty_edit_nor_a_stage(main_window, tmp_path, monkeypatch):
    """Cell и: the refresh must not look like a user edit — `_dirty` stays what it was and
    `_stage_trees` is never called (that would write the file and lose an unsaved edit)."""
    dock, _root = _dock(main_window, tmp_path)
    staged = []
    monkeypatch.setattr(dock, "_stage_trees", lambda: staged.append(1))
    dirty_before = dock._dirty

    assert _refresh(dock) is True

    assert dock._dirty is dirty_before
    assert staged == []
