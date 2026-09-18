# tests/test_cli_overrides_config.py
"""Т5в (plan_2026_09_18_field_overrides_store): the OPTIONAL `--config` of
`extract`, `extract-net` and `channel-copy`.

Two halves, and both need guarding:

  * the PLUMBING — with the flag each command asks the factory for the profile's
    adapter, without it for the BARE one. A flag that changes nothing is worse
    than no flag at all: the help text promises a different truth, so the promise
    is what gets pinned here;
  * the SUBSTANCE — the value these commands read (and, for `extract`, write INTO
    THE CELL: `template_extraction.py` reads exactly this call for every selected
    footprint) is the EFFECTIVE one when the store is in force, and the board's
    when it is not.
"""
import sys
from types import SimpleNamespace

import pytest

import kicadstamp.cli as cli_mod
import kicadstamp.cli_main as cli_main_mod
from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.field_override_adapter import FieldOverrideAdapter
from kicadstamp.field_overrides import FieldOverrides, SOURCE_CLI
from kicadstamp.utils.paths import overrides_path_for_config


class _Inner:
    """Minimal board adapter: a Role per refdes, straight from the "board"."""

    def __init__(self, fields):
        self._fields = fields
        self.field_reads = 0

    def get_field_value(self, fp, field):
        self.field_reads += 1
        return self._fields.get((fp.ref, field))


def _fp(ref, symbol_uuid):
    return SimpleNamespace(
        ref=ref,
        sheet_path=SimpleNamespace(path=[SimpleNamespace(value=symbol_uuid)]))


def _profile_with_store(tmp_path, *records):
    """A real store next to a throwaway profile — the shape the flag points at."""
    profile = tmp_path / "prof.sexp"
    profile.write_text("", encoding="utf-8")
    store = FieldOverrides(overrides_path_for_config(str(profile)))
    for symbol_uuid, ref, field, value in records:
        store.set(symbol_uuid, ref, field, value, SOURCE_CLI)
    store.save()
    return profile, store


def _fat_args(**extra):
    """Every field the three commands touch BEFORE they build the adapter — this
    test is about the adapter call, not about their own validation."""
    base = dict(config="prof.sexp", timeout_ms=13, net="DAC_DB0", anchor_role="R_FB",
                output="out.sexp", src="Channel_0", dst=["Channel_1"], verbose=False)
    base.update(extra)
    return SimpleNamespace(**base)


# ── the plumbing: the flag reaches the factory ────────────────────────────

def test_with_config_the_adapter_is_the_profiles(monkeypatch):
    """WITH the flag the profile decides — its override store is layered in, and
    its own role_cluster_source switch keeps the last word, so the CLI never
    passes use_store behind the profile's back (one profile, one answer)."""
    seen = {}

    def _fake(timeout_ms=None, **kwargs):
        seen["timeout_ms"] = timeout_ms
        seen.update(kwargs)
        return "adapter"

    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter", _fake)

    assert cli_mod._board_adapter(_fat_args()) == "adapter"
    assert seen == {"timeout_ms": 13, "config_path": "prof.sexp"}
    assert "use_store" not in seen


def test_without_config_the_adapter_is_bare(monkeypatch):
    """WITHOUT it the values are the BOARD's, as before — spelled out as
    use_store=False rather than left to whatever the default happens to be."""
    seen = {}

    def _fake(timeout_ms=None, **kwargs):
        seen["timeout_ms"] = timeout_ms
        seen.update(kwargs)
        return "adapter"

    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter", _fake)

    assert cli_mod._board_adapter(_fat_args(config=None)) == "adapter"
    assert seen == {"timeout_ms": 13, "use_store": False}
    assert "config_path" not in seen


@pytest.mark.parametrize("command", ["cmd_extract", "cmd_extract_net", "cmd_channel_copy"])
def test_every_command_goes_through_the_shared_helper(command, monkeypatch):
    """One helper, three callers. Three hand-rolled copies would drift, and the
    shared help text would start lying about whichever one drifted first."""
    class _Stop(Exception):
        pass

    monkeypatch.setattr(cli_mod, "_board_adapter", lambda args: (_ for _ in ()).throw(_Stop()))

    with pytest.raises(_Stop):
        getattr(cli_mod, command)(_fat_args())


def test_the_three_commands_document_both_halves(monkeypatch, capsys):
    """The plan's own words: «В справке сказать прямо, что без него берутся
    значения ПЛАТЫ». A flag whose help hides its default is exactly how a user ends
    up with a cell full of the board's roles and no idea why."""
    for command in ("extract", "extract-net", "channel-copy"):
        monkeypatch.setattr(sys, "argv", ["kicadstamp", command, "--help"])
        with pytest.raises(SystemExit) as exit_info:
            cli_main_mod.main()
        assert exit_info.value.code == 0
        # argparse WRAPS help text, so compare with the newlines collapsed —
        # otherwise this guard would be about the terminal width, not the text.
        help_text = " ".join(capsys.readouterr().out.split())
        assert "--config FILE" in help_text
        assert "WITHOUT it the values come from the BOARD" in help_text


# ── the substance: the value read is the effective one ───────────────────

def test_the_role_read_for_the_cell_is_the_stores_when_it_has_one(tmp_path):
    """С24. `extract` writes into the cell whatever `get_field_value(fp,
    Role)` answers for each selected footprint, so this ONE call is the whole
    difference between a usable cell and a cell full of stale roles:
    through the overlay it is our value, bare it is the board's."""
    _profile, store = _profile_with_store(
        tmp_path, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))
    board = _Inner({("R1", ROLE_FIELD_NAME): "FROM_BOARD"})
    fp = _fp("R1", "uuid-R1")

    # WITHOUT --config: the bare adapter, i.e. what physically lies on the board.
    assert board.get_field_value(fp, ROLE_FIELD_NAME) == "FROM_BOARD"

    # WITH --config: the store's value wins, even though the board disagrees.
    layered = FieldOverrideAdapter(board, store)
    assert layered.get_field_value(fp, ROLE_FIELD_NAME) == "FROM_STORE"


def test_a_component_the_store_does_not_know_still_comes_from_the_board(tmp_path):
    """The other direction, and the one that keeps the overlay honest: an empty
    store (or a component nobody noted) must answer EXACTLY like the bare
    adapter — that is the pre-store world the golden guard С2 pins."""
    _profile, store = _profile_with_store(tmp_path)   # EMPTY store
    board = _Inner({("R1", ROLE_FIELD_NAME): "FROM_BOARD"})
    fp = _fp("R1", "uuid-R1")

    bare = FieldOverrideAdapter(board, store).get_field_value(fp, ROLE_FIELD_NAME)

    assert bare == "FROM_BOARD"
