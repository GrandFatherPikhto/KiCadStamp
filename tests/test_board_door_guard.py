# tests/test_board_door_guard.py
"""С1–С11 of plan_2026_09_21_board_door_enforcement: the guard standing in the
ONE door to the live board (gui/connection.py).

The property under test is the `board` getter's JURISDICTION. The plan's §5.7
table was written before these watchdogs (rule 35), and every cell has a test
here:

  | # | thread  | predicate     | value    | sign          | caller     | expect  |
  |---|---------|---------------|----------|---------------|------------|---------|
  | 1 | UI      | installed     | not None | none          | outside    | REFUSED |
  | 2 | UI      | installed     | not None | taken         | outside    | passes  |
  | 3 | non-UI  | installed     | not None | none          | outside    | passes  |
  | 4 | UI      | installed     | None     | none          | outside    | None    |
  | 5 | UI      | installed     | not None | none          | connection | passes  |
  | 6 | UI      | every thread  | not None | OTHER thread  | outside    | REFUSED |
  | 7 | UI      | installed     | not None | nested (inner exit) | outside | passes |
  | 8 | UI      | NOT installed | not None | none          | outside    | passes  |

С1 = cell 1 (plus its Log line and the "the named line is the reading line" half);
С2 = cell 2; С3 = cell 3; С4 = cell 4 (and its quiet half); С10 = cell 5; С7 =
cell 6; С8 = cell 7 (plus the balanced-exit half); С11 = cell 8 — the jurisdiction
that keeps the existing suite green without a single edit; С5 — the setter is not
the point of force. С9a/С9b are about the production entry point, where the guard
became ARMED on 23.09.2026 (Ш6, plan_2026_09_23_door_s6_entry — the door effort's
last step; the call had been deliberately ABSENT until then, Denis 21.09.2026):
С9a forbids the silent third state ("neither armed nor saying why"), and С9b pins
the arming itself, in the USER's mode (refusal="log"; a raise inside a Qt slot is
a core dump, measured 2026-09-21). С9b spent the un-armed era marked
@pytest.mark.xfail(strict=True) — which turns XPASS, a FAILURE on purpose, the
moment the line returns, so the mark could not be forgotten in either direction;
the day Ш6 landed the line, the mark was removed, exactly as it required. С12
covers that mode: the same violation, reported at ERROR once per site with the read
going on. С6 — the nine existing watchdogs in tests/test_board_access_door.py —
lives in THAT file, untouched.

The value's TYPE never takes part in any of this (plan §5.1, decided with Denis):
a plain `object()` is enough to be refused, which is the whole point — kipy's
Board needs a live client, so a test could not put a real one behind the door,
and a type check would have made С1–С3 unwritable while protecting nothing (a
stand-in never appears in production).

No Qt and no KiCad: the predicate is a plain callable and the board a plain
object — the guard itself never imports Qt, which is exactly why the answer is
injected from outside.
"""
import logging
import re
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gui import connection as connection_mod
from gui.connection import (BoardConnection, UiThreadBoardReadRefused,
                            set_ui_thread_predicate, ui_thread_board_read)

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_guard_state():
    """No test may inherit a sign or a logged site from another: the sign is
    thread-local storage (it outlives a test on the same thread) and the
    once-per-site Log memory is process-global."""
    connection_mod._ui_read_sign.depth = 0
    connection_mod._refused_sites.clear()
    yield
    connection_mod._ui_read_sign.depth = 0
    connection_mod._refused_sites.clear()


@pytest.fixture
def ui_thread(monkeypatch):
    """A REAL answer to the question the guard asks, in the TEST RIG's mode: the
    predicate says "the current thread is the UI thread" for the thread that
    installed this test (production installs gui.worker.is_ui_thread, which
    compares against QApplication's thread — same shape, same answer), and a
    violation RAISES."""
    main = threading.current_thread()
    monkeypatch.setattr(connection_mod, "ui_thread_predicate",
                        lambda: threading.current_thread() is main)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal",
                        connection_mod.UI_READ_RAISE)


@pytest.fixture
def ui_thread_log_mode(monkeypatch):
    """The same predicate in the PRODUCTION mode: a violation is a red Log line
    and the read goes on (С12)."""
    main = threading.current_thread()
    monkeypatch.setattr(connection_mod, "ui_thread_predicate",
                        lambda: threading.current_thread() is main)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal",
                        connection_mod.UI_READ_LOG)


def _read_in_worker(connection):
    """Read `connection.board` on a fresh thread and return ("value", value) or
    ("refused", None) — the refusal is an exception, not a None."""
    outcome = {}

    def _worker():
        try:
            outcome["read"] = ("value", connection.board)
        except UiThreadBoardReadRefused:
            outcome["read"] = ("refused", None)

    worker = threading.Thread(target=_worker, name="GuardProbe")
    worker.start()
    worker.join()
    return outcome["read"]


