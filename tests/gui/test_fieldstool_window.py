# tests/gui/test_fieldstool_window.py
"""
gui.fieldstool_window.MainWindow tests are headless AND .kicad_sch-
mutation-free except for the one deliberate "apply succeeds" test, which
DOES write a throwaway tmp_path fixture (never anything under the real
repo) to prove the whole staging -> Apply -> write chain actually
round-trips, not just that each piece is individually plausible.
"""
from pathlib import Path
from unittest.mock import Mock

from PyQt6.QtWidgets import QDialog, QListWidget

from gui import fieldstool_window as fieldstool_window_mod
from gui.docks.pending import PendingEdit
from kicadstamp.explore import Selected
from kicadstamp.schematic_editing import EditReport
from tests.fieldstool_fixtures import sch_file, symbol_block
from tests.gui.conftest import _FakeConnection


def _write_root(tmp_path, *blocks):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(*blocks), encoding="utf-8")
    return root


def _selected(ref, role, cluster):
    return Selected(ref=ref, role=role, cluster=cluster, sheet=[], nets={}, fp=None)


class _FakeAdapter:
    def __init__(self, missing_fields=()):
        """missing_fields: {(ref, field_name), ...} — has_field() answers
        False for exactly these pairs, True for everything else (matches
        every real footprint having the field, the common case)."""
        self.calls = []
        self._fps = {}
        self._missing_fields = set(missing_fields)

    def get_footprint(self, ref):
        fp = self._fps.setdefault(ref, Mock())
        fp.ref = ref
        return fp

    def has_field(self, fp, field_name):
        ref = fp.ref
        return (ref, field_name) not in self._missing_fields

    def set_field_values_bulk(self, updates, description):
        self.calls.append((updates, description))


class _FakeBoard:
    def __init__(self, missing_fields=()):
        self.adapter = _FakeAdapter(missing_fields)


def _run_sync(connection, widgets, fn, on_success, on_error, *args):
    """Fake start_long_op — runs fn(*args) and on_success() immediately, on
    the calling thread (same reasoning as tests/gui/test_role_cluster_tree.py's
    own _run_sync: avoids spinning a real QThread that outlives the test)."""
    result = fn(*args)
    on_success(result)
    return "fake-controller"


def _connect_board(fieldstool_window, monkeypatch, missing_fields=()):
    """Wires a fake connected board + synchronous start_long_op — _on_stage()
    now writes to the live board over IPC (2026-08-03 redesign), so it needs
    both to run at all instead of hanging on a real "Not connected" dialog."""
    monkeypatch.setattr(fieldstool_window_mod, "start_long_op", _run_sync)
    board = _FakeBoard(missing_fields)
    fieldstool_window.connection.board = board
    return board


