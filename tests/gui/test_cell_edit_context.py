# tests/gui/test_cell_edit_context.py
"""Tests for the remembered (Cluster, Sheet) cell-edit context — Phase E of
plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md ("Идея D" of
note_2026_09_08_cell_anchor_selection_and_coordinate_converter.md).

Covers:
  * gui/cell_edit_context.py — the gui_state.json round-trip (per-root scoping,
    "last used" overwrite, silent degradation on missing/malformed state) and
    the live-board resolution helper (resolve_context_footprints);
  * the IDENTIFIED refs of that instance (2026-09-17, stage 1 of the spoke work):
    remember_cell_instance / remembered_cell_refs, and the rule that ANY context
    write without refs erases them (plan_2026_09_17_spoke_s1_identify_by_selection
    Р5, guard С8);
  * the OPENING of the page (2026-09-17, stage 1а of the spoke work,
    plan_2026_09_17_spoke_s1a_fixes): the prefill never reads the board — the judge
    is the snapshot the page already holds, and with nothing to judge the
    remembered pair is a HINT — so a busy socket can no longer empty the
    Sheet/Cluster fields while the refs survive. Guards С1а–С5а at the end of
    this file;
  * the cell-anchor page (gui/docks/cell_anchor_view.py) — opening a cell
    prefills the working Sheet/Cluster combos from the remembered context, and
    "Read from selection" overwrites it with the cluster it just read;
  * the Cell editor (gui/docks/cell_editor.py) — the "Select cluster of this
    cell on the board" worker selects exactly the remembered (Cluster, Sheet)
    footprints, and a stale context selects nothing (never a fatal). Since
    2026-09-17 IDENTIFIED refs win over the cluster (Р7 of
    plan_2026_09_17_spoke_s1_identify_by_selection): the pair is selected
    instead of the whole spoke cluster, a stale map selects nothing and is
    reported, and the failure path is a Log line, never a modal.

Headless and board-mutation-free, same reasoning as test_cell_anchor_view.py /
test_cell_editor.py — selection/resolution run against fake adapters.
"""
from types import SimpleNamespace

import gui.cell_edit_context as ctx_mod
import gui.docks.cell_anchor_view as view_mod
import gui.docks.cell_editor as cell_editor_mod
from gui import settings
from gui.cell_edit_context import (
    CELL_EDIT_CONTEXT_KEY,
    CELL_ROLE_TABLE_KEY,
    remember_cell_edit_context,
    remember_cell_instance,
    remember_role_table,
    remembered_cell_edit_context,
    remembered_cell_refs,
    remembered_role_table,
    resolve_context_footprints,
)
from gui.cell_identification import Identification
from gui.docks.cell_anchor_view import CellAnchorView
from gui.docks.cell_editor import CellDock
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import Vector2


# ── Shared fixtures/helpers ───────────────────────────────────────────────

def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _cell_data():
    """A tiny cells:-only config whose cell has two components (C1, C2)."""
    return {"cells": {
        "cell1": {
            "layer": "F.Cu",
            "components": [
                {"role": "C1", "offset_along_mm": 1.0, "offset_across_mm": -2.0},
                {"role": "C2", "offset_along_mm": 3.0, "offset_across_mm": -4.0},
            ],
            "vias": [],
            "tracks": [],
            "clone_placements": [],
        }
    }}


def _fp(uuid, ref):
    return Footprint(ref=ref, uuid=uuid, position=Vector2.from_xy(0, 0),
                     angle_deg=0.0, layer="F.Cu")


class FakeAdapter:
    """Duck-typed adapter: in-memory footprints with Cluster/Role field values,
    and a recorded list of whatever select_items() was given."""

    def __init__(self, footprints=None):
        self.footprints = footprints or []
        self.field_values = {}
        self.selected = []

    def set_field(self, fp, name, value):
        self.field_values[(getattr(fp, "uuid", None), name)] = value

    def get_footprints(self):
        return list(self.footprints)

    def get_field_value(self, fp, field_name):
        return self.field_values.get((getattr(fp, "uuid", None), field_name))

    def get_footprint_pads(self, fp):
        return []

    def get_footprint(self, ref):
        """The by-refdes read the identified-refs selection uses (Р7)."""
        return next((fp for fp in self.footprints
                     if getattr(fp, "ref", None) == ref), None)

    def get_selected_items(self):
        return []

    def select_items(self, items):
        self.selected.append(list(items))


def _cluster_adapter(cluster):
    """An adapter whose footprints all belong to `cluster` (with distinct
    refs), plus one foreign footprint on another cluster."""
    a = _fp("fp1", "R1")
    b = _fp("fp2", "R2")
    foreign = _fp("fp3", "U1")
    adapter = FakeAdapter([a, b, foreign])
    for fp in (a, b):
        adapter.set_field(fp, CLUSTER_FIELD_NAME, cluster)
    adapter.set_field(foreign, CLUSTER_FIELD_NAME, "AD_DAC/IC2")
    return adapter


# ── gui_state.json round-trip ─────────────────────────────────────────────

