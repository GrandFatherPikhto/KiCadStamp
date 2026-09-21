# tests/gui/test_board_door_offenders.py
"""Т5-1..Т5-3 of plan_2026_09_21_board_door_enforcement: the first three offenders
of the armed run, each pinned by the REAL guard rather than by a spy.

The guard (gui/connection.py) is armed here in its TEST RIG's mode, for the UI
thread of the test, so any path that still reads `connection.board` unsigned
RAISES inside the path under test. That is the strongest form of these watchdogs:
they do not assert that some call was not made — they let the guard say so itself,
naming the file and the line that has to change.

The three, in the order the armed run of 21.09.2026 named them:

  Т5-1  `TreesDock.set_root_file` -> `_clear_all_tree_markers` -> `_live_adapter`:
        the FIRST thing a user runs into after connecting (an actual root switch).
        Fixed with the door's own sign — the read is deliberate (it hands the
        shared adapter to the marker-cleanup worker), so it says so at the call
        site instead of being moved or hidden.
  Т5-2  `DockHub._warn_if_sheet_narrowing_disabled`: a legal presence check, now
        `connection.is_connected`.
  Т5-3  `gui.worker.snapshot_refresh_supported`: the same, answered by the
        connection itself now (`BoardConnection.snapshot_refresh_supported`), so
        the capability question never opens the door at all.

Numbers live in the docstrings, names describe the property (rule 37).
"""
from types import SimpleNamespace

import pytest

from gui.worker import snapshot_refresh_supported
from kicadstamp.config.sexp_format import dict_to_sexp


@pytest.fixture
def armed_door(qapp, monkeypatch):
    """The door's guard, ARMED for this test's UI thread in the rig's mode: a
    violation raises instead of writing a red Log line, which is what makes these
    watchdogs able to fail."""
    from gui import connection as connection_mod
    from gui.worker import is_ui_thread

    monkeypatch.setattr(connection_mod, "ui_thread_predicate", is_ui_thread)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal",
                        connection_mod.UI_READ_RAISE)


def test_opening_a_root_does_not_read_the_board_unsigned(
        real_main_window, tmp_path, armed_door):
    """Т5-1 — an actual root switch clears the tree markers, and that must not read
    the board without a sign.

    Mutation check: drop the `with ui_thread_board_read(...)` from
    `TreesDock._clear_all_tree_markers` and this fails with a refusal naming
    gui/docks/trees_dock.py's `_live_adapter` line."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"cells": {}, "trees": []}), encoding="utf-8")
    real_main_window.connection.board = SimpleNamespace(adapter=object())

    real_main_window._dock_hub.trees_dock.set_root_file(root)   # must not raise


def test_the_sheet_narrowing_warning_does_not_read_the_board_unsigned(
        real_main_window, armed_door):
    """Т5-2 — "is there a board?" is `is_connected`, not a read of the board.

    Mutation check: restore `getattr(connection, "board", None) is None` in
    `DockHub._warn_if_sheet_narrowing_disabled` and this fails with the refusal."""
    real_main_window.connection.board = SimpleNamespace(adapter=object())

    # A root that does not exist is fine here: the board check comes FIRST, and it
    # is the read this test is about (the failed load is logged, not raised).
    real_main_window._dock_hub._warn_if_sheet_narrowing_disabled("nope.sexp")


def test_snapshot_refresh_supported_does_not_read_the_board_unsigned(
        real_main_window, armed_door):
    """Т5-3 — the capability question is answered by the connection itself
    (`BoardConnection.snapshot_refresh_supported`), so the door is never opened
    for it.

    Mutation check: put the `getattr(getattr(connection, "board", None), "refresh",
    None)` body back in `gui/worker.snapshot_refresh_supported` and this fails."""
    real_main_window.connection.board = SimpleNamespace(refresh=lambda: None)

    assert snapshot_refresh_supported(real_main_window.connection) is True
