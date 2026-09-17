# tests/gui/test_spoke_cell_identification.py
"""Guards for stage 1 of the SPOKE cell work — identifying a cell instance by
refs instead of by (Cluster, Role) (plan_2026_09_17_spoke_s1_identify_by_selection.md,
design_2026_09_17_spoke_cell_editing.md §2.1/§3).

Why the refs path exists at all: a spoke cell's Cluster holds the SAME Role many
times (measured on the live board 2026-09-17: FPGA_PWR_BANK carries
C_FPGA_BULK x25 and C_FPGA_BYPASS x25), so the cell editor's live frame reader
gui/docks/live_position._live_cluster_frame — which looks every role up in the
working cluster through resolve_footprint_by_cluster_role and needs the match to
be UNIQUE — cannot find the instance, and the user is told a flat lie ("role
'C_FPGA_BULK' of this cell has no footprint in cluster 'FPGA_PWR_BANK'").
The diagnostic probe (kicadstamp/diagnostics/probe_spoke_cell_identification.py)
proved that the ONLY broken link is that lookup: with the role->refs pair pinned,
the same function reproduced the spoke frame to 0.0000 mm / 0.000° on 24 of 24
spokes. Hence `role_to_ref` and nothing else below it changes.

Headless: a duck-typed adapter stands in for the live board (the same discipline
tests/gui/test_cell_anchor_view.py uses for the frame tests). No Qt needed —
live_position.py is deliberately Qt-free.
"""
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

import gui.docks.cell_editor as cell_editor_mod
from gui.cell_edit_context import remember_cell_instance, remembered_cell_refs
from gui.cell_identification import (
    Identification,
    SelectionRecord,
    identify_cell_instance,
)
from gui.docks import cell_anchor_view as view_mod
from gui.docks import live_position
from gui.docks.cell_anchor_view import CellAnchorView
from gui.docks.cell_editor import CellDock
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.utils.units import MM

BULK = "C_FPGA_BULK"
BYPASS = "C_FPGA_BYPASS"
CLUSTER = "FPGA_PWR_BANK"


def _fp(ref, role, cluster, x_mm, y_mm):
    """A live footprint with its Role/Cluster FIELD values, standing at (x, y)."""
    fp = Footprint(ref=ref, uuid=f"u-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm),
                   angle_deg=0.0, layer="F.Cu")
    fp.role = role
    fp.cluster = cluster
    fp.sheet_path_uuids = []
    return fp


class _SpokeAdapter:
    """The v103 FPGA_PWR_BANK shape: ONE cluster holding `pairs` identical
    (BULK, BYPASS) pairs, so each of the cell's roles occurs `pairs` times.

    Pair k stands at BULK = (100 + 20k, 200), BYPASS = BULK + (10, -4) — the
    cell's own stored offsets — so a frame read from pair k must land on pair k
    and NOT on the first role match. `calls` records the live-read sequence
    (the K.1 rule: refresh first, then read footprints)."""

    def __init__(self, pairs=4):
        self._fps = []
        for k in range(pairs):
            x_mm = 100.0 + 20.0 * k
            self._fps.append(_fp(f"C{60 + 2 * k}", BULK, CLUSTER, x_mm, 200.0))
            self._fps.append(_fp(f"C{60 + 2 * k + 1}", BYPASS, CLUSTER,
                                 x_mm + 10.0, 196.0))
        self.calls = []

    def refresh_board(self):
        self.calls.append("refresh")

    def get_footprints(self):
        self.calls.append("get_footprints")
        return list(self._fps)

    def get_footprint(self, ref):
        """The by-refdes read the identification path uses (IBoardAdapter's own
        method — the refs path must not walk the cluster looking for a role)."""
        self.calls.append(f"get_footprint:{ref}")
        return next((fp for fp in self._fps if fp.ref == ref), None)

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return fp.role
        if name == CLUSTER_FIELD_NAME:
            return fp.cluster
        return None


def _spoke_cell():
    """The `fpga_pwr_bank` cell as the config stores it: TWO roles, BULK at the
    mount (0, 0) and BYPASS at (10, -4); the anchor is role-only (no pad), so no
    board pad read is involved in the frame."""
    comps = [SimpleNamespace(role=BULK, offset_along_mm=0.0,
                             offset_across_mm=0.0, angle_deg=0.0),
             SimpleNamespace(role=BYPASS, offset_along_mm=10.0,
                             offset_across_mm=-4.0, angle_deg=0.0)]
    return SimpleNamespace(name="fpga_pwr_bank", layer="F.Cu", components=comps,
                           anchor_xy=None, anchor_pad=None, anchor_role=BULK,
                           vias=[], tracks=[], clone_placements=[])


def _fatal_title(message: str) -> str:
    """The 'FATAL ERROR: ...' line — format_fatal_error also appends a generic
    footer whose wording is not the error under test."""
    return next(line for line in message.splitlines() if "FATAL ERROR" in line)