def _site_from(message: str) -> str:
    """The `file:line` the refusal names — the only path-like token in it."""
    match = re.search(r"([^\s:]+\.py:\d+)", message)
    assert match is not None, f"the refusal names no site: {message!r}"
    return match.group(1)


# ── С1 — refused, and the message names the CALLER ───────────────────────────

def test_ui_thread_read_without_a_sign_is_refused(ui_thread):
    """С1 (table cell 1) — a UI-thread read with no sign is refused, and the
    message names the CALLER's site: the point of the refusal is to name the
    place that has to change, not the guard.

    Mutation check: remove the `_refuse_ui_thread_read(frame)` call from the
    getter and this test fails with DID NOT RAISE."""
    connection = BoardConnection()
    connection.board = object()

    with pytest.raises(UiThreadBoardReadRefused) as excinfo:
        _ = connection.board

    site = _site_from(str(excinfo.value))
    assert site.startswith(str(Path(__file__).resolve())), site
    assert "gui/connection.py:" not in str(excinfo.value)


def test_the_refusal_names_the_line_that_actually_reads_the_board(ui_thread):
    """С1, the strict half: the named line is the line that read the board —
    `file` alone would also pass if the guard named the getter or the caller of
    the caller.

    Mutation check: record `sys._getframe(2)` (the caller's caller) instead of
    frame 1 in the getter and the named line stops being a `connection.board`
    read."""
    connection = BoardConnection()
    connection.board = object()

    with pytest.raises(UiThreadBoardReadRefused) as excinfo:
        board = connection.board  # noqa: F841 — the read under test

    site = _site_from(str(excinfo.value))
    line_no = int(site.rsplit(":", 1)[1])
    source = Path(__file__).read_text(encoding="utf-8").splitlines()
    assert "connection.board" in source[line_no - 1], source[line_no - 1]


def test_the_refusal_is_logged_once_per_site(ui_thread, caplog):
    """С1, the Log half — the refusal also reaches the Log, ONCE per `file:line`,
    so a handler that keeps reading in a loop cannot flood it. This is what makes
    the refusal visible where a flow catches broadly and logs instead of
    propagating.

    Mutation check: drop the `first` flag in _refuse_ui_thread_read and the
    count becomes 3; drop the logging call and it becomes 0."""
    connection = BoardConnection()
    connection.board = object()
    marker = Path(__file__).name

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            with pytest.raises(UiThreadBoardReadRefused):
                _ = connection.board

    hits = [record for record in caplog.records if marker in record.getMessage()]
    assert len(hits) == 1, [record.getMessage() for record in hits]


# ── С2 — the sign opens the door ─────────────────────────────────────────────

def test_a_signed_read_passes(ui_thread):
    """С2 (table cell 2) — inside `ui_thread_board_read` the same read passes.

    Mutation check: drop the `_ui_read_sign` depth test from the getter and this
    fails with the refusal."""
    connection = BoardConnection()
    board = object()
    connection.board = board

    with ui_thread_board_read(reason="С2 — a deliberate UI-thread read"):
        assert connection.board is board


def test_the_sign_requires_a_non_empty_reason():
    """Т3 — a sign without a reason is not a sign: `reason` is keyword-only AND
    must say something. That is what makes a deliberate UI-thread read visible in
    the code where the next person reads it.

    Mutation check: give `reason` a default (`reason: str = ""`) and the first
    raise disappears; drop the `.strip()` test and the second one does."""
    with pytest.raises(TypeError):
        with ui_thread_board_read():
            pass
    with pytest.raises(ValueError):
        with ui_thread_board_read(reason="   "):
            pass


# ── С3 — another thread passes with no sign ──────────────────────────────────

def test_a_read_from_a_non_ui_thread_passes_without_a_sign(ui_thread):
    """С3 (table cell 3) — the SAME connection, read from a thread the predicate
    does not call the UI thread, passes untouched; and the same read on the main
    thread IS refused. The predicate, not the value, is the jurisdiction — which
    is also what keeps every worker/CLI/test read working.

    Mutation check: replace the predicate call in the getter with an
    unconditional check and the worker's read is refused."""
    connection = BoardConnection()
    board = object()
    connection.board = board

    assert _read_in_worker(connection) == ("value", board)
    with pytest.raises(UiThreadBoardReadRefused):
        _ = connection.board


# ── С4 — None is not a refusal ───────────────────────────────────────────────

def test_none_is_returned_normally_when_there_is_no_connection(ui_thread):
    """С4 (table cell 4) — with nothing behind the door a UI-thread read returns
    None and is NOT refused: None means "there is no connection", no socket is
    touched, and the guard must not be mistakable for it.

    Mutation check: move the `value is None` test below the guard and this test
    raises instead of returning None (and every presence check in the GUI starts
    to blow up)."""
    connection = BoardConnection()
    assert connection.board is None
    assert connection.is_connected is False