def test_roundtrip_is_scoped_by_root_config(tmp_path):
    """remember/read round-trip; the SAME cell name under two DIFFERENT roots
    stays separate (the cell name is a Cluster-tag slug, so pif_3v3_vdd in two
    profiles means two different boards)."""
    root_a = tmp_path / "a.sexp"
    root_b = tmp_path / "b.sexp"
    remember_cell_edit_context(root_a, "pif_3v3_vdd", "PIF_3V3_VDD", "FPGA")
    remember_cell_edit_context(root_b, "pif_3v3_vdd", "PIF_3V3_VDD", "Channel_1")

    assert remembered_cell_edit_context(root_a, "pif_3v3_vdd") == \
        ("PIF_3V3_VDD", "FPGA")
    assert remembered_cell_edit_context(root_b, "pif_3v3_vdd") == \
        ("PIF_3V3_VDD", "Channel_1")
    # And two cell names under the SAME root are separate too.
    remember_cell_edit_context(root_a, "dac_buf", "DAC_BUF", None)
    assert remembered_cell_edit_context(root_a, "dac_buf") == ("DAC_BUF", None)
    assert remembered_cell_edit_context(root_a, "pif_3v3_vdd") == \
        ("PIF_3V3_VDD", "FPGA")


def test_last_used_overwrite(tmp_path):
    """Re-extracting the same cell from another (structurally identical)
    cluster overwrites the record — "last used", not a history."""
    root = tmp_path / "root.sexp"
    remember_cell_edit_context(root, "pif", "PIF_3V3_VDD", "Channel_0")
    remember_cell_edit_context(root, "pif", "PIF_3V3_VDD", "Channel_1")
    assert remembered_cell_edit_context(root, "pif") == \
        ("PIF_3V3_VDD", "Channel_1")


def test_missing_and_malformed_state_are_empty(tmp_path):
    """Missing record, missing root/cell, and MALFORMED stored shapes all read
    as (None, None) — never an exception (the stale-value case is the norm)."""
    root = tmp_path / "root.sexp"
    assert remembered_cell_edit_context(root, "ghost") == (None, None)
    assert remembered_cell_edit_context(None, "x") == (None, None)
    assert remembered_cell_edit_context(root, "") == (None, None)

    settings.state.set(CELL_EDIT_CONTEXT_KEY, "not-a-dict")
    assert remembered_cell_edit_context(root, "x") == (None, None)
    settings.state.set(CELL_EDIT_CONTEXT_KEY, {str(root): {"cell1": "nope"}})
    assert remembered_cell_edit_context(root, "cell1") == (None, None)


def test_remember_noop_cases_write_nothing(tmp_path):
    """A missing cell name / cluster / root writes nothing (and never raises)."""
    root = tmp_path / "root.sexp"
    remember_cell_edit_context(root, "", "PIF", None)
    remember_cell_edit_context(root, "cell1", "", None)
    remember_cell_edit_context(None, "cell1", "PIF", None)
    assert settings.state.get(CELL_EDIT_CONTEXT_KEY) in (None, {})


# ── Live-board resolution helpers ─────────────────────────────────────────
#
# cluster_present_on_board was the prefill gate until 2026-09-17 (stage 1а): a
# board read on the UI thread, swallowing every exception, which reported a busy
# socket as "the cluster is gone". It is gone itself now — the prefill judges the
# snapshot the page already holds (guards С1а/С4а at the end of this file).

def test_resolve_context_footprints_cluster_gate():
    """Without a Sheet the context resolves to every footprint of the cluster
    tag (cluster_prefix_match); a cluster absent from the board -> [] (stale),
    never an exception."""
    adapter = _cluster_adapter("PIF_3V3_VDD")
    got = resolve_context_footprints(
        adapter, adapter.get_footprints(), "PIF_3V3_VDD", None, {})
    assert {fp.uuid for fp in got} == {"fp1", "fp2"}
    assert resolve_context_footprints(
        adapter, adapter.get_footprints(), "GHOST", None, {}) == []


def test_resolve_context_footprints_sheet_narrowing_delegates(monkeypatch):
    """The Sheet step delegates to role_narrowing.narrow_candidates_by_sheet
    (the project's ONE (Sheet, Cluster) addressing cascade — no second
    terminology) and applies its result only when it reduces the set."""
    adapter = _cluster_adapter("PIF_3V3_VDD")
    called = []

    def _fake_narrow(candidates, sheet, sheet_names):
        called.append(sheet)
        return [candidates[0]]          # "narrowed" to R1's footprint

    monkeypatch.setattr(ctx_mod, "narrow_candidates_by_sheet", _fake_narrow)
    got = resolve_context_footprints(
        adapter, adapter.get_footprints(), "PIF_3V3_VDD", "FPGA", {"x": "y"})
    assert called == ["FPGA"]
    assert [fp.uuid for fp in got] == ["fp1"]


# ── cell_anchor_view: prefill + read-from-selection ──────────────────────

def _make_view(main_window, tmp_path, adapter=None, data=None):
    root = tmp_path / "root.sexp"
    _write(root, data if data is not None else _cell_data())
    if adapter is not None:
        main_window.connection.board = SimpleNamespace(adapter=adapter)
    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.load_entry("cell1", root)
    return view, root


