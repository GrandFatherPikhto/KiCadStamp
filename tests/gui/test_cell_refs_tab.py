# tests/gui/test_cell_refs_tab.py
"""Tests for the "Refs" tab of the cell editor — gui/docks/cell_refs_tab.py.

Stage 2 of the spoke work (plan_2026_09_17_spoke_s2_role_table.md, design
design_2026_09_17_spoke_cell_editing.md §3 Р2/Р4/Р5, UI delta
plan/delta_2026_09_17_spoke_s2_refs_tab_ui.md).

What this file pins:

  * the two WORKER functions (read the selection / write the batch) against a
    fake adapter: what they read, that the write is ONE set_field_values_bulk
    call with the rows' own values, and that a per-field skip is reported
    instead of rolling the whole batch back;
  * the tab's OWN wiring: socket_busy refuses before anything starts, the
    on_board_written hook fires after a write and the refs are NOT remembered
    (Р6), a saved table is restored with ZERO adapter calls, "Fill from
    selection" never overwrites a table the user filled in, and the group fill
    reaches every row that can take a cluster;
  * the door: the UI thread never touches the adapter — every board read in this
    file goes through start_long_op, which the tests replace with a synchronous
    call so no QThread is involved.

Headless: the tab is a plain QWidget on the conftest `main_window` stub, and the
tables are built from records, never from KiCad.
"""
from types import SimpleNamespace

import pytest

import gui.docks.cell_refs_tab as tab_mod
from gui import settings
from gui.cell_identification import SelectionRecord, identify_cell_instance
from gui.cell_edit_context import (
    remember_role_table,
    remembered_cell_refs,
    remembered_role_table,
)
from gui.docks.cell_anchor_view import CellAnchorView
from gui.docks.cell_refs_tab import (
    RefsTabWidget,
    read_selection_rows_worker,
    write_role_table_worker,
)
from gui.role_table_model import (
    SKIP_NO_ROLE_FIELD,
    SKIP_NOT_ON_BOARD,
    BoardRecord,
    RoleRow,
)
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import Vector2

CELL_ROLES = ["C_BULK", "C_BYPASS"]


# ── Fixtures and helpers ───────────────────────────────────────────────────

def _fp(ref, role=None, cluster=None, role_field=True, cluster_field=True):
    """A domain Footprint DTO with per-footprint field values and the two
    "does this footprint have the field at all" facts."""
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer="F.Cu")
    fp.role = role
    fp.cluster = cluster
    fp.role_field = role_field
    fp.cluster_field = cluster_field
    return fp


class FakeAdapter:
    """Duck-typed adapter: in-memory footprints, a settable selection and a
    RECORDED list of every set_field_values_bulk call."""

    def __init__(self, footprints=(), selected=()):
        self.footprints = list(footprints)
        self.selected = list(selected)
        self.bulk = []
        self.refreshed = 0
        self.calls = []

    def refresh_board(self):
        self.refreshed += 1
        self.calls.append("refresh_board")

    def get_selected_items(self):
        self.calls.append("get_selected_items")
        return list(self.selected)

    def get_footprints(self):
        self.calls.append("get_footprints")
        return list(self.footprints)

    def get_field_value(self, fp, field_name):
        return getattr(fp, field_name.lower(), None)

    def has_field(self, fp, field_name):
        if field_name == ROLE_FIELD_NAME:
            return getattr(fp, "role_field", True)
        if field_name == CLUSTER_FIELD_NAME:
            return getattr(fp, "cluster_field", True)
        return False

    def set_field_values_bulk(self, updates, description):
        self.bulk.append((list(updates), description))
        return True


def _adapter(footprints, selected=()):
    return FakeAdapter(footprints, selected)


def _records(*tpairs):
    """(ref, role, cluster) triples -> the snapshot records the tab is fed."""
    return [BoardRecord(ref=ref, role=role, cluster=cluster)
            for ref, role, cluster in tpairs]


def _sync_long_op(monkeypatch, module=tab_mod):
    """Replace start_long_op with a SYNCHRONOUS call — no QThread, the same
    "run + finish" shape the real controller produces (role_cluster_tree's own
    _do_/_run_ pair does this for its tests)."""
    def _start(connection, widgets, fn, on_success, on_error, *args):
        try:
            result = fn(*args)
        except Exception as e:                      # noqa: BLE001 — mirrors the worker
            on_error(str(e))
            return SimpleNamespace(widgets=widgets)
        on_success(result)
        return SimpleNamespace(widgets=widgets)

    monkeypatch.setattr(module, "start_long_op", _start)