# ── С1/М1 — with role_to_ref the frame comes from THAT pair ────────────────

def test_c1_frame_is_built_from_the_given_refs_not_the_first_role_match():
    """С1: the pair the user identified wins over whichever pair the cluster
    search would have grabbed. Pair 3 is asked for; every other pair stands
    somewhere else, so taking the first role match (mutation M1) can only land
    on pair 1 — or, on a spoke board, refuse outright."""
    adapter = _SpokeAdapter(pairs=4)
    origin, rotation, mirror = live_position._live_cluster_frame(
        adapter, _spoke_cell(), CLUSTER, "", {"MCU": "MCU"},
        role_to_ref={BULK: "C64", BYPASS: "C65"})

    assert origin.x == int(140 * MM) and origin.y == int(200 * MM)
    assert rotation == 0.0
    assert mirror is False


def test_c1_refresh_still_happens_before_the_read():
    """The refs path keeps the K.1 contract (2026-09-10): the board is refreshed
    before any live read, or a moved spoke draws at its old position."""
    adapter = _SpokeAdapter(pairs=2)
    live_position._live_cluster_frame(
        adapter, _spoke_cell(), CLUSTER, "", {},
        role_to_ref={BULK: "C62", BYPASS: "C63"})

    assert adapter.calls[0] == "refresh"
    assert adapter.calls.count("refresh") == 1


# ── С2/М2 — a ref whose Role changed is a STALE identification ─────────────

def test_c2_ref_with_a_foreign_role_is_a_stale_fatal():
    """С2: the refdes still exists but now carries another Role — the
    identification is stale, and the message must name BOTH roles and the ref,
    never a generic 'no footprint' (which is what the user saw before and what
    sent him hunting for a tagging bug that was not there)."""
    adapter = _SpokeAdapter(pairs=2)
    adapter._fps[0].role = "C_SHUNT"          # C60 is no longer the bulk cap

    with pytest.raises(ValidationError) as ei:
        live_position._live_cluster_frame(
            adapter, _spoke_cell(), CLUSTER, "", {},
            role_to_ref={BULK: "C60", BYPASS: "C61"})

    title = _fatal_title(str(ei.value))
    assert "stale" in title
    assert "C60" in title
    assert "C_SHUNT" in title          # what the board says now
    assert BULK in title               # what the cell expects


# ── С3/М3 — a ref that is gone from the board is a STALE identification ────

def test_c3_ref_absent_from_the_board_is_a_stale_fatal():
    """С3: the pair was identified against a board state that no longer exists —
    a refdes was deleted or renamed. Refuse; never silently fall back to the
    remaining role, which would draw a frame from half a pair."""
    adapter = _SpokeAdapter(pairs=2)
    adapter._fps = [fp for fp in adapter._fps if fp.ref != "C62"]

    with pytest.raises(ValidationError) as ei:
        live_position._live_cluster_frame(
            adapter, _spoke_cell(), CLUSTER, "", {},
            role_to_ref={BULK: "C62", BYPASS: "C63"})

    title = _fatal_title(str(ei.value))
    assert "stale" in title
    assert "C62" in title
    # The ABSENT wording specifically — "now has Role None" would be a different
    # story (and would let a "skip the missing ref" mutation survive).
    assert "is not on the live board" in title


# ── С4/М4 — without refs, a spoke says WHY (multiplicity), not 'no footprint' ─

def test_c4_spoke_without_refs_reports_the_role_multiplicity():
    """С4: no refs (an old remembered context) on a spoke cluster — the honest
    message names the repeated role and its count and tells the user which button
    identifies the instance. 'has no footprint in cluster' stays reserved for a
    role that is genuinely absent."""
    adapter = _SpokeAdapter(pairs=4)

    with pytest.raises(ValidationError) as ei:
        live_position._live_cluster_frame(adapter, _spoke_cell(), CLUSTER, "", {})

    title = _fatal_title(str(ei.value))
    assert "occurs 4 times" in title
    assert BULK in title
    assert CLUSTER in title
    assert "no footprint" not in title
    assert "Fill from selection" in str(ei.value)


def test_c4b_duplicated_role_is_refused_even_when_another_role_resolves():
    """С4b: a HALF-spoke (one role repeated, another unique) must not quietly use
    the unique role — that would draw the overlay of an instance the user never
    picked. Any repeated cell role makes the cluster search unusable."""
    cell = _spoke_cell()
    adapter = _SpokeAdapter(pairs=2)
    # BYPASS is unique in the cluster now: only C61 survives.
    adapter._fps = [fp for fp in adapter._fps if fp.ref != "C63"]

    with pytest.raises(ValidationError) as ei:
        live_position._live_cluster_frame(adapter, cell, CLUSTER, "", {})

    title = _fatal_title(str(ei.value))
    assert "occurs 2 times" in title
    assert BULK in title


# ── The genuinely-absent case keeps its own message ────────────────────────

