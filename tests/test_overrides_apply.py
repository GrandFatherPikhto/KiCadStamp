# tests/test_overrides_apply.py
"""Т5а (plan_2026_09_18_field_overrides_store): writing our stored values OUT
again — `overrides-apply --to board|schematic` and `overrides-list`.

Why this exists at all: Т5 moved Stage and the Refs table OFF the board, because
our stored value outranks it (writing the board would have been invisible), but
the board is still the carrier OTHER tools read (BOM, net classes — design §4.2).
So the write outward stays — as an explicit, named action, and one that works
where it matters most: `--to schematic` is an OFFLINE `.kicad_sch` splice, i.e.
KiCad-closed by nature, which until now was reachable only from the GUI.

Guard С20: both destinations do what the GUI's own write does, and `--dry-run`
prints the plan without writing ANYTHING (mutation М20: `--dry-run` writes).

The three properties pinned here, in the order they can go wrong:

  * the board write is addressed by SYMBOL UUID, not by the refdes stored next to
    it (С3's rule on the outward path): an F8 re-annotation renames components,
    and a refdes-keyed write would land on a FOREIGN one;
  * a record the board cannot take (no such symbol, no such field) is REFUSED BY
    NAME, never silently dropped, and the rest of the batch still goes;
  * `--dry-run` writes nothing and still says what it WOULD do — otherwise the
    flag is a trap, not a preflight.
"""
import sys
from types import SimpleNamespace

import pytest

import kicadstamp.cli as cli_mod
import kicadstamp.cli_main as cli_main_mod
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.field_overrides import FieldOverrides, SOURCE_CELL_TABLE
from kicadstamp.utils.paths import overrides_path_for_config
from tests.fieldstool_fixtures import sch_file, symbol_block


class _FakeBoard:
    """A live-board stand-in: footprints with a symbol uuid, the per-field
    has_field answer, and the ONE bulk write the real adapter exposes."""

    def __init__(self, footprints):
        self._footprints = list(footprints)
        self.bulk = []
        self.closed = 0

    def close(self):
        """The real adapter's close() — the CLI calls it, so the pynng socket
        is never left for the GC (see kicadstamp/kicad/pynng_safety.py)."""
        self.closed += 1

    def get_footprints(self):
        return list(self._footprints)

    def has_field(self, fp, field):
        return field not in (fp.missing_fields or ())

    def set_field_values_bulk(self, updates, description):
        self.bulk.append((list(updates), description))
        return True


def _fp(ref, symbol_uuid, missing_fields=()):
    return SimpleNamespace(
        ref=ref,
        missing_fields=tuple(missing_fields),
        sheet_path=SimpleNamespace(path=[SimpleNamespace(value=symbol_uuid)]))


def _profile(tmp_path, root_sheet=None):
    """A throwaway profile (JSON: the raw reader takes .sexp and .json alike)."""
    profile = tmp_path / "prof.json"
    profile.write_text('{"root_sheet": "%s"}' % (root_sheet or ""), encoding="utf-8")
    return profile


def _store_of(profile, *records):
    store = FieldOverrides(overrides_path_for_config(str(profile)))
    for symbol_uuid, ref, field, value in records:
        store.set(symbol_uuid, ref, field, value, SOURCE_CELL_TABLE)
    store.save()
    return store


def _stored_records(profile):
    """The store as it is ON DISK — the truth after any command."""
    from kicadstamp.field_overrides import load_field_overrides
    return load_field_overrides(overrides_path_for_config(str(profile))).records()


def _store_bytes(profile):
    from kicadstamp.utils.paths import overrides_path_for_config as _p
    from pathlib import Path
    path = Path(_p(str(profile)))
    return path.read_bytes() if path.exists() else b""


def _args(config, to="board", dry_run=False, root_sheet=None):
    return SimpleNamespace(config=str(config), to=to, dry_run=dry_run,
                           root_sheet=root_sheet, timeout_ms=13, verbose=False)


