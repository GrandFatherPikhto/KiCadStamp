# tests/gui/test_role_cluster_tree.py
from unittest.mock import Mock

from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import QMessageBox

from gui.schema_model import SchematicComponent
from kicadstamp.explore import Selected

from gui import settings
import gui.docks.role_cluster_tree as role_cluster_tree_mod
from gui.docks.role_cluster_tree import RoleClusterTreeDock
from kicadstamp.exceptions import ValidationError


def _diverged(ref, role, cluster):
    """A live-board Selected whose Role disagrees with the schematic value
    passed alongside it — the minimum needed to put `ref` into fieldstool_
    window.pending_refs, which "Not yet applied" mode now filters by
    (2026-08-03: it used to list every schematic component unconditionally)."""
    return Selected(ref=ref, role=(role or "") + "_DIVERGED", cluster=cluster,
                    sheet=[], nets={}, fp=None)


class FakeSelected:
    def __init__(self, ref, role, cluster):
        self.ref, self.role, self.cluster = ref, role, cluster
        self.fp = object()


class FakeAdapter:
    def __init__(self, missing_fields=None):
        self.calls = []
        self.select_items_calls = []
        # fp -> set of field names that footprint reports as absent (not
        # just empty) — has_field() below consults this, defaulting every
        # footprint to "has both fields" unless a test says otherwise.
        self._missing_fields = missing_fields or {}

    def has_field(self, fp, field_name):
        return field_name not in self._missing_fields.get(fp, set())

    def set_field_values_bulk(self, updates, description):
        self.calls.append((updates, description))

    def select_items(self, items):
        self.select_items_calls.append(items)


class FakeBoard:
    def __init__(self):
        self.adapter = FakeAdapter()


def _select_item(dock, item) -> None:
    index = dock.tree.model().indexFromItem(item)
    dock.tree.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select)


def _run_sync(connection, widgets, fn, on_success, on_error, *args):
    """Fake start_long_op — runs fn(*args) and on_success() immediately, on
    the calling thread. Avoids spinning a REAL QThread in a test (which
    outlives the test function and crashes the process on teardown) while
    still exercising the full _on_delete_selected/_on_clear_all handler,
    not just the lower-level _run_clear/_finish_clear pair — same reasoning
    tests/gui/test_placer_dock.py's test_on_redraw_dispatches_to_worker
    keeps that boundary as its own separate (dispatch-only) test instead of
    letting a real thread run during a behavior test."""
    result = fn(*args)
    on_success(result)
    return "fake-controller"


def test_group_by_persists_across_restart(main_window):
    dock = RoleClusterTreeDock(main_window)
    assert dock.group_by.currentIndex() == 0  # Role, the default

    dock.group_by.setCurrentIndex(1)  # Cluster
    assert settings.load()["tree_group_by"] == 1

    restarted = RoleClusterTreeDock(main_window)  # simulates a fresh launch
    assert restarted.group_by.currentIndex() == 1


def _find_item(model, text):
    def walk(item):
        for row in range(item.rowCount()):
            child = item.child(row)
            if child.text() == text:
                return child
            found = walk(child)
            if found is not None:
                return found
        return None
    return walk(model.invisibleRootItem())


def test_clicking_a_cluster_group_node_fires_cluster_picked_signal(main_window):
    dock = RoleClusterTreeDock(main_window)
    dock.group_by.setCurrentIndex(1)  # Cluster grouping
    dock.set_footprints([
        FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER"),
        FakeSelected("C2", "C_IN", "Channel_2/PI_FILTER"),
    ])

    picked = []
    dock.cluster_picked.connect(picked.append)

    model = dock.tree.model()
    top_level = _find_item(model, "Channel_1")
    nested = _find_item(model, "PI_FILTER")  # first match, under Channel_1

    dock._on_clicked(model.indexFromItem(top_level))
    assert picked == ["Channel_1"]

    dock._on_clicked(model.indexFromItem(nested))
    assert picked == ["Channel_1", "Channel_1/PI_FILTER"]