def test_role_absent_from_the_cluster_still_says_no_footprint():
    """The honest-absence wording is NOT lost while fixing the spoke case: a role
    that simply is not tagged in the cluster keeps the old message."""
    adapter = _SpokeAdapter(pairs=1)
    adapter._fps = [_fp("C70", "SOMETHING_ELSE", CLUSTER, 100.0, 200.0)]

    with pytest.raises(ValidationError) as ei:
        live_position._live_cluster_frame(adapter, _spoke_cell(), CLUSTER, "", {})

    title = _fatal_title(str(ei.value))
    assert "has no footprint in cluster" in title
    assert "occurs" not in title


def test_cluster_absent_from_the_board_keeps_its_own_message():
    """The cluster-not-on-the-board wording is untouched by this stage."""
    adapter = _SpokeAdapter(pairs=1)
    for fp in adapter._fps:
        fp.cluster = "OTHER_BANK"

    with pytest.raises(ValidationError) as ei:
        live_position._live_cluster_frame(adapter, _spoke_cell(), CLUSTER, "", {})

    title = _fatal_title(str(ei.value))
    assert "not on the live board" in title


def test_role_multiplicity_counter_reads_each_footprint_once():
    """The multiplicity helper behind С4 must not re-walk the board per role: the
    live cluster is read in ONE pass and counted in memory (the probe measures
    the same shape — role_multiplicity over an already-read footprint list)."""
    adapter = _SpokeAdapter(pairs=3)

    counts = live_position.role_multiplicity_in_cluster(
        adapter, CLUSTER, {BULK, BYPASS})

    assert counts == Counter({BULK: 3, BYPASS: 3})
    assert adapter.calls.count("get_footprints") == 1


# ── С5/С6/С7 — the identification of a SELECTION (pure, no board) ──────────
#
# identify_cell_instance is the button's whole brain: the worker reads the board
# (the selection, the cluster's members with their roles) and the UI thread runs
# THIS pure function on that data. It mirrors the diagnostic probe's
# classification step for step (probe_spoke_cell_identification.probe_selection),
# including the ORDER of the refusals — the probe is the "before/after" ruler and
# is not edited, so the two must agree by construction.

def _sel(ref, role, cluster=CLUSTER, sheet=("FP",)):
    """One selected component, shaped like explore.Selected (ref/role/cluster/
    sheet; `.sheet` is the pre-resolved chain, exactly what the worker hands the
    UI thread)."""
    return SimpleNamespace(ref=ref, role=role, cluster=cluster, sheet=list(sheet))


def _member(ref, role, sheet=("FP",)):
    """One board member of the cluster INSTANCE (a footprint with its Role)."""
    return SimpleNamespace(ref=ref, role=role, sheet=list(sheet))


def _roles_cell(*roles, name="pif_3v3_vdd"):
    comps = [SimpleNamespace(role=r) for r in roles]
    return SimpleNamespace(name=name, components=comps)


def _entity(cluster, sheet):
    """An Entity (entities:) row — the sheet rule maps a footprint's chain to the
    placed instance through it (reead.instance_sheet)."""
    return SimpleNamespace(cluster=cluster, sheet=sheet)


# ── С5/М5 — an ordinary cluster: refs of EVERY cell role found in the cluster ─

def test_c5_partial_selection_fills_the_refs_of_all_roles_in_the_cluster():
    """С5: selecting PART of an ordinary cluster identifies the whole instance:
    the refs of the roles the user did not click are found in the cluster, so the
    Source tab (and every later read) covers all of them. Mutation M5 (take only
    the clicked refs) must break this."""
    cell = _roles_cell("R1", "R2", "R3")
    members = [_member("A", "R1"), _member("B", "R2"), _member("C", "R3")]

    ident = identify_cell_instance(
        cell, [_sel("A", "R1")], members, [_entity(CLUSTER, "FP")], {})

    assert ident.kind == "cluster"
    assert ident.cluster == CLUSTER
    assert ident.sheet == "FP"
    assert ident.role_to_ref == {"R1": "A", "R2": "B", "R3": "C"}


def test_c5_a_role_missing_from_the_cluster_is_simply_left_out():
    """A cell role with no component in the cluster is NOT an error here: the map
    carries what the cluster has, and the caller logs the missing role's name
    (plan Р3)."""
    cell = _roles_cell("R1", "R2", "R3")
    members = [_member("A", "R1"), _member("B", "R2")]

    ident = identify_cell_instance(
        cell, [_sel("A", "R1")], members, [_entity(CLUSTER, "FP")], {})

    assert ident.role_to_ref == {"R1": "A", "R2": "B"}
    assert "R3" not in ident.role_to_ref


