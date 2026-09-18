# tests/gui/test_fieldstool_window.py
"""
gui.fieldstool_window.MainWindow tests are headless AND .kicad_sch-
mutation-free except for the one deliberate "apply succeeds" test, which
DOES write a throwaway tmp_path fixture (never anything under the real
repo) to prove the whole staging -> Apply -> write chain actually
round-trips, not just that each piece is individually plausible.
"""
import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget

from gui import fieldstool_window as fieldstool_window_mod
from gui.docks.pending import PendingEdit
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.explore import Selected
from kicadstamp.schematic_editing import EditReport
from tests.fieldstool_fixtures import sch_file, symbol_block
from tests.gui.conftest import _FakeConnection, _pump


def _write_root(tmp_path, *blocks):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(*blocks), encoding="utf-8")
    return root


def _selected(ref, role, cluster, role_field_exists=True, cluster_field_exists=True,
              symbol_uuid=None):
    """role_field_exists/cluster_field_exists — a physically-absent board
    field (2026-08-27 handoff pending_exclude_missing_board_fields); default
    True so callers not exercising that scenario are unaffected.

    symbol_uuid (Т5) fills fp.sheet_path.path[-1] — the key the override store
    uses, which Stage resolves from THIS cached snapshot. None keeps fp=None: a
    target Stage cannot key, and therefore cannot record (С12)."""
    fp = (SimpleNamespace(sheet_path=SimpleNamespace(
        path=[SimpleNamespace(value=symbol_uuid)])) if symbol_uuid else None)
    return Selected(ref=ref, role=role, cluster=cluster,
                    role_field_exists=role_field_exists,
                    cluster_field_exists=cluster_field_exists,
                    sheet=[], nets={}, fp=fp)


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


def _store_for(fieldstool_window, tmp_path):
    """Give the window a real PROJECT store — Т5's Stage records into it, so
    without one the operation refuses by design. A file next to a throwaway
    profile, exactly like the real thing."""
    from kicadstamp.field_overrides import FieldOverrides
    from kicadstamp.utils.paths import overrides_path_for_config
    profile = tmp_path / "prof.sexp"
    profile.write_text(dict_to_sexp({"layer": "B.Cu"}), encoding="utf-8")
    fieldstool_window.set_overrides_store(
        FieldOverrides(overrides_path_for_config(str(profile))))
    return fieldstool_window.overrides


def _recorded(fieldstool_window):
    """The store's records AS THEY ARE ON DISK: Stage re-reads the file before
    writing it (so a stale in-process copy can never lose another pane's
    records), which also means the window's own object is a fresh copy."""
    from kicadstamp.field_overrides import load_field_overrides
    return load_field_overrides(str(fieldstool_window.overrides.path)).records()


def test_stage_records_role_cluster_in_the_override_store(
        fieldstool_window, tmp_path, monkeypatch):
    """Т5 REWROTE this test — it used to pin the opposite ("Stage writes straight
    to the board over IPC"). With the store in force our value WINS over the
    board, so a board write would be invisible: Stage records into the store, and
    the board is not touched at all."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)
    _store_for(fieldstool_window, tmp_path)
    fieldstool_window.set_live_snapshot(
        [_selected("R1", "OLD", None, symbol_uuid="uuid-R1")])

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()

    assert [(r.symbol_uuid, r.field, r.value)
            for r in _recorded(fieldstool_window)] == [
        ("uuid-R1", ROLE_FIELD_NAME, "NEW")]
    assert board.adapter.calls == []


def test_role_combo_does_not_silently_rewrite_a_differently_cased_typed_value(
        fieldstool_window, tmp_path, monkeypatch):
    """2026-08-04, Denis live: typed "C_Out_Bulk" instead of the existing
    "C_OUT_BULK" and fieldstool staged the OLD value back — Qt's default
    combo completer is case-insensitive and silently snaps typed text to
    an existing item's casing on Enter, before the value is ever read.
    configure_searchable() (now case-sensitive, see gui/docks/_common.py)
    fixes this."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="C_OUT_BULK"))
    fieldstool_window._set_root_sheet(root)
    _connect_board(fieldstool_window, monkeypatch)
    _store_for(fieldstool_window, tmp_path)
    fieldstool_window.set_live_snapshot(
        [_selected("R1", "C_OUT_BULK", None, symbol_uuid="uuid-R1")])

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("C_Out_Bulk")
    fieldstool_window.role_combo.lineEdit().returnPressed.emit()

    assert [(r.field, r.value) for r in _recorded(fieldstool_window)] == [
        (ROLE_FIELD_NAME, "C_Out_Bulk")]


