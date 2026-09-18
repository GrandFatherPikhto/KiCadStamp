# tests/gui/test_role_table_model.py
"""Tests for gui/role_table_model.py — the PURE (Qt-free, adapter-free) brain of
the cell editor's "Refs" tab: the rows of the role table, the cluster group fill
and the batch that gets written to the board.

Stage 2 of the spoke work (plan_2026_09_17_spoke_s2_role_table.md, design
design_2026_09_17_spoke_cell_editing.md §3 Р2/Р4/Р5, UI delta
plan/delta_2026_09_17_spoke_s2_refs_tab_ui.md):

  * a row carries what the USER put in the table (role, cluster) and what the
    LAST BOARD READ says (board_role, board_cluster, the field-existence facts);
  * only the differences from the board are ever written, in ONE batch;
  * an emptied Role is "do not touch", never "erase the board value" (erasing
    roles is the Role/Cluster panel's "Clear all", not this table);
  * the cluster is per-row ("Cluster to write") with the group fill
    (apply_cluster_to_all) as the convenience Денис asked for on 2026-09-17;
  * rows whose footprint has no Role/Cluster field are skipped PER FIELD, and a
    ref that left the board snapshot is skipped whole — both named in the Log.

Headless and board-free: this module never touches an adapter, so every guard
here is a plain function call.
"""
from gui.role_table_model import (
    BoardRecord,
    RoleRow,
    add_ref,
    append_rows,
    apply_cluster_to_all,
    build_tag_updates,
    can_write,
    default_cluster,
    is_table_empty,
    records_from_items,
    refresh_board_values,
    remove_rows,
    replace_rows,
    role_choices,
    role_table_warnings,
    rows_from_refs,
    rows_from_state,
    table_to_state,
)


def _rec(ref, role=None, cluster=None, sheet=(), role_exists=True,
         cluster_exists=True):
    """A record as a real board read produces it — symbol uuid included: the
    override store is keyed by it, and `can_write` (Т5) asks for the STORE's
    batch, so a row without one could never be recorded."""
    return BoardRecord(ref=ref, role=role, cluster=cluster, sheet=tuple(sheet),
                       role_field_exists=role_exists,
                       cluster_field_exists=cluster_exists,
                       symbol_uuid=f"uuid-{ref}")


CELL_ROLES = ["C_BULK", "C_BYPASS"]


# ── Building rows from a board read (С2б) ──────────────────────────────────

def test_c2b_rows_from_a_selection_without_roles_come_up_empty():
    """The whole point of the tab (design Р2/Р4): a pair that has just been
    placed and routed has NO roles and NO cluster yet, and the table must still
    show its rows — that is exactly when the user needs it."""
    rows = replace_rows([_rec("C41"), _rec("C42")])

    assert [r.ref for r in rows] == ["C41", "C42"]
    assert [r.role for r in rows] == ["", ""]
    assert [r.cluster for r in rows] == ["", ""]
    assert all(r.on_board for r in rows)
    assert all(r.role_differs is False and r.cluster_differs is False for r in rows)


def test_c2b_rows_from_a_tagged_selection_are_prefilled_from_the_board():
    """A selection that IS tagged (acceptance 2б) opens with the Role and the
    cluster already in the to-write cells — then the write button is inactive,
    because there is nothing left to write."""
    rows = replace_rows([
        _rec("C74", role="C_BULK", cluster="FPGA_PWR_BANK"),
        _rec("C58", role="C_BYPASS", cluster="FPGA_PWR_BANK"),
    ])

    assert [r.role for r in rows] == ["C_BULK", "C_BYPASS"]
    assert [r.cluster for r in rows] == ["FPGA_PWR_BANK", "FPGA_PWR_BANK"]
    assert all(r.role_differs is False for r in rows)
    assert can_write(rows) is False


def test_rows_missing_a_board_field_are_marked_not_writable_for_that_field():
    """Probe 2026-09-17 (design §2.3): H1-H4 have no Role field, R37 has no
    Cluster field. That is a PER-FIELD fact — the row must stay writable for
    the field it does have."""
    rows = replace_rows([_rec("H1", role_exists=False, cluster_exists=False),
                         _rec("R37", cluster_exists=False)])

    assert rows[0].role_field_exists is False
    assert rows[0].cluster_field_exists is False
    assert rows[1].role_field_exists is True
    assert rows[1].cluster_field_exists is False


