# tests/gui/test_board_door_offenders.py
"""Т5-1..Т5-3 of plan_2026_09_21_board_door_enforcement: the first three offenders
of the armed run, each pinned by the REAL guard rather than by a spy.

The guard (gui/connection.py) is armed here in its TEST RIG's mode, for the UI
thread of the test, so any path that still reads `connection.board` unsigned
RAISES inside the path under test. That is the strongest form of these watchdogs:
they do not assert that some call was not made — they let the guard say so itself,
naming the file and the line that has to change.

The three, in the order the armed run of 21.09.2026 named them:

  Т5-1  `TreesDock.set_root_file` -> `_clear_all_tree_markers` -> `_live_adapter`:
        the FIRST thing a user runs into after connecting (an actual root switch).
        Fixed with the door's own sign — the read is deliberate (it hands the
        shared adapter to the marker-cleanup worker), so it says so at the call
        site instead of being moved or hidden.
  Т5-2  `DockHub._warn_if_sheet_narrowing_disabled`: a legal presence check, now
        `connection.is_connected`.
  Т5-3  `gui.worker.snapshot_refresh_supported`: the same, answered by the
        connection itself now (`BoardConnection.snapshot_refresh_supported`), so
        the capability question never opens the door at all.

Numbers live in the docstrings, names describe the property (rule 37).
"""
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QInputDialog

from gui.connection import UiThreadBoardReadRefused
from gui.worker import snapshot_refresh_supported
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.domain.geometry import Vector2


def _arm_the_door(monkeypatch) -> None:
    """The door's guard, ARMED for this test's UI thread in the rig's mode: a
    violation raises instead of writing a red Log line, which is what makes these
    watchdogs able to fail. Callable in the MIDDLE of a test too — the Т2-2 guards
    arm it only after their setup, because `set_root_file` still reads the board
    unsigned through the forms it builds (that is Т2-7's business, and a guard for
    a LATER read must not depend on it)."""
    from gui import connection as connection_mod
    from gui.worker import is_ui_thread

    monkeypatch.setattr(connection_mod, "ui_thread_predicate", is_ui_thread)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal",
                        connection_mod.UI_READ_RAISE)


@pytest.fixture
def armed_door(qapp, monkeypatch):
    """The door armed for the WHOLE test — see `_arm_the_door`."""
    _arm_the_door(monkeypatch)


def test_opening_a_root_does_not_read_the_board_unsigned(
        real_main_window, tmp_path, armed_door):
    """Т5-1 — an actual root switch clears the tree markers, and that must not read
    the board without a sign.

    Mutation check: drop the `with ui_thread_board_read(...)` from
    `TreesDock._clear_all_tree_markers` and this fails with a refusal naming
    gui/docks/trees_dock.py's `_live_adapter` line."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"cells": {}, "trees": []}), encoding="utf-8")
    real_main_window.connection.board = SimpleNamespace(adapter=object())

    real_main_window._dock_hub.trees_dock.set_root_file(root)   # must not raise


def test_the_sheet_narrowing_warning_does_not_read_the_board_unsigned(
        real_main_window, armed_door):
    """Т5-2 — "is there a board?" is `is_connected`, not a read of the board.

    Mutation check: restore `getattr(connection, "board", None) is None` in
    `DockHub._warn_if_sheet_narrowing_disabled` and this fails with the refusal."""
    real_main_window.connection.board = SimpleNamespace(adapter=object())

    # A root that does not exist is fine here: the board check comes FIRST, and it
    # is the read this test is about (the failed load is logged, not raised).
    real_main_window._dock_hub._warn_if_sheet_narrowing_disabled("nope.sexp")


def test_snapshot_refresh_supported_does_not_read_the_board_unsigned(
        real_main_window, armed_door):
    """Т5-3 — the capability question is answered by the connection itself
    (`BoardConnection.snapshot_refresh_supported`), so the door is never opened
    for it.

    Mutation check: put the `getattr(getattr(connection, "board", None), "refresh",
    None)` body back in `gui/worker.snapshot_refresh_supported` and this fails."""
    real_main_window.connection.board = SimpleNamespace(refresh=lambda: None)

    assert snapshot_refresh_supported(real_main_window.connection) is True


# ── Ш4 (plan_2026_09_22_board_door_finish) — the Extract entry ───────────────
# The place the armed run of 22.09.2026 named (dock_hub.py:2137). Its presence
# half is is_connected; the adapter it captures for the flow's LIVE preview reads
# is taken under the door's sign.

def test_the_extract_entry_does_not_read_the_board_unsigned(
        real_main_window, armed_door, monkeypatch):
    """Ш4 — with the door ARMED the Extract entry refuses nothing. The flow stops
    at the missing root (this window has none) and never reaches its dialog, so
    the cells here are the entry itself.

    Mutation check: drop the `with ui_thread_board_read(...)` wrapper and this
    fails with a refusal naming gui/dock_hub.py."""
    import gui.dock_hub as hub_mod
    monkeypatch.setattr(hub_mod.QMessageBox, "warning", lambda *a, **k: None)
    real_main_window.connection.board = SimpleNamespace(adapter=object())

    real_main_window._dock_hub._extract_tree_from_selection_now()   # must not raise


class _PresenceOnlyConnection:
    """Answers the presence question and DIES if the door is opened anyway: a
    presence check must not read `board` — the Т5-2 idiom, applied to the Extract
    entry (Ш4) and to the Instantiate-from-selection continuation (Т2-1)."""

    def __init__(self, is_connected=False):
        self.is_connected = is_connected
        self.long_op_active = False
        self.timeout_ms = DEFAULT_TIMEOUT_MS

    @property
    def board(self):
        raise AssertionError("the presence check read the board through the door")


def test_the_extract_presence_check_does_not_open_the_door(
        real_main_window, monkeypatch):
    """Ш4 — "is there a board?" is `connection.is_connected`, not a read of the
    door (the Т5-2 idiom, applied to the Extract entry). Pinned with a connection
    whose `board` raises when read: the flow must report "Not connected." without
    touching it.

    Mutation check: restore `getattr(connection, "board", None)` as the presence
    check and this fails on the stand-in's AssertionError."""
    import gui.dock_hub as hub_mod
    warnings = []
    monkeypatch.setattr(hub_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    real_main_window.connection = _PresenceOnlyConnection()

    real_main_window._dock_hub._extract_tree_from_selection_now()

    assert warnings, "the flow must report the missing connection"


# ── Т2-1 (plan_2026_09_22_live_adapter_class) — the Instantiate continuation ──
# `_anchor_base_then` (the "from selection" half of Instantiate from Cell) asked
# "is there a board?" BY READING THE DOOR and handed nothing through that read:
# the base read itself runs on the WORKER, which builds its own adapter
# (run_anchor_base_mm_worker). The presence question is the connection's now.

def test_the_instantiate_continuation_does_not_read_the_board_unsigned(
        real_main_window, tmp_path, armed_door, monkeypatch):
    """Т2-1 — with the door ARMED the continuation refuses nothing: it asks the
    connection whether a board is there and goes on to its worker. The stand-in's
    `board` RAISES when read, so a door read fails the test outright.

    The root comes first (a continuation without a config refuses for that reason,
    not for the board's) and the modal is recorded rather than shown, so a
    regression fails on the assert instead of waiting for a click.

    Mutation check: restore `self._live_adapter() is None` and this fails — the
    door refuses first, and the stand-in would raise anyway."""
    import gui.docks.trees_dock as td_mod

    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"cells": {}, "trees": []}), encoding="utf-8")
    dock = real_main_window._dock_hub.trees_dock
    dock.set_root_file(root)

    started = []
    warnings = []
    monkeypatch.setattr(td_mod, "start_long_op",
                        lambda *a, **k: started.append(a) or None)
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    real_main_window.connection = _PresenceOnlyConnection(is_connected=True)

    dock._anchor_base_then((1.0, 2.0), "cell1", "ENT_A", "CL", "",
                           SimpleNamespace(name="probe_tree"))

    assert warnings == [], "the presence check refused a connection that is there"
    assert started, "the continuation must reach its worker"