def test_enter_in_role_combo_stages_immediately(fieldstool_window, tmp_path, monkeypatch):
    """2026-08-04, Denis: "долго Stage жать" — Enter in either field must
    do exactly what clicking Stage does, guards included."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    _connect_board(fieldstool_window, monkeypatch)
    _store_for(fieldstool_window, tmp_path)
    fieldstool_window.set_live_snapshot(
        [_selected("R1", "OLD", None, symbol_uuid="uuid-R1")])

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window.role_combo.lineEdit().returnPressed.emit()

    assert [(r.field, r.value) for r in _recorded(fieldstool_window)] == [
        (ROLE_FIELD_NAME, "NEW")]


def test_enter_in_cluster_combo_stages_immediately(fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    _connect_board(fieldstool_window, monkeypatch)
    _store_for(fieldstool_window, tmp_path)
    fieldstool_window.set_live_snapshot(
        [_selected("R1", "OLD", None, symbol_uuid="uuid-R1")])

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.cluster_combo.setCurrentText("NEW_CLUSTER")
    fieldstool_window.cluster_combo.lineEdit().returnPressed.emit()

    assert [(r.field, r.value) for r in _recorded(fieldstool_window)] == [
        (CLUSTER_FIELD_NAME, "NEW_CLUSTER")]


def test_stage_fires_the_store_hook_and_never_the_board_one(
        fieldstool_window, tmp_path, monkeypatch):
    """Т5 REWROTE this test, and the reason is worth keeping: it used to demand
    BOTH hooks fire ("tell both owners"), which looked like symmetry and was a
    bug in the making.

    on_board_written is wired to MainWindow.request_refresh, which runs a REAL
    board refresh over IPC (gui/main_window.py), on the thread that drives the
    shared socket. A store record does not change the board, so firing it bought
    nothing and cost a round-trip per Stage click. One record, ONE owner: the
    store hook reloads the file wherever another pane holds a copy (the Refs
    table — and that reload recomputes the three-sided diff by itself).

    So the board hook is asserted SILENT. If it ever fires here again, Stage has
    gone back to poking a board it never touched."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    _connect_board(fieldstool_window, monkeypatch)
    _store_for(fieldstool_window, tmp_path)
    fieldstool_window.set_live_snapshot(
        [_selected("R1", "OLD", None, symbol_uuid="uuid-R1")])
    overrides_fired, board_fired = [], []
    fieldstool_window.on_overrides_written = lambda: overrides_fired.append(1)
    fieldstool_window.on_board_written = lambda: board_fired.append(1)

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()

    assert overrides_fired == [1]
    assert board_fired == []


