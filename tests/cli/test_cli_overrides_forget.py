# tests/test_cli_overrides_forget.py
"""Т6 (plan_2026_09_18_field_overrides_store): `overrides-forget` — the explicit
"передумал" of the store.

Two guards, both about honesty:

  * a record the user ASKED to forget goes — and nothing else does (a `--field`
    narrows it to one field, `--ref` never means "all of them");
  * `--dry-run` writes NOTHING. A preflight that prunes the file would be a trap,
    exactly like the `--dry-run` the splash of Т5а pins.

The command reads the store FILE directly rather than resolving it through the
profile's `role_cluster_source` switch: that switch says whether notes are IN
FORCE, while forgetting is file maintenance — a note that is currently inactive is
still the user's note, and the command that removes it must not answer "there is
nothing in force to remove".
"""
from types import SimpleNamespace

import kicadstamp.cli as cli_mod
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.field_overrides import (FieldOverrides, load_field_overrides,
                                        SOURCE_CELL_TABLE)
from kicadstamp.utils.paths import overrides_path_for_config


def _profile_with_store(tmp_path, *records):
    profile = tmp_path / "prof.json"
    profile.write_text("{}", encoding="utf-8")
    store = FieldOverrides(overrides_path_for_config(str(profile)))
    for symbol_uuid, ref, field, value in records:
        store.set(symbol_uuid, ref, field, value, SOURCE_CELL_TABLE)
    store.save()
    return profile, store


def _args(profile, refs=(), field=None, all_records=False, dry_run=False):
    return SimpleNamespace(config=str(profile), ref=list(refs), field=field,
                           all=all_records, dry_run=dry_run, verbose=False)


def _on_disk(profile):
    return load_field_overrides(overrides_path_for_config(str(profile)))


def test_forget_removes_the_named_component_and_leaves_the_rest(tmp_path):
    profile, _store = _profile_with_store(
        tmp_path,
        ("uuid-R1", "R1", ROLE_FIELD_NAME, "A"),
        ("uuid-R1", "R1", CLUSTER_FIELD_NAME, "bank"),
        ("uuid-R2", "R2", ROLE_FIELD_NAME, "B"))

    lines = cli_mod.cmd_overrides_forget(_args(profile, refs=["R1"]))

    assert [(r.ref, r.field) for r in _on_disk(profile).records()] == [
        ("R2", ROLE_FIELD_NAME)]
    assert any("R1" in line for line in lines)


def test_forget_can_narrow_to_one_field(tmp_path):
    """The everyday case: the Role is settled, the Cluster is not. Forgetting the
    component WHOLE would drop a note the user still needs."""
    profile, _store = _profile_with_store(
        tmp_path,
        ("uuid-R1", "R1", ROLE_FIELD_NAME, "A"),
        ("uuid-R1", "R1", CLUSTER_FIELD_NAME, "bank"))

    cli_mod.cmd_overrides_forget(_args(profile, refs=["R1"], field=ROLE_FIELD_NAME))

    assert [(r.field, r.value) for r in _on_disk(profile).records()] == [
        (CLUSTER_FIELD_NAME, "bank")]


def test_forget_dry_run_leaves_the_file_byte_identical(tmp_path):
    """М-dry-run: the plan is printed, the file is untouched — and the plan has to
    name what would go, or the flag is decoration."""
    profile, store = _profile_with_store(tmp_path, ("uuid-R1", "R1", ROLE_FIELD_NAME, "A"))
    before = store.path.read_bytes()

    lines = cli_mod.cmd_overrides_forget(_args(profile, refs=["R1"], dry_run=True))

    assert store.path.read_bytes() == before
    assert any("R1" in line and ROLE_FIELD_NAME in line for line in lines)


def test_forget_all_drops_every_record(tmp_path):
    profile, store = _profile_with_store(
        tmp_path,
        ("uuid-R1", "R1", ROLE_FIELD_NAME, "A"),
        ("uuid-R2", "R2", CLUSTER_FIELD_NAME, "bank"))

    cli_mod.cmd_overrides_forget(_args(profile, all_records=True))

    assert _on_disk(profile).has_any() is False


def test_forget_needs_a_name_and_says_so(tmp_path):
    """No --ref and no --all: refusing is the honest answer. A command that
    defaults to "wipe everything" is one keystroke away from destroying notes the
    user cannot retype from memory."""
    profile, _store = _profile_with_store(tmp_path, ("uuid-R1", "R1", ROLE_FIELD_NAME, "A"))

    lines = cli_mod.cmd_overrides_forget(_args(profile))

    assert any("--ref" in line for line in lines)
    assert _on_disk(profile).has_any() is True


def test_forget_names_a_ref_it_has_no_record_for(tmp_path):
    """A typo (or a component somebody else already forgot) is reported by name,
    never silently ignored — and the records that DO match still go."""
    profile, _store = _profile_with_store(
        tmp_path,
        ("uuid-R1", "R1", ROLE_FIELD_NAME, "A"),
        ("uuid-R2", "R2", ROLE_FIELD_NAME, "B"))

    lines = cli_mod.cmd_overrides_forget(_args(profile, refs=["R9", "R1"]))

    assert any("R9" in line for line in lines)
    assert [r.ref for r in _on_disk(profile).records()] == ["R2"]


def test_forget_an_empty_store_says_so_without_creating_a_file(tmp_path):
    profile = tmp_path / "prof.json"
    profile.write_text("{}", encoding="utf-8")

    lines = cli_mod.cmd_overrides_forget(_args(profile, refs=["R1"]))

    assert any("empty" in line.lower() for line in lines)
    assert not (tmp_path / "overrides").exists()
