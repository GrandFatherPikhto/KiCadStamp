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

from PyQt6.QtCore import Qt

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import TreeNode

from gui.docks.trees_dock import TreesDock
from gui.worker import start_long_op

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


def _wait_for(predicate, ms: int = 3000) -> bool:
    """Pump the REAL event loop until `predicate()` holds — a `start_long_op` worker runs on
    its own QThread, so a fixed sleep would be either flaky or slow."""
    import time

    from PyQt6.QtWidgets import QApplication

    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _broken_cfg() -> dict:
    """A template caught mid-edit: a node naming no entity makes the expansion refuse."""
    broken = _dirty_instance_cfg()
    broken["trees"][0]["nodes"][0]["children"].append(
        {"ref": "no_such_entity", "kind": "placement", "xy": [0.0, 0.0]})
    return broken


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


@pytest.mark.parametrize("dock_name", ["entity_dock", "cells_dock", "net_trace_dock"])
def test_every_section_dock_reaches_the_refresh(hub, tmp_path, dock_name):
    """Cell в, over ALL THREE docks Claude's §2.4 asked for: every section the expansion
    READS — entities, cells, net_traces — stages through its own dock, and `dock_hub` wires
    each `saved` to the refresh. The graph change here is an Entity cluster rename, so the
    answer is the same for all three; what the parameter pins is the WIRING, one dock at a
    time (a mutant that drops `net_trace_dock.saved` stayed green against the old cell)."""
    root = tmp_path / "hub.sexp"
    data = _dirty_instance_cfg()
    _write(root, data)
    hub.trees_dock.set_root_file(root)
    assert "pif_avdd__ch1_dac_buf" in {e.name for e in hub.trees_dock._cfg.entities}

    for entity in data["entities"]:
        if entity["name"] == "pif_avdd":
            entity["cluster"] = "PIF_RENAMED"
    _write(root, data)

    getattr(hub, dock_name).saved.emit()
    assert hub.trees_dock._instances_refresh._timer.isActive(), (
        f"{dock_name}.saved must reach the refresh through the hub's wiring")
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


def test_a_finished_operation_no_longer_blocks_the_refresh(main_window, tmp_path):
    """Cell з (rewritten for Claude's §2.2): the dock KEEPS the controller after a run, so
    «is an operation running?» has to be asked OF THE CONTROLLER. The old test
    (`_active_op is not None`) said "busy" for ever after the first redraw — the refresh
    re-armed its timer against that stale flag and the clones stopped following the template.

    The operation is a REAL `start_long_op` (not `object()`), and BOTH endings are pinned:
    success, and the `failed` refusal."""
    dock, _root = _dock(main_window, tmp_path)
    template = _tree(dock, TEMPLATE)

    template.nodes[0].children.append(_node("extra_sw"))
    dock._mark_dirty()
    ok = start_long_op(main_window.connection, (), lambda: "done",
                       lambda _result: None, lambda _message: None)
    dock._active_op = ok
    assert ok.running is True
    assert _refresh(dock) is False, "a running operation owns the dock"
    assert dock._instances_refresh._timer.isActive(), "the deferral must re-arm itself"

    assert _wait_for(lambda: not ok.running), "the worker thread never finished"
    assert _wait_for(lambda: "extra_sw__ch1_dac_buf" in dock._node_items), (
        "the deferred refresh must run after the operation, with no second _mark_dirty")

    def _boom():
        raise ValidationError("boom")

    failed = start_long_op(main_window.connection, (), _boom,
                           lambda _result: None, lambda _message: None)
    dock._active_op = failed
    assert _refresh(dock) is False
    assert _wait_for(lambda: not failed.running), (
        "a FAILED operation must stop answering «running» too")
    assert _refresh(dock) is True, "the refresh must work again once the op is over"


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


# ── Claude's §2: the defects the first acceptance reproduced ───────────────

