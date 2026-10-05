"""tools/door_lint.py — the static census of board reads that bypass the board
door's UI-thread rules (docs/board_door.md).

Two kinds of cells: the RATCHET on the real gui/ (no file may grow its suspect
count, a new file must have none) and the CLASSIFIER on a synthetic tree (each
bucket — worker / signed / suspect — is reachable and means what it says).
"""
import importlib.util
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _lint():
    spec = importlib.util.spec_from_file_location("door_lint", ROOT / "tools" / "door_lint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_ratchet_holds_on_the_real_gui():
    lint = _lint()
    problems = lint.check(lint.scan(), lint._load_baseline())
    assert not problems, (
        "a board read off the worker and without the sign was added — read the "
        "board in a start_long_op worker, or use connection.is_connected / "
        "connection.snapshot (docs/board_door.md). Problems:\n  "
        + "\n  ".join(problems))


def _scan(tmp_path, source: str) -> dict:
    """{function: set of kinds of the board reads inside it}."""
    (tmp_path / "gui").mkdir()
    (tmp_path / "gui" / "dock.py").write_text(textwrap.dedent(source), encoding="utf-8")
    rows = _lint().scan(tmp_path)
    out: dict = {}
    for r in rows:
        out.setdefault(r["func"], set()).add(r["kind"])
    return out


SOURCE = '''
    from functools import partial

    class Dock:
        def _on_click(self):                      # UI slot
            board = self._connection.board
            start_long_op(self._connection, (), self._run, ok, err)
            start_long_op(self._connection, (), partial(self._run_p, 1), ok, err)
            start_long_op(self._connection, (), lambda: self._connection.board, ok, err)

        def _run(self):                           # worker fn
            return self._helper_worker_only()

        def _run_p(self, n):                      # worker via partial
            return self._connection.board

        def _helper_worker_only(self):            # called only by a worker
            return getattr(self._connection, "board", None)

        def _helper_both(self):                   # called from UI AND worker
            return self._connection.board

        def _signed(self):
            with ui_thread_board_read(reason="measured 0.2 ms"):
                return self._connection.board

        def _ui_again(self):
            self._helper_both()

        def _run2(self):
            self._helper_both()

        def _go(self):
            start_long_op(self._connection, (), self._run2, ok, err)
'''


@pytest.fixture
def kinds(tmp_path):
    return _scan(tmp_path, SOURCE)


def test_a_ui_slot_read_is_a_suspect_and_its_worker_lambda_is_not(kinds):
    # the slot reads the board itself (suspect) AND defines a lambda handed to
    # start_long_op (worker) — both reads are attributed to the slot
    assert kinds["Dock._on_click"] == {"suspect", "worker"}


def test_a_worker_fn_via_partial_is_worker(kinds):
    assert kinds["Dock._run_p"] == {"worker"}


def test_a_helper_called_only_by_workers_is_worker(kinds):
    assert kinds["Dock._helper_worker_only"] == {"worker"}


def test_a_helper_called_from_both_threads_is_a_suspect(kinds):
    assert kinds["Dock._helper_both"] == {"suspect"}


def test_a_signed_read_is_signed(kinds):
    assert kinds["Dock._signed"] == {"signed"}


def test_a_new_file_with_a_suspect_breaks_the_ratchet(tmp_path):
    lint = _lint()
    (tmp_path / "gui").mkdir()
    (tmp_path / "gui" / "new.py").write_text(
        "def slot(c):\n    return c.board\n", encoding="utf-8")
    assert lint.check(lint.scan(tmp_path), {}) == [
        "gui/new.py: 1 suspect board read(s), baseline 0"]