def _kicad_closed(monkeypatch):
    """Pretend KiCad is not running. The offline splice is guarded by
    check_kicad_not_running() and this DEVELOPER MACHINE really does have KiCad
    up — the guard firing is the correct behaviour, so a test that wants to write
    has to state "KiCad is closed" itself instead of hoping. The guard's own
    firing is pinned by test_c20_a_running_kicad_stops_the_offline_splice below."""
    import kicadstamp.schematic_editing as editing

    monkeypatch.setattr(editing, "list_kicad_pids", lambda: [])


def _board_adapter(monkeypatch, board):
    """Route the command's adapter creation to this stand-in. The command
    imports create_board_adapter lazily (like every other board-bearing
    command), so the patch has to hit the FACTORY's own attribute."""
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda *a, **kw: board)


# ── --to board ─────────────────────────────────────────────────────────────

def test_c20_a_dry_run_board_write_prints_the_plan_and_writes_nothing(
        tmp_path, monkeypatch):
    """С20/М20, first half: `--dry-run` is a PREFLIGHT. It reads the board (it
    has to — "will this land?" is the whole question) but the ONE write call must
    never happen, and what it prints has to be worth reading: the ref, the field
    and the value."""
    profile = _profile(tmp_path)
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))
    board = _FakeBoard([_fp("R1", "uuid-R1")])
    _board_adapter(monkeypatch, board)

    lines = cli_mod.cmd_overrides_apply(_args(profile, dry_run=True))

    assert board.bulk == []                     # nothing written
    text = "\n".join(lines)
    assert "R1" in text and ROLE_FIELD_NAME in text and "FROM_STORE" in text


def test_c20_the_board_write_targets_the_footprint_with_that_symbol_uuid_not_the_refdes(
        tmp_path, monkeypatch):
    """The store's `ref` is a HUMAN-READABLE LABEL, not the key (Т1). The board
    here says the symbol now sits on a DIFFERENT refdes than the record remembers
    — the state an F8 re-annotation leaves behind — and the write must still land
    on the footprint carrying that symbol uuid. Keying by the stored refdes would
    put our value on whatever component happens to own the old name now."""
    profile = _profile(tmp_path)
    _store_of(profile, ("uuid-R7", "R7_OLD_REF", ROLE_FIELD_NAME, "FROM_STORE"))
    renamed = _fp("R7", "uuid-R7")
    board = _FakeBoard([renamed, _fp("R7_OLD_REF", "uuid-OTHER")])
    _board_adapter(monkeypatch, board)

    cli_mod.cmd_overrides_apply(_args(profile))

    assert board.bulk == [([(renamed, ROLE_FIELD_NAME, "FROM_STORE")],
                           board.bulk[0][1])]
    assert board.bulk[0][1]                       # ... and it is labelled


def test_c20_the_board_write_is_one_bulk_commit(tmp_path, monkeypatch):
    """One commit, not one per record: KiCad's Ctrl+Z then takes the whole batch
    back, which is the same reasoning the tree's Tag and the Refs table follow."""
    profile = _profile(tmp_path)
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "A"),
              ("uuid-R1", "R1", CLUSTER_FIELD_NAME, "CL_A"),
              ("uuid-C9", "C9", ROLE_FIELD_NAME, "B"))
    board = _FakeBoard([_fp("R1", "uuid-R1"), _fp("C9", "uuid-C9")])
    _board_adapter(monkeypatch, board)

    cli_mod.cmd_overrides_apply(_args(profile))

    assert len(board.bulk) == 1
    assert len(board.bulk[0][0]) == 3
    # ... and the socket is handed back, never left to the GC.
    assert board.closed == 1


def test_c20_a_record_whose_symbol_is_not_on_the_board_is_refused_by_name(
        tmp_path, monkeypatch):
    """A stored symbol the live board does not carry (another board, a deleted
    component) has nowhere to land. It is reported BY NAME — the store's own
    refdes, which is exactly why it is stored next to the key."""
    profile = _profile(tmp_path)
    _store_of(profile, ("uuid-GONE", "GONE1", ROLE_FIELD_NAME, "X"))
    board = _FakeBoard([_fp("R1", "uuid-R1")])
    _board_adapter(monkeypatch, board)

    lines = cli_mod.cmd_overrides_apply(_args(profile))

    assert board.bulk == []
    assert any("GONE1" in line for line in lines)


