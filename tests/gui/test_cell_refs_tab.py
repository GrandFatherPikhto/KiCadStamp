# tests/gui/test_cell_refs_tab.py
"""Tests for the "Refs" tab of the cell editor — gui/docks/cell_refs_tab.py.

Stage 2 of the spoke work (plan_2026_09_17_spoke_s2_role_table.md, design
design_2026_09_17_spoke_cell_editing.md §3 Р2/Р4/Р5, UI delta
plan/delta_2026_09_17_spoke_s2_refs_tab_ui.md).

What this file pins (2026-09-18: the write half was REWRITTEN for Т5 of
plan_2026_09_18_field_overrides_store — the tab records into the project's
override store and never writes the board):

  * the READ worker (the selection) against a fake adapter: what it reads and
    that an empty selection is refused;
  * RECORDING (С8, С10, С11, С12) against a REAL temporary store file: the
    adapter is never touched, only what the user edited earns a record (an
    untouched table records nothing), the keys are symbol uuids and a row
    without one is refused by name;
  * the tab's OWN wiring: socket_busy refuses before a READ starts, the
    on_overrides_written hook fires after a record and the refs are NOT
    remembered (Р6), a saved table is restored with ZERO adapter calls, "Fill
    from selection" never overwrites a table the user filled in, and the group
    fill reaches every row that can take a cluster;
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
from gui.docks.cell_refs_tab import RefsTabWidget, read_selection_rows_worker
from gui.role_table_model import (
    SKIP_NO_SYMBOL_UUID,
    SKIP_NOT_ON_BOARD,
    BoardRecord,
    RoleRow,
    skip_label,
)
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.field_overrides import FieldOverrides
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
    # The symbol uuid the override store is keyed by (kicadstamp.field_overrides.
    # symbol_uuid_of reads exactly this): a real footprint always carries one, so
    # the fake does too — the read path records it (Т5).
    fp.sheet_path = SimpleNamespace(path=[SimpleNamespace(value=f"uuid-{ref}")])
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
    """(ref, role, cluster) triples -> the snapshot records the tab is fed.

    `symbol_uuid` is filled from the refdes, exactly like a real read does
    (symbol_uuid_of(fp)) — the store is keyed by it (Т5), so a record test needs
    it, and a record it cannot be for one of these."""
    return [BoardRecord(ref=ref, role=role, cluster=cluster,
                        symbol_uuid=f"uuid-{ref}")
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
         cell="cell1", store=None):
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
    # A store is part of the CONTEXT since Т5 and the write button needs one, so
    # the default is an in-memory FieldOverrides (path=None -> save() is a no-op):
    # the read-only tests behave exactly as before, and the record tests hand in a
    # real file-backed store through `store=`.
    tab.set_context(root, cell, roles if roles is not None else CELL_ROLES,
                    records, sheet_names={},
                    overrides=store if store is not None else FieldOverrides())
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


# ── Recording into the override store (Т5: С8, С10, С11, С12) ─────────────
#
# The four board-write worker tests that stood here are REPLACED, not deleted
# silently: with the override store the tab's record path is a FILE write, so
# what used to be proved about one set_field_values_bulk commit is now proved
# about the store — and the strongest of those statements (С8) becomes "the
# adapter is never called at all".

def _records_with_uuids(*tpairs):
    """Snapshot records that carry symbol uuids — what a real read produces
    (symbol_uuid_of(fp)) and what the store is keyed by."""
    return [BoardRecord(ref=ref, role=role, cluster=cluster,
                        symbol_uuid=f"uuid-{ref}")
            for ref, role, cluster in tpairs]


def _store_file(tmp_path):
    """A real FieldOverrides next to a throwaway profile."""
    from kicadstamp.field_overrides import FieldOverrides
    from kicadstamp.utils.paths import overrides_path_for_config
    profile = tmp_path / "prof.sexp"
    profile.write_text(dict_to_sexp({"layer": "B.Cu"}), encoding="utf-8")
    return FieldOverrides(overrides_path_for_config(str(profile)))


def _written(store):
    """The store as it is ON DISK — the truth after a record: the tab re-reads the
    file before writing and keeps ITS OWN object, so the instance a test passed in
    is not necessarily the one holding the records."""
    from kicadstamp.field_overrides import load_field_overrides
    return load_field_overrides(str(store.path))


def _recording_tab(main_window, tmp_path, records, adapter=None, cell="cell1"):
    """A tab whose context is a real store file and whose rows came from
    `records` (uuids included), exactly like a real "Take selection"."""
    store = _store_file(tmp_path)
    tab = _tab(main_window, adapter=adapter, records=records, cell=cell,
               store=store)
    return tab, store


def test_c8_recording_never_touches_the_board(main_window, tmp_path):
    """С8: the record path uses NO adapter call at all — no refresh, no
    has_field, no bulk write. A spy that raises on ANY attribute use is the
    strongest form of that statement."""
    class _Spy(FakeAdapter):
        def __getattr__(self, name):
            raise AssertionError(f"the adapter was used: {name}")

    adapter = _Spy([_fp("C41")])
    tab, store = _recording_tab(main_window, tmp_path,
                                _records_with_uuids(("C41", None, None)), adapter)
    _role_combo(tab, 0).setCurrentText("C_BULK")

    tab.write_to_store()

    assert _written(store).get("uuid-C41", ROLE_FIELD_NAME) == "C_BULK"


def test_c11_only_what_the_user_touched_is_recorded(main_window, tmp_path):
    """С11: two rows shown, one edited — the store gets ONE record. A mirror
    filled from the board wholesale is exactly what the plan forbids (a snapshot
    plus priority would freeze every later board edit)."""
    tab, store = _recording_tab(
        main_window, tmp_path,
        _records_with_uuids(("C41", None, None), ("C42", "C_BYPASS", "bank")))

    _role_combo(tab, 0).setCurrentText("C_BULK")
    tab.write_to_store()

    assert [(r.symbol_uuid, r.field, r.value) for r in _written(store).records()] == [
        ("uuid-C41", ROLE_FIELD_NAME, "C_BULK")]


def test_c11_a_cluster_edit_is_recorded_side_by_side_with_the_role(
        main_window, tmp_path):
    tab, store = _recording_tab(main_window, tmp_path,
                                _records_with_uuids(("C41", None, None)))

    _role_combo(tab, 0).setCurrentText("C_BULK")
    _cluster_edit(tab, 0).setCurrentText("bank")
    tab.write_to_store()

    assert sorted((r.field, r.value) for r in _written(store).records()) == [
        (CLUSTER_FIELD_NAME, "bank"), (ROLE_FIELD_NAME, "C_BULK")]


def test_c10_an_untouched_table_records_nothing(main_window, tmp_path):
    """С10: open the tab, look at the rows, press the button — the store stays
    empty and the button was never enabled for it (nothing differs)."""
    tab, store = _recording_tab(main_window, tmp_path,
                                _records_with_uuids(("C41", "C_BULK", "bank")))

    assert tab.write_button_enabled() is False
    tab.write_to_store()

    assert _written(store).has_any() is False


def test_c12_a_row_without_a_symbol_uuid_is_refused_by_name(main_window, tmp_path):
    """С12: the store is keyed by symbol uuid. A row without one (a ref the last
    read did not carry) cannot be recorded — refused BY NAME, never under an
    invented key that an F8 would later move to another component."""
    tab, store = _recording_tab(main_window, tmp_path,
                                _records_with_uuids(("C41", None, None)))
    # One row the user really edited (it HAS a key)...
    _role_combo(tab, 0).setCurrentText("C_BULK")
    # ... and a second one the snapshot does not carry: no uuid, so no key.
    tab._rows = tab._rows + [RoleRow(ref="C99", role="C_BULK")]
    tab._after_table_change()

    tab.write_to_store()

    assert [r.symbol_uuid for r in _written(store).records()] == ["uuid-C41"]
    assert "C99" in tab.status_text()
    assert skip_label(SKIP_NO_SYMBOL_UUID) in tab.status_text()


def test_recording_keeps_what_another_pane_already_recorded(main_window, tmp_path):
    """The tab re-reads the FILE before it saves (Т5): the other holder of that
    store in this process — fieldstool's Stage — may have recorded since this
    table was handed its copy, and a save built on the stale in-memory picture
    would silently DROP that record. Here the file already holds a value for
    ANOTHER component, and both must survive."""
    from kicadstamp.field_overrides import SOURCE_FIELDSTOOL, load_field_overrides

    tab, store = _recording_tab(main_window, tmp_path,
                                _records_with_uuids(("C41", None, None)))
    other = load_field_overrides(str(store.path))
    other.set("uuid-C99", "C99", ROLE_FIELD_NAME, "FROM_FIELDSTOOL",
              SOURCE_FIELDSTOOL)
    other.save()

    _role_combo(tab, 0).setCurrentText("C_BULK")
    tab.write_to_store()

    kept = {(r.symbol_uuid, r.value) for r in _written(store).records()}
    assert ("uuid-C99", "FROM_FIELDSTOOL") in kept
    assert ("uuid-C41", "C_BULK") in kept


# ── Т5а: the explicit BOARD write ("Write to board") ──────────────────────
#
# Т5 moved the table's own path into the store, but putting the values back ONTO
# the board stays possible — as this named button, because the board is what
# foreign tools read (BOM, net classes, design §4.2). It is a BOARD operation:
# a live KiCad, the shared socket gate and the worker thread.

def test_t5a_the_board_button_writes_the_tables_batch_in_one_commit(
        main_window, tmp_path, monkeypatch):
    """С20, the GUI half: one set_field_values_bulk for the whole table — KiCad's
    own Ctrl+Z then takes the batch back — and the STORE is not touched at all:
    this button writes OUTWARD, recording is the other button's job."""
    _sync_long_op(monkeypatch)
    adapter = _adapter([_fp("C41", role="OLD_ROLE")])
    tab, store = _recording_tab(
        main_window, tmp_path, _records_with_uuids(("C41", "OLD_ROLE", None)),
        adapter)
    _role_combo(tab, 0).setCurrentText("C_BULK")

    tab.write_to_board()

    assert len(adapter.bulk) == 1
    updates, description = adapter.bulk[0]
    assert [(fp.ref, field, value) for fp, field, value in updates] == [
        ("C41", ROLE_FIELD_NAME, "C_BULK")]
    assert description
    assert _written(store).has_any() is False