def test_records_from_items_reads_the_optional_field_flags_with_defaults():
    """The snapshot (explore.Selected) carries the two existence flags; a
    hand-built object without them means "the field is there" (the same default
    explore.Selected itself uses)."""
    class _Item:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    records = records_from_items([
        _Item(ref="C1", role="A", cluster="PIF", sheet=["FPGA"]),
        _Item(ref="H1", role=None, cluster=None, sheet=[],
              role_field_exists=False, cluster_field_exists=False),
    ])

    assert records[0].role_field_exists is True
    assert records[0].cluster_field_exists is True
    assert records[0].sheet == ("FPGA",)
    assert records[1].role_field_exists is False
    assert records[1].cluster_field_exists is False


# ── Append / remove / hand-typed refs (С2в, С2г, С2д) ──────────────────────

def test_c2v_append_keeps_existing_rows_and_never_duplicates_a_ref():
    """"Add selection" appends the NEW components; a ref already in the table
    keeps the row it has — including whatever the user typed into it."""
    rows = replace_rows([_rec("C41")])
    rows[0].role = "C_BULK"                     # the user typed this

    rows = append_rows(rows, [_rec("C41", role="SOMETHING_ELSE"),
                              _rec("C42", role="C_BYPASS")])

    assert [r.ref for r in rows] == ["C41", "C42"]
    assert rows[0].role == "C_BULK"             # the edit survived
    assert rows[1].role == "C_BYPASS"


def test_c2g_hand_typed_ref_is_refused_when_unknown_or_already_present():
    """Typing a ref by hand resolves against the page's snapshot, never the
    board: an unknown ref and a duplicate are both refused with a reason."""
    records = [_rec("C41"), _rec("C42")]
    rows = replace_rows([_rec("C41")])

    same, error = add_ref(rows, "C41", records)
    assert [r.ref for r in same] == ["C41"]
    assert error and "C41" in error

    unknown, error = add_ref(rows, "C99", records)
    assert [r.ref for r in unknown] == ["C41"]
    assert error and "C99" in error


def test_c2g_hand_typed_ref_is_added_with_the_board_values():
    records = [_rec("C41"), _rec("C42", role="C_BYPASS",
                                 cluster="FPGA_PWR_BANK")]
    rows, error = add_ref([], "C42", records)

    assert error is None
    assert [r.ref for r in rows] == ["C42"]
    assert rows[0].role == "C_BYPASS"
    assert rows[0].cluster == "FPGA_PWR_BANK"


def test_c2d_remove_drops_only_the_named_rows():
    rows = replace_rows([_rec("C41"), _rec("C42"), _rec("C43")])
    rows = remove_rows(rows, {"C41", "C43"})

    assert [r.ref for r in rows] == ["C42"]


# ── The write batch (С1, С2, С2а, С3, С4, С15) ─────────────────────────────

def test_c1_each_row_carries_its_own_role_into_the_batch():
    rows = replace_rows([_rec("C41"), _rec("C42")])
    rows[0].role = "C_BULK"
    rows[1].role = "C_BYPASS"

    plan = build_tag_updates(rows)

    assert plan.updates == [("C41", "Role", "C_BULK"),
                            ("C42", "Role", "C_BYPASS")]
    assert plan.skipped == []


def test_c2_an_emptied_role_is_not_written_back():
    """Removing the text from a row whose footprint HAS a Role means "leave it
    alone", never "erase it" — erasing is Role/Cluster "Clear all"."""
    rows = replace_rows([_rec("C41", role="C_BULK")])
    rows[0].role = ""

    plan = build_tag_updates(rows)

    assert plan.updates == []
    assert can_write(rows) is False


def test_c2a_values_equal_to_the_board_are_not_written():
    rows = replace_rows([_rec("C41", role="C_BULK", cluster="FPGA_PWR_BANK"),
                         _rec("C42", role="C_BYPASS", cluster="FPGA_PWR_BANK")])
    rows[1].cluster = "MCU_PWR_BANK"            # only this one differs

    plan = build_tag_updates(rows)

    assert plan.updates == [("C42", "Cluster", "MCU_PWR_BANK")]


