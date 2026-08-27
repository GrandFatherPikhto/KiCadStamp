# tests/gui/test_pending_dock.py
from gui.docks.pending import (PendingChangesDock, PendingEdit,
                                compute_pending_edits, edits_to_fields_cfg)
from gui.schema_model import SchematicComponent, SchematicInstance
from kicadstamp.explore import Selected


class _FakeUuid:
    def __init__(self, value):
        self.value = value


class _FakePath:
    def __init__(self, uuids):
        self.path = [_FakeUuid(u) for u in uuids]


class _FakeFp:
    """Minimal stand-in for a kipy FootprintInstance: exposes
    fp.sheet_path.path (a list of uuids) so compute_pending_edits' identity
    and full-path checks can read a board symbol uuid / full chain without a
    live KiCad."""
    def __init__(self, uuids):
        self.sheet_path = _FakePath(uuids)


def _component(ref, role, cluster, divergent=False, symbol_uuids=()):
    return SchematicComponent(ref=ref, role=role, cluster=cluster, file="x.kicad_sch",
                              block_start=0, divergent=divergent, symbol_uuids=symbol_uuids)


def _selected(ref, role, cluster, last=None, path=None):
    """last: single symbol uuid (identity check); path: full chain (full-path
    join). path wins; last=None and path=None -> no fp at all."""
    uuids = path if path is not None else ([last] if last else None)
    fp = _FakeFp(uuids) if uuids is not None else None
    return Selected(ref=ref, role=role, cluster=cluster, sheet=[], nets={}, fp=fp)


# ── compute_pending_edits — no Qt dependency, testable without a QApplication ──

def test_no_edits_when_board_matches_schematic():
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = [_selected("R1", "ROLE_A", "CL_A")]

    assert compute_pending_edits(components, snapshot) == []


def test_role_diff_detected():
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = [_selected("R1", "ROLE_B", "CL_A")]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Role", "ROLE_A", "ROLE_B")]


def test_cluster_diff_detected():
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = [_selected("R1", "ROLE_A", "CL_B")]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Cluster", "CL_A", "CL_B")]


def test_both_fields_diff_detected():
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = [_selected("R1", "ROLE_B", "CL_B")]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Cluster", "CL_A", "CL_B"),
                      PendingEdit("R1", "Role", "ROLE_A", "ROLE_B")]


def test_erasing_on_the_board_is_a_diff_too():
    """Regression: Clear all blanks Role/Cluster on the live board — that
    must show up as a pending edit (new_value == ''), not be swallowed as
    "nothing changed" just because it's now falsy."""
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = [_selected("R1", None, None)]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Cluster", "CL_A", ""),
                      PendingEdit("R1", "Role", "ROLE_A", "")]


def test_ref_only_on_board_is_ignored():
    """Not yet in the schematic tree this session (or a stale/removed part
    number) — nothing to diff against."""
    components = []
    snapshot = [_selected("R1", "ROLE_A", "CL_A")]

    assert compute_pending_edits(components, snapshot) == []


def test_ref_only_in_schematic_is_ignored():
    """Not currently on the board — nothing to diff against."""
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = []

    assert compute_pending_edits(components, snapshot) == []


def test_edits_sorted_by_ref_then_field():
    components = [_component("R2", "A", "A"), _component("R1", "A", "A")]
    snapshot = [_selected("R2", "B", "B"), _selected("R1", "B", "B")]

    edits = compute_pending_edits(components, snapshot)

    assert [(e.ref, e.field) for e in edits] == [
        ("R1", "Cluster"), ("R1", "Role"), ("R2", "Cluster"), ("R2", "Role")]


def test_edits_to_fields_cfg_groups_by_ref():
    edits = [PendingEdit("R1", "Role", "A", "NEW_A"), PendingEdit("R1", "Cluster", "B", "NEW_B"),
             PendingEdit("R2", "Role", "C", "NEW_C")]

    cfg = edits_to_fields_cfg(edits)

    assert cfg == {"R1": {"Role": "NEW_A", "Cluster": "NEW_B"}, "R2": {"Role": "NEW_C"}}