def test_a_clean_buffer_refresh_keeps_the_templates_own_nodes(main_window, tmp_path):
    """§2.1 — THE lost edit. With `_dirty` False the re-read used to REPLACE the whole buffer
    (`fresh`), while the refresh re-renders only the CLONE pages: the template's rows kept
    `TreeNode` objects the buffer no longer held, and the next edit of such a row landed in a
    node nobody stages (measured on `extra_sw`: the staged geometry stayed `[0.0, 1.0]`). The
    refresh therefore merges even on a CLEAN buffer — the row keeps ITS node."""
    dock, root = _dock(main_window, tmp_path)
    assert dock._dirty is False
    template = _tree(dock, TEMPLATE)
    row_node = dock._node_items["pif_avdd"].data(0, Qt.ItemDataRole.UserRole)
    assert isinstance(row_node, TreeNode)

    # A REAL graph change on disk — another writer added a node to the template.
    _write(root, _dirty_instance_cfg(extra_template_node=True))
    assert _refresh(dock) is True

    assert _tree(dock, TEMPLATE) is template, "a clean buffer must keep its hand-written tree"
    assert dock._tree_of_node(row_node) is not None, (
        "the row's own TreeNode must still be in the buffer — otherwise the next edit of that "
        "row is written into an object nobody stages")

    row_node.xy = (9.0, 9.0)
    dock._mark_dirty()
    staged = next(t for t in dock._cfg.trees if t.name == TEMPLATE)
    assert staged.nodes[0].children[0].xy == (9.0, 9.0), (
        "the edit of that row must reach the buffer the dock stages")


def test_a_repeated_refusal_is_one_log_line(main_window, tmp_path, caplog):
    """§2.3 — `reload_root` said the refusal itself AND the refresh said it again, so ONE
    refusal produced two Log records and every further attempt another one. On the refresh
    path `reload_root` is silent: the line belongs to `_report_refusal`, once."""
    dock, root = _dock(main_window, tmp_path)
    _write(root, _broken_cfg())

    with caplog.at_level(logging.WARNING):
        assert _refresh(dock) is False
        assert _refresh(dock) is False

    said = [record.getMessage() for record in caplog.records
            if record.name in ("gui.docks.trees_instances_sync",
                               "gui.docks.trees_instances_refresh")]
    assert len(said) == 1, said


def test_the_same_refusal_after_a_good_expansion_is_said_again(
        main_window, tmp_path, monkeypatch):
    """§2.4 — the «same sentence twice in a row» guard must NOT swallow a refusal that comes
    BACK after a good expansion: the user fixed the template, broke it again, and has to be
    told again. Kills a mutant that drops the `_last_refusal` reset after success."""
    import gui.docks.trees_instances_refresh as mod

    said = []
    monkeypatch.setattr(mod, "show_message",
                        lambda text, style="", log=None: said.append(text))
    dock, root = _dock(main_window, tmp_path)

    _write(root, _broken_cfg())
    assert _refresh(dock) is False
    assert len(said) == 1, said

    _write(root, _dirty_instance_cfg())
    assert _refresh(dock) is True

    _write(root, _broken_cfg())
    assert _refresh(dock) is False
    assert len(said) == 2, "the same refusal after a GOOD expansion is news again"


def test_reload_trees_on_a_clean_buffer_takes_an_external_edit(main_window, tmp_path):
    """§4.3 — `reload_root`'s CLEAN branch, the mutation that survived without this cell.

    `reload_trees` is the dock's half of "Tools → Trees → Extract tree…": that write goes
    through `config_writer` and BYPASSES this dock, so `_dirty` stays False and the fresh shape
    has to win OUTRIGHT. An EDIT of a hand-written tree is the case that tells the two rules
    apart — `merge_reload` keeps the buffer's own trees and only APPENDS the fresh ones, so the
    merged branch would show the old geometry (and the old node objects) for ever.
    """
    dock, root = _dock(main_window, tmp_path)
    manual = _tree(dock, "manual")
    assert dock._dirty is False, "the premise: nobody edited the buffer here"

    moved = _dirty_instance_cfg()
    moved["trees"][1]["nodes"][0]["xy"] = [7.0, -3.0]
    _write(root, moved)
    dock.reload_trees()

    fresh = _tree(dock, "manual")
    assert fresh is not manual, "a clean buffer takes the fresh tree, not the merged one"
    assert fresh.nodes[0].xy == (7.0, -3.0), (
        "the external EDIT of a hand-written tree must reach the buffer")