def test_view_prefills_remembered_context_and_narrows(main_window, tmp_path):
    """Opening the cell-anchor page for a cell with a remembered Cluster that
    IS on the live board sets the Cluster combo AND narrows the Role combo —
    no click on the board needed (Phase E's main consumer).

    Э3 (plan_2026_09_14_ui_thread_offenders): the narrowing is served by the board
    SNAPSHOT (role/cluster as field VALUES), so that is what the page is given; the
    FakeAdapter below stays because the page's OTHER paths (read-from-selection, the
    marker/bbox workers) legitimately read the live board."""
    adapter = FakeAdapter()
    c1 = _fp("fp1", "R1")
    c2 = _fp("fp2", "R2")          # C2 lives on a DIFFERENT cluster
    adapter.footprints = [c1, c2]
    adapter.set_field(c1, CLUSTER_FIELD_NAME, "PIF_3V3_VDD")
    adapter.set_field(c1, ROLE_FIELD_NAME, "C1")
    adapter.set_field(c2, CLUSTER_FIELD_NAME, "AD_DAC/IC2")
    adapter.set_field(c2, ROLE_FIELD_NAME, "C2")
    main_window.connection.snapshot = [
        SimpleNamespace(role="C1", cluster="PIF_3V3_VDD"),
        SimpleNamespace(role="C2", cluster="AD_DAC/IC2")]
    remember_cell_edit_context(tmp_path / "root.sexp", "cell1",
                               "PIF_3V3_VDD", None)

    view, _ = _make_view(main_window, tmp_path, adapter=adapter)

    assert view._cluster_combo.currentText().strip() == "PIF_3V3_VDD"
    roles = [view._role_combo.itemText(i)
             for i in range(view._role_combo.count())]
    assert roles == ["C1"]            # C2 is not on the remembered cluster


def test_view_stale_remembered_cluster_leaves_fields_empty(main_window,
                                                           tmp_path):
    """THE Phase-E acceptance criterion: a remembered cluster that is NOT on the
    board leaves both working-context fields empty and raises nothing — the rest
    of the page still works.

    The judge is the page's OWN snapshot (2026-09-17, stage 1а): the page is fed a
    board read (refresh_known_roles — what DockHub.push_snapshot does on every
    ~2s tick) that does not carry the remembered cluster, so the pair is provably
    stale. An empty snapshot is the OTHER case: nothing has been read yet, so the
    pair is kept as a hint (the next test)."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", "FPGA")

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.refresh_known_roles([SimpleNamespace(role="C1", cluster="AD_DAC/IC2")])
    view.load_entry("cell1", root)

    assert view._cluster_combo.currentText().strip() == ""
    assert view._sheet_combo.currentText().strip() == ""
    # The page still functions: the full role set is available.
    roles = [view._role_combo.itemText(i)
             for i in range(view._role_combo.count())]
    assert roles == ["C1", "C2"]


def test_view_no_record_is_today_behaviour(main_window, tmp_path):
    """Without a record the combos stay empty — exactly the pre-Phase-E state."""
    view, _ = _make_view(main_window, tmp_path)
    assert view._cluster_combo.currentText().strip() == ""
    assert view._sheet_combo.currentText().strip() == ""


def test_view_opening_another_cell_drops_previous_context(main_window,
                                                          tmp_path):
    """A reused page must not leak the PREVIOUS cell's context into the next
    cell (a cell without a record opens empty)."""
    adapter = FakeAdapter()
    c1 = _fp("fp1", "R1")
    adapter.footprints = [c1]
    adapter.set_field(c1, CLUSTER_FIELD_NAME, "PIF_3V3_VDD")
    adapter.set_field(c1, ROLE_FIELD_NAME, "C1")
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", None)

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.load_entry("cell1", root)
    assert view._cluster_combo.currentText().strip() == "PIF_3V3_VDD"

    # Open a DIFFERENT cell name with no record -> the context must be empty,
    # not the previous cell's remembered cluster.
    view.load_entry("ghost", root)
    assert view._cluster_combo.currentText().strip() == ""


def test_read_from_selection_updates_remembered_context(main_window, tmp_path,
                                                        monkeypatch):
    """"Read from selection" brings a fresh Cluster — it overwrites the cell's
    remembered (last-used) context (sheet optional, stored as whatever the
    Sheet combo holds)."""
    adapter = FakeAdapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    monkeypatch.setattr(
        view_mod, "read_anchor_source",
        lambda *a, **k: {"kind": "footprint", "role": "C1", "pad": None,
                         "cluster": "PIF_3V3_VDD"})
    view, root = _make_view(main_window, tmp_path, adapter=adapter)

    view._on_read_from_selection()

    assert remembered_cell_edit_context(root, "cell1") == ("PIF_3V3_VDD", None)


# ── Two Phase C gaps fixed in this phase (same file, same commit) ─────────

def test_sheet_combo_shows_names_not_uuid_keys(main_window, tmp_path,
                                               monkeypatch):
    """The Sheet combo lists ctx.sheet_names VALUES (readable sheet names), not
    the uuid-path KEYS — ctx.sheet_names is a "path -> name" dict, iterating it
    gives keys (rename.py / trees_dock take .values())."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    ctx = SimpleNamespace(sheet_names={
        "aaaaaaaaaaaaaaaa/aaaaaaaa": "Channel_0",
        "bbbbbbbbbbbbbbbb/bbbbbbbb": "Channel_1",
    })
    monkeypatch.setattr(view_mod, "load_config", lambda path: (object(), ctx))

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)

    items = [view._sheet_combo.itemText(i)
             for i in range(view._sheet_combo.count())]
    assert items == ["Channel_0", "Channel_1"]
    assert not any("aaaa" in t or "bbbb" in t for t in items)