def test_none_is_not_logged_as_a_refusal(ui_thread, caplog):
    """С4, the quiet half — the None path must not even LOOK like a refusal in
    the Log, or every offline tick would write a line.

    Mutation check: drop the `value is None` test and the Log records the site."""
    connection = BoardConnection()
    with caplog.at_level(logging.WARNING):
        assert connection.board is None
    assert [r for r in caplog.records
            if Path(__file__).name in r.getMessage()] == []


# ── С10 — the connection's OWN reads are not consumers ───────────────────────

def test_the_connections_own_reads_are_not_refused(ui_thread):
    """С10 (table cell 5) — `is_connected`, `refresh()` and `disconnect()` read
    `self.board` from gui/connection.py itself and keep working on the UI thread.
    `is_connected` is the sanctioned UI-thread presence check (MainWindow's
    _finish_poll calls it on the UI thread on every tick), so refusing it would
    break the poll, not a debugger convenience.

    Mutation check: drop the `_is_own_read(frame)` test from the getter and all
    three raise."""
    connection = BoardConnection()
    board = MagicMock()
    connection.board = board

    assert connection.is_connected is True
    assert connection.refresh() is None
    connection.disconnect()
    assert connection.board is None


# ── С5 — the setter is not the point of force ────────────────────────────────

def test_the_setter_is_not_guarded(ui_thread):
    """С5 — assigning a board never raises, on any thread: the door's point of
    force is the READ (the door's rule), and ~20 existing test assignments of
    `connection.board = <fake>` depend on that.

    Mutation check: move the guard into the setter and this raises."""
    connection = BoardConnection()
    fake = object()
    connection.board = fake                    # must not raise
    assert connection._board is fake
    connection.board = None
    assert connection._board is None


# ── С7 — the sign is thread-local ────────────────────────────────────────────

def test_a_sign_does_not_travel_to_another_thread(monkeypatch):
    """С7 (table cell 6) — the sign counts per thread (Денис, 21.09.2026). The
    predicate here answers "UI thread" for EVERY thread, so the sign's
    thread-locality is the only thing between the worker's read and the board —
    the property in isolation.

    Mutation check: hold the depth in a module-global namespace instead of
    threading.local() and the worker's read stops being refused."""
    monkeypatch.setattr(connection_mod, "ui_thread_predicate", lambda: True)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal",
                        connection_mod.UI_READ_RAISE)
    connection = BoardConnection()
    connection.board = object()

    with ui_thread_board_read(reason="С7 — taken on the test's own thread"):
        assert connection.board is not None    # signed HERE
        assert _read_in_worker(connection) == ("refused", None)


# ── С8 — nesting: an inner exit does not clear an outer sign ─────────────────

def test_leaving_an_inner_sign_does_not_clear_an_outer_one(ui_thread):
    """С8 (table cell 7) — the sign counts nesting: after an inner `with` exits,
    the outer one still holds.

    Mutation check: set the depth to 0 (or to False) in the finally instead of
    restoring the previous value, and the post-inner read is refused."""
    connection = BoardConnection()
    connection.board = object()

    with ui_thread_board_read(reason="С8 — outer"):
        with ui_thread_board_read(reason="С8 — inner"):
            assert connection.board is not None
        assert connection.board is not None    # the outer sign still holds
    with pytest.raises(UiThreadBoardReadRefused):
        _ = connection.board                   # and both are gone now


def test_a_sign_left_behind_does_not_outlive_its_block(ui_thread):
    """С8's other half — the sign is balanced even when the block raises, or a
    single failure inside a signed block would leave the door open for the rest
    of the session.

    Mutation check: drop the try/finally around the yield and this fails."""
    connection = BoardConnection()
    connection.board = object()

    with pytest.raises(RuntimeError):
        with ui_thread_board_read(reason="С8 — the block fails"):
            assert connection.board is not None
            raise RuntimeError("boom")

    with pytest.raises(UiThreadBoardReadRefused):
        _ = connection.board


# ── С12 — the production mode: report, do not kill ───────────────────────────

def test_log_mode_reports_the_read_and_hands_the_board_over(
        ui_thread_log_mode, caplog):
    """С12 — the PRODUCTION answer, decided with Denis 2026-09-21 after measuring
    that an exception inside a Qt slot is a core dump: report the site at ERROR
    level, ONCE, and hand the board over. A violation the user meets is a red Log
    line, not a lost session.

    Mutation checks: make the raise unconditional (m12-raise-always) and this
    fails with UiThreadBoardReadRefused; log the line at WARNING instead of ERROR
    and the level assertion fails."""
    connection = BoardConnection()
    board = object()
    connection.board = board

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert connection.board is board      # handed over, never raised

    errors = [record for record in caplog.records
              if record.levelno == logging.ERROR
              and Path(__file__).name in record.getMessage()]
    assert len(errors) == 1, [record.getMessage() for record in errors]