def test_c20_a_field_the_footprint_lacks_is_refused_per_field_and_the_rest_goes(
        tmp_path, monkeypatch):
    """The `has_field` rule belongs to the BOARD write (the store does not care):
    a footprint physically lacking Role cannot be handed that field. The skip is
    PER FIELD and PER COMPONENT — one bad footprint must never roll back the
    batch, the same contract the Refs table's board write had."""
    profile = _profile(tmp_path)
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "A"),
              ("uuid-R1", "R1", CLUSTER_FIELD_NAME, "CL_A"),
              ("uuid-FB3", "FB3", CLUSTER_FIELD_NAME, "CL_B"))
    board = _FakeBoard([_fp("R1", "uuid-R1", missing_fields=(CLUSTER_FIELD_NAME,)),
                        _fp("FB3", "uuid-FB3")])
    _board_adapter(monkeypatch, board)

    lines = cli_mod.cmd_overrides_apply(_args(profile))

    # FB3 first: a plan is reported in REF order (overrides_apply._sort_key), so
    # the printed plan reads like the table a human is looking at, not like the
    # store's uuid-keyed iteration order.
    assert [(fp.ref, field) for fp, field, _v in board.bulk[0][0]] == [
        ("FB3", CLUSTER_FIELD_NAME), ("R1", ROLE_FIELD_NAME)]
    assert any("R1" in line for line in lines), "the skipped field is named"


def test_c20_an_empty_store_never_even_connects_to_the_board(tmp_path, monkeypatch):
    """An empty store means nothing to do — and "nothing to do" must not open a
    socket to KiCad: a read-only connect can still block for the full dial
    timeout on a stale socket (see gui/connection.py's own docstring)."""
    profile = _profile(tmp_path)
    calls = []
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda *a, **kw: calls.append(kw))

    lines = cli_mod.cmd_overrides_apply(_args(profile))

    assert calls == []
    assert any("store" in line.lower() for line in lines)


def test_c20_the_profile_switch_board_makes_the_store_inapplicable(
        tmp_path, monkeypatch):
    """A profile whose `role_cluster_source` says "board" has values that are NOT
    in force (Т3/Т7) — so writing them out would be writing something the
    resolver ignores. Both destinations refuse, out loud, and touch nothing."""
    profile = tmp_path / "prof.json"
    profile.write_text('{"role_cluster_source": "board"}', encoding="utf-8")
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "X"))
    board = _FakeBoard([_fp("R1", "uuid-R1")])
    _board_adapter(monkeypatch, board)

    lines = cli_mod.cmd_overrides_apply(_args(profile))
    cli_mod.cmd_overrides_apply(_args(profile, to="schematic"))

    assert board.bulk == []
    assert any("board" in line.lower() for line in lines)


# ── --to schematic ─────────────────────────────────────────────────────────