def test_re_picking_a_target_shows_the_stored_value_not_the_stale_board_one(
        fieldstool_window, tmp_path, monkeypatch):
    """Т5's half of the 2026-08-04 fix (see _prefill_combos_for_refs).

    The combos show the value IN FORCE. Before Т5 that was "board if the
    snapshot knows the ref, else schematic"; now the staged value lives in the
    STORE, so a board-only prefill would show the OLD value again — Denis's exact
    live complaint ("прописал роли... но когда кликаю эти диоды, ...роль... не
    видно"). The board never hears about NEW here, yet re-picking the very same
    target must bring NEW back into the combo."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    # Deliberately NO connection: recording needs none (the snapshot is where
    # the keys come from), and a pick only tries to highlight the board when
    # there IS one.
    _store_for(fieldstool_window, tmp_path)
    fieldstool_window.set_live_snapshot(
        [_selected("R1", "OLD", None, symbol_uuid="uuid-R1")])

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()

    fieldstool_window.role_combo.setCurrentText("")
    fieldstool_window._on_tree_leaf_picked(["R1"])

    assert fieldstool_window.role_combo.currentText() == "NEW"


def test_stage_refuses_a_target_without_a_key_and_records_the_rest(
        fieldstool_window, tmp_path, monkeypatch, caplog):
    """Т5 replaced the old per-field skip ('Разрыв B', 2026-08-04): the store
    needs NO field on the footprint, so a missing Cluster can no longer refuse
    anything. What refuses a target now is having no KEY — a ref the last board
    read does not carry — and that one is named, while the rest is still
    recorded: the same "never roll the whole batch back" intent (С12)."""
    root = _write_root(tmp_path, symbol_block(["R1", "R2"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)
    _store_for(fieldstool_window, tmp_path)
    # R2 is NOT in the snapshot: no uuid, so no key.
    fieldstool_window.set_live_snapshot(
        [_selected("R1", "OLD", None, symbol_uuid="uuid-R1")])

    fieldstool_window._set_targets(["R1", "R2"])
    fieldstool_window.role_combo.setCurrentText("NEW_ROLE")
    fieldstool_window.cluster_combo.setCurrentText("NEW_CLUSTER")
    caplog.clear()

    fieldstool_window._on_stage()

    recorded = {(r.symbol_uuid, r.field, r.value)
                for r in _recorded(fieldstool_window)}
    assert ("uuid-R1", ROLE_FIELD_NAME, "NEW_ROLE") in recorded
    assert ("uuid-R1", CLUSTER_FIELD_NAME, "NEW_CLUSTER") in recorded
    assert "R2" in caplog.text          # refused BY NAME in the status/summary line
    assert board.adapter.calls == []


def test_stage_with_no_recordable_target_writes_nothing_but_does_not_fail(
        fieldstool_window, tmp_path, monkeypatch, caplog):
    """No target of the selection is in the last board read: nothing to record,
    and nothing blows up — one line saying so (the old version of this test pinned
    the same shape for a footprint missing the Cluster field, a case the store
    does not care about at all)."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)
    _store_for(fieldstool_window, tmp_path)
    fieldstool_window.set_live_snapshot([])

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.cluster_combo.setCurrentText("NEW_CLUSTER")
    caplog.clear()

    fieldstool_window._on_stage()

    assert _recorded(fieldstool_window) == []
    assert board.adapter.calls == []
    assert "nothing was recorded" in caplog.text


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


def _capture_apply_dialog(fieldstool_window, monkeypatch, report):
    """Runs _confirm_apply with QDialog.exec stubbed out and returns the dialog
    it built, so the guards below can read its widgets headlessly (no modal
    event loop)."""
    captured = []

    def fake_exec(self):
        captured.append(self)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    fieldstool_window._confirm_apply(report)
    assert captured, "the dialog was never built"
    return captured[0]


def _dialog_texts(dialog):
    return " ".join(lbl.text() for lbl in dialog.findChildren(QLabel))


def test_confirm_apply_names_the_direction_and_advises_f8(fieldstool_window, monkeypatch):
    """Т2.3/п.4 (plan_2026_09_16... Э2): the dialog must say that Apply
    REPLACES the schematic values with the board ones — the old wording
    ("About to write N change(s)") said nothing about direction, which is
    exactly how a single OK reverted an edited schematic. The rows name both
    sides too, instead of a bare "old -> new"."""
    dialog = _capture_apply_dialog(fieldstool_window, monkeypatch, _report(1))

    texts = _dialog_texts(dialog)
    assert "REPLACES the values in the SCHEMATIC with the values from the BOARD" in texts
    assert "Update PCB from Schematic (F8)" in texts
    row = dialog.findChild(QListWidget).item(0).text()
    assert "schematic" in row and "board" in row


def test_confirm_apply_warns_when_a_board_value_is_empty(fieldstool_window, monkeypatch):
    """Т2.3/п.2: an empty BOARD value means Apply CLEARS the schematic value
    (the live case measured 16.09 — the board was empty and one OK wiped the
    Role/Cluster of every such component). The line must be there, with the
    count."""
    report = _report(1)
    report[0].new_value = ""
    dialog = _capture_apply_dialog(fieldstool_window, monkeypatch, report)

    texts = _dialog_texts(dialog)
    assert "1 value(s) on the board are empty" in texts
    assert "CLEARED" in texts


def test_confirm_apply_omits_the_erase_line_without_empty_board_values(
        fieldstool_window, monkeypatch):
    """Same guard, negative half: no empty board value — no erase line (the
    dialog must not cry wolf on an ordinary apply)."""
    dialog = _capture_apply_dialog(fieldstool_window, monkeypatch, _report(2))

    texts = _dialog_texts(dialog)
    assert "empty" not in texts
    assert "CLEARED" not in texts