def test_c5_second_instance_on_another_sheet_is_not_mixed_in():
    """The same Cluster tag CAN stand on two sheets (cloned sheets). Only the
    members of the identified SHEET may contribute refs — otherwise the Source
    tab would fill in a component of the neighbouring channel."""
    cell = _roles_cell("R1", "R2")
    members = [_member("A", "R1", sheet=("MCU",)),
               _member("X", "R2", sheet=("MCU",)),
               _member("B", "R1", sheet=("FPGA",)),
               _member("Y", "R2", sheet=("FPGA",))]

    ident = identify_cell_instance(
        cell, [_sel("B", "R1", sheet=("FPGA",))], members,
        [_entity(CLUSTER, "FPGA")], {})

    assert ident.sheet == "FPGA"
    assert ident.role_to_ref == {"R1": "B", "R2": "Y"}


# ── С6/М6 — a spoke: exactly one component per cell role ───────────────────

def test_c6_spoke_is_identified_by_its_pair():
    """С6: with a repeated role in the cluster the instance is the SELECTED pair,
    and the refs come straight from it — no cluster lookup is involved."""
    cell = _spoke_cell()
    members = [_member("C68", BULK), _member("C52", BYPASS),
               _member("C69", BULK), _member("C53", BYPASS)]

    ident = identify_cell_instance(
        cell, [_sel("C69", BULK), _sel("C53", BYPASS)], members,
        [_entity(CLUSTER, "FP")], {})

    assert ident.kind == "spoke"
    assert ident.role_to_ref == {BULK: "C69", BYPASS: "C53"}
    assert dict(ident.repeated_roles) == {BULK: 2, BYPASS: 2}


def test_c6_spoke_needs_exactly_one_component_per_cell_role():
    """С6/М6: a spoke is pinned to ONE pair — a selection that does not cover
    every role of the cell is refused by ROLE NAME (mutation M6, allowing the
    incomplete set, must break this)."""
    cell = _spoke_cell()
    members = [_member("C68", BULK), _member("C52", BYPASS),
               _member("C69", BULK), _member("C53", BYPASS)]

    with pytest.raises(ValidationError) as ei:
        identify_cell_instance(
            cell, [_sel("C69", BULK)], members, [_entity(CLUSTER, "FP")], {})

    message = str(ei.value)
    assert "exactly one component per cell role" in message
    assert BYPASS in message


def test_c6_spoke_selected_twice_falls_to_the_duplicate_rule_first():
    """The duplicate-Role refusal comes BEFORE the spoke rule (the probe's own
    order): clicking two bulks says nothing about which one is meant, and the
    message names the role instead of talking about pairs."""
    cell = _spoke_cell()
    members = [_member("C68", BULK), _member("C52", BYPASS),
               _member("C69", BULK), _member("C53", BYPASS)]

    with pytest.raises(ValidationError) as ei:
        identify_cell_instance(
            cell, [_sel("C68", BULK), _sel("C69", BULK), _sel("C52", BYPASS)],
            members, [_entity(CLUSTER, "FP")], {})

    assert "twice" in str(ei.value)
    assert BULK in str(ei.value)


def test_c7_component_without_a_cluster_is_refused_by_ref():
    """Without a Cluster tag there is no instance to identify — the same class of
    refusal as a missing Role, and it must name the ref."""
    cell = _roles_cell("R1")
    with pytest.raises(ValidationError) as ei:
        identify_cell_instance(
            cell, [_sel("A", "R1", cluster=None)], [], [], {})
    message = str(ei.value)
    assert "Cluster" in message
    assert "A" in message


# ── С7/М7 — the refusals, in the probe's own order ─────────────────────────

def test_c7_nothing_selected_is_refused():
    with pytest.raises(ValidationError) as ei:
        identify_cell_instance(_roles_cell("R1"), [], [], [], {})
    assert "nothing is selected" in str(ei.value)


def test_c7_component_without_a_role_is_refused_by_ref():
    cell = _roles_cell("R1", "R2")
    with pytest.raises(ValidationError) as ei:
        identify_cell_instance(
            cell, [_sel("A", None), _sel("B", "R2")], [], [], {})
    message = str(ei.value)
    assert "Role" in message
    assert "A" in message


def test_c7_a_role_selected_twice_is_refused_with_the_role():
    """С7/М7: the same Role clicked twice says nothing about WHICH component is
    meant (mutation M7 — dropping this check — must break this test)."""
    cell = _roles_cell("R1", "R2")
    with pytest.raises(ValidationError) as ei:
        identify_cell_instance(
            cell, [_sel("A", "R1"), _sel("B", "R1")], [], [], {})
    message = str(ei.value)
    assert "twice" in message
    assert "R1" in message


def test_c7_two_clusters_are_refused_with_both_names():
    cell = _roles_cell("R1", "R2")
    with pytest.raises(ValidationError) as ei:
        identify_cell_instance(
            cell, [_sel("A", "R1"), _sel("B", "R2", cluster="OTHER")], [], [], {})
    message = str(ei.value)
    assert CLUSTER in message and "OTHER" in message


