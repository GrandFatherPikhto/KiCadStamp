# tests/gui/test_main_window.py
"""Tests for gui/main_window.py's settings persistence — window geometry
(plain ints) and the dock/splitter/tab/floating layout (2026-08-27: Qt's own
saveState(), base64 into the SAME gui_state.json, a documented exception to
the file's human-readability principle — see _persist_settings/_restore_
window_state docstrings). Uses the existing real_main_window fixture (tests/
gui/conftest.py) rather than building a MainWindow by hand, and
gui.settings' state is isolated per-test by the autouse isolated_settings
fixture (so this never touches the developer's real gui_state.json).
"""
import base64

from PyQt6.QtCore import QByteArray

from gui import settings
from gui.main_window import _DOCK_STATE_VERSION


def test_persist_settings_writes_dock_state(real_main_window):
    """_persist_settings() writes a non-empty base64 "dock_state" that
    decodes without error (a real saveState() blob, not an empty string)."""
    real_main_window._persist_settings()

    raw = settings.state.get("dock_state")
    assert raw
    blob = base64.b64decode(raw)
    assert blob  # a non-trivial Qt state payload, not just the header


def test_dock_state_round_trip_feeds_same_bytes_to_restore_state(
        real_main_window, monkeypatch):
    """restoreState() must receive EXACTLY the bytes _persist_settings()
    saved — a persist -> restore round-trip through the same state object.
    restoreState is stubbed (a real call would mutate live geometry); the
    assertion is on the argument it was handed."""
    real_main_window._persist_settings()
    raw = settings.state.get("dock_state")
    blob = base64.b64decode(raw)

    calls = []
    monkeypatch.setattr(real_main_window, "restoreState",
                        lambda qb, version: calls.append((qb, version)) or True)
    real_main_window._restore_window_state()

    assert calls, "restoreState was never called for a saved dock_state"
    qb, version = calls[0]
    assert isinstance(qb, QByteArray)
    assert bytes(qb) == blob
    assert version == _DOCK_STATE_VERSION


def test_missing_dock_state_never_attempts_restore(real_main_window, monkeypatch):
    """First run — no "dock_state" key at all: _restore_window_state() must
    not crash and must not attempt restoreState on empty data."""
    calls = []
    monkeypatch.setattr(real_main_window, "restoreState",
                        lambda *a, **k: calls.append(a) or True)

    real_main_window._restore_window_state()  # must not raise

    assert not calls


def test_corrupt_dock_state_logs_and_falls_through(real_main_window, caplog):
    """A hand-edited/undecodable "dock_state" must never crash startup — log
    the decode failure and fall through to the default layout."""
    settings.state.set("dock_state", "not-valid-base64!!!")

    real_main_window._restore_window_state()  # must not raise

    assert "Failed to decode saved dock_state" in caplog.text


def test_restore_state_false_returns_logs_and_falls_through(
        real_main_window, monkeypatch, caplog):
    """restoreState() returning False (a version/dock-set mismatch — the
    layout was saved by a future release, or the dock set changed) is just a
    log line, never an exception: the default layout stands."""
    settings.state.set("dock_state", "AAAA")  # decodes to 3 zero bytes
    monkeypatch.setattr(real_main_window, "restoreState",
                        lambda *a, **k: False)

    real_main_window._restore_window_state()  # must not raise

    assert "did not apply" in caplog.text


# ── View menu (2026-08-27, handoff sync_skip_message_and_view_menu) ───────

def _view_menu(real_main_window):
    """The single top-level "View" menu (the app had no menu bar before this
    feature)."""
    view_menus = [a for a in real_main_window.menuBar().actions()
                  if a.text() == "View"]
    assert len(view_menus) == 1
    return view_menus[0].menu()


def test_view_menu_has_one_checkable_action_per_dock(real_main_window):
    """One checkable toggleViewAction per real top-level dock — the only way
    to bring back a closed dock (the app previously had no menu bar at all).
    Count must match DockHub.docks (8), NOT DetailDock's internal panels."""
    menu = _view_menu(real_main_window)
    actions = menu.actions()
    assert len(actions) == len(real_main_window._dock_hub.docks) == 8
    assert all(a.isCheckable() for a in actions)


def test_view_menu_toggle_shows_and_hides_a_dock(real_main_window):
    """toggleViewAction self-tracks shown/hidden: hiding a dock unchecks its
    action, triggering the action on a hidden dock brings it back. Uses
    isHidden() rather than isVisible() — the real main window is never shown()
    in offscreen tests, so isVisible() would be False for every dock."""
    log_dock = real_main_window._dock_hub.log_dock
    log_action = next(a for a in _view_menu(real_main_window).actions()
                      if a.text() == log_dock.windowTitle())

    log_dock.hide()
    assert log_action.isChecked() is False
    assert log_dock.isHidden() is True

    log_action.trigger()
    assert log_dock.isHidden() is False