def test_c3_a_row_without_a_role_field_is_skipped_with_its_reason():
    rows = replace_rows([_rec("H1", role_exists=False), _rec("C41")])
    rows[1].role = "C_BULK"

    plan = build_tag_updates(rows)

    assert plan.updates == [("C41", "Role", "C_BULK")]
    assert plan.skipped == [("H1", "no_role_field")]


def test_c4_a_missing_cluster_field_does_not_skip_the_role_write():
    """Probe §2.3: R37 has no Cluster field. Writing its Role must still
    happen — the skip is PER FIELD, exactly like RoleClusterTreeDock._run_tag."""
    rows = replace_rows([_rec("R37", cluster_exists=False)])
    rows[0].role = "C_BULK"
    rows[0].cluster = "FPGA_PWR_BANK"           # asked for, but impossible

    plan = build_tag_updates(rows)

    assert plan.updates == [("R37", "Role", "C_BULK")]
    assert plan.skipped == [("R37", "no_cluster_field")]


def test_a_ref_that_left_the_board_snapshot_is_skipped_whole():
    rows = replace_rows([_rec("C41")])
    rows[0].on_board = False
    rows[0].role = "C_BULK"
    rows[0].cluster = "FPGA_PWR_BANK"

    plan = build_tag_updates(rows)

    assert plan.updates == []
    assert plan.skipped == [("C41", "not_on_board")]


def test_c15_the_cluster_comes_from_the_rows_not_from_a_shared_value():
    """Денис 2026-09-17: the table is the truth — two rows may carry two
    different clusters, and the batch follows the rows."""
    rows = replace_rows([_rec("C41"), _rec("C74")])
    rows[0].role, rows[0].cluster = "C_BULK", "FPGA_PWR_BANK"
    rows[1].role, rows[1].cluster = "C_OUT_BULK", "MCU_PWR_BANK"

    plan = build_tag_updates(rows)

    assert ("C41", "Cluster", "FPGA_PWR_BANK") in plan.updates
    assert ("C74", "Cluster", "MCU_PWR_BANK") in plan.updates


# ── Refresh from a later board read (С2з) ──────────────────────────────────

def test_c2z_refresh_takes_the_board_values_and_keeps_the_user_input():
    rows = replace_rows([_rec("C41", role="C_BULK", cluster="FPGA_PWR_BANK")])
    rows[0].role = "C_BYPASS"                   # the user retagged it

    rows = refresh_board_values(rows, [_rec("C41", role="C_NEW_ON_BOARD",
                                            cluster="ANOTHER_BANK")])

    assert rows[0].role == "C_BYPASS"           # untouched by the refresh
    assert rows[0].board_role == "C_NEW_ON_BOARD"
    assert rows[0].board_cluster == "ANOTHER_BANK"
    assert rows[0].cluster == "FPGA_PWR_BANK"   # the user's own value stays


def test_refresh_marks_a_row_that_left_the_snapshot():
    rows = refresh_board_values(replace_rows([_rec("C41")]), [])

    assert rows[0].on_board is False
    assert rows[0].board_role is None


# ── The group fill (С13, С14) ──────────────────────────────────────────────

def test_c13_group_fill_reaches_every_row_with_a_cluster_field():
    """Денис' case: the components have no cluster or disagree — one click puts
    the chosen cluster into every row that can take it, and names the rows that
    cannot."""
    rows = replace_rows([_rec("C41"), _rec("C74", role="C_OUT_BULK"),
                         _rec("H1", role_exists=False, cluster_exists=False)])

    rows, unfilled = apply_cluster_to_all(rows, "FPGA_PWR_BANK")

    assert [r.cluster for r in rows] == ["FPGA_PWR_BANK", "FPGA_PWR_BANK", ""]
    assert unfilled == ["H1"]


def test_c14_group_fill_with_an_empty_value_changes_nothing():
    rows = replace_rows([_rec("C41")])

    rows, unfilled = apply_cluster_to_all(rows, "   ")

    assert [r.cluster for r in rows] == [""]
    assert unfilled == []


# ── The shared cluster field (С2е) ─────────────────────────────────────────