def _tab(main_window, adapter=None, roles=None, records=None, root=None,
         cell="cell1"):
    """A Refs tab wired to the fake board and to a cell context.

    `records` is the BOARD SNAPSHOT (what the page has already read) and it is
    deliberately NOT the row source: the real snapshot is the WHOLE board (334
    footprints on Denis's board), so rows come from the state — which this
    helper seeds from the records, exactly what "the table was filled in" means
    in a real session. The board columns of those rows then come from the
    snapshot, the only difference being that the test does not have to press
    "Take selection" first.
    """
    connection = main_window.connection
    if adapter is not None:
        connection.board = SimpleNamespace(adapter=adapter)
    root = root if root is not None else "test-root"
    records = list(records or ())
    if records and remembered_role_table(root, cell) is None:
        remember_role_table(root, cell, {
            "cluster": "",
            "rows": [{"ref": r.ref,
                      "role": r.role or "",
                      "cluster": r.cluster or ""} for r in records],
        })
    # parent=main_window ON PURPOSE: a QWidget with no Qt parent is owned by
    # Python alone, so it is destroyed from inside the garbage collector — and
    # enough of those (39 tabs in this file, each with its cell editors) aborted
    # the interpreter under the full GUI run ("Fatal Python error: Aborted",
    # while Garbage-collecting). Handing the widget to the window makes Qt the
    # owner, exactly as adding it to a tab widget does in the real page.
    tab = RefsTabWidget(main_window, connection=connection, parent=main_window)
    tab.set_context(root, cell, roles if roles is not None else CELL_ROLES,
                    records, sheet_names={})
    return tab


class _ItemEditor:
    """Stands in for the cell editor the tab installs: the cell is edited
    through the ITEM, which is exactly what the delegate's editor writes on
    commit (setModelData -> model.setData). No Qt widget is involved, so a test
    edits the same model the user's editor writes to — the tab keeps its
    editors as DELEGATES, see gui/docks/cell_refs_tab._ComboDelegate."""

    def __init__(self, table, row: int, column: int):
        self._table = table
        self._row = row
        self._column = column

    def setCurrentText(self, text: str) -> None:
        self._table.item(self._row, self._column).setText(text)

    def text(self) -> str:
        return self._table.item(self._row, self._column).text()


def _role_combo(tab, row_index):
    return _ItemEditor(tab._table, row_index, 1)


def _cluster_edit(tab, row_index):
    return _ItemEditor(tab._table, row_index, 2)


# ── The read worker (С2б + the field facts of Ф5) ──────────────────────────

def test_c2b_the_read_worker_returns_the_selection_with_its_field_facts():
    """The selection is read in the WORKER (refresh + selected items + fields +
    has_field), and a pair that has no roles yet still comes back as records —
    that is exactly the state the tab exists for."""
    h1 = _fp("H1", role_field=False, cluster_field=False)
    c41 = _fp("C41")
    adapter = _adapter([h1, c41], selected=[h1, c41])

    result = read_selection_rows_worker({"adapter": adapter, "sheet_names": {},
                                         "cell_name": "cell1"})

    assert adapter.refreshed == 1
    by_ref = {r.ref: r for r in result["records"]}
    assert set(by_ref) == {"H1", "C41"}
    assert by_ref["C41"].role is None
    assert by_ref["C41"].role_field_exists is True
    assert by_ref["H1"].role_field_exists is False
    assert by_ref["H1"].cluster_field_exists is False


def test_the_read_worker_refuses_an_empty_selection():
    adapter = _adapter([], selected=[])
    with pytest.raises(Exception) as ei:
        read_selection_rows_worker({"adapter": adapter, "sheet_names": {},
                                    "cell_name": "cell1"})
    assert "nothing is selected" in str(ei.value)


# ── The write worker (С1, С3, С4, С5) ──────────────────────────────────────

def test_c5_the_write_worker_issues_exactly_one_bulk_call():
    c41, c42 = _fp("C41"), _fp("C42")
    adapter = _adapter([c41, c42])
    payload = {"adapter": adapter, "cell_name": "cell1",
               "updates": [("C41", ROLE_FIELD_NAME, "C_BULK"),
                           ("C42", ROLE_FIELD_NAME, "C_BYPASS")],
               "skipped": []}

    result = write_role_table_worker(payload)

    assert len(adapter.bulk) == 1
    updates, description = adapter.bulk[0]
    assert [(fp.ref, field, value) for fp, field, value in updates] == [
        ("C41", ROLE_FIELD_NAME, "C_BULK"), ("C42", ROLE_FIELD_NAME, "C_BYPASS")]
    assert "2" in description
    assert result["count"] == 2
    assert result["skipped"] == []