def test_set_root_sheet_populates_components_and_combos(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A", cluster="Cl_A"))
    fieldstool_window._set_root_sheet(root)

    assert len(fieldstool_window._components) == 1
    assert fieldstool_window.role_combo.findText("R_A") != -1
    assert fieldstool_window.cluster_combo.findText("Cl_A") != -1


def test_rescan_fires_on_components_changed_callback(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A"))
    calls = []
    fieldstool_window.on_components_changed = lambda: calls.append(1)

    fieldstool_window._set_root_sheet(root)  # _set_root_sheet triggers _rescan() internally

    assert calls == [1]


def test_rescan_with_no_callback_set_does_not_raise(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A"))
    assert fieldstool_window.on_components_changed is None

    fieldstool_window._set_root_sheet(root)  # must not raise AttributeError


def test_group_picked_sets_targets_and_prefills_combo(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1", "R2"], role="R_A"))
    fieldstool_window._set_root_sheet(root)

    fieldstool_window._on_group_picked("Role", "R_A", ["R1", "R2"])

    assert sorted(fieldstool_window._current_targets) == ["R1", "R2"]
    assert fieldstool_window.role_combo.currentText() == "R_A"
    assert fieldstool_window.stage_button.isEnabled()


def test_group_picked_also_fills_the_other_field_when_it_happens_to_be_uniform(fieldstool_window, tmp_path):
    # Grouped by Role, but both members also happen to share the same
    # Cluster — _prefill_combos_for_refs should fill that too, not just the
    # field being grouped by.
    root = _write_root(
        tmp_path,
        symbol_block(["R1"], role="R_A", cluster="Cl_A"),
        symbol_block(["R2"], role="R_A", cluster="Cl_A"),
    )
    fieldstool_window._set_root_sheet(root)

    fieldstool_window._on_group_picked("Role", "R_A", ["R1", "R2"])

    assert fieldstool_window.role_combo.currentText() == "R_A"
    assert fieldstool_window.cluster_combo.currentText() == "Cl_A"


def test_leaf_picked_prefills_both_combos_from_existing_values(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A", cluster="Cl_A"))
    fieldstool_window._set_root_sheet(root)

    fieldstool_window._on_tree_leaf_picked(["R1"])

    assert fieldstool_window.role_combo.currentText() == "R_A"
    assert fieldstool_window.cluster_combo.currentText() == "Cl_A"


def test_leaf_picked_prefers_the_live_board_value_over_the_stale_schematic_one(
        fieldstool_window, tmp_path):
    """2026-08-04, Denis live: "прописал роли... но когда кликаю эти диоды,
    ...роль... не видно" — a ref already Staged but not yet Applied has its
    NEW value only on the live board; re-selecting it must show that, not
    the schematic's pre-Stage value."""
    root = _write_root(tmp_path, symbol_block(["D5"], role="OLD_ROLE", cluster="OLD_CLUSTER"))
    fieldstool_window._set_root_sheet(root)
    fieldstool_window.set_live_snapshot([_selected("D5", "NEW_ROLE", "NEW_CLUSTER")])

    fieldstool_window._on_tree_leaf_picked(["D5"])

    assert fieldstool_window.role_combo.currentText() == "NEW_ROLE"
    assert fieldstool_window.cluster_combo.currentText() == "NEW_CLUSTER"


def test_leaf_picked_falls_back_to_schematic_value_when_ref_not_in_live_snapshot(
        fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A", cluster="Cl_A"))
    fieldstool_window._set_root_sheet(root)
    fieldstool_window.set_live_snapshot([_selected("OTHER_REF", "X", "Y")])

    fieldstool_window._on_tree_leaf_picked(["R1"])

    assert fieldstool_window.role_combo.currentText() == "R_A"
    assert fieldstool_window.cluster_combo.currentText() == "Cl_A"


def test_leaf_picked_shows_pending_indicator_when_live_diverges_from_schematic(
        fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["D5"], role="OLD_ROLE"))
    fieldstool_window._set_root_sheet(root)
    fieldstool_window.set_live_snapshot([_selected("D5", "NEW_ROLE", None)])

    fieldstool_window._on_tree_leaf_picked(["D5"])

    assert "D5" in fieldstool_window.pending_label.text()


def test_leaf_picked_clears_pending_indicator_when_live_matches_schematic(
        fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A"))
    fieldstool_window._set_root_sheet(root)
    fieldstool_window.set_live_snapshot([_selected("R1", "R_A", None)])

    fieldstool_window._on_tree_leaf_picked(["R1"])

    assert fieldstool_window.pending_label.text() == ""


def test_pending_indicator_updates_on_a_fresh_poll_tick_without_a_reclick(
        fieldstool_window, tmp_path):
    """_recompute_pending() (fired on every set_live_snapshot(), e.g. the
    poll tick right after Stage writes) must refresh the indicator for
    whatever is CURRENTLY selected, not just on the next explicit click."""
    root = _write_root(tmp_path, symbol_block(["D5"], role="OLD_ROLE"))
    fieldstool_window._set_root_sheet(root)
    fieldstool_window._on_tree_leaf_picked(["D5"])
    assert fieldstool_window.pending_label.text() == ""

    fieldstool_window.set_live_snapshot([_selected("D5", "NEW_ROLE", None)])

    assert "D5" in fieldstool_window.pending_label.text()


def test_leaf_picked_clears_combos_when_targets_differ(fieldstool_window, tmp_path):
    root = _write_root(
        tmp_path,
        symbol_block(["R1"], role="R_A", cluster="Cl_A"),
        symbol_block(["R2"], role="R_B", cluster="Cl_B"),
    )
    fieldstool_window._set_root_sheet(root)
    # Prime the combos with a stale value from an earlier pick — must be
    # cleared, not left showing a misleading single value, once the new
    # pick turns out to be mixed.
    fieldstool_window.role_combo.setCurrentText("STALE")
    fieldstool_window.cluster_combo.setCurrentText("STALE")

    fieldstool_window._on_tree_leaf_picked(["R1", "R2"])

    assert fieldstool_window.role_combo.currentText() == ""
    assert fieldstool_window.cluster_combo.currentText() == ""


def test_stage_writes_role_cluster_to_the_live_board(fieldstool_window, tmp_path, monkeypatch):
    """2026-08-03 redesign — Stage writes straight to the board over IPC
    (same mechanism RoleClusterTreeDock's Clear all uses) instead of a JSON
    queue; Apply's diff picks it up once the board's snapshot reflects it."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()

    updates, _description = board.adapter.calls[0]
    assert (board.adapter._fps["R1"], "Role", "NEW") in updates


def test_role_combo_does_not_silently_rewrite_a_differently_cased_typed_value(
        fieldstool_window, tmp_path, monkeypatch):
    """2026-08-04, Denis live: typed "C_Out_Bulk" instead of the existing
    "C_OUT_BULK" and fieldstool staged the OLD value back — Qt's default
    combo completer is case-insensitive and silently snaps typed text to
    an existing item's casing on Enter, before _run_stage ever reads
    currentText(). configure_searchable() (now case-sensitive, see
    gui/docks/_common.py) fixes this."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="C_OUT_BULK"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("C_Out_Bulk")
    fieldstool_window.role_combo.lineEdit().returnPressed.emit()

    updates, _description = board.adapter.calls[0]
    assert (board.adapter._fps["R1"], "Role", "C_Out_Bulk") in updates


def test_enter_in_role_combo_stages_immediately(fieldstool_window, tmp_path, monkeypatch):
    """2026-08-04, Denis: "долго Stage жать" — Enter in either field must
    do exactly what clicking Stage does, guards included."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window.role_combo.lineEdit().returnPressed.emit()

    updates, _description = board.adapter.calls[0]
    assert (board.adapter._fps["R1"], "Role", "NEW") in updates


def test_enter_in_cluster_combo_stages_immediately(fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.cluster_combo.setCurrentText("NEW_CLUSTER")
    fieldstool_window.cluster_combo.lineEdit().returnPressed.emit()

    updates, _description = board.adapter.calls[0]
    assert (board.adapter._fps["R1"], "Cluster", "NEW_CLUSTER") in updates


def test_stage_success_fires_on_board_written_callback(fieldstool_window, tmp_path, monkeypatch):
    """2026-08-03 fix: the automatic poll tick never refreshes on its own
    once already connected (see MainWindow._poll's docstring), so without
    this hook Pending changes never saw a Stage write until the user
    happened to click Refresh — found live right after the Apply redesign."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    _connect_board(fieldstool_window, monkeypatch)
    calls = []
    fieldstool_window.on_board_written = lambda: calls.append(1)

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()

    assert calls == [1]


def test_stage_skips_a_target_missing_the_field_but_writes_the_rest(
        fieldstool_window, tmp_path, monkeypatch):
    """2026-08-04 (handoff_2026_08_04_arch_review_handoff_and_cluster_bug.md,
    'Разрыв B'): FB3-like case — a footprint missing Cluster used to make
    set_field_value's fatal ValidationError roll back the WHOLE batch.
    Stage must now skip just that (ref, field) pair and still write the
    rest, same has_field guard Clear all already uses."""
    root = _write_root(tmp_path, symbol_block(["R1", "R2"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch, missing_fields={("R2", "Cluster")})

    fieldstool_window._set_targets(["R1", "R2"])
    fieldstool_window.role_combo.setCurrentText("NEW_ROLE")
    fieldstool_window.cluster_combo.setCurrentText("NEW_CLUSTER")
    warned = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(a[2])))

    fieldstool_window._on_stage()

    updates, _description = board.adapter.calls[0]
    assert (board.adapter._fps["R1"], "Role", "NEW_ROLE") in updates
    assert (board.adapter._fps["R1"], "Cluster", "NEW_CLUSTER") in updates
    assert (board.adapter._fps["R2"], "Role", "NEW_ROLE") in updates
    assert not any(u[1] == "Cluster" and u[0] is board.adapter._fps["R2"] for u in updates)
    assert len(warned) == 1 and "R2 (Cluster)" in warned[0]


def test_stage_with_every_target_missing_the_field_writes_nothing_but_does_not_fail(
        fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch, missing_fields={("R1", "Cluster")})

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.cluster_combo.setCurrentText("NEW_CLUSTER")
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    fieldstool_window._on_stage()

    assert board.adapter.calls == []  # set_field_values_bulk never called — nothing to write


def test_stage_with_no_target_does_nothing(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)

    fieldstool_window._set_targets([])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()  # no targets -> returns before touching the board

    assert fieldstool_window._pending_edits == []


def test_apply_blocked_when_kicad_running(fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    _connect_board(fieldstool_window, monkeypatch)
    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()
    # Simulate the main GUI's next poll tick picking up the board write —
    # _on_stage() itself doesn't recompute the diff (see its docstring).
    fieldstool_window.set_live_snapshot([_selected("R1", "NEW", None)])

    monkeypatch.setattr(fieldstool_window_mod, "check_kicad_not_running",
                        lambda force: (_ for _ in ()).throw(RuntimeError("kicad running")))
    write_calls = []
    monkeypatch.setattr(fieldstool_window_mod, "write_files",
                        lambda *a, **k: write_calls.append(1) or ([], []))
    shown = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    fieldstool_window._on_apply()

    assert write_calls == []  # never reached the write path
    assert shown == ["info"]
    assert len(fieldstool_window._pending_edits) == 1  # still pending, nothing consumed


def test_apply_with_nothing_pending_shows_message(fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)

    monkeypatch.setattr(fieldstool_window_mod, "check_kicad_not_running", lambda force: None)
    shown = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    fieldstool_window._on_apply()
    assert shown == ["info"]


def test_apply_succeeds_writes_file_and_clears_pending(fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    _connect_board(fieldstool_window, monkeypatch)
    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()
    fieldstool_window.set_live_snapshot([_selected("R1", "NEW", None)])

    monkeypatch.setattr(fieldstool_window_mod, "check_kicad_not_running", lambda force: None)
    monkeypatch.setattr(fieldstool_window, "_confirm_apply", lambda report: True)
    shown = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    fieldstool_window._on_apply()

    assert '"Role" "NEW"' in root.read_text(encoding="utf-8")
    assert fieldstool_window._pending_edits == []  # _rescan() found the schematic now matches the board
    assert shown == ["info"]
    assert Path(str(root) + ".bak").exists()


def test_ensure_fields_blocked_when_kicad_running(fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["FB3"], role="PI_FILTER_FB"))  # no Cluster
    fieldstool_window._set_root_sheet(root)

    monkeypatch.setattr(fieldstool_window_mod, "check_kicad_not_running",
                        lambda force: (_ for _ in ()).throw(RuntimeError("kicad running")))
    write_calls = []
    monkeypatch.setattr(fieldstool_window_mod, "write_files",
                        lambda *a, **k: write_calls.append(1) or ([], []))
    shown = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    fieldstool_window._on_ensure_fields()

    assert write_calls == []
    assert shown == ["info"]


def test_ensure_fields_with_nothing_missing_shows_message(fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD", cluster="SOME"))
    fieldstool_window._set_root_sheet(root)

    monkeypatch.setattr(fieldstool_window_mod, "check_kicad_not_running", lambda force: None)
    shown = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    fieldstool_window._on_ensure_fields()
    assert shown == ["info"]


def test_ensure_fields_adds_a_missing_cluster_property_without_touching_role(
        fieldstool_window, tmp_path, monkeypatch):
    """The FB3 case: Role present, Cluster entirely absent — Ensure fields
    must add an empty Cluster property and leave Role's own value alone."""
    root = _write_root(tmp_path, symbol_block(["FB3"], role="PI_FILTER_FB"))
    fieldstool_window._set_root_sheet(root)

    monkeypatch.setattr(fieldstool_window_mod, "check_kicad_not_running", lambda force: None)
    monkeypatch.setattr(fieldstool_window, "_confirm_apply", lambda report: True)
    shown = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    fieldstool_window._on_ensure_fields()

    text = root.read_text(encoding="utf-8")
    assert '"Role" "PI_FILTER_FB"' in text  # untouched
    assert '"Cluster" ""' in text  # newly added, empty
    assert shown == ["info"]
    assert Path(str(root) + ".bak").exists()


def test_pending_refs_reflects_only_refs_with_a_discrepancy(fieldstool_window, tmp_path):
    """pending_refs (2026-08-03) is what the main GUI's Components tree
    filters "Not yet applied" mode by — R1's live Role disagrees with its
    schematic value and must be included; R2's matches and must not."""
    root = _write_root(
        tmp_path,
        symbol_block(["R1"], role="OLD"),
        symbol_block(["R2"], role="SAME"),
    )
    fieldstool_window._set_root_sheet(root)

    fieldstool_window.set_live_snapshot([
        _selected("R1", "NEW", None),
        _selected("R2", "SAME", None),
    ])

    assert fieldstool_window.pending_refs == {"R1"}


def test_pending_refs_empty_before_any_live_snapshot(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)

    assert fieldstool_window.pending_refs == set()


def _report(count):
    return [EditReport(file="x.kicad_sch", refs=[f"R{i}"], field="Role",
                       old_value="OLD", new_value="NEW", kind="replace")
            for i in range(count)]


def test_confirm_apply_lists_one_row_per_report_entry(fieldstool_window, monkeypatch):
    """2026-08-03 regression: a plain QMessageBox with one line per changed
    ref grew into an enormous window on a real board (hundreds of pending
    edits), pushing OK/Cancel off-screen — the summary must be a
    height-capped, scrollable list instead."""
    captured = []

    def fake_exec(self):
        captured.append(self)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)

    confirmed = fieldstool_window._confirm_apply(_report(300))

    assert confirmed is True
    list_widget = captured[0].findChild(QListWidget)
    assert list_widget.count() == 300
    assert list_widget.maximumHeight() <= 300


def test_confirm_apply_returns_false_on_cancel(fieldstool_window, monkeypatch):
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    confirmed = fieldstool_window._confirm_apply(_report(1))

    assert confirmed is False


# ── shared-connection public hooks ──────────────────────────────────────────

def test_set_live_selection_sets_targets_and_enables_stage(fieldstool_window):
    fieldstool_window.set_live_selection(["R1", "R2"])
    assert fieldstool_window._current_targets == ["R1", "R2"]
    assert fieldstool_window.stage_button.isEnabled()


def test_set_live_selection_empty_is_noop(fieldstool_window):
    fieldstool_window._set_targets(["R1"])
    fieldstool_window.set_live_selection([])
    assert fieldstool_window._current_targets == ["R1"]


def test_set_connection_status_updates_label(fieldstool_window):
    fieldstool_window.set_connection_status(None)
    assert "Connected" in fieldstool_window.status_label.text()
    fieldstool_window.set_connection_status("boom")
    assert "boom" in fieldstool_window.status_label.text()


def test_connection_is_the_injected_one(qapp):
    """MainWindow never creates its own BoardConnection — the embedding main
    GUI always injects its own (one kipy client, one REQ socket, one polling
    loop feeding this window through set_connection_status()/
    set_live_selection())."""
    shared = _FakeConnection()
    window = fieldstool_window_mod.MainWindow(connection=shared)
    assert window.connection is shared


def test_push_selection_to_board_gated_during_long_op(fieldstool_window):
    """While a background long op (Extract/Redraw) holds the shared socket,
    a tree-pick must not fire select_items() into it (that would interleave
    a second request into the op's in-flight REQ)."""
    from types import SimpleNamespace
    select_calls = []

    class _Adapter:
        def get_footprint(self, ref):
            return SimpleNamespace(ref=ref)

        def select_items(self, footprints):
            select_calls.append([fp.ref for fp in footprints])

    fieldstool_window.connection.board = SimpleNamespace(adapter=_Adapter())
    # is_connected is a property (board is not None) -> the pick would
    # normally reach adapter.select_items(); only the long-op flag blocks it.

    fieldstool_window.connection.long_op_active = True
    fieldstool_window._push_selection_to_board(["R1"])
    assert select_calls == []

    fieldstool_window.connection.long_op_active = False
    fieldstool_window._push_selection_to_board(["R1", "R2"])
    assert select_calls == [["R1", "R2"]]


# ── Sync from schematic (2026-08-27) ──────────────────────────────────────

def test_sync_from_schematic_writes_old_value_to_the_live_board(
        fieldstool_window, tmp_path, monkeypatch):
    """The SCHEMATIC value (PendingEdit.old_value — "OLD"), NOT the board's
    current one ("NEW"), is written back over IPC — the whole point of
    "Sync from schematic"."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)
    fieldstool_window._pending_edits = [PendingEdit("R1", "Role", "OLD", "NEW")]
    monkeypatch.setattr(fieldstool_window, "_confirm_sync", lambda edits: True)

    fieldstool_window._on_sync_from_schematic()

    updates, _description = board.adapter.calls[0]
    assert (board.adapter._fps["R1"], "Role", "OLD") in updates


def test_sync_from_schematic_skips_mismatched_edits(
        fieldstool_window, tmp_path, monkeypatch):
    """A mismatched edit (refdes/symbol mismatch) is never written — same
    exclusion Apply's own edits_to_fields_cfg() applies — only the ordinary
    edit reaches the adapter."""
    root = _write_root(tmp_path, symbol_block(["R1", "R2"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)
    fieldstool_window._pending_edits = [
        PendingEdit("R1", "Role", "OLD", "NEW"),
        PendingEdit("R2", "Role", "A", "B", mismatched=True),
    ]
    monkeypatch.setattr(fieldstool_window, "_confirm_sync", lambda edits: True)

    fieldstool_window._on_sync_from_schematic()

    updates, _description = board.adapter.calls[0]
    assert (board.adapter._fps["R1"], "Role", "OLD") in updates
    # R2 (mismatched) was never even requested from the adapter — its
    # footprint is not in _fps at all, and certainly not in the updates.
    assert "R2" not in board.adapter._fps
    assert {u[0].ref for u in updates} == {"R1"}


def test_sync_from_schematic_fires_on_board_written_callback(
        fieldstool_window, tmp_path, monkeypatch):
    """Same "Pending changes never sees a write until told" fix as Stage — a
    successful sync calls on_board_written so the diff refreshes against the
    now-matching board."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    _connect_board(fieldstool_window, monkeypatch)
    fieldstool_window._pending_edits = [PendingEdit("R1", "Role", "OLD", "NEW")]
    monkeypatch.setattr(fieldstool_window, "_confirm_sync", lambda edits: True)
    calls = []
    fieldstool_window.on_board_written = lambda: calls.append(1)

    fieldstool_window._on_sync_from_schematic()

    assert calls == [1]


def test_sync_from_schematic_requires_connection(
        fieldstool_window, tmp_path, monkeypatch):
    """Not connected -> a warning, nothing is written (and no long op is
    started)."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    warnings = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    fieldstool_window._pending_edits = [PendingEdit("R1", "Role", "OLD", "NEW")]

    fieldstool_window._on_sync_from_schematic()

    assert warnings
    assert fieldstool_window._pending_edits  # unchanged, nothing written


def test_sync_from_schematic_confirm_cancelled_writes_nothing(
        fieldstool_window, tmp_path, monkeypatch):
    """A cancelled confirmation dialog aborts before any IPC write."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)
    fieldstool_window._pending_edits = [PendingEdit("R1", "Role", "OLD", "NEW")]
    monkeypatch.setattr(fieldstool_window, "_confirm_sync", lambda edits: False)

    fieldstool_window._on_sync_from_schematic()

    assert board.adapter.calls == []