def test_c7_two_sheets_of_one_cluster_are_refused():
    """One Cluster tag on two sheets means two INSTANCES — say which, never pick
    one silently (the same 'never guess' rule the cluster check follows)."""
    cell = _roles_cell("R1", "R2")
    with pytest.raises(ValidationError) as ei:
        identify_cell_instance(
            cell, [_sel("A", "R1", sheet=("FP",)), _sel("B", "R2", sheet=("MCU",))],
            [], [], {})
    message = str(ei.value)
    assert "FP" in message and "MCU" in message


def test_c7_role_not_in_the_cell_is_refused_with_both_names():
    cell = _roles_cell("R1", "R2")
    with pytest.raises(ValidationError) as ei:
        identify_cell_instance(
            cell, [_sel("C12", "WEIRD")], [], [], {})
    message = str(ei.value)
    assert "C12" in message
    assert "WEIRD" in message
    assert "R1" in message or "pif_3v3_vdd" in message


# ── С9/М9 — the three overlay workers forward the refs ─────────────────────
#
# The frame itself is tested above; what С9 guards is the WIRING: every worker
# the anchor page dispatches must hand the identified map to _live_cluster_frame,
# or the overlay would silently draw the cluster-search result (i.e. nothing at
# all on a spoke). The spy raises instead of reading the board, so the test needs
# no adapter — it only records what arrived.

def test_c9_all_three_overlay_workers_forward_the_refs(monkeypatch):
    """С9/М9: _ensure_bbox_worker, _ensure_marker_worker and _read_marker_worker
    each pass role_to_ref through (dropping it in ANY ONE of them must fail)."""
    from gui.docks import cell_anchor_view as view_mod

    seen = []

    def spy(adapter, cell, cluster, sheet, sheet_names, role_to_ref=None):
        seen.append(role_to_ref)
        raise ValidationError("spy: the frame was reached, stop before the read")

    monkeypatch.setattr(view_mod, "_live_cluster_frame", spy)
    # The marker READER asks the overlay owner where the dragged marker is before
    # it derives the frame — stub that one lookup out.
    monkeypatch.setattr(view_mod.overlay_markers.owner, "read_position",
                        lambda adapter, key: (1.0, 2.0))

    refs = {BULK: "C69", BYPASS: "C53"}
    workers = (
        lambda: view_mod._ensure_bbox_worker(
            None, _spoke_cell(), CLUSTER, "", {}, "key", "User.KiCadStamp", refs),
        lambda: view_mod._ensure_marker_worker(
            None, _spoke_cell(), CLUSTER, "", {}, "key", "User.KiCadStamp", refs),
        lambda: view_mod._read_marker_worker(
            None, _spoke_cell(), CLUSTER, "", {}, "key", refs),
    )
    for worker in workers:
        with pytest.raises(ValidationError):
            worker()

    assert seen == [refs, refs, refs]


# ── С12 — the trees' live read is NOT touched by this stage ────────────────

def test_c12_read_record_live_pose_still_uses_the_cluster_search(monkeypatch):
    """С12: read_record_live_pose (a tree node's per-node live read) must keep
    passing NO refs — its behaviour is deliberately unchanged by this stage (Р9).
    A spy on _live_cluster_frame sees role_to_ref=None."""
    seen = []

    def spy(adapter, cell, cluster, sheet, sheet_names, role_to_ref=None):
        seen.append(role_to_ref)
        raise ValidationError("spy: stop before the read")

    monkeypatch.setattr(live_position, "_live_cluster_frame", spy)
    cfg = SimpleNamespace(cells={"fpga_pwr_bank": _spoke_cell()})
    record = SimpleNamespace(kind="placement", obj=SimpleNamespace(
        name="node1", cell="fpga_pwr_bank", cluster=CLUSTER, sheet="FP"))

    with pytest.raises(ValidationError):
        live_position.read_record_live_pose(None, cfg, "node1", record, {})

    assert seen == [None]


# ── Т1.7: the Source tab's button, field and refusals (С11/С13/С14/С15) ─────
#
# The view-level half of the identification: the button dispatches a worker
# (board reads off the UI thread), the PURE identification runs in the finish
# handler, and the hand-typed refs go through the same rules against the snapshot
# the GUI already has.

def _cell_config(tmp_path, name="fpga_pwr_bank",
                 roles=(BULK, BYPASS), filename="root.sexp"):
    """A cells:-only config whose cell has exactly `roles` as its components."""
    root = tmp_path / filename
    root.write_text(dict_to_sexp({"cells": {name: {
        "layer": "F.Cu",
        "components": [{"role": role, "offset_along_mm": 0.0,
                        "offset_across_mm": 0.0} for role in roles],
        "vias": [], "tracks": [], "clone_placements": []}}}), encoding="utf-8")
    return root


def _view_for(main_window, root, name="fpga_pwr_bank"):
    view = CellAnchorView(main_window, connection=main_window.connection)
    view.set_root_path(root)
    view.load_entry(name, root)
    return view