def test_c2e_default_cluster_uses_the_common_value_or_the_source_tab():
    common = replace_rows([_rec("C41", cluster="FPGA_PWR_BANK"),
                           _rec("C58", cluster="FPGA_PWR_BANK")])
    assert default_cluster(common, "SOURCE") == "FPGA_PWR_BANK"

    mixed = replace_rows([_rec("C41", cluster="FPGA_PWR_BANK"),
                          _rec("C74", cluster="MCU_PWR_BANK")])
    assert default_cluster(mixed, "SOURCE") == "SOURCE"

    empty = replace_rows([_rec("C41"), _rec("C42")])
    assert default_cluster(empty, "SOURCE") == "SOURCE"


# ── State round-trip (С2ж, С2з, С2к) ──────────────────────────────────────

def test_c2zh_state_round_trip_keeps_order_refs_roles_and_clusters():
    rows = replace_rows([_rec("C74"), _rec("C58")])
    rows[0].role, rows[0].cluster = "C_BULK", "FPGA_PWR_BANK"
    rows[1].role = ""                           # deliberately left empty

    state = table_to_state(rows, "FPGA_PWR_BANK")
    back, cluster = rows_from_state(state, [_rec("C74"), _rec("C58")])

    assert cluster == "FPGA_PWR_BANK"
    assert [r.ref for r in back] == ["C74", "C58"]
    assert [r.role for r in back] == ["C_BULK", ""]
    assert [r.cluster for r in back] == ["FPGA_PWR_BANK", ""]


def test_c2z_the_state_holds_no_board_values_only_what_the_user_typed():
    rows = replace_rows([_rec("C41", role="BOARD_ROLE",
                              cluster="BOARD_CLUSTER")])
    rows[0].role = "TYPED"

    state = table_to_state(rows, "")

    assert state["rows"] == [{"ref": "C41", "role": "TYPED", "cluster": ""}]
    assert "BOARD_ROLE" not in repr(state)
    assert "BOARD_CLUSTER" not in repr(state)


def test_c2z_a_restored_row_takes_its_board_columns_from_the_snapshot():
    rows = [RoleRow(ref="C41", role="TYPED", cluster="")]
    state = table_to_state(rows, "")

    back, _cluster = rows_from_state(state, [_rec("C41", role="NOW_ON_BOARD",
                                                  cluster="BANK")])

    assert back[0].board_role == "NOW_ON_BOARD"
    assert back[0].board_cluster == "BANK"
    assert back[0].role == "TYPED"


# ── The rule "only the user's own input goes into gui_state.json" (plan 2а Р5)
# Guards С4 and С5. Both behaviours are CORRECT on the base: these tests are the
# mutation guards the plan 2а §1.3 asked for, not a red-first fix.

def test_x3_an_untouched_row_prefilled_from_the_board_keeps_no_board_values():
    """С4 / mutation X3 (plan 2а §1.3). A row taken from the board and NEVER
    touched carries the board's own values in its to-write cells and in its
    board columns — and NONE of them may reach gui_state.json.

    Stored, they would freeze a value that ages: the board is retagged elsewhere
    (fieldstool + F8) and the reopening table would show the OLD role from the
    state, bold (it differs from the board now) and ready to be written back —
    the exact silent revert X3 would bring back."""
    rows = replace_rows([_rec("C41", role="C_BULK", cluster="FPGA_PWR_BANK")])

    state = table_to_state(rows, "")

    assert state["rows"] == [{"ref": "C41", "role": "", "cluster": ""}]
    assert "C_BULK" not in repr(state)
    assert "FPGA_PWR_BANK" not in repr(state)


def test_x3_a_role_changed_on_the_board_comes_back_new_not_stale():
    """С4 / mutation X3 — the live danger of §1.3, end to end: the table was
    never typed into, the role on the board then changed, and the reopened table
    must carry the NEW role with nothing left to write.

    With X3 in place the state holds the old role, it wins over the snapshot, the
    row opens bold and "Write to board" sends the stale role back to the board."""
    rows = replace_rows([_rec("C41", role="OLD_ROLE", cluster="BANK")])
    state = table_to_state(rows, "")            # the user typed nothing at all

    back, _cluster = rows_from_state(
        state, [_rec("C41", role="NEW_ROLE", cluster="BANK")])

    assert back[0].role == "NEW_ROLE"           # the board's own, not the state's
    assert back[0].board_role == "NEW_ROLE"
    assert build_tag_updates(back).updates == []