# ── §3: the WORKING-SET path is the one every other door uses ──────────────
#
# `_mark_dirty` and the three docks' `saved` signals were the only doors wired to
# `note_instances_changed` (09.11). Every OTHER door that stages a section the expansion
# reads lands in `WORKING_SET.stage_write` → the hub's debounced `refresh_from_working_set`
# → `_refresh_graph_dependent_choices` → `trees_dock.refresh_ref_candidates` — and that
# method never nudged the clone refresh, so a live profile's clone lagged until a restart.
# §3 adds the one line and the cells below drive each path to it.

def _hub_instances(hub, tmp_path):
    """The HUB's own trees dock on a fresh `_dirty_instance_cfg` root: the working-set
    cells need the hub's timer and its graph handler, not a dock built by hand."""
    root = tmp_path / "ws.sexp"
    _write(root, _dirty_instance_cfg())
    hub.trees_dock.set_root_file(root)
    return hub.trees_dock, root


def _staged_copy(root, extra_ref="extra_sw", *, keep_uuid=True):
    """The file's OWN shape with ONE more node under the template's first node.

    Read back through `read_data`, not built by hand: the loader LIFTS the file to format 4
    on disk (every reference carries the target record's uuid), so a hand-built dict would
    be a format-3 file whose nodes lost their uuids — a fatal that has nothing to do with
    the cell. The new node COPIES an existing one (its `kind`/`group` and, for a real ref,
    the record's own uuid) and changes only what the cell is about."""
    from kicadstamp.config_writer import read_data

    data = read_data(root)
    ids = {e["name"]: e.get("uuid") for e in data.get("entities") or []}
    parent = data["trees"][0]["nodes"][0]
    node = dict(parent["children"][0])
    node.update({"ref": extra_ref, "xy": [0.0, 1.0]})
    if keep_uuid and ids.get(extra_ref):
        node["ref_uuid"] = ids[extra_ref]
    elif not keep_uuid:
        node.pop("ref_uuid", None)          # a bare NAME — nothing for the loader to resolve
    parent["children"].append(node)
    return data


def _clone_child_refs(dock):
    """The clone's own node refs, read from the BUFFER (the page renders only expanded
    rows, so `_node_items` is not the place to ask whether the copy follows)."""
    return [child.ref for node in _tree(dock, CLONE).nodes for child in node.children]


def test_a_working_set_write_alone_reaches_the_clone(hub, tmp_path, monkeypatch):
    """§3, cell а — THE point. A write staged into the working set with NO dock signal and
    NO `_mark_dirty` must still reach the generated tabs: the hub's debounced
    `refresh_from_working_set` is the path every other door takes."""
    from kicadstamp.config_working_set import WORKING_SET

    dock, root = _hub_instances(hub, tmp_path)
    assert "pif_avdd__ch1_dac_buf" in dock._node_items
    assert dock._instances_refresh._timer.isActive() is False

    cfg_before = dock._cfg
    staged = _staged_copy(root)
    monkeypatch.setattr(WORKING_SET, "enabled", True)
    try:
        WORKING_SET.stage_write(root, staged)
        hub._refresh_from_working_set()          # the hub timer's own tail
        assert dock._instances_refresh._timer.isActive(), (
            "a working-set write must ARM the clone refresh")
        assert dock._instances_refresh.flush() is True
    finally:
        WORKING_SET.clear()

    assert dock._cfg is not cfg_before, (
        "the refresh must RE-READ the root through this path, not keep the old object")


def test_one_working_set_write_is_one_clone_read(hub, tmp_path, monkeypatch):
    """§3, cell в — the new trigger is not a storm, and it is not an edit. ONE staged write
    costs exactly ONE config read BY THE CLONE REFRESH.

    The counter is scoped to `trees_instances_sync.load_config` ON PURPOSE:
    `refresh_ref_candidates` reads the same file on the same path by design (it serves the
    dialogs' candidate combos), so a counter over `load_config` in general would say 2 and
    invite someone to "fix" the second read, which is not this refresh's."""
    import gui.docks.trees_instances_sync as sync
    from kicadstamp.config_working_set import WORKING_SET

    real = sync.load_config
    reads = []
    monkeypatch.setattr(sync, "load_config", lambda path: reads.append(path) or real(path))
    dock, root = _hub_instances(hub, tmp_path)
    reads.clear()
    staged_calls = []
    monkeypatch.setattr(dock, "_stage_trees", lambda: staged_calls.append(1))
    dirty_before = dock._dirty

    staged = _staged_copy(root)
    monkeypatch.setattr(WORKING_SET, "enabled", True)
    try:
        WORKING_SET.stage_write(root, staged)
        hub._refresh_from_working_set()
        assert dock._instances_refresh.flush() is True
    finally:
        WORKING_SET.clear()

    assert len(reads) == 1, reads
    assert dock._dirty is dirty_before, "the refresh must not look like a user edit"
    assert staged_calls == [], "the refresh must never stage (that would write the file)"