# ── PendingChangesDock — the Qt wrapper, fed by set_edits() ─────────────────

def test_set_edits_populates_table(qapp, main_window):
    dock = PendingChangesDock(main_window)
    dock.set_edits([PendingEdit("R1", "Role", "OLD", "NEW")])

    assert dock.table.rowCount() == 1
    assert dock.table.item(0, 0).text() == "R1"
    assert dock.table.item(0, 1).text() == "Role"
    assert dock.table.item(0, 2).text() == "OLD"
    assert dock.table.item(0, 3).text() == "NEW"
    assert dock.apply_button.isEnabled()


def test_apply_button_disabled_when_empty(qapp, main_window):
    dock = PendingChangesDock(main_window)
    assert not dock.apply_button.isEnabled()


def test_set_edits_empty_disables_apply_and_clears_table(qapp, main_window):
    dock = PendingChangesDock(main_window)
    dock.set_edits([PendingEdit("R1", "Role", "OLD", "NEW")])

    dock.set_edits([])

    assert dock.table.rowCount() == 0
    assert not dock.apply_button.isEnabled()


def test_apply_button_click_calls_callback(qapp, main_window):
    dock = PendingChangesDock(main_window)
    dock.set_edits([PendingEdit("R1", "Role", "OLD", "NEW")])
    calls = []
    dock.on_apply_clicked = lambda: calls.append(True)

    dock.apply_button.click()

    assert calls == [True]


def test_ensure_fields_button_click_calls_callback(qapp, main_window):
    """Unlike Apply, Ensure fields has nothing to do with the diff table —
    always enabled, always wired straight through to its own callback."""
    dock = PendingChangesDock(main_window)
    calls = []
    dock.on_ensure_fields_clicked = lambda: calls.append(True)

    assert dock.ensure_fields_button.isEnabled()
    dock.ensure_fields_button.click()

    assert calls == [True]


# ── refdes/symbol identity mismatch (2026-08-08) ────────────────────────────

def test_symbol_mismatch_flagged_and_never_applied():
    """Same refdes, DIFFERENT symbol on the two sides (board uuid != schematic
    uuid): surfaced as a mismatched edit, excluded from Apply's config."""
    components = [_component("R1", "ROLE_A", "CL_A", symbol_uuids=("uuid-sch",))]
    snapshot = [_selected("R1", "ROLE_B", "CL_B", last="uuid-board")]

    edits = compute_pending_edits(components, snapshot)

    assert len(edits) == 1
    assert edits[0].ref == "R1"
    assert edits[0].mismatched is True
    assert edits_to_fields_cfg(edits) == {}


def test_symbol_match_edits_normally():
    components = [_component("R1", "ROLE_A", "CL_A", symbol_uuids=("uuid-1",))]
    snapshot = [_selected("R1", "ROLE_B", "CL_A", last="uuid-1")]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Role", "ROLE_A", "ROLE_B")]


def test_mismatch_check_skipped_when_schematic_has_no_uuids():
    """No false positives: a schematic component without uuid info cannot be
    verified, so it diffs normally."""
    components = [_component("R1", "ROLE_A", "CL_A")]  # symbol_uuids=()
    snapshot = [_selected("R1", "ROLE_B", "CL_A", last="uuid-board")]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Role", "ROLE_A", "ROLE_B")]


def test_mismatch_check_skipped_when_board_has_no_fp():
    """fp=None (tests / unavailable handle) means no identity check — the
    refdes join still works as before."""
    components = [_component("R1", "ROLE_A", "CL_A", symbol_uuids=("uuid-sch",))]
    snapshot = [_selected("R1", "ROLE_B", "CL_A", last=None)]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Role", "ROLE_A", "ROLE_B")]


def test_mismatch_and_real_edits_mixed_in_cfg():
    edits = [PendingEdit("R1", "Role", "A", "NEW_A"),
             PendingEdit("R2", "Role", "C", "NEW_C", mismatched=True)]

    cfg = edits_to_fields_cfg(edits)

    assert cfg == {"R1": {"Role": "NEW_A"}}