def _record(ref, role, sheet=("FP",), cluster=CLUSTER):
    return SelectionRecord(ref=ref, role=role, cluster=cluster, sheet=sheet)


class _FillAdapter:
    """The board surface read_identification_worker touches."""

    def __init__(self, selected, footprints):
        self._selected = list(selected)
        self._footprints = list(footprints)
        self.calls = []

    def refresh_board(self):
        self.calls.append("refresh")

    def get_selected_items(self):
        self.calls.append("get_selected_items")
        return list(self._selected)

    def get_footprints(self):
        self.calls.append("get_footprints")
        return list(self._footprints)

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return fp.role
        if name == CLUSTER_FIELD_NAME:
            return fp.cluster
        return None


def _fp_pair():
    """Two live footprints of the spoke bank, as the board returns them."""
    return [_fp("C69", BULK, CLUSTER, 100.0, 200.0),
            _fp("C53", BYPASS, CLUSTER, 110.0, 196.0)]


def test_c11_a_busy_socket_refuses_the_fill_before_any_read(main_window,
                                                            tmp_path, caplog):
    """С11/М11: П3.2 — while another owner holds the shared socket the op does
    NOT start (no second request on the same REQ socket), it only says so."""
    root = _cell_config(tmp_path)
    main_window.connection.board = SimpleNamespace(
        adapter=_FillAdapter(_fp_pair(), _fp_pair()))
    main_window.connection.long_op_active = True
    try:
        view = _view_for(main_window, root)
        caplog.clear()

        view._on_fill_from_selection()

        assert view._active_op is None
        assert "busy" in caplog.text
    finally:
        main_window.connection.long_op_active = False


def test_c13_finish_remembers_the_refs_of_a_spoke(main_window, tmp_path, caplog):
    """С13: a successful identification is remembered — the refs AND the context
    — and the Source tab shows BOTH (acceptance: "Refs: C53, C69" after a pair
    is filled)."""
    root = _cell_config(tmp_path)
    view = _view_for(main_window, root)
    cfg = SimpleNamespace(cells={"fpga_pwr_bank": _spoke_cell()},
                          entities=[_entity(CLUSTER, "FP")])
    # The CLUSTER holds two pairs (that is what makes it a spoke); the user
    # identified ONE of them.
    selected = [_record("C69", BULK), _record("C53", BYPASS)]
    caplog.clear()

    view._finish_fill_from_selection(
        {"selected": selected, "members": _spoke_snapshot(), "sheet_names": {}},
        cfg)

    assert remembered_cell_refs(root, "fpga_pwr_bank") == \
        {BULK: "C69", BYPASS: "C53"}
    assert view._cluster_combo.currentText() == CLUSTER
    assert view._sheet_combo.currentText() == "FP"
    assert view._refs_edit.text() == "C69, C53"      # cell's role order
    assert "SPOKE" in caplog.text


def test_c13_finish_remembers_the_refs_of_an_ordinary_cluster(main_window,
                                                              tmp_path):
    """С13/М12: an ORDINARY cluster remembers its refs too — the refs are not a
    spoke-only feature (the same path serves both kinds)."""
    root = _cell_config(tmp_path, name="pif_3v3_vdd", roles=("R1", "R2"))
    view = _view_for(main_window, root, name="pif_3v3_vdd")
    cfg = SimpleNamespace(
        cells={"pif_3v3_vdd": _roles_cell("R1", "R2", name="pif_3v3_vdd")},
        entities=[_entity("PIF", "FP")])
    members = [_record("A", "R1", cluster="PIF"), _record("B", "R2", cluster="PIF")]
    selected = [members[0]]                    # the user clicked only one of two

    view._finish_fill_from_selection(
        {"selected": selected, "members": members, "sheet_names": {}}, cfg)

    assert remembered_cell_refs(root, "pif_3v3_vdd") == {"R1": "A", "R2": "B"}
    assert view._refs_edit.text() == "A, B"


def test_c15_partial_selection_is_reported_as_k_of_n(main_window, tmp_path,
                                                     caplog):
    """С15/М14: identifying an ordinary cluster from PART of it works and says
    how much was clicked — "Refresh geometry from selection" will need all of
    them, and the user must not discover that from a fatal."""
    root = _cell_config(tmp_path, name="pif_3v3_vdd", roles=("R1", "R2"))
    view = _view_for(main_window, root, name="pif_3v3_vdd")
    cfg = SimpleNamespace(
        cells={"pif_3v3_vdd": _roles_cell("R1", "R2", name="pif_3v3_vdd")},
        entities=[_entity("PIF", "FP")])
    members = [_record("A", "R1", cluster="PIF"), _record("B", "R2", cluster="PIF")]
    caplog.clear()

    view._finish_fill_from_selection(
        {"selected": [members[0]], "members": members, "sheet_names": {}}, cfg)

    assert "1 of 2 components" in caplog.text
    assert view._cluster_combo.currentText() == "PIF"


