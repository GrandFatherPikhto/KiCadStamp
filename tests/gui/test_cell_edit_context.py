# tests/gui/test_cell_edit_context.py
"""Tests for the remembered (Cluster, Sheet) cell-edit context — Phase E of
plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md ("Идея D" of
note_2026_09_08_cell_anchor_selection_and_coordinate_converter.md).

Covers:
  * gui/cell_edit_context.py — the gui_state.json round-trip (per-root scoping,
    "last used" overwrite, silent degradation on missing/malformed state) and
    the live-board resolution helpers (cluster_present_on_board /
    resolve_context_footprints);
  * the cell-anchor page (gui/docks/cell_anchor_view.py) — opening a cell
    prefills the working Sheet/Cluster combos from the remembered context, and
    "Read from selection" overwrites it with the cluster it just read;
  * the Cell editor (gui/docks/cell_editor.py) — the "Select cluster of this
    cell on the board" worker selects exactly the remembered (Cluster, Sheet)
    footprints, and a stale context selects nothing (never a fatal).

Headless and board-mutation-free, same reasoning as test_cell_anchor_view.py /
test_cell_editor.py — selection/resolution run against fake adapters.
"""
from types import SimpleNamespace

import gui.cell_edit_context as ctx_mod
import gui.docks.cell_anchor_view as view_mod
from gui import settings
from gui.cell_edit_context import (
    CELL_EDIT_CONTEXT_KEY,
    cluster_present_on_board,
    remember_cell_edit_context,
    remembered_cell_edit_context,
    resolve_context_footprints,
)
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

def test_cluster_present_on_board():
    """cluster_present_on_board gates prefill: present cluster -> True; stale /
    offline / empty -> False, never raising."""
    adapter = _cluster_adapter("PIF_3V3_VDD")
    assert cluster_present_on_board(adapter, "PIF_3V3_VDD") is True
    assert cluster_present_on_board(adapter, "PIF_NOPE") is False
    assert cluster_present_on_board(None, "PIF_3V3_VDD") is False
    assert cluster_present_on_board(adapter, "") is False


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
    view = CellAnchorView(main_window, connection=main_window.connection)
    view.set_root_path(root)
    view.load_entry("cell1", root)
    return view, root


def test_view_prefills_remembered_context_and_narrows(main_window, tmp_path):
    """Opening the cell-anchor page for a cell with a remembered Cluster that
    IS on the live board sets the Cluster combo AND narrows the Role combo —
    no click on the board needed (Phase E's main consumer)."""
    adapter = FakeAdapter()
    c1 = _fp("fp1", "R1")
    c2 = _fp("fp2", "R2")          # C2 lives on a DIFFERENT cluster
    adapter.footprints = [c1, c2]
    adapter.set_field(c1, CLUSTER_FIELD_NAME, "PIF_3V3_VDD")
    adapter.set_field(c1, ROLE_FIELD_NAME, "C1")
    adapter.set_field(c2, CLUSTER_FIELD_NAME, "AD_DAC/IC2")
    adapter.set_field(c2, ROLE_FIELD_NAME, "C2")
    remember_cell_edit_context(tmp_path / "root.sexp", "cell1",
                               "PIF_3V3_VDD", None)

    view, _ = _make_view(main_window, tmp_path, adapter=adapter)

    assert view._cluster_combo.currentText().strip() == "PIF_3V3_VDD"
    roles = [view._role_combo.itemText(i)
             for i in range(view._role_combo.count())]
    assert roles == ["C1"]            # C2 is not on the remembered cluster


def test_view_stale_remembered_cluster_leaves_fields_empty(main_window,
                                                           tmp_path):
    """THE Phase-E acceptance criterion: a remembered cluster that does NOT
    exist on the current board leaves both working-context fields empty and
    raises nothing — the rest of the page still works."""
    adapter = FakeAdapter()          # no footprint carries the remembered cluster
    remember_cell_edit_context(tmp_path / "root.sexp", "cell1",
                               "PIF_3V3_VDD", "FPGA")

    view, _ = _make_view(main_window, tmp_path, adapter=adapter)

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

    view = CellAnchorView(main_window, connection=main_window.connection)
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

    view = CellAnchorView(main_window, connection=main_window.connection)
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