def test_leaf_click_selects_the_footprint_on_the_live_board(main_window):
    board = FakeBoard()
    main_window.connection.board = board
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    c1 = FakeSelected("C1", "C_IN", "Channel_1")
    dock.set_footprints([c1])

    leaf = _find_item(dock.tree.model(), "C1")
    dock._on_clicked(dock.tree.model().indexFromItem(leaf))

    assert board.adapter.select_items_calls == [[c1.fp]]


def test_leaf_click_does_not_touch_the_board_during_a_long_op(main_window):
    """Regression: found live — "ConnectionError: Error receiving reply from
    KiCad: Operation canceled" on a tree click. select_items() ran
    synchronously with no long_op_active guard, unlike fieldstool_window.py's
    identical _push_selection_to_board() — a click landing while a
    background poll/Extract/Redraw holds the shared kipy socket interleaved
    into its in-flight REQ transaction and corrupted it. Became much easier
    to hit once MainWindow.request_refresh() started firing a background
    poll right after every Stage/Clear all write (2026-08-03)."""
    board = FakeBoard()
    main_window.connection.board = board
    main_window.connection.long_op_active = True
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    dock.set_footprints([FakeSelected("C1", "C_IN", "Channel_1")])

    leaf = _find_item(dock.tree.model(), "C1")
    dock._on_clicked(dock.tree.model().indexFromItem(leaf))

    assert board.adapter.select_items_calls == []


def test_collapse_all_survives_a_later_rebuild(main_window):
    # Regression test for the "snaps back open on the next poll tick" bug:
    # _rebuild() runs on every set_footprints() call (simulating a ~2s poll
    # tick), and its expanded-state restore used to treat "nothing expanded"
    # as "first build ever" every time, re-expanding depth 0 regardless of
    # whether the user had just collapsed everything on purpose.
    dock = RoleClusterTreeDock(main_window)
    dock.group_by.setCurrentIndex(1)  # Cluster grouping
    footprints = [
        FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER"),
        FakeSelected("C2", "C_IN", "Channel_2/PI_FILTER"),
    ]
    dock.set_footprints(footprints)

    top_level = _find_item(dock.tree.model(), "Channel_1")
    assert dock.tree.isExpanded(dock.tree.model().indexFromItem(top_level))

    dock.tree_collapse_all()
    assert not dock.tree.isExpanded(dock.tree.model().indexFromItem(top_level))

    dock.set_footprints(footprints)  # simulates the next poll tick
    top_level = _find_item(dock.tree.model(), "Channel_1")
    assert not dock.tree.isExpanded(dock.tree.model().indexFromItem(top_level))


