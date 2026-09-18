# tests/test_field_overrides.py
"""Guards for the Role/Cluster field-overrides store — Т1 of
plan_2026_09_18_field_overrides_store.md (design
design_2026_09_18_field_overrides_store.md).

The store is the SPARSE table of the values a HUMAN typed in KiCadStamp. The
rule it exists for: the effective value of Role/Cluster is OUR value when we
have one, the board's value otherwise — our value wins ALWAYS (design §0). The
file is machine-only json (never a config, never pulled in through `include:`),
keyed by the SYMBOL uuid (`fp.sheet_path.path[-1]`), so a re-annotation (F8) can
never move a value onto a foreign component. `ref` is kept next to the key for
readability only — it is NOT the key.

Guards pinned here (С11-С14 of the plan, plus the two rules they rest on):

  * С11 — a read never springs a file into existence, and this module never
    talks to the board (no adapter, no kipy, no field read): the board
    conversation belongs to the factory/overlay (Т2/Т2а);
  * С12 — a component without an accessible symbol uuid is refused LOUDLY and
    nothing is written: guessing would attach our value to a foreign symbol
    after F8;
  * С13 — the write is atomic and leaves a timestamped backup of the previous
    content (the copper registry writes with a bare `write_text` and no backup
    — that mistake is deliberately not repeated);
  * С14 — a FUTURE schema_version is fatal, not a silent re-parse;
  * В1 — the key is the symbol uuid, not the refdes;
  * В2 — only Role/Cluster can be stored at all, so no `Value`/`Reference`/
    `Datasheet` override can ever reach a foreign tool (BOM) by accident.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import kicadstamp.field_overrides as field_overrides
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.exceptions import ValidationError
from kicadstamp.field_overrides import (FIELD_OVERRIDES_SCHEMA_VERSION,
                                        SOURCE_CELL_TABLE, SOURCE_FIELDSTOOL,
                                        FieldOverrides, load_field_overrides)
from kicadstamp.utils.paths import overrides_path_for_config, registry_path_for_config

UUID_A = "aaaaaaaa-0000-0000-0000-000000000001"
UUID_B = "bbbbbbbb-0000-0000-0000-000000000002"
UUID_ABSENT = "cccccccc-0000-0000-0000-000000000003"


def _store_path(tmp_path: Path) -> Path:
    return tmp_path / "overrides" / "config.fields.json"


# ── the path helper: next to the registries, keyed by the config stem ───────

def test_store_path_mirrors_the_registry_convention():
    cfg = "/tmp/project/config.sexp"
    assert overrides_path_for_config(cfg) == "/tmp/project/overrides/config.fields.json"
    assert Path(registry_path_for_config(cfg)).name == "config.registry.json"


# ── С11: read-only means read-only, and no board here ──────────────────────

def test_c11_reading_a_missing_store_creates_nothing(tmp_path):
    path = _store_path(tmp_path)

    store = load_field_overrides(path)

    assert store.records() == []
    assert store.get(UUID_A, ROLE_FIELD_NAME) is None
    assert not path.exists()
    assert not path.parent.exists()


def _code_tokens(path) -> str:
    """The module's CODE with every string literal and comment dropped — so a
    docstring may legitimately EXPLAIN the seam (`get_field_value`) while a real
    reference to it stays visible. Known limitation, stated on purpose: the
    string form (`getattr(adapter, "get_field_value")`) is invisible here; that
    escape hatch is watched by the door's own watchdog over `adapter._board`."""
    import io
    import tokenize

    src = Path(path).read_text(encoding="utf-8")
    return " ".join(tok.string for tok in
                    tokenize.generate_tokens(io.StringIO(src).readline)
                    if tok.type not in (tokenize.COMMENT, tokenize.STRING))


def test_c11_the_store_module_never_touches_the_board():
    """A token scan, not a mock: whoever adds a board read to the store breaks
    the one place that must stay a plain table. Prose (docstrings/comments) is
    excluded — the module is SUPPOSED to explain where the overlay lives."""
    code = _code_tokens(inspect.getfile(field_overrides))

    for forbidden in ("KiCadBoardAdapter", "IBoardAdapter", "get_field_value", "kipy"):
        assert forbidden not in code, forbidden