def test_a_template_form_survives_the_working_set_path(hub, tmp_path, monkeypatch):
    """§3, cell г — the hub path reaches the clone refresh and STOPS there: the template's
    page object, the form open inside it and the active tab are the same afterwards."""
    from kicadstamp.config_working_set import WORKING_SET

    dock, root = _hub_instances(hub, tmp_path)
    template = _tree(dock, TEMPLATE)
    index = next(i for i, t in enumerate(dock._trees) if t.name == TEMPLATE)
    dock.tree_tabs.setCurrentIndex(index)
    dock._rebuild_active_form_panel()
    page = dock.tree_tabs.widget(index)
    form = dock._embedded_form_of(dock._panel_page(dock._form_panel_of_page(page)))
    assert form is not None, "the template page must have a form to protect"

    staged = _staged_copy(root)
    monkeypatch.setattr(WORKING_SET, "enabled", True)
    try:
        WORKING_SET.stage_write(root, staged)
        hub._refresh_from_working_set()
        assert dock._instances_refresh.flush() is True
    finally:
        WORKING_SET.clear()

    assert dock.tree_tabs.widget(index) is page
    assert dock._embedded_form_of(dock._panel_page(dock._form_panel_of_page(page))) is form
    assert dock._current_tab_tree_name() == TEMPLATE
    assert template is _tree(dock, TEMPLATE), "the hand-written template stays the same object"


def test_a_broken_template_on_the_working_set_path_says_it_once(
        hub, tmp_path, monkeypatch, caplog):
    """§3 п.3 — a refusal on this path is ONE line too, and it is the SAME line whether the
    expansion refuses inside `refresh_ref_candidates` or inside the clone refresh.

    Nothing is flushed here on purpose: a config the candidates re-read cannot load is also a
    config the clone refresh cannot load, and only one of the two is on screen. Measured —
    calling `flush()` by hand adds the refresh's own copy of the sentence, which no user door
    can produce."""
    from kicadstamp.config_working_set import WORKING_SET

    dock, root = _hub_instances(hub, tmp_path)
    staged = _staged_copy(root, "no_such_entity", keep_uuid=False)

    monkeypatch.setattr(WORKING_SET, "enabled", True)
    with caplog.at_level(logging.WARNING):
        try:
            WORKING_SET.stage_write(root, staged)
            hub._refresh_from_working_set()
        finally:
            WORKING_SET.clear()

    said = [record.getMessage() for record in caplog.records
            if record.name in ("gui.docks.trees_instances_sync",
                               "gui.docks.trees_instances_refresh",
                               "gui.docks.trees_dock")]
    assert len(said) == 1, said
    assert "no_such_entity" in said[0], said
    assert "pif_avdd__ch1_dac_buf" in dock._node_items, "the clones keep their last good state"


#: The two ConfigTreeDock actions §3 names ("переименование или удаление в дереве конфига"):
#: both write through `config_writer` and emit ONE `graph_changed`, and neither is one of the
#: three docks whose `saved` was wired to the refresh.
GRAPH_DOORS = ["rename", "delete"]