def test_refresh_known_roles_populates_cluster_combo(main_window, tmp_path):
    """The snapshot feed (wired into DockHub.push_snapshot, Phase C gap) makes
    the working-context Cluster combo non-empty with distinct live clusters."""
    view, _ = _make_view(main_window, tmp_path)
    assert view._cluster_combo.count() == 0       # nothing fed yet

    view.refresh_known_roles([
        SimpleNamespace(cluster="PIF_3V3_VDD", role="C1"),
        SimpleNamespace(cluster="PIF_AVDD", role="DAC"),
        SimpleNamespace(cluster="PIF_3V3_VDD", role="C2"),  # duplicate cluster
    ])

    items = [view._cluster_combo.itemText(i)
             for i in range(view._cluster_combo.count())]
    assert items == ["PIF_3V3_VDD", "PIF_AVDD"]   # sorted, deduped


def test_cluster_combo_stays_editable_after_snapshot_feed(main_window,
                                                          tmp_path):
    """Fill, never restrict: after the snapshot feed the Cluster combo still
    accepts a typed cluster that is NOT in the list (the value is read back,
    and the list is not polluted — NoInsert)."""
    view, _ = _make_view(main_window, tmp_path)
    view.refresh_known_roles([SimpleNamespace(cluster="PIF_3V3_VDD",
                                              role="C1")])

    view._cluster_combo.setCurrentText("HAND_TYPED_CLUSTER")

    assert view._cluster_combo.currentText().strip() == "HAND_TYPED_CLUSTER"
    items = [view._cluster_combo.itemText(i)
             for i in range(view._cluster_combo.count())]
    assert "HAND_TYPED_CLUSTER" not in items       # NoInsert — no pollution


# ── cell_editor: "Select cluster of this cell on the board" ──────────────

def _make_cell_dock(main_window, tmp_path, data=None):
    root = tmp_path / "root.sexp"
    _write(root, data if data is not None else _cell_data())
    dock = CellDock(main_window)
    dock.set_root_path(root)
    dock.load_entry("cell1", root)
    return dock, root


def test_select_cluster_worker_selects_exactly_the_cluster(main_window,
                                                           tmp_path):
    """The worker resolves the remembered (Cluster, Sheet) footprints and hands
    them to adapter.select_items — the whole cluster instance, nothing else."""
    adapter = _cluster_adapter("PIF_3V3_VDD")
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    dock, root = _make_cell_dock(main_window, tmp_path)
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", None)

    result = dock._run_select_cluster_on_board({
        "board": main_window.connection.board,
        "root_path": str(root),
        "cell_name": "cell1",
        "cluster": "PIF_3V3_VDD",
        "sheet": None,
    })

    assert result["selected"] == 2
    assert len(adapter.selected) == 1
    assert {fp.uuid for fp in adapter.selected[0]} == {"fp1", "fp2"}


def test_select_cluster_worker_stale_context_selects_nothing(main_window,
                                                             tmp_path):
    """A remembered cluster absent from the current board: the worker selects
    NOTHING and reports selected == 0 — no exception, no hard dependency."""
    adapter = _cluster_adapter("AD_DAC/IC2")   # the remembered cluster is gone
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    dock, root = _make_cell_dock(main_window, tmp_path)
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", "FPGA")

    result = dock._run_select_cluster_on_board({
        "board": main_window.connection.board,
        "root_path": str(root),
        "cell_name": "cell1",
        "cluster": "PIF_3V3_VDD",
        "sheet": "FPGA",
    })

    assert result["selected"] == 0
    assert adapter.selected == []


# ── G.3: the working context is remembered on a MANUAL pick ───────────────

def test_manual_cluster_pick_remembers_the_context(main_window, tmp_path):
    """Until G.3 the context was written ONLY by "Read from selection" — a
    hand-picked Cluster was never persisted."""
    view, root = _make_view(main_window, tmp_path)

    view._cluster_combo.setCurrentText("PIF_3V3_VDD")

    assert remembered_cell_edit_context(root, "cell1") == ("PIF_3V3_VDD", None)


def test_manual_sheet_pick_remembers_the_context(main_window, tmp_path):
    view, root = _make_view(main_window, tmp_path)

    view._cluster_combo.setCurrentText("PIF_3V3_VDD")
    view._sheet_combo.setCurrentText("MCU")

    assert remembered_cell_edit_context(root, "cell1") == ("PIF_3V3_VDD", "MCU")


def test_prefill_does_not_write_a_parasitic_context(main_window, tmp_path):
    """Opening a cell (prefill + the guarded combo refills) must not persist
    anything by itself."""
    _view, root = _make_view(main_window, tmp_path)

    assert remembered_cell_edit_context(root, "cell1") == (None, None)


def test_offline_prefill_applies_the_remembered_context(main_window, tmp_path):
    """G.3 cause 2: with NO live board the remembered cluster is a hint we
    cannot confirm (not a stale one) — it must be applied, not thrown away."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", "MCU")
    assert main_window.connection.board is None

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.load_entry("cell1", root)

    assert view._cluster_combo.currentText() == "PIF_3V3_VDD"


def test_live_prefill_keeps_the_hint_when_no_snapshot_was_fed_yet(main_window,
                                                                 tmp_path):
    """Р2 (stage 1а): with a live board but NO board read fed to the page yet —
    the first tick after a connect — an empty snapshot says nothing, so the
    remembered pair is applied as a HINT instead of being dropped. This test used
    to assert the opposite, on a fresh adapter read of the live board; that read
    is exactly what stage 1а removed (two UI-thread reads, one of them failing,
    were how the fields came up empty on reopen — guards С1а/С4а)."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", None)
    main_window.connection.board = SimpleNamespace(adapter=FakeAdapter())

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.load_entry("cell1", root)

    assert view._cluster_combo.currentText() == "PIF_3V3_VDD"