def test_c3_the_write_worker_skips_a_footprint_without_the_field():
    """The field fact is re-checked against the LIVE footprint (the snapshot
    could be stale): a component KiCad would refuse the field on is skipped
    BEFORE the batch, never left to roll the whole commit back."""
    h1 = _fp("H1", role_field=False)
    c41 = _fp("C41")
    adapter = _adapter([h1, c41])
    payload = {"adapter": adapter, "cell_name": "cell1",
               "updates": [("H1", ROLE_FIELD_NAME, "C_BULK"),
                           ("C41", ROLE_FIELD_NAME, "C_BULK")],
               "skipped": []}

    result = write_role_table_worker(payload)

    assert [(fp.ref, field) for fp, field, _v in adapter.bulk[0][0]] == [
        ("C41", ROLE_FIELD_NAME)]
    assert ("H1", SKIP_NO_ROLE_FIELD) in result["skipped"]


def test_the_write_worker_reports_a_footprint_that_left_the_board():
    c41 = _fp("C41")
    adapter = _adapter([c41])
    payload = {"adapter": adapter, "cell_name": "cell1",
               "updates": [("C41", ROLE_FIELD_NAME, "C_BULK"),
                           ("C99", ROLE_FIELD_NAME, "C_BULK")],
               "skipped": []}

    result = write_role_table_worker(payload)

    assert ("C99", SKIP_NOT_ON_BOARD) in result["skipped"]
    assert result["count"] == 1


def test_the_write_worker_returns_an_error_instead_of_raising():
    class _Failing(FakeAdapter):
        def set_field_values_bulk(self, updates, description):
            from kicadstamp.exceptions import ValidationError
            raise ValidationError("KiCad said no")

    adapter = _Failing([_fp("C41")])
    payload = {"adapter": adapter, "cell_name": "cell1",
               "updates": [("C41", ROLE_FIELD_NAME, "C_BULK")], "skipped": []}

    result = write_role_table_worker(payload)

    assert "KiCad said no" in result["error"]


# ── The tab: taking the selection (С2б, С2в, С6, С7, С11) ──────────────────

def test_c2b_take_selection_replaces_the_rows_and_keeps_the_field_facts(
        main_window, monkeypatch):
    h1 = _fp("H1", role_field=False)
    c41 = _fp("C41", role="C_BULK", cluster="FPGA_PWR_BANK")
    adapter = _adapter([h1, c41], selected=[c41, h1])
    tab = _tab(main_window, adapter=adapter)
    _sync_long_op(monkeypatch)

    tab.take_selection()

    assert tab.row_refs() == ["C41", "H1"]
    assert tab.rows[0].role == "C_BULK"          # pre-filled from the board
    assert tab.rows[0].cluster == "FPGA_PWR_BANK"
    assert tab.rows[1].role_field_exists is False


def test_c2v_add_selection_appends_and_keeps_the_typed_role(
        main_window, monkeypatch):
    c41, c42 = _fp("C41"), _fp("C42")
    adapter = _adapter([c41, c42], selected=[c41])
    tab = _tab(main_window, adapter=adapter)
    _sync_long_op(monkeypatch)
    tab.take_selection()
    _role_combo(tab, 0).setCurrentText("C_BULK")

    adapter.selected = [c41, c42]
    tab.add_selection()

    assert tab.row_refs() == ["C41", "C42"]
    assert tab.rows[0].role == "C_BULK"          # the edit survived
    assert tab.rows[1].role == ""


def test_c11_the_role_cells_offer_the_cells_roles_in_the_cells_order(
        main_window):
    tab = _tab(main_window, roles=["C_BYPASS", "C_BULK"], records=_records(
        ("C41", None, None)))

    assert [tab._role_combo_items()[i] for i in range(2)] == ["C_BYPASS",
                                                              "C_BULK"]


