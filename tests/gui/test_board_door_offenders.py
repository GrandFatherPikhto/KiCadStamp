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
from kicadstamp.constants import DEFAULT_TIMEOUT_MS


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


# ── Ш4 (plan_2026_09_22_board_door_finish) — the Extract entry ───────────────
# The place the armed run of 22.09.2026 named (dock_hub.py:2137). Its presence
# half is is_connected; the adapter it captures for the flow's LIVE preview reads
# is taken under the door's sign.

def test_the_extract_entry_does_not_read_the_board_unsigned(
        real_main_window, armed_door, monkeypatch):
    """Ш4 — with the door ARMED the Extract entry refuses nothing. The flow stops
    at the missing root (this window has none) and never reaches its dialog, so
    the cells here are the entry itself.

    Mutation check: drop the `with ui_thread_board_read(...)` wrapper and this
    fails with a refusal naming gui/dock_hub.py."""
    import gui.dock_hub as hub_mod
    monkeypatch.setattr(hub_mod.QMessageBox, "warning", lambda *a, **k: None)
    real_main_window.connection.board = SimpleNamespace(adapter=object())

    real_main_window._dock_hub._extract_tree_from_selection_now()   # must not raise


class _PresenceOnlyConnection:
    """Answers the presence question and DIES if the door is opened anyway: a
    presence check must not read `board` — the Т5-2 idiom, applied to the Extract
    entry (Ш4) and to the Instantiate-from-selection continuation (Т2-1)."""

    def __init__(self, is_connected=False):
        self.is_connected = is_connected
        self.long_op_active = False
        self.timeout_ms = DEFAULT_TIMEOUT_MS

    @property
    def board(self):
        raise AssertionError("the presence check read the board through the door")


def test_the_extract_presence_check_does_not_open_the_door(
        real_main_window, monkeypatch):
    """Ш4 — "is there a board?" is `connection.is_connected`, not a read of the
    door (the Т5-2 idiom, applied to the Extract entry). Pinned with a connection
    whose `board` raises when read: the flow must report "Not connected." without
    touching it.

    Mutation check: restore `getattr(connection, "board", None)` as the presence
    check and this fails on the stand-in's AssertionError."""
    import gui.dock_hub as hub_mod
    warnings = []
    monkeypatch.setattr(hub_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    real_main_window.connection = _PresenceOnlyConnection()

    real_main_window._dock_hub._extract_tree_from_selection_now()

    assert warnings, "the flow must report the missing connection"


# ── Т2-1 (plan_2026_09_22_live_adapter_class) — the Instantiate continuation ──
# `_anchor_base_then` (the "from selection" half of Instantiate from Cell) asked
# "is there a board?" BY READING THE DOOR and handed nothing through that read:
# the base read itself runs on the WORKER, which builds its own adapter
# (run_anchor_base_mm_worker). The presence question is the connection's now.

def test_the_instantiate_continuation_does_not_read_the_board_unsigned(
        real_main_window, tmp_path, armed_door, monkeypatch):
    """Т2-1 — with the door ARMED the continuation refuses nothing: it asks the
    connection whether a board is there and goes on to its worker. The stand-in's
    `board` RAISES when read, so a door read fails the test outright.

    The root comes first (a continuation without a config refuses for that reason,
    not for the board's) and the modal is recorded rather than shown, so a
    regression fails on the assert instead of waiting for a click.

    Mutation check: restore `self._live_adapter() is None` and this fails — the
    door refuses first, and the stand-in would raise anyway."""
    import gui.docks.trees_dock as td_mod

    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"cells": {}, "trees": []}), encoding="utf-8")
    dock = real_main_window._dock_hub.trees_dock
    dock.set_root_file(root)

    started = []
    warnings = []
    monkeypatch.setattr(td_mod, "start_long_op",
                        lambda *a, **k: started.append(a) or None)
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    real_main_window.connection = _PresenceOnlyConnection(is_connected=True)

    dock._anchor_base_then((1.0, 2.0), "cell1", "ENT_A", "CL", "",
                           SimpleNamespace(name="probe_tree"))

    assert warnings == [], "the presence check refused a connection that is there"
    assert started, "the continuation must reach its worker"