# ── Identified refs (2026-09-17, stage 1 of the spoke work) ───────────────
#
# The (Cluster, Sheet) pair cannot name WHICH pair of components of a spoke cell
# is being edited; the identification gives a role -> refdes map, and this key
# remembers it. It is a hint checked against the board on every use, and it is
# ERASED by any context write that carries no refs.

def _identification(cluster="FPGA_PWR_BANK", sheet=None, refs=None):
    return Identification(
        cluster=cluster, sheet=sheet,
        role_to_ref=refs if refs is not None
        else {"C_FPGA_BULK": "C69", "C_FPGA_BYPASS": "C53"},
        kind="spoke")


def test_identified_instance_round_trips_cluster_sheet_and_refs(tmp_path):
    """One write remembers all three: the working context AND the pair."""
    root = tmp_path / "root.sexp"

    remember_cell_instance(root, "fpga_pwr_bank",
                           _identification(sheet="FPGA"))

    assert remembered_cell_edit_context(root, "fpga_pwr_bank") == \
        ("FPGA_PWR_BANK", "FPGA")
    assert remembered_cell_refs(root, "fpga_pwr_bank") == \
        {"C_FPGA_BULK": "C69", "C_FPGA_BYPASS": "C53"}


def test_c8_a_context_write_without_refs_erases_them(tmp_path):
    """С8/М8: the manual Cluster/Sheet pick (and every other plain context write)
    must ERASE the remembered refs — a leftover map belongs to another instance
    and is exactly the stale data the live reader would have to refuse."""
    root = tmp_path / "root.sexp"
    remember_cell_instance(root, "fpga_pwr_bank", _identification())
    assert remembered_cell_refs(root, "fpga_pwr_bank")

    remember_cell_edit_context(root, "fpga_pwr_bank", "FPGA_PWR_BANK", None)

    assert remembered_cell_refs(root, "fpga_pwr_bank") is None
    assert remembered_cell_edit_context(root, "fpga_pwr_bank") == \
        ("FPGA_PWR_BANK", None)


def test_an_identification_without_refs_writes_none(tmp_path):
    """An empty map is not a memory: the entry behaves exactly like a plain
    context write (the caller then falls back to the honest spoke message)."""
    root = tmp_path / "root.sexp"

    remember_cell_instance(root, "fpga_pwr_bank", _identification(refs={}))

    assert remembered_cell_edit_context(root, "fpga_pwr_bank") == \
        ("FPGA_PWR_BANK", None)
    assert remembered_cell_refs(root, "fpga_pwr_bank") is None


def test_refs_are_never_a_fatal_on_missing_or_malformed_state(tmp_path):
    """§E.5 discipline: every reader degrades silently. Missing entry, a
    non-dict entry, a non-dict/empty/malformed refs map and a missing root all
    read as None."""
    root = tmp_path / "root.sexp"

    assert remembered_cell_refs(root, "ghost") is None
    assert remembered_cell_refs(None, "x") is None
    assert remembered_cell_refs(root, "") is None

    settings.state.set(CELL_EDIT_CONTEXT_KEY, "not-a-dict")
    assert remembered_cell_refs(root, "x") is None
    settings.state.set(CELL_EDIT_CONTEXT_KEY, {str(root): {"cell1": "nope"}})
    assert remembered_cell_refs(root, "cell1") is None
    settings.state.set(CELL_EDIT_CONTEXT_KEY,
                       {str(root): {"cell1": {"refs": "nope"}}})
    assert remembered_cell_refs(root, "cell1") is None
    settings.state.set(CELL_EDIT_CONTEXT_KEY,
                       {str(root): {"cell1": {"refs": {"role": None}}}})
    assert remembered_cell_refs(root, "cell1") is None


def test_remember_instance_without_a_cluster_writes_nothing(tmp_path):
    """Best-effort, never fatal, and never a half-entry: no cluster means there
    is nothing to scope the refs to."""
    root = tmp_path / "root.sexp"

    remember_cell_instance(root, "cell1", _identification(cluster=""))
    remember_cell_instance(None, "cell1", _identification())

    assert settings.state.get(CELL_EDIT_CONTEXT_KEY) in (None, {})


# ── The identified refs win over the whole cluster (Р7, С10/С16) ──────────
#
# For a spoke cell the cluster holds the same Role many times, so selecting "the
# cluster" means selecting 25 pairs where the user asked for one. When the refs
# are identified they ARE the answer — and because they are a cache, they are
# checked against the board on every use.

def _spoke_adapter():
    """Three components of one cluster: the identified pair (C68/C52) plus a
    SECOND bulk cap (C69) the cluster path would have swept in."""
    a, b, c = _fp("fp1", "C68"), _fp("fp2", "C52"), _fp("fp3", "C69")
    adapter = FakeAdapter([a, b, c])
    for fp, role in ((a, "C_FPGA_BULK"), (b, "C_FPGA_BYPASS"),
                     (c, "C_FPGA_BULK")):
        adapter.set_field(fp, CLUSTER_FIELD_NAME, "FPGA_PWR_BANK")
        adapter.set_field(fp, ROLE_FIELD_NAME, role)
    return adapter