def test_t5a_the_board_button_needs_a_live_board_and_says_so(
        main_window, tmp_path, monkeypatch, caplog):
    """No KiCad, no board write — refused with a Log line by the ONE gate
    (_can_start), the same way the two reads are refused. The value stays in the
    table (and in the store, if it was recorded): nothing is lost."""
    _sync_long_op(monkeypatch)
    tab, store = _recording_tab(main_window, tmp_path,
                                _records_with_uuids(("C41", "OLD_ROLE", None)))
    _role_combo(tab, 0).setCurrentText("C_BULK")
    caplog.clear()

    tab.write_to_board()

    assert any("No live board" in r.message for r in caplog.records)


def test_t5a_the_two_write_buttons_ask_two_different_questions(
        main_window, tmp_path):
    """The store button asks "does this differ from the value IN FORCE", the
    board button asks "does the BOARD lack this". A row whose cell already holds
    what the board has, while OUR store holds something else, is exactly the case
    where the answers differ: there IS something to put on the board, and nothing
    new to record."""
    store = _store_file(tmp_path)
    store.set("uuid-C41", "C41", ROLE_FIELD_NAME, "STORE_OLD", "cell_table")
    store.save()
    adapter = _adapter([_fp("C41", role="C_BULK")])

    tab = _tab(main_window, adapter=adapter,
               records=_records_with_uuids(("C41", "C_BULK", None)), store=store)

    assert tab.board_write_button_enabled() is False   # the board already has it
    assert tab.write_button_enabled() is True          # ... but the store owes one