# ── Т2-2 (plan_2026_09_22_live_adapter_class) — the three handoff reads ──────
# Three more `_live_adapter()` callers hand the SHARED adapter to a worker and own
# the socket through start_long_op for the whole operation: one tree's marker
# cleanup, the other trees' marker cleanup, and the node reread. Same fix as Т5-1,
# each with its OWN reason at its own call site.

def test_one_trees_marker_cleanup_does_not_read_the_board_unsigned(
        real_main_window, monkeypatch):
    """Т2-2 — `_clear_tree_markers` (a rename, or leaving the tree) hands the
    shared adapter to its worker under the door's sign.

    Mutation check: drop that `with ui_thread_board_read(...)` and this fails with
    a refusal naming gui/docks/trees_dock.py."""
    import gui.docks.trees_dock as td_mod

    started = []
    monkeypatch.setattr(td_mod, "start_long_op",
                        lambda *a, **k: started.append(a) or None)
    dock = real_main_window._dock_hub.trees_dock
    td_mod.settings.state.set(
        td_mod.overlay_markers.OVERLAY_MARKERS_KEY,
        {key: "uuid-1" for key in td_mod._tree_marker_keys("probe_tree")})
    real_main_window.connection.board = SimpleNamespace(adapter=object())
    _arm_the_door(monkeypatch)

    dock._clear_tree_markers("probe_tree")

    assert started, "the removal must reach its worker"


def test_other_trees_marker_cleanup_does_not_read_the_board_unsigned(
        real_main_window, monkeypatch):
    """Т2-2 — `_clear_other_tree_markers` (a tree-tab switch) does the same, with
    its own reason.

    Mutation check: drop that sign and this fails with the refusal."""
    import gui.docks.trees_dock as td_mod

    started = []
    monkeypatch.setattr(td_mod, "start_long_op",
                        lambda *a, **k: started.append(a) or None)
    dock = real_main_window._dock_hub.trees_dock
    td_mod.settings.state.set(
        td_mod.overlay_markers.OVERLAY_MARKERS_KEY,
        {td_mod._TREE_ANCHOR_NS + "/some_other_tree": "uuid-2"})
    real_main_window.connection.board = SimpleNamespace(adapter=object())
    _arm_the_door(monkeypatch)

    dock._clear_other_tree_markers()

    assert started, "the removal must reach its worker"


def test_the_node_reread_flow_does_not_read_the_board_unsigned(
        real_main_window, tmp_path, monkeypatch):
    """Т2-2 — "Reread current position" hands the shared adapter to its worker
    under the sign; its own `socket_busy` refusal stays where it was.

    Mutation check: drop that sign and this fails with the refusal."""
    import gui.docks.trees_dock as td_mod

    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"trees": [
        {"name": "probe_tree", "anchor": {"ref": "U1"},
         "nodes": [{"ref": "R_DEBUG", "kind": "external", "xy": [1.0, 2.0]}]}]}),
        encoding="utf-8")
    dock = real_main_window._dock_hub.trees_dock
    dock.set_root_file(root)                     # built BEFORE the door is armed
    tree = dock._current_tree()
    node = tree.nodes[0]

    started = []
    monkeypatch.setattr(td_mod, "start_long_op",
                        lambda *a, **k: started.append(a) or None)
    real_main_window.connection.board = SimpleNamespace(adapter=object())
    _arm_the_door(monkeypatch)

    dock._reread_node_flow(tree, node)

    assert started, "the read must reach its worker"


# ── Т2-3 (plan_2026_09_22_live_adapter_class) — the first-run copper probe ────
# `_confirm_first_run_redraw` hands the SHARED adapter to the Bug-3 heads-up, which
# reads the whole board's copper (get_tracks + get_vias, measured 30.1 + 14.2 ms,
# one exchange each). It stays on the UI thread on purpose — the callers branch on
# its answer BEFORE the redraw worker starts — and behind a socket_busy gate.

def test_the_first_run_copper_probe_does_not_read_the_board_unsigned(
        real_main_window, monkeypatch):
    """Т2-3 — with the door ARMED the heads-up refuses nothing, and the adapter the
    probe receives is the live one (the spy proves it really arrived).

    Mutation check: drop the `with ui_thread_board_read(...)` from
    `_confirm_first_run_redraw` and this fails with the refusal."""
    import gui.docks.trees_dock as td_mod

    adapter = object()
    handed = []
    monkeypatch.setattr(
        td_mod, "confirm_first_run_adoption",
        lambda parent, config_path, adapter=None: handed.append(adapter) or True)
    dock = real_main_window._dock_hub.trees_dock
    real_main_window.connection.board = SimpleNamespace(adapter=adapter)
    _arm_the_door(monkeypatch)

    assert dock._confirm_first_run_redraw() is True
    assert handed == [adapter], "the probe must receive the shared adapter"