def test_x10_an_empty_saved_role_and_cluster_are_restored_from_the_snapshot():
    """С5 / mutation X10 (plan 2а §1.3): an empty saved value means "the user
    typed nothing here", so the snapshot fills the cell — for the Role AND the
    cluster (Р2б: the board values are never stored, they are read again).

    With X10 in place the cell comes back empty even though the footprint on the
    board carries both values."""
    state = {"cluster": "",
             "rows": [{"ref": "C41", "role": "", "cluster": ""}]}

    rows, _cluster = rows_from_state(
        state, [_rec("C41", role="C_BULK", cluster="FPGA_PWR_BANK")])

    assert rows[0].role == "C_BULK"
    assert rows[0].cluster == "FPGA_PWR_BANK"
    assert build_tag_updates(rows).updates == []


def test_c2k_an_unknown_ref_in_the_state_is_shown_but_not_writable():
    """A cell remembered under another board: the rows are still displayed (the
    table is a hint), but the write cannot reach them."""
    state = table_to_state([RoleRow(ref="C90", role="R")], "")

    rows, _cluster = rows_from_state(state, [_rec("C41")])

    assert [r.ref for r in rows] == ["C90"]
    assert rows[0].on_board is False
    assert can_write(rows) is False


def test_rows_from_state_tolerates_a_missing_or_malformed_state():
    assert rows_from_state(None, [_rec("C41")]) == ([], "")
    assert rows_from_state({"rows": "nope"}, [_rec("C41")]) == ([], "")


def test_c2k_remembered_refs_are_a_fallback_table():
    """"Fill from selection" remembered a role -> refdes map (stage 1): with no
    saved table the Refs tab opens on exactly that pair (acceptance 7)."""
    rows = rows_from_refs({"C_BULK": "C74", "C_BYPASS": "C58"},
                          [_rec("C74", role="C_BULK", cluster="FPGA_PWR_BANK"),
                           _rec("C58", role="C_BYPASS", cluster="FPGA_PWR_BANK")])

    assert [r.ref for r in rows] == ["C74", "C58"]
    assert [r.role for r in rows] == ["C_BULK", "C_BYPASS"]
    assert [r.cluster for r in rows] == ["FPGA_PWR_BANK", "FPGA_PWR_BANK"]


def test_a_table_of_blank_rows_counts_as_empty():
    assert is_table_empty([]) is True
    assert is_table_empty([RoleRow(ref="")]) is True
    assert is_table_empty([RoleRow(ref="C41")]) is False


# ── Warnings and the write button (С6, С7, С11) ────────────────────────────

def test_c6_a_role_on_two_rows_warns_but_does_not_block():
    rows = replace_rows([_rec("C41"), _rec("C43")])
    rows[0].role = "C_OUT_BULK"
    rows[1].role = "C_OUT_BULK"

    warnings = role_table_warnings(rows, CELL_ROLES)

    assert any("C_OUT_BULK" in w and "C41" in w and "C43" in w for w in warnings)
    assert can_write(rows) is True


def test_c7_a_role_that_is_not_the_cells_is_reported():
    rows = replace_rows([_rec("C41")])
    rows[0].role = "R_NOT_IN_CELL"

    warnings = role_table_warnings(rows, CELL_ROLES)

    assert any("R_NOT_IN_CELL" in w for w in warnings)


def test_an_unassigned_cell_role_is_reported_after_the_write():
    rows = replace_rows([_rec("C41")])
    rows[0].role = "C_BYPASS"                   # C_BULK stays unassigned

    warnings = role_table_warnings(rows, CELL_ROLES)

    assert any("C_BULK" in w for w in warnings)


def test_a_fully_tagged_table_warns_about_nothing():
    rows = replace_rows([_rec("C41"), _rec("C42")])
    rows[0].role = "C_BULK"
    rows[1].role = "C_BYPASS"

    assert role_table_warnings(rows, CELL_ROLES) == []


def test_c11_role_choices_are_the_cells_roles_in_the_cells_order():
    assert role_choices(["C_BYPASS", "C_BULK", "C_BYPASS"]) == ["C_BYPASS",
                                                                "C_BULK"]
    assert role_choices([]) == []