def _spoke_snapshot():
    """TWO pairs of one bank — the real shape of a spoke cluster (the same role
    twice is what MAKES it a spoke), which is what the hand-typed refs path reads
    out of the board snapshot."""
    return [_record("C69", BULK), _record("C53", BYPASS),
            _record("C68", BULK), _record("C52", BYPASS)]


def test_c14_a_hand_typed_incomplete_pair_is_refused_and_not_remembered(
        main_window, tmp_path, caplog):
    """С14/М13: the Refs field is editable by hand, and a hand-typed set goes
    through the SAME rules — one bulk cap of a spoke pair is refused by name and
    NOTHING is remembered (mutation M13, storing without checking, must fail)."""
    root = _cell_config(tmp_path)
    view = _view_for(main_window, root)
    view.refresh_known_roles(_spoke_snapshot())
    view._refs_edit.setText("C69")
    caplog.clear()

    view._on_refs_edited()

    assert "exactly one component per cell role" in caplog.text
    assert remembered_cell_refs(root, "fpga_pwr_bank") is None


def test_c14_a_hand_typed_pair_is_parsed_and_remembered(main_window, tmp_path):
    """The happy half of the field: "C69, C53" (or spaces) identifies the pair
    from the snapshot WITHOUT touching the board."""
    root = _cell_config(tmp_path)
    view = _view_for(main_window, root)
    view.refresh_known_roles(_spoke_snapshot())
    view._refs_edit.setText("C69 C53")

    view._on_refs_edited()

    assert remembered_cell_refs(root, "fpga_pwr_bank") == \
        {BULK: "C69", BYPASS: "C53"}


def test_c14_a_ref_not_in_the_snapshot_is_refused(main_window, tmp_path, caplog):
    """A typo (or a refdes added in KiCad since the last read) is refused with
    the ref named — never silently dropped, which would pin a half pair."""
    root = _cell_config(tmp_path)
    view = _view_for(main_window, root)
    view.refresh_known_roles(_spoke_snapshot())
    view._refs_edit.setText("C69, C999")
    caplog.clear()

    view._on_refs_edited()

    assert "C999" in caplog.text
    assert remembered_cell_refs(root, "fpga_pwr_bank") is None


def test_the_refs_field_survives_reopening_the_editor(main_window, tmp_path):
    """Acceptance item 7: the refs are REMEMBERED, so closing and reopening the
    cell shows the same pair (the field is a view of the state, never its own
    copy)."""
    root = _cell_config(tmp_path)
    view = _view_for(main_window, root)
    view.refresh_known_roles(_spoke_snapshot())
    view._refs_edit.setText("C69, C53")
    view._on_refs_edited()

    reopened = _view_for(main_window, root)

    assert reopened._refs_edit.text() == "C69, C53"
    assert BULK in reopened._refs_edit.toolTip()


def test_a_manual_cluster_pick_erases_the_refs_field(main_window, tmp_path):
    """The other side of С8 at the UI level: picking a Cluster by hand erases the
    remembered refs, and the field must show that (an empty field), or the user
    would keep looking at a pair that is no longer identified."""
    root = _cell_config(tmp_path)
    view = _view_for(main_window, root)
    view.refresh_known_roles(_spoke_snapshot())
    view._refs_edit.setText("C69, C53")
    view._on_refs_edited()
    assert remembered_cell_refs(root, "fpga_pwr_bank")

    # A GENUINE change of the working context (another cluster, not a re-set of
    # the same text, which would be a no-op and rightly leave the refs alone).
    view._cluster_combo.setCurrentText("ANOTHER_BANK")

    assert remembered_cell_refs(root, "fpga_pwr_bank") is None
    assert view._refs_edit.text() == ""


def test_c12_tree_from_selection_never_carries_the_refs():
    """The static half of С12: gui/docks/tree_from_selection.py must not grow a
    role_to_ref anywhere — neither now nor by a future copy-paste of the anchor
    page's plumbing (the tree places a NODE, it never edits an identified pair).
    Read as source text, like the project's other watchdogs."""
    source = (Path(__file__).resolve().parents[2]
              / "gui" / "docks" / "tree_from_selection.py").read_text(
                  encoding="utf-8")

    assert "role_to_ref" not in source


# ── С18 — rereading a cell stays selection-only (Р7а) ─────────────────────
#
# "Update cell from selection" / "Import vias/tracks from selection" rebuild the
# cell from the CURRENT selection. That is deliberate and this stage must not
# change it: a reread driven by the remembered refs would silently rewrite a
# spoke cell from a pair the user is not looking at any more.

def _method_source(text: str, name: str) -> str:
    """The body of a dock method, by the next method at the same indent (the
    project's other source watchdogs read text too)."""
    start = text.index(f"    def {name}(")
    rest = text[start + 1:]
    match = re.search(r"\n    def ", rest)
    return rest[:match.start()] if match else rest