# ── Т2-4 (plan_2026_09_22_live_adapter_class) — the re-hang base resolve ─────
# `_rehang_offset_or_ask` resolves the OLD and the NEW parent's base to keep the
# node physically still. Measured before the fix (probe_2026_09_22_rehang_offset_
# cost): TWO whole-board sweeps — 2x get_footprints + 664 get_field_value for one
# re-hang. It now reads through the door's sign, passes the connection's snapshot
# (so each resolve answers the identity question in memory) and refuses while the
# shared socket is busy (door rule 3 — the read this handler never guarded).

def test_the_rehang_offset_read_does_not_read_the_board_unsigned(
        real_main_window, armed_door, monkeypatch):
    """Т2-4 — with the door ARMED the re-hang read refuses nothing, and the
    snapshot the dock owns travels into the resolver (the spy proves both).

    Mutation check: drop the sign and this fails with the refusal."""
    import gui.docks.trees_dock as td_mod

    seen = []
    questions = []
    monkeypatch.setattr(td_mod, "_reparented_offset",
                        lambda *a, **k: seen.append(k) or (None, None, 0.0))
    # The refusal path below this read asks the user "re-hang WITHOUT recalculating?"
    # — recorded rather than shown, so a regression FAILS instead of waiting for a
    # click (the first version of this test hung, and the mutation harness refused
    # to call that a kill: a timeout shows no FAILED line).
    monkeypatch.setattr(td_mod.QMessageBox, "question",
                        lambda *a, **k: questions.append(a)
                        or td_mod.QMessageBox.StandardButton.No)
    dock = real_main_window._dock_hub.trees_dock
    connection = real_main_window.connection
    connection.board = SimpleNamespace(adapter=object())
    snapshot = [object()]
    monkeypatch.setattr(type(connection), "snapshot",
                        property(lambda self: snapshot))

    proceed, shift = dock._rehang_offset_or_ask(
        SimpleNamespace(name="probe_tree"),
        SimpleNamespace(ref="R_DEBUG", kind="external"), None, None)

    assert questions == [], \
        "an unsigned read must not degrade into the 'cannot hold still' question"
    assert (proceed, shift) == (True, (None, None, 0.0))
    assert seen and seen[0]["snapshot"] is snapshot, \
        "the connection's snapshot must travel to the resolver"


def test_the_rehang_offset_read_is_refused_while_the_socket_is_busy(
        real_main_window, monkeypatch, caplog):
    """Т2-4/Т2-4б — door rule 3: the resolve touches the SHARED adapter, so a
    busy socket refuses instead of interleaving a second REQ transaction into the
    tick's in-flight one ("Operation canceled"). The caller defers the whole
    re-hang once (Т2-4б), so this branch is the RACE WINDOW — a tick may start
    between the deferred attempt's own check and this read — and that is exactly
    why the refusal is NAMED in the Log instead of being silent.

    Mutation check: drop the `socket_busy` early return and this fails — the
    resolver is entered on a busy socket; drop the Log line and the caplog
    assertion below fails while the rest still passes (silence looks like a
    no-op)."""
    import gui.docks.trees_dock as td_mod

    seen = []
    monkeypatch.setattr(td_mod, "_reparented_offset",
                        lambda *a, **k: seen.append(k) or (None, None, 0.0))
    dock = real_main_window._dock_hub.trees_dock
    real_main_window.connection.board = SimpleNamespace(adapter=object())
    real_main_window.connection.long_op_active = True      # the tick is in flight

    caplog.clear()
    proceed, shift = dock._rehang_offset_or_ask(
        SimpleNamespace(name="probe_tree"),
        SimpleNamespace(ref="R_DEBUG", kind="external"), None, None)

    assert (proceed, shift) == (False, None), "a busy socket must refuse the move"
    assert seen == [], "the resolve ran while the tick owned the socket"
    told = [r.message for r in caplog.records]
    assert any("busy" in m and "NOT moved" in m for m in told), \
        f"the refusal must be NAMED in the Log, not silent — got {told!r}"


# ── Т2-4б (plan_2026_09_22_live_adapter_class) — the refusal tells the user ──
# "Move to…" is triggered by a DRAG, and the busy refusal used to eat the whole
# move: the read answered (False, None), the caller returned, and nothing on
# screen said a word. The flow now defers the whole re-hang ONCE
# (defer_while_socket_busy, the helper every other shared-socket read here uses)
# and, when the retry meets the busy socket again, tells the user in the same
# words the race-window Log line uses (_REHANG_BUSY_TEXT).

def _rehang_dock(window, tmp_path, monkeypatch):
    """A TreesDock over a throwaway root plus the "Move to…" shape
    tests/gui/test_trees_dock.py uses: two mount nodes and one record node, with
    the base resolve and the rebuild stubbed, so the flow needs no board and no
    widgets. Returns (dock, tree, mount_b, moved)."""
    import gui.docks.trees_dock as trees_mod
    from kicadstamp.trees import Tree, TreeAnchor, TreeNode

    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"trees": []}), encoding="utf-8")
    dock = trees_mod.TreesDock(window)
    dock.set_root_file(root)             # built BEFORE the door is armed
    monkeypatch.setattr(dock, "_rebuild_tabs", lambda: None)
    monkeypatch.setattr(trees_mod, "_reparented_offset",
                        lambda *a, **k: (None, None, 0.0))
    mount_b = TreeNode(ref="mnt_b", kind="mount", xy=None, polar=None,
                       rotation=0.0, name=None, group=None, children=[],
                       anchor=TreeAnchor(role="IC2"))
    moved = TreeNode(ref="R_MOVED", kind="external", xy=(12.0, 0.0), polar=None,
                     rotation=0.0, name=None, group=None, children=[])
    tree = Tree(name="rehang", anchor=TreeAnchor(is_origin=True),
                nodes=[mount_b, moved])

    def _get_item(*args, **kwargs):
        labels = list(args[3] if len(args) > 3 else kwargs.get("items"))
        assert "mnt_b" in labels, ("the row under test is not offered", labels)
        return ("mnt_b", True)
    monkeypatch.setattr(QInputDialog, "getItem", _get_item)
    return dock, tree, mount_b, moved