def test_the_refusal_mode_must_be_chosen_where_the_guard_is_armed():
    """Т4 — the mode is a DECISION, made where the guard is armed, in writing:
    `refusal` is a required keyword and only its two values are accepted.

    Mutation check: give `refusal` a default in set_ui_thread_predicate
    (m13-mode-not-required) and the first raise disappears."""
    with pytest.raises(TypeError):
        set_ui_thread_predicate(lambda: True)
    with pytest.raises(ValueError):
        set_ui_thread_predicate(lambda: True, refusal="shout")


# ── С11 — no predicate: the guard is not looking at threads ──────────────────

def test_without_a_predicate_nothing_is_refused(monkeypatch, caplog):
    """С11 (table cell 8) — with no predicate installed (the default: the CLI,
    the MCP server, diagnostics and every test that does not install one) the
    guard is not looking at threads AT ALL: the read passes and NOTHING is
    reported, not even a Log line. This cell is why the existing suite needed no
    edits — and the Log half is what makes it hold in both modes: in "log" mode a
    guard that looked would still hand the board over, so an assertion on the
    value alone cannot see the difference.

    Mutation check: invert the predicate test (m3b-no-predicate-treated-as-ui)
    and this fails on the Log assertion, even though the value is still handed
    over."""
    monkeypatch.setattr(connection_mod, "ui_thread_predicate", None)
    connection = BoardConnection()
    connection.board = object()
    with caplog.at_level(logging.DEBUG):
        assert connection.board is not None
    assert [record for record in caplog.records
            if Path(__file__).name in record.getMessage()] == []


# ── С9 — the production entry point: the guard is ARMED there ────────────────

_ENTRY = _REPO_ROOT / "kicadstamp" / "gui_main.py"
# The production form names the USER's mode: log, never raise (a raise inside a
# Qt slot is a core dump — see UI_READ_LOG in gui/connection.py).
_ARMING = 'set_ui_thread_predicate(is_ui_thread, refusal="log")'
# The phrase the entry point must carry if it is EVER deliberately un-armed again
# — the state С9a exists to forbid is "un-armed AND silent". It was the sentinel of
# the un-armed era (the call absent by decision, Denis 21.09.2026) and is kept as
# the forward-looking half of С9a's either/or: gui_main.py does not say it today.
_T5_NOTE = "THE CALL IS DELIBERATELY ABSENT"


def test_the_entry_point_is_either_armed_or_says_why_not():
    """С9a — no silent third state. The guard is only STANDING if the production
    GUI arms it — the ONE Qt entry point of the project (pyproject.toml
    [project.scripts] → kicadstamp-gui) is kicadstamp/gui_main.py. Since Ш6
    (23.09.2026) that entry point IS armed, so the first half of the either/or
    below carries the test; the second half (_T5_NOTE) keeps the state it once
    described from ever coming back SILENTLY — an entry point that is neither
    armed nor documented would look protected while nothing stands in the door.

    Mutation check: remove the arming line and leave no note saying why, and this
    fails — that is mutation m32 of diagnostics/run_board_door_guard_mutations.py.
    (Its predecessor m10, which deleted the un-armed era's note, was retired when
    the note itself left the file and the state it described became unreachable.)"""
    text = _ENTRY.read_text(encoding="utf-8")
    assert _ARMING in text or _T5_NOTE in text, (
        "kicadstamp/gui_main.py neither arms the guard nor says why it does not: "
        "the app would look protected while nothing stands in the door")


def test_the_entry_point_arms_the_guard():
    """С9b — the arming itself, pinned. Ш6 (23.09.2026) landed the two things this
    asserts on: the import of the production predicate (gui.worker.is_ui_thread —
    the same answer the diagnostics recorder is given) and the call that hands it
    to the door in the USER's mode.

    This test spent the un-armed era marked @pytest.mark.xfail(strict=True): the
    asserts below failed ON PURPOSE and strict xfail reported that as EXPECTED. The
    moment the arming line returned they passed, strict turned it into XPASS — a
    FAILURE by design — and the mark was removed as PART of closing the door (Ш6),
    not as a clean-up afterwards. That is the tripwire's whole point: the mark
    could not be forgotten in either direction.

    Mutation check: remove the set_ui_thread_predicate(is_ui_thread, refusal="log")
    line and the second assert goes red — "assert ... in text" (m32 of
    diagnostics/run_board_door_guard_mutations.py, the row Ш6 added)."""
    text = _ENTRY.read_text(encoding="utf-8")
    assert "from gui.worker import is_ui_thread" in text
    assert _ARMING in text
