# tests/config/test_upsert_identity.py
"""Part 0а-1 of plan_2026_10_05_uuid_tails: `upsert_list_entry` edits a record by
its UUID when the entry carries one.

A form save after a RENAME in the form keeps the record's uuid (the form-identity
rule of part 0). Matching by NAME alone found no record under the new name and
appended a second one, and the format-3 writer stamp refused the whole write with
"duplicate uuid" — a renamed record became un-saveable (Denis/Claude, 07.10).

The rule (ONE place, the write primitive every dock's Save goes through):
  * the entry replaces the record that already carries ITS uuid — a rename lands
    in place, the position is kept;
  * the entry has no uuid -> the historical NAME-based behaviour, byte for byte
    (including a legacy record without a uuid being replaced by name);
  * a name held by ANOTHER record is refused BEFORE anything is written.
"""
import pytest

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_writer import RecordNameTaken, upsert_list_entry


@pytest.fixture(autouse=True)
def _restore_active_graph_root():
    """The format-3 writer stamp resolves references against the process-wide
    ACTIVE GRAPH ROOT; `_seed` points it at the file it wrote and this restores
    it, so nothing leaks between tests."""
    from kicadstamp.config_working_set import active_graph_root, set_active_graph_root

    previous = active_graph_root()
    yield
    set_active_graph_root(previous)


def _seed(tmp_path, records, name="t.sexp"):
    p = tmp_path / name
    p.write_text(dict_to_sexp({"thermal_via_arrays": records}, format_number=3),
                 encoding="utf-8")
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(p)
    return p


def _records(path):
    return (sexp_to_dict(path.read_text(encoding="utf-8")) or {}).get("thermal_via_arrays") or []


def test_a_rename_lands_in_place(tmp_path):
    """The entry's uuid is the record's identity: the rename REPLACES that record
    (same position, same uuid, new name) instead of appending a second one."""
    p = _seed(tmp_path, [
        {"name": "tva_a", "uuid": "U-A", "pad": "1"},
        {"name": "tva_b", "uuid": "U-B", "pad": "2"},
    ])

    overwritten = upsert_list_entry(p, "thermal_via_arrays",
                                    {"name": "tva_a_renamed", "uuid": "U-A", "pad": "9"})

    assert overwritten is True
    records = _records(p)
    assert [r["name"] for r in records] == ["tva_a_renamed", "tva_b"]
    assert [r["uuid"] for r in records] == ["U-A", "U-B"]   # no duplicate uuid
    assert records[0]["pad"] == "9"


def test_a_name_taken_by_another_record_is_refused_and_nothing_is_written(tmp_path):
    p = _seed(tmp_path, [
        {"name": "tva_a", "uuid": "U-A", "pad": "1"},
        {"name": "tva_b", "uuid": "U-B", "pad": "2"},
    ])
    before = p.read_text(encoding="utf-8")

    with pytest.raises(RecordNameTaken) as e:
        upsert_list_entry(p, "thermal_via_arrays",
                          {"name": "tva_b", "uuid": "U-A", "pad": "1"})

    assert "already used by another record" in str(e.value)
    assert p.read_text(encoding="utf-8") == before          # refused BEFORE writing


def test_an_entry_without_a_uuid_still_edits_by_name(tmp_path):
    """The historical behaviour, unchanged: no uuid in the entry -> the record
    with that identity is replaced in place."""
    p = _seed(tmp_path, [{"name": "tva_a", "uuid": "U-A", "pad": "1"}])

    overwritten = upsert_list_entry(p, "thermal_via_arrays",
                                    {"name": "tva_a", "pad": "7"})

    assert overwritten is True
    assert len(_records(p)) == 1
    assert _records(p)[0]["pad"] == "7"


def test_a_form_entry_replaces_a_legacy_record_of_the_same_name(tmp_path):
    """A format-2 record (no uuid) is still replaced by its name when the form
    entry carries a uuid — the upgrade path, not a conflict."""
    p = _seed(tmp_path, [{"name": "tva_a", "pad": "1"}])   # no uuid

    overwritten = upsert_list_entry(p, "thermal_via_arrays",
                                    {"name": "tva_a", "uuid": "U-A", "pad": "2"})

    assert overwritten is True
    assert _records(p) == [{"name": "tva_a", "pad": "2", "uuid": "U-A"}]


def test_a_new_record_is_appended(tmp_path):
    p = _seed(tmp_path, [{"name": "tva_a", "uuid": "U-A", "pad": "1"}])

    overwritten = upsert_list_entry(p, "thermal_via_arrays",
                                    {"name": "tva_new", "uuid": "U-N", "pad": "3"})

    assert overwritten is False
    assert [r["name"] for r in _records(p)] == ["tva_a", "tva_new"]


def test_the_refusal_is_also_an_oserror(tmp_path):
    """Every write path already wraps its upsert in `except OSError` and prints a
    red Log line — the refusal must be caught there (and never escape into a Qt
    slot, where an uncaught exception aborts the process)."""
    p = _seed(tmp_path, [
        {"name": "tva_a", "uuid": "U-A", "pad": "1"},
        {"name": "tva_b", "uuid": "U-B", "pad": "2"},
    ])
    with pytest.raises(OSError):
        upsert_list_entry(p, "thermal_via_arrays",
                          {"name": "tva_b", "uuid": "U-A", "pad": "1"})
