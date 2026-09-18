# tests/gui/test_pending_three_sided.py
"""Guards for the THREE-sided Pending changes (Т4 of
plan_2026_09_18_field_overrides_store.md).

Pending used to diff TWO sides — the schematic on disk against the live board.
The store adds a THIRD: OUR value, which wins over both. The rule of this tab is
that PRIORITY and DIFF are different jobs:

  * the resolver takes our value (Т2's layer, guarded in
    tests/test_adapter_factory.py);
  * Pending shows all three sides AS THEY ARE — otherwise the user never sees
    that the board has moved on, and Apply would quietly overwrite someone
    else's edit.

Guards pinned here:

  * С4 — "the board moved": our A, board B, schematic C — all three disagree, the
    row stays VISIBLE, and the diff does not decide who wins;
  * С16 — Apply writes OUR value into the schematic, never the board's (and the
    board's when we have no record at all — the pre-store behaviour, unchanged);
  * С17 — that conflict is marked by its OWN field, NOT by `mismatched`:
    `mismatched` means "this refdes is a DIFFERENT symbol over there" and is
    dropped from Apply; ours only says the board moved, so it MUST travel
    through Apply;
  * the "Наше" column is ALWAYS there, empty table and empty store included
    (decided 18.09: a conditional column would need its own guard — "hidden
    exactly when the store is empty, not when there is no conflict" — and it
    raises the question "where did the column go?");
  * the wiring: the store is the PROFILE's, so opening another project re-points
    it (set_root_path is the one place that knows both);
  * golden: with NO store everything is exactly the two-sided result (Т7's cheap
    half in the GUI; the full plan-level golden is С2).
"""
from pathlib import Path

from gui.docks.pending import (PendingChangesDock, PendingEdit,
                               compute_pending_edits, edits_to_fields_cfg,
                               reminder_log_line)
from gui.schema_model import SchematicComponent
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.explore import Selected
from kicadstamp.field_overrides import SOURCE_FIELDSTOOL, FieldOverrides
from kicadstamp.utils.paths import overrides_path_for_config

UUID_A = "aaaa-0001"
UUID_B = "bbbb-0002"


# ── the same minimal fakes tests/gui/test_pending_dock.py uses ─────────────

class _FakeUuid:
    def __init__(self, value):
        self.value = value


class _FakePath:
    def __init__(self, uuids):
        self.path = [_FakeUuid(u) for u in uuids]


class _FakeFp:
    def __init__(self, uuids):
        self.sheet_path = _FakePath(uuids)


def _component(ref, role, cluster, symbol_uuids=()):
    return SchematicComponent(ref=ref, role=role, cluster=cluster,
                              file="x.kicad_sch", block_start=0, divergent=False,
                              symbol_uuids=symbol_uuids)


def _selected(ref, role, cluster, last=None):
    return Selected(ref=ref, role=role, cluster=cluster, role_field_exists=True,
                    cluster_field_exists=True, sheet=[], nets={},
                    fp=_FakeFp([last]) if last else None)


def _store(*records, path=None) -> FieldOverrides:
    """A store built through its own public API — no test can create a shape the
    store itself would refuse."""
    store = FieldOverrides(path)
    for symbol_uuid, field, value in records:
        store.set(symbol_uuid, "R1", field, value, SOURCE_FIELDSTOOL)
    return store


# ── С4: three sides, all different ─────────────────────────────────────────

def test_c4_the_board_moving_away_is_visible_with_all_three_sides():
    """Our A, board B, schematic C: the row exists, and it carries all three
    values — that is the whole point of the third column."""
    components = [_component("R1", "C_SCHEM", None, symbol_uuids=(UUID_A,))]
    snapshot = [_selected("R1", "B_BOARD", None, last=UUID_A)]

    edits = compute_pending_edits(
        components, snapshot, store=_store((UUID_A, ROLE_FIELD_NAME, "A_OURS")))

    assert [(e.ref, e.field) for e in edits] == [("R1", "Role")]
    e = edits[0]
    assert (e.old_value, e.our_value, e.new_value) == ("C_SCHEM", "A_OURS", "B_BOARD")
    assert e.board_moved is True


def test_c4_our_value_alone_is_a_pending_change_too():
    """Schematic and board agree; OUR record does not. Apply would still change
    the schematic (that is what our value wins), so it must be visible."""
    components = [_component("R1", "SAME", None, symbol_uuids=(UUID_A,))]
    snapshot = [_selected("R1", "SAME", None, last=UUID_A)]

    edits = compute_pending_edits(
        components, snapshot, store=_store((UUID_A, ROLE_FIELD_NAME, "OURS")))

    assert len(edits) == 1
    e = edits[0]
    assert (e.old_value, e.new_value, e.our_value) == ("SAME", "SAME", "OURS")
    assert e.board_moved is False        # nothing moved: only we disagree
    assert edits_to_fields_cfg(edits) == {"R1": {"Role": "OURS"}}