def test_confirm_apply_cancel_is_the_default_button(fieldstool_window, monkeypatch):
    """Т2.3/п.3: Enter must write nothing — the safe answer to this dialog is
    "no" far more often than "yes", so Cancel carries the default role and OK
    must not steal it back through Qt's autoDefault."""
    dialog = _capture_apply_dialog(fieldstool_window, monkeypatch, _report(1))

    box = dialog.findChild(QDialogButtonBox)
    cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
    ok = box.button(QDialogButtonBox.StandardButton.Ok)
    assert cancel.isDefault() is True
    assert ok.isDefault() is False
    assert ok.autoDefault() is False


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
        fieldstool_window, tmp_path, monkeypatch, caplog):
    """Not connected -> ONE ERROR line in the Log (never a modal —
    plan_2026_09_11_no_modals_and_busy_kicad X.1), nothing is written and no
    long op is started."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)

    def _no_boxes(*a, **k):
        raise AssertionError("a connection-state error must not open a QMessageBox")
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "warning", _no_boxes)
    fieldstool_window._pending_edits = [PendingEdit("R1", "Role", "OLD", "NEW")]
    caplog.clear()

    fieldstool_window._on_sync_from_schematic()

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Connect to KiCad first." in errors[0].message
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


def test_sync_skip_reports_missing_field_separately(
        fieldstool_window, tmp_path, monkeypatch):
    """A target whose footprint EXISTS but has no such field lands in
    result['missing_field'] (NOT a generic 'skipped') — the finish handler
    then explains the real, far more common cause: the field is in the
    schematic but was never created on the board footprint (Ensure fields /
    add by hand + Update PCB from Schematic)."""
    root = _write_root(tmp_path, symbol_block(["R1", "R2"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch,
                           missing_fields={("R2", "Role")})

    result = fieldstool_window._run_sync_from_schematic(
        {"edits": [("R1", "Role", "OLD"), ("R2", "Role", "OLD")]})

    assert result["missing_field"] == ["R2 (Role)"]
    assert result["not_found"] == []
    updates, _description = board.adapter.calls[0]
    assert {u[0].ref for u in updates} == {"R1"}


def test_sync_skip_reports_not_found_ref_separately(
        fieldstool_window, tmp_path, monkeypatch):
    """A ref not on the live board AT ALL (get_footprint -> None) lands in
    result['not_found'] — a genuinely different failure, worded differently
    in the finish handler."""
    root = _write_root(tmp_path, symbol_block(["R1", "R2"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    board = _connect_board(fieldstool_window, monkeypatch)
    orig_get = board.adapter.get_footprint
    board.adapter.get_footprint = lambda ref: None if ref == "R2" else orig_get(ref)

    result = fieldstool_window._run_sync_from_schematic(
        {"edits": [("R1", "Role", "OLD"), ("R2", "Role", "OLD")]})

    assert result["not_found"] == ["R2 (Role)"]
    assert result["missing_field"] == []
    updates, _description = board.adapter.calls[0]
    assert {u[0].ref for u in updates} == {"R1"}


def test_pending_excludes_a_board_field_that_does_not_exist(
        fieldstool_window, tmp_path):
    """End-to-end (handoff pending_exclude_missing_board_fields): a schematic
    Cluster with no corresponding field on the board footprint must not
    appear as a pending edit at all — the whole path from the live snapshot
    to the table produces no such row."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD", cluster="CL_A"))
    fieldstool_window._set_root_sheet(root)
    # Board footprint has the Role but NOT the Cluster field (physically
    # absent) — without the exists-guard this would be a pending Cluster diff
    # ("board: ''") that can never resolve itself.
    fieldstool_window.set_live_snapshot([
        _selected("R1", "OLD", None, cluster_field_exists=False)])

    assert fieldstool_window.pending_dock.table.rowCount() == 0


class _RefreshingConnection:
    """BoardConnection stand-in with a LIVE board: refresh() swaps the frozen
    snapshot for the next one, counting the rebuilds and recording the thread."""

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