# ── full-path (per-instance) join via path_index (2026-08-08) ────────────────

def test_path_index_matches_board_to_schematic_instance_by_full_path():
    """Re-annotated board: the board refdes (C610) differs from the schematic's
    (C110) for the same physical instance, but the FULL path matches — the diff
    must compare against the schematic instance and emit ITS refdes (the one
    Apply can write), not the stale board refdes."""
    comps = [_component("C110", "ROLE_A", "CL_A", symbol_uuids=("uuid-1",))]
    path_index = {("inst-A", "uuid-1"):
                  SchematicInstance("C110", "ROLE_A", "CL_A", "x.kicad_sch", 0)}
    snapshot = [_selected("C610", "ROLE_B", "CL_A", path=("inst-A", "uuid-1"))]

    edits = compute_pending_edits(comps, snapshot, path_index)

    assert edits == [PendingEdit("C110", "Role", "ROLE_A", "ROLE_B")]


def test_path_index_unmatched_falls_back_to_refdes_join():
    """A footprint the index doesn't know falls back to the refdes join (with
    its symbol-uuid guard), so nothing regresses when paths don't line up."""
    comps = [_component("R1", "ROLE_A", "CL_A", symbol_uuids=("uuid-1",))]
    path_index = {("other",): SchematicInstance("R9", "ROLE_A", "CL_A", "x.kicad_sch", 0)}
    snapshot = [_selected("R1", "ROLE_B", "CL_A", path=("inst-A", "uuid-1"))]

    edits = compute_pending_edits(comps, snapshot, path_index)

    assert edits == [PendingEdit("R1", "Role", "ROLE_A", "ROLE_B")]


def test_path_index_cluster_diff_also_emitted():
    comps = [_component("C110", "ROLE_A", "CL_A")]
    path_index = {("inst-A", "uuid-1"):
                  SchematicInstance("C110", "ROLE_A", "CL_A", "x.kicad_sch", 0)}
    snapshot = [_selected("C610", "ROLE_A", "CL_B", path=("inst-A", "uuid-1"))]

    edits = compute_pending_edits(comps, snapshot, path_index)

    assert edits == [PendingEdit("C110", "Cluster", "CL_A", "CL_B")]


def test_table_minimum_height_is_explicitly_overridden(qapp, main_window):
    """1, not 0 (handoff pending_dock_min_height / log_dock_min_height_fix2):
    Qt treats an explicit minimumHeight of exactly 0 as "unset" — the same
    sentinel as never calling setMinimumHeight at all — so it silently falls
    back to QTableWidget's much larger minimumSizeHint and changes nothing.
    Verified live: the container's effective layout minimum stays 110px at
    baseline and with 0, drops to 41px with 1. 1 is the smallest value Qt
    actually honors as an explicit override, so the dock (tabified with
    LogDock) can shrink freely."""
    dock = PendingChangesDock(main_window)
    assert dock.table.minimumHeight() == 1


def test_sync_button_enabled_only_when_non_mismatched_edit_exists(qapp, main_window):
    """"Sync from schematic" (2026-08-27) is a separate enablement from
    Apply: only non-mismatched edits have something safe to sync (a
    mismatched row's refdes means a DIFFERENT symbol on the two sides —
    writing there would hit the wrong component)."""
    dock = PendingChangesDock(main_window)
    assert dock.sync_button.isEnabled() is False      # empty list
    assert dock.apply_button.isEnabled() is False

    dock.set_edits([PendingEdit("R1", "Role", "OLD", "NEW", mismatched=True)])
    assert dock.sync_button.isEnabled() is False      # all mismatched
    assert dock.apply_button.isEnabled() is True      # Apply still enabled (it drops mismatched)

    dock.set_edits([PendingEdit("R1", "Role", "OLD", "NEW"),
                    PendingEdit("R2", "Role", "A", "B", mismatched=True)])
    assert dock.sync_button.isEnabled() is True       # at least one ordinary
    assert dock.apply_button.isEnabled() is True
