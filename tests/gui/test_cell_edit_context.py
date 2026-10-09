# tests/gui/test_cell_edit_context.py
"""Tests for the remembered (Cluster, Sheet) cell-edit context — Phase E of
plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md ("Идея D" of
note_2026_09_08_cell_anchor_selection_and_coordinate_converter.md).

Covers:
  * gui/cell_edit_context.py — the gui_state.json round-trip (per-root scoping,
    "last used" overwrite, silent degradation on missing/malformed state). The
    live-board resolution helper (resolve_context_footprints) MOVED to
    kicadstamp/cell_instance.py (2026-10-05) and is tested in
    tests/test_cell_instance.py;
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
import pytest
from types import SimpleNamespace

import gui.cell_edit_context as ctx_mod
import gui.docks.cell_editor as cell_editor_mod
import gui.entity.anchor_tab as anchor_mod
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
)
from gui.cell_identification import Identification
from gui.docks.cell_editor import CellDock
from gui.entity.anchor_tab import AnchorTabWidget
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import Vector2


# ── Shared fixtures/helpers ───────────────────────────────────────────────


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")


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


class _FakeAdapter:
    """Duck-typed adapter: in-memory footprints with Cluster/Role field values,
    and a recorded list of whatever select_items() was given."""

    def __init__(self, footprints=None):
        self.footprints = footprints or []
        self.field_values = {}
        self.selected = []
        # The worker builds its OWN adapter now (СЦ-3) and closes it; the fake
        # records that so the guards can assert the lifetime.
        self.closed = False

    def set_field(self, fp, name, value):
        self.field_values[(getattr(fp, "uuid", None), name)] = value

    def refresh_board(self):
        pass

    def close(self):
        self.closed = True

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
    adapter = _FakeAdapter([a, b, foreign])
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




# ── Two Phase C gaps fixed in this phase (same file, same commit) ─────────

def _make_cell_dock(main_window, tmp_path, data=None):
    root = tmp_path / "root.sexp"
    _write(root, data if data is not None else _cell_data())
    dock = CellDock(main_window)
    dock.set_root_path(root)
    dock.load_entry("cell1", root)
    return dock, root


def test_select_cluster_worker_selects_exactly_the_cluster(main_window,
                                                           tmp_path,
                                                           monkeypatch):
    """The worker resolves the remembered (Cluster, Sheet) footprints and hands
    them to adapter.select_items — the whole cluster instance, nothing else."""
    adapter = _cluster_adapter("PIF_3V3_VDD")
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    dock, root = _make_cell_dock(main_window, tmp_path)
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", None)
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda **kw: adapter)

    result = dock._run_select_cluster_on_board({
        "root_path": str(root),
        "timeout_ms": 5000,
        "cell_name": "cell1",
        "cluster": "PIF_3V3_VDD",
        "sheet": None,
    })

    assert result["selected"] == 2
    assert len(adapter.selected) == 1
    assert {fp.uuid for fp in adapter.selected[0]} == {"fp1", "fp2"}
    assert adapter.closed is True          # СЦ-3: the worker's own adapter


def test_select_cluster_worker_stale_context_selects_nothing(main_window,
                                                             tmp_path,
                                                             monkeypatch):
    """A remembered cluster absent from the current board: the worker selects
    NOTHING and reports selected == 0 — no exception, no hard dependency."""
    adapter = _cluster_adapter("AD_DAC/IC2")   # the remembered cluster is gone
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    dock, root = _make_cell_dock(main_window, tmp_path)
    remember_cell_edit_context(root, "cell1", "PIF_3V3_VDD", "FPGA")
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda **kw: adapter)

    result = dock._run_select_cluster_on_board({
        "root_path": str(root),
        "timeout_ms": 5000,
        "cell_name": "cell1",
        "cluster": "PIF_3V3_VDD",
        "sheet": "FPGA",
    })

    assert result["selected"] == 0
    assert adapter.selected == []
    assert adapter.closed is True          # СЦ-3: the worker's own adapter


# ── G.3: the working context is remembered on a MANUAL pick ───────────────

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
    adapter = _FakeAdapter([a, b, c])
    for fp, role in ((a, "C_FPGA_BULK"), (b, "C_FPGA_BYPASS"),
                     (c, "C_FPGA_BULK")):
        adapter.set_field(fp, CLUSTER_FIELD_NAME, "FPGA_PWR_BANK")
        adapter.set_field(fp, ROLE_FIELD_NAME, role)
    return adapter


def _spoke_payload(root):
    # СЦ-3: the payload carries NO board handle (the UI-thread read the door
    # forbids) — the worker builds its own adapter; only its timeout travels.
    return {
        "root_path": str(root),
        "timeout_ms": 5000,
        "cell_name": "cell1",
        "cluster": "FPGA_PWR_BANK",
        "sheet": None,
        "refs": {"C_FPGA_BULK": "C68", "C_FPGA_BYPASS": "C52"},
    }


def test_c10_identified_refs_win_over_the_whole_cluster(main_window, tmp_path,
                                                        monkeypatch):
    """С10/М10: the button selects EXACTLY the identified pair — not the three
    components of the cluster, not the 50 of the live spoke bank."""
    adapter = _spoke_adapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    dock, root = _make_cell_dock(main_window, tmp_path)
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda **kw: adapter)

    result = dock._run_select_cluster_on_board(_spoke_payload(root))

    assert result["selected"] == 2
    assert result.get("identified") is True
    assert len(adapter.selected) == 1
    assert {fp.uuid for fp in adapter.selected[0]} == {"fp1", "fp2"}
    assert adapter.closed is True          # СЦ-3: the worker's own adapter


def test_c16_stale_refs_select_nothing_and_say_so(main_window, tmp_path,
                                                  monkeypatch):
    """С16/М15: a ref whose Role changed makes the map stale — NOTHING is
    selected (never a fallback to the whole cluster) and the reason travels back
    to the finish handler."""
    adapter = _spoke_adapter()
    adapter.set_field(adapter.footprints[0], ROLE_FIELD_NAME, "C_SHUNT")
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    dock, root = _make_cell_dock(main_window, tmp_path)
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda **kw: adapter)

    result = dock._run_select_cluster_on_board(_spoke_payload(root))

    assert result["stale"]
    assert "C68" in result["stale"][0]
    assert adapter.selected == []
    assert adapter.closed is True          # СЦ-3: the worker's own adapter


def test_c16_a_ref_that_left_the_board_is_stale_too(main_window, tmp_path,
                                                    monkeypatch):
    """С16: the same verdict when the refdes is gone (renamed/deleted)."""
    adapter = _spoke_adapter()
    adapter.footprints = [fp for fp in adapter.footprints if fp.ref != "C52"]
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    dock, root = _make_cell_dock(main_window, tmp_path)
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda **kw: adapter)

    result = dock._run_select_cluster_on_board(_spoke_payload(root))

    assert result["stale"]
    assert "C52" in result["stale"][0]
    assert adapter.selected == []
    assert adapter.closed is True          # СЦ-3: the worker's own adapter


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


class _FailingAdapter(_FakeAdapter):
    """A live board whose every read blows up — the occupied-socket shape
    ("Error receiving reply from KiCad: Operation canceled")."""

    _BROKEN = "Error receiving reply from KiCad: Operation canceled"

    def get_footprints(self):
        raise RuntimeError(self._BROKEN)

    def get_field_value(self, fp, field_name):
        raise RuntimeError(self._BROKEN)


def test_c5a_identified_refs_make_the_working_cluster_optional(
        main_window, tmp_path, monkeypatch, caplog):
    """С5а/М5а: Ф6/Р5 — with an identified pair the Cluster is not a prerequisite:
    "Show bbox" must dispatch the frame worker WITH the refs instead of refusing
    with "pick the working Cluster first" (the exact WARNING Denis saw after
    reopening the editor).

    Step 4 of plan_2026_10_09_entity_page moved the anchor to the ENTITY page with
    the entity's FIXED address: model the lost cluster of the live session by
    handing the tab an entity that carries its refs but NO cluster."""
    root = tmp_path / "root.sexp"
    _write(root, _cell_data())
    main_window.connection.board = SimpleNamespace(adapter=_RecordingAdapter())

    tab = AnchorTabWidget(main_window, connection=main_window.connection,
                          parent=main_window)
    tab.set_root_path(root)
    tab.set_context("cell1", "", "", root,
                    refs={"C1": "C74", "C2": "C58"}, entity_count=1)

    started = []
    monkeypatch.setattr(anchor_mod, "start_long_op",
                        lambda *args, **kwargs: started.append(args) or object())
    caplog.clear()

    tab._on_show_bbox()

    assert started, ("the frame worker must start from the remembered refs — a "
                     "cluster is not required when the pair is identified")
    assert started[0][-1] == {"C1": "C74", "C2": "C58"}
    assert "no Cluster" not in caplog.text


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