def _schematic_profile(tmp_path, role="OLD", symbol_uuid="uuid-R1"):
    """A profile pointing at a real (synthetic) .kicad_sch with one symbol.

    The symbol CARRIES a top-level uuid on purpose: that is the bridge between the
    store's key and the schematic (and therefore what makes the С5 forgetting of
    this step possible at all)."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role=role, cluster="CL_OLD",
                                          symbol_uuid=symbol_uuid)),
                    encoding="utf-8")
    return _profile(tmp_path, root_sheet="root.kicad_sch"), root


def test_c20_a_dry_run_schematic_write_leaves_the_file_byte_identical(tmp_path):
    """С20/М20, second half: the offline splice is a real file write, so the
    preflight has to be provably inert — byte-identical, not "looks unchanged"."""
    profile, root = _schematic_profile(tmp_path)
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))
    before = root.read_bytes()

    lines = cli_mod.cmd_overrides_apply(_args(profile, to="schematic", dry_run=True))

    assert root.read_bytes() == before
    assert "FROM_STORE" in "\n".join(lines)


def test_c20_the_schematic_write_lands_the_stored_value_in_the_sheet(
        tmp_path, monkeypatch):
    """The stored value — NOT the schematic's own — is what lands, keyed by the
    refdes the record carries (the .kicad_sch splice is refdes/block addressed;
    the store's uuid is what keeps the STORE honest). This is the same truth
    Apply writes (С16), now reachable with KiCad closed."""
    _kicad_closed(monkeypatch)
    profile, root = _schematic_profile(tmp_path)
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))

    cli_mod.cmd_overrides_apply(_args(profile, to="schematic"))

    text = root.read_text(encoding="utf-8")
    assert 'property "Role" "FROM_STORE"' in text
    # The Role property itself is gone; the CLUSTER block still says CL_OLD (it
    # was never in the store), so the assertion has to name the property.
    assert 'property "Role" "OLD"' not in text
    assert 'property "Cluster" "CL_OLD"' in text


def test_c20_a_schematic_write_keeps_a_backup_of_what_it_replaced(
        tmp_path, monkeypatch):
    """`write_files` leaves a .bak next to the file it rewrote — the offline
    splice's only undo. Pinned here because "our value wins ALWAYS" makes a bad
    write expensive: the schematic is the side a human reads."""
    _kicad_closed(monkeypatch)
    profile, root = _schematic_profile(tmp_path)
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))

    cli_mod.cmd_overrides_apply(_args(profile, to="schematic"))

    assert (tmp_path / "root.kicad_sch.bak").is_file()


def test_c20_a_running_kicad_stops_the_offline_splice(tmp_path, monkeypatch):
    """The other half of the same rule: while Eeschema has the file open, its own
    next save would silently overwrite what we spliced, so the write must be
    REFUSED (as the CLI's own error type, never as a traceback) with the file
    left byte-identical."""
    import kicadstamp.schematic_editing as editing
    from kicadstamp.exceptions import PlacerError

    monkeypatch.setattr(editing, "list_kicad_pids", lambda: [4242])
    profile, root = _schematic_profile(tmp_path)
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))
    before = root.read_bytes()

    with pytest.raises(PlacerError, match="KiCad appears to be running"):
        cli_mod.cmd_overrides_apply(_args(profile, to="schematic"))

    assert root.read_bytes() == before


def test_c20_an_unknown_refdes_is_fatal_and_nothing_is_written(
        tmp_path, monkeypatch):
    """A record for a refdes no sheet reachable from the root carries: the
    planner raises (all problems at once, nothing written) — reported as the
    CLI's own error type, so run_cli maps it to exit code 1 instead of a
    traceback, and never as a silent partial splice."""
    from kicadstamp.exceptions import PlacerError

    _kicad_closed(monkeypatch)
    profile, root = _schematic_profile(tmp_path)
    _store_of(profile, ("uuid-NOPE", "NOPE1", ROLE_FIELD_NAME, "X"))
    before = root.read_bytes()

    with pytest.raises(PlacerError, match="NOPE1"):
        cli_mod.cmd_overrides_apply(_args(profile, to="schematic"))

    assert root.read_bytes() == before


# ── С5: the note goes when the SCHEMATIC and the note agree ───────────────

def test_c5_the_schematic_apply_forgets_the_note_it_just_made_redundant(
        tmp_path, monkeypatch):
    """С5: once the schematic carries our value, the note has done its job.

    Keeping it would turn a reminder into a VETO: while a record lives, OUR value
    outranks everything (plan §0) — including the schematic — so the user's next
    edit in KiCad would be silently ignored. The board is not part of this test
    (see the next one): the board is rewritten by F8, and the note is exactly what
    protects the intended value until then."""
    _kicad_closed(monkeypatch)
    profile, root = _schematic_profile(tmp_path)
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))

    lines = cli_mod.cmd_overrides_apply(_args(profile, to="schematic"))

    assert 'property "Role" "FROM_STORE"' in root.read_text(encoding="utf-8")
    assert _stored_records(profile) == []
    assert any("forgot" in line.lower() for line in lines)


def test_c5_a_board_apply_keeps_every_record(tmp_path, monkeypatch):
    """М5, the half that matters most: writing the value ONTO THE BOARD does not
    touch the store. The board agreeing today says nothing about tomorrow's F8 —
    and the note is what keeps our value in force across it."""
    profile = _profile(tmp_path)
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))
    board = _FakeBoard([_fp("R1", "uuid-R1")])
    _board_adapter(monkeypatch, board)

    cli_mod.cmd_overrides_apply(_args(profile))

    assert board.bulk, "the board write happened"
    assert [r.symbol_uuid for r in _stored_records(profile)] == ["uuid-R1"]


def test_c5_a_schematic_dry_run_forgets_nothing(tmp_path, monkeypatch):
    """The preflight half: --dry-run must not prune the store either, or it would
    be a write wearing a dry-run's name."""
    _kicad_closed(monkeypatch)
    profile, _root = _schematic_profile(tmp_path)
    _store_of(profile, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))
    before = _store_bytes(profile)

    cli_mod.cmd_overrides_apply(_args(profile, to="schematic", dry_run=True))

    assert _store_bytes(profile) == before


# ── overrides-list ─────────────────────────────────────────────────────────

def test_overrides_list_prints_ref_field_value_and_source(tmp_path):
    """The store is invisible otherwise, and it now outranks the board — so
    "what is in there?" has to be answerable without opening the GUI. Sorted by
    ref so two runs are diffable."""
    profile = _profile(tmp_path)
    _store_of(profile, ("uuid-R2", "R2", ROLE_FIELD_NAME, "B"),
              ("uuid-R1", "R1", CLUSTER_FIELD_NAME, "CL_A"))

    lines = cli_mod.cmd_overrides_list(SimpleNamespace(config=str(profile),
                                                       verbose=False))

    assert len(lines) == 2
    assert "R1" in lines[0] and CLUSTER_FIELD_NAME in lines[0] and "CL_A" in lines[0]
    assert SOURCE_CELL_TABLE in lines[0]
    assert "R2" in lines[1]


def test_overrides_list_on_a_missing_store_says_it_is_empty(tmp_path):
    """С11's rule seen from the CLI: reading a store that does not exist must not
    CREATE it (that write would be invisible otherwise), so the command reports an
    empty store and leaves the directory alone."""
    profile = _profile(tmp_path)

    lines = cli_mod.cmd_overrides_list(SimpleNamespace(config=str(profile),
                                                       verbose=False))

    assert any("empty" in line.lower() for line in lines)
    assert not (tmp_path / "overrides").exists()


# ── the parser: the names must survive the bare-config rewrite ────────────

def test_c20_the_two_new_subcommands_are_not_swallowed_by_the_bare_config_rewrite():
    """`kicadstamp_cli.py <unknown>` is the bare-config shorthand for `apply`, so
    a subcommand that is not in _SUBCOMMANDS gets an 'apply' prepended and dies
    as "unrecognized arguments" — the trap the flattened list at the bottom of
    main() already fell into once. Both new names must be registered."""
    for name in ("overrides-apply", "overrides-list"):
        assert cli_main_mod._rewrite_bare_config_to_apply(["kicadstamp", name]) is False


def test_c20_both_subcommands_document_the_destinations_and_the_dry_run(
        monkeypatch, capsys):
    """A user has to learn three things from `-h` alone: that the store is what is
    written, that there are two destinations, and that --dry-run is safe.
    argparse wraps, so compare with newlines collapsed."""
    for command, expected in (("overrides-apply", ("--to", "board", "schematic",
                                                   "--dry-run")),
                              ("overrides-list", ("--config",))):
        monkeypatch.setattr(sys, "argv", ["kicadstamp", command, "--help"])
        with pytest.raises(SystemExit) as exit_info:
            cli_main_mod.main()
        assert exit_info.value.code == 0
        help_text = " ".join(capsys.readouterr().out.split())
        for needle in expected:
            assert needle in help_text, f"{command}: {needle!r} not documented"
        assert "store" in help_text.lower()