def _spoke_payload(board, root):
    return {
        "board": board,
        "root_path": str(root),
        "cell_name": "cell1",
        "cluster": "FPGA_PWR_BANK",
        "sheet": None,
        "refs": {"C_FPGA_BULK": "C68", "C_FPGA_BYPASS": "C52"},
    }


def test_c10_identified_refs_win_over_the_whole_cluster(main_window, tmp_path):
    """С10/М10: the button selects EXACTLY the identified pair — not the three
    components of the cluster, not the 50 of the live spoke bank."""
    adapter = _spoke_adapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    dock, root = _make_cell_dock(main_window, tmp_path)

    result = dock._run_select_cluster_on_board(_spoke_payload(
        main_window.connection.board, root))

    assert result["selected"] == 2
    assert result.get("identified") is True
    assert len(adapter.selected) == 1
    assert {fp.uuid for fp in adapter.selected[0]} == {"fp1", "fp2"}


def test_c16_stale_refs_select_nothing_and_say_so(main_window, tmp_path):
    """С16/М15: a ref whose Role changed makes the map stale — NOTHING is
    selected (never a fallback to the whole cluster) and the reason travels back
    to the finish handler."""
    adapter = _spoke_adapter()
    adapter.set_field(adapter.footprints[0], ROLE_FIELD_NAME, "C_SHUNT")
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    dock, root = _make_cell_dock(main_window, tmp_path)

    result = dock._run_select_cluster_on_board(_spoke_payload(
        main_window.connection.board, root))

    assert result["stale"]
    assert "C68" in result["stale"][0]
    assert adapter.selected == []


def test_c16_a_ref_that_left_the_board_is_stale_too(main_window, tmp_path):
    """С16: the same verdict when the refdes is gone (renamed/deleted)."""
    adapter = _spoke_adapter()
    adapter.footprints = [fp for fp in adapter.footprints if fp.ref != "C52"]
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    dock, root = _make_cell_dock(main_window, tmp_path)

    result = dock._run_select_cluster_on_board(_spoke_payload(
        main_window.connection.board, root))

    assert result["stale"]
    assert "C52" in result["stale"][0]
    assert adapter.selected == []


def test_c16_finish_reports_a_stale_identification_as_a_hint(main_window,
                                                             tmp_path, caplog):
    """The UI half of С16: the stale verdict becomes one WARNING Log line that
    names the fix, and nothing is written anywhere."""
    dock, _root = _make_cell_dock(main_window, tmp_path)
    caplog.clear()

    dock._finish_select_cluster_on_board({"stale": ["C68 now has Role 'C_SHUNT'"]})

    assert "stale" in caplog.text.lower()
    assert "C68" in caplog.text


# ── С17/М16 — the failure path is a Log line, never a modal ──────────────

def _no_boxes(*args, **kwargs):
    """Stand-in for QMessageBox.warning that FAILS the test if called — the
    strongest form of "this state error must not open a dialog"."""
    raise AssertionError("a selection failure must not open a QMessageBox")


def test_c17_select_cluster_error_is_never_a_modal(main_window, tmp_path,
                                                   monkeypatch, caplog):
    """С17/М16: this stage converts the last modal of the select-cluster path
    (the worker's error branch) into a Log line — the same rule the rest of the
    GUI already follows (plan_2026_09_11_no_modals_and_busy_kicad)."""
    monkeypatch.setattr(cell_editor_mod.QMessageBox, "warning", _no_boxes)
    dock, _root = _make_cell_dock(main_window, tmp_path)
    caplog.clear()

    dock._finish_select_cluster_on_board({"error": "KiCad IPC exploded"})

    assert "KiCad IPC exploded" in caplog.text
    assert dock._active_op is None


# ── С1а–С5а: the prefill of the working context (2026-09-17, stage 1а) ─────
#
# The bug Denis hit live: after closing and reopening the editor the Sheet and
# Cluster fields came up EMPTY while Refs stayed put ("pick the working Cluster
# first" from the marker circle). The cause is that the prefill was gated on a
# LIVE BOARD READ taken on the UI thread (cluster_present_on_board): any failing
# read — the shared socket owned by the ~400ms selection tick — reads as "the
# cluster is gone", and that early return skips BOTH fields (plan §1.3 Д1).
#
# From here on the judge is the SNAPSHOT the page already holds
# (refresh_known_roles -> self._snapshot), and when there is nothing to judge the
# remembered pair is a HINT, not a stale value — the same semantics the offline
# case has had since G.3.

class _RecordingAdapter:
    """Records EVERY board read and answers nothing useful.

    The guards below assert that this list stays EMPTY. A spy that merely blew up
    would not do: the pre-2026 code catches `Exception` inside
    cluster_present_on_board, the traceback would be swallowed and the guard would
    have to read the fields to fail — i.e. fail for the wrong reason (plan §1.3 Д2)."""

    def __init__(self):
        self.calls = []

    def _rec(self, name):
        self.calls.append(name)
        return []

    def get_footprints(self):
        return self._rec("get_footprints")

    def get_field_value(self, fp, name):
        return self._rec(f"get_field_value:{name}") or None

    def get_selected_items(self):
        return self._rec("get_selected_items")

    def get_footprint(self, ref):
        return self._rec(f"get_footprint:{ref}") or None

    def get_tracks(self):
        return self._rec("get_tracks")

    def get_vias(self):
        return self._rec("get_vias")

    def select_items(self, items):
        self.calls.append("select_items")

    def refresh_board(self):
        self.calls.append("refresh_board")