def test_a_move_deferred_by_a_busy_socket_still_happens(
        real_main_window, tmp_path, monkeypatch):
    """Т2-4б — the deferral is not a cosmetic Log line: the ONE armed retry
    finishes the move the user asked for, with the door ARMED over the retry, so
    the deferred read is proven to go through the sign as well.

    Mutation check: run `_rehang` directly instead of deferring and this fails —
    nothing is armed and the retry below never fires."""
    import gui.worker as worker_mod

    dock, tree, mount_b, moved = _rehang_dock(real_main_window, tmp_path,
                                              monkeypatch)
    connection = real_main_window.connection
    connection.board = SimpleNamespace(adapter=object())
    connection.long_op_active = True            # the poll tick is in flight
    scheduled: list = []
    monkeypatch.setattr(worker_mod.QTimer, "singleShot",
                        lambda delay, callback: scheduled.append((delay, callback)))
    _arm_the_door(monkeypatch)

    dock._move_node_flow(tree, moved)

    assert dock._in_list(moved, tree.nodes), \
        "the move must not run while the tick owns the socket"
    assert not dock._in_list(moved, mount_b.children)
    assert len(scheduled) == 1, "the whole re-hang must be deferred EXACTLY once"
    assert scheduled[0][0] == worker_mod.SNAPSHOT_REFRESH_RETRY_DELAY_MS, \
        "the retry uses the helper's own delay, not a number of its own"

    connection.long_op_active = False           # the tick finished
    scheduled[0][1]()                           # the armed retry

    assert dock._in_list(moved, mount_b.children) and moved not in tree.nodes, \
        "the deferred attempt must complete the move the user asked for"


def test_a_move_still_busy_on_the_retry_tells_the_user(
        real_main_window, tmp_path, monkeypatch):
    """Т2-4б — the retry is the LAST attempt, so THIS is where a drag that is not
    going to happen stops being silent: the user gets a message box whose text
    names the cause (the board is busy) and the outcome (the node did NOT move).

    Mutation check: hand the helper a bare `lambda: None` as on_still_busy and
    this fails on the empty `told` — the second refusal is silent again."""
    import gui.docks.trees_dock as trees_mod
    import gui.worker as worker_mod

    dock, tree, mount_b, moved = _rehang_dock(real_main_window, tmp_path,
                                              monkeypatch)
    connection = real_main_window.connection
    connection.board = SimpleNamespace(adapter=object())
    connection.long_op_active = True
    scheduled: list = []
    monkeypatch.setattr(worker_mod.QTimer, "singleShot",
                        lambda delay, callback: scheduled.append((delay, callback)))
    told: list = []
    monkeypatch.setattr(trees_mod.QMessageBox, "warning",
                        lambda *a, **k: told.append(a) or None)

    dock._move_node_flow(tree, moved)

    assert told == [] and len(scheduled) == 1, \
        "the FIRST busy socket defers silently — the retry is still ahead"

    scheduled[0][1]()                           # the retry: STILL busy

    assert len(told) == 1, "a second busy socket must TELL the user, not whisper"
    text = str(told[0][2])
    assert "busy" in text and "NOT moved" in text, (
        "the message must name the cause AND the fact that nothing moved", text)
    assert dock._in_list(moved, tree.nodes) and not dock._in_list(
        moved, mount_b.children), "nothing may move while the retry is refused"
    # The retry is not called a second time on purpose: the stub above is a LIST,
    # not a timer, so re-running it would fake a third attempt the real
    # QTimer.singleShot never makes. "EXACTLY ONE retry" is the assertion above
    # (`len(scheduled) == 1`) plus the helper's own sentinel in
    # tests/gui/test_snapshot_freshness.py.
    assert len(scheduled) == 1


# ── Т2-6 (plan_2026_09_22_live_adapter_class) — the tab-2 extraction ──────────
# "Instantiate from Cell…" tab 2 used to read the door and then run a whole-board
# extraction inline: `adapter = self._live_adapter()  # lazy: tab 1 stays usable
# offline` followed by `if adapter is None:`, both in `_instantiate_from_cell_now`.
# It now asks the CONNECTION for presence (the Т5-2 idiom) and the read happens on
# a WORKER that builds its OWN adapter — so the UI half has no adapter read at all.

def test_the_new_cell_extraction_does_not_read_the_board_unsigned(
        real_main_window, tmp_path, monkeypatch, qapp):
    """Т2-6 — with the door ARMED, driving the tab-2 continuation reads nothing
    unsigned. The worker is stubbed here (its own thread and the shared-socket
    token are pinned in tests/gui/test_ui_thread_board_reads.py), so what this
    cell adds is the DOOR: the continuation must not reach for the adapter object
    at all.

    The continuation is `_extract_new_cell_then` — a function the fix CREATED, so
    this cell cannot vouch for the place the read used to stand: that was
    `_instantiate_from_cell_now`, one level up, and it has its own cell below
    (Кm of plan_2026_09_22_door_guard_aim_and_socket_close —
    test_instantiate_tab_2_does_not_read_the_board_unsigned).

    Mutation check: mutation m22 restores the pre-Т2-6 inline extraction (the door
    read included) INSIDE `_extract_new_cell_then`, and this fails with the
    refusal."""
    import gui.docks.trees_dock as trees_mod
    from tests.gui.conftest import _pump

    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"trees": [
        {"name": "t", "anchor": {"origin": True}, "nodes": []}]}), encoding="utf-8")
    dock = trees_mod.TreesDock(real_main_window)
    dock.set_root_file(root)                     # built BEFORE the door is armed
    tree = dock._trees[0]
    real_main_window.connection.board = SimpleNamespace(adapter=object())
    # The rebuild builds the FORM widgets, and those still read the board unsigned
    # — Т2-8's business, not this cell's. Stubbed exactly like _rehang_dock does, so
    # this guard measures the extraction path and nothing else.
    monkeypatch.setattr(dock, "_rebuild_tabs", lambda: None)
    started: list = []
    monkeypatch.setattr(
        trees_mod, "run_extract_new_cell_worker",
        lambda payload: started.append(payload)
        or {"cell": {"new_cell": {"components": [{"role": "R1"}]}}})
    _arm_the_door(monkeypatch)

    dock._extract_new_cell_then(
        SimpleNamespace(cluster="CL", sheet=""), [], absolute=False,
        origin_role=None, origin_pad=None, cell_name="new_cell",
        entity_name="ENT_A", cluster="CL", sheet="", tree=tree, selected=[],
        from_selection=False, manual_xy=(1.0, 2.0))
    _pump(qapp, lambda: not real_main_window.connection.long_op_active)

    assert started, "the extraction must go to its worker"
    assert started[0]["cluster"].cluster == "CL", \
        "the detected cluster must travel in the payload"