# ── С12: no symbol uuid -> refusal, never a guess ──────────────────────────

def test_c12_a_component_without_symbol_uuid_is_refused_loudly(tmp_path):
    path = _store_path(tmp_path)
    store = load_field_overrides(path)

    for bad in ("", None):
        with pytest.raises(ValidationError):
            store.set(bad, "C41", ROLE_FIELD_NAME, "C_BULK", SOURCE_CELL_TABLE)

    assert store.records() == []
    store.save()
    assert not path.exists()


# ── В2: Role/Cluster only, at every entrance ───────────────────────────────

def test_b2_only_role_and_cluster_can_be_stored():
    store = FieldOverrides()

    for field in ("Value", "Reference", "Datasheet", "Footprint", "role", "cluster"):
        with pytest.raises(ValidationError):
            store.set(UUID_A, "R1", field, "x", SOURCE_FIELDSTOOL)

    assert store.records() == []


def test_b2_a_foreign_field_already_in_the_file_is_ignored(tmp_path, caplog):
    """Defence in depth: a hand-edited (or future) file must not smuggle a
    `Value` override into the resolver."""
    path = _store_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": FIELD_OVERRIDES_SCHEMA_VERSION,
        "records": [
            {"symbol_uuid": UUID_A, "ref": "R1", "field": "Value", "value": "10k",
             "source": SOURCE_FIELDSTOOL},
            {"symbol_uuid": UUID_A, "ref": "R1", "field": ROLE_FIELD_NAME,
             "value": "R_IN", "source": SOURCE_FIELDSTOOL},
        ],
    }), encoding="utf-8")

    with caplog.at_level("WARNING"):
        store = load_field_overrides(path)

    assert store.get(UUID_A, ROLE_FIELD_NAME) == "R_IN"
    assert store.get(UUID_A, "Value") is None
    assert len(store.records()) == 1


# ── the round trip: sparse, uuid-keyed, ref kept for readability ───────────

def test_round_trip_is_sparse_and_keeps_ref_for_readability(tmp_path):
    path = _store_path(tmp_path)
    store = load_field_overrides(path)
    store.set(UUID_A, "C41", ROLE_FIELD_NAME, "C_BULK", SOURCE_CELL_TABLE)
    store.set(UUID_B, "C42", CLUSTER_FIELD_NAME, "FPGA_PWR_BANK", SOURCE_FIELDSTOOL)
    store.save()

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == FIELD_OVERRIDES_SCHEMA_VERSION
    assert len(raw["records"]) == 2
    assert {r["symbol_uuid"] for r in raw["records"]} == {UUID_A, UUID_B}
    assert {r["ref"] for r in raw["records"]} == {"C41", "C42"}

    back = load_field_overrides(path)
    assert back.get(UUID_A, ROLE_FIELD_NAME) == "C_BULK"
    assert back.get(UUID_B, CLUSTER_FIELD_NAME) == "FPGA_PWR_BANK"
    # SPARSE (В4а): nothing was recorded for the fields nobody touched.
    assert back.get(UUID_A, CLUSTER_FIELD_NAME) is None
    assert back.get(UUID_B, ROLE_FIELD_NAME) is None
    assert back.get(UUID_ABSENT, ROLE_FIELD_NAME) is None