def test_c4_a_stored_cluster_is_carried_side_by_side_with_a_stored_role():
    components = [_component("R1", "A", "CA", symbol_uuids=(UUID_A,))]
    snapshot = [_selected("R1", "B", "CB", last=UUID_A)]

    edits = compute_pending_edits(components, snapshot, store=_store(
        (UUID_A, ROLE_FIELD_NAME, "OUR_ROLE"),
        (UUID_A, CLUSTER_FIELD_NAME, "OUR_CLUSTER")))

    assert [(e.field, e.our_value) for e in edits] == [
        ("Cluster", "OUR_CLUSTER"), ("Role", "OUR_ROLE")]


# ── С16: Apply writes OUR value into the schematic ─────────────────────────

def test_c16_apply_writes_our_value_not_the_board_value():
    components = [_component("R1", "C_SCHEM", None, symbol_uuids=(UUID_A,))]
    snapshot = [_selected("R1", "B_BOARD", None, last=UUID_A)]

    edits = compute_pending_edits(
        components, snapshot, store=_store((UUID_A, ROLE_FIELD_NAME, "A_OURS")))

    assert edits_to_fields_cfg(edits) == {"R1": {"Role": "A_OURS"}}


def test_c16_without_a_record_the_board_value_is_written_as_always():
    components = [_component("R1", "C_SCHEM", None, symbol_uuids=(UUID_A,))]
    snapshot = [_selected("R1", "B_BOARD", None, last=UUID_A)]

    edits = compute_pending_edits(
        components, snapshot, store=_store((UUID_B, ROLE_FIELD_NAME, "ANOTHER_REF")))

    assert edits == [PendingEdit("R1", "Role", "C_SCHEM", "B_BOARD")]
    assert edits_to_fields_cfg(edits) == {"R1": {"Role": "B_BOARD"}}


# ── С17: the conflict marker is its OWN field ──────────────────────────────

def test_c17_the_conflict_marker_is_not_mismatched():
    components = [_component("R1", "C_SCHEM", None, symbol_uuids=(UUID_A,))]
    snapshot = [_selected("R1", "B_BOARD", None, last=UUID_A)]

    e = compute_pending_edits(
        components, snapshot, store=_store((UUID_A, ROLE_FIELD_NAME, "A_OURS")))[0]

    assert e.board_moved is True
    assert e.mismatched is False


def test_c17_the_conflict_row_survives_apply_while_a_mismatch_row_does_not():
    """The two markers mean different things and must not be reused: a mismatch
    is dropped (writing there hits the WRONG symbol), a moved board travels.

    The conflict row is built by compute_pending_edits itself, on purpose: a
    hand-made PendingEdit would only show what edits_to_fields_cfg does with the
    flags, and would NOT catch the production path marking that row `mismatched`
    (that is exactly the mutation this guard exists for)."""
    components = [_component("R1", "C_SCHEM", None, symbol_uuids=(UUID_A,))]
    snapshot = [_selected("R1", "B_BOARD", None, last=UUID_A)]
    conflict = compute_pending_edits(
        components, snapshot, store=_store((UUID_A, ROLE_FIELD_NAME, "A_OURS")))
    mismatch = [PendingEdit("R2", "Refdes/symbol mismatch", "sch: u1", "board: u2",
                            mismatched=True)]

    assert conflict[0].board_moved is True
    assert edits_to_fields_cfg(conflict + mismatch) == {"R1": {"Role": "A_OURS"}}


# ── golden: no store, no change ────────────────────────────────────────────

def test_without_a_store_everything_is_the_two_sided_result():
    components = [_component("R1", "A", "CA", symbol_uuids=(UUID_A,)),
                  _component("R2", "B", "CB", symbol_uuids=(UUID_B,))]
    snapshot = [_selected("R1", "B", "CA", last=UUID_A),
                _selected("R2", "B", "CB", last=UUID_B)]

    before = compute_pending_edits(components, snapshot)
    empty = compute_pending_edits(components, snapshot, store=FieldOverrides())

    assert before == empty
    assert all(e.our_value is None and e.board_moved is False for e in empty)
    assert edits_to_fields_cfg(empty) == {"R1": {"Role": "B"}}


# ── the dock: the Наше column is ALWAYS there ──────────────────────────────