# ── Кm (plan_2026_09_22_door_guard_aim_and_socket_close) — the place the defect
# REALLY lived ────────────────────────────────────────────────────────────────
# The Т2-6 cell above drives `_extract_new_cell_then`, which commit e4ddc5a
# CREATED. The read the fix removed stood one level up, in
# `_instantiate_from_cell_now` — verbatim, from
# `git show e4ddc5a -- gui/docks/trees_dock.py`:
#     adapter = self._live_adapter()  # lazy: tab 1 stays usable offline
#     if adapter is None:
# Nothing watched that place, so the defect could be put back in silence: the proof
# is the mutation of the acceptance, which passed EVERY watchdog. That is why this
# cell exists and why its mutation's pattern is taken from the diff, not from memory.

def test_instantiate_tab_2_does_not_read_the_board_unsigned(
        real_main_window, tmp_path, monkeypatch, qapp):
    """Кm (plan_2026_09_22_door_guard_aim_and_socket_close) — with the door ARMED,
    driving "Instantiate from Cell…" TAB 2 through its REAL entry reads nothing
    unsigned, and the flow still reaches its worker.

    It drives `_instantiate_from_cell_now` itself, because that is where the
    pre-Т2-6 read lived: the presence check opened the door
    (`adapter = self._live_adapter()`) and the extraction behind it ran inline on
    the UI thread. The Т2-6 cell above drives only the continuation the fix created
    and says nothing about this place.

    Every seam the flow crosses is stubbed BELOW the door, so the only thing that can
    refuse is the door itself: the modal dialog (its answers are plain data), the
    fully-selected-cluster detection (exactly ONE cluster, so the dialog's own gate
    is passed), the tab-2 worker, and `_rebuild_tabs` (the tab rebuild still reads
    the board through the forms it builds — that is Т2-8's business, not this
    cell's).

    Mutation check: put the removed lines back —
    `adapter = self._live_adapter()  # lazy: tab 1 stays usable offline` followed by
    `if adapter is None:` in `_instantiate_from_cell_now` (the pattern is the `-`
    half of the diff of the commit that removed them) — and this fails with the
    door's refusal naming gui/docks/trees_dock.py."""
    import gui.docks.instantiate_cell_dialog as dialog_mod
    import gui.docks.reead as reead_mod
    import gui.docks.trees_dock as trees_mod
    from PyQt6.QtWidgets import QDialog
    from tests.gui.conftest import _pump

    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"trees": [
        {"name": "t", "anchor": {"origin": True}, "nodes": []}]}), encoding="utf-8")
    dock = trees_mod.TreesDock(real_main_window)
    dock.set_root_file(root)                     # built BEFORE the door is armed
    real_main_window.connection.board = SimpleNamespace(adapter=object())
    monkeypatch.setattr(dock, "_rebuild_tabs", lambda: None)

    class _NewCellDialog:
        """The modal of "Instantiate from Cell…", answered on TAB 2: `exec()`
        returns Accepted so the flow goes on, and every answer the flow reads comes
        back as plain data — no widget is ever shown."""

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def entity_name(self):
            return "ENT_A"

        def result_cell(self):
            return "new_cell"

        def cluster(self):
            return "CL"

        def sheet(self):
            return ""

        def is_new_cell(self):
            return True

        def origin_override(self):
            return None, None

        def absolute_origin(self):
            return False

        def from_selection(self):
            return False

        def manual_xy(self):
            return (1.0, 2.0)

    monkeypatch.setattr(dialog_mod, "InstantiateCellDialog", _NewCellDialog)
    # Exactly ONE fully selected cluster: the dialog only enables OK in that case,
    # and the real detection needs live snapshot items this cell does not model.
    monkeypatch.setattr(reead_mod, "fully_selected_clusters",
                        lambda *a, **k: [SimpleNamespace(cluster="CL", sheet="")])
    started: list = []
    monkeypatch.setattr(
        trees_mod, "run_extract_new_cell_worker",
        lambda payload: started.append(payload)
        or {"cell": {"new_cell": {"components": [{"role": "R1"}]}}})
    _arm_the_door(monkeypatch)

    dock._instantiate_from_cell_now([])          # must not raise
    _pump(qapp, lambda: not real_main_window.connection.long_op_active)

    assert started, "the flow must reach its worker"
    assert started[0]["cluster"].cluster == "CL", \
        "the detected cluster must travel in the payload"
    assert started[0]["cell_name"] == "new_cell"


# ── Кj (plan_2026_09_22_live_adapter_class) — the sign NAMES the refresh ─────
# `_live_cluster_frame` refreshes the board before it reads (bug of 2026-09-10)
# and the re-hang reaches it from the UI THREAD, so covering that call with a sign
# is not enough: the sign has to NAME its price. The acceptance was exact about it
# — "либо он уходит из этого пути, либо знак обязан называть его ценой отдельной
# строкой"; "подписать и не назвать" is what it refused.

def test_the_rehang_sign_names_the_whole_board_refresh(
        real_main_window, monkeypatch):
    """Кj — the reason the door's sign carries must say that an Entity-typed
    parent pays ONE whole-board `adapter.refresh_board()` from the UI thread: the
    cost the snapshot does NOT remove is the cost the sign has to name.

    Mutation check: drop the refresh sentence from the reason and this fails
    (m20 of the mutation run) while every other cell stays green — the resolve
    itself is unaffected, which is exactly why the naming needs its own cell."""
    import gui.docks.trees_dock as td_mod

    reasons: list = []

    @contextmanager
    def _spy(*, reason):
        reasons.append(reason)
        yield

    monkeypatch.setattr(td_mod, "ui_thread_board_read", _spy)
    monkeypatch.setattr(td_mod, "_reparented_offset",
                        lambda *a, **k: (None, None, 0.0))
    dock = real_main_window._dock_hub.trees_dock
    real_main_window.connection.board = SimpleNamespace(adapter=object())

    dock._rehang_offset_or_ask(
        SimpleNamespace(name="probe_tree"),
        SimpleNamespace(ref="R_DEBUG", kind="external"), None, None)

    assert reasons, "the re-hang resolve must go through the door's sign"
    text = reasons[0]
    assert "refresh_board" in text, (
        "the sign must NAME the whole-board refresh it covers, not only the two "
        "resolves — signing a cost without naming it was refused", text)
    assert "Entity" in text, (
        "…and say WHICH parent pays it (the Entity-typed one, the typical node)",
        text)
    assert "full-board" in text, (
        "…AND name the full-board re-read that refresh FORCES on the next line: a "
        "call count is not what the board costs, because the adapter caches and "
        "refresh_board() empties that cache (Кk of the acceptance) — a reason that "
        "mentions refresh_board alone would read as if the cost were a round trip "
        "and nothing else", text)