def test_leaf_click_and_role_mode_do_not_fire_cluster_picked_signal(main_window):
    dock = RoleClusterTreeDock(main_window)
    dock.set_footprints([FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER")])

    picked = []
    dock.cluster_picked.connect(picked.append)

    # Role grouping (the default) — clicking a group here is a Role, not a Cluster.
    # _build_flat() suffixes group labels with a "(count)", unlike _build_hierarchical().
    model = dock.tree.model()
    role_group = _find_item(model, "C_IN (1)")
    dock._on_clicked(model.indexFromItem(role_group))
    assert picked == []

    # Cluster grouping, but a LEAF (component) click, not a group.
    dock.group_by.setCurrentIndex(1)
    model = dock.tree.model()
    leaf = _find_item(model, "C1 (C_IN)")  # role now shown next to the ref (2026-08-13)
    dock._on_clicked(model.indexFromItem(leaf))
    assert picked == []


# ── Role shown next to ref in Cluster grouping (2026-08-13, plan
# components_tree_show_role) ─────────────────────────────────────────────

def test_cluster_grouping_leaf_text_includes_the_role(main_window):
    dock = RoleClusterTreeDock(main_window)
    dock.group_by.setCurrentIndex(1)  # Cluster grouping
    dock.set_footprints([FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER")])

    model = dock.tree.model()
    # exact-text lookup: the leaf is now "C1 (C_IN)", not a bare "C1"
    assert _find_item(model, "C1 (C_IN)") is not None
    assert _find_item(model, "C1") is None


def test_role_grouping_leaf_text_does_not_repeat_the_role(main_window):
    """Role grouping already shows the role as the parent group — repeating it
    per leaf would be noise (the plan's "не дублировать то, что уже видно")."""
    dock = RoleClusterTreeDock(main_window)
    dock.set_footprints([FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER")])  # Role grouping (default)

    model = dock.tree.model()
    assert _find_item(model, "C1") is not None
    assert _find_item(model, "C1 (C_IN)") is None


def test_leaf_without_a_role_stays_bare_ref_in_both_groupings(main_window):
    dock = RoleClusterTreeDock(main_window)
    dock.set_footprints([FakeSelected("C1", None, "Channel_1/PI_FILTER")])
    assert _find_item(dock.tree.model(), "C1") is not None

    dock.group_by.setCurrentIndex(1)  # Cluster grouping
    assert _find_item(dock.tree.model(), "C1") is not None
    assert _find_item(dock.tree.model(), "C1 ()") is None  # no empty parens


# ── Schematic ("Not yet applied") mode — needs a real fieldstool_dock to
#    route into, so these use the real_main_window fixture (see
#    tests/gui/conftest.py), not the bare main_window stub. ─────────────────

def test_schematic_mode_not_restored_in_init_but_restore_method_works(real_main_window):
    dock = real_main_window.tree_dock
    assert not dock.mode_checkbox.isChecked()

    dock.mode_checkbox.setChecked(True)
    assert settings.load()["tree_schematic_mode"] is True

    restarted = RoleClusterTreeDock(real_main_window)
    assert not restarted.mode_checkbox.isChecked()  # NOT auto-restored in __init__
    restarted.restore_mode_from_settings()
    assert restarted.mode_checkbox.isChecked()


def test_schematic_leaf_click_routes_to_fieldstool_and_opens_tab(real_main_window):
    dock = real_main_window.tree_dock
    window = real_main_window.fieldstool_dock.window
    window._components = [
        SchematicComponent("R1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    window.set_live_snapshot([_diverged("R1", "R_A", "Cl_A")])
    dock.mode_checkbox.setChecked(True)

    leaf_picked = Mock()
    open_fieldstool = Mock()
    real_main_window.fieldstool_dock.window._on_tree_leaf_picked = leaf_picked
    real_main_window.open_fieldstool = open_fieldstool

    leaf = _find_item(dock.tree.model(), "R1")
    dock._on_clicked(dock.tree.model().indexFromItem(leaf))

    leaf_picked.assert_called_once_with(["R1"])
    open_fieldstool.assert_called_once()


def test_schematic_group_click_uses_hierarchical_cluster_value(real_main_window):
    dock = real_main_window.tree_dock
    window = real_main_window.fieldstool_dock.window
    window._components = [
        SchematicComponent("R1", "R_A", "Channel_1/PI_FILTER", "root.kicad_sch", 0, divergent=False),
    ]
    window.set_live_snapshot([_diverged("R1", "R_A", "Channel_1/PI_FILTER")])
    dock.mode_checkbox.setChecked(True)
    dock.group_by.setCurrentIndex(1)  # Cluster grouping -> hierarchical, matches live mode

    group_picked = Mock()
    real_main_window.fieldstool_dock.window._on_group_picked = group_picked
    real_main_window.open_fieldstool = Mock()

    nested = _find_item(dock.tree.model(), "PI_FILTER")
    dock._on_clicked(dock.tree.model().indexFromItem(nested))

    group_picked.assert_called_once_with("Cluster", "Channel_1/PI_FILTER", ["R1"])


def test_schematic_divergent_component_gets_warning_marker(real_main_window):
    dock = real_main_window.tree_dock
    window = real_main_window.fieldstool_dock.window
    window._components = [
        SchematicComponent("R1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=True),
    ]
    window.set_live_snapshot([_diverged("R1", "R_A", "Cl_A")])
    dock.mode_checkbox.setChecked(True)

    assert _find_item(dock.tree.model(), "R1 ⚠") is not None


def test_schematic_mode_hides_a_component_with_no_pending_discrepancy(real_main_window):
    """2026-08-03: "Not yet applied" used to list every schematic component
    unconditionally — found live: a component stayed listed there even after
    a successful Apply left nothing outstanding ("по факту, поскольку
    изменения на схеме уже применились и плата обновилась, они уже не
    должны оставаться в списке Not yet applied"). Now filtered to
    pending_refs: R1 has a live-board discrepancy and must show up, R2's
    live value already matches its schematic value and must not."""
    dock = real_main_window.tree_dock
    window = real_main_window.fieldstool_dock.window
    window._components = [
        SchematicComponent("R1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
        SchematicComponent("R2", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    window.set_live_snapshot([
        _diverged("R1", "R_A", "Cl_A"),
        Selected(ref="R2", role="R_A", cluster="Cl_A", sheet=[], nets={}, fp=None),
    ])
    dock.mode_checkbox.setChecked(True)

    assert _find_item(dock.tree.model(), "R1") is not None
    assert _find_item(dock.tree.model(), "R2") is None


def test_set_footprints_does_not_clobber_active_schematic_view(real_main_window):
    dock = real_main_window.tree_dock
    window = real_main_window.fieldstool_dock.window
    window._components = [
        SchematicComponent("SCH1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    window.set_live_snapshot([_diverged("SCH1", "R_A", "Cl_A")])
    dock.mode_checkbox.setChecked(True)
    assert _find_item(dock.tree.model(), "SCH1") is not None

    dock.set_footprints([FakeSelected("PCB1", "R_B", "Cl_B")])  # simulates a live poll tick

    assert _find_item(dock.tree.model(), "SCH1") is not None
    assert _find_item(dock.tree.model(), "PCB1") is None


def test_refresh_schematic_view_noop_in_live_mode_rebuilds_in_schematic_mode(real_main_window):
    dock = real_main_window.tree_dock
    window = real_main_window.fieldstool_dock.window
    window._components = [
        SchematicComponent("R1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]

    # Live mode (default) -> no-op: refresh_schematic_view() must not touch the
    # model at all (asserting the model OBJECT is unchanged, not its contents —
    # this dev machine may have a real KiCad reachable, which would otherwise
    # make an emptiness assumption flaky).
    model_before = dock.tree.model()
    dock.refresh_schematic_view()
    assert dock.tree.model() is model_before

    dock.mode_checkbox.setChecked(True)
    window._components = [
        SchematicComponent("R2", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    window.set_live_snapshot([_diverged("R2", "R_A", "Cl_A")])
    dock.refresh_schematic_view()  # schematic mode -> rebuilds with the fresh list
    assert _find_item(dock.tree.model(), "R2") is not None


# ── Delete selected / Clear all (2026-08-03) ─────────────────────────────

def test_delete_selected_clears_role_and_cluster_on_selected_leaf(main_window, monkeypatch, caplog):
    monkeypatch.setattr(role_cluster_tree_mod, "start_long_op", _run_sync)
    board = FakeBoard()
    main_window.connection.board = board
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    c1, c2 = FakeSelected("C1", "C_IN", "Channel_1"), FakeSelected("C2", "C_IN", "Channel_2")
    dock.set_footprints([c1, c2])

    leaf = _find_item(dock.tree.model(), "C1")
    _select_item(dock, leaf)
    dock._on_delete_selected()

    assert len(board.adapter.calls) == 1
    updates, _description = board.adapter.calls[0]
    assert set(updates) == {(c1.fp, "Role", ""), (c1.fp, "Cluster", "")}
    assert any("Cleared Role/Cluster on 1 component" in r.message for r in caplog.records)


def test_delete_selected_on_a_group_clears_every_member(main_window, monkeypatch):
    monkeypatch.setattr(role_cluster_tree_mod, "start_long_op", _run_sync)
    board = FakeBoard()
    main_window.connection.board = board
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    dock.group_by.setCurrentIndex(1)  # Cluster grouping -> hierarchical
    c1 = FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER")
    c2 = FakeSelected("C2", "C_IN", "Channel_1/PI_FILTER")
    other = FakeSelected("C3", "C_IN", "Channel_2/PI_FILTER")
    dock.set_footprints([c1, c2, other])

    group = _find_item(dock.tree.model(), "Channel_1")
    _select_item(dock, group)
    dock._on_delete_selected()

    updates, _description = board.adapter.calls[0]
    touched_fps = {fp for fp, _field, _value in updates}
    assert touched_fps == {c1.fp, c2.fp}  # other cluster's member untouched


def test_delete_selected_with_nothing_selected_shows_message(main_window, caplog):
    board = FakeBoard()
    main_window.connection.board = board
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    dock.set_footprints([FakeSelected("C1", "C_IN", "Channel_1")])

    dock._on_delete_selected()

    assert board.adapter.calls == []
    assert any("Nothing selected" in r.message for r in caplog.records)


def test_delete_selected_not_connected_shows_message(main_window, caplog):
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    dock.set_footprints([FakeSelected("C1", "C_IN", "Channel_1")])
    leaf = _find_item(dock.tree.model(), "C1")
    _select_item(dock, leaf)

    dock._on_delete_selected()  # main_window.connection.board is None by default

    assert any("Not connected" in r.message for r in caplog.records)


def test_clear_all_declined_writes_nothing(main_window, monkeypatch):
    board = FakeBoard()
    main_window.connection.board = board
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    dock.set_footprints([FakeSelected("C1", "C_IN", "Channel_1")])

    monkeypatch.setattr(role_cluster_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    dock._on_clear_all()

    assert board.adapter.calls == []


def test_clear_all_confirmed_writes_every_footprint(main_window, monkeypatch, caplog):
    monkeypatch.setattr(role_cluster_tree_mod, "start_long_op", _run_sync)
    board = FakeBoard()
    main_window.connection.board = board
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    c1, c2 = FakeSelected("C1", "C_IN", "Channel_1"), FakeSelected("C2", "C_IN", "Channel_2")
    dock.set_footprints([c1, c2])

    monkeypatch.setattr(role_cluster_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dock._on_clear_all()

    updates, _description = board.adapter.calls[0]
    touched_fps = {fp for fp, _field, _value in updates}
    assert touched_fps == {c1.fp, c2.fp}
    assert any("Cleared Role/Cluster on all 2 component" in r.message for r in caplog.records)


def test_clear_all_skips_footprint_missing_a_field_instead_of_rolling_back_the_batch(
        main_window, monkeypatch, caplog):
    """Regression: found live on a real 287-component board — one footprint
    (FB15) had no Cluster field at all, and set_field_values_bulk wraps the
    whole batch in ONE commit, so that single footprint rolled back the
    ENTIRE Clear all, clearing nothing. A footprint missing Role or Cluster
    entirely must be excluded from the batch up front (and reported), not
    sent and rolled back."""
    monkeypatch.setattr(role_cluster_tree_mod, "start_long_op", _run_sync)
    ok_fp = Mock()
    missing_fp = Mock()
    missing_fp.ref = "FB15"
    board = FakeBoard()
    board.adapter._missing_fields = {missing_fp: {"Cluster"}}
    main_window.connection.board = board
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    c1 = FakeSelected("C1", "C_IN", "Channel_1")
    c1.fp = ok_fp
    c2 = FakeSelected("FB15", None, None)
    c2.fp = missing_fp
    dock.set_footprints([c1, c2])

    monkeypatch.setattr(role_cluster_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dock._on_clear_all()

    updates, _description = board.adapter.calls[0]
    touched_fps = {fp for fp, _field, _value in updates}
    assert touched_fps == {ok_fp}
    assert any("Cleared Role/Cluster on all 1 component" in r.message for r in caplog.records)
    assert any("Skipped 1 without Role/Cluster field: FB15" in r.message for r in caplog.records)


def test_clear_all_success_fires_on_board_written_callback(main_window, monkeypatch):
    """2026-08-03 fix: the automatic poll tick never refreshes on its own
    once already connected (see MainWindow._poll's docstring), so without
    this hook Pending changes never saw a Clear all write until the user
    happened to click Refresh — the exact "wrote to the board but staged
    nothing" gap the Apply redesign was meant to close."""
    monkeypatch.setattr(role_cluster_tree_mod, "start_long_op", _run_sync)
    board = FakeBoard()
    main_window.connection.board = board
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    dock.set_footprints([FakeSelected("C1", "C_IN", "Channel_1")])
    calls = []
    dock.on_board_written = lambda: calls.append(1)

    monkeypatch.setattr(role_cluster_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dock._on_clear_all()

    assert calls == [1]


def test_clear_all_with_nothing_on_board_shows_message(main_window, monkeypatch, caplog):
    board = FakeBoard()
    main_window.connection.board = board
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)

    called = []
    monkeypatch.setattr(role_cluster_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: called.append(1) or QMessageBox.StandardButton.Yes))
    dock._on_clear_all()

    assert board.adapter.calls == []
    assert not called  # never even asked — nothing to clear
    assert any("Nothing to clear" in r.message for r in caplog.records)


def test_buttons_disabled_in_schematic_mode(main_window):
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    assert dock.delete_selected_button.isEnabled()
    assert dock.clear_all_button.isEnabled()

    dock.mode_checkbox.setChecked(True)
    assert not dock.delete_selected_button.isEnabled()
    assert not dock.clear_all_button.isEnabled()

    dock.mode_checkbox.setChecked(False)
    assert dock.delete_selected_button.isEnabled()
    assert dock.clear_all_button.isEnabled()


def test_clear_op_surfaces_validation_error_from_adapter(main_window, caplog):
    class _FailingAdapter:
        def has_field(self, fp, field_name):
            return True

        def set_field_values_bulk(self, updates, description):
            raise ValidationError("boom: missing field")

    class _FailingBoard:
        adapter = _FailingAdapter()

    main_window.connection.board = _FailingBoard()
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    c1 = FakeSelected("C1", "C_IN", "Channel_1")
    dock.set_footprints([c1])

    dock._do_clear([c1.fp], "Cleared {count}")

    assert any("boom" in r.message for r in caplog.records)


def test_on_delete_selected_dispatches_to_worker(main_window, monkeypatch):
    """The button handler must NOT block the UI thread — it collects/
    validates on the UI thread then hands off to start_long_op, same
    discipline as PlacerDock's Redraw (see gui/worker.py)."""
    board = FakeBoard()
    main_window.connection.board = board
    dock = RoleClusterTreeDock(main_window, connection=main_window.connection)
    c1 = FakeSelected("C1", "C_IN", "Channel_1")
    dock.set_footprints([c1])
    leaf = _find_item(dock.tree.model(), "C1")
    _select_item(dock, leaf)

    captured = {}

    def _fake_start(connection, widgets, fn, on_success, on_error, *args):
        captured["connection"] = connection
        captured["widgets"] = widgets
        captured["args"] = args
        return "fake-controller"

    monkeypatch.setattr(role_cluster_tree_mod, "start_long_op", _fake_start)

    dock._on_delete_selected()

    assert dock._active_op == "fake-controller"
    assert captured["connection"] is main_window.connection
    assert captured["widgets"] == (dock.delete_selected_button, dock.clear_all_button)
    assert captured["args"][0]["footprints"] == [c1.fp]
    assert board.adapter.calls == []  # not actually run — dispatch only