def test_rescan_button_rebuilds_the_board_side_before_diffing(
        fieldstool_window, tmp_path, qapp):
    """T.6 #3 (K.2 #7, plan_2026_09_11_stale_snapshot_minor.md) — the pending
    table diffs the schematic against the BOARD, and the board side was only
    ever pushed by the main GUI's poll (a deliberate no-op on its automatic tick
    once connected). The explicit Rescan rebuilds it FIRST, on the worker
    thread, so a component that appeared on the board after connecting shows up
    in "pending" instead of staying invisible until a manual Refresh."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)          # the schematic side
    connection = _RefreshingConnection([[], [_selected("R1", "NEW", None)]])
    fieldstool_window.connection = connection
    assert fieldstool_window.pending_refs == set()   # the board looks empty

    fieldstool_window._on_rescan()

    assert connection.long_op_active                 # the rebuild owns the socket
    _pump(qapp, lambda: not connection.long_op_active)

    assert connection.refresh_calls == 1
    assert connection.refresh_threads[0] != threading.main_thread().name
    assert fieldstool_window.pending_refs == {"R1"}  # the board side is fresh now


def test_rescan_button_without_a_live_board_keeps_the_old_behaviour(
        fieldstool_window, tmp_path):
    """Same gate, offline: no refreshable board -> the cached snapshot is used
    and the schematic re-read still happens (the button never dead-ends)."""
    root = _write_root(tmp_path, symbol_block(["R1"], role="NEW"))
    fieldstool_window._set_root_sheet(root)
    fieldstool_window.set_live_snapshot([_selected("R1", "NEW", None)])

    fieldstool_window._on_rescan()                   # must not raise / hang

    assert fieldstool_window.pending_refs == set()


def test_rescan_falls_back_to_the_cache_when_the_socket_stays_busy(
        fieldstool_window, tmp_path, monkeypatch, caplog):
    """Э2/Э2-M — with the socket busy on BOTH attempts the Rescan still works on
    the CACHED board side, and the fallback is reported through
    show_message(WARN_STYLE) from fieldstool's OWN logger.

    Mutations: dropping the on_cached report -> no WARN -> red; dropping the
    cached continuation -> the board side is never adopted -> red."""
    import gui.worker as worker_mod

    root = _write_root(tmp_path, symbol_block(["R1"], role="NEW"))
    fieldstool_window._set_root_sheet(root)          # the schematic side
    # The cached board side says OLD, so the pending diff is the observable proof
    # that the continuation ran at all.
    connection = _RefreshingConnection([[_selected("R1", "OLD", None)]])
    connection.long_op_active = True                 # another op holds the socket
    fieldstool_window.connection = connection
    scheduled = []
    monkeypatch.setattr(
        worker_mod.QTimer, "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)))

    caplog.clear()
    fieldstool_window._on_rescan()
    assert len(scheduled) == 1                       # the single retry is armed

    scheduled[0][1]()                                # retry: still busy

    assert connection.refresh_calls == 0             # never rebuilt
    assert fieldstool_window.pending_refs == {"R1"}  # ...but the Rescan ran
    warns = [r for r in caplog.records
             if r.name == "gui.fieldstool_window"
             and r.levelno == logging.WARNING]
    assert len(warns) == 1
    assert "The board is busy" in warns[0].message


def test_rescan_passes_the_window_as_the_retry_owner(
        fieldstool_window, tmp_path, monkeypatch):
    """Э2 ловушка 3, wiring half — fieldstool also calls the helper with
    widgets=(), so `owner` is its ONLY liveness guard and must be the window
    itself; otherwise the retry would run the continuation against a window
    closed during the 120 ms delay (the behaviour is pinned on the helper in
    test_snapshot_freshness.py::test_retry_skips_a_window_closed_during_the_delay)."""
    import gui.worker as worker_mod

    root = _write_root(tmp_path, symbol_block(["R1"], role="NEW"))
    fieldstool_window._set_root_sheet(root)
    connection = _RefreshingConnection([[_selected("R1", "OLD", None)]])
    connection.long_op_active = True
    fieldstool_window.connection = connection
    calls: list = []
    # Patched AFTER the setup rescan, so only the _on_rescan below is counted.
    monkeypatch.setattr(
        worker_mod, "refresh_snapshot_then_with_retry",
        lambda *a, **k: calls.append((a, k)))

    fieldstool_window._on_rescan()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[1] == ()                             # no guard widgets here
    assert kwargs["owner"] is fieldstool_window      # the only liveness guard
    assert "on_cached" in kwargs                     # the fallback is reported