# ── Т2-3 (plan numbering, plan_2026_09_22_live_adapter_class) — PointsDock ───
# The two point-circle cleanups of the Points dock, the same class Т2-2 closed
# three times in TreesDock: presence plus handing the SHARED adapter to a worker
# that then owns the socket. Missed by the first pass (its own numbering hid the
# step), so it gets its own cells here.

def test_one_point_marker_removal_does_not_read_the_board_unsigned(
        real_main_window, tmp_path, monkeypatch):
    """Т2-3 — `_forget_point_marker` (a point being loaded or renamed) hands the
    shared adapter to its worker under the door's sign.

    Mutation check: drop that sign and this fails with the refusal."""
    import gui.docks.points as points_mod
    from gui import settings

    started = []
    monkeypatch.setattr(points_mod, "start_long_op",
                        lambda *a, **k: started.append(a) or None)
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"points": {}}), encoding="utf-8")
    dock = points_mod.PointsDock(real_main_window)
    dock.set_root_path(root)                     # built BEFORE the door is armed
    settings.state.set(points_mod.overlay_markers.OVERLAY_MARKERS_KEY,
                       {points_mod._point_key("probe"): "uuid-point"})
    real_main_window.connection.board = SimpleNamespace(adapter=object())
    _arm_the_door(monkeypatch)

    dock._forget_point_marker("probe")

    assert started, "the removal must reach its worker"


def test_the_whole_point_layer_removal_does_not_read_the_board_unsigned(
        real_main_window, tmp_path, monkeypatch):
    """Т2-3 — `_clear_point_markers` (an actual root switch) does the same, with
    its own reason.

    Mutation check: drop that sign and this fails with the refusal."""
    import gui.docks.points as points_mod
    from gui import settings

    started = []
    monkeypatch.setattr(points_mod, "start_long_op",
                        lambda *a, **k: started.append(a) or None)
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"points": {}}), encoding="utf-8")
    dock = points_mod.PointsDock(real_main_window)
    dock.set_root_path(root)
    settings.state.set(points_mod.overlay_markers.OVERLAY_MARKERS_KEY,
                       {points_mod._point_key("probe"): "uuid-point",
                        points_mod._point_key("other"): "uuid-point-2"})
    adapter = object()
    real_main_window.connection.board = SimpleNamespace(adapter=adapter)
    _arm_the_door(monkeypatch)

    dock._clear_point_markers()

    # start_long_op(connection, widgets, worker_fn, on_success, on_error,
    #               adapter, uuids) — the LAST two arguments are the handoff.
    assert started, "the removal must reach its worker"
    assert sorted(started[0][-1]) == ["uuid-point", "uuid-point-2"], \
        "the whole point layer goes in ONE call"
    assert started[0][-2] is adapter, "the shared adapter arrives with it"


# ── Т2-7 (plan_2026_09_22_door_t2_7_imprint_place) — "Take from selection" ───
# The last unsigned door read outside the form widget: the opt-in checkbox of the
# Place Imprint page reads the live board to fill X/Y (the selection centre minus
# the chosen parent's live base). THREE things were wrong at once, and each one
# gets its own cells below:
#
#   (а) no `socket_busy` at all — the read went straight into the shared kipy REQ
#       socket the ~400 ms selection tick holds 16.4 % of a run;
#   (б) `except Exception -> None` dressed the door's refusal up as "Cannot derive
#       the node offset from the selection — enter the X/Y offset manually", i.e.
#       as the SELECTION's fault (the false message Т2-4's mutation m9 found once
#       already, on the re-hang path);
#   (в) the parent-node branch calls `resolve_base_live_position` DIRECTLY, not
#       `read_record_live_pose`, so an Entity parent answers a different question
#       than TreesDock's "Read current position" does. That is a FINDING for the
#       plan (probe row D prints both answers), NOT something this step repairs.
#
# THE SLOT IS CALLED DIRECTLY in every cell, never through `setChecked(True)`: an
# exception raised inside a Qt slot kills PyQt6 with SIGABRT (measured twice,
# independently), so a mutation that removes the sign must make these cells FAIL
# rather than abort the interpreter. The mutation harness carries the same note
# next to its Т2-7 entries.