def test_b1_the_key_is_the_symbol_uuid_not_the_refdes(tmp_path):
    """A re-annotation (F8) keeps the symbol uuid and changes `ref`. The value
    must stay with the COMPONENT, and a different component that took over the
    old refdes must NOT inherit it."""
    path = _store_path(tmp_path)
    store = load_field_overrides(path)
    store.set(UUID_A, "C41", ROLE_FIELD_NAME, "C_BULK", SOURCE_CELL_TABLE)
    store.set(UUID_A, "C99", ROLE_FIELD_NAME, "C_BULK", SOURCE_CELL_TABLE)
    store.set(UUID_B, "C41", ROLE_FIELD_NAME, "OTHER", SOURCE_CELL_TABLE)

    # The IN-MEMORY table is what proves the key: a save/load round trip
    # re-keys a refdes-keyed file correctly (the record still carries its
    # symbol_uuid), so the file alone let a refdes-keyed store survive. Found by
    # mutation М3, 2026-09-18.
    assert store.get(UUID_A, ROLE_FIELD_NAME) == "C_BULK"
    assert store.get(UUID_B, ROLE_FIELD_NAME) == "OTHER"

    store.save()
    back = load_field_overrides(path)

    assert back.get(UUID_A, ROLE_FIELD_NAME) == "C_BULK"
    assert back.get(UUID_B, ROLE_FIELD_NAME) == "OTHER"
    assert [(r.symbol_uuid, r.ref) for r in back.records()] == [
        (UUID_A, "C99"), (UUID_B, "C41")]


def test_saving_an_empty_table_never_creates_a_file(tmp_path):
    path = _store_path(tmp_path)

    load_field_overrides(path).save()

    assert not path.exists()


def test_forgetting_everything_over_an_existing_file_persists_an_empty_table(tmp_path):
    path = _store_path(tmp_path)
    store = load_field_overrides(path)
    store.set(UUID_A, "C41", ROLE_FIELD_NAME, "C_BULK", SOURCE_CELL_TABLE)
    store.save()

    store.forget(UUID_A)
    store.save()

    assert json.loads(path.read_text(encoding="utf-8"))["records"] == []


def test_forget_drops_one_field_or_a_whole_component(tmp_path):
    path = _store_path(tmp_path)
    store = load_field_overrides(path)
    store.set(UUID_A, "C41", ROLE_FIELD_NAME, "C_BULK", SOURCE_CELL_TABLE)
    store.set(UUID_A, "C41", CLUSTER_FIELD_NAME, "BANK", SOURCE_CELL_TABLE)
    store.set(UUID_B, "C42", ROLE_FIELD_NAME, "X", SOURCE_CELL_TABLE)

    assert store.forget(UUID_A, ROLE_FIELD_NAME) == 1
    assert store.get(UUID_A, ROLE_FIELD_NAME) is None
    assert store.get(UUID_A, CLUSTER_FIELD_NAME) == "BANK"

    assert store.forget(UUID_ABSENT) == 0
    assert store.forget(UUID_B) == 1
    assert len(store.records()) == 1

    assert store.forget(UUID_A) == 1
    assert store.records() == []


# ── С13: atomic write + backup of the previous content ─────────────────────

def test_c13_saving_over_an_existing_file_leaves_a_backup(tmp_path):
    path = _store_path(tmp_path)
    store = load_field_overrides(path)
    store.set(UUID_A, "C41", ROLE_FIELD_NAME, "C_BULK", SOURCE_CELL_TABLE)
    store.save()

    store.set(UUID_B, "C42", CLUSTER_FIELD_NAME, "BANK", SOURCE_FIELDSTOOL)
    store.save()

    backups = sorted(path.parent.glob(path.name + ".bak.*"))
    assert len(backups) == 1
    assert [r["ref"] for r in
            json.loads(backups[0].read_text(encoding="utf-8"))["records"]] == ["C41"]

    assert len(json.loads(path.read_text(encoding="utf-8"))["records"]) == 2
    # write_text_atomic's temp file is never left behind
    assert list(path.parent.glob(".*.tmp")) == []


# ── С14: a future format is fatal, never a silent re-parse ─────────────────

def test_c14_a_future_schema_version_is_fatal(tmp_path):
    path = _store_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": FIELD_OVERRIDES_SCHEMA_VERSION + 1, "records": []}),
        encoding="utf-8")

    with pytest.raises(ValueError):
        load_field_overrides(path)


def test_a_malformed_store_reads_as_empty_and_warns(tmp_path, caplog):
    """Same leniency as the copper registry: a corrupt file must not take the
    resolver down — it warns and reads as empty (the Log line is the visible
    symptom), while a FUTURE version stays fatal above."""
    path = _store_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        store = load_field_overrides(path)

    assert store.records() == []
    assert caplog.records