@pytest.mark.parametrize("door", GRAPH_DOORS)
def test_a_config_tree_door_reaches_the_clone_refresh(hub, tmp_path, monkeypatch, door):
    """§3, cell б — the doors that stage a graph section and woke NOTHING until now.

    `_on_rename` / `_on_delete` are ConfigTreeDock's own actions: they write through
    `config_writer` (into the working set when a project is open) and emit `graph_changed`,
    which the hub turns into `_refresh_graph_dependent_choices` → `refresh_ref_candidates`.
    That method never nudged the clone refresh, so the tab lagged until a restart — the live
    complaint of 10.11. Measured: both actions now arm it, and the refresh really runs."""
    import gui.docks.config_tree as ct
    from kicadstamp.config_working_set import set_active_graph_root

    dock, root = _hub_instances(hub, tmp_path)
    hub.config_tree_dock.set_root_file(root)
    # A format-3+ WRITE resolves reference UUIDs against the project graph, so the door needs
    # the root the dock's own Save would have set (the fixture's placeholder is reset after).
    set_active_graph_root(root)
    assert dock._instances_refresh._timer.isActive() is False

    monkeypatch.setattr(ct.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    if door == "rename":
        monkeypatch.setattr(ct.QInputDialog, "getText",
                            staticmethod(lambda *a, **k: ("c_dac_r", True)))
        hub.config_tree_dock._on_rename(root, "cells", "c_dac")
    else:
        monkeypatch.setattr(ct.QMessageBox, "question",
                            staticmethod(lambda *a, **k:
                                         ct.QMessageBox.StandardButton.Yes))
        hub.config_tree_dock._on_delete(root, "cells", "c_extra")

    assert dock._instances_refresh._timer.isActive(), (
        f"the {door} door must ARM the clone refresh")
    assert dock._instances_refresh.flush() is True


# ── §3.3: the graph cache used to hand the dock back its own buffer ────────
#
# HP-Power, 10.11: Reread added nodes to `switch_a`, Save wrote them, and the clone tab kept
# its OLD nodes until a restart. `refresh_ref_candidates` REBINDS `dock._cfg.trees` to the
# dock's buffer, and while `load_config` returned the cached object itself that rebinding
# landed IN THE CACHE — so the next `reload_root` read `fresh = list(cfg.trees)` and got the
# dock's own stale buffer back. The two cells below put `refresh_ref_candidates` BETWEEN the
# edit and the flush, which is exactly the order no §3 cell exercised.

def test_a_template_edit_then_the_candidates_reload_reaches_the_clone(
        main_window, tmp_path, monkeypatch):
    """§3.3, cell е — the live order: edit the template, `refresh_ref_candidates` (what the
    hub does on every staged write), THEN the 300 ms refresh. The clone must show the new
    node, and a Redraw of that tree must carry it in its payload."""
    import gui.docks.trees_dock as td

    dock, _root = _dock(main_window, tmp_path)
    template = _tree(dock, TEMPLATE)
    template.nodes[0].children.append(_node("extra_sw", xy=(3.5, -4.5)))
    dock._mark_dirty()

    dock.refresh_ref_candidates()          # the hub's step before the debounced flush
    assert dock._instances_refresh._timer.isActive(), (
        "`refresh_ref_candidates` must ARM the refresh (§3) — flushing by hand would hide it")
    assert _refresh(dock) is True

    clone = _tree(dock, CLONE)
    refs = [n.ref for n in clone.nodes[0].children]
    assert "extra_sw__ch1_dac_buf" in refs, refs

    starts = []
    monkeypatch.setattr(td, "start_long_op",
                        lambda connection, widgets, worker, finish, failed, payload,
                        **kwargs: starts.append(payload) or object())
    assert dock._on_redraw_whole_tree(None, clone) is True
    in_payload = [t.name for t in starts[0]["trees"]]
    assert CLONE in in_payload
    clone_in_payload = next(t for t in starts[0]["trees"] if t.name == CLONE)
    assert "extra_sw__ch1_dac_buf" in [n.ref for n in clone_in_payload.nodes[0].children]


def test_a_saved_template_keeps_the_clone_fresh_across_the_candidates_reload(
        main_window, tmp_path):
    """§3.3, cell ж — the same order with a SAVE in the middle: after the file is written,
    `refresh_ref_candidates` + the refresh must leave the clone свежим, not restore the
    buffer (nor the cache) it came from."""
    dock, _root = _dock(main_window, tmp_path)
    template = _tree(dock, TEMPLATE)
    template.nodes[0].children.append(_node("extra_sw", xy=(3.5, -4.5)))
    dock._mark_dirty()

    dock._do_save()
    dock.refresh_ref_candidates()
    assert dock._instances_refresh._timer.isActive(), (
        "the candidates re-read must still ARM the refresh after a Save")
    assert _refresh(dock) is True

    refs = [n.ref for n in _tree(dock, CLONE).nodes[0].children]
    assert "extra_sw__ch1_dac_buf" in refs, refs