class _FailingAdapter(FakeAdapter):
    """A live board whose every read blows up — the occupied-socket shape
    ("Error receiving reply from KiCad: Operation canceled")."""

    _BROKEN = "Error receiving reply from KiCad: Operation canceled"

    def get_footprints(self):
        raise RuntimeError(self._BROKEN)

    def get_field_value(self, fp, field_name):
        raise RuntimeError(self._BROKEN)


def _sheet_names_map(*names):
    """A `load_config` stand-in whose ctx.sheet_names VALUES are `names` — the
    Sheet combo is filled from the VALUES (the existing pattern of
    test_sheet_combo_shows_names_not_uuid_keys); zero names models a profile whose
    sheet list is not known yet.

    The cfg is the REAL one read from the file, because the dispatch path needs its
    cells — only the sheet map is replaced, and it is replaced for every caller of
    the module's `load_config` (prefill, `_context`, `_dispatch`)."""
    real = view_mod.load_config

    def _load(path):
        cfg, _ctx = real(path)
        return cfg, SimpleNamespace(sheet_names={
            f"{i:024x}/{i:024x}": name for i, name in enumerate(names)})

    return _load


def test_c1a_a_failing_board_read_does_not_wipe_the_remembered_context(
        main_window, tmp_path, monkeypatch):
    """С1а/М1а: Ф3 — the shape of the live bug. A board is there and its reads
    FAIL; opening the cell must still bring back Sheet, Cluster and Refs."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    remember_cell_instance(root, "cell1", _identification(
        cluster="PIF_3V3_VDD", sheet="MCU", refs={"C1": "C74", "C2": "C58"}))
    main_window.connection.board = SimpleNamespace(adapter=_FailingAdapter())
    monkeypatch.setattr(view_mod, "load_config", _sheet_names_map("MCU", "FPGA"))

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.load_entry("cell1", root)

    assert view._cluster_combo.currentText().strip() == "PIF_3V3_VDD"
    assert view._sheet_combo.currentText().strip() == "MCU"
    assert view._refs_edit.text() == "C74, C58"


def test_c2a_a_sheet_that_cannot_be_checked_yet_is_still_applied(
        main_window, tmp_path, monkeypatch):
    """С2а/М2а: Ф4 — the config's sheet list is unknown at the moment the cell is
    opened (the profile carries no schematic path, so ctx.sheet_names is empty).
    An EMPTY list is "nothing to judge", so the remembered Sheet is applied; only
    a NON-EMPTY list that lacks it makes it stale."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", "MCU")
    monkeypatch.setattr(view_mod, "load_config", _sheet_names_map())

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.load_entry("cell1", root)

    assert view._cluster_combo.currentText().strip() == "PIF_3V3_VDD"
    assert view._sheet_combo.currentText().strip() == "MCU"


def test_c2a_a_sheet_the_list_does_not_know_is_dropped(
        main_window, tmp_path, monkeypatch):
    """The other half of С2а: a NON-EMPTY sheet list that does not carry the
    remembered name is the §E.5 stale case — the field stays empty (the list
    knows better than the hint)."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", "GONE_SHEET")
    monkeypatch.setattr(view_mod, "load_config", _sheet_names_map("MCU", "FPGA"))

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.load_entry("cell1", root)

    assert view._cluster_combo.currentText().strip() == "PIF_3V3_VDD"
    assert view._sheet_combo.currentText().strip() == ""


def test_c3a_a_snapshot_tick_does_not_wipe_the_prefilled_cluster(
        main_window, tmp_path, monkeypatch):
    """С3а/М3а: Ф5 — the tick's combo refill must not clear a cluster that came
    from the remembered context, even when the (sheet-narrowed) list does not
    carry it: "Fill, never restrict" is the combo's own contract."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", None)
    monkeypatch.setattr(view_mod, "load_config", _sheet_names_map("MCU"))

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.load_entry("cell1", root)
    assert view._cluster_combo.currentText().strip() == "PIF_3V3_VDD"

    view.refresh_known_roles([SimpleNamespace(cluster="ANOTHER_BANK", role="C1")])

    assert view._cluster_combo.currentText().strip() == "PIF_3V3_VDD"


