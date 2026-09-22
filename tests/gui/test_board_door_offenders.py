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
from types import SimpleNamespace

import pytest

from gui.worker import snapshot_refresh_supported
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import DEFAULT_TIMEOUT_MS


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
        real_main_window, monkeypatch):
    """Т2-4 — door rule 3: the resolve touches the SHARED adapter, so a busy
    socket refuses instead of interleaving a second REQ transaction into the
    tick's in-flight one ("Operation canceled"). The next attempt recalculates.

    Mutation check: drop the `socket_busy` early return and this fails — the
    resolver is entered on a busy socket."""
    import gui.docks.trees_dock as td_mod

    seen = []
    monkeypatch.setattr(td_mod, "_reparented_offset",
                        lambda *a, **k: seen.append(k) or (None, None, 0.0))
    dock = real_main_window._dock_hub.trees_dock
    real_main_window.connection.board = SimpleNamespace(adapter=object())
    real_main_window.connection.long_op_active = True      # the tick is in flight

    proceed, shift = dock._rehang_offset_or_ask(
        SimpleNamespace(name="probe_tree"),
        SimpleNamespace(ref="R_DEBUG", kind="external"), None, None)

    assert (proceed, shift) == (False, None), "a busy socket must refuse the move"
    assert seen == [], "the resolve ran while the tick owned the socket"


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
