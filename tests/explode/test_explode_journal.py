# tests/explode/test_explode_journal.py
"""Cells for the explode JOURNAL and EXECUTION (plan
``plan_2026_10_05_explode_r1_core.md`` §3, design РЗ5/РЗ6).

The property under guard is the plan's headline one: **explode -> restore = the
board exactly as before, 0 nm**. Around it: the journal is written BEFORE the
transaction (a failed transaction leaves no journal and an untouched board), a
live journal refuses a second explode, the post-checks catch an item off target
or a dragged non-moved copper, and restore names what was moved by hand / is
gone and keeps the journal unless everything is back.
"""
import pytest

from kicadstamp import explode_journal as journal_mod
from kicadstamp.explode import ExplodeError, Pose

from tests.explode.board import F_CU, track
from tests.explode.test_explode_plan import _plan, _scenario


def _poses(board):
    out = {}
    for item in (list(board.get_footprints()) + list(board.get_tracks())
                 + list(board.get_vias())):
        out[item.uuid] = Pose.of(item)
    return out


def test_explode_then_restore_is_the_board_as_before(tmp_path, monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg)
    assert {mv.uuid for mv in plan.moves}          # something moves
    before = _poses(board)

    journal_mod.explode(board, plan, journal_dir_override=tmp_path)
    assert _poses(board) != before                # the board really moved
    path = journal_mod.journal_path(board, tmp_path)
    assert path.is_file()

    journal_mod.restore(board, path)
    assert _poses(board) == before                # 0 nm, exactly as before
    assert not path.is_file()                     # journal removed on success


def test_failed_transaction_leaves_no_journal_and_the_board_untouched(
        tmp_path, monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg)
    before = _poses(board)
    board.fail_update = True
    with pytest.raises(RuntimeError):
        journal_mod.explode(board, plan, journal_dir_override=tmp_path)
    assert not journal_mod.journal_path(board, tmp_path).is_file()
    assert _poses(board) == before                # drop_commit rolled back


def test_journal_is_written_before_the_transaction(tmp_path, monkeypatch):
    """The journal must exist AT update time; a failed update then removes it."""
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg)
    seen = {}

    def _observe():
        seen["journal"] = journal_mod.journal_path(board, tmp_path).is_file()

    board.on_update = _observe
    board.fail_update = True
    with pytest.raises(RuntimeError):
        journal_mod.explode(board, plan, journal_dir_override=tmp_path)
    assert seen.get("journal") is True            # written BEFORE the shift
    assert not journal_mod.journal_path(board, tmp_path).is_file()  # and removed


def test_a_live_journal_refuses_a_second_explode(tmp_path, monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg)
    journal_mod.explode(board, plan, journal_dir_override=tmp_path)
    with pytest.raises(ExplodeError):
        journal_mod.explode(board, plan, journal_dir_override=tmp_path)


def test_verification_flags_an_item_not_on_target(tmp_path, monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg)

    def _push():                                   # KiCad "applies" it elsewhere
        p1 = board.get_footprint("C1")
        p1.position = type(p1.position).from_xy(p1.position.x + 12345,
                                                p1.position.y)
    board.on_push = _push
    with pytest.raises(ExplodeError):
        journal_mod.explode(board, plan, journal_dir_override=tmp_path)
    assert journal_mod.journal_path(board, tmp_path).is_file()   # kept


def test_verification_flags_dragged_non_moved_copper(tmp_path, monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg)

    def _push():                                   # nt1 is NOT moved
        nt1 = next(t for t in board.get_tracks() if t.uuid == "nt1")
        nt1.start = type(nt1.start).from_xy(nt1.start.x + 7777, nt1.start.y)
    board.on_push = _push
    with pytest.raises(ExplodeError):
        journal_mod.explode(board, plan, journal_dir_override=tmp_path)


def test_restore_names_hand_moved_and_gone_and_keeps_the_journal(
        tmp_path, monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg)
    journal_mod.explode(board, plan, journal_dir_override=tmp_path)
    path = journal_mod.journal_path(board, tmp_path)
    p1 = board.get_footprint("C1")
    p1.position = type(p1.position).from_xy(p1.position.x + 500_000,
                                            p1.position.y)
    board._tracks[:] = [t for t in board._tracks if t.uuid != "t1"]

    lines = journal_mod.restore(board, path)
    text = "\n".join(lines)
    assert "C1" in text                            # named: moved by hand
    assert "t1" in text                            # named: no longer on the board
    assert path.is_file()                          # kept — not everything is back


def test_restore_without_a_journal_refuses(tmp_path, monkeypatch):
    board, cfg = _scenario(monkeypatch)
    with pytest.raises(ExplodeError):
        journal_mod.restore(board, tmp_path / "nope.json")


def test_journal_directory_is_never_under_profiles():
    assert "profiles" not in str(journal_mod.journal_dir())


def test_journal_status_reads_the_board_journal(tmp_path, monkeypatch):
    board, cfg = _scenario(monkeypatch)
    assert journal_mod.journal_status(board, tmp_path) is None
    journal_mod.explode(board, _plan(board, cfg), journal_dir_override=tmp_path)
    status = journal_mod.journal_status(board, tmp_path)
    assert status is not None and status["cell"] == "dac_buf"