def test_c4a_opening_and_context_never_reach_the_adapter_on_the_ui_thread(
        main_window, tmp_path, monkeypatch):
    """С4а/М4а: П3.1 — the prefill, the form reload and `_context()` collect their
    inputs from the interface state alone. The adapter is a RECORDER: the list of
    calls must stay empty (see _RecordingAdapter for why a raising spy is wrong)."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", "MCU")
    adapter = _RecordingAdapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    monkeypatch.setattr(view_mod, "load_config", _sheet_names_map("MCU"))

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.load_entry("cell1", root)
    view._reload_form()
    ctx = view._context()

    assert adapter.calls == [], f"the UI thread read the board: {adapter.calls}"
    assert ctx is not None
    assert view._cluster_combo.currentText().strip() == "PIF_3V3_VDD"


def test_c5a_identified_refs_make_the_working_cluster_optional(
        main_window, tmp_path, monkeypatch, caplog):
    """С5а/М5а: Ф6/Р5 — with an identified pair the Cluster is not a prerequisite:
    "Show bbox" must dispatch the frame worker WITH the refs instead of refusing
    with "pick the working Cluster first" (the exact WARNING Denis saw after
    reopening the editor)."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    remember_cell_instance(root, "cell1", _identification(
        cluster="PIF_3V3_VDD", refs={"C1": "C74", "C2": "C58"}))
    main_window.connection.board = SimpleNamespace(adapter=_RecordingAdapter())
    monkeypatch.setattr(view_mod, "load_config", _sheet_names_map("MCU"))

    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(root)
    view.load_entry("cell1", root)
    # Model the lost cluster of the live session: the combo is EMPTY while the
    # refs are remembered (a manual clear, or a stale-context drop).
    view._loading = True
    try:
        view._cluster_combo.setCurrentText("")
    finally:
        view._loading = False

    started = []
    monkeypatch.setattr(view_mod, "start_long_op",
                        lambda *args, **kwargs: started.append(args) or object())
    caplog.clear()

    view._on_show_bbox()

    assert started, ("the frame worker must start from the remembered refs — a "
                     "cluster is not required when the pair is identified")
    assert started[0][-1] == {"C1": "C74", "C2": "C58"}
    assert "working Cluster" not in caplog.text


# ── The "Refs" tab's table in gui_state.json (2026-09-17, stage 2) ─────────
#
# The table the user types on the Refs tab is remembered under its OWN key
# (cell_role_table), NOT inside cell_edit_context. That is what makes it survive
# the stage-1 rule "a context write WITHOUT refs erases the identified refs"
# (С2и): a manual Cluster/Sheet pick on the Source tab must not wipe a table
# the user has just filled in.

def _table(refs_roles, cluster="FPGA_PWR_BANK"):
    """A saved-table dict: {cluster, rows:[{ref, role, cluster}]}."""
    return {"cluster": cluster,
            "rows": [{"ref": ref, "role": role, "cluster": ""}
                     for ref, role in refs_roles]}


def test_c2zh_role_table_round_trip_is_scoped_by_root_and_cell(tmp_path):
    """С2ж: the table survives a restart — and, like the working context, it is
    scoped per root config (a cell name is a cluster-tag slug, so the same name
    in two profiles means two different boards)."""
    root_a = tmp_path / "a.sexp"
    root_b = tmp_path / "b.sexp"
    table = _table([("C74", "C_FPGA_BULK"), ("C58", "")])

    remember_role_table(root_a, "fpga_pwr_bank", table)

    assert remembered_role_table(root_a, "fpga_pwr_bank") == table
    assert remembered_role_table(root_b, "fpga_pwr_bank") is None
    assert remembered_role_table(root_a, "other_cell") is None


def test_c2zh_clearing_the_table_forgets_it(tmp_path):
    """«Clear» empties the table: the remembered copy goes with it, otherwise the
    rows the user deleted would come back on the next open."""
    root = tmp_path / "root.sexp"
    remember_role_table(root, "cell1", _table([("C41", "C_BULK")]))

    remember_role_table(root, "cell1", {"cluster": "", "rows": []})

    assert remembered_role_table(root, "cell1") is None


def test_c2zh_a_missing_or_malformed_table_reads_as_none(tmp_path):
    """State is a hint everywhere in this project: nothing recorded, a state
    replaced by a string and a cell entry replaced by a string all read as
    "no table", never an exception."""
    root = tmp_path / "root.sexp"
    assert remembered_role_table(root, "cell1") is None
    assert remembered_role_table(None, "cell1") is None

    settings.state.set(CELL_ROLE_TABLE_KEY, "not-a-dict")
    assert remembered_role_table(root, "cell1") is None
    settings.state.set(CELL_ROLE_TABLE_KEY, {str(root): {"cell1": "nope"}})
    assert remembered_role_table(root, "cell1") is None


def test_c2z_the_stored_table_holds_only_what_the_user_typed(tmp_path):
    """С2з: the state write adds nothing of its own — the board's values are the
    snapshot's business, and a stale copy of them here would be shown as if the
    user had typed it."""
    root = tmp_path / "root.sexp"
    table = _table([("C74", "C_FPGA_BULK")], cluster="FPGA_PWR_BANK")

    remember_role_table(root, "cell1", table)

    assert settings.state.get(CELL_ROLE_TABLE_KEY)[str(root)]["cell1"] == table


def test_c2i_a_context_write_without_refs_keeps_the_table(tmp_path):
    """С2и — the reason for the separate key. Picking another Cluster/Sheet on
    the Source tab erases the IDENTIFIED refs of the instance (stage 1, on
    purpose), and a fresh identification with nobody selected writes nothing.
    Neither may touch the role table."""
    root = tmp_path / "root.sexp"
    table = _table([("C41", "C_BULK")], cluster="")
    remember_role_table(root, "cell1", table)

    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", None)
    remember_cell_instance(root, "cell1",
                           _identification(cluster="PIF_3V3_VDD", refs={}))

    assert remembered_cell_refs(root, "cell1") is None      # the refs are gone
    assert remembered_role_table(root, "cell1") == table    # the table is not