def test_c6_a_duplicate_role_warns_in_the_status_but_the_write_still_runs(
        main_window, monkeypatch):
    c41, c43 = _fp("C41"), _fp("C43")
    adapter = _adapter([c41, c43], selected=[c41, c43])
    tab = _tab(main_window, adapter=adapter)
    _sync_long_op(monkeypatch)
    tab.take_selection()
    _role_combo(tab, 0).setCurrentText("C_BULK")
    _role_combo(tab, 1).setCurrentText("C_BULK")

    assert "C_BULK" in tab.status_text()

    tab.write_to_board()

    assert len(adapter.bulk) == 1               # not blocked


def test_c7_a_role_that_is_not_the_cells_is_warned_about(main_window):
    tab = _tab(main_window, records=_records(("C41", None, None)))
    _role_combo(tab, 0).setCurrentText("R_NOT_IN_CELL")

    assert "R_NOT_IN_CELL" in tab.status_text()


# ── The tab: writing, and what happens after (С5, С8, С9) ──────────────────

def test_c5_write_builds_one_batch_from_the_rows_own_values(
        main_window, monkeypatch):
    c74 = _fp("C74", cluster="FPGA_PWR_BANK")
    c58 = _fp("C58", cluster="FPGA_PWR_BANK")
    adapter = _adapter([c74, c58], selected=[c74, c58])
    tab = _tab(main_window, adapter=adapter)
    _sync_long_op(monkeypatch)
    tab.take_selection()
    _role_combo(tab, 0).setCurrentText("C_BULK")
    _role_combo(tab, 1).setCurrentText("C_BYPASS")

    tab.write_to_board()

    assert len(adapter.bulk) == 1
    updates = [(fp.ref, field, value) for fp, field, value in adapter.bulk[0][0]]
    assert ("C74", ROLE_FIELD_NAME, "C_BULK") in updates
    assert ("C58", ROLE_FIELD_NAME, "C_BYPASS") in updates


def test_c8_a_busy_socket_refuses_before_anything_starts(main_window,
                                                         monkeypatch, caplog):
    c41 = _fp("C41")
    adapter = _adapter([c41], selected=[c41])
    tab = _tab(main_window, adapter=adapter)
    _sync_long_op(monkeypatch)
    main_window.connection.long_op_active = True
    caplog.clear()

    tab.take_selection()

    assert adapter.calls == []
    assert tab.row_refs() == []
    assert "busy" in caplog.text


def test_c9_after_the_write_the_hook_fires_and_the_refs_are_not_remembered(
        main_window, monkeypatch, tmp_path):
    """Р6: right after a write KiCad may hand back the OLD field value over IPC
    (seen live 2026-08-14), so the instance is NOT identified automatically —
    nothing is remembered, and the user is told to press "Fill from selection"
    on the Source tab."""
    c74 = _fp("C74")
    adapter = _adapter([c74], selected=[c74])
    root = tmp_path / "root.sexp"
    tab = _tab(main_window, adapter=adapter, root=root)
    _sync_long_op(monkeypatch)
    tab.take_selection()
    _role_combo(tab, 0).setCurrentText("C_BULK")
    fired = []
    tab.on_board_written = lambda: fired.append(True)

    tab.write_to_board()

    assert fired == [True]
    assert remembered_cell_refs(root, "cell1") is None
    assert "Fill from selection" in tab.status_text()


# ── The tab: rows, group fill, removal (С2г, С2д, С2е, С13, С14) ───────────

def test_c2g_a_hand_typed_ref_is_added_and_a_typo_is_refused(
        main_window, caplog):
    tab = _tab(main_window, records=_records(("C41", None, None)))
    tab.clear_rows()
    caplog.clear()

    tab.add_ref_by_hand("C99")                  # not in the snapshot
    assert tab.row_refs() == []
    assert "C99" in caplog.text

    tab.add_ref_by_hand("C41")
    assert tab.row_refs() == ["C41"]


def test_c2d_remove_and_clear_only_touch_the_table(main_window, monkeypatch,
                                                   tmp_path):
    c41, c42 = _fp("C41"), _fp("C42")
    adapter = _adapter([c41, c42], selected=[c41, c42])
    root = tmp_path / "root.sexp"
    tab = _tab(main_window, adapter=adapter, root=root)
    _sync_long_op(monkeypatch)
    tab.take_selection()
    tab.select_rows([0])

    tab.remove_selected_rows()
    assert tab.row_refs() == ["C42"]

    tab.clear_rows()
    assert tab.row_refs() == []
    assert remembered_role_table(root, "cell1") is None