def test_t5a_the_board_button_is_off_when_the_table_is_untouched(
        main_window, tmp_path):
    """Open the tab, look at the table: nothing differs from the board, so the
    board button cannot be pressed — the same "nothing to write" discipline the
    store button has (Р4)."""
    adapter = _adapter([_fp("C41", role="C_BULK")])
    tab, _store = _recording_tab(
        main_window, tmp_path, _records_with_uuids(("C41", "C_BULK", None)),
        adapter)

    assert tab.board_write_button_enabled() is False


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


# ── Р3/Р4 of plan 2а: the hints and the status do not ride on the rebuild ──
# Guards С2 and С3. Both are RED on the base: _render exits on an unchanged
# signature BEFORE set_choices and _refresh_status, so a cell-role edit leaves
# stale hints and a late adapter leaves the write button off.

def test_m3_the_role_hints_follow_a_cell_role_added_while_the_table_stands(
        main_window):
    """С2 / mutation М3 (plan 2а §1.2): the ROWS did not move, only the cell's
    own roles did — the Role editor must offer the new role right away, without
    reopening the cell."""
    tab = _tab(main_window, roles=["R_A", "R_B"],
               records=_records(("C41", None, None)))
    assert tab._role_combo_items() == ["R_A", "R_B"]

    tab.set_cell_roles(["R_A", "R_B", "R_NEW"])

    assert tab._role_combo_items() == ["R_A", "R_B", "R_NEW"]


def test_m3_the_role_hints_follow_the_roles_of_a_form_reload(main_window):
    """С2 / mutation М3, the PRODUCTION path: the cell entry was edited, the
    editor reloaded the form and fed the tab the new roles through set_context
    (cell_anchor_view._reload_form -> _sync_refs_tab) with the SAME board read —
    only the hints change, the table itself stays exactly as the user left it."""
    tab = _tab(main_window, roles=["R_A", "R_B"],
               records=_records(("C41", None, None)))

    tab.set_context("test-root", "cell1", ["R_A", "R_B", "R_NEW"],
                    tab._records, sheet_names={})

    assert tab._role_combo_items() == ["R_A", "R_B", "R_NEW"]