def _place_dock(window, tmp_path):
    """The window's OWN Place Imprint dock over a throwaway root, with a tree
    whose anchor is (origin) — so the base resolve is pure arithmetic and a cell
    needs no fake board to answer it. Returns (dock, connection)."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({
        "entities": [{"name": "PARENT", "cell": "c_parent"}],
        "trees": [{"name": "main", "anchor": {"origin": True},
                   "nodes": [{"ref": "PARENT", "kind": "placement",
                              "xy": [0.0, 0.0]}]}],
    }), encoding="utf-8")
    dock = window._dock_hub.imprint_place_dock
    dock.set_root_path(root)                 # built BEFORE the door is armed
    dock.tree_combo.setCurrentText("main")
    dock.parent_combo.setCurrentIndex(0)     # the top-level sentinel
    dock.set_board_selection([], [SimpleNamespace(fp=SimpleNamespace(
        position=Vector2.from_xy_mm(10.0, 10.0)))])
    return dock, window.connection


def _tick_take_from_selection(dock) -> None:
    """Set the checkbox ON and run the slot ONCE — the call is DIRECT on purpose
    (see the section note above)."""
    dock.from_selection_check.blockSignals(True)
    dock.from_selection_check.setChecked(True)
    dock.from_selection_check.blockSignals(False)
    dock._on_from_selection_toggled(True)


def _no_message_boxes(monkeypatch, module) -> list:
    """Record every message box text instead of showing one."""
    told: list = []
    monkeypatch.setattr(module.QMessageBox, "warning",
                        lambda *args, **kwargs: told.append(args[2]))
    return told


def test_the_take_from_selection_fill_does_not_read_the_board_unsigned(
        real_main_window, tmp_path, monkeypatch, armed_door):
    """Т2-7 — with the door ARMED the checkbox fills X/Y without a single refusal:
    the whole read, the `_live_adapter()` door read included, sits under
    `ui_thread_board_read(reason=...)` — and no false "enter the X/Y manually"
    message appears (the (б) half of the property).

    Mutation check: drop that `with` and this fails with the refusal naming
    gui/docks/imprint_place.py's `_live_adapter` line; move the door read out of
    the sign and the recorded messages show the false modal."""
    import gui.docks.imprint_place as imprint_place_mod

    dock, connection = _place_dock(real_main_window, tmp_path)
    connection.board = SimpleNamespace(adapter=object())
    told = _no_message_boxes(monkeypatch, imprint_place_mod)

    _tick_take_from_selection(dock)          # must not raise

    assert (dock.x_spin.value(), dock.y_spin.value()) == (10.0, 10.0), \
        "the offset is the selection centre minus the origin-anchored tree base"
    assert told == [], f"the fill refused something it should have answered: {told!r}"


def test_a_busy_socket_refuses_the_fill_and_tells_the_user(
        real_main_window, tmp_path, monkeypatch):
    """Т2-7 (а) — door rule 3: the fill reads the SHARED adapter, so a busy socket
    refuses instead of interleaving a second REQ transaction into the tick's
    in-flight one. The deferral is not the refusal: the user is told when the ONE
    retry meets the busy socket AGAIN, in words that name both the cause and the
    outcome (the checkbox is off).

    Mutation check: run the fill inline instead of deferring and the "deferred
    EXACTLY once" assertion fails while the rest passes (a busy socket then eats
    the click silently); hand the helper a bare `lambda: None` as on_still_busy and
    `told` stays empty."""
    import gui.docks.imprint_place as imprint_place_mod
    import gui.worker as worker_mod

    dock, connection = _place_dock(real_main_window, tmp_path)
    connection.board = SimpleNamespace(adapter=object())
    connection.long_op_active = True          # the poll tick is in flight
    told = _no_message_boxes(monkeypatch, imprint_place_mod)
    scheduled: list = []
    monkeypatch.setattr(worker_mod.QTimer, "singleShot",
                        lambda delay, callback: scheduled.append((delay, callback)))

    _tick_take_from_selection(dock)

    assert (dock.x_spin.value(), dock.y_spin.value()) == (0.0, 0.0), \
        "the fill ran while the tick owned the socket"
    assert len(scheduled) == 1, "the fill must be deferred EXACTLY once"
    assert scheduled[0][0] == worker_mod.SNAPSHOT_REFRESH_RETRY_DELAY_MS, \
        "the retry uses the helper's own delay, not a number of its own"
    assert told == [], "the deferral itself is not the refusal"

    scheduled[0][1]()                         # the retry, socket STILL busy

    assert told and "busy" in told[0] and "turned off" in told[0], (
        "the last attempt must TELL the user, naming the cause AND the outcome "
        f"(the checkbox went off) — got {told!r}")
    assert dock.from_selection_check.isChecked() is False, \
        "the action must leave a visible trace, not a tick that did nothing"


def test_the_deferred_fill_still_happens_when_the_socket_frees_up(
        real_main_window, tmp_path, monkeypatch):
    """Т2-7 (а) — the deferral is not cosmetic: the ONE armed retry finishes the
    fill the user asked for, with the door ARMED over the retry, so the deferred
    read is proven to go through the sign as well.

    Mutation check: run the fill inline and nothing is armed, so the retry below
    never fires and the offset stays 0."""
    import gui.docks.imprint_place as imprint_place_mod
    import gui.worker as worker_mod

    dock, connection = _place_dock(real_main_window, tmp_path)
    connection.board = SimpleNamespace(adapter=object())
    connection.long_op_active = True
    told = _no_message_boxes(monkeypatch, imprint_place_mod)
    scheduled: list = []
    monkeypatch.setattr(worker_mod.QTimer, "singleShot",
                        lambda delay, callback: scheduled.append((delay, callback)))

    _tick_take_from_selection(dock)

    assert len(scheduled) == 1
    connection.long_op_active = False         # the tick finished
    _arm_the_door(monkeypatch)                # the retry must go through the sign
    scheduled[0][1]()

    assert (dock.x_spin.value(), dock.y_spin.value()) == (10.0, 10.0), \
        "the deferred attempt must complete the fill the user asked for"
    assert told == [], f"a completed fill must not warn: {told!r}"
    assert dock.from_selection_check.isChecked() is True, \
        "the box stays on: the user's chosen mode was honoured"


def test_the_race_window_of_a_busy_socket_is_named_not_silent(
        real_main_window, tmp_path, monkeypatch, caplog):
    """Т2-7 (а) — the helper's own check and the read are two different instants: a
    tick can start in between. That window is where a refusal would go silent
    again, so the continuation checks the socket ITSELF one line before the read
    and BOTH names it in the Log (caplog) and tells the user.

    Mutation check: drop that inner `socket_busy` check and `told` stays empty —
    the fill goes on and lands a second transaction on a busy socket; drop only the
    Log line and the caplog assertion fails (silence looks like a no-op)."""
    import gui.docks.imprint_place as imprint_place_mod

    dock, connection = _place_dock(real_main_window, tmp_path)
    connection.board = SimpleNamespace(adapter=object())
    connection.long_op_active = False         # free at the helper's own check...
    monkeypatch.setattr(imprint_place_mod, "socket_busy", lambda _c: True)
    told = _no_message_boxes(monkeypatch, imprint_place_mod)
    caplog.clear()

    _tick_take_from_selection(dock)           # ...busy one line before the read

    assert (dock.x_spin.value(), dock.y_spin.value()) == (0.0, 0.0), \
        "the read must not go out while the socket is busy"
    assert told and told[0] == imprint_place_mod._BOARD_BUSY_TEXT, told
    assert any(imprint_place_mod._BOARD_BUSY_TEXT in r.message
               for r in caplog.records), \
        "the race window is named in the Log as well — the same reason, two channels"
    assert dock.from_selection_check.isChecked() is False


def test_a_real_base_failure_still_asks_for_manual_entry(
        real_main_window, tmp_path, monkeypatch):
    """Т2-7 (б) — the OTHER half of the distinction: a base that genuinely does not
    resolve keeps the pre-existing modal and unchecks the box. Without this cell
    the door-refusal cell next to it could pass by silencing EVERY failure — the
    difference between a true refusal and a false message is exactly this pair.

    Mutation check: make the fill swallow everything and return a bogus offset and
    this fails on the modal text."""
    import gui.docks.imprint_place as imprint_place_mod

    dock, connection = _place_dock(real_main_window, tmp_path)
    connection.board = SimpleNamespace(adapter=object())
    told = _no_message_boxes(monkeypatch, imprint_place_mod)
    # A parent ref that is NOT a node of the chosen tree: the honest failure —
    # the selection is fine, the parent is not there.
    dock.parent_combo.addItem("ghost", "GHOST")
    dock.parent_combo.setCurrentIndex(dock.parent_combo.findData("GHOST"))

    _tick_take_from_selection(dock)

    assert told and "enter the X/Y offset manually" in told[0], told
    assert dock.from_selection_check.isChecked() is False


def test_a_door_refusal_is_never_dressed_up_as_the_selections_fault(
        real_main_window, tmp_path, monkeypatch, armed_door):
    """Т2-7 (б) — the property the fix exists for: a DOOR violation must not be
    folded into the "the selection did not resolve" answer. The sign is stubbed
    out here on purpose, so the guard refuses for real, and the cell asserts both
    halves — the refusal ESCAPES (it is not swallowed) and the false modal is NOT
    shown.

    Mutation check: put a bare `except Exception: return None` back around the read
    and this fails on the empty `pytest.raises` — the user would be told to enter
    the offset by hand while the real problem was the door (m9 of Т2-4, again)."""
    import gui.docks.imprint_place as imprint_place_mod
    from contextlib import contextmanager

    dock, connection = _place_dock(real_main_window, tmp_path)
    connection.board = SimpleNamespace(adapter=object())
    told = _no_message_boxes(monkeypatch, imprint_place_mod)

    @contextmanager
    def _no_sign(**_kwargs):
        yield
    monkeypatch.setattr(imprint_place_mod, "ui_thread_board_read", _no_sign)

    with pytest.raises(UiThreadBoardReadRefused):
        _tick_take_from_selection(dock)

    assert not any("enter the X/Y offset manually" in m for m in told), (
        "a door refusal must never be reported as the selection's fault — got "
        f"{told!r}")


@pytest.mark.parametrize("branch", ["top level (the tree anchor)", "the node parent"])
def test_the_connection_snapshot_reaches_each_branchs_resolver(
        real_main_window, tmp_path, monkeypatch, branch):
    """Т2-7 — the snapshot is handed to BOTH branches of `_live_parent_base_mm`,
    one cell per branch (rule 35: a row that names two branches is a promise that
    both were driven). The resolver is SPYED rather than run — the identity
    question it would answer needs a board, and this cell is about the ARGUMENT
    arriving (the resolvers' own empty-snapshot behaviour is celled in
    tests/test_tree_position_snapshot_threading.py).

    Mutation check: drop `snapshot=snapshot` from the branch this row names and the
    spy records None."""
    import gui.docks.imprint_place as imprint_place_mod
    import kicadstamp.tree_position as tp_mod

    dock, connection = _place_dock(real_main_window, tmp_path)
    connection.board = SimpleNamespace(adapter=object())
    snapshot = [SimpleNamespace(role="R1", cluster="C", fp=object())]
    connection._snapshot = snapshot            # the connection's own cache
    _no_message_boxes(monkeypatch, imprint_place_mod)
    seen: list = []
    monkeypatch.setattr(
        tp_mod, "_anchor_base_live_position",
        lambda *a, **k: seen.append(k) or (Vector2.from_xy_mm(1.0, 2.0), 0.0))
    monkeypatch.setattr(
        tp_mod, "resolve_base_live_position",
        lambda *a, **k: seen.append(k) or Vector2.from_xy_mm(1.0, 2.0))
    if branch.startswith("the node"):
        index = dock.parent_combo.findData("PARENT")
        assert index >= 0, "the rig's PARENT node is not in the parent combo"
        dock.parent_combo.setCurrentIndex(index)

    _tick_take_from_selection(dock)

    assert seen, f"the {branch} branch never reached its resolver"
    assert seen[0].get("snapshot") is snapshot, (
        f"the {branch} branch lost the connection's snapshot — got "
        f"{seen[0].get('snapshot')!r}")


def test_the_connections_empty_snapshot_is_passed_through_not_refused(
        real_main_window, tmp_path, monkeypatch):
    """Т2-7 — the CLASS property (already caught twice in this заход): before the
    first poll `connection._snapshot` IS `[]`, and an empty snapshot is NOT "no
    snapshot" — the resolvers treat a falsy snapshot as the historical sweep
    (Кl). So the dock must hand the EMPTY LIST through and still fill the offset,
    rather than read the emptiness as "the board is not there" and refuse.

    Mutation check: make the dock pass `snapshot or None` and the identity
    assertion fails; make it refuse on an empty snapshot and the fill stays 0."""
    import gui.docks.imprint_place as imprint_place_mod
    import kicadstamp.tree_position as tp_mod

    dock, connection = _place_dock(real_main_window, tmp_path)
    connection.board = SimpleNamespace(adapter=object())
    assert connection._snapshot == [], "the pre-first-poll state this cell is about"
    _no_message_boxes(monkeypatch, imprint_place_mod)
    seen: list = []
    monkeypatch.setattr(
        tp_mod, "_anchor_base_live_position",
        lambda *a, **k: seen.append(k) or (Vector2.from_xy_mm(1.0, 2.0), 0.0))

    _tick_take_from_selection(dock)

    # The selection centre (10, 10) minus the SPYED base (1, 2) — the fill ran.
    assert (dock.x_spin.value(), dock.y_spin.value()) == (9.0, 8.0), \
        "an empty snapshot must not stop the fill (the resolver sweeps)"
    assert seen and seen[0].get("snapshot") == [], (
        "the empty list must travel as the CONNECTION has it — normalising it to "
        "None would be the dock's own opinion, not the cache's state")