def test_c2e_the_cluster_field_defaults_to_the_common_value(main_window):
    tab = _tab(main_window, records=_records(("C41", None, "FPGA_PWR_BANK"),
                                             ("C58", None, "FPGA_PWR_BANK")))
    assert tab.cluster_text() == "FPGA_PWR_BANK"

    # Another CELL, deliberately: the state is per (root, cell), and a second
    # tab of the SAME cell would legitimately open on the saved table above.
    mixed = _tab(main_window, cell="cell2",
                 records=_records(("C41", None, "FPGA_PWR_BANK"),
                                  ("C74", None, "MCU_PWR_BANK")))
    assert mixed.cluster_text() == ""


def test_c13_apply_to_all_rows_fills_every_row_that_can_take_a_cluster(
        main_window, caplog):
    h1 = _fp("H1", role_field=False, cluster_field=False)
    tab = _tab(main_window, records=[
        BoardRecord(ref="C41"), BoardRecord(ref="C74"),
        BoardRecord(ref="H1", role_field_exists=False,
                    cluster_field_exists=False)])
    caplog.clear()

    tab.apply_cluster_to_all_rows("FPGA_PWR_BANK")

    assert tab.rows[0].cluster == "FPGA_PWR_BANK"
    assert tab.rows[1].cluster == "FPGA_PWR_BANK"
    assert tab.rows[2].cluster == ""
    assert "H1" in caplog.text


def test_c14_apply_to_all_rows_with_an_empty_field_changes_nothing(
        main_window):
    """An EMPTY cluster field is "leave the cluster alone", so the group fill
    must not touch a row that already carries one."""
    tab = _tab(main_window, records=_records(("C41", None, "FPGA_PWR_BANK")))

    tab.apply_cluster_to_all_rows("")

    assert tab.rows[0].cluster == "FPGA_PWR_BANK"
    assert tab.cluster_text() == ""