def test_the_ours_column_is_always_there(qapp, main_window):
    dock = PendingChangesDock(main_window)
    headers = [dock.table.horizontalHeaderItem(i).text()
               for i in range(dock.table.columnCount())]

    assert headers == ["Ref", "Field", "Schematic", "Ours", "Board"]
    assert dock.table.isColumnHidden(3) is False     # not hidden when empty
    dock.set_edits([])
    assert dock.table.isColumnHidden(3) is False     # and not after an empty diff


def test_set_edits_shows_our_value_in_its_own_column(qapp, main_window):
    dock = PendingChangesDock(main_window)

    dock.set_edits([PendingEdit("R1", "Role", "C", "B", our_value="A")])

    assert [dock.table.item(0, c).text() for c in range(5)] == [
        "R1", "Role", "C", "A", "B"]


def test_a_row_without_a_record_leaves_the_ours_cell_empty(qapp, main_window):
    dock = PendingChangesDock(main_window)

    dock.set_edits([PendingEdit("R1", "Role", "C", "B")])

    assert dock.table.item(0, 3).text() == ""


def test_the_conflict_row_is_marked_apart_but_still_appliable(qapp, main_window):
    """Visually distinct from a plain row AND from a mismatch row (which is the
    red "Apply will not carry this" colour), and Apply stays enabled."""
    conflict = PendingChangesDock(main_window)
    conflict.set_edits([PendingEdit("R1", "Role", "C", "B", our_value="A",
                                    board_moved=True)])
    plain = PendingChangesDock(main_window)
    plain.set_edits([PendingEdit("R1", "Role", "C", "B")])
    mismatch = PendingChangesDock(main_window)
    mismatch.set_edits([PendingEdit("R1", "Refdes/symbol mismatch", "s", "b",
                                    mismatched=True)])

    conflict_colour = conflict.table.item(0, 0).background().color().name()
    assert conflict_colour != plain.table.item(0, 0).background().color().name()
    assert conflict_colour != mismatch.table.item(0, 0).background().color().name()
    assert "OUR" in conflict.table.item(0, 0).toolTip()
    assert conflict.apply_button.isEnabled()


def test_the_apply_tooltip_states_whose_value_wins(qapp, main_window):
    """The button still says the BOARD values go into the schematic (the
    pre-store truth, unchanged without a record) — plus the exception our store
    introduced."""
    dock = PendingChangesDock(main_window)

    tip = dock.apply_button.toolTip()

    assert "BOARD values into the SCHEMATIC" in tip
    assert "stored value" in tip


def test_the_reminder_no_longer_claims_the_values_are_on_the_board_only():
    """The count now includes rows where ONLY our store differs, so the sentence
    must talk about the schematic changing, not about the board."""
    line = reminder_log_line(0, 2)

    assert "would change in the schematic" in line
    assert "2 " in line
    assert "Pending changes" in line and "(F8)" in line


# ── the wiring: the store is the PROFILE's ─────────────────────────────────

def _profile_with_store(tmp_path, *records):
    from kicadstamp.config.sexp_format import dict_to_sexp
    profile = tmp_path / "prof.sexp"
    profile.write_text(dict_to_sexp({"layer": "B.Cu"}), encoding="utf-8")
    overrides = Path(overrides_path_for_config(str(profile)))
    _store(*records, path=overrides).save()
    return profile


def test_the_profile_switch_hands_the_new_projects_store_to_the_diff(
        qapp, real_main_window, tmp_path):
    """set_root_path is the one place that knows BOTH the project and the window
    (it already resolves root_sheet out of the raw config), so the store rides
    along: a project switch cannot keep the previous project's overrides."""
    profile = _profile_with_store(tmp_path, (UUID_A, ROLE_FIELD_NAME, "OURS"))

    real_main_window.fieldstool_dock.set_root_path(profile)

    window = real_main_window.fieldstool_dock.window
    assert window.overrides is not None
    assert window.overrides.get(UUID_A, ROLE_FIELD_NAME) == "OURS"


def test_no_project_means_no_store(qapp, real_main_window):
    real_main_window.fieldstool_dock.set_root_path(None)

    assert real_main_window.fieldstool_dock.window.overrides is None


def test_a_profile_without_a_store_file_gives_an_empty_store(
        qapp, real_main_window, tmp_path):
    """Absent file = the pre-store world exactly (Т7): the diff then has no third
    side to show, and nothing about it changes."""
    from kicadstamp.config.sexp_format import dict_to_sexp
    profile = tmp_path / "bare.sexp"
    profile.write_text(dict_to_sexp({"layer": "B.Cu"}), encoding="utf-8")

    real_main_window.fieldstool_dock.set_root_path(profile)

    window = real_main_window.fieldstool_dock.window
    assert window.overrides is not None
    assert window.overrides.has_any() is False