def test_m4_the_write_button_follows_an_adapter_that_arrives_later(main_window,
                                                                  tmp_path):
    """С3 / mutation М4 (plan 2а Р4), REWRITTEN by Т5: the button used to need a
    live BOARD and to wake up when KiCad connected late. It now needs the PROJECT
    (the override store) and must ignore the board completely — recording is a
    file write, so a board-driven gate would be exactly the regression this guard
    exists for (guard С8).

    The role comes from the SAVED table, not from a mid-test keystroke: a cell
    edit does not re-render (see _on_item_changed), so typing here would leave a
    stale signature cached and the rebuild would happen at the wrong moment. The
    re-render below is forced through the cell's ROLE LIST instead — a context
    change that genuinely re-renders (it does not take the unchanged-context early
    exit), which is what makes the board assertion bite."""
    root = tmp_path / "root.sexp"
    remember_role_table(root, "cell1", {
        "cluster": "",
        "rows": [{"ref": "C41", "role": "C_BYPASS", "cluster": ""}],
    })
    # The board still carries the OLD role, so the saved table has something to
    # record — and the table itself is already rendered and cached.
    records = _records(("C41", "C_BULK", None))
    tab = RefsTabWidget(main_window, connection=main_window.connection,
                        parent=main_window)
    tab.set_context(root, "cell1", ["C_BULK"], records, sheet_names={})
    assert tab.row_refs() == ["C41"]
    assert tab.write_button_enabled() is False       # no project/store yet

    main_window.connection.board = SimpleNamespace(adapter=FakeAdapter())
    tab.set_context(root, "cell1", ["C_BULK", "C_BYPASS"], records, sheet_names={})

    assert tab.write_button_enabled() is False      # a board changes NOTHING

    tab.set_overrides(FieldOverrides())

    assert tab.write_button_enabled() is True       # the project is what it needs


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

    tab.write_to_store()

    assert tab._overrides.has_any() is True     # the warning never blocks
    assert adapter.bulk == []                   # and the board stayed untouched


def test_c7_a_role_that_is_not_the_cells_is_warned_about(main_window):
    tab = _tab(main_window, records=_records(("C41", None, None)))
    _role_combo(tab, 0).setCurrentText("R_NOT_IN_CELL")

    assert "R_NOT_IN_CELL" in tab.status_text()


# ── The tab: writing, and what happens after (С5, С8, С9) ──────────────────

def test_c5_the_record_batch_carries_each_rows_own_values(
        main_window, monkeypatch):
    """Two rows, two DIFFERENT roles: each lands under ITS OWN symbol uuid. The
    old version of this guard pinned one set_field_values_bulk commit keyed by
    refdes — with the store the key is the SYMBOL, which is what survives an F8."""
    c74 = _fp("C74", cluster="FPGA_PWR_BANK")
    c58 = _fp("C58", cluster="FPGA_PWR_BANK")
    adapter = _adapter([c74, c58], selected=[c74, c58])
    tab = _tab(main_window, adapter=adapter)
    _sync_long_op(monkeypatch)
    tab.take_selection()
    _role_combo(tab, 0).setCurrentText("C_BULK")
    _role_combo(tab, 1).setCurrentText("C_BYPASS")

    tab.write_to_store()

    recorded = {(r.symbol_uuid, r.field, r.value) for r in tab._overrides.records()}
    assert ("uuid-C74", ROLE_FIELD_NAME, "C_BULK") in recorded
    assert ("uuid-C58", ROLE_FIELD_NAME, "C_BYPASS") in recorded
    assert adapter.bulk == []


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


def test_c9_after_the_record_the_hook_fires_and_the_refs_are_not_remembered(
        main_window, monkeypatch, tmp_path):
    """Р6, kept by Т5: the instance is NOT identified automatically — nothing is
    remembered, and the user is told to press "Fill from selection" on the Source
    tab. The hook that fires is the STORE one (on_overrides_written); the board
    hook is a separate concern, because nothing on the board changed."""
    c74 = _fp("C74")
    adapter = _adapter([c74], selected=[c74])
    root = tmp_path / "root.sexp"
    tab = _tab(main_window, adapter=adapter, root=root)
    _sync_long_op(monkeypatch)
    tab.take_selection()
    _role_combo(tab, 0).setCurrentText("C_BULK")
    fired = []
    tab.on_overrides_written = lambda: fired.append(True)

    tab.write_to_store()

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

    # parent=main_window ON PURPOSE — see the note in the _tab helper above.
    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
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
    # parent=main_window ON PURPOSE — see the note in the _tab helper above.
    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
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
    # parent=main_window ON PURPOSE — see the note in the _tab helper above.
    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
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
    tab.write_to_store()
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