def test_the_table_persists_what_the_user_typed(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    tab = _tab(main_window, root=root, records=_records(("C41", None, None)))
    _role_combo(tab, 0).setCurrentText("C_BULK")
    tab.apply_cluster_to_all_rows("FPGA_PWR_BANK")

    saved = remembered_role_table(root, "cell1")

    assert saved["rows"] == [{"ref": "C41", "role": "C_BULK",
                             "cluster": "FPGA_PWR_BANK"}]
    assert saved["cluster"] == "FPGA_PWR_BANK"


# ── The tab inside the editor: restore without a board read (С2л, С10) ─────

def _write(path, data):
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _cell_data():
    return {"cells": {"cell1": {
        "layer": "F.Cu",
        "components": [
            {"role": "C_BULK", "offset_along_mm": 1.0, "offset_across_mm": -2.0},
            {"role": "C_BYPASS", "offset_along_mm": 3.0, "offset_across_mm": -4.0},
        ],
        "vias": [], "tracks": [], "clone_placements": []}}}


class _RecordingAdapter(FakeAdapter):
    """A spy that RECORDS calls instead of raising: a raising spy would make a
    guard pass for the wrong reason (a swallowed exception looks like "no call"
    to anyone downstream)."""

    def __init__(self, footprints=(), selected=()):
        super().__init__(footprints, selected)


def test_c10_opening_the_editor_restores_the_table_without_reading_the_board(
        main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    remember_role_table(root, "cell1", {
        "cluster": "FPGA_PWR_BANK",
        "rows": [{"ref": "C74", "role": "C_BULK", "cluster": "FPGA_PWR_BANK"}],
    })
    adapter = _RecordingAdapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)

    view = CellAnchorView(main_window, connection=main_window.connection)
    view.set_root_path(root)
    view.load_entry("cell1", root)

    assert adapter.calls == [], f"the UI thread read the board: {adapter.calls}"
    assert view._refs_tab.row_refs() == ["C74"]
    assert view._refs_tab.rows[0].role == "C_BULK"
    assert view._refs_tab.rows[0].cluster == "FPGA_PWR_BANK"


def test_c2k_fill_from_selection_fills_an_empty_table_and_never_a_filled_one(
        main_window, tmp_path):
    """Р2: "Fill from selection" on the Source tab fills the Refs table from the
    identified pair ONLY when the table is empty — a table the user has already
    typed into is theirs."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    view = CellAnchorView(main_window, connection=main_window.connection)
    view.set_root_path(root)
    view.load_entry("cell1", root)
    view.refresh_known_roles([
        SimpleNamespace(ref="C74", role="C_BULK", cluster="FPGA_PWR_BANK",
                        sheet=["FPGA"]),
        SimpleNamespace(ref="C58", role="C_BYPASS", cluster="FPGA_PWR_BANK",
                        sheet=["FPGA"]),
    ])
    ident = SimpleNamespace(cluster="FPGA_PWR_BANK", sheet="FPGA",
                            role_to_ref={"C_BULK": "C74", "C_BYPASS": "C58"})

    view._refs_tab.fill_from_refs(ident.role_to_ref)
    assert view._refs_tab.row_refs() == ["C74", "C58"]

    _role_combo(view._refs_tab, 0).setCurrentText("C_BYPASS")
    view._refs_tab.fill_from_refs({"C_BULK": "C99"})

    assert view._refs_tab.row_refs() == ["C74", "C58"]      # not overwritten
    assert view._refs_tab.rows[0].role == "C_BYPASS"


def test_the_tab_is_the_second_tab_of_the_editor(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    view = CellAnchorView(main_window, connection=main_window.connection)
    view.set_root_path(root)
    view.load_entry("cell1", root)

    titles = [view._tabs.tabText(i) for i in range(view._tabs.count())]
    assert titles[:2] == ["Source", "Refs"]


class _FailingSelectionAdapter(FakeAdapter):
    def get_selected_items(self):
        from kicadstamp.exceptions import ValidationError
        raise ValidationError("KiCad is busy")


def test_a_failing_read_is_reported_and_leaves_the_table_alone(
        main_window, monkeypatch, caplog):
    adapter = _FailingSelectionAdapter([_fp("C41")])
    tab = _tab(main_window, adapter=adapter, records=_records(("C41", None, None)))
    _sync_long_op(monkeypatch)
    caplog.clear()

    tab.take_selection()

    assert tab.row_refs() == ["C41"]            # the table survived
    assert "KiCad is busy" in caplog.text


def test_the_write_button_is_inactive_when_there_is_nothing_to_write(
        main_window):
    tab = _tab(main_window, adapter=FakeAdapter(),
               records=_records(("C41", "C_BULK", "FPGA_PWR_BANK")))
    assert tab.write_button_enabled() is False

    _role_combo(tab, 0).setCurrentText("C_BYPASS")
    assert tab.write_button_enabled() is True


def test_clearing_the_role_of_a_tagged_row_means_do_not_touch(
        main_window, monkeypatch):
    """The board already carries a Role; emptying the cell must NOT queue an
    erasure (that is "Clear all" in the Role/Cluster panel)."""
    c41 = _fp("C41", role="C_BULK")
    adapter = _adapter([c41], selected=[c41])
    tab = _tab(main_window, adapter=adapter)
    _sync_long_op(monkeypatch)
    tab.take_selection()
    _role_combo(tab, 0).setCurrentText("")

    assert tab.write_button_enabled() is False
    tab.write_to_board()
    assert adapter.bulk == []


def test_an_empty_record_table_keeps_the_rows_unwritable(main_window):
    """A cell opened with no board read yet (or on another board): rows are
    shown, the write cannot reach them."""
    tab = _tab(main_window, records=[])
    tab._rows = [RoleRow(ref="C90", role="R", cluster="")]
    tab._render()

    assert tab.row_refs() == ["C90"]
    assert tab.write_button_enabled() is False


# ── Р8: the tagging refusals point at the Refs tab (С12) ───────────────────

def _cell(name="cell1", roles=("C_BULK", "C_BYPASS")):
    return SimpleNamespace(name=name,
                           components=[SimpleNamespace(role=r) for r in roles])


def test_c12_the_tagging_refusals_name_the_refs_tab():
    """Р8: the three refusals that are about TAGGING tell the user WHERE the
    tagging happens — the "Refs" tab of this cell. The old hint pointed at
    Tools → Role/Cluster, whose "Tag selected" writes ONE role into EVERY
    selected component: the exact limitation this tab exists to remove."""
    cell = _cell()

    with pytest.raises(Exception) as no_role:
        identify_cell_instance(cell, [SelectionRecord(ref="C41")], [])
    assert "Refs" in str(no_role.value)

    with pytest.raises(Exception) as no_cluster:
        identify_cell_instance(
            cell, [SelectionRecord(ref="C41", role="C_BULK")], [])
    assert "Refs" in str(no_cluster.value)

    with pytest.raises(Exception) as foreign:
        identify_cell_instance(
            cell, [SelectionRecord(ref="C41", role="NOPE", cluster="BANK")], [])
    assert "Refs" in str(foreign.value)