def test_c18_the_reread_flows_never_touch_the_identification():
    """С18/М17: the geometry-refresh engine and the two reread flows of the cell
    dialog must stay free of the identification — no import of gui.
    cell_edit_context in the engine, and no `refs` anywhere in the flows."""
    root = Path(__file__).resolve().parents[2]
    refresh = (root / "kicadstamp" / "cell_geometry_refresh.py").read_text(
        encoding="utf-8")
    # NOT the plain `role_to_ref`: that file has always had its own LOCAL
    # role -> ref map for matching the current selection. What must never appear
    # is the IDENTIFICATION — the remembered refs read out of gui_state.json.
    assert "cell_edit_context" not in refresh
    assert "remembered_cell_refs" not in refresh

    editor = (root / "gui" / "docks" / "cell_editor.py").read_text(
        encoding="utf-8")
    for method in ("_on_refresh_geometry", "_run_refresh_geometry",
                   "_on_import_vias_tracks", "_run_import_vias_tracks"):
        assert "refs" not in _method_source(editor, method), method


# ── С6а–С8а: the WIRING of the remembered refs (2026-09-17, stage 1а) ──────
#
# Stage 1's guards proved the LOGIC of the refs path (С1–С18) and every worker's
# own signature (С9), but three links between them were held by nothing at all:
# the page's dispatch, the Cell dialog's button payload, and the refusal an
# unusable map meets before it reaches the reference slot. Three mutations of the
# Claude reconciliation run survived on 3bd9c6a for exactly that reason (C3, D2,
# A4), so these guards are written against THOSE mutations: they are green on the
# code as it stands and must die when the wiring is broken.

def test_c6a_dispatch_hands_the_remembered_refs_to_the_worker(
        main_window, tmp_path, monkeypatch):
    """С6а/C3: the anchor page's own `_dispatch` must carry the identified refs to
    the overlay worker. С9 pins the three worker functions, but nothing pinned the
    CALL: with `None` in place of the refs (mutation C3) every overlay falls back
    to the cluster search — i.e. draws nothing at all on a spoke — and no test
    noticed."""
    root = _cell_config(tmp_path)
    remember_cell_instance(root, "fpga_pwr_bank", Identification(
        cluster=CLUSTER, sheet=None,
        role_to_ref={BULK: "C69", BYPASS: "C53"}, kind="spoke"))
    main_window.connection.board = SimpleNamespace(adapter=SimpleNamespace())
    view = _view_for(main_window, root)
    assert view._cluster_combo.currentText() == CLUSTER

    started = []
    monkeypatch.setattr(view_mod, "start_long_op",
                        lambda *args, **kwargs: started.append(args) or object())

    view._on_show_bbox()

    assert started, "the bbox worker must be dispatched"
    assert started[0][-1] == {BULK: "C69", BYPASS: "C53"}, \
        "the identified refs must travel as the LAST positional argument"


def test_c7a_the_select_cluster_button_payload_carries_the_remembered_refs(
        main_window, tmp_path, monkeypatch):
    """С7а/D2: the Cell dialog's "Select cluster of this cell on the board" reads
    the identified refs out of the state and puts them into the worker payload.
    С10/С16 cover the worker, but they BUILD the payload by hand — so a payload
    built with an empty map (mutation D2) went unnoticed and the button silently
    went back to selecting the whole spoke cluster (50 components)."""
    root = _cell_config(tmp_path)
    remember_cell_instance(root, "fpga_pwr_bank", Identification(
        cluster=CLUSTER, sheet=None,
        role_to_ref={BULK: "C69", BYPASS: "C53"}, kind="spoke"))
    main_window.connection.board = SimpleNamespace(adapter=SimpleNamespace())
    dock = CellDock(main_window)
    dock.set_root_path(root)
    dock.load_entry("fpga_pwr_bank", root)

    started = []
    monkeypatch.setattr(cell_editor_mod, "start_long_op",
                        lambda *args, **kwargs: started.append(args) or object())

    dock._on_select_cluster_on_board()

    assert started, "the selection worker must be dispatched"
    payload = started[0][-1]
    assert payload["refs"] == {BULK: "C69", BYPASS: "C53"}
    assert payload["cluster"] == CLUSTER


def test_c8a_refs_of_another_cell_are_refused_as_stale_not_as_a_crash():
    """С8а/A4: a remembered map that shares NO role with this cell must come back
    as the honest "stale" refusal — never as an AttributeError from an empty map
    reaching the reference slot. Mutation A4 removed that refusal and the crash
    stayed invisible: no test ever asked for a map that resolves nothing."""
    adapter = _SpokeAdapter(pairs=2)

    with pytest.raises(ValidationError) as ei:
        live_position._live_cluster_frame(
            adapter, _spoke_cell(), CLUSTER, "", {},
            {"C_MCU_BULK": "C69"})       # no role of this cell

    message = str(ei.value)
    assert "stale" in message
    assert "identify the instance again" in message
