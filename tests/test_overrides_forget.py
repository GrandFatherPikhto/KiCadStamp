# tests/test_overrides_forget.py
"""Т6 (plan_2026_09_18_field_overrides_store): when a stored value has done its
job — and the explicit "forget" for the "передумал" case.

Guard С5: the record goes away when the SCHEMATIC and the record AGREE. The board
is not part of that test (М5: dropping on board agreement) — and that is not a
detail, it is the whole rule:

  * the board is rewritten by F8, so "the board happens to agree" is a passing
    state, not a decision. Dropping the note there would lose the intended value
    at the next F8 — exactly the thing the store exists to prevent;
  * while the note lives, OUR value outranks EVERYTHING (plan §0) — including a
    later schematic edit. So once the schematic itself carries our value, keeping
    the note turns it from a reminder into a veto over the schematic.

The rule therefore reads the schematic — the durable side — and this module is
where that reading lives, in two shapes: from read components (the GUI already
has them) and straight from the .kicad_sch tree (the CLI, which must not import
gui/).
"""
from types import SimpleNamespace

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.field_overrides import FieldOverrides, SOURCE_CELL_TABLE
from kicadstamp.overrides_forget import (forget_records, redundant_records,
                                         values_by_uuid,
                                         values_by_uuid_from_sheet)
from kicadstamp.utils.paths import overrides_path_for_config
from tests.fieldstool_fixtures import sch_file, symbol_block

UUID_A = "uuid-A"
UUID_B = "uuid-B"


def _store(tmp_path, *records):
    profile = tmp_path / "prof.json"
    profile.write_text("{}", encoding="utf-8")
    store = FieldOverrides(overrides_path_for_config(str(profile)))
    for symbol_uuid, ref, field, value in records:
        store.set(symbol_uuid, ref, field, value, SOURCE_CELL_TABLE)
    store.save()
    return store


def _component(ref, role, cluster, uuids, divergent=False):
    """The GUI's shape (gui/schema_model.SchematicComponent) — duck-typed on
    purpose: this module never imports gui/."""
    return SimpleNamespace(ref=ref, role=role, cluster=cluster,
                           divergent=divergent, symbol_uuids=tuple(uuids))


# ── the rule (С5) ──────────────────────────────────────────────────────────

def test_c5_a_record_the_schematic_already_carries_is_redundant():
    """The applied case: `overrides-apply --to schematic` (or an Apply in the
    GUI) put our value in the schematic — the note has done its job."""
    records = [SimpleNamespace(symbol_uuid=UUID_A, ref="R1", field=ROLE_FIELD_NAME,
                               value="OUR_ROLE")]

    redundant = redundant_records(records, {UUID_A: {ROLE_FIELD_NAME: "OUR_ROLE"}})

    assert redundant == records


def test_c5_a_record_that_disagrees_with_the_schematic_stays():
    """The still-pending case: our value has NOT reached the schematic, so the
    note is the only thing keeping it in force."""
    records = [SimpleNamespace(symbol_uuid=UUID_A, ref="R1", field=ROLE_FIELD_NAME,
                               value="OUR_ROLE")]

    assert redundant_records(records,
                             {UUID_A: {ROLE_FIELD_NAME: "SOMETHING_ELSE"}}) == []
    # ... and a component the schematic does not carry AT ALL keeps it too.
    assert redundant_records(records, {}) == []


def test_c5_the_board_is_not_part_of_the_test():
    """М5, stated as a positive: the ONLY input is the schematic's own values, so
    a board that agrees (or disagrees) cannot change the answer. The caller has no
    way to pass a board value in — that is what makes the mutation impossible."""
    records = [SimpleNamespace(symbol_uuid=UUID_A, ref="R1", field=ROLE_FIELD_NAME,
                               value="BOARD_AND_OURS")]

    # The schematic says something else entirely: nothing is forgotten, even
    # though the value in the record is exactly what the board carries.
    assert redundant_records(records,
                             {UUID_A: {ROLE_FIELD_NAME: "SCHEMATIC_ONLY"}}) == []


def test_c5_an_empty_value_is_a_real_value_and_agreement_counts():
    """An empty stored value is legitimate (В4а: "this component has no Role") —
    so an empty schematic value AGREES with it, and the note goes."""
    records = [SimpleNamespace(symbol_uuid=UUID_A, ref="R1", field=ROLE_FIELD_NAME,
                               value="")]

    assert redundant_records(records, {UUID_A: {ROLE_FIELD_NAME: ""}}) == records


def test_c5_only_the_agreeing_field_of_a_component_goes():
    """Role and Cluster are decided one by one: a component whose Role reached the
    schematic and whose Cluster did not keeps exactly one of the two notes."""
    role = SimpleNamespace(symbol_uuid=UUID_A, ref="R1", field=ROLE_FIELD_NAME,
                           value="OUR_ROLE")
    cluster = SimpleNamespace(symbol_uuid=UUID_A, ref="R1", field=CLUSTER_FIELD_NAME,
                              value="OUR_CLUSTER")

    redundant = redundant_records([role, cluster],
                                  {UUID_A: {ROLE_FIELD_NAME: "OUR_ROLE"}})

    assert redundant == [role]


# ── reading the schematic: the GUI's shape ────────────────────────────────

def test_c5_values_are_keyed_by_symbol_uuid_not_by_refdes():
    """The store is keyed by symbol uuid, so the comparison has to be too —
    otherwise a re-annotated component would look like it agreed when it didn't."""
    components = [_component("R7", "C_BULK", "bank", [UUID_A])]

    assert values_by_uuid(components) == {
        UUID_A: {ROLE_FIELD_NAME: "C_BULK", CLUSTER_FIELD_NAME: "bank"}}


def test_c5_a_divergent_component_is_left_out_entirely():
    """A multi-unit symbol whose units disagree INSIDE the schematic: "the
    schematic says X" would be a guess, and a guess must never drop a note."""
    components = [_component("R1", "A", "C", [UUID_A], divergent=True)]

    assert values_by_uuid(components) == {}


def test_c5_all_uuids_of_a_component_get_its_values():
    """A multi-instance component's refdes sits in several blocks sharing one
    value; the store may hold a note under ANY of those uuids."""
    components = [_component("R1", "A", "C", [UUID_A, UUID_B])]

    assert set(values_by_uuid(components)) == {UUID_A, UUID_B}


# ── reading the schematic: straight from the sheet (the CLI's half) ───────

def test_c5_the_sheet_reader_reads_the_same_spans(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role="C_BULK", cluster="bank",
                                          symbol_uuid=UUID_A)), encoding="utf-8")

    assert values_by_uuid_from_sheet(str(root)) == {
        UUID_A: {ROLE_FIELD_NAME: "C_BULK", CLUSTER_FIELD_NAME: "bank"}}


def test_c5_the_sheet_reader_drops_an_ambiguous_uuid(tmp_path):
    """Two blocks carrying the SAME uuid with different Roles — the file is
    broken, and a broken file must not authorise a forget. (The CLI's own splice
    planner fatals on the refdes/block conflicts it can see; this reader only has
    to refuse to guess.)"""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(
        symbol_block(["R1"], role="A", symbol_uuid=UUID_A),
        symbol_block(["R1"], role="B", symbol_uuid=UUID_A)), encoding="utf-8")

    assert values_by_uuid_from_sheet(str(root)) == {}


def test_c5_the_sheet_reader_keeps_a_block_without_a_uuid_out(tmp_path):
    """No top-level uuid — nothing the store can be keyed by; the reader simply
    has no entry for it (the store is uuid-keyed, so nothing is lost)."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role="A")), encoding="utf-8")

    assert values_by_uuid_from_sheet(str(root)) == {}


# ── forgetting ─────────────────────────────────────────────────────────────

def test_forget_records_drops_them_and_saves_the_file(tmp_path):
    store = _store(tmp_path,
                   (UUID_A, "R1", ROLE_FIELD_NAME, "X"),
                   (UUID_B, "R2", ROLE_FIELD_NAME, "Y"))
    doomed = [r for r in store.records() if r.symbol_uuid == UUID_A]

    assert forget_records(store, doomed) == 1

    from kicadstamp.field_overrides import load_field_overrides
    on_disk = load_field_overrides(str(store.path))
    assert [(r.symbol_uuid, r.value) for r in on_disk.records()] == [(UUID_B, "Y")]


def test_forget_records_with_save_false_touches_nothing(tmp_path):
    """The dry-run half: the in-memory table may be pruned for the report, but
    the FILE keeps everything (that is what a preflight has to mean)."""
    store = _store(tmp_path, (UUID_A, "R1", ROLE_FIELD_NAME, "X"))
    before = store.path.read_bytes()
    doomed = store.records()

    forget_records(store, doomed, save=False)

    assert store.path.read_bytes() == before
